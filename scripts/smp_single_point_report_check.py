"""Compare software single-point BET with Micromeritics XLS reports in a folder.

Usage: python -m scripts.smp_single_point_report_check "<folder>" --output report.json
Pairs NAME.SMP with NAME.xls/.XLS, reads "Single point surface area at P/Po = x: A m²/g",
and checks sample name and mass first so unrelated files are flagged
as unpaired rather than counted as algorithm failures. Read-only on the folder.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from tristar_bet import load_file
from tristar_bet.analysis import single_point_bet_analysis
from tristar_bet.ui.main_window import single_point_target

PATTERN = re.compile(r"Single point surface area at P/Po = ([0-9.]+)", re.I)


def report_values(path: Path) -> dict:
    import xlrd
    book = xlrd.open_workbook(str(path))
    found = {}
    for sheet in book.sheets():
        for r in range(sheet.nrows):
            for c in range(sheet.ncols):
                text = str(sheet.cell_value(r, c)).strip()
                nxt = str(sheet.cell_value(r, c + 1)) if c + 1 < sheet.ncols else ""
                match = PATTERN.search(text)
                if match and "single_point" not in found:
                    found["single_point"] = (float(match.group(1)), float(re.sub(r"[^0-9.\-]", "", nxt)))
                if text == "Sample:" and "sample" not in found:
                    found["sample"] = nxt.strip()
                if text == "Sample Mass:" and "mass" not in found:
                    found["mass"] = float(re.sub(r"[^0-9.\-].*", "", nxt.strip()) or "nan")
        break
    return found


def check(folder: Path) -> list[dict]:
    rows = []
    for smp in sorted(folder.glob("*.SMP")):
        xls = next((p for p in (smp.with_suffix(".xls"), smp.with_suffix(".XLS")) if p.exists()), None)
        if xls is None:
            continue
        entry = {"smp": smp.name, "xls": xls.name}
        try:
            report = report_values(xls)
            result = load_file(smp)
        except Exception as exc:
            rows.append({**entry, "status": "read_error", "error": str(exc)})
            continue
        if "single_point" not in report:
            rows.append({**entry, "status": "no_single_point_in_report"})
            continue
        mass = result.sample.sample_mass_g
        paired = (report.get("sample") == result.sample_name and mass is not None
                  and abs(mass - report.get("mass", float("nan"))) < 5e-5)
        target, source = single_point_target(result, None)
        fit = single_point_bet_analysis(result, target) if target is not None else None
        reported_x, reported_area = report["single_point"]
        area = fit.surface_area_m2_g if fit else None
        status = ("unpaired" if not paired else "no_file_target" if target is None
                  else "match" if area is not None and abs(area - reported_area) <= 5e-5 * max(1, abs(reported_area))
                  else "differs")
        rows.append({**entry, "status": status, "model": result.method_options.get("instrument_model"),
                     "file_target": target if source == "文件设置" else None,
                     "reported_pressure": reported_x, "reported_area": reported_area,
                     "used_pressure": fit.pressure_min if fit else None, "software_area": area})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = check(args.folder)
    summary = {}
    for row in rows:
        key = f"{row.get('model')}:{row['status']}"
        summary[key] = summary.get(key, 0) + 1
    if args.output:
        args.output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
