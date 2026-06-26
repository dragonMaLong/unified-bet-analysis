from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import numpy as np

from .dft_models import interpolate_dft_kernel, load_dft_model_kernel
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
DEFAULT_DH_DIAMETER_MIN_NM = 0.75
JWGB_BJH_MIN_DIAMETER_NM = 2.0
MMHG_TO_KPA = 101.325 / 760.0
DFT_DEFAULT_ANALYSIS_TYPE = "dft_pore"
DFT_DEFAULT_GEOMETRY = "slit"
DFT_DEFAULT_MODEL = "n2_dft_model"
DFT_DEFAULT_REGULARIZATION = 0.316
DFT_REGULARIZATION_VALUES = (
    0.0,
    0.00001,
    0.00003,
    0.00010,
    0.00032,
    0.00100,
    0.00316,
    0.01000,
    0.03160,
    0.10000,
    0.31600,
    1.00000,
    3.16000,
    10.00000,
)
HK_AVOGADRO = 6.02214129e23
HK_GAS_CONSTANT_ERG_MOL_K = 8.31441e7
HK_ELECTRON_KINETIC_ENERGY_ERG = 0.8183e-6
HK_DEFAULT_GEOMETRY = "slit"
HK_DEFAULT_ADSORBENT = "zeolite"
HK_DEFAULT_ADSORPTIVE = "N2"
HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4 = 3.490e-43
HK_ADSORBENT_PRESETS: dict[str, dict[str, float | str]] = {
    "zeolite": {
        "label": "Zeolite",
        "diameter_nm": 0.3040,
        "zero_diameter_nm": 0.2609,
        "polarizability_cm3": 8.500e-25,
        "susceptibility_cm3": 1.940e-29,
        "density_per_cm2": 3.750e15,
    },
    "aluminophosphate": {
        "label": "Aluminophosphate",
        "diameter_nm": 0.2600,
        "zero_diameter_nm": 0.2232,
        "polarizability_cm3": 2.500e-24,
        "susceptibility_cm3": 1.300e-29,
        "density_per_cm2": 1.480e15,
    },
    "aluminosilicate": {
        "label": "Aluminosilicate",
        "diameter_nm": 0.2760,
        "zero_diameter_nm": 0.2369,
        "polarizability_cm3": 2.500e-24,
        "susceptibility_cm3": 1.300e-29,
        "density_per_cm2": 1.310e15,
    },
    "carbon_graphite_ross_olivier": {
        "label": "Carbon-Graphite (Ross/Olivier)",
        "diameter_nm": 0.3400,
        "zero_diameter_nm": 0.2918,
        "polarizability_cm3": 1.020e-24,
        "susceptibility_cm3": 1.050e-29,
        "density_per_cm2": 3.845e15,
    },
    "carbon_graphite_hk": {
        "label": "Carbon-Graphite (HK)",
        "diameter_nm": 0.3400,
        "zero_diameter_nm": 0.2918,
        "polarizability_cm3": 1.020e-24,
        "susceptibility_cm3": 1.350e-28,
        "density_per_cm2": 3.845e15,
    },
    "other": {
        "label": "Other",
        "diameter_nm": 0.3000,
        "zero_diameter_nm": 0.2575,
        "polarizability_cm3": 1.000e-24,
        "susceptibility_cm3": 1.000e-29,
        "density_per_cm2": 1.500e15,
    },
}
HK_ADSORPTIVE_PRESETS: dict[str, dict[str, float | str]] = {
    "N2": {
        "label": "N2",
        "diameter_nm": 0.3000,
        "zero_diameter_nm": 0.2574,
        "polarizability_cm3": 1.760e-24,
        "susceptibility_cm3": 3.600e-29,
        "density_per_cm2": 6.710e14,
    },
    "AR": {
        "label": "AR",
        "diameter_nm": 0.2950,
        "zero_diameter_nm": 0.2530,
        "polarizability_cm3": 1.630e-24,
        "susceptibility_cm3": 3.220e-29,
        "density_per_cm2": 7.608e14,
    },
    "CO2": {
        "label": "CO2",
        "diameter_nm": 0.3230,
        "zero_diameter_nm": 0.2770,
        "polarizability_cm3": 2.700e-24,
        "susceptibility_cm3": 5.000e-29,
        "density_per_cm2": 5.450e14,
    },
    "He": {
        "label": "He",
        "diameter_nm": 0.2000,
        "zero_diameter_nm": 0.1000,
        "polarizability_cm3": 1.000e-24,
        "susceptibility_cm3": 1.000e-29,
        "density_per_cm2": 1.000e14,
    },
    "Kr": {
        "label": "Kr",
        "diameter_nm": 0.2000,
        "zero_diameter_nm": 0.1000,
        "polarizability_cm3": 1.000e-24,
        "susceptibility_cm3": 1.000e-29,
        "density_per_cm2": 1.000e14,
    },
}
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


@dataclass(frozen=True)
class DftPoreDistributionResult:
    name: str
    phase: str
    status: str
    point_count: int = 0
    regularization: float = DFT_DEFAULT_REGULARIZATION
    analysis_type: str = DFT_DEFAULT_ANALYSIS_TYPE
    geometry: str = DFT_DEFAULT_GEOMETRY
    model: str = DFT_DEFAULT_MODEL
    rows: list[dict[str, float]] = field(default_factory=list)
    fit_rows: list[dict[str, float]] = field(default_factory=list)
    diagnostic_rows: list[dict[str, float]] = field(default_factory=list)

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


def _uses_official_dh_table(result: TriStarResult) -> bool:
    return bool(
        result.method_options.get("use_official_dh_table")
        or result.method_options.get("use_official_excel_dh_table")
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


def _official_dh_rows(result: TriStarResult, phase: str) -> list[dict[str, float]] | None:
    raw_rows = result.method_options.get(f"micromeritics_dh_{phase}_rows")
    if not isinstance(raw_rows, list) or len(raw_rows) < 2:
        return None

    rows: list[dict[str, float]] = []
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            continue
        try:
            pore_diameter = float(raw["pore_diameter_nm"])
            cumulative_volume = float(raw.get("cumulative_pore_volume_cm3_g", 0.0))
            incremental_volume = float(raw.get("incremental_pore_volume_cm3_g", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if pore_diameter <= 0.0:
            continue
        row: dict[str, float] = {
            "phase": phase,
            "interval_index": float(index),
            "pore_diameter_nm": pore_diameter,
            "incremental_pore_volume_cm3_g": incremental_volume,
            "cumulative_pore_volume_cm3_g": cumulative_volume,
            "dh_correction": "official_excel_table",
            "pore_distribution_source": str(raw.get("pore_distribution_source", "official_excel_table")),
        }
        for key in (
            "cumulative_pore_diameter_nm",
            "pore_diameter_range_high_nm",
            "pore_diameter_range_low_nm",
            "cumulative_pore_area_m2_g",
            "incremental_pore_area_m2_g",
            "dlog_diameter",
            "differential_pore_volume_cm3_g",
            "raw_differential_pore_volume_cm3_g",
            "differential_pore_volume_per_nm_cm3_g_nm",
            "differential_pore_area_m2_g",
            "differential_pore_area_per_nm_m2_g_nm",
        ):
            value = raw.get(key)
            if _valid_number(value):
                row[key] = float(value)
        if "cumulative_pore_diameter_nm" not in row:
            low = row.get("pore_diameter_range_low_nm")
            row["cumulative_pore_diameter_nm"] = float(low) if _valid_number(low) else pore_diameter
        rows.append(row)
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
    *,
    dollimore_heal: bool = False,
) -> PoreDistributionResult:
    """Approximate BJH pore-size distribution from one isotherm branch.

    The current implementation uses the Kelvin equation plus the selected
    adsorbed-film thickness equation. Correction-specific variants and
    open-pore fraction are reserved inputs until their vendor definitions are
    decoded.
    """
    phase = "adsorption" if phase == "adsorption" else "desorption"
    distribution_name = "Dollimore-Heal" if dollimore_heal else "BJH"
    if not dollimore_heal and _uses_official_bjh_table(result):
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
            return PoreDistributionResult(distribution_name, phase, "ok", len(flex_official_rows), rows=flex_official_rows)
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
                distribution_name,
                phase,
                "ok",
                len(quantachrome_official_rows),
                rows=quantachrome_official_rows,
            )
        official_rows = _bsd_official_bjh_rows(result, phase, thickness_method, correction, open_pore_fraction)
        if official_rows is not None:
            return PoreDistributionResult(distribution_name, phase, "ok", len(official_rows), rows=official_rows)
    points = _bjh_branch_points(result, phase)
    points = sorted(points, key=lambda point: float(point.relative_pressure), reverse=True)
    if len(points) < 3:
        return PoreDistributionResult(distribution_name, phase, "not_enough_points", len(points))

    density_factor = density_conversion_factor(result)
    temperature_k = result.run_conditions.bath_temperature_K or 77.350
    if not (50.0 < float(temperature_k) < 150.0):
        temperature_k = 77.350
    bsd_bjh = _uses_bsd_defaults(result)
    jwgb_bjh = _uses_jwgb_defaults(result)
    arithmetic_interval_bjh = bsd_bjh or jwgb_bjh
    if bsd_bjh and thickness_method == "reference":
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
        return PoreDistributionResult(distribution_name, phase, "not_enough_valid_points", len(base_rows), rows=base_rows)

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
    if dollimore_heal and not flex_bjh:
        minimum_pore_diameter = DEFAULT_DH_DIAMETER_MIN_NM
    elif quantachrome_bjh:
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
        if dollimore_heal:
            pore_diameter = 0.5 * (high_diameter + low_diameter)
        elif use_standard_correction:
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
        return PoreDistributionResult(distribution_name, phase, "not_enough_distribution_points", len(distribution_rows), rows=distribution_rows)
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
    return PoreDistributionResult(distribution_name, phase, "ok", len(distribution_rows), rows=distribution_rows)


def dh_pore_distribution(
    result: TriStarResult,
    phase: str = "adsorption",
    thickness_method: str | None = None,
    thickness_params: dict[str, float] | None = None,
    correction: str = "standard",
    open_pore_fraction: float = 0.0,
    smooth: bool | None = None,
) -> PoreDistributionResult:
    """Dollimore-Heal pore-size distribution.

    Micromeritics' calculation guide defines DH as the same report family as
    BJH with a different interval-average pore diameter and pore-length
    treatment. Official Excel DH tables are retained for validation, but normal
    analysis recalculates DH from the imported isotherm.
    """
    phase = "adsorption" if phase == "adsorption" else "desorption"
    use_smooth = bool(smooth) if smooth is not None else bool(result.method_options.get("vendor_dh_smooth_derivative", False))
    if _uses_official_dh_table(result):
        official_rows = _official_dh_rows(result, phase)
        if official_rows is not None:
            rows = [dict(row) for row in official_rows]
            if use_smooth:
                _smooth_distribution_rows(rows)
            return PoreDistributionResult("Dollimore-Heal", phase, "ok", len(rows), rows=rows)

    method = thickness_method or str(
        result.method_options.get(
            "vendor_dh_thickness_method",
            result.method_options.get("vendor_bjh_thickness_method", DEFAULT_THICKNESS_METHOD),
        )
    )
    if method == "auto":
        method = str(result.method_options.get("vendor_dh_thickness_method", DEFAULT_THICKNESS_METHOD))
    return bjh_pore_distribution(
        result,
        phase=phase,
        thickness_method=method,
        thickness_params=thickness_params,
        correction=correction,
        open_pore_fraction=open_pore_fraction,
        smooth=use_smooth,
        dollimore_heal=True,
    )


def dft_pore_distribution(
    result: TriStarResult,
    *,
    analysis_type: str = DFT_DEFAULT_ANALYSIS_TYPE,
    geometry: str = DFT_DEFAULT_GEOMETRY,
    model: str = DFT_DEFAULT_MODEL,
    regularization: float = DFT_DEFAULT_REGULARIZATION,
    phase: str = "adsorption",
    include_diagnostics: bool = True,
) -> DftPoreDistributionResult:
    """Regularized DFT/NLDFT pore distribution.

    Official Micromeritics DFT model files are used when available. The smooth
    analytic kernel remains only as a fallback so the UI can still run when the
    optional model archive is missing.
    """
    phase_key = "adsorption" if phase == "adsorption" else "desorption"
    source_points = adsorption_points(result) if phase_key == "adsorption" else desorption_points(result)
    points = [
        point
        for point in source_points
        if _valid_number(point.relative_pressure)
        and _valid_number(point.quantity_adsorbed_cm3_g_stp)
        and 0.0 < float(point.relative_pressure) < 1.0
        and float(point.quantity_adsorbed_cm3_g_stp or 0.0) >= 0.0
    ]
    points = sorted(points, key=lambda point: float(point.relative_pressure))
    if len(points) < 5:
        return DftPoreDistributionResult("DFT", phase_key, "not_enough_points", len(points))

    geometry_key = str(geometry or DFT_DEFAULT_GEOMETRY).strip().lower()
    if geometry_key not in {"slit", "cylinder"}:
        geometry_key = DFT_DEFAULT_GEOMETRY
    analysis_key = str(analysis_type or DFT_DEFAULT_ANALYSIS_TYPE).strip().lower()
    if analysis_key not in {"dft_pore", "typical"}:
        analysis_key = DFT_DEFAULT_ANALYSIS_TYPE
    model_key = str(model or DFT_DEFAULT_MODEL)
    try:
        lambda_value = float(regularization)
    except (TypeError, ValueError):
        lambda_value = DFT_DEFAULT_REGULARIZATION
    if not _valid_number(lambda_value):
        lambda_value = DFT_DEFAULT_REGULARIZATION
    lambda_value = max(0.0, min(lambda_value, 10.0))

    pressure = np.asarray([float(point.relative_pressure) for point in points], dtype=float)
    quantity_stp = np.asarray([float(point.quantity_adsorbed_cm3_g_stp or 0.0) for point in points], dtype=float)
    target_mmol = np.asarray(
        [
            float(point.quantity_adsorbed_mmol_g)
            if _valid_number(point.quantity_adsorbed_mmol_g)
            else float(point.quantity_adsorbed_cm3_g_stp or 0.0) / 22.414
            for point in points
        ],
        dtype=float,
    )
    target_mmol = np.maximum(target_mmol, 0.0)
    density_factor = density_conversion_factor(result)
    target_liquid = np.maximum(quantity_stp * density_factor, 0.0)
    if not np.any(target_liquid > 0.0):
        return DftPoreDistributionResult("DFT", phase_key, "not_enough_adsorbed_volume", len(points))

    official_kernel = load_dft_model_kernel(model_key)
    if official_kernel is not None:
        pore_widths = np.asarray(official_kernel.pore_widths_nm, dtype=float)
        kernel = interpolate_dft_kernel(official_kernel, pressure)
    else:
        pore_widths = _dft_width_grid_nm(analysis_key)
        kernel = _dft_kernel_matrix(pressure, pore_widths, geometry_key)
    increments = _dft_regularized_nonnegative_solution(kernel, target_liquid, lambda_value)
    model_liquid = kernel @ increments
    residual_mmol_factor = 1.0 / max(density_factor * 22.414, 1e-12)
    diagnostic_rows = (
        _dft_regularization_diagnostics(
            kernel,
            target_liquid,
            target_to_mmol_factor=residual_mmol_factor,
        )
        if include_diagnostics
        else []
    )

    rows: list[dict[str, float]] = []
    cumulative = 0.0
    width_edges = _dft_width_bin_edges_nm(pore_widths)
    for index, (width, increment) in enumerate(zip(pore_widths, increments)):
        width = float(width)
        increment = max(0.0, float(increment))
        cumulative += increment
        width_low = float(width_edges[index])
        width_high = float(width_edges[index + 1])
        width_delta = max(width_high - width_low, 0.0)
        log_delta = math.log10(width_high) - math.log10(width_low) if width_low > 0.0 else 0.0
        differential_log = increment / log_delta if abs(log_delta) > 1e-12 else 0.0
        differential_linear = increment / width_delta if width_delta > 1e-12 else 0.0
        rows.append(
            {
                "phase": phase_key,
                "pore_width_nm": width,
                "pore_diameter_nm": width,
                "cumulative_pore_diameter_nm": width,
                "pore_width_low_nm": width_low,
                "pore_width_high_nm": width_high,
                "incremental_pore_volume_cm3_g": increment,
                "cumulative_pore_volume_cm3_g": cumulative,
                "dwidth_nm": width_delta,
                "dlog_diameter": abs(log_delta),
                "differential_pore_volume_per_nm_cm3_g_nm": differential_linear,
                "differential_pore_volume_cm3_g": differential_log,
                "dft_regularization": lambda_value,
            }
        )

    fit_rows: list[dict[str, float]] = []
    for point, measured_mmol, model_volume in zip(points, target_mmol, model_liquid):
        measured = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        model_quantity = float(model_volume / density_factor) if density_factor > 0.0 else 0.0
        fit_rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": float(point.relative_pressure),
                "quantity_adsorbed_cm3_g_stp": measured,
                "model_quantity_adsorbed_cm3_g_stp": model_quantity,
                "quantity_adsorbed_mmol_g": float(measured_mmol),
                "model_quantity_adsorbed_mmol_g": model_quantity / 22.414,
            }
        )

    if len(rows) < 2:
        return DftPoreDistributionResult("DFT", phase_key, "not_enough_distribution_points", len(rows))
    return DftPoreDistributionResult(
        "DFT",
        phase_key,
        "ok",
        len(points),
        regularization=lambda_value,
        analysis_type=analysis_key,
        geometry=geometry_key,
        model=model_key,
        rows=rows,
        fit_rows=fit_rows,
        diagnostic_rows=diagnostic_rows,
    )


def _dft_width_grid_nm(analysis_type: str) -> np.ndarray:
    if analysis_type == "typical":
        return np.geomspace(0.55, 80.0, 96)
    return np.geomspace(0.45, 100.0, 112)


def _dft_kernel_matrix(pressure: np.ndarray, widths_nm: np.ndarray, geometry: str) -> np.ndarray:
    log_pressure = np.log(np.clip(pressure, 1e-10, 0.999999))
    widths = np.asarray(widths_nm, dtype=float)
    if geometry == "cylinder":
        effective_size = np.maximum(widths * 0.50 - 0.18, 0.035)
        transition_width = 0.55
    else:
        effective_size = np.maximum(widths - 0.32, 0.035)
        transition_width = 0.48
    # Empirical N2 77 K filling pressure used only for the initial scaffold.
    characteristic_log_p = -np.power(0.86 / effective_size, 1.12)
    matrix = 1.0 / (1.0 + np.exp(-(log_pressure[:, None] - characteristic_log_p[None, :]) / transition_width))
    matrix[pressure[:, None] < 1e-9] = 0.0
    return matrix


def _dft_width_bin_edges_nm(widths_nm: np.ndarray) -> np.ndarray:
    widths = np.asarray(widths_nm, dtype=float)
    if widths.size == 0:
        return np.zeros(1, dtype=float)
    if widths.size == 1:
        width = max(float(widths[0]), 1e-9)
        return np.asarray([width * 0.9, width * 1.1], dtype=float)
    edges = np.empty(widths.size + 1, dtype=float)
    edges[1:-1] = np.sqrt(widths[:-1] * widths[1:])
    first_ratio = max(widths[1] / max(widths[0], 1e-12), 1.000001)
    last_ratio = max(widths[-1] / max(widths[-2], 1e-12), 1.000001)
    edges[0] = widths[0] / math.sqrt(first_ratio)
    edges[-1] = widths[-1] * math.sqrt(last_ratio)
    edges = np.maximum(edges, 1e-9)
    return edges


def _dft_second_difference_matrix(size: int) -> np.ndarray:
    if size < 3:
        return np.zeros((0, size), dtype=float)
    matrix = np.zeros((size - 2, size), dtype=float)
    for index in range(size - 2):
        matrix[index, index] = 1.0
        matrix[index, index + 1] = -2.0
        matrix[index, index + 2] = 1.0
    return matrix


def _dft_regularized_nonnegative_solution(
    kernel: np.ndarray,
    target: np.ndarray,
    regularization: float,
) -> np.ndarray:
    if kernel.size == 0 or target.size == 0:
        return np.zeros(kernel.shape[1] if kernel.ndim == 2 else 0, dtype=float)
    target_scale = max(float(np.nanmax(np.abs(target))), 1e-12)
    y = np.asarray(target, dtype=float) / target_scale
    k = np.asarray(kernel, dtype=float)
    column_norm = np.linalg.norm(k, axis=0)
    column_norm[column_norm <= 1e-12] = 1.0
    k_scaled = k / column_norm[None, :]
    size = k_scaled.shape[1]
    second = _dft_second_difference_matrix(size)
    lambda_value = max(0.0, float(regularization)) * 0.02
    gram = k_scaled.T @ k_scaled
    if second.size:
        gram = gram + lambda_value * (second.T @ second)
    gram = gram + 1e-9 * np.eye(size)
    rhs = k_scaled.T @ y
    lipschitz = float(np.linalg.norm(gram, ord=2))
    if not (_valid_number(lipschitz) and lipschitz > 1e-12):
        lipschitz = 1.0
    x = np.zeros(size, dtype=float)
    z = x.copy()
    t = 1.0
    for _ in range(900):
        gradient = gram @ z - rhs
        next_x = np.maximum(0.0, z - gradient / lipschitz)
        next_t = 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * t * t))
        z = next_x + ((t - 1.0) / next_t) * (next_x - x)
        x = next_x
        t = next_t
    return np.maximum(0.0, x / column_norm * target_scale)


def _dft_solution_metrics(
    kernel: np.ndarray,
    target: np.ndarray,
    increments: np.ndarray,
    *,
    target_to_mmol_factor: float = 1.0,
) -> tuple[float, float]:
    fit = kernel @ increments
    residual_mmol_g = (fit - target) * float(target_to_mmol_factor)
    rms_mmol_g = math.sqrt(float(np.mean(np.square(residual_mmol_g))))
    if increments.size >= 3:
        second = np.diff(increments, n=2)
        denominator = max(float(np.sum(np.abs(increments))), 1e-12)
        roughness = float(np.sqrt(np.mean(np.square(second))) / denominator * increments.size * increments.size)
    else:
        roughness = 0.0
    return rms_mmol_g, roughness


def _dft_regularization_diagnostics(
    kernel: np.ndarray,
    target: np.ndarray,
    *,
    target_to_mmol_factor: float = 1.0,
) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for value in DFT_REGULARIZATION_VALUES:
        increments = _dft_regularized_nonnegative_solution(kernel, target, value)
        rms, roughness = _dft_solution_metrics(
            kernel,
            target,
            increments,
            target_to_mmol_factor=target_to_mmol_factor,
        )
        rows.append(
            {
                "regularization": float(value),
                "rms_error_mmol_g": rms,
                "distribution_roughness": roughness,
            }
        )
    return rows


def hk_adsorbent_presets() -> dict[str, dict[str, float | str]]:
    return {key: dict(value) for key, value in HK_ADSORBENT_PRESETS.items()}


def hk_adsorptive_presets() -> dict[str, dict[str, float | str]]:
    return {key: dict(value) for key, value in HK_ADSORPTIVE_PRESETS.items()}


def calculate_hk_interaction_parameter(
    adsorbent_properties: dict[str, object] | None = None,
    adsorptive_properties: dict[str, object] | None = None,
    *,
    adsorbent_key: str = HK_DEFAULT_ADSORBENT,
    adsorptive_key: str = HK_DEFAULT_ADSORPTIVE,
) -> float:
    adsorbent = _hk_resolved_properties(HK_ADSORBENT_PRESETS, adsorbent_key, adsorbent_properties)
    adsorptive = _hk_resolved_properties(HK_ADSORPTIVE_PRESETS, adsorptive_key, adsorptive_properties)
    alpha_s = _hk_property_float(adsorbent, "polarizability_cm3")
    alpha_a = _hk_property_float(adsorptive, "polarizability_cm3")
    chi_s = _hk_property_float(adsorbent, "susceptibility_cm3")
    chi_a = _hk_property_float(adsorptive, "susceptibility_cm3")
    density_s = _hk_property_float(adsorbent, "density_per_cm2")
    density_a = _hk_property_float(adsorptive, "density_per_cm2")
    if min(alpha_s, alpha_a, chi_s, chi_a, density_s, density_a) <= 0.0:
        return HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4
    denominator = alpha_s / chi_s + alpha_a / chi_a
    if denominator <= 0.0:
        return HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4
    sample_dispersion = 6.0 * HK_ELECTRON_KINETIC_ENERGY_ERG * alpha_s * alpha_a / denominator
    adsorptive_dispersion = 1.5 * HK_ELECTRON_KINETIC_ENERGY_ERG * alpha_a * chi_a
    interaction = density_a * adsorptive_dispersion + density_s * sample_dispersion
    if _valid_number(interaction) and interaction > 0.0:
        return float(interaction)
    return HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4


def horvath_kawazoe_pore_distribution(
    result: TriStarResult,
    *,
    geometry: str = HK_DEFAULT_GEOMETRY,
    adsorbent_key: str = HK_DEFAULT_ADSORBENT,
    adsorptive_key: str = HK_DEFAULT_ADSORPTIVE,
    adsorbent_properties: dict[str, object] | None = None,
    adsorptive_properties: dict[str, object] | None = None,
    interaction_parameter_erg_cm4: float | None = HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4,
    interaction_parameter_mode: str = "input",
    cheng_yang_correction: bool = False,
    smooth: bool = False,
) -> PoreDistributionResult:
    points = adsorption_points(result)
    if len(points) < 3:
        return PoreDistributionResult("Horvath-Kawazoe", "adsorption", "not_enough_points", len(points))

    geometry_key = str(geometry or HK_DEFAULT_GEOMETRY).strip().lower()
    if geometry_key not in {"slit", "cylinder", "sphere"}:
        geometry_key = HK_DEFAULT_GEOMETRY
    adsorbent = _hk_resolved_properties(HK_ADSORBENT_PRESETS, adsorbent_key, adsorbent_properties)
    adsorptive = _hk_resolved_properties(HK_ADSORPTIVE_PRESETS, adsorptive_key, adsorptive_properties)
    if str(interaction_parameter_mode).lower() == "calculated":
        interaction_parameter = calculate_hk_interaction_parameter(
            adsorbent,
            adsorptive,
            adsorbent_key=adsorbent_key,
            adsorptive_key=adsorptive_key,
        )
    else:
        try:
            interaction_parameter = float(interaction_parameter_erg_cm4 or HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4)
        except (TypeError, ValueError):
            interaction_parameter = HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4
        if not (_valid_number(interaction_parameter) and interaction_parameter > 0.0):
            interaction_parameter = HK_DEFAULT_INTERACTION_PARAMETER_ERG_CM4

    temperature_k = result.run_conditions.bath_temperature_K or 77.350
    if not (50.0 < float(temperature_k) < 500.0):
        temperature_k = 77.350
    density_factor = density_conversion_factor(result)
    monolayer_capacity = _hk_cheng_yang_monolayer_capacity(result, points) if cheng_yang_correction else None

    rows: list[dict[str, float]] = []
    previous_width_nm: float | None = None
    previous_volume: float | None = None
    for point in points:
        pressure = float(point.relative_pressure)
        quantity = float(point.quantity_adsorbed_cm3_g_stp or 0.0)
        if not (0.0 < pressure < 1.0) or quantity <= 0.0:
            continue
        width_angstrom = _hk_solve_width_angstrom(
            pressure,
            quantity,
            temperature_k,
            geometry_key,
            adsorbent,
            adsorptive,
            interaction_parameter,
            monolayer_capacity,
        )
        if width_angstrom is None:
            continue
        shell_width_angstrom = width_angstrom - 10.0 * _hk_property_float(adsorbent, "diameter_nm")
        if not (_valid_number(shell_width_angstrom) and shell_width_angstrom > 0.0):
            continue
        shell_width_nm = shell_width_angstrom * 0.1
        cumulative_volume = quantity * density_factor
        if previous_width_nm is None or previous_volume is None:
            incremental_volume = max(0.0, cumulative_volume)
            width_delta = shell_width_nm
            log_delta = 0.0
            differential_log = 0.0
        else:
            incremental_volume = max(0.0, cumulative_volume - previous_volume)
            width_delta = shell_width_nm - previous_width_nm
            if width_delta <= 1e-12:
                continue
            log_delta = math.log10(shell_width_nm) - math.log10(previous_width_nm)
            differential_log = incremental_volume / log_delta if abs(log_delta) > 1e-12 else 0.0
        differential_linear = incremental_volume / width_delta if width_delta > 1e-12 else 0.0
        rows.append(
            {
                "phase": "adsorption",
                "point_index": float(point.index),
                "relative_pressure": pressure,
                "quantity_adsorbed_cm3_g_stp": quantity,
                "pore_width_nm": shell_width_nm,
                "pore_diameter_nm": shell_width_nm,
                "cumulative_pore_diameter_nm": shell_width_nm,
                "pore_diameter_range_high_nm": shell_width_nm,
                "pore_diameter_range_low_nm": previous_width_nm if previous_width_nm is not None else 0.0,
                "nucleus_to_nucleus_width_angstrom": width_angstrom,
                "incremental_pore_volume_cm3_g": incremental_volume,
                "cumulative_pore_volume_cm3_g": cumulative_volume,
                "dwidth_nm": width_delta,
                "dlog_diameter": abs(log_delta),
                "differential_pore_volume_per_nm_cm3_g_nm": differential_linear,
                "differential_pore_volume_cm3_g": differential_log,
                "raw_differential_pore_volume_per_nm_cm3_g_nm": differential_linear,
                "raw_differential_pore_volume_cm3_g": differential_log,
                "hk_geometry": geometry_key,
                "hk_interaction_parameter_erg_cm4": interaction_parameter,
            }
        )
        previous_width_nm = shell_width_nm
        previous_volume = cumulative_volume

    if len(rows) < 2:
        return PoreDistributionResult("Horvath-Kawazoe", "adsorption", "not_enough_distribution_points", len(rows), rows=rows)
    if smooth:
        _smooth_distribution_rows(rows)
    return PoreDistributionResult("Horvath-Kawazoe", "adsorption", "ok", len(rows), rows=rows)


def _hk_resolved_properties(
    presets: dict[str, dict[str, float | str]],
    key: str,
    overrides: dict[str, object] | None,
) -> dict[str, object]:
    resolved = dict(presets.get(str(key), presets.get(str(key).lower(), next(iter(presets.values())))))
    if overrides:
        resolved.update(overrides)
    return resolved


def _hk_property_float(properties: dict[str, object], key: str) -> float:
    try:
        return float(properties.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _hk_cheng_yang_monolayer_capacity(
    result: TriStarResult,
    points: Sequence[IsothermPoint],
) -> float | None:
    max_pressure = max(float(point.relative_pressure) for point in points)
    p_max = min(0.2, max_pressure)
    if p_max <= 0.02:
        return None
    fit = langmuir_analysis(result, 0.02, p_max)
    if fit.ok and fit.monolayer_capacity_cm3_g_stp and fit.monolayer_capacity_cm3_g_stp > 0.0:
        return float(fit.monolayer_capacity_cm3_g_stp)
    return None


def _hk_solve_width_angstrom(
    relative_pressure: float,
    quantity_adsorbed: float,
    temperature_k: float,
    geometry: str,
    adsorbent: dict[str, object],
    adsorptive: dict[str, object],
    interaction_parameter: float,
    monolayer_capacity: float | None,
) -> float | None:
    target = math.log(relative_pressure)
    d0 = 5.0 * (
        _hk_property_float(adsorptive, "diameter_nm")
        + _hk_property_float(adsorbent, "diameter_nm")
    )
    sample_diameter = 10.0 * _hk_property_float(adsorbent, "diameter_nm")
    if geometry == "slit":
        lower = max(2.0 * d0, sample_diameter) + 1e-8
    elif geometry == "cylinder":
        lower = max(2.0 * d0, sample_diameter) + 1e-8
    else:
        lower = max(d0, sample_diameter) + 1e-8
    if not (_valid_number(lower) and lower > 0.0):
        return None

    def value(width: float) -> float:
        base = _hk_geometry_ln_relative_pressure(
            width,
            temperature_k,
            geometry,
            adsorbent,
            adsorptive,
            interaction_parameter,
        )
        if not _valid_number(base):
            return float("nan")
        if monolayer_capacity is not None and monolayer_capacity > 0.0:
            theta = quantity_adsorbed / monolayer_capacity
            theta = min(max(theta, 1e-9), 1.0 - 1e-9)
            base -= 1.0 - math.log(1.0 / (1.0 - theta)) / theta
        return base

    f_low = value(lower) - target
    if not _valid_number(f_low):
        lower = lower + 1e-5
        f_low = value(lower) - target
    if not _valid_number(f_low):
        return None
    if f_low > 0.0:
        return None
    high = max(lower * 1.05, lower + 0.05)
    f_high = value(high) - target
    for _ in range(160):
        if _valid_number(f_high) and f_high >= 0.0:
            break
        high *= 1.35
        if high > 1.0e6:
            return None
        f_high = value(high) - target
    if not (_valid_number(f_high) and f_high >= 0.0):
        return None
    lo = lower
    hi = high
    for _ in range(90):
        mid = 0.5 * (lo + hi)
        f_mid = value(mid) - target
        if not _valid_number(f_mid):
            lo = mid
            continue
        if f_mid < 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _hk_geometry_ln_relative_pressure(
    width_angstrom: float,
    temperature_k: float,
    geometry: str,
    adsorbent: dict[str, object],
    adsorptive: dict[str, object],
    interaction_parameter: float,
) -> float:
    if geometry == "cylinder":
        return _hk_cylinder_ln_relative_pressure(
            width_angstrom,
            temperature_k,
            adsorbent,
            adsorptive,
            interaction_parameter,
        )
    if geometry == "sphere":
        return _hk_sphere_ln_relative_pressure(
            width_angstrom,
            temperature_k,
            adsorbent,
            adsorptive,
        )
    return _hk_slit_ln_relative_pressure(
        width_angstrom,
        temperature_k,
        adsorbent,
        adsorptive,
        interaction_parameter,
    )


def _hk_slit_ln_relative_pressure(
    width_angstrom: float,
    temperature_k: float,
    adsorbent: dict[str, object],
    adsorptive: dict[str, object],
    interaction_parameter: float,
) -> float:
    d0 = 5.0 * (
        _hk_property_float(adsorptive, "diameter_nm")
        + _hk_property_float(adsorbent, "diameter_nm")
    )
    sigma = 5.0 * (
        _hk_property_float(adsorptive, "zero_diameter_nm")
        + _hk_property_float(adsorbent, "zero_diameter_nm")
    )
    if min(d0, sigma, temperature_k, interaction_parameter) <= 0.0 or width_angstrom <= 2.0 * d0:
        return float("nan")
    distance = width_angstrom - d0
    if distance <= 0.0:
        return float("nan")
    factor = (
        HK_AVOGADRO
        / (HK_GAS_CONSTANT_ERG_MOL_K * temperature_k)
        * interaction_parameter
        * 1.0e32
        / (sigma**4 * (width_angstrom - 2.0 * d0))
    )
    term = (
        sigma**4 / (3.0 * distance**3)
        - sigma**10 / (9.0 * distance**9)
        - sigma**4 / (3.0 * d0**3)
        + sigma**10 / (9.0 * d0**9)
    )
    return factor * term


def _hk_cylinder_ln_relative_pressure(
    width_angstrom: float,
    temperature_k: float,
    adsorbent: dict[str, object],
    adsorptive: dict[str, object],
    interaction_parameter: float,
) -> float:
    d0 = 5.0 * (
        _hk_property_float(adsorptive, "diameter_nm")
        + _hk_property_float(adsorbent, "diameter_nm")
    )
    radius = width_angstrom / 2.0
    if min(d0, radius, temperature_k, interaction_parameter) <= 0.0 or radius <= d0:
        return float("nan")
    x = d0 / radius
    one_minus = 1.0 - x
    if not (0.0 <= one_minus < 1.0):
        return float("nan")
    alpha = 1.0
    beta = 1.0
    series = 0.0
    for k in range(180):
        if k > 0:
            alpha *= ((-4.5 - k) / k) ** 2
            beta *= ((-1.5 - k) / k) ** 2
        term = (
            (one_minus ** (2 * k))
            / (k + 1.0)
            * ((21.0 / 32.0) * alpha * x**10 - beta * x**4)
        )
        series += term
        if k > 20 and abs(term) < 1e-14:
            break
    factor = (
        0.75
        * math.pi
        * HK_AVOGADRO
        / (HK_GAS_CONSTANT_ERG_MOL_K * temperature_k)
        * interaction_parameter
        * 1.0e32
        / d0**4
    )
    return factor * series


def _hk_sphere_ln_relative_pressure(
    width_angstrom: float,
    temperature_k: float,
    adsorbent: dict[str, object],
    adsorptive: dict[str, object],
) -> float:
    diameter_s = 10.0 * _hk_property_float(adsorbent, "diameter_nm")
    diameter_a = 10.0 * _hk_property_float(adsorptive, "diameter_nm")
    d0 = 0.5 * (diameter_a + diameter_s)
    if min(d0, width_angstrom, temperature_k) <= 0.0 or width_angstrom <= d0:
        return float("nan")
    alpha_s = _hk_property_float(adsorbent, "polarizability_cm3")
    alpha_a = _hk_property_float(adsorptive, "polarizability_cm3")
    chi_s = _hk_property_float(adsorbent, "susceptibility_cm3")
    chi_a = _hk_property_float(adsorptive, "susceptibility_cm3")
    density_s = _hk_property_float(adsorbent, "density_per_cm2")
    density_a = _hk_property_float(adsorptive, "density_per_cm2")
    if min(alpha_s, alpha_a, chi_s, chi_a, density_s, density_a) <= 0.0:
        return float("nan")
    denominator = alpha_s / chi_s + alpha_a / chi_a
    if denominator <= 0.0:
        return float("nan")
    sample_dispersion = 6.0 * HK_ELECTRON_KINETIC_ENERGY_ERG * alpha_s * alpha_a / denominator
    adsorptive_dispersion = 1.5 * HK_ELECTRON_KINETIC_ENERGY_ERG * alpha_a * chi_a
    epsilon_12 = sample_dispersion / (4.0 * diameter_s**6)
    epsilon_22 = adsorptive_dispersion / (4.0 * diameter_a**6)
    n1 = 4.0 * math.pi * width_angstrom**2 * density_s
    n2 = 4.0 * math.pi * (width_angstrom - d0) ** 2 * density_a
    s_value = (width_angstrom - d0) / width_angstrom
    if abs(1.0 - s_value) < 1e-12 or abs(1.0 + s_value) < 1e-12:
        return float("nan")
    t1 = 1.0 / (1.0 - s_value) ** 3 - 1.0 / (1.0 + s_value) ** 3
    t2 = 1.0 / (1.0 + s_value) ** 2 - 1.0 / (1.0 - s_value) ** 2
    t3 = 1.0 / (1.0 - s_value) ** 9 - 1.0 / (1.0 + s_value) ** 9
    t4 = 1.0 / (1.0 + s_value) ** 8 - 1.0 / (1.0 - s_value) ** 8
    volume_denominator = width_angstrom**3 - d0**3
    if volume_denominator <= 0.0:
        return float("nan")
    factor = (
        (6.0 * n1 * epsilon_12 + n2 * epsilon_22)
        * width_angstrom**3
        * 1.0e32
        / (HK_GAS_CONSTANT_ERG_MOL_K * temperature_k * volume_denominator)
    )
    bracket = (
        -(d0 / width_angstrom) ** 6 * (t1 / 12.0 + t2 / 8.0)
        + (d0 / width_angstrom) ** 12 * (t3 / 90.0 + t4 / 80.0)
    )
    return factor * bracket


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
