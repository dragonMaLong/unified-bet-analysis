"""Explicit research probe, NOT a validated JWGBT quantitative importer.

Run from the repository: python -m scripts.jwgb_reconstruction_probe FILE --output REPORT.json
The app labels supported BK200C uptake as an unvalidated reconstruction.
This independent tool retains four temperature hypotheses and their assumptions for future
comparison with a SAME-RUN vendor export. A plausible fit is not validation.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from tristar_bet.analysis import bet_analysis
from tristar_bet.jwgb_native import load_jwgb_native
from scripts.jwgb_paired_probe import (closure_hypothesis, bet_hypothesis, bet_diagnostics,
                                     reconstruct as reconstruct_paired_rule)


TEMPERATURE_MODELS = ("separate", "before", "after", "mean")
STP_RATIO = 2.69578  # Literal in the vendor manual's calibration equation.
SOURCES = [
    "https://www.instrument.com.cn/netshow/SH100710/news_887572.htm",
    "https://www.scribd.com/document/629104312/112-Manual-Specific-Surface-and-PoreSiz-Analyzer",
    "https://doi.org/10.1039/D4RA05672H",
    "https://cloud.uni-jena.de/s/8stWzTWfXeYY37d",
]
ASSUMPTIONS = [
    "研究候选值，未与同一次测试的原厂 RAW/XLSX 逐点验证，不是原厂测量结果。",
    "由旧版手册的 Q 差值校准式推断 Vtc=Q*Vd，并假定 Q 没有额外零点偏移；尚未验证 BK200C 版本。",
    "PdT/PcdT 假定为摄氏温度；同时比较四种温度取法，不自动判定哪一种是原厂实现。",
    "新增证据：5 个公开 Micro200B/PAS 10.06.00.72 项目的 Q 校准均支持分时温度公式；尚不能证明 BK200C 00.01.05.00 的吸附量算法相同。",
    "未实现非理想气体、液位、样品占据体积或其他未公开补偿；IsRealGas=0 不足以证明所有补偿均关闭。",
    "初始参考状态的吸附量假定为零；脱附沿用吸附终点的累计量，不重新归零。",
    "BET 使用现有分析器与文件中的拟合区间，仅用于候选模型比较；拟合优度不证明重建正确。",
    "2026-10-31：另一组 Sync440A 原始/导出配对已复现 50 个点；其非理想修正和闭合规则单列为迁移候选，未由 BK200C 配对验证。",
]


def finite(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def volume_terms(pd, pcd, previous_pcd, pd_t, pcd_t, vd, q, model):
    """Return signed STP volume terms; uptake change = manifold - free space.

    Pressures: kPa; temperatures: Celsius; Vd: cm3. Q's units/mapping are
    a hypothesis, not established metadata. Desorption uses the same signed
    conservation equation, so a reversible cycle has zero net uptake change.
    """
    pd, pcd, previous_pcd, pd_t, pcd_t, vd, q = [
        finite(value, name) for value, name in zip(
            (pd, pcd, previous_pcd, pd_t, pcd_t, vd, q),
            ("Pd", "Pcd", "previous Pcd", "PdT", "PcdT", "Vd", "Q"))
    ]
    if min(pd, pcd, previous_pcd, q) < 0 or vd <= 0:
        raise ValueError("Pressure/Q must be nonnegative and Vd positive")
    before, after = pd_t + 273.15, pcd_t + 273.15
    if min(before, after) <= 0:
        raise ValueError("Temperature must be above absolute zero")
    if model == "separate":
        gas_term = pd / before - pcd / after
    elif model in ("before", "after", "mean"):
        temperature = {"before": before, "after": after, "mean": (before + after) / 2}[model]
        gas_term = (pd - pcd) / temperature
    else:
        raise ValueError(f"Unknown temperature model: {model}")
    return STP_RATIO * vd * gas_term, vd * q * (pcd - previous_pcd)


def probe(path):
    result = load_jwgb_native(path, reconstruct_uptake=False)
    config = ET.fromstring(result.method_options["jwgb_native_config_xml"])
    general = ET.fromstring(result.method_options["jwgb_native_general_xml"])
    if result.method_options["instrument_model"] != "BK200C":
        raise ValueError("This research probe only covers the observed BK200C layout")
    expected = [(general, "GeneralConfig/IsRealGas", "0"),
                (general, "GeneralConfig/IsInsitu", "0"),
                (general, "GeneralConfig/AdsorbGas", "N2"),
                (config, "AutoTestQ", "0"), (config, "FastTestQ", "0"),
                (config, "IsMicroproe", "0")]
    for section, name, value in expected:
        if section.findtext(name) != value:
            raise ValueError(f"Unsupported research configuration: {name} must be {value}")
    if finite(general.findtext("GeneralConfig/CrossSectionalArea"), "cross section") != 0.162:
        raise ValueError("Candidate BET comparison assumes N2 cross section 0.162 nm2")
    vd, q = finite(config.findtext("Vd"), "Vd"), finite(config.findtext("QValue"), "QValue")
    low = finite(config.findtext("MultiBETReport/MinRange"), "BET lower bound")
    high = finite(config.findtext("MultiBETReport/MaxRange"), "BET upper bound")
    if not 0 < low < high < 1:
        raise ValueError("Invalid native BET range")
    mass = result.sample.sample_mass_g
    records = result.method_options["jwgb_native_pressure_records"]
    candidates = []
    for model in TEMPERATURE_MODELS:
        total, previous, phase = 0.0, None, None
        rows, points, warnings = [], [], []
        last_time = None
        for record in records:
            current_phase = record["phase"]
            pd, pcd = finite(record["Pd"], "Pd"), finite(record["Pcd"], "Pcd")
            when = record["Day"] + " " + record["Time"]
            if last_time is not None and when < last_time:
                raise ValueError("Pressure records are not chronological")
            last_time = when
            if phase != current_phase:
                if pd != -0.00001:
                    raise ValueError("Each branch must start with its reference record")
                if phase is None and current_phase != "adsorption":
                    raise ValueError("Cannot reconstruct desorption without adsorption")
                if previous is not None and not math.isclose(pcd, previous, abs_tol=1e-6, rel_tol=0):
                    raise ValueError("Desorption reference differs from adsorption endpoint")
                phase, previous = current_phase, pcd
                continue
            if pd < 0:
                raise ValueError("Unexpected reference inside measured branch")
            if record.get("Balance") != "1" or record.get("SubIndex") != "0":
                raise ValueError("Only balanced records with SubIndex=0 are covered")
            manifold, free = volume_terms(pd, pcd, previous, record.get("PdT"),
                                          record.get("PcdT"), vd, q, model)
            total += manifold - free
            if not math.isfinite(total):
                raise ValueError("Candidate volume overflow")
            source_point = result.isotherm[len(points)]
            if (source_point.phase, source_point.index) != (phase, int(record["ID"])):
                raise ValueError("Native point mapping mismatch")
            value = total / mass
            points.append(replace(source_point, raw_internal_cm3_stp=total,
                                  quantity_adsorbed_cm3_g_stp=value,
                                  quantity_adsorbed_mmol_g=value / 22.414))
            rows.append({"phase": phase, "id": source_point.index,
                         "relative_pressure": source_point.relative_pressure,
                         "manifold_delta_cm3_stp": manifold,
                         "free_space_delta_cm3_stp": free,
                         "candidate_uptake_cm3_g_stp": value})
            if value < 0:
                warnings.append(f"Negative candidate uptake: {phase}/{source_point.index}; retained without clipping")
            previous = pcd
        candidate = replace(result, isotherm=points, method_options={
            **result.method_options, 'quantity_status': 'reconstructed_unvalidated',
            'jwgb_reconstructed': True,
        })
        fit = bet_analysis(candidate, p_min=low, p_max=high)
        candidates.append({"temperature_model": model, "rows": rows, "warnings": warnings,
                           "candidate_bet_m2_g": fit.surface_area_m2_g if fit.ok else None})
    transfer = {"status": "not_evaluated_missing_nonideality_factor"}
    factor_text = general.findtext('GeneralConfig/NonIdealityFactor')
    if factor_text is not None:
        # Preserve the old four controls. This is a separately labelled
        # cross-model experiment, never a silent replacement for measured data.
        experiment = {'vd_cm3': vd, 'mass_g': mass, 'stored_q': q,
                      'nonideality_factor': finite(factor_text, 'NonIdealityFactor'),
                      'records': records}
        transferred = reconstruct_paired_rule(experiment, rounded_q=True)
        transfer = {
            'status': 'unvalidated_cross_model_transfer',
            'source_model': 'Sync440A', 'target_model': 'BK200C',
            'quantitative_validation_passed': False,
            'same_run_reference_available': False,
            'calibration_source': 'manual QValue used unchanged; no helium calibration used by this transfer',
            'raw_balance': transferred,
        }
        ids = [r['measurement'] for r in transferred['rows']
               if r['phase'] == 'adsorption' and low <= r['relative_pressure'] <= high]
        try:
            fit = bet_hypothesis(transferred['rows'], ids)
            fit['selection_source'] = 'inclusive native pressure bounds; vendor boundary behavior unverified'
            fit['surface_area_m2_g_app_constants'] = fit['vm_cm3_g'] * (6.02214076e23 * .162e-18 / 22414)
            fit['diagnostics'] = bet_diagnostics(transferred['rows'], fit)
            transfer['bet_candidate'] = fit
        except ValueError as exc:
            transfer['bet_candidate'] = {'status': 'unsupported', 'reason': str(exc)}
        try:
            transfer['closure_hypothesis'] = closure_hypothesis(transferred['rows'])
        except ValueError as exc:
            transfer['closure_hypothesis'] = {'status': 'unsupported', 'reason': str(exc)}
    return {"status": "unvalidated_research_only", "source_file": str(Path(path).resolve()),
            "source_sha256": result.method_options["jwgb_native_sha256"],
            "assumptions": ASSUMPTIONS, "formula_sources": SOURCES,
            "parameters": {"Vd_cm3": vd, "Q": q, "mass_g": mass,
                           "STP_ratio": STP_RATIO, "bet_range": [low, high]},
            "candidates": candidates, "sync440_transfer": transfer}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.file.resolve():
        parser.error("Output must not overwrite the source file")
    report = probe(args.file)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Unvalidated research report: {args.output}")
    print("Research only: this command does not change application import settings.")


if __name__ == "__main__":
    main()
