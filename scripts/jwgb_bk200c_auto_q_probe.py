"""Diagnose BK200C automatic-Q imports and retain unsuccessful BET fits.

Matching the stored Q is a calibration check, not a vendor uptake/BET reference.
Both precision controls retain the saved BET interval and all signed quantities.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from tristar_bet.analysis import bet_analysis
from tristar_bet.jwgb_native import load_jwgb_native
from tristar_bet.jwgb_reconstruction import calibrated_q, number, reconstruct


def audit_file(path: Path) -> dict:
    result = load_jwgb_native(path)
    root = ET.parse(path).getroot()
    required = {
        'StationConfig/Station/Model': 'BK200C',
        'Config/Base/Version': '00.01.05.00',
        'Config/AutoTestQ': '1', 'Config/FastTestQ': '0',
        'Config/IsMicroproe': '0', 'Config/Base/DoseGasType': '0',
        'General/GeneralConfig/AdsorbGas': 'N2',
        'General/GeneralConfig/AdsorbTemp': '77.35',
        'General/GeneralConfig/IsRealGas': '0',
        'General/GeneralConfig/IsInsitu': '0',
    }
    for field, expected in required.items():
        if root.findtext(field) != expected:
            raise ValueError(f'Uncovered automatic-Q profile: {field}')
    q_records = [dict(row.attrib) for row in root.findall('Data/Q/Item')]
    q = calibrated_q(q_records)
    stored_q = number(root.findtext('Config/QValue'))
    start = datetime.fromisoformat(root.findtext('Data/BaseInfo/StartTime'))
    end = datetime.fromisoformat(root.findtext('Data/BaseInfo/EndTime'))
    q_times = [datetime.fromisoformat(r['Day'] + 'T' + r['Time']) for r in q_records]
    if not start <= q_times[0] < q_times[1] <= end:
        raise ValueError('Calibration times outside the run or out of order')
    # The observed files store six decimals; this does not establish hidden precision.
    if stored_q <= 0 or abs(q - stored_q) > 0.0000005 + 1e-12:
        raise ValueError('Recomputed Q does not match the six-decimal stored Q')
    low = number(root.findtext('Config/MultiBETReport/MinRange'))
    high = number(root.findtext('Config/MultiBETReport/MaxRange'))
    if not 0 < low < high < 1:
        raise ValueError('Invalid saved BET interval')
    experiment = {
        'records': result.method_options['jwgb_native_pressure_records'],
        'q_records': q_records, 'stored_q': stored_q,
        'vd_cm3': number(root.findtext('Config/Vd')),
        'mass_g': result.sample.sample_mass_g,
        'nonideality_factor': number(root.findtext('General/GeneralConfig/NonIdealityFactor')),
    }
    controls = {}
    # Production (since the BK200C 1(1).xlsx same-run match) applies no dose
    # non-ideality term; the previous candidate is kept as a labelled control.
    for label, rounded, nonideal in (('calibrated_q', False, False), ('stored_q', True, False),
                                     ('calibrated_q_nonideal_on', False, True)):
        reconstruction = reconstruct(experiment, rounded_q=rounded, nonideal=nonideal)
        rows = [r for r in reconstruction['rows'] if not r['is_reference']]
        if len(rows) != len(result.isotherm) or any(
            (point.phase, point.index) != (row['phase'], row['measurement'])
            for point, row in zip(result.isotherm, rows)
        ):
            raise ValueError('Candidate rows do not align with imported points')
        points = [replace(point, quantity_adsorbed_cm3_g_stp=row['candidate_cm3_g'])
                  for point, row in zip(result.isotherm, rows)]
        candidate = replace(result, isotherm=points, method_options={
            **result.method_options, 'quantity_status': 'reconstructed_unvalidated',
            'jwgb_reconstructed': True,
        })
        fit = bet_analysis(candidate, low, high)
        controls[label] = {
            'q_used': reconstruction['q_used'], 'nonideality_applied': nonideal, 'fit': asdict(fit),
            'negative_point_count': sum(r['candidate_cm3_g'] < 0 for r in rows),
            'rows': rows,
        }
    ads_times = [datetime.fromisoformat(r['Day'] + 'T' + r['Time'])
                 for r in experiment['records'] if r['phase'] == 'adsorption']
    calibration_order = ('before_adsorption' if q_times[-1] < min(ads_times)
                         else 'after_adsorption' if q_times[0] > max(ads_times)
                         else 'overlapping_adsorption')
    return {
        'file': str(path.resolve()), 'source_sha256': result.method_options['jwgb_native_sha256'],
        'point_counts': dict(Counter(p.phase for p in result.isotherm)),
        'production_bet': asdict(bet_analysis(result)),
        'production_unavailable_reason': result.method_options.get('jwgb_reconstruction_unavailable_reason'),
        'stored_q': stored_q, 'recomputed_q': q, 'q_matches_six_decimals': True,
        'calibration_order': calibration_order, 'controls': controls,
        'same_run_vendor_uptake_available': False,
        'bk200c_quantitative_validation_passed': False,
        'limitations': [
            'Matching calibration Q does not validate the pressure-to-uptake formula.',
            'Candidate formula is transferred from Sync440A; no BK vendor output is used.',
            'Failed BET fits are retained; no point deletion, Q substitution, or range search.',
            'A failed candidate does not establish that the original experiment is invalid.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in [p.resolve() for p in args.native]:
        parser.error('Output must not overwrite an input')
    reports = [audit_file(path) for path in args.native]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
