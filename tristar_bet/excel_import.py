from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

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


MMHG_TO_KPA = 101.325 / 760.0
KPA_TO_MMHG = 760.0 / 101.325
CM3_STP_PER_MMOL = 22.414
EXCEL_EPOCH = datetime(1899, 12, 30)


class ExcelParseError(ValueError):
    """Raised when an Excel workbook is not a supported BET export."""


@dataclass(frozen=True)
class SheetGrid:
    name: str
    values: list[list[Any]]

    @property
    def nrows(self) -> int:
        return len(self.values)

    @property
    def ncols(self) -> int:
        return max((len(row) for row in self.values), default=0)

    def cell(self, row: int, column: int) -> Any:
        if row < 0 or row >= len(self.values):
            return None
        values = self.values[row]
        if column < 0 or column >= len(values):
            return None
        return values[column]


@dataclass(frozen=True)
class ExcelWorkbook:
    sheets: list[SheetGrid]

    def text_values(self) -> Iterable[str]:
        for sheet in self.sheets:
            for row in sheet.values:
                for value in row:
                    text = _text(value)
                    if text:
                        yield text


@dataclass(frozen=True)
class IsothermTable:
    points: list[IsothermPoint]
    source: str


def load_excel(path: str | Path) -> TriStarResult:
    return OfficialExcelParser().parse(path)


class OfficialExcelParser:
    def parse(self, path: str | Path) -> TriStarResult:
        file_path = Path(path)
        workbook = _read_workbook(file_path)
        labels = _collect_labels(workbook.sheets)
        instrument = _detect_instrument(workbook)
        sample = self._build_sample(file_path, labels, workbook, instrument)
        run_conditions = self._build_run_conditions(labels)
        free_space = self._build_free_space(labels)
        adsorptive_properties = self._build_adsorptive_properties(labels, run_conditions)

        table = self._parse_bsd_isotherm(workbook)
        if table is None:
            table = self._parse_belmaster_isotherm(workbook)
        if table is None:
            table = self._parse_micromeritics_isotherm(workbook, sample)
        if table is None:
            table = self._parse_microactive_copy_isotherm(workbook)
        if table is None or not table.points:
            raise ExcelParseError(f"No supported isotherm table found in {file_path}")

        created_raw, created_time = self._parse_started_time(labels)
        completed_raw, completed_time = self._parse_completed_time(labels)
        report_raw, report_time = self._parse_report_time(labels)
        modified_raw, modified_time = _file_modified_timestamp(file_path)
        if not created_time:
            created_raw, created_time = modified_raw, modified_time
        header = SmpHeader(
            file_path=str(file_path.resolve()),
            file_name=file_path.name,
            byte_count=file_path.stat().st_size,
            magic="OFFICIAL_EXCEL",
            version=instrument.get("instrument_software", ""),
            created_raw=created_raw,
            created_time=created_time,
            modified_raw=modified_raw,
            modified_time=modified_time,
            directory_offset=0,
            directory_size=0,
        )

        method_options: dict[str, Any] = {
            **instrument,
            "excel_import_source": table.source,
            "excel_quantity_source": "official_report_quantity_adsorbed_cm3_g_stp",
            "excel_phase_source": "pressure_sequence_peak" if table.source.startswith("micromeritics") else "sheet_branch_marker",
            "excel_elapsed_time_note": "source display text preserved; not used for analysis",
            "target_pressure_table_source": "measured_isotherm_pressure_sequence",
            "sample_saved_time": modified_time,
            "sample_saved_raw": modified_raw,
            "sample_saved_time_source": "Windows file LastWriteTime",
            "excel_missing_method_settings": (
                "Excel reports often omit method settings such as selected thickness curve, "
                "BET/t-plot ranges, smoothing and BJH correction details."
            ),
        }
        if created_time:
            method_options["test_started_time"] = created_time
            method_options["test_started_raw"] = created_raw
        if completed_time:
            method_options["test_completed_time"] = completed_time
            method_options["test_completed_raw"] = completed_raw
        if report_time:
            method_options["excel_report_time"] = report_time
            method_options["excel_report_raw"] = report_raw
        duration_text, duration_seconds = _duration_text(created_raw, completed_raw)
        if duration_text:
            method_options["test_duration_time"] = duration_text
            method_options["test_duration_seconds"] = duration_seconds
            method_options["test_duration_source"] = "Excel Started/Completed fields"
        self._add_report_values(labels, method_options)
        self._add_bsd_report_values(workbook, method_options)
        self._add_bet_fit_range(workbook, method_options)

        return TriStarResult(
            header=header,
            subsets=[],
            sample=sample,
            run_conditions=run_conditions,
            target_pressure_table=_target_pressure_table_from_isotherm(table.points),
            free_space=free_space,
            po_records=[],
            isotherm=table.points,
            adsorptive_properties=adsorptive_properties,
            log_messages=[],
            sample_tube_strings=[],
            method_options=method_options,
            raw_strings={},
        )

    def _build_sample(
        self,
        file_path: Path,
        labels: dict[str, Any],
        workbook: ExcelWorkbook,
        instrument: dict[str, Any],
    ) -> SampleInfo:
        sample_name = _as_clean_string(_first_label(labels, "sample", "样品名称"))
        if not sample_name and instrument.get("instrument_manufacturer") == "MicrotracBEL":
            sample_name = self._belmaster_sample_name(workbook, labels)
        if not sample_name:
            sample_name = self._microactive_copy_sample_name(workbook)
        if not sample_name:
            data_file = _as_clean_string(_first_label(labels, "data file", "file"))
            sample_name = Path(data_file).stem if data_file else file_path.stem
        return SampleInfo(
            sample_name=sample_name,
            operator=_as_clean_string(_first_label(labels, "operator", "测试人员")),
            submitter=_as_clean_string(_first_label(labels, "submitter", "送样人员", "送样单位")),
            bar_code=_as_clean_string(_first_label(labels, "bar code")),
            sample_mass_g=_number(_first_label(labels, "sample mass", "sample weight/g", "样品质量", "脱气后样品质量")),
            sample_density_g_cm3=_number(_first_label(labels, "sample density", "样品密度")),
        )

    @staticmethod
    def _belmaster_sample_name(workbook: ExcelWorkbook, labels: dict[str, Any]) -> str:
        for sheet in workbook.sheets:
            if sheet.name.lower() not in {"summary", "isotherm"}:
                continue
            for row_index in range(min(sheet.nrows, 10)):
                if _label_key(sheet.cell(row_index, 0)):
                    continue
                value = sheet.cell(row_index, 2)
                if not isinstance(value, str):
                    continue
                text = _as_clean_string(value)
                if text and not any(mark in text.lower() for mark in ("data file", "vacuum", ".dat")):
                    return text
        data_file = _as_clean_string(_first_label(labels, "data file"))
        return Path(data_file).stem if data_file else ""

    @staticmethod
    def _build_run_conditions(labels: dict[str, Any]) -> RunConditions:
        raw_adsorptive = _as_clean_string(_first_label(labels, "analysis adsorptive", "adsorptive", "吸附质"))
        adsorptive = _adsorptive_short(raw_adsorptive)
        bath_temperature = _temperature_k(
            _first_label(labels, "analysis bath temp.", "analysis bath temp", "adsorption temperature")
        )
        if bath_temperature is None:
            bath_temperature = _adsorptive_temperature_k(raw_adsorptive)
        equilibration = _number(_first_label(labels, "equilibration interval"))
        return RunConditions(
            evacuation_rate_mmHg_s=None,
            unrestricted_evacuate_from_mmHg=None,
            evacuation_time_h=None,
            leak_test_time_s=None,
            equilibration_interval_s=equilibration,
            free_space_equilibration_time_h=None,
            ambient_free_space_entered_cm3=_number(_first_label(labels, "ambient free space")),
            analysis_free_space_entered_cm3=_number(_first_label(labels, "analysis free space")),
            desorption_test_time_s=None,
            po_reference_mmHg=None,
            bath_temperature_K=bath_temperature,
            adsorptive_short=adsorptive,
            adsorptive_name=adsorptive,
        )

    @staticmethod
    def _build_free_space(labels: dict[str, Any]) -> FreeSpaceInfo:
        warm = _number(_first_label(labels, "warm free space"))
        cold = _number(_first_label(labels, "cold free space"))
        generic = _number(_first_label(labels, "free space"))
        analysis = _number(_first_label(labels, "analysis free space"))
        ambient = _number(_first_label(labels, "ambient free space"))
        return FreeSpaceInfo(
            analysis_entered_cm3=analysis,
            ambient_entered_cm3=ambient,
            nonideality_factor=None,
            cold_free_space_cm3=cold,
            warm_free_space_cm3=warm,
            stem_volume_cm3=None,
            vbath_cm3=None,
            vfree_factor_cm3=generic,
            vfree_factor_source="official_excel_report",
        )

    @staticmethod
    def _build_adsorptive_properties(labels: dict[str, Any], run_conditions: RunConditions) -> AdsorptiveProperties | None:
        cross_section = _number(_first_label(labels, "molecular cross-sectional area"))
        psat_kpa = _number(_first_label(labels, "saturation vapor pressure"))
        if not run_conditions.adsorptive_short and cross_section is None and psat_kpa is None:
            return None
        psat_table = []
        if psat_kpa is not None:
            psat_table.append({"index": 1, "saturation_pressure_mmHg": psat_kpa * KPA_TO_MMHG})
        return AdsorptiveProperties(
            adsorptive=run_conditions.adsorptive_short,
            mnemonic=run_conditions.adsorptive_short,
            max_manifold_pressure_mmHg=None,
            max_manifold_pressure_kPa=None,
            nonideality_factor=None,
            density_conversion_factor=None,
            thermal_transpiration_hard_sphere_A=None,
            thermal_transpiration_hard_sphere_nm=None,
            molecular_cross_sectional_area_nm2=cross_section,
            ui_field_rel101=None,
            psat_table=psat_table,
        )

    def _parse_belmaster_isotherm(self, workbook: ExcelWorkbook) -> IsothermTable | None:
        sheet = next((item for item in workbook.sheets if item.name.lower() == "isotherm"), None)
        if sheet is None:
            return None
        header = None
        for row_index, row in enumerate(sheet.values):
            normalized = [_normalize_header(value) for value in row]
            if "p/p0" in normalized and any("va/cm3" in item or "va/cm" in item for item in normalized):
                header = row_index
                break
        if header is None:
            return None

        phase = "adsorption"
        points: list[IsothermPoint] = []
        point_index = 0
        for row_index in range(header + 1, sheet.nrows):
            first = _as_clean_string(sheet.cell(row_index, 0)).upper()
            if first in {"ADS", "ADSORPTION"}:
                phase = "adsorption"
                continue
            if first in {"DES", "DESORPTION"}:
                phase = "desorption"
                continue
            pe_kpa = _number(sheet.cell(row_index, 1))
            p0_kpa = _number(sheet.cell(row_index, 2))
            relative = _number(sheet.cell(row_index, 3))
            quantity = _number(sheet.cell(row_index, 4))
            if pe_kpa is None or p0_kpa is None or relative is None or quantity is None:
                continue
            points.append(
                IsothermPoint(
                    index=point_index,
                    phase=phase,
                    record_rel_offset=0,
                    absolute_pressure_mmHg=pe_kpa * KPA_TO_MMHG,
                    relative_pressure=relative,
                    raw_internal_cm3_stp=quantity,
                    saturation_pressure_mmHg=p0_kpa * KPA_TO_MMHG,
                    elapsed_seconds=None,
                    quantity_adsorbed_cm3_g_stp=quantity,
                    quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
                )
            )
            point_index += 1
        return IsothermTable(points=points, source=f"belmaster:{sheet.name}") if points else None

    def _parse_bsd_isotherm(self, workbook: ExcelWorkbook) -> IsothermTable | None:
        if not _is_bsd_workbook(workbook):
            return None
        for sheet in workbook.sheets:
            table_position = self._find_bsd_isotherm_table(sheet)
            if table_position is None:
                continue
            header_row, columns = table_position
            raw_rows: list[dict[str, Any]] = []
            invalid_streak = 0
            for row_index in range(header_row + 1, sheet.nrows):
                serial = _number(sheet.cell(row_index, columns["serial"]))
                pressure_pa = _number(sheet.cell(row_index, columns["pressure_pa"]))
                p0_pa = _number(sheet.cell(row_index, columns["p0_pa"]))
                relative = _number(sheet.cell(row_index, columns["relative_pressure"]))
                quantity = _number(sheet.cell(row_index, columns["quantity"]))
                if serial is None or pressure_pa is None or p0_pa is None or relative is None or quantity is None:
                    if raw_rows:
                        invalid_streak += 1
                        if invalid_streak >= 3:
                            break
                    continue
                invalid_streak = 0
                elapsed_seconds = None
                elapsed_col = columns.get("elapsed")
                if elapsed_col is not None:
                    elapsed_seconds = _elapsed_display_seconds(sheet.cell(row_index, elapsed_col))
                raw_rows.append(
                    {
                        "relative": relative,
                        "absolute_mmHg": pressure_pa * KPA_TO_MMHG / 1000.0,
                        "saturation_mmHg": p0_pa * KPA_TO_MMHG / 1000.0,
                        "quantity": quantity,
                        "elapsed_seconds": elapsed_seconds,
                    }
                )
            if not raw_rows:
                continue
            peak_index = max(range(len(raw_rows)), key=lambda index: raw_rows[index]["relative"])
            points = []
            for index, row in enumerate(raw_rows):
                quantity = float(row["quantity"])
                points.append(
                    IsothermPoint(
                        index=index,
                        phase="adsorption" if index <= peak_index else "desorption",
                        record_rel_offset=0,
                        absolute_pressure_mmHg=float(row["absolute_mmHg"]),
                        relative_pressure=float(row["relative"]),
                        raw_internal_cm3_stp=quantity,
                        saturation_pressure_mmHg=float(row["saturation_mmHg"]),
                        elapsed_seconds=row["elapsed_seconds"],
                        quantity_adsorbed_cm3_g_stp=quantity,
                        quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
                    )
                )
            return IsothermTable(points=points, source=f"bsd:{sheet.name}")
        return None

    @staticmethod
    def _find_bsd_isotherm_table(sheet: SheetGrid) -> tuple[int, dict[str, int]] | None:
        for row_index in range(sheet.nrows):
            headers = [_normalize_header(sheet.cell(row_index, column_index)) for column_index in range(sheet.ncols)]
            columns: dict[str, int] = {}
            for column_index, header in enumerate(headers):
                compact = header.replace(" ", "")
                if compact == "serial":
                    columns["serial"] = column_index
                elif "p/(pa)" in compact or compact == "p(pa)":
                    columns["pressure_pa"] = column_index
                elif "p0/(pa)" in compact or compact == "p0(pa)":
                    columns["p0_pa"] = column_index
                elif compact in {"p/p0", "p/po"}:
                    columns["relative_pressure"] = column_index
                elif "v/(cm3/g.stp)" in compact or "∑v/(cm3/g.stp)" in compact:
                    columns["quantity"] = column_index
                elif compact in {"t/(h:m)", "t(s)"}:
                    columns["elapsed"] = column_index
            required = {"serial", "pressure_pa", "p0_pa", "relative_pressure", "quantity"}
            if required.issubset(columns):
                return row_index, columns
        return None

    def _parse_micromeritics_isotherm(self, workbook: ExcelWorkbook, sample: SampleInfo) -> IsothermTable | None:
        for sheet in workbook.sheets:
            table_position = self._find_micromeritics_table(sheet)
            if table_position is None:
                continue
            header_row, rel_col = table_position
            raw_rows: list[dict[str, Any]] = []
            invalid_streak = 0
            for row_index in range(header_row + 1, sheet.nrows):
                relative = _number(sheet.cell(row_index, rel_col))
                absolute = _number(sheet.cell(row_index, rel_col + 1))
                quantity = _number(sheet.cell(row_index, rel_col + 2))
                elapsed_seconds = _elapsed_display_seconds(sheet.cell(row_index, rel_col + 3))
                saturation = _number(sheet.cell(row_index, rel_col + 4))
                if relative is None or absolute is None or quantity is None:
                    if raw_rows:
                        invalid_streak += 1
                        if invalid_streak >= 5:
                            break
                    continue
                invalid_streak = 0
                raw_rows.append(
                    {
                        "relative": relative,
                        "absolute": absolute,
                        "quantity": quantity,
                        "elapsed_seconds": elapsed_seconds,
                        "saturation": saturation,
                    }
                )
            if not raw_rows:
                continue
            peak_index = max(range(len(raw_rows)), key=lambda index: raw_rows[index]["relative"])
            points = []
            for index, row in enumerate(raw_rows):
                quantity = float(row["quantity"])
                points.append(
                    IsothermPoint(
                        index=index,
                        phase="adsorption" if index <= peak_index else "desorption",
                        record_rel_offset=0,
                        absolute_pressure_mmHg=float(row["absolute"]),
                        relative_pressure=float(row["relative"]),
                        raw_internal_cm3_stp=quantity * sample.sample_mass_g if sample.sample_mass_g else quantity,
                        saturation_pressure_mmHg=row["saturation"],
                        elapsed_seconds=row["elapsed_seconds"],
                        quantity_adsorbed_cm3_g_stp=quantity,
                        quantity_adsorbed_mmol_g=quantity / CM3_STP_PER_MMOL,
                    )
                )
            return IsothermTable(points=points, source=f"micromeritics:{sheet.name}")
        return None

    @staticmethod
    def _find_micromeritics_table(sheet: SheetGrid) -> tuple[int, int] | None:
        for row_index in range(sheet.nrows):
            for column_index in range(sheet.ncols - 2):
                current = _normalize_header(sheet.cell(row_index, column_index))
                next_one = _normalize_header(sheet.cell(row_index, column_index + 1))
                next_two = _normalize_header(sheet.cell(row_index, column_index + 2))
                if (
                    "relative pressure" in current
                    and "absolute pressure" in next_one
                    and "quantity adsorbed" in next_two
                ):
                    return row_index, column_index
        return None

    def _parse_microactive_copy_isotherm(self, workbook: ExcelWorkbook) -> IsothermTable | None:
        """Parse an isotherm copied out of MicroActive's tabular report.

        For instruments whose native files we cannot read yet (e.g. the
        3Flex 3500 ``.smp``), the isotherm can be copied from MicroActive's
        *isotherm* report into a spreadsheet.  That layout stacks an adsorption
        table and a desorption table, each preceded by an explicit branch
        marker like ``Sample : Sample : Adsorption`` / ``... : Desorption`` and
        a header row with ``Relative Pressure (p/p°)`` and ``Quantity Adsorbed
        (mmol/g)`` (or ``cm³/g STP``).  Only relative pressure and quantity are
        needed for BET / Langmuir / t-Plot / BJH, since the quantity is already
        per gram.

        The explicit branch marker is *required*: it distinguishes the intended
        copy-the-isotherm workflow from other report exports that merely
        contain a ``Relative Pressure``/``Quantity Adsorbed`` table (e.g. the
        tabular "Entered Data Table" of a full report), so those are left to
        the dedicated parsers and not silently imported here.
        """
        for sheet in workbook.sheets:
            points: list[IsothermPoint] = []
            branch: str | None = None
            rel_col: int | None = None
            quantity_col = 0
            quantity_in_mmol = True
            last_key: tuple[str, float, float] | None = None
            for row_index in range(sheet.nrows):
                for column_index in range(min(sheet.ncols, 3)):
                    detected = _copy_isotherm_branch(_text(sheet.cell(row_index, column_index)))
                    if detected is not None:
                        branch = detected
                header = self._find_copy_isotherm_header(sheet, row_index)
                if header is not None:
                    rel_col, quantity_col, quantity_in_mmol = header
                    continue
                if rel_col is None or branch is None:
                    continue
                relative = _number(sheet.cell(row_index, rel_col))
                quantity = _number(sheet.cell(row_index, quantity_col))
                if relative is None or quantity is None or not (0.0 < relative <= 1.5):
                    continue
                key = (branch, float(relative), float(quantity))
                if key == last_key:
                    continue
                last_key = key
                quantity_cm3 = quantity * CM3_STP_PER_MMOL if quantity_in_mmol else quantity
                saturation = 760.0
                points.append(
                    IsothermPoint(
                        index=len(points) + 1,
                        phase=branch,
                        record_rel_offset=0,
                        absolute_pressure_mmHg=relative * saturation,
                        relative_pressure=relative,
                        raw_internal_cm3_stp=quantity_cm3,
                        saturation_pressure_mmHg=saturation,
                        elapsed_seconds=None,
                        quantity_adsorbed_cm3_g_stp=quantity_cm3,
                        quantity_adsorbed_mmol_g=quantity_cm3 / CM3_STP_PER_MMOL,
                    )
                )
            if len(points) >= 3:
                return IsothermTable(points=points, source=f"microactive_copy:{sheet.name}")
        return None

    @staticmethod
    def _find_copy_isotherm_header(sheet: SheetGrid, row_index: int) -> tuple[int, int, bool] | None:
        """Locate a genuine ``Relative Pressure (p/p°) | Quantity Adsorbed (...)``
        table header.  Both column headers must be present on the same row so
        that label rows such as ``Relative Pressure: 0.95 p/p°`` (found in
        report-options exports with no measured isotherm) are not mistaken for
        a data table."""
        for column_index in range(sheet.ncols - 1):
            current = _normalize_header(sheet.cell(row_index, column_index))
            if "relative pressure" not in current:
                continue
            for quantity_col in range(column_index + 1, sheet.ncols):
                quantity_header = _normalize_header(sheet.cell(row_index, quantity_col))
                if "quantity adsorbed" in quantity_header:
                    return column_index, quantity_col, "mmol" in quantity_header
        return None

    @staticmethod
    def _microactive_copy_sample_name(workbook: ExcelWorkbook) -> str:
        for sheet in workbook.sheets:
            for row_index in range(sheet.nrows):
                for column_index in range(min(sheet.ncols, 3)):
                    text = _text(sheet.cell(row_index, column_index))
                    if _copy_isotherm_branch(text) is not None and ":" in text:
                        first = text.split(":", 1)[0].strip()
                        if first:
                            return first
        return ""

    @staticmethod
    def _parse_started_time(labels: dict[str, Any]) -> tuple[int, str]:
        started = _first_label(labels, "started", "开始时间")
        if started:
            return _timestamp_pair(started)
        analysis_date = _first_label(labels, "analysis date")
        analysis_time = _first_label(labels, "analysis time")
        combined = _combine_date_time(analysis_date, analysis_time)
        return _timestamp_pair(combined) if combined else (0, "")

    @staticmethod
    def _parse_completed_time(labels: dict[str, Any]) -> tuple[int, str]:
        return _timestamp_pair(_first_label(labels, "completed", "结束时间"))

    @staticmethod
    def _parse_report_time(labels: dict[str, Any]) -> tuple[int, str]:
        return _timestamp_pair(_first_label(labels, "report time"))

    @staticmethod
    def _add_report_values(labels: dict[str, Any], method_options: dict[str, Any]) -> None:
        mappings = {
            "bet surface area": "excel_bet_surface_area_m2_g",
            "langmuir surface area": "excel_langmuir_surface_area_m2_g",
            "t-plot micropore area": "excel_t_plot_micropore_area_m2_g",
            "t-plot external surface area": "excel_t_plot_external_surface_area_m2_g",
            "t-plot micropore volume": "excel_t_plot_micropore_volume_cm3_g",
        }
        for label, option_key in mappings.items():
            value = _number(_first_label(labels, label))
            if value is not None:
                method_options[option_key] = value

    @staticmethod
    def _add_bet_fit_range(workbook: ExcelWorkbook, method_options: dict[str, Any]) -> None:
        bsd_range = _bsd_bet_pressure_range(workbook)
        if bsd_range is not None:
            p_min, p_max, source = bsd_range
            method_options["stored_bet_pressure_min"] = p_min
            method_options["stored_bet_pressure_max"] = p_max
            method_options["excel_bet_range_source"] = source
            return
        fit_range = _bet_fit_pressure_range(workbook)
        if fit_range is None:
            return
        p_min, p_max, source = fit_range
        method_options["stored_bet_pressure_min"] = p_min
        method_options["stored_bet_pressure_max"] = p_max
        method_options["excel_bet_range_source"] = source

    @staticmethod
    def _add_bsd_report_values(workbook: ExcelWorkbook, method_options: dict[str, Any]) -> None:
        if not _is_bsd_workbook(workbook):
            return
        method_options["bsd_excel_import"] = True
        method_options["bsd_bjh_thickness_method"] = "halsey"
        method_options["bsd_bjh_kelvin_factor_nm"] = 0.954853
        for sheet in workbook.sheets:
            title = sheet.name
            if "BET" in title:
                _update_from_label_pairs(
                    sheet,
                    method_options,
                    {
                        "斜率a": "bsd_bet_slope",
                        "截距b": "bsd_bet_intercept",
                        "相关系数r": "bsd_bet_r",
                        "BET常数C": "bsd_bet_c_constant",
                        "单层吸附量Vm": "bsd_bet_monolayer_capacity_cm3_g_stp",
                        "BET比表面积": "excel_bet_surface_area_m2_g",
                    },
                )
            elif "Langmuir" in title:
                _update_from_label_pairs(
                    sheet,
                    method_options,
                    {
                        "斜率a": "bsd_langmuir_slope",
                        "截距b": "bsd_langmuir_intercept",
                        "相关系数r": "bsd_langmuir_r",
                        "常数C": "bsd_langmuir_c_constant",
                        "单层吸附量Vm": "bsd_langmuir_monolayer_capacity_cm3_g_stp",
                        "Langmuir比表面积": "excel_langmuir_surface_area_m2_g",
                    },
                )
                langmuir_range = _bsd_range_from_label(sheet, "P取点范围/kPa")
                if langmuir_range is not None:
                    method_options["bsd_langmuir_pressure_min_kpa"] = langmuir_range[0]
                    method_options["bsd_langmuir_pressure_max_kpa"] = langmuir_range[1]
            elif "T-Plot" in title or "T-plot" in title:
                _update_from_label_pairs(
                    sheet,
                    method_options,
                    {
                        "斜率a": "bsd_t_plot_slope",
                        "截距b": "bsd_t_plot_intercept",
                        "相关系数r": "bsd_t_plot_r",
                        "T-Plot微孔容积": "excel_t_plot_micropore_volume_cm3_g",
                        "T-Plot微孔比表面积": "excel_t_plot_micropore_area_m2_g",
                        "T-Plot外比表面积": "excel_t_plot_external_surface_area_m2_g",
                    },
                )
                t_range = _bsd_range_from_label(sheet, "P/P0取点范围")
                if t_range is not None:
                    method_options["bsd_t_plot_pressure_min"] = t_range[0]
                    method_options["bsd_t_plot_pressure_max"] = t_range[1]
            elif "BJH" in title:
                phase_key = "adsorption" if "吸附" in title else "desorption" if "脱附" in title else "unknown"
                prefix = f"bsd_bjh_{phase_key}"
                _update_from_label_pairs(
                    sheet,
                    method_options,
                    {
                        "BJH累积孔容积": f"{prefix}_pore_volume_cm3_g",
                        "BJH平均孔直径": f"{prefix}_average_diameter_nm",
                        "累计孔面积S": f"{prefix}_pore_area_m2_g",
                        "最可几孔直径": f"{prefix}_mode_diameter_nm",
                        "D10孔直径": f"{prefix}_d10_nm",
                        "D50孔直径": f"{prefix}_d50_nm",
                        "D90孔直径": f"{prefix}_d90_nm",
                        "D99孔直径": f"{prefix}_d99_nm",
                    },
                )
                table_rows = _bsd_bjh_table_rows(sheet, phase_key)
                if table_rows:
                    method_options[f"{prefix}_rows"] = table_rows


def _read_workbook(path: Path) -> ExcelWorkbook:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return _read_xls(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(path)
    raise ExcelParseError(f"Unsupported Excel extension: {path.suffix}")


def _read_xls(path: Path) -> ExcelWorkbook:
    try:
        import xlrd
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ExcelParseError("Reading .xls files requires xlrd. Please run: python -m pip install xlrd") from exc
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:
        raise ExcelParseError(f"Unsupported or corrupt .xls workbook: {path}") from exc
    sheets = []
    for sheet in book.sheets():
        rows = []
        for row_index in range(sheet.nrows):
            values = []
            for column_index in range(sheet.ncols):
                cell = sheet.cell(row_index, column_index)
                if cell.ctype == xlrd.XL_CELL_EMPTY:
                    values.append(None)
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    values.append(_xlrd_datetime(cell.value, book.datemode))
                else:
                    values.append(cell.value)
            rows.append(values)
        sheets.append(SheetGrid(name=sheet.name, values=rows))
    if not sheets:
        raise ExcelParseError(f"Workbook has no sheets: {path}")
    return ExcelWorkbook(sheets=sheets)


def _read_xlsx(path: Path) -> ExcelWorkbook:
    try:
        from openpyxl import load_workbook
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise ExcelParseError("Reading .xlsx/.xlsm files requires openpyxl. Please run: python -m pip install openpyxl") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            book = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:
        raise ExcelParseError(f"Unsupported or corrupt .xlsx/.xlsm workbook: {path}") from exc
    sheets = []
    for worksheet in book.worksheets:
        rows = []
        for row in worksheet.iter_rows(values_only=True):
            rows.append(list(row))
        sheets.append(SheetGrid(name=worksheet.title, values=rows))
    if not sheets:
        raise ExcelParseError(f"Workbook has no sheets: {path}")
    return ExcelWorkbook(sheets=sheets)


def _xlrd_datetime(value: float, datemode: int) -> Any:
    try:
        import xlrd

        moment = xlrd.xldate_as_datetime(value, datemode)
    except Exception:
        return value
    if 0 <= value < 1:
        return moment.time().replace(microsecond=0)
    if moment.time() == time(0, 0):
        return moment.date()
    return moment.replace(microsecond=0)


def _collect_labels(sheets: list[SheetGrid]) -> dict[str, Any]:
    labels: dict[str, Any] = {}
    for sheet in sheets:
        for row in sheet.values:
            for column_index, value in enumerate(row):
                label = _label_key(value)
                if not _is_label(label, value):
                    continue
                label_value = _scan_label_value(row, column_index)
                if label_value in (None, ""):
                    continue
                labels.setdefault(label, label_value)
    return labels


def _scan_label_value(row: list[Any], column_index: int) -> Any:
    for offset in range(1, 5):
        index = column_index + offset
        if index >= len(row):
            return None
        candidate = row[index]
        if _is_separator(candidate):
            return None
        if _is_blank(candidate):
            continue
        if _is_label(_label_key(candidate), candidate):
            return None
        return candidate
    return None


KNOWN_LABELS = {
    "adsorptive",
    "adsorption temperature",
    "analysis adsorptive",
    "analysis bath temp",
    "analysis bath temp.",
    "analysis date",
    "analysis free space",
    "analysis time",
    "automatic degas",
    "bar code",
    "cold free space",
    "comment",
    "completed",
    "data file",
    "equilibration interval",
    "file",
    "free space",
    "langmuir surface area",
    "molecular cross-sectional area",
    "molecular diameter",
    "operator",
    "report time",
    "sample",
    "sample density",
    "sample mass",
    "sample weight/g",
    "saturation vapor pressure",
    "started",
    "submitter",
    "t-plot external surface area",
    "t-plot micropore area",
    "t-plot micropore volume",
    "warm free space",
}


def _is_label(label: str, value: Any) -> bool:
    text = _as_clean_string(value)
    return bool(label) and (text.endswith(":") or label in KNOWN_LABELS)


def _label_key(value: Any) -> str:
    text = _as_clean_string(value).replace("：", ":")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.rstrip(":").strip()
    return text.lower()


def _detect_instrument(workbook: ExcelWorkbook) -> dict[str, Any]:
    texts = list(workbook.text_values())
    joined = "\n".join(texts[:500])
    lower = joined.lower()

    def first_contains(pattern: str) -> str:
        regex = re.compile(pattern, re.IGNORECASE)
        return next((text for text in texts if regex.search(text)), "")

    if "bsd-660" in lower or "贝士德" in joined or "bsd instrument" in lower:
        model = first_contains(r"BSD-660[^\s]*") or "BSD-660"
        version = first_contains(r"V\.\d+(?:\.\d+)+(?:\s+Date\s+\d{2}\.\d{2}\.\d{2})?")
        return {
            "instrument_manufacturer": "BSD",
            "instrument_model": model,
            "instrument_software": version,
        }
    if "microactive for tristar ii plus" in lower:
        software = first_contains(r"MicroActive for TriStar II Plus")
        return {
            "instrument_manufacturer": "Micromeritics",
            "instrument_model": "TriStar II Plus",
            "instrument_software": software,
        }
    if "tristar ii 3020" in lower:
        software = first_contains(r"TriStar II 3020")
        return {
            "instrument_manufacturer": "Micromeritics",
            "instrument_model": "TriStar II 3020",
            "instrument_software": software,
        }
    if "asap 2460" in lower:
        software = first_contains(r"ASAP 2460")
        return {
            "instrument_manufacturer": "Micromeritics",
            "instrument_model": "ASAP 2460",
            "instrument_software": software,
        }
    if "asap 2020 plus" in lower:
        software = first_contains(r"ASAP 2020 Plus")
        return {
            "instrument_manufacturer": "Micromeritics",
            "instrument_model": "ASAP 2020 Plus",
            "instrument_software": software,
        }
    if "belmaster" in lower:
        software = first_contains(r"BELMaster")
        return {
            "instrument_manufacturer": "MicrotracBEL",
            "instrument_model": "BELMaster",
            "instrument_software": software,
        }
    if "quantachrome" in lower or "autosorb" in lower:
        software = first_contains(r"Quantachrome|Autosorb")
        return {
            "instrument_manufacturer": "Quantachrome",
            "instrument_model": "Autosorb",
            "instrument_software": software,
        }
    return {
        "instrument_manufacturer": "",
        "instrument_model": "",
        "instrument_software": "",
    }


def _first_label(labels: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = labels.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _target_pressure_table_from_isotherm(isotherm: list[IsothermPoint]) -> list[TargetPressureRow]:
    previous_by_phase: dict[str, float] = {}
    rows = []
    for row_index, point in enumerate(isotherm, start=1):
        pressure = float(point.relative_pressure)
        previous = previous_by_phase.get(point.phase, 0.0)
        rows.append(
            TargetPressureRow(
                row=row_index,
                branch=point.phase,
                starting_pressure_p_po=previous,
                ending_pressure_p_po=pressure,
                pressure_increment_p_po=pressure - previous,
                ending_pressure_rel_offset=0,
            )
        )
        previous_by_phase[point.phase] = pressure
    return rows


def _is_bsd_workbook(workbook: ExcelWorkbook) -> bool:
    for text in workbook.text_values():
        lower = text.lower()
        if "bsd-660" in lower or "bsd instrument" in lower or "贝士德" in text:
            return True
    return False


def _bsd_bet_pressure_range(workbook: ExcelWorkbook) -> tuple[float, float, str] | None:
    for sheet in workbook.sheets:
        if "BET" not in sheet.name:
            continue
        values = _bsd_numeric_table_column(sheet, "p/p0")
        if len(values) >= 3:
            return min(values), max(values), f"BSD BET data table:{sheet.name}"
        label_range = _bsd_range_from_label(sheet, "P/P0取点范围")
        if label_range is not None:
            return label_range[0], label_range[1], f"BSD BET range label:{sheet.name}"
    return None


def _bsd_numeric_table_column(sheet: SheetGrid, header_text: str) -> list[float]:
    target = _normalize_header(header_text).replace(" ", "")
    for row_index in range(sheet.nrows):
        for column_index in range(sheet.ncols):
            header = _normalize_header(sheet.cell(row_index, column_index)).replace(" ", "")
            if header != target:
                continue
            values: list[float] = []
            blank_streak = 0
            for data_row in range(row_index + 1, sheet.nrows):
                value = _number(sheet.cell(data_row, column_index))
                if value is None:
                    if values:
                        blank_streak += 1
                        if blank_streak >= 2:
                            return values
                    continue
                if 0.0 < value < 1.0:
                    values.append(float(value))
                    blank_streak = 0
                elif values:
                    return values
            if values:
                return values
    return []


def _bsd_range_from_label(sheet: SheetGrid, label_text: str) -> tuple[float, float] | None:
    for row_index, row in enumerate(sheet.values):
        for column_index, value in enumerate(row):
            if label_text not in _as_clean_string(value):
                continue
            for offset in range(1, 7):
                raw = sheet.cell(row_index, column_index + offset)
                parsed = _range_from_text(raw)
                if parsed is not None:
                    return parsed
    return None


def _bsd_bjh_table_rows(sheet: SheetGrid, phase: str) -> list[dict[str, float]]:
    start: tuple[int, int] | None = None
    for row_index in range(sheet.nrows):
        for column_index in range(max(0, sheet.ncols - 8)):
            header = _normalize_header(sheet.cell(row_index, column_index)).replace(" ", "")
            next_header = _as_clean_string(sheet.cell(row_index, column_index + 1)).replace(" ", "")
            next_two = _as_clean_string(sheet.cell(row_index, column_index + 2)).replace(" ", "")
            if header in {"p/p0", "p/po"} and next_header == "dnm" and next_two == "Dnm":
                start = (row_index, column_index)
                break
        if start is not None:
            break
    if start is None:
        return []

    header_row, start_col = start
    raw_rows: list[dict[str, float]] = []
    blank_streak = 0
    for row_index in range(header_row + 1, sheet.nrows):
        values = [_number(sheet.cell(row_index, start_col + offset)) for offset in range(9)]
        if any(value is None for value in values):
            if raw_rows:
                blank_streak += 1
                if blank_streak >= 2:
                    break
            continue
        blank_streak = 0
        (
            relative_pressure,
            kelvin_pore_diameter,
            pore_diameter,
            cumulative_volume,
            cumulative_area,
            differential_volume_per_nm,
            differential_area_per_nm,
            differential_volume,
            differential_area,
        ) = [float(value) for value in values]
        raw_rows.append(
            {
                "phase": phase,
                "relative_pressure": relative_pressure,
                "kelvin_pore_diameter_nm": kelvin_pore_diameter,
                "pore_diameter_nm": pore_diameter,
                "cumulative_pore_volume_cm3_g": cumulative_volume,
                "cumulative_pore_area_m2_g": cumulative_area,
                "differential_pore_volume_per_nm_cm3_g_nm": differential_volume_per_nm,
                "differential_pore_area_per_nm_m2_g_nm": differential_area_per_nm,
                "differential_pore_volume_cm3_g": differential_volume,
                "differential_pore_area_m2_g": differential_area,
            }
        )
    return raw_rows


def _update_from_label_pairs(sheet: SheetGrid, options: dict[str, Any], mapping: dict[str, str]) -> None:
    for row_index, row in enumerate(sheet.values):
        for column_index, value in enumerate(row):
            text = _as_clean_string(value)
            if not text:
                continue
            for label, option_key in mapping.items():
                if label not in text or option_key in options:
                    continue
                candidate = _next_numeric_value(sheet, row_index, column_index)
                if candidate is not None:
                    options[option_key] = candidate


def _next_numeric_value(sheet: SheetGrid, row_index: int, column_index: int) -> float | None:
    for offset in range(1, 8):
        value = _number(sheet.cell(row_index, column_index + offset))
        if value is not None:
            return value
    return None


def _bet_fit_pressure_range(workbook: ExcelWorkbook) -> tuple[float, float, str] | None:
    for sheet in workbook.sheets:
        candidate = _find_bet_transform_table(sheet)
        if candidate is not None:
            return candidate
    return None


def _find_bet_transform_table(sheet: SheetGrid) -> tuple[float, float, str] | None:
    for row_index in range(sheet.nrows):
        for column_index in range(sheet.ncols - 1):
            current = _normalize_header(sheet.cell(row_index, column_index))
            next_one = _normalize_header(sheet.cell(row_index, column_index + 1))
            next_two = _normalize_header(sheet.cell(row_index, column_index + 2))
            if not _is_relative_pressure_header(current):
                continue
            if _is_bet_transform_header(next_one):
                pressures = _numeric_pressure_column(sheet, row_index + 1, column_index)
                if len(pressures) >= 3:
                    return min(pressures), max(pressures), f"BET Surface Area Plot:{sheet.name}"
            if "quantity adsorbed" in next_one and _is_bet_transform_header(next_two):
                pressures = _numeric_pressure_column(sheet, row_index + 1, column_index)
                if len(pressures) >= 3:
                    return min(pressures), max(pressures), f"BET Surface Area Report:{sheet.name}"
    return None


def _numeric_pressure_column(sheet: SheetGrid, start_row: int, column_index: int) -> list[float]:
    values: list[float] = []
    blank_streak = 0
    for row_index in range(start_row, sheet.nrows):
        pressure = _number(sheet.cell(row_index, column_index))
        if pressure is None:
            if values:
                blank_streak += 1
                if blank_streak >= 2:
                    break
            continue
        if 0.0 < pressure < 1.0:
            values.append(float(pressure))
            blank_streak = 0
            continue
        if values:
            break
    return values


def _copy_isotherm_branch(value: Any) -> str | None:
    """Return ``"adsorption"`` / ``"desorption"`` only for a MicroActive copy
    branch marker of the form ``Sample : Sample : Adsorption``.

    The colon-prefixed form is required so that neither option labels such as
    ``BJH Cumulative Adsorption:`` nor bare section headings such as
    ``Adsorption`` (both present in full report exports) are treated as the
    start of a copy-paste isotherm branch."""
    text = _as_clean_string(value).lower().rstrip(": ").strip()
    for branch in ("adsorption", "desorption"):
        if text.endswith(f": {branch}"):
            return branch
    return None


def _is_relative_pressure_header(value: str) -> bool:
    return "relative pressure" in value or value.replace(" ", "") in {"p/p0", "p/po"}


def _is_bet_transform_header(value: str) -> bool:
    compact = value.replace(" ", "").replace("po", "p0")
    return "1/[q(p0/p-1)]" in compact or "1/[q(p0/p-1)]" in compact.replace("−", "-")


def _normalize_header(value: Any) -> str:
    return _as_clean_string(value).lower().replace("³", "3").replace("²", "2")


def _as_clean_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _text(value: Any) -> str:
    return _as_clean_string(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = _as_clean_string(value)
    if not text:
        return None
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _range_from_text(value: Any) -> tuple[float, float] | None:
    text = _as_clean_string(value)
    if not text:
        return None
    numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", text)
    if len(numbers) < 2:
        return None
    try:
        left = float(numbers[0])
        right = float(numbers[1])
    except ValueError:
        return None
    return (min(left, right), max(left, right))


def _adsorptive_short(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    first = re.split(r"[,，;；\s]+", text, maxsplit=1)[0].strip()
    return first or text


def _adsorptive_temperature_k(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    match = re.search(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*[kK]\b", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    match = re.search(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:℃|°C|°c)\b", text)
    if match:
        try:
            return float(match.group(1)) + 273.15
        except ValueError:
            return None
    return None


def _temperature_k(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    text = _as_clean_string(value).lower()
    if "°c" in text or re.search(r"\bc\b", text):
        return number + 273.15
    if number < 0.0:
        return number + 273.15
    return number


def _elapsed_display_seconds(value: Any) -> int | None:
    text = _as_clean_string(value)
    match = re.match(r"^\s*(\d+):([0-5]\d)(?::([0-5]\d))?\s*$", text)
    if not match:
        return None
    first = int(match.group(1))
    second = int(match.group(2))
    third = int(match.group(3) or 0)
    if match.group(3) is None:
        return first * 60 + second
    return first * 3600 + second * 60 + third


def _timestamp_pair(value: Any) -> tuple[int, str]:
    moment = _as_datetime(value)
    if moment is None:
        return 0, _as_clean_string(value)
    try:
        raw = int(moment.timestamp())
    except (OverflowError, OSError, ValueError):
        raw = 0
    return raw, moment.strftime("%Y-%m-%d %H:%M:%S")


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time())
    text = _as_clean_string(value)
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _combine_date_time(date_value: Any, time_value: Any) -> datetime | None:
    date_part = _as_datetime(date_value)
    if date_part is None:
        return None
    if isinstance(time_value, time):
        return datetime.combine(date_part.date(), time_value)
    time_part = _as_datetime(time_value)
    if time_part is not None:
        return datetime.combine(date_part.date(), time_part.time())
    text = _as_clean_string(time_value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        return datetime.combine(date_part.date(), parsed)
    return date_part


def _duration_text(start_raw: int, end_raw: int) -> tuple[str, int]:
    if not start_raw or not end_raw or end_raw < start_raw:
        return "", 0
    total = int(end_raw - start_raw)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}", total


def _file_modified_timestamp(path: Path) -> tuple[int, str]:
    try:
        raw = int(path.stat().st_mtime)
    except OSError:
        return 0, ""
    try:
        return raw, datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return raw, ""


def _is_blank(value: Any) -> bool:
    return value is None or _as_clean_string(value) == ""


def _is_separator(value: Any) -> bool:
    return _as_clean_string(value) == "|"
