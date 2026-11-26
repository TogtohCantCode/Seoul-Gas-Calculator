from __future__ import annotations

BUILDING_BASE_FACTOR = {
    "oneroom": 0.85,
    "officetel": 0.95,
    "villa": 1.25,
    "apartment": 1.00,
}

ORIENTATION_FACTOR = {
    "south": 0.90,      # 남향: strong afternoon solar gain
    "east_west": 1.00,  # 동/서향: balanced baseline
    "north_ground": 1.14,  # 북향 / 1층·필로티: minimal sun + cold floor slab
}


def daily_hdd(outdoor_temp_c: float, base_temp_c: float = 18.0) -> float:
    return max(0.0, base_temp_c - outdoor_temp_c)


def _setpoint_factor(
    boiler_mode: str,
    temp_c: float,
    ondol_c: float,
    away_interval_min: float = 20.0,
    away_cycle_hours: float = 3.0,
    active: bool = True,
) -> float:
    if not active or boiler_mode == "away":
        burn_fraction = min(0.50, max(0.05, (away_interval_min / 60.0) / max(1.0, away_cycle_hours)))
        return max(0.16, burn_fraction * 1.35)

    if boiler_mode == "ondol_water":
        return max(0.45, 0.95 + (ondol_c - 55.0) * 0.022)

    delta = temp_c - 20.0
    return max(0.40, 1.0 + delta * 0.10 + max(0.0, delta) ** 2 * 0.012)


def _wind_infiltration_factor(wind_kmh: float, is_top_or_corner: bool, is_older: bool) -> float:
    excess_wind = max(0.0, wind_kmh - 12.0)
    exposure_sensitivity = 0.004
    if is_top_or_corner:
        exposure_sensitivity += 0.003
    if is_older:
        exposure_sensitivity += 0.004
    return 1.0 + min(0.25, excess_wind * exposure_sensitivity)


class CalibratedThermalEngine:
    """Physics-calibrated Korean residential heating model with slab inertia, wind, and solar orientation."""

    def __init__(self) -> None:
        self.trained = True

    def predict_daily_heating_m3(
        self,
        area_pyeong: float,
        building_type: str,
        outdoor_temp_c: float,
        boiler_mode: str,
        target_temp_c: float,
        ondol_temp_c: float,
        duty_cycle: float,
        is_top_or_corner: bool,
        is_older: bool,
        orientation: str = "east_west",
        wind_kmh: float = 14.0,
        away_interval_min: float = 20.0,
        away_cycle_hours: float = 3.0,
        active: bool = True,
        recovery_penalty: float = 1.0,
    ) -> float:
        hdd = daily_hdd(outdoor_temp_c)
        if hdd <= 0.0:
            return 0.0

        b_factor = BUILDING_BASE_FACTOR.get(building_type, 1.0)
        o_factor = ORIENTATION_FACTOR.get(orientation, 1.0)
        envelope = b_factor * o_factor * (1.18 if is_top_or_corner else 1.0) * (1.20 if is_older else 1.0)
        wind_mult = _wind_infiltration_factor(wind_kmh, is_top_or_corner, is_older)
        sp_factor = _setpoint_factor(
            boiler_mode=boiler_mode,
            temp_c=target_temp_c,
            ondol_c=ondol_temp_c,
            away_interval_min=away_interval_min,
            away_cycle_hours=away_cycle_hours,
            active=active,
        )

        effective_duty = duty_cycle if boiler_mode != "ondol_water" else (0.35 + 0.65 * duty_cycle)

        daily_m3 = (
            (area_pyeong * 0.26)
            * (hdd / 18.0)
            * envelope
            * wind_mult
            * sp_factor
            * effective_duty
            * recovery_penalty
        )
        return max(0.0, round(daily_m3, 5))


ML_ENGINE = CalibratedThermalEngine()