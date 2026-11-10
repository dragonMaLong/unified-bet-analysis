"""MicroActive Excel export with every report side by side on one sheet (ASAP 2460 3.01).

The synthetic tests build the layout with openpyxl.  The vendor comparison reads a
local copy of the customer workbook (never committed) and is skipped without it.
"""
import math
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from tristar_bet.analysis import (
    bet_analysis,
    bjh_pore_distribution,
    dh_pore_distribution,
    langmuir_analysis,
    single_point_bet_analysis,
    t_plot_analysis,
)
from tristar_bet.excel_import import load_excel

VENDOR_COPY = Path(__file__).resolve().parents[1] / "outputs" / "asap2460_xls_check" / "xm-1495-1.XLS"


def _isotherm():
    adsorption = [(p, 300.0 * 40 * p / (1 + 40 * p) + 20 * p) for p in
                  (0.01, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.70, 0.90, 0.99)]
    desorption = [(p, q + 5.0) for p, q in reversed(adsorption[8:-1])]
    return adsorption + desorption


def _write_combined_sheet(path, *, thickness="Harkins and Jura", pore_pressure_range=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.cell(2, 1, "ASAP 2460 3.01")
    sheet.cell(2, 2, "2460")
    sheet.cell(6, 1, "Sample:")
    sheet.cell(6, 2, "S-1")
    titles = {1: "Summary Report", 7: "Isotherm Tabular Report", 12: "t-Plot Report",
              17: "BJH Adsorption Pore Distribution Report"}
    for column, title in titles.items():
        sheet.cell(12, column, title)
    for column in (6, 11, 16, 23):
        sheet.cell(12, column, "|")
    # Isotherm Tabular Report: header, saturation pressure on its own first row, then points.
    sheet.cell(14, 7, "Relative Pressure (p/p°)")
    sheet.cell(14, 8, "Quantity Adsorbed (cm³/g STP)")
    sheet.cell(14, 9, "Saturation Pressure (kPa)")
    sheet.cell(15, 9, 101.325)
    for row, (pressure, quantity) in enumerate(_isotherm(), start=16):
        sheet.cell(row, 7, pressure)
        sheet.cell(row, 8, quantity)
    # t-Plot data table with the fitted rows marked.
    sheet.cell(14, 12, "Relative Pressure (p/p°)")
    sheet.cell(14, 13, "Statistical Thickness (nm)")
    sheet.cell(14, 14, "Quantity Adsorbed (cm³/g STP)")
    sheet.cell(14, 15, "Fitted")
    for row, (pressure, quantity) in enumerate(_isotherm()[:12], start=15):
        sheet.cell(row, 12, pressure)
        sheet.cell(row, 14, quantity)
        sheet.cell(row, 15, "*" if 0.15 <= pressure <= 0.40 else "")
    # BJH report options as MicroActive prints them.
    sheet.cell(14, 18, "Faas Correction")
    sheet.cell(15, 18, thickness)
    sheet.cell(16, 18, "t = [ 13.99 / ( 0.034 - log(p/p°) ) ] ^ 0.5")
    sheet.cell(21, 17, "Width range:")
    sheet.cell(21, 18, "1.7000 to 300.0000 nm")
    sheet.cell(22, 17, "Adsorbate property factor:")
    sheet.cell(22, 18, "0.95300 nm")
    if pore_pressure_range is not None:
        sheet.cell(23, 17, "Relative pressure range:")
        sheet.cell(23, 18, pore_pressure_range)
    workbook.save(path)


class CombinedSheetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "combined.xlsx"

    def test_isotherm_is_read_from_the_tabular_report_block(self):
        _write_combined_sheet(self.path)
        result = load_excel(self.path)
        expected = _isotherm()
        self.assertEqual(result.point_count, len(expected))
        self.assertEqual([round(p.relative_pressure, 12) for p in result.isotherm], [p for p, _q in expected])
        self.assertEqual(sum(p.phase == "adsorption" for p in result.isotherm), 14)
        self.assertEqual(result.method_options["excel_import_source"], "micromeritics_report_block:Sheet1")
        self.assertEqual(result.method_options["instrument_model"], "ASAP 2460")
        self.assertTrue(math.isclose(result.isotherm[0].saturation_pressure_mmHg, 760.0, rel_tol=1e-6))

    def test_report_selections_become_the_fit_ranges(self):
        _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
        options = load_excel(self.path).method_options
        self.assertTrue(options["use_official_excel_fit_ranges"])
        self.assertEqual((options["stored_t_plot_pressure_min"], options["stored_t_plot_pressure_max"]), (0.15, 0.40))
        self.assertEqual(options["excel_report_t_plot_point_indices"], [6, 7, 8, 9, 10])
        self.assertNotIn("stored_langmuir_pressure_min", options)
        self.assertEqual(options["single_point_bet_method"], "nearest_measured_point")
        settings = options["vendor_bjh_report_settings"]["adsorption"]
        self.assertEqual(settings["thickness_method"], "harkins_jura")
        self.assertEqual(settings["thickness_params"], {"numerator": 13.99, "offset": 0.034, "exponent": 0.5})
        self.assertEqual(settings["correction"], "faas")
        self.assertEqual((settings["diameter_min_angstrom"], settings["diameter_max_angstrom"]), (17.0, 3000.0))
        self.assertAlmostEqual(settings["kelvin_factor_angstrom"], 9.53)
        self.assertNotIn("desorption", options["vendor_bjh_report_settings"])
        self.assertNotIn("vendor_dh_report_settings", options)

    def test_unrecognised_thickness_leaves_bjh_to_the_generic_calculation(self):
        _write_combined_sheet(self.path, thickness="Halsey")
        options = load_excel(self.path).method_options
        self.assertNotIn("vendor_bjh_report_settings", options)

    def edit(self, cells):
        with self.path.open("rb") as stream:
            workbook = load_workbook(stream)
        for row, column, value in cells:
            workbook.active.cell(row, column).value = value
        workbook.save(self.path)
        workbook.close()

    def test_noncontiguous_bet_and_t_plot_rows_keep_the_report_points(self):
        _write_combined_sheet(self.path)
        cells = [(12, 24, "BET Surface Area Plot"), (12, 28, "|"),
                 (14, 24, "Relative Pressure (p/p0)"), (14, 25, "1/[Q(p0/p - 1)]")]
        for row, (pressure, quantity) in enumerate(
            (pair for pair in _isotherm()[:14] if pair[0] in (0.05, 0.10, 0.20)), start=15
        ):
            cells.extend([(row, 24, pressure), (row, 25, 1 / (quantity * (1 / pressure - 1)))])
        for row, (pressure, _quantity) in enumerate(_isotherm()[:12], start=15):
            cells.append((row, 15, "*" if pressure in (0.15, 0.30, 0.40) else None))
        self.edit(cells)
        result = load_excel(self.path)
        self.assertEqual(result.method_options["excel_report_bet_point_indices"], [3, 5, 7])
        self.assertEqual(result.method_options["excel_report_t_plot_point_indices"], [6, 9, 10])
        bet, t_plot = bet_analysis(result), t_plot_analysis(result)
        self.assertEqual([row["relative_pressure"] for row in bet.rows], [0.05, 0.10, 0.20])
        self.assertEqual([row["relative_pressure"] for row in t_plot.rows], [0.15, 0.30, 0.40])
        # Independent review probe: including the unselected interior rows gave
        # 978.514076 and 274.098575 respectively, instead of these selected fits.
        self.assertAlmostEqual(bet.surface_area_m2_g, 972.6706551757197, places=7)
        self.assertAlmostEqual(t_plot.external_surface_area_m2_g, 288.1942808763811, places=7)

    def test_all_point_langmuir_requires_this_files_matching_summary(self):
        _write_combined_sheet(self.path)
        for summary, accepted in ((1393.731146145383, True), (1340.5142410688081, False), (None, False)):
            with self.subTest(summary=summary):
                self.edit([(14, 1, "Langmuir Surface Area:"), (14, 2, summary)])
                options = load_excel(self.path).method_options
                self.assertEqual("stored_langmuir_pressure_min" in options, accepted)
                if accepted:
                    self.assertEqual((options["stored_langmuir_pressure_min"], options["stored_langmuir_pressure_max"]), (0.01, 0.99))
                    self.assertIn("verified against this report", options["excel_langmuir_range_source"])

    def test_explicit_langmuir_selection_wins_over_an_all_point_summary(self):
        _write_combined_sheet(self.path)
        cells = [(14, 1, "Langmuir Surface Area:"), (14, 2, 1393.731146145383),
                 (12, 24, "Langmuir Tabular Report"), (12, 28, "|"),
                 (14, 24, "Relative Pressure (p/p0)"), (14, 25, "Quantity Adsorbed (cm³/g STP)")]
        for row, (pressure, quantity) in enumerate(
            (pair for pair in _isotherm()[:14] if pair[0] in (0.05, 0.10, 0.20)), start=15
        ):
            cells.extend([(row, 24, pressure), (row, 25, quantity)])
        self.edit(cells)
        result = load_excel(self.path)
        self.assertEqual(result.method_options["excel_report_langmuir_point_indices"], [3, 5, 7])
        self.assertEqual([row["relative_pressure"] for row in langmuir_analysis(result).rows], [0.05, 0.10, 0.20])
        self.assertIn("explicit", result.method_options["excel_langmuir_range_source"])

    def test_unknown_instrument_does_not_gain_micromeritics_defaults(self):
        _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
        self.edit([(2, 1, "Unidentified instrument"), (2, 2, None)])
        options = load_excel(self.path).method_options
        self.assertNotIn("single_point_bet_method", options)
        self.assertNotIn("vendor_bjh_report_settings", options)

    def test_missing_pressure_range_and_totals_do_not_create_vendor_settings(self):
        _write_combined_sheet(self.path)
        options = load_excel(self.path).method_options
        self.assertNotIn("vendor_bjh_report_settings", options)
        self.assertNotIn("vendor_dh_report_settings", options)

    def write_totals(self, method, volume, area):
        self.edit([
            (30, 1, f"{method} Adsorption cumulative volume of pores"),
            (31, 1, "between 1.7000 nm and 300.0000 nm width:"), (31, 2, volume),
            (33, 1, f"{method} Adsorption cumulative surface area of pores"),
            (34, 1, "between 1.7000 nm and 300.0000 nm width:"), (34, 2, area),
        ])

    def test_implicit_pressure_window_requires_both_current_file_totals(self):
        _write_combined_sheet(self.path)
        for volume, area, accepted in (
            (0.15696378265307243, 226.66166491862404, True),
            (0.15696378265307243, 230.0, False),
            (0.2, 226.66166491862404, False),
            (0.15696378265307243, None, False),
        ):
            with self.subTest(volume=volume, area=area):
                self.write_totals("BJH", volume, area)
                options = load_excel(self.path).method_options
                self.assertEqual("vendor_bjh_report_settings" in options, accepted)
                if accepted:
                    settings = options["vendor_bjh_report_settings"]["adsorption"]
                    self.assertEqual(settings["pressure_range"], [0.1, 0.995])
                    self.assertIn("verified", settings["pressure_range_source"])

    def test_dh_candidate_requires_its_own_totals_and_never_overrides_an_explicit_block(self):
        _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
        self.write_totals("D-H", 0.1462705079136064, 207.98382008148332)
        result = load_excel(self.path)
        settings = result.method_options["vendor_dh_report_settings"]["adsorption"]
        self.assertIn("verified against this report's DH", settings["settings_source"])
        fit = dh_pore_distribution(result, "adsorption", "harkins_jura", None, "faas", 0.0, False)
        self.assertAlmostEqual(fit.rows[-1]["cumulative_pore_volume_cm3_g"], 0.1462705079136064, places=12)
        self.write_totals("D-H", 0.2, 207.98382008148332)
        self.assertNotIn("vendor_dh_report_settings", load_excel(self.path).method_options)
        self.write_totals("D-H", 0.1462705079136064, 207.98382008148332)
        self.edit([(12, 24, "Dollimore-Heal Adsorption Pore Distribution Report"), (12, 30, "|"),
                   (14, 25, "Standard Correction"), (15, 25, "Halsey")])
        self.assertNotIn("vendor_dh_report_settings", load_excel(self.path).method_options)

    def test_desorption_and_dh_read_their_own_printed_options(self):
        _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
        cells = []
        for first, title, width, factor in (
            (24, "BJH Desorption Pore Distribution Report", "4 to 100 nm", "0.800 nm"),
            (31, "Dollimore-Heal Adsorption Pore Distribution Report", "5 to 90 nm", "0.900 nm"),
        ):
            cells.extend([
                (12, first, title), (12, first + 6, "|"),
                (14, first + 1, "Standard Correction"), (15, first + 1, "Harkins and Jura"),
                (16, first + 1, "t = [ 13.99 / ( 0.034 - log(p/p0) ) ] ^ 0.5"),
                (21, first, "Width range:"), (21, first + 1, width),
                (22, first, "Adsorbate property factor:"), (22, first + 1, factor),
                (23, first, "Relative pressure range:"), (23, first + 1, "0.2 to 0.99"),
            ])
        self.edit(cells)
        result = load_excel(self.path)
        options = result.method_options
        adsorption = options["vendor_bjh_report_settings"]["adsorption"]
        desorption = options["vendor_bjh_report_settings"]["desorption"]
        dh = options["vendor_dh_report_settings"]["adsorption"]
        self.assertEqual(adsorption["correction"], "faas")
        self.assertEqual(desorption["correction"], "standard")
        self.assertEqual((desorption["diameter_min_angstrom"], desorption["diameter_max_angstrom"]), (40.0, 1000.0))
        self.assertEqual(desorption["kelvin_factor_angstrom"], 8.0)
        self.assertEqual(desorption["pressure_range"], [0.2, 0.99])
        self.assertEqual((dh["diameter_min_angstrom"], dh["diameter_max_angstrom"]), (50.0, 900.0))
        self.assertEqual(dh["kelvin_factor_angstrom"], 9.0)
        self.assertNotIn("desorption", options["vendor_dh_report_settings"])
        # The UI has a shared correction selector; explicitly requesting this
        # branch's Standard correction must use its own vendor settings.
        fit = bjh_pore_distribution(result, "desorption", "harkins_jura", None, "standard", 0.0, False)
        self.assertEqual(fit.status, "ok")
        self.assertTrue(all(row.get("vendor_report_algorithm") == 1.0 for row in fit.rows))

    def test_angstrom_units_and_p_p0_equation_preserve_the_physical_distribution(self):
        _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
        baseline = load_excel(self.path)
        self.edit([(16, 18, "t = [ 13.99 / ( 0.034 - log(p/p0) ) ] ^ 0.5"),
                   (21, 18, "17.0000 to 3000.0000 Å"), (22, 18, "9.5300 Å")])
        converted = load_excel(self.path)
        settings = converted.method_options["vendor_bjh_report_settings"]["adsorption"]
        self.assertEqual(settings["thickness_params"]["exponent"], 0.5)
        self.assertEqual(settings["kelvin_factor_angstrom"], 9.53)
        before = bjh_pore_distribution(baseline, "adsorption", "harkins_jura", None, "faas", 0.0, False)
        after = bjh_pore_distribution(converted, "adsorption", "harkins_jura", None, "faas", 0.0, False)
        self.assertEqual(before.rows, after.rows)

    def test_invalid_equation_units_and_ranges_are_not_accepted_as_vendor_options(self):
        for row, value in ((16, "t = [ 13.99 / ( 0.034 - log(p/p0) ) ] ^ 0"),
                           (21, "1.7 to 300 um"), (21, "300 to 1.7 nm"),
                           (22, "-0.953 nm"), (23, "0.99 to 0.1")):
            with self.subTest(row=row, value=value):
                _write_combined_sheet(self.path, pore_pressure_range="0.1 to 0.995")
                self.edit([(row, 18, value)])
                self.assertNotIn("vendor_bjh_report_settings", load_excel(self.path).method_options)


@unittest.skipUnless(VENDOR_COPY.is_file(), "local copy of the ASAP 2460 3.01 customer workbook not present")
class Asap2460CombinedExportVendorComparison(unittest.TestCase):
    """Isotherm, surface-area fits and BJH/DH cumulative totals in xm-1495-1."""

    @classmethod
    def setUpClass(cls):
        cls.result = load_excel(VENDOR_COPY)

    def close(self, ours, vendor, tolerance):
        self.assertLessEqual(abs(ours - vendor) / abs(vendor), tolerance, (ours, vendor))

    def test_points(self):
        self.assertEqual(self.result.point_count, 73)
        self.assertEqual(sum(p.phase == "adsorption" for p in self.result.isotherm), 52)

    def test_surface_areas(self):
        bet = bet_analysis(self.result)
        self.close(bet.surface_area_m2_g, 1208.8192, 1e-6)
        self.close(bet.c_constant, 117.818566, 1e-6)
        self.close(langmuir_analysis(self.result).surface_area_m2_g, 1976.1785, 1e-6)
        self.close(single_point_bet_analysis(self.result, 0.3).surface_area_m2_g, 971.8123, 1e-6)
        t_plot = t_plot_analysis(self.result)
        self.close(t_plot.external_surface_area_m2_g, 349.0899, 1e-6)
        self.close(t_plot.micropore_volume_cm3_g, 0.323928, 5e-6)
        self.close(bet.surface_area_m2_g - t_plot.external_surface_area_m2_g, 859.7294, 1e-6)

    def test_bjh_and_dh_cumulative_values(self):
        params = self.result.method_options["vendor_bjh_report_settings"]["adsorption"]["thickness_params"]
        cases = {
            ("BJH", "adsorption"): (0.603505, 215.3301), ("BJH", "desorption"): (0.632628, 304.8510),
            ("DH", "adsorption"): (0.597381, 209.8395), ("DH", "desorption"): (0.622442, 290.4383),
        }
        for (method, phase), (volume, area) in cases.items():
            with self.subTest(method=method, phase=phase):
                function = bjh_pore_distribution if method == "BJH" else dh_pore_distribution
                rows = function(self.result, phase, "harkins_jura", params, "faas", 0.0, False).rows
                self.close(rows[-1]["cumulative_pore_volume_cm3_g"], volume, 2e-6)
                self.close(rows[-1]["cumulative_pore_area_m2_g"], area, 2e-6)


if __name__ == "__main__":
    unittest.main()
