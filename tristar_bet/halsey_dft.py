"""Validated MOD005 adsorption inversion (non-AMS, log-grid second derivative).

Deliberately not a general replacement for other DFT/GAI models. All input
comes from the sample and bundled kernel; no vendor installation is required.
See outputs/microactive_dft_alignment/Halsey_三组正则化对齐说明.md.
"""
from dataclasses import dataclass

import numpy as np

from .dft_models import DftModelKernel


def akima_interpolate(x: np.ndarray, y: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Original Akima cubic Hermite interpolation; no extrapolation."""
    x, y, query = map(lambda a: np.asarray(a, dtype=float), (x, y, query))
    if len(x) < 2 or np.any(np.diff(x) <= 0):
        raise ValueError("Akima requires at least two distinct sorted pressures")
    if np.any(query < x[0]) or np.any(query > x[-1]):
        raise ValueError("Kernel pressures must be inside the measurement range")
    if len(x) == 2:
        return np.interp(query, x, y)
    slopes = np.empty(len(x) + 3)
    slopes[2:-2] = np.diff(y) / np.diff(x)
    slopes[1] = 2 * slopes[2] - slopes[3]
    slopes[0] = 2 * slopes[1] - slopes[2]
    slopes[-2] = 2 * slopes[-3] - slopes[-4]
    slopes[-1] = 2 * slopes[-2] - slopes[-3]
    weights = np.abs(np.diff(slopes))
    left, right = weights[2:], weights[:-2]
    total = left + right
    tangent = .5 * (slopes[3:] + slopes[:-3])
    active = total > 1e-9 * np.max(total)
    tangent[active] = ((left * slopes[1:-2] + right * slopes[2:-1])[active]
                       / total[active])
    i = np.clip(np.searchsorted(x, query, side="right") - 1, 0, len(x)-2)
    h = x[i+1] - x[i]
    t = (query - x[i]) / h
    return ((2*t**3 - 3*t**2 + 1)*y[i] + (t**3 - 2*t**2 + t)*h*tangent[i]
            + (-2*t**3 + 3*t**2)*y[i+1] + (t**3-t**2)*h*tangent[i+1])


def nonnegative_least_squares(matrix: np.ndarray, target: np.ndarray, *, initial: np.ndarray | None = None) -> np.ndarray:
    """Lawson-Hanson active set with direct least-squares passive solves.

    Common scaling preserves the objective, unlike per-column normalization
    followed by penalizing the normalized coefficients. Fail explicitly on
    non-convergence instead of returning a fixed-iteration approximation.
    """
    scale = max(float(np.max(np.abs(target))), 1.0)
    a, b = matrix / scale, target / scale
    n = a.shape[1]
    if initial is None:
        x = np.zeros(n)
    else:
        x = np.asarray(initial, dtype=float).copy()
        if x.shape != (n,) or not np.all(np.isfinite(x)) or np.any(x < 0):
            raise ValueError("Invalid NNLS initial solution")
    passive = x > 0
    tolerance = 10 * max(a.shape) * np.finfo(float).eps * max(1., np.linalg.norm(a, 1))
    iterations = 0
    # A warm start is feasible, but is NOT stationary for the new alpha.
    # Re-solve its passive set before testing the inactive KKT conditions.
    needs_passive_solve = bool(np.any(passive))
    while True:
        if not needs_passive_solve:
            gradient = a.T @ (b - a @ x)
            candidates = (~passive) & (gradient > tolerance)
            if not np.any(candidates):
                return np.maximum(x, 0.)
            passive[np.argmax(np.where(candidates, gradient, -np.inf))] = True
        needs_passive_solve = False
        while True:
            iterations += 1
            if iterations > 30 * n:
                raise RuntimeError("Halsey NNLS did not converge")
            z = np.zeros(n)
            z[passive] = np.linalg.lstsq(a[:, passive], b, rcond=None)[0]
            blocked = passive & (z <= 0)
            if not np.any(blocked):
                x = z
                break
            alpha = np.min(x[blocked] / (x[blocked] - z[blocked]))
            x += alpha * (z-x)
            released = passive & (x <= np.finfo(float).eps * max(1., np.max(x)))
            x[released] = 0
            passive[released] = False


def log_grid_penalty(widths: np.ndarray) -> np.ndarray:
    n = len(widths)
    if n < 3 or np.any(np.diff(widths) <= 0):
        raise ValueError("Invalid regularization width grid")
    step = np.log(widths[-2] / widths[0]) / (n-2)
    delta = np.log(widths[1:] / widths[:-1]) / step
    bins = np.r_[delta[0], (delta[1:]+delta[:-1])/2, delta[-1]]
    penalty = np.zeros((n-2, n))
    for i in range(1, n-1):
        h1, h2 = delta[i-1:i+1]
        mid = (h1+h2)/2
        penalty[i-1, i-1:i+2] = [1/(bins[i-1]*h1*mid),
                                    -2/(bins[i]*h1*h2),
                                    1/(bins[i+1]*h2*mid)]
    return penalty


@dataclass
class HalseyProblem:
    pressure: np.ndarray
    target_stp: np.ndarray
    matrix: np.ndarray
    widths: np.ndarray
    columns: np.ndarray
    report_low: int
    report_high: int
    penalty: np.ndarray

    def solve(self, regularization: float) -> np.ndarray:
        strength = regularization * np.sqrt(np.sum(self.matrix**2) / (6*(len(self.columns)-2)))
        return nonnegative_least_squares(
            np.vstack([self.matrix, strength*self.penalty]),
            np.r_[self.target_stp, np.zeros(len(self.columns)-2)],
        )

    def distribution(self, area: np.ndarray, regularization: float, phase: str) -> list[dict]:
        all_area = np.zeros(len(self.widths))
        all_area[self.columns] = area
        volume = all_area*self.widths/4000
        cumulative = np.cumsum(volume)
        widths = self.widths[self.report_low:self.report_high+1]
        # Arithmetic bin widths for dV/dW; log widths for dV/dlog10(W).
        edges = np.r_[widths[0]-(widths[1]-widths[0])/2,
                      (widths[:-1]+widths[1:])/2,
                      widths[-1]+(widths[-1]-widths[-2])/2]
        log_delta = np.diff(np.log10(edges))
        rows = []
        for j, w in enumerate(widths):
            i = j+self.report_low
            delta = edges[j+1]-edges[j]
            rows.append(dict(phase=phase, pore_width_nm=float(w), pore_diameter_nm=float(w),
                             cumulative_pore_diameter_nm=float(w),
                             pore_width_low_nm=float(edges[j]), pore_width_high_nm=float(edges[j+1]),
                             incremental_pore_volume_cm3_g=float(volume[i]),
                             cumulative_pore_volume_cm3_g=float(cumulative[i]),
                             dwidth_nm=float(delta), dlog_diameter=float(log_delta[j]),
                             differential_pore_volume_per_nm_cm3_g_nm=float(volume[i]/delta),
                             differential_pore_volume_cm3_g=float(volume[i]/log_delta[j]),
                             dft_regularization=regularization))
        return rows

    def metrics(self, area: np.ndarray) -> tuple[float, float]:
        rms = float(np.sqrt(np.mean(((self.matrix @ area-self.target_stp)/22.414)**2)))
        roughness = float(np.sqrt(np.mean((self.penalty @ area)**2)))
        return rms, roughness


def prepare_halsey(kernel: DftModelKernel, pressure: np.ndarray, quantity_stp: np.ndarray) -> HalseyProblem:
    threshold = kernel.filling_threshold
    if threshold is None or not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("Missing MOD005 filling threshold")
    unique = np.r_[np.diff(pressure) != 0, True]  # retain final duplicate as in validation
    p, q = pressure[unique], quantity_stp[unique]
    selected = (kernel.pressures >= p[0]) & (kernel.pressures <= p[-1])
    if np.count_nonzero(selected) < 3:
        raise ValueError("Insufficient kernel pressure coverage")
    kp, mat, widths = kernel.pressures[selected], kernel.kernel[selected], kernel.pore_widths_nm
    target = akima_interpolate(p, q, kp)
    filling = mat/(widths[None, :]*10)*1e8/22414/10000*4
    lower = np.flatnonzero(filling[0] >= threshold)
    low = int(lower[-1]+1) if len(lower) and not selected[0] else 0
    upper = np.flatnonzero(filling[-1] < threshold)
    high = int(upper[0]-1) if len(upper) else len(widths)-2
    high = min(high, len(widths)-2)  # never report the background column as pore volume
    if high-low < 1:
        raise ValueError("Insufficient resolved pore-width range")
    cols = np.unique(np.r_[np.arange(min(high+3, len(widths)-1)), len(widths)-1])
    return HalseyProblem(kp, target, mat[:, cols], widths, cols, low, high,
                         log_grid_penalty(widths[cols]))
