from __future__ import annotations

import os
import unittest

import numpy as np

from tristar_bet.dft_models import (
    convert_dft_kernel_to_pore_volume_basis,
    dft_model_options,
    dft_model_spec,
    load_dft_model_kernel,
)


class DftModelArchiveTests(unittest.TestCase):
    def test_all_distinct_archive_models_are_exposed_and_loadable(self) -> None:
        options = dft_model_options()

        self.assertEqual(len(options), 81)
        self.assertEqual(len({key for key, _label in options}), 81)
        self.assertEqual(len({label for _key, label in options}), 81)
        self.assertEqual(options[0][0], "n2_dft_model")
        self.assertIn(
            ("n2_nldft_carbon_slit", "N2 @ 77 on Carbon Slit Pores"),
            options,
        )
        self.assertIn(("het_n2_carbon_slit", "Het N2 carbon slit"), options)
        self.assertIn(("het_co2_carbon_slit", "Het CO2 carbon slit"), options)
        keys = {key for key, _label in options}
        for model_id in ("101", "110", "111", "112", "240"):
            self.assertIn(f"micromeritics_mod{model_id}", keys)
        self.assertIn("n2_77_carbon_slit_qsdft_eq", keys)
        self.assertIn("co2_273_carbon_slit_gcmc", keys)
        self.assertTrue(all("MOD" not in label for _key, label in options))

        for key, _label in options:
            with self.subTest(model=key):
                kernel = load_dft_model_kernel(key)
                self.assertIsNotNone(kernel)
                assert kernel is not None
                self.assertEqual(
                    kernel.kernel.shape,
                    (kernel.pressures.size, kernel.pore_widths_nm.size),
                )
                self.assertGreaterEqual(kernel.pressures.size, 3)
                self.assertGreaterEqual(kernel.pore_widths_nm.size, 3)
                self.assertTrue(np.all(np.diff(kernel.pressures) > 0.0))
                self.assertTrue(np.all(np.diff(kernel.pore_widths_nm) > 0.0))
                self.assertTrue(np.all(np.isfinite(kernel.kernel)))
                self.assertTrue(np.all(kernel.kernel >= 0.0))

    def test_type_geometry_and_adsorptive_filters_are_combined(self) -> None:
        classical_slit_n2 = dft_model_options(
            analysis_type="typical",
            geometry="slit",
            adsorptive="n2",
        )
        all_types_slit_n2 = dft_model_options(
            analysis_type="all",
            geometry="slit",
            adsorptive="n2",
        )
        all_filters = dft_model_options(
            analysis_type="all",
            geometry="all",
            adsorptive="all",
        )

        self.assertEqual(len(classical_slit_n2), 3)
        self.assertEqual(len(all_types_slit_n2), 17)
        self.assertEqual(len(all_filters), 81)
        self.assertEqual(
            {dft_model_spec(key).analysis_type for key, _label in classical_slit_n2},
            {"typical"},
        )
        self.assertEqual(
            {dft_model_spec(key).geometry for key, _label in all_types_slit_n2},
            {"slit"},
        )
        self.assertEqual(
            {dft_model_spec(key).adsorptive for key, _label in all_types_slit_n2},
            {"n2"},
        )

    def test_special_grid_layouts_do_not_fall_back_to_the_analytic_model(self) -> None:
        non_monotonic = load_dft_model_kernel("micromeritics_mod005")
        above_one = load_dft_model_kernel("micromeritics_mod225")
        duplicate_pressure = load_dft_model_kernel("micromeritics_mod255")
        extended_pressure = load_dft_model_kernel("micromeritics_mod610")

        assert non_monotonic is not None
        assert above_one is not None
        assert duplicate_pressure is not None
        assert extended_pressure is not None
        self.assertEqual(non_monotonic.kernel.shape, (186, 92))
        self.assertAlmostEqual(float(above_one.pressures[-1]), 1.05)
        self.assertEqual(duplicate_pressure.kernel.shape, (167, 111))
        self.assertAlmostEqual(float(extended_pressure.pressures[-1]), 9.99)

    def test_gai_kernels_use_pressure_rows_and_convert_amount_units(self) -> None:
        n2_slit = load_dft_model_kernel("n2_77_carbon_slit_nldft_eq")
        co2_gcmc = load_dft_model_kernel("co2_273_carbon_slit_gcmc")

        assert n2_slit is not None
        assert co2_gcmc is not None
        self.assertEqual(n2_slit.kernel.shape, (327, 111))
        self.assertAlmostEqual(float(n2_slit.pressures[0]), 1e-9, places=14)
        self.assertAlmostEqual(float(n2_slit.pressures[-1]), 0.999, places=5)
        self.assertEqual(co2_gcmc.kernel.shape, (90, 38))
        self.assertAlmostEqual(float(co2_gcmc.pressures[-1]), 0.02961075, places=7)

        converted = convert_dft_kernel_to_pore_volume_basis(
            n2_slit,
            n2_slit.kernel,
            liquid_molar_volume_cm3_mmol=0.03468,
        )
        self.assertEqual(converted.shape, n2_slit.kernel.shape)
        self.assertTrue(np.all(np.isfinite(converted)))
        self.assertGreater(float(np.max(converted)), 0.9)
        self.assertLess(float(np.max(converted)), 1.5)

    def test_halsey_cylinder_kernel_uses_surface_stp_units(self) -> None:
        kernel = load_dft_model_kernel("micromeritics_mod005")
        assert kernel is not None
        self.assertEqual(kernel.spec.kernel_basis, "surface_stp")
        original = kernel.kernel.copy()
        # The kernel's own reference liquid/STP factor is 0.001546.
        # Filled cylinders have V/A = D/4, with nm to cm3/m2 conversion.
        factor = 0.001546
        converted = convert_dft_kernel_to_pore_volume_basis(
            kernel, kernel.kernel, liquid_molar_volume_cm3_mmol=factor * 22.414,
        )
        ordinary_pores = kernel.pore_widths_nm <= 200.0
        np.testing.assert_allclose(converted[-1, ordinary_pores], 1.0, atol=6e-7, rtol=0)
        np.testing.assert_array_equal(kernel.kernel, original)
        # This is a unit conversion, not a fit to a particular sample/export.
        surface_area = np.zeros(kernel.pore_widths_nm.size)
        surface_area[10] = 50.0
        surface_area[40] = 125.0
        volume = surface_area * kernel.pore_widths_nm / 4000.0
        np.testing.assert_allclose(
            converted @ volume, kernel.kernel @ surface_area * factor,
            rtol=2e-14, atol=1e-14,
        )

    def test_halsey_surface_conversion_uses_selected_liquid_density(self) -> None:
        kernel = load_dft_model_kernel("micromeritics_mod005")
        assert kernel is not None
        converted = convert_dft_kernel_to_pore_volume_basis(
            kernel, kernel.kernel[:3], liquid_molar_volume_cm3_mmol=0.03468,
        )
        np.testing.assert_allclose(
            converted,
            kernel.kernel[:3] * (0.03468 / 22.414) * 4000.0 / kernel.pore_widths_nm,
        )

    def test_ui_model_filters_are_cascaded_as_an_intersection(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
        from PyQt5 import QtWidgets

        from tristar_bet.ui.main_window import MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow()

        def select(combo, value: str) -> None:
            index = combo.findData(value)
            self.assertGreaterEqual(index, 0)
            combo.setCurrentIndex(index)
            app.processEvents()

        try:
            self.assertEqual(window.dft_model_combo.count(), 14)
            select(window.dft_type_combo, "all")
            self.assertEqual(window.dft_model_combo.count(), 17)
            select(window.dft_geometry_combo, "cylinder")
            self.assertEqual(window.dft_model_combo.count(), 15)
            select(window.dft_adsorptive_combo, "all")
            self.assertEqual(window.dft_model_combo.count(), 32)

            select(window.dft_geometry_combo, "mixed")
            self.assertEqual(window.dft_model_combo.count(), 9)

            select(window.dft_type_combo, "typical")
            select(window.dft_geometry_combo, "slit")
            select(window.dft_adsorptive_combo, "n2")
            self.assertEqual(window.dft_model_combo.count(), 3)
            self.assertTrue(
                all("MOD" not in window.dft_model_combo.itemText(index) for index in range(3))
            )

            select(window.dft_adsorptive_combo, "ar")
            self.assertFalse(window.dft_model_combo.isEnabled())
            self.assertEqual(window.dft_model_combo.currentData(), "__no_matching_model__")
        finally:
            window.close()

    def test_dft_y_axis_uses_only_visible_curves_inside_green_region(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("PYQTGRAPH_QT_LIB", "PyQt5")
        from PyQt5 import QtWidgets

        from tristar_bet.ui.main_window import MainWindow

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window = MainWindow()
        try:
            window.visible_results = [True, False]
            window._dft_distribution_rows_by_index = {
                0: [
                    {"pore_width_nm": 0.5, "differential_pore_volume_cm3_g": 1000.0},
                    {"pore_width_nm": 1.0, "differential_pore_volume_cm3_g": 2.0},
                    {"pore_width_nm": 5.0, "differential_pore_volume_cm3_g": 4.0},
                ],
                1: [
                    {"pore_width_nm": 2.0, "differential_pore_volume_cm3_g": 800.0},
                ],
            }

            window._fit_dft_y_axis_to_width_range((1.0, 10.0))
            app.processEvents()

            y_lo, y_hi = window.dft_plot.viewRange()[1]
            self.assertAlmostEqual(y_lo, 0.0, places=7)
            self.assertAlmostEqual(y_hi, 4.32, places=7)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
