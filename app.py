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

# 2026 Residential City Gas Tariffs (주택용 취사·난방 단가, VAT excluded)
REGION_TARIFFS = {
    "seoul": {
        "unit_rate_krw_per_mj": 22.5268,
        "base_charge_krw": 1250.0,
        "avg_calorific_mj_per_m3": 43.1,
        "correction_factor": 0.998,
        "vat_rate": 0.10,
    },
    "gyeonggi": {
        "unit_rate_krw_per_mj": 22.6226,
        "base_charge_krw": 1250.0,
        "avg_calorific_mj_per_m3": 43.1,
        "correction_factor": 0.998,
        "vat_rate": 0.10,
    },
    "incheon": {
        "unit_rate_krw_per_mj": 22.5084,
        "base_charge_krw": 1000.0,
        "avg_calorific_mj_per_m3": 43.1,
        "correction_factor": 0.998,
        "vat_rate": 0.10,
    },
}

MONTH_TO_NUM = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
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

WEEKDAY_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


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
    orientation: str,
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
            orientation=orientation,
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
            orientation=orientation,
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


def _build_forecast_action_plan(
    *,
    as_of_dt: date,
    forecast_daily: list[dict[str, Any]],
    fallback_temp: float,
    fallback_wind: float,
    boiler_mode: str,
    is_older_building: bool,
    is_top_or_corner: bool,
) -> dict[str, Any]:
    """Build a 7-day forecast strip and concrete day-by-day actions for the coming week."""
    seven_days: list[dict[str, Any]] = []
    for offset in range(1, 8):
        fut_dt = as_of_dt + timedelta(days=offset)
        if offset <= len(forecast_daily):
            row = forecast_daily[offset - 1]
            avg_t = float(row["avg_temp_c"])
            min_t = float(row.get("min_temp_c", avg_t - 4.0))
            max_t = float(row.get("max_temp_c", avg_t + 4.0))
            wind_v = float(row.get("wind_kmh", fallback_wind))
        else:
            avg_t = MONTHLY_FALLBACK_TEMP_C.get(fut_dt.month, fallback_temp)
            min_t = round(avg_t - 4.0, 1)
            max_t = round(avg_t + 4.0, 1)
            wind_v = MONTHLY_FALLBACK_WIND_KMH.get(fut_dt.month, fallback_wind)

        if min_t <= 0.0:
            regime = "freeze"
        elif avg_t <= 5.0:
            regime = "cold"
        elif wind_v >= 18.0:
            regime = "windy"
        else:
            regime = "mild"

        wd = fut_dt.weekday()
        seven_days.append({
            "date": fut_dt.isoformat()[5:],  # MM-DD
            "full_date": fut_dt.isoformat(),
            "day_en": WEEKDAY_EN[wd],
            "day_ko": WEEKDAY_KO[wd],
            "min_c": round(min_t, 1),
            "max_c": round(max_t, 1),
            "avg_c": round(avg_t, 1),
            "wind_kmh": round(wind_v, 0),
            "regime": regime,
        })

    actions: list[dict[str, str]] = []

    # 1. Detect incoming cold snap or sub-zero overnight lows
    freeze_days = [d for d in seven_days if d["min_c"] <= 0.0]
    biggest_drop = 0.0
    drop_day: Optional[dict[str, Any]] = None
    for idx in range(1, len(seven_days)):
        drop = seven_days[idx - 1]["avg_c"] - seven_days[idx]["avg_c"]
        if drop > biggest_drop:
            biggest_drop = drop
            drop_day = seven_days[idx]

    if freeze_days:
        first_f = freeze_days[0]
        lowest_d = min(freeze_days, key=lambda x: x["min_c"])
        actions.append({
            "badge_en": f"FREEZE · {first_f['day_en']} ({first_f['date']})",
            "badge_ko": f"영하권 · {first_f['day_ko']} ({first_f['date']})",
            "tone": "cold",
            "text_en": (
                f"Overnight lows drop to {lowest_d['min_c']:.0f}°C starting {first_f['day_en']}. "
                "Don't turn the boiler off when leaving—hold an 18.5°C setback and keep power plugged in for pipe freeze protection."
            ),
            "text_ko": (
                f"{first_f['day_ko']}요일({first_f['date']})부터 최저 {lowest_d['min_c']:.0f}°C까지 떨어집니다. "
                "단기 외출 시 보일러를 끄지 말고 18.5°C 미온 유지로 설정하고, 동파 방지를 위해 전원 코드를 절대 뽑지 마세요."
            ),
        })
    elif biggest_drop >= 4.0 and drop_day is not None:
        actions.append({
            "badge_en": f"TEMP DROP · {drop_day['day_en']} ({drop_day['date']})",
            "badge_ko": f"기온 급강하 · {drop_day['day_ko']} ({drop_day['date']})",
            "tone": "cold",
            "text_en": (
                f"Temperatures drop {biggest_drop:.1f}°C on {drop_day['day_en']} (low {drop_day['min_c']:.0f}°C). "
                "Close curtains right at sunset (~17:15) to trap daytime floor warmth."
            ),
            "text_ko": (
                f"{drop_day['day_ko']}요일({drop_day['date']})에 기온이 {biggest_drop:.1f}°C 급락합니다(최저 {drop_day['min_c']:.0f}°C). "
                "해 지는 오후 5시 15분경 커튼을 미리 쳐두면 낮 동안 데워진 바닥 온기를 오래 지킬 수 있습니다."
            ),
        })

    # 2. Detect windy days where drafts fool indoor thermostats
    windy_days = [d for d in seven_days if d["wind_kmh"] >= 18.0]
    if windy_days and (is_older_building or is_top_or_corner or boiler_mode == "indoor_temp"):
        w_labels_en = ", ".join(d["day_en"] for d in windy_days[:3])
        w_labels_ko = "·".join(d["day_ko"] for d in windy_days[:3])
        peak_w = max(d["wind_kmh"] for d in windy_days)
        actions.append({
            "badge_en": f"GUSTY · {w_labels_en}",
            "badge_ko": f"강풍 외풍 · {w_labels_ko}요일",
            "tone": "wind",
            "text_en": (
                f"Winds up to {peak_w:.0f} km/h on {w_labels_en}. "
                "Switch to 온돌 (53–56°C) so cold window drafts don't trigger nonstop boiler firing."
            ),
            "text_ko": (
                f"{w_labels_ko}요일 최대 {peak_w:.0f}km/h 바람이 예보되어 있습니다. "
                "창가 외풍에 실내 온도계가 속아 보일러가 과가동되지 않도록 '온돌 모드(53~56°C)'로 전환하세요."
            ),
        })

    # 3. Detect mild afternoons where daytime heating can be paused
    mild_days = [d for d in seven_days if d["max_c"] >= 11.0]
    if mild_days:
        m_labels_en = ", ".join(d["day_en"] for d in mild_days[:3])
        m_labels_ko = "·".join(d["day_ko"] for d in mild_days[:3])
        warmest = max(mild_days, key=lambda x: x["max_c"])
        actions.append({
            "badge_en": f"MILD DAY · {m_labels_en}",
            "badge_ko": f"온화한 낮 · {m_labels_ko}요일",
            "tone": "mild",
            "text_en": (
                f"Afternoon highs reach {warmest['max_c']:.0f}°C on {m_labels_en}. "
                "Pause daytime heating (09–18h) and open windows 5–10 min at 2 PM to vent moisture without chilling the floor."
            ),
            "text_ko": (
                f"{m_labels_ko}요일 낮 최고 {warmest['max_c']:.0f}°C까지 올라갑니다. "
                "낮 시간(09~18시) 난방은 끄고 오후 2시경 5~10분 짧게 환기하면 바닥 열 손실 없이 결로를 예방할 수 있습니다."
            ),
        })

    if not actions:
        actions.append({
            "badge_en": "STEADY WEEK",
            "badge_ko": "기온 안정 주간",
            "tone": "mild",
            "text_en": "Temperatures stay steady over the next 7 days—keep your 24h gentle setback schedule unchanged.",
            "text_ko": "향후 7일간 큰 기온 급변이 없으므로 현재의 24시간 미온 유지 스케줄을 그대로 유지하세요.",
        })

    return {
        "days": seven_days,
        "actions": actions[:3],
    }


def _build_savings_recommendations(
    *,
    cycle_avg_temp_c: float,
    avg_wind: float,
    billing_cycle_days: int,
    baseline_per_person: float,
    household_size: int,
    area_pyeong: float,
    building_type: str,
    orientation: str,
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
) -> tuple[list[dict[str, Any]], int, Optional[dict[str, str]]]:
    tips: list[dict[str, Any]] = []
    norm_sched = _normalized_schedule(raw_schedule)

    hdd_norm = daily_hdd(cycle_avg_temp_c) / 18.0
    cycle_hw_m3 = billing_cycle_days * household_size * baseline_per_person * (1.0 + 0.15 * hdd_norm)
    base_day_heat = _day_heating_m3(
        outdoor_temp_c=cycle_avg_temp_c,
        wind_kmh=avg_wind,
        area_pyeong=area_pyeong,
        building_type=building_type,
        orientation=orientation,
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

    active_block_temps = [v["temp_c"] for v in norm_sched.values() if v["active"]]
    peak_sched_temp = max(active_block_temps) if active_block_temps else 22.0
    has_deep_away = use_schedule and any(not v["active"] for v in norm_sched.values())

    shock_alert: Optional[dict[str, str]] = None
    effective_peak = peak_sched_temp if (boiler_mode == "indoor_temp" and use_schedule) else target_temp_c
    if boiler_mode == "indoor_temp" and effective_peak >= 23.0 and base_heat_m3 > 15.0:
        shock_alert = {
            "en": f"High Setpoint Alert ({effective_peak:.1f}°C): Every 1°C above 20°C increases gas burn exponentially (~12–15% per degree).",
            "ko": f"고온 설정 주의 ({effective_peak:.1f}°C): 실내온도를 20°C보다 1°C 높일 때마다 가스 사용량이 약 12~15%씩 급증합니다.",
        }
    elif boiler_mode == "ondol_water" and ondol_temp_c >= 68.0 and base_heat_m3 > 15.0:
        shock_alert = {
            "en": f"High Water Temp Alert ({ondol_temp_c:.0f}°C): Floor water above 65°C causes rapid boiler cycling and high bills.",
            "ko": f"난방수 고온 주의 ({ondol_temp_c:.0f}°C): 난방수 65°C 이상 설정은 보일러 연속 가동으로 요금 폭탄의 주원인이 됩니다.",
        }

    best_behavioral_day = base_day_heat
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
                orientation=orientation,
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
            best_behavioral_day = min(best_behavioral_day, trim_day_m3)
            trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
            trim_savings = base_bill_krw - trim_bill_krw
            new_peak = max((v["temp_c"] for v in trimmed_sched.values() if v["active"]), default=peak_sched_temp)
            if trim_savings >= 1000:
                tips.append({
                    "id": "trim_schedule_temps",
                    "title_en": f"Trim Active Schedule by 1.5°C (Peak {peak_sched_temp:.1f}°C → {new_peak:.1f}°C) + Humidifier",
                    "title_ko": f"스케줄 온도 1.5°C 낮추기 (최고 {peak_sched_temp:.1f}°C → {new_peak:.1f}°C) + 가습기",
                    "savings_krw": int(trim_savings),
                    "note_en": "Keeping humidity at 45–55% improves air heat transfer so your trimmed schedule feels equally warm.",
                    "note_ko": "실내 습도를 45~55%로 유지하면 공기 대류가 빨라져 1.5°C 낮춰도 체감 온도는 동일하게 따뜻합니다.",
                    "label_en": f"Trim to {new_peak:.1f}°C",
                    "label_ko": f"{new_peak:.1f}°C로 적용",
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
            orientation=orientation,
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
        best_behavioral_day = min(best_behavioral_day, trim_day_m3)
        trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
        trim_savings = base_bill_krw - trim_bill_krw
        if trim_savings >= 1000:
            tips.append({
                "id": "trim_indoor_temp",
                "title_en": f"Trim Target Temp by 1.5°C ({target_temp_c:.1f}°C → {new_target:.1f}°C) + Humidifier",
                "title_ko": f"희망 실내온도 1.5°C 낮추기 ({target_temp_c:.1f}°C → {new_target:.1f}°C) + 가습기",
                "savings_krw": int(trim_savings),
                "note_en": f"45–55% humidity makes {new_target:.1f}°C feel as warm as {target_temp_c:.1f}°C while cutting peak gas burn.",
                "note_ko": f"습도 45~55%를 유지하면 {new_target:.1f}°C로 설정해도 {target_temp_c:.1f}°C처럼 따뜻하게 지낼 수 있습니다.",
                "label_en": f"Set {new_target:.1f}°C",
                "label_ko": f"{new_target:.1f}°C 적용",
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
            orientation=orientation,
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
        best_behavioral_day = min(best_behavioral_day, trim_day_m3)
        trim_bill_krw = _calculate_krw(cycle_hw_m3 + trim_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
        trim_savings = base_bill_krw - trim_bill_krw
        if trim_savings >= 1000:
            tips.append({
                "id": "trim_ondol_temp",
                "title_en": f"Lower Floor Water Temp ({ondol_temp_c:.0f}°C → {new_ondol:.0f}°C)",
                "title_ko": f"온돌 난방수 온도 5°C 낮추기 ({ondol_temp_c:.0f}°C → {new_ondol:.0f}°C)",
                "savings_krw": int(trim_savings),
                "note_en": "Once the floor slab is warm, 48–55°C water maintains steady radiant floor heat without overheating.",
                "note_ko": "바닥이 한 번 데워진 후에는 48~55°C 난방수만으로도 복사열이 오래 유지되어 가스비를 크게 줄입니다.",
                "label_en": f"Set Ondol {new_ondol:.0f}°C",
                "label_ko": f"온돌 {new_ondol:.0f}°C 적용",
                "action_payload": {
                    "boiler_mode": "ondol_water",
                    "ondol_temp_c": new_ondol,
                },
            })

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
        orientation=orientation,
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
    best_behavioral_day = min(best_behavioral_day, sched_day_m3)
    sched_bill_krw = _calculate_krw(cycle_hw_m3 + sched_day_m3 * billing_cycle_days, tariff)["total_bill_krw"]
    sched_savings = base_bill_krw - sched_bill_krw

    if sched_savings >= 1500:
        tips.append({
            "id": "smart_setback_schedule",
            "title_en": (
                f"Replace 'Away' Off-Blocks with {setback_temp:.1f}°C Gentle Setback"
                if has_deep_away
                else f"Switch to 24h Gentle Setback Schedule ({warm_temp:.1f}°C / {setback_temp:.1f}°C)"
            ),
            "title_ko": (
                f"외출(끄기) 대신 {setback_temp:.1f}°C 미온 유지 스케줄 적용"
                if has_deep_away
                else f"24시간 스마트 절약 스케줄 전환 ({warm_temp:.1f}°C / {setback_temp:.1f}°C)"
            ),
            "savings_krw": int(sched_savings),
            "note_en": "Prevents your concrete slab from going cold while you're away or asleep, avoiding high-burn reheating spikes.",
            "note_ko": "외출·취침 시 바닥이 완전히 식는 것을 막아 귀가 후 보일러가 폭주하며 재가열하는 가스 낭비를 없앱니다.",
            "label_en": "Apply Smart Schedule",
            "label_ko": "스마트 스케줄 적용",
            "action_payload": {
                "boiler_mode": "indoor_temp",
                "use_schedule": True,
                "schedule": smart_schedule,
            },
        })

    draft_m3_saved = 0.0
    if is_top_or_corner or is_older_building or orientation == "north_ground" or building_type in {"oneroom", "villa"}:
        sealed_day_m3 = _day_heating_m3(
            outdoor_temp_c=cycle_avg_temp_c,
            wind_kmh=min(12.0, avg_wind),
            area_pyeong=area_pyeong,
            building_type=building_type,
            orientation="east_west" if orientation == "north_ground" else orientation,
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
            tips.append({
                "id": "draft_proofing",
                "title_en": "Seal Window & Door Drafts (에어캡 · 문풍지 · 암막커튼)",
                "title_ko": "창문 에어캡(뽁뽁이)·문풍지·암막커튼으로 웃풍 차단",
                "savings_krw": int(draft_savings),
                "note_en": (
                    f"Blocks cold wind ({avg_wind:.0f} km/h) from chilling the floor. Switch to 온돌 mode so window drafts don't fool your thermostat."
                    if boiler_mode == "indoor_temp"
                    else f"Blocks cold wind ({avg_wind:.0f} km/h) from chilling your floor slab and perimeter."
                ),
                "label_en": "Switch to 온돌 55°C" if boiler_mode == "indoor_temp" else None,
                "label_ko": "온돌 55°C 전환" if boiler_mode == "indoor_temp" else None,
                "action_payload": {"boiler_mode": "ondol_water", "ondol_temp_c": 55.0} if boiler_mode == "indoor_temp" else None,
            })

    behavioral_m3_saved = max(0.0, (base_day_heat - best_behavioral_day) * billing_cycle_days)
    total_stack_m3_saved = behavioral_m3_saved + draft_m3_saved
    if total_stack_m3_saved >= 2.0:
        cashback_krw = int(round(total_stack_m3_saved * 100, -1))
        tips.append({
            "id": "k_gas_cashback",
            "title_en": "Enroll in K-Gas Cashback (도시가스 절약 캐시백)",
            "title_ko": "한국가스공사 '도시가스 절약 캐시백' 신청하기",
            "savings_krw": cashback_krw,
            "note_en": "Cutting winter usage by 3%+ vs. last winter earns ₩50–200/m³ back in cash on top of lower bills.",
            "note_ko": "전년 동절기 대비 사용량을 3% 이상 줄이면 요금 절감과 별도로 m³당 50~200원을 현금으로 환급받습니다.",
            "label_en": None,
            "label_ko": None,
            "action_payload": None,
        })

    tips.sort(key=lambda x: x["savings_krw"], reverse=True)
    top_tips = tips[:3]

    combined_m3 = max(0.0, base_total_m3 - total_stack_m3_saved)
    combined_bill = _calculate_krw(combined_m3, tariff)["total_bill_krw"]
    cashback_bonus = int(round(total_stack_m3_saved * 100, -1)) if total_stack_m3_saved >= 2.0 else 0
    combined_savings_krw = max(
        (top_tips[0]["savings_krw"] if top_tips else 0),
        (base_bill_krw - combined_bill) + cashback_bonus,
    )

    return top_tips, int(combined_savings_krw), shock_alert


def estimate_gas_bill(payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}

    region = _safe_choice(data.get("region"), set(REGION_TARIFFS.keys()), "seoul")
    reg_defaults = REGION_TARIFFS[region]

    area_pyeong = _safe_float(data.get("area_pyeong"), default=8.0, min_val=2.0, max_val=120.0)
    building_type = _safe_choice(data.get("building_type"), set(BUILDING_HEATING_FACTOR.keys()), "oneroom")
    orientation = _safe_choice(data.get("orientation"), {"south", "east_west", "north_ground"}, "east_west")
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
    month = _safe_choice(data.get("month"), set(MONTH_TO_NUM.keys()), "nov")
    billing_cycle_days = _safe_int(data.get("billing_cycle_days"), default=30, min_val=15, max_val=45)
    use_schedule = _safe_bool(data.get("use_schedule"), default=False)
    raw_schedule = data.get("schedule") if isinstance(data.get("schedule"), dict) else {}

    tariff = {
        "unit_rate_krw_per_mj": _safe_float(data.get("unit_rate_krw_per_mj"), reg_defaults["unit_rate_krw_per_mj"], 1.0, 200.0),
        "base_charge_krw": _safe_float(data.get("base_charge_krw"), reg_defaults["base_charge_krw"], 0.0, 50000.0),
        "avg_calorific_mj_per_m3": _safe_float(data.get("avg_calorific_mj_per_m3"), reg_defaults["avg_calorific_mj_per_m3"], 20.0, 60.0),
        "correction_factor": _safe_float(data.get("correction_factor"), reg_defaults["correction_factor"], 0.8, 1.2),
        "vat_rate": _safe_float(data.get("vat_rate"), reg_defaults["vat_rate"], 0.0, 0.5),
    }

    baseline_per_person = 0.16 if stove_type == "induction" else 0.22

    weather_info = fetch_seoul_weather_range(data.get("billing_start_date"), region=region)
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
            orientation=orientation,
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

    month_num = MONTH_TO_NUM.get(month, 11)
    fallback_temp = MONTHLY_FALLBACK_TEMP_C.get(month_num, 5.0)
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
            orientation=orientation,
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
            orientation=orientation,
            wind_kmh=avg_wind,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            active=b_active,
            recovery_penalty=rec,
        )
        b_month_m3 = b_daily * billing_cycle_days
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

    recommendations, combined_savings_krw, shock_alert = _build_savings_recommendations(
        cycle_avg_temp_c=cycle_avg_temp_c,
        avg_wind=avg_wind,
        billing_cycle_days=billing_cycle_days,
        baseline_per_person=baseline_per_person,
        household_size=household_size,
        area_pyeong=area_pyeong,
        building_type=building_type,
        orientation=orientation,
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

    forecast_plan = _build_forecast_action_plan(
        as_of_dt=as_of_dt,
        forecast_daily=forecast_daily,
        fallback_temp=fallback_temp,
        fallback_wind=fallback_wind,
        boiler_mode=boiler_mode,
        is_older_building=is_older_building,
        is_top_or_corner=is_top_or_corner,
    )

    return {
        "region": region,
        "region_tariffs": REGION_TARIFFS,
        "mode": "schedule" if use_schedule else "constant",
        "boiler_mode": boiler_mode,
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
        "forecast_plan": forecast_plan,
        "combined_savings_krw": combined_savings_krw,
        "shock_alert": shock_alert,
        "comparison": {
            "schedule_blocks": schedule_blocks_out,
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