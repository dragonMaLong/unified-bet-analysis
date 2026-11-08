"""Row-frame SMP layout (TriStar II 3020 V1.0x, early ASAP 2020 Plus).

Unit tests use synthetic blocks only. The last class reads the Micromeritics
example files shipped with MicroActive and is skipped when they are absent.
Run with ``python -m unittest tests.test_smp_row_frames -v``.
"""

from __future__ import annotations

import math
import struct
import unittest
from dataclasses import replace
from pathlib import Path

from tristar_bet.models import AdsorptiveProperties, RunConditions, SampleInfo
from tristar_bet.smp import TriStarSmpParser, load_smp

FRAME_PREFIX = b"\x05\x00\xe0" + struct.pack("<d", float("nan")) + b"\x04\x00"


def _mic_string(text: str) -> bytes:
    raw = text.encode("utf-16le") + b"\x00\x00"
    return b"\xe0\x01\x00" + struct.pack("<I", len(raw)) + raw


def _block_303(software: str, points, version: int = 13, tube_values=None, tag: int = 4,
               stride: int | None = None) -> bytes:
    block = bytearray(b"SUBSET303\x00" + b"\x00" * 8)
    block += bytes([version]) + b"\xe0\x02\x00\x00\x01\x00\x03\x00"
    block += _mic_string(software) + _mic_string(software) + b"\x00" * 60
    stride = stride or {4: 58, 5: 66}[tag]
    for absolute, relative, dosed in points:
        frame = FRAME_PREFIX[:-2] + bytes([tag, 0]) + struct.pack("<ddd", absolute, relative, dosed) + struct.pack("<I", 100)
        block += frame + b"\x00" * (stride - len(frame))
    block += b"\x00" * 200
    if tube_values is not None:
        tube = len(block) + 300
        block += b"\x00" * (tube - len(block) - 7)
        block += _mic_string("Sample Tube")
        assert TriStarSmpParser()._read_mic_strings(bytes(block))[-1].rel_offset == tube
        block += b"\x00" * 400
        for rel, value in tube_values.items():
            if isinstance(value, int) and not isinstance(value, bool) and rel == 196:
                block[tube + rel] = value
            else:
                struct.pack_into("<d", block, tube + rel, value)
    return bytes(block)


def _run(bath: float | None = 77.35) -> RunConditions:
    fields = {name: None for name in RunConditions.__dataclass_fields__}
    fields["bath_temperature_K"] = bath
    return RunConditions(**fields)


POINTS = [(40.0 * k, 40.0 * k / 750.0, 10.0 + k) for k in range(1, 8)] + [(200.0, 200.0 / 750.0, 15.0)]


class RowFrameUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TriStarSmpParser()

    def test_family_needs_record_version_13_and_known_software(self) -> None:
        self.assertEqual(self.parser._row_frame_family(_block_303("TriStar II 3020 V1.03", POINTS)), "TriStar II 3020")
        self.assertEqual(self.parser._row_frame_family(_block_303("ASAP 2020 Plus ", POINTS)), "ASAP 2020 Plus")
        self.assertEqual(self.parser._row_frame_family(_block_303("TriStar II 3020 V1.03", POINTS, version=12)), "")
        self.assertEqual(self.parser._row_frame_family(_block_303("ASAP 2020 Plus Version 1.03", POINTS)), "")
        self.assertEqual(self.parser._row_frame_family(_block_303("Gemini VII", POINTS)), "")

    def test_frames_are_read_in_order_with_branch_split(self) -> None:
        block = _block_303("TriStar II 3020 V1.03", POINTS)
        rows = self.parser._scan_row_frames(block, len(block) - 18)
        self.assertEqual([round(row[1], 9) for row in rows], [round(p[0], 9) for p in POINTS])
        self.assertEqual({b[0] - a[0] for a, b in zip(rows, rows[1:])}, {58})
        sample = SampleInfo("s", "", "", "", 1.0, None)
        points = self.parser._parse_row_frame_isotherm(block, len(block) - 18, sample, _missing_free_space())
        self.assertEqual([p.phase for p in points].count("desorption"), 1)
        self.assertTrue(all(math.isclose(p.saturation_pressure_mmHg, 750.0) for p in points))

    def test_asap_2020_plus_v200_66_byte_frames(self) -> None:
        # Version 2.00 re-saves with tag 05 and 66-byte frames; the doubles are unchanged.
        old = _block_303("ASAP 2020 Plus", POINTS)
        new = _block_303("ASAP 2020 Plus", POINTS, tag=5)
        old_rows = self.parser._scan_row_frames(old, len(old) - 18)
        new_rows = self.parser._scan_row_frames(new, len(new) - 18)
        self.assertEqual(len(new_rows), len(POINTS))
        self.assertEqual([row[1:] for row in new_rows], [row[1:] for row in old_rows])
        self.assertEqual({b[0] - a[0] for a, b in zip(new_rows, new_rows[1:])}, {66})
        self.assertEqual(self.parser._row_frame_family(new), "ASAP 2020 Plus")

    def test_tag_and_stride_must_agree(self) -> None:
        for tag, stride in ((5, 58), (4, 66)):
            with self.subTest(tag=tag, stride=stride):
                block = _block_303("ASAP 2020 Plus", POINTS, tag=tag, stride=stride)
                self.assertEqual(self.parser._scan_row_frames(block, len(block) - 18), [])

    def test_saturation_frame_is_dropped_after_branch_split(self) -> None:
        # MicroActive leaves out P/P0 > 1 (Titania 1.0008) but the branch still
        # turns there: a following 0.990 point is desorption, not adsorption.
        points = [(375.0 * k / 10, k / 10 * 0.5, 10.0 + k) for k in range(1, 6)]
        points += [(750.6, 1.0008, 30.0), (742.5, 0.990, 29.0), (700.0, 0.9333, 28.0)]
        block = _block_303("TriStar II 3020 V1.00", points)
        sample = SampleInfo("s", "", "", "", 1.0, None)
        parsed = self.parser._parse_row_frame_isotherm(block, len(block) - 18, sample, _missing_free_space())
        self.assertEqual(len(parsed), 7)
        self.assertTrue(all(p.relative_pressure <= 1.0 for p in parsed))
        self.assertEqual([p.phase for p in parsed].count("adsorption"), 5)

    def test_stored_bet_range_is_first_pair_of_subset_311_tail(self) -> None:
        def block(p_min: float, p_max: float) -> bytes:
            return (b"SUBSET311\x00" + b"\x00" * 60 + b"\x02" + struct.pack("<dd", p_min, p_max)
                    + b"\x01" + struct.pack("<dd", 0.0, 1.0) + b"\x00\x00\x00")
        self.assertEqual(self.parser._row_frame_stored_bet_range(block(0.046804, 0.296619)), (0.046804, 0.296619))
        # Lower bound 0 is not a fitted range (13X, Activated Carbon).
        self.assertIsNone(self.parser._row_frame_stored_bet_range(block(0.0, 0.3)))
        self.assertIsNone(self.parser._row_frame_stored_bet_range(block(0.3, 0.1)))
        self.assertIsNone(self.parser._row_frame_stored_bet_range(b"SUBSET311\x00" + b"\x00" * 80))

    def test_nitrogen_compressibility_matches_microactive(self) -> None:
        import json

        from tristar_bet.real_gas import nitrogen_compressibility

        ref = json.loads((Path(__file__).parent / "fixtures" / "microactive_row_frame_reference.json").read_text())
        sample = ref["samples"]["Activated Carbon (real gas)"]
        for pressure, z in zip(sample["absolute_pressure_mmHg"], sample["compressibility_factor"]):
            # MicroActive's Z at 77.3 K; largest difference 7.2e-5 (top point).
            self.assertAlmostEqual(nitrogen_compressibility(pressure, 77.3), z, delta=1e-4)
        self.assertEqual(nitrogen_compressibility(0.0, 77.3), 1.0)

    def test_real_gas_quantity_inverts_free_space_model(self) -> None:
        from tristar_bet.models import FreeSpaceInfo

        fs = FreeSpaceInfo(None, None, 0.0, 84.85, 27.51, 0.0, 77.5, None, "x")
        sample = SampleInfo("s", "", "", "", 0.1427, None)
        x, z, k, adsorbed = 500.0 / 760.0, 0.97, 0.005477, 30.0
        dosed = adsorbed + x * (84.85 - 77.5) + x * 77.5 / z - x * k * adsorbed / z
        q = self.parser._real_gas_quantity(500.0, dosed, sample, fs, z, k)
        self.assertAlmostEqual(q * 0.1427, adsorbed, places=10)

    def test_weber_transpiration_ratio(self) -> None:
        # 13X example: transducer 2.035201e-4 mmHg -> MicroActive 1.079294e-4 mmHg
        # (tube 9.53 mm, hard sphere 3.86 A, bath 77.183 K, ambient 298 K).
        ratio = self.parser._weber_transpiration_ratio(2.035201177932322e-04, 9.53, 3.86, 77.18300781250002)
        self.assertAlmostEqual(ratio, 1.079294e-04 / 2.035201177932322e-04, places=5)
        self.assertGreater(self.parser._weber_transpiration_ratio(1.0, 9.53, 3.86, 77.18), 0.99)

    def test_legacy_v6_record_chain(self) -> None:
        block = bytearray(b"SUBSET303\x00" + b"\x00" * 8 + b"\x06" + b"\x00" * 100)
        struct.pack_into("<H", block, 86, 3)
        block += b"\x00" * (119 - len(block))
        for k, tags in enumerate(((), (103,), (103, 104))):
            block += struct.pack("<ddd", 20.0 * (k + 1), 20.0 * (k + 1) / 745.0, 3.0 + k)
            block += struct.pack("<HH", 100 + k, len(tags)) + b"".join(struct.pack("<H", t) for t in tags) + b"\x00"
        block += b"\x00" * 40
        rows = self.parser._legacy_v6_records(bytes(block))
        self.assertEqual([r[5] for r in rows], [(), (103,), (103, 104)])
        self.assertAlmostEqual(rows[2][1], 60.0)
        block[18] = 13
        self.assertEqual(self.parser._legacy_v6_records(bytes(block)), [])

    def test_stray_prefix_off_stride_is_ignored(self) -> None:
        block = bytearray(_block_303("TriStar II 3020 V1.03", POINTS))
        block += FRAME_PREFIX + struct.pack("<ddd", 100.0, 100.0 / 750.0, 1.0) + b"\x00" * 40
        rows = self.parser._scan_row_frames(bytes(block), len(block) - 18)
        self.assertEqual(len(rows), len(POINTS))

    def test_sample_mass_is_first_of_mass_tube_total_record(self) -> None:
        base = SampleInfo("s", "", "", "", 2.132, None)
        block = b"SUBSET301\x00" + b"\x00" * 150 + struct.pack("<dddd", 1.132, 1.0, 2.132, 1.0) + b"\x00" * 40
        self.assertAlmostEqual(self.parser._with_row_frame_sample_mass(base, block).sample_mass_g, 1.132)
        block = b"SUBSET301\x00" + b"\x00" * 150 + struct.pack("<dddd", 0.5057, 28.9788, 29.4845, 1.9) + b"\x00" * 40
        weighed = self.parser._with_row_frame_sample_mass(base, block)
        self.assertAlmostEqual(weighed.sample_mass_g, 0.5057)
        self.assertAlmostEqual(weighed.sample_density_g_cm3, 1.9)
        self.assertIsNone(self.parser._with_row_frame_sample_mass(base, b"SUBSET301\x00" + b"\x00" * 200).sample_mass_g)

    def test_tristar_measured_free_space_applies_correction(self) -> None:
        block = _block_303(
            "TriStar II 3020 V1.00", POINTS,
            tube_values={-57: 295.15, -32: 6.2e-05, -24: 28.526728, -16: 10.201172, 196: 0, 218: -0.256815},
        )
        fs = self.parser._parse_row_frame_free_space(block, "TriStar II 3020", _run(), None)
        # The analysis log of the same file: "Warm: 9.9444 cm3, Cold: 28.2699 cm3".
        self.assertAlmostEqual(fs.cold_free_space_cm3, 28.2699, places=4)
        self.assertAlmostEqual(fs.warm_free_space_cm3, 9.9444, places=4)
        self.assertAlmostEqual(fs.nonideality_factor, 6.2e-05)
        self.assertEqual(fs.vfree_factor_source, "tristar_3020_v1_row_frame_measured_free_space")
        self.assertIsNone(self.parser._parse_row_frame_free_space(block, "TriStar II 3020", _run(None), None).vfree_factor_cm3)

    def test_asap_entered_free_space_uses_entered_slots(self) -> None:
        block = _block_303(
            "ASAP 2020 Plus ", POINTS,
            tube_values={-48: 84.8183, -40: 27.6145, -32: 5.6e-05, -24: 1.0, -16: 1.0, 196: 2},
        )
        fs = self.parser._parse_row_frame_free_space(block, "ASAP 2020 Plus", _run(1e-300), None)
        self.assertAlmostEqual(fs.cold_free_space_cm3, 84.8183)
        self.assertAlmostEqual(fs.warm_free_space_cm3, 27.6145)
        self.assertEqual(fs.vfree_factor_source, "asap_2020_plus_row_frame_entered_free_space")

    def test_unsupported_free_space_modes_ignore_stale_measured_volumes(self) -> None:
        # A file can retain valid measured/entered volumes after its mode is
        # changed. Only the mode, not the plausibility of those values, decides
        # whether the decoded correction is applicable.
        for family, software in (
            ("TriStar II 3020", "TriStar II 3020 V1.00"),
            ("ASAP 2020 Plus", "ASAP 2020 Plus "),
            ("ASAP 2425", "ASAP 2425"),
        ):
            for mode in (4, 6, 255):
                with self.subTest(family=family, mode=mode):
                    block = _block_303(
                        software, POINTS,
                        tube_values={-57: 298.0, -48: 45.0, -40: 16.0, -32: 5.6e-05,
                                     -24: 47.7, -16: 16.3, 196: mode, 218: 0.0},
                    )
                    if family == "ASAP 2425":
                        fs = self.parser._parse_free_space(block, len(block) - 18, _run(), None)
                        self.assertEqual(fs.vfree_factor_source, "asap_2425_free_space_mode_not_decoded")
                    else:
                        fs = self.parser._parse_row_frame_free_space(block, family, _run(), None)
                    self.assertIsNone(fs.vfree_factor_cm3)
                    self.assertIsNone(fs.cold_free_space_cm3)
                    points = self.parser._parse_row_frame_isotherm(
                        block, len(block) - 18, SampleInfo("s", "", "", "", 1.0, None), fs,
                    )
                    self.assertTrue(points)
                    self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in points))

    def test_adsorptive_fields_follow_actual_mnemonic(self) -> None:
        block = bytearray(b"SUBSET320\x00" + b"\x00" * 9)
        block += b"\x00" * (26 - 7 - len(block)) + _mic_string("Krypton @ 77.35 K")
        mnemonic = len(block) + 7
        block += _mic_string("Kr")
        block += b"\x00" * 700
        for offset, value in ((8, 925.0), (18, 3.0e-05), (26, 0.0018), (34, 3.6), (42, 0.21), (50, 83.8)):
            struct.pack_into("<d", block, mnemonic + offset, value)
        props = self.parser._parse_adsorptive_properties(bytes(block))
        self.assertEqual(props.mnemonic, "Kr")
        self.assertAlmostEqual(props.molecular_cross_sectional_area_nm2, 0.21)
        self.assertAlmostEqual(props.nonideality_factor, 3.0e-05)
        self.assertAlmostEqual(props.max_manifold_pressure_mmHg, 925.0)

    def test_reasons_for_files_without_isotherm(self) -> None:
        sample = SampleInfo("s", "", "", "", 1.0, None)
        chem = {302: b"SUBSET302\x00" + b"\x00" * 9 + _mic_string("Chemisorption Analysis")}
        self.assertIn("化学吸附", self.parser._no_isotherm_reason(chem, sample))
        self.assertIn("SUBSET303", self.parser._no_isotherm_reason({}, sample))
        old = {303: b"SUBSET303\x00" + b"\x00" * 8 + bytes([6]) + b"\x00" * 50}
        self.assertIn("记录版本 6", self.parser._no_isotherm_reason(old, sample))
        template = {303: b"SUBSET303\x00" + b"\x00" * 8 + bytes([14]) + b"\x00" * 50}
        self.assertIn("模板", self.parser._no_isotherm_reason(template, sample))


def _missing_free_space():
    from tristar_bet.models import FreeSpaceInfo

    return FreeSpaceInfo(None, None, None, None, None, None, None, None, "missing")


EXAMPLES = Path(r"E:\MicroActive\data")


@unittest.skipUnless(EXAMPLES.is_dir(), "MicroActive example data not installed")
class MicroActiveExampleFiles(unittest.TestCase):
    """Plausibility on vendor demo files; not a comparison with vendor output."""

    def test_tristar_3020_v1_examples_import_with_logged_free_space(self) -> None:
        for name in ("Carbon Black D-6.smp", "Silica Gel.smp", "Silica-Alumina.smp", "Titania.smp"):
            with self.subTest(name=name):
                result = load_smp(EXAMPLES / "TriStar example data" / name)
                self.assertGreater(result.point_count, 10)
                self.assertEqual(result.method_options["format_family"], "TriStar II 3020 row-frame SMP")
                self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is not None for p in result.isotherm))
                logged = [m.text for m in result.log_messages if "Measured free space" in m.text][-1]
                self.assertIn(f"Cold: {result.free_space.cold_free_space_cm3:.4f}", logged)
                self.assertIn(f"Warm: {result.free_space.warm_free_space_cm3:.4f}", logged)

    def test_silica_alumina_reference_bet_near_label(self) -> None:
        from tristar_bet.analysis import bet_analysis

        tri = load_smp(EXAMPLES / "TriStar example data" / "Silica-Alumina.smp")
        asap = load_smp(EXAMPLES / "ASAP example data" / "Silica-Alumina - nitrogen.SMP")
        # Sample description: "Silica Alumina - 215 m2/g". 5 % band: plausibility only.
        for result in (tri, asap):
            self.assertAlmostEqual(bet_analysis(result, 0.05, 0.30).surface_area_m2_g, 215.0, delta=10.75)

    def test_points_match_microactive_output(self) -> None:
        import json

        reference = json.loads((Path(__file__).parent / "fixtures" / "microactive_row_frame_reference.json").read_text())
        for name, ref in reference["samples"].items():
            with self.subTest(name=name):
                result = load_smp(EXAMPLES / ref["example_path"])
                self.assertEqual(result.point_count, len(ref["absolute_pressure_mmHg"]))
                self.assertEqual(sum(p.phase == "adsorption" for p in result.isotherm), ref["number_adsorption"])
                self.assertAlmostEqual(result.sample.sample_mass_g, ref["sample_mass_g"], places=9)
                tolerance = ref["quantity_rel_tolerance"]
                for point, pressure, dosed, adsorbed in zip(
                    result.isotherm, ref["absolute_pressure_mmHg"], ref["quantity_dosed_cm3_stp"], ref["quantity_adsorbed_cm3_g_stp"]
                ):
                    # Thermal-transpiration files: corrected pressure to 1e-6 relative
                    # (MicroActive keeps the derived bath temperature as float32).
                    self.assertAlmostEqual(point.absolute_pressure_mmHg, pressure, delta=1e-6 * pressure)
                    self.assertEqual(point.raw_internal_cm3_stp, dosed)
                    self.assertAlmostEqual(point.quantity_adsorbed_cm3_g_stp, adsorbed, delta=tolerance * max(1e-3, abs(adsorbed)))
                from tristar_bet.analysis import bet_analysis, t_plot_analysis

                # Default BET / t-Plot (ranges and thickness model saved in the file).
                vendor_bet = ref["bet_surface_area_m2_g"]
                if vendor_bet:  # 0 when the report is not enabled in the file
                    self.assertAlmostEqual(bet_analysis(result).surface_area_m2_g, vendor_bet, delta=1e-3 * vendor_bet)
                if ref.get("t_plot_external_area_m2_g"):
                    vendor_ext = ref["t_plot_external_area_m2_g"]
                    self.assertAlmostEqual(t_plot_analysis(result).external_surface_area_m2_g, vendor_ext, delta=5e-4 * vendor_ext)

    def test_non_physisorption_examples_give_reason(self) -> None:
        result = load_smp(EXAMPLES / "Autopore example data" / "CPG 1000 Port 1.SMP")
        self.assertEqual(result.point_count, 0)
        self.assertIn("SUBSET303", result.method_options["no_isotherm_reason"])


ASAP_DATA = Path(r"C:\ASAP 2020 Plus\data")
RESAVED = Path(__file__).resolve().parents[1] / "outputs" / "asap_v200_check"


@unittest.skipUnless(ASAP_DATA.is_dir() and RESAVED.is_dir(), "ASAP 2020 Plus examples / re-saved copies not present")
class AsapPlusVersion200Resaved(unittest.TestCase):
    """Vendor examples re-saved by ASAP 2020 Plus Version 2.00 (66-byte frames) import as the originals."""

    PAIRS = {"ac_v200.SMP": "Activated Carbon.SMP",
             "sa_v200.SMP": "Silica Alumina Reference Material with Nitrogen.SMP",
             "glass_v200.SMP": "Glass Reference Material.SMP"}

    def test_resaved_files_match_originals(self) -> None:
        from tristar_bet.analysis import bet_analysis

        for resaved, original in self.PAIRS.items():
            with self.subTest(resaved=resaved):
                new, old = load_smp(RESAVED / resaved), load_smp(ASAP_DATA / original)
                self.assertGreater(new.point_count, 5)
                self.assertEqual(new.point_count, old.point_count)
                self.assertEqual(new.method_options["format_family"], "ASAP 2020 Plus row-frame SMP")
                self.assertEqual(new.free_space.vfree_factor_source, old.free_space.vfree_factor_source)
                if resaved != "glass_v200.SMP":
                    self.assertEqual(new.run_conditions.bath_temperature_K, 77.35)
                    self.assertEqual(old.run_conditions.bath_temperature_K, 77.35)
                for a, b in zip(new.isotherm, old.isotherm):
                    self.assertEqual((a.absolute_pressure_mmHg, a.relative_pressure, a.quantity_adsorbed_cm3_g_stp),
                                     (b.absolute_pressure_mmHg, b.relative_pressure, b.quantity_adsorbed_cm3_g_stp))
                self.assertEqual(bet_analysis(new).surface_area_m2_g, bet_analysis(old).surface_area_m2_g)

    def test_glass_invalid_generic_bath_is_hidden_without_changing_pore_calculations(self) -> None:
        from tristar_bet.analysis import (
            bjh_pore_distribution, dh_pore_distribution, horvath_kawazoe_pore_distribution,
        )

        for path in (ASAP_DATA / self.PAIRS["glass_v200.SMP"], RESAVED / "glass_v200.SMP"):
            with self.subTest(path=path.name):
                result = load_smp(path)
                self.assertIsNone(result.run_conditions.bath_temperature_K)
                # Recover the previous generic value directly from subset 302;
                # this must not be confused with the independently decoded
                # temperature used by ASAP's real-gas/transpiration correction.
                data = path.read_bytes()
                parser = TriStarSmpParser()
                header = parser._parse_header(path, data)
                entries = parser._parse_directory(data, header.directory_offset)
                run_entry = next(entry for entry in entries if entry.subset_id == 302)
                block = data[run_entry.offset:run_entry.offset + run_entry.total_size]
                raw_run = parser._parse_run_conditions(block)
                self.assertIsNotNone(raw_run.bath_temperature_K)
                self.assertFalse(50.0 < raw_run.bath_temperature_K < 500.0)
                legacy = replace(result, run_conditions=replace(
                    result.run_conditions, bath_temperature_K=raw_run.bath_temperature_K,
                ))
                for calculate in (bjh_pore_distribution, dh_pore_distribution, horvath_kawazoe_pore_distribution):
                    self.assertEqual(calculate(result), calculate(legacy))


if __name__ == "__main__":
    unittest.main()
