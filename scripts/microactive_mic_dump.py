"""MicroActive Python report: dump the vendor's own isotherm data for the open sample.

Run INSIDE MicroActive only (the ``mic`` module exists only in microactive.exe):
  Report Options -> Python Report -> Add Selected Python Script -> this file,
  then generate the report on a COPY of the sample.
It writes one JSON per run to OUT_DIR and shows the path in a summary table.
It reads data only; it does not change the sample or MicroActive settings.
"""
import json
import os
import time
import traceback

import mic

OUT_DIR = r"D:\software\analysis\Data\BET\Collected_PDF_SMP_XLS\BET解析及软件开发\tristar_bet_app_zh\outputs\microactive_smp_research\mic_dumps"

ISOTHERM_ITEMS = [
    "number adsorption", "absolute pressure", "relative pressure", "saturation pressure",
    "quantity adsorbed", "quantity dosed", "compressibility factor", "elapsed time",
    "free space", "warm free space", "cold free space", "temperature",
]
SAMPLE_ITEMS = [
    "sample mass", "analysis temperature", "sample density", "file name", "sample description",
    "analysis adsorptive", "free space", "warm free space", "cold free space", "operator",
    "instrument", "serial number", "analysis start", "analysis end",
]
REPORTS = [  # (report, item); unknown keys are recorded as errors, not guessed silently
    ("bet", "surface area"), ("bet", "c"), ("bet", "bet constant"), ("bet", "monolayer capacity"),
    ("sp", "surface area"), ("langmuir", "surface area"),
    ("tplot", "micropore volume"), ("tplot", "external surface area"),
]


def plain(value):
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            return [plain(v) for v in value.tolist()]
        if isinstance(value, np.generic):
            return value.item()
    except Exception:
        pass
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return repr(value)


def attempt(fn, *args, **kwargs):
    try:
        return {"ok": True, "value": plain(fn(*args, **kwargs))}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


out = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "mic_dir": [n for n in dir(mic) if not n.startswith("__")]}
docs = {}
for name in ("isotherm", "sample_information", "adsorptive_data", "report", "scan_data"):
    obj = getattr(mic, name, None)
    docs[name] = getattr(obj, "__doc__", None) if obj is not None else None
out["docs"] = docs
out["isotherm_rel"] = attempt(mic.isotherm, "rel")
out["isotherm_abs"] = attempt(mic.isotherm, "abs")
out["isotherm_items"] = {item: attempt(mic.isotherm, item=item) for item in ISOTHERM_ITEMS}
out["sample_information"] = {item: attempt(mic.sample_information, item) for item in SAMPLE_ITEMS}
out["adsorptive_data"] = attempt(mic.adsorptive_data)
out["scan_data"] = attempt(mic.scan_data)
out["reports"] = {"%s|%s" % pair: attempt(mic.report, *pair) for pair in REPORTS}

desc = ""
try:
    desc = str(out["isotherm_rel"]["value"][6])
except Exception:
    pass
safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in desc)[:60] or "sample"
path = os.path.join(OUT_DIR, "%s_%s.json" % (safe, time.strftime("%Y%m%d_%H%M%S")))
status = path
try:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
except Exception:
    status = "WRITE FAILED: " + traceback.format_exc()[-300:]

mic.summary("mic dump")
mic.summary.add("mic dump", ["Description:", "Written to:"], [desc, status])
