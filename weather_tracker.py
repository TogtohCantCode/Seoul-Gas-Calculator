from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any, Optional

REGION_COORDS = {
    "seoul": (37.5665, 126.9780),
    "gyeonggi": (37.2636, 127.0286),  # Suwon / Gyeonggi central
    "incheon": (37.4563, 126.7052),
}

MONTHLY_FALLBACK_TEMP_C = {
    1: -1.8,
    2: 1.2,
    3: 6.5,
    4: 13.0,
    5: 18.5,
    6: 22.8,
    7: 25.5,
    8: 26.2,
    9: 21.8,
    10: 15.2,
    11: 7.5,
    12: 1.0,
}

MONTHLY_FALLBACK_WIND_KMH = {
    1: 15.0,
    2: 15.5,
    3: 16.0,
    4: 15.0,
    5: 13.5,
    6: 12.5,
    7: 12.0,
    8: 12.0,
    9: 11.5,
    10: 12.0,
    11: 13.5,
    12: 14.5,
}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes


def resolve_billing_start_date(raw_start: Any, today: Optional[date] = None) -> date:
    ref_today = today or date.today()
    if isinstance(raw_start, date) and not isinstance(raw_start, datetime):
        start_dt = raw_start
    elif isinstance(raw_start, str) and raw_start.strip():
        try:
            start_dt = date.fromisoformat(raw_start.strip()[:10])
        except ValueError:
            start_dt = date(ref_today.year, ref_today.month, 1)
    else:
        start_dt = date(ref_today.year, ref_today.month, 1)

    if start_dt > ref_today:
        start_dt = date(ref_today.year, ref_today.month, 1)
    earliest = ref_today - timedelta(days=45)
    return max(earliest, start_dt)


def _fallback_series(start_dt: date, end_dt: date) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    cur = start_dt
    while cur <= end_dt:
        base_t = MONTHLY_FALLBACK_TEMP_C.get(cur.month, 5.0)
        base_w = MONTHLY_FALLBACK_WIND_KMH.get(cur.month, 14.0)
        days.append({
            "date": cur.isoformat(),
            "avg_temp_c": base_t,
            "min_temp_c": round(base_t - 4.0, 1),
            "max_temp_c": round(base_t + 4.0, 1),
            "wind_kmh": base_w,
            "source": "fallback",
        })
        cur += timedelta(days=1)
    return days


def _parse_open_meteo_daily(payload: Any, source_label: str) -> list[dict[str, Any]]:
    daily = payload.get("daily") if isinstance(payload, dict) else None
    times = daily.get("time") if isinstance(daily, dict) else None
    means = daily.get("temperature_2m_mean") if isinstance(daily, dict) else None
    mins = daily.get("temperature_2m_min") if isinstance(daily, dict) else None
    maxs = daily.get("temperature_2m_max") if isinstance(daily, dict) else None
    winds = daily.get("wind_speed_10m_max") if isinstance(daily, dict) else None

    if not (isinstance(times, list) and isinstance(means, list) and len(times) == len(means) and len(times) > 0):
        return []

    rows: list[dict[str, Any]] = []
    for idx, d_str in enumerate(times):
        dt_obj = date.fromisoformat(str(d_str)[:10])
        m_val = means[idx]
        if m_val is None:
            m_val = MONTHLY_FALLBACK_TEMP_C.get(dt_obj.month, 5.0)
        min_v = mins[idx] if isinstance(mins, list) and idx < len(mins) and mins[idx] is not None else float(m_val) - 4.0
        max_v = maxs[idx] if isinstance(maxs, list) and idx < len(maxs) and maxs[idx] is not None else float(m_val) + 4.0
        wind_v = (
            winds[idx]
            if isinstance(winds, list) and idx < len(winds) and winds[idx] is not None
            else MONTHLY_FALLBACK_WIND_KMH.get(dt_obj.month, 14.0)
        )
        rows.append({
            "date": dt_obj.isoformat(),
            "avg_temp_c": round(float(m_val), 1),
            "min_temp_c": round(float(min_v), 1),
            "max_temp_c": round(float(max_v), 1),
            "wind_kmh": round(float(wind_v), 1),
            "source": source_label,
        })
    return rows


def _fetch_forecast_14d(today: date, lat: float, lon: float) -> tuple[list[dict[str, Any]], bool]:
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "forecast_days": 14,
        "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,wind_speed_10m_max",
        "timezone": "Asia/Seoul",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SeoulGasEstimator/3.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        parsed = _parse_open_meteo_daily(payload, "forecast_14d")
        future_only = [r for r in parsed if r["date"] > today.isoformat()]
        return future_only, len(future_only) > 0
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
        return [], False


def fetch_seoul_weather_range(
    raw_start_date: Any,
    region: str = "seoul",
    today: Optional[date] = None,
) -> dict[str, Any]:
    end_dt = today or date.today()
    start_dt = resolve_billing_start_date(raw_start_date, today=end_dt)
    clean_region = region if region in REGION_COORDS else "seoul"
    lat, lon = REGION_COORDS[clean_region]
    cache_key = f"v3:{clean_region}:{start_dt.isoformat()}:{end_dt.isoformat()}"

    now_ts = datetime.now().timestamp()
    cached = _CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
        "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,wind_speed_10m_max",
        "timezone": "Asia/Seoul",
    })
    url = f"https://historical-forecast-api.open-meteo.com/v1/forecast?{params}"

    daily_rows: list[dict[str, Any]] = []
    source = "live_open_meteo"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SeoulGasEstimator/3.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        daily_rows = _parse_open_meteo_daily(payload, "live")
        if not daily_rows:
            daily_rows = _fallback_series(start_dt, end_dt)
            source = "fallback"
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError):
        daily_rows = _fallback_series(start_dt, end_dt)
        source = "fallback"

    forecast_rows, forecast_live = _fetch_forecast_14d(end_dt, lat, lon)

    temps = [row["avg_temp_c"] for row in daily_rows]
    winds = [row["wind_kmh"] for row in daily_rows]
    elapsed_avg_temp = round(sum(temps) / max(1, len(temps)), 1)
    elapsed_avg_wind = round(sum(winds) / max(1, len(winds)), 1)
    hdds = [max(0.0, 18.0 - t) for t in temps]
    cumulative_hdd = round(sum(hdds), 1)

    result = {
        "region": clean_region,
        "billing_start_date": start_dt.isoformat(),
        "as_of_date": end_dt.isoformat(),
        "days_tracked": len(daily_rows),
        "elapsed_avg_temp_c": elapsed_avg_temp,
        "elapsed_avg_wind_kmh": elapsed_avg_wind,
        "cumulative_hdd": cumulative_hdd,
        "source": source,
        "forecast_live": forecast_live,
        "forecast_daily": forecast_rows,
        "daily": daily_rows,
    }
    _CACHE[cache_key] = (now_ts, result)
    return result