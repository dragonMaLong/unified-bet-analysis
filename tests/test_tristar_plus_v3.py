"""Synthetic regressions for the TriStar II Plus 3.02 SMP layout.

All binary fixtures are constructed here; no instrument/customer files are
needed. Run with ``python -m unittest discover -s tests -v``.
"""

from __future__ import annotations

import math
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tristar_bet.analysis import (
    analysis_bundle,
    automatic_bet_range,
    automatic_langmuir_range,
    automatic_t_plot_pressure_range,
    bet_analysis,
    density_conversion_factor,
    langmuir_analysis,
    t_plot_analysis,
    t_plot_analysis_by_thickness,
)
from tristar_bet.models import AdsorptiveProperties, FreeSpaceInfo, RunConditions, SampleInfo
from tristar_bet.smp import TriStarSmpParser, _isotherm_rows


SOFTWARE = "TriStar II Plus Version 3.02"
POINT_START = 326
POINT_STRIDE = 66
TUBE_TEXT_OFFSET = 1000


def _block(subset_id: int, length: int) -> bytearray:
    result = bytearray(length)
    marker = f"SUBSET{subset_id}".encode("ascii")
    result[: len(marker)] = marker
    return result


def _string(block: bytearray, text_offset: int, text: str) -> None:
    encoded = (text + "\0").encode("utf-16le")
    block[text_offset - 7 : text_offset] = b"\xe0\x01\x00" + struct.pack("<I", len(encoded))
    block[text_offset : text_offset + len(encoded)] = encoded


def _run() -> RunConditions:
    return RunConditions(
        evacuation_rate_mmHg_s=None,
        unrestricted_evacuate_from_mmHg=None,
        evacuation_time_h=None,
        leak_test_time_s=None,
        equilibration_interval_s=None,
        free_space_equilibration_time_h=None,
        ambient_free_space_entered_cm3=None,
        analysis_free_space_entered_cm3=None,
        desorption_test_time_s=None,
        po_reference_mmHg=760.0,
        bath_temperature_K=77.3,
        adsorptive_short="N2",
        adsorptive_name="Nitrogen @ 77.35 K",
    )


def _properties() -> AdsorptiveProperties:
    return AdsorptiveProperties(
        adsorptive="Nitrogen @ 77.35 K",
        mnemonic="N2",
        max_manifold_pressure_mmHg=760.0,
        max_manifold_pressure_kPa=101.325,
        nonideality_factor=0.000062,
        density_conversion_factor=0.0015468,
        thermal_transpiration_hard_sphere_A=3.86,
        thermal_transpiration_hard_sphere_nm=0.386,
        molecular_cross_sectional_area_nm2=0.162,
        ui_field_rel101=28.0134,
    )


def _sample() -> SampleInfo:
    return SampleInfo("Synthetic sample", "Tester", "", "", 2.0, None)


def _measured_block(*, software: str = SOFTWARE, correction: float = -0.3) -> bytes:
    block = _block(303, 1400)
    _string(block, 34, software)
    _string(block, TUBE_TEXT_OFFSET, "Sample Tube")
    struct.pack_into("<d", block, TUBE_TEXT_OFFSET - 24, 31.0)
    struct.pack_into("<d", block, TUBE_TEXT_OFFSET - 16, 11.0)
    struct.pack_into("<d", block, TUBE_TEXT_OFFSET + 218, correction)
    struct.pack_into("<d", block, TUBE_TEXT_OFFSET - 237, 298.0)
    # These raw volumes correspond to corrected quantities 2, 4, 3 cm3/g
    # for a 2 g sample with Vfree=30, Vbath=20 and nonideality=0.000062.
    rows = ((76.0, 0.1, 7.009424, 90), (380.0, 0.5, 23.2356, 180), (152.0, 0.2, 12.037696, 240))
    for index, row in enumerate(rows):
        struct.pack_into("<dddI", block, POINT_START + POINT_STRIDE * index, *row)
    return bytes(block)


def _method_block(*, name: str = "Method", gas_offset: int = 500) -> bytes:
    block = _block(302, gas_offset + 300)
    _string(block, 26, name)
    name_end = 26 + len((name + "\0").encode("utf-16le"))
    struct.pack_into("<ddd", block, name_end + 5, 10.0, 5.0, 0.5)
    struct.pack_into("<Id", block, name_end + 31, 120, 10.0)
    _string(block, gas_offset, "N2")
    _string(block, gas_offset + 13, "NITROGEN")
    struct.pack_into("<d", block, gas_offset - 39, 760.0)
    struct.pack_into("<d", block, gas_offset - 31, 77.3)
    # The report's measurement interval differs from the earlier 10 s field.
    struct.pack_into("<d", block, gas_offset - 121, 20.0)
    return bytes(block)


def _compact_sample_block(*, comment: str, density: float = 1.0, total_mass: float = 28.0) -> bytes:
    block = _block(301, 1000)
    cursor = 28
    for text in ("Synthetic", "", "Submitter", "BAR-001", "Sample:", "Operator:", "Submitter:", "Bar Code:"):
        _string(block, cursor, text)
        cursor += len((text + "\0").encode("utf-16le")) + 7
    _string(block, cursor, comment)
    comment_end = cursor + len((comment + "\0").encode("utf-16le"))
    block[comment_end : comment_end + 5] = b"\x01\x01\x00\x00\x00"
    struct.pack_into("<dddd", block, comment_end + 5, 2.0, 26.0, total_mass, density)
    _string(block, comment_end + 46, "")
    return bytes(block)


def _report_blocks(*, pressure_min: float = 0.0, pressure_max: float = 760.0,
                   t_min_angstrom: float = 3.5, t_max_angstrom: float = 5.0,
                   selector: int = 1, thickness_method: str = "Harkins and Jura") -> dict[int, bytes]:
    langmuir = _block(312, 100)
    struct.pack_into("<dd", langmuir, len(langmuir) - 18, pressure_min, pressure_max)
    t_plot = _block(314, 208)
    struct.pack_into("<H", t_plot, 19, selector)
    _string(t_plot, 103, thickness_method)
    struct.pack_into("<dd", t_plot, 184, t_min_angstrom, t_max_angstrom)
    return {312: bytes(langmuir), 314: bytes(t_plot)}


def _container(measured: bytes) -> bytes:
    sample = _block(301, 300)
    struct.pack_into("<d", sample, 19, 2.0)
    _string(sample, 40, "Synthetic")
    _string(sample, 70, "Tester")
    properties = _block(320, 562)
    _string(properties, 26, "Nitrogen @ 77.35 K")
    _string(properties, 71, "N2")
    for offset, value in ((79, 760.0), (89, 0.000062), (97, 0.0015468), (105, 3.86), (113, 0.162)):
        struct.pack_into("<d", properties, offset, value)
    data = bytearray(32)
    data[2:11] = b"MIC##&&FS"
    data[12:16] = b"0001"
    directory_entries = []
    for subset_id, block in ((301, sample), (302, _method_block()), (303, measured), (320, properties), (705, _block(705, 64))):
        directory_entries.append((subset_id, len(data), len(block) - 18))
        data.extend(block)
    directory_offset = len(data)
    directory = _block(101, 21)
    for entry in directory_entries:
        directory.extend(struct.pack("<HII", *entry))
    directory.extend(bytes(10))
    data.extend(directory)
    struct.pack_into("<II", data, 24, directory_offset, len(directory))
    return bytes(data)


class TriStarPlusV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TriStarSmpParser()

    def test_legacy_name_is_recognized_without_matching_other_instruments(self) -> None:
        self.assertTrue(self.parser._is_tristar_plus_v3(_measured_block()))
        for software in ("ASAP 2460 Version 3.02", "TriStar II 3020 Version 3.02", "TriStar II Plus Version 3.03", "MicroActive for TriStar II Plus Version 1.01"):
            with self.subTest(software=software):
                self.assertFalse(self.parser._is_tristar_plus_v3(_measured_block(software=software)))

    def test_point_table_has_correct_quantity_branches_and_elapsed_seconds(self) -> None:
        block = _measured_block()
        free_space = FreeSpaceInfo(11.0, 31.0, 0.000062, 31.0, 11.0, -0.3, 20.0, 30.0, "synthetic")
        points = self.parser._parse_microactive_isotherm(block, len(block) - 18, _sample(), free_space)
        self.assertEqual(len(points), 3)
        self.assertEqual([point.phase for point in points], ["adsorption", "adsorption", "desorption"])
        self.assertEqual([point.elapsed_seconds for point in points], [5400, 10800, 14400])
        self.assertEqual([point.record_rel_offset for point in points], [326, 392, 458])
        for point, expected in zip(points, (2.0, 4.0, 3.0)):
            self.assertAlmostEqual(point.quantity_adsorbed_cm3_g_stp, expected, places=10)
            self.assertAlmostEqual(point.quantity_adsorbed_mmol_g, expected / 22.414, places=10)
            self.assertAlmostEqual(point.saturation_pressure_mmHg, 760.0, places=10)

    def test_missing_free_space_never_labels_raw_volume_as_corrected_quantity(self) -> None:
        block = _measured_block()
        unavailable = FreeSpaceInfo(None, None, None, None, None, None, None, None, "missing")
        points = self.parser._parse_microactive_isotherm(block, len(block) - 18, _sample(), unavailable)
        self.assertEqual(len(points), 3)
        self.assertTrue(all(point.quantity_adsorbed_cm3_g_stp is None for point in points))
        self.assertTrue(all(point.quantity_adsorbed_mmol_g is None for point in points))

    def test_negative_corrected_measurement_is_preserved(self) -> None:
        block = bytearray(_measured_block())
        struct.pack_into("<d", block, POINT_START + 16, 1.0)
        free_space = FreeSpaceInfo(11.0, 31.0, 0.000062, 31.0, 11.0, -0.3, 20.0, 30.0, "synthetic")
        points = self.parser._parse_microactive_isotherm(bytes(block), len(block) - 18, _sample(), free_space)
        self.assertEqual(len(points), 3)
        self.assertAlmostEqual(points[0].quantity_adsorbed_cm3_g_stp, -1.004712, places=10)

    def test_correction_comes_from_each_file(self) -> None:
        first = self.parser._parse_tristar_plus_v3_free_space(_measured_block(correction=-0.3), _run(), _properties())
        second = self.parser._parse_tristar_plus_v3_free_space(_measured_block(correction=-0.6), _run(), _properties())
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertAlmostEqual(first.stem_volume_cm3, -0.3)
        self.assertAlmostEqual(second.stem_volume_cm3, -0.6)
        self.assertAlmostEqual(first.cold_free_space_cm3, 30.7)
        self.assertAlmostEqual(first.warm_free_space_cm3, 10.7)
        self.assertAlmostEqual(second.cold_free_space_cm3, 30.4)
        self.assertAlmostEqual(second.warm_free_space_cm3, 10.4)
        self.assertAlmostEqual(first.vfree_factor_cm3 - second.vfree_factor_cm3, 0.3, places=5)
        self.assertAlmostEqual(first.vbath_cm3, second.vbath_cm3)
        self.assertEqual(first.vfree_factor_source, "tristar_ii_plus_v3_sample_tube_fields_empirical")

    def test_run_metadata_uses_the_variable_position_gas_section(self) -> None:
        for name, gas_offset in (("Method", 500), ("Longer method name", 6400)):
            with self.subTest(name=name, gas_offset=gas_offset):
                run = self.parser._parse_tristar_plus_v3_run_conditions(_method_block(name=name, gas_offset=gas_offset), _properties())
                self.assertEqual(run.adsorptive_short, "N2")
                self.assertIn("Nitrogen", run.adsorptive_name)
                self.assertAlmostEqual(run.bath_temperature_K, 77.3)
                self.assertAlmostEqual(run.po_reference_mmHg, 760.0)
                self.assertAlmostEqual(run.evacuation_rate_mmHg_s, 10.0)
                self.assertAlmostEqual(run.unrestricted_evacuate_from_mmHg, 5.0)
                self.assertAlmostEqual(run.evacuation_time_h, 0.5)
                self.assertEqual(run.leak_test_time_s, 120)
                self.assertAlmostEqual(run.equilibration_interval_s, 20.0)

    def test_missing_or_invalid_measurement_interval_does_not_use_the_earlier_10_seconds(self) -> None:
        for interval in (0.0, -1.0, math.nan, math.inf, 86401.0):
            with self.subTest(interval=interval):
                block = bytearray(_method_block())
                struct.pack_into("<d", block, 500 - 121, interval)
                run = self.parser._parse_tristar_plus_v3_run_conditions(bytes(block), _properties())
                self.assertIsNone(run.equilibration_interval_s)
        for block, properties in ((b"", _properties()), (_method_block(), None)):
            with self.subTest(block_length=len(block), properties=properties):
                run = self.parser._parse_tristar_plus_v3_run_conditions(block, properties)
                self.assertIsNone(run.equilibration_interval_s)

    def test_compact_sample_density_and_submitter_follow_variable_length_comment(self) -> None:
        for comment, density in (("", 1.0), ("80 degrees overnight, N2  ", 2.75), ("  样品备注\0\0", 0.75)):
            with self.subTest(comment=comment, density=density):
                block = _compact_sample_block(comment=comment, density=density)
                sample = self.parser._with_tristar_plus_v3_sample_info(_sample(), block)
                self.assertEqual(sample.submitter, "Submitter")
                self.assertEqual(sample.bar_code, "BAR-001")
                self.assertAlmostEqual(sample.sample_density_g_cm3, density)

    def test_invalid_or_incomplete_compact_sample_does_not_invent_density(self) -> None:
        blocks = [b"", _compact_sample_block(comment="comment")[:100],
                  _compact_sample_block(comment="comment", total_mass=30.0)]
        blocks.extend(_compact_sample_block(comment="comment", density=density)
                      for density in (0.0, -1.0, math.nan, math.inf, 1000.0))
        for block in blocks:
            with self.subTest(block_length=len(block), tail=block[250:290]):
                sample = self.parser._with_tristar_plus_v3_sample_info(_sample(), block)
                self.assertIsNone(sample.sample_density_g_cm3)

    def test_report_fit_ranges_convert_pressure_and_thickness_units(self) -> None:
        options = self.parser._parse_tristar_plus_v3_report_options(_report_blocks(), _run())
        self.assertTrue(options["use_stored_fit_ranges"])
        self.assertAlmostEqual(options["stored_langmuir_pressure_min"], 0.0)
        self.assertAlmostEqual(options["stored_langmuir_pressure_max"], 1.0)
        self.assertEqual(options["vendor_t_plot_thickness_method"], "harkins_jura")
        self.assertAlmostEqual(options["stored_t_plot_thickness_min_nm"], 0.35)
        self.assertAlmostEqual(options["stored_t_plot_thickness_max_nm"], 0.5)
        options = self.parser._parse_tristar_plus_v3_report_options(
            _report_blocks(pressure_min=76.0, pressure_max=304.0), replace(_run(), po_reference_mmHg=380.0))
        self.assertAlmostEqual(options["stored_langmuir_pressure_min"], 0.2)
        self.assertAlmostEqual(options["stored_langmuir_pressure_max"], 0.8)

    def test_missing_or_invalid_report_ranges_are_not_created(self) -> None:
        self.assertEqual(self.parser._parse_tristar_plus_v3_report_options({}, _run()),
                         {"use_stored_fit_ranges": True})
        self.assertEqual(self.parser._parse_tristar_plus_v3_report_options({312: b"", 314: b""}, _run()),
                         {"use_stored_fit_ranges": True})
        for lower, upper in ((-1.0, 760.0), (0.0, 761.0), (760.0, 0.0), (5.0, 5.0), (math.nan, 760.0), (0.0, math.inf)):
            with self.subTest(pressure_range=(lower, upper)):
                options = self.parser._parse_tristar_plus_v3_report_options(
                    _report_blocks(pressure_min=lower, pressure_max=upper), _run())
                self.assertNotIn("stored_langmuir_pressure_min", options)
                self.assertNotIn("stored_langmuir_pressure_max", options)
        options = self.parser._parse_tristar_plus_v3_report_options(
            _report_blocks(), replace(_run(), po_reference_mmHg=None))
        self.assertNotIn("stored_langmuir_pressure_min", options)
        for kwargs in ({"selector": 0}, {"selector": 2}, {"thickness_method": "Halsey"},
                       {"t_min_angstrom": 0.0}, {"t_min_angstrom": 6.0},
                       {"t_min_angstrom": math.nan}, {"t_max_angstrom": math.inf}):
            with self.subTest(thickness_options=kwargs):
                options = self.parser._parse_tristar_plus_v3_report_options(_report_blocks(**kwargs), _run())
                self.assertNotIn("vendor_t_plot_thickness_method", options)
                self.assertNotIn("stored_t_plot_thickness_min_nm", options)
                self.assertNotIn("stored_t_plot_thickness_max_nm", options)

    def test_unavailable_or_invalid_free_space_does_not_create_a_correction(self) -> None:
        for block, run, props in (
            (b"", _run(), _properties()),
            (_measured_block(correction=0.3), _run(), _properties()),
            (_measured_block(), replace(_run(), bath_temperature_K=None), _properties()),
            (_measured_block(), _run(), None),
        ):
            with self.subTest(block_length=len(block), bath=run.bath_temperature_K, props=props):
                free_space = self.parser._parse_tristar_plus_v3_free_space(block, run, props)
                self.assertIsNone(free_space.vfree_factor_cm3)
                self.assertIsNone(free_space.vbath_cm3)

    def test_old_microactive_elapsed_and_fallback_are_unchanged(self) -> None:
        block = _measured_block(software="MicroActive for TriStar II Plus Version 1.01")
        unavailable = FreeSpaceInfo(None, None, None, None, None, None, None, None, "missing")
        points = self.parser._parse_microactive_isotherm(block, len(block) - 18, _sample(), unavailable)
        self.assertEqual([point.elapsed_seconds for point in points], [90, 180, 240])
        for point in points:
            self.assertEqual(point.quantity_adsorbed_cm3_g_stp, point.raw_internal_cm3_stp / 2.0)

    def test_parse_container_preserves_software_and_exposes_measured_points(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.SMP"
            path.write_bytes(_container(_measured_block()))
            result = self.parser.parse(path)
        self.assertEqual(result.point_count, 3)
        self.assertEqual(result.sample_name, "Synthetic")
        self.assertEqual(result.method_options["instrument_model"], "TriStar II Plus")
        self.assertEqual(result.method_options["instrument_software"], SOFTWARE)
        self.assertEqual(result.method_options["format_family"], "TriStar II Plus 3.02 SMP")
        self.assertEqual(result.run_conditions.adsorptive_short, "N2")
        self.assertAlmostEqual(result.run_conditions.bath_temperature_K, 77.3)
        self.assertTrue(all(math.isfinite(point.quantity_adsorbed_cm3_g_stp) for point in result.isotherm))
        self.assertTrue(all(abs(point.saturation_pressure_mmHg - 760.0) < 1e-8 for point in result.isotherm))
        self.assertEqual(len(result.po_records), 3)
        self.assertEqual([po.index for po in result.po_records], [1, 2, 3])
        self.assertEqual([po.elapsed_seconds for po in result.po_records], [5400, 10800, 14400])
        self.assertTrue(all(abs(po.saturation_pressure_mmHg - 760.0) < 1e-8 for po in result.po_records))
        exported_rows = _isotherm_rows([result])
        self.assertEqual(len(exported_rows), 3)
        self.assertEqual([row["point_index"] for row in exported_rows], [1, 2, 3])
        self.assertNotIn("po_only", [row["phase"] for row in exported_rows])

    def test_missing_free_space_is_reported_in_full_parse_metadata(self) -> None:
        block = bytearray(_measured_block())
        block[TUBE_TEXT_OFFSET - 7 : TUBE_TEXT_OFFSET + 24] = bytes(31)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-free-space.SMP"
            path.write_bytes(_container(bytes(block)))
            result = self.parser.parse(path)
        self.assertEqual(result.point_count, 3)
        self.assertEqual(result.method_options["microactive_quantity_source"], "unavailable_missing_free_space_or_sample_mass")
        self.assertTrue(all(point.quantity_adsorbed_cm3_g_stp is None for point in result.isotherm))


class TriStarPlusV3AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-analysis.SMP"
            path.write_bytes(_container(_measured_block()))
            parsed = TriStarSmpParser().parse(path)
        points = []
        # Harkins-Jura thicknesses: the two valid measurements lie on a
        # line with external area -0.2 m2/g and micropore volume 0.00009.
        # The third measurement is inside the thickness window but negative.
        for index, thickness in enumerate((0.36, 0.40, 0.49), start=1):
            pressure = 10.0 ** (0.034 - 13.99 / (thickness * 10.0) ** 2)
            quantity = (0.00009 - 0.0002 * thickness) / density_conversion_factor(parsed)
            points.append(replace(
                parsed.isotherm[index - 1], index=index, phase="adsorption",
                relative_pressure=pressure, absolute_pressure_mmHg=pressure * 760.0,
                quantity_adsorbed_cm3_g_stp=quantity,
                quantity_adsorbed_mmol_g=quantity / 22.414,
            ))
        options = dict(parsed.method_options)
        options.update({
            "use_stored_fit_ranges": True,
            "stored_bet_pressure_min": 0.05,
            "stored_bet_pressure_max": 0.20,
            "stored_langmuir_pressure_min": 0.0,
            "stored_langmuir_pressure_max": 1.0,
            "stored_t_plot_thickness_min_nm": 0.35,
            "stored_t_plot_thickness_max_nm": 0.50,
            "vendor_t_plot_thickness_method": "harkins_jura",
        })
        self.result = replace(parsed, isotherm=points, method_options=options)

    def test_saved_two_point_bet_and_langmuir_fits_are_available(self) -> None:
        self.assertEqual(automatic_bet_range(self.result), (0.05, 0.20))
        self.assertEqual(automatic_langmuir_range(self.result), (0.0, 1.0))
        for fit in (bet_analysis(self.result), langmuir_analysis(self.result)):
            with self.subTest(fit=fit.name):
                self.assertEqual(fit.point_count, 2)
                self.assertIsNotNone(fit.surface_area_m2_g)
                self.assertEqual([row["point_index"] for row in fit.rows], [1, 2])
        # Two points determine a line, but cannot determine residual errors.
        fit = bet_analysis(self.result)
        self.assertIsNone(fit.slope_standard_error)
        self.assertIsNone(fit.surface_area_standard_error)

    def test_saved_thickness_fit_keeps_negative_external_area_and_skips_negative_measurements(self) -> None:
        fit = t_plot_analysis(self.result)
        self.assertEqual(fit.point_count, 2)
        self.assertEqual([row["point_index"] for row in fit.rows], [1, 2])
        self.assertAlmostEqual(fit.external_surface_area_m2_g, -0.2, places=10)
        self.assertAlmostEqual(fit.micropore_volume_cm3_g, 0.00009, places=12)
        self.assertEqual(analysis_bundle(self.result)["t-Plot"], fit)
        lo, hi = automatic_t_plot_pressure_range(self.result)
        self.assertAlmostEqual(lo, self.result.isotherm[0].relative_pressure)
        self.assertAlmostEqual(hi, self.result.isotherm[1].relative_pressure)

    def test_explicit_thickness_method_and_parameters_are_preserved(self) -> None:
        params = {"scale": 0.11}
        expected = t_plot_analysis_by_thickness(
            self.result, 0.35, 0.50, thickness_method="halsey", thickness_params=params,
        )
        actual = t_plot_analysis(self.result, thickness_method="halsey", thickness_params=params)
        self.assertEqual(actual, expected)
        self.assertNotEqual(actual.rows, t_plot_analysis(self.result).rows)

    def test_two_point_exception_does_not_apply_to_older_instruments(self) -> None:
        options = dict(self.result.method_options)
        options.pop("format_family")
        options["instrument_software"] = "MicroActive for TriStar II Plus Version 1.01"
        old = replace(self.result, method_options=options)
        for fit in (bet_analysis(old), langmuir_analysis(old), t_plot_analysis_by_thickness(old, 0.35, 0.50)):
            with self.subTest(fit=fit.name):
                self.assertEqual(fit.status, "not_enough_points")


if __name__ == "__main__":
    unittest.main()
