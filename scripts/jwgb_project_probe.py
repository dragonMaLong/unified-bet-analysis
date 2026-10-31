"""Decode public PAS project files and audit measured Q against stored Q.

Usage: python -m scripts.jwgb_project_probe FILE [FILE ...] --output REPORT.json
With --xml-dir DIR, also save exact decoded XML for independent inspection.
This research command does not enable quantitative import in the app.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from tristar_bet.jwgb_project import read_jwgb_project
from scripts.jwgb_reconstruction_probe import TEMPERATURE_MODELS, finite, volume_terms


def audit_uptake_hypothesis(records, vd, q, mass, bet_low, bet_high, model="separate"):
    """Stress-test the candidate on independent pressure records; never clip.

    A Q calibration match is not uptake validation. In particular, excluding
    negative candidate points could produce a seemingly acceptable fit, so a
    nonpositive selected point invalidates this audit's BET estimate.
    """
    vd, q, mass, bet_low, bet_high = [finite(v, n) for v, n in zip(
        (vd, q, mass, bet_low, bet_high), ("Vd", "Q", "mass", "BET min", "BET max"))]
    if mass <= 0 or not 0 < bet_low < bet_high < 1:
        raise ValueError("Invalid mass or BET interval")
    cumulative = 0.0
    previous = None
    rows = []
    for record in records:
        pd, pcd = finite(record.get("Pd"), "Pd"), finite(record.get("Pcd"), "Pcd")
        if previous is None:
            if pd != -0.00001:
                raise ValueError("Missing adsorption reference")
            previous = pcd
            continue
        p0 = finite(record.get("P0"), "P0")
        if pd < 0 or p0 <= 0 or record.get("IsBalance") != "1":
            raise ValueError("Unsupported measured adsorption record")
        manifold, free = volume_terms(pd, pcd, previous, record.get("PdT"), record.get("PcdT"), vd, q, model)
        cumulative += manifold - free
        quantity = cumulative / mass
        if not math.isfinite(quantity):
            raise ValueError("Candidate volume overflow")
        rows.append({"id": record.get("ID"), "relative_pressure": pcd / p0,
                     "candidate_uptake_cm3_g_stp": quantity})
        previous = pcd
    selected = [row for row in rows if bet_low <= row["relative_pressure"] <= bet_high]
    fit = {"status": "not_enough_points", "surface_area_m2_g": None, "selected_points": len(selected)}
    if any(row["candidate_uptake_cm3_g_stp"] <= 0 for row in selected):
        fit["status"] = "nonpositive_selected_uptake"
    elif len(selected) >= 3:
        x = [row["relative_pressure"] for row in selected]
        y = [p / ((1 - p) * row["candidate_uptake_cm3_g_stp"]) for p, row in zip(x, selected)]
        mx, my = sum(x) / len(x), sum(y) / len(y)
        denominator = sum((p - mx) ** 2 for p in x)
        if denominator <= 0:
            fit["status"] = "degenerate_pressure_range"
        else:
            slope = sum((p - mx) * (v - my) for p, v in zip(x, y)) / denominator
            intercept = my - slope * mx
            fit.update(slope=slope, intercept=intercept)
            if slope + intercept <= 0 or intercept <= 0:
                fit["status"] = "nonphysical_bet_fit"
            else:
                fit["status"] = "ok_unvalidated"
                fit["surface_area_m2_g"] = 1 / (slope + intercept) * (6.02214076e23 * 0.162e-18 / 22414)
    return {"temperature_model": model, "quantitative_validation_passed": False,
            "negative_adsorption_points": sum(row["candidate_uptake_cm3_g_stp"] < 0 for row in rows),
            "minimum_candidate_uptake": min((row["candidate_uptake_cm3_g_stp"] for row in rows), default=None),
            "candidate_bet": fit, "rows": rows}


def audit_q(q_records, stored_q_text):
    """Check a single-dose helium calibration against the stored six decimals.

    This is independent evidence about Q/temperature handling in the observed
    PAS version. It does not validate uptake corrections or BK200C firmware.
    """
    if len(q_records) != 2 or finite(q_records[0].get("Pd"), "reference Pd") != -0.00001:
        raise ValueError("Only the observed reference + one-dose Q calibration is covered")
    reference, dose = q_records
    previous = finite(reference.get("Pcd"), "reference Pcd")
    pcd = finite(dose.get("Pcd"), "Pcd")
    stored = finite(stored_q_text, "stored Q")
    if pcd <= previous or stored <= 0:
        raise ValueError("Q calibration requires a positive pressure change and stored Q")
    # Six decimal places are observed in the actual project field. Do not
    # silently apply that tolerance to another format or a different precision.
    if not isinstance(stored_q_text, str) or len(stored_q_text.split(".")[-1]) != 6:
        raise ValueError("Q comparison requires the observed six-decimal storage format")
    output = {}
    for model in TEMPERATURE_MODELS:
        manifold, _ = volume_terms(dose.get("Pd"), pcd, previous,
                                   dose.get("PdT"), dose.get("PcdT"), 1.0, 0.0, model)
        predicted = manifold / (pcd - previous)
        residual = predicted - stored
        output[model] = {"predicted_q": predicted, "stored_q": stored,
                         "residual": residual,
                         "within_storage_rounding": abs(residual) <= 0.5e-6}
    return output


def inspect_project(path, xml_dir=None):
    path = Path(path)
    document = read_jwgb_project(path)
    root = document.root
    q_records = [dict(row.attrib) for row in root.findall("Data/Q/Item")]
    config_version = root.findtext("Config/Base/Version")
    station_model = root.findtext("Config/Station/Model")
    supported_calibration = (config_version == "10.06.00.72" and station_model == "Micro200B"
                             and root.findtext("Config/Base/PressUnits") == "0"
                             and root.findtext("Config/LabParamEx/AutoTestQ") == "1"
                             and root.findtext("Config/LabParamEx/FastTestQ") == "1")
    audit = audit_q(q_records, root.findtext("Config/LabParamEx/QValue")) if supported_calibration else None
    uptake_audit = None
    if supported_calibration and root.findtext("Config/LabParamBae/AdsorbGas") == "N2":
        uptake_audit = audit_uptake_hypothesis(
            [dict(row.attrib) for row in root.findall("Data/Adsorb/Item")],
            root.findtext("Config/LabParamBae/Vd"), root.findtext("Config/LabParamEx/QValue"),
            root.findtext("Config/SampleInfo/SampleWeight"),
            root.findtext("Config/MultiBETReport/MinRange"), root.findtext("Config/MultiBETReport/MaxRange"))
    counts = {branch: len(root.findall(f"Data/{branch}/Item")) for branch in ("Q", "Adsorb", "Doff")}
    record_times = [row.get("Day", "") + " " + row.get("Time", "")
                    for branch in ("Q", "Adsorb", "Doff") for row in root.findall(f"Data/{branch}/Item")]
    result = {"file": str(path.resolve()), "source_sha256": document.source_sha256,
              "source_bytes": document.source_bytes,
              "decoded_sha256": hashlib.sha256(document.xml).hexdigest(),
              "decoded_bytes": len(document.xml), "encoding": "hex-minus-index-modulo-48",
              "software_version": config_version, "instrument_model": station_model,
              "quantitative_uptake_validated": False,
              "record_counts_including_references": counts, "q_calibration": audit,
              "uptake_hypothesis_audit": uptake_audit,
              "times": {"config_start": root.findtext("Config/Base/StartTime"),
                        "config_end": root.findtext("Config/Base/CompletedTime"),
                        "data_start": root.findtext("Data/BaseInfo/StartTime"),
                        "data_end": root.findtext("Data/BaseInfo/EndTime"),
                        "first_record": min(record_times) if record_times else None,
                        "last_record": max(record_times) if record_times else None},
              "limitations": ["解码只恢复压力、温度、配置和日志；没有得到原厂计算后的吸附量。",
                              "Q 校验针对 Micro200B / PAS 10.06.00.72，不自动外推到 BK200C。",
                              "Data/BaseInfo 时间可能与 Config 和测量记录不一致，必须保留来源差异。"]}
    if xml_dir is not None:
        target = Path(xml_dir) / (path.name + ".xml")
        if target.resolve() == path.resolve():
            raise ValueError("Cannot overwrite the source project")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(document.xml)
        result["decoded_file"] = str(target.resolve())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--xml-dir", type=Path)
    args = parser.parse_args()
    if args.output.resolve() in {path.resolve() for path in args.files}:
        parser.error("Output must not overwrite a source file")
    reports = [inspect_project(path, args.xml_dir) for path in args.files]
    audited = [report for report in reports if report["q_calibration"] is not None]
    summary = {"sources": {"paper_doi": "10.1039/D4RA05672H",
                            "public_share": "https://cloud.uni-jena.de/s/8stWzTWfXeYY37d"},
               "calibrations_audited": len(audited),
               "matches_by_temperature_model": {
                   model: sum(report["q_calibration"][model]["within_storage_rounding"] for report in audited)
                   for model in TEMPERATURE_MODELS},
               "projects": reports}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "projects"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
