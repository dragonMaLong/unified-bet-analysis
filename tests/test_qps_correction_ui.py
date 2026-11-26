import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from test_quantachrome_qps_reduction import build_qps, sample_groups
from tristar_bet.i18n import tr
from tristar_bet.ui.main_window import MainWindow


class QpsCorrectionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.settings = QtCore.QSettings(str(Path(temp) / "settings.ini"), QtCore.QSettings.IniFormat)
        self.enterContext(patch.object(MainWindow, "_auto_check_for_updates"))
        self.enterContext(patch.object(MainWindow, "_initialize_telemetry"))
        with patch("tristar_bet.ui.main_window.QtCore.QSettings", return_value=self.settings):
            self.window = MainWindow()
        self.addCleanup(self.close_window)
        paths = []
        for name in ("A", "B"):
            path = Path(temp) / (name + ".qps")
            path.write_bytes(build_qps(sample_groups(adminData={"sampleId": name})))
            paths.append(str(path))
        self.window.load_files(paths, replace=True)

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def activate(self, label, enabled):
        table = self.window.metrics_table
        row = next(i for i in range(table.rowCount()) if table.item(i, 0).text() == tr(label))
        combo = table.cellWidget(row, 1)
        self.assertTrue(combo.isEnabled())
        combo.setCurrentIndex(0 if enabled else 1)
        combo.activated[int].emit(combo.currentIndex())

    def test_temperature_toggle_then_sample_switch_recalculates_only_original_sample(self):
        window = self.window
        a, b = window.results
        manual = (0.07, 0.28)
        window._set_custom_bet_fit_range(a, manual)
        window._set_custom_langmuir_fit_range(a, manual)
        window._set_custom_t_plot_fit_range(a, (0.4, 0.7))
        old_area = window._bet_analysis_for_result(a).surface_area_m2_g
        cached_b = window._bet_analysis_for_result(b)

        self.activate("温度补偿", True)
        window.sample_table.setCurrentCell(1, 1)
        self.app.processEvents()

        corrected = window.results[0]
        self.assertIs(window.active_result(), b)
        self.assertTrue(corrected.method_options["quantachrome_temperature_compensation_applied"])
        self.assertFalse(b.method_options["quantachrome_temperature_compensation_applied"])
        self.assertEqual(window._bet_fit_range_for_result(corrected), manual)
        self.assertEqual(window._langmuir_fit_range_for_result(corrected), manual)
        self.assertEqual(window._t_plot_fit_range_for_result(corrected), (0.4, 0.7))
        self.assertNotEqual(window._bet_analysis_for_result(corrected).surface_area_m2_g, old_area)
        self.assertIs(window._bet_analysis_for_result(b), cached_b)

    def test_thermal_toggle_replaces_fit_ranges_when_relative_pressures_change(self):
        window = self.window
        old = window.active_result()
        window._set_custom_bet_fit_range(old, (0.07, 0.28))
        window._set_custom_langmuir_fit_range(old, (0.07, 0.28))
        window._set_custom_t_plot_fit_range(old, (0.4, 0.7))

        self.activate("热流逸修正", False)
        self.app.processEvents()
        corrected = window.active_result()
        self.assertNotEqual(old.isotherm[0].relative_pressure, corrected.isotherm[0].relative_pressure)
        self.assertFalse(window._is_custom_bet_fit(corrected))
        self.assertFalse(window._is_custom_langmuir_fit(corrected))
        self.assertFalse(window._is_custom_t_plot_fit(corrected))
