from __future__ import annotations

import unittest

import numpy as np

from tristar_bet.ui.plots import _spline_display_xy


class PlotSplineTests(unittest.TestCase):
    def test_linear_spline_is_dense_and_bounded(self):
        x = np.asarray([0.01, 0.05, 0.10, 0.20], dtype=float)
        y = np.asarray([1.0, 4.0, 2.0, 5.0], dtype=float)
        original_x = x.copy()
        original_y = y.copy()

        curve_x, curve_y = _spline_display_xy(x, y, nonnegative=True)

        self.assertGreater(curve_x.size, x.size)
        self.assertEqual(curve_x.size, curve_y.size)
        self.assertTrue(np.all(curve_y >= 0.0))
        self.assertGreaterEqual(float(curve_y.min()), float(y.min()))
        self.assertLessEqual(float(curve_y.max()), float(y.max()))
        np.testing.assert_array_equal(x, original_x)
        np.testing.assert_array_equal(y, original_y)

    def test_log_spline_samples_uniformly_in_display_domain(self):
        x = np.asarray([0.1, 1.0, 10.0, 100.0], dtype=float)
        y = np.asarray([0.0, 1.0, 0.5, 2.0], dtype=float)

        curve_x, _curve_y = _spline_display_xy(x, y, x_log=True, nonnegative=True)
        log_steps = np.diff(np.log10(curve_x))

        self.assertGreater(curve_x.size, x.size)
        self.assertAlmostEqual(float(log_steps.max()), float(log_steps.min()), places=12)

    def test_width_reversal_is_split_instead_of_bridged(self):
        x = np.asarray([0.7, 0.9, 1.2, 1.4, 1.35], dtype=float)
        y = np.asarray([0.1, 0.3, 0.2, 0.5, 0.5], dtype=float)

        curve_x, curve_y = _spline_display_xy(x, y, x_log=True, nonnegative=True)

        self.assertEqual(int(np.count_nonzero(np.isnan(curve_x))), 1)
        self.assertEqual(int(np.count_nonzero(np.isnan(curve_y))), 1)
        split = int(np.flatnonzero(np.isnan(curve_x))[0])
        self.assertAlmostEqual(float(curve_x[split - 1]), 1.4, places=12)
        self.assertAlmostEqual(float(curve_x[split + 1]), 1.4, places=12)
        self.assertAlmostEqual(float(curve_x[-1]), 1.35, places=12)


if __name__ == "__main__":
    unittest.main()
