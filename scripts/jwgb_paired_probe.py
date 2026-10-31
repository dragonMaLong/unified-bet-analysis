"""Reproduce and audit a JWGBT / AIF-parser JSON same-run pair.

Research only: this is not called by the application importer. The public
Sync440A pair matches to export precision with the inferred closure rule.
This is a discovery-set reproduction, not independent validation or evidence
of transfer to BK200C / Micro200B. Raw balance and postprocessing stay separate.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from datetime import datetime
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from tristar_bet.jwgb_native import load_jwgb_native

from tristar_bet.jwgb_reconstruction import STP_RATIO, number, calibrated_q, reconstruct
EXPORT_HALF_UNIT = 0.000005  # Source table reports cm3 STP/g to five decimals.


def load_experiment(path):
    # Reuse the production size, XML, unit, time, ID and pressure checks.
    result = load_jwgb_native(path, reconstruct_uptake=False)
    root = ET.parse(path).getroot()
    config = root.find("Config")
    if result.method_options["instrument_model"] != "Sync440A":
        raise ValueError("Paired probe only covers the observed Sync440A layout")
    if config.findtext("AutoTestQ") != "1" or config.findtext("FastTestQ") != "0":
        raise ValueError("Paired probe requires the observed non-fast automatic Q calibration")
    if root.findtext("General/GeneralConfig/AdsorbGas") != "N2":
        raise ValueError("Paired probe only covers N2")
    return {
        "source_sha256": result.method_options["jwgb_native_sha256"],
        "model": result.method_options["instrument_model"],
        "sample": root.findtext("Config/SampleInfo/SampleName"),
        "material": root.findtext("Config/SampleInfo/SampleNum"),
        "start": result.method_options["test_started_time"],
        "end": result.method_options["test_completed_time"],
        "mass_g": result.sample.sample_mass_g,
        "vd_cm3": number(config.findtext("Vd")),
        "stored_q": number(config.findtext("QValue")),
        "nonideality_factor": number(root.findtext("General/GeneralConfig/NonIdealityFactor")),
        "q_records": [dict(n.attrib) for n in root.findall("Data/Q/Item")],
        "records": result.method_options["jwgb_native_pressure_records"],
    }


def closure_hypothesis(rows, threshold=0.8):
    """Inferred Sync440A export postprocessing, using ONLY native-derived rows.

    Linearly interpolate adsorption at the final desorption relative pressure.
    Below the candidate threshold, distribute the endpoint gap by equilibrium
    point ordinal and floor the result at the interpolated adsorption branch.
    The pair only bounds the threshold to (0.79485145, 0.84035697]; 0.8 is an
    explicit hypothesis, not a recovered setting. Negative endpoint gaps and
    incomplete/out-of-range branches have not been demonstrated by this pair.
    """
    threshold = number(threshold)
    if not 0 < threshold < 1:
        raise ValueError("Closure threshold must be between zero and one")
    ads = [r for r in rows if r['phase'] == 'adsorption']
    des = [r for r in rows if r['phase'] == 'desorption']
    if len(ads) < 2 or len(des) < 2:
        raise ValueError("Closure requires both complete branches")
    x = [number(r['relative_pressure']) for r in ads]
    y = [number(r['candidate_cm3_g']) for r in ads]
    if any(b <= a for a, b in zip(x, x[1:])):
        raise ValueError("Adsorption pressures must strictly increase")

    def interpolate(value):
        value = number(value)
        if not x[0] <= value <= x[-1]:
            raise ValueError("No extrapolation outside adsorption pressure range")
        i = bisect_left(x, value)
        if i == 0 or x[i] == value:
            return y[i]
        return y[i - 1] + (y[i] - y[i - 1]) * (value - x[i - 1]) / (x[i] - x[i - 1])

    dx = [number(r['relative_pressure']) for r in des]
    if any(b >= a for a, b in zip(dx, dx[1:])):
        raise ValueError("Desorption pressures must strictly decrease")
    if abs(dx[0] - x[-1]) > 1e-10 or abs(number(des[0]['candidate_cm3_g']) - y[-1]) > 1e-10:
        raise ValueError("Closure requires a duplicate peak reference")
    gap = interpolate(dx[-1]) - number(des[-1]['candidate_cm3_g'])
    if gap < 0:
        raise ValueError("Negative closure gap is not covered by the observed pair")
    output, ordinal = [], 0
    for row in rows:
        raw = number(row['candidate_cm3_g'])
        corrected, shift, floor, floor_applied = raw, 0.0, None, False
        if row['phase'] == 'desorption':
            floor = interpolate(row['relative_pressure'])
            if row['relative_pressure'] < threshold:
                shift = gap * ordinal / (len(des) - 1)
                floor_applied = raw + shift < floor
                corrected = max(raw + shift, floor)
            ordinal += 1
        output.append({
            **row, 'closure_candidate_cm3_g': corrected,
            'closure_shift_cm3_g': shift, 'adsorption_interpolation_cm3_g': floor,
            'adsorption_floor_applied': floor_applied,
        })
    return {'threshold_hypothesis': threshold, 'endpoint_gap_cm3_g': gap,
            'point_ordinal_step_cm3_g': gap / (len(des) - 1),
            'floor_point_count': sum(r['adsorption_floor_applied'] for r in output),
            'rows': output}


def bet_hypothesis(rows, fit_point_ids):
    """Reproduce BET for explicitly selected export-table points, not XML bounds.

    The one report constrains the area conversion factor, not Avogadro's
    constant and molar volume individually. These historical constant values
    are a compatible candidate and do not change application-wide defaults.
    """
    if len(fit_point_ids) < 3 or len(set(fit_point_ids)) != len(fit_point_ids):
        raise ValueError('BET needs at least three distinct point IDs')
    by_id = {r['measurement']: r for r in rows if r['phase'] == 'adsorption'}
    if any(i not in by_id for i in fit_point_ids):
        raise ValueError('BET selection contains an absent adsorption point')
    points = [by_id[i] for i in fit_point_ids]
    x = [number(r['relative_pressure']) for r in points]
    v = [number(r['candidate_cm3_g']) for r in points]
    if any(not 0 < p < 1 for p in x) or min(v) <= 0:
        raise ValueError('Nonphysical BET inputs')
    y = [p / ((1 - p) * amount) for p, amount in zip(x, v)]
    x_mean, y_mean = sum(x) / len(x), sum(y) / len(y)
    denominator = sum((p - x_mean) ** 2 for p in x)
    if denominator <= 0:
        raise ValueError('BET needs distinct pressures')
    slope = sum((p - x_mean) * (value - y_mean) for p, value in zip(x, y)) / denominator
    intercept = y_mean - slope * x_mean
    if min(intercept, slope + intercept) <= 0:
        raise ValueError('Nonphysical BET fit')
    vm = 1 / (slope + intercept)
    factor = 6.02214129e23 * .162e-18 / 22413.83
    return {
        'fit_point_ids': fit_point_ids, 'selection_source': 'explicit export-table selection; not inferred from rounded XML bounds',
        'vm_cm3_g': vm, 'c': 1 + slope / intercept,
        'surface_area_m2_g': vm * factor, 'slope': slope, 'intercept': intercept,
        'constant_hypothesis': {'avogadro': 6.02214129e23, 'cross_section_nm2': .162, 'molar_volume_cm3': 22413.83},
        'limitation': 'One report constrains only the combined area factor; the individual vendor constants are not identified.',
    }


def bet_diagnostics(rows, fit):
    """Consistency checks, not evidence that native uptake was reconstructed correctly.

    IUPAC 2015, DOI 10.1515/pac-2014-1117, section 5.2.2: positive C,
    increasing n(1-p/p0), and monolayer capacity within the selected range.
    The pressure predicted by the BET model is reported separately from the
    observed uptake bracket; it does not substitute for the measured isotherm.
    """
    by_id = {row['measurement']: row for row in rows if row['phase'] == 'adsorption'}
    selected = sorted((by_id[i] for i in fit['fit_point_ids']), key=lambda r: r['relative_pressure'])
    x = [number(row['relative_pressure']) for row in selected]
    v = [number(row['candidate_cm3_g']) for row in selected]
    vm, c = number(fit['vm_cm3_g']), number(fit['c'])
    transform = [amount * (1 - p) for p, amount in zip(x, v)]
    positive_c = c > 0
    increasing = all(b >= a for a, b in zip(transform, transform[1:]))
    bracketed = min(v) <= vm <= max(v)
    mono_pressure = 1 / (1 + c ** .5) if positive_c else None
    return {
        'source': 'https://doi.org/10.1515/pac-2014-1117',
        'positive_c': positive_c, 'rouquerol_transform_increases': increasing,
        'monolayer_quantity_in_selected_range': bracketed,
        'selected_pressure_range': [min(x), max(x)],
        'selected_quantity_range_cm3_g': [min(v), max(v)],
        'bet_model_monolayer_pressure': mono_pressure,
        'monolayer_pressure_in_selected_range': mono_pressure is not None and min(x) <= mono_pressure <= max(x),
        'three_checks_pass': positive_c and increasing and bracketed,
        'limitation': 'Passing consistency checks does not validate the native reconstruction or establish a true surface area.',
    }


def audit_pair(experiment, reference):
    meta, expected = reference["meta"], reference["data"]
    checks = {
        "sample": meta["sample"] == experiment["sample"],
        "material": meta["material"] == experiment["material"],
        "start": datetime.fromisoformat(meta["date"]) == datetime.fromisoformat(experiment["start"]),
        "end": datetime.fromisoformat(meta["stoped_time"]) == datetime.fromisoformat(experiment["end"]),
        "mass": abs(number(meta["material_mass"].replace("(g)", "").replace(",", ".")) - experiment["mass_g"]) < 1e-10,
        "units": (meta["pressure_unit"], meta["loading_unit"], meta["material_unit"]) == ("kPa", "cm3(STP)", "g"),
    }
    if not all(checks.values()):
        raise ValueError(f"Reference metadata does not match: {checks}")
    candidate = reconstruct(experiment)
    required = ("branch", "measurement", "pressure", "pressure_relative", "loading", "time_point")
    if any(len(expected[k]) != len(candidate["rows"]) for k in required):
        raise ValueError("Reference point counts differ")
    for i, row in enumerate(candidate["rows"]):
        # These thresholds are half a unit at the source export's precision.
        if (expected["branch"][i] != (row["phase"] == "desorption")
                or expected["measurement"][i] != row["measurement"]
                or expected["time_point"][i] != row["time"]
                or abs(number(expected["pressure"][i]) - row["pressure_kpa"]) > 5.01e-9
                or abs(number(expected["pressure_relative"][i]) - row["relative_pressure"]) > 5.01e-9):
            raise ValueError(f"Reference point mapping differs at {i + 1}")
        row["export_cm3_g"] = number(expected["loading"][i])
        row["error_cm3_g"] = row["candidate_cm3_g"] - row["export_cm3_g"]
        row["matches_export_precision"] = abs(row["error_cm3_g"]) <= EXPORT_HALF_UNIT + 1e-10
    summaries = {}
    for phase in ("adsorption", "desorption"):
        rows = [row for row in candidate["rows"] if row["phase"] == phase]
        summaries[phase] = {
            "point_count": len(rows),
            "matching_points": sum(row["matches_export_precision"] for row in rows),
            "max_absolute_error_cm3_g": max(abs(row["error_cm3_g"]) for row in rows),
            "first_mismatch_id": next((row["record_id"] for row in rows if not row["matches_export_precision"]), None),
        }
    controls = {}
    for label, kwargs in (("rounded_q", {"rounded_q": True}), ("no_nonideal_correction", {"nonideal": False})):
        control = reconstruct(experiment, **kwargs)
        controls[label] = max(abs(row["candidate_cm3_g"] - number(expected["loading"][i]))
                              for i, row in enumerate(control["rows"]) if row["phase"] == "adsorption")
    closure = closure_hypothesis(candidate['rows'])
    for row in closure['rows']:
        row['closure_error_cm3_g'] = row['closure_candidate_cm3_g'] - row['export_cm3_g']
        row['closure_matches_export_precision'] = abs(row['closure_error_cm3_g']) <= EXPORT_HALF_UNIT + 1e-10
    closure['matching_points'] = sum(row['closure_matches_export_precision'] for row in closure['rows'])
    closure['all_points_match'] = closure['matching_points'] == len(closure['rows'])
    closure['max_absolute_error_cm3_g'] = max(abs(row['closure_error_cm3_g']) for row in closure['rows'])
    des_rows = [row for row in candidate['rows'] if row['phase'] == 'desorption']
    unmodified = [row['relative_pressure'] for row in des_rows if row['matches_export_precision']]
    modified = [row['relative_pressure'] for row in des_rows if not row['matches_export_precision']]
    closure['discovery_set_threshold_interval'] = {
        'lower_exclusive': max(modified) if modified else None,
        'upper_inclusive': min(unmodified) if unmodified else None,
        'note': 'Bounds only for this single-threshold hypothesis, not an identified vendor parameter.',
    }
    return {
        "status": "paired_research_audit", "model": experiment["model"],
        "source_sha256": experiment["source_sha256"], "metadata_checks": checks,
        "all_points_match": all(row["matches_export_precision"] for row in candidate["rows"]),
        "production_quantitative_import_enabled": False,
        "stp_ratio": STP_RATIO, "q_recalculated": candidate["q_used"],
        "q_stored": experiment["stored_q"], "dose_count": candidate["dose_count"],
        "export_half_unit_cm3_g": EXPORT_HALF_UNIT,
        "branch_summary": summaries, "control_adsorption_max_error_cm3_g": controls,
        "closure_hypothesis": closure,
        "formula": "delta_V = Vd * [(273.15/101.325)*(Pd/(PdT+273.15)-Pcd/(PcdT+273.15)) - Q*(Pcd-Pcd_previous)] * (1+NonIdealityFactor*Pcd); uptake=sum(delta_V)/mass",
        "limitations": [
            "One paired Sync440A dataset; these same points were used to discover the rule, not a blinded independent validation set.",
            "Raw balance mismatches are retained. Closure hypothesis is an explicitly separate export-processing reconstruction.",
            "The exact closure threshold is unidentifiable in this pair. 0.8 and other thresholds inside the reported interval produce the same result.",
            "No matched BK200C or Micro200B quantitative export is available; this rule has not been validated for those models.",
        ],
        "rows": candidate["rows"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("reference_json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--bet-point-ids', type=int, nargs='+', help='Explicit adsorption IDs from the independent BET report')
    args = parser.parse_args()
    if args.output.resolve() in (args.native.resolve(), args.reference_json.resolve()):
        parser.error("Output must not overwrite an input")
    reference_bytes = args.reference_json.read_bytes()
    report = audit_pair(load_experiment(args.native), json.loads(reference_bytes))
    if args.bet_point_ids:
        report['bet_hypothesis'] = bet_hypothesis(report['rows'], args.bet_point_ids)
        report['bet_hypothesis']['diagnostics'] = bet_diagnostics(report['rows'], report['bet_hypothesis'])
    report["reference_sha256"] = hashlib.sha256(reference_bytes).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report["branch_summary"], indent=2))
    print('Closure hypothesis: {} / {} matching; maximum error {:.9g} cm3/g'.format(
        report['closure_hypothesis']['matching_points'], len(report['rows']),
        report['closure_hypothesis']['max_absolute_error_cm3_g']))


if __name__ == "__main__":
    main()
