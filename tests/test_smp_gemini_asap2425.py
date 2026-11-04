"""Gemini VII 2390 and ASAP 2425 SMP files.

Unit tests use synthetic data. The vendor comparison reads the example files
installed with the Gemini VII and ASAP 2425 software and checks them against
MicroActive 7.00 output (tests/fixtures/microactive_gemini_asap2425_reference.json);
it is skipped when the files are absent.
"""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path

from tristar_bet.smp import TriStarSmpParser, load_smp

REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "microactive_gemini_asap2425_reference.json").read_text(encoding="utf-8")
)
ROOTS = {name: Path(path) for name, path in REFERENCE["roots"].items()}
GEMINI_PREFIX = b"\xe0" + struct.pack("<d", float("nan")) + b"\x02\x00"


def _gemini_record(absolute: float, relative: float, dosed: float, tags: tuple[int, ...]) -> bytes:
    # P, P/P0, Q_dosed, u16 elapsed, 1 byte, u16 tag count, tags, 1 byte.
    body = struct.pack("<dddH", absolute, relative, dosed, 60) + b"\x00"
    body += struct.pack("<H", len(tags)) + b"".join(struct.pack("<H", tag) for tag in tags) + b"\x00"
    return GEMINI_PREFIX + body


class SyntheticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TriStarSmpParser()

    def test_gemini_records_are_chained_by_prefix_with_variable_length(self) -> None:
        block = bytearray(b"\x00" * 136)
        points = [(40.0, 0.05, 1.0, (103,)), (120.0, 0.15, 2.0, (103, 105, 106)), (200.0, 0.25, 3.0, ()),
                  (700.0, 0.95, 9.0, (105,))]
        offsets = []
        for absolute, relative, dosed, tags in points:
            offsets.append(len(block) + len(GEMINI_PREFIX))
            block += _gemini_record(absolute, relative, dosed, tags)
        block += b"\x00" * 64
        rows = self.parser._gemini_rows(bytes(block), len(block) - 18)
        self.assertEqual([row[0] for row in rows], offsets)
        self.assertEqual([row[1] for row in rows], [p[0] for p in points])
        self.assertEqual([self.parser._gemini_point_tags(bytes(block), at) for at in offsets], [p[3] for p in points])

    def test_branch_split_ends_before_first_point_above_saturation(self) -> None:
        split = TriStarSmpParser._phase_points_dropping_saturation
        rows = [(0, 1.0, 0.5, 0.0), (0, 1.0, 0.98, 0.0), (0, 1.0, 0.997, 0.0), (0, 1.0, 0.995, 0.0),
                (0, 1.0, 1.0015, 0.0), (0, 1.0, 0.99, 0.0)]
        # Gemini sialfull: the turning point is the highest point *before* the
        # P/P0 > 1 record, which itself is left out.
        self.assertEqual([phase for _row, phase in split(rows)],
                         ["adsorption", "adsorption", "adsorption", "desorption", "desorption"])
        rows = [(0, 1.0, 0.5, 0.0), (0, 1.0, 0.98909, 0.0), (0, 1.0, 1.0008, 0.0), (0, 1.0, 0.99007, 0.0)]
        # TriStar Titania: 0.99007 after the saturation frame is desorption.
        self.assertEqual([phase for _row, phase in split(rows)], ["adsorption", "adsorption", "desorption"])


def _example(sample: dict) -> Path:
    return ROOTS[sample["root"]] / sample["path"]


@unittest.skipUnless(all(root.is_dir() for root in ROOTS.values()), "Gemini VII / ASAP 2425 example data not installed")
class VendorComparison(unittest.TestCase):
    def test_points_and_reports_match_microactive(self) -> None:
        from tristar_bet.analysis import bet_analysis, t_plot_analysis

        for name, ref in REFERENCE["samples"].items():
            with self.subTest(name=name):
                result = load_smp(_example(ref))
                expected_model = "Gemini VII 2390" if ref["root"] == "gemini" else "ASAP 2425"
                self.assertEqual(result.method_options["instrument_model"], expected_model)
                self.assertEqual(result.point_count, len(ref["absolute_pressure_mmHg"]))
                self.assertEqual(sum(p.phase == "adsorption" for p in result.isotherm), ref["number_adsorption"])
                self.assertAlmostEqual(result.sample.sample_mass_g, ref["sample_mass_g"], places=9)
                tolerance = ref["quantity_rel_tolerance"]
                for point, pressure, dosed, adsorbed in zip(
                    result.isotherm, ref["absolute_pressure_mmHg"], ref["quantity_dosed_cm3_stp"], ref["quantity_adsorbed_cm3_g_stp"]
                ):
                    self.assertAlmostEqual(point.absolute_pressure_mmHg, pressure, delta=1e-12 * max(1.0, pressure))
                    self.assertEqual(point.raw_internal_cm3_stp, dosed)
                    self.assertAlmostEqual(point.quantity_adsorbed_cm3_g_stp, adsorbed, delta=tolerance * max(1e-3, abs(adsorbed)))
                if ref["bet_surface_area_m2_g"]:
                    vendor = ref["bet_surface_area_m2_g"]
                    self.assertAlmostEqual(bet_analysis(result).surface_area_m2_g, vendor, delta=1e-6 * vendor)
                if ref["t_plot_external_area_m2_g"]:
                    t_plot = t_plot_analysis(result)
                    vendor = ref["t_plot_external_area_m2_g"]
                    self.assertAlmostEqual(t_plot.external_surface_area_m2_g, vendor, delta=1e-6 * vendor)
                    volume = ref["t_plot_micropore_volume_cm3_g"]
                    self.assertAlmostEqual(t_plot.micropore_volume_cm3_g, volume, delta=1e-6 * max(1e-3, abs(volume)))

    def test_undecoded_asap_2425_free_space_gives_no_quantities(self) -> None:
        # Free-space mode 4 (Y-Zeolite Nitrogen example) is not decoded; the
        # file must not fall back to unrelated doubles.
        result = load_smp(ROOTS["asap2425"] / "examples" / "Y-Zeolite Nitrogen.SMP")
        self.assertEqual(result.point_count, 0)
        self.assertIn("ASAP 2425", result.method_options["no_isotherm_reason"])


if __name__ == "__main__":
    unittest.main()
