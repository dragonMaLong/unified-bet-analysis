"""Micromeritics single-point BET: subset-306 target and nearest-measured-point rule.

Synthetic blocks only. Real-file evidence (5/5 TriStar II Plus SMP/XLS pairs) is
reproduced locally with scripts/smp_single_point_report_check.py.
"""
import struct
import unittest

from tristar_bet.analysis import single_point_bet_analysis, surface_area_factor_m2_per_cm3
from tristar_bet.smp import TriStarSmpParser
from test_detail_result_tables import sample

PLUS = {"instrument_model": "TriStar II Plus"}


def layout_a(value):
    block = bytearray(407)
    block[26:26 + 14] = "Summary".encode("utf-16-le")
    struct.pack_into("<d", block, 93, value)
    return bytes(block)


def layout_b(value):
    block = bytearray(341)
    struct.pack_into("<d", block, 23, value)
    return bytes(block)


class SubsetOptionTests(unittest.TestCase):
    parse = staticmethod(TriStarSmpParser._parse_single_point_options)

    def test_tristar_plus_layouts(self):
        for block in (layout_a(0.3), layout_b(0.3)):
            options = self.parse(block, PLUS)
            self.assertEqual(options["single_point_bet_pressure"], 0.3)
            self.assertEqual(options["single_point_bet_method"], "nearest_measured_point")

    def test_other_models_and_bad_values_read_no_target(self):
        for block, meta in ((layout_a(0.3), {"instrument_model": "TriStar II 3020"}),
                            (layout_b(0.0), {"instrument_model": "ASAP 2460"}),
                            (layout_b(0.0), PLUS), (layout_a(1.5), PLUS), (b"", PLUS),
                            (layout_a(0.3)[:-1], PLUS)):
            options = self.parse(block, meta)
            self.assertNotIn("single_point_bet_pressure", options)
            self.assertEqual(options["single_point_bet_method"], "nearest_measured_point")


class NearestPointRuleTests(unittest.TestCase):
    def setUp(self):
        base = sample("A", 1)  # adsorption points at P/P0 = 0.035 * k
        self.base = base
        self.micromeritics = base.__class__(**{**base.__dict__, "method_options": {"single_point_bet_method": "nearest_measured_point"}})

    def test_uses_nearest_measured_point_at_its_own_pressure(self):
        fit = single_point_bet_analysis(self.micromeritics, 0.3)
        point = min((p for p in self.base.isotherm), key=lambda p: abs(p.relative_pressure - 0.3))
        self.assertAlmostEqual(fit.pressure_min, point.relative_pressure)
        expected = point.quantity_adsorbed_cm3_g_stp * (1 - point.relative_pressure) * surface_area_factor_m2_per_cm3(self.base)
        self.assertAlmostEqual(fit.surface_area_m2_g, expected)
        self.assertEqual(fit.rows[0]["target_relative_pressure"], 0.3)

    def test_other_formats_still_interpolate(self):
        fit = single_point_bet_analysis(self.base, 0.3)
        self.assertEqual(fit.pressure_min, 0.3)
        self.assertNotAlmostEqual(fit.surface_area_m2_g, single_point_bet_analysis(self.micromeritics, 0.3).surface_area_m2_g)

    def test_nonpositive_nearest_point_is_not_reported(self):
        from dataclasses import replace
        isotherm = [replace(p, quantity_adsorbed_cm3_g_stp=-1.0) if abs(p.relative_pressure - 0.315) < 1e-9 else p
                    for p in self.micromeritics.isotherm]
        broken = replace(self.micromeritics, isotherm=isotherm)
        self.assertEqual(single_point_bet_analysis(broken, 0.3).status, "not_enough_points")



class PlausibleConditionTests(unittest.TestCase):
    def test_garbage_reads_are_dropped_and_real_values_kept(self):
        from tristar_bet.smp import _plausible
        self.assertIsNone(_plausible(3.47328271065196e-310, 0.5, 3600.0))   # TriStar II Plus garbage
        self.assertIsNone(_plausible(1.7612850261246065e-307, 0.0, 1000.0))
        self.assertIsNone(_plausible(-2.4e-185, 0.5, 3600.0))
        self.assertIsNone(_plausible(float("nan"), 0.0, 1.0))
        self.assertIsNone(_plausible(None, 0.0, 1.0))
        self.assertEqual(_plausible(10.0, 0.5, 3600.0), 10.0)               # TriStar II 3020 value
        self.assertEqual(_plausible(0.0, 0.0, 1000.0), 0.0)


def conditions_block(rows, fs_eq=0.5, leak=180.0, ambient=16.0, analysis=45.0, start=100):
    block = bytearray(900)
    for offset, value in ((0, fs_eq), (10, leak), (18, ambient), (26, analysis)):
        struct.pack_into("<d", block, start + offset, value)
    count_at = start + 40
    struct.pack_into("<H", block, count_at, len(rows))
    for i, (interval, bound) in enumerate(rows):
        struct.pack_into("<Hd", block, count_at + 2 + 10 * i, interval, bound)
    return bytes(block)


class MicroActiveConditionTests(unittest.TestCase):
    def test_single_row_table(self):
        from tristar_bet.smp import _microactive_conditions
        self.assertEqual(_microactive_conditions(conditions_block([(15, 1.0)])), (0.5, 16.0, 45.0, 15.0))

    def test_multi_row_table_reports_first_interval(self):
        from tristar_bet.smp import _microactive_conditions
        same = conditions_block([(10, 0.3), (10, 1.0)])
        self.assertEqual(_microactive_conditions(same)[3], 10.0)
        mixed = conditions_block([(5, 0.3), (20, 1.0)])
        self.assertEqual(_microactive_conditions(mixed), (0.5, 16.0, 45.0, 5.0))  # first row, as the vendor report prints

    def test_garbage_is_rejected(self):
        from tristar_bet.smp import _microactive_conditions
        self.assertIsNone(_microactive_conditions(bytes(900)))
        self.assertIsNone(_microactive_conditions(conditions_block([(15, 1.0)], ambient=7.29112e-304)))
        self.assertIsNone(_microactive_conditions(conditions_block([(15, 0.9)])))  # table must end at 1.0
        self.assertIsNone(_microactive_conditions(conditions_block([(15, 0.5), (15, 0.4), (15, 1.0)])))

    def test_asap_2460_small_block_uses_located_group(self):
        block = conditions_block([(20, 0.01), (10, 1.0)], fs_eq=1.0, start=157)[:991]
        run = TriStarSmpParser()._parse_run_conditions(block)
        self.assertEqual((run.equilibration_interval_s, run.free_space_equilibration_time_h,
                          run.ambient_free_space_entered_cm3, run.analysis_free_space_entered_cm3),
                         (20.0, 1.0, 16.0, 45.0))


if __name__ == "__main__":
    unittest.main()
