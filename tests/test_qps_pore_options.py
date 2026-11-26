"""QPS pore selections and optional smoothing never change the saved totals."""
from dataclasses import replace
import unittest

from tristar_bet.analysis import bjh_pore_distribution, dh_pore_distribution

from test_quantachrome_qps_reduction import QpsFileCase, sample_groups


class QpsPoreOptionsTests(QpsFileCase):
    def setUp(self):
        self.result = self.load(sample_groups(DRedParams={"thermalOn": 0}))

    @staticmethod
    def with_pore_tags(result, indices, *, ignore_below=False):
        options = dict(result.method_options)
        options["quantachrome_point_tags"] = {
            index: tag[:7] + ("P" if index in indices else "_") + tag[8:]
            for index, tag in options["quantachrome_point_tags"].items()
        }
        options["quantachrome_bjh_ignore_below_035"] = ignore_below
        return replace(result, method_options=options)

    def distribution(self, function, result, phase, **kwargs):
        return function(
            result, phase, thickness_method=result.method_options["vendor_bjh_thickness_method"], **kwargs
        )

    def test_two_tagged_points_make_only_their_one_interval(self):
        # The intervening untagged point at 0.80 must not be silently included.
        for phase, indices in (("adsorption", {10, 12}), ("desorption", {12, 14})):
            selected = self.with_pore_tags(self.result, indices, ignore_below=True)
            for function in (bjh_pore_distribution, dh_pore_distribution):
                with self.subTest(phase=phase, method=function.__name__):
                    result = self.distribution(function, selected, phase, smooth=False)
                    self.assertEqual(result.status, "ok")
                    self.assertEqual(len(result.rows), 1)
                    row = result.rows[0]
                    self.assertEqual(row["bjh_correction"], "quantachrome_qps_tagged_points")
                    self.assertAlmostEqual(row["relative_pressure_high"], 0.95)
                    self.assertAlmostEqual(row["relative_pressure_low"], 0.60)
                    self.assertGreater(row["incremental_pore_volume_cm3_g"], 0.0)

    def test_one_valid_tag_or_all_filtered_tags_report_insufficient_points(self):
        for indices in ({12}, {2, 3}, {2, 3, 12}):
            selected = self.with_pore_tags(self.result, indices, ignore_below=True)
            for function in (bjh_pore_distribution, dh_pore_distribution):
                with self.subTest(indices=indices, method=function.__name__):
                    result = self.distribution(function, selected, "adsorption", smooth=False)
                    self.assertEqual(result.status, "not_enough_points")
                    self.assertEqual(result.rows, [])

    def test_tags_only_on_other_branch_do_not_enable_whole_branch_fallback(self):
        selected = self.with_pore_tags(self.result, {9, 10, 11})
        for function in (bjh_pore_distribution, dh_pore_distribution):
            result = self.distribution(function, selected, "desorption", smooth=False)
            self.assertEqual(result.status, "not_enough_points")
            self.assertEqual(result.rows, [])

    def test_file_without_pore_tags_keeps_legacy_general_calculation(self):
        untagged = self.with_pore_tags(self.result, set())
        options = dict(untagged.method_options, quantachrome_qps_native=False)
        legacy = replace(untagged, method_options=options)
        for function in (bjh_pore_distribution, dh_pore_distribution):
            for phase in ("adsorption", "desorption"):
                with self.subTest(method=function.__name__, phase=phase):
                    actual = self.distribution(function, untagged, phase, smooth=False)
                    expected = self.distribution(function, legacy, phase, smooth=False)
                    self.assertEqual(actual.status, "ok")
                    self.assertEqual(actual.rows, expected.rows)

    def test_smoothing_changes_only_derivatives_and_can_be_disabled_again(self):
        differential_fields = (
            "differential_pore_volume_cm3_g", "differential_pore_volume_per_nm_cm3_g_nm",
            "differential_pore_area_m2_g", "differential_pore_area_per_nm_m2_g_nm",
        )
        for function in (bjh_pore_distribution, dh_pore_distribution):
            for phase in ("adsorption", "desorption"):
                with self.subTest(method=function.__name__, phase=phase):
                    raw = self.distribution(function, self.result, phase, smooth=False)
                    smooth = self.distribution(function, self.result, phase, smooth=True)
                    restored = self.distribution(function, self.result, phase, smooth=False)
                    self.assertEqual(raw.status, "ok")
                    self.assertEqual(raw.rows, restored.rows)
                    self.assertEqual(len(raw.rows), len(smooth.rows))
                    for field in differential_fields:
                        self.assertNotEqual([r[field] for r in raw.rows], [r[field] for r in smooth.rows])
                    for before, after in zip(raw.rows, smooth.rows):
                        for field in before.keys() - set(differential_fields):
                            self.assertEqual(before[field], after[field], field)

    def test_default_dh_remains_unsmoothed_and_explicit_option_wins(self):
        default = self.distribution(dh_pore_distribution, self.result, "adsorption")
        raw = self.distribution(dh_pore_distribution, self.result, "adsorption", smooth=False)
        self.assertEqual(default.rows, raw.rows)
        saved_on = replace(
            self.result, method_options=dict(self.result.method_options, vendor_dh_smooth_derivative=True)
        )
        self.assertNotEqual(self.distribution(dh_pore_distribution, saved_on, "adsorption").rows, raw.rows)
        self.assertEqual(
            self.distribution(dh_pore_distribution, saved_on, "adsorption", smooth=False).rows, raw.rows
        )


if __name__ == "__main__":
    unittest.main()
