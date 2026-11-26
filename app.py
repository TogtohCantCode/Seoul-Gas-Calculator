from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request

from ml_model import ML_ENGINE, daily_hdd
from weather_tracker import (
    MONTHLY_FALLBACK_TEMP_C,
    MONTHLY_FALLBACK_WIND_KMH,
    fetch_seoul_weather_range,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# Seoul City Gas residential tariff (주택용 취사·난방 단가, VAT excluded)
DEFAULT_TARIFF = {
    "unit_rate_krw_per_mj": 22.5268,
    "base_charge_krw": 1250.0,
    "avg_calorific_mj_per_m3": 43.1,
    "correction_factor": 0.998,
    "vat_rate": 0.10,
}

MONTH_TO_NUM = {
    "nov": 11,
    "dec": 12,
    "jan": 1,
    "feb": 2,
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


def _normalized_schedule(raw_schedule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    norm: dict[str, dict[str, Any]] = {}
    for block in DEFAULT_SCHEDULE_BLOCKS:
        bid = block["id"]
        b_data = raw_schedule.get(bid) if isinstance(raw_schedule.get(bid), dict) else {}
        norm[bid] = {
            "temp_c": _safe_float(b_data.get("temp_c"), default=block["default_temp_c"], min_val=15.0, max_val=30.0),
            "active": _safe_bool(b_data.get("active"), default=block["default_active"]),
        }
    return norm


def _slab_recovery_penalty(
    b_active: bool,
    b_temp: float,
    prev_temp: Optional[float],
    away_hours_accumulated: float,
    building_type: str,
) -> float:
    """Concrete ondol slabs lose stored heat during multi-hour Away blocks and need extra burn to recover."""
    if not b_active:
        return 1.0
    temp_jump = (b_temp - prev_temp) if prev_temp is not None else 0.0
    if temp_jump < 2.0 and away_hours_accumulated <= 0.0:
        return 1.0

    slab_inertia = 1.25 if building_type in {"apartment", "officetel"} else 1.0
    jump_pen = max(0.0, temp_jump) * 0.025
    soak_pen = min(0.14, (away_hours_accumulated / 9.0) * 0.09)
    return 1.0 + min(0.28, (jump_pen + soak_pen) * slab_inertia)


def _day_heating_m3(
    outdoor_temp_c: float,
    wind_kmh: float,
    area_pyeong: float,
    building_type: str,
    boiler_mode: str,
    target_temp_c: float,
    ondol_temp_c: float,
    away_interval_min: float,
    away_cycle_hours: float,
    heating_hours: float,
    is_top_or_corner: bool,
    is_older: bool,
    use_schedule: bool,
    raw_schedule: dict[str, Any],
) -> float:
    if not use_schedule:
        duty = heating_hours / 24.0
        return ML_ENGINE.predict_daily_heating_m3(
            area_pyeong=area_pyeong,
            building_type=building_type,
            outdoor_temp_c=outdoor_temp_c,
            boiler_mode=boiler_mode,
            target_temp_c=target_temp_c,
            ondol_temp_c=ondol_temp_c,
            duty_cycle=duty,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older,
            wind_kmh=wind_kmh,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            active=True,
            recovery_penalty=1.0,
        )

    total_day_m3 = 0.0
    prev_temp: Optional[float] = None
    away_hours_acc = 0.0

    for block in DEFAULT_SCHEDULE_BLOCKS:
        bid = block["id"]
        b_data = raw_schedule.get(bid) if isinstance(raw_schedule.get(bid), dict) else {}
        b_temp = _safe_float(b_data.get("temp_c"), default=block["default_temp_c"], min_val=15.0, max_val=30.0)
        b_active = _safe_bool(b_data.get("active"), default=block["default_active"])

        block_outdoor = outdoor_temp_c + block["outdoor_offset_c"]
        recovery = _slab_recovery_penalty(
            b_active=b_active,
            b_temp=b_temp,
            prev_temp=prev_temp,
            away_hours_accumulated=away_hours_acc,
            building_type=building_type,
        )

        duty = block["hours"] / 24.0
        total_day_m3 += ML_ENGINE.predict_daily_heating_m3(
            area_pyeong=area_pyeong,
            building_type=building_type,
            outdoor_temp_c=block_outdoor,
            boiler_mode=boiler_mode,
            target_temp_c=b_temp,
            ondol_temp_c=ondol_temp_c,
            duty_cycle=duty,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older,
            wind_kmh=wind_kmh,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            active=b_active,
            recovery_penalty=recovery,
        )

        if b_active:
            prev_temp = b_temp
            away_hours_acc = 0.0
        else:
            prev_temp = min(b_temp, 16.0)
            away_hours_acc += block["hours"]

    return total_day_m3


def _build_savings_recommendations(
    *,
    cycle_avg_temp_c: float,
    avg_wind: float,
    billing_cycle_days: int,
    baseline_per_person: float,
    household_size: int,
    area_pyeong: float,
    building_type: str,
    boiler_mode: str,
    target_temp_c: float,
    ondol_temp_c: float,
    away_interval_min: float,
    away_cycle_hours: float,
    heating_hours: float,
    is_top_or_corner: bool,
    is_older_building: bool,
    use_schedule: bool,
    raw_schedule: dict[str, Any],
    tariff: dict[str, float],
) -> list[dict[str, Any]]:
    """Simulate counterfactual strategies on the exact same cycle weather baseline."""
    tips: list[dict[str, Any]] = []
    norm_sched = _normalized_schedule(raw_schedule)

    # Apples-to-apples full-cycle baseline using the user's current settings
    hdd_norm = daily_hdd(cycle_avg_temp_c) / 18.0
    cycle_hw_m3 = billing_cycle_days * household_size * baseline_per_person * (1.0 + 0.15 * hdd_norm)
    base_day_heat = _day_heating_m3(
        outdoor_temp_c=cycle_avg_temp_c,
        wind_kmh=avg_wind,
        area_pyeong=area_pyeong,
        building_type=building_type,
        boiler_mode=boiler_mode,
        target_temp_c=target_temp_c,
        ondol_temp_c=ondol_temp_c,
        away_interval_min=away_interval_min,
        away_cycle_hours=away_cycle_hours,
        heating_hours=heating_hours,
        is_top_or_corner=is_top_or_corner,
        is_older=is_older_building,
        use_schedule=use_schedule,
        raw_schedule=norm_sched,
    )
    base_heat_m3 = base_day_heat * billing_cycle_days
    base_total_m3 = cycle_hw_m3 + base_heat_m3
    base_bill_krw = _calculate_krw(base_total_m3, tariff)["total_bill_krw"]

    # Determine active schedule temperatures if user is in 24h schedule mode
    active_block_temps = [v["temp_c"] for v in norm_sched.values() if v["active"]]
    peak_sched_temp = max(active_block_temps) if active_block_temps else 22.0
    has_deep_away = use_schedule and any(not v["active"] for v in norm_sched.values())

    # 1. Trim current temperatures by 1.5°C (works for BOTH 24h Schedule and Constant Slider)
    if boiler_mode == "indoor_temp" and use_schedule:
        max_trim = max((v["temp_c"] - 19.0 for v in norm_sched.values() if v["active"]), default=0.0)
        if max_trim >= 0.5:
            trimmed_sched: dict[str, dict[str, Any]] = {}
            for bid, cfg in norm_sched.items():
                if cfg["active"] and cfg["temp_c"] > 19.0:
                    trimmed_sched[bid] = {"temp_c": round(max(18.5, cfg["temp_c"] - 1.5), 1), "active": True}
                else:
                    trimmed_sched[bid] = dict(cfg)

            trim_day_m3 = _day_heating_m3(
                outdoor_temp_c=cycle_avg_temp_c,
                wind_kmh=avg_wind,
                area_pyeong=area_pyeong,
                building_type=building_type,
                boiler_mode="indoor_temp",
                target_temp_c=target_temp_c,
                ondol_temp_c=ondol_temp_c,
                away_interval_min=away_interval_min,
                away_cycle_hours=away_cycle_hours,
                heating_hours=heating_hours,
                is_top_or_corner=is_top_or_corner,
                is_older=is_older_building,
                use_schedule=True,
                raw_schedule=trimmed_sched,
            )
            trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
            trim_savings = base_bill_krw - trim_bill_krw
            new_peak = max((v["temp_c"] for v in trimmed_sched.values() if v["active"]), default=peak_sched_temp)
            if trim_savings >= 1000:
                tips.append({
                    "id": "trim_schedule_temps",
                    "title": f"Trim Active Schedule Blocks by 1.5°C (Peak {peak_sched_temp:.1f}°C → {new_peak:.1f}°C) + Humidifier",
                    "savings_krw": int(trim_savings),
                    "comfort_note": (
                        "Keeping indoor humidity at 45–55% improves heat transfer so your trimmed schedule "
                        "feels just as warm while cutting peak gas burn."
                    ),
                    "action_type": "apply_schedule",
                    "action_label": f"Trim Schedule to {new_peak:.1f}°C",
                    "action_payload": {
                        "boiler_mode": "indoor_temp",
                        "use_schedule": True,
                        "schedule": trimmed_sched,
                    },
                })
    elif boiler_mode == "indoor_temp" and not use_schedule and target_temp_c > 19.0:
        new_target = round(max(18.5, target_temp_c - 1.5), 1)
        trim_day_m3 = _day_heating_m3(
            outdoor_temp_c=cycle_avg_temp_c,
            wind_kmh=avg_wind,
            area_pyeong=area_pyeong,
            building_type=building_type,
            boiler_mode="indoor_temp",
            target_temp_c=new_target,
            ondol_temp_c=ondol_temp_c,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            heating_hours=heating_hours,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older_building,
            use_schedule=False,
            raw_schedule={},
        )
        trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
        trim_savings = base_bill_krw - trim_bill_krw
        if trim_savings >= 1000:
            tips.append({
                "id": "trim_indoor_temp",
                "title": f"Trim Target Temp by 1.5°C ({target_temp_c:.1f}°C → {new_target:.1f}°C) + Humidifier",
                "savings_krw": int(trim_savings),
                "comfort_note": (
                    "Keeping indoor humidity at 45–55% (humidifier or damp towel) improves heat transfer "
                    f"and makes {new_target:.1f}°C feel as warm as {target_temp_c:.1f}°C."
                ),
                "action_type": "apply_constant",
                "action_label": f"Set {new_target:.1f}°C",
                "action_payload": {
                    "boiler_mode": "indoor_temp",
                    "use_schedule": False,
                    "target_temp_c": new_target,
                },
            })
    elif boiler_mode == "ondol_water" and ondol_temp_c > 48.0:
        new_ondol = max(45.0, ondol_temp_c - 5.0)
        trim_day_m3 = _day_heating_m3(
            outdoor_temp_c=cycle_avg_temp_c,
            wind_kmh=avg_wind,
            area_pyeong=area_pyeong,
            building_type=building_type,
            boiler_mode="ondol_water",
            target_temp_c=target_temp_c,
            ondol_temp_c=new_ondol,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            heating_hours=heating_hours,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older_building,
            use_schedule=False,
            raw_schedule={},
        )
        trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
        trim_savings = base_bill_krw - trim_bill_krw
        if trim_savings >= 1000:
            tips.append({
                "id": "trim_ondol_temp",
                "title": f"Lower Floor Water Temp from {ondol_temp_c:.0f}°C → {new_ondol:.0f}°C",
                "savings_krw": int(trim_savings),
                "comfort_note": (
                    "Once the floor slab is warm, 48–55°C water maintains steady radiant floor heat "
                    "without overheating the room."
                ),
                "action_type": "apply_ondol",
                "action_label": f"Set Ondol {new_ondol:.0f}°C",
                "action_payload": {
                    "boiler_mode": "ondol_water",
                    "ondol_temp_c": new_ondol,
                },
            })

    # 2. Gentle Setback 24h Schedule (keeps concrete slab warm during daytime/night)
    ref_warm = peak_sched_temp if use_schedule else target_temp_c
    warm_temp = round(min(22.0, max(19.5, ref_warm - 1.0)), 1)
    setback_temp = round(max(18.0, warm_temp - 2.5), 1)
    night_temp = round(max(18.5, warm_temp - 2.0), 1)
    smart_schedule = {
        "morning": {"temp_c": warm_temp, "active": True},
        "daytime": {"temp_c": setback_temp, "active": True},
        "evening": {"temp_c": warm_temp, "active": True},
        "night": {"temp_c": night_temp, "active": True},
    }
    sched_day_m3 = _day_heating_m3(
        outdoor_temp_c=cycle_avg_temp_c,
        wind_kmh=avg_wind,
        area_pyeong=area_pyeong,
        building_type=building_type,
        boiler_mode="indoor_temp",
        target_temp_c=warm_temp,
        ondol_temp_c=ondol_temp_c,
        away_interval_min=away_interval_min,
        away_cycle_hours=away_cycle_hours,
        heating_hours=heating_hours,
        is_top_or_corner=is_top_or_corner,
        is_older=is_older_building,
        use_schedule=True,
        raw_schedule=smart_schedule,
    )
    sched_bill_krw = _calculate_krw(cycle_hw_m3 + sched_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
    sched_savings = base_bill_krw - sched_bill_krw

    if sched_savings >= 1500:
        tips.append({
            "id": "smart_setback_schedule",
            "title": (
                f"Replace 'Away' Off-Blocks with a {setback_temp:.1f}°C Gentle Setback"
                if has_deep_away
                else f"Switch to a 24h Gentle Setback Schedule ({warm_temp:.1f}°C / {setback_temp:.1f}°C)"
            ),
            "savings_krw": int(sched_savings),
            "comfort_note": (
                "Keeps your concrete floor warm during work and sleep hours instead of overheating overnight "
                "or letting the slab go cold."
            ),
            "action_type": "apply_schedule",
            "action_label": "Apply Smart Schedule",
            "action_payload": {
                "boiler_mode": "indoor_temp",
                "use_schedule": True,
                "schedule": smart_schedule,
            },
        })

    # 3. Draft-Proofing (뽁뽁이/문풍지) & Ondol Mode Switch for Drafty/Older/Corner Homes
    if is_top_or_corner or is_older_building or building_type in {"oneroom", "villa"}:
        sealed_day_m3 = _day_heating_m3(
            outdoor_temp_c=cycle_avg_temp_c,
            wind_kmh=min(12.0, avg_wind),
            area_pyeong=area_pyeong,
            building_type=building_type,
            boiler_mode=boiler_mode,
            target_temp_c=target_temp_c,
            ondol_temp_c=ondol_temp_c,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            heating_hours=heating_hours,
            is_top_or_corner=False,
            is_older=is_older_building,
            use_schedule=use_schedule,
            raw_schedule=norm_sched,
        )
        draft_m3_saved = max(base_heat_m3 * 0.11, (base_heat_m3 - sealed_day_m3 * billing_cycle_days))
        draft_bill_krw = _calculate_krw(max(0.0, base_total_m3 - draft_m3_saved), tariff)["total_bill_krw"]
        draft_savings = base_bill_krw - draft_bill_krw
        if draft_savings >= 1000:
            mode_hint = (
                " Also switch to 온돌 (Floor Water) mode so cold window drafts don't trick your wall thermostat."
                if boiler_mode == "indoor_temp"
                else ""
            )
            tips.append({
                "id": "draft_proofing",
                "title": "Seal Window & Front-Door Drafts (에어캡 · 문풍지 · 암막커튼)",
                "savings_krw": int(draft_savings),
                "comfort_note": (
                    f"Stops cold Seoul wind ({avg_wind:.0f} km/h) from chilling the floor perimeter.{mode_hint}"
                ),
                "action_type": "apply_ondol" if boiler_mode == "indoor_temp" else "info",
                "action_label": "Switch to 온돌 55°C" if boiler_mode == "indoor_temp" else None,
                "action_payload": {"boiler_mode": "ondol_water", "ondol_temp_c": 55.0} if boiler_mode == "indoor_temp" else None,
            })

    # 4. K-Gas Cashback (도시가스 절약 캐시백) Bonus Estimate
    best_opt_day = min(base_day_heat, sched_day_m3)
    best_m3_saved = max(0.0, (base_day_heat - best_opt_day) * billing_cycle_days)
    if best_m3_saved >= 2.0:
        cashback_krw = int(round(best_m3_saved * 100, -1))
        tips.append({
            "id": "k_gas_cashback",
            "title": "Enroll in K-Gas Cashback (도시가스 절약 캐시백)",
            "savings_krw": cashback_krw,
            "comfort_note": (
                "Cutting winter usage by 3%+ vs. last winter earns ₩50–200/m³ back in cash on top of lower bills."
            ),
            "action_type": "info",
            "action_label": None,
            "action_payload": None,
        })

    tips.sort(key=lambda x: x["savings_krw"], reverse=True)
    return tips[:3]


def estimate_gas_bill(payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}

    area_pyeong = _safe_float(data.get("area_pyeong"), default=8.0, min_val=2.0, max_val=120.0)
    building_type = _safe_choice(data.get("building_type"), set(BUILDING_HEATING_FACTOR.keys()), "oneroom")
    boiler_mode = _safe_choice(data.get("boiler_mode"), {"indoor_temp", "ondol_water", "away"}, "indoor_temp")
    stove_type = _safe_choice(data.get("stove_type"), {"gas", "induction"}, "gas")
    target_temp_c = _safe_float(data.get("target_temp_c"), default=22.0, min_val=15.0, max_val=30.0)
    ondol_temp_c = _safe_float(data.get("ondol_temp_c"), default=60.0, min_val=40.0, max_val=80.0)
    away_interval_min = _safe_float(data.get("away_interval_min"), default=20.0, min_val=10.0, max_val=60.0)
    away_cycle_hours = _safe_float(data.get("away_cycle_hours"), default=3.0, min_val=1.0, max_val=6.0)
    heating_hours = _safe_float(data.get("heating_hours_per_day"), default=12.0, min_val=0.0, max_val=24.0)
    is_top_or_corner = _safe_bool(data.get("is_top_or_corner_unit"), default=False)
    is_older_building = _safe_bool(data.get("is_older_building"), default=False)
    household_size = _safe_int(data.get("household_size"), default=1, min_val=1, max_val=10)
    month = _safe_choice(data.get("month"), set(MONTH_TO_NUM.keys()), "jan")
    billing_cycle_days = _safe_int(data.get("billing_cycle_days"), default=30, min_val=15, max_val=45)
    use_schedule = _safe_bool(data.get("use_schedule"), default=False)
    raw_schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}

    tariff = {
        "unit_rate_krw_per_mj": _safe_float(data.get("unit_rate_krw_per_mj"), DEFAULT_TARIFF["unit_rate_krw_per_mj"], 1.0, 200.0),
        "base_charge_krw": _safe_float(data.get("base_charge_krw"), DEFAULT_TARIFF["base_charge_krw"], 0.0, 50000.0),
        "avg_calorific_mj_per_m3": _safe_float(data.get("avg_calorific_mj_per_m3"), DEFAULT_TARIFF["avg_calorific_mj_per_m3"], 20.0, 60.0),
        "correction_factor": _safe_float(data.get("correction_factor"), DEFAULT_TARIFF["correction_factor"], 0.8, 1.2),
        "vat_rate": _safe_float(data.get("vat_rate"), DEFAULT_TARIFF["vat_rate"], 0.0, 0.5),
    }

    baseline_per_person = 0.16 if stove_type == "induction" else 0.22

    weather_info = fetch_seoul_weather_range(data.get("billing_start_date"))
    daily_weather = weather_info["daily"]

    elapsed_heating_m3 = 0.0
    elapsed_hw_m3 = 0.0
    daily_ledger: list[dict[str, Any]] = []
    all_cycle_temps: list[float] = []

    for row in daily_weather:
        d_temp = float(row["avg_temp_c"])
        d_wind = float(row.get("wind_kmh", 14.0))
        all_cycle_temps.append(d_temp)
        hdd_norm = daily_hdd(d_temp) / 18.0
        hw_day = household_size * baseline_per_person * (1.0 + 0.15 * hdd_norm)
        heat_day = _day_heating_m3(
            outdoor_temp_c=d_temp,
            wind_kmh=d_wind,
            area_pyeong=area_pyeong,
            building_type=building_type,
            boiler_mode=boiler_mode,
            target_temp_c=target_temp_c,
            ondol_temp_c=ondol_temp_c,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            heating_hours=heating_hours,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older_building,
            use_schedule=use_schedule,
            raw_schedule=raw_schedule,
        )
        day_total = hw_day + heat_day
        day_cost = _calculate_krw(day_total, tariff)["total_bill_krw"] - int(
            round(tariff["base_charge_krw"] * (1.0 + tariff["vat_rate"]), -1)
        )

        elapsed_heating_m3 += heat_day
        elapsed_hw_m3 += hw_day
        daily_ledger.append({
            "date": row["date"],
            "avg_temp_c": d_temp,
            "wind_kmh": d_wind,
            "heating_m3": round(heat_day, 3),
            "total_m3": round(day_total, 3),
            "approx_usage_krw": max(0, int(day_cost)),
        })

    days_elapsed = max(1, len(daily_weather))
    accrued_to_date_m3 = elapsed_heating_m3 + elapsed_hw_m3
    accrued_to_date_billing = _calculate_krw(accrued_to_date_m3, tariff)

    month_num = MONTH_TO_NUM.get(month, 1)
    fallback_temp = MONTHLY_FALLBACK_TEMP_C.get(month_num, 2.5)
    fallback_wind = MONTHLY_FALLBACK_WIND_KMH.get(month_num, 14.0)
    remaining_days = max(0, billing_cycle_days - days_elapsed)

    forecast_daily = weather_info.get("forecast_daily", [])
    as_of_dt = date.fromisoformat(weather_info["as_of_date"])

    rem_heating_m3 = 0.0
    rem_hw_m3 = 0.0
    future_temps: list[float] = []
    live_forecast_days_used = 0

    for offset in range(1, remaining_days + 1):
        fut_dt = as_of_dt + timedelta(days=offset)
        if offset <= len(forecast_daily):
            f_row = forecast_daily[offset - 1]
            f_temp = float(f_row["avg_temp_c"])
            f_wind = float(f_row.get("wind_kmh", fallback_wind))
            live_forecast_days_used += 1
        else:
            f_temp = MONTHLY_FALLBACK_TEMP_C.get(fut_dt.month, fallback_temp)
            f_wind = MONTHLY_FALLBACK_WIND_KMH.get(fut_dt.month, fallback_wind)

        future_temps.append(f_temp)
        all_cycle_temps.append(f_temp)
        f_hdd_norm = daily_hdd(f_temp) / 18.0
        rem_hw_m3 += household_size * baseline_per_person * (1.0 + 0.15 * f_hdd_norm)
        rem_heating_m3 += _day_heating_m3(
            outdoor_temp_c=f_temp,
            wind_kmh=f_wind,
            area_pyeong=area_pyeong,
            building_type=building_type,
            boiler_mode=boiler_mode,
            target_temp_c=target_temp_c,
            ondol_temp_c=ondol_temp_c,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            heating_hours=heating_hours,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older_building,
            use_schedule=use_schedule,
            raw_schedule=raw_schedule,
        )

    forecast_avg_temp_c = (
        round(sum(future_temps) / len(future_temps), 1)
        if future_temps
        else round(fallback_temp, 1)
    )
    cycle_avg_temp_c = (
        round(sum(all_cycle_temps) / len(all_cycle_temps), 1)
        if all_cycle_temps
        else forecast_avg_temp_c
    )

    projected_total_m3 = accrued_to_date_m3 + rem_hw_m3 + rem_heating_m3
    projected_heating_m3 = elapsed_heating_m3 + rem_heating_m3
    projected_hw_m3 = elapsed_hw_m3 + rem_hw_m3
    projected_billing = _calculate_krw(projected_total_m3, tariff)

    schedule_blocks_out = []
    sched_heat_sum = 0.0
    prev_t: Optional[float] = None
    away_acc = 0.0
    avg_wind = weather_info.get("elapsed_avg_wind_kmh", fallback_wind)

    for block in DEFAULT_SCHEDULE_BLOCKS:
        bid = block["id"]
        b_data = raw_schedule.get(bid) if isinstance(raw_schedule.get(bid), dict) else {}
        b_temp = _safe_float(b_data.get("temp_c"), default=block["default_temp_c"], min_val=15.0, max_val=30.0)
        b_active = _safe_bool(b_data.get("active"), default=block["default_active"])
        rec = _slab_recovery_penalty(
            b_active=b_active,
            b_temp=b_temp,
            prev_temp=prev_t,
            away_hours_accumulated=away_acc,
            building_type=building_type,
        )
        b_daily = ML_ENGINE.predict_daily_heating_m3(
            area_pyeong=area_pyeong,
            building_type=building_type,
            outdoor_temp_c=cycle_avg_temp_c + block["outdoor_offset_c"],
            boiler_mode=boiler_mode,
            target_temp_c=b_temp,
            ondol_temp_c=ondol_temp_c,
            duty_cycle=block["hours"] / 24.0,
            is_top_or_corner=is_top_or_corner,
            is_older=is_older_building,
            wind_kmh=avg_wind,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            active=b_active,
            recovery_penalty=rec,
        )
        b_month_m3 = b_daily * billing_cycle_days
        sched_heat_sum += b_month_m3
        b_cost = round(
            b_month_m3
            * tariff["correction_factor"]
            * tariff["avg_calorific_mj_per_m3"]
            * tariff["unit_rate_krw_per_mj"]
            * (1.0 + tariff["vat_rate"])
        )
        schedule_blocks_out.append({
            "id": bid,
            "label": block["label"],
            "window": block["window"],
            "temp_c": b_temp,
            "active": b_active,
            "monthly_m3": round(b_month_m3, 2),
            "monthly_heating_cost_krw": int(b_cost),
        })
        if b_active:
            prev_t = b_temp
            away_acc = 0.0
        else:
            prev_t = min(b_temp, 16.0)
            away_acc += block["hours"]

    const_day_heat = _day_heating_m3(
        outdoor_temp_c=cycle_avg_temp_c,
        wind_kmh=avg_wind,
        area_pyeong=area_pyeong,
        building_type=building_type,
        boiler_mode=boiler_mode,
        target_temp_c=target_temp_c,
        ondol_temp_c=ondol_temp_c,
        away_interval_min=away_interval_min,
        away_cycle_hours=away_cycle_hours,
        heating_hours=heating_hours,
        is_top_or_corner=is_top_or_corner,
        is_older=is_older_building,
        use_schedule=False,
        raw_schedule={},
    )
    const_bill_krw = _calculate_krw(projected_hw_m3 + const_day_heat * billing_cycle_days, tariff)["total_bill_krw"]
    sched_bill_krw = _calculate_krw(projected_hw_m3 + sched_heat_sum, tariff)["total_bill_krw"]

    recommendations = _build_savings_recommendations(
        cycle_avg_temp_c=cycle_avg_temp_c,
        avg_wind=avg_wind,
        billing_cycle_days=billing_cycle_days,
        baseline_per_person=baseline_per_person,
        household_size=household_size,
        area_pyeong=area_pyeong,
        building_type=building_type,
        boiler_mode=boiler_mode,
        target_temp_c=target_temp_c,
        ondol_temp_c=ondol_temp_c,
        away_interval_min=away_interval_min,
        away_cycle_hours=away_cycle_hours,
        heating_hours=heating_hours,
        is_top_or_corner=is_top_or_corner,
        is_older_building=is_older_building,
        use_schedule=use_schedule,
        raw_schedule=raw_schedule,
        tariff=tariff,
    )

    return {
        "mode": "schedule" if use_schedule else "constant",
        "boiler_mode": boiler_mode,
        "ml_engine": {
            "model": "CalibratedThermalPhysics(Slab+Wind)",
            "sklearn_available": True,
        },
        "weather_tracker": {
            "billing_start_date": weather_info["billing_start_date"],
            "as_of_date": weather_info["as_of_date"],
            "days_tracked": len(daily_weather),
            "remaining_days": remaining_days,
            "live_forecast_days_used": live_forecast_days_used,
            "elapsed_avg_temp_c": weather_info["elapsed_avg_temp_c"],
            "elapsed_avg_wind_kmh": avg_wind,
            "forecast_avg_temp_c": forecast_avg_temp_c,
            "cumulative_hdd": weather_info["cumulative_hdd"],
            "source": weather_info["source"],
            "forecast_live": weather_info.get("forecast_live", False),
            "accrued_to_date_m3": round(accrued_to_date_m3, 2),
            "accrued_to_date_krw": accrued_to_date_billing["total_bill_krw"],
            "daily_ledger": daily_ledger,
        },
        "estimated_m3": round(projected_total_m3, 2),
        "breakdown_m3": {
            "hot_water_and_cooking": round(projected_hw_m3, 2),
            "space_heating": round(projected_heating_m3, 2),
        },
        "billing": projected_billing,
        "recommendations": recommendations,
        "comparison": {
            "constant_temp_c": target_temp_c,
            "schedule_savings_krw": const_bill_krw - sched_bill_krw,
            "schedule_blocks": schedule_blocks_out,
        },
        "assumptions": {
            "seoul_avg_temp_c": forecast_avg_temp_c,
        },
    }


@app.get("/")
def index():
    default_start = date.today().replace(day=1).isoformat()
    return render_template("index.html", default_start_date=default_start)


@app.post("/api/estimate")
def api_estimate():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = request.form.to_dict() if request.form else {}
    return jsonify(estimate_gas_bill(body))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)