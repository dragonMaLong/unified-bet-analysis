"""Explore a JWGBT run's gas-balance residuals under synthetic perturbations.

Usage: python -m scripts.jwgb_free_space_probe FILE --output REPORT.json
Injects synthetic isotherms into the measured balance and reports when the
refitted residual exceeds an exploratory threshold. This is neither an
instrument detection limit nor an upper bound on real adsorption. This command
does not change application import settings.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from tristar_bet.jwgb_balance import balance_residuals, dosed_series, fit_free_space, free_space_diagnostic
from tristar_bet.jwgb_native import load_jwgb_native

N2_AREA_FACTOR = 6.02214076e23 * 0.162e-18 / 22414
WINDOWS = (0.35, 0.90)
BET_C_VALUES = (3, 10, 30, 100, 300)
LANGMUIR_C = 1e4
DETECTION_FACTOR = 3.0


def bet(x, c):
    return c * x / ((1 - x) * (1 + (c - 1) * x))


def langmuir(x, c=LANGMUIR_C):
    return c * x / (1 + c * x)


def detection_limit(pressures, relative, dosed, to_uptake, shape):
    """Smallest monolayer capacity (cm3/g) the fit visibly fails to absorb."""
    baseline = np.abs(balance_residuals(pressures, dosed, fit_free_space(pressures, dosed))).max() * to_uptake
    for capacity in np.geomspace(0.005, 100, 800):
        injected = dosed + capacity * shape(relative) / to_uptake
        residual = balance_residuals(pressures, injected, fit_free_space(pressures, injected))
        if np.abs(residual).max() * to_uptake > DETECTION_FACTOR * baseline:
            return float(capacity)
    return None


def audit(path):
    result = load_jwgb_native(path, reconstruct_uptake=False)
    options = result.method_options
    records = options["jwgb_native_pressure_records"]
    config = ET.fromstring(options["jwgb_native_config_xml"])
    vd, stored_q = float(config.findtext("Vd")), float(config.findtext("QValue"))
    mass = result.sample.sample_mass_g
    if any(value is None or not math.isfinite(value) or value <= 0
           for value in (vd, stored_q, mass)):
        raise ValueError("Positive finite Vd, stored Q and sample mass are required")
    pressures, relative, dosed = dosed_series(records, "adsorption")
    shapes = [(f"BET C={c}", lambda x, c=c: bet(x, c)) for c in BET_C_VALUES]
    shapes.append(("Langmuir micropore", langmuir))
    limits = []
    for high in WINDOWS:
        keep = relative <= high
        for name, shape in shapes:
            capacity = detection_limit(pressures[keep], relative[keep], dosed[keep], vd / mass, shape)
            limits.append({"window_max_p_p0": high, "shape": name, "capacity_limit_cm3_g": capacity,
                           "area_limit_m2_g": None if capacity is None else capacity * N2_AREA_FACTOR})
    return {"file": str(Path(path).resolve()), "sha256": options["jwgb_native_sha256"],
            "diagnostic": free_space_diagnostic(records, vd, stored_q, mass), "detection_limits": limits,
            "limitations": ["未与原厂逐点结果配对验证；小残差不证明没有吸附。",
                            "拟合会吸收吸附量中的线性和二次项，表观 Q 不能代替独立校准。",
                            "注入实验只对所列形状和经验阈值成立，不是仪器检出限、实际误差界限或置信区间。"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.file.resolve():
        parser.error("Output must not overwrite the source file")
    report = audit(args.file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report["diagnostic"], ensure_ascii=False))
    for row in report["detection_limits"]:
        print(row)


if __name__ == "__main__":
    main()
