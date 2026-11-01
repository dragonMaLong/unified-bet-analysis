"""Micromeritics report options saved in SMP files (synthetic blocks only).

Real-file evidence: scripts/smp_report_audit.py compares every SMP with its
vendor XLS report (TriStar II 3020, ASAP 2460, ASAP 2020 Plus, 3Flex all match).
"""
import struct
import unittest
from dataclasses import replace

from tristar_bet.analysis import bet_analysis, langmuir_analysis, single_point_bet_analysis, surface_area_factor_m2_per_cm3
from tristar_bet.models import IsothermPoint
from tristar_bet.smp import (
    MICROACTIVE_FREE_SPACE_ENTERED,
    TriStarSmpParser,
    _micromeritics_t_plot_settings,
    _report_range_pair,
    _tristar_3020_point_tags,
    micromeritics_report_settings,
)
from test_detail_result_tables import sample


def range_tail(p1, p2, flag=1, prefix=b"\x00" * 40):
    return prefix + b"\x02" + struct.pack("<dd", *p1) + struct.pack("<H", flag) + struct.pack("<dd", *p2) + b"\x00\x00"


def points(pressures, p0=760.0, record_step=49):
    out = []
    for i, x in enumerate(pressures):
        q = 50.0 * x / ((1.0 - x) * (0.05 + x))
        out.append(IsothermPoint(i + 1, "adsorption", 100 + record_step * i, x * p0, x, q, p0, 60 * i, q, q / 22.414))
    return out


def tagged_303(isotherm, tags_by_index):
    block = bytearray(100 + 49 * len(isotherm) + 64)
    for point in isotherm:
        codes = tags_by_index[point.index]
        at = point.record_rel_offset + 27
        struct.pack_into("<H", block, at, len(codes))
        struct.pack_into(f"<{len(codes)}H", block, at + 2, *codes)
    return bytes(block)


def t_plot_block(model_index, t_lo, t_hi, layout="A", table=None):
    block = bytearray(120)
    if layout == "A":
        block[65:71] = b"\x69\x00\x01\x00" + struct.pack("<H", model_index)
    else:
        block[18] = 5
        block[19:21] = struct.pack("<H", model_index)
    block += b"\x02\x00" + struct.pack("<dddd", 0.0, 1000.0, t_lo, t_hi)
    block += b"\x00\x00\x00\x00\x00\x00\xf0\x3f\x01\x00\x01\x00\x04" + b"\x00" * 30
    if table:
        block += struct.pack("<H", len(table))
        for p, t in table:
            block += b"\x02\x00" + struct.pack("<dd", p, t)
    return bytes(block + b"\x00" * 8)


class RangePairTests(unittest.TestCase):
    def test_flag_one_uses_intersection_flag_zero_first_interval(self):
        self.assertEqual(_report_range_pair(range_tail((0.05, 0.3), (0.0, 0.3))), (0.05, 0.3))
        self.assertEqual(_report_range_pair(range_tail((0.0, 0.3), (0.0263, 0.2033))), (0.0263, 0.2033))
        self.assertEqual(_report_range_pair(range_tail((10.0, 500.0), (0.0, 1000.0), flag=0)), (10.0, 500.0))

    def test_unknown_layouts_are_not_read(self):
        self.assertIsNone(_report_range_pair(range_tail((0.05, 0.3), (0.0, 0.3), flag=7)))
        self.assertIsNone(_report_range_pair(range_tail((0.3, 0.3), (0.0, 0.3))))  # empty
        self.assertIsNone(_report_range_pair(b"\x00" * 30))
        bad = bytearray(range_tail((0.05, 0.3), (0.0, 0.3)))
        bad[-37] = 4
        self.assertIsNone(_report_range_pair(bytes(bad)))


class ThreeTwentyTagTests(unittest.TestCase):
    def test_tags_select_bet_and_langmuir_points_and_single_point_rule(self):
        iso = points([0.05, 0.09, 0.12, 0.15, 0.19, 0.23, 0.26, 0.30, 0.34, 0.5, 0.9])
        tags = {p.index: (0x67, 0x68, 0x6A, 0x69) if p.index <= 7 else (0x6A, 0x69) for p in iso}
        block = tagged_303(iso, tags)
        self.assertEqual(_tristar_3020_point_tags(block, iso[0].record_rel_offset), (0x67, 0x68, 0x6A, 0x69))
        options = micromeritics_report_settings({303: block}, iso, {"instrument_model": "TriStar II 3020"})
        self.assertEqual((options["stored_bet_pressure_min"], options["stored_bet_pressure_max"]), (0.05, 0.26))
        self.assertEqual(options["stored_langmuir_pressure_max"], 0.26)
        self.assertEqual(options["vendor_bet_point_indices"], [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(options["use_stored_fit_ranges"])
        result = replace(sample("A"), isotherm=iso, method_options={
            "instrument_model": "TriStar II 3020", "single_point_bet_method": "nearest_measured_point", **options})
        fit = bet_analysis(result)
        self.assertEqual((fit.point_count, fit.pressure_max), (7, 0.26))
        # closest to 0.3 among the BET points is 0.26, not the measured 0.30
        sp = single_point_bet_analysis(result, options["single_point_bet_pressure"])
        self.assertEqual(sp.pressure_min, 0.26)

    def test_untagged_or_damaged_records_give_no_selection(self):
        iso = points([0.05, 0.1, 0.2])
        self.assertEqual(micromeritics_report_settings({303: bytes(400)}, iso, {"instrument_model": "TriStar II 3020"}), {})


class RangeFamilyTests(unittest.TestCase):
    def test_asap_bet_and_langmuir_ranges(self):
        iso = points([0.01, 0.04, 0.08, 0.12, 0.18, 0.25, 0.32, 0.6, 0.95])
        blocks = {311: range_tail((0.05, 0.3), (0.0, 0.3)), 312: range_tail((0.0, 760.0), (0.0, 760.0), flag=0)}
        options = micromeritics_report_settings(blocks, iso, {"instrument_model": "ASAP 2460"})
        self.assertEqual((options["stored_bet_pressure_min"], options["stored_bet_pressure_max"]), (0.08, 0.25))
        self.assertEqual((options["stored_langmuir_pressure_min"], options["stored_langmuir_pressure_max"]), (0.01, 0.95))
        self.assertEqual(options["single_point_bet_candidates"], "vendor_bet_points")

    def test_other_instruments_untouched(self):
        iso = points([0.05, 0.1, 0.2])
        self.assertEqual(micromeritics_report_settings({311: range_tail((0.05, 0.3), (0.0, 0.3))}, iso,
                                                       {"instrument_model": "Autosorb iQ"}), {})


class TPlotSettingsTests(unittest.TestCase):
    def test_model_index_and_thickness_range_in_both_layouts(self):
        for layout in ("A", "B"):
            options = _micromeritics_t_plot_settings(t_plot_block(1, 3.5, 5.0, layout))
            self.assertEqual(options["vendor_t_plot_thickness_method"], "harkins_jura")
            self.assertAlmostEqual(options["stored_t_plot_thickness_min_nm"], 0.35)
            self.assertAlmostEqual(options["stored_t_plot_thickness_max_nm"], 0.5)

    def test_reference_curve_table_is_read_in_nm(self):
        table = [(1e-6, 0.08529), (0.05, 4.212), (0.5, 7.897), (0.98, 30.56), (0.99, 31.0)]
        options = _micromeritics_t_plot_settings(t_plot_block(4, 3.0, 5.4439, "A", table))
        self.assertEqual(options["vendor_t_plot_thickness_method"], "reference")
        self.assertAlmostEqual(options["vendor_t_plot_reference_points"][1][1], 0.4212)
        self.assertEqual(len(options["vendor_t_plot_reference_points"]), 5)

    def test_only_harkins_jura_is_applied(self):
        iso = points([0.01, 0.04, 0.08, 0.12, 0.18, 0.25, 0.32, 0.6, 0.95])
        base = {311: range_tail((0.05, 0.3), (0.0, 0.3))}
        hj = micromeritics_report_settings({**base, 314: t_plot_block(1, 3.5, 5.0)}, iso, {"instrument_model": "ASAP 2460"})
        ref = micromeritics_report_settings({**base, 314: t_plot_block(4, 3.0, 5.0)}, iso, {"instrument_model": "ASAP 2460"})
        self.assertTrue(hj["use_vendor_t_plot_settings"])
        self.assertNotIn("use_vendor_t_plot_settings", ref)

    def test_garbage_is_rejected(self):
        self.assertEqual(_micromeritics_t_plot_settings(bytes(200)), {})
        self.assertEqual(_micromeritics_t_plot_settings(t_plot_block(9, 3.5, 5.0)), {})
        self.assertEqual(_micromeritics_t_plot_settings(t_plot_block(1, 5.0, 3.5)), {})


class LangmuirAxisTests(unittest.TestCase):
    def test_absolute_pressure_fit_when_requested(self):
        base = sample("A")
        iso = [replace(p, absolute_pressure_mmHg=p.relative_pressure * (740.0 + 3.0 * p.index)) for p in base.isotherm]
        plain = replace(base, isotherm=iso)
        vendor = replace(plain, method_options={"langmuir_fit_axis": "absolute_pressure"})
        lo, hi = 0.05, 0.4
        selected = [p for p in iso if lo <= p.relative_pressure <= hi]
        xs = [p.absolute_pressure_mmHg for p in selected]
        ys = [x / p.quantity_adsorbed_cm3_g_stp for x, p in zip(xs, selected)]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
        expected = surface_area_factor_m2_per_cm3(vendor) / slope
        self.assertAlmostEqual(langmuir_analysis(vendor, lo, hi).surface_area_m2_g, expected, places=6)
        self.assertNotAlmostEqual(langmuir_analysis(plain, lo, hi).surface_area_m2_g, expected, places=3)


def mic_string(text):
    raw = text.encode("utf-16-le")
    return b"\xe0\x01\x00" + struct.pack("<I", len(raw)) + raw


class FreeSpaceModeTests(unittest.TestCase):
    def block(self, mode):
        head = mic_string("MicroActive for TriStar II Plus") + b"\x00" * 200
        tube = len(head) + 7  # text starts after marker and length
        block = bytearray(head + mic_string("Sample Tube") + b"\x00" * 260)
        struct.pack_into("<dd", block, tube - 48, 29.3765, 10.1097)   # entered
        struct.pack_into("<dd", block, tube - 24, 29.7641, 10.1097)   # measured
        block[tube + 196] = mode
        return bytes(block)

    def test_entered_mode_uses_entered_values(self):
        from tristar_bet.models import RunConditions
        run = RunConditions(*([None] * 11), adsorptive_short="N2", adsorptive_name="")
        parse = TriStarSmpParser()._parse_microactive_free_space
        measured = parse(self.block(0), run)
        entered = parse(self.block(MICROACTIVE_FREE_SPACE_ENTERED), run)
        self.assertAlmostEqual(measured.cold_free_space_cm3, 29.7641)
        self.assertAlmostEqual(entered.cold_free_space_cm3, 29.3765)
        self.assertAlmostEqual(entered.warm_free_space_cm3, 10.1097)


class ReferenceInterpolationTests(unittest.TestCase):
    TABLE = [(0.05, 0.4212), (0.101, 0.4854), (0.1486, 0.5296), (0.2005, 0.5707), (0.25085, 0.6076),
             (0.899, 1.363), (0.9539, 1.625), (0.9837, 3.056)]

    def test_nodes_end_intervals_and_hold_between_node_values(self):
        from tristar_bet.reference_thickness import micromeritics_reference_thickness as f
        for p, t in self.TABLE:
            self.assertAlmostEqual(f(p, self.TABLE), t, places=12)
        # end chords are repeated, so the last interval is a straight line
        x = 0.97
        expected = 1.625 + (x - 0.9539) * (3.056 - 1.625) / (0.9837 - 0.9539)
        self.assertAlmostEqual(f(x, self.TABLE), expected, places=12)
        # the steep last chord pulls the cubic below 1.363; the vendor holds it there
        self.assertEqual(f(0.92, self.TABLE), 1.363)
        self.assertIsNone(f(0.99, self.TABLE))       # beyond the table: no thickness
        self.assertIsNone(f(0.01, self.TABLE))

    def test_matches_vendor_values(self):
        # SIO2OH.THK rows and two thicknesses printed in TriStar II Plus reports (QC, YQ170min)
        from tristar_bet.reference_thickness import default_reference_points, micromeritics_reference_thickness as f
        table = default_reference_points()
        if not table:
            self.skipTest("bundled sio2oh.thk not available")
        self.assertAlmostEqual(f(0.061037084656671, table), 0.4374460185793151, places=5)
        self.assertAlmostEqual(f(0.95160026, table), 1.52445, places=4)

    def test_t_plot_window_is_read(self):
        block = bytearray(t_plot_block(4, 3.0, 5.4439, "A")[:-8])
        anchor = bytes(block).find(b"\x00\x00\x00\x00\x00\x00\xf0\x3f\x01\x00\x01\x00\x04")
        block += b"\x00" * (anchor + 70 - len(block))
        block[anchor + 46] = 2
        struct.pack_into("<dd", block, anchor + 47, 0.0556245, 0.7758)
        block[anchor + 63:anchor + 65] = b"\x01\x00"
        table = [(1e-6, 0.08529), (0.05, 4.212), (0.5, 7.897), (0.9, 13.63), (0.98, 30.56)]
        block += struct.pack("<H", len(table)) + b"".join(b"\x02\x00" + struct.pack("<dd", *row) for row in table)
        options = _micromeritics_t_plot_settings(bytes(block))
        self.assertAlmostEqual(options["stored_t_plot_pressure_window_min"], 0.0556245)
        self.assertEqual(options["vendor_t_plot_thickness_params"]["interpolation"], "micromeritics_akima")


class SignedTPlotTests(unittest.TestCase):
    def fit(self, model):
        from tristar_bet.analysis import t_plot_analysis_by_thickness
        base = sample("A")
        # quantities on a line with negative intercept in liquid volume
        iso = [replace(p, quantity_adsorbed_cm3_g_stp=max(1e-3, 100.0 * p.relative_pressure - 2.0)) for p in base.isotherm]
        result = replace(base, isotherm=iso, method_options={"instrument_model": model})
        return t_plot_analysis_by_thickness(result, 0.35, 0.6)

    def test_micromeritics_keeps_negative_micropore_volume(self):
        self.assertLess(self.fit("TriStar II 3020").micropore_volume_cm3_g, 0.0)
        self.assertEqual(self.fit("Autosorb iQ").micropore_volume_cm3_g, 0.0)

    def test_negative_micropore_area_is_left_out_like_the_report(self):
        from tristar_bet.ui.main_window import (_micropore_area_m2_g, _t_plot_value_note,
                                                MISSING_MICROPORE_AREA_NOTE, NEGATIVE_MICROPORE_VOLUME_NOTE)
        self.assertIsNone(_micropore_area_m2_g(100.0, 120.0, negative_as_missing=True))
        self.assertEqual(_micropore_area_m2_g(100.0, 120.0), 0.0)
        self.assertEqual(_micropore_area_m2_g(120.0, 100.0, negative_as_missing=True), 20.0)
        mic = replace(sample("A"), method_options={"instrument_model": "ASAP 2460"})
        other = replace(sample("A"), method_options={"instrument_model": "Autosorb iQ"})
        self.assertEqual(_t_plot_value_note("micro_volume", -0.004, mic), NEGATIVE_MICROPORE_VOLUME_NOTE)
        self.assertEqual(_t_plot_value_note("micro_area", None, mic), MISSING_MICROPORE_AREA_NOTE)
        self.assertIsNone(_t_plot_value_note("micro_volume", 0.004, mic))
        self.assertIsNone(_t_plot_value_note("micro_volume", -0.004, other))


if __name__ == "__main__":
    unittest.main()
