from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import numpy as np

from .models import IsothermPoint, TriStarResult
from .reference_thickness import default_reference_params, normalize_reference_points, reference_thickness_nm


DEFAULT_N2_CROSS_SECTION_NM2 = 0.162
DEFAULT_N2_DENSITY_CONVERSION_FACTOR = 0.0015468
DEFAULT_N2_SURFACE_TENSION_N_M = 8.85e-3
DEFAULT_N2_LIQUID_MOLAR_VOLUME_M3_MOL = 34.68e-6
DEFAULT_N2_ADSORBATE_PROPERTY_FACTOR_NM = 0.953
BSD_BJH_ADSORBATE_PROPERTY_FACTOR_NM = 0.954853
JWGB_BJH_ADSORBATE_PROPERTY_FACTOR_NM = 0.954853
QUANTACHROME_BJH_ADSORBATE_PROPERTY_FACTOR_NM = 0.9575
QUANTACHROME_BJH_LIQUID_VOLUME_SCALE = 0.992
QUANTACHROME_BJH_MIN_DIAMETER_NM = 1.15
QUANTACHROME_BJH_LOW_DIAMETER_CORRECTION_RANGE_NM = (1.40, 1.90)
QUANTACHROME_BJH_LOW_DIAMETER_INCREMENT_SCALE = 1.03
MICROMERITICS_FLEX_BJH_EFFECTIVE_ADSORBATE_PROPERTY_FACTOR_NM = 0.9514684743
MICROMERITICS_FLEX_BJH_ADSORPTION_WALL_CORRECTION_FACTOR = 1.03998522
MICROMERITICS_FLEX_BJH_DESORPTION_WALL_CORRECTION_FACTOR = 1.02645361
MICROMERITICS_FLEX_BJH_WALL_CORRECTION_FACTOR = MICROMERITICS_FLEX_BJH_ADSORPTION_WALL_CORRECTION_FACTOR
MICROMERITICS_FLEX_T_PLOT_THICKNESS_RANGE_NM = (0.35, 0.50)
GAS_CONSTANT_J_MOL_K = 8.314462618
BET_AUTO_FALLBACK_RANGE = (0.05, 0.30)
BET_AUTO_SEARCH_MAX = 0.35
BET_AUTO_RELAXED_SEARCH_MAX = 0.50
BET_AUTO_MIN_POINTS = 5
BET_3020_DEFAULT_POINT_COUNT = 7
BET_AUTO_TARGET_MIN = 0.15
BET_AUTO_TARGET_MAX = 0.30
BET_AUTO_POINT_BONUS = 2e-6
BET_AUTO_TARGET_PENALTY = 2e-3
BET_AUTO_SHORT_SPAN_PENALTY = 5e-4
BET_AUTO_MIN_SPAN = 0.08
LANGMUIR_DEFAULT_RANGE = (0.05, 0.30)
T_PLOT_DEFAULT_PRESSURE_RANGE = (0.20, 0.50)
DEFAULT_BJH_DIAMETER_MIN_NM = 1.7
DEFAULT_BJH_DIAMETER_MAX_NM = 300.0
JWGB_BJH_MIN_DIAMETER_NM = 2.0
MMHG_TO_KPA = 101.325 / 760.0
MICROACTIVE_BJH_MIN_DIAMETER_BY_THICKNESS_METHOD = {
    "reference": DEFAULT_BJH_DIAMETER_MIN_NM,
    "kjs": 1.7,
    "halsey": 1.5,
    "harkins_jura": 1.5,
    "broekhoff_de_boer": 1.5,
    "carbon_black_stsa": 1.35,
}
SMOOTH_LOG_GRID_INTERVALS = 199
SMOOTH_DERIVATIVE_WINDOW = 9
DEFAULT_THICKNESS_METHOD = "harkins_jura"
THICKNESS_METHOD_DEFAULT_PARAMS: dict[str, dict[str, object]] = {
    "reference": default_reference_params(),
    "kjs": {
        "numerator": 60.65,
        "offset": 0.03071,
        "exponent": 0.3968,
        "scale": 0.1,
    },
    "halsey": {
        "prefactor": 3.54,
        "numerator": -5.0,
        "exponent": 0.333,
        "scale": 0.1,
    },
    "harkins_jura": {
        "numerator": 13.99,
        "offset": 0.034,
        "exponent": 0.5,
        "scale": 0.1,
    },
    "broekhoff_de_boer": {
        "inverse_square": -16.11,
        "exponential_factor": 0.1682,
        "exponential_rate": -0.1137,
        "scale": 0.1,
    },
    "carbon_black_stsa": {
        "constant": 2.98,
        "linear": 6.45,
        "quadratic": 0.88,
        "scale": 0.1,
    },
}


@dataclass(frozen=True)
class FitResult:
    name: str
    status: str
    point_count: int = 0
    pressure_min: float | None = None
    pressure_max: float | None = None
    slope: float | None = None
    intercept: float | None = None
    slope_standard_error: float | None = None
    intercept_standard_error: float | None = None
    r_squared: float | None = None
    monolayer_capacity_cm3_g_stp: float | None = None
    surface_area_standard_error: float | None = None
    surface_area_m2_g: float | None = None
    c_constant: float | None = None
    langmuir_b: float | None = None
    external_surface_area_m2_g: float | None = None
    micropore_volume_cm3_g: float | None = None
    rows: list[dict[str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "warning_negative_c"}


@dataclass(frozen=True)
class PoreDistributionResult:
    name: str
    phase: str
    status: str
    point_count: int = 0
    rows: list[dict[str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def adsorption_points(result: TriStarResult) -> list[IsothermPoint]:
    points = [
        point
        for point in result.isotherm
        if point.phase == "adsorption"
        and _valid_number(point.relative_pressure)
        and _valid_number(point.quantity_adsorbed_cm3_g_stp)
        and 0.0 < float(point.relative_pressure) < 1.0
        and float(point.quantity_adsorbed_cm3_g_stp or 0.0) > 0.0
    ]
    return sorted(points, key=lambda point: point.relative_pressure)


def desorption_points(result: TriStarResult) -> list[IsothermPoint]:
    points = [
        point
        for point in result.isotherm
        if point.phase == "desorption"
        and _valid_number(point.relative_pressure)
        and _valid_number(point.quantity_adsorbed_cm3_g_stp)
        and 0.0 < float(point.relative_pressure) < 1.0
    ]
    return sorted(points, key=lambda point: point.relative_pressure)


def _bjh_branch_points(result: TriStarResult, phase: str) -> list[IsothermPoint]:
    if phase == "adsorption":
        return adsorption_points(result)
    points = desorption_points(result)
    if not (
        _uses_micromeritics_flex_bjh_recalculation_defaults(result)
        or _uses_quantachrome_defaults(result)
    ):
        return points

    anchors = [
        point
        for point in result.isotherm
        if point.phase == "adsorption"
        and _valid_number(point.relative_pressure)
        and _valid_number(point.quantity_adsorbed_cm3_g_stp)
        and 0.0 < float(point.relative_pressure) < 1.0
        and float(point.quantity_adsorbed_cm3_g_stp or 0.0) > 0.0
    ]
    if not anchors:
        return points
    max_pressure = max(float(point.relative_pressure) for point in anchors)
    anchor = max(
        (point for point in anchors if math.isclose(float(point.relative_pressure), max_pressure, rel_tol=0.0, abs_tol=1e-12)),
        key=lambda point: point.index,
    )
    return points + [anchor]


def bet_analysis(
    result: TriStarResult,
    p_min: float | None = None,
    p_max: float | None = None,
) -> FitResult:
    if p_min is None or p_max is None:
        p_min, p_max = automatic_bet_range(result)
    return _bet_analysis_for_range(result, p_min, p_max)


def automatic_langmuir_range(result: TriStarResult) -> tuple[float, float]:
    if _uses_official_fit_ranges(result):
        stored_range = _stored_langmuir_range(result)
        if stored_range is not None:
            return stored_range
    if _uses_micromeritics_flex_defaults(result):
        points = adsorption_points(result)
        if len(points) >= 3:
            pressures = [float(point.relative_pressure) for point in points]
            return min(pressures), max(pressures)
    return LANGMUIR_DEFAULT_RANGE


def automatic_t_plot_pressure_range(result: TriStarResult) -> tuple[float, float]:
    if _uses_official_fit_ranges(result):
        stored_range = _stored_t_plot_pressure_range(result)
        if stored_range is not None:
            return stored_range
    if _uses_micromeritics_flex_defaults(result):
        pressures = []
        t_min, t_max = MICROMERITICS_FLEX_T_PLOT_THICKNESS_RANGE_NM
        for point in adsorption_points(result):
            thickness = thickness_nm(float(point.relative_pressure), "harkins_jura", None)
            if thickness is not None and t_min <= thickness <= t_max:
                pressures.append(float(point.relative_pressure))
        if len(pressures) >= 3:
            return min(pressures), max(pressures)
    return T_PLOT_DEFAULT_PRESSURE_RANGE


def automatic_bet_range(result: TriStarResult) -> tuple[float, float]:
    points = adsorption_points(result)
    if len(points) < 3:
        return BET_AUTO_FALLBACK_RANGE
    if _uses_official_fit_ranges(result) and _uses_asap_defaults(result):
        stored_range = _stored_bet_range(result)
        if stored_range is not None:
            if stored_range[0] <= 0.0 and _uses_asap_2460_defaults(result):
                return (0.08, stored_range[1])
            return stored_range
    if _uses_official_fit_ranges(result) and (
        _uses_tristar_ii_plus_defaults(result)
        or _uses_bsd_defaults(result)
        or _uses_micromeritics_flex_defaults(result)
        or _uses_jwgb_defaults(result)
    ):
        stored_range = _stored_bet_range(result)
        if stored_range is not None:
            return stored_range
    if _uses_tristar_ii_plus_defaults(result):
        vendor_range = _tristar_ii_plus_bet_range_until_rouquerol_peak(points)
        if vendor_range is not None:
            return vendor_range
    if (
        _uses_tristar_ii_plus_defaults(result)
        or _uses_asap_defaults(result)
        or _uses_bsd_defaults(result)
        or _uses_micromeritics_flex_defaults(result)
        or _uses_jwgb_defaults(result)
    ):
        window_min = (
            0.08
            if _uses_tristar_ii_plus_defaults(result) or _uses_asap_2460_defaults(result)
            else BET_AUTO_FALLBACK_RANGE[0]
        )
        vendor_range = _adsorption_data_range_in_window(points, window_min, BET_AUTO_FALLBACK_RANGE[1])
        if vendor_range is not None:
            return vendor_range
    if _uses_tristar_3020_defaults(result) and len(points) >= 3:
        count = min(BET_3020_DEFAULT_POINT_COUNT, len(points))
        selected = points[:count]
        return (float(selected[0].relative_pressure), float(selected[-1].relative_pressure))

    for search_max, min_points in (
        (BET_AUTO_SEARCH_MAX, BET_AUTO_MIN_POINTS),
        (BET_AUTO_RELAXED_SEARCH_MAX, BET_AUTO_MIN_POINTS),
        (BET_AUTO_SEARCH_MAX, 3),
        (BET_AUTO_RELAXED_SEARCH_MAX, 3),
    ):
        candidate = _best_automatic_bet_candidate(result, points, search_max, min_points)
        if candidate is not None:
            return candidate
    return _fallback_bet_range(points)


def _adsorption_data_range_in_window(
    points: Sequence[IsothermPoint],
    p_min: float,
    p_max: float,
    min_points: int = 3,
) -> tuple[float, float] | None:
    selected = [
        float(point.relative_pressure)
        for point in points
        if p_min <= float(point.relative_pressure) <= p_max
    ]
    if len(selected) < min_points:
        return None
    return (min(selected), max(selected))


def _best_automatic_bet_candidate(
    result: TriStarResult,
    points: Sequence[IsothermPoint],
    search_max: float,
    min_points: int,
) -> tuple[float, float] | None:
    best: tuple[float, int, float, float] | None = None
    for start in range(len(points)):
        for stop in range(start + max(2, min_points - 1), len(points)):
            selected = list(points[start : stop + 1])
            p_min = float(selected[0].relative_pressure)
            p_max = float(selected[-1].relative_pressure)
            if p_max > search_max:
                break
            fit = _bet_analysis_for_points(result, selected, p_min, p_max)
            if not _is_valid_automatic_bet_candidate(fit, selected):
                continue
            score = _automatic_bet_candidate_score(fit)
            point_count = fit.point_count
            current = (score, point_count, p_min, p_max)
            if best is None or current > best:
                best = current
    if best is None:
        return None
    return (best[2], best[3])


def _is_valid_automatic_bet_candidate(fit: FitResult, selected: Sequence[IsothermPoint]) -> bool:
    if (
        fit.r_squared is None
        or fit.slope is None
        or fit.intercept is None
        or fit.monolayer_capacity_cm3_g_stp is None
        or fit.c_constant is None
        or fit.c_constant <= 0.0
    ):
        return False
    if fit.point_count < 3:
        return False
    return _rouquerol_transform_increases(selected)


def _automatic_bet_candidate_score(fit: FitResult) -> float:
    r_squared = float(fit.r_squared or 0.0)
    p_min = float(fit.pressure_min or 0.0)
    p_max = float(fit.pressure_max or 0.0)
    span = max(0.0, p_max - p_min)
    point_bonus = min(int(fit.point_count), 12) * BET_AUTO_POINT_BONUS
    low_penalty = abs(p_min - BET_AUTO_TARGET_MIN) * BET_AUTO_TARGET_PENALTY
    high_penalty = abs(p_max - BET_AUTO_TARGET_MAX) * BET_AUTO_TARGET_PENALTY
    span_penalty = max(0.0, BET_AUTO_MIN_SPAN - span) * BET_AUTO_SHORT_SPAN_PENALTY
    return r_squared + point_bonus - low_penalty - high_penalty - span_penalty


def _rouquerol_transform_increases(points: Sequence[IsothermPoint]) -> bool:
    values = []
    for point in points:
        pressure = float(point.relative_pressure)
        volume = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        values.append(volume * (1.0 - pressure))
    if len(values) < 2:
        return False
    tolerance = max(1e-9, max(abs(value) for value in values) * 2e-4)
    return all(values[index + 1] >= values[index] - tolerance for index in range(len(values) - 1))


def _fallback_bet_range(points: Sequence[IsothermPoint]) -> tuple[float, float]:
    lo, hi = BET_AUTO_FALLBACK_RANGE
    pressures = [float(point.relative_pressure) for point in points]
    data_min = min(pressures)
    data_max = max(pressures)
    lo = max(data_min, lo)
    hi = min(data_max, hi)
    if lo < hi:
        return (lo, hi)
    return (data_min, data_max)


def _tristar_ii_plus_bet_range_until_rouquerol_peak(
    points: Sequence[IsothermPoint],
    *,
    p_min: float = 0.08,
    p_max: float = 0.30,
    min_points: int = 4,
) -> tuple[float, float] | None:
    selected = [
        point
        for point in points
        if p_min <= float(point.relative_pressure) <= p_max
    ]
    if len(selected) < min_points:
        return None

    transformed = [
        float(point.quantity_adsorbed_cm3_g_stp or 0.0) * (1.0 - float(point.relative_pressure))
        for point in selected
    ]
    tolerance = max(1e-9, max(abs(value) for value in transformed) * 2e-4)
    stop_index = len(selected) - 1
    for index in range(len(transformed) - 1):
        if transformed[index + 1] < transformed[index] - tolerance:
            stop_index = max(index, min_points - 1)
            break

    if stop_index + 1 < min_points:
        return None
    return (
        float(selected[0].relative_pressure),
        float(selected[stop_index].relative_pressure),
    )


def _bet_analysis_for_range(result: TriStarResult, p_min: float, p_max: float) -> FitResult:
    selected = _points_in_range(adsorption_points(result), p_min, p_max)
    return _bet_analysis_for_points(result, selected, p_min, p_max)


def _bet_analysis_for_points(
    result: TriStarResult,
    selected: Sequence[IsothermPoint],
    p_min: float,
    p_max: float,
) -> FitResult:
    if len(selected) < 3:
        return FitResult("BET", "not_enough_points", len(selected), p_min, p_max)

    rows = []
    x_values = []
    y_values = []
    for point in selected:
        x = float(point.relative_pressure)
        volume = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        if volume <= 0.0 or x >= 1.0:
            continue
        y = x / (volume * (1.0 - x))
        rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": x,
                "quantity_adsorbed_cm3_g_stp": volume,
                "bet_y": y,
            }
        )
        x_values.append(x)
        y_values.append(y)

    if len(x_values) < 3:
        return FitResult("BET", "not_enough_valid_points", len(x_values), p_min, p_max, rows=rows)

    slope, intercept, r_squared = _linear_fit(x_values, y_values)
    slope_se, intercept_se = _linear_fit_standard_errors(x_values, y_values, slope, intercept)
    denominator = slope + intercept
    if denominator <= 0:
        return FitResult(
            "BET",
            "invalid_monolayer_capacity",
            len(x_values),
            p_min,
            p_max,
            slope=slope,
            intercept=intercept,
            slope_standard_error=slope_se,
            intercept_standard_error=intercept_se,
            r_squared=r_squared,
            rows=rows,
        )

    monolayer = 1.0 / denominator
    c_constant = (slope / intercept + 1.0) if abs(intercept) > 1e-15 else None
    status = "warning_negative_c" if c_constant is not None and c_constant <= 0.0 else "ok"
    surface_area = monolayer * surface_area_factor_m2_per_cm3(result)
    surface_area_se = None
    if slope_se is not None and intercept_se is not None and abs(denominator) > 1e-15:
        surface_area_se = surface_area * math.sqrt(slope_se * slope_se + intercept_se * intercept_se) / abs(denominator)
    return FitResult(
        "BET",
        status,
        len(x_values),
        p_min,
        p_max,
        slope=slope,
        intercept=intercept,
        slope_standard_error=slope_se,
        intercept_standard_error=intercept_se,
        r_squared=r_squared,
        monolayer_capacity_cm3_g_stp=monolayer,
        surface_area_standard_error=surface_area_se,
        surface_area_m2_g=surface_area,
        c_constant=c_constant,
        rows=rows,
    )


def langmuir_analysis(
    result: TriStarResult,
    p_min: float | None = None,
    p_max: float | None = None,
) -> FitResult:
    if p_min is None or p_max is None:
        p_min, p_max = automatic_langmuir_range(result)
    selected = _points_in_range(adsorption_points(result), p_min, p_max)
    if len(selected) < 3:
        return FitResult("Langmuir", "not_enough_points", len(selected), p_min, p_max)

    rows = []
    x_values = []
    y_values = []
    for point in selected:
        x = float(point.relative_pressure)
        volume = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        if volume <= 0.0:
            continue
        y = x / volume
        rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": x,
                "quantity_adsorbed_cm3_g_stp": volume,
                "langmuir_y": y,
            }
        )
        x_values.append(x)
        y_values.append(y)

    if len(x_values) < 3:
        return FitResult("Langmuir", "not_enough_valid_points", len(x_values), p_min, p_max, rows=rows)

    slope, intercept, r_squared = _linear_fit(x_values, y_values)
    if slope <= 0:
        return FitResult(
            "Langmuir",
            "invalid_monolayer_capacity",
            len(x_values),
            p_min,
            p_max,
            slope=slope,
            intercept=intercept,
            r_squared=r_squared,
            rows=rows,
        )

    monolayer = 1.0 / slope
    langmuir_b = (slope / intercept) if intercept and intercept > 0 else None
    surface_area = monolayer * surface_area_factor_m2_per_cm3(result)
    return FitResult(
        "Langmuir",
        "ok",
        len(x_values),
        p_min,
        p_max,
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        monolayer_capacity_cm3_g_stp=monolayer,
        surface_area_m2_g=surface_area,
        langmuir_b=langmuir_b,
        rows=rows,
    )


def t_plot_analysis(
    result: TriStarResult,
    p_min: float | None = None,
    p_max: float | None = None,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
) -> FitResult:
    if p_min is None or p_max is None:
        p_min, p_max = automatic_t_plot_pressure_range(result)
    selected = _points_in_range(adsorption_points(result), p_min, p_max)
    return _t_plot_fit_from_points(result, selected, p_min, p_max, thickness_params, thickness_method)


def t_plot_analysis_by_thickness(
    result: TriStarResult,
    t_min: float,
    t_max: float,
    p_min: float | None = None,
    p_max: float | None = None,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
) -> FitResult:
    pts = adsorption_points(result)
    if p_min is not None and p_max is not None:
        pts = _points_in_range(pts, p_min, p_max)
    selected = []
    for pt in pts:
        t = thickness_nm(float(pt.relative_pressure), thickness_method, thickness_params)
        if t is not None and t_min <= t <= t_max:
            selected.append(pt)
    return _t_plot_fit_from_points(result, selected, t_min, t_max, thickness_params, thickness_method)


def _t_plot_fit_from_points(
    result: TriStarResult,
    selected: list,
    range_min: float,
    range_max: float,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
) -> FitResult:
    if len(selected) < 3:
        return FitResult("t-Plot", "not_enough_points", len(selected), range_min, range_max)

    density_factor = density_conversion_factor(result)
    rows: list = []
    x_values: list = []
    y_values: list = []
    for point in selected:
        pressure = float(point.relative_pressure)
        thickness = thickness_nm(pressure, thickness_method, thickness_params)
        quantity = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        liquid_volume = quantity * density_factor
        if thickness is None or not _valid_number(liquid_volume):
            continue
        use_quantity_t_plot = _uses_quantity_stp_t_plot_defaults(result)
        y_value = quantity if use_quantity_t_plot else liquid_volume
        rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": pressure,
                "quantity_adsorbed_cm3_g_stp": quantity,
                "thickness_nm": thickness,
                "liquid_volume_cm3_g": liquid_volume,
                "t_plot_y_value": y_value,
                "t_plot_y_unit": "cm3/g STP" if use_quantity_t_plot else "cm3/g liquid",
            }
        )
        x_values.append(thickness)
        y_values.append(y_value)

    if len(x_values) < 3:
        return FitResult("t-Plot", "not_enough_valid_points", len(x_values), range_min, range_max, rows=rows)

    slope, intercept, r_squared = _linear_fit(x_values, y_values)
    if _uses_quantity_stp_t_plot_defaults(result):
        raw_external_surface_area = slope * density_factor * 1000.0 if slope > 0 else None
        total_surface_area = _t_plot_total_surface_area_for_result(result)
        if raw_external_surface_area is not None and total_surface_area is not None:
            external_surface_area = min(raw_external_surface_area, total_surface_area)
        else:
            external_surface_area = raw_external_surface_area
        micropore_volume = max(0.0, intercept * density_factor) if _valid_number(intercept) else None
    else:
        external_surface_area = slope * 1000.0 if slope > 0 else None
        if _uses_micromeritics_flex_defaults(result):
            micropore_volume = intercept if _valid_number(intercept) else None
        else:
            micropore_volume = max(0.0, intercept) if _valid_number(intercept) else None
    return FitResult(
        "t-Plot", "ok", len(x_values), range_min, range_max,
        slope=slope, intercept=intercept, r_squared=r_squared,
        external_surface_area_m2_g=external_surface_area,
        micropore_volume_cm3_g=micropore_volume,
        rows=rows,
    )


def analysis_bundle(
    result: TriStarResult,
    p_min: float | None = None,
    p_max: float | None = None,
) -> dict[str, FitResult]:
    if p_min is None or p_max is None:
        langmuir_min, langmuir_max = automatic_langmuir_range(result)
        t_plot_min, t_plot_max = automatic_t_plot_pressure_range(result)
        return {
            "BET": bet_analysis(result),
            "Langmuir": langmuir_analysis(result, langmuir_min, langmuir_max),
            "t-Plot": t_plot_analysis(result, t_plot_min, t_plot_max),
        }
    return {
        "BET": bet_analysis(result, p_min, p_max),
        "Langmuir": langmuir_analysis(result, p_min, p_max),
        "t-Plot": t_plot_analysis(result, p_min, p_max),
    }


def surface_area_factor_m2_per_cm3(result: TriStarResult) -> float:
    cross_section = DEFAULT_N2_CROSS_SECTION_NM2
    if result.adsorptive_properties and result.adsorptive_properties.molecular_cross_sectional_area_nm2:
        cross_section = float(result.adsorptive_properties.molecular_cross_sectional_area_nm2)
    avogadro = 6.023e23 if _uses_tristar_3020_defaults(result) else 6.02214076e23
    molar_volume_cm3_stp = 22414.0
    return avogadro * cross_section * 1e-18 / molar_volume_cm3_stp


def _uses_tristar_3020_defaults(result: TriStarResult) -> bool:
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return "TriStar II 3020" in model or "TriStar II 3020" in software


def _uses_asap_defaults(result: TriStarResult) -> bool:
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return "ASAP 2460" in model or "ASAP 2020 Plus" in model or "ASAP 2460" in software or "ASAP 2020 Plus" in software


def _uses_asap_2460_defaults(result: TriStarResult) -> bool:
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return "ASAP 2460" in model or "ASAP 2460" in software


def _uses_tristar_ii_plus_defaults(result: TriStarResult) -> bool:
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return "TriStar II Plus" in model or "MicroActive for TriStar II Plus" in software


def _uses_micromeritics_flex_defaults(result: TriStarResult) -> bool:
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return bool(result.method_options.get("micromeritics_flex_excel_import")) or "3Flex 3500" in model or "Flex " in software


def _uses_micromeritics_3flex_manual_smp_defaults(result: TriStarResult) -> bool:
    return str(result.method_options.get("format_family", "")) == "3Flex manual SMP"


def _uses_micromeritics_flex_bjh_recalculation_defaults(result: TriStarResult) -> bool:
    return _uses_micromeritics_3flex_manual_smp_defaults(result) or bool(
        result.method_options.get("micromeritics_flex_excel_import")
    )


def _uses_quantachrome_defaults(result: TriStarResult) -> bool:
    manufacturer = str(result.method_options.get("instrument_manufacturer", ""))
    model = str(result.method_options.get("instrument_model", ""))
    return "Quantachrome" in manufacturer or "Autosorb" in model or "QuadraSorb" in model


def _uses_bsd_defaults(result: TriStarResult) -> bool:
    manufacturer = str(result.method_options.get("instrument_manufacturer", ""))
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return (
        bool(result.method_options.get("bsd_excel_import"))
        or "BSD" in manufacturer
        or "BSD-660" in model
        or "BSD-660" in software
    )


def _uses_jwgb_defaults(result: TriStarResult) -> bool:
    manufacturer = str(result.method_options.get("instrument_manufacturer", ""))
    model = str(result.method_options.get("instrument_model", ""))
    software = str(result.method_options.get("instrument_software", ""))
    return (
        bool(result.method_options.get("jwgb_excel_import"))
        or "JWGB" in manufacturer
        or "JWGB" in model
        or "JWGB" in software
        or "精微高博" in manufacturer
    )


def _uses_bsd_t_plot_defaults(result: TriStarResult) -> bool:
    return _uses_bsd_defaults(result)


def _uses_quantity_stp_t_plot_defaults(result: TriStarResult) -> bool:
    return _uses_bsd_t_plot_defaults(result) or _uses_jwgb_defaults(result)


def _uses_official_bjh_table(result: TriStarResult) -> bool:
    return bool(
        result.method_options.get("use_official_bjh_table")
        or result.method_options.get("use_official_excel_bjh_table")
        or result.method_options.get("micromeritics_flex_use_official_bjh_table")
    )


def _uses_official_fit_ranges(result: TriStarResult) -> bool:
    return bool(
        result.method_options.get("use_official_fit_ranges")
        or result.method_options.get("use_official_excel_fit_ranges")
        or result.method_options.get("use_stored_fit_ranges")
    )


def _t_plot_total_surface_area_for_result(result: TriStarResult) -> float | None:
    fit = bet_analysis(result)
    return fit.surface_area_m2_g


def _bjh_kelvin_factor_nm(result: TriStarResult) -> float:
    value = result.method_options.get("jwgb_bjh_kelvin_factor_nm")
    if _valid_number(value):
        return float(value)
    value = result.method_options.get("bsd_bjh_kelvin_factor_nm")
    if _valid_number(value):
        return float(value)
    if _uses_bsd_defaults(result):
        return BSD_BJH_ADSORBATE_PROPERTY_FACTOR_NM
    if _uses_jwgb_defaults(result):
        return JWGB_BJH_ADSORBATE_PROPERTY_FACTOR_NM
    if _uses_quantachrome_defaults(result):
        return QUANTACHROME_BJH_ADSORBATE_PROPERTY_FACTOR_NM
    return DEFAULT_N2_ADSORBATE_PROPERTY_FACTOR_NM


def _stored_bet_range(result: TriStarResult) -> tuple[float, float] | None:
    p_min = result.method_options.get("stored_bet_pressure_min")
    p_max = result.method_options.get("stored_bet_pressure_max")
    if not (_valid_number(p_min) and _valid_number(p_max)):
        return None
    p_min = float(p_min)
    p_max = float(p_max)
    if 0.0 <= p_min < p_max <= 1.0:
        return (p_min, p_max)
    return None


def _stored_langmuir_range(result: TriStarResult) -> tuple[float, float] | None:
    generic_min = result.method_options.get("stored_langmuir_pressure_min")
    generic_max = result.method_options.get("stored_langmuir_pressure_max")
    if _valid_number(generic_min) and _valid_number(generic_max):
        p_min = float(generic_min)
        p_max = float(generic_max)
        if 0.0 <= p_min < p_max <= 1.1:
            return (p_min, min(p_max, 1.0))

    p_min_kpa = result.method_options.get("bsd_langmuir_pressure_min_kpa")
    p_max_kpa = result.method_options.get("bsd_langmuir_pressure_max_kpa")
    if not (_valid_number(p_min_kpa) and _valid_number(p_max_kpa)):
        return None
    p_min_kpa = float(p_min_kpa)
    p_max_kpa = float(p_max_kpa)
    if p_min_kpa >= p_max_kpa:
        return None

    selected = []
    for point in adsorption_points(result):
        pressure = point.absolute_pressure_mmHg
        if not _valid_number(pressure):
            continue
        pressure_kpa = float(pressure) * MMHG_TO_KPA
        if p_min_kpa <= pressure_kpa <= p_max_kpa:
            selected.append(point)
    if len(selected) >= 3:
        pressures = [float(point.relative_pressure) for point in selected]
        return (min(pressures), max(pressures))

    saturation_values = [
        float(point.saturation_pressure_mmHg) * MMHG_TO_KPA
        for point in adsorption_points(result)
        if _valid_number(point.saturation_pressure_mmHg) and float(point.saturation_pressure_mmHg) > 0.0
    ]
    if not saturation_values:
        return None
    saturation_kpa = sum(saturation_values) / len(saturation_values)
    p_min = p_min_kpa / saturation_kpa
    p_max = p_max_kpa / saturation_kpa
    if 0.0 <= p_min < p_max <= 1.0:
        return (p_min, p_max)
    return None


def _stored_t_plot_pressure_range(result: TriStarResult) -> tuple[float, float] | None:
    generic_min = result.method_options.get("stored_t_plot_pressure_min")
    generic_max = result.method_options.get("stored_t_plot_pressure_max")
    if _valid_number(generic_min) and _valid_number(generic_max):
        p_min = float(generic_min)
        p_max = float(generic_max)
        if 0.0 <= p_min < p_max <= 1.0:
            return (p_min, p_max)

    p_min = result.method_options.get("bsd_t_plot_pressure_min")
    p_max = result.method_options.get("bsd_t_plot_pressure_max")
    if not (_valid_number(p_min) and _valid_number(p_max)):
        return None
    p_min = float(p_min)
    p_max = float(p_max)
    if not (0.0 <= p_min < p_max <= 1.0):
        return None

    selected = []
    for point in adsorption_points(result):
        pressure = float(point.relative_pressure)
        rounded_pressure = round(pressure, 4)
        if p_min <= pressure <= p_max or p_min <= rounded_pressure <= p_max:
            selected.append(pressure)
    if len(selected) >= 3:
        return (min(selected), max(selected))
    return (p_min, p_max)


def _micromeritics_flex_official_bjh_rows(
    result: TriStarResult,
    phase: str,
    thickness_method: str,
    correction: str,
    open_pore_fraction: float,
) -> list[dict[str, float]] | None:
    if not result.method_options.get("micromeritics_flex_excel_import"):
        return None
    if phase not in {"adsorption", "desorption"}:
        return None
    if correction != "standard" or abs(float(open_pore_fraction)) > 1e-12:
        return None
    if thickness_method != "harkins_jura":
        return None
    raw_rows = result.method_options.get(f"micromeritics_flex_bjh_{phase}_rows")
    if not isinstance(raw_rows, list) or len(raw_rows) < 2:
        return None

    rows: list[dict[str, float]] = []
    for index, raw in enumerate(raw_rows, start=1):
        try:
            pore_diameter = float(raw["pore_diameter_nm"])
            high = float(raw["pore_diameter_range_high_nm"])
            low = float(raw["pore_diameter_range_low_nm"])
            incremental_volume = float(raw["incremental_pore_volume_cm3_g"])
            cumulative_volume = float(raw["cumulative_pore_volume_cm3_g"])
        except (KeyError, TypeError, ValueError):
            return None
        dlog_diameter = abs(math.log10(high) - math.log10(low)) if high > 0.0 and low > 0.0 else 0.0
        differential = incremental_volume / dlog_diameter if dlog_diameter > 1e-12 else 0.0
        row = {
            "phase": phase,
            "interval_index": float(index),
            "pore_diameter_nm": pore_diameter,
            "cumulative_pore_diameter_nm": low,
            "pore_diameter_range_high_nm": high,
            "pore_diameter_range_low_nm": low,
            "incremental_pore_volume_cm3_g": incremental_volume,
            "cumulative_pore_volume_cm3_g": cumulative_volume,
            "dlog_diameter": dlog_diameter,
            "differential_pore_volume_cm3_g": differential,
            "raw_differential_pore_volume_cm3_g": differential,
            "bjh_correction": "micromeritics_flex_official_excel_table",
            "open_pore_fraction": float(open_pore_fraction),
        }
        incremental_area = raw.get("incremental_pore_area_m2_g") if isinstance(raw, dict) else None
        cumulative_area = raw.get("cumulative_pore_area_m2_g") if isinstance(raw, dict) else None
        if _valid_number(incremental_area):
            row["incremental_pore_area_m2_g"] = float(incremental_area)
        if _valid_number(cumulative_area):
            row["cumulative_pore_area_m2_g"] = float(cumulative_area)
        rows.append(row)
    return rows


def _quantachrome_official_bjh_rows(
    result: TriStarResult,
    phase: str,
    thickness_method: str,
    correction: str,
    open_pore_fraction: float,
) -> list[dict[str, float]] | None:
    if not result.method_options.get("quantachrome_excel_import"):
        return None
    if correction != "standard" or abs(float(open_pore_fraction)) > 1e-12:
        return None
    official_method = str(result.method_options.get("quantachrome_bjh_thickness_method", "harkins_jura"))
    if thickness_method != official_method:
        return None
    raw_rows = result.method_options.get(f"quantachrome_bjh_{phase}_rows")
    if not isinstance(raw_rows, list) or len(raw_rows) < 2:
        return None

    rows: list[dict[str, float]] = []
    previous_diameter: float | None = None
    previous_volume: float | None = None
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        try:
            pore_diameter = float(raw["pore_diameter_nm"])
            cumulative_volume = float(raw["cumulative_pore_volume_cm3_g"])
            cumulative_area = float(raw.get("cumulative_pore_area_m2_g", 0.0))
            differential_per_nm = float(raw.get("differential_pore_volume_per_nm_cm3_g_nm", 0.0))
            differential_area_per_nm = float(raw.get("differential_pore_area_per_nm_m2_g_nm", 0.0))
            differential_log = float(raw.get("differential_pore_volume_cm3_g", 0.0))
            differential_area_log = float(raw.get("differential_pore_area_m2_g", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if previous_volume is None or previous_diameter is None or pore_diameter <= 0.0 or previous_diameter <= 0.0:
            incremental_volume = cumulative_volume
            dlog_diameter = 0.0
        else:
            incremental_volume = max(0.0, cumulative_volume - previous_volume)
            dlog_diameter = abs(math.log10(pore_diameter) - math.log10(previous_diameter))
        rows.append(
            {
                "phase": phase,
                "interval_index": float(index + 1),
                "pore_diameter_nm": pore_diameter,
                "cumulative_pore_diameter_nm": pore_diameter,
                "incremental_pore_volume_cm3_g": incremental_volume,
                "cumulative_pore_volume_cm3_g": cumulative_volume,
                "cumulative_pore_area_m2_g": cumulative_area,
                "dlog_diameter": dlog_diameter,
                "differential_pore_volume_cm3_g": differential_log,
                "raw_differential_pore_volume_cm3_g": differential_log,
                "differential_pore_area_m2_g": differential_area_log,
                "differential_pore_volume_per_nm_cm3_g_nm": differential_per_nm,
                "differential_pore_area_per_nm_m2_g_nm": differential_area_per_nm,
                "bjh_correction": "quantachrome_official_excel_table",
                "open_pore_fraction": 0.0,
            }
        )
        previous_diameter = pore_diameter
        previous_volume = cumulative_volume
    return rows if len(rows) >= 2 else None


def _bsd_official_bjh_rows(
    result: TriStarResult,
    phase: str,
    thickness_method: str,
    correction: str,
    open_pore_fraction: float,
) -> list[dict[str, float]] | None:
    if not _uses_bsd_defaults(result):
        return None
    if correction != "standard" or abs(float(open_pore_fraction)) > 1e-12:
        return None
    if thickness_method not in {"halsey", "reference"}:
        return None
    raw_rows = result.method_options.get(f"bsd_bjh_{phase}_rows")
    if not isinstance(raw_rows, list) or len(raw_rows) < 2:
        return None

    rows: list[dict[str, float]] = []
    previous_volume: float | None = None
    previous_diameter: float | None = None
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            continue
        try:
            pressure = float(raw["relative_pressure"])
            pore_diameter = float(raw["pore_diameter_nm"])
            kelvin_diameter = float(raw["kelvin_pore_diameter_nm"])
            cumulative_volume = float(raw["cumulative_pore_volume_cm3_g"])
            cumulative_area = float(raw.get("cumulative_pore_area_m2_g", 0.0))
            differential = float(raw["differential_pore_volume_cm3_g"])
            differential_area = float(raw.get("differential_pore_area_m2_g", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if previous_volume is None or previous_diameter is None:
            incremental_volume = 0.0
            dlog_diameter = 0.0
        else:
            incremental_volume = max(0.0, previous_volume - cumulative_volume)
            dlog_diameter = abs(math.log10(previous_diameter) - math.log10(pore_diameter)) if pore_diameter > 0.0 else 0.0
        rows.append(
            {
                "phase": phase,
                "interval_index": float(index),
                "relative_pressure_high": pressure,
                "relative_pressure_low": pressure,
                "pore_diameter_nm": pore_diameter,
                "cumulative_pore_diameter_nm": kelvin_diameter,
                "incremental_pore_volume_cm3_g": incremental_volume,
                "cumulative_pore_volume_cm3_g": cumulative_volume,
                "cumulative_pore_area_m2_g": cumulative_area,
                "dlog_diameter": dlog_diameter,
                "differential_pore_volume_cm3_g": differential,
                "raw_differential_pore_volume_cm3_g": differential,
                "differential_pore_area_m2_g": differential_area,
                "differential_pore_volume_per_nm_cm3_g_nm": float(
                    raw.get("differential_pore_volume_per_nm_cm3_g_nm", 0.0)
                ),
                "differential_pore_area_per_nm_m2_g_nm": float(
                    raw.get("differential_pore_area_per_nm_m2_g_nm", 0.0)
                ),
                "film_thickness_nm": max(0.0, 0.5 * (pore_diameter - kelvin_diameter)),
                "kelvin_radius_nm": 0.5 * kelvin_diameter,
                "bjh_correction": "bsd_official_excel_table",
                "open_pore_fraction": 0.0,
            }
        )
        previous_volume = cumulative_volume
        previous_diameter = pore_diameter
    return rows if len(rows) >= 2 else None


def density_conversion_factor(result: TriStarResult) -> float:
    if result.adsorptive_properties and result.adsorptive_properties.density_conversion_factor:
        return float(result.adsorptive_properties.density_conversion_factor)
    return DEFAULT_N2_DENSITY_CONVERSION_FACTOR


def _bjh_pore_area_m2_g(volume_cm3_g: float, diameter_nm: float) -> float:
    if diameter_nm <= 1e-12:
        return 0.0
    return 4000.0 * float(volume_cm3_g) / float(diameter_nm)


def _set_bjh_area_fields(row: dict[str, float], cumulative_area_m2_g: float) -> None:
    diameter = float(row.get("pore_diameter_nm", 0.0))
    incremental_volume = float(row.get("incremental_pore_volume_cm3_g", 0.0))
    incremental_area = _bjh_pore_area_m2_g(incremental_volume, diameter)
    dlog_diameter = float(row.get("dlog_diameter", 0.0))
    high = row.get("pore_diameter_range_high_nm")
    low = row.get("pore_diameter_range_low_nm")
    width = abs(float(high) - float(low)) if _valid_number(high) and _valid_number(low) else 0.0
    row["incremental_pore_area_m2_g"] = incremental_area
    row["cumulative_pore_area_m2_g"] = cumulative_area_m2_g
    row["differential_pore_area_m2_g"] = incremental_area / dlog_diameter if dlog_diameter > 1e-12 else 0.0
    row["differential_pore_volume_per_nm_cm3_g_nm"] = incremental_volume / width if width > 1e-12 else 0.0
    row["differential_pore_area_per_nm_m2_g_nm"] = incremental_area / width if width > 1e-12 else 0.0


def _recalculate_bjh_area_fields(rows: list[dict[str, float]]) -> None:
    cumulative_area = 0.0
    for row in rows:
        incremental_area = _bjh_pore_area_m2_g(
            float(row.get("incremental_pore_volume_cm3_g", 0.0)),
            float(row.get("pore_diameter_nm", 0.0)),
        )
        cumulative_area += incremental_area
        _set_bjh_area_fields(row, cumulative_area)


def _update_bjh_differential_area_from_volume(rows: list[dict[str, float]]) -> None:
    for row in rows:
        diameter = float(row.get("pore_diameter_nm", 0.0))
        differential_volume = row.get("differential_pore_volume_cm3_g")
        if diameter > 1e-12 and _valid_number(differential_volume):
            row["differential_pore_area_m2_g"] = 4000.0 * float(differential_volume) / diameter


def bjh_pore_distribution(
    result: TriStarResult,
    phase: str = "desorption",
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
    thickness_params: dict[str, float] | None = None,
    correction: str = "standard",
    open_pore_fraction: float = 0.0,
    smooth: bool = True,
) -> PoreDistributionResult:
    """Approximate BJH pore-size distribution from one isotherm branch.

    The current implementation uses the Kelvin equation plus the selected
    adsorbed-film thickness equation. Correction-specific variants and
    open-pore fraction are reserved inputs until their vendor definitions are
    decoded.
    """
    phase = "adsorption" if phase == "adsorption" else "desorption"
    if _uses_official_bjh_table(result):
        flex_official_rows = _micromeritics_flex_official_bjh_rows(
            result,
            phase,
            thickness_method,
            correction,
            open_pore_fraction,
        )
        if flex_official_rows is not None:
            if smooth:
                flex_official_rows = [dict(row) for row in flex_official_rows]
                _smooth_distribution_rows(flex_official_rows)
            return PoreDistributionResult("BJH", phase, "ok", len(flex_official_rows), rows=flex_official_rows)
        quantachrome_official_rows = _quantachrome_official_bjh_rows(
            result,
            phase,
            thickness_method,
            correction,
            open_pore_fraction,
        )
        if quantachrome_official_rows is not None:
            if smooth:
                quantachrome_official_rows = [dict(row) for row in quantachrome_official_rows]
                _smooth_quantachrome_distribution_rows(quantachrome_official_rows)
            return PoreDistributionResult(
                "BJH",
                phase,
                "ok",
                len(quantachrome_official_rows),
                rows=quantachrome_official_rows,
            )
        official_rows = _bsd_official_bjh_rows(result, phase, thickness_method, correction, open_pore_fraction)
        if official_rows is not None:
            return PoreDistributionResult("BJH", phase, "ok", len(official_rows), rows=official_rows)
    points = _bjh_branch_points(result, phase)
    points = sorted(points, key=lambda point: float(point.relative_pressure), reverse=True)
    if len(points) < 3:
        return PoreDistributionResult("BJH", phase, "not_enough_points", len(points))

    density_factor = density_conversion_factor(result)
    temperature_k = result.run_conditions.bath_temperature_K or 77.350
    if not (50.0 < float(temperature_k) < 150.0):
        temperature_k = 77.350
    bsd_bjh = _uses_bsd_defaults(result)
    jwgb_bjh = _uses_jwgb_defaults(result)
    arithmetic_interval_bjh = bsd_bjh or jwgb_bjh
    if arithmetic_interval_bjh and thickness_method == "reference":
        thickness_method = "halsey"
        thickness_params = dict(THICKNESS_METHOD_DEFAULT_PARAMS["halsey"])
    flex_bjh = _uses_micromeritics_flex_bjh_recalculation_defaults(result)
    flex_bjh_adsorption = flex_bjh and phase == "adsorption"
    kelvin_factor_nm = (
        MICROMERITICS_FLEX_BJH_EFFECTIVE_ADSORBATE_PROPERTY_FACTOR_NM
        if flex_bjh_adsorption
        else _bjh_kelvin_factor_nm(result)
    )
    base_rows: list[dict[str, float]] = []
    for point_index, point in enumerate(points):
        pressure = float(point.relative_pressure)
        geometry_pressure = min(pressure, 0.99) if bsd_bjh and point_index == 0 else pressure
        quantity = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        liquid_volume = _bjh_liquid_volume_cm3_g(result, point, density_factor)
        film_thickness = _bjh_film_thickness_nm(geometry_pressure, thickness_method, thickness_params)
        kelvin_radius = _kelvin_radius_nm(geometry_pressure, temperature_k, kelvin_factor_nm)
        if film_thickness is None or kelvin_radius is None or liquid_volume < 0.0:
            continue
        pore_radius = kelvin_radius + film_thickness
        pore_diameter = 2.0 * pore_radius
        if not _valid_number(pore_diameter) or pore_diameter <= 0.0:
            continue
        base_rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": geometry_pressure,
                "measured_relative_pressure": pressure,
                "quantity_adsorbed_cm3_g_stp": quantity,
                "liquid_volume_cm3_g": liquid_volume,
                "film_thickness_nm": film_thickness,
                "kelvin_radius_nm": kelvin_radius,
                "pore_diameter_nm": pore_diameter,
            }
        )

    if len(base_rows) < 3:
        return PoreDistributionResult("BJH", phase, "not_enough_valid_points", len(base_rows), rows=base_rows)

    flex_faas_correction = flex_bjh and correction == "faas"
    use_standard_correction = correction == "standard" or flex_faas_correction
    quantachrome_bjh = _uses_quantachrome_defaults(result) and use_standard_correction and thickness_method == "harkins_jura"
    standard_increments: dict[int, float] = {}
    if use_standard_correction:
        flex_wall_factor = None
        if flex_bjh:
            flex_wall_factor = (
                MICROMERITICS_FLEX_BJH_ADSORPTION_WALL_CORRECTION_FACTOR
                if phase == "adsorption"
                else MICROMERITICS_FLEX_BJH_DESORPTION_WALL_CORRECTION_FACTOR
            )
        standard_increments = _bjh_standard_increment_volumes(
            base_rows,
            thickness_method,
            thickness_params,
            temperature_k,
            flex_standard=flex_bjh,
            flex_wall_factor=flex_wall_factor,
        )

    distribution_rows: list[dict[str, float]] = []
    cumulative_volume = 0.0
    cumulative_area = 0.0
    if quantachrome_bjh:
        minimum_pore_diameter = QUANTACHROME_BJH_MIN_DIAMETER_NM
    elif jwgb_bjh:
        minimum_pore_diameter = JWGB_BJH_MIN_DIAMETER_NM
    elif use_standard_correction and _uses_micromeritics_flex_bjh_recalculation_defaults(result):
        minimum_pore_diameter = DEFAULT_BJH_DIAMETER_MIN_NM
    else:
        minimum_pore_diameter = _bjh_minimum_diameter_nm(thickness_method) if use_standard_correction else DEFAULT_BJH_DIAMETER_MIN_NM
    for index in range(len(base_rows) - 1):
        high = base_rows[index]
        low = base_rows[index + 1]
        high_diameter = float(high["pore_diameter_nm"])
        low_diameter = float(low["pore_diameter_nm"])
        if high_diameter <= 0.0 or low_diameter <= 0.0:
            continue
        range_high_diameter, range_low_diameter, dlog_diameter = _bjh_interval_report_range_nm(
            high_diameter,
            low_diameter,
            flex_bjh=flex_bjh,
        )
        if dlog_diameter <= 1e-12:
            continue
        if use_standard_correction:
            incremental_volume = standard_increments.get(index)
            if incremental_volume is None:
                if not quantachrome_bjh:
                    continue
                incremental_volume = 0.0
        else:
            incremental_volume = abs(float(high["liquid_volume_cm3_g"]) - float(low["liquid_volume_cm3_g"]))
        if incremental_volume < 0.0 or (incremental_volume == 0.0 and not quantachrome_bjh):
            continue
        if use_standard_correction:
            pore_diameter = _bjh_interval_average_diameter_nm(high, low, bsd_bjh=arithmetic_interval_bjh)
        else:
            pore_diameter = math.sqrt(high_diameter * low_diameter)
        if pore_diameter > DEFAULT_BJH_DIAMETER_MAX_NM:
            continue
        if pore_diameter < minimum_pore_diameter:
            continue
        differential = incremental_volume / dlog_diameter
        cumulative_volume += incremental_volume
        incremental_area = _bjh_pore_area_m2_g(incremental_volume, pore_diameter)
        cumulative_area += incremental_area
        row = {
            "phase": phase,
            "interval_index": float(index + 1),
            "relative_pressure_high": float(high["relative_pressure"]),
            "relative_pressure_low": float(low["relative_pressure"]),
            "pore_diameter_nm": pore_diameter,
            "cumulative_pore_diameter_nm": range_low_diameter,
            "pore_diameter_range_high_nm": range_high_diameter,
            "pore_diameter_range_low_nm": range_low_diameter,
            "incremental_pore_volume_cm3_g": incremental_volume,
            "cumulative_pore_volume_cm3_g": cumulative_volume,
            "dlog_diameter": dlog_diameter,
            "differential_pore_volume_cm3_g": differential,
            "raw_differential_pore_volume_cm3_g": differential,
            "film_thickness_nm": (float(high["film_thickness_nm"]) + float(low["film_thickness_nm"])) / 2.0,
            "kelvin_radius_nm": (float(high["kelvin_radius_nm"]) + float(low["kelvin_radius_nm"])) / 2.0,
            "bjh_correction": correction,
            "open_pore_fraction": float(open_pore_fraction),
        }
        _set_bjh_area_fields(row, cumulative_area)
        distribution_rows.append(row)

    if len(distribution_rows) < 2:
        return PoreDistributionResult("BJH", phase, "not_enough_distribution_points", len(distribution_rows), rows=distribution_rows)
    if quantachrome_bjh:
        distribution_rows.sort(key=lambda row: float(row["pore_diameter_nm"]))
        _apply_quantachrome_bjh_low_diameter_increment_correction(distribution_rows)
        cumulative_volume = 0.0
        for row in distribution_rows:
            cumulative_volume += float(row["incremental_pore_volume_cm3_g"])
            row["cumulative_pore_volume_cm3_g"] = cumulative_volume
        _recalculate_bjh_area_fields(distribution_rows)
    if smooth:
        if quantachrome_bjh:
            _smooth_quantachrome_distribution_rows(distribution_rows)
        else:
            _smooth_distribution_rows(distribution_rows)
    return PoreDistributionResult("BJH", phase, "ok", len(distribution_rows), rows=distribution_rows)


def _apply_quantachrome_bjh_low_diameter_increment_correction(rows: list[dict[str, float]]) -> None:
    low, high = QUANTACHROME_BJH_LOW_DIAMETER_CORRECTION_RANGE_NM
    for row in rows:
        diameter = float(row["pore_diameter_nm"])
        if not (low <= diameter < high):
            continue
        incremental_volume = (
            float(row["incremental_pore_volume_cm3_g"])
            * QUANTACHROME_BJH_LOW_DIAMETER_INCREMENT_SCALE
        )
        row["incremental_pore_volume_cm3_g"] = incremental_volume
        dlog_diameter = float(row["dlog_diameter"])
        differential = incremental_volume / dlog_diameter if dlog_diameter > 1e-12 else 0.0
        row["differential_pore_volume_cm3_g"] = differential
        row["raw_differential_pore_volume_cm3_g"] = differential


def _smooth_quantachrome_distribution_rows(rows: list[dict[str, float]]) -> None:
    if len(rows) < 3:
        return
    for field in (
        "differential_pore_volume_cm3_g",
        "differential_pore_volume_per_nm_cm3_g_nm",
        "differential_pore_area_m2_g",
        "differential_pore_area_per_nm_m2_g_nm",
    ):
        values: list[float] = []
        row_indices: list[int] = []
        for index, row in enumerate(rows):
            value = row.get(field)
            if _valid_number(value):
                values.append(float(value))
                row_indices.append(index)
        if len(values) < 3:
            continue
        smoothed = _moving_point_average(np.asarray(values, dtype=float))
        for index, value in zip(row_indices, smoothed):
            rows[index][field] = max(0.0, float(value))


def _moving_point_average(values: np.ndarray) -> np.ndarray:
    smoothed = np.array(values, dtype=float, copy=True)
    for index in range(len(values)):
        start = max(0, index - 1)
        stop = min(len(values), index + 2)
        smoothed[index] = float(np.mean(values[start:stop]))
    return smoothed


def bjh_pore_volume_cm3_g(
    result: TriStarResult,
    diameter_min_nm: float = 2.0,
    diameter_max_nm: float = 10.0,
    phase: str = "adsorption",
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
    thickness_params: dict[str, float] | None = None,
    correction: str = "standard",
    open_pore_fraction: float = 0.0,
) -> float | None:
    distribution = bjh_pore_distribution(
        result,
        phase=phase,
        thickness_method=thickness_method,
        thickness_params=thickness_params,
        correction=correction,
        open_pore_fraction=open_pore_fraction,
        smooth=False,
    )
    if not distribution.rows:
        return None
    lo, hi = sorted((float(diameter_min_nm), float(diameter_max_nm)))
    volume = 0.0
    for row in distribution.rows:
        diameter = float(row["pore_diameter_nm"])
        if lo <= diameter <= hi:
            volume += float(row["incremental_pore_volume_cm3_g"])
    return volume


def kelvin_radius_nm(relative_pressure: float, temperature_k: float = 77.350) -> float | None:
    return _kelvin_radius_nm(relative_pressure, temperature_k, DEFAULT_N2_ADSORBATE_PROPERTY_FACTOR_NM)


def _kelvin_radius_nm(
    relative_pressure: float,
    temperature_k: float = 77.350,
    factor_nm: float = DEFAULT_N2_ADSORBATE_PROPERTY_FACTOR_NM,
) -> float | None:
    if not (0.0 < relative_pressure < 1.0) or temperature_k <= 0.0:
        return None
    factor = factor_nm * (77.350 / float(temperature_k))
    radius_nm = -factor / math.log(relative_pressure)
    return radius_nm if _valid_number(radius_nm) and radius_nm > 0.0 else None


def _bjh_interval_report_range_nm(
    high_diameter_nm: float,
    low_diameter_nm: float,
    *,
    flex_bjh: bool = False,
) -> tuple[float, float, float]:
    high = float(high_diameter_nm)
    low = float(low_diameter_nm)
    # Flex/MicroActive prints one-decimal pore ranges in distribution reports,
    # but copied dV/dlog(w) graph data is based on the exact cumulative pore
    # width boundaries. Keep the calculation boundary exact and leave display
    # rounding to the UI/reporting layer.
    dlog = abs(math.log10(high) - math.log10(low))
    return high, low, dlog


def _smooth_distribution_rows(rows: list[dict[str, float]]) -> None:
    if len(rows) <= SMOOTH_DERIVATIVE_WINDOW - 1:
        return
    diameters = np.array(
        [float(row.get("cumulative_pore_diameter_nm", row["pore_diameter_nm"])) for row in rows],
        dtype=float,
    )
    cumulative = np.array([float(row["cumulative_pore_volume_cm3_g"]) for row in rows], dtype=float)
    if np.any(diameters <= 0.0) or not np.all(np.isfinite(diameters)) or not np.all(np.isfinite(cumulative)):
        return
    log_diameter = np.log10(diameters)
    if float(log_diameter.max()) == float(log_diameter.min()):
        return

    grid_x = np.linspace(float(log_diameter.min()), float(log_diameter.max()), SMOOTH_LOG_GRID_INTERVALS + 1)
    grid_h = float(grid_x[1] - grid_x[0])
    grid_cumulative = _akima_interpolate_array(log_diameter, cumulative, grid_x)
    derivative_per_grid_step = _nine_point_smoothed_derivative(grid_cumulative)

    path_sign = 1.0 if log_diameter[-1] > log_diameter[0] else -1.0
    grid_log_diff = path_sign * derivative_per_grid_step / grid_h
    smooth_log = _akima_interpolate_array(grid_x, grid_log_diff, log_diameter)
    for row, value in zip(rows, smooth_log):
        if _valid_number(value):
            row["differential_pore_volume_cm3_g"] = float(value)
            diameter = float(row.get("cumulative_pore_diameter_nm", row.get("pore_diameter_nm", 0.0)))
            if diameter > 1e-12:
                row["differential_pore_volume_per_nm_cm3_g_nm"] = float(value) / (math.log(10.0) * diameter)

    cumulative_area = np.array([float(row.get("cumulative_pore_area_m2_g", float("nan"))) for row in rows], dtype=float)
    if np.all(np.isfinite(cumulative_area)):
        grid_cumulative_area = _akima_interpolate_array(log_diameter, cumulative_area, grid_x)
        area_derivative_per_grid_step = _nine_point_smoothed_derivative(grid_cumulative_area)
        grid_area_log_diff = path_sign * area_derivative_per_grid_step / grid_h
        smooth_area_log = _akima_interpolate_array(grid_x, grid_area_log_diff, log_diameter)
        for row, value in zip(rows, smooth_area_log):
            if _valid_number(value):
                row["differential_pore_area_m2_g"] = float(value)
                diameter = float(row.get("cumulative_pore_diameter_nm", row.get("pore_diameter_nm", 0.0)))
                if diameter > 1e-12:
                    row["differential_pore_area_per_nm_m2_g_nm"] = float(value) / (math.log(10.0) * diameter)
    else:
        _update_bjh_differential_area_from_volume(rows)


def _akima_interpolate_array(
    x_values: np.ndarray,
    y_values: np.ndarray,
    target_x: np.ndarray,
) -> np.ndarray:
    x_unique, y_unique = _unique_sorted_xy(x_values, y_values)
    if len(x_unique) < 2:
        return np.zeros_like(target_x, dtype=float)
    if len(x_unique) < 5:
        return np.interp(target_x, x_unique, y_unique)

    return np.asarray([_akima_interpolate_scalar(float(x), x_unique, y_unique) for x in target_x], dtype=float)


def _unique_sorted_xy(x_values: np.ndarray, y_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x_values)
    sorted_x = x_values[order]
    sorted_y = y_values[order]
    unique_x: list[float] = []
    unique_y: list[float] = []
    for x_value, y_value in zip(sorted_x, sorted_y):
        if unique_x and float(x_value) == unique_x[-1]:
            unique_y[-1] = float(y_value)
        else:
            unique_x.append(float(x_value))
            unique_y.append(float(y_value))
    return np.array(unique_x, dtype=float), np.array(unique_y, dtype=float)


def _akima_interpolate_scalar(value: float, xs: np.ndarray, ys: np.ndarray) -> float:
    if value <= xs[0]:
        return float(ys[0])
    if value >= xs[-1]:
        return float(ys[-1])
    interval = int(np.searchsorted(xs, value, side="right") - 1)
    interval = max(0, min(interval, len(xs) - 2))

    slopes = [(ys[index + 1] - ys[index]) / (xs[index + 1] - xs[index]) for index in range(len(xs) - 1)]
    extended = [0.0] * (len(xs) + 3)
    extended[2:-2] = slopes
    extended[1] = 2.0 * extended[2] - extended[3]
    extended[0] = 2.0 * extended[1] - extended[2]
    extended[-2] = 2.0 * extended[-3] - extended[-4]
    extended[-1] = 2.0 * extended[-2] - extended[-3]

    deltas = [abs(extended[index + 1] - extended[index]) for index in range(len(extended) - 1)]
    max_weight = 0.0
    weights: list[tuple[float, float]] = []
    for index in range(len(xs)):
        left_weight = deltas[index + 2]
        right_weight = deltas[index]
        weights.append((left_weight, right_weight))
        max_weight = max(max_weight, left_weight + right_weight)

    threshold = 1e-9 * max_weight
    derivatives = []
    for index, (left_weight, right_weight) in enumerate(weights):
        total_weight = left_weight + right_weight
        if total_weight > threshold:
            derivatives.append((left_weight * extended[index + 1] + right_weight * extended[index + 2]) / total_weight)
        else:
            derivatives.append(0.5 * (extended[index + 3] + extended[index]))

    x0 = xs[interval]
    x1 = xs[interval + 1]
    y0 = ys[interval]
    y1 = ys[interval + 1]
    span = x1 - x0
    if span == 0.0:
        return float(y0)
    ratio = (value - x0) / span
    ratio2 = ratio * ratio
    ratio3 = ratio2 * ratio
    return float(
        (2.0 * ratio3 - 3.0 * ratio2 + 1.0) * y0
        + (ratio3 - 2.0 * ratio2 + ratio) * span * derivatives[interval]
        + (-2.0 * ratio3 + 3.0 * ratio2) * y1
        + (ratio3 - ratio2) * span * derivatives[interval + 1]
    )


def _nine_point_smoothed_derivative(y_values: np.ndarray) -> np.ndarray:
    derivative = np.zeros_like(y_values, dtype=float)
    half_window = SMOOTH_DERIVATIVE_WINDOW // 2
    for index in range(len(y_values)):
        if index == 0:
            derivative[index] = _anchored_linear_derivative(
                y_values,
                index,
                0,
                min(len(y_values), half_window + 1),
            )
        elif index < half_window:
            derivative[index] = _local_linear_derivative(
                y_values,
                index,
                0,
                min(len(y_values), index + half_window),
            )
        elif index >= len(y_values) - half_window:
            derivative[index] = _local_linear_derivative(
                y_values,
                index,
                max(0, index - half_window),
                len(y_values),
            )
        else:
            derivative[index] = _local_linear_derivative(
                y_values,
                index,
                index - half_window,
                index + half_window + 1,
            )
    return derivative


def _local_linear_derivative(y_values: np.ndarray, center_index: int, start: int, stop: int) -> float:
    indexes = np.arange(start, stop, dtype=float)
    offsets = indexes - float(center_index)
    design = np.vstack([np.ones_like(offsets), offsets]).T
    coefficients = np.linalg.lstsq(design, y_values[start:stop], rcond=None)[0]
    return float(coefficients[1])


def _anchored_linear_derivative(y_values: np.ndarray, center_index: int, start: int, stop: int) -> float:
    indexes = np.arange(start, stop, dtype=float)
    offsets = indexes - float(center_index)
    values = y_values[start:stop]
    mask = offsets != 0.0
    denominator = float(np.sum(offsets[mask] ** 2))
    if denominator == 0.0:
        return 0.0
    return float(np.sum(offsets[mask] * (values[mask] - y_values[center_index])) / denominator)


def _bjh_liquid_volume_cm3_g(result: TriStarResult, point: IsothermPoint, density_factor: float) -> float:
    scale = QUANTACHROME_BJH_LIQUID_VOLUME_SCALE if _uses_quantachrome_defaults(result) else 1.0
    return float(point.quantity_adsorbed_cm3_g_stp or 0.0) * density_factor * scale


def _bjh_interval_average_diameter_nm(
    high: dict[str, float],
    low: dict[str, float],
    *,
    bsd_bjh: bool = False,
) -> float:
    """Return the MicroActive-style plotted BJH interval diameter."""
    if bsd_bjh:
        return 0.5 * (float(high["pore_diameter_nm"]) + float(low["pore_diameter_nm"]))
    high_kelvin_radius = float(high["kelvin_radius_nm"])
    low_kelvin_radius = float(low["kelvin_radius_nm"])
    denominator = high_kelvin_radius * high_kelvin_radius + low_kelvin_radius * low_kelvin_radius
    if denominator <= 0.0:
        return math.sqrt(float(high["pore_diameter_nm"]) * float(low["pore_diameter_nm"]))
    diameter = (
        2.0
        * (high_kelvin_radius + low_kelvin_radius)
        * high_kelvin_radius
        * low_kelvin_radius
        / denominator
    )
    high_thickness = float(high["film_thickness_nm"])
    low_thickness = float(low["film_thickness_nm"])
    if high_thickness > 0.0 and low_thickness > 0.0:
        diameter += high_thickness + low_thickness
    return diameter


def _bjh_standard_increment_volumes(
    base_rows: Sequence[dict[str, float]],
    thickness_method: str,
    thickness_params: dict[str, float] | None,
    temperature_k: float = 77.350,
    *,
    flex_standard: bool = False,
    flex_wall_factor: float | None = None,
) -> dict[int, float]:
    """Micromeritics-style BJH wall correction for MicroActive collected data."""
    if len(base_rows) < 3:
        return {}
    pores: list[dict[str, float]] = []
    increments: dict[int, float] = {}
    for index in range(len(base_rows) - 1):
        high = base_rows[index]
        low = base_rows[index + 1]
        volume_step = float(high["liquid_volume_cm3_g"]) - float(low["liquid_volume_cm3_g"])
        if volume_step <= 0.0:
            continue

        high_wall_thickness = _bjh_wall_correction_thickness_nm(high, thickness_method, thickness_params)
        low_wall_thickness = _bjh_wall_correction_thickness_nm(low, thickness_method, thickness_params)
        wall_volume = 0.0
        for pore in pores:
            radius = float(pore["wall_radius_nm"])
            denominator_radius = float(pore["wall_denominator_radius_nm"])
            pore_volume = float(pore["pore_volume_cm3_g"])
            high_core = max(0.0, radius - high_wall_thickness)
            low_core = max(0.0, radius - low_wall_thickness)
            if denominator_radius <= 0.0:
                continue
            wall_volume += max(
                0.0,
                pore_volume * (low_core * low_core - high_core * high_core) / (denominator_radius * denominator_radius),
            )
        if flex_standard:
            wall_volume *= (
                float(flex_wall_factor)
                if flex_wall_factor is not None
                else MICROMERITICS_FLEX_BJH_WALL_CORRECTION_FACTOR
            )
        core_volume = volume_step - wall_volume
        if core_volume <= 0.0:
            continue

        high_pore_radius = float(high["kelvin_radius_nm"]) + float(high["film_thickness_nm"])
        low_pore_radius = float(low["kelvin_radius_nm"]) + float(low["film_thickness_nm"])
        high_kelvin_radius = float(high["kelvin_radius_nm"])
        low_kelvin_radius = float(low["kelvin_radius_nm"])
        if high_pore_radius <= 0.0 or low_pore_radius <= 0.0 or high_kelvin_radius <= 0.0 or low_kelvin_radius <= 0.0:
            continue

        if flex_standard:
            # 3Flex BJH follows the same desorption-model recurrence, but its
            # collected-data report is closer when the newly opened pore length
            # is carried on the report average diameter rather than on the
            # earlier harmonic-radius 3Flex approximation.
            pore_radius = math.sqrt(high_pore_radius * low_pore_radius)
            empty_radius = math.sqrt(high_kelvin_radius * low_kelvin_radius)
            wall_radius = _bjh_interval_average_diameter_nm(high, low) / 2.0
            wall_denominator_radius = wall_radius
        else:
            pore_radius = math.sqrt(high_pore_radius * low_pore_radius)
            empty_radius = math.sqrt(high_kelvin_radius * low_kelvin_radius)
            wall_radius = pore_radius
            wall_denominator_radius = pore_radius
        incremental_volume = core_volume * (pore_radius / empty_radius) ** 2
        if incremental_volume <= 0.0:
            continue

        increments[index] = incremental_volume
        pores.append(
            {
                "pore_radius_nm": pore_radius,
                "wall_radius_nm": wall_radius,
                "wall_denominator_radius_nm": wall_denominator_radius,
                "pore_volume_cm3_g": incremental_volume,
            }
        )
    return increments


def _harmonic_mean(left: float, right: float) -> float:
    denominator = left + right
    if denominator <= 0.0:
        return math.sqrt(left * right)
    return 2.0 * left * right / denominator


def _bjh_wall_correction_thickness_nm(
    row: dict[str, float],
    thickness_method: str,
    thickness_params: dict[str, float] | None,
) -> float:
    if thickness_method != "reference":
        return float(row["film_thickness_nm"])
    thickness = _bjh_reference_wall_thickness_nm(float(row["relative_pressure"]), thickness_params)
    if thickness is None:
        return float(row["film_thickness_nm"])
    return thickness


def _bjh_reference_wall_thickness_nm(
    relative_pressure: float,
    params: dict[str, float] | None,
) -> float | None:
    points = normalize_reference_points((params or {}).get("reference_points"))
    if not points:
        return None
    pressure = float(relative_pressure)
    if pressure > points[-1][0]:
        return 0.0
    if len(points) >= 5 and points[0][0] <= pressure <= points[-1][0]:
        xs = np.array([point[0] for point in points], dtype=float)
        ys = np.array([point[1] for point in points], dtype=float)
        thickness = _akima_interpolate_scalar(pressure, xs, ys)
        if _valid_number(thickness) and thickness > 0.0:
            return float(thickness)
    return reference_thickness_nm(pressure, {"reference_points": points})


def _bjh_minimum_diameter_nm(thickness_method: str) -> float:
    return MICROACTIVE_BJH_MIN_DIAMETER_BY_THICKNESS_METHOD.get(
        thickness_method,
        DEFAULT_BJH_DIAMETER_MIN_NM,
    )


def _bjh_film_thickness_nm(
    relative_pressure: float,
    method: str,
    params: dict[str, float] | None,
) -> float | None:
    if method == "reference":
        points = normalize_reference_points((params or {}).get("reference_points"))
        if points and relative_pressure > points[-1][0]:
            return 0.0
    return thickness_nm(relative_pressure, method, params)


def harkins_jura_thickness_nm(
    relative_pressure: float,
    params: dict[str, float] | None = None,
) -> float | None:
    return thickness_nm(relative_pressure, "harkins_jura", params)


def thickness_nm(
    relative_pressure: float,
    method: str = DEFAULT_THICKNESS_METHOD,
    params: dict[str, float] | None = None,
) -> float | None:
    if not (0.0 < relative_pressure < 1.0):
        return None
    method = method if method in THICKNESS_METHOD_DEFAULT_PARAMS else DEFAULT_THICKNESS_METHOD
    merged_params = _thickness_params(method, params)
    if method == "reference":
        return reference_thickness_nm(relative_pressure, merged_params)
    if method == "halsey":
        return _halsey_thickness_nm(relative_pressure, merged_params)
    if method == "broekhoff_de_boer":
        return _broekhoff_de_boer_thickness_nm(relative_pressure, merged_params)
    if method == "carbon_black_stsa":
        return _carbon_black_stsa_thickness_nm(relative_pressure, merged_params)
    return _power_log_thickness_nm(relative_pressure, merged_params)


def _thickness_params(method: str, params: dict[str, object] | None) -> dict[str, object]:
    defaults = THICKNESS_METHOD_DEFAULT_PARAMS.get(method, THICKNESS_METHOD_DEFAULT_PARAMS[DEFAULT_THICKNESS_METHOD])
    merged = dict(defaults)
    if params:
        merged.update(params)
    return merged


def _power_log_thickness_nm(relative_pressure: float, params: dict[str, float]) -> float | None:
    numerator = float(params["numerator"])
    offset = float(params["offset"])
    exponent = float(params["exponent"])
    scale = float(params["scale"])
    denominator = offset - math.log10(relative_pressure)
    if denominator <= 0.0:
        return None
    base = numerator / denominator
    if base <= 0.0:
        return None
    return scale * (base**exponent)


def _halsey_thickness_nm(relative_pressure: float, params: dict[str, float]) -> float | None:
    prefactor = float(params["prefactor"])
    numerator = float(params["numerator"])
    exponent = float(params["exponent"])
    scale = float(params["scale"])
    denominator = math.log(relative_pressure)
    if denominator == 0.0:
        return None
    base = numerator / denominator
    if base <= 0.0:
        return None
    thickness_angstrom = prefactor * (base**exponent)
    return scale * thickness_angstrom if thickness_angstrom > 0.0 else None


def _broekhoff_de_boer_thickness_nm(relative_pressure: float, params: dict[str, float]) -> float | None:
    target_log = math.log10(relative_pressure)
    inverse_square = float(params["inverse_square"])
    exponential_factor = float(params["exponential_factor"])
    exponential_rate = float(params["exponential_rate"])
    scale = float(params["scale"])

    def value(t_angstrom: float) -> float:
        return inverse_square / (t_angstrom * t_angstrom) + exponential_factor * math.exp(exponential_rate * t_angstrom) - target_log

    lo = 0.05
    hi = 200.0
    f_lo = value(lo)
    f_hi = value(hi)
    while f_lo * f_hi > 0.0 and hi < 2000.0:
        hi *= 2.0
        f_hi = value(hi)
    if f_lo * f_hi > 0.0:
        return None

    for _ in range(80):
        mid = (lo + hi) / 2.0
        f_mid = value(mid)
        if abs(f_mid) < 1e-12:
            return scale * mid
        if f_lo * f_mid <= 0.0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return scale * ((lo + hi) / 2.0)


def _carbon_black_stsa_thickness_nm(relative_pressure: float, params: dict[str, float]) -> float | None:
    constant = float(params["constant"])
    linear = float(params["linear"])
    quadratic = float(params["quadratic"])
    scale = float(params["scale"])
    thickness_angstrom = constant + linear * relative_pressure + quadratic * relative_pressure * relative_pressure
    return scale * thickness_angstrom if thickness_angstrom > 0.0 else None


def _points_in_range(points: Sequence[IsothermPoint], p_min: float, p_max: float) -> list[IsothermPoint]:
    lo, hi = sorted((float(p_min), float(p_max)))
    tolerance = max(1e-12, max(abs(lo), abs(hi), 1.0) * 1e-9)
    return [point for point in points if lo - tolerance <= float(point.relative_pressure) <= hi + tolerance]


def _linear_fit(x_values: Sequence[float], y_values: Sequence[float]) -> tuple[float, float, float]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residual = float(np.sum((y - fitted) ** 2))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - residual / total if total > 0.0 else 1.0
    return float(slope), float(intercept), float(r_squared)


def _linear_fit_standard_errors(
    x_values: Sequence[float],
    y_values: Sequence[float],
    slope: float,
    intercept: float,
) -> tuple[float | None, float | None]:
    if len(x_values) < 3:
        return None, None
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    sxx = float(np.sum((x - np.mean(x)) ** 2))
    if sxx <= 0.0:
        return None, None
    fitted = slope * x + intercept
    residual_variance = float(np.sum((y - fitted) ** 2)) / float(x.size - 2)
    if residual_variance < 0.0:
        return None, None
    slope_se = math.sqrt(residual_variance / sxx)
    intercept_se = math.sqrt(residual_variance * (1.0 / x.size + float(np.mean(x)) ** 2 / sxx))
    return float(slope_se), float(intercept_se)


def _valid_number(value: float | None) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
