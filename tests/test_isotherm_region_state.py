import os
import unittest

import numpy as np


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from tristar_bet.ui.main_window import MainWindow


class _StubRegion:
    def __init__(self, pressure_range):
        self._pressure_range = pressure_range

    def getRegion(self):
        return self._pressure_range


class IsothermRegionStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()

    def _select_tab_without_refresh(self, widget):
        previous = self.window.plot_tabs.blockSignals(True)
        try:
            self.window.plot_tabs.setCurrentWidget(widget)
        finally:
            self.window.plot_tabs.blockSignals(previous)

    def test_pressure_region_state_is_independent_for_every_analysis_tab(self):
        expected = {
            self.window.bet_tab: (0.05, 0.30),
            self.window.langmuir_tab: (0.10, 0.40),
            self.window.t_plot_tab: (0.20, 0.50),
            self.window.bjh_tab: (0.35, 0.99),
            self.window.dh_tab: (0.45, 0.98),
            self.window.hk_tab: (0.001, 0.10),
        }

        for tab, pressure_range in expected.items():
            self._select_tab_without_refresh(tab)
            self.window._last_isotherm_region_range = pressure_range
            self.window._isotherm_region_custom = True

        for tab, pressure_range in expected.items():
            self._select_tab_without_refresh(tab)
            self.assertTrue(self.window._isotherm_region_custom)
            self.assertEqual(self.window._last_isotherm_region_range, pressure_range)

    def test_region_drawn_for_previous_tab_cannot_overwrite_destination_tab(self):
        self._select_tab_without_refresh(self.window.bet_tab)
        self.window._last_isotherm_region_range = (0.05, 0.30)
        self.window._isotherm_region_custom = True
        self.window.region = _StubRegion((0.05, 0.30))
        self.window._displayed_isotherm_region_key = "bet"

        self._select_tab_without_refresh(self.window.bjh_tab)
        self.window._last_isotherm_region_range = (0.40, 0.99)
        self.window._isotherm_region_custom = True

        self.assertEqual(self.window._current_pressure_region(), (0.40, 0.99))
        self.assertEqual(self.window._isotherm_region_ranges["bet"], (0.05, 0.30))

        self.window.region = None
        self.window._displayed_isotherm_region_key = None

    def test_t_plot_first_open_uses_zero_to_point_six_isotherm_region(self):
        pressure = np.asarray([0.01, 0.20, 0.50, 0.60, 0.95], dtype=float)

        self._select_tab_without_refresh(self.window.t_plot_tab)

        self.assertEqual(self.window._default_isotherm_region(pressure), [0.01, 0.60])

        self._select_tab_without_refresh(self.window.bet_tab)
        self.assertEqual(self.window._default_isotherm_region(pressure), [0.05, 0.30])


if __name__ == "__main__":
    unittest.main()
