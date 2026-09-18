import copy
import math
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tristar_bet.analysis import bjh_pore_volume_cm3_g, pore_volume_in_range
from tristar_bet.ui.main_window import MainWindow


def bin_row(low, high, volume, *, dft=False):
    row = {
        "pore_diameter_nm": math.sqrt(low * high) if low > 0 else high,
        "incremental_pore_volume_cm3_g": volume,
    }
    if dft:
        row.update(pore_width_low_nm=low, pore_width_high_nm=high)
    else:
        row.update(pore_diameter_range_low_nm=low, pore_diameter_range_high_nm=high)
    return row


class SelectedPoreVolumeTests(unittest.TestCase):
    def test_boundary_moves_continuously_without_crossing_recorded_point(self):
        rows = [bin_row(1.0, 10.0, 0.01)]
        for fraction in (0.1, 0.2, 0.3, 0.4):
            upper = 10.0 ** fraction
            self.assertLess(upper, rows[0]["pore_diameter_nm"])
            self.assertAlmostEqual(pore_volume_in_range(rows, (1.0, upper)), 0.01 * fraction)

    def test_zero_width_outside_and_reversed_selection(self):
        rows = [bin_row(1.0, 10.0, 0.01)]
        self.assertEqual(pore_volume_in_range(rows, (3.0, 3.0)), 0.0)
        self.assertEqual(pore_volume_in_range(rows, (10.0, 20.0)), 0.0)
        self.assertEqual(pore_volume_in_range(rows, (-5.0, 1.0)), 0.0)
        self.assertAlmostEqual(pore_volume_in_range(rows, (10.0, 1.0)), 0.01)

    def test_full_bins_keep_volume_and_adjacent_ranges_are_additive(self):
        rows = [bin_row(1.0, 2.0, 0.01), bin_row(2.0, 7.0, 0.03), bin_row(10.0, 40.0, 0.04)]
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.0, 100.0)), 0.08)
        self.assertEqual(pore_volume_in_range(rows, (7.0, 10.0)), 0.0)
        for split in (1.0, 1.2, 2.0, 3.0, 7.0, 12.0, 40.0):
            total = pore_volume_in_range(rows, (0.0, split)) + pore_volume_in_range(rows, (split, 100.0))
            self.assertAlmostEqual(total, 0.08)

    def test_dft_uses_bin_edges_not_bin_centers(self):
        rows = [bin_row(1.0, 4.0, 0.06, dft=True)]
        self.assertAlmostEqual(pore_volume_in_range(rows, (1.0, 2.0)), 0.03)
        self.assertAlmostEqual(pore_volume_in_range(rows, (2.0, 4.0)), 0.03)

    def test_hk_zero_origin_uses_linear_fraction(self):
        rows = [bin_row(0.0, 0.8, 0.004)]
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.2, 0.6)), 0.002)
        self.assertAlmostEqual(pore_volume_in_range(rows, (-1.0, 2.0)), 0.004)

    def test_hk_reversal_preserves_both_increments(self):
        rows = [bin_row(1.0, 4.0, 0.01), bin_row(4.0, 1.0, 0.03)]
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.0, 5.0)), 0.04)
        self.assertAlmostEqual(pore_volume_in_range(rows, (1.0, 2.0)), 0.02)
        self.assertAlmostEqual(pore_volume_in_range(list(reversed(rows)), (1.0, 2.0)), 0.02)

    def test_cumulative_only_import_uses_previous_coordinate_in_source_order(self):
        rows = [
            {"pore_diameter_nm": 10.0, "cumulative_pore_volume_cm3_g": 0.01, "incremental_pore_volume_cm3_g": 0.01},
            {"pore_diameter_nm": 1.0, "cumulative_pore_volume_cm3_g": 0.03, "incremental_pore_volume_cm3_g": 0.02},
        ]
        self.assertAlmostEqual(pore_volume_in_range(rows, (1.0, math.sqrt(10.0))), 0.01)
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.0, 20.0)), 0.03)

    def test_missing_and_degenerate_edges_do_not_invent_widths(self):
        rows = [bin_row(2.0, 2.0, 0.01), {"pore_width_nm": 3.0, "incremental_pore_volume_cm3_g": 0.02}]
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.0, 4.0)), 0.03)
        self.assertEqual(pore_volume_in_range(rows, (1.0, 1.5)), 0.0)
        self.assertEqual(pore_volume_in_range(rows, (2.0, 2.0)), 0.0)
        self.assertAlmostEqual(
            pore_volume_in_range(rows, (0.0, 2.0)) + pore_volume_in_range(rows, (2.0, 4.0)), 0.03,
        )

    def test_empty_invalid_and_signed_rows(self):
        self.assertIsNone(pore_volume_in_range([], (1.0, 10.0)))
        rows = [bin_row(1.0, 10.0, 0.03), bin_row(1.0, 10.0, -0.01)]
        rows += [{}, {"pore_width_nm": float("nan"), "incremental_pore_volume_cm3_g": 9}, bin_row(1, 2, float("nan"))]
        self.assertAlmostEqual(pore_volume_in_range(rows, (0.0, 20.0)), 0.02)
        self.assertIsNone(pore_volume_in_range(rows, (1.0, float("nan"))))

    def test_display_density_cannot_change_volume_and_rows_are_not_mutated(self):
        rows = [bin_row(1.0, 10.0, 0.01)]
        original = copy.deepcopy(rows)
        expected = pore_volume_in_range(rows, (1.0, 2.0))
        self.assertEqual(rows, original)
        rows[0]["differential_pore_volume_cm3_g"] = 12345.0
        self.assertEqual(pore_volume_in_range(rows, (1.0, 2.0)), expected)

    def test_ui_wrappers_and_public_bjh_api_use_same_rule(self):
        rows = [bin_row(1.0, 10.0, 0.01)]
        expected = 0.004
        selection = (1.0, 10.0 ** 0.4)
        self.assertAlmostEqual(MainWindow._bjh_pore_volume_from_rows(rows, selection), expected)
        self.assertAlmostEqual(MainWindow._hk_pore_volume_from_rows(rows, selection), expected)
        with patch("tristar_bet.analysis.bjh_pore_distribution", return_value=SimpleNamespace(rows=rows)):
            self.assertAlmostEqual(bjh_pore_volume_cm3_g(object(), *selection), expected)


if __name__ == "__main__":
    unittest.main()
