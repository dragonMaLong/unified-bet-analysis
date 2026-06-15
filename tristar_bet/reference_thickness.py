from __future__ import annotations

from functools import lru_cache
from pathlib import Path


DEFAULT_REFERENCE_DIR = Path(r"C:\Users\Public\Documents\Micromeritics\TriStar II Plus\referenc")
DEFAULT_REFERENCE_FILE = DEFAULT_REFERENCE_DIR / "ref.thk"


def normalize_reference_points(points) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    for point in points or []:
        try:
            pressure, thickness = point
            pressure = float(pressure)
            thickness = float(thickness)
        except (TypeError, ValueError):
            continue
        normalized.append((pressure, thickness))
    normalized.sort(key=lambda item: item[0])
    return normalized


def default_reference_params() -> dict[str, object]:
    path = DEFAULT_REFERENCE_FILE
    points = default_reference_points()
    return {
        "reference_name": path.name,
        "reference_path": str(path),
        "reference_points": points,
    }


@lru_cache(maxsize=1)
def default_reference_points() -> tuple[tuple[float, float], ...]:
    try:
        return tuple(read_reference_points(DEFAULT_REFERENCE_FILE))
    except OSError:
        return ()


def read_reference_points(path: str | Path) -> list[tuple[float, float]]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    points: list[tuple[float, float]] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.replace(",", " ").split()
        if len(parts) < 2:
            continue
        try:
            pressure = float(parts[0])
            thickness = float(parts[1])
        except ValueError:
            continue
        points.append((pressure, thickness))
    return points


def write_reference_points(path: str | Path, points) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{float(pressure):.9g}\t{float(thickness):.6g}"
        for pressure, thickness in normalize_reference_points(points)
    ]
    path.write_text("\r\n".join(lines) + ("\r\n" if lines else ""), encoding="utf-8")


def reference_thickness_nm(relative_pressure: float, params: dict[str, object] | None = None) -> float | None:
    try:
        pressure = float(relative_pressure)
    except (TypeError, ValueError):
        return None
    if pressure <= 0.0:
        return None
    points = normalize_reference_points((params or {}).get("reference_points"))
    if not points:
        points = list(default_reference_points())
    if not points:
        return None
    if len(points) == 1:
        return points[0][1]

    for index, (left_pressure, left_thickness) in enumerate(points[:-1]):
        right_pressure, right_thickness = points[index + 1]
        if left_pressure <= pressure <= right_pressure or right_pressure <= pressure <= left_pressure:
            return _linear_interpolate(pressure, left_pressure, left_thickness, right_pressure, right_thickness)

    if pressure < points[0][0]:
        left_pressure, left_thickness = points[0]
        right_pressure, right_thickness = points[1]
        return _linear_interpolate(pressure, left_pressure, left_thickness, right_pressure, right_thickness)

    left_pressure, left_thickness = points[-2]
    right_pressure, right_thickness = points[-1]
    return _linear_interpolate(pressure, left_pressure, left_thickness, right_pressure, right_thickness)


def _linear_interpolate(
    pressure: float,
    left_pressure: float,
    left_thickness: float,
    right_pressure: float,
    right_thickness: float,
) -> float | None:
    span = right_pressure - left_pressure
    if span == 0.0:
        return left_thickness
    fraction = (pressure - left_pressure) / span
    thickness = left_thickness + fraction * (right_thickness - left_thickness)
    return thickness if thickness > 0.0 else None
