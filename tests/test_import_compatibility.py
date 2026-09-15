from __future__ import annotations

import math
import struct
import tempfile
import unittest
from pathlib import Path

from tristar_bet import (
    JwgbRawParseError,
    automatic_bet_range,
    bet_analysis,
    load_file,
    single_point_bet_analysis,
)
from tristar_bet.models import (
    AdsorptiveProperties,
    FreeSpaceInfo,
    IsothermPoint,
    RunConditions,
    SampleInfo,
    SmpHeader,
    TriStarResult,
)
from tristar_bet.smp import TriStarSmpParser


JWGB_RAW = """REPORT FOR STATION 2
SAMPLE ID K2861
ANALYSIS TIME 857.18 minutes
GASTYPE: Nitrogen
SAMPLE WEIGHT 0.1216
P/Po        VOLUME (cc)
3.07192e-07 1.53084 AP
0.0475277 13.919 AP
0.0919782 14.4086 MAP
0.970533 20.3729 ADPV
0.893722 20.088 DP
0.161159 15.1034 DP

ENDRUN: 2026-04-03
DATE: 2026-04-03
OPERATOR: Test Operator
USE THERMAL: 0
"""


def _mic_string(text: str, text_offset: int = 34) -> bytes:
    encoded = (text + "\0").encode("utf-16le")
    block = bytearray(text_offset + len(encoded) + 8)
    block[text_offset - 7 : text_offset] = b"\xe0\x01\x00" + struct.pack("<I", len(encoded))
    block[text_offset : text_offset + len(encoded)] = encoded
    return bytes(block)


def _mic_log_block(entries: list[tuple[int, tuple[int, int, int, int, int, int], str]]) -> bytes:
    block_size = max(offset + len((text + "\0").encode("utf-16le")) for offset, _parts, text in entries) + 8
    block = bytearray(block_size)
    for text_offset, (year, month, day, hour, minute, second), text in entries:
        encoded = (text + "\0").encode("utf-16le")
        struct.pack_into(
            "<9H",
            block,
            text_offset - 25,
            second,
            minute,
            hour,
            day,
            month - 1,
            year - 1900,
            0,
            0,
            0,
        )
        block[text_offset - 7 : text_offset] = b"\xe0\x01\x00" + struct.pack("<I", len(encoded))
        block[text_offset : text_offset + len(encoded)] = encoded
    return bytes(block)


class JwgbRawImportTests(unittest.TestCase):
    def test_dispatches_and_normalizes_raw_volume_by_sample_weight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.RAW"
            path.write_text(JWGB_RAW, encoding="ascii")
            result = load_file(path)

        self.assertEqual(result.method_options["format_family"], "JWGB APAS text RAW")
        self.assertEqual(result.method_options["instrument_model"], "APAS1000_ZQ")
        self.assertEqual(result.sample.sample_name, "K2861")
        self.assertEqual(result.sample.operator, "Test Operator")
        self.assertEqual(result.point_count, 6)
        self.assertEqual([point.phase for point in result.isotherm], ["adsorption"] * 4 + ["desorption"] * 2)
        self.assertTrue(
            math.isclose(
                result.isotherm[0].quantity_adsorbed_cm3_g_stp or 0.0,
                1.53084 / 0.1216,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )
        self.assertEqual(result.test_duration_seconds, 51431)
        self.assertEqual(result.run_conditions.adsorptive_short, "N2")

    def test_map_rows_are_the_official_raw_bet_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.raw"
            path.write_text(JWGB_RAW, encoding="ascii")
            result = load_file(path)

        self.assertEqual(result.method_options["jwgb_raw_bet_fit_point_ids"], [3])
        self.assertEqual(automatic_bet_range(result), (0.0919782, 0.0919782))
        fit = bet_analysis(result)
        self.assertEqual(fit.status, "not_enough_points")
        self.assertEqual(fit.point_count, 1)
        self.assertIsNone(fit.surface_area_m2_g)
        self.assertEqual([int(row["point_index"]) for row in fit.rows], [3])

    def test_jwgb_raw_single_point_bet_matches_k2861_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.raw"
            path.write_text(JWGB_RAW, encoding="ascii")
            result = load_file(path)

        self.assertEqual(result.method_options["jwgb_single_point_bet_pressure"], 0.05)
        self.assertEqual(result.method_options["jwgb_single_point_bet_source_point_id"], 2)
        fit = single_point_bet_analysis(result)
        self.assertEqual(fit.status, "ok")
        self.assertEqual(fit.point_count, 1)
        self.assertAlmostEqual(fit.pressure_min or 0.0, 0.05, places=12)
        self.assertAlmostEqual(fit.surface_area_m2_g or 0.0, 474.29168, delta=0.1)

    def test_default_raw_bet_uses_only_map_rows_not_intervening_ap_rows(self) -> None:
        raw = """REPORT FOR STATION 1
SAMPLE ID MAP selection
GASTYPE: Nitrogen
SAMPLE WEIGHT 1
P/Po VOLUME (cc)
0.05 20 MAP
0.10 21 AP
0.15 22 MAP
0.20 23 AP
0.25 24 MAP
ENDRUN: 2026-04-03
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.raw"
            path.write_text(raw, encoding="ascii")
            result = load_file(path)

        fit = bet_analysis(result)
        self.assertEqual(fit.point_count, 3)
        self.assertEqual([int(row["point_index"]) for row in fit.rows], [1, 3, 5])

    def test_rejects_an_unrelated_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.raw"
            path.write_bytes(b"not an APAS export")
            with self.assertRaises(JwgbRawParseError):
                load_file(path)


class LegacyAsap2020ImportTests(unittest.TestCase):
    def test_asap_2020_v4_software_name_is_recognized(self) -> None:
        parser = TriStarSmpParser()
        self.assertEqual(parser._asap_software_text(_mic_string("ASAP 2020 V4.04")), "ASAP 2020 V4.04")
        self.assertEqual(parser._asap_software_text(_mic_string("unrelated text")), "")

    def test_v4_short_analysis_log_events_supply_exact_start_and_last_completion(self) -> None:
        parser = TriStarSmpParser()
        log_block = _mic_log_block(
            [
                (50, (2026, 8, 11, 17, 26, 26), "Analysis started."),
                (125, (2026, 8, 12, 7, 6, 48), "Analysis done."),
                (195, (2026, 8, 12, 7, 6, 49), "Analysis done."),
            ]
        )
        header = SmpHeader("", "", 0, "", "", 0, "", 0, "", 0, 0)

        options = parser._parse_test_time_options(header, {"instrument_model": "ASAP 2020"}, log_block)

        self.assertEqual(options["test_started_time"], "2026-08-11 17:26:26")
        self.assertEqual(options["test_completed_time"], "2026-08-12 07:06:49")
        self.assertEqual(options["test_duration_seconds"], 49223)
        self.assertEqual(options["test_duration_time"], "13:40:23")
        self.assertEqual(options["test_time_source"], "SMP log Started event timestamp")
        self.assertEqual(options["test_completed_time_source"], "SMP log Finished/Done event timestamp")

    def test_invalid_new_layout_run_fields_are_removed(self) -> None:
        parser = TriStarSmpParser()
        run = RunConditions(5.0, 5.0, 0.5, 7864320, 0.0, 0.0, 1e-307, 1e-307, 1493172224, None, 77.35, "N2", "")
        props = AdsorptiveProperties("Nitrogen @ 77.35 K", "N2", 925.0, 123.3, 6.2e-5, 0.0015468, 3.86, 0.386, 0.162, 28.01)
        cleaned = parser._sanitize_legacy_asap_2020_run_conditions(run, props)
        self.assertEqual(cleaned.evacuation_rate_mmHg_s, 5.0)
        self.assertIsNone(cleaned.leak_test_time_s)
        self.assertIsNone(cleaned.equilibration_interval_s)
        self.assertIsNone(cleaned.ambient_free_space_entered_cm3)
        self.assertIsNone(cleaned.desorption_test_time_s)
        self.assertEqual(cleaned.adsorptive_name, "Nitrogen @ 77.35 K")

    def test_v4_measured_free_space_uses_legacy_asap_equations(self) -> None:
        parser = TriStarSmpParser()
        block = bytearray(360)
        for text_offset, text in ((34, "ASAP 2020 V4.04"), (300, "Sample Tube")):
            encoded = (text + "\0").encode("utf-16le")
            block[text_offset - 7 : text_offset] = b"\xe0\x01\x00" + struct.pack("<I", len(encoded))
            block[text_offset : text_offset + len(encoded)] = encoded
        struct.pack_into("<d", block, 276, 84.36249542236328)
        struct.pack_into("<d", block, 284, 27.518156051635742)
        run = RunConditions(None, None, None, None, None, None, None, None, None, None, 77.35, "N2", "Nitrogen")
        props = AdsorptiveProperties("Nitrogen", "N2", 925.0, 123.3, 6.2e-5, 0.0015468, 3.86, 0.386, 0.162, 28.01)

        free = parser._parse_asap_free_space(bytes(block), run, props)

        self.assertIsNotNone(free)
        assert free is not None
        self.assertAlmostEqual(free.vfree_factor_cm3 or 0.0, 84.36249542236328)
        self.assertAlmostEqual(
            free.vbath_cm3 or 0.0,
            (84.36249542236328 - 27.518156051635742) / (1.0 - 77.35 / 298.0),
        )
        self.assertEqual(free.vfree_factor_source, "asap_2020_v4_measured_free_space_fields")

    def test_v4_saved_two_point_bet_range_reproduces_microactive_reference(self) -> None:
        parser = TriStarSmpParser()
        sample = SampleInfo("ASAP reference", "", "", "", 0.0845, None)
        free = FreeSpaceInfo(
            84.36249542236328,
            27.518156051635742,
            6.2e-5,
            84.36249542236328,
            27.518156051635742,
            0.0,
            (84.36249542236328 - 27.518156051635742) / (1.0 - 77.35 / 298.0),
            84.36249542236328,
            "asap_2020_v4_measured_free_space_fields",
        )
        raw_rows = (
            (0.3200227916240692, 0.0004239682330935096, 6.412203311920166),
            (7.357598781585693, 0.009747781498860034, 7.682044506072998),
            (17.416303634643555, 0.023074442902312906, 8.948859214782715),
            (42.52552032470703, 0.05634110533504941, 11.896084785461426),
        )
        points = []
        for index, (absolute, relative, raw) in enumerate(raw_rows, 1):
            quantity = parser._calculate_quantity(absolute, raw, sample, free)
            points.append(IsothermPoint(index, "adsorption", index, absolute, relative, raw, absolute / relative, None, quantity, None))
        result = TriStarResult(
            SmpHeader("", "", 0, "", "", 0, "", 0, "", 0, 0),
            [],
            sample,
            RunConditions(None, None, None, None, None, None, None, None, None, None, 77.35, "N2", "Nitrogen"),
            [],
            free,
            [],
            points,
            AdsorptiveProperties("Nitrogen", "N2", 925.0, 123.3, 6.2e-5, 0.0015468, 3.86, 0.386, 0.162, 28.01),
            [],
            [],
            {
                "format_family": "ASAP 2020 V4 SMP",
                "instrument_model": "ASAP 2020",
                "instrument_software": "ASAP 2020 V4.04",
                "use_stored_fit_ranges": True,
                "stored_bet_pressure_min": 0.005157697747482876,
                "stored_bet_pressure_max": 0.05528205128205128,
            },
        )

        self.assertEqual(automatic_bet_range(result), (0.005157697747482876, 0.05528205128205128))
        fit = bet_analysis(result)
        self.assertEqual(fit.point_count, 2)
        self.assertEqual(fit.status, "ok")
        self.assertAlmostEqual(fit.surface_area_m2_g or 0.0, 354.9445, places=4)


if __name__ == "__main__":
    unittest.main()
