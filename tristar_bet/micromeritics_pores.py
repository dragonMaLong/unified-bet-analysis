"""Micromeritics BJH, Dollimore-Heal and Horvath-Kawazoe as the vendor computes them.

Reproduced against the vendor reports that came with the SMP files:

* BJH, Faas correction (TriStar II 3020, ASAP 2460, ASAP 2020 Plus, 3Flex):
  every adsorption and desorption table of 34 TriStar II 3020, 26 ASAP 2460
  and 2 ASAP 2020 Plus reports (relative error < 3e-5).  3Flex files carry
  P/P0 with five decimals, so their tables agree to about 1e-4.
* BJH, Standard correction (MicroActive, TriStar II Plus): 172 of 180 tables;
  the other 8 belong to report files that were exported for another sample.
* Dollimore-Heal (TriStar II Plus, 3Flex): 170 of 182 tables (same 8 reports,
  plus 3 reports exported before their DH pressure range was changed).
* Horvath-Kawazoe: the vendor's own iteration (see ``_vendor_hk_width``).

The settings come from the report-option subsets of the SMP file:
315/316 BJH adsorption/desorption, 331/332 DH adsorption/desorption, 317 HK.
All internal lengths are in Angstrom.
"""

from __future__ import annotations

import math
import struct
from typing import Callable, Sequence

THICKNESS_MODELS = (
    "halsey", "harkins_jura", "carbon_black_stsa", "broekhoff_de_boer", "reference", "kjs",
)
BJH_CORRECTIONS = {0: "standard", 1: "kjs", 2: "faas"}
HK_GEOMETRIES = {0: "slit", 1: "cylinder", 2: "sphere"}
HK_SAMPLE_PRESETS = (
    "aluminophosphate", "aluminosilicate", "carbon_graphite_ross_olivier",
    "carbon_graphite_hk", "zeolite", "other",
)
HK_TOLERANCE = 1.0e-3  # "within 0.1% of the collected pressure" (Calculations manual)
# The MicroActive thickness lookup answers -1 A outside its reference table.
REFERENCE_OUTSIDE_TABLE_A = -1.0


def _double(block: bytes, offset: int) -> float | None:
    if offset < 0 or offset + 8 > len(block):
        return None
    value = struct.unpack_from("<d", block, offset)[0]
    return value if math.isfinite(value) else None


def _uint16(block: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(block):
        return None
    return struct.unpack_from("<H", block, offset)[0]


def _model_index(block: bytes) -> int | None:
    """Selected thickness model of a BJH/DH options subset."""
    if len(block) > 21 and block[18] == 8:  # ASAP 2460 / 3Flex / newer MicroActive layout
        return _uint16(block, 19)
    for at in range(60, min(len(block) - 10, 260)):  # titled layout: <tag> 00 01 00 <index> 04 00
        if (0x60 <= block[at] <= 0x9F and block[at + 1:at + 4] == b"\x00\x01\x00"
                and block[at + 5] == 0 and block[at + 6:at + 8] == b"\x04\x00"):
            return _uint16(block, at + 4)
    return None


def _pressure_range(block: bytes) -> tuple[float, float] | None:
    """``02 | lo hi | 01 00`` (or ``00 00``) at the end of the subset."""
    n = len(block)
    if n < 40 or block[n - 19] != 2:
        return None
    lo, hi = _double(block, n - 18), _double(block, n - 10)
    if lo is None or hi is None or not 0.0 <= lo < hi <= 1.5:
        return None
    return lo, hi


def _reference_table(block: bytes) -> list[tuple[float, float]] | None:
    for at in range(0, len(block) - 22):
        count = _uint16(block, at)
        if not count or not 5 <= count <= 500 or at + 2 + 18 * count > len(block):
            continue
        if all(block[at + 2 + 18 * k:at + 4 + 18 * k] == b"\x02\x00" for k in range(count)):
            rows = [struct.unpack_from("<dd", block, at + 4 + 18 * k) for k in range(count)]
            if all(0.0 < p <= 1.0 and t >= 0.0 for p, t in rows) and all(
                rows[k][0] < rows[k + 1][0] for k in range(count - 1)
            ):
                return [(float(p), float(t)) for p, t in rows]
    return None


def _thickness_params(block: bytes, method: str) -> dict[str, float] | None:
    """Halsey / Harkins-Jura constants as stored (Angstrom based)."""
    for at in range(16, min(len(block) - 24, 200)):
        a, b, c = _double(block, at), _double(block, at + 8), _double(block, at + 16)
        if a is None or b is None or c is None:
            continue
        if -50.0 < a < -0.5 and 0.5 < b < 20.0 and 0.05 < c < 1.0:  # Halsey: numerator, prefactor, exponent
            if method == "halsey":
                return {"numerator": a, "prefactor": b, "exponent": c}
            for at2 in range(at + 24, min(len(block) - 24, at + 90)):
                d, e, f = _double(block, at2), _double(block, at2 + 8), _double(block, at2 + 16)
                if d is not None and e is not None and f is not None and 1.0 < d < 100.0 and 1e-4 < e < 1.0 and 0.05 < f <= 1.0:
                    return {"numerator": d, "offset": e, "exponent": f}
            return None
    return None


def parse_pore_options_block(block: bytes) -> dict[str, object] | None:
    """BJH or DH report options (subsets 315/316/331/332)."""
    if len(block) < 200:
        return None
    index = _model_index(block)
    if index is None or not 0 <= index < len(THICKNESS_MODELS):
        return None
    marker = block.find(b"\xe0\xe0\x01\x00")
    if marker < 100:
        return None
    name_length = struct.unpack_from("<I", block, marker + 4)[0]
    if not 0 < name_length < 64:
        return None
    try:
        adsorptive = block[marker + 8:marker + 8 + name_length].decode("utf-16-le").rstrip("\x00")
    except UnicodeDecodeError:
        return None
    factor_at = marker + 8 + name_length
    kelvin_factor = _double(block, factor_at)
    d_min, d_max = _double(block, factor_at - 92), _double(block, factor_at - 84)
    correction = _uint16(block, factor_at - 48)
    if kelvin_factor is None or not 1.0 < kelvin_factor < 50.0:
        return None
    if d_min is None or d_max is None or not 0.0 < d_min < d_max <= 1.0e5:
        return None
    method = THICKNESS_MODELS[index]
    settings: dict[str, object] = {
        "thickness_method": method,
        "kelvin_factor_angstrom": kelvin_factor,
        "kelvin_adsorptive": adsorptive,
        "diameter_min_angstrom": d_min,
        "diameter_max_angstrom": d_max,
        "correction": BJH_CORRECTIONS.get(correction, f"code_{correction}"),
    }
    pressure_range = _pressure_range(block)
    if pressure_range is not None:
        settings["pressure_range"] = list(pressure_range)
    if method == "reference":
        table = _reference_table(block)
        if table is None:
            return None
        settings["reference_table_angstrom"] = [list(row) for row in table]
    elif method in ("halsey", "harkins_jura"):
        params = _thickness_params(block, method)
        if params is None:
            return None
        settings["thickness_params"] = params
    else:
        return None  # not verified against a report
    return settings


def thickness_function(settings: dict[str, object]) -> Callable[[float], float]:
    """Film thickness (Angstrom) at P/P0 for the stored model; 0 at P <= 0."""
    method = settings["thickness_method"]
    if method == "reference":
        from .reference_thickness import micromeritics_reference_thickness

        table = [(float(p), float(t) / 10.0) for p, t in settings["reference_table_angstrom"]]

        def reference(pressure: float) -> float:
            if pressure <= 0.0:
                return 0.0
            value = micromeritics_reference_thickness(pressure, table)
            return REFERENCE_OUTSIDE_TABLE_A if value is None else 10.0 * value

        return reference
    params = dict(settings["thickness_params"])
    if method == "halsey":
        numerator, prefactor, exponent = params["numerator"], params["prefactor"], params["exponent"]
        return lambda p: prefactor * (numerator / math.log(p)) ** exponent if 0.0 < p < 1.0 else 0.0
    numerator, offset, exponent = params["numerator"], params["offset"], params["exponent"]
    return lambda p: (numerator / (offset - math.log10(p))) ** exponent if p > 0.0 else 0.0


def branch_points(isotherm, phase: str, settings: dict[str, object]) -> list[tuple[float, float, int]]:
    """Points the vendor feeds into BJH/DH, from high to low pressure.

    Adsorption: the adsorption points in reverse measuring order.  Desorption:
    the last adsorption point followed by the desorption points.  Points with
    no uptake, outside the saved pressure range, or not below the previous
    point's pressure are skipped.
    """
    adsorption = [p for p in isotherm if p.phase == "adsorption"]
    if phase == "adsorption":
        candidates = adsorption[::-1]
    else:
        candidates = adsorption[-1:] + [p for p in isotherm if p.phase == "desorption"]
    pressure_range = settings.get("pressure_range")
    selected: list[tuple[float, float, int]] = []
    for point in candidates:
        pressure, quantity = point.relative_pressure, point.quantity_adsorbed_cm3_g_stp
        if pressure is None or quantity is None or not quantity > 0.0 or not pressure > 0.0:
            continue
        if pressure_range and not pressure_range[0] <= pressure <= pressure_range[1]:
            continue
        if selected and not pressure < selected[-1][0]:
            continue
        selected.append((float(pressure), float(quantity), int(point.index)))
    return selected


def _kelvin(apf: float, pressure: float) -> float:
    return -apf / math.log(pressure)


def _weighted_average(r1: float, r2: float) -> float:
    return r1 * r2 * (r1 + r2) / (r1 * r1 + r2 * r2)


def faas_bjh(points, settings, density_factor):
    """BJH with the Faas correction (TriStar II 3020 / ASAP / 3Flex reports).

    Classic Micromeritics bookkeeping: each new pore group keeps its average
    core diameter and length; walls of earlier groups thin by the film change
    of every later interval.  When the thinning volume reaches the desorbed
    volume the interval only thins walls, by the amount that desorbs exactly
    that volume (never more than the film change).  A final interval to P = 0
    removes the remaining film.
    """
    thickness = thickness_function(settings)
    apf = float(settings["kelvin_factor_angstrom"])
    data = [(p, q) for p, q, _ in points] + [(0.0, 0.0)]
    volumes = [q * density_factor for _, q in data]
    pressures = [p for p, _ in data]
    bounds = [_kelvin(apf, pressures[0])]
    diameters: list[float] = []
    lengths: list[float] = []
    intervals: list[tuple[float, float]] = []
    for i in range(len(data) - 1):
        p_high, p_low = pressures[i], pressures[i + 1]
        desorbed = volumes[i] - volumes[i + 1]
        t_high = thickness(p_high)
        t_low = thickness(p_low) if p_low > 0.0 else 0.0
        film_change = t_high - t_low
        wall_volume = sum(
            math.pi * ((d / 2.0 + film_change) ** 2 - (d / 2.0) ** 2) * 1e-16 * length
            for d, length in zip(diameters, lengths)
        )
        if diameters and (wall_volume >= desorbed or p_low <= 0.0):
            wall_area = sum(math.pi * d * length * 1e-8 for d, length in zip(diameters, lengths))
            if wall_area > 0.0:
                film_change = min(film_change, desorbed / wall_area * 1e8)
            diameters = [d + 2.0 * film_change for d in diameters]
            bounds = [r + film_change for r in bounds]
            if p_low <= 0.0:
                break
            continue
        if p_low <= 0.0:
            break
        core_volume = desorbed - wall_volume
        r_high, r_low = _kelvin(apf, p_high), _kelvin(apf, p_low)
        core = 2.0 * _weighted_average(r_high, r_low)
        immediate = thickness(math.exp(-2.0 * apf / core)) - t_low
        length = core_volume / (math.pi * (core / 2.0 + immediate) ** 2 * 1e-16)
        diameters = [d + 2.0 * film_change for d in diameters]
        bounds = [r + film_change for r in bounds]
        diameters.append(core + 2.0 * immediate)
        lengths.append(length)
        bounds.append(r_low)
        intervals.append((p_high, p_low))
    rows = []
    for k, (diameter, length) in enumerate(zip(diameters, lengths)):
        rows.append({
            "high": 2.0 * bounds[k], "low": 2.0 * bounds[k + 1], "average": diameter,
            "volume": math.pi * length * (diameter / 2.0) ** 2 * 1e-16,
            "p_high": intervals[k][0], "p_low": intervals[k][1],
        })
    return rows


def standard_bjh(points, settings, density_factor):
    """MicroActive "Standard" BJH (TriStar II Plus reports).

    Intervals in which no new pores open are merged into the next one; the
    wall thinning of earlier pores uses their core radius at the start of the
    interval; the average pore radius is the weighted Kelvin radius plus the
    film thickness at the corresponding pressure.
    """
    thickness = thickness_function(settings)
    apf = float(settings["kelvin_factor_angstrom"])
    pressures = [p for p, _, _ in points]
    volumes = [q * density_factor for _, q, _ in points]
    films = [thickness(p) for p in pressures]
    groups: list[tuple[float, float]] = []  # (average pore radius, length)
    rows = []
    start = 0
    r_start = _kelvin(apf, pressures[0])
    for end in range(1, len(points) - 1):
        film_change = films[start] - films[end]
        desorbed = volumes[start] - volumes[end]
        wall_volume = 0.0
        for radius, length in groups:
            core = radius - films[start]
            wall_volume += math.pi * ((core + film_change) ** 2 - core ** 2) * 1e-16 * length
        core_volume = desorbed - wall_volume
        if core_volume < 0.0:
            continue
        r_end = _kelvin(apf, pressures[end])
        kelvin_average = _weighted_average(r_start, r_end)
        pore_radius = kelvin_average + thickness(math.exp(-apf / kelvin_average))
        length = core_volume / (math.pi * kelvin_average ** 2 * 1e-16)
        groups.append((pore_radius, length))
        rows.append({
            "high": 2.0 * (r_start + films[start]), "low": 2.0 * (r_end + films[end]),
            "average": 2.0 * pore_radius,
            "volume": math.pi * length * pore_radius ** 2 * 1e-16,
            "p_high": pressures[start], "p_low": pressures[end],
        })
        start, r_start = end, r_end
    return rows


def dollimore_heal(points, settings, density_factor):
    """Dollimore-Heal as MicroActive computes it (arithmetic interval averages)."""
    thickness = thickness_function(settings)
    apf = float(settings["kelvin_factor_angstrom"])
    pressures = [p for p, _, _ in points]
    volumes = [q * density_factor for _, q, _ in points]
    kelvin = [_kelvin(apf, p) for p in pressures]
    films = [thickness(p) for p in pressures]
    rows = []
    cumulative_area = 0.0  # cm2
    cumulative_length = 0.0  # cm
    for i in range(len(points) - 1):
        kelvin_average = 0.5 * (kelvin[i] + kelvin[i + 1])
        film_average = 0.5 * (films[i] + films[i + 1])
        film_change = films[i] - films[i + 1]
        pore_radius = kelvin_average + film_average
        ratio = (pore_radius / (kelvin_average + film_change)) ** 2
        core_volume = (
            volumes[i] - volumes[i + 1]
            - film_change * 1e-8 * cumulative_area
            + film_average * 1e-8 * film_change * 1e-8 * 2.0 * math.pi * cumulative_length
        )
        volume = max(core_volume, 0.0) * ratio
        area = 2.0 * volume / (pore_radius * 1e-8)
        cumulative_area += area
        cumulative_length += area / (2.0 * math.pi * pore_radius * 1e-8)
        rows.append({
            "high": 2.0 * (kelvin[i] + films[i]), "low": 2.0 * (kelvin[i + 1] + films[i + 1]),
            "average": 2.0 * pore_radius, "volume": volume,
            "p_high": pressures[i], "p_low": pressures[i + 1],
        })
    return rows


def distribution_rows(result, phase: str, settings: dict[str, object], dollimore: bool, density_factor: float):
    """Vendor rows (nm, cm3/g) or None when the settings are not supported."""
    correction = settings.get("correction")
    if not dollimore and correction not in ("faas", "standard"):
        return None
    points = branch_points(result.isotherm, phase, settings)
    if len(points) < 3:
        return None
    if dollimore:
        raw = dollimore_heal(points, settings, density_factor)
    elif correction == "faas":
        raw = faas_bjh(points, settings, density_factor)
    else:
        raw = standard_bjh(points, settings, density_factor)
    d_min = float(settings["diameter_min_angstrom"])
    d_max = float(settings["diameter_max_angstrom"])
    rows = [row for row in raw if d_min <= row["average"] <= d_max]
    return rows


# --- Horvath-Kawazoe ------------------------------------------------------------------


def parse_hk_options_block(block: bytes) -> dict[str, object] | None:
    """HK report options (subset 317)."""
    names = ("N2", "AR", "CO2")
    for at in range(16, min(len(block) - 32, 400)):
        if block[at:at + 4] not in (b"\xe0\x01\x00\x06", b"\xe0\x01\x00\x08"):
            continue
        length = block[at + 3]
        try:
            name = block[at + 7:at + 7 + length].decode("utf-16-le").rstrip("\x00").upper()
        except UnicodeDecodeError:
            continue
        if name not in names:
            continue
        string_at = at + 7
        interaction = _double(block, string_at - 20)
        geometry = _uint16(block, string_at - 12)
        entered = _uint16(block, string_at - 22)
        sample = _uint16(block, string_at + length)
        if interaction is None or not 1e-46 < interaction < 1e-39:
            return None
        if geometry not in HK_GEOMETRIES or sample is None or not 0 <= sample < len(HK_SAMPLE_PRESETS):
            return None
        settings: dict[str, object] = {
            "geometry": HK_GEOMETRIES[geometry],
            "adsorptive_key": name,
            "adsorbent_key": HK_SAMPLE_PRESETS[sample],
            "interaction_parameter_mode": "input" if entered == 1 else "calculated",
            "interaction_parameter": interaction,
        }
        n = len(block)  # tail: 02 | lo hi | 01 00 00 00
        lo = _double(block, n - 20) if block[n - 21] == 2 else None
        hi = _double(block, n - 12) if lo is not None else None
        if lo is not None and hi is not None and 0.0 <= lo < hi <= 1.5:
            settings["pressure_range"] = [lo, hi]
        return settings
    return None


def vendor_hk_width(previous: float, target: float, ln_pressure: Callable[[float], float]) -> float:
    """The vendor's iteration for the nucleus-to-nucleus width L (Angstrom).

    Starting from the previous point's L (the first point starts at 2*d0),
    the factor L/L_previous is changed by decimal steps of 0.1, 0.01, 0.001,
    ...: upward until the calculated pressure passes the measured one, then
    downward with the next smaller step, and so on.  The first width whose
    pressure is within 0.1% of the measured pressure is taken.
    """
    factor, step, direction = 1.0, 0.1, 1.0
    for _ in range(4000):
        factor += direction * step
        value = ln_pressure(previous * factor)
        calculated = math.exp(value) if math.isfinite(value) else 0.0
        if abs(calculated - target) <= HK_TOLERANCE * target:
            break
        if (direction > 0 and calculated > target) or (direction < 0 and calculated < target):
            direction = -direction
            step /= 10.0
            if step < 1e-13:
                break
    return previous * factor


# --- SMP import -------------------------------------------------------------------------

REPORT_SUBSETS = {
    "vendor_bjh_report_settings": {"adsorption": 315, "desorption": 316},
    "vendor_dh_report_settings": {"adsorption": 331, "desorption": 332},
}


def _bath_temperature_from_conditions(block: bytes) -> float | None:
    """ASAP 2460 / 3Flex subset 302 keeps the analysis bath temperature 247 bytes from the end."""
    value = _double(block, len(block) - 247) if len(block) > 300 else None
    return value if value is not None and 60.0 < value < 400.0 else None


# Nitrogen saturation-pressure table carried in MicroActive files (mmHg, K).
N2_SATURATION_TABLE = (
    (600.193, 75.4), (634.512, 75.85), (674.383, 76.35), (720.42, 76.9), (742.119, 77.15),
    (759.833, 77.35), (777.867, 77.55), (805.525, 77.85), (853.268, 78.35), (903.122, 78.85),
)


def _temperature_from_p0(p0_mmHg: float, table=N2_SATURATION_TABLE) -> float | None:
    rows = sorted((float(p), float(t)) for p, t in table)
    if len(rows) < 2 or not rows[0][0] <= p0_mmHg <= rows[-1][0]:
        return None
    for (p1, t1), (p2, t2) in zip(rows, rows[1:]):
        if p1 <= p0_mmHg <= p2:
            return t1 + (p0_mmHg - p1) / (p2 - p1) * (t2 - t1)
    return None


def pore_report_settings(blocks: dict[int, bytes], run_conditions, *, model: str = "", po_records=(),
                         adsorptive: str = "") -> dict[str, object]:
    """Method options for the vendor BJH/DH/HK report settings of an SMP file."""
    options: dict[str, object] = {}
    for key, subsets in REPORT_SUBSETS.items():
        parsed = {phase: parse_pore_options_block(blocks.get(subset, b"")) for phase, subset in subsets.items()}
        parsed = {phase: value for phase, value in parsed.items() if value is not None}
        if parsed:
            options[key] = parsed
    bjh = options.get("vendor_bjh_report_settings", {})
    if len(bjh) == 2 and len({(v["thickness_method"], v["correction"]) for v in bjh.values()}) == 1:
        first = next(iter(bjh.values()))
        options["vendor_bjh_thickness_method"] = first["thickness_method"]
        options["vendor_bjh_correction"] = first["correction"]
        # Reports: MicroActive (Standard) tables/plots are smoothed, Faas reports print raw values.
        options["vendor_bjh_smooth_derivative"] = first["correction"] == "standard"
        if first["correction"] == "standard":
            options["vendor_dh_smooth_derivative"] = True
    dh = options.get("vendor_dh_report_settings", {})
    if len(dh) == 2 and len({v["thickness_method"] for v in dh.values()}) == 1:
        options["vendor_dh_thickness_method"] = next(iter(dh.values()))["thickness_method"]
    hk = parse_hk_options_block(blocks.get(317, b""))
    if hk is not None:
        temperature = getattr(run_conditions, "bath_temperature_K", None)
        if not (temperature and 60.0 < float(temperature) < 400.0):
            temperature = _bath_temperature_from_conditions(blocks.get(302, b""))
        if model == "ASAP 2020 Plus" and po_records and "N" in adsorptive.upper():
            # Reported "Analysis bath temp." = N2 table temperature at the mean measured P0
            # (TL-7091: -195.651 C; with it the Saito-Foley HK widths match the report).
            pressures = [float(p.saturation_pressure_mmHg) for p in po_records if p.saturation_pressure_mmHg]
            measured = _temperature_from_p0(sum(pressures) / len(pressures)) if pressures else None
            if measured:
                temperature = measured
        if temperature:
            hk["bath_temperature_K"] = float(temperature)
            options["vendor_hk_report_settings"] = hk
    return options


# --- Smoothed dV/dlog(D) (MicroActive) --------------------------------------------------

SMOOTH_GRID_INTERVALS = 199
SMOOTH_HALF_WINDOW = 4


def _akima_slopes(x, y, left: str, right: str):
    import numpy as np

    chords = np.diff(y) / np.diff(x)
    if left == "repeat":
        head = [chords[0], chords[0]]
    else:  # standard Akima end: extrapolated chords
        head = [3.0 * chords[0] - 2.0 * chords[1], 2.0 * chords[0] - chords[1]]
    if right == "repeat":
        tail = [chords[-1], chords[-1]]
    else:
        tail = [2.0 * chords[-1] - chords[-2], 3.0 * chords[-1] - 2.0 * chords[-2]]
    ext = np.concatenate([head, chords, tail])
    m1, m2, m3, m4 = ext[:-3], ext[1:-2], ext[2:-1], ext[3:]
    w_left, w_right = np.abs(m4 - m3), np.abs(m2 - m1)
    total = w_left + w_right
    with np.errstate(invalid="ignore", divide="ignore"):
        slopes = np.where(total > 0.0, (w_left * m2 + w_right * m3) / np.where(total > 0.0, total, 1.0), 0.5 * (m2 + m3))
    return slopes


def _clamped_akima(x, y, targets, left: str, right: str):
    """Akima Hermite interpolation with each value held between its interval's two nodes."""
    import numpy as np

    slopes = _akima_slopes(x, y, left, right)
    t = np.clip(np.asarray(targets, dtype=float), x[0], x[-1])
    i = np.clip(np.searchsorted(x, t, side="right") - 1, 0, len(x) - 2)
    span = x[i + 1] - x[i]
    u = (t - x[i]) / span
    value = ((2 * u**3 - 3 * u**2 + 1) * y[i] + (u**3 - 2 * u**2 + u) * span * slopes[i]
             + (-2 * u**3 + 3 * u**2) * y[i + 1] + (u**3 - u**2) * span * slopes[i + 1])
    return np.minimum(np.maximum(value, np.minimum(y[i], y[i + 1])), np.maximum(y[i], y[i + 1]))


def smoothed_log_derivative(low_diameters, first_high_diameter: float, cumulative):
    """Smoothed d(cumulative)/dlog10(D) at each row's lower diameter, as MicroActive plots it.

    Reproduced from 218 dV/dlog(D) and dA/dlog(D) plot tables of TriStar II
    Plus reports (BJH and DH, both branches): all rows agree to 2e-6 except
    the last few small-pore rows (up to ~1%).  Steps: the cumulative curve
    (0 at the first row's upper diameter, then each row's cumulative value at
    its lower diameter) is interpolated in log10(D) by Akima - repeated end
    chords at the large-pore end, standard end chords at the small-pore end,
    values held between neighbouring nodes - onto 200 equally spaced points;
    the slope there is the 9-point least-squares slope with the curve
    extended by point reflection about each end; the slopes are interpolated
    back (Akima, repeated end chords, held) to each row's lower diameter.
    """
    import numpy as np

    lows = np.asarray(low_diameters, dtype=float)
    cum = np.asarray(cumulative, dtype=float)
    if len(lows) < 3 or first_high_diameter <= 0.0 or np.any(lows <= 0.0):
        return None
    x = np.log10(np.concatenate([[first_high_diameter], lows]))
    y = np.concatenate([[0.0], cum])
    order = np.argsort(x)
    x, y = x[order], y[order]
    if np.any(np.diff(x) <= 0.0):
        return None
    grid = np.linspace(x[0], x[-1], SMOOTH_GRID_INTERVALS + 1)
    step = grid[1] - grid[0]
    values = _clamped_akima(x, y, grid, "standard", "repeat")
    h = SMOOTH_HALF_WINDOW
    offsets = np.arange(1, h + 1)
    padded = np.concatenate([
        (2.0 * values[0] - values[1:h + 1])[::-1], values, 2.0 * values[-1] - values[-2:-h - 2:-1],
    ])
    weights = np.arange(-h, h + 1, dtype=float)
    slope = np.convolve(padded, weights[::-1], mode="valid") / float(np.dot(weights, weights)) / step
    result = _clamped_akima(grid, slope, np.log10(lows), "repeat", "repeat")
    return -result  # rows run from large to small diameter
