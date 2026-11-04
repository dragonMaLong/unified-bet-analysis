"""Compare the Q notation in CN116429546A [0086] with observed JWGBT Q.

The patent is a primary description of a control method, not a specification
of BK200C 00.01.05.00. Its common-temperature formula omits the nonideality
term used by the existing reconstruction. No application defaults are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from scripts.jwgb_paired_probe import audit_pair, load_experiment
from tristar_bet.jwgb_native import load_jwgb_native
from tristar_bet.jwgb_reconstruction import STP_RATIO, calibrated_q, number, reconstruct

PATENT_URL = 'https://patents.google.com/patent/CN116429546A/zh'
PATENT_PDF_URL = 'https://patentimages.storage.googleapis.com/ef/bd/15/9124f379a7f32c/CN116429546A.pdf'
PATENT_PDF_SHA256 = '01f460ff5b2b9e988f6e704622e36795842e829e1042514caf90c74a05cfb4de'


def patent_delta(pd, pcd, previous, vd, temperature_k, q_patent):
    pd,pcd,previous,vd,temperature_k,q_patent = map(number,(pd,pcd,previous,vd,temperature_k,q_patent))
    if min(pd,pcd,previous,q_patent) < 0 or min(vd,temperature_k) <= 0:
        raise ValueError('Invalid patent-formula inputs')
    return STP_RATIO*vd/temperature_k*((pd-pcd)-q_patent*(pcd-previous))


def equivalent_q(normalized_q, temperature_k):
    normalized_q,temperature_k = map(number,(normalized_q,temperature_k))
    if min(normalized_q,temperature_k) <= 0:
        raise ValueError('Positive Q and absolute temperature required')
    return normalized_q*temperature_k/STP_RATIO


def audit_experiment(experiment, reference=None):
    if experiment['model']=='Sync440A':
        q = calibrated_q(experiment['q_records'])
        rounded_q = False
        q_source = 'unrounded helium-derived Q in the paired reconstruction'
    elif experiment['model']=='BK200C':
        q = number(experiment['stored_q'])
        rounded_q = True
        q_source = 'manual QValue, unchanged and not vendor-validated'
    else:
        raise ValueError('Only the observed BK200C and Sync440A layouts are covered')
    if reference is not None:
        if experiment['model']!='Sync440A':
            raise ValueError('No verified same-run BK200C reference adapter is available')
        audit_pair(experiment,reference)
    baseline = [r for r in reconstruct(experiment,rounded_q=rounded_q)['rows'] if r['phase']=='adsorption']
    literal = scale_control = 0.0
    previous = None
    rows,temperatures = [],[]
    vd,mass,c = map(number,(experiment['vd_cm3'],experiment['mass_g'],experiment['nonideality_factor']))
    for record in experiment['records']:
        if record['phase']!='adsorption':
            break
        pd,pcd = number(record['Pd']),number(record['Pcd'])
        if previous is None:
            if pd!=-0.00001:
                raise ValueError('Missing initial reference')
            previous=pcd
            continue
        td,tc = number(record['PdT'])+273.15,number(record['PcdT'])+273.15
        temperatures.append(td)
        literal += patent_delta(pd,pcd,previous,vd,td,experiment['stored_q'])
        # Only change the scale of Q; retain separate temperatures and the
        # current per-dose nonideality term to isolate the notation mistake.
        scale_control += vd*(STP_RATIO*(pd/td-pcd/tc)
                             - STP_RATIO/td*q*(pcd-previous))*(1+c*pcd)
        previous=pcd
        if record['Balance']!='1':
            continue
        index=len(rows)
        current=baseline[index]
        assert current['record_id']==record['ID']
        row=dict(record_id=record['ID'],relative_pressure=current['relative_pressure'],
                 current_candidate_cm3_g=current['candidate_cm3_g'],
                 literal_patent_with_stored_q_cm3_g=literal/mass,
                 q_scale_only_control_cm3_g=scale_control/mass)
        if reference is not None:
            row['vendor_export_cm3_g']=number(reference['data']['loading'][index])
        rows.append(row)
    comparisons={}
    if reference is not None:
        for key in ('current_candidate_cm3_g','literal_patent_with_stored_q_cm3_g','q_scale_only_control_cm3_g'):
            errors=[abs(row[key]-row['vendor_export_cm3_g']) for row in rows]
            comparisons[key]=dict(max_absolute_error_cm3_g=max(errors),
                                  matching_five_decimal_exports=sum(e<=.000005+1e-10 for e in errors))
    return dict(
        native_sha256=experiment['source_sha256'], model=experiment['model'],adsorption_points=len(rows),
        patent=dict(url=PATENT_URL,pdf_url=PATENT_PDF_URL,pdf_sha256=PATENT_PDF_SHA256,
                    pdf_page=11,printed_description_page='8/8',paragraph='0086',
                    equation='deltaV = (273.15/101.325)*Vd/Td * [(Pd-Pcd) - Q_patent*(Pcd-Pcd_previous)]'),
        stored_q=experiment['stored_q'],candidate_q=q,candidate_q_source=q_source,
        algebraic_mapping='Q_patent = Q_normalized * Td / (273.15/101.325), conditional on a common-temperature equivalent formula',
        equivalent_q_patent_range=[equivalent_q(q,min(temperatures)),equivalent_q(q,max(temperatures))],
        assumptions=['Td in the literal control is PdT+273.15; the patent uses one temperature.',
                     'The algebraic mapping is a derived equivalence, not a recovered vendor field conversion.',
                     'The patent does not identify BK200C or JWGBT software version 00.01.05.00.'],
        same_run_reference_available=reference is not None,comparisons=comparisons,rows=rows,
        conclusion='Identically naming Q does not establish identical numeric meaning. The literal substitution is tested only where a same-run reference exists.',
        bk200c_quantitative_validation_passed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native',type=Path)
    parser.add_argument('--reference',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    sources=[args.native]+([args.reference] if args.reference else [])
    if args.output.resolve() in [p.resolve() for p in sources]:
        parser.error('Output must not overwrite an input')
    result=load_jwgb_native(args.native,reconstruct_uptake=False)
    if result.method_options['instrument_model']=='Sync440A':
        experiment=load_experiment(args.native)
    elif result.method_options['instrument_model']=='BK200C':
        root=ET.parse(args.native).getroot()
        if root.findtext('Config/AutoTestQ')!='0':
            raise ValueError('Only manual-Q BK200C profile is covered')
        experiment=dict(model='BK200C',source_sha256=result.method_options['jwgb_native_sha256'],
                        vd_cm3=number(root.findtext('Config/Vd')),mass_g=result.sample.sample_mass_g,
                        stored_q=number(root.findtext('Config/QValue')),
                        nonideality_factor=number(root.findtext('General/GeneralConfig/NonIdealityFactor')),
                        records=result.method_options['jwgb_native_pressure_records'])
    else:
        raise ValueError('Unsupported instrument model')
    reference=json.loads(args.reference.read_text(encoding='utf-8')) if args.reference else None
    report=audit_experiment(experiment,reference)
    if args.reference:
        report['reference_sha256']=hashlib.sha256(args.reference.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False))


if __name__=='__main__':
    main()
