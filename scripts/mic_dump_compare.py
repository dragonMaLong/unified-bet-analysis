"""Compare a MicroActive mic dump (scripts/microactive_mic_dump.py) with the independent parser.

Usage: python -m scripts.mic_dump_compare <dump.json> <sample.smp> [--json out.json]
Matches points by absolute pressure; reports per-column max deviations.
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tristar_bet.smp import load_smp


def val(d, *keys):
    for k in keys:
        d = d[k]
    if not d.get('ok'):
        raise KeyError(d.get('error'))
    return d['value']


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('dump'); ap.add_argument('smp'); ap.add_argument('--json'); ap.add_argument('--by-index', action='store_true', help='pair points by order (vendor pressures thermal-transpiration corrected)')
    a = ap.parse_args()
    d = json.load(open(a.dump, encoding='utf-8'))
    P = val(d, 'isotherm_items', 'absolute pressure'); R = val(d, 'isotherm_items', 'relative pressure')
    QD = val(d, 'isotherm_items', 'quantity dosed'); QA = val(d, 'isotherm_items', 'quantity adsorbed')
    nads = val(d, 'isotherm_items', 'number adsorption'); mass = val(d, 'sample_information', 'sample mass')
    r = load_smp(a.smp)
    # Match in order: each vendor point to the next parser point whose
    # absolute pressure agrees within 1e-6 relative (micro-pore files have
    # pressures far below 1 mmHg, so rounding to a fixed decimal collides).
    rows, unmatched, used = [], [], set()
    pts = list(r.isotherm)
    j = 0
    for i, (p, rel, qd, qa) in enumerate(zip(P, R, QD, QA)):
        o = None
        for k in range(j, len(pts)):
            if a.by_index or abs(pts[k].absolute_pressure_mmHg - p) <= 1e-6 * max(abs(p), 1e-9):
                o = pts[k]; j = k + 1; used.add(k); break
        if o is None:
            unmatched.append(i); continue
        rows.append(dict(i=i, P=p, dP=o.absolute_pressure_mmHg - p, drel=o.relative_pressure - rel,
                         dQd=o.raw_internal_cm3_stp - qd, Qa=qa, dQa=o.quantity_adsorbed_cm3_g_stp - qa,
                         relQa=(o.quantity_adsorbed_cm3_g_stp - qa) / qa if qa else None,
                         phase=o.phase, vendor_phase='adsorption' if i < nads else 'desorption'))
    extra = [(q.index, q.absolute_pressure_mmHg, q.relative_pressure, q.raw_internal_cm3_stp) for k, q in enumerate(pts) if k not in used]
    summ = dict(
        sample=Path(a.smp).name, vendor_points=len(P), vendor_adsorption=nads, ours_points=len(r.isotherm),
        ours_adsorption=sum(p.phase == 'adsorption' for p in r.isotherm), matched=len(rows),
        vendor_unmatched=unmatched, ours_extra=extra, mass_vendor=mass, mass_ours=r.sample.sample_mass_g,
        max_abs_dP=max(abs(x['dP']) for x in rows), max_abs_drel=max(abs(x['drel']) for x in rows),
        max_abs_dQdosed=max(abs(x['dQd']) for x in rows), max_abs_dQads=max(abs(x['dQa']) for x in rows),
        max_rel_dQads=max(abs(x['relQa']) for x in rows if x['relQa'] is not None and x['Qa'] > 1e-6),
        phase_mismatch=[x['i'] for x in rows if x['phase'] != x['vendor_phase']],
        vendor_reports={k: v.get('value') for k, v in d.get('reports', {}).items() if v.get('ok')},
    )
    for k, v in summ.items():
        print(f'{k}: {v}')
    if a.json:
        Path(a.json).write_text(json.dumps(dict(summary=summ, rows=rows), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
