"""Opt-in CLI smoke check of the actual frozen executable and its workers."""
import json
from pathlib import Path
from types import SimpleNamespace


def run_dft_smoke(report_path: Path) -> int:
    import numpy as np
    from .analysis import dft_pore_distribution
    from .dft_batch import DftBatchCalculator
    from .dft_models import dft_model_options, load_dft_model_kernel
    from .version import __version__
    report = {"version": __version__, "ok": False}
    pool = DftBatchCalculator(max_workers=2)
    try:
        # Every bundled model must be present in the frozen archive.
        models = dft_model_options()
        for key, _label in models:
            kernel = load_dft_model_kernel(key)
            if kernel is None:
                raise RuntimeError(f"Missing bundled kernel: {key}")
        report["model_count"] = len(models)
        points = [SimpleNamespace(index=i+1, phase="adsorption", relative_pressure=float(p),
                  quantity_adsorbed_cm3_g_stp=float(q*22.414), quantity_adsorbed_mmol_g=float(q))
                  for i, (p, q) in enumerate(zip(np.geomspace(1e-6, .99, 100),
                      np.linspace(.02, 4., 100)))]
        sample = SimpleNamespace(isotherm=points, adsorptive_properties=None, method_options={})
        jobs = [(sample, dict(model="n2_77_carbon_cylinder_qsdft_ads", include_diagnostics=True,
                             automatic_regularization=automatic, regularization=alpha))
                for automatic, alpha in [(True, .316), (False, .003)]]
        expected = [dft_pore_distribution(s, **options) for s, options in jobs]
        for repetition in range(2):
            actual = pool.calculate(jobs)
            if pool.last_error:
                raise RuntimeError(pool.last_error)
            for a, b in zip(expected, actual):
                if not a.ok or not b.ok or a.regularization != b.regularization or a.rows != b.rows:
                    raise RuntimeError("Frozen serial/worker result mismatch")
        report.update(ok=True, workers=2, pool_repetitions=2,
                      alphas=[r.regularization for r in expected],
                      distribution_rows=[len(r.rows) for r in expected])
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        pool.close()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
