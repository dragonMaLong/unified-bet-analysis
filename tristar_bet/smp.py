from __future__ import annotations

import csv
import json
import math
import re
import struct
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None

from .models import (
    AdsorptiveProperties,
    FreeSpaceInfo,
    IsothermPoint,
    MicString,
    PoRecord,
    RunConditions,
    SampleInfo,
    SmpHeader,
    SubsetEntry,
    TargetPressureRow,
    TriStarResult,
)


MMHG_TO_KPA = 101.325 / 760.0
CM3_STP_PER_MMOL = 22.414
ASSUMED_AMBIENT_TEMPERATURE_K = 298.0

# Empirical reconstruction from current validated TriStar II 3020 samples.
# It reproduces the fitted free-space pressure factor within about 6e-6 cm3.
VFREE_INTERCEPT = -2.12304152e-05
VFREE_COLD_COEFF = 0.996624395
VFREE_WARM_COEFF = 0.00337683123
VFREE_STEM_COEFF = 0.999997685
MICROACTIVE_BET_FREE_SPACE_COLD_FRACTION = 1.0 / 24.0
MICROACTIVE_BJH_FREE_SPACE_COLD_FRACTION = 0.3362697292133636
MICROACTIVE_PORT_FREE_SPACE_OFFSETS_CM3 = {
    1: 0.72130228,
    2: 0.72695300,
    3: 0.74996550,
}
ASAP_2460_VFREE_COLD_COEFF = 0.997299507937274
ASAP_2460_VFREE_WARM_COEFF = 0.002700500767790325
ASAP_2460_VBATH_COLD_COEFF = 1.2827383463131363
ASAP_2460_VBATH_WARM_COEFF = -1.282742509264505
ASAP_2460_VBATH_INTERCEPT = 2.120842412745617e-05
ASAP_2020_PLUS_VFREE_COLD_COEFF = 0.9959455914168879
ASAP_2020_PLUS_VFREE_WARM_COEFF = 0.004054408583112112
ASAP_2020_PLUS_VBATH_DIFF_COEFF = 1.351469213988709
THREE_FLEX_MANUAL_DENSITY_CONVERSION_FACTOR = 0.00155
THREE_FLEX_MANUAL_VFREE_FACTOR_PER_AMBIENT_CM3 = 1.00536419
THREE_FLEX_MANUAL_PRESSURE_SQUARED_GAS_COEFF = -7.11189937e-09
THREE_FLEX_MANUAL_LIQUID_DISPLACEMENT_FACTOR = 3.70136440
THREE_FLEX_MANUAL_VBATH_FACTOR_PER_ANALYSIS_CM3 = 0.0


class TriStarParseError(ValueError):
    """Raised when a file is not a supported TriStar II 3020 SMP container."""


def load_smp(path: str | Path) -> TriStarResult:
    parser = TriStarSmpParser()
    return parser.parse(path)


def load_file(path: str | Path) -> TriStarResult:
    """Load any supported instrument file, dispatching on its extension.

    Micromeritics ``.smp`` containers go to :func:`load_smp`; MicrotracBEL
    BELMaster ``.dat`` exports go to :func:`tristar_bet.belmaster.load_dat`;
    Quantachrome Autosorb iQ ``.qps`` files go to
    :func:`tristar_bet.quantachrome.load_qps`; official Excel report
    workbooks go to :func:`tristar_bet.excel_import.load_excel`.
    """
    suffix = Path(path).suffix.lower()
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        from .excel_import import load_excel

        return load_excel(path)
    if suffix == ".dat":
        from .belmaster import load_dat

        return load_dat(path)
    if suffix == ".qps":
        from .quantachrome import load_qps

        return load_qps(path)
    return load_smp(path)


def load_many(paths: Iterable[str | Path]) -> list[TriStarResult]:
    return [load_file(path) for path in paths]


class TriStarSmpParser:
    def parse(self, path: str | Path) -> TriStarResult:
        file_path = Path(path)
        data = file_path.read_bytes()
        header = self._parse_header(file_path, data)
        subsets = self._parse_directory(data, header.directory_offset)
        blocks = {entry.subset_id: data[entry.offset : entry.offset + entry.total_size] for entry in subsets}

        sample = self._parse_sample_info(blocks.get(301, b""))
        run_conditions = self._parse_run_conditions(blocks.get(302, b""))
        target_pressure_table = self._parse_target_pressure_table(blocks.get(302, b""))
        adsorptive_properties = self._parse_adsorptive_properties(blocks.get(320, b""))
        free_space = self._parse_free_space(
            blocks.get(303, b""),
            self._payload_size(subsets, 303),
            run_conditions,
            adsorptive_properties,
            blocks.get(705, b""),
        )
        po_records = self._parse_po_records(blocks.get(303, b""), self._payload_size(subsets, 303))
        isotherm = self._parse_isotherm(blocks.get(303, b""), po_records, sample, free_space)
        used_microactive_point_table = False
        used_asap_point_table = False
        used_3flex_manual_point_table = False
        if not isotherm:
            isotherm = self._parse_asap_isotherm(blocks.get(303, b""), self._payload_size(subsets, 303), po_records, sample, free_space)
            used_asap_point_table = bool(isotherm)
        if not isotherm:
            isotherm = self._parse_microactive_isotherm(blocks.get(303, b""), self._payload_size(subsets, 303), sample, free_space)
            used_microactive_point_table = bool(isotherm)
        if not isotherm:
            manual_run_conditions = self._with_3flex_manual_run_conditions(run_conditions, blocks.get(302, b""))
            manual_adsorptive_properties = self._with_3flex_manual_adsorptive_properties(
                self._parse_3flex_adsorptive_properties(blocks.get(320, b"")) or adsorptive_properties
            )
            manual_free_space = self._parse_3flex_manual_free_space(
                blocks.get(303, b""),
                manual_run_conditions,
                manual_adsorptive_properties,
            )
            isotherm = self._parse_3flex_manual_isotherm(
                blocks.get(303, b""),
                sample,
                manual_free_space,
                manual_adsorptive_properties,
            )
            used_3flex_manual_point_table = bool(isotherm)
            if used_3flex_manual_point_table:
                run_conditions = self._with_3flex_manual_run_conditions(
                    manual_run_conditions,
                    blocks.get(302, b""),
                    manual_free_space,
                )
                adsorptive_properties = manual_adsorptive_properties
                free_space = manual_free_space
        method_options = self._parse_method_options(blocks.get(302, b""), blocks.get(725, b""), blocks.get(320, b""))
        method_options.update(self._parse_instrument_info(blocks))
        method_options.update(self._parse_stored_bet_range(blocks.get(311, b"")))
        method_options.update(self._parse_test_time_options(header, method_options, blocks.get(705, b"")))
        if used_asap_point_table:
            method_options["asap_quantity_source"] = "raw_cm3_stp_free_space_corrected_and_normalized"
            if free_space.vfree_factor_cm3 is not None:
                method_options["asap_vfree_factor_cm3"] = free_space.vfree_factor_cm3
            if free_space.vbath_cm3 is not None:
                method_options["asap_vbath_cm3"] = free_space.vbath_cm3
        if used_microactive_point_table:
            if free_space.vfree_factor_cm3 is not None:
                method_options["microactive_bet_vfree_factor_cm3"] = free_space.vfree_factor_cm3
            bjh_vfree = self._microactive_bjh_vfree_factor(free_space)
            if bjh_vfree is not None:
                method_options["microactive_bjh_vfree_factor_cm3"] = bjh_vfree
            method_options["microactive_quantity_source"] = (
                "raw_cm3_stp_free_space_corrected_and_normalized"
                if free_space.vfree_factor_cm3 is not None
                else "raw_cm3_stp_normalized_by_sample_mass"
                if sample.sample_mass_g
                else "raw_cm3_stp_unscaled_missing_sample_mass"
            )
        if used_3flex_manual_point_table:
            method_options["instrument_manufacturer"] = "Micromeritics"
            method_options["instrument_model"] = "3Flex 3500"
            method_options["3flex_quantity_source"] = (
                "manual_total_cm3_stp_free_space_corrected_and_normalized"
                if free_space.vfree_factor_cm3 is not None
                else "manual_total_cm3_stp_normalized_by_sample_mass"
            )
            method_options["3flex_point_table_source"] = "SMP subset 303 manual entered data"
            method_options["3flex_free_space_source"] = free_space.vfree_factor_source
            if free_space.ambient_entered_cm3 is not None:
                method_options["3flex_ambient_free_space_entered_cm3"] = free_space.ambient_entered_cm3
            if free_space.analysis_entered_cm3 is not None:
                method_options["3flex_analysis_free_space_entered_cm3"] = free_space.analysis_entered_cm3
            if free_space.vfree_factor_cm3 is not None:
                method_options["3flex_vfree_factor_cm3"] = free_space.vfree_factor_cm3
            if free_space.vbath_cm3 is not None:
                method_options["3flex_vbath_cm3"] = free_space.vbath_cm3
            if free_space.nonideality_factor is not None:
                method_options["3flex_nonideality_factor"] = free_space.nonideality_factor
            method_options["3flex_manual_density_conversion_factor"] = THREE_FLEX_MANUAL_DENSITY_CONVERSION_FACTOR
            method_options["3flex_liquid_displacement_factor"] = THREE_FLEX_MANUAL_LIQUID_DISPLACEMENT_FACTOR
            method_options["3flex_pressure_squared_gas_coeff"] = THREE_FLEX_MANUAL_PRESSURE_SQUARED_GAS_COEFF
            method_options["vendor_bjh_thickness_method"] = "harkins_jura"
            method_options["vendor_bjh_correction"] = "faas"
            method_options["vendor_bjh_smooth_derivative"] = False
            method_options["format_family"] = "3Flex manual SMP"
        if self._target_pressure_rows_valid(target_pressure_table):
            method_options["target_pressure_table_source"] = "method_file"
        else:
            target_pressure_table = self._target_pressure_table_from_isotherm(isotherm)
            if target_pressure_table:
                method_options["target_pressure_table_source"] = "measured_isotherm_pressure_sequence"
        log_messages = self._parse_log_messages(blocks.get(705, b""))
        sample_tube_strings = self._read_mic_strings(blocks.get(1021, b""))
        raw_strings = {
            subset_id: self._read_mic_strings(block)
            for subset_id, block in blocks.items()
            if subset_id in {301, 302, 303, 311, 312, 314, 315, 316, 320, 331, 332, 705, 1021}
        }

        return TriStarResult(
            header=header,
            subsets=subsets,
            sample=sample,
            run_conditions=run_conditions,
            target_pressure_table=target_pressure_table,
            free_space=free_space,
            po_records=po_records,
            isotherm=isotherm,
            adsorptive_properties=adsorptive_properties,
            log_messages=log_messages,
            sample_tube_strings=sample_tube_strings,
            method_options=method_options,
            raw_strings=raw_strings,
        )

    def _parse_header(self, path: Path, data: bytes) -> SmpHeader:
        if len(data) < 32 or data[2:11] != b"MIC##&&FS":
            raise TriStarParseError(f"Unsupported SMP container: {path}")

        version = data[12:16].decode("ascii", errors="replace")
        created_raw, modified_raw = struct.unpack_from("<II", data, 16)
        directory_offset, directory_size = struct.unpack_from("<II", data, 24)
        return SmpHeader(
            file_path=str(path.resolve()),
            file_name=path.name,
            byte_count=len(data),
            magic=data[2:11].decode("ascii", errors="replace"),
            version=version,
            created_raw=created_raw,
            created_time=_timestamp_text(created_raw),
            modified_raw=modified_raw,
            modified_time=_timestamp_text(modified_raw),
            directory_offset=directory_offset,
            directory_size=directory_size,
        )

    def _parse_directory(self, data: bytes, directory_offset: int) -> list[SubsetEntry]:
        if data[directory_offset : directory_offset + 9] != b"SUBSET101":
            raise TriStarParseError("SUBSET101 directory marker was not found.")

        for start in (directory_offset + 21, directory_offset + 20, directory_offset + 22):
            entries: list[SubsetEntry] = []
            pos = start
            while pos + 10 <= len(data):
                subset_id, offset, payload_size = struct.unpack_from("<HII", data, pos)
                if subset_id == 0 or offset == 0 or offset >= len(data) or payload_size > len(data):
                    break
                marker = _marker_text(data[offset : offset + 10])
                if not marker.startswith("SUBSET"):
                    break
                total_size = 18 + payload_size
                entries.append(
                    SubsetEntry(
                        subset_id=subset_id,
                        marker=marker,
                        offset=offset,
                        payload_size=payload_size,
                        total_size=total_size,
                    )
                )
                pos += 10
            if len(entries) >= 5:
                return entries

        raise TriStarParseError("Could not parse SUBSET101 directory entries.")

    def _payload_size(self, subsets: Sequence[SubsetEntry], subset_id: int) -> int:
        for entry in subsets:
            if entry.subset_id == subset_id:
                return entry.payload_size
        return 0

    def _parse_sample_info(self, block: bytes) -> SampleInfo:
        strings = self._read_mic_strings(block)
        first_label = next((item.rel_offset for item in strings if item.text == "Sample:"), 85)
        early_values = [item.text for item in strings if item.text and item.rel_offset < first_label]
        sample_name = early_values[0] if early_values else ""
        operator = early_values[1] if len(early_values) > 1 else ""
        mass = _read_double(block, 19)
        compact_mass = self._parse_compact_sample_mass(block, strings)
        if compact_mass is not None:
            mass = compact_mass
        if mass is not None and not (1e-8 < mass < 100.0):
            mass = None
        density = _read_double(block, 192)
        if density is not None and not (0.01 < density < 100.0):
            density = None

        return SampleInfo(
            sample_name=sample_name,
            operator=operator,
            submitter="",
            bar_code="",
            sample_mass_g=mass,
            sample_density_g_cm3=density,
        )

    def _parse_compact_sample_mass(self, block: bytes, strings: Sequence[MicString]) -> float | None:
        barcode_rel = next((item.rel_offset for item in strings if item.text == "Bar Code:"), None)
        start = (barcode_rel + 32) if barcode_rel is not None else 180
        end = min(len(block) - 8, start + 90)
        candidates: list[tuple[int, float]] = []
        for rel in range(max(0, start), max(0, end + 1)):
            value = _read_double(block, rel)
            if value is None or not (1e-8 < value < 100.0) or math.isclose(value, 1.0, abs_tol=1e-12):
                continue
            candidates.append((rel, value))
        for rel, value in candidates:
            following = _read_double(block, rel + 8)
            following_sum = _read_double(block, rel + 16)
            if (
                following is not None
                and math.isclose(following, 1.0, rel_tol=0.0, abs_tol=1e-12)
                and following_sum is not None
                and math.isclose(following_sum, value + 1.0, rel_tol=0.0, abs_tol=1e-8)
            ):
                return value
        return candidates[0][1] if candidates else None

    def _parse_run_conditions(self, block: bytes) -> RunConditions:
        strings = self._read_mic_strings(block)
        if len(block) <= 1000:
            adsorptive_short = _first_string_at_or_after(strings, 330, max_offset=345)
            adsorptive_name = _first_string_at_or_after(strings, 600, max_offset=635)
            return RunConditions(
                evacuation_rate_mmHg_s=_read_double(block, 61),
                unrestricted_evacuate_from_mmHg=_read_double(block, 69),
                evacuation_time_h=_read_double(block, 77),
                leak_test_time_s=None,
                equilibration_interval_s=_read_double(block, 93),
                free_space_equilibration_time_h=_read_double(block, 155),
                ambient_free_space_entered_cm3=_read_double(block, 173),
                analysis_free_space_entered_cm3=_read_double(block, 181),
                desorption_test_time_s=_int_from_double(block, 165),
                po_reference_mmHg=_read_double(block, 563),
                bath_temperature_K=_read_double(block, 571),
                adsorptive_short=adsorptive_short,
                adsorptive_name=adsorptive_name,
            )

        adsorptive_short = _first_string_at_or_after(strings, 2890, max_offset=2905)
        adsorptive_name = _first_string_at_or_after(strings, 2905, max_offset=2940)
        if not adsorptive_short:
            adsorptive_short = _first_string_at_or_after(strings, 330, max_offset=360)
        if not adsorptive_name:
            adsorptive_name = _first_string_at_or_after(strings, 4490, max_offset=4540)
        if "@" not in adsorptive_name:
            adsorptive_name = next((item.text for item in strings if "@" in item.text and "K" in item.text), adsorptive_name)
        bath_temperature = _read_double(block, 2881)
        if bath_temperature is None or not (50.0 < bath_temperature < 150.0):
            bath_temperature = _temperature_from_text(adsorptive_name)
        if bath_temperature is None and (adsorptive_short.upper() == "N2" or "NITROGEN" in adsorptive_name.upper()):
            bath_temperature = 77.35
        po_reference = _read_double(block, 2873)
        if po_reference is not None and not (100.0 < po_reference < 1000.0):
            po_reference = None

        return RunConditions(
            evacuation_rate_mmHg_s=_read_double(block, 61),
            unrestricted_evacuate_from_mmHg=_read_double(block, 69),
            evacuation_time_h=_read_double(block, 77),
            leak_test_time_s=_read_uint32(block, 87),
            equilibration_interval_s=_read_double(block, 91),
            free_space_equilibration_time_h=_read_double(block, 112),
            ambient_free_space_entered_cm3=_read_double(block, 120),
            analysis_free_space_entered_cm3=_read_double(block, 128),
            desorption_test_time_s=_read_uint32(block, 138),
            po_reference_mmHg=po_reference,
            bath_temperature_K=bath_temperature,
            adsorptive_short=adsorptive_short,
            adsorptive_name=adsorptive_name,
        )

    def _parse_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        for parser in (
            self._parse_tristar_3020_target_pressure_table,
            self._parse_microactive_target_pressure_table,
            self._parse_asap_target_pressure_table,
        ):
            rows = parser(block)
            if self._target_pressure_rows_valid(rows):
                return rows
        return []

    def _parse_asap_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        rows = self._parse_asap_2460_target_pressure_table(block)
        if self._target_pressure_rows_valid(rows):
            return rows
        rows = self._parse_asap_2020_target_pressure_table(block)
        if self._target_pressure_rows_valid(rows):
            return rows
        return []

    def _parse_asap_2460_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        rows: list[TargetPressureRow] = []
        previous_end = 0.0
        for rel in range(432, len(block) - 8, 66):
            ending = _read_double(block, rel)
            if not _valid_target_pressure(ending):
                if rows:
                    break
                continue
            starting = _read_double(block, rel + 21)
            increment = _read_double(block, rel + 29)
            ending = float(ending)
            start = float(starting) if _valid_target_pressure(starting) else previous_end
            if increment is not None and math.isfinite(float(increment)) and abs(float(increment)) > 1e-9:
                step = abs(float(increment)) if ending >= start else -abs(float(increment))
            else:
                step = ending - start
            rows.append(
                TargetPressureRow(
                    row=len(rows) + 1,
                    branch="adsorption" if ending >= start else "desorption",
                    starting_pressure_p_po=start,
                    ending_pressure_p_po=ending,
                    pressure_increment_p_po=step,
                    ending_pressure_rel_offset=rel,
                )
            )
            previous_end = ending
        return rows

    def _parse_asap_2020_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        if len(block) <= 1200:
            return []
        candidates = [
            (rel, float(value))
            for rel in range(350, len(block) - 8)
            if _valid_target_pressure(value := _read_double(block, rel))
        ]
        if len(candidates) < 3:
            return []

        best: list[tuple[int, float]] = []
        for start_index, (start_rel, start_value) in enumerate(candidates):
            if start_rel > 700:
                break
            sequence = [(start_rel, start_value)]
            current_rel = start_rel
            current_value = start_value
            search_index = start_index + 1
            while len(sequence) < 200:
                next_record: tuple[int, float] | None = None
                for index in range(search_index, len(candidates)):
                    rel, value = candidates[index]
                    if rel <= current_rel:
                        continue
                    if _pressures_close(value, current_value):
                        continue
                    has_previous_start = any(
                        rel < start_rel <= rel + 35 and _pressures_close(start_value, current_value)
                        for start_rel, start_value in candidates[index + 1 :]
                    )
                    if has_previous_start:
                        next_record = (rel, value)
                        search_index = index + 1
                        break
                if next_record is None:
                    break
                sequence.append(next_record)
                current_rel, current_value = next_record
            if len(sequence) > len(best):
                best = sequence

        endings = [(index + 1, value, rel) for index, (rel, value) in enumerate(best)]
        return self._target_pressure_rows_from_endings(endings)

    def _parse_tristar_3020_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        if len(block) <= 1000:
            return []

        endings: list[tuple[int, float, int]] = []
        for row in range(1, 56):
            if row <= 8:
                rel = 334 + (row - 1) * 47
            elif row <= 28:
                rel = 706 + (row - 9) * 43
            elif row == 29:
                rel = 1570
            else:
                rel = 1570 + (row - 29) * 43

            ending = _read_double(block, rel)
            if ending is None:
                continue
            endings.append((row, ending, rel))
        return self._target_pressure_rows_from_endings(endings)

    def _parse_microactive_target_pressure_table(self, block: bytes) -> list[TargetPressureRow]:
        if len(block) <= 1000:
            return []
        best: list[tuple[int, float, int]] = []
        for gap in range(48, 73):
            for start in range(400, min(540, len(block) - 8)):
                endings: list[tuple[int, float, int]] = []
                rel = start
                while rel + 8 <= len(block):
                    ending = _read_double(block, rel)
                    if not _valid_target_pressure(ending):
                        break
                    endings.append((len(endings) + 1, float(ending), rel))
                    rel += gap
                if len(endings) > len(best):
                    best = endings
        if len(best) < 60 or best[0][1] >= 1e-4:
            return []
        return self._target_pressure_rows_from_endings(best)

    @staticmethod
    def _target_pressure_rows_from_endings(endings: Sequence[tuple[int, float, int]]) -> list[TargetPressureRow]:
        values = [(row, float(ending), rel) for row, ending, rel in endings if _valid_target_pressure(ending)]
        if not values:
            return []
        peak_position = max(range(len(values)), key=lambda pos: values[pos][1])
        rows: list[TargetPressureRow] = []
        previous_end = 0.0
        for output_row, (_source_row, ending, rel) in enumerate(values, start=1):
            branch = "adsorption" if output_row - 1 <= peak_position else "desorption"
            rows.append(
                TargetPressureRow(
                    row=output_row,
                    branch=branch,
                    starting_pressure_p_po=previous_end,
                    ending_pressure_p_po=ending,
                    pressure_increment_p_po=ending - previous_end,
                    ending_pressure_rel_offset=rel,
                )
            )
            previous_end = ending
        return rows

    @staticmethod
    def _target_pressure_rows_valid(rows: Sequence[TargetPressureRow]) -> bool:
        if len(rows) < 3:
            return False
        endings = [float(row.ending_pressure_p_po) for row in rows]
        if any(not _valid_target_pressure(value) for value in endings):
            return False
        if max(endings) - min(endings) <= 1e-8:
            return False
        return True

    @staticmethod
    def _target_pressure_table_from_isotherm(isotherm: Sequence[IsothermPoint]) -> list[TargetPressureRow]:
        rows: list[TargetPressureRow] = []
        previous_end = 0.0
        for point in isotherm:
            ending = point.relative_pressure
            if not _valid_target_pressure(ending):
                continue
            ending = float(ending)
            rows.append(
                TargetPressureRow(
                    row=len(rows) + 1,
                    branch=point.phase,
                    starting_pressure_p_po=previous_end,
                    ending_pressure_p_po=ending,
                    pressure_increment_p_po=ending - previous_end,
                    ending_pressure_rel_offset=point.record_rel_offset,
                )
            )
            previous_end = ending
        return rows

    def _parse_free_space(
        self,
        block: bytes,
        payload_size: int,
        run: RunConditions,
        adsorptive_properties: AdsorptiveProperties | None = None,
        log_block: bytes = b"",
    ) -> FreeSpaceInfo:
        if not block or payload_size <= 0:
            return FreeSpaceInfo(None, None, None, None, None, None, None, None, "missing")

        asap_free_space = self._parse_asap_free_space(block, adsorptive_properties)
        if asap_free_space is not None:
            return asap_free_space

        microactive_free_space = self._parse_microactive_free_space(block, run, adsorptive_properties, log_block)
        if microactive_free_space is not None:
            return microactive_free_space

        if payload_size < 1200:
            return FreeSpaceInfo(
                analysis_entered_cm3=run.analysis_free_space_entered_cm3,
                ambient_entered_cm3=run.ambient_free_space_entered_cm3,
                nonideality_factor=_read_double(block, 504),
                cold_free_space_cm3=None,
                warm_free_space_cm3=None,
                stem_volume_cm3=None,
                vbath_cm3=None,
                vfree_factor_cm3=None,
                vfree_factor_source="method_file_not_analyzed",
            )

        analysis_entered = _read_double(block, payload_size - 509)
        ambient_entered = _read_double(block, payload_size - 501)
        nonideality = _read_double(block, payload_size - 493)
        cold = _read_double(block, payload_size - 485)
        warm = _read_double(block, payload_size - 477)
        stem = _read_double(block, payload_size - 243)

        vbath = None
        vfree = None
        source = "missing_inputs"
        bath_temperature = run.bath_temperature_K
        if None not in (cold, warm, stem, bath_temperature):
            denominator = 1.0 - float(bath_temperature) / ASSUMED_AMBIENT_TEMPERATURE_K
            if abs(denominator) > 1e-12:
                vbath = (float(cold) - float(warm)) / denominator
            vfree = (
                VFREE_INTERCEPT
                + VFREE_COLD_COEFF * float(cold)
                + VFREE_WARM_COEFF * float(warm)
                + VFREE_STEM_COEFF * float(stem)
            )
            source = "empirical_from_smp_free_space_fields"

        return FreeSpaceInfo(
            analysis_entered_cm3=analysis_entered,
            ambient_entered_cm3=ambient_entered,
            nonideality_factor=nonideality,
            cold_free_space_cm3=cold,
            warm_free_space_cm3=warm,
            stem_volume_cm3=stem,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source=source,
        )

    @staticmethod
    def _free_space_unavailable_for_3flex_manual_table() -> FreeSpaceInfo:
        return FreeSpaceInfo(
            analysis_entered_cm3=None,
            ambient_entered_cm3=None,
            nonideality_factor=None,
            cold_free_space_cm3=None,
            warm_free_space_cm3=None,
            stem_volume_cm3=None,
            vbath_cm3=None,
            vfree_factor_cm3=None,
            vfree_factor_source="3flex_manual_table_not_used_for_quantity",
        )

    @staticmethod
    def _with_3flex_manual_adsorptive_properties(
        adsorptive_properties: AdsorptiveProperties | None,
    ) -> AdsorptiveProperties | None:
        if adsorptive_properties is None:
            return None
        if (adsorptive_properties.mnemonic or adsorptive_properties.adsorptive).upper() != "N2":
            return adsorptive_properties
        return replace(
            adsorptive_properties,
            density_conversion_factor=THREE_FLEX_MANUAL_DENSITY_CONVERSION_FACTOR,
        )

    def _parse_3flex_manual_free_space(
        self,
        block: bytes,
        run: RunConditions,
        adsorptive_properties: AdsorptiveProperties | None,
    ) -> FreeSpaceInfo:
        sample_tube_rel = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if sample_tube_rel is None:
            return self._free_space_unavailable_for_3flex_manual_table()

        ambient = self._first_valid_volume(
            _read_double(block, sample_tube_rel - 24),
            _read_double(block, sample_tube_rel - 48),
        )
        analysis = self._first_valid_volume(
            _read_double(block, sample_tube_rel - 16),
            _read_double(block, sample_tube_rel - 40),
        )
        if ambient is None or analysis is None:
            return self._free_space_unavailable_for_3flex_manual_table()

        nonideality = self._first_valid_nonideality(
            _read_double(block, sample_tube_rel - 32),
            _read_double(block, sample_tube_rel + 163),
            adsorptive_properties.nonideality_factor if adsorptive_properties is not None else None,
        )
        if nonideality is None:
            nonideality = 0.0

        ambient_temperature = _read_double(block, sample_tube_rel - 77)
        if ambient_temperature is None or not (250.0 <= ambient_temperature <= 330.0):
            ambient_temperature = ASSUMED_AMBIENT_TEMPERATURE_K

        vfree = THREE_FLEX_MANUAL_VFREE_FACTOR_PER_AMBIENT_CM3 * ambient
        vbath = THREE_FLEX_MANUAL_VBATH_FACTOR_PER_ANALYSIS_CM3 * analysis
        return FreeSpaceInfo(
            analysis_entered_cm3=analysis,
            ambient_entered_cm3=ambient,
            nonideality_factor=nonideality,
            cold_free_space_cm3=analysis,
            warm_free_space_cm3=ambient,
            stem_volume_cm3=0.0,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source="3flex_flex_6_03_manual_ambient_plus_liquid_displacement",
            ambient_temperature_K_assumed=ambient_temperature,
        )

    @staticmethod
    def _first_valid_volume(*values: float | None) -> float | None:
        for value in values:
            if value is not None and 0.0 < value < 300.0:
                return float(value)
        return None

    @staticmethod
    def _first_valid_nonideality(*values: float | None) -> float | None:
        for value in values:
            if value is not None and math.isfinite(float(value)) and abs(float(value)) < 0.01:
                return float(value)
        return None

    def _parse_asap_free_space(
        self,
        block: bytes,
        adsorptive_properties: AdsorptiveProperties | None = None,
    ) -> FreeSpaceInfo | None:
        software = self._asap_software_text(block)
        if not software:
            return None
        sample_tube_rel = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if sample_tube_rel is None:
            return None

        cold = _read_double(block, sample_tube_rel - 24)
        warm = _read_double(block, sample_tube_rel - 16)
        if cold is None or warm is None or not (0.0 < float(warm) < float(cold) < 300.0):
            return None

        if "ASAP 2020 Plus" in software:
            vfree = ASAP_2020_PLUS_VFREE_COLD_COEFF * float(cold) + ASAP_2020_PLUS_VFREE_WARM_COEFF * float(warm)
            vbath = ASAP_2020_PLUS_VBATH_DIFF_COEFF * (float(cold) - float(warm))
            source = "asap_2020_plus_sample_tube_fields"
        else:
            vfree = ASAP_2460_VFREE_COLD_COEFF * float(cold) + ASAP_2460_VFREE_WARM_COEFF * float(warm)
            vbath = (
                ASAP_2460_VBATH_INTERCEPT
                + ASAP_2460_VBATH_COLD_COEFF * float(cold)
                + ASAP_2460_VBATH_WARM_COEFF * float(warm)
            )
            source = "asap_2460_sample_tube_fields"

        nonideality = (
            float(adsorptive_properties.nonideality_factor)
            if adsorptive_properties is not None and adsorptive_properties.nonideality_factor is not None
            else 0.0
        )
        return FreeSpaceInfo(
            analysis_entered_cm3=float(cold),
            ambient_entered_cm3=float(warm),
            nonideality_factor=nonideality,
            cold_free_space_cm3=float(cold),
            warm_free_space_cm3=float(warm),
            stem_volume_cm3=0.0,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source=source,
        )

    def _asap_software_text(self, block: bytes) -> str:
        for item in self._read_mic_strings(block):
            if "ASAP 2460 Version" in item.text or "ASAP 2020 Plus Version" in item.text:
                return item.text
        return ""

    def _parse_microactive_free_space(
        self,
        block: bytes,
        run: RunConditions,
        adsorptive_properties: AdsorptiveProperties | None = None,
        log_block: bytes = b"",
    ) -> FreeSpaceInfo | None:
        strings = self._read_mic_strings(block)
        if not any("MicroActive for TriStar II Plus" in item.text for item in strings):
            return None
        sample_tube_rel = next((item.rel_offset for item in strings if item.text == "Sample Tube"), None)
        if sample_tube_rel is None:
            return None

        warm = _read_double(block, sample_tube_rel - 24)
        cold = _read_double(block, sample_tube_rel - 16)
        if warm is None or cold is None or not (0.0 < warm < 200.0 and 0.0 < cold < 200.0):
            return None

        ambient_temperature = _read_double(block, sample_tube_rel - 57) or ASSUMED_AMBIENT_TEMPERATURE_K
        if not (250.0 <= ambient_temperature <= 330.0):
            ambient_temperature = ASSUMED_AMBIENT_TEMPERATURE_K
        bath_temperature = run.bath_temperature_K or 77.350
        if not (50.0 < float(bath_temperature) < 150.0):
            bath_temperature = 77.350

        denominator = 1.0 - float(bath_temperature) / float(ambient_temperature)
        vbath = (float(warm) - float(cold)) / denominator if abs(denominator) > 1e-12 else 0.0
        nonideality = (
            float(adsorptive_properties.nonideality_factor)
            if adsorptive_properties is not None and adsorptive_properties.nonideality_factor is not None
            else 0.0
        )

        port = self._parse_analysis_port(log_block)
        port_offset = MICROACTIVE_PORT_FREE_SPACE_OFFSETS_CM3.get(port or 0)
        if port_offset is not None:
            log_warm = float(cold) - port_offset
            log_cold = float(warm) - port_offset
            vfree = VFREE_INTERCEPT + VFREE_COLD_COEFF * log_cold + VFREE_WARM_COEFF * log_warm
            source = "microactive_port_corrected_free_space_fields"
        else:
            log_warm, log_cold = self._parse_measured_free_space_log(log_block)
            if log_warm is not None and log_cold is not None:
                vfree = VFREE_INTERCEPT + VFREE_COLD_COEFF * float(log_cold) + VFREE_WARM_COEFF * float(log_warm)
                source = "microactive_log_free_space_fields"
            else:
                vfree = float(warm) - (float(warm) - float(cold)) * MICROACTIVE_BET_FREE_SPACE_COLD_FRACTION
                source = "microactive_sample_tube_bet_fields"
        if not (0.0 < vfree < 200.0):
            return None

        return FreeSpaceInfo(
            analysis_entered_cm3=float(cold),
            ambient_entered_cm3=float(warm),
            nonideality_factor=nonideality,
            cold_free_space_cm3=float(cold),
            warm_free_space_cm3=float(warm),
            stem_volume_cm3=0.0,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source=source,
            ambient_temperature_K_assumed=float(ambient_temperature),
        )

    def _parse_analysis_port(self, log_block: bytes) -> int | None:
        for item in self._parse_log_messages(log_block):
            match = re.search(r"\bport\s+([0-9]+)\b", item.text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def _parse_measured_free_space_log(self, log_block: bytes) -> tuple[float | None, float | None]:
        for item in self._parse_log_messages(log_block):
            match = re.search(
                r"Measured free space .*Warm:\s*([0-9]+(?:\.[0-9]+)?)\s*cm.\s*,\s*Cold:\s*([0-9]+(?:\.[0-9]+)?)\s*cm",
                item.text,
                flags=re.IGNORECASE,
            )
            if match:
                return float(match.group(1)), float(match.group(2))
        return None, None

    @staticmethod
    def _microactive_bjh_vfree_factor(free_space: FreeSpaceInfo) -> float | None:
        warm = free_space.ambient_entered_cm3
        cold = free_space.analysis_entered_cm3
        if warm is None or cold is None or not (0.0 < float(cold) < float(warm) < 200.0):
            return None
        vfree = float(warm) - (float(warm) - float(cold)) * MICROACTIVE_BJH_FREE_SPACE_COLD_FRACTION
        return vfree if 0.0 < vfree < 200.0 else None

    def _parse_po_records(self, block: bytes, payload_size: int) -> list[PoRecord]:
        if not block or payload_size <= 0:
            return []
        tail_start = payload_size - 509
        best: list[PoRecord] = []
        for rel in range(1500, max(1500, tail_start - 8)):
            records: list[PoRecord] = []
            previous_elapsed = -1
            cursor = rel
            while cursor + 10 <= tail_start:
                saturation = _read_double(block, cursor)
                elapsed = _read_uint16(block, cursor + 8)
                if (
                    saturation is None
                    or elapsed is None
                    or not (500.0 < saturation < 900.0)
                    or elapsed < previous_elapsed
                ):
                    break
                records.append(
                    PoRecord(
                        index=len(records),
                        rel_offset=cursor,
                        saturation_pressure_mmHg=saturation,
                        elapsed_seconds=elapsed,
                    )
                )
                previous_elapsed = elapsed
                cursor += 10
            if len(records) > len(best):
                best = records
        return best

    def _parse_isotherm(
        self,
        block: bytes,
        po_records: Sequence[PoRecord],
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
    ) -> list[IsothermPoint]:
        if not block or len(po_records) < 2:
            return []

        record_offsets = self._scan_isotherm_record_offsets(block, po_records)
        raw_points: list[tuple[int, float, float, float]] = []
        for rel in record_offsets:
            absolute, relative, raw_internal = struct.unpack_from("<ddd", block, rel)
            raw_points.append((rel, absolute, relative, raw_internal))

        max_index = max(range(len(raw_points)), key=lambda idx: raw_points[idx][2]) if raw_points else -1
        points: list[IsothermPoint] = []
        for idx, (rel, absolute, relative, raw_internal) in enumerate(raw_points, start=1):
            phase = "adsorption" if idx - 1 <= max_index else "desorption"
            po = po_records[idx] if idx < len(po_records) else None
            quantity = self._calculate_quantity(absolute, raw_internal, sample, free_space)
            points.append(
                IsothermPoint(
                    index=idx,
                    phase=phase,
                    record_rel_offset=rel,
                    absolute_pressure_mmHg=absolute,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=raw_internal,
                    saturation_pressure_mmHg=po.saturation_pressure_mmHg if po else None,
                    elapsed_seconds=po.elapsed_seconds if po else None,
                    quantity_adsorbed_cm3_g_stp=quantity,
                    quantity_adsorbed_mmol_g=(quantity / CM3_STP_PER_MMOL if quantity is not None else None),
                )
            )
        return points

    def _parse_microactive_isotherm(
        self,
        block: bytes,
        payload_size: int,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
    ) -> list[IsothermPoint]:
        if not block or payload_size <= 0:
            return []
        strings = self._read_mic_strings(block)
        if not any("MicroActive for TriStar II Plus" in item.text for item in strings):
            return []

        rows = self._scan_microactive_point_rows(block, payload_size)
        if len(rows) < 3:
            return []

        max_relative = max(row[2] for row in rows)
        max_index = max(
            idx
            for idx, row in enumerate(rows)
            if math.isclose(row[2], max_relative, rel_tol=0.0, abs_tol=1e-12)
        )
        points: list[IsothermPoint] = []
        sample_mass = float(sample.sample_mass_g) if sample.sample_mass_g and sample.sample_mass_g > 0.0 else None
        for idx, (rel, absolute, relative, quantity) in enumerate(rows, start=1):
            phase = "adsorption" if idx - 1 <= max_index else "desorption"
            saturation_pressure = absolute / relative if relative else None
            elapsed = _read_uint32(block, rel + 24)
            if elapsed is not None and not (0 <= elapsed <= 60 * 60 * 24 * 30):
                elapsed = None
            quantity_per_g = self._calculate_quantity(absolute, quantity, sample, free_space)
            if quantity_per_g is None:
                quantity_per_g = quantity / sample_mass if sample_mass else quantity
            points.append(
                IsothermPoint(
                    index=idx,
                    phase=phase,
                    record_rel_offset=rel,
                    absolute_pressure_mmHg=absolute,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=quantity,
                    saturation_pressure_mmHg=saturation_pressure,
                    elapsed_seconds=elapsed,
                    quantity_adsorbed_cm3_g_stp=quantity_per_g,
                    quantity_adsorbed_mmol_g=quantity_per_g / CM3_STP_PER_MMOL,
                )
            )
        return points

    def _parse_3flex_manual_isotherm(
        self,
        block: bytes,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
        adsorptive_properties: AdsorptiveProperties | None,
    ) -> list[IsothermPoint]:
        if len(block) < 240:
            return []
        point_count = _read_uint32(block, 128)
        if point_count is None or not (3 <= point_count <= 1000):
            return []
        record_start = 209
        record_stride = 66
        if record_start + record_stride * (point_count - 1) + 24 > len(block):
            return []

        rows: list[tuple[int, float, float, float]] = []
        for index in range(point_count):
            rel_offset = record_start + record_stride * index
            absolute = _read_double(block, rel_offset)
            relative = _read_double(block, rel_offset + 8)
            total_volume = _read_double(block, rel_offset + 16)
            if (
                absolute is None
                or relative is None
                or total_volume is None
                or not (0.0 < absolute <= 900.0)
                or not (1e-10 <= relative <= 1.1)
                or not (0.0 <= total_volume <= 10000.0)
            ):
                return []
            rows.append((rel_offset, absolute, relative, total_volume))

        max_relative = max(row[2] for row in rows)
        max_index = max(
            idx
            for idx, row in enumerate(rows)
            if math.isclose(row[2], max_relative, rel_tol=0.0, abs_tol=1e-12)
        )
        sample_mass = float(sample.sample_mass_g) if sample.sample_mass_g and sample.sample_mass_g > 0.0 else None
        points: list[IsothermPoint] = []
        for idx, (rel_offset, absolute, relative, total_volume) in enumerate(rows, start=1):
            phase = "adsorption" if idx - 1 <= max_index else "desorption"
            corrected_quantity = self._calculate_3flex_manual_quantity(
                absolute,
                total_volume,
                sample,
                free_space,
                adsorptive_properties,
            )
            quantity_per_g = corrected_quantity if corrected_quantity is not None else (total_volume / sample_mass if sample_mass else total_volume)
            saturation_pressure = absolute / relative if relative else None
            points.append(
                IsothermPoint(
                    index=idx,
                    phase=phase,
                    record_rel_offset=rel_offset,
                    absolute_pressure_mmHg=absolute,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=total_volume,
                    saturation_pressure_mmHg=saturation_pressure,
                    elapsed_seconds=None,
                    quantity_adsorbed_cm3_g_stp=quantity_per_g,
                    quantity_adsorbed_mmol_g=quantity_per_g / CM3_STP_PER_MMOL,
                )
            )
        return points

    @staticmethod
    def _with_3flex_manual_run_conditions(
        run_conditions: RunConditions,
        method_block: bytes,
        free_space: FreeSpaceInfo | None = None,
    ) -> RunConditions:
        bath_temperature = _read_double(method_block, 653)
        if bath_temperature is None or not (50.0 < bath_temperature < 150.0):
            bath_temperature = 77.3
        return replace(
            run_conditions,
            bath_temperature_K=bath_temperature,
            adsorptive_short="N2",
            adsorptive_name="Nitrogen",
            ambient_free_space_entered_cm3=(
                free_space.ambient_entered_cm3
                if free_space is not None and free_space.ambient_entered_cm3 is not None
                else run_conditions.ambient_free_space_entered_cm3
            ),
            analysis_free_space_entered_cm3=(
                free_space.analysis_entered_cm3
                if free_space is not None and free_space.analysis_entered_cm3 is not None
                else run_conditions.analysis_free_space_entered_cm3
            ),
        )

    @staticmethod
    def _parse_3flex_adsorptive_properties(adsorptive_block: bytes) -> AdsorptiveProperties | None:
        if not adsorptive_block:
            return None
        density_factor = _read_double(adsorptive_block, 77)
        hard_sphere_A = _read_double(adsorptive_block, 85)
        cross_section = _read_double(adsorptive_block, 93)
        molecular_weight = _read_double(adsorptive_block, 448)
        nonideality = _read_double(adsorptive_block, 69)
        if nonideality is not None and not (0.0 <= abs(nonideality) < 0.01):
            nonideality = None
        if density_factor is None or not (0.0 < density_factor < 0.01):
            return None
        return AdsorptiveProperties(
            adsorptive="Nitrogen",
            mnemonic="N2",
            max_manifold_pressure_mmHg=None,
            max_manifold_pressure_kPa=None,
            nonideality_factor=nonideality,
            density_conversion_factor=density_factor,
            thermal_transpiration_hard_sphere_A=hard_sphere_A,
            thermal_transpiration_hard_sphere_nm=hard_sphere_A / 10.0 if hard_sphere_A is not None else None,
            molecular_cross_sectional_area_nm2=cross_section,
            ui_field_rel101=molecular_weight,
            psat_table=[],
        )

    def _parse_asap_isotherm(
        self,
        block: bytes,
        payload_size: int,
        po_records: Sequence[PoRecord],
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
    ) -> list[IsothermPoint]:
        if not block or payload_size <= 0 or not self._asap_software_text(block):
            return []

        rows = self._scan_asap_point_rows(block)
        if len(rows) < 3:
            return []

        max_index = max(range(len(rows)), key=lambda idx: rows[idx][2])
        points: list[IsothermPoint] = []
        for idx, (rel_offset, absolute, relative, raw_internal) in enumerate(rows, start=1):
            phase = "adsorption" if idx - 1 <= max_index else "desorption"
            saturation_pressure = absolute / relative if relative else None
            elapsed = None
            if idx < len(po_records):
                po = po_records[idx]
                if saturation_pressure is None or abs(po.saturation_pressure_mmHg - saturation_pressure) < 5.0:
                    elapsed = po.elapsed_seconds
            quantity = self._calculate_quantity(absolute, raw_internal, sample, free_space)
            points.append(
                IsothermPoint(
                    index=idx,
                    phase=phase,
                    record_rel_offset=rel_offset,
                    absolute_pressure_mmHg=absolute,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=raw_internal,
                    saturation_pressure_mmHg=saturation_pressure,
                    elapsed_seconds=elapsed,
                    quantity_adsorbed_cm3_g_stp=quantity,
                    quantity_adsorbed_mmol_g=(quantity / CM3_STP_PER_MMOL if quantity is not None else None),
                )
            )
        return points

    def _scan_asap_point_rows(self, block: bytes) -> list[tuple[int, float, float, float]]:
        candidates: list[tuple[int, float, float, float]] = []
        for rel in range(0, len(block) - 24):
            absolute, relative, raw_internal = struct.unpack_from("<ddd", block, rel)
            if not (
                math.isfinite(absolute)
                and math.isfinite(relative)
                and math.isfinite(raw_internal)
                and 0.0001 <= absolute <= 800.0
                and 1e-9 <= relative <= 1.1
                and 0.0 <= raw_internal <= 1000.0
            ):
                continue
            saturation = absolute / relative
            if 700.0 <= saturation <= 800.0:
                candidates.append((rel, absolute, relative, raw_internal))

        if len(candidates) < 3:
            return []

        by_offset = {row[0]: row for row in candidates}
        best: list[tuple[int, float, float, float]] = []
        for row in candidates:
            if row[0] - 66 in by_offset:
                continue
            sequence = []
            rel = row[0]
            while rel in by_offset:
                sequence.append(by_offset[rel])
                rel += 66
            if len(sequence) > len(best):
                best = sequence
        return best if len(best) >= 3 else candidates

    def _scan_microactive_point_rows(self, block: bytes, payload_size: int) -> list[tuple[int, float, float, float]]:
        limit = min(len(block), payload_size + 18)
        best: list[tuple[int, float, float, float]] = []
        for gap in range(40, 91):
            start_limit = min(900, max(101, limit - 24))
            for start in range(100, start_limit):
                rows: list[tuple[int, float, float, float]] = []
                rel = start
                while rel + 28 <= limit:
                    absolute, relative, quantity = struct.unpack_from("<ddd", block, rel)
                    if not self._is_valid_microactive_point(absolute, relative, quantity):
                        break
                    rows.append((rel, absolute, relative, quantity))
                    rel += gap
                if len(rows) > len(best):
                    best = rows
        return best

    @staticmethod
    def _is_valid_microactive_point(absolute: float, relative: float, quantity: float) -> bool:
        if not (math.isfinite(absolute) and math.isfinite(relative) and math.isfinite(quantity)):
            return False
        if not (0.001 < absolute < 1200.0 and 1e-8 < relative < 1.2 and -1000.0 < quantity < 100000.0):
            return False
        saturation = absolute / relative
        return 100.0 < saturation < 1000.0

    def _scan_isotherm_record_offsets(self, block: bytes, po_records: Sequence[PoRecord]) -> list[int]:
        offsets: list[int] = []
        rel = 136
        expected_points = max(0, len(po_records) - 1)
        for idx in range(expected_points):
            po_value = po_records[idx + 1].saturation_pressure_mmHg
            if idx == 0:
                if not self._is_valid_point_at(block, rel, po_value):
                    found = next((candidate for candidate in range(100, 180) if self._is_valid_point_at(block, candidate, po_value)), None)
                    if found is None:
                        break
                    rel = found
            else:
                found = None
                for gap in (49, 47, 45, 43):
                    candidate = rel + gap
                    if self._is_valid_point_at(block, candidate, po_value):
                        found = candidate
                        break
                if found is None:
                    found = next(
                        (candidate for candidate in range(rel + 35, rel + 70) if self._is_valid_point_at(block, candidate, po_value)),
                        None,
                    )
                if found is None:
                    break
                rel = found
            offsets.append(rel)
        return offsets

    def _is_valid_point_at(self, block: bytes, rel: int, saturation_pressure: float) -> bool:
        if rel < 0 or rel + 24 > len(block):
            return False
        absolute, relative, raw_internal = struct.unpack_from("<ddd", block, rel)
        if not (math.isfinite(absolute) and math.isfinite(relative) and math.isfinite(raw_internal)):
            return False
        if not (1.0 < absolute < 1200.0 and 0.001 < relative < 1.2 and -10000.0 < raw_internal < 10000.0):
            return False
        return abs(absolute / saturation_pressure - relative) < 2e-5

    def _calculate_3flex_manual_quantity(
        self,
        absolute_pressure: float,
        raw_internal: float,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
        adsorptive_properties: AdsorptiveProperties | None,
    ) -> float | None:
        if sample.sample_mass_g is None or free_space.vfree_factor_cm3 is None:
            return None
        sample_mass = float(sample.sample_mass_g)
        if sample_mass <= 0.0:
            return None
        density_factor = THREE_FLEX_MANUAL_DENSITY_CONVERSION_FACTOR
        if adsorptive_properties and adsorptive_properties.density_conversion_factor:
            density_factor = float(adsorptive_properties.density_conversion_factor)
        pressure_atm = float(absolute_pressure) / 760.0
        gas_without_liquid = (
            pressure_atm * float(free_space.vfree_factor_cm3)
            + float(absolute_pressure) * float(absolute_pressure) * THREE_FLEX_MANUAL_PRESSURE_SQUARED_GAS_COEFF
        )
        liquid_displacement = pressure_atm * THREE_FLEX_MANUAL_LIQUID_DISPLACEMENT_FACTOR * density_factor
        denominator = sample_mass * (1.0 - liquid_displacement)
        if abs(denominator) < 1e-15:
            return None
        return (float(raw_internal) - gas_without_liquid) / denominator

    def _calculate_quantity(
        self,
        absolute_pressure: float,
        raw_internal: float,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
    ) -> float | None:
        if None in (
            sample.sample_mass_g,
            free_space.vfree_factor_cm3,
            free_space.vbath_cm3,
            free_space.nonideality_factor,
        ):
            return None
        if not sample.sample_mass_g:
            return None
        pressure_atm = absolute_pressure / 760.0
        gas_in_free_space = (
            pressure_atm * float(free_space.vfree_factor_cm3)
            + pressure_atm * absolute_pressure * float(free_space.nonideality_factor) * float(free_space.vbath_cm3)
        )
        return (raw_internal - gas_in_free_space) / float(sample.sample_mass_g)

    def _parse_adsorptive_properties(self, block: bytes) -> AdsorptiveProperties | None:
        if not block:
            return None
        strings = self._read_mic_strings(block)
        has_long_mnemonic_slot = any(item.rel_offset == 71 for item in strings)
        if len(block) > 600 or has_long_mnemonic_slot:
            name_rel = 26
            mnemonic_rel = 71
            max_rel = 79
            nonideal_rel = 89
            density_rel = 97
            hard_sphere_rel = 105
            cross_section_rel = 113
            molecular_or_ui_rel = 121
            psat_start = 142
        else:
            name_rel = 26
            mnemonic_rel = 51
            max_rel = 59
            nonideal_rel = 69
            density_rel = 77
            hard_sphere_rel = 85
            cross_section_rel = 93
            molecular_or_ui_rel = 101
            psat_start = 132

        max_manifold = _read_double(block, max_rel)
        hard_sphere_a = _read_double(block, hard_sphere_rel)
        psat_table: list[dict[str, float | int]] = []
        if len(block) > 600:
            count = _read_uint32(block, 138) or 0
            rel = psat_start
            for table_index in range(count):
                pressure = _read_double(block, rel)
                temperature = _read_double(block, rel + 8)
                if pressure is None or temperature is None or not (50.0 < temperature < 150.0 and 100.0 < pressure < 1500.0):
                    break
                psat_table.append(
                    {
                        "table_index": table_index,
                        "record_rel": rel,
                        "code_uint16": "",
                        "saturation_pressure_mmHg": pressure,
                        "saturation_pressure_kPa": pressure * MMHG_TO_KPA,
                        "temperature_K": temperature,
                    }
                )
                rel += 16
        else:
            rel = psat_start
            table_index = 0
            while rel + 18 <= len(block):
                code = _read_uint16(block, rel)
                pressure = _read_double(block, rel + 2)
                temperature = _read_double(block, rel + 10)
                if code != 2 or pressure is None or temperature is None or not (50.0 < temperature < 150.0 and 100.0 < pressure < 1500.0):
                    break
                psat_table.append(
                    {
                        "table_index": table_index,
                        "record_rel": rel,
                        "code_uint16": code,
                        "saturation_pressure_mmHg": pressure,
                        "saturation_pressure_kPa": pressure * MMHG_TO_KPA,
                        "temperature_K": temperature,
                    }
                )
                rel += 18
                table_index += 1

        return AdsorptiveProperties(
            adsorptive=_first_string_at_or_after(strings, name_rel, max_offset=name_rel + 45),
            mnemonic=_first_string_at_or_after(strings, mnemonic_rel, max_offset=mnemonic_rel + 12),
            max_manifold_pressure_mmHg=max_manifold,
            max_manifold_pressure_kPa=(max_manifold * MMHG_TO_KPA if max_manifold is not None else None),
            nonideality_factor=_read_double(block, nonideal_rel),
            density_conversion_factor=_read_double(block, density_rel),
            thermal_transpiration_hard_sphere_A=hard_sphere_a,
            thermal_transpiration_hard_sphere_nm=(hard_sphere_a / 10.0 if hard_sphere_a is not None else None),
            molecular_cross_sectional_area_nm2=_read_double(block, cross_section_rel),
            ui_field_rel101=_read_double(block, molecular_or_ui_rel),
            psat_table=psat_table,
        )

    def _parse_method_options(self, run_block: bytes, degas_block: bytes, adsorptive_block: bytes) -> dict[str, object]:
        options: dict[str, object] = {}
        if run_block and len(run_block) <= 1000:
            options.update(
                {
                    "format_family": "TriStar II Plus 3.04 method/unanalysed SMP",
                    "free_space_mode_candidate_rel150_uint16": _read_uint16(run_block, 150),
                    "free_space_equilibration_time_h_rel155": _read_double(run_block, 155),
                    "free_space_degas_test_duration_s_rel165": _read_double(run_block, 165),
                    "free_space_ambient_input_cm3_rel173": _read_double(run_block, 173),
                    "free_space_analysis_input_cm3_rel181": _read_double(run_block, 181),
                    "target_first_pressure_fixed_dose_cm3_g_stp_rel418": _read_double(run_block, 418),
                    "target_max_volume_increment_cm3_g_stp_rel454": _read_double(run_block, 454),
                    "target_absolute_pressure_tolerance_mmHg_rel462": _read_double(run_block, 462),
                    "target_relative_pressure_tolerance_pct_rel470": _read_double(run_block, 470),
                    "dose_increment_row1_end_p_po_rel498": _read_double(run_block, 498),
                    "dose_increment_row1_increment_cm3_g_stp_rel508": _read_double(run_block, 508),
                    "dose_increment_row1_previous_pct_rel516": _read_double(run_block, 516),
                    "po_reference_mmHg_rel563": _read_double(run_block, 563),
                    "bath_temperature_K_rel571": _read_double(run_block, 571),
                    "analysis_temperature_K_rel587": _read_double(run_block, 587),
                }
            )

        if degas_block:
            evacuation_rate = _read_double(degas_block, 122)
            unrestricted = _read_double(degas_block, 130)
            target_vacuum = _read_double(degas_block, 138)
            hold_pressure = _read_double(degas_block, 172)
            heating_temp_c = _read_double(degas_block, 64)
            target_temp_c = _read_double(degas_block, 162)
            options.update(
                {
                    "degas_flags_rel60_63_hex": degas_block[60:64].hex(" ") if len(degas_block) >= 64 else "",
                    "degas_heating_phase1_temperature_C_rel64": heating_temp_c,
                    "degas_heating_phase1_temperature_K_calculated": heating_temp_c + 273.15 if heating_temp_c is not None else None,
                    "degas_heating_phase1_ramp_K_min_rel72": _read_double(degas_block, 72),
                    "degas_heating_phase1_time_min_rel80": _read_double(degas_block, 80),
                    "degas_evacuation_rate_mmHg_s_rel122": evacuation_rate,
                    "degas_evacuation_rate_kPa_s_calculated": evacuation_rate * MMHG_TO_KPA if evacuation_rate is not None else None,
                    "degas_unrestricted_evacuate_from_mmHg_rel130": unrestricted,
                    "degas_unrestricted_evacuate_from_kPa_calculated": unrestricted * MMHG_TO_KPA if unrestricted is not None else None,
                    "degas_target_vacuum_mmHg_rel138": target_vacuum,
                    "degas_target_vacuum_kPa_calculated": target_vacuum * MMHG_TO_KPA if target_vacuum is not None else None,
                    "degas_evacuation_time_min_rel146": _read_double(degas_block, 146),
                    "degas_temperature_ramp_rate_K_min_rel154": _read_double(degas_block, 154),
                    "degas_target_temperature_C_rel162": target_temp_c,
                    "degas_target_temperature_K_calculated": target_temp_c + 273.15 if target_temp_c is not None else None,
                    "degas_hold_pressure_mmHg_rel172": hold_pressure,
                    "degas_hold_pressure_kPa_calculated": hold_pressure * MMHG_TO_KPA if hold_pressure is not None else None,
                }
            )

        if adsorptive_block and len(adsorptive_block) > 600:
            options.update(
                {
                    "adsorptive_non_condensing_flag_candidate_rel352_uint16": _read_uint16(adsorptive_block, 352),
                    "adsorptive_dosing_method_candidate_rel400_uint16": _read_uint16(adsorptive_block, 400),
                    "adsorptive_ideal_gas_nonideality_flag_candidate_rel536_uint16": _read_uint16(adsorptive_block, 536),
                    "adsorptive_molecular_weight_rel121": _read_double(adsorptive_block, 121),
                }
            )
        return options

    def _parse_instrument_info(self, blocks: dict[int, bytes]) -> dict[str, object]:
        texts: list[str] = []
        for subset_id in (100, 303, 705, 302, 304, 320, 322, 330):
            texts.extend(item.text for item in self._read_mic_strings(blocks.get(subset_id, b"")) if item.text)
        joined = "\n".join(texts)

        software = ""
        for text in texts:
            if (
                "MicroActive for TriStar II Plus" in text
                or "TriStar II Plus Version" in text
                or "TriStar II 3020 Version" in text
                or "ASAP 2460 Version" in text
                or "ASAP 2020 Plus Version" in text
            ):
                software = text
                break

        manufacturer = "Micromeritics" if "TriStar" in joined or "MicroActive" in joined or "ASAP" in joined else ""
        model = ""
        if "TriStar II Plus" in joined:
            model = "TriStar II Plus"
        elif "TriStar II 3020" in joined:
            model = "TriStar II 3020"
        elif "ASAP 2460" in joined:
            model = "ASAP 2460"
        elif "ASAP 2020 Plus" in joined:
            model = "ASAP 2020 Plus"
        elif "3500" in texts or "3Flex" in joined:
            manufacturer = "Micromeritics"
            model = "3Flex 3500"
            if not software:
                software = next((text for text in texts if re.fullmatch(r"V\d+(?:\.\d+)+", text)), "")

        options: dict[str, object] = {}
        if manufacturer:
            options["instrument_manufacturer"] = manufacturer
        if model:
            options["instrument_model"] = model
        if software:
            options["instrument_software"] = software
        return options

    def _parse_test_time_options(
        self,
        header: SmpHeader,
        method_options: dict[str, object],
        log_block: bytes = b"",
    ) -> dict[str, object]:
        time_zone = _timestamp_zone_for_instrument(method_options)
        file_modified_raw, file_modified_time = _file_modified_timestamp(header.file_path)
        log_times = self._parse_log_event_times(log_block)
        started_time = str(log_times.get("log_started_time") or _timestamp_text(header.created_raw, time_zone))
        completed_time = str(log_times.get("log_completed_time") or "")
        started_raw = log_times.get("log_started_sort_key", header.created_raw)
        completed_raw = log_times.get("log_completed_sort_key", 0)
        duration_seconds = 0
        duration_time = ""
        started_dt = log_times.get("_log_started_datetime")
        completed_dt = log_times.get("_log_completed_datetime")
        if isinstance(started_dt, datetime) and isinstance(completed_dt, datetime) and completed_dt >= started_dt:
            duration_seconds = int((completed_dt - started_dt).total_seconds())
            duration_time = _duration_text_from_seconds(duration_seconds)
        return {
            "test_started_raw": started_raw,
            "test_started_time": started_time,
            "test_completed_raw": completed_raw,
            "test_completed_time": completed_time,
            "test_duration_seconds": duration_seconds,
            "test_duration_time": duration_time,
            "test_modified_raw": header.modified_raw,
            "test_modified_time": _timestamp_text(header.modified_raw, time_zone),
            "sample_saved_raw": file_modified_raw,
            "sample_saved_time": file_modified_time,
            "sample_saved_time_source": "file last modified time",
            "test_file_modified_raw": file_modified_raw,
            "test_file_modified_time": file_modified_time,
            "test_time_zone": time_zone or "system_local",
            "test_time_source": "SMP log Started event timestamp" if log_times.get("log_started_time") else "SMP header timestamp",
            "test_completed_time_source": "SMP log Finished/Done event timestamp" if completed_time else "",
            "test_duration_source": "SMP log Started/Finished event timestamps" if duration_time else "",
        }

    def _parse_log_event_times(self, log_block: bytes) -> dict[str, object]:
        events: dict[str, object] = {}
        for item in self._parse_log_messages(log_block):
            event_time = self._log_event_datetime(log_block, item.rel_offset)
            if event_time is None:
                continue
            text = item.text.strip()
            text_lower = text.lower()
            event_text = event_time.strftime("%Y-%m-%d %H:%M:%S")
            event_key = _datetime_sort_key(event_time)

            if (
                "started analysis of file" in text_lower
                or "starting a sample analysis" in text_lower
                or ("analysis on file" in text_lower and " started" in text_lower)
            ):
                events.setdefault("log_started_time", event_text)
                events.setdefault("log_started_sort_key", event_key)
                events.setdefault("log_started_event", text)
                events.setdefault("_log_started_datetime", event_time)
            elif "finished a sample analysis" in text_lower or ("analysis on file" in text_lower and " done" in text_lower):
                events["log_completed_time"] = event_text
                events["log_completed_sort_key"] = event_key
                events["log_completed_event"] = text
                events["_log_completed_datetime"] = event_time
        return events

    @staticmethod
    def _log_event_datetime(block: bytes, text_rel_offset: int) -> datetime | None:
        timestamp_rel = text_rel_offset - 25
        if timestamp_rel < 0 or timestamp_rel + 18 > len(block):
            return None
        second, minute, hour, day, month_zero, year_from_1900, _weekday, _yearday, _is_dst = struct.unpack_from(
            "<9H",
            block,
            timestamp_rel,
        )
        if not (
            0 <= second < 60
            and 0 <= minute < 60
            and 0 <= hour < 24
            and 1 <= day <= 31
            and 0 <= month_zero <= 11
            and 70 <= year_from_1900 <= 300
        ):
            return None
        try:
            return datetime(1900 + year_from_1900, month_zero + 1, day, hour, minute, second)
        except ValueError:
            return None

    def _parse_stored_bet_range(self, block: bytes) -> dict[str, object]:
        if not block or len(block) < 26:
            return {}
        p_min = _read_double(block, len(block) - 18)
        p_max = _read_double(block, len(block) - 10)
        if p_min is None or p_max is None:
            return {}
        if 0.0 <= float(p_min) < float(p_max) <= 1.0:
            return {
                "stored_bet_pressure_min": float(p_min),
                "stored_bet_pressure_max": float(p_max),
            }
        return {}

    def _parse_log_messages(self, block: bytes) -> list[MicString]:
        strings = self._read_mic_strings(block)
        return [item for item in strings if item.text and not item.text.startswith((")", "!"))]

    def _read_mic_strings(self, block: bytes) -> list[MicString]:
        strings: list[MicString] = []
        marker = b"\xe0\x01\x00"
        pos = 0
        while True:
            idx = block.find(marker, pos)
            if idx < 0 or idx + 7 > len(block):
                break
            length = struct.unpack_from("<I", block, idx + 3)[0]
            text_start = idx + 7
            text_end = text_start + length
            if 0 <= length <= len(block) - text_start and length % 2 == 0:
                raw = block[text_start:text_end]
                text = raw.decode("utf-16le", errors="ignore").rstrip("\x00").strip()
                strings.append(MicString(rel_offset=text_start, text=text))
                pos = max(text_end, idx + 1)
            else:
                pos = idx + 1
        return strings


def export_results_csv(results: Sequence[TriStarResult], out_dir: str | Path, prefix: str = "tristar3020_minimal_parser") -> list[Path]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        _write_csv(output_dir / f"{prefix}_header.csv", _header_rows(results)),
        _write_csv(output_dir / f"{prefix}_blocks.csv", _block_rows(results)),
        _write_csv(output_dir / f"{prefix}_sample_run_free_space.csv", _sample_run_rows(results)),
        _write_csv(output_dir / f"{prefix}_target_pressure_table.csv", _target_pressure_rows(results)),
        _write_csv(output_dir / f"{prefix}_isotherm.csv", _isotherm_rows(results)),
        _write_csv(output_dir / f"{prefix}_po_elapsed.csv", _po_rows(results)),
        _write_csv(output_dir / f"{prefix}_adsorptive_properties.csv", _adsorptive_rows(results)),
        _write_csv(output_dir / f"{prefix}_adsorptive_psat_table.csv", _adsorptive_psat_rows(results)),
        _write_csv(output_dir / f"{prefix}_sample_logs.csv", _log_rows(results)),
    ]
    summary_path = output_dir / f"{prefix}_summary.json"
    summary_path.write_text(json.dumps([result.summary_dict() for result in results], ensure_ascii=False, indent=2), encoding="utf-8")
    paths.append(summary_path)
    return paths


def _header_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    return [
        {
            "file": result.file_name,
            "file_path": result.header.file_path,
            "byte_count": result.header.byte_count,
            "magic": result.header.magic,
            "version": result.header.version,
            "created_raw": result.header.created_raw,
            "created_time": result.header.created_time,
            "test_started_raw": result.test_started_raw,
            "test_started_time": result.test_started_time,
            "test_completed_raw": result.test_completed_raw,
            "test_completed_time": result.test_completed_time,
            "test_duration_seconds": result.test_duration_seconds,
            "test_duration_time": result.test_duration_time,
            "sample_saved_raw": result.sample_saved_raw,
            "sample_saved_time": result.sample_saved_time,
            "test_time_zone": result.method_options.get("test_time_zone", ""),
            "modified_raw": result.header.modified_raw,
            "modified_time": result.header.modified_time,
            "directory_offset": result.header.directory_offset,
            "directory_size": result.header.directory_size,
            "subset_count": len(result.subsets),
        }
        for result in results
    ]


def _block_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        for block_index, entry in enumerate(result.subsets):
            rows.append(
                {
                    "file": result.file_name,
                    "block_index": block_index,
                    "subset_id": entry.subset_id,
                    "marker": entry.marker,
                    "offset": entry.offset,
                    "payload_size": entry.payload_size,
                    "total_size": entry.total_size,
                }
            )
    return rows


def _sample_run_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows = []
    for result in results:
        run = result.run_conditions
        fs = result.free_space
        row = result.summary_dict()
        row.update(
            {
                "submitter": result.sample.submitter,
                "bar_code": result.sample.bar_code,
                "sample_density_g_cm3": result.sample.sample_density_g_cm3,
                "evacuation_rate_mmHg_s": run.evacuation_rate_mmHg_s,
                "evacuation_rate_kPa_s": run.evacuation_rate_mmHg_s * MMHG_TO_KPA if run.evacuation_rate_mmHg_s is not None else None,
                "unrestricted_evacuate_from_mmHg": run.unrestricted_evacuate_from_mmHg,
                "evacuation_time_h": run.evacuation_time_h,
                "leak_test_time_s": run.leak_test_time_s,
                "free_space_equilibration_time_h": run.free_space_equilibration_time_h,
                "ambient_free_space_entered_run_cm3": run.ambient_free_space_entered_cm3,
                "analysis_free_space_entered_run_cm3": run.analysis_free_space_entered_cm3,
                "desorption_test_time_s": run.desorption_test_time_s,
                "po_reference_mmHg": run.po_reference_mmHg,
                "adsorptive_name": run.adsorptive_name,
                "analysis_entered_cm3": fs.analysis_entered_cm3,
                "ambient_entered_cm3": fs.ambient_entered_cm3,
                "nonideality_factor": fs.nonideality_factor,
                "vbath_cm3": fs.vbath_cm3,
                "stem_volume_cm3": fs.stem_volume_cm3,
                "ambient_temperature_K_assumed": fs.ambient_temperature_K_assumed,
            }
        )
        row.update(result.method_options)
        rows.append(row)
    return rows


def _target_pressure_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        for item in result.target_pressure_table:
            rows.append(
                {
                    "file": result.file_name,
                    "row": item.row,
                    "branch": item.branch,
                    "starting_pressure_p_po": item.starting_pressure_p_po,
                    "ending_pressure_p_po": item.ending_pressure_p_po,
                    "pressure_increment_p_po": item.pressure_increment_p_po,
                    "ending_pressure_rel_offset": item.ending_pressure_rel_offset,
                }
            )
    return rows


def _isotherm_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        if result.po_records:
            first_po = result.po_records[0]
            rows.append(
                {
                    "file": result.file_name,
                    "point_index": 0,
                    "phase": "po_only",
                    "record_rel_offset": "",
                    "absolute_pressure_mmHg": "",
                    "relative_pressure": "",
                    "raw_internal_cm3_stp": "",
                    "saturation_pressure_mmHg": first_po.saturation_pressure_mmHg,
                    "elapsed_seconds": first_po.elapsed_seconds,
                    "elapsed_time": first_po.elapsed_time,
                    "quantity_adsorbed_cm3_g_stp": "",
                    "quantity_adsorbed_mmol_g": "",
                    "vfree_factor_cm3": result.free_space.vfree_factor_cm3,
                    "vfree_factor_source": result.free_space.vfree_factor_source,
                }
            )
        for point in result.isotherm:
            rows.append(
                {
                    "file": result.file_name,
                    "point_index": point.index,
                    "phase": point.phase,
                    "record_rel_offset": point.record_rel_offset,
                    "absolute_pressure_mmHg": point.absolute_pressure_mmHg,
                    "relative_pressure": point.relative_pressure,
                    "raw_internal_cm3_stp": point.raw_internal_cm3_stp,
                    "saturation_pressure_mmHg": point.saturation_pressure_mmHg,
                    "elapsed_seconds": point.elapsed_seconds,
                    "elapsed_time": point.elapsed_time,
                    "quantity_adsorbed_cm3_g_stp": point.quantity_adsorbed_cm3_g_stp,
                    "quantity_adsorbed_mmol_g": point.quantity_adsorbed_mmol_g,
                    "vfree_factor_cm3": result.free_space.vfree_factor_cm3,
                    "vfree_factor_source": result.free_space.vfree_factor_source,
                }
            )
    return rows


def _po_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        for record in result.po_records:
            rows.append(
                {
                    "file": result.file_name,
                    "po_index": record.index,
                    "record_rel_offset": record.rel_offset,
                    "saturation_pressure_mmHg": record.saturation_pressure_mmHg,
                    "elapsed_seconds": record.elapsed_seconds,
                    "elapsed_time": record.elapsed_time,
                }
            )
    return rows


def _adsorptive_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        props = result.adsorptive_properties
        if props is None:
            continue
        rows.append(
            {
                "file": result.file_name,
                "adsorptive": props.adsorptive,
                "mnemonic": props.mnemonic,
                "max_manifold_pressure_mmHg": props.max_manifold_pressure_mmHg,
                "max_manifold_pressure_kPa": props.max_manifold_pressure_kPa,
                "nonideality_factor": props.nonideality_factor,
                "density_conversion_factor": props.density_conversion_factor,
                "thermal_transpiration_hard_sphere_A": props.thermal_transpiration_hard_sphere_A,
                "thermal_transpiration_hard_sphere_nm": props.thermal_transpiration_hard_sphere_nm,
                "molecular_cross_sectional_area_nm2": props.molecular_cross_sectional_area_nm2,
                "ui_field_rel101": props.ui_field_rel101,
                "psat_table_count": len(props.psat_table),
            }
        )
    return rows


def _adsorptive_psat_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        props = result.adsorptive_properties
        if props is None:
            continue
        for item in props.psat_table:
            row = {"file": result.file_name}
            row.update(item)
            rows.append(row)
    return rows


def _log_rows(results: Sequence[TriStarResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        for item in result.log_messages:
            rows.append({"file": result.file_name, "subset_id": 705, "rel_offset": item.rel_offset, "text": item.text})
        for item in result.sample_tube_strings:
            rows.append({"file": result.file_name, "subset_id": 1021, "rel_offset": item.rel_offset, "text": item.text})
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if fieldnames:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        else:
            handle.write("")
    return path


def _read_double(data: bytes, rel: int) -> float | None:
    if rel < 0 or rel + 8 > len(data):
        return None
    value = struct.unpack_from("<d", data, rel)[0]
    return value if math.isfinite(value) else None


def _valid_target_pressure(value: float | None) -> bool:
    if value is None:
        return False
    try:
        pressure = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(pressure) and 1e-9 <= pressure <= 1.2


def _pressures_close(left: float, right: float, tolerance: float = 5e-7) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _read_uint16(data: bytes, rel: int) -> int | None:
    if rel < 0 or rel + 2 > len(data):
        return None
    return struct.unpack_from("<H", data, rel)[0]


def _read_uint32(data: bytes, rel: int) -> int | None:
    if rel < 0 or rel + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, rel)[0]


def _int_from_double(data: bytes, rel: int) -> int | None:
    value = _read_double(data, rel)
    if value is None:
        return None
    return int(round(value))


def _timestamp_text(raw: int, time_zone: str | None = None) -> str:
    try:
        if time_zone and ZoneInfo is not None:
            return datetime.fromtimestamp(raw, ZoneInfo(time_zone)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        return datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError, KeyError):
        return ""


def _datetime_sort_key(value: datetime) -> int:
    return int(value.strftime("%Y%m%d%H%M%S"))


def _duration_text_from_seconds(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _timestamp_zone_for_instrument(method_options: dict[str, object]) -> str | None:
    software = str(method_options.get("instrument_software") or "")
    model = str(method_options.get("instrument_model") or "")
    if "MicroActive for TriStar II Plus" in software or "TriStar II Plus Version" in software or model == "TriStar II Plus":
        return "America/New_York"
    return None


def _file_modified_timestamp(file_path: str) -> tuple[int, str]:
    try:
        raw = int(Path(file_path).stat().st_mtime)
    except OSError:
        return 0, ""
    return raw, _timestamp_text(raw)


def _temperature_from_text(text: str) -> float | None:
    match = re.search(r"@\s*([0-9]+(?:\.[0-9]+)?)\s*K", text or "", flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value if 50.0 < value < 150.0 else None


def _marker_text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _first_string_at_or_after(strings: Sequence[MicString], min_offset: int, max_offset: int | None = None) -> str:
    for item in strings:
        if item.rel_offset >= min_offset and (max_offset is None or item.rel_offset <= max_offset) and item.text:
            return item.text
    return ""
