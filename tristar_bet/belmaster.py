"""Parser for MicrotracBEL BELSORP (BELMaster) ``.DAT`` export files.

BELMaster writes four files per measurement port:

* ``*.DAT``   - a compact, tab-separated text report with the final isotherm.
* ``*.mcs`` / ``*.NDAT`` - the full raw instrument record (identical to each
  other) holding every intermediate equilibration step in Pa units.
* ``*.xls``  - a formatted spreadsheet report.

The ``.DAT`` file is the right import target: it already carries the reduced
adsorption/desorption isotherm (Pe, P0 and quantity adsorbed per gram) together
with the sample mass and run conditions, with no binary decoding required. This
module reads it and produces the same :class:`TriStarResult` the Micromeritics
``.smp`` parser emits, so the rest of the application treats both identically.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from .models import (
    FreeSpaceInfo,
    IsothermPoint,
    RunConditions,
    SampleInfo,
    SmpHeader,
    TriStarResult,
)

MMHG_TO_KPA = 101.325 / 760.0
CM3_STP_PER_MMOL = 22.414

INSTRUMENT_MANUFACTURER = "MicrotracBEL"


class BELMasterParseError(ValueError):
    """Raised when a file is not a supported BELMaster ``.DAT`` export."""


def load_dat(path: str | Path) -> TriStarResult:
    return BELMasterDatParser().parse(path)


class BELMasterDatParser:
    def parse(self, path: str | Path) -> TriStarResult:
        file_path = Path(path)
        text = self._read_text(file_path)
        lines = text.splitlines()
        if not any("System property" in line and "BELSORP" in line for line in lines):
            raise BELMasterParseError(f"Unsupported BELMaster .DAT file: {file_path}")

        labels = self._collect_labels(lines)
        model, software_version = self._parse_system_property(lines)

        adsorption_rows = self._parse_data_table(lines, "Adsorption data")
        desorption_rows = self._parse_data_table(lines, "Desorption data")

        sample = self._build_sample(labels)
        run_conditions = self._build_run_conditions(labels)
        free_space = self._build_free_space(labels)
        isotherm = self._build_isotherm(adsorption_rows, desorption_rows)
        if not isotherm:
            raise BELMasterParseError(f"No isotherm points found in {file_path}")

        created_raw, created_time = self._parse_measurement_date(labels)
        modified_raw, modified_time = self._file_modified_timestamp(file_path)
        header = SmpHeader(
            file_path=str(file_path.resolve()),
            file_name=file_path.name,
            byte_count=len(text.encode("utf-8", errors="replace")),
            magic="BELMASTER",
            version=software_version,
            created_raw=created_raw,
            created_time=created_time,
            modified_raw=modified_raw,
            modified_time=modified_time,
            directory_offset=0,
            directory_size=0,
        )

        method_options: dict[str, object] = {
            "instrument_manufacturer": INSTRUMENT_MANUFACTURER,
            "instrument_model": model,
            "instrument_software": f"{model} {software_version}".strip(),
            "instrument_serial": labels.get("Instrument S/N"),
            "test_started_time": created_time,
            "test_started_raw": created_raw,
            "belmaster_quantity_source": "dat_v_ml_stp_per_g",
        }
        vs = self._label_float(labels, "Vs/ml")
        if vs is not None:
            method_options["belmaster_vs_cm3"] = vs
        average_dead_volume = self._label_float(labels, "Average dead volume")
        if average_dead_volume is not None:
            method_options["belmaster_average_dead_volume_cm3"] = average_dead_volume
        pretreatment = self._label_text(labels, "Comment1")
        if pretreatment:
            method_options["belmaster_pretreatment"] = pretreatment

        return TriStarResult(
            header=header,
            subsets=[],
            sample=sample,
            run_conditions=run_conditions,
            target_pressure_table=[],
            free_space=free_space,
            po_records=[],
            isotherm=isotherm,
            adsorptive_properties=None,
            log_messages=[],
            sample_tube_strings=[],
            method_options=method_options,
            raw_strings={},
        )

    # -- file/text helpers --------------------------------------------------

    @staticmethod
    def _read_text(path: Path) -> str:
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "gbk", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", errors="replace")

    @staticmethod
    def _collect_labels(lines: list[str]) -> dict[str, str]:
        """Map ``"label:"`` keys to their (unquoted) values across all sections."""
        labels: dict[str, str] = {}
        for line in lines:
            if "\t" not in line:
                continue
            key, _, value = line.partition("\t")
            key = key.strip().strip('"')
            if not key.endswith(":"):
                continue
            key = key[:-1].strip()
            value = value.strip().strip('"')
            if key and key not in labels:
                labels[key] = value
        return labels

    @staticmethod
    def _parse_system_property(lines: list[str]) -> tuple[str, str]:
        for line in lines:
            if "System property" in line and "BELSORP" in line:
                tokens = line.replace("System property", "").split()
                model = tokens[0] if tokens else "BELSORP"
                version = tokens[1] if len(tokens) > 1 else ""
                return model, version
        return "BELSORP", ""

    @staticmethod
    def _parse_data_table(lines: list[str], section_title: str) -> list[list[float]]:
        rows: list[list[float]] = []
        in_section = False
        header_seen = False
        for line in lines:
            if section_title in line:
                in_section = True
                header_seen = False
                continue
            if not in_section:
                continue
            stripped = line.strip()
            if not header_seen:
                # The column header row starts with the quoted "No." label. The
                # "====" divider closing the section *title* block appears before
                # it, so only treat dividers as section-end once data has begun.
                if stripped.startswith('"No."') or stripped.startswith("No."):
                    header_seen = True
                continue
            if stripped.startswith("===="):
                break
            fields = [field.strip() for field in line.split("\t") if field.strip() != ""]
            if len(fields) < 5:
                continue
            try:
                index = int(float(fields[0]))
                pe = float(fields[1])
                p0 = float(fields[2])
                vd = float(fields[3])
                quantity = float(fields[4])
            except ValueError:
                continue
            if index == 0 and pe == 0.0 and p0 == 0.0:
                # Terminator row written by BELMaster at the end of each table.
                break
            rows.append([float(index), pe, p0, vd, quantity])
        return rows

    # -- section builders ---------------------------------------------------

    def _build_sample(self, labels: dict[str, str]) -> SampleInfo:
        sample_name = self._label_text(labels, "Comment2")
        mass = self._label_float(labels, "Sample weight/g")
        if mass is not None and not (1e-8 < mass < 100.0):
            mass = None
        return SampleInfo(
            sample_name=sample_name,
            operator="",
            submitter="",
            bar_code="",
            sample_mass_g=mass,
            sample_density_g_cm3=None,
        )

    def _build_run_conditions(self, labels: dict[str, str]) -> RunConditions:
        adsorptive = self._label_text(labels, "Adsorptive") or "N2"
        bath_temperature = self._label_float(labels, "Meas. Temp./K")
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
            po_reference_mmHg=None,
            bath_temperature_K=bath_temperature,
            adsorptive_short=adsorptive,
            adsorptive_name=adsorptive,
        )

    def _build_free_space(self, labels: dict[str, str]) -> FreeSpaceInfo:
        return FreeSpaceInfo(
            analysis_entered_cm3=None,
            ambient_entered_cm3=None,
            nonideality_factor=None,
            cold_free_space_cm3=None,
            warm_free_space_cm3=None,
            stem_volume_cm3=None,
            vbath_cm3=None,
            vfree_factor_cm3=None,
            vfree_factor_source="belmaster_dat_direct",
        )

    def _build_isotherm(
        self,
        adsorption_rows: list[list[float]],
        desorption_rows: list[list[float]],
    ) -> list[IsothermPoint]:
        points: list[IsothermPoint] = []
        index = 0
        for phase, rows in (("adsorption", adsorption_rows), ("desorption", desorption_rows)):
            for _, pe_kpa, p0_kpa, _vd, quantity in rows:
                if p0_kpa <= 0.0:
                    continue
                relative = pe_kpa / p0_kpa
                if not math.isfinite(relative):
                    continue
                index += 1
                absolute_mmhg = pe_kpa / MMHG_TO_KPA
                saturation_mmhg = p0_kpa / MMHG_TO_KPA
                points.append(
                    IsothermPoint(
                        index=index,
                        phase=phase,
                        record_rel_offset=0,
                        absolute_pressure_mmHg=absolute_mmhg,
                        relative_pressure=relative,
                        raw_internal_cm3_stp=quantity,
                        saturation_pressure_mmHg=saturation_mmhg,
                        elapsed_seconds=None,
                        quantity_adsorbed_cm3_g_stp=quantity,
                        quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
                    )
                )
        return points

    # -- value helpers ------------------------------------------------------

    @staticmethod
    def _label_text(labels: dict[str, str], key: str) -> str:
        return labels.get(key, "").strip()

    @staticmethod
    def _label_float(labels: dict[str, str], key: str) -> float | None:
        value = labels.get(key)
        if value is None:
            return None
        try:
            return float(value.strip())
        except ValueError:
            return None

    def _parse_measurement_date(self, labels: dict[str, str]) -> tuple[int, str]:
        raw_date = labels.get("Date of measurement", "").strip()
        # BELMaster writes the date as yy/mm/dd (e.g. "26/01/06" == 2026-01-06).
        match = re.match(r"^(\d{2})/(\d{2})/(\d{2})$", raw_date)
        if match:
            yy, mm, dd = (int(group) for group in match.groups())
            year = 2000 + yy
            try:
                moment = datetime(year, mm, dd)
                return int(moment.timestamp()), moment.strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                pass
        return 0, raw_date

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
