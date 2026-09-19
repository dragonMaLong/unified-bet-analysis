from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import unittest

import numpy as np
import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from tristar_bet.analysis import adsorption_points
from tristar_bet.ui.plots import (
    _isotherm_display_points,
    plot_isotherm_multi,
    plot_isotherm_selection,
)


def point(pressure, quantity, phase="adsorption"):
    return SimpleNamespace(relative_pressure=pressure,
                           quantity_adsorbed_cm3_g_stp=quantity, phase=phase)


def record(points):
    return SimpleNamespace(isotherm=points, file_name="synthetic.SMP", sample_name="synthetic")


class IsothermNegativeValueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.plot = pg.PlotWidget()

    def tearDown(self):
        self.plot.close()
        self.plot.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)

    def test_display_keeps_negative_and_zero_but_analysis_filter_is_unchanged(self):
        points = [point(0.3, 2), point(0.1, -3), point(0.2, 0)]
        result = record(points)
        self.assertEqual(_isotherm_display_points(result, "adsorption"),
                         [points[1], points[2], points[0]])
        self.assertEqual(adsorption_points(result), [points[0]])
        self.assertEqual(result.isotherm, points)
        self.assertEqual([p.quantity_adsorbed_cm3_g_stp for p in points], [2, -3, 0])

    def test_display_rejects_missing_nonfinite_and_out_of_domain_values(self):
        valid = point(0.1, -1)
        result = record([valid, point(None, 1), point(0.2, None), point(0.2, np.nan),
                         point(np.inf, 1), point(0.2, np.inf), point(0, -1),
                         point(1, -1), point(-0.1, 2), point(0.2, -1, "desorption")])
        self.assertEqual(_isotherm_display_points(result, "adsorption"), [valid])

    def _assert_curve(self, quantities, phase, x_log):
        pressures = np.linspace(0.1, 0.8, len(quantities))
        points = [point(float(x), float(y), phase) for x, y in zip(pressures, quantities)]
        result = record(points)
        plot_isotherm_multi(self.plot, [result], [True], ["#2563eb"],
                            active_index=0, x_log=x_log)
        lines = [item for item in self.plot.listDataItems()
                 if hasattr(item, "_interaction_y_values")]
        self.assertEqual(len(lines), 1)
        line = lines[0]
        curve_y = line._interaction_y_values
        self.assertTrue(np.all(np.isfinite(curve_y)))
        self.assertAlmostEqual(float(curve_y[0]), quantities[0])
        self.assertAlmostEqual(float(curve_y[-1]), quantities[-1])
        self.assertLessEqual(float(np.min(curve_y)), min(quantities) + 1e-12)
        self.assertGreaterEqual(float(np.max(curve_y)), max(quantities) - 1e-12)
        self.assertEqual(line.opts["connect"], "finite")
        markers = [item for item in self.plot.listDataItems() if item.opts.get("symbol") == "o"]
        self.assertEqual(len(markers), 1)
        np.testing.assert_array_equal(markers[0].yData, quantities)
        self.assertLess(self.plot.viewRange()[1][0], min(quantities))
        self.assertGreater(self.plot.viewRange()[1][1], max(quantities))
        np.testing.assert_array_equal([p.quantity_adsorbed_cm3_g_stp for p in points], quantities)

    def test_mixed_sign_isotherms_connect_on_linear_and_log_pressure_axes(self):
        for phase in ("adsorption", "desorption"):
            for x_log in (False, True):
                with self.subTest(phase=phase, x_log=x_log):
                    self._assert_curve([-3.0, -1.0, 0.0, 2.0], phase, x_log)

    def test_all_negative_isotherms_retain_both_branches(self):
        for phase in ("adsorption", "desorption"):
            with self.subTest(phase=phase):
                self._assert_curve([-3.0, -2.0, -1.0], phase, False)

    def test_short_and_zero_only_isotherms_are_not_discarded(self):
        for values in ([-2.0], [-2.0, 1.0], [0.0, 0.0, 0.0]):
            with self.subTest(values=values):
                self._assert_curve(values, "adsorption", False)

    def test_pressure_selection_highlights_negative_and_zero_points(self):
        points = [point(p, q, phase) for phase in ("adsorption", "desorption")
                  for p, q in ((0.1, -2), (0.2, 0), (0.3, 1))]
        items = plot_isotherm_selection(self.plot, [record(points)], [True],
                                        ["#2563eb"], (0.1, 0.2), active_index=0)
        self.assertEqual(len(items), 2)
        for item in items:
            np.testing.assert_array_equal(item.xData, [0.1, 0.2])
            np.testing.assert_array_equal(item.yData, [-2, 0])

    def test_bridge_keeps_negative_endpoints(self):
        result = record([point(0.1, -3), point(0.8, -1),
                         point(0.1, -4, "desorption"), point(0.7, -2, "desorption")])
        plot_isotherm_multi(self.plot, [result], [True], ["#2563eb"], active_index=0)
        bridges = [item for item in self.plot.listDataItems()
                   if not hasattr(item, "_interaction_y_values") and item.opts.get("symbol") is None]
        self.assertEqual(len(bridges), 1)
        np.testing.assert_array_equal(bridges[0].yData, [-1, -2])


if __name__ == "__main__":
    unittest.main()
