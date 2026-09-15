"""Parser for the text ``.raw`` export produced by JWGB APAS instruments."""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from .models import FreeSpaceInfo, IsothermPoint, RunConditions, SampleInfo, SmpHeader, TargetPressureRow, TriStarResult


CM3_STP_PER_MMOL = 22.414
DEFAULT_SATURATION_PRESSURE_MMHG = 760.0


class JwgbRawParseError(ValueError):
    """Raised when a file is not a supported JWGB/APAS text RAW export."""


def load_jwgb_raw(path: str | Path) -> TriStarResult:
    return JwgbRawParser().parse(path)


class JwgbRawParser:
    _POINT_RE = re.compile(
        r"^\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?)"
        r"\s+([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?)"
        r"(?:\s+([A-Za-z]+))?\s*$"
    )

    def parse(self, path: str | Path) -> TriStarResult:
        file_path = Path(path)
        data = file_path.read_bytes()
        text = self._decode(data)
        lines = text.splitlines()
        if not self._is_supported(lines):
            raise JwgbRawParseError(f"Unsupported JWGB/APAS .raw file: {file_path}")

        labels = self._labels(lines)
        sample_mass = self._number(labels.get("SAMPLE WEIGHT"))
        if sample_mass is None or not (0.0 < sample_mass < 100000.0):
            raise JwgbRawParseError(f"Invalid or missing SAMPLE WEIGHT in {file_path}")

        rows = self._point_rows(lines)
        if len(rows) < 3:
            raise JwgbRawParseError(f"No isotherm points found in {file_path}")

        points = self._build_isotherm(rows, sample_mass)
        gas_text = labels.get("GASTYPE", "")
        adsorptive_short, adsorptive_name, bath_temperature = self._gas_properties(gas_text)
        sample_name = labels.get("SAMPLE ID", "").strip() or file_path.stem
        operator = labels.get("OPERATOR", "").strip()
        run_date = labels.get("DATE", "").strip() or labels.get("ENDRUN", "").strip()
        created_raw = self._date_sort_key(run_date)
        modified_raw, modified_time = self._file_modified_timestamp(file_path)
        duration_minutes = self._number(labels.get("ANALYSIS TIME"))
        duration_seconds = int(round(duration_minutes * 60.0)) if duration_minutes is not None and duration_minutes >= 0.0 else 0

        header = SmpHeader(
            file_path=str(file_path.resolve()),
            file_name=file_path.name,
            byte_count=len(data),
            magic="JWGB_RAW",
            version="",
            created_raw=created_raw,
            created_time=run_date,
            modified_raw=modified_raw,
            modified_time=modified_time,
            directory_offset=0,
            directory_size=0,
        )
        sample = SampleInfo(
            sample_name=sample_name,
            operator=operator,
            submitter="",
            bar_code="",
            sample_mass_g=sample_mass,
            sample_density_g_cm3=None,
        )
        run_conditions = RunConditions(
            evacuation_rate_mmHg_s=None,
            unrestricted_evacuate_from_mmHg=None,
            evacuation_time_h=None,
            leak_test_time_s=None,
            equilibration_interval_s=None,
            free_space_equilibration_time_h=None,
            ambient_free_space_entered_cm3=None,
            analysis_free_space_entered_cm3=None,
            desorption_test_time_s=None,
            po_reference_mmHg=DEFAULT_SATURATION_PRESSURE_MMHG,
            bath_temperature_K=bath_temperature,
            adsorptive_short=adsorptive_short,
            adsorptive_name=adsorptive_name,
        )
        free_space = FreeSpaceInfo(None, None, None, None, None, None, None, None, "not_exported_by_jwgb_raw")
        method_options: dict[str, object] = {
            "instrument_manufacturer": "JWGB",
            "instrument_model": "APAS1000_ZQ",
            "instrument_software": "JWGB RAW export",
            "format_family": "JWGB APAS text RAW",
            "jwgb_raw_import": True,
            "jwgb_raw_quantity_source": "raw_adsorbed_volume_cm3_stp_divided_by_sample_weight_g",
            "jwgb_raw_absolute_pressure_source": "relative_pressure_times_assumed_760_mmHg",
            "jwgb_raw_point_codes": [code for _relative, _volume, code in rows],
            "target_pressure_table_source": "measured_isotherm_pressure_sequence",
            "test_started_time": run_date,
            "test_started_raw": created_raw,
            "test_time_source": "RAW DATE (date only)",
            "sample_saved_time": modified_time,
            "sample_saved_raw": modified_raw,
            "sample_saved_time_source": "file last modified time",
        }
        bet_point_ids = [index for index, (_relative, _volume, code) in enumerate(rows, start=1) if code == "MAP"]
        if bet_point_ids:
            method_options.update(
                {
                    "jwgb_raw_bet_fit_point_ids": bet_point_ids,
                    "jwgb_raw_bet_fit_point_source": "RAW MAP point codes",
                    "use_official_raw_bet_points": True,
                    "official_fit_range_usage": "vendor_default_from_raw_point_codes",
                }
            )
        single_point = self._single_point_bet_target(rows)
        if single_point is not None:
            target_pressure, source_point_id = single_point
            method_options.update(
                {
                    "jwgb_single_point_bet_pressure": target_pressure,
                    "jwgb_single_point_bet_source_point_id": source_point_id,
                    "jwgb_single_point_bet_pressure_source": "nominal pressure preceding first RAW MAP point",
                }
            )
        if duration_seconds:
            method_options.update(
                {
                    "test_duration_seconds": duration_seconds,
                    "test_duration_time": self._duration_text(duration_seconds),
                    "test_duration_source": "RAW ANALYSIS TIME",
                }
            )
        end_date = labels.get("ENDRUN", "").strip()
        if end_date:
            method_options["test_completed_time"] = end_date
            method_options["test_completed_raw"] = self._date_sort_key(end_date)
            method_options["test_completed_time_source"] = "RAW ENDRUN (date only)"

        return TriStarResult(
            header=header,
            subsets=[],
            sample=sample,
            run_conditions=run_conditions,
            target_pressure_table=self._target_pressure_table(points),
            free_space=free_space,
            po_records=[],
            isotherm=points,
            adsorptive_properties=None,
            log_messages=[],
            sample_tube_strings=[],
            method_options=method_options,
            raw_strings={},
        )

    @staticmethod
    def _decode(data: bytes) -> str:
        for encoding in ("utf-8-sig", "gb18030", "cp1252"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", errors="replace")

    @staticmethod
    def _is_supported(lines: list[str]) -> bool:
        normalized = {line.strip().upper() for line in lines if line.strip()}
        return (
            any(line.startswith("REPORT FOR STATION ") for line in normalized)
            and any(JwgbRawParser._is_table_header(line) for line in normalized)
            and any(line.startswith("GASTYPE:") for line in normalized)
        )

    @staticmethod
    def _is_table_header(line: str) -> bool:
        return re.fullmatch(r"P/PO\s+VOLUME\s*\(CC\)", line.strip(), re.IGNORECASE) is not None

    @staticmethod
    def _labels(lines: list[str]) -> dict[str, str]:
        labels: dict[str, str] = {}
        patterns = {
            "SAMPLE ID": re.compile(r"^SAMPLE\s+ID\s*(?:=\s*)?(.*)$", re.IGNORECASE),
            "ANALYSIS TIME": re.compile(r"^ANALYSIS\s+TIME\s*(?:=\s*)?([^\s]+)", re.IGNORECASE),
            "GASTYPE": re.compile(r"^GASTYPE\s*:\s*(.*)$", re.IGNORECASE),
            "SAMPLE WEIGHT": re.compile(r"^SAMPLE\s+WEIGHT\s*(?:=\s*)?([^\s]+)", re.IGNORECASE),
            "ENDRUN": re.compile(r"^ENDRUN\s*:\s*(.*)$", re.IGNORECASE),
            "DATE": re.compile(r"^DATE\s*:\s*(.*)$", re.IGNORECASE),
            "OPERATOR": re.compile(r"^OPERATOR\s*:\s*(.*)$", re.IGNORECASE),
        }
        for line in lines:
            stripped = line.strip()
            for key, pattern in patterns.items():
                match = pattern.match(stripped)
                if match:
                    labels[key] = match.group(1).strip()
                    break
        return labels

    def _point_rows(self, lines: list[str]) -> list[tuple[float, float, str]]:
        rows: list[tuple[float, float, str]] = []
        in_table = False
        for line in lines:
            if self._is_table_header(line):
                in_table = True
                continue
            if not in_table:
                continue
            match = self._POINT_RE.match(line)
            if not match:
                if rows and line.strip():
                    break
                continue
            relative, volume = float(match.group(1)), float(match.group(2))
            code = (match.group(3) or "").upper()
            if not (math.isfinite(relative) and math.isfinite(volume) and 0.0 < relative <= 1.1):
                if rows:
                    break
                continue
            rows.append((relative, volume, code))
        return rows

    @staticmethod
    def _build_isotherm(rows: list[tuple[float, float, str]], sample_mass: float) -> list[IsothermPoint]:
        peak_index = max(range(len(rows)), key=lambda index: rows[index][0])
        points: list[IsothermPoint] = []
        for index, (relative, raw_volume, code) in enumerate(rows, start=1):
            if code == "DP":
                phase = "desorption"
            elif code in {"AP", "MAP", "ADPV"}:
                phase = "adsorption"
            else:
                phase = "adsorption" if index - 1 <= peak_index else "desorption"
            quantity = raw_volume / sample_mass
            points.append(
                IsothermPoint(
                    index=index,
                    phase=phase,
                    record_rel_offset=0,
                    absolute_pressure_mmHg=relative * DEFAULT_SATURATION_PRESSURE_MMHG,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=raw_volume,
                    saturation_pressure_mmHg=DEFAULT_SATURATION_PRESSURE_MMHG,
                    elapsed_seconds=None,
                    quantity_adsorbed_cm3_g_stp=quantity,
                    quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
                )
            )
        return points

    @staticmethod
    def _single_point_bet_target(rows: list[tuple[float, float, str]]) -> tuple[float, int] | None:
        """Infer the nominal JWGB single-point target immediately preceding MAP.

        The text RAW format does not export the report setting itself.  The validated
        K2861 Matrix1000 report uses the ordinary adsorption point immediately before
        the first MAP point for the separately reported single-point BET value.  Only
        accept a nearby conventional target so unrelated points are not guessed.
        """
        first_map = next((index for index, row in enumerate(rows) if row[2] == "MAP"), None)
        if first_map is None or first_map == 0:
            return None
        relative_pressure, _volume, code = rows[first_map - 1]
        if code != "AP":
            return None
        targets = (0.05, 0.10, 0.20, 0.30)
        target = min(targets, key=lambda value: abs(relative_pressure - value))
        if abs(relative_pressure - target) > max(0.005, target * 0.05):
            return None
        return target, first_map

    @staticmethod
    def _gas_properties(value: str) -> tuple[str, str, float | None]:
        normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
        if normalized in {"N2", "NITROGEN"}:
            return "N2", "Nitrogen", 77.35
        if normalized in {"AR", "ARGON"}:
            return "Ar", "Argon", 87.30
        if normalized in {"CO2", "CARBONDIOXIDE"}:
            return "CO2", "Carbon dioxide", 273.15
        return value.strip(), value.strip(), None

    @staticmethod
    def _target_pressure_table(points: list[IsothermPoint]) -> list[TargetPressureRow]:
        rows: list[TargetPressureRow] = []
        previous = 0.0
        for index, point in enumerate(points, start=1):
            ending = float(point.relative_pressure)
            rows.append(
                TargetPressureRow(
                    row=index,
                    branch=point.phase,
                    starting_pressure_p_po=previous,
                    ending_pressure_p_po=ending,
                    pressure_increment_p_po=ending - previous,
                    ending_pressure_rel_offset=0,
                )
            )
            previous = ending
        return rows

    @staticmethod
    def _number(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _date_sort_key(value: str) -> int:
        try:
            return int(datetime.strptime(value, "%Y-%m-%d").timestamp())
        except (TypeError, ValueError, OSError):
            return 0

    @staticmethod
    def _file_modified_timestamp(path: Path) -> tuple[int, str]:
        try:
            raw = int(path.stat().st_mtime)
            return raw, datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return 0, ""

    @staticmethod
    def _duration_text(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
