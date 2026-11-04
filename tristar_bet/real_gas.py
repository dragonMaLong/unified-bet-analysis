"""Gas-phase compressibility factor for the real-gas free-space correction.

MicroActive's "Real gas equation of state" option divides the cold free-space
gas by the compressibility factor Z(P, T) of the adsorptive. For nitrogen this
module evaluates the reference equation of state of

    Span, R., Lemmon, E.W., Jacobsen, R.T, Wagner, W. and Yokozeki, A.,
    "A Reference Equation of State for the Thermodynamic Properties of Nitrogen
    for Temperatures from 63.151 to 1000 K and Pressures to 2200 MPa",
    J. Phys. Chem. Ref. Data 29 (2000) 1361-1433.

Only the residual Helmholtz energy derivative with respect to density is
needed: Z = 1 + delta * d(alpha_r)/d(delta). The density is found for a
given pressure and temperature on the gas branch (Newton from the ideal gas).
Checked against MicroActive 7.00's own compressibility factors for N2 at
77.3 K, 0.001-737 mmHg.
"""
from __future__ import annotations

import functools
import math
import struct

MMHG_TO_KPA = 101.325 / 760.0

# Span et al. (2000), nitrogen.
_N2_TC = 126.192          # K
_N2_RHOC = 11.1839        # mol/L
_N2_R = 8.31451           # J/(mol K)
# (n, t, d, l): l = 0 polynomial term, l > 0 term times exp(-delta**l).
_N2_TERMS = (
    (0.924803575275, 0.250, 1, 0), (-0.492448489428, 0.875, 1, 0), (0.661883336938, 0.500, 2, 0),
    (-0.192902649201e1, 0.875, 2, 0), (-0.622469309629e-1, 0.375, 3, 0), (0.349943957581, 0.750, 3, 0),
    (0.564857472498, 0.500, 1, 1), (-0.161720005987e1, 0.750, 1, 1), (-0.481395031883, 2.000, 1, 1),
    (0.421150636384, 1.250, 3, 1), (-0.161962230825e-1, 3.500, 3, 1), (0.172100994165, 1.000, 4, 1),
    (0.735448924933e-2, 0.500, 6, 1), (0.168077305479e-1, 3.000, 6, 1), (-0.107626664179e-2, 0.000, 7, 1),
    (-0.137318088513e-1, 2.750, 7, 1), (0.635466899859e-3, 0.750, 8, 1), (0.304432279419e-2, 2.500, 8, 1),
    (-0.435762336045e-1, 4.000, 1, 2), (-0.723174889316e-1, 6.000, 2, 2), (0.389644315272e-1, 6.000, 3, 2),
    (-0.212201363910e-1, 3.000, 4, 2), (0.408822981509e-2, 3.000, 5, 2), (-0.551990017984e-4, 6.000, 8, 2),
    (-0.462016716479e-1, 16.000, 4, 3), (-0.300311716011e-2, 11.000, 5, 3), (0.368825891208e-1, 15.000, 5, 3),
    (-0.255856846220e-2, 12.000, 8, 3), (0.896915264558e-2, 12.000, 3, 4), (-0.441513370350e-2, 7.000, 5, 4),
    (0.133722924858e-2, 4.000, 6, 4), (0.264832491957e-3, 16.000, 9, 4),
)
# (n, t, d, eta, beta, gamma, epsilon): term times exp(-eta (delta-eps)^2 - beta (tau-gamma)^2).
_N2_GAUSS = (
    (0.196688194015e2, 0.0, 1, 20.0, 325.0, 1.16, 1.0),
    (-0.209115600730e2, 1.0, 1, 20.0, 325.0, 1.16, 1.0),
    (0.167788306989e-1, 2.0, 3, 15.0, 300.0, 1.13, 1.0),
    (0.262767566274e4, 3.0, 2, 25.0, 275.0, 1.25, 1.0),
)

# Mnemonics as stored in Micromeritics SMP files -> key in fluid_eos_data.
_ALIASES = {"N2": "N2", "AR": "AR", "KR": "KR", "CO2": "CO2", "NC4": "NC4", "N-C4": "NC4", "NC5": "NC5", "N-C5": "NC5",
            "NC6": "NC6", "N-C6": "NC6"}
SUPPORTED_ADSORPTIVES = frozenset(_ALIASES)


def _n2_delta_alpha_r_delta(delta: float, tau: float) -> float:
    total = 0.0
    for n, t, d, l in _N2_TERMS:
        if l == 0:
            total += n * d * delta ** d * tau ** t
        else:
            dl = delta ** l
            total += n * tau ** t * delta ** d * math.exp(-dl) * (d - l * dl)
    for n, t, d, eta, beta, gamma, eps in _N2_GAUSS:
        g = math.exp(-eta * (delta - eps) ** 2 - beta * (tau - gamma) ** 2)
        total += n * tau ** t * delta ** d * g * (d - 2.0 * eta * delta * (delta - eps))
    return total


def nitrogen_compressibility(pressure_mmHg: float, temperature_K: float) -> float:
    """Z of gaseous nitrogen (Span et al. 2000) at the given pressure and temperature."""
    pressure = float(pressure_mmHg) * MMHG_TO_KPA  # kPa = J/L
    temperature = float(temperature_K)
    if pressure <= 0.0:
        return 1.0
    if not 63.151 <= temperature <= 1000.0:
        raise ValueError(f"temperature outside the nitrogen equation of state range: {temperature} K")
    tau = _N2_TC / temperature
    rho = pressure / (_N2_R * temperature)  # ideal-gas start, mol/L
    for _ in range(50):
        delta = rho / _N2_RHOC
        z = 1.0 + _n2_delta_alpha_r_delta(delta, tau)
        residual = rho * _N2_R * temperature * z - pressure
        # dP/drho at constant T via a small relative step (gas branch is smooth here).
        h = rho * 1e-6
        delta_h = (rho + h) / _N2_RHOC
        slope = ((rho + h) * _N2_R * temperature * (1.0 + _n2_delta_alpha_r_delta(delta_h, tau)) - (pressure + residual)) / h
        step = residual / slope
        rho -= step
        if abs(step) <= 1e-14 * rho:
            break
    return 1.0 + _n2_delta_alpha_r_delta(rho / _N2_RHOC, tau)


def _generic_delta_alpha_r_delta(fluid: dict, delta: float, tau: float) -> float:
    total = 0.0
    for n, t, d, l in fluid["poly"]:
        if l == 0:
            total += n * d * delta ** d * tau ** t
        else:
            dl = delta ** l
            total += n * tau ** t * delta ** d * math.exp(-dl) * (d - l * dl)
    for n, t, d, eta, beta, gamma, eps in fluid["gauss"]:
        g = math.exp(-eta * (delta - eps) ** 2 - beta * (tau - gamma) ** 2)
        total += n * tau ** t * delta ** d * g * (d - 2.0 * eta * delta * (delta - eps))
    return total


def _generic_compressibility(fluid: dict, pressure_mmHg: float, temperature_K: float) -> float:
    pressure = float(pressure_mmHg) * MMHG_TO_KPA
    temperature = float(temperature_K)
    if pressure <= 0.0:
        return 1.0
    gas_constant, rho_r = fluid["R"], fluid["rhor"]
    tau = fluid["Tr"] / temperature
    rho = pressure / (gas_constant * temperature)

    def residual(r: float) -> float:
        return r * gas_constant * temperature * (1.0 + _generic_delta_alpha_r_delta(fluid, r / rho_r, tau)) - pressure

    for _ in range(100):
        h = rho * 1e-7
        slope = (residual(rho + h) - residual(rho - h)) / (2.0 * h)
        step = residual(rho) / slope
        rho -= step
        if abs(step) <= 1e-15 * rho:
            break
    return 1.0 + _generic_delta_alpha_r_delta(fluid, rho / rho_r, tau)


def compressibility(adsorptive: str, pressure_mmHg: float, temperature_K: float) -> float | None:
    """Z for a supported adsorptive, or None when no equation of state is available.

    N2: Span et al. (2000). Ar, Kr, CO2, n-butane, n-pentane: see
    fluid_eos_data (checked against MicroActive 7.00 on 3Flex example files).
    """
    key = _ALIASES.get((adsorptive or "").strip().upper())
    if key is None:
        return None
    if key == "N2":
        return nitrogen_compressibility(pressure_mmHg, temperature_K)
    from .fluid_eos_data import FLUIDS

    return _generic_compressibility(FLUIDS[key], pressure_mmHg, temperature_K)

# --- Saturation (vapour-liquid equilibrium) -----------------------------------

_N2_CONSTANTS = {"Ttriple": 63.151, "pc_kPa": 3395.8, "acentric": 0.0372}


def _fluid(adsorptive: str) -> dict | None:
    key = _ALIASES.get((adsorptive or "").strip().upper())
    if key is None:
        return None
    if key == "N2":
        return {"Tr": _N2_TC, "rhor": _N2_RHOC, "R": _N2_R, "poly": _N2_TERMS, "gauss": _N2_GAUSS, **_N2_CONSTANTS}
    from .fluid_eos_data import FLUIDS

    return FLUIDS[key]


def _alpha_r(fluid: dict, delta: float, tau: float) -> float:
    total = 0.0
    for n, t, d, l in fluid["poly"]:
        total += n * delta ** d * tau ** t * (math.exp(-delta ** l) if l else 1.0)
    for n, t, d, eta, beta, gamma, eps in fluid["gauss"]:
        total += n * tau ** t * delta ** d * math.exp(-eta * (delta - eps) ** 2 - beta * (tau - gamma) ** 2)
    return total


def saturation(adsorptive: str, temperature_K: float) -> tuple[float, float] | None:
    """(saturation pressure in mmHg, saturated liquid density in mol/L) at T.

    Equal pressure and Gibbs energy of both phases on the equation of state.
    None below the triple point, near the critical point or without an equation.
    MicroActive's 3Flex analysis temperature is the inverse of this at the
    mean measured P0 (5 N2 files and 1 Ar file to 2e-6 K).
    """
    fluid = _fluid(adsorptive)
    temperature = float(temperature_K)
    if fluid is None or not fluid["Ttriple"] <= temperature <= 0.95 * fluid["Tr"]:
        return None
    gas_constant, rho_r = fluid["R"], fluid["rhor"]
    tau = fluid["Tr"] / temperature

    def pressure(rho: float) -> float:
        return rho * gas_constant * temperature * (1.0 + _generic_delta_alpha_r_delta(fluid, rho / rho_r, tau))

    def gibbs(rho: float) -> float:  # residual + ln(rho); the ideal T-terms cancel between phases
        delta = rho / rho_r
        return math.log(rho) + _alpha_r(fluid, delta, tau) + _generic_delta_alpha_r_delta(fluid, delta, tau)

    def density(p: float, rho: float) -> float:
        for _ in range(100):
            h = rho * 1e-7
            step = (pressure(rho) - p) / ((pressure(rho + h) - pressure(rho - h)) / (2.0 * h))
            rho -= step
            if not rho > 0.0:
                raise ValueError("density iteration failed")
            if abs(step) <= 1e-14 * rho:
                break
        return rho

    # Wilson estimate as starting pressure.
    p = fluid["pc_kPa"] * 10.0 ** (7.0 / 3.0 * (1.0 + fluid["acentric"]) * (1.0 - fluid["Tr"] / temperature))
    liquid = 2.6 * rho_r
    try:
        for _ in range(200):
            liquid = density(p, liquid)
            vapour = density(p, p / (gas_constant * temperature))
            if abs(liquid - vapour) < 1e-6 * liquid:
                return None
            new_p = p - (gibbs(liquid) - gibbs(vapour)) * gas_constant * temperature / (1.0 / liquid - 1.0 / vapour)
            new_p = min(max(new_p, 0.5 * p), 1.5 * p)
            if abs(new_p - p) <= 1e-12 * p:
                p = new_p
                break
            p = new_p
        else:
            return None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return p / MMHG_TO_KPA, liquid


def saturation_temperature(adsorptive: str, pressure_mmHg: float, guess_K: float) -> float | None:
    """Temperature at which the saturation pressure equals pressure_mmHg."""
    temperature = float(guess_K)
    target = math.log(float(pressure_mmHg))
    for _ in range(50):
        here = saturation(adsorptive, temperature)
        there = saturation(adsorptive, temperature + 1e-4)
        if here is None or there is None:
            return None
        slope = (math.log(there[0]) - math.log(here[0])) / 1e-4
        step = (math.log(here[0]) - target) / slope
        temperature -= step
        if abs(step) < 1e-10:
            return temperature
    return None


# --- MicroActive's tabulated compressibility -----------------------------------

_TABLE_P_STEP = 10.0   # mmHg
_TABLE_T_STEP = 0.1    # K


@functools.lru_cache(maxsize=4096)
def _table_saturation_pressure(key: str, t_index: int) -> float | None:
    result = saturation(key, t_index * _TABLE_T_STEP)
    return result[0] if result is not None else None


@functools.lru_cache(maxsize=65536)
def _table_node(key: str, p_index: int, t_index: int) -> float | None:
    pressure, temperature = p_index * _TABLE_P_STEP, t_index * _TABLE_T_STEP
    saturated = _table_saturation_pressure(key, t_index)
    if saturated is not None and pressure > saturated:
        return None  # liquid at this node
    if p_index == 0:
        return 1.0
    value = compressibility(key, pressure, temperature)
    return float(struct.unpack("<f", struct.pack("<f", value))[0]) if value is not None else None


def tabulated_compressibility(adsorptive: str, pressure_mmHg: float, temperature_K: float) -> float | None:
    """Z as MicroActive 7.00 evaluates it for 3Flex files.

    Bilinear interpolation in a float32 table on a 10 mmHg x 0.1 K grid of
    the equation of state. Nodes in the liquid region are replaced by the
    node at the same pressure in the other temperature row (or, if both are
    liquid, by the neighbouring pressure). This reproduces MicroActive's
    compressibility factors to 3e-8 on 10 N2 files, including the plateau it
    shows next to saturation, and the exactly linear Z of Kr below 10 mmHg.
    """
    key = _ALIASES.get((adsorptive or "").strip().upper())
    if key is None or pressure_mmHg < 0:
        return None
    if compressibility(key, 1.0, temperature_K) is None:
        return None
    i = int(math.floor(pressure_mmHg / _TABLE_P_STEP))
    j = int(math.floor(temperature_K / _TABLE_T_STEP + 1e-9))
    fp = pressure_mmHg / _TABLE_P_STEP - i
    ft = temperature_K / _TABLE_T_STEP - j
    corner = {(a, b): _table_node(key, i + a, j + b) for a in (0, 1) for b in (0, 1)}
    for a in (0, 1):
        for b in (0, 1):
            if corner[a, b] is None:
                corner[a, b] = corner[a, 1 - b]
    for a in (0, 1):
        for b in (0, 1):
            if corner[a, b] is None:
                corner[a, b] = corner[1 - a, b] if corner[1 - a, b] is not None else corner[1 - a, 1 - b]
    if any(value is None for value in corner.values()):
        return None
    low = corner[0, 0] + (corner[1, 0] - corner[0, 0]) * fp
    high = corner[0, 1] + (corner[1, 1] - corner[0, 1]) * fp
    return low + (high - low) * ft
