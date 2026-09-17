from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import struct
import sys
import zipfile

import numpy as np


@dataclass(frozen=True)
class DftModelSpec:
    key: str
    label: str
    model_id: str
    file_name: str
    analysis_type: str = "dft_pore"
    geometry: str = "slit"
    adsorptive: str = "n2"
    kernel_basis: str = "pore_volume"


@dataclass(frozen=True)
class GaiMetadata:
    """Numerical settings and per-column metadata, not just the GAI matrix."""

    command: str
    options: tuple[tuple[str, float], ...]
    transition_pressures: np.ndarray
    geometry_divisors: np.ndarray | None
    temperature_k: float | None
    method: str
    units: str

    def option(self, key: str, default: float = 0.0) -> float:
        return dict(self.options).get(key, default)


@dataclass(frozen=True)
class DftModelKernel:
    spec: DftModelSpec
    pressures: np.ndarray
    pore_widths_nm: np.ndarray
    kernel: np.ndarray
    filling_threshold: float | None = None
    gai: GaiMetadata | None = None


DFT_MODEL_SPECS: dict[str, DftModelSpec] = {
    "n2_dft_model": DftModelSpec(
        key="n2_dft_model",
        label="N2 - DFT Model",
        model_id="MOD000",
        file_name="mod000.df2",
    ),
    "n2_nldft_carbon_slit": DftModelSpec(
        key="n2_nldft_carbon_slit",
        label="N2 @ 77 on Carbon Slit Pores by NLDFT",
        model_id="MOD200",
        file_name="mod200.df3",
    ),
    "het_n2_carbon_slit": DftModelSpec(
        key="het_n2_carbon_slit",
        label="Het N2 carbon slit",
        model_id="HET_N2_SLIT",
        file_name="het_n2_slit.df3",
        adsorptive="n2",
    ),
    "het_co2_carbon_slit": DftModelSpec(
        key="het_co2_carbon_slit",
        label="Het CO2 carbon slit",
        model_id="HET_CO2_SLIT",
        file_name="het_co2_slit.df3",
        adsorptive="co2",
    ),
}


def _gai_model_spec(
    key: str,
    label: str,
    model_id: str,
    file_name: str,
    geometry: str,
    adsorptive: str,
    *,
    kernel_basis: str = "surface_mmol",
) -> DftModelSpec:
    return DftModelSpec(
        key=key,
        label=label,
        model_id=model_id,
        file_name=file_name,
        geometry=geometry,
        adsorptive=adsorptive,
        kernel_basis=kernel_basis,
    )


# GAI labels describe only the physical model; the software vendor is not part
# of the user-facing name.
GAI_MODEL_SPECS: tuple[DftModelSpec, ...] = (
    _gai_model_spec("n2_77_carbon_slit_nldft_eq", "N2 @ 77K, Carbon Slit Pores, NLDFT Equilibrium", "QK001", "N2_carb1.gai", "slit", "n2"),
    _gai_model_spec("n2_77_carbon_slit_qsdft_eq", "N2 @ 77K, Carbon Slit Pores, QSDFT Equilibrium", "QK002", "N2_carb1_QSDFT-0s.gai", "slit", "n2"),
    _gai_model_spec("n2_77_carbon_cylinder_nldft_eq", "N2 @ 77K, Carbon Cylindrical Pores, NLDFT Equilibrium", "QK003", "N2-carb-allcyl.gai", "cylinder", "n2"),
    _gai_model_spec("n2_77_carbon_slit_cylinder_nldft_eq", "N2 @ 77K, Carbon Slit/Cylindrical Pores, NLDFT Equilibrium", "QK004", "N2-carb-cyl.gai", "mixed", "n2"),
    _gai_model_spec("ar_77_carbon_slit_nldft_eq", "Ar @ 77K, Carbon Slit Pores, NLDFT Equilibrium", "QK005", "A77_CARB.gai", "slit", "ar", kernel_basis="volume_mmol"),
    _gai_model_spec("ar_87_carbon_cylinder_nldft_eq", "Ar @ 87K, Carbon Cylindrical Pores, NLDFT Equilibrium", "QK006", "A87-carb-allcyl.gai", "cylinder", "ar"),
    _gai_model_spec("ar_87_carbon_slit_nldft_eq", "Ar @ 87K, Carbon Slit Pores, NLDFT Equilibrium", "QK007", "A87_carb1.gai", "slit", "ar"),
    _gai_model_spec("co2_273_carbon_slit_nldft", "CO2 @ 273K, Carbon Slit Pores, NLDFT", "QK008", "CO2_nldf.gai", "slit", "co2"),
    _gai_model_spec("n2_77_silica_cylinder_nldft_eq", "N2 @ 77K, Silica Cylindrical Pores, NLDFT Equilibrium", "QK009", "N2-silica-eq.gai", "cylinder", "n2"),
    _gai_model_spec("n2_77_silica_cylinder_nldft_ads", "N2 @ 77K, Silica Cylindrical Pores, NLDFT Adsorption", "QK010", "N2-silica-ads.gai", "cylinder", "n2"),
    _gai_model_spec("n2_77_silica_cylinder_sphere_nldft_ads", "N2 @ 77K, Silica Cylindrical/Spherical Pores, NLDFT Adsorption", "QK011", "N2-silica-sph-ads-55.gai", "mixed", "n2"),
    _gai_model_spec("ar_87_zeolite_silica_cylinder_nldft_eq", "Ar @ 87K, Zeolite/Silica Cylindrical Pores, NLDFT Equilibrium", "QK012", "A87-zeol-Si-cyl-eq.gai", "cylinder", "ar"),
    _gai_model_spec("ar_87_zeolite_silica_cylinder_nldft_ads", "Ar @ 87K, Zeolite/Silica Cylindrical Pores, NLDFT Adsorption", "QK013", "A87-zeol-Si-cyl-ads.gai", "cylinder", "ar"),
    _gai_model_spec("ar_87_zeolite_silica_sphere_cylinder_nldft_eq", "Ar @ 87K, Zeolite/Silica Spherical/Cylindrical Pores, NLDFT Equilibrium", "QK014", "A87-zeol-Si-sph-eq.gai", "mixed", "ar"),
    _gai_model_spec("ar_87_zeolite_silica_sphere_cylinder_nldft_ads", "Ar @ 87K, Zeolite/Silica Spherical/Cylindrical Pores, NLDFT Adsorption", "QK015", "A87-zeol-Si-sph-ads.gai", "mixed", "ar"),
    _gai_model_spec("ar_87_carbon_slit_qsdft_eq", "Ar @ 87K, Carbon Slit Pores, QSDFT Equilibrium", "QK016", "Ar_Carbon_QSDFT_0(REPA_0).gai", "slit", "ar"),
    _gai_model_spec("n2_77_carbon_cylinder_qsdft_ads", "N2 @ 77K, Carbon Cylindrical Pores, QSDFT Adsorption", "QK017", "N2-carb-QS-cyl-ads.gai", "cylinder", "n2"),
    _gai_model_spec("n2_77_carbon_cylinder_qsdft_eq", "N2 @ 77K, Carbon Cylindrical Pores, QSDFT Equilibrium", "QK018", "N2-carb-QS-cyl-eq.gai", "cylinder", "n2"),
    _gai_model_spec("n2_77_carbon_cylinder_sphere_qsdft_ads", "N2 @ 77K, Carbon Cylindrical/Spherical Pores, QSDFT Adsorption", "QK019", "N2-carb-QS-sph-5-cyl-ads.gai", "mixed", "n2"),
    _gai_model_spec("n2_77_carbon_slit_cylinder_qsdft_ads", "N2 @ 77K, Carbon Slit/Cylindrical Pores, QSDFT Adsorption", "QK020", "N2-carb-QS-cyl-slit-ads.gai", "mixed", "n2"),
    _gai_model_spec("n2_77_carbon_slit_cylinder_qsdft_eq", "N2 @ 77K, Carbon Slit/Cylindrical Pores, QSDFT Equilibrium", "QK021", "N2-carb-QS-cyl-slit-equ.gai", "mixed", "n2"),
    _gai_model_spec("n2_77_carbon_slit_cylinder_sphere_qsdft_ads", "N2 @ 77K, Carbon Slit/Cylindrical/Spherical Pores, QSDFT Adsorption", "QK022", "N2-carb-QS-sph-cyl-slit-ads.gai", "mixed", "n2"),
    _gai_model_spec("ar_87_carbon_cylinder_sphere_qsdft_ads", "Ar @ 87K, Carbon Cylindrical/Spherical Pores, QSDFT Adsorption", "QK023", "argon-carbon-cyl_5_sph-ads-QSDFT.gai", "mixed", "ar"),
    _gai_model_spec("ar_87_carbon_cylinder_qsdft_eq", "Ar @ 87K, Carbon Cylindrical Pores, QSDFT Equilibrium", "QK024", "argon-carbon-cyl-equ-QSDFT.gai", "cylinder", "ar"),
    _gai_model_spec("ar_87_carbon_cylinder_qsdft_ads", "Ar @ 87K, Carbon Cylindrical Pores, QSDFT Adsorption", "QK025", "argon-carbon-QSDFT-cyl-ads.gai", "cylinder", "ar"),
    _gai_model_spec("co2_273_carbon_slit_gcmc", "CO2 @ 273K, Carbon Slit Pores, GCMC", "QK026", "CO2_GCMC.gai", "slit", "co2"),
)

_MODEL_KEY_ALIASES = {
    "MOD000": "n2_dft_model",
    "MOD200": "n2_nldft_carbon_slit",
}
_MODEL_HEADER_SIZE = 239


def dft_model_options(
    *,
    analysis_type: str = "all",
    geometry: str = "all",
    adsorptive: str = "all",
) -> list[tuple[str, str]]:
    type_filter = str(analysis_type or "all").lower()
    geometry_filter = str(geometry or "all").lower()
    adsorptive_filter = str(adsorptive or "all").lower()
    return [
        (spec.key, spec.label)
        for spec in _available_model_specs().values()
        if (type_filter == "all" or spec.analysis_type == type_filter)
        and (geometry_filter == "all" or spec.geometry == geometry_filter)
        and (adsorptive_filter == "all" or spec.adsorptive == adsorptive_filter)
    ]


def dft_model_spec(key: str) -> DftModelSpec | None:
    value = str(key)
    specs = _available_model_specs()
    direct = specs.get(value)
    if direct is not None:
        return direct
    normalized = Path(value).stem.upper()
    return next((spec for spec in specs.values() if spec.model_id == normalized), None)


def dft_model_label(key: str) -> str:
    spec = dft_model_spec(str(key))
    return spec.label if spec is not None else str(key)


@lru_cache(maxsize=64)
def load_dft_model_kernel(key: str) -> DftModelKernel | None:
    spec = dft_model_spec(str(key))
    if spec is None:
        return None
    data = _read_model_file(spec.file_name)
    if data is None:
        return None
    return _parse_model_file(spec, data)


def interpolate_dft_kernel(kernel: DftModelKernel, pressures: np.ndarray) -> np.ndarray:
    pressure = np.asarray(pressures, dtype=float)
    source_pressure = np.asarray(kernel.pressures, dtype=float)
    source_matrix = np.asarray(kernel.kernel, dtype=float)
    if pressure.size == 0 or source_pressure.size == 0 or source_matrix.size == 0:
        return np.zeros((pressure.size, source_matrix.shape[1] if source_matrix.ndim == 2 else 0), dtype=float)
    matrix = np.empty((pressure.size, source_matrix.shape[1]), dtype=float)
    pressure = np.clip(pressure, source_pressure[0], source_pressure[-1])
    for column in range(source_matrix.shape[1]):
        values = source_matrix[:, column]
        matrix[:, column] = np.interp(
            pressure,
            source_pressure,
            values,
            left=float(values[0]),
            right=float(values[-1]),
        )
    matrix[~np.isfinite(matrix)] = 0.0
    matrix[matrix < 0.0] = 0.0
    return matrix


def convert_dft_kernel_to_pore_volume_basis(
    kernel: DftModelKernel,
    matrix: np.ndarray,
    *,
    liquid_molar_volume_cm3_mmol: float,
) -> np.ndarray:
    """Convert explicitly identified amount kernels to a pore-volume basis.

    ``surface_stp`` contains cm3(STP)/m2, unlike GAI ``surface_mmol``.
    Do not treat either surface basis as a dimensionless pore filling fraction.
    """
    values = np.asarray(matrix, dtype=float)
    basis = kernel.spec.kernel_basis
    if basis == "pore_volume":
        return values
    molar_volume = float(liquid_molar_volume_cm3_mmol)
    if not np.isfinite(molar_volume) or molar_volume <= 0.0:
        return values
    if basis == "volume_mmol":
        converted = values * molar_volume
    elif basis in {"surface_mmol", "surface_stp"}:
        liquid_per_amount = molar_volume / 22.414 if basis == "surface_stp" else molar_volume
        widths_nm = np.asarray(kernel.pore_widths_nm, dtype=float)
        if kernel.gai is not None and kernel.gai.geometry_divisors is not None:
            divisors = kernel.gai.geometry_divisors
        elif kernel.spec.geometry == "slit":
            divisors = np.full(widths_nm.shape, 2.0)
        elif kernel.spec.geometry == "cylinder":
            divisors = np.full(widths_nm.shape, 4.0)
        elif kernel.spec.geometry == "sphere":
            divisors = np.full(widths_nm.shape, 6.0)
        else:
            # Mixed kernels change shape across the width grid.  Their
            # high-pressure capacities identify the matching W/2, D/4, or
            # D/6 geometric volume/area relation for each kernel column.
            source = np.asarray(kernel.kernel, dtype=float)
            high_pressure = np.asarray(kernel.pressures, dtype=float) >= 0.95
            capacity_rows = source[high_pressure, :] if np.any(high_pressure) else source
            capacity = np.max(capacity_rows, axis=0)
            inferred = np.divide(
                widths_nm * 1e-3,
                capacity * liquid_per_amount,
                out=np.full(widths_nm.shape, 4.0),
                where=capacity > 1e-15,
            )
            choices = np.asarray([2.0, 4.0, 6.0])
            divisors = choices[np.argmin(np.abs(inferred[:, None] - choices[None, :]), axis=1)]
        volume_per_area = np.maximum(widths_nm * 1e-3 / divisors, 1e-15)
        converted = values * liquid_per_amount / volume_per_area[None, :]
    else:
        return values
    converted[~np.isfinite(converted)] = 0.0
    converted[converted < 0.0] = 0.0
    return converted


def _read_model_file(file_name: str) -> bytes | None:
    zip_path = _find_model_zip()
    if zip_path is None:
        return None
    target = file_name.lower()
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        for name in names:
            if Path(name).name.lower() == target:
                return archive.read(name)
    return None


def _find_model_zip() -> Path | None:
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "DFT-NLDFT-Models.zip")
    module_root = Path(__file__).resolve().parent
    candidates.extend(
        [
            module_root / "DFT-NLDFT-Models.zip",
            module_root.parent / "DFT-NLDFT-Models.zip",
            Path.cwd() / "DFT-NLDFT-Models.zip",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def _available_model_specs() -> dict[str, DftModelSpec]:
    """Return every distinct Micromeritics model shipped in the archive.

    The archive contains a few byte-identical ``.df2``/``.df3`` pairs.  They
    represent one logical model, so only the newer ``.df3`` entry is exposed.
    MOD000 and MOD200 retain their original keys for backward compatibility.
    """
    specs_by_id: dict[str, DftModelSpec] = {
        spec.model_id.upper(): spec for spec in (*DFT_MODEL_SPECS.values(), *GAI_MODEL_SPECS)
    }
    zip_path = _find_model_zip()
    if zip_path is None:
        return {spec.key: spec for spec in specs_by_id.values()}

    try:
        with zipfile.ZipFile(zip_path) as archive:
            candidates: dict[str, str] = {}
            for name in archive.namelist():
                path = Path(name)
                suffix = path.suffix.lower()
                model_id = path.stem.upper()
                if suffix not in {".df2", ".df3"} or re.fullmatch(r"MOD\d{3}", model_id) is None:
                    continue
                previous = candidates.get(model_id)
                if previous is None or (suffix == ".df3" and Path(previous).suffix.lower() != ".df3"):
                    candidates[model_id] = name

            for model_id, archive_name in candidates.items():
                file_name = Path(archive_name).name
                data = archive.read(archive_name)
                key = _MODEL_KEY_ALIASES.get(model_id, f"micromeritics_{model_id.lower()}")
                analysis_type, geometry, adsorptive = _model_classification_from_header(data)
                specs_by_id[model_id] = DftModelSpec(
                    key=key,
                    label=_model_label_from_header(model_id, data),
                    model_id=model_id,
                    file_name=file_name,
                    analysis_type=analysis_type,
                    geometry=geometry,
                    adsorptive=adsorptive,
                    # MOD005's 1--200 nm filled-cylinder capacities satisfy
                    # K * 0.001546 = D_nm / 4000 (cm3/m2). Its values are
                    # cm3(STP)/m2, not a liquid-volume filling fraction.
                    # Limit this correction to the independently checked model;
                    # other model bases and special columns need separate audits.
                    kernel_basis="surface_stp" if model_id == "MOD005" else "pore_volume",
                )
    except (OSError, zipfile.BadZipFile, KeyError):
        pass

    def sort_key(spec: DftModelSpec) -> tuple[int, int, str]:
        match = re.fullmatch(r"MOD(\d{3})", spec.model_id.upper())
        if match is not None:
            return 0, int(match.group(1)), spec.label.casefold()
        return 1, 0, spec.label.casefold()

    ordered = sorted(specs_by_id.values(), key=sort_key)
    return {spec.key: spec for spec in ordered}


def _model_label_from_header(model_id: str, data: bytes) -> str:
    short_name = _ascii_header_field(data, 19, 40)
    description = _ascii_header_field(data, 99, 100)
    description_has_adsorptive = re.search(
        r"\b(?:N2|AR|ARGON|CO2|O2|H2)\b",
        description,
        flags=re.IGNORECASE,
    )
    name = description if description_has_adsorptive else short_name or description or model_id
    return name


def _model_adsorptive_from_header(data: bytes) -> str:
    value = _ascii_header_field(data, 15, 4).lower()
    aliases = {
        "nitrogen": "n2",
        "argon": "ar",
        "carbon dioxide": "co2",
        "oxygen": "o2",
        "hydrogen": "h2",
    }
    return aliases.get(value, value or "unknown")


def _model_classification_from_header(data: bytes) -> tuple[str, str, str]:
    """Read the official type, geometry, and gas fields from a model header."""
    analysis_type = "typical" if len(data) > 3 and data[3] == 1 else "dft_pore"
    geometry = "cylinder" if len(data) > 5 and data[5] == 1 else "slit"
    return analysis_type, geometry, _model_adsorptive_from_header(data)


def _ascii_header_field(data: bytes, offset: int, size: int) -> str:
    raw = data[offset : offset + size].split(b"\x00", 1)[0]
    return raw.decode("latin-1", errors="replace").strip()


def _parse_model_file(spec: DftModelSpec, data: bytes) -> DftModelKernel | None:
    if Path(spec.file_name).suffix.lower() == ".gai":
        return _parse_gai_model_file(spec, data)
    layout = _detect_model_layout(data)
    if layout is None:
        return None
    row_count, column_count, width_offset, matrix_offset = layout
    matrix_count = row_count * column_count
    pressure = np.frombuffer(
        data,
        dtype="<f8",
        count=row_count,
        offset=_MODEL_HEADER_SIZE,
    ).copy()
    pore_width_angstrom = np.frombuffer(
        data,
        dtype="<f8",
        count=column_count,
        offset=width_offset,
    ).copy()
    matrix = np.frombuffer(data, dtype="<f8", count=matrix_count, offset=matrix_offset).copy()
    matrix = matrix.reshape(row_count, column_count)

    valid_pressure = np.isfinite(pressure) & (pressure > 0.0)
    valid_width = np.isfinite(pore_width_angstrom) & (pore_width_angstrom > 0.0)
    if int(np.count_nonzero(valid_pressure)) < 3 or int(np.count_nonzero(valid_width)) < 3:
        return None
    pressure = pressure[valid_pressure]
    matrix = matrix[valid_pressure, :]
    pore_width_angstrom = pore_width_angstrom[valid_width]
    matrix = matrix[:, valid_width]

    pressure, matrix = _sort_and_deduplicate_axis(pressure, matrix, axis=0)
    pore_width_angstrom, matrix = _sort_and_deduplicate_axis(
        pore_width_angstrom,
        matrix,
        axis=1,
    )
    if pressure.size < 3 or pore_width_angstrom.size < 3:
        return None
    matrix[~np.isfinite(matrix)] = 0.0
    matrix[matrix < 0.0] = 0.0
    return DftModelKernel(
        spec=spec,
        pressures=np.asarray(pressure, dtype=float),
        pore_widths_nm=np.asarray(pore_width_angstrom, dtype=float) * 0.1,
        kernel=matrix,
        filling_threshold=(struct.unpack_from("<d", data, 227)[0]
                           if spec.model_id == "MOD005" else None),
    )


def _parse_gai_model_file(spec: DftModelSpec, data: bytes) -> DftModelKernel | None:
    """Read the matrix and optional numerical metadata without guessing tails.

    Supplementary GCMC sections are not yet decoded. A readable primary
    matrix does not establish compatibility with its native inversion path.
    """
    offset = 0
    headers = []
    try:
        for _ in range(3):
            if offset + 2 > len(data):
                return None
            field_size = struct.unpack_from("<H", data, offset)[0]
            headers.append(data[offset + 2:offset + 2 + field_size].decode("latin-1").strip("\x00 "))
            offset += 2 + int(field_size)
            if offset > len(data):
                return None
        if offset + 2 > len(data):
            return None
        width_count = int(struct.unpack_from("<H", data, offset)[0])
        offset += 2
        if not 3 <= width_count <= 4096 or offset + width_count * 4 + 2 > len(data):
            return None
        pore_width_angstrom = np.frombuffer(
            data,
            dtype="<f4",
            count=width_count,
            offset=offset,
        ).astype(float)
        offset += width_count * 4
        pressure_count = int(struct.unpack_from("<H", data, offset)[0])
        offset += 2
        table_count = pressure_count * (width_count + 1)
        if not 3 <= pressure_count <= 8192 or offset + table_count * 4 > len(data):
            return None
        table = np.frombuffer(
            data,
            dtype="<f4",
            count=table_count,
            offset=offset,
        ).astype(float).reshape(pressure_count, width_count + 1)
    except (ValueError, struct.error):
        return None

    metadata = _parse_gai_metadata(data, offset + table_count * 4, width_count, headers)
    # Only attach column metadata when its correspondence is unambiguous.
    # Malformed/nonmonotonic axes still retain the legacy primary-table reader.
    if (not np.all(np.isfinite(pore_width_angstrom)) or
            np.any(pore_width_angstrom <= 0) or np.any(np.diff(pore_width_angstrom) <= 0)):
        metadata = None
    pressure = table[:, 0]
    matrix = table[:, 1:]
    valid_pressure = np.isfinite(pressure) & (pressure > 0.0)
    valid_width = np.isfinite(pore_width_angstrom) & (pore_width_angstrom > 0.0)
    if int(np.count_nonzero(valid_pressure)) < 3 or int(np.count_nonzero(valid_width)) < 3:
        return None
    pressure = pressure[valid_pressure]
    matrix = matrix[valid_pressure, :]
    pore_width_angstrom = pore_width_angstrom[valid_width]
    matrix = matrix[:, valid_width]
    pressure, matrix = _sort_and_deduplicate_axis(pressure, matrix, axis=0)
    pore_width_angstrom, matrix = _sort_and_deduplicate_axis(
        pore_width_angstrom,
        matrix,
        axis=1,
    )
    matrix[~np.isfinite(matrix)] = 0.0
    matrix[matrix < 0.0] = 0.0
    return DftModelKernel(
        spec=spec,
        pressures=np.asarray(pressure, dtype=float),
        pore_widths_nm=np.asarray(pore_width_angstrom, dtype=float) * 0.1,
        kernel=np.asarray(matrix, dtype=float),
        gai=metadata,
    )


def _parse_gai_metadata(data: bytes, offset: int, count: int, headers: list[str]) -> GaiMetadata | None:
    """GAI tail used by QNNLS32 2.62; all optional reads are bounds checked."""
    try:
        transitions = np.frombuffer(data, dtype="<f4", count=count, offset=offset).astype(float)
        offset += count * 4
        if not np.all(np.isfinite(transitions)) or np.any(transitions < 0):
            return None
        fields = []
        for kind in ("s", "s", "f", "s", "s", "s", "s", "s", "f", "f",
                     "f", "f", "s", "f", "f", "f", "s", "f", "s", "s"):
            if kind == "s":
                size = struct.unpack_from("<H", data, offset)[0]
                offset += 2
                if offset + size > len(data):
                    raise ValueError("Truncated GAI metadata string")
                fields.append(data[offset:offset + size].decode("latin-1").strip("\x00 "))
                offset += size
            else:
                fields.append(struct.unpack_from("<f", data, offset)[0])
                offset += 4
        divisors = None
        native_geometry_fallback = offset + count * 4 > len(data)
        if offset + count * 4 <= len(data):
            stored = np.frombuffer(data, dtype="<f4", count=count, offset=offset).astype(float)
            if np.all(np.isin(stored, [20000., 40000., 60000.])):
                divisors = stored / 10000.
            # QNNLS32 0x401f75 tests the integer part of the first value.
            # CO2_GCMC has an additional old data block beginning with zero;
            # the native reader ignores it and uses the declared geometry.
            native_geometry_fallback = np.isfinite(stored[0]) and int(stored[0]) == 0
        if divisors is None:
            shape = {"slit": 2., "cyl": 4., "sphere": 6.}.get(str(fields[4]).lower())
            # Never infer mixed geometry from an unknown tail.
            if shape is not None and native_geometry_fallback:
                divisors = np.full(count, shape)
        command = next((value for value in headers if re.search(r"-[A-Za-z]+=", value)), "")
        # Native flags are stored as float32. Keeping decimal R in float64
        # can wrongly exclude the matching kernel row (e.g. Ar QSDFT R=.99).
        options = tuple((key.upper(), float(np.float32(value))) for key, value in re.findall(
            r"-([A-Za-z]+)=([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", command))
        return GaiMetadata(command, options, transitions, divisors,
                           float(fields[2]), str(fields[3]), str(fields[5]))
    except (ValueError, struct.error, IndexError):
        return None


def _detect_model_layout(data: bytes) -> tuple[int, int, int, int] | None:
    """Infer the two grid sizes from the exact Micromeritics file layout.

    Relying on increasing values is insufficient: several official kernels
    contain pressures above 1, a non-monotonic pressure grid, or duplicate
    pressure/width entries.  The stored width count and total matrix size make
    the layout unambiguous without guessing from the numeric values.
    """
    max_rows = min(4096, max(0, (len(data) - _MODEL_HEADER_SIZE - 12) // 8))
    for row_count in range(3, max_rows + 1):
        count_offset = _MODEL_HEADER_SIZE + row_count * 8
        if count_offset + 4 > len(data):
            break
        column_count = struct.unpack_from("<I", data, count_offset)[0]
        if not 3 <= column_count <= 4096:
            continue
        width_offset = count_offset + 4
        matrix_offset = width_offset + column_count * 8 + 8
        expected_size = matrix_offset + row_count * column_count * 8
        if expected_size == len(data):
            return row_count, int(column_count), width_offset, matrix_offset
    return None


def _sort_and_deduplicate_axis(
    values: np.ndarray,
    matrix: np.ndarray,
    *,
    axis: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Sort a kernel axis and keep the last official row/column at duplicates."""
    order = np.argsort(values, kind="stable")
    sorted_values = np.asarray(values[order], dtype=float)
    sorted_matrix = matrix[order, :] if axis == 0 else matrix[:, order]
    keep = np.ones(sorted_values.size, dtype=bool)
    if sorted_values.size > 1:
        keep[:-1] = sorted_values[:-1] != sorted_values[1:]
    if axis == 0:
        sorted_matrix = sorted_matrix[keep, :]
    else:
        sorted_matrix = sorted_matrix[:, keep]
    return sorted_values[keep], np.asarray(sorted_matrix, dtype=float).copy()
