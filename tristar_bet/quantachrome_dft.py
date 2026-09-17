"""Independent reconstruction of the GAI/QNNLS nonnegative inversion.

No vendor executable, reference CSV, or sample-specific fit constants are
used here. Production eligibility is deliberately narrower than parseability.
"""
from dataclasses import dataclass

import numpy as np
from threadpoolctl import threadpool_limits

from .dft_models import DftModelKernel, GAI_MODEL_SPECS
from .halsey_dft import nonnegative_least_squares


# Report validation and native-engine parity are different evidence levels.
# All 26 bundled profiles have two synthetic native-engine comparisons;
# 13 have user-exported reports, another 10 have real Ar87 input comparisons.
# Ar77 and the two CO2 profiles still lack real-sample report validation.
REPORT_VALIDATED_GAI_MODELS = frozenset({
    "n2_77_silica_cylinder_nldft_eq", "n2_77_silica_cylinder_nldft_ads",
    "n2_77_silica_cylinder_sphere_nldft_ads", "n2_77_carbon_slit_nldft_eq",
    "n2_77_carbon_slit_qsdft_eq", "n2_77_carbon_cylinder_qsdft_ads",
    "n2_77_carbon_cylinder_nldft_eq", "n2_77_carbon_slit_cylinder_nldft_eq",
    "n2_77_carbon_cylinder_qsdft_eq", "n2_77_carbon_cylinder_sphere_qsdft_ads",
    "n2_77_carbon_slit_cylinder_qsdft_ads", "n2_77_carbon_slit_cylinder_qsdft_eq",
    "n2_77_carbon_slit_cylinder_sphere_qsdft_ads",
})
_NATIVE_VALIDATED_IDS = frozenset(f"QK{i:03}" for i in range(1, 27))
VALIDATED_GAI_MODELS = frozenset(spec.key for spec in GAI_MODEL_SPECS
                                 if spec.model_id in _NATIVE_VALIDATED_IDS)


def central_derivative(x: np.ndarray, cumulative: np.ndarray) -> np.ndarray:
    """Secant of cumulative output over neighboring report nodes."""
    if len(x) < 2 or np.any(np.diff(x) <= 0):
        raise ValueError("At least two increasing report coordinates required")
    out = np.empty_like(cumulative, dtype=float)
    out[1:-1] = (cumulative[2:] - cumulative[:-2]) / (x[2:] - x[:-2])
    out[0] = (cumulative[1] - cumulative[0]) / (x[1] - x[0])
    out[-1] = (cumulative[-1] - cumulative[-2]) / (x[-1] - x[-2])
    return out


def merge_equilibrium_branches(adsorption: np.ndarray, desorption: np.ndarray) -> np.ndarray:
    """Native equilibrium input: low-p adsorption prepended to desorption.

    Arrays are (p/p0, mmol/g), with pressure increasing. The 0.4 ceiling and
    turnover exclusion follow qcgsDRPhys 0x484428. The app already assigns
    the shared turnover point to adsorption only.
    """
    if len(desorption) < 2:
        return adsorption.copy()
    des = desorption
    if len(adsorption) and des[-1, 0] == adsorption[-1, 0]:
        des = des[:-1]
    low = adsorption[(adsorption[:, 0] <= min(des[0, 0], .4))]
    return np.vstack((low, des))


def native_width_limits(kernel: DftModelKernel, low: float, high: float) -> tuple[float, float]:
    """Filling-pressure based bounds, independent of reference report widths."""
    meta = kernel.gai
    if meta is None:
        raise ValueError("GAI numerical metadata unavailable")
    w, p, matrix = kernel.pore_widths_nm, kernel.pressures, kernel.kernel
    transitions = meta.transition_pressures.copy()
    if not meta.option("FC") and not meta.option("E"):
        slopes = np.diff(matrix, axis=0) / np.diff(np.log(p))[:, None]
        indices = np.argmax(slopes, axis=0)
        transitions = (p[indices] + p[indices + 1]) * .5
    increasing = np.flatnonzero((transitions[1:-1] < transitions[2:]) &
                                (transitions[1:-1] > transitions[:-2]))
    if not len(increasing):
        raise ValueError("No increasing GAI filling-pressure range")
    minimum_width = w[increasing[0]]
    lower_candidates = np.flatnonzero((transitions >= low) & (w > minimum_width))
    upper_candidates = np.flatnonzero((transitions < high) & (w > minimum_width))
    if not len(lower_candidates) or not len(upper_candidates):
        raise ValueError("No resolvable pore-width range")
    ilo = max(0, int(lower_candidates[0]) - 1)
    ihi = int(upper_candidates[-1])
    if np.count_nonzero((p[1:] <= high) & (p[1:] > transitions[ihi])) < 2:
        ihi -= 1
    if ihi <= ilo:
        raise ValueError("Insufficient resolved pore widths")
    return float(w[ilo]), float(w[ihi])


@dataclass
class GaiProblem:
    pressure: np.ndarray
    target_mmol: np.ndarray
    matrix: np.ndarray
    widths: np.ndarray
    volume_per_area: np.ndarray  # volume per solver coefficient; unity for volume-basis Ar77
    penalty: np.ndarray
    report_limits: tuple[float, float]
    regularization_order: int

    def solve(self, alpha: float | None = None, *, solver=nonnegative_least_squares):
        """Normalized NNLS; None selects from the native 21-candidate grid."""
        # These small dense passive systems lose time to BLAS thread startup.
        # Parallelism belongs between samples, not inside each tiny factorization.
        with threadpool_limits(limits=1, user_api="blas"):
            return self._solve(alpha, solver=solver)

    def _solve(self, alpha: float | None, *, solver):
        singular = float(np.linalg.svd(self.matrix, compute_uv=False)[0])
        norm = float(np.linalg.norm(self.target_mmol))
        if not singular > 0 or not norm > 0:
            raise ValueError("Zero kernel or uptake norm")
        a, b = self.matrix / singular, self.target_mmol / norm
        alphas = 10. ** (-2 - .2*np.arange(21)) if alpha is None else [float(alpha)]
        candidates = []
        previous = None
        for value in alphas:
            if not np.isfinite(value) or value < 0:
                raise ValueError("Invalid regularization strength")
            augmented = np.vstack((a, value * self.penalty))
            target = np.r_[b, np.zeros(len(self.penalty))]
            if solver is nonnegative_least_squares:
                try:
                    z = solver(augmented, target, initial=previous)
                except RuntimeError:
                    if previous is None:
                        raise
                    # A pathological warm active set must not turn a case the
                    # established cold solver handles into a new failure.
                    z = solver(augmented, target)
            else:
                # Preserve the injectable cold solver for independent parity checks.
                z = solver(augmented, target)
            previous = z
            residual = float(np.linalg.norm(a @ z - b))
            score = residual * float(np.linalg.norm(z))
            area = z * norm / singular
            candidates.append((score, float(value), area, dict(
                regularization=float(value),
                rms_error_mmol_g=float(np.sqrt(np.mean((self.matrix @ area - self.target_mmol)**2))),
                distribution_roughness=float(np.linalg.norm(self.penalty @ area)),
                selection_score=score)))
        selected = min(candidates, key=lambda item: item[0])
        return selected[2], selected[1], [item[3] for item in candidates]

    def distribution(self, area: np.ndarray, alpha: float, phase: str):
        increments = area * self.volume_per_area
        cumulative = np.cumsum(increments)
        shown = np.flatnonzero((self.widths >= self.report_limits[0]) &
                               (self.widths <= self.report_limits[1]))
        # Derivatives are taken on the reported cumulative table, while its
        # cumulative values retain all fitted sub-resolution contributions.
        linear = np.zeros_like(cumulative)
        logarithmic = np.zeros_like(cumulative)
        linear[shown] = central_derivative(self.widths[shown], cumulative[shown])
        logarithmic[shown] = central_derivative(np.log10(self.widths[shown]), cumulative[shown])
        edges = np.r_[self.widths[0]**1.5 / self.widths[1]**.5,
                      np.sqrt(self.widths[:-1] * self.widths[1:]),
                      self.widths[-1]**1.5 / self.widths[-2]**.5]
        rows = []
        for i, width in enumerate(self.widths):
            if not self.report_limits[0] <= width <= self.report_limits[1]:
                continue
            rows.append(dict(phase=phase, pore_width_nm=float(width), pore_diameter_nm=float(width),
                cumulative_pore_diameter_nm=float(width), pore_width_low_nm=float(edges[i]),
                pore_width_high_nm=float(edges[i+1]), dwidth_nm=float(edges[i+1]-edges[i]),
                dlog_diameter=float(np.log10(edges[i+1]/edges[i])),
                incremental_pore_volume_cm3_g=float(increments[i]),
                cumulative_pore_volume_cm3_g=float(cumulative[i]),
                differential_pore_volume_per_nm_cm3_g_nm=float(linear[i]),
                differential_pore_volume_cm3_g=float(logarithmic[i]), dft_regularization=float(alpha)))
        return rows


def prepare_gai(kernel: DftModelKernel, points: np.ndarray) -> GaiProblem:
    """Prepare a kernel-grid problem with GAI header and tail rules."""
    meta = kernel.gai
    if (meta is None or meta.geometry_divisors is None or
            kernel.spec.kernel_basis not in {"surface_mmol", "volume_mmol"}):
        raise ValueError("Unsupported GAI numerical profile")
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2:
        raise ValueError("Isotherm must have pressure and uptake columns")
    points = points[np.all(np.isfinite(points), axis=1) & (points[:, 0] > 0) & (points[:, 0] < 1)
                    & (points[:, 1] >= 0)]
    points = points[np.argsort(points[:, 0], kind="stable")]
    # Binary handoff to QNNLS uses float32; reject decreasing uptake points.
    points = points.astype(np.float32).astype(float)
    kept = []
    for point in points:
        if not kept or (point[0] > kept[-1][0] and point[1] >= kept[-1][1]):
            kept.append(point)
    if len(kept) < 3:
        raise ValueError("Insufficient monotonic isotherm points")
    p, q = np.asarray(kept).T
    high = min(meta.option("R", 1.), p[-1])
    if high == p[-1]:
        high -= .1*(p[-1]-p[-2])
    before_high = np.flatnonzero(p < high)
    if not len(before_high):
        raise ValueError("No points inside kernel pressure range")
    fraction = meta.option("LA")
    lower = np.flatnonzero(q > q[before_high[-1]]*fraction)
    if not len(lower):
        raise ValueError("Insufficient positive uptake")
    low = max(meta.option("L", 1e-7), p[lower[0]])
    if low == p[0]:
        low += .1*(p[1]-p[0])
    report_limits = native_width_limits(kernel, low, high)
    # The native initializer sets FL=1: unresolved small columns participate
    # in fitting/cumulative volume, even when omitted from the report.
    fix_lower = meta.option("FL", meta.option("F", 1.))
    width_low = meta.option("LH", 0.) * .1 if fix_lower else report_limits[0]
    width_high = meta.option("RH", 1000.) * .1 if meta.option("F") or meta.option("FR") else report_limits[1]
    # REPA=1 reports all fitted columns rather than applying the resolvable
    # filling-pressure bounds (QNNLS32 0x407c93). CO2 fixes those columns via
    # F/LH/RH; its report must not be cropped again to the filling range.
    if meta.option("REPA"):
        report_limits = (width_low, width_high)
    stride = max(1, int(meta.option("H", 1)))
    columns = np.arange(stride-1, len(kernel.pore_widths_nm), stride)
    columns = columns[(kernel.pore_widths_nm[columns] >= width_low) &
                      (kernel.pore_widths_nm[columns] <= width_high)]
    rows = (kernel.pressures >= low) & (kernel.pressures <= high)
    pressure = kernel.pressures[rows]
    widths = kernel.pore_widths_nm[columns]
    if len(pressure) < 3 or len(widths) < 3:
        raise ValueError("Insufficient kernel overlap")
    # Older kernels omit I and use linear pressure interpolation.
    target = (np.interp(np.log(pressure), np.log(p), q) if meta.option("I")
              else np.interp(pressure, p, q))
    penalty = np.eye(len(columns))
    order = 2 if meta.option("S") == 1 else 0
    if order == 2:
        penalty = -2*penalty + np.eye(len(columns), k=1) + np.eye(len(columns), k=-1)
    volume_factor = (np.ones(len(columns)) if kernel.spec.kernel_basis == "volume_mmol"
                     else widths*.001/meta.geometry_divisors[columns])
    return GaiProblem(pressure, target, kernel.kernel[np.ix_(rows, columns)], widths,
                      volume_factor, penalty, report_limits, order)
