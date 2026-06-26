from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
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
}


def dft_model_options() -> list[tuple[str, str]]:
    return [(spec.key, spec.label) for spec in DFT_MODEL_SPECS.values()]


def dft_model_label(key: str) -> str:
    spec = DFT_MODEL_SPECS.get(str(key))
    return spec.label if spec is not None else str(key)


@lru_cache(maxsize=16)
def load_dft_model_kernel(key: str) -> DftModelKernel | None:
    spec = DFT_MODEL_SPECS.get(str(key))
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


def _parse_model_file(spec: DftModelSpec, data: bytes) -> DftModelKernel | None:
    pressure, pressure_end = _read_increasing_doubles(data, 239, min_value=0.0, max_value=1.0)
    if pressure.size < 3:
        return None
    pore_width_angstrom, width_end = _read_increasing_doubles(
        data,
        pressure_end + 4,
        min_value=0.0,
        max_value=10000.0,
    )
    if pore_width_angstrom.size < 3:
        return None

    row_count = int(pressure.size)
    column_count = int(pore_width_angstrom.size)
    matrix_count = row_count * column_count
    # Micromeritics model files place an 8-byte sentinel before the matrix for
    # the model families currently exposed in the UI.
    matrix_offset = width_end + 8
    if matrix_offset + matrix_count * 8 > len(data):
        matrix_offset = width_end
    if matrix_offset + matrix_count * 8 > len(data):
        return None
    matrix = np.frombuffer(data, dtype="<f8", count=matrix_count, offset=matrix_offset).copy()
    matrix = matrix.reshape(row_count, column_count)
    matrix[~np.isfinite(matrix)] = 0.0
    matrix[matrix < 0.0] = 0.0
    return DftModelKernel(
        spec=spec,
        pressures=np.asarray(pressure, dtype=float),
        pore_widths_nm=np.asarray(pore_width_angstrom, dtype=float) * 0.1,
        kernel=matrix,
    )


def _read_increasing_doubles(
    data: bytes,
    offset: int,
    *,
    min_value: float,
    max_value: float,
) -> tuple[np.ndarray, int]:
    values: list[float] = []
    last = -math.inf
    cursor = int(offset)
    while cursor + 8 <= len(data):
        value = struct.unpack_from("<d", data, cursor)[0]
        if not (math.isfinite(value) and min_value < value < max_value):
            break
        if values and value <= last:
            break
        values.append(float(value))
        last = float(value)
        cursor += 8
    return np.asarray(values, dtype=float), cursor
