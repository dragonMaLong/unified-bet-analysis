"""Raw 3Flex 3500 SMP files and the equations of state behind them.

The unit tests need no external files. The vendor comparison reads the example
files installed with the 3Flex software and checks them against MicroActive 7.00
output (tests/fixtures/microactive_3flex_reference.json); it is skipped when the
files are absent.
"""

from __future__ import annotations

import json
import struct
import unittest
from pathlib import Path
from unittest.mock import patch

from tristar_bet.models import AdsorptiveProperties, SampleInfo
from tristar_bet.real_gas import (
    compressibility,
    saturation,
    saturation_temperature,
    tabulated_compressibility,
)
from tristar_bet.smp import TriStarSmpParser, load_smp

REFERENCE = json.loads((Path(__file__).parent / "fixtures" / "microactive_3flex_reference.json").read_text(encoding="utf-8"))
ROOT = Path(REFERENCE["root"])


class InvalidCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TriStarSmpParser()
        # Minimal v12 records: three raw point frames, measured free space,
        # sample mass and an entered nitrogen bath temperature.
        block = bytearray(800)
        block[18] = 12
        prefix = b"\x05\x00\xe0" + struct.pack("<d", float("nan")) + b"\x04\x00"
        for i, pressure in enumerate((10.0, 100.0, 200.0)):
            at = 50 + 58 * i
            block[at:at + len(prefix)] = prefix
            struct.pack_into("<ddd", block, at + len(prefix), pressure, pressure / 760.0, 5.0 + i)
        name = "Sample Tube".encode("utf-16le") + b"\x00\x00"
        block[393:400 + len(name)] = b"\xe0\x01\x00" + struct.pack("<I", len(name)) + name
        for at, value in ((343, 298.0), (376, 30.0), (384, 10.0)):
            struct.pack_into("<d", block, at, value)
        sample_block = bytearray(40)
        struct.pack_into("<d", sample_block, 19, 0.1)
        run = bytearray(200)
        name = "N2".encode("utf-16le") + b"\x00\x00"
        run[93:100 + len(name)] = b"\xe0\x01\x00" + struct.pack("<I", len(name)) + name
        for at, value in ((69, 77.3), (77, 760.0), (85, 298.0)):
            struct.pack_into("<d", run, at, value)
        self.blocks = {301: bytes(sample_block), 302: bytes(run), 303: bytes(block)}
        self.sample = SampleInfo("s", "", "", "", 0.1, None)
        fields = {name: None for name in AdsorptiveProperties.__dataclass_fields__}
        fields.update(adsorptive="Nitrogen", mnemonic="N2", max_manifold_pressure_mmHg=925.0, psat_table=[])
        self.props = AdsorptiveProperties(**fields)

    def test_unavailable_or_invalid_z_returns_reason_without_partial_isotherm(self) -> None:
        for invalid in (None, 0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(z=invalid), patch(
                "tristar_bet.smp.real_gas_tabulated_compressibility", side_effect=[1.0, invalid],
            ):
                result = self.parser._parse_3flex_raw(self.blocks, [], self.sample, self.props)
                self.assertIn("第 2 个数据点", result["reason"])
                self.assertIn("压缩因子", result["reason"])
                self.assertFalse(result.get("isotherm"))

    def test_singular_adsorbed_phase_correction_returns_reason(self) -> None:
        factor = 0.002
        k = factor * 273.15 / 77.3
        z = 10.0 / 760.0 * k
        with patch("tristar_bet.smp.real_gas_saturation", return_value=(760.0, 1.0 / (factor * 22.414))), patch(
            "tristar_bet.smp.real_gas_tabulated_compressibility", return_value=z,
        ):
            result = self.parser._parse_3flex_raw(self.blocks, [], self.sample, self.props)
        self.assertIn("吸附相自由空间修正无效", result["reason"])
        self.assertFalse(result.get("isotherm"))


class Version500FrameTests(unittest.TestCase):
    """3Flex Version 5.00 files: version-bearing software string, 66-byte frames."""

    def setUp(self) -> None:
        self.parser = TriStarSmpParser()

    @staticmethod
    def _mic_string(text: str) -> bytes:
        name = text.encode("utf-16le") + b"\x00\x00"
        return b"\xe0\x01\x00" + struct.pack("<I", len(name)) + name

    def _frames_block(self, tag: int, stride: int) -> bytes:
        block = bytearray(800)
        block[18] = 13
        prefix = b"\x05\x00\xe0" + struct.pack("<d", float("nan")) + bytes((tag, 0))
        for i, pressure in enumerate((0.04, 10.0, 200.0)):
            at = 50 + stride * i
            block[at:at + len(prefix)] = prefix
            struct.pack_into("<ddd", block, at + len(prefix), pressure, pressure / 747.0, 5.0 + i)
        return bytes(block)

    def test_software_string_with_version_is_3flex(self) -> None:
        for text, expected in (("3Flex Version 5.00", True), ("3Flex", True), ("ASAP 2020 Plus Version 2.00", False)):
            with self.subTest(text=text):
                block = bytearray(400)
                block[18] = 13
                block[100:100] = self._mic_string(text)
                self.assertEqual(self.parser._is_3flex_raw(bytes(block)), expected)

    def test_66_byte_frames_are_read(self) -> None:
        for tag, stride in ((4, 58), (5, 66)):
            with self.subTest(stride=stride):
                frames = self.parser._flex_frames(self._frames_block(tag, stride))
                self.assertEqual(len(frames), 3)
                self.assertEqual(self.parser._flex_frame_stride(frames), stride)
                self.assertAlmostEqual(frames[1][1], 10.0)

    def test_tag_and_spacing_must_agree(self) -> None:
        # Tag 05 frames 58 bytes apart (or tag 04 frames 66 apart) are not a point table.
        self.assertEqual(self.parser._flex_frames(self._frames_block(5, 58)), [])
        self.assertEqual(self.parser._flex_frames(self._frames_block(4, 66)), [])

    def test_stored_relative_pressure_is_not_corrected_twice(self) -> None:
        fields = {name: None for name in AdsorptiveProperties.__dataclass_fields__}
        fields.update(adsorptive="Nitrogen", mnemonic="N2", max_manifold_pressure_mmHg=925.0, psat_table=[],
                      thermal_transpiration_hard_sphere_A=3.681)
        props = AdsorptiveProperties(**fields)
        for tag, stride, relative_corrected in ((4, 58, True), (5, 66, False)):
            with self.subTest(stride=stride):
                block = bytearray(self._frames_block(tag, stride))
                block[18] = 12
                name = "Sample Tube".encode("utf-16le") + b"\x00\x00"
                block[393:400 + len(name)] = b"\xe0\x01\x00" + struct.pack("<I", len(name)) + name
                for at, value in ((343, 298.0), (376, 30.0), (384, 10.0)):
                    struct.pack_into("<d", block, at, value)
                sample_block = bytearray(40)
                struct.pack_into("<d", sample_block, 19, 0.1)
                run = bytearray(200)
                gas = "N2".encode("utf-16le") + b"\x00\x00"
                run[93:100 + len(gas)] = b"\xe0\x01\x00" + struct.pack("<I", len(gas)) + gas
                for at, value in ((69, 77.3), (77, 760.0), (85, 298.0)):
                    struct.pack_into("<d", run, at, value)
                blocks = {301: bytes(sample_block), 302: bytes(run), 303: bytes(block)}
                sample = SampleInfo("s", "", "", "", 0.1, None)
                with patch.object(TriStarSmpParser, "_row_frame_transpiration_setting", return_value=9.53), patch.object(
                    TriStarSmpParser, "_weber_transpiration_ratio", side_effect=[0.9, 1.0, 1.0],
                ):
                    result = self.parser._parse_3flex_raw(blocks, [], sample, props)
                first = result["isotherm"][0]
                self.assertAlmostEqual(first.absolute_pressure_mmHg, 0.04 * 0.9)
                expected_relative = 0.04 / 747.0 * (0.9 if relative_corrected else 1.0)
                self.assertAlmostEqual(first.relative_pressure, expected_relative, delta=1e-15)
                self.assertAlmostEqual(first.saturation_pressure_mmHg, 747.0 if relative_corrected else 747.0 * 0.9)
                self.assertEqual(result["options"]["flex_point_frame_bytes"], stride)


class EquationOfStateTests(unittest.TestCase):
    def test_compressibility_matches_microactive(self) -> None:
        # (adsorptive, P mmHg, T K, MicroActive 7.00 Z) from the 3Flex example dumps.
        cases = [
            ("Ar", 742.0, 87.08627833982138, None),  # only checks that an equation exists
            ("Kr", 1.0, 77.14492788217353, 1.0 - 1.7999e-4),
        ]
        for gas, pressure, temperature, expected in cases:
            with self.subTest(gas=gas):
                value = compressibility(gas, pressure, temperature)
                self.assertIsNotNone(value)
                if expected is not None:
                    self.assertAlmostEqual(value, expected, delta=2e-7)
        self.assertIsNone(compressibility("He", 1.0, 77.0))

    def test_normal_boiling_points(self) -> None:
        self.assertAlmostEqual(saturation("N2", 77.355)[0], 760.0, delta=0.01)
        self.assertAlmostEqual(saturation("Ar", 87.302)[0], 760.0, delta=0.02)
        self.assertIsNone(saturation("Kr", 77.3))  # below the triple point

    def test_analysis_temperature_from_p0(self) -> None:
        # Mean logged P0 -> MicroActive analysis temperature (Glass, ZSM-5 Ar).
        self.assertAlmostEqual(saturation_temperature("N2", 739.3924, 77.3), 77.12254467522135, delta=1e-5)
        self.assertAlmostEqual(saturation_temperature("Ar", 742.8256, 87.3), 87.08627833982138, delta=1e-5)

    def test_tabulated_z_reproduces_plateau_next_to_saturation(self) -> None:
        # Same float32 node value at two temperatures and pressures (N2 blank, Silica Alumina).
        self.assertAlmostEqual(tabulated_compressibility("N2", 742.8876953125, 77.17447600368318), 0.957808768, delta=5e-8)
        self.assertAlmostEqual(tabulated_compressibility("N2", 744.8919677734375, 77.18539410816847), 0.957808768, delta=5e-8)
        # Away from saturation the table follows the equation of state closely.
        self.assertAlmostEqual(tabulated_compressibility("N2", 200.0, 77.3), compressibility("N2", 200.0, 77.3), delta=1e-6)


@unittest.skipUnless(ROOT.is_dir(), "3Flex example data not installed")
class VendorComparison(unittest.TestCase):
    def test_points_and_reports_match_microactive(self) -> None:
        from tristar_bet.analysis import bet_analysis, t_plot_analysis

        for name, ref in REFERENCE["samples"].items():
            with self.subTest(name=name):
                result = load_smp(ROOT / ref["path"])
                options = result.method_options
                self.assertEqual(options["format_family"], "3Flex raw SMP")
                self.assertEqual(result.adsorptive_properties.mnemonic, ref["adsorptive"])
                self.assertEqual(result.point_count, len(ref["absolute_pressure_mmHg"]))
                self.assertEqual(sum(p.phase == "adsorption" for p in result.isotherm), ref["number_adsorption"])
                self.assertAlmostEqual(result.sample.sample_mass_g, ref["sample_mass_g"], places=9)
                self.assertAlmostEqual(options["flex_analysis_temperature_K"], ref["analysis_temperature_K"], delta=5e-5)
                for point, pressure, dosed, adsorbed in zip(
                    result.isotherm, ref["absolute_pressure_mmHg"], ref["quantity_dosed_cm3_stp"], ref["quantity_adsorbed_cm3_g_stp"]
                ):
                    self.assertAlmostEqual(point.absolute_pressure_mmHg, pressure, delta=1e-7 * pressure)
                    self.assertEqual(point.raw_internal_cm3_stp, dosed)
                    if ref["quantity_rel_tolerance"] is not None:
                        tolerance = ref["quantity_rel_tolerance"] * max(1e-3, abs(adsorbed))
                    else:
                        tolerance = ref["quantity_abs_tolerance_cm3_g"]
                    self.assertAlmostEqual(point.quantity_adsorbed_cm3_g_stp, adsorbed, delta=tolerance)
                if ref["bet_surface_area_m2_g"]:
                    vendor = ref["bet_surface_area_m2_g"]
                    self.assertAlmostEqual(bet_analysis(result).surface_area_m2_g, vendor, delta=5e-5 * vendor)
                if ref["t_plot_external_area_m2_g"]:
                    vendor = ref["t_plot_external_area_m2_g"]
                    self.assertAlmostEqual(t_plot_analysis(result).external_surface_area_m2_g, vendor, delta=2e-3 * vendor)

    def test_chemisorption_and_template_files_give_reason(self) -> None:
        for name in ("3500Chemi.smp", "3500.smp"):
            matches = list(ROOT.parent.rglob(name)) or list(ROOT.rglob(name))
            if not matches:
                continue
            with self.subTest(name=name):
                result = load_smp(matches[0])
                self.assertEqual(result.point_count, 0)
                self.assertTrue(result.method_options.get("no_isotherm_reason"))


V500_REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "flex603_3flex_v500_reference.json").read_text(encoding="utf-8"))
V500_SMP = Path(__file__).resolve().parents[1] / V500_REFERENCE["smp_path"]
KPA_PER_MMHG = 0.133322368


@unittest.skipUnless(V500_SMP.is_file(), "3Flex Version 5.00 sample (customer file) not present")
class Version500VendorComparison(unittest.TestCase):
    """Checked against the Flex 6.03 default XLS report of the same file."""

    def test_points_and_reports_match_flex_603(self) -> None:
        from tristar_bet.analysis import bet_analysis, t_plot_analysis

        ref = V500_REFERENCE
        result = load_smp(V500_SMP)
        self.assertEqual(result.method_options["format_family"], "3Flex raw SMP")
        self.assertEqual(result.method_options["flex_point_frame_bytes"], 66)
        self.assertEqual(result.point_count, len(ref["relative_pressure"]))
        self.assertEqual(sum(p.phase == "adsorption" for p in result.isotherm), ref["number_adsorption"])
        self.assertAlmostEqual(result.sample.sample_mass_g, ref["sample_mass_g"], places=9)
        for point, relative, absolute, adsorbed, p0 in zip(
            result.isotherm, ref["relative_pressure"], ref["absolute_pressure_kPa"],
            ref["quantity_adsorbed_mmol_g"], ref["saturation_pressure_kPa"],
        ):
            self.assertAlmostEqual(point.relative_pressure, relative, delta=5e-6 * relative)
            self.assertAlmostEqual(point.absolute_pressure_mmHg * KPA_PER_MMHG, absolute, delta=1e-6 * absolute)
            self.assertAlmostEqual(point.saturation_pressure_mmHg * KPA_PER_MMHG, p0, delta=1e-6 * p0)
            self.assertAlmostEqual(point.quantity_adsorbed_mmol_g, adsorbed, delta=5e-5 * adsorbed)
        bet = bet_analysis(result)
        self.assertAlmostEqual(bet.pressure_min, ref["bet_pressure_range"][0], delta=1e-9)
        self.assertAlmostEqual(bet.pressure_max, ref["bet_pressure_range"][1], delta=1e-9)
        self.assertAlmostEqual(bet.surface_area_m2_g, ref["bet_surface_area_m2_g"], delta=5e-5 * ref["bet_surface_area_m2_g"])
        self.assertAlmostEqual(bet.c_constant, ref["bet_c"], delta=5e-5 * ref["bet_c"])
        t_plot = t_plot_analysis(result)
        self.assertAlmostEqual(t_plot.micropore_volume_cm3_g, ref["t_plot_micropore_volume_cm3_g"],
                               delta=1e-4 * ref["t_plot_micropore_volume_cm3_g"])
        self.assertAlmostEqual(t_plot.external_surface_area_m2_g, ref["t_plot_external_area_m2_g"],
                               delta=2e-3 * ref["t_plot_external_area_m2_g"])


if __name__ == "__main__":
    unittest.main()
