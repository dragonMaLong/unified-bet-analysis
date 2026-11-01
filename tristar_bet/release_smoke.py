"""Opt-in CLI smoke check of the actual frozen executable and its workers."""
import json
from pathlib import Path
from types import SimpleNamespace


def run_ui_smoke(report_path: Path) -> int:
    """Import, display and close the real main window in the frozen application.

    Settings are private to this explicit diagnostic run. Only automatic network
    start-up callbacks are disabled; the complete UI and its imports are exercised.
    """
    import os
    import tempfile
    import traceback
    from .version import __version__

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
    report = {"version": __version__, "ok": False}
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    window = None
    app = None
    try:
        from PyQt5 import QtCore, QtGui, QtWidgets
        from .ui.main_window import MainWindow
        import bet_telemetry.schema

        with tempfile.TemporaryDirectory(prefix="bet-ui-smoke-") as directory:
            QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
            QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, directory)
            QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.SystemScope, directory)
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            app.setStyle("windowsvista")
            app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))

            class SmokeWindow(MainWindow):
                def _auto_check_for_updates(self):
                    pass

                def _initialize_telemetry(self):
                    pass

            window = SmokeWindow()
            window.show()

            def inspect_and_close():
                try:
                    if not window.isVisible() or not window.windowTitle():
                        raise RuntimeError("Main window did not become visible")
                    snapshot = report_path.with_suffix(".png")
                    if not window.grab().save(str(snapshot)):
                        raise RuntimeError("Could not capture the main window")
                    report.update(ok=True, title=window.windowTitle(),
                                  screenshot=str(snapshot), telemetry_schema_imported=True,
                                  widget_count=len(window.findChildren(QtWidgets.QWidget)))
                except Exception:
                    report["error"] = traceback.format_exc()
                finally:
                    window.close()
                    app.quit()

            QtCore.QTimer.singleShot(1200, inspect_and_close)
            app.exec_()
            window.close()
            window.deleteLater()
            QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
            window = None
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


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
