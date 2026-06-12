from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_FORMAT_DIR = Path(r"D:\software\analysis\Data\BET\TriStar_II_3020_format_analysis")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate minimal parser export against existing XLS-derived CSV files")
    parser.add_argument("--format-dir", default=str(DEFAULT_FORMAT_DIR))
    parser.add_argument("--prefix", default="tristar3020_minimal_parser")
    args = parser.parse_args(argv)

    format_dir = Path(args.format_dir)
    isotherm_csv = format_dir / f"{args.prefix}_isotherm.csv"
    xls_csv = format_dir / "tristar3020_xls_isotherm.csv"
    validation_csv = format_dir / f"{args.prefix}_validation_vs_xls.csv"
    summary_csv = format_dir / f"{args.prefix}_validation_summary.csv"

    rows = build_validation_rows(isotherm_csv, xls_csv)
    write_csv(validation_csv, rows)
    write_csv(summary_csv, build_summary_rows(rows))

    print(validation_csv)
    print(summary_csv)
    return 0


def build_validation_rows(isotherm_csv: Path, xls_csv: Path) -> list[dict[str, object]]:
    parsed = {}
    with isotherm_csv.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            parsed[(row["file"], int(row["point_index"]))] = row

    rows: list[dict[str, object]] = []
    with xls_csv.open(encoding="utf-8-sig") as handle:
        for xls_row in csv.DictReader(handle):
            smp_file = xls_row["file"].replace(".xls", ".SMP")
            point_index = int(xls_row["row"]) - 27
            parsed_row = parsed.get((smp_file, point_index))
            if parsed_row is None:
                rows.append(
                    {
                        "file": smp_file,
                        "xls_row": xls_row["row"],
                        "point_index": point_index,
                        "status": "missing_smp_or_not_parsed",
                    }
                )
                continue

            rows.append(
                {
                    "file": smp_file,
                    "xls_row": xls_row["row"],
                    "point_index": point_index,
                    "status": "ok",
                    "phase": parsed_row["phase"],
                    "xls_elapsed_time": xls_row["elapsed_time"],
                    "parsed_elapsed_time": parsed_row["elapsed_time"],
                    "elapsed_match": xls_row["elapsed_time"] == parsed_row["elapsed_time"],
                    "xls_relative_pressure": xls_row["relative_pressure"],
                    "parsed_relative_pressure": parsed_row["relative_pressure"],
                    "relative_pressure_diff": diff(xls_row["relative_pressure"], parsed_row["relative_pressure"]),
                    "xls_absolute_pressure_mmHg": xls_row["absolute_pressure_mmHg"],
                    "parsed_absolute_pressure_mmHg": parsed_row["absolute_pressure_mmHg"],
                    "absolute_pressure_diff": diff(xls_row["absolute_pressure_mmHg"], parsed_row["absolute_pressure_mmHg"]),
                    "xls_quantity_cm3_g_stp": xls_row["quantity_adsorbed_cm3_g_STP"],
                    "parsed_quantity_cm3_g_stp": parsed_row["quantity_adsorbed_cm3_g_stp"],
                    "quantity_diff_cm3_g_stp": diff(xls_row["quantity_adsorbed_cm3_g_STP"], parsed_row["quantity_adsorbed_cm3_g_stp"]),
                    "xls_saturation_pressure_mmHg": xls_row["saturation_pressure_mmHg"],
                    "parsed_saturation_pressure_mmHg": parsed_row["saturation_pressure_mmHg"],
                    "saturation_pressure_diff": diff(xls_row["saturation_pressure_mmHg"], parsed_row["saturation_pressure_mmHg"]),
                }
            )
    return rows


def build_summary_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    summary_rows: list[dict[str, object]] = []
    for column in (
        "relative_pressure_diff",
        "absolute_pressure_diff",
        "quantity_diff_cm3_g_stp",
        "saturation_pressure_diff",
    ):
        values = [float(row[column]) for row in rows if row.get(column) not in (None, "")]
        if values:
            summary_rows.append(
                {
                    "metric": column,
                    "n": len(values),
                    "mae": sum(abs(value) for value in values) / len(values),
                    "maxabs": max(abs(value) for value in values),
                }
            )
    elapsed_compared = [row for row in rows if row.get("status") == "ok" and row.get("xls_elapsed_time")]
    elapsed_mismatches = [row for row in elapsed_compared if row.get("elapsed_match") is not True]
    summary_rows.append(
        {
            "metric": "elapsed_time_mismatches",
            "n": len(elapsed_compared),
            "mae": "",
            "maxabs": len(elapsed_mismatches),
        }
    )
    return summary_rows


def diff(left: str, right: str) -> str:
    if left == "" or right == "":
        return ""
    return str(float(right) - float(left))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
