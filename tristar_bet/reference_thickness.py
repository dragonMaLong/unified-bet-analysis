from __future__ import annotations

from functools import lru_cache
from pathlib import Path


DEFAULT_REFERENCE_DIR = Path(r"C:\Users\Public\Documents\Micromeritics\TriStar II Plus\referenc")
DEFAULT_REFERENCE_FILE = DEFAULT_REFERENCE_DIR / "ref.thk"
ANGSTROM_REFERENCE_FILES = {
    "sio2oh.thk",
}


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
    thickness_scale = _reference_thickness_scale(path, points)
    if thickness_scale != 1.0:
        points = [(pressure, thickness * thickness_scale) for pressure, thickness in points]
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


def _akima_interpolate(pressure: float, points: list[tuple[float, float]]) -> float | None:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    if any(xs[index] <= xs[index - 1] for index in range(1, len(xs))):
        return None

    interval = None
    for index in range(len(xs) - 1):
        if xs[index] <= pressure <= xs[index + 1]:
            interval = index
            break
    if interval is None:
        return None

    slopes = [
        (ys[index + 1] - ys[index]) / (xs[index + 1] - xs[index])
        for index in range(len(xs) - 1)
    ]
    extended = [0.0] * (len(xs) + 3)
    extended[2:-2] = slopes
    extended[1] = 2.0 * extended[2] - extended[3]
    extended[0] = 2.0 * extended[1] - extended[2]
    extended[-2] = 2.0 * extended[-3] - extended[-4]
    extended[-1] = 2.0 * extended[-2] - extended[-3]

    derivatives = []
    deltas = [abs(extended[index + 1] - extended[index]) for index in range(len(extended) - 1)]
    max_weight = 0.0
    weights: list[tuple[float, float]] = []
    for index in range(len(xs)):
        left_weight = deltas[index + 2]
        right_weight = deltas[index]
        weights.append((left_weight, right_weight))
        max_weight = max(max_weight, left_weight + right_weight)

    threshold = 1e-9 * max_weight
    for index, (left_weight, right_weight) in enumerate(weights):
        total_weight = left_weight + right_weight
        if total_weight > threshold:
            derivatives.append(
                (left_weight * extended[index + 1] + right_weight * extended[index + 2]) / total_weight
            )
        else:
            derivatives.append(0.5 * (extended[index + 3] + extended[index]))

    x0 = xs[interval]
    x1 = xs[interval + 1]
    y0 = ys[interval]
    y1 = ys[interval + 1]
    span = x1 - x0
    if span == 0.0:
        return y0 if y0 > 0.0 else None
    ratio = (pressure - x0) / span
    ratio2 = ratio * ratio
    ratio3 = ratio2 * ratio
    thickness = (
        (2.0 * ratio3 - 3.0 * ratio2 + 1.0) * y0
        + (ratio3 - 2.0 * ratio2 + ratio) * span * derivatives[interval]
        + (-2.0 * ratio3 + 3.0 * ratio2) * y1
        + (ratio3 - ratio2) * span * derivatives[interval + 1]
    )
    return thickness if thickness > 0.0 else None


def _reference_thickness_scale(path: Path, points: list[tuple[float, float]]) -> float:
    """Return the file-to-nm scale for known vendor reference tables."""
    if path.name.lower() in ANGSTROM_REFERENCE_FILES and max((thickness for _pressure, thickness in points), default=0.0) > 5.0:
        return 0.1
    return 1.0
