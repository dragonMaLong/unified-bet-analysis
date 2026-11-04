"""Bound a fixed two-zone free-space hypothesis without fitting its warm fraction.

This is an independent alternative to the Sync440A per-dose correction, NOT
a recovered BK200C algorithm. The warm-zone temperature is assumed equal to
the recorded manifold temperature. Helium calibration fixes only the sum of
warm/cold contributions at calibration time, not their separate values.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from scripts.jwgb_bk200c_auto_q_probe import audit_file
from tristar_bet.jwgb_native import load_jwgb_native
from tristar_bet.jwgb_reconstruction import STP_RATIO, number


def zone_bounds(records, *, q, vd, mass, calibration_temperature_k, coefficient):
    """Use F(P,T,w)=Q*P*((1-w)*(1+c*P)+w*Tcal/T), 0<=w<=1.

    F is free gas per manifold volume, expressed as STP volume. Physical zone
    volumes, calibration Q, cold temperature and reference uptake stay fixed.
    Uptake = Vd/mass * (sum(STP*(Pd/Td-Pcd/Tcd)) - (F_final-F_initial)).
    It is affine in w, so endpoint min/max bounds cover every w, not just a grid.
    """
    q, vd, mass, tcal, c = map(number, (q, vd, mass, calibration_temperature_k, coefficient))
    if min(q, vd, mass, tcal) <= 0 or c < 0:
        raise ValueError('Invalid zone parameters')
    total, previous, phase = 0.0, None, None
    initial_cold = initial_warm = None
    output = []
    for row in records:
        pd, p = number(row['Pd']), number(row['Pcd'])
        tc = number(row['PcdT']) + 273.15
        if p < 0 or tc <= 0:
            raise ValueError('Invalid pressure or absolute temperature')
        if row['phase'] != phase:
            if (phase, row['phase']) not in ((None, 'adsorption'), ('adsorption', 'desorption')):
                raise ValueError('Invalid branch order')
            if pd != -.00001 or (previous is not None and abs(previous - p) > 1e-8):
                raise ValueError('Missing or discontinuous reference')
            if phase is None:
                initial_cold = q * p * (1 + c * p)
                initial_warm = q * p * tcal / tc
            previous, phase = p, row['phase']
            continue
        td = number(row['PdT']) + 273.15
        if pd < 0 or td <= 0 or row['Balance'] not in ('0', '1'):
            raise ValueError('Invalid dose')
        total += STP_RATIO * (pd / td - p / tc)
        previous = p
        if row['Balance'] == '0':
            continue
        p0 = number(row['P0'])
        if p0 <= 0:
            raise ValueError('Invalid saturation pressure')
        cold = vd / mass * (total - (q * p * (1 + c * p) - initial_cold))
        warm = vd / mass * (total - (q * p * tcal / tc - initial_warm))
        output.append({
            'phase': phase, 'record_id': row['ID'], 'relative_pressure': p / p0,
            'all_cold_cm3_g': cold, 'all_warm_cm3_g': warm,
            'minimum_cm3_g': min(cold, warm), 'maximum_cm3_g': max(cold, warm),
        })
    if not output:
        raise ValueError('No measured rows')
    return output


def audit(path):
    # Reuse model, native schema, calibration, time and Q consistency checks.
    calibration = audit_file(path)
    root = ET.parse(path).getroot()
    native = load_jwgb_native(path, reconstruct_uptake=False)
    q_rows = root.findall('Data/Q/Item')
    tcal = number(q_rows[-1].get('PcdT')) + 273.15
    low, high = (number(root.findtext('Config/MultiBETReport/' + key))
                 for key in ('MinRange', 'MaxRange'))
    rows = zone_bounds(
        native.method_options['jwgb_native_pressure_records'],
        q=calibration['recomputed_q'], vd=number(root.findtext('Config/Vd')),
        mass=native.sample.sample_mass_g, calibration_temperature_k=tcal,
        coefficient=number(root.findtext('General/GeneralConfig/NonIdealityFactor')),
    )
    selected = [r for r in rows if r['phase'] == 'adsorption' and low <= r['relative_pressure'] <= high]
    impossible = [r['record_id'] for r in selected if r['maximum_cm3_g'] < 0]
    return {
        'source_file': str(path.resolve()), 'source_sha256': calibration['source_sha256'],
        'hypothesis': 'Fixed physical warm/cold volumes; manifold temperature represents warm gas; constant cold temperature; zero initial uptake',
        'free_gas_formula': 'Q*P*((1-w)*(1+c*P)+w*Tcal/T), 0<=w<=1',
        'uptake_formula': 'Vd/mass*(sum(STP*(Pd/Td-Pcd/Tcd))-(F_final-F_initial))',
        'q': calibration['recomputed_q'], 'calibration_temperature_k': tcal,
        'warm_fraction_bounds': [0, 1], 'saved_bet_range': [low, high],
        'saved_range_rows': selected, 'rows': rows,
        'negative_for_every_warm_fraction_ids': impossible,
        'same_run_vendor_uptake_available': False,
        'quantitative_validation_passed': False,
        'limitations': [
            'Bounds cover this affine hypothesis, not arbitrary vendor compensation or measurement error.',
            'Recorded values are treated as exact; no instrument uncertainty interval is asserted.',
            'Liquid-level drift, actual warm-zone temperature, helium adsorption and baseline offsets are not identified.',
            'A negative bound rejects a positive-uptake explanation under these assumptions, not the original experiment.',
            'No warm fraction is fitted or selected for production; no BET area is inferred.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in [p.resolve() for p in args.native]:
        parser.error('Output must not overwrite input')
    reports = [audit(path) for path in args.native]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
