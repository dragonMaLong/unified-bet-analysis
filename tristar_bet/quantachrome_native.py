"""Data reduction rules of Quantachrome ASiQwin for native ``.qps`` files.

A ``.qps`` file stores, per point, the measured relative pressure, the
saturation pressure, the total adsorbed volume and the elapsed time.  ASiQwin
turns these into the isotherm it reports by applying an optional thermal
transpiration correction to the pressure and an optional temperature
compensation to the volume, and it computes BJH/DH from the points the
operator tagged for pore-size analysis.  The functions here reproduce those
steps; they were checked against ASiQwin 3.01 tables.
"""

from __future__ import annotations

import math
from typing import Sequence

# Tag letters of a point, by position in the 16-character tag string.
TAG_ADSORPTION = 0
TAG_DESORPTION = 1
TAG_MULTI_POINT_BET = 2
TAG_SINGLE_POINT_BET = 3
TAG_THICKNESS = 4
TAG_PORE_VOLUME = 5
TAG_LANGMUIR = 6
TAG_PORE_SIZE = 7
TAG_DR = 8

# Thickness equation chosen by DRedParams.tMethodType -> application method key.
THICKNESS_METHOD_BY_TYPE = {0: "harkins_jura", 1: "carbon_black_stsa", 2: "halsey", 3: "halsey"}

MOLAR_VOLUME_CM3_STP = 22414.0
GAS_CONSTANT_ERG = 8.31441e7

# Temperature compensation of Autosorb iQ files: polynomial in elapsed minutes
# (lowest order first) and scale, for a 77 K and an 87 K bath.
_IQ_TEMP_COMP = {
    77: ((-0.0028, 0.0002, 1e-08, -2e-11, 6e-15, -6e-19, 2e-23), 1.1),
    87: (
        (0.000774414, 0.0003927887, -1.88953409e-07, 6.7777548e-11, -1.394072e-14, 1.4815208e-18, -6.276278e-23),
        1.0,
    ),
}
_IQ_TEMP_COMP_CELL_FACTORS = (1.8, 1.0, 3.8, 0.93484, 5.0, 1.3)
# Autosorb-1 files: volume correction = P/P0 * (a*t^2 + b*t) by cell type.
_AS1_TEMP_COMP = (
    (-6e-09, 0.0004),
    (-2e-09, 0.0001),
    (-2e-07, 0.0013),
    (-6e-08, 0.0003),
    (-4e-07, 0.0029),
    (-1e-07, 0.0006),
)


def thermal_transpiration_available(admin: dict, reduction: dict) -> bool:
    """The file carries what ASiQwin needs to correct pressures for thermal transpiration."""
    ambient = _number(admin.get("ambientTemp"))
    bath = _number(admin.get("bathTemp"))
    return bool(
        admin.get("canThermTransp")
        and ambient is not None
        and bath is not None
        and ambient > 0.0
        and bath > 0.0
        and _number(reduction.get("effMolDiameter"))
        and _number(reduction.get("effCellStemDiameter"))
    )


def thermal_transpiration_factor(
    pressure_torr: float,
    bath_temperature_k: float,
    ambient_temperature_k: float,
    molecular_diameter_angstrom: float,
    stem_diameter_mm: float,
) -> float:
    """Ratio of the pressure at the sample to the pressure read by the transducer."""
    ratio = bath_temperature_k / ambient_temperature_k
    phi = 10.0 ** ((math.log10(molecular_diameter_angstrom * 1e-10) + 9.59) * (100.0 / 27.0))
    x = pressure_torr * stem_diameter_mm * 0.001 * 1333.0
    term = 2.52 * phi * phi * x * x + 7.68 * (1.0 - math.sqrt(ratio)) * phi * x
    return (math.sqrt(ratio) + term) / (term + 1.0)


def temperature_compensation_kind(header_type: str, admin: dict, run_data: dict) -> str | None:
    """Which temperature compensation ASiQwin can apply to this file, if any."""
    header_type = str(header_type or "").strip().upper()
    if header_type == "IQ":
        if run_data.get("vvReMeasure"):
            return "iq_remeasure"
        bath = _number(admin.get("bathTemp"))
        if bath is None:
            return None
        if abs(bath - 77.0) <= 0.6:
            return "iq_77"
        if abs(bath - 87.0) <= 0.6:
            return "iq_87"
        return None
    if header_type == "AS":
        weight = _number(admin.get("adsMolecWeight"))
        if weight is not None and abs(weight - 28.01) <= 1e-4:
            return "as1"
    return None


def temperature_compensation_volume(
    kind: str | None,
    cell_type: int,
    relative_pressure: float,
    absolute_pressure_torr: float,
    elapsed_minutes: float,
    run_data: dict,
) -> float:
    """Volume (cm3 STP, whole sample) ASiQwin subtracts from the stored volume."""
    if kind in {"iq_77", "iq_87"}:
        if not 0 <= cell_type < len(_IQ_TEMP_COMP_CELL_FACTORS):
            return 0.0
        polynomial, scale = _IQ_TEMP_COMP[77 if kind == "iq_77" else 87]
        value = sum(coefficient * elapsed_minutes**power for power, coefficient in enumerate(polynomial))
        return relative_pressure * scale * _IQ_TEMP_COMP_CELL_FACTORS[cell_type] * value
    if kind == "as1":
        if not 0 <= cell_type < len(_AS1_TEMP_COMP):
            return 0.0
        a, b = _AS1_TEMP_COMP[cell_type]
        return relative_pressure * (a * elapsed_minutes * elapsed_minutes + b * elapsed_minutes)
    if kind == "iq_remeasure":
        time_1 = _number(run_data.get("vvRMtime1"))
        time_2 = _number(run_data.get("vvRMtime2"))
        volume_1 = _number(run_data.get("vvRMvolume1"))
        volume_2 = _number(run_data.get("vvRMvolume2"))
        if None in (time_1, time_2, volume_1, volume_2) or time_1 == time_2 or volume_1 == volume_2:
            return 0.0
        low, high = sorted((time_1, time_2))
        if not low <= elapsed_minutes <= high:
            return 0.0
        slope = (volume_2 - volume_1) / (time_2 - time_1)
        return absolute_pressure_torr / 760.0 * (elapsed_minutes - time_1) * slope
    return 0.0


def film_thickness_angstrom(relative_pressure: float, thickness_type: int, pre_exponent: float, exponent: float) -> float | None:
    """Statistical thickness for DRedParams.tMethodType."""
    if thickness_type == 1:
        return 0.88 * relative_pressure * relative_pressure + 6.45 * relative_pressure + 2.98
    if thickness_type == 2:
        return 3.54 * 1.294945 * (-math.log10(relative_pressure)) ** (-1.0 / 3.0)
    if thickness_type == 3:
        if pre_exponent <= 0.0 or exponent <= 0.0:
            return None
        try:
            thickness = pre_exponent * (-math.log(relative_pressure)) ** (-1.0 / exponent)
        except OverflowError:
            return None
        return thickness if math.isfinite(thickness) and thickness > 0.0 else None
    return math.sqrt(13.99 / (0.034 - math.log10(relative_pressure)))


def pore_size_intervals(
    points: Sequence[tuple[float, float]],
    *,
    dollimore_heal: bool,
    thickness_type: int,
    thickness_pre_exponent: float,
    thickness_exponent: float,
    surface_tension: float,
    molecular_weight: float,
    liquid_density: float,
    temperature_k: float,
) -> list[dict[str, float]]:
    """BJH or DH intervals from ``(P/P0, cm3/g STP)`` points ordered from high to low pressure.

    Rows are returned in the same order (largest pores first).
    """
    if len(points) < 2:
        return []
    molar_volume_liquid = molecular_weight / liquid_density
    liquid_factor = molar_volume_liquid / MOLAR_VOLUME_CM3_STP
    kelvin_constant = 2.0 * surface_tension * molar_volume_liquid / (GAS_CONSTANT_ERG * temperature_k * math.log(10.0)) * 1e8
    # ASiQwin's DH volume conversion; about 3.8 % below the liquid factor used for the area.
    dh_volume_factor = 2.303 / (4.46153111717185e-05 * molar_volume_liquid)

    state = []
    for pressure, volume in points:
        thickness = film_thickness_angstrom(pressure, thickness_type, thickness_pre_exponent, thickness_exponent)
        if thickness is None:
            return []
        kelvin = -kelvin_constant / math.log10(pressure)
        state.append((pressure, volume, thickness, kelvin))

    rows: list[dict[str, float]] = []
    mean_radii: list[float] = []
    areas: list[float] = []
    area_sum = 0.0
    length_sum = 0.0
    for index in range(1, len(state)):
        high_p, high_v, high_t, high_k = state[index - 1]
        low_p, low_v, low_t, low_k = state[index]
        mean_radius = 0.5 * (high_k + high_t + low_k + low_t)
        mean_kelvin = 0.5 * (high_k + low_k)
        mean_thickness = 0.5 * (high_t + low_t)
        delta_thickness = high_t - low_t
        delta_volume = high_v - low_v
        if dollimore_heal:
            radius_nm = mean_radius * 0.1
            thickness_nm = mean_thickness * 0.1
            core_ratio = ((radius_nm - thickness_nm) / radius_nm) ** 2
            gas_volume_m3 = (
                delta_volume * 1e-6
                - delta_thickness * 0.1 * (area_sum - 6.283184 * thickness_nm * length_sum * 1e-9) * 1e-9
            ) / core_ratio
            gas_area = 2.0 * gas_volume_m3 / (radius_nm * 1e-9)
            area_sum += gas_area
            length_sum += gas_area / (6.283184 * radius_nm * 1e-9)
            volume = max(gas_volume_m3 * dh_volume_factor, 0.0)
            area = max(gas_area * liquid_factor, 0.0)
        else:
            wall = sum((1.0 - mean_thickness / radius) * area for radius, area in zip(mean_radii, areas))
            volume = (mean_radius / (mean_kelvin + 0.5 * delta_thickness)) ** 2 * (
                liquid_factor * delta_volume - 1e-4 * delta_thickness * wall
            )
            volume = max(volume, 0.0)
            area = volume * 20000.0 / mean_radius
            mean_radii.append(mean_radius)
            areas.append(area)
        rows.append(
            {
                "relative_pressure_high": high_p,
                "relative_pressure_low": low_p,
                "pore_diameter_nm": 0.2 * mean_radius,
                "pore_diameter_range_high_nm": 0.2 * (high_k + high_t),
                "pore_diameter_range_low_nm": 0.2 * (low_k + low_t),
                "incremental_pore_volume_cm3_g": volume,
                "incremental_pore_area_m2_g": area,
                "film_thickness_nm": 0.1 * mean_thickness,
                "kelvin_radius_nm": 0.1 * mean_kelvin,
            }
        )
    return rows


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None
