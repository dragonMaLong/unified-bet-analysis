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

from .micromeritics_pores import pore_report_settings
from .real_gas import compressibility as real_gas_compressibility
from .real_gas import saturation as real_gas_saturation
from .real_gas import saturation_temperature as real_gas_saturation_temperature
from .real_gas import tabulated_compressibility as real_gas_tabulated_compressibility
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
MICROACTIVE_FREE_SPACE_ENTERED = 2
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

    Micromeritics ``.smp`` containers go to :func:`load_smp`; JWGB/APAS
    text ``.raw`` exports go to :func:`tristar_bet.jwgb_raw.load_jwgb_raw`; MicrotracBEL
    BELMaster ``.dat`` exports go to :func:`tristar_bet.belmaster.load_dat`;
    Quantachrome Autosorb iQ ``.qps`` files go to
    :func:`tristar_bet.quantachrome.load_qps`; official Excel report
    workbooks go to :func:`tristar_bet.excel_import.load_excel`.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".jwgbt":
        from .jwgb_native import load_jwgb_native

        return load_jwgb_native(path)
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        from .excel_import import load_excel

        return load_excel(path)
    if suffix == ".dat":
        from .belmaster import load_dat

        return load_dat(path)
    if suffix == ".qps":
        from .quantachrome import load_qps

        return load_qps(path)
    if suffix == ".raw":
        from .jwgb_raw import load_jwgb_raw

        return load_jwgb_raw(path)
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
        asap_software = self._asap_software_text(blocks.get(303, b""))
        legacy_asap_2020 = bool(asap_software and "ASAP 2020" in asap_software and "ASAP 2020 Plus" not in asap_software)
        if legacy_asap_2020:
            run_conditions = self._sanitize_legacy_asap_2020_run_conditions(run_conditions, adsorptive_properties)
        tristar_plus_v3 = self._is_tristar_plus_v3(blocks.get(303, b""))
        if tristar_plus_v3:
            sample = self._with_tristar_plus_v3_sample_info(sample, blocks.get(301, b""))
            run_conditions = self._parse_tristar_plus_v3_run_conditions(blocks.get(302, b""), adsorptive_properties)
        asap_2425 = "ASAP 2425" in asap_software
        if asap_2425:
            # The compact mass reader can land on a neighbouring double (Carbon
            # Black example: "Lot Number" shifts the layout); the record
            # [mass, empty tube, tube + sample, density] is unambiguous.
            recorded = self._with_row_frame_sample_mass(sample, blocks.get(301, b""))
            if recorded.sample_mass_g is not None:
                sample = recorded
        free_space = self._parse_free_space(
            blocks.get(303, b""),
            self._payload_size(subsets, 303),
            run_conditions,
            adsorptive_properties,
            blocks.get(705, b""),
        )
        # Version 3.02 stores P/P0 and elapsed minutes in each point row. The
        # old separate-P0 scanner otherwise mistakes point bytes for P0 data.
        # Gemini VII files share the legacy record layout but not its meaning
        # (no free-space correction), so they never go through the other paths.
        gemini = self._is_gemini(blocks.get(303, b""))
        gemini_inputs = None
        # Raw 3Flex files are only read by their own path: the generic
        # fallbacks would apply free-space formulas that were never validated
        # for this instrument.
        flex = self._parse_3flex_raw(blocks, subsets, sample, adsorptive_properties) if (
            not gemini and self._is_3flex_raw(blocks.get(303, b""))) else None
        handled = gemini or flex is not None
        if flex is not None:
            po_records = []
            isotherm = list(flex.get("isotherm", []))
            if isotherm:
                sample, free_space = flex["sample"], flex["free_space"]
                if flex.get("density_conversion_factor") and adsorptive_properties is not None:
                    adsorptive_properties = replace(
                        adsorptive_properties, density_conversion_factor=flex["density_conversion_factor"])
        elif gemini:
            po_records = []
            gemini_inputs = self._gemini_inputs(blocks, adsorptive_properties.mnemonic if adsorptive_properties else "")
            if gemini_inputs is not None:
                sample = replace(sample, sample_mass_g=gemini_inputs["mass"],
                                 sample_density_g_cm3=gemini_inputs["density"] or sample.sample_density_g_cm3)
            isotherm = self._parse_gemini_isotherm(blocks.get(303, b""), self._payload_size(subsets, 303), gemini_inputs)
        else:
            po_records = [] if tristar_plus_v3 else self._parse_po_records(blocks.get(303, b""), self._payload_size(subsets, 303))
            isotherm = [] if tristar_plus_v3 else self._parse_isotherm(blocks.get(303, b""), po_records, sample, free_space)
        used_microactive_point_table = False
        used_asap_point_table = False
        used_3flex_manual_point_table = False
        if not isotherm and not handled:
            isotherm = self._parse_asap_isotherm(blocks.get(303, b""), self._payload_size(subsets, 303), po_records, sample, free_space)
            used_asap_point_table = bool(isotherm)
        if not isotherm and not handled:
            isotherm = self._parse_microactive_isotherm(blocks.get(303, b""), self._payload_size(subsets, 303), sample, free_space)
            used_microactive_point_table = bool(isotherm)
        if not isotherm and not handled:
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
        row_frame_family = ""
        row_frame_failure = ""
        transpiration_note: dict[str, object] = {}
        if asap_2425 and used_asap_point_table:
            isotherm, transpiration_note = self._apply_asap_2425_transpiration(isotherm, blocks, subsets, adsorptive_properties)
        if not isotherm and not handled:
            # Last resort, only for software versions whose layout was decoded
            # (vendor example files of TriStar II 3020 V1.0x / early ASAP 2020
            # Plus): fixed 58-byte point frames and a "Sample Tube" anchored
            # free-space record. Earlier paths are left untouched.
            row_frame_family = self._row_frame_family(blocks.get(303, b""))
            if row_frame_family:
                frame_sample = self._with_row_frame_sample_mass(sample, blocks.get(301, b""))
                frame_free_space = self._parse_row_frame_free_space(
                    blocks.get(303, b""), row_frame_family, run_conditions, adsorptive_properties,
                )
                real_gas = None
                # Temperature location and the model are verified on ASAP 2020 Plus
                # files only (TriStar 302 has another layout).
                if (
                    row_frame_family == "ASAP 2020 Plus"
                    and "real_gas_eos" in frame_free_space.vfree_factor_source
                    and adsorptive_properties is not None
                ):
                    temperature = self._asap_row_frame_bath_temperature(
                        blocks.get(302, b""), blocks.get(303, b""), self._payload_size(subsets, 303),
                        adsorptive_properties.mnemonic,
                    )
                    density_factor = adsorptive_properties.density_conversion_factor
                    gas = adsorptive_properties.mnemonic
                    if (
                        temperature is not None
                        and density_factor is not None
                        and 0.0 < float(density_factor) < 0.01
                        and real_gas_compressibility(gas, 1.0, temperature) is not None
                    ):
                        real_gas = (gas, temperature, float(density_factor))
                        frame_free_space = replace(
                            frame_free_space,
                            vfree_factor_source=frame_free_space.vfree_factor_source.replace(
                                "real_gas_eos_approximated", "real_gas_eos_span2000"
                            ),
                        )
                isotherm = self._parse_row_frame_isotherm(
                    blocks.get(303, b""), self._payload_size(subsets, 303), frame_sample, frame_free_space, real_gas,
                )
                # Without a usable free space or sample mass the frames give only
                # dosed gas; do not present that as an adsorption isotherm.
                if isotherm and all(point.quantity_adsorbed_cm3_g_stp is not None for point in isotherm):
                    sample, free_space = frame_sample, frame_free_space
                    if row_frame_family == "ASAP 2020 Plus":
                        isotherm, transpiration_note = self._apply_row_frame_transpiration(
                            isotherm, blocks, subsets, adsorptive_properties,
                        )
                else:
                    isotherm = []
                    row_frame_failure = (
                        "已识别数据点，但缺少可用的自由空间或样品质量，无法计算吸附量"
                        if self._scan_row_frames(blocks.get(303, b""), self._payload_size(subsets, 303))
                        else ""
                    )
                    row_frame_family = ""
        legacy_v6_options: dict[str, object] = {}
        if not isotherm and blocks.get(303, b"")[18:19] == b"\x06":
            legacy = self._parse_legacy_v6(blocks, sample)
            if legacy is not None:
                isotherm, free_space, adsorptive_properties, legacy_v6_options = legacy
        method_options = self._parse_method_options(blocks.get(302, b""), b"" if tristar_plus_v3 else blocks.get(725, b""), blocks.get(320, b""))
        method_options.update(self._parse_instrument_info(blocks))
        method_options.update(self._parse_stored_bet_range(blocks.get(311, b"")))
        if legacy_asap_2020:
            # Subset 311 is the report's saved selection interval. This legacy
            # file can intentionally contain only two collected points inside
            # that interval, which is a valid exact linear BET fit.
            method_options["use_stored_fit_ranges"] = True
            if "stored_bet_pressure_min" in method_options:
                method_options["stored_bet_range_source"] = "ASAP 2020 V4 SMP subset 311 report selection"
            method_options["vendor_bet_minimum_points"] = 2
        if tristar_plus_v3:
            method_options.update(self._parse_tristar_plus_v3_report_options(blocks, run_conditions))
        method_options.update(self._parse_test_time_options(header, method_options, blocks.get(705, b"")))
        method_options.update(self._parse_single_point_options(blocks.get(306, b""), method_options))
        if not tristar_plus_v3 and not legacy_asap_2020:
            method_options.update(micromeritics_report_settings(blocks, isotherm, method_options))
        if not legacy_asap_2020 and method_options.get("instrument_model") in MICROMERITICS_REPORT_SETTINGS_MODELS:
            method_options.update(pore_report_settings(
                blocks, run_conditions, model=str(method_options.get("instrument_model", "")),
                po_records=po_records, adsorptive=adsorptive_properties.mnemonic if adsorptive_properties else "",
            ))
        if used_asap_point_table:
            method_options["asap_quantity_source"] = "raw_cm3_stp_free_space_corrected_and_normalized"
            if free_space.vfree_factor_cm3 is not None:
                method_options["asap_vfree_factor_cm3"] = free_space.vfree_factor_cm3
            if free_space.vbath_cm3 is not None:
                method_options["asap_vbath_cm3"] = free_space.vbath_cm3
            if legacy_asap_2020:
                method_options["format_family"] = "ASAP 2020 V4 SMP"
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
        if tristar_plus_v3:
            method_options["format_family"] = "TriStar II Plus 3.02 SMP"
            method_options["po_record_source"] = "derived_from_point_absolute_and_relative_pressure"
            method_options["tristar_plus_quantity_validation"] = "empirical_reconstruction_summary_checked_on_reference_samples"
            method_options["microactive_quantity_source"] = (
                "raw_cm3_stp_free_space_corrected_and_normalized"
                if isotherm and all(point.quantity_adsorbed_cm3_g_stp is not None for point in isotherm)
                else "unavailable_missing_free_space_or_sample_mass"
            )
            # Keep each derived P0 alongside its own point; do not invent an
            # initial P0-only record from unrelated bytes in the container.
            po_records = [
                PoRecord(point.index, point.record_rel_offset, point.saturation_pressure_mmHg, point.elapsed_seconds)
                for point in isotherm
                if point.saturation_pressure_mmHg is not None and point.elapsed_seconds is not None
            ]
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
        if row_frame_family:
            method_options["format_family"] = f"{row_frame_family} row-frame SMP"
            method_options.setdefault("instrument_manufacturer", "Micromeritics")
            method_options.setdefault("instrument_model", row_frame_family)
            method_options["row_frame_quantity_source"] = (
                "raw_cm3_stp_free_space_corrected_and_normalized"
                if all(point.quantity_adsorbed_cm3_g_stp is not None for point in isotherm)
                else "unavailable_missing_free_space_or_sample_mass"
            )
            method_options["row_frame_free_space_source"] = free_space.vfree_factor_source
            real_gas = "real_gas_eos_approximated" in free_space.vfree_factor_source
            method_options["row_frame_validation"] = (
                "MicroActive 7.00 vendor uses a real-gas equation of state for this file and none is available "
                "for this adsorptive here; quantities use the ideal-gas correction and are approximate"
                if real_gas
                else "real-gas free space (Span et al. 2000 N2 equation of state, adsorbed-phase correction) as "
                "selected in the file; checked against MicroActive 7.00"
                if "real_gas_eos_span2000" in free_space.vfree_factor_source
                else "points, P/P0, dosed gas and sample mass match MicroActive 7.00 exactly and adsorbed "
                "quantity within 0.1 % on 6 vendor example files; default BET (report range from subset 311) within "
                "0.1 % of MicroActive on 7 files"
            )
            if real_gas:
                method_options["row_frame_quantity_source"] = "approximate_ideal_gas_vendor_used_real_gas_eos"
            method_options.update(transpiration_note)
            # t-Plot: thickness model, thickness range and P/P0 window saved in
            # subset 314 (same layout as the families already decoded). With
            # them the external area matches MicroActive within 0.011 %.
            t_plot_settings = _micromeritics_t_plot_settings(blocks.get(314, b""))
            if t_plot_settings:
                method_options.update(t_plot_settings)
                method_options["use_vendor_t_plot_settings"] = True
                method_options["use_stored_fit_ranges"] = True
            stored_bet = self._row_frame_stored_bet_range(blocks.get(311, b""))
            if stored_bet is not None:
                method_options["stored_bet_pressure_min"], method_options["stored_bet_pressure_max"] = stored_bet
                method_options["stored_bet_range_source"] = "row-frame SMP subset 311 report selection"
                method_options["use_stored_fit_ranges"] = True
        if asap_2425:
            method_options.update(transpiration_note)
            if free_space.vfree_factor_source == "asap_2425_free_space_mode_not_decoded":
                # Points without quantities would load as an empty result.
                isotherm = []
                method_options["no_isotherm_reason"] = "该 ASAP 2425 文件的自由空间方式尚未解析，暂不计算吸附量"
        if flex is not None:
            method_options.update(flex.get("options", {}))
            if not isotherm:
                method_options["no_isotherm_reason"] = flex.get("reason", "3Flex 文件暂不能计算吸附量")
        if gemini:
            method_options["instrument_manufacturer"] = "Micromeritics"
            method_options["instrument_model"] = "Gemini VII 2390"
            software = next((item.text.strip() for item in self._read_mic_strings(blocks.get(303, b""))
                             if item.text.startswith("Gemini VII")), "")
            if software:
                method_options["instrument_software"] = software
            method_options["format_family"] = "Gemini VII SMP"
            block303 = blocks.get(303, b"")
            bet_ids = [point.index for point in isotherm
                       if point.phase == "adsorption" and 103 in self._gemini_point_tags(block303, point.record_rel_offset)]
            if len(bet_ids) >= 3:
                # The selection can skip points (silaliso: every other point), so
                # pass the points themselves rather than a pressure range.
                method_options["use_official_raw_bet_points"] = True
                method_options["jwgb_raw_bet_fit_point_ids"] = bet_ids
                method_options["bet_point_selection_source"] = "Gemini SMP point tag 103"
            # Subset 314 has the TriStar layout; with its thickness model and
            # range, t-Plot equals MicroActive's to 1e-8 (carbiso STSA,
            # sialfull / silaliso Harkins-Jura).
            t_plot = _micromeritics_t_plot_settings(blocks.get(314, b""))
            if t_plot.get("vendor_t_plot_thickness_method") in ("harkins_jura", "carbon_black_stsa"):
                method_options.update(t_plot)
                method_options["use_vendor_t_plot_settings"] = True
                method_options["use_stored_fit_ranges"] = True
            if gemini_inputs is not None:
                method_options["gemini_free_space_mode"] = gemini_inputs["mode"]
                method_options["gemini_free_space_difference_cm3"] = gemini_inputs["difference"]
                if gemini_inputs["temperature"] is not None:
                    method_options["gemini_bath_temperature_K"] = gemini_inputs["temperature"]
            elif not isotherm:
                method_options["no_isotherm_reason"] = "Gemini 文件的自由空间设置未能识别，暂不计算吸附量"
        if legacy_v6_options:
            method_options.update(legacy_v6_options)
            # Point tag 103 marks the report's BET selection: on it the BET
            # fit equals MicroActive's (Alumina Kr 0.482461 vs 0.482460 m2/g,
            # C 73.626; Carbon Black D-4 24.3777 vs 24.3793).
            selected = [p.relative_pressure for p, tags in zip(isotherm, legacy_v6_options["legacy_v6_point_tags"]) if 103 in tags]
            if len(selected) >= 2:
                method_options["stored_bet_pressure_min"] = min(selected)
                method_options["stored_bet_pressure_max"] = max(selected)
                method_options["stored_bet_range_source"] = "legacy ASAP v6 point tag 103"
                method_options["use_stored_fit_ranges"] = True
        if not isotherm:
            reason = row_frame_failure or self._no_isotherm_reason(blocks, sample)
            if reason:
                method_options.setdefault("no_isotherm_reason", reason)
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
            # ASAP 2460 (991-byte block) uses the same structure as MicroActive; checked
            # in MicroActive 7.00 (Free Space and Equilibration dialogs) on a file copy.
            located = _microactive_conditions(block)
            if located is not None:
                fs_eq, ambient, analysis, interval = located
            else:
                interval = _plausible(_read_double(block, 93), 0.5, 3600.0)
                fs_eq = _plausible(_read_double(block, 155), 0.0, 1000.0)
                ambient = _plausible(_read_double(block, 173), 0.0, 1000.0)
                analysis = _plausible(_read_double(block, 181), 0.0, 1000.0)
            return RunConditions(
                evacuation_rate_mmHg_s=_read_double(block, 61),
                unrestricted_evacuate_from_mmHg=_read_double(block, 69),
                evacuation_time_h=_read_double(block, 77),
                leak_test_time_s=None,
                equilibration_interval_s=interval,
                free_space_equilibration_time_h=fs_eq,
                ambient_free_space_entered_cm3=ambient,
                analysis_free_space_entered_cm3=analysis,
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

        # These fixed offsets hold for TriStar II 3020 (checked against its reports).
        # TriStar II Plus (MicroActive) and ASAP 2460 store other data here: when the
        # equilibration interval is not a plausible value, the whole group is unknown.
        equilibration = _plausible(_read_double(block, 91), 0.5, 3600.0)
        known = equilibration is not None
        microactive = None if known else _microactive_conditions(block)
        if microactive is not None:
            free_space_eq, ambient_entered, analysis_entered, equilibration = microactive
        else:
            free_space_eq = _plausible(_read_double(block, 112), 0.0, 1000.0) if known else None
            ambient_entered = _plausible(_read_double(block, 120), 0.0, 1000.0) if known else None
            analysis_entered = _plausible(_read_double(block, 128), 0.0, 1000.0) if known else None
        return RunConditions(
            evacuation_rate_mmHg_s=_read_double(block, 61),
            unrestricted_evacuate_from_mmHg=_read_double(block, 69),
            evacuation_time_h=_read_double(block, 77),
            leak_test_time_s=_read_uint32(block, 87),
            equilibration_interval_s=equilibration,
            free_space_equilibration_time_h=free_space_eq,
            ambient_free_space_entered_cm3=ambient_entered,
            analysis_free_space_entered_cm3=analysis_entered,
            desorption_test_time_s=_read_uint32(block, 138),
            po_reference_mmHg=po_reference,
            bath_temperature_K=bath_temperature,
            adsorptive_short=adsorptive_short,
            adsorptive_name=adsorptive_name,
        )

    def _is_tristar_plus_v3(self, block: bytes) -> bool:
        """The 3.02 layout is verified separately from MicroActive 1.x."""
        return any(item.text == "TriStar II Plus Version 3.02" for item in self._read_mic_strings(block))

    def _with_tristar_plus_v3_sample_info(self, sample: SampleInfo, block: bytes) -> SampleInfo:
        strings = self._read_mic_strings(block)
        first_label = next((item.rel_offset for item in strings if item.text == "Sample:"), 0)
        values = [item.text for item in strings if item.rel_offset < first_label]
        barcode_label = next((item.rel_offset for item in strings if item.text == "Bar Code:"), None)
        comment = next((item for item in strings if barcode_label is not None and item.rel_offset > barcode_label), None)
        density = None
        mass = sample.sample_mass_g
        if comment is not None:
            length = _read_uint32(block, comment.rel_offset - 4)
            if length is not None and length % 2 == 0 and comment.rel_offset + length + 37 <= len(block):
                end = comment.rel_offset + length
                sample_mass = _read_double(block, end + 5)
                tare = _read_double(block, end + 13)
                total = _read_double(block, end + 21)
                value = _read_double(block, end + 29)
                if (
                    block[end:end + 5] == b"\x01\x01\x00\x00\x00"
                    and sample_mass is not None and 1e-8 < sample_mass < 100.0
                    and tare is not None and 0.0 <= tare < 1000.0
                    and total is not None and math.isclose(sample_mass + tare, total, rel_tol=0.0, abs_tol=1e-8)
                ):
                    mass = sample_mass
                    if value is not None and 0.01 < value < 100.0:
                        density = value
        return replace(
            sample,
            submitter=values[2] if len(values) > 2 else sample.submitter,
            bar_code=values[3] if len(values) > 3 else sample.bar_code,
            sample_mass_g=mass,
            sample_density_g_cm3=density,
        )

    def _parse_tristar_plus_v3_run_conditions(
        self, block: bytes, props: AdsorptiveProperties | None,
    ) -> RunConditions:
        strings = self._read_mic_strings(block)
        mnemonic = props.mnemonic if props else ""
        adsorptive = props.adsorptive if props else ""
        bath = None
        po_reference = None
        equilibration_interval = None
        # The gas mnemonic follows the variable-size method pressure table.
        for item in reversed(strings):
            if mnemonic and item.text == mnemonic:
                candidate_bath = _read_double(block, item.rel_offset - 31)
                candidate_po = _read_double(block, item.rel_offset - 39)
                if candidate_bath is not None and 50.0 < candidate_bath < 150.0 and candidate_po is not None and 100.0 < candidate_po < 1000.0:
                    bath, po_reference = candidate_bath, candidate_po
                    interval = _read_double(block, item.rel_offset - 121)
                    if interval is not None and 0.0 < interval <= 86400.0:
                        equilibration_interval = interval
                    break
        if bath is None:
            bath = _temperature_from_text(adsorptive)

        # The method name has a variable UTF-16 length. The verified early
        # fields begin five bytes after it; later legacy offsets do not apply.
        name_end = None
        if len(block) >= 26 and block[19:22] == b"\xe0\x01\x00":
            length = _read_uint32(block, 22)
            if length is not None and length % 2 == 0 and 26 + length + 43 <= len(block):
                name_end = 26 + length
        def run_value(offset: int) -> float | None:
            value = _read_double(block, name_end + offset) if name_end is not None else None
            return value if value is not None and (value == 0.0 or 1e-8 <= value <= 1e6) else None
        return RunConditions(
            evacuation_rate_mmHg_s=run_value(5),
            unrestricted_evacuate_from_mmHg=run_value(13),
            evacuation_time_h=run_value(21),
            leak_test_time_s=_read_uint32(block, name_end + 31) if name_end is not None else None,
            equilibration_interval_s=equilibration_interval,
            free_space_equilibration_time_h=None,
            ambient_free_space_entered_cm3=None,
            analysis_free_space_entered_cm3=None,
            desorption_test_time_s=None,
            po_reference_mmHg=po_reference,
            bath_temperature_K=bath,
            adsorptive_short=mnemonic,
            adsorptive_name=adsorptive,
        )

    @staticmethod
    def _sanitize_legacy_asap_2020_run_conditions(
        run: RunConditions,
        props: AdsorptiveProperties | None,
    ) -> RunConditions:
        """Discard fields whose offsets belong to newer, incompatible layouts."""

        def valid(value: float | None, lower: float, upper: float, *, allow_zero: bool = False) -> float | None:
            if value is None or not math.isfinite(float(value)):
                return None
            value = float(value)
            if (lower <= value if allow_zero else lower < value) and value <= upper:
                return value
            return None

        return replace(
            run,
            evacuation_rate_mmHg_s=valid(run.evacuation_rate_mmHg_s, 0.0, 1000.0),
            unrestricted_evacuate_from_mmHg=valid(run.unrestricted_evacuate_from_mmHg, 0.0, 1000.0, allow_zero=True),
            evacuation_time_h=valid(run.evacuation_time_h, 0.0, 168.0, allow_zero=True),
            leak_test_time_s=(run.leak_test_time_s if run.leak_test_time_s is not None and 0 <= run.leak_test_time_s <= 604800 else None),
            equilibration_interval_s=valid(run.equilibration_interval_s, 0.0, 86400.0),
            free_space_equilibration_time_h=valid(run.free_space_equilibration_time_h, 0.0, 168.0),
            ambient_free_space_entered_cm3=valid(run.ambient_free_space_entered_cm3, 1e-8, 300.0),
            analysis_free_space_entered_cm3=valid(run.analysis_free_space_entered_cm3, 1e-8, 300.0),
            desorption_test_time_s=(
                run.desorption_test_time_s
                if run.desorption_test_time_s is not None and 0 <= run.desorption_test_time_s <= 604800
                else None
            ),
            adsorptive_short=(props.mnemonic if props and props.mnemonic else run.adsorptive_short),
            adsorptive_name=(props.adsorptive if props and props.adsorptive else run.adsorptive_name),
        )

    def _parse_tristar_plus_v3_report_options(
        self, blocks: dict[int, bytes], run: RunConditions,
    ) -> dict[str, object]:
        options: dict[str, object] = {"use_stored_fit_ranges": True}
        langmuir = blocks.get(312, b"")
        if len(langmuir) >= 18:
            absolute_min = _read_double(langmuir, len(langmuir) - 18)
            absolute_max = _read_double(langmuir, len(langmuir) - 10)
            po = run.po_reference_mmHg
            if absolute_min is not None and absolute_max is not None and po is not None and po > 0.0 and 0.0 <= absolute_min < absolute_max <= po:
                options.update({
                    "stored_langmuir_pressure_min": absolute_min / po,
                    "stored_langmuir_pressure_max": absolute_max / po,
                    "stored_langmuir_range_source": "SMP subset 312 absolute mmHg / method P0",
                })
        t_plot = blocks.get(314, b"")
        # This layout contains a selected thickness model and a thickness
        # interval in Angstrom, not a P/P0 interval.
        if len(t_plot) >= 208 and _read_uint16(t_plot, 19) == 1:
            strings = self._read_mic_strings(t_plot)
            if any(item.rel_offset == 103 and item.text == "Harkins and Jura" for item in strings):
                t_min = _read_double(t_plot, 184)
                t_max = _read_double(t_plot, 192)
                if t_min is not None and t_max is not None and 0.0 < t_min < t_max < 100.0:
                    options.update({
                        "vendor_t_plot_thickness_method": "harkins_jura",
                        "stored_t_plot_thickness_min_nm": t_min / 10.0,
                        "stored_t_plot_thickness_max_nm": t_max / 10.0,
                        "stored_t_plot_range_source": "SMP subset 314 thickness Angstrom / 10",
                    })
        bjh_blocks = [blocks.get(subset_id, b"") for subset_id in (315, 316)]
        if all(
            len(block) >= 137 and _read_uint16(block, 19) == 1
            and any(item.rel_offset == 103 and item.text == "Harkins and Jura" for item in self._read_mic_strings(block))
            for block in bjh_blocks
        ):
            options["vendor_bjh_thickness_method"] = "harkins_jura"
            options["vendor_bjh_thickness_source"] = "SMP subsets 315/316 selected thickness model"
        return options

    @staticmethod
    def _parse_single_point_options(block: bytes, method_options: dict[str, object]) -> dict[str, object]:
        """Micromeritics single-point BET: report target and calculation rule.

        Verified against 10 same-run SMP/XLS report pairs: the reported area is
        V(1 - P/P0) at the measured adsorption point nearest the target (no
        interpolation). The target itself is read only for TriStar II Plus,
        whose subset 306 ("Summary" options) stores it at offset 93 (407-byte
        layout) or 23 (341-byte layout); 5/5 such pairs reproduce the report.
        3Flex 3500 uses the same 341-byte layout (3/3 reports reproduced).
        TriStar II 3020 / ASAP 2460 targets are not identified, so none is read.
        """
        options: dict[str, object] = {"single_point_bet_method": "nearest_measured_point"}
        if method_options.get("instrument_model") not in ("TriStar II Plus", "3Flex 3500") or len(block) < 101:
            return options
        offset = None
        if len(block) == 407 and b"S\x00u\x00m\x00m\x00a\x00r\x00y\x00" in block[:60]:
            offset = 93
        elif len(block) == 341:
            offset = 23
        value = _read_double(block, offset) if offset is not None else None
        if value is not None and 0.0 < value < 1.0:
            options.update(single_point_bet_pressure=float(value),
                           single_point_bet_pressure_source=f"SMP subset 306 offset {offset}")
        return options

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

        if self._is_tristar_plus_v3(block):
            return self._parse_tristar_plus_v3_free_space(block, run, adsorptive_properties)

        asap_free_space = self._parse_asap_free_space(block, run, adsorptive_properties)
        if asap_free_space is not None:
            return asap_free_space
        if "ASAP 2425" in self._asap_software_text(block):
            # Mode 4 (Y-Zeolite Nitrogen example) is not decoded; the generic
            # fallbacks below would read unrelated doubles as free space.
            return FreeSpaceInfo(None, None, None, None, None, None, None, None, "asap_2425_free_space_mode_not_decoded")

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
        run: RunConditions,
        adsorptive_properties: AdsorptiveProperties | None = None,
    ) -> FreeSpaceInfo | None:
        software = self._asap_software_text(block)
        if not software:
            return None
        sample_tube_rel = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if sample_tube_rel is None:
            return None

        if "ASAP 2425" in software:
            # Other modes can retain plausible measured volumes from an
            # earlier run. They must not silently use the measured formula.
            if sample_tube_rel + 196 >= len(block) or block[sample_tube_rel + 196] not in (0, MICROACTIVE_FREE_SPACE_ENTERED):
                return None

        # ASAP 2425: mode byte at Sample Tube + 196; 2 = entered free space,
        # stored at -48/-40 instead of the measured -24/-16 (MicroActive 7.00).
        entered = (
            "ASAP 2425" in software
            and sample_tube_rel + 196 < len(block)
            and block[sample_tube_rel + 196] == MICROACTIVE_FREE_SPACE_ENTERED
        )
        cold = _read_double(block, sample_tube_rel - (48 if entered else 24))
        warm = _read_double(block, sample_tube_rel - (40 if entered else 16))
        if cold is None or warm is None or not (0.0 < float(warm) < float(cold) < 300.0):
            return None

        ambient_temperature = ASSUMED_AMBIENT_TEMPERATURE_K
        if "ASAP 2020 Plus" in software:
            vfree = ASAP_2020_PLUS_VFREE_COLD_COEFF * float(cold) + ASAP_2020_PLUS_VFREE_WARM_COEFF * float(warm)
            vbath = ASAP_2020_PLUS_VBATH_DIFF_COEFF * (float(cold) - float(warm))
            source = "asap_2020_plus_sample_tube_fields"
        elif "ASAP 2020" in software:
            # Legacy 2020 V4 stores Vfc and Vfw in the two Sample Tube fields.
            # The original report calculation uses Vfc directly for the
            # ideal-gas term and derives the immersed volume from the measured
            # warm/cold pair (Calculations - 2020 ASAP, pp. 51-53).
            bath_temperature = run.bath_temperature_K
            if bath_temperature is None or not (50.0 < float(bath_temperature) < 150.0):
                bath_temperature = 77.35
            denominator = 1.0 - float(bath_temperature) / ambient_temperature
            if denominator <= 0.0:
                return None
            vfree = float(cold)
            vbath = (float(cold) - float(warm)) / denominator
            source = "asap_2020_v4_measured_free_space_fields"
        else:
            vfree = ASAP_2460_VFREE_COLD_COEFF * float(cold) + ASAP_2460_VFREE_WARM_COEFF * float(warm)
            vbath = (
                ASAP_2460_VBATH_INTERCEPT
                + ASAP_2460_VBATH_COLD_COEFF * float(cold)
                + ASAP_2460_VBATH_WARM_COEFF * float(warm)
            )
            # ASAP 2425 example files fit the same coefficients exactly.
            source = "asap_2425_sample_tube_fields" if "ASAP 2425" in software else "asap_2460_sample_tube_fields"

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
            ambient_temperature_K_assumed=ambient_temperature,
        )

    def _asap_software_text(self, block: bytes) -> str:
        for item in self._read_mic_strings(block):
            if re.search(r"\bASAP\s+(?:2460|2425|2020(?:\s+Plus)?)\s+(?:Version\s+)?V?\d", item.text, re.IGNORECASE):
                return item.text
        # Some ASAP 2425 example files keep only the bare model name.
        for item in self._read_mic_strings(block):
            if item.text.strip() == "ASAP 2425":
                return item.text.strip()
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

        # Before "Sample Tube": entered analysis/ambient free space at -48/-40,
        # measured ones at -24/-16. The byte at +196 is the free-space mode the
        # vendor uses (0 measured, 2 entered; checked in MicroActive 7.00 and
        # against the E41 report). The calculations below use whichever applies.
        mode = block[sample_tube_rel + 196] if sample_tube_rel + 196 < len(block) else 0
        values_at = sample_tube_rel - (48 if mode == MICROACTIVE_FREE_SPACE_ENTERED else 24)
        warm = _read_double(block, values_at)
        cold = _read_double(block, values_at + 8)
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
            # Display fields follow the MicroActive report: "Warm Free Space" is the
            # value at tube-16 (local name cold) and "Cold Free Space" the one at
            # tube-24 (local name warm); verified on 10 same-run report pairs. The
            # local names stay because the calculations above were fitted with them.
            cold_free_space_cm3=float(warm),
            warm_free_space_cm3=float(cold),
            stem_volume_cm3=0.0,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source=source,
            ambient_temperature_K_assumed=float(ambient_temperature),
        )

    def _parse_tristar_plus_v3_free_space(
        self, block: bytes, run: RunConditions, props: AdsorptiveProperties | None,
    ) -> FreeSpaceInfo:
        missing = FreeSpaceInfo(None, None, None, None, None, None, None, None, "tristar_plus_v3_missing_free_space")
        tube = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if tube is None:
            return missing
        cold = _read_double(block, tube - 24)
        warm = _read_double(block, tube - 16)
        correction = _read_double(block, tube + 218)
        if cold is None or warm is None or correction is None:
            return missing
        if not (0.0 < warm < cold < 300.0 and -warm < correction <= 0.0):
            return missing
        ambient = _read_double(block, tube - 237)
        if ambient is None or not 250.0 <= ambient <= 330.0:
            ambient = ASSUMED_AMBIENT_TEMPERATURE_K
        bath = run.bath_temperature_K
        nonideality = props.nonideality_factor if props else None
        if bath is None or not 50.0 < bath < 150.0 or nonideality is None or not 0.0 <= nonideality < 0.01:
            return missing
        # The file-specific signed correction reproduces the measured warm
        # and cold volumes in subset 705. Old per-port constants belong to a
        # different instrument and must not be applied to these files.
        measured_cold, measured_warm = cold + correction, warm + correction
        vfree = VFREE_INTERCEPT + VFREE_COLD_COEFF * cold + VFREE_WARM_COEFF * warm + VFREE_STEM_COEFF * correction
        vbath = (cold - warm) / (1.0 - bath / ambient)
        return FreeSpaceInfo(
            analysis_entered_cm3=cold,
            ambient_entered_cm3=warm,
            nonideality_factor=nonideality,
            cold_free_space_cm3=measured_cold,
            warm_free_space_cm3=measured_warm,
            stem_volume_cm3=correction,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source="tristar_ii_plus_v3_sample_tube_fields_empirical",
            ambient_temperature_K_assumed=ambient,
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
        tristar_plus_v3 = self._is_tristar_plus_v3(block)
        if not tristar_plus_v3 and not any("MicroActive for TriStar II Plus" in item.text for item in strings):
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
            if elapsed is not None and tristar_plus_v3:
                elapsed *= 60
            if elapsed is not None and not (0 <= elapsed <= 60 * 60 * 24 * 30):
                elapsed = None
            quantity_per_g = self._calculate_quantity(absolute, quantity, sample, free_space)
            if quantity_per_g is None and not tristar_plus_v3:
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
                    quantity_adsorbed_mmol_g=quantity_per_g / CM3_STP_PER_MMOL if quantity_per_g is not None else None,
                )
            )
        return points

    # --- Row-frame SMP (TriStar II 3020 V1.0x, early ASAP 2020 Plus) -------------
    # Subset 303 record version 13 written by these versions keeps each point in
    # a fixed 58-byte frame: 05 00 | E0 + NaN double | 04 00 | P_abs, P/P0,
    # Q_dosed (3 little-endian doubles) | u32 | padding. The same frames occur in
    # TriStar II Plus files, where the existing parser's offsets, pressures and
    # raw quantities match the frame values exactly (119 of 121 files).
    _ROW_FRAME_PREFIX = re.compile(rb"\x05\x00\xe0\x00\x00\x00\x00\x00\x00\xf8\x7f\x04\x00")
    _ROW_FRAME_STRIDE = 58

    def _row_frame_family(self, block: bytes) -> str:
        if len(block) <= 18 or block[18] != 13:
            return ""
        texts = [item.text for item in self._read_mic_strings(block[:400]) if item.text]
        software = texts[0].strip() if texts else ""
        if re.fullmatch(r"TriStar II 3020 V1\.\d\d", software):
            return "TriStar II 3020"
        if software == "ASAP 2020 Plus":
            return "ASAP 2020 Plus"
        return ""

    def _scan_row_frames(self, block: bytes, payload_size: int) -> list[tuple[int, float, float, float]]:
        limit = min(len(block), payload_size + 18)
        frames: list[tuple[int, float, float, float]] = []
        for match in self._ROW_FRAME_PREFIX.finditer(block, 0, limit):
            rel = match.end()
            if rel + 24 > limit:
                break
            absolute, relative, quantity = struct.unpack_from("<ddd", block, rel)
            if not (math.isfinite(absolute) and math.isfinite(relative) and math.isfinite(quantity)):
                continue
            # P0 bounds cover krypton (about 2.4 mmHg) as well as nitrogen/argon.
            if not (0.0 < absolute < 1200.0 and 0.0 < relative < 1.2 and -1000.0 < quantity < 100000.0):
                continue
            if not (0.5 < absolute / relative < 1200.0):
                continue
            frames.append((rel, absolute, relative, quantity))
        # Keep the longest run with the fixed stride; stray matches elsewhere
        # in the block are not point rows.
        best: list[tuple[int, float, float, float]] = []
        run: list[tuple[int, float, float, float]] = []
        for frame in frames:
            if run and frame[0] - run[-1][0] != self._ROW_FRAME_STRIDE:
                run = []
            run.append(frame)
            if len(run) > len(best):
                best = list(run)
        return best if len(best) >= 3 else []

    @staticmethod
    def _row_frame_analysis_temperature(run_block: bytes) -> float | None:
        # Subset 302: the analysis (bath) temperature sits 149 bytes before the
        # P0/temperature table (rows 18 bytes apart). MicroActive reports this
        # value (77.3 K) as "analysis temperature"; the 77.35 K read elsewhere
        # is a table entry.
        for start in range(149, len(run_block) - 4 * 18 - 8):
            temps = [_read_double(run_block, start + 18 * k) for k in range(4)]
            if all(t is not None and 50.0 < t < 150.0 for t in temps) and all(a < b for a, b in zip(temps, temps[1:])):
                value = _read_double(run_block, start - 149)
                if value is not None and 50.0 < value < 150.0:
                    return value
        return None

    def _asap_row_frame_bath_temperature(self, run_block: bytes, sample_block: bytes, payload_size: int, mnemonic: str) -> float | None:
        """Analysis bath temperature MicroActive uses for an ASAP 2020 Plus row-frame file.

        Subset 302: the entered temperature is the double 31 bytes before the
        adsorptive mnemonic string (followed by standard pressure and ambient
        temperature); the byte 18 before it is 1 when the temperature is
        derived from P0. Then MicroActive takes the mean of the measured P0
        records in subset 303 and interpolates T linearly in ln(P0) over the
        P0/T table of subset 302: 13X gives 77.183016 K vs 77.183008 K
        (float32) reported by MicroActive; Y-Zeolite uses the entered 77.15 K.
        """
        entered_at = None
        for item in self._read_mic_strings(run_block):
            if item.text != mnemonic:
                continue
            at = item.rel_offset - 31
            temperature = _read_double(run_block, at)
            standard = _read_double(run_block, at + 8)
            ambient = _read_double(run_block, at + 16)
            if (
                temperature is not None and 50.0 < temperature < 150.0
                and standard is not None and 500.0 < standard < 900.0
                and ambient is not None and 250.0 < ambient < 330.0
                and at >= 18
            ):
                entered_at = at
                break
        if entered_at is None:
            return None
        entered = float(_read_double(run_block, entered_at))
        if run_block[entered_at - 18] != 1:
            return entered
        table: list[tuple[float, float]] = []
        for at in range(entered_at, len(run_block) - 18):
            if run_block[at:at + 2] != b"\x02\x00":
                continue
            rows = []
            pos = at
            while pos + 18 <= len(run_block) and run_block[pos:pos + 2] == b"\x02\x00":
                pressure, temperature = struct.unpack_from("<dd", run_block, pos + 2)
                rows.append((pressure, temperature))
                pos += 18
            rows = [(p, t) for p, t in rows if 100.0 < p < 1500.0 and 50.0 < t < 150.0]
            if len(rows) >= 3 and all(a[0] < b[0] for a, b in zip(rows, rows[1:])):
                table = rows
                break
        records = self._parse_po_records(sample_block, payload_size)
        if len(table) < 2 or not records:
            return entered
        mean_p0 = sum(r.saturation_pressure_mmHg for r in records) / len(records)
        logs = [math.log(p) for p, _ in table]
        x = math.log(mean_p0)
        if x <= logs[0]:
            i = 0
        elif x >= logs[-1]:
            i = len(logs) - 2
        else:
            i = next(k for k in range(len(logs) - 1) if logs[k] <= x <= logs[k + 1])
        t0, t1 = table[i][1], table[i + 1][1]
        return t0 + (t1 - t0) * (x - logs[i]) / (logs[i + 1] - logs[i])

    def _apply_asap_2425_transpiration(
        self,
        isotherm: list[IsothermPoint],
        blocks: dict[int, bytes],
        subsets: Sequence[SubsetEntry],
        props: AdsorptiveProperties | None,
    ) -> tuple[list[IsothermPoint], dict[str, object]]:
        """Thermal transpiration in ASAP 2425 files.

        Unlike the MicroActive TriStar/ASAP files, the 2425 stores the measured
        absolute pressure next to an already corrected P/P0, and its quantities
        use the measured pressure. MicroActive 7.00 reports the absolute pressure
        times the Weber ratio (Y-Zeolite Reference Material: 17 points to 1e-16).
        """
        diameter = self._row_frame_transpiration_setting(blocks.get(304, b""))
        if diameter is None:
            return isotherm, {}
        hard_sphere = props.thermal_transpiration_hard_sphere_A if props else None
        bath = self._asap_row_frame_bath_temperature(
            blocks.get(302, b""), blocks.get(303, b""), self._payload_size(subsets, 303), props.mnemonic if props else "",
        )
        if hard_sphere is None or not 1.0 < hard_sphere < 10.0 or bath is None:
            return isotherm, {"thermal_transpiration": "selected_in_file_but_inputs_missing"}
        corrected: list[IsothermPoint] = []
        active = True
        count = 0
        for point in isotherm:
            ratio = self._weber_transpiration_ratio(point.absolute_pressure_mmHg, diameter, hard_sphere, bath) if active else 1.0
            if ratio >= 0.99:
                active = False
                corrected.append(point)
                continue
            count += 1
            corrected.append(replace(point, absolute_pressure_mmHg=point.absolute_pressure_mmHg * ratio))
        return corrected, {
            "thermal_transpiration": "weber_micromeritics_asap_2425_absolute_pressure",
            "thermal_transpiration_tube_diameter_mm": diameter,
            "thermal_transpiration_hard_sphere_A": hard_sphere,
            "thermal_transpiration_bath_temperature_K": bath,
            "thermal_transpiration_corrected_points": count,
        }

    def _apply_row_frame_transpiration(
        self,
        isotherm: list[IsothermPoint],
        blocks: dict[int, bytes],
        subsets: Sequence[SubsetEntry],
        props: AdsorptiveProperties | None,
    ) -> tuple[list[IsothermPoint], dict[str, object]]:
        diameter = self._row_frame_transpiration_setting(blocks.get(304, b""))
        if diameter is None:
            return isotherm, {}
        hard_sphere = props.thermal_transpiration_hard_sphere_A if props else None
        bath = self._asap_row_frame_bath_temperature(
            blocks.get(302, b""), blocks.get(303, b""), self._payload_size(subsets, 303), props.mnemonic if props else "",
        )
        if hard_sphere is None or not 1.0 < hard_sphere < 10.0 or bath is None:
            return isotherm, {"thermal_transpiration": "selected_in_file_but_inputs_missing"}
        corrected: list[IsothermPoint] = []
        active = True
        count = 0
        for point in isotherm:
            ratio = self._weber_transpiration_ratio(point.absolute_pressure_mmHg, diameter, hard_sphere, bath) if active else 1.0
            if ratio >= 0.99:
                # "performed until the pressure ratio (PC/P) is >= 0.99": from
                # here on pressures are used as measured.
                active = False
                corrected.append(point)
                continue
            count += 1
            corrected.append(replace(
                point,
                absolute_pressure_mmHg=point.absolute_pressure_mmHg * ratio,
                relative_pressure=point.relative_pressure * ratio,
            ))
        return corrected, {
            "thermal_transpiration": "weber_micromeritics_2020_asap",
            "thermal_transpiration_tube_diameter_mm": diameter,
            "thermal_transpiration_hard_sphere_A": hard_sphere,
            "thermal_transpiration_bath_temperature_K": bath,
            "thermal_transpiration_corrected_points": count,
        }

    @staticmethod
    def _row_frame_transpiration_setting(report_block: bytes) -> float | None:
        # Subset 304 ends with the sample tube inside diameter (mm, 15 bytes
        # before the end) and, 8 bytes later, the "Apply thermal transpiration
        # correction" flag (1 in 13X and Y-Zeolite, 0 elsewhere).
        if len(report_block) < 40:
            return None
        at = len(report_block) - 15
        diameter = _read_double(report_block, at)
        if report_block[at + 8] != 1 or diameter is None or not 1.0 < diameter < 50.0:
            return None
        return float(diameter)

    @staticmethod
    def _weber_transpiration_ratio(
        pressure_mmHg: float, tube_diameter_mm: float, hard_sphere_A: float, bath_K: float, ambient_K: float = 298.0,
    ) -> float:
        # Calculations - 2020 ASAP (Micromeritics, Apr 2024), "Thermal Transpiration
        # Correction" (Weber). mu printed as (1+G)Y/((1+H)Y) is (1+G*Y)/(1+H*Y);
        # with that, 13X and Y-Zeolite agree with MicroActive to 2e-16.
        alpha, beta, g, h = 0.033, 0.245, 2.5, 2.0
        average = (bath_K + ambient_K) / 2.0
        y = pressure_mmHg * tube_diameter_mm * hard_sphere_A ** 2 / (2.33 * average) * 1e3
        mu = (1.0 + g * y) / (1.0 + h * y)
        f = 1.0 / (alpha * y * y + beta * y + mu)
        return 1.0 - f * (1.0 - math.sqrt(bath_K / ambient_K))

    @staticmethod
    def _real_gas_quantity(
        absolute_pressure: float,
        dosed: float,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
        compressibility: float,
        adsorbed_phase_factor: float,
    ) -> float | None:
        # MicroActive "Real gas equation of state" + "Ads.-phase F.S. correction":
        # gas in free space = x (Vc - Vb) + x Vb / Z - x k n_ads / Z, x = P/760,
        # Vb the immersed (cold) part, k = liquid volume per cm3 STP adsorbed at
        # bath temperature. Reproduces MicroActive's Activated Carbon quantities
        # to 3e-5 relative when fed MicroActive's own Z.
        if not sample.sample_mass_g or free_space.cold_free_space_cm3 is None or free_space.vbath_cm3 is None:
            return None
        x = float(absolute_pressure) / 760.0
        vc, vb = float(free_space.cold_free_space_cm3), float(free_space.vbath_cm3)
        denominator = 1.0 - x * adsorbed_phase_factor / compressibility
        if abs(denominator) < 1e-12:
            return None
        adsorbed = (float(dosed) - x * (vc - vb) - x * vb / compressibility) / denominator
        return adsorbed / float(sample.sample_mass_g)

    def _parse_row_frame_isotherm(
        self,
        block: bytes,
        payload_size: int,
        sample: SampleInfo,
        free_space: FreeSpaceInfo,
        real_gas: tuple[str, float, float] | None = None,
    ) -> list[IsothermPoint]:
        """real_gas: (adsorptive, analysis temperature K, density conversion factor)."""
        rows = self._scan_row_frames(block, payload_size)
        if not rows:
            return []
        # MicroActive 7.00 leaves out frames above saturation (P/P0 1.0008 in
        # Titania, 1.0001 in Silica Gel) and keeps 0.99976 (ASAP Silica-Alumina).
        phased = self._phase_points_dropping_saturation(rows)
        if len(phased) < 3:
            return []
        points: list[IsothermPoint] = []
        for idx, ((rel, absolute, relative, dosed), phase) in enumerate(phased, start=1):
            if real_gas is not None:
                gas, temperature, density_factor = real_gas
                z = real_gas_compressibility(gas, absolute, temperature)
                quantity = (
                    self._real_gas_quantity(absolute, dosed, sample, free_space, z, density_factor * 273.15 / temperature)
                    if z is not None
                    else None
                )
            else:
                quantity = self._calculate_quantity(absolute, dosed, sample, free_space)
            points.append(
                IsothermPoint(
                    index=idx,
                    phase=phase,
                    record_rel_offset=rel,
                    absolute_pressure_mmHg=absolute,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=dosed,
                    saturation_pressure_mmHg=absolute / relative,
                    # The u32 after the doubles rises like an elapsed time, but
                    # its unit is not confirmed for these versions.
                    elapsed_seconds=None,
                    quantity_adsorbed_cm3_g_stp=quantity,
                    quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL if quantity is not None else None,
                )
            )
        return points

    @staticmethod
    def _with_row_frame_sample_mass(sample: SampleInfo, block: bytes) -> SampleInfo:
        # Subset 301 keeps [sample mass, empty tube, tube + sample, density]
        # (confirmed by the MicroActive Mass panel: 1.1320 / 1.0000 / 2.1320 / 1.000).
        # Where the tube was not weighed the tube slot is 1.0 and the total is
        # mass + 1.0, so the total must not be taken as the sample mass.
        for rel in range(19, max(19, len(block) - 31)):
            mass, tube, total, density = struct.unpack_from("<dddd", block, rel)
            if not all(math.isfinite(value) for value in (mass, tube, total, density)):
                continue
            if not (1e-5 < mass < 100.0 and 0.0 < tube < 1000.0 and 0.0 < density < 100.0):
                continue
            if abs(mass + tube - total) <= 1e-7 * max(1.0, total):
                sample_density = density if not math.isclose(density, 1.0, abs_tol=1e-12) else sample.sample_density_g_cm3
                return replace(sample, sample_mass_g=mass, sample_density_g_cm3=sample_density)
        return replace(sample, sample_mass_g=None)

    def _parse_row_frame_free_space(
        self,
        block: bytes,
        family: str,
        run: RunConditions,
        props: AdsorptiveProperties | None,
    ) -> FreeSpaceInfo:
        missing = FreeSpaceInfo(None, None, None, None, None, None, None, None, "row_frame_missing_free_space")
        tube = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if tube is None or tube < 57 or tube + 226 > len(block):
            return missing
        if block[tube + 196] not in (0, MICROACTIVE_FREE_SPACE_ENTERED):
            return missing
        # Byte tube+196: 0 measured (tube-24/-16), 2 entered (tube-48/-40);
        # both stored as (cold, warm). Measured values are stored before the
        # correction at tube+218: stored + correction equals the "Measured free
        # space" line of the analysis log in all four TriStar example files.
        entered = block[tube + 196] == MICROACTIVE_FREE_SPACE_ENTERED
        cold = _read_double(block, tube - (48 if entered else 24))
        warm = _read_double(block, tube - (40 if entered else 16))
        correction = 0.0 if entered else (_read_double(block, tube + 218) or 0.0)
        if cold is None or warm is None or not (0.0 < warm < cold < 300.0) or not (-warm < correction <= 0.0):
            return missing
        nonideality = _read_double(block, tube - 32)
        if nonideality is None or not (0.0 <= nonideality < 0.01):
            nonideality = props.nonideality_factor if props else None
        if nonideality is None:
            return missing
        mode = "entered" if entered else "measured"
        # A stored factor of exactly 0 went with "Real gas equation of state"
        # + "Ads.-phase F.S. correction" in both files checked in MicroActive
        # (Activated Carbon, Glass). That EOS is not implemented here; the
        # ideal-gas result differs from MicroActive by up to 6.6 % at high
        # P/P0 (Activated Carbon), 0.05 % in BET.
        if nonideality == 0.0:
            mode += "_real_gas_eos_approximated"
        if family == "TriStar II 3020":
            ambient = _read_double(block, tube - 57)
            if ambient is None or not 250.0 <= ambient <= 330.0:
                ambient = ASSUMED_AMBIENT_TEMPERATURE_K
            bath = run.bath_temperature_K
            if bath is None or not 50.0 < bath < 150.0:
                return missing
            # Same TriStar II 3020 coefficients as the report-validated legacy
            # path; the correction takes the place of its stem term.
            vfree = VFREE_INTERCEPT + VFREE_COLD_COEFF * cold + VFREE_WARM_COEFF * warm + VFREE_STEM_COEFF * correction
            vbath = (cold - warm) / (1.0 - bath / ambient)
            source = f"tristar_3020_v1_row_frame_{mode}_free_space"
        else:
            ambient = ASSUMED_AMBIENT_TEMPERATURE_K
            vfree = ASAP_2020_PLUS_VFREE_COLD_COEFF * cold + ASAP_2020_PLUS_VFREE_WARM_COEFF * warm
            vbath = ASAP_2020_PLUS_VBATH_DIFF_COEFF * (cold - warm)
            source = f"asap_2020_plus_row_frame_{mode}_free_space"
        return FreeSpaceInfo(
            analysis_entered_cm3=cold,
            ambient_entered_cm3=warm,
            nonideality_factor=nonideality,
            cold_free_space_cm3=cold + correction,
            warm_free_space_cm3=warm + correction,
            stem_volume_cm3=correction,
            vbath_cm3=vbath,
            vfree_factor_cm3=vfree,
            vfree_factor_source=source,
            ambient_temperature_K_assumed=ambient,
        )

    @staticmethod
    def _row_frame_stored_bet_range(block: bytes) -> tuple[float, float] | None:
        # Subset 311 ends with 02 | P/P0 min | P/P0 max | 01 | 0.0 | 1.0 | 00 00.
        # The first pair is the report's BET range: on MicroActive's own
        # quantities it reproduces its monolayer capacity and C exactly
        # (Titania 0.046804-0.296619, Silica Gel 0.055625-0.299947). The last
        # pair (0-1) is what the generic reader picks up.
        if len(block) < 60 or block[-37] != 2 or block[-20] != 1:
            return None
        p_min, p_max = struct.unpack_from("<dd", block, len(block) - 36)
        # A lower bound of exactly 0 (13X 0-1, Activated Carbon 0-0.3) is not
        # the range MicroActive fitted: there the vendor BET (C 1.6e5 / 1763)
        # follows the automatic micropore range instead.
        if not (math.isfinite(p_min) and math.isfinite(p_max) and 0.0 < p_min < p_max <= 1.0):
            return None
        return float(p_min), float(p_max)

    # --- 3Flex 3500 raw SMP (subset 303 record version 12/13) ------------------
    # Checked against MicroActive 7.00 on 3Flex example files (N2, Ar 87 K,
    # Kr, CO2 273 K, n-butane, n-pentane): 58-byte point frames as in the
    # TriStar/ASAP row-frame files, real-gas free space with the adsorptive's
    # equation of state, and the analysis temperature from the measured P0.

    def _is_3flex_raw(self, block: bytes) -> bool:
        return len(block) > 18 and block[18] in (12, 13) and any(
            item.text == "3Flex" for item in self._read_mic_strings(block))

    def _flex_frames(self, block: bytes) -> list[tuple[int, float, float, float]]:
        # Same frames as _scan_row_frames, with P0 up to CO2 at 273 K (~26600 mmHg).
        frames: list[tuple[int, float, float, float]] = []
        for match in self._ROW_FRAME_PREFIX.finditer(block):
            rel = match.end()
            if rel + 24 > len(block):
                break
            absolute, relative, dosed = struct.unpack_from("<ddd", block, rel)
            if not (all(math.isfinite(v) for v in (absolute, relative, dosed))
                    and 0.0 < absolute < 2000.0 and 0.0 < relative < 1.2 and 0.5 < absolute / relative < 50000.0
                    and -1000.0 < dosed < 100000.0):
                continue
            if frames and rel - frames[-1][0] != 58:
                if len(frames) >= 3:
                    break
                frames = []
            frames.append((rel, absolute, relative, dosed))
        return frames if len(frames) >= 3 else []

    @staticmethod
    def _flex_p0_log(block: bytes, start: int, stop: int) -> list[float]:
        """Logged P0 values: u16 count, 10 bytes, then count x (u16 time, double P0)."""
        best: list[float] = []
        for at in range(start, max(start, stop - 12)):
            count = struct.unpack_from("<H", block, at)[0]
            first = at + 12
            if not 2 <= count <= 5000 or first + 10 * count > stop:
                continue
            records = [struct.unpack_from("<Hd", block, first + 10 * k) for k in range(count)]
            values = [value for _, value in records]
            if (all(math.isfinite(v) and 0.1 < v < 50000.0 for v in values) and max(values) / min(values) < 1.05
                    and all(a[0] < b[0] for a, b in zip(records, records[1:])) and len(values) > len(best)):
                best = values
        return best

    def _flex_analysis_temperature(
        self, run_block: bytes, block: bytes, frames, tube: int, adsorptive: str,
    ) -> tuple[float | None, str]:
        # Subset 302: entered temperature 31 bytes before the bath-gas mnemonic,
        # ambient 16 bytes after it; the byte 18 before it is 1 when the
        # temperature is derived from P0.
        entered_at = None
        for item in self._read_mic_strings(run_block):
            at = item.rel_offset - 31
            if at < 18:
                continue
            temperature, ambient = _read_double(run_block, at), _read_double(run_block, at + 16)
            if temperature is not None and 50.0 < temperature < 400.0 and ambient is not None and 250.0 < ambient < 330.0:
                entered_at = at
                break
        if entered_at is None:
            return None, "entered temperature not found"
        entered = float(_read_double(run_block, entered_at))
        if run_block[entered_at - 18] != 1:
            return entered, "entered"
        # Derived from P0: saturation temperature of the mean measured P0.
        # Normally the logged P0 of the P0 tube; when the P0 field after the
        # frames is not the default 760 (one example, "12 mm Tube N2"),
        # MicroActive used the mean of the point P0 values instead.
        frames_end = frames[-1][0] + 52
        logged = self._flex_p0_log(block, frames_end, tube)
        p0_field = _read_double(block, frames_end + 48)
        if logged and (p0_field is None or abs(p0_field - 760.0) < 1e-9):
            p0, source = sum(logged) / len(logged), "mean logged P0"
        elif logged:
            p0, source = sum(a / r for _, a, r, _ in frames) / len(frames), "mean point P0"
        else:
            p0, source = _read_double(block, tube - 90), "P0 before Sample Tube"
        if p0 is None or not 0.1 < p0 < 50000.0:
            return None, "P0 not found"
        # The P0 tube may hold the bath gas rather than the adsorptive (Kr runs
        # use N2 P0): take the gas whose saturation temperature is near the
        # entered one.
        candidates = []
        for gas in dict.fromkeys((adsorptive, "N2")):
            temperature = real_gas_saturation_temperature(gas, p0, entered)
            if temperature is not None and abs(temperature - entered) < 3.0:
                candidates.append((abs(temperature - entered), temperature, gas))
        if not candidates:
            return None, "no saturation temperature for P0"
        _, temperature, gas = min(candidates)
        return temperature, f"saturation temperature of {gas} at {source}"

    def _parse_3flex_raw(
        self, blocks: dict[int, bytes], subsets: Sequence[SubsetEntry], sample: SampleInfo,
        props: AdsorptiveProperties | None,
    ) -> dict[str, object]:
        block = blocks.get(303, b"")
        frames = self._flex_frames(block)
        if not frames:
            return {"reason": "3Flex 文件中未找到数据点"}
        tube = next((item.rel_offset for item in self._read_mic_strings(block) if item.text == "Sample Tube"), None)
        if tube is None or tube < 90 or tube + 197 > len(block):
            return {"reason": "3Flex 文件中未找到自由空间记录"}
        mode = block[tube + 196]
        if mode not in (0, 1, 4):
            return {"reason": f"3Flex 文件的自由空间方式（{mode}）尚未解析，暂不计算吸附量"}
        ambient = _read_double(block, tube - 57)
        if ambient is None or not 250.0 <= ambient <= 330.0:
            ambient = ASSUMED_AMBIENT_TEMPERATURE_K
        sample_block = blocks.get(301, b"")
        if block[18] == 12:
            mass = _read_double(sample_block, 19)
            label = next((item.rel_offset for item in self._read_mic_strings(sample_block) if item.text == "Sample:"), None)
            density = _read_double(sample_block, label + 106) if label is not None else None
        else:
            recorded = self._with_row_frame_sample_mass(sample, sample_block)
            mass, density = recorded.sample_mass_g, recorded.sample_density_g_cm3
        if mass is None or not 1e-6 < mass < 100.0:
            return {"reason": "3Flex 文件中未找到样品质量"}
        adsorptive = props.mnemonic if props else ""
        temperature, temperature_source = self._flex_analysis_temperature(
            blocks.get(302, b""), block, frames, tube, adsorptive)
        if temperature is None:
            return {"reason": f"3Flex 文件的分析温度未能确定（{temperature_source}）"}
        if mode == 4:
            # "Calculated" free space: the empty-tube cold / warm free space of
            # the tube definition (subset 1021, before the 8163.688 sentinel)
            # minus the sample volume m / rho at analysis and ambient
            # temperature. D-7 example: Vc equals MicroActive's to 1e-6; the
            # quantities to 3e-5.
            tube_block = blocks.get(1021, b"")
            empty_warm = _read_double(tube_block, len(tube_block) - 26) if len(tube_block) >= 26 else None
            empty_cold = _read_double(tube_block, len(tube_block) - 18) if len(tube_block) >= 18 else None
            if (empty_cold is None or empty_warm is None or not 0.0 < empty_warm < empty_cold < 300.0
                    or not density or not 0.0 < density < 30.0):
                return {"reason": "3Flex 文件按计算自由空间测试，但缺少样品管体积或样品密度"}
            volume = mass / density
            cold = empty_cold - volume * 273.15 / temperature
            warm = empty_warm - volume * 273.15 / ambient
        else:
            cold, warm = _read_double(block, tube - 24), _read_double(block, tube - 16)
        if cold is None or warm is None or not 0.0 < warm <= cold < 300.0:
            return {"reason": "3Flex 文件的自由空间数值无效"}
        if real_gas_compressibility(adsorptive, 1.0, temperature) is None:
            return {"reason": f"暂不支持 {adsorptive or '该吸附质'} 的真实气体状态方程，暂不计算吸附量"}
        # Immersed (cold) part; near ambient the whole free space is at the
        # analysis temperature (n-butane / n-pentane examples).
        vbath = min((cold - warm) / (1.0 - temperature / ambient), cold) if temperature < ambient else cold
        # Adsorbed-phase correction (liquid volume per cm3 STP adsorbed): from
        # the saturated liquid density (N2, Ar, CO2, n-pentane, n-hexane);
        # below the triple point the stored factor (Kr at 77 K). None when the
        # saturation pressure exceeds the maximum manifold pressure and P0 was
        # set to that maximum (n-butane at 296.65 K: P0 925 mmHg, two files).
        mean_p0 = sum(a / r for _, a, r, _ in frames) / len(frames)
        saturation = real_gas_saturation(adsorptive, temperature)
        manifold_max = props.max_manifold_pressure_mmHg if props and props.max_manifold_pressure_mmHg else 925.0
        if saturation is None:
            factor = props.density_conversion_factor if props and props.density_conversion_factor else None
            if factor is None or not 0.0 < factor < 0.05:
                return {"reason": "3Flex 文件的吸附相修正系数无效"}
            k_source = "stored density conversion factor"
        elif saturation[0] > manifold_max and abs(mean_p0 - manifold_max) < 1e-6 * manifold_max:
            factor = 0.0
            k_source = "none (P0 limited to the maximum manifold pressure)"
        else:
            factor = 1.0 / (saturation[1] * 22.414)
            k_source = "saturated liquid density (equation of state)"
        k = factor * 273.15 / temperature
        # MicroActive also converts gas to liquid volume (t-Plot micropore
        # volume and external area) with the saturated liquid density: Ar
        # ZSM-5 and n-pentane t-Plot results equal ours only with it.
        liquid_factor = 1.0 / (saturation[1] * 22.414) if saturation is not None else None
        rows = self._phase_points_dropping_saturation(frames)
        points: list[IsothermPoint] = []
        for idx, ((rel, absolute, relative, dosed), phase) in enumerate(rows, start=1):
            z = real_gas_tabulated_compressibility(adsorptive, absolute, temperature)
            if z is None or not math.isfinite(z) or z <= 0.0:
                return {"reason": f"3Flex 第 {idx} 个数据点的真实气体压缩因子无效，暂不计算吸附量"}
            x = absolute / 760.0
            denominator = 1.0 - x * k / z
            if not math.isfinite(denominator) or denominator <= 1e-12:
                return {"reason": f"3Flex 第 {idx} 个数据点的吸附相自由空间修正无效，暂不计算吸附量"}
            quantity = (dosed - x * (cold - vbath) - x * vbath / z) / denominator / mass
            points.append(IsothermPoint(
                index=idx, phase=phase, record_rel_offset=rel, absolute_pressure_mmHg=absolute,
                relative_pressure=relative, raw_internal_cm3_stp=dosed, saturation_pressure_mmHg=absolute / relative,
                elapsed_seconds=None, quantity_adsorbed_cm3_g_stp=quantity,
                quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
            ))
        options: dict[str, object] = {
            "format_family": "3Flex raw SMP",
            "flex_analysis_temperature_K": temperature,
            "flex_analysis_temperature_source": temperature_source,
            "flex_free_space_mode": mode,
            "flex_adsorbed_phase_factor_source": k_source,
            "row_frame_quantity_source": "real gas equation of state with adsorbed-phase free-space correction",
        }
        # Thermal transpiration: quantities use the stored (measured) pressure;
        # MicroActive reports P and P/P0 times the Weber ratio (13X: 8e-9).
        diameter = self._row_frame_transpiration_setting(blocks.get(304, b""))
        hard_sphere = props.thermal_transpiration_hard_sphere_A if props else None
        if diameter is not None and hard_sphere is not None and 1.0 < hard_sphere < 10.0:
            corrected, active, count = [], True, 0
            for point in points:
                ratio = self._weber_transpiration_ratio(
                    point.absolute_pressure_mmHg, diameter, hard_sphere, temperature) if active else 1.0
                if ratio >= 0.99:
                    active = False
                    corrected.append(point)
                    continue
                count += 1
                corrected.append(replace(point, absolute_pressure_mmHg=point.absolute_pressure_mmHg * ratio,
                                         relative_pressure=point.relative_pressure * ratio))
            points = corrected
            options.update({
                "thermal_transpiration": "weber_micromeritics_3flex",
                "thermal_transpiration_tube_diameter_mm": diameter,
                "thermal_transpiration_hard_sphere_A": hard_sphere,
                "thermal_transpiration_bath_temperature_K": temperature,
                "thermal_transpiration_corrected_points": count,
            })
        free_space = FreeSpaceInfo(
            analysis_entered_cm3=cold, ambient_entered_cm3=warm, nonideality_factor=0.0,
            cold_free_space_cm3=cold, warm_free_space_cm3=warm, stem_volume_cm3=0.0, vbath_cm3=vbath,
            vfree_factor_cm3=cold, vfree_factor_source=f"3flex_real_gas_eos_mode_{mode}",
            ambient_temperature_K_assumed=ambient,
        )
        return {"isotherm": points, "sample": replace(sample, sample_mass_g=mass), "free_space": free_space,
                "options": options, "density_conversion_factor": liquid_factor}

    # --- Gemini VII (2390) -----------------------------------------------------
    # Point records: P_abs, P/P0, Q_dosed (3 doubles) followed by variable-length
    # tag lists; every record is preceded by E0 + NaN double + 02 00. Gemini
    # measures against a balanced reference tube, so MicroActive reports
    # quantity adsorbed = quantity dosed / sample mass (its free-space gas is 0).
    _GEMINI_PREFIX = re.compile(rb"\xe0\x00\x00\x00\x00\x00\x00\xf8\x7f\x02\x00", re.S)

    def _is_gemini(self, block: bytes) -> bool:
        # The software string follows the point table, so search the whole block.
        return b"G\x00e\x00m\x00i\x00n\x00i\x00 \x00V\x00I\x00I\x00" in block and any(
            item.text.startswith("Gemini VII") for item in self._read_mic_strings(block))

    def _gemini_rows(self, block: bytes, payload_size: int) -> list[tuple[int, float, float, float]]:
        limit = min(len(block), payload_size + 18)
        rows: list[tuple[int, float, float, float]] = []
        for match in self._GEMINI_PREFIX.finditer(block, 0, limit):
            at = match.end()
            if at + 24 > limit:
                break
            absolute, relative, dosed = struct.unpack_from("<ddd", block, at)
            if not (all(math.isfinite(v) for v in (absolute, relative, dosed))
                    and 0.0 < absolute < 1200.0 and 0.0 < relative < 1.2 and 0.5 < absolute / relative < 1200.0
                    and -1000.0 < dosed < 100000.0):
                continue
            if rows and not 30 <= at - rows[-1][0] <= 200:
                # Records follow each other closely; a distant match is other data.
                if len(rows) >= 3:
                    break
                rows = []
            rows.append((at, absolute, relative, dosed))
        return rows if len(rows) >= 3 else []

    @staticmethod
    def _phase_points_dropping_saturation(rows):
        """Pair each row with its branch and drop rows above saturation (P/P0 > 1).

        MicroActive ends the adsorption branch at the highest remaining point,
        unless a P/P0 > 1 record comes first, in which case it ends just before
        that record (TriStar Titania: ..., 0.98909, [1.0008], 0.99007 des;
        Gemini sialfull: ..., 0.99772, 0.99558 des, [1.0015], ...).
        """
        kept = [i for i, row in enumerate(rows) if row[2] <= 1.0]
        if not kept:
            return []
        top = max(rows[i][2] for i in kept)
        boundary = max(i for i in kept if rows[i][2] == top)
        first_saturation = next((i for i, row in enumerate(rows) if row[2] > 1.0), None)
        if first_saturation is not None and first_saturation < boundary:
            boundary = first_saturation - 1
        return [(rows[i], "adsorption" if i <= boundary else "desorption") for i in kept]

    @staticmethod
    def _gemini_point_tags(block: bytes, at: int) -> tuple[int, ...]:
        # After the three doubles: u16 elapsed, 1 byte, u16 tag count, tags (u16).
        # Tag 103 marks the report's BET points (fits on them equal MicroActive's
        # monolayer capacity and C on all six Gemini example files).
        count = _read_uint16(block, at + 27)
        if count is None or count > 40 or at + 29 + 2 * count > len(block):
            return ()
        return tuple(struct.unpack_from("<" + "H" * count, block, at + 29)) if count else ()

    def _gemini_inputs(self, blocks: dict[int, bytes], mnemonic: str) -> dict[str, object] | None:
        """Mass, density, bath temperature and free-space difference of a Gemini file.

        Checked against MicroActive 7.00 on five example files: mass at subset
        301 offset 19; density at "Sample:" + 86; bath temperature 15 bytes
        before the adsorptive mnemonic in subset 302; at "Sample Tube" in 303
        the non-ideality factor (-32), the free-space difference (-24) and the
        mode byte (+196): 0 measured (stored value), 4 calculated
        (-(mass/density) * 273.15 / T), 6 none.
        """
        sample_block, run_block, block = blocks.get(301, b""), blocks.get(302, b""), blocks.get(303, b"")
        mass = _read_double(sample_block, 19)
        if mass is None or not 1e-6 < mass < 100.0:
            return None
        label = next((s.rel_offset for s in self._read_mic_strings(sample_block) if s.text == "Sample:"), None)
        density = _read_double(sample_block, label + 86) if label is not None else None
        temperature = next((
            _read_double(run_block, s.rel_offset - 15) for s in self._read_mic_strings(run_block)
            if mnemonic and s.text == mnemonic and _read_double(run_block, s.rel_offset - 15) is not None
            and 50.0 < _read_double(run_block, s.rel_offset - 15) < 400.0), None)
        tube = next((s.rel_offset for s in self._read_mic_strings(block) if s.text == "Sample Tube"), None)
        if tube is None or tube < 32 or tube + 197 > len(block):
            return None
        nonideality = _read_double(block, tube - 32)
        if nonideality is None or not 0.0 <= nonideality < 0.01:
            return None
        mode = block[tube + 196]
        if mode == 0:
            difference = _read_double(block, tube - 24)
            source = "measured"
        elif mode == 4:
            if not density or density <= 0 or temperature is None:
                return None
            difference = -(mass / density) * 273.15 / temperature
            source = "calculated_from_density"
        elif mode == 6:
            difference, source = 0.0, "none"
        else:
            return None
        if difference is None or not math.isfinite(difference) or abs(difference) > 50.0:
            return None
        return {"mass": mass, "density": density, "temperature": temperature, "nonideality": nonideality,
                "difference": difference, "mode": source}

    def _parse_gemini_isotherm(self, block: bytes, payload_size: int, inputs: dict[str, object] | None) -> list[IsothermPoint]:
        rows = self._gemini_rows(block, payload_size)
        if not rows or inputs is None:
            return []
        mass = float(inputs["mass"])
        difference = float(inputs["difference"])
        alpha = float(inputs["nonideality"])
        phased = self._phase_points_dropping_saturation(rows)
        points = []
        for idx, ((at, absolute, relative, dosed), phase) in enumerate(phased, start=1):
            # Gas not balanced by the reference tube: x * V * (1 + alpha * P).
            x = absolute / 760.0
            quantity = (dosed - x * difference * (1.0 + alpha * absolute)) / mass
            points.append(IsothermPoint(
                index=idx,
                phase=phase,
                record_rel_offset=at,
                absolute_pressure_mmHg=absolute,
                relative_pressure=relative,
                raw_internal_cm3_stp=dosed,
                saturation_pressure_mmHg=absolute / relative,
                elapsed_seconds=None,
                quantity_adsorbed_cm3_g_stp=quantity,
                quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
            ))
        return points

    # --- Legacy ASAP SMP, subset 303 record version 6 ("ASAP 2010 V5.00") -------
    @staticmethod
    def _legacy_v6_records(block: bytes) -> list[tuple[int, float, float, float, int, tuple[int, ...]]]:
        # u16 point count at 86; records from 119: P, P/P0, Q_dosed (doubles),
        # u16 elapsed, u16 tag count, tags (u16 each), one pad byte. Decoded
        # values equal MicroActive's absolute pressure and quantity dosed
        # exactly (Carbon Black D-4, Alumina Kr example files).
        if len(block) < 150 or block[18] != 6:
            return []
        count = _read_uint16(block, 86) or 0
        rows = []
        at = 119
        for _ in range(count):
            if at + 28 > len(block):
                return []
            absolute, relative, dosed = struct.unpack_from("<ddd", block, at)
            elapsed, tags = struct.unpack_from("<HH", block, at + 24)
            if tags > 8 or at + 29 + 2 * tags > len(block):
                return []
            if not (all(math.isfinite(v) for v in (absolute, relative, dosed)) and 0.0 < absolute < 1200.0
                    and 0.0 < relative < 1.2 and 0.5 < absolute / relative < 1200.0 and -1000.0 < dosed < 100000.0):
                return []
            tag_values = struct.unpack_from("<" + "H" * tags, block, at + 28) if tags else ()
            rows.append((at, absolute, relative, dosed, elapsed, tag_values))
            at += 28 + 2 * tags + 1
        return rows if len(rows) >= 3 else []

    def _parse_legacy_v6(
        self, blocks: dict[int, bytes], sample: SampleInfo,
    ) -> tuple[list[IsothermPoint], FreeSpaceInfo, AdsorptiveProperties, dict[str, object]] | None:
        block = blocks.get(303, b"")
        rows = self._legacy_v6_records(block)
        if not rows:
            return None
        # Free space before the ASCII "Sample Tube" name: entered cold/warm at
        # -33/-25, measured cold/warm at -17/-9 (both example files measured).
        tube = block.find(b"\x01Sample Tube")
        if tube < 40:
            return None
        tube += 1
        cold, warm = _read_double(block, tube - 17), _read_double(block, tube - 9)
        source = "legacy_asap_v6_measured_free_space"
        if cold is None or warm is None or not (0.0 < warm < cold < 300.0):
            cold, warm = _read_double(block, tube - 33), _read_double(block, tube - 25)
            source = "legacy_asap_v6_entered_free_space"
        if cold is None or warm is None or not (0.0 < warm < cold < 300.0):
            return None
        adsorptive_block = blocks.get(320, b"")
        nonideality = _read_double(adsorptive_block, 74)
        if nonideality is None or not 0.0 <= nonideality < 0.01:
            return None
        # MicroActive's own quantities back-solve to the ASAP 2020 Plus
        # coefficients (0.995947 / 0.004063 vs 0.995946 / 0.004054).
        free_space = FreeSpaceInfo(
            analysis_entered_cm3=cold,
            ambient_entered_cm3=warm,
            nonideality_factor=nonideality,
            cold_free_space_cm3=cold,
            warm_free_space_cm3=warm,
            stem_volume_cm3=0.0,
            vbath_cm3=ASAP_2020_PLUS_VBATH_DIFF_COEFF * (cold - warm),
            vfree_factor_cm3=ASAP_2020_PLUS_VFREE_COLD_COEFF * cold + ASAP_2020_PLUS_VFREE_WARM_COEFF * warm,
            vfree_factor_source=source,
        )
        max_relative = max(row[2] for row in rows)
        max_index = max(i for i, row in enumerate(rows) if row[2] == max_relative)
        points = []
        for idx, (at, absolute, relative, dosed, _elapsed, _tags) in enumerate(rows, start=1):
            quantity = self._calculate_quantity(absolute, dosed, sample, free_space)
            points.append(IsothermPoint(
                index=idx,
                phase="adsorption" if idx - 1 <= max_index else "desorption",
                record_rel_offset=at,
                absolute_pressure_mmHg=absolute,
                relative_pressure=relative,
                raw_internal_cm3_stp=dosed,
                saturation_pressure_mmHg=absolute / relative,
                elapsed_seconds=None,
                quantity_adsorbed_cm3_g_stp=quantity,
                quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL if quantity is not None else None,
            ))
        if any(point.quantity_adsorbed_cm3_g_stp is None for point in points):
            return None
        text = block.decode("latin1")
        software = re.search(r"ASAP \d{4}[ A-Za-z0-9.]*", text)
        name = adsorptive_block[19:48].split(b"\x00")[0].decode("latin1", "replace").strip()
        mnemonic = re.sub(r"[^A-Za-z0-9]", "", adsorptive_block[60:68].split(b"\x00")[0].decode("latin1", "replace"))
        # ASCII adsorptive record: max manifold 66, non-ideality 74, density
        # conversion 82, hard sphere diameter 90, cross-section 98 (N2 0.162,
        # Kr 0.21 nm2 as MicroActive reports).
        max_manifold = _read_double(adsorptive_block, 66)
        hard_sphere = _read_double(adsorptive_block, 90)
        cross_section = _read_double(adsorptive_block, 98)
        density = _read_double(adsorptive_block, 82)
        if cross_section is None or not 0.05 < cross_section < 1.0:
            return None
        properties = AdsorptiveProperties(
            adsorptive=name,
            mnemonic=mnemonic.upper() if mnemonic.upper() == "KR" else mnemonic,
            max_manifold_pressure_mmHg=max_manifold,
            max_manifold_pressure_kPa=max_manifold * MMHG_TO_KPA if max_manifold is not None else None,
            nonideality_factor=nonideality,
            density_conversion_factor=density,
            thermal_transpiration_hard_sphere_A=hard_sphere,
            thermal_transpiration_hard_sphere_nm=hard_sphere / 10.0 if hard_sphere is not None else None,
            molecular_cross_sectional_area_nm2=cross_section,
            ui_field_rel101=None,
            psat_table=[],
        )
        options: dict[str, object] = {
            "instrument_manufacturer": "Micromeritics",
            "instrument_model": "ASAP 2010" if "2010" in text else "ASAP (legacy)",
            "format_family": "legacy ASAP v6 SMP",
            "legacy_v6_point_tags": [list(row[5]) for row in rows],
            "legacy_v6_validation": "points, dosed gas and free-space model checked against MicroActive 7.00 "
                                    "(Carbon Black D-4, Alumina Kr example files)",
        }
        if software:
            options["instrument_software"] = software.group(0).strip()
        return points, free_space, properties, options

    def _no_isotherm_reason(self, blocks: dict[int, bytes], sample: SampleInfo) -> str:
        if 303 not in blocks:
            texts = " ".join(
                item.text for subset_id in (100, 302, 304) for item in self._read_mic_strings(blocks.get(subset_id, b"")) if item.text
            ).lower()
            for keys, label in (
                (("mercury", "penetrometer", "autopore"), "压汞"),
                (("chemisorption", "chemi", "tpr", "tpd", "pulse"), "化学吸附"),
                (("pycnometer", "accupyc"), "真密度"),
                (("breakthrough",), "穿透曲线"),
                (("sedigraph", "particle"), "粒度"),
            ):
                if any(key in texts for key in keys):
                    return f"该文件似乎是{label}数据，不含物理吸附等温线"
            return "文件中没有物理吸附分析数据块（SUBSET303），可能不是物理吸附文件"
        record_version = blocks[303][18] if len(blocks[303]) > 18 else None
        if record_version is not None and record_version < 12:
            return f"物理吸附数据块为旧版格式（记录版本 {record_version}），当前尚不支持"
        if not self._parse_log_messages(blocks.get(705, b"")):
            return "文件中没有分析记录，可能是尚未测试的样品/模板文件"
        return ""

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

        # ASAP 2425 example files include Kr (P0 ~1.7 mmHg) and CO2 at 273 K
        # (P0 ~26000 mmHg); only the 66-byte chain is accepted there.
        rows = self._scan_asap_point_rows(block, wide_saturation="ASAP 2425" in self._asap_software_text(block))
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

    def _scan_asap_point_rows(self, block: bytes, wide_saturation: bool = False) -> list[tuple[int, float, float, float]]:
        candidates: list[tuple[int, float, float, float]] = []
        saturation_range = (0.5, 40000.0) if wide_saturation else (700.0, 800.0)
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
            if saturation_range[0] <= saturation <= saturation_range[1]:
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
        if wide_saturation:
            return best if len(best) >= 3 else []
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
        # The numeric fields follow the mnemonic string at fixed distances
        # (+8 ... +50) in both layouts above. The name before it has variable
        # length, so when the mnemonic is not where the fixed layout expects it
        # (e.g. "nitrogen" in a long block, "Krypton @ 77.35 K"), read the
        # fields relative to the actual mnemonic instead of the wrong bytes.
        actual = next(
            (item for item in strings if name_rel < item.rel_offset <= 140 and 0 < len(item.text) <= 8),
            None,
        )
        actual_mnemonic = actual.rel_offset if actual is not None else None
        if actual_mnemonic is not None and actual_mnemonic != mnemonic_rel and not any(
            item.rel_offset == mnemonic_rel for item in strings
        ):
            # The distances hold for a two-character mnemonic ("N2", "Ar");
            # longer ones ("CO2", "nC4") push the fields back by 2 bytes per
            # character (3Flex CO2 / butane / pentane examples).
            shift = 2 * max(0, len(actual.text) - 2)
            mnemonic_rel = actual_mnemonic
            max_rel = mnemonic_rel + 8 + shift
            nonideal_rel = mnemonic_rel + 18 + shift
            density_rel = mnemonic_rel + 26 + shift
            hard_sphere_rel = mnemonic_rel + 34 + shift
            cross_section_rel = mnemonic_rel + 42 + shift
            molecular_or_ui_rel = mnemonic_rel + 50 + shift
            psat_start = mnemonic_rel + 71 + shift
        psat_count_rel = psat_start - 4

        max_manifold = _read_double(block, max_rel)
        hard_sphere_a = _read_double(block, hard_sphere_rel)
        psat_table: list[dict[str, float | int]] = []
        if len(block) > 600:
            count = _read_uint32(block, psat_count_rel) or 0
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
                or "ASAP 2425 Version" in text
                or "ASAP 2020 Plus Version" in text
                or re.search(r"\bASAP\s+2020\s+(?:Version\s+)?V?\d", text, re.IGNORECASE)
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
        elif "ASAP 2425" in joined:
            model = "ASAP 2425"
        elif "ASAP 2020 Plus" in joined:
            model = "ASAP 2020 Plus"
        elif "ASAP 2020" in joined:
            model = "ASAP 2020"
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
            normalized_event = text_lower.rstrip(" .")
            event_text = event_time.strftime("%Y-%m-%d %H:%M:%S")
            event_key = _datetime_sort_key(event_time)

            if (
                normalized_event == "analysis started"
                or "started analysis of file" in text_lower
                or "starting a sample analysis" in text_lower
                or ("analysis on file" in text_lower and " started" in text_lower)
            ):
                events.setdefault("log_started_time", event_text)
                events.setdefault("log_started_sort_key", event_key)
                events.setdefault("log_started_event", text)
                events.setdefault("_log_started_datetime", event_time)
            elif (
                normalized_event == "analysis done"
                or "finished a sample analysis" in text_lower
                or ("analysis on file" in text_lower and " done" in text_lower)
            ):
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
        if result.po_records and result.po_records[0].index == 0:
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
                    "quantity_status": result.method_options.get('quantity_status', ''),
                    "quantity_notice": result.method_options.get('import_notice', ''),
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


# Micromeritics report options ("SUBSET311" BET, "312" Langmuir, "314" t-Plot).
# Verified against same-run XLS reports: TriStar II 3020 (18), ASAP 2460 (14),
# ASAP 2020 Plus (1), TriStar II Plus 3.04 (7 of 9; the other two reports were
# exported before the saved selection changed) and 3Flex (3).
MICROMERITICS_THICKNESS_MODELS = (
    "halsey", "harkins_jura", "carbon_black_stsa", "broekhoff_de_boer", "reference", "kjs",
)
MICROMERITICS_REPORT_SETTINGS_MODELS = ("TriStar II 3020", "TriStar II Plus", "ASAP 2460", "ASAP 2425", "ASAP 2020 Plus", "3Flex 3500")
_TRISTAR_3020_POINT_TAG_OFFSET = 27
_TAG_BET = 0x67
_TAG_LANGMUIR = 0x68


def _report_range_pair(block: bytes) -> tuple[float, float] | None:
    """Selection interval saved at the end of a report-options subset.

    Layout: ``02 | lo1 hi1 | flag u16 | lo2 hi2 | 00 00``. With flag 0 the
    vendor uses the first interval; with flag 1 it uses the points inside both
    intervals (their intersection). Other flag values are not decoded.
    """
    n = len(block)
    if n < 40 or block[n - 2:] != b"\x00\x00" or block[n - 37] != 2:
        return None
    flag = _read_uint16(block, n - 20)
    lo1, hi1 = struct.unpack_from("<dd", block, n - 36)
    lo2, hi2 = struct.unpack_from("<dd", block, n - 18)
    if flag not in (0, 1) or not all(math.isfinite(v) for v in (lo1, hi1, lo2, hi2)):
        return None
    lo, hi = (lo1, hi1) if flag == 0 else (max(lo1, lo2), min(hi1, hi2))
    return (lo, hi) if 0.0 <= lo < hi else None


def _tristar_3020_point_tags(block: bytes, record_offset: int) -> tuple[int, ...] | None:
    """Per-point report tags after each TriStar II 3020 isotherm record.

    0x67 BET, 0x68 Langmuir, 0x69 t-Plot, 0x6a/0x6b BJH adsorption/desorption,
    0x64 single-point pore volume (matched to the report tables).
    """
    at = record_offset + _TRISTAR_3020_POINT_TAG_OFFSET
    count = _read_uint16(block, at)
    if count is None or not 1 <= count <= 16 or at + 2 + 2 * count > len(block):
        return None
    codes = tuple(struct.unpack_from(f"<{count}H", block, at + 2))
    return codes if all(0x60 <= code <= 0x9F for code in codes) else None


def _contiguous_pressure_range(selected, adsorption) -> tuple[float, float] | None:
    """(min, max) P/P0 when the selected points form one unbroken run."""
    if len(selected) < 2:
        return None
    chosen = {id(point) for point in selected}
    flags = [id(point) in chosen for point in adsorption]
    first, last = flags.index(True), len(flags) - 1 - flags[::-1].index(True)
    if not all(flags[first:last + 1]):
        return None
    return float(adsorption[first].relative_pressure), float(adsorption[last].relative_pressure)


def _micromeritics_t_plot_settings(block: bytes) -> dict[str, object]:
    if len(block) < 80:
        return {}
    if block[18] == 5:  # ASAP 2460 / 3Flex layout: model index right after the header
        index = _read_uint16(block, 19)
    elif block[65:69] == b"\x69\x00\x01\x00":  # TriStar II 3020 / Plus / ASAP 2020 Plus
        index = _read_uint16(block, 69)
    else:
        return {}
    anchor = block.find(b"\x00\x00\x00\x00\x00\x00\xf0\x3f\x01\x00\x01\x00\x04")
    if index is None or not 0 <= index < len(MICROMERITICS_THICKNESS_MODELS) or anchor < 16:
        return {}
    t_min, t_max = struct.unpack_from("<dd", block, anchor - 16)
    if not (math.isfinite(t_min) and math.isfinite(t_max) and 0.0 < t_min < t_max < 200.0):
        return {}
    method = MICROMERITICS_THICKNESS_MODELS[index]
    options: dict[str, object] = {
        "vendor_t_plot_thickness_method": method,
        "stored_t_plot_thickness_min_nm": t_min / 10.0,
        "stored_t_plot_thickness_max_nm": t_max / 10.0,
        "stored_t_plot_range_source": "SMP subset 314 thickness Angstrom / 10",
    }
    # P/P0 window of the t-Plot data table ("02 | lo hi | 01 00", 47 bytes
    # after the anchor); points outside it are not listed or fitted.
    window_at = anchor + 47
    if window_at + 18 <= len(block) and block[window_at - 1] == 2 and block[window_at + 16:window_at + 18] == b"\x01\x00":
        p_lo, p_hi = struct.unpack_from("<dd", block, window_at)
        if math.isfinite(p_lo) and math.isfinite(p_hi) and 0.0 <= p_lo < p_hi <= 1.0:
            options["stored_t_plot_pressure_window_min"] = p_lo
            options["stored_t_plot_pressure_window_max"] = p_hi
    if method == "reference":
        for at in range(anchor, len(block) - 22):
            count = _read_uint16(block, at)
            if not count or not 5 <= count <= 500 or at + 2 + 18 * count > len(block):
                continue
            if all(block[at + 2 + 18 * k:at + 4 + 18 * k] == b"\x02\x00" for k in range(count)):
                rows = [struct.unpack_from("<dd", block, at + 4 + 18 * k) for k in range(count)]
                if all(0.0 < p <= 1.0 and 0.0 <= t for p, t in rows):
                    options["vendor_t_plot_reference_points"] = [(p, t / 10.0) for p, t in rows]
                    options["vendor_t_plot_thickness_params"] = {
                        "reference_name": "SMP subset 314 table",
                        "reference_path": "",
                        "reference_points": options["vendor_t_plot_reference_points"],
                        "interpolation": "micromeritics_akima",
                    }
                    break
    return options


def micromeritics_report_settings(blocks: dict[int, bytes], isotherm, method_options) -> dict[str, object]:
    """Fit selections saved with a Micromeritics report (see constants above)."""
    model = str(method_options.get("instrument_model", ""))
    if model not in MICROMERITICS_REPORT_SETTINGS_MODELS:
        return {}
    adsorption = sorted(
        (p for p in isotherm if p.phase == "adsorption" and p.relative_pressure is not None
         and p.quantity_adsorbed_cm3_g_stp is not None and 0.0 < p.relative_pressure < 1.0
         and p.quantity_adsorbed_cm3_g_stp > 0.0),
        key=lambda p: p.relative_pressure,
    )
    options: dict[str, object] = {}
    if model == "TriStar II 3020":
        points_block = blocks.get(303, b"")
        tagged = {p.index: _tristar_3020_point_tags(points_block, p.record_rel_offset) for p in adsorption}
        if not tagged or any(codes is None for codes in tagged.values()):
            return {}
        bet = [p for p in adsorption if _TAG_BET in tagged[p.index]]
        langmuir = [p for p in adsorption if _TAG_LANGMUIR in tagged[p.index]]
        source = "SMP subset 303 per-point report tags"
    else:
        bet_range = _report_range_pair(blocks.get(311, b""))
        bet = [p for p in adsorption if bet_range and bet_range[0] <= p.relative_pressure <= bet_range[1]]
        langmuir_range = _report_range_pair(blocks.get(312, b""))  # absolute pressure, mmHg
        langmuir = [
            p for p in adsorption
            if langmuir_range and p.absolute_pressure_mmHg is not None
            and langmuir_range[0] <= p.absolute_pressure_mmHg <= langmuir_range[1]
        ]
        if langmuir_range:
            options["stored_langmuir_absolute_pressure_min_mmHg"] = langmuir_range[0]
            options["stored_langmuir_absolute_pressure_max_mmHg"] = langmuir_range[1]
        source = "SMP subsets 311/312 saved selection intervals (intersection)"
    bet_span = _contiguous_pressure_range(bet, adsorption)
    if bet_span is not None:
        options.update(
            stored_bet_pressure_min=bet_span[0], stored_bet_pressure_max=bet_span[1],
            stored_bet_range_source=source, vendor_bet_point_indices=[p.index for p in bet],
        )
    langmuir_span = _contiguous_pressure_range(langmuir, adsorption)
    if langmuir_span is not None:
        options.update(
            stored_langmuir_pressure_min=langmuir_span[0], stored_langmuir_pressure_max=langmuir_span[1],
            stored_langmuir_range_source=source,
        )
    t_plot = _micromeritics_t_plot_settings(blocks.get(314, b""))
    # Verified: Harkins-Jura (3020, ASAP 2460/2020 Plus, 3Flex), Carbon Black
    # STSA (ASAP 2425 Carbon Black example, MicroActive 7.00 to 1e-7) and the
    # reference table with MicroActive's interpolation (TriStar II Plus, 83/90
    # reports; the rest were exported before the file's settings changed).
    method = t_plot.get("vendor_t_plot_thickness_method")
    if method in ("harkins_jura", "carbon_black_stsa") or (method == "reference" and "vendor_t_plot_thickness_params" in t_plot):
        t_plot["use_vendor_t_plot_settings"] = True
    options.update({key: value for key, value in t_plot.items()})
    if not ("stored_bet_pressure_min" in options or "stored_langmuir_pressure_min" in options):
        return options
    options["use_stored_fit_ranges"] = True
    options["micromeritics_report_settings"] = source
    # The vendor fits P/Q against absolute pressure P (mmHg); with a P0 that
    # varies per point this differs slightly from the P/P0 form.
    options["langmuir_fit_axis"] = "absolute_pressure"
    if model in ("TriStar II 3020", "ASAP 2460", "ASAP 2425", "ASAP 2020 Plus") and bet_span is not None:
        # Calculations manual (Summary report): "pressure closest to 0.3 of the
        # relative pressure points designated for surface area calculations".
        options.update(
            single_point_bet_pressure=0.3,
            single_point_bet_pressure_source="Micromeritics summary rule: BET point closest to P/P0 0.3",
            single_point_bet_candidates="vendor_bet_points",
        )
    return options


def _microactive_conditions(block: bytes) -> tuple[float, float, float, float | None] | None:
    """TriStar II Plus (MicroActive) analysis conditions in subset 302.

    Verified on file copies in MicroActive 3.04 (TriStar II Plus) and 7.00
    (TriStar II Plus, ASAP 2460), Free Space and Equilibration dialogs: four
    doubles spaced 10/8/8 bytes hold the free-space "Evacuation time" (h, with
    "Lower dewar for evacuation"), outgas-test duration (s), entered ambient
    and analysis free space (cm3); later the equilibration table follows as a
    uint16 row count and rows of (uint16 interval s, float64 P/P0 upper bound),
    the last bound being 1.0. The group sits at different offsets in the 991-,
    4761- and 5245-byte layouts, so it is located by this structure.
    Returns (evacuation_h, ambient_cm3, analysis_cm3, interval_s), where the
    interval is the first table row, as the vendor report prints it (ASAP 2460
    table 20 s / 10 s, report "Equilibration interval: 20 s"); None when the
    group is not found uniquely.
    """
    def double(offset):
        return _read_double(block, offset) if 0 <= offset <= len(block) - 8 else None

    def u16(offset):
        return struct.unpack_from("<H", block, offset)[0] if 0 <= offset <= len(block) - 2 else None

    groups = []
    for start in range(40, min(len(block) - 34, 600)):
        values = [double(start), double(start + 10), double(start + 18), double(start + 26)]
        if None in values:
            continue
        fs_eq, leak, ambient, analysis = values
        if not (0.001 <= fs_eq <= 100.0 and 1.0 <= leak <= 100000.0 and float(leak).is_integer()
                and _plausible(ambient, 0.0, 1000.0) is not None and _plausible(analysis, 0.0, 1000.0) is not None):
            continue
        table = None
        for count_at in range(start + 34, min(start + 120, len(block) - 12)):
            count = u16(count_at)
            if not count or count > 30:
                continue
            rows = [(u16(count_at + 2 + 10 * i), double(count_at + 4 + 10 * i)) for i in range(count)]
            if any(r[0] is None or r[1] is None for r in rows):
                continue
            bounds = [r[1] for r in rows]
            if (all(1 <= r[0] <= 3600 for r in rows) and all(0.0 < b <= 1.0 for b in bounds)
                    and all(b < c for b, c in zip(bounds, bounds[1:])) and bounds[-1] == 1.0):
                table = rows
                break
        if table is not None:
            groups.append((fs_eq, ambient, analysis, table))
    if len(groups) != 1:
        return None
    fs_eq, ambient, analysis, table = groups[0]
    return fs_eq, ambient, analysis, float(table[0][0])


def _plausible(value: float | None, low: float, high: float) -> float | None:
    """Display-only guard: drop reads from layouts where the offset holds other data.

    Rejects values outside [low, high] and subnormal/near-zero garbage such as
    3.47e-310, which appeared for TriStar II Plus files; exact 0 is kept.
    """
    if value is None or not math.isfinite(value):
        return None
    if value != 0.0 and abs(value) < 1e-9:
        return None
    return value if low <= value <= high else None


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
