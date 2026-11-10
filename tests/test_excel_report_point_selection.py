"""Report-selected rows must not become every row inside their pressure span."""

import unittest
from dataclasses import fields, replace

from tristar_bet.analysis import (
    automatic_bet_range,
    bet_analysis,
    langmuir_analysis,
    t_plot_analysis,
    t_plot_analysis_by_thickness,
    thickness_nm,
)
from tristar_bet.models import FreeSpaceInfo, IsothermPoint, RunConditions, SampleInfo, SmpHeader, TriStarResult


def _empty_record(cls, **values):
    return cls(**{field.name: values.get(field.name) for field in fields(cls)})


def _report_sample():
    points = []
    for ordinal, pressure in enumerate((0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50)):
        quantity = 300.0 * 40.0 * pressure / (1.0 + 40.0 * pressure) + 20.0 * pressure
        points.append(IsothermPoint(101 + ordinal * 3, "adsorption", 0, pressure * 760.0,
                                   pressure, quantity, 760.0, ordinal * 60.0, quantity, quantity / 22.414))
    ids = {point.relative_pressure: point.index for point in points}
    return TriStarResult(
        header=_empty_record(SmpHeader, file_name="report.xlsx", magic="OFFICIAL_EXCEL"),
        subsets=[], sample=SampleInfo("report", "", "", "", 1.0, None),
        run_conditions=_empty_record(RunConditions, adsorptive_short="N2"),
        target_pressure_table=[], free_space=_empty_record(FreeSpaceInfo), po_records=[],
        isotherm=points, adsorptive_properties=None, log_messages=[], sample_tube_strings=[],
        method_options={
            "instrument_model": "ASAP 2460",
            "micromeritics_report_block_excel_import": True,
            "use_official_excel_fit_ranges": True,
            "stored_bet_pressure_min": 0.05,
            "stored_bet_pressure_max": 0.20,
            "excel_report_bet_point_indices": [ids[p] for p in (0.05, 0.10, 0.20)],
            "stored_t_plot_pressure_min": 0.15,
            "stored_t_plot_pressure_max": 0.40,
            "excel_report_t_plot_point_indices": [ids[p] for p in (0.15, 0.30, 0.40)],
            "stored_langmuir_pressure_min": 0.05,
            "stored_langmuir_pressure_max": 0.20,
            "excel_report_langmuir_point_indices": [ids[p] for p in (0.05, 0.10, 0.20)],
        },
    )


def _without_selection(result, pressures=None):
    options = {key: value for key, value in result.method_options.items()
               if not key.startswith("excel_report_")}
    points = [point for point in result.isotherm
              if pressures is None or point.relative_pressure in pressures]
    return replace(result, isotherm=points, method_options=options)


def _pressures(fit):
    return [row["relative_pressure"] for row in fit.rows]


class ReportPointSelectionTests(unittest.TestCase):
    def setUp(self):
        self.result = _report_sample()

    def test_bet_default_and_explicit_default_range_use_only_report_rows(self):
        expected = bet_analysis(_without_selection(self.result, (0.05, 0.10, 0.20)), 0.05, 0.20)
        self.assertEqual(automatic_bet_range(self.result), (0.05, 0.20))
        for fit in (bet_analysis(self.result), bet_analysis(self.result, 0.05, 0.20)):
            self.assertEqual(_pressures(fit), [0.05, 0.10, 0.20])
            self.assertEqual(fit, expected)
        full = bet_analysis(_without_selection(self.result), 0.05, 0.20)
        self.assertNotEqual(full.surface_area_m2_g, expected.surface_area_m2_g)

    def test_t_plot_default_and_explicit_default_range_use_only_fitted_rows(self):
        expected = t_plot_analysis(_without_selection(self.result, (0.15, 0.30, 0.40)), 0.15, 0.40)
        for fit in (t_plot_analysis(self.result), t_plot_analysis(self.result, 0.15, 0.40)):
            self.assertEqual(_pressures(fit), [0.15, 0.30, 0.40])
            self.assertEqual(fit, expected)
        full = t_plot_analysis(_without_selection(self.result), 0.15, 0.40)
        self.assertNotEqual(full.external_surface_area_m2_g, expected.external_surface_area_m2_g)

    def test_t_plot_default_thickness_interval_keeps_exact_report_rows(self):
        interval = [thickness_nm(p, "harkins_jura", None) for p in (0.15, 0.40)]
        expected = t_plot_analysis_by_thickness(
            _without_selection(self.result, (0.15, 0.30, 0.40)), *interval,
            thickness_method="harkins_jura",
        )
        for window in ({}, {"p_min": 0.0, "p_max": 1.0}):
            fit = t_plot_analysis_by_thickness(self.result, *interval, thickness_method="harkins_jura", **window)
            self.assertEqual(_pressures(fit), [0.15, 0.30, 0.40])
            self.assertEqual(fit, expected)

    def test_changed_pressure_ranges_include_previously_unselected_rows(self):
        for function, interval, pressures in (
            (bet_analysis, (0.049, 0.201), [0.05, 0.08, 0.10, 0.15, 0.20]),
            (t_plot_analysis, (0.149, 0.401), [0.15, 0.20, 0.25, 0.30, 0.40]),
            (langmuir_analysis, (0.049, 0.201), [0.05, 0.08, 0.10, 0.15, 0.20]),
        ):
            with self.subTest(method=function.__name__):
                fit = function(self.result, *interval)
                self.assertEqual(_pressures(fit), pressures)
                self.assertEqual(fit, function(_without_selection(self.result), *interval))

    def test_changed_thickness_interval_includes_previously_unselected_rows(self):
        interval = [thickness_nm(p, "harkins_jura", None) for p in (0.149, 0.401)]
        fit = t_plot_analysis_by_thickness(self.result, *interval, thickness_method="harkins_jura")
        self.assertEqual(_pressures(fit), [0.15, 0.20, 0.25, 0.30, 0.40])
        self.assertEqual(fit, t_plot_analysis_by_thickness(
            _without_selection(self.result), *interval, thickness_method="harkins_jura"))

    def test_manual_selection_can_use_all_rows_even_at_the_default_endpoints(self):
        for function, interval in ((bet_analysis, (0.05, 0.20)),
                                   (t_plot_analysis, (0.15, 0.40)),
                                   (langmuir_analysis, (0.05, 0.20))):
            with self.subTest(method=function.__name__):
                fit = function(self.result, *interval, use_report_point_selection=False)
                self.assertEqual(fit.point_count, 5)
                self.assertEqual(fit, function(_without_selection(self.result), *interval))
        interval = [thickness_nm(p, "harkins_jura", None) for p in (0.15, 0.40)]
        fit = t_plot_analysis_by_thickness(self.result, *interval, thickness_method="harkins_jura",
                                          use_report_point_selection=False)
        self.assertEqual(_pressures(fit), [0.15, 0.20, 0.25, 0.30, 0.40])

    def test_langmuir_explicit_report_rows_are_respected(self):
        expected = langmuir_analysis(_without_selection(self.result, (0.05, 0.10, 0.20)), 0.05, 0.20)
        for fit in (langmuir_analysis(self.result), langmuir_analysis(self.result, 0.05, 0.20)):
            self.assertEqual(_pressures(fit), [0.05, 0.10, 0.20])
            self.assertEqual(fit, expected)

    def test_report_keys_do_not_change_unrelated_import_sources(self):
        self.result.method_options.pop("micromeritics_report_block_excel_import")
        for function, interval in ((bet_analysis, (0.05, 0.20)), (t_plot_analysis, (0.15, 0.40))):
            self.assertEqual(function(self.result, *interval), function(_without_selection(self.result), *interval))

    def test_empty_explicit_selection_does_not_fall_back_to_whole_interval(self):
        self.result.method_options["excel_report_bet_point_indices"] = []
        fit = bet_analysis(self.result)
        self.assertEqual(fit.status, "not_enough_points")
        self.assertEqual(fit.point_count, 0)
        self.assertEqual(fit.rows, [])


if __name__ == "__main__":
    unittest.main()
