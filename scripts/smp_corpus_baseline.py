"""Run the independent SMP parser over a corpus of SMP files (read-only).

Usage: python -m scripts.smp_corpus_baseline <dir-or-file> [...] [--output out.json]
Records per file: SHA256, header magic/version, directory marker, subset ids,
analysis-type hints, parse outcome (points / exception class + message).
Nothing is written next to the input files.
"""
from __future__ import annotations
import argparse, hashlib, json, re, struct, sys, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tristar_bet.smp import load_smp  # noqa: E402

HINT_PATTERNS = {
    "physisorption": [rb"Isotherm", rb"BET Surface Area", rb"P/Po"],
    "chemisorption": [rb"Chemisorption", rb"Metal Dispersion", rb"Active Metal"],
    "dynamic_chemi": [rb"TPR", rb"TPD", rb"Pulse"],
    "mercury": [rb"Mercury", rb"Penetrometer", rb"Intrusion"],
    "pycnometry": [rb"Pycnometer", rb"Skeletal"],
}


def structure(data: bytes) -> dict:
    out: dict = {"size": len(data)}
    out["magic_at_2"] = data[2:11].decode("latin1", "replace")
    out["ver_12_16"] = data[12:16].decode("latin1", "replace")
    if len(data) >= 32:
        doff, dsize = struct.unpack_from("<II", data, 24)
        out["dir_offset"], out["dir_size"] = doff, dsize
        out["dir_marker"] = data[doff:doff + 12].decode("latin1", "replace") if doff < len(data) else None
    markers = sorted({int(m.group(1)) for m in re.finditer(rb"SUBSET(\d{3,4})", data)})
    out["subset_markers"] = markers
    out["hints"] = {k: sum(data.count(p) for p in pats) for k, pats in HINT_PATTERNS.items()}
    return out


def run(path: Path) -> dict:
    data = path.read_bytes()
    rec = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), **structure(data)}
    try:
        r = load_smp(path)
        iso = r.isotherm
        rec["status"] = "ok" if iso else "no_points"
        rec["points"] = len(iso)
        rec["ads"] = sum(1 for p in iso if getattr(p, "branch", "") != "desorption")
        rec["mass_g"] = r.sample.sample_mass_g
        rec["model"] = r.method_options.get("instrument_model")
        rec["format_family"] = r.method_options.get("format_family")
        rec["adsorptive"] = r.adsorptive_properties.mnemonic if r.adsorptive_properties else None
        rec["parsed_subsets"] = [s.subset_id for s in r.subsets]
    except Exception as exc:  # record, don't hide
        rec["status"] = "error"
        rec["error_type"] = type(exc).__name__
        rec["error"] = str(exc)[:300]
        rec["trace_tail"] = traceback.format_exc().strip().splitlines()[-3:]
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--output", type=Path)
    a = ap.parse_args()
    files: list[Path] = []
    for p in a.inputs:
        files += [p] if p.is_file() else sorted(q for q in p.rglob("*") if q.suffix.lower() == ".smp")
    seen, rows = set(), []
    for f in files:
        rec = run(f)
        rec["duplicate_of_earlier"] = rec["sha256"] in seen
        seen.add(rec["sha256"])
        rows.append(rec)
        print(f"{rec['status']:9} {rec.get('points', ''):>4} {rec.get('model') or rec.get('error_type', '')!s:28} {f.name}")
    if a.output:
        a.output.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
