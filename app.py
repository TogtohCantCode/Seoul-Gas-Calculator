from __future__ import annotations

from typing import Any, Optional
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

DEFAULT_TARIFF = {
    "unit_rate_krw_per_mj": 22.5268,
    "base_charge_krw": 1250.0,
    "avg_calorific_mj_per_m3": 43.1,
    "correction_factor": 0.998,
    "vat_rate": 0.10,
}

DEFAULT_SEOUL_TEMP_C = {
    "nov": 7.5,
    "dec": 1.0,
    "jan": -1.8,
    "feb": 1.2,
}

BUILDING_HEATING_FACTOR = {
    "oneroom": 0.85,
    "officetel": 0.95,
    "villa": 1.25,
    "apartment": 1.00,
}

DEFAULT_SCHEDULE_BLOCKS = [
    {"id": "morning", "label": "Morning", "window": "06–09h", "hours": 3.0, "outdoor_offset_c": -2.5, "default_temp_c": 22.0, "default_active": True},
    {"id": "daytime", "label": "Daytime", "window": "09–18h", "hours": 9.0, "outdoor_offset_c": 2.5, "default_temp_c": 18.0, "default_active": False},
    {"id": "evening", "label": "Evening", "window": "18–23h", "hours": 5.0, "outdoor_offset_c": -0.5, "default_temp_c": 23.0, "default_active": True},
    {"id": "night", "label": "Night", "window": "23–06h", "hours": 7.0, "outdoor_offset_c": -3.0, "default_temp_c": 20.0, "default_active": True},
]


def _safe_float(val: Any, default: float, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    if val is None or val == "":
        result = default
    else:
        try:
            result = float(val)
            if result != result:
                result = default
        except (TypeError, ValueError):
            result = default
    if min_val is not None:
        result = max(min_val, result)
    if max_val is not None:
        result = min(max_val, result)
    return result


def _safe_int(val: Any, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    if val is None or val == "":
        result = default
    else:
        try:
            result = int(float(val))
        except (TypeError, ValueError):
            result = default
    if min_val is not None:
        result = max(min_val, result)
    if max_val is not None:
        result = min(max_val, result)
    return result


def _safe_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        lowered = val.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off", ""}:
            return False
    return default


def _safe_choice(val: Any, allowed: set[str], default: str) -> str:
    if not isinstance(val, str):
        return default
    cleaned = val.strip().lower()
    return cleaned if cleaned in allowed else default


def compute_hdd(avg_outdoor_temp_c: float, base_temp_c: float = 18.0) -> float:
    return max(0.0, base_temp_c - avg_outdoor_temp_c) / 18.0


def _setpoint_multiplier(boiler_mode: str, target_temp_c: float, ondol_temp_c: float, active: bool = True) -> float:
    if not active or boiler_mode == "away":
        return 0.25
    if boiler_mode == "ondol_water":
        return max(0.4, 1.0 + (ondol_temp_c - 60.0) * 0.025)
    temp_delta = target_temp_c - 20.0
    return max(0.4, 1.0 + temp_delta * 0.11 + max(0.0, temp_delta) ** 2 * 0.015)


def _calculate_krw(volume_m3: float, tariff: dict[str, float]) -> dict[str, Any]:
    billed_mj = volume_m3 * tariff["correction_factor"] * tariff["avg_calorific_mj_per_m3"]
    usage_charge = billed_mj * tariff["unit_rate_krw_per_mj"]
    subtotal = tariff["base_charge_krw"] + usage_charge
    vat = subtotal * tariff["vat_rate"]
    total_krw = int(round(subtotal + vat, -1))
    return {
        "billed_mj": round(billed_mj, 2),
        "base_charge_krw": round(tariff["base_charge_krw"]),
        "usage_charge_krw": round(usage_charge),
        "vat_krw": round(vat),
        "total_bill_krw": total_krw,
    }


def estimate_gas_bill(payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}

    area_pyeong = _safe_float(data.get("area_pyeong"), default=8.0, min_val=2.0, max_val=120.0)
    building_type = _safe_choice(data.get("building_type"), set(BUILDING_HEATING_FACTOR.keys()), "oneroom")
    boiler_mode = _safe_choice(data.get("boiler_mode"), {"indoor_temp", "ondol_water", "away"}, "indoor_temp")
    target_temp_c = _safe_float(data.get("target_temp_c"), default=22.0, min_val=15.0, max_val=30.0)
    ondol_temp_c = _safe_float(data.get("ondol_temp_c"), default=60.0, min_val=35.0, max_val=80.0)
    heating_hours = _safe_float(data.get("heating_hours_per_day"), default=12.0, min_val=0.0, max_val=24.0)
    is_top_or_corner = _safe_bool(data.get("is_top_or_corner_unit"), default=False)
    is_older_building = _safe_bool(data.get("is_older_building"), default=False)
    household_size = _safe_int(data.get("household_size"), default=1, min_val=1, max_val=10)
    month = _safe_choice(data.get("month"), set(DEFAULT_SEOUL_TEMP_C.keys()), "jan")
    outdoor_temp = _safe_float(data.get("seoul_avg_temp_c"), default=DEFAULT_SEOUL_TEMP_C[month], min_val=-25.0, max_val=35.0)

    tariff = {
        "unit_rate_krw_per_mj": _safe_float(data.get("unit_rate_krw_per_mj"), DEFAULT_TARIFF["unit_rate_krw_per_mj"], 1.0, 200.0),
        "base_charge_krw": _safe_float(data.get("base_charge_krw"), DEFAULT_TARIFF["base_charge_krw"], 0.0, 50000.0),
        "avg_calorific_mj_per_m3": _safe_float(data.get("avg_calorific_mj_per_m3"), DEFAULT_TARIFF["avg_calorific_mj_per_m3"], 20.0, 60.0),
        "correction_factor": _safe_float(data.get("correction_factor"), DEFAULT_TARIFF["correction_factor"], 0.8, 1.2),
        "vat_rate": _safe_float(data.get("vat_rate"), DEFAULT_TARIFF["vat_rate"], 0.0, 0.5),
    }

    envelope_factor = BUILDING_HEATING_FACTOR[building_type]
    if is_top_or_corner:
        envelope_factor *= 1.18
    if is_older_building:
        envelope_factor *= 1.20

    daily_hdd = compute_hdd(outdoor_temp)
    hot_water_m3 = household_size * 0.22 * (1.0 + 0.15 * daily_hdd) * 30.0

    # Constant temperature baseline
    const_mode_factor = _setpoint_multiplier(boiler_mode, target_temp_c, ondol_temp_c, active=True)
    const_duty = min(1.0, max(0.15, heating_hours / 24.0)) if boiler_mode == "away" else min(1.0, max(0.2, heating_hours / 16.0))
    const_heating_m3 = (area_pyeong * 1.45) * daily_hdd * envelope_factor * const_mode_factor * const_duty
    const_total_m3 = hot_water_m3 + const_heating_m3
    const_billing = _calculate_krw(const_total_m3, tariff)

    # 24h schedule blocks
    raw_schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}
    use_schedule = _safe_bool(data.get("use_schedule"), default=False)

    schedule_breakdown = []
    schedule_heating_m3 = 0.0
    prev_temp = None

    for block in DEFAULT_SCHEDULE_BLOCKS:
        bid = block["id"]
        b_data = raw_schedule.get(bid) if isinstance(raw_schedule.get(bid), dict) else {}
        b_temp = _safe_float(b_data.get("temp_c"), default=block["default_temp_c"], min_val=15.0, max_val=30.0)
        b_active = _safe_bool(b_data.get("active"), default=block["default_active"])

        block_outdoor = outdoor_temp + block["outdoor_offset_c"]
        block_hdd = compute_hdd(block_outdoor)
        block_factor = _setpoint_multiplier(boiler_mode, b_temp, ondol_temp_c, active=b_active)

        recovery_penalty = 1.0
        if b_active and prev_temp is not None and (b_temp - prev_temp) >= 3.0:
            recovery_penalty = 1.0 + min(0.18, (b_temp - prev_temp) * 0.03)

        time_share = block["hours"] / 16.0
        block_m3 = (area_pyeong * 1.45) * block_hdd * envelope_factor * block_factor * time_share * recovery_penalty
        block_krw = round(
            block_m3 * tariff["correction_factor"] * tariff["avg_calorific_mj_per_m3"] * tariff["unit_rate_krw_per_mj"] * (1.0 + tariff["vat_rate"])
        )

        schedule_heating_m3 += block_m3
        schedule_breakdown.append({
            "id": bid,
            "label": block["label"],
            "window": block["window"],
            "hours": block["hours"],
            "temp_c": b_temp,
            "active": b_active,
            "monthly_m3": round(block_m3, 2),
            "monthly_heating_cost_krw": int(block_krw),
        })
        prev_temp = b_temp if b_active else min(b_temp, 16.0)

    schedule_total_m3 = hot_water_m3 + schedule_heating_m3
    schedule_billing = _calculate_krw(schedule_total_m3, tariff)

    # Optional meter burn-rate projection
    prev_meter = data.get("previous_reading_m3")
    curr_meter = data.get("current_reading_m3")
    elapsed_days = _safe_int(data.get("elapsed_days"), default=0, min_val=0, max_val=45)
    cycle_days = _safe_int(data.get("billing_cycle_days"), default=30, min_val=15, max_val=45)

    active_m3 = schedule_total_m3 if use_schedule else const_total_m3
    active_heating_m3 = schedule_heating_m3 if use_schedule else const_heating_m3
    active_billing = schedule_billing if use_schedule else const_billing

    meter_projection = None
    if prev_meter not in (None, "") and curr_meter not in (None, "") and elapsed_days > 0:
        p_val = _safe_float(prev_meter, default=0.0, min_val=0.0)
        c_val = _safe_float(curr_meter, default=p_val, min_val=0.0)
        delta_m3 = max(0.0, c_val - p_val)
        daily_rate = delta_m3 / elapsed_days
        rem_days = max(0, cycle_days - elapsed_days)
        projected_m3 = delta_m3 + (daily_rate * rem_days)
        if elapsed_days < 3:
            w = elapsed_days / 3.0
            projected_m3 = (w * projected_m3) + ((1.0 - w) * active_m3)
        active_m3 = projected_m3
        active_billing = _calculate_krw(projected_m3, tariff)
        meter_projection = {
            "consumed_to_date_m3": round(delta_m3, 2),
            "daily_burn_m3": round(daily_rate, 3),
            "projected_cycle_m3": round(projected_m3, 2),
        }

    diff_krw = const_billing["total_bill_krw"] - schedule_billing["total_bill_krw"]
    diff_m3 = round(const_total_m3 - schedule_total_m3, 2)

    return {
        "mode": "schedule" if use_schedule else "constant",
        "estimated_m3": round(active_m3, 2),
        "breakdown_m3": {
            "hot_water_and_cooking": round(hot_water_m3, 2),
            "space_heating": round(active_heating_m3, 2),
        },
        "billing": active_billing,
        "meter_projection": meter_projection,
        "comparison": {
            "constant_temp_c": target_temp_c,
            "constant_bill_krw": const_billing["total_bill_krw"],
            "constant_m3": round(const_total_m3, 2),
            "schedule_bill_krw": schedule_billing["total_bill_krw"],
            "schedule_m3": round(schedule_total_m3, 2),
            "schedule_savings_krw": diff_krw,
            "schedule_savings_m3": diff_m3,
            "schedule_blocks": schedule_breakdown,
        },
        "assumptions": {
            "seoul_avg_temp_c": round(outdoor_temp, 1),
        },
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/estimate")
def api_estimate():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = request.form.to_dict() if request.form else {}
    return jsonify(estimate_gas_bill(body))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)