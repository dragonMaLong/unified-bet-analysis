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


@dataclass(frozen=True)
class DftModelKernel:
    spec: DftModelSpec
    pressures: np.ndarray
    pore_widths_nm: np.ndarray
    kernel: np.ndarray


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
        spec.model_id.upper(): spec for spec in DFT_MODEL_SPECS.values()
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
    )


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
