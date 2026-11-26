"""Generalized Halsey saved in QPS: units, default propagation and overrides."""
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtCore, QtWidgets

from tristar_bet.analysis import (
    THICKNESS_METHOD_DEFAULT_PARAMS, bet_analysis, bjh_pore_distribution,
    dh_pore_distribution, t_plot_analysis, thickness_nm, vendor_t_plot_thickness_params,
)
from tristar_bet.ui.main_window import MainWindow

from test_quantachrome_qps_reduction import QpsFileCase, build_qps, sample_groups


CASES = ((4.25, 2.5), (6.125, 3.7))


def halsey_groups(a, n, *, pore_tags=True):
    groups = sample_groups(DRedParams={
        "tMethodType": 3, "tmethPreExp": a, "tmethExp": n, "thermalOn": 0,
    })
    if not pore_tags:
        data = groups["Data"]
        for key, value in list(data.items()):
            if key.startswith("PtFlg"):
                data[key] = value[:7] + "_" + value[8:]
    return groups


def expected_thickness(p, a, n):
    # Vendor coefficients describe angstroms; the application reports nanometres.
    return 0.1 * a * (-math.log(p)) ** (-1.0 / n)


def assert_pore_thickness(case, distribution, formula):
    case.assertEqual(distribution.status, "ok")
    case.assertTrue(distribution.rows)
    for row in distribution.rows:
        expected = 0.5 * (formula(row["relative_pressure_high"]) + formula(row["relative_pressure_low"]))
        case.assertAlmostEqual(row["film_thickness_nm"], expected, delta=1e-12)


class QpsGeneralizedHalseyTests(QpsFileCase):
    def test_negative_intercept_below_bet_keeps_signed_volume_and_raw_area(self):
        # Synthetic observations only. This exercises the signed result seen in
        # ASiQwin when the fitted external area is below the BET total.
        result = self.load(halsey_groups(10.0, 3.7))
        fit = t_plot_analysis(result)
        total = bet_analysis(result).surface_area_m2_g
        self.assertEqual(fit.status, "ok")
        self.assertLess(fit.intercept, 0.0)
        self.assertGreater(fit.slope, 0.0)
        self.assertLess(fit.slope * 1000.0, total)
        self.assertAlmostEqual(fit.external_surface_area_m2_g, fit.slope * 1000.0, delta=1e-12)
        self.assertAlmostEqual(fit.micropore_volume_cm3_g, fit.intercept, delta=1e-12)
        self.assertAlmostEqual(fit.summary_micropore_volume_cm3_g, fit.intercept, delta=1e-12)

    def test_same_isotherm_smaller_a_caps_area_with_zero_detail_and_empty_summary_volume(self):
        # Halving A halves every thickness, doubles the fitted slope, and keeps
        # the intercept/BET unchanged. It moves this case above the total area.
        below = self.load(halsey_groups(10.0, 3.7))
        above = self.load(halsey_groups(5.0, 3.7))
        self.assertEqual(below.isotherm, above.isotherm)
        self.assertEqual(below.method_options["quantachrome_point_tags"], above.method_options["quantachrome_point_tags"])
        total = bet_analysis(above).surface_area_m2_g
        self.assertEqual(total, bet_analysis(below).surface_area_m2_g)
        original = t_plot_analysis(below)
        capped = t_plot_analysis(above)
        self.assertEqual(capped.status, "ok")
        self.assertAlmostEqual(capped.slope, 2.0 * original.slope, delta=1e-12)
        self.assertAlmostEqual(capped.intercept, original.intercept, delta=1e-12)
        self.assertLess(capped.intercept, 0.0)
        self.assertGreater(capped.slope * 1000.0, total)
        self.assertEqual(capped.external_surface_area_m2_g, total)
        self.assertEqual(capped.micropore_volume_cm3_g, 0.0)
        self.assertIsNone(capped.summary_micropore_volume_cm3_g)
        self.assertEqual([row["point_index"] for row in capped.rows], [row["point_index"] for row in original.rows])

    def test_saved_coefficients_drive_thickness_and_default_t_plot_fit(self):
        for a, n in CASES:
            with self.subTest(a=a, n=n):
                result = self.load(halsey_groups(a, n))
                params = vendor_t_plot_thickness_params(result)
                self.assertIsNotNone(params)
                for p in (0.02, 0.15, 0.80, 0.95):
                    self.assertAlmostEqual(thickness_nm(p, "halsey", params), expected_thickness(p, a, n), delta=1e-12)
                fit = t_plot_analysis(result)
                self.assertEqual(fit.status, "ok")
                self.assertEqual(fit.point_count, 5)
                xs, ys = [], []
                for row in fit.rows:
                    expected = expected_thickness(row["relative_pressure"], a, n)
                    self.assertAlmostEqual(row["thickness_nm"], expected, delta=1e-12)
                    xs.append(expected)
                    ys.append(row["quantity_adsorbed_cm3_g_stp"] * result.adsorptive_properties.density_conversion_factor)
                mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
                slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
                self.assertAlmostEqual(fit.slope, slope, delta=1e-12)
                self.assertAlmostEqual(fit.intercept, mean_y - slope * mean_x, delta=1e-12)

    def test_default_bjh_and_dh_use_saved_coefficients_without_explicit_parameters(self):
        for a, n in CASES:
            result = self.load(halsey_groups(a, n))
            for function in (bjh_pore_distribution, dh_pore_distribution):
                for phase in ("adsorption", "desorption"):
                    with self.subTest(a=a, n=n, method=function.__name__, phase=phase):
                        distribution = function(result, phase, smooth=False)
                        assert_pore_thickness(self, distribution, lambda p: expected_thickness(p, a, n))
                        self.assertTrue(all(row["bjh_correction"] == "quantachrome_qps_tagged_points" for row in distribution.rows))

    def test_no_pore_tags_fallback_still_uses_file_coefficients(self):
        for a, n in CASES:
            result = self.load(halsey_groups(a, n, pore_tags=False))
            for function in (bjh_pore_distribution, dh_pore_distribution):
                with self.subTest(a=a, n=n, method=function.__name__):
                    distribution = function(result, "adsorption", smooth=False)
                    assert_pore_thickness(self, distribution, lambda p: expected_thickness(p, a, n))

    def test_explicit_ordinary_halsey_or_manual_parameters_override_file(self):
        result = self.load(halsey_groups(*CASES[1]))
        for params in (
            dict(THICKNESS_METHOD_DEFAULT_PARAMS["halsey"]),
            {"prefactor": 5.75, "numerator": -2.0, "exponent": 0.41, "scale": 0.1},
        ):
            formula = lambda p: params["scale"] * params["prefactor"] * (params["numerator"] / math.log(p)) ** params["exponent"]
            fit = t_plot_analysis(result, thickness_method="halsey", thickness_params=params)
            self.assertEqual(fit.status, "ok")
            for row in fit.rows:
                self.assertAlmostEqual(row["thickness_nm"], formula(row["relative_pressure"]), delta=1e-12)
            for function in (bjh_pore_distribution, dh_pore_distribution):
                with self.subTest(params=params, method=function.__name__):
                    distribution = function(result, "adsorption", thickness_method="halsey", thickness_params=params, smooth=False)
                    assert_pore_thickness(self, distribution, formula)

    def test_invalid_or_missing_coefficients_do_not_fall_back_to_ordinary_halsey(self):
        baseline_bet = bet_analysis(self.load(halsey_groups(*CASES[0])))
        for field in ("tmethPreExp", "tmethExp"):
            for value in (None, 0.0, -2.0, float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    groups = halsey_groups(*CASES[0])
                    if value is None:
                        del groups["DRedParams"][field]
                    else:
                        groups["DRedParams"][field] = value
                    result = self.load(groups)
                    bet = bet_analysis(result)
                    self.assertEqual(bet.status, "ok")
                    self.assertAlmostEqual(bet.surface_area_m2_g, baseline_bet.surface_area_m2_g, delta=1e-12)
                    fit = t_plot_analysis(result)
                    self.assertIsNone(fit.external_surface_area_m2_g)
                    self.assertIsNone(fit.micropore_volume_cm3_g)
                    self.assertFalse(fit.rows)
                    for function in (bjh_pore_distribution, dh_pore_distribution):
                        distribution = function(result, "adsorption", smooth=False)
                        self.assertNotEqual(distribution.status, "ok")
                        self.assertFalse(distribution.rows)


class QpsHalseyUiDefaultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_three_analysis_panels_keep_saved_coefficients_and_reciprocal_precision(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = QtCore.QSettings(str(Path(temp) / "settings.ini"), QtCore.QSettings.IniFormat)
            with patch.object(MainWindow, "_auto_check_for_updates"), patch.object(MainWindow, "_initialize_telemetry"), patch(
                "tristar_bet.ui.main_window.QtCore.QSettings", return_value=settings
            ):
                window = MainWindow()
                try:
                    for a, n in CASES:
                        path = Path(temp) / "halsey.qps"
                        path.write_bytes(build_qps(halsey_groups(a, n)))
                        window.load_files([str(path)], replace=True)
                        result = window.active_result()
                        for context in ("t_plot", "bjh", "dh"):
                            with self.subTest(a=a, n=n, context=context):
                                values = getattr(window, f"_{context}_settings_for_result")(result)
                                self.assertEqual(values["thickness_method"], "halsey")
                                params = values["thickness_params"]
                                self.assertAlmostEqual(params["prefactor"], a, delta=1e-12)
                                self.assertEqual(params["numerator"], -1.0)
                                self.assertAlmostEqual(params["exponent"], 1.0 / n, delta=1e-14)
                                self.assertEqual(params["scale"], 0.1)
                                for key, spin in getattr(window, f"{context}_param_spins")["halsey"].items():
                                    self.assertAlmostEqual(spin.value(), params[key], delta=0.500001 * 10 ** -spin.decimals())
                                self.assertAlmostEqual(thickness_nm(0.15, "halsey", params), expected_thickness(0.15, a, n), delta=1e-12)
                                # Editing A must not round the displayed 1/n back into stored settings.
                                getattr(window, f"{context}_param_spins")["halsey"]["prefactor"].setValue(a + 0.125)
                                edited = getattr(window, f"_{context}_settings_for_result")(result)["thickness_params"]
                                self.assertAlmostEqual(edited["prefactor"], a + 0.125, delta=1e-12)
                                self.assertAlmostEqual(edited["exponent"], 1.0 / n, delta=1e-14)
                                self.assertEqual(edited["numerator"], -1.0)
                                self.assertAlmostEqual(
                                    thickness_nm(0.15, "halsey", edited), expected_thickness(0.15, a + 0.125, n), delta=1e-12
                                )
                finally:
                    window.close()
                    window.deleteLater()
                    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
