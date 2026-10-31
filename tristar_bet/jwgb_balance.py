"""Gas-balance diagnostic for JWGB pressure records; never produces uptake.

The dosed gas is Vd * 2.69578 * sum(Pd/Td - Pcd/Tcd) in cm3 STP (kPa, K). An
empty or non-adsorbing tube may follow dosed/Vd = a*P + b*P**2. However,
adsorption terms proportional to P or P**2 are indistinguishable from these
fitted coefficients. Small residuals therefore establish compatibility with
this empirical model, not absence of adsorption or an independent Q estimate.
This diagnostic does not reproduce vendor results or validate reconstructed
uptake. Its thresholds are exploratory, not calibrated detection limits.
"""
from __future__ import annotations

import math

import numpy as np

KPA_CM3_K_TO_STP = 2.69578
REFERENCE_PD = -0.00001
FIT_MAX_RELATIVE_PRESSURE = 0.90
# Bounds are in cm3 STP/g. Observed on the BK200C sample: 0.03 and 0.23.
MAX_FIT_RMS = 0.10
MAX_HOLDOUT_RMS = 0.50
MIN_FIT_POINTS = 8
MIN_HOLDOUT_POINTS = 4
Q_MISMATCH_FRACTION = 0.05


def dosed_series(records, phase, start=0.0):
    """Return pressure, P/P0 and cumulative dosed gas in units of Vd."""
    pressures, relative, dosed = [], [], []
    total = start
    for row in records:
        if row["phase"] != phase or float(row["Pd"]) == REFERENCE_PD:
            continue
        pd, pcd = float(row["Pd"]), float(row["Pcd"])
        pd_temperature, pcd_temperature = float(row["PdT"]) + 273.15, float(row["PcdT"]) + 273.15
        total += KPA_CM3_K_TO_STP * (pd / pd_temperature - pcd / pcd_temperature)
        pressures.append(pcd)
        relative.append(pcd / float(row["P0"]))
        dosed.append(total)
    return np.array(pressures), np.array(relative), np.array(dosed)


def fit_free_space(pressures, dosed):
    """Fit dosed = a*P + b*P**2 through the origin, returning (a, b)."""
    design = np.column_stack([pressures, pressures ** 2])
    return np.linalg.lstsq(design, dosed, rcond=None)[0]


def balance_residuals(pressures, dosed, coefficients):
    return dosed - (coefficients[0] * pressures + coefficients[1] * pressures ** 2)


def _rms(values):
    return float(np.sqrt(np.mean(values ** 2)))


def free_space_diagnostic(records, vd, stored_q, mass):
    """Summarise a run's gas balance, or return None when it cannot be judged.

    The desorption branch stays out of the fit, providing a held-out residual
check. Reversible adsorption can also pass that check. Files without
temperatures or calibration return None.
    """
    try:
        vd, stored_q, mass = float(vd), float(stored_q), float(mass)
        pressures, relative, dosed = dosed_series(records, "adsorption")
        window = relative <= FIT_MAX_RELATIVE_PRESSURE
        if window.sum() < MIN_FIT_POINTS or vd <= 0 or stored_q <= 0 or mass <= 0:
            return None
        desorption_pressures, _, desorption_dosed = dosed_series(records, "desorption", dosed[-1])
    except (KeyError, TypeError, ValueError):
        return None
    if len(desorption_pressures) < MIN_HOLDOUT_POINTS:
        return None
    if not all(map(math.isfinite, (*dosed, *desorption_dosed))):
        return None
    coefficients = fit_free_space(pressures[window], dosed[window])
    to_uptake = vd / mass
    fit_rms = _rms(balance_residuals(pressures[window], dosed[window], coefficients)) * to_uptake
    holdout_rms = _rms(balance_residuals(desorption_pressures, desorption_dosed, coefficients)) * to_uptake
    fitted_q = float(coefficients[0])
    return {
        "apparent_q": fitted_q, "stored_q": stored_q,
        "pressure_quadratic_coefficient": float(coefficients[1]),
        "fit_rms_cm3_g": fit_rms, "desorption_holdout_rms_cm3_g": holdout_rms,
        "fit_points": int(window.sum()), "holdout_points": len(desorption_pressures),
        "compatible_with_quadratic_balance": fit_rms <= MAX_FIT_RMS and holdout_rms <= MAX_HOLDOUT_RMS,
        "apparent_q_differs_from_stored": abs(fitted_q / stored_q - 1) > Q_MISMATCH_FRACTION,
        "adsorption_identifiable": False,
    }


def diagnostic_notice(diagnostic):
    """Describe a flagged run for the import status row, else return ''."""
    if not diagnostic or not diagnostic["compatible_with_quadratic_balance"]:
        return ""
    notice = ("累计气量可由压力二次模型拟合"
              f"（拟合残差 {diagnostic['fit_rms_cm3_g']:.3f} cm³/g）；"
              "吸附量与自由空间项可能混叠，不能据此判定没有吸附。")
    if diagnostic["apparent_q_differs_from_stored"]:
        notice += (f"文件 Q={diagnostic['stored_q']:.6f}，拟合的表观系数为 "
                   f"{diagnostic['apparent_q']:.6f}；该系数可能包含吸附贡献，"
                   "不能据此替换文件 Q 或认定文件 Q 错误。")
    return notice
