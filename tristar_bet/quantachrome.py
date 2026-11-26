"""Parser for Quantachrome Autosorb iQ ``.qps`` files.

Autosorb iQ stores analysis data in a compact QBIN container.  The container is
a typed record stream with a string table followed by named groups.  This
module decodes the reduced isotherm and core run metadata into the shared
``TriStarResult`` model used by the rest of the application.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import (
    AdsorptiveProperties,
    FreeSpaceInfo,
    IsothermPoint,
    RunConditions,
    SampleInfo,
    SmpHeader,
    TargetPressureRow,
    TriStarResult,
)
from . import quantachrome_native as native

CM3_STP_PER_MMOL = 22.414
EXCEL_EPOCH = datetime(1899, 12, 30)

INSTRUMENT_MANUFACTURER = "Quantachrome"
DEFAULT_MODEL = "Autosorb iQ"


class QuantachromeParseError(ValueError):
    """Raised when a file is not a supported Quantachrome ``.qps`` file."""


def load_qps(
    path: str | Path,
    *,
    thermal_transpiration: bool | None = None,
    temperature_compensation: bool | None = None,
) -> TriStarResult:
    """Load a ``.qps`` file.

    The two switches default to the setting saved in the file, which is what
    ASiQwin applies; pass ``True``/``False`` to override it.
    """
    return QuantachromeQpsParser().parse(
        path,
        thermal_transpiration=thermal_transpiration,
        temperature_compensation=temperature_compensation,
    )


# Adsorbates ASiQwin resolves by name when the file does not carry its own
# adsorbate block (material.AdsOverride = 0).  Values from the ASiQwin library.
ADSORBATE_LIBRARY: dict[str, dict[str, float]] = {
    "Krypton": {
        "AdsTemp": 77.35, "MolWt": 83.8, "XArea": 20.5, "LiqDen": 2.413, "AvgDiam": 0.4, "Polariz": 1.0,
        "MagSus": 1.0, "MolDen": 1.0, "SurfTen": 1.0, "NonIdeality": 3e-05,
    },
    "Krypton87": {
        "AdsTemp": 87.3, "MolWt": 83.8, "XArea": 20.5, "LiqDen": 2.6, "AvgDiam": 0.4, "Polariz": 1.0,
        "MagSus": 1.0, "MolDen": 1.0, "SurfTen": 22.172, "NonIdeality": 3e-05,
    },
    "Argon77": {
        "AdsTemp": 77.35, "MolWt": 39.948, "XArea": 13.85, "LiqDen": 1.45, "AvgDiam": 0.336, "Polariz": 1.63,
        "MagSus": 3.25, "MolDen": 8.52, "SurfTen": 15.03, "NonIdeality": 1.14e-04,
    },
}


@dataclass(frozen=True)
class _QbinItem:
    key: str
    key_index: int
    value: Any
    value_offset: int
    is_string: bool = False


@dataclass(frozen=True)
class _QbinDocument:
    strings: list[str]
    groups: dict[str, list[_QbinItem]]

    def group(self, name: str) -> dict[str, Any]:
        return {item.key: item.value for item in self.groups.get(name, [])}

    def group_items(self, name: str) -> list[_QbinItem]:
        return self.groups.get(name, [])


class QuantachromeQpsParser:
    def parse(
        self,
        path: str | Path,
        *,
        thermal_transpiration: bool | None = None,
        temperature_compensation: bool | None = None,
    ) -> TriStarResult:
        file_path = Path(path)
        data = file_path.read_bytes()
        doc = self._parse_qbin(data, file_path)
        admin = doc.group("adminData")
        run_data = doc.group("runData")
        adsorbate = self._resolve_adsorbate(doc, admin, run_data)

        sample = self._build_sample(file_path, doc, admin)
        run_conditions = self._build_run_conditions(doc, admin, run_data, adsorbate)
        isotherm, tags, corrections = self._build_isotherm(
            doc, sample, thermal_transpiration, temperature_compensation
        )
        if not isotherm:
            raise QuantachromeParseError(f"No isotherm points found in {file_path}")

        created_raw, created_time = self._parse_analysis_date(doc, run_data)
        modified_raw, modified_time = self._file_modified_timestamp(file_path)
        saved_raw, saved_time = self._parse_saved_time(doc)
        header = SmpHeader(
            file_path=str(file_path.resolve()),
            file_name=file_path.name,
            byte_count=len(data),
            magic="QBINa",
            version=self._version_text(doc),
            created_raw=created_raw,
            created_time=created_time,
            modified_raw=modified_raw,
            modified_time=modified_time,
            directory_offset=0,
            directory_size=0,
        )

        method_options = self._build_method_options(
            doc,
            admin,
            run_data,
            sample,
            created_raw,
            created_time,
            modified_raw,
            modified_time,
            saved_raw,
            saved_time,
        )
        method_options.update(corrections)
        method_options.update(self._reduction_options(doc, adsorbate, isotherm, tags))

        return TriStarResult(
            header=header,
            subsets=[],
            sample=sample,
            run_conditions=run_conditions,
            target_pressure_table=self._build_target_pressure_table(isotherm),
            free_space=self._build_free_space(),
            po_records=[],
            isotherm=isotherm,
            adsorptive_properties=self._build_adsorptive_properties(doc, admin, adsorbate),
            log_messages=[],
            sample_tube_strings=[],
            method_options=method_options,
            raw_strings={},
        )

    # -- QBIN decoding -----------------------------------------------------

    def _parse_qbin(self, data: bytes, path: Path) -> _QbinDocument:
        if not data.startswith(b"QBINa"):
            raise QuantachromeParseError(f"Unsupported Quantachrome .qps file: {path}")

        records = self._read_records(data, path)
        if len(records) < 4 or records[2][0] != 2:
            raise QuantachromeParseError(f"Invalid Quantachrome QBIN header: {path}")
        string_count = self._payload_int(records[2][1])
        string_records = records[3 : 3 + string_count]
        if len(string_records) != string_count or any(tag != 3 for tag, _payload, _offset in string_records):
            raise QuantachromeParseError(f"Invalid Quantachrome QBIN string table: {path}")
        strings = [payload.decode("ascii", errors="replace") for _tag, payload, _offset in string_records]

        groups: dict[str, list[_QbinItem]] = {}
        pos = 3 + string_count
        if pos < len(records) and records[pos][0] == 8:
            pos += 1
        while pos < len(records):
            tag, payload, _offset = records[pos]
            if tag != 7:
                raise QuantachromeParseError(f"Unexpected Quantachrome QBIN group marker at record {pos}: {path}")
            group_index = self._payload_int(payload)
            group_name = self._string_at(strings, group_index)
            pos += 1
            if pos >= len(records) or records[pos][0] != 4:
                raise QuantachromeParseError(f"Missing Quantachrome QBIN group count after {group_name}: {path}")
            item_count = self._payload_int(records[pos][1])
            pos += 1
            items: list[_QbinItem] = []
            for _ in range(item_count):
                if pos + 1 >= len(records) or records[pos][0] != 5 or records[pos + 1][0] != 6:
                    raise QuantachromeParseError(f"Invalid Quantachrome QBIN item in {group_name}: {path}")
                key_index = self._payload_int(records[pos][1])
                key = self._string_at(strings, key_index)
                payload = records[pos + 1][1]
                value = self._decode_value(payload)
                # Type 1 values are references into the string table.
                is_string = len(payload) >= 9 and payload[0] == 1
                if is_string:
                    value = strings[value] if isinstance(value, int) and 0 <= value < len(strings) else ""
                items.append(
                    _QbinItem(
                        key=key,
                        key_index=key_index,
                        value=value,
                        value_offset=records[pos + 1][2],
                        is_string=is_string,
                    )
                )
                pos += 2
            groups[group_name] = items

        return _QbinDocument(strings=strings, groups=groups)

    @staticmethod
    def _read_records(data: bytes, path: Path) -> list[tuple[int, bytes, int]]:
        records: list[tuple[int, bytes, int]] = []
        pos = 5
        while pos < len(data):
            if pos + 3 > len(data):
                raise QuantachromeParseError(f"Truncated Quantachrome QBIN record: {path}")
            tag = data[pos]
            length = int.from_bytes(data[pos + 1 : pos + 3], "little")
            end = pos + 3 + length
            if end > len(data):
                raise QuantachromeParseError(f"Truncated Quantachrome QBIN payload: {path}")
            records.append((tag, data[pos + 3 : end], pos))
            pos = end
        return records

    @staticmethod
    def _payload_int(payload: bytes) -> int:
        if len(payload) < 4:
            return 0
        return int(struct.unpack("<i", payload[:4])[0])

    @staticmethod
    def _decode_value(payload: bytes) -> Any:
        if len(payload) >= 9:
            code = payload[0]
            if code == 2:
                return float(struct.unpack("<d", payload[1:9])[0])
            if code in {1, 3}:
                return int(struct.unpack("<i", payload[1:5])[0])
        if len(payload) == 8:
            return float(struct.unpack("<d", payload)[0])
        if len(payload) >= 4:
            return int(struct.unpack("<i", payload[:4])[0])
        return payload

    @staticmethod
    def _string_at(strings: list[str], index: int) -> str:
        if 0 <= index < len(strings):
            return strings[index]
        return f"#{index}"

    # -- result builders ---------------------------------------------------

    def _build_sample(self, path: Path, doc: _QbinDocument, admin: dict[str, Any]) -> SampleInfo:
        sample_name = self._string_value(doc, "adminData", "sampleId") or self._string_value(doc, "adminData", "sampleDesc")
        return SampleInfo(
            sample_name=sample_name or path.stem,
            operator=self._string_value(doc, "adminData", "operatorId"),
            submitter="",
            bar_code="",
            sample_mass_g=self._float_value(admin, "sampleWeight"),
            sample_density_g_cm3=None,
        )

    def _build_run_conditions(
        self,
        doc: _QbinDocument,
        admin: dict[str, Any],
        run_data: dict[str, Any],
        adsorbate: dict[str, Any],
    ) -> RunConditions:
        adsorptive_short = self._string_value(doc, "runData", "adsName") or "N2"
        adsorptive_name = str(adsorbate.get("name") or "").strip() or adsorptive_short
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
            po_reference_mmHg=self._float_value(run_data, "reportedPo") or self._float_value(run_data, "actualPo"),
            bath_temperature_K=(
                self._float_value(adsorbate, "AdsTemp")
                or self._float_value(admin, "bathTemp")
            ),
            adsorptive_short=adsorptive_short,
            adsorptive_name=adsorptive_name,
        )

    def _build_free_space(self) -> FreeSpaceInfo:
        return FreeSpaceInfo(
            analysis_entered_cm3=None,
            ambient_entered_cm3=None,
            nonideality_factor=None,
            cold_free_space_cm3=None,
            warm_free_space_cm3=None,
            stem_volume_cm3=None,
            vbath_cm3=None,
            vfree_factor_cm3=None,
            vfree_factor_source="quantachrome_qps_direct_quantity",
        )

    def _build_isotherm(
        self,
        doc: _QbinDocument,
        sample: SampleInfo,
        thermal_transpiration: bool | None,
        temperature_compensation: bool | None,
    ) -> tuple[list[IsothermPoint], dict[int, str], dict[str, Any]]:
        """Isotherm as ASiQwin reports it, the tag string of each point, and the corrections used."""
        data = doc.group("Data")
        admin = doc.group("adminData")
        run_data = doc.group("runData")
        reduction = doc.group("DRedParams")
        temp_comp = doc.group("DRedParams.TempComp")
        n_points = self._int_value(data, "NPoints") or 0
        n_columns = self._int_value(data, "NColumns") or 0
        mass = sample.sample_mass_g if sample.sample_mass_g and sample.sample_mass_g > 0.0 else None

        rows: list[tuple[int, float, float, float, float | None, str]] = []
        for point_index in range(n_points):
            suffix = f"{point_index:03d}"
            relative_pressure = self._float_value(data, f"Pt#0#{suffix}")
            saturation_pressure = self._float_value(data, f"Pt#1#{suffix}")
            raw_volume = self._float_value(data, f"Pt#2#{suffix}")
            minutes = self._float_value(data, f"Pt#5#{suffix}") if n_columns > 5 else None
            tag = data.get(f"PtFlg{suffix}")
            if relative_pressure is None or raw_volume is None:
                continue
            if not math.isfinite(relative_pressure) or not math.isfinite(raw_volume) or relative_pressure <= 0.0:
                continue
            if saturation_pressure is None or not math.isfinite(saturation_pressure) or saturation_pressure <= 0.0:
                saturation_pressure = 760.0
            rows.append(
                (point_index, relative_pressure, saturation_pressure, raw_volume, minutes, tag if isinstance(tag, str) else "")
            )

        has_time = any(row[4] is not None and row[4] > 0.0 for row in rows)
        override_po = self._float_value(reduction, "PoOverride") if reduction.get("overridePo") else None
        if override_po is not None and override_po <= 0.0:
            override_po = None

        thermal_available = native.thermal_transpiration_available(admin, reduction)
        thermal_saved = bool(reduction.get("thermalOn"))
        thermal_on = thermal_available and (thermal_saved if thermal_transpiration is None else bool(thermal_transpiration))

        comp_kind = (
            native.temperature_compensation_kind(str(doc.group("Header").get("hdrType") or ""), admin, run_data)
            if has_time
            else None
        )
        comp_saved = bool(temp_comp.get("enabled"))
        comp_on = comp_kind is not None and (
            comp_saved if temperature_compensation is None else bool(temperature_compensation)
        )
        cell_type = self._int_value(temp_comp, "cellType") or 0

        corrections: dict[str, Any] = {
            "quantachrome_thermal_transpiration_available": thermal_available,
            "quantachrome_thermal_transpiration_saved": thermal_saved,
            "quantachrome_thermal_transpiration_applied": thermal_on,
            "quantachrome_temperature_compensation_available": comp_kind is not None,
            "quantachrome_temperature_compensation_saved": comp_saved,
            "quantachrome_temperature_compensation_applied": comp_on,
        }
        if not rows:
            return [], {}, corrections

        reduced: list[tuple[float, float, float, float]] = []
        for _point_index, relative_pressure, saturation_pressure, raw_volume, minutes, _tag in rows:
            absolute_pressure = relative_pressure * saturation_pressure
            reference_pressure = override_po or saturation_pressure
            if thermal_on:
                absolute_pressure *= native.thermal_transpiration_factor(
                    absolute_pressure,
                    float(admin["bathTemp"]),
                    float(admin["ambientTemp"]),
                    float(reduction["effMolDiameter"]),
                    float(reduction["effCellStemDiameter"]),
                )
            pressure = absolute_pressure / reference_pressure
            volume = raw_volume
            if comp_on:
                volume -= native.temperature_compensation_volume(
                    comp_kind, cell_type, pressure, absolute_pressure, minutes or 0.0, run_data
                )
            reduced.append((absolute_pressure, pressure, reference_pressure, volume))

        max_position = max(range(len(reduced)), key=lambda idx: reduced[idx][1])
        points: list[IsothermPoint] = []
        tags: dict[int, str] = {}
        for row_index, (row, values) in enumerate(zip(rows, reduced), start=1):
            point_index, _relative, _saturation, raw_volume, minutes, tag = row
            absolute_pressure, pressure, reference_pressure, volume = values
            phase = "adsorption" if row_index - 1 <= max_position else "desorption"
            quantity_cm3_g = volume / mass if mass else volume
            tags[row_index] = tag
            points.append(
                IsothermPoint(
                    index=row_index,
                    phase=phase,
                    record_rel_offset=point_index,
                    absolute_pressure_mmHg=absolute_pressure,
                    relative_pressure=pressure,
                    raw_internal_cm3_stp=raw_volume,
                    saturation_pressure_mmHg=reference_pressure,
                    elapsed_seconds=int(round(minutes * 60.0)) if has_time and minutes is not None else None,
                    quantity_adsorbed_cm3_g_stp=quantity_cm3_g,
                    quantity_adsorbed_mmol_g=quantity_cm3_g / CM3_STP_PER_MMOL,
                )
            )
        return points, tags, corrections

    def _resolve_adsorbate(self, doc: _QbinDocument, admin: dict[str, Any], run_data: dict[str, Any]) -> dict[str, Any]:
        """Adsorbate properties ASiQwin uses for this file."""
        block = dict(doc.group("material.Adsorbate"))
        if doc.group("material").get("AdsOverride"):
            return block
        name = run_data.get("adsName")
        name = name.strip() if isinstance(name, str) else ""
        if not name or name == str(block.get("name") or "").strip():
            return block
        # The block still holds another gas; the run header names the gas used.
        resolved = dict(block)
        resolved["name"] = name
        for key, source in (
            ("AdsTemp", "bathTemp"),
            ("MolWt", "adsMolecWeight"),
            ("XArea", "adsCrossSectionArea"),
            ("NonIdeality", "adsNonIdeality"),
        ):
            value = self._float_value(admin, source)
            if value is not None and value > 0.0:
                resolved[key] = value
        resolved.update(ADSORBATE_LIBRARY.get(name, {}))
        return resolved

    def _reduction_options(
        self,
        doc: _QbinDocument,
        adsorbate: dict[str, Any],
        isotherm: list[IsothermPoint],
        tags: dict[int, str],
    ) -> dict[str, Any]:
        """Report settings saved in the file: tagged points and calculation choices."""
        reduction = doc.group("DRedParams")
        thickness_type = self._int_value(reduction, "tMethodType") or 0
        thickness_method = native.THICKNESS_METHOD_BY_TYPE.get(thickness_type, "harkins_jura")
        pre_exponent = self._float_value(reduction, "tmethPreExp") or 0.0
        exponent = self._float_value(reduction, "tmethExp") or 0.0
        options: dict[str, Any] = {
            "quantachrome_qps_native": True,
            "quantachrome_point_tags": dict(tags),
            "quantachrome_thickness_type": thickness_type,
            "quantachrome_thickness_pre_exponent": pre_exponent,
            "quantachrome_thickness_exponent": exponent,
            "quantachrome_bjh_ignore_below_035": bool(reduction.get("bjhdhIgnoreBelow")),
            "vendor_bjh_thickness_method": thickness_method,
            "vendor_dh_thickness_method": thickness_method,
            "quantachrome_bjh_thickness_method": thickness_method,
            "use_vendor_t_plot_settings": True,
            "vendor_t_plot_thickness_method": thickness_method,
        }
        if thickness_type == 3:
            # ASiQwin saves A and n in t[Å] = A * (-ln(P/P0)) ** (-1/n).
            # A zero prefactor leaves thickness undefined for incomplete files.
            options["vendor_t_plot_thickness_params"] = {
                "prefactor": pre_exponent if exponent > 0 else 0.0,
                "numerator": -1.0,
                "exponent": 1.0 / exponent if exponent > 0 else 0.0,
                "scale": 0.1,
            }
        for key in ("SurfTen", "MolWt", "LiqDen", "AdsTemp"):
            value = self._float_value(adsorbate, key)
            if value is not None and value > 0.0:
                options[f"quantachrome_adsorbate_{key}"] = value

        adsorption = [point for point in isotherm if point.phase == "adsorption"]
        for position, method in (
            (native.TAG_MULTI_POINT_BET, "bet"),
            (native.TAG_LANGMUIR, "langmuir"),
            (native.TAG_THICKNESS, "t_plot"),
        ):
            selected = [point for point in adsorption if self._has_tag(tags.get(point.index, ""), position)]
            if not selected:
                continue
            # The interval is only the displayed envelope. A fit must retain the
            # operator's exact point IDs, including gaps and insufficient selections.
            options[f"quantachrome_{method}_point_indices"] = [point.index for point in selected]
            pressures = [float(point.relative_pressure) for point in selected]
            options[f"stored_{method}_pressure_min"] = min(pressures)
            options[f"stored_{method}_pressure_max"] = max(pressures)
            options["use_stored_fit_ranges"] = True
        return options

    @staticmethod
    def _has_tag(tag: str, position: int) -> bool:
        return len(tag) > position and tag[position] not in "_ "

    @staticmethod
    def _build_target_pressure_table(isotherm: list[IsothermPoint]) -> list[TargetPressureRow]:
        rows: list[TargetPressureRow] = []
        previous_end = 0.0
        for point in isotherm:
            ending = float(point.relative_pressure)
            if not math.isfinite(ending) or ending <= 0.0:
                continue
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

    def _build_adsorptive_properties(
        self,
        doc: _QbinDocument,
        admin: dict[str, Any],
        adsorbate: dict[str, Any],
    ) -> AdsorptiveProperties:
        name = str(adsorbate.get("name") or "").strip() or self._string_value(doc, "runData", "adsName") or "N2"
        mnemonic = self._string_value(doc, "runData", "adsName") or name
        molecular_weight = self._float_value(adsorbate, "MolWt") or self._float_value(admin, "adsMolecWeight")
        liquid_density = self._float_value(adsorbate, "LiqDen")
        density_factor = self._density_conversion_factor(molecular_weight, liquid_density)
        cross_section_angstrom2 = self._float_value(adsorbate, "XArea") or self._float_value(admin, "adsCrossSectionArea")
        return AdsorptiveProperties(
            adsorptive=name,
            mnemonic=mnemonic,
            max_manifold_pressure_mmHg=None,
            max_manifold_pressure_kPa=None,
            nonideality_factor=self._float_value(adsorbate, "NonIdeality") or self._float_value(admin, "adsNonIdeality"),
            density_conversion_factor=density_factor,
            thermal_transpiration_hard_sphere_A=None,
            thermal_transpiration_hard_sphere_nm=None,
            molecular_cross_sectional_area_nm2=(cross_section_angstrom2 / 100.0 if cross_section_angstrom2 else None),
            ui_field_rel101=None,
            psat_table=[],
        )

    def _build_method_options(
        self,
        doc: _QbinDocument,
        admin: dict[str, Any],
        run_data: dict[str, Any],
        sample: SampleInfo,
        created_raw: int,
        created_time: str,
        modified_raw: int,
        modified_time: str,
        saved_raw: int,
        saved_time: str,
    ) -> dict[str, Any]:
        instrument_type = self._string_value(doc, "adminData", "instrumentType")
        pc_version = self._string_value(doc, "adminData", "pcVersion")
        inst_version = self._string_value(doc, "adminData", "instVerString")
        duration_text, duration_seconds = self._duration_from_minutes(self._float_value(run_data, "analysisTime"))
        options: dict[str, Any] = {
            "instrument_manufacturer": INSTRUMENT_MANUFACTURER,
            "instrument_model": DEFAULT_MODEL if instrument_type == "Autosorb" else instrument_type or DEFAULT_MODEL,
            "instrument_software": f"Autosorb {pc_version}".strip() if pc_version else inst_version,
            "instrument_serial": self._string_value(doc, "runData", "stationId"),
            "test_started_time": created_time,
            "test_started_raw": created_raw,
            "target_pressure_table_source": "measured_isotherm_pressure_sequence",
            "vendor_bjh_smooth_derivative": False,
            "vendor_dh_smooth_derivative": False,
            "quantachrome_quantity_source": (
                "qps_total_cm3_stp_normalized_by_sample_weight"
                if sample.sample_mass_g
                else "qps_total_cm3_stp_missing_sample_weight"
            ),
            "quantachrome_qbin_version": self._version_text(doc),
            "sample_saved_time": saved_time or modified_time,
            "sample_saved_raw": saved_raw or modified_raw,
            "sample_saved_time_source": "QPS audit timestamp" if saved_time else "Windows file LastWriteTime",
        }
        if duration_text:
            options["test_duration_time"] = duration_text
            options["test_duration_seconds"] = duration_seconds
            options["test_duration_source"] = "QPS runData.analysisTime minutes"
        for key in ("outgasTemp", "outgasTime", "ambientTemp", "bathTemp"):
            value = self._float_value(admin, key)
            if value is not None:
                options[f"quantachrome_{key}"] = value
        return options

    # -- value helpers -----------------------------------------------------

    @staticmethod
    def _float_value(group: dict[str, Any], key: str) -> float | None:
        value = group.get(key)
        if isinstance(value, (int, float)):
            result = float(value)
            return result if math.isfinite(result) else None
        return None

    @staticmethod
    def _int_value(group: dict[str, Any], key: str) -> int | None:
        value = group.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return int(value)
        return None

    @staticmethod
    def _density_conversion_factor(molecular_weight_g_mol: float | None, liquid_density_g_cm3: float | None) -> float | None:
        if not molecular_weight_g_mol or not liquid_density_g_cm3 or liquid_density_g_cm3 <= 0.0:
            return None
        return (molecular_weight_g_mol / liquid_density_g_cm3) / (CM3_STP_PER_MMOL * 1000.0)

    @staticmethod
    def _duration_from_minutes(minutes: float | None) -> tuple[str, int]:
        if minutes is None or not math.isfinite(minutes) or minutes <= 0.0:
            return "", 0
        total_seconds = int(round(minutes * 60.0))
        hours, remainder = divmod(total_seconds, 3600)
        mins, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{mins:02d}:{seconds:02d}", total_seconds

    def _string_value(self, doc: _QbinDocument, group_name: str, key: str) -> str:
        for item in doc.group_items(group_name):
            if item.key == key:
                if item.is_string:
                    return str(item.value).strip()
                if key == "stationId" and isinstance(item.value, int):
                    return str(item.value)
        return ""

    def _version_text(self, doc: _QbinDocument) -> str:
        header = doc.group("Header")
        pc_version = self._string_value(doc, "adminData", "pcVersion")
        file_version = self._int_value(header, "FileVersion")
        if pc_version and file_version:
            return f"{pc_version} (QBIN {file_version})"
        if pc_version:
            return pc_version
        return f"QBIN {file_version}" if file_version else ""

    def _parse_analysis_date(self, doc: _QbinDocument, run_data: dict[str, Any]) -> tuple[int, str]:
        raw_date = self._string_value(doc, "runData", "analysisDate")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                moment = datetime.strptime(raw_date, fmt)
            except ValueError:
                continue
            return self._timestamp_pair(moment)
        return 0, raw_date

    def _parse_saved_time(self, doc: _QbinDocument) -> tuple[int, str]:
        serials: list[float] = []
        pattern = re.compile(r"^\s*\d+\s+-?\d+\s+(\d{4,6}\.\d+)\s*$")
        for text in doc.strings:
            match = pattern.match(text)
            if not match:
                continue
            try:
                serials.append(float(match.group(1)))
            except ValueError:
                continue
        if not serials:
            return 0, ""
        try:
            moment = EXCEL_EPOCH + timedelta(days=max(serials))
        except OverflowError:
            return 0, ""
        return self._timestamp_pair(moment)

    @staticmethod
    def _file_modified_timestamp(path: Path) -> tuple[int, str]:
        try:
            raw = int(path.stat().st_mtime)
        except OSError:
            return 0, ""
        try:
            return raw, datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return raw, ""

    @staticmethod
    def _timestamp_pair(moment: datetime) -> tuple[int, str]:
        try:
            raw = int(moment.timestamp())
        except (OSError, OverflowError, ValueError):
            raw = 0
        if moment.hour or moment.minute or moment.second:
            return raw, moment.strftime("%Y-%m-%d %H:%M:%S")
        return raw, moment.strftime("%Y-%m-%d")
