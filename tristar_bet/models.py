from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SmpHeader:
    file_path: str
    file_name: str
    byte_count: int
    magic: str
    version: str
    created_raw: int
    created_time: str
    modified_raw: int
    modified_time: str
    directory_offset: int
    directory_size: int


@dataclass(frozen=True)
class SubsetEntry:
    subset_id: int
    marker: str
    offset: int
    payload_size: int
    total_size: int


@dataclass(frozen=True)
class MicString:
    rel_offset: int
    text: str


@dataclass(frozen=True)
class SampleInfo:
    sample_name: str
    operator: str
    submitter: str
    bar_code: str
    sample_mass_g: float | None
    sample_density_g_cm3: float | None


@dataclass(frozen=True)
class RunConditions:
    evacuation_rate_mmHg_s: float | None
    unrestricted_evacuate_from_mmHg: float | None
    evacuation_time_h: float | None
    leak_test_time_s: int | None
    equilibration_interval_s: float | None
    free_space_equilibration_time_h: float | None
    ambient_free_space_entered_cm3: float | None
    analysis_free_space_entered_cm3: float | None
    desorption_test_time_s: int | None
    po_reference_mmHg: float | None
    bath_temperature_K: float | None
    adsorptive_short: str
    adsorptive_name: str


@dataclass(frozen=True)
class TargetPressureRow:
    row: int
    branch: str
    starting_pressure_p_po: float
    ending_pressure_p_po: float
    pressure_increment_p_po: float
    ending_pressure_rel_offset: int


@dataclass(frozen=True)
class FreeSpaceInfo:
    analysis_entered_cm3: float | None
    ambient_entered_cm3: float | None
    nonideality_factor: float | None
    cold_free_space_cm3: float | None
    warm_free_space_cm3: float | None
    stem_volume_cm3: float | None
    vbath_cm3: float | None
    vfree_factor_cm3: float | None
    vfree_factor_source: str
    ambient_temperature_K_assumed: float = 298.0


@dataclass(frozen=True)
class PoRecord:
    index: int
    rel_offset: int
    saturation_pressure_mmHg: float
    elapsed_seconds: int

    @property
    def elapsed_time(self) -> str:
        minutes, seconds = divmod(int(self.elapsed_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class IsothermPoint:
    index: int
    phase: str
    record_rel_offset: int
    absolute_pressure_mmHg: float
    relative_pressure: float
    raw_internal_cm3_stp: float
    saturation_pressure_mmHg: float | None
    elapsed_seconds: int | None
    quantity_adsorbed_cm3_g_stp: float | None
    quantity_adsorbed_mmol_g: float | None

    @property
    def elapsed_time(self) -> str:
        if self.elapsed_seconds is None:
            return ""
        minutes, seconds = divmod(int(self.elapsed_seconds), 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class AdsorptiveProperties:
    adsorptive: str
    mnemonic: str
    max_manifold_pressure_mmHg: float | None
    max_manifold_pressure_kPa: float | None
    nonideality_factor: float | None
    density_conversion_factor: float | None
    thermal_transpiration_hard_sphere_A: float | None
    thermal_transpiration_hard_sphere_nm: float | None
    molecular_cross_sectional_area_nm2: float | None
    ui_field_rel101: float | None
    psat_table: list[dict[str, float | int]] = field(default_factory=list)


@dataclass(frozen=True)
class TriStarResult:
    header: SmpHeader
    subsets: list[SubsetEntry]
    sample: SampleInfo
    run_conditions: RunConditions
    target_pressure_table: list[TargetPressureRow]
    free_space: FreeSpaceInfo
    po_records: list[PoRecord]
    isotherm: list[IsothermPoint]
    adsorptive_properties: AdsorptiveProperties | None
    log_messages: list[MicString]
    sample_tube_strings: list[MicString]
    method_options: dict[str, Any] = field(default_factory=dict)
    raw_strings: dict[int, list[MicString]] = field(default_factory=dict)

    @property
    def file_name(self) -> str:
        return self.header.file_name

    @property
    def sample_name(self) -> str:
        return self.sample.sample_name or Path(self.header.file_name).stem

    @property
    def point_count(self) -> int:
        return len(self.isotherm)

    def summary_dict(self) -> dict[str, Any]:
        return {
            "file": self.header.file_name,
            "sample_name": self.sample_name,
            "operator": self.sample.operator,
            "sample_mass_g": self.sample.sample_mass_g,
            "created_time": self.header.created_time,
            "modified_time": self.header.modified_time,
            "adsorptive": self.run_conditions.adsorptive_short,
            "bath_temperature_K": self.run_conditions.bath_temperature_K,
            "equilibration_interval_s": self.run_conditions.equilibration_interval_s,
            "cold_free_space_cm3": self.free_space.cold_free_space_cm3,
            "warm_free_space_cm3": self.free_space.warm_free_space_cm3,
            "vfree_factor_cm3": self.free_space.vfree_factor_cm3,
            "vfree_factor_source": self.free_space.vfree_factor_source,
            "point_count": self.point_count,
            "po_record_count": len(self.po_records),
            "target_pressure_rows": len(self.target_pressure_table),
        }
