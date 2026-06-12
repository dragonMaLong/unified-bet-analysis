from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

import numpy as np

from .models import IsothermPoint, TriStarResult


DEFAULT_N2_CROSS_SECTION_NM2 = 0.162
DEFAULT_N2_DENSITY_CONVERSION_FACTOR = 0.0015468
DEFAULT_THICKNESS_METHOD = "harkins_jura"
THICKNESS_METHOD_DEFAULT_PARAMS: dict[str, dict[str, float]] = {
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
    r_squared: float | None = None
    monolayer_capacity_cm3_g_stp: float | None = None
    surface_area_m2_g: float | None = None
    c_constant: float | None = None
    langmuir_b: float | None = None
    external_surface_area_m2_g: float | None = None
    micropore_volume_cm3_g: float | None = None
    rows: list[dict[str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "warning_negative_c"}


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


def bet_analysis(result: TriStarResult, p_min: float = 0.05, p_max: float = 0.30) -> FitResult:
    selected = _points_in_range(adsorption_points(result), p_min, p_max)
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
            r_squared=r_squared,
            rows=rows,
        )

    monolayer = 1.0 / denominator
    c_constant = (slope / intercept + 1.0) if abs(intercept) > 1e-15 else None
    status = "warning_negative_c" if c_constant is not None and c_constant <= 0.0 else "ok"
    surface_area = monolayer * surface_area_factor_m2_per_cm3(result)
    return FitResult(
        "BET",
        status,
        len(x_values),
        p_min,
        p_max,
        slope=slope,
        intercept=intercept,
        r_squared=r_squared,
        monolayer_capacity_cm3_g_stp=monolayer,
        surface_area_m2_g=surface_area,
        c_constant=c_constant,
        rows=rows,
    )


def langmuir_analysis(result: TriStarResult, p_min: float = 0.05, p_max: float = 0.30) -> FitResult:
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
    p_min: float = 0.20,
    p_max: float = 0.50,
    thickness_params: dict[str, float] | None = None,
    thickness_method: str = DEFAULT_THICKNESS_METHOD,
) -> FitResult:
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
        rows.append(
            {
                "point_index": float(point.index),
                "relative_pressure": pressure,
                "quantity_adsorbed_cm3_g_stp": quantity,
                "thickness_nm": thickness,
                "liquid_volume_cm3_g": liquid_volume,
            }
        )
        x_values.append(thickness)
        y_values.append(liquid_volume)

    if len(x_values) < 3:
        return FitResult("t-Plot", "not_enough_valid_points", len(x_values), range_min, range_max, rows=rows)

    slope, intercept, r_squared = _linear_fit(x_values, y_values)
    external_surface_area = slope * 1000.0 if slope > 0 else None
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
        return {
            "BET": bet_analysis(result),
            "Langmuir": langmuir_analysis(result),
            "t-Plot": t_plot_analysis(result),
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
    avogadro = 6.02214076e23
    molar_volume_cm3_stp = 22414.0
    return avogadro * cross_section * 1e-18 / molar_volume_cm3_stp


def density_conversion_factor(result: TriStarResult) -> float:
    if result.adsorptive_properties and result.adsorptive_properties.density_conversion_factor:
        return float(result.adsorptive_properties.density_conversion_factor)
    return DEFAULT_N2_DENSITY_CONVERSION_FACTOR


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
    if method == "halsey":
        return _halsey_thickness_nm(relative_pressure, merged_params)
    if method == "broekhoff_de_boer":
        return _broekhoff_de_boer_thickness_nm(relative_pressure, merged_params)
    if method == "carbon_black_stsa":
        return _carbon_black_stsa_thickness_nm(relative_pressure, merged_params)
    return _power_log_thickness_nm(relative_pressure, merged_params)


def _thickness_params(method: str, params: dict[str, float] | None) -> dict[str, float]:
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
    return [point for point in points if lo <= float(point.relative_pressure) <= hi]


def _linear_fit(x_values: Sequence[float], y_values: Sequence[float]) -> tuple[float, float, float]:
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    residual = float(np.sum((y - fitted) ** 2))
    total = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - residual / total if total > 0.0 else 1.0
    return float(slope), float(intercept), float(r_squared)


def _valid_number(value: float | None) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
