"""UI caches distinguish report defaults from manual intervals at the same endpoints."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from tristar_bet.analysis import bet_analysis, langmuir_analysis, t_plot_analysis, t_plot_analysis_by_thickness
from tristar_bet.ui.main_window import MainWindow, active_metric_rows

from qt_window_support import isolated_main_window
from test_excel_report_point_selection import _pressures, _report_sample, _without_selection


class ReportSelectionCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = isolated_main_window(self, MainWindow)
        self.result = _report_sample()

    def test_bet_and_langmuir_default_manual_default_keep_separate_caches(self):
        for method, function in (("bet", bet_analysis), ("langmuir", langmuir_analysis)):
            with self.subTest(method=method):
                cached = getattr(self.window, f"_cached_{method}_analysis")
                table = getattr(self.window, f"_{method}_analysis_for_result")
                set_custom = getattr(self.window, f"_set_custom_{method}_fit_range")
                clear_custom = getattr(self.window, f"_clear_custom_{method}_fit_range")
                default_table = table(self.result)
                default_plot = cached(self.result, 0.05, 0.20)
                self.assertEqual(_pressures(default_table), [0.05, 0.10, 0.20])
                self.assertEqual(default_plot, default_table)
                self.assertIs(cached(self.result, 0.05, 0.20), default_plot)

                set_custom(self.result, (0.05, 0.20))
                manual = table(self.result)
                self.assertEqual(_pressures(manual), [0.05, 0.08, 0.10, 0.15, 0.20])
                self.assertEqual(manual, function(_without_selection(self.result), 0.05, 0.20))
                self.assertIs(cached(self.result, 0.05, 0.20), manual)
                self.assertIsNot(manual, default_plot)

                clear_custom(self.result)
                self.assertIs(table(self.result), default_table)
                self.assertIs(cached(self.result, 0.05, 0.20), default_plot)

    def test_t_plot_pressure_and_thickness_caches_follow_manual_interval_state(self):
        settings = self.window._t_plot_settings_for_result(self.result)
        interval = self.window._t_plot_fit_range_for_result(self.result)
        options = dict(thickness_params=settings["thickness_params"], thickness_method=settings["thickness_method"])
        pressure_fit = lambda: self.window._cached_t_plot_pressure_analysis(self.result, 0.15, 0.40, **options)
        thickness_fit = lambda: self.window._cached_t_plot_thickness_analysis(self.result, *interval, **options)
        default_pressure, default_thickness = pressure_fit(), thickness_fit()
        self.assertEqual(_pressures(default_pressure), [0.15, 0.30, 0.40])
        self.assertEqual(_pressures(default_thickness), [0.15, 0.30, 0.40])
        self.assertIs(self.window._t_plot_analysis_for_result(self.result), default_thickness)

        self.window._set_custom_t_plot_fit_range(self.result, interval)
        manual_pressure, manual_thickness = pressure_fit(), thickness_fit()
        self.assertEqual(_pressures(manual_pressure), [0.15, 0.20, 0.25, 0.30, 0.40])
        self.assertEqual(_pressures(manual_thickness), [0.15, 0.20, 0.25, 0.30, 0.40])
        self.assertEqual(manual_pressure, t_plot_analysis(_without_selection(self.result), 0.15, 0.40, **options))
        self.assertEqual(manual_thickness, t_plot_analysis_by_thickness(_without_selection(self.result), *interval, **options))
        self.assertIs(self.window._t_plot_analysis_for_result(self.result), manual_thickness)
        self.assertIs(pressure_fit(), manual_pressure)
        self.assertIsNot(manual_pressure, default_pressure)
        self.assertIsNot(manual_thickness, default_thickness)

        self.window._clear_custom_t_plot_fit_range(self.result)
        self.assertIs(pressure_fit(), default_pressure)
        self.assertIs(thickness_fit(), default_thickness)
        self.assertIs(self.window._t_plot_analysis_for_result(self.result), default_thickness)

    def test_custom_t_plot_settings_disable_report_ids_without_reusing_default_cache(self):
        default = self.window._t_plot_analysis_for_result(self.result)
        self.window.custom_t_plot_settings[id(self.result)] = {"surface_area_correction": 2.0}
        self.assertTrue(self.window._is_custom_t_plot_fit(self.result))
        manual = self.window._t_plot_analysis_for_result(self.result)
        self.assertEqual(_pressures(manual), [0.15, 0.20, 0.25, 0.30, 0.40])
        self.assertIsNot(manual, default)
        del self.window.custom_t_plot_settings[id(self.result)]
        self.assertIs(self.window._t_plot_analysis_for_result(self.result), default)

    def test_custom_model_settings_do_not_remove_existing_vendor_pressure_window(self):
        self.result.method_options.update(
            use_vendor_t_plot_settings=True,
            stored_t_plot_pressure_window_min=0.10,
            stored_t_plot_pressure_window_max=0.50,
        )
        self.assertEqual(self.window._t_plot_default_pressure_window(self.result), (0.10, 0.50))
        self.window.custom_t_plot_settings[id(self.result)] = {"thickness_method": "halsey"}
        self.assertTrue(self.window._is_custom_t_plot_fit(self.result))
        self.assertEqual(self.window._t_plot_default_pressure_window(self.result), (0.10, 0.50))
        self.window._set_custom_t_plot_fit_range(self.result, (0.30, 0.50))
        self.assertIsNone(self.window._t_plot_default_pressure_window(self.result))

    def test_metric_rows_use_supplied_manual_analyses_without_recomputing_defaults(self):
        interval = self.window._t_plot_fit_range_for_result(self.result)
        self.window._set_custom_bet_fit_range(self.result, (0.05, 0.20))
        self.window._set_custom_langmuir_fit_range(self.result, (0.05, 0.20))
        self.window._set_custom_t_plot_fit_range(self.result, interval)
        fits = {
            "bet_analysis_result": self.window._bet_analysis_for_result(self.result),
            "langmuir_analysis_result": self.window._langmuir_analysis_for_result(self.result),
            "t_plot_analysis_result": self.window._t_plot_analysis_for_result(self.result),
        }
        self.assertEqual([fit.point_count for fit in fits.values()], [5, 5, 5])
        with patch("tristar_bet.ui.main_window.analysis_bundle", side_effect=AssertionError("must use supplied fits")):
            rows = active_metric_rows(self.result, bet_fit_range=(0.05, 0.20),
                                      langmuir_fit_range=(0.05, 0.20), t_plot_fit_range=interval, **fits)
        self.assertTrue(rows)


if __name__ == "__main__":
    unittest.main()
