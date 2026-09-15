from __future__ import annotations

import os
import unittest

import numpy as np

from tristar_bet.dft_models import dft_model_options, dft_model_spec, load_dft_model_kernel


class DftModelArchiveTests(unittest.TestCase):
    def test_all_distinct_archive_models_are_exposed_and_loadable(self) -> None:
        options = dft_model_options()

        self.assertEqual(len(options), 48)
        self.assertEqual(len({key for key, _label in options}), 48)
        self.assertEqual(len({label for _key, label in options}), 48)
        self.assertEqual(options[0][0], "n2_dft_model")
        self.assertIn(
            ("n2_nldft_carbon_slit", "N2 @ 77 on Carbon Slit Pores"),
            options,
        )
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
        self.assertEqual(len(all_types_slit_n2), 11)
        self.assertEqual(len(all_filters), 48)
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
            self.assertEqual(window.dft_model_combo.count(), 8)
            select(window.dft_type_combo, "all")
            self.assertEqual(window.dft_model_combo.count(), 11)
            select(window.dft_geometry_combo, "cylinder")
            self.assertEqual(window.dft_model_combo.count(), 10)
            select(window.dft_adsorptive_combo, "all")
            self.assertEqual(window.dft_model_combo.count(), 22)

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


if __name__ == "__main__":
    unittest.main()
