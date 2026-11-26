"""QPS report tags select exact points; manual regions select their whole interval."""

import os
from io import BytesIO
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from openpyxl import load_workbook
from PyQt5 import QtWidgets

from tristar_bet.analysis import (
    analysis_bundle, automatic_bet_range, automatic_langmuir_range,
    automatic_t_plot_pressure_range, bet_analysis, langmuir_analysis,
    t_plot_analysis, t_plot_analysis_by_thickness, thickness_nm,
)
from tristar_bet.quantachrome import load_qps
from tristar_bet.ui.main_window import MainWindow, export_results_xlsx

from qt_window_support import isolated_main_window
from test_quantachrome_qps_reduction import build_qps, sample_groups


SELECTIONS = {"bet": (2, 4, 6), "langmuir": (3, 5, 7), "t_plot": (4, 6, 8)}
METHODS = (
    ("bet", "BET", bet_analysis, automatic_bet_range),
    ("langmuir", "Langmuir", langmuir_analysis, automatic_langmuir_range),
    ("t_plot", "t-Plot", t_plot_analysis, automatic_t_plot_pressure_range),
)


def tagged_sample(selections=None):
    selections = SELECTIONS if selections is None else selections
    groups = sample_groups(DRedParams={"thermalOn": 0})
    data = groups["Data"]
    for offset in range(data["NPoints"]):
        tag = list(data[f"PtFlg{offset:03d}"])
        # ASiQwin tag positions: M = multi-point BET, L = Langmuir, T = t-Plot.
        for method, position, letter in (("bet", 2, "M"), ("langmuir", 6, "L"), ("t_plot", 4, "T")):
            tag[position] = letter if offset + 1 in selections.get(method, ()) else "_"
        data[f"PtFlg{offset:03d}"] = "".join(tag)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "noncontiguous.qps"
        path.write_bytes(build_qps(groups))
        return load_qps(path)


def indices(fit):
    return [int(row["point_index"]) for row in fit.rows]


class TaggedFitTests(unittest.TestCase):
    def test_default_fits_and_range_entry_points_use_exact_tagged_points(self):
        result = tagged_sample()
        bundle = analysis_bundle(result)
        for method, label, function, automatic_range in METHODS:
            with self.subTest(method=method):
                expected_ids = list(SELECTIONS[method])
                points = [point for point in result.isotherm if point.index in expected_ids]
                interval = (points[0].relative_pressure, points[-1].relative_pressure)
                self.assertEqual(result.method_options[f"quantachrome_{method}_point_indices"], expected_ids)
                self.assertEqual(automatic_range(result), interval)
                fits = (function(result), function(result, *interval), bundle[label])
                for fit in fits:
                    self.assertEqual(indices(fit), expected_ids)
                    self.assertEqual(fit.point_count, len(expected_ids))
                # Independently fit just the vendor-designated observations.
                pressures = np.array([point.relative_pressure for point in points])
                quantities = np.array([point.quantity_adsorbed_cm3_g_stp for point in points])
                if method == "bet":
                    x, y = pressures, pressures / (quantities * (1.0 - pressures))
                elif method == "langmuir":
                    x, y = pressures, pressures / quantities
                else:
                    x = [thickness_nm(p, "carbon_black_stsa", None) for p in pressures]
                    y = quantities * (28.0134 / 0.808) / 22414.0
                slope, intercept = np.polyfit(x, y, 1)
                self.assertAlmostEqual(fits[0].slope, slope, places=10)
                self.assertAlmostEqual(fits[0].intercept, intercept, places=10)

    def test_manual_pressure_ranges_include_untagged_intervening_points(self):
        result = tagged_sample()
        for method, _label, function, automatic_range in METHODS:
            with self.subTest(method=method):
                lo, hi = automatic_range(result)
                expected = list(range(SELECTIONS[method][0], SELECTIONS[method][-1] + 1))
                self.assertEqual(indices(function(result, lo, hi, use_report_point_selection=False)), expected)
                self.assertEqual(indices(function(result, lo - 1e-4, hi + 1e-4)), expected)

    def test_thickness_defaults_keep_tags_but_manual_interval_keeps_all_points(self):
        result = tagged_sample()
        interval = [thickness_nm(p, "carbon_black_stsa", None)
                    for p in automatic_t_plot_pressure_range(result)]
        self.assertEqual(indices(t_plot_analysis_by_thickness(result, *interval)), [4, 6, 8])
        self.assertEqual(indices(t_plot_analysis_by_thickness(
            result, *interval, use_report_point_selection=False)), [4, 5, 6, 7, 8])

    def test_one_tag_stays_insufficient_instead_of_switching_to_auto_points(self):
        result = tagged_sample({method: (4,) for method, *_ in METHODS})
        for method, _label, function, automatic_range in METHODS:
            with self.subTest(method=method):
                pressure = result.isotherm[3].relative_pressure
                self.assertEqual(automatic_range(result), (pressure, pressure))
                fit = function(result)
                self.assertEqual(fit.point_count, 1)
                self.assertNotEqual(fit.status, "ok")

    def test_no_tags_preserve_existing_automatic_selection(self):
        result = tagged_sample({})
        self.assertNotIn("use_stored_fit_ranges", result.method_options)
        without_tag_metadata = replace(result, method_options={
            key: value for key, value in result.method_options.items()
            if key not in ("quantachrome_qps_native", "quantachrome_point_tags")
        })
        for method, _label, function, automatic_range in METHODS:
            with self.subTest(method=method):
                self.assertNotIn(f"quantachrome_{method}_point_indices", result.method_options)
                self.assertEqual(automatic_range(result), automatic_range(without_tag_metadata))
                self.assertEqual(function(result), function(without_tag_metadata))

    def test_default_excel_export_contains_only_tagged_fit_rows(self):
        result = tagged_sample()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xlsx"
            export_results_xlsx([result], path)
            workbook = load_workbook(BytesIO(path.read_bytes()), data_only=True)
            try:
                for method, label, *_ in METHODS:
                    rows = list(workbook[label].values)
                    header = next(i for i, row in enumerate(rows) if "point_index" in row)
                    column = rows[header].index("point_index")
                    self.assertEqual([int(row[column]) for row in rows[header + 1:]], list(SELECTIONS[method]))
            finally:
                workbook.close()


class TaggedFitUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_gui_default_manual_default_preserves_point_selection_and_cache(self):
        window = isolated_main_window(self, MainWindow)
        result = tagged_sample()
        for method, _label, _function, _automatic_range in METHODS:
            with self.subTest(method=method):
                provider = getattr(window, f"_{method}_analysis_for_result")
                fit_range = getattr(window, f"_{method}_fit_range_for_result")(result)
                default = provider(result)
                self.assertEqual(indices(default), list(SELECTIONS[method]))
                getattr(window, f"_set_custom_{method}_fit_range")(result, fit_range)
                manual = provider(result)
                expected = list(range(SELECTIONS[method][0], SELECTIONS[method][-1] + 1))
                self.assertEqual(indices(manual), expected)
                self.assertIsNot(manual, default)
                getattr(window, f"_clear_custom_{method}_fit_range")(result)
                self.assertIs(provider(result), default)


if __name__ == "__main__":
    unittest.main()
