"""Conditional coefficient bounds from native Sync440A / rounded export data.

This constrains an already hypothesized formula, not the space of all possible
vendor algorithms. Decimal arithmetic avoids treating export midpoints as exact
measurements. No fitted coefficient is installed into the application.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import combinations
import hashlib
import json
from pathlib import Path

from scripts.jwgb_paired_probe import audit_pair, load_experiment


CORRECTION_MODELS = {
    'per_dose': 'V_i = a * sum_j d_j*(1+c*P_j)',
    'after_cumulative': 'V_i = a * (sum_j d_j)*(1+c*P_i)',
    'manifold_only': 'V_i = a * sum_j [M_j*(1+c*P_j)-F_j]',
    'per_equilibrium_group': 'V_i = a * sum_g [(sum_j_in_g d_j)*(1+c*P_last_in_g)]',
    'previous_pressure': 'V_i = a * sum_j d_j*(1+c*P_previous_j)',
    'mean_pressure': 'V_i = a * sum_j d_j*(1+c*(P_j+P_previous_j)/2)',
}


class InfeasibleCoefficientsError(ValueError):
    """The supplied bounded model has no jointly feasible coefficients."""


def D(value):
    return Decimal(str(value))


def feasible_vertices(rows):
    """Vertices of |u*a+w*b-y|<=half with a,b>=0, for nonnegative basis.

    Bounds are conditional on rounded output being nearest rounding; endpoint
    inclusion is conservative. Nonnegative basis with positive column sums
    guarantees boundedness in the nonnegative quadrant.
    """
    with localcontext() as ctx:
        ctx.prec = 50
        rows = [tuple(D(v) for v in row) for row in rows]
        if not rows or any(not v.is_finite() for row in rows for v in row):
            raise ValueError('Finite rows required')
        if any(u < 0 or w < 0 or h <= 0 for u, w, y, h in rows):
            raise ValueError('Nonnegative basis and positive rounding width required')
        if sum(r[0] for r in rows) <= 0 or sum(r[1] for r in rows) <= 0:
            raise ValueError('Both coefficient directions must be bounded')
        constraints = [(D(-1), D(0), D(0)), (D(0), D(-1), D(0))]
        for u, w, y, h in rows:
            constraints.extend([(u, w, y+h), (-u, -w, -y+h)])
        vertices = set()
        # Arithmetic slack is 25 orders below the exported half-unit, and is
        # only for Decimal division roundoff at an intersected boundary.
        slack = D('1e-30')
        for (p, q, r), (s, t, v) in combinations(constraints, 2):
            det = p*t-q*s
            if det == 0:
                continue
            a, b = (r*t-q*v)/det, (p*v-r*s)/det
            if all(x*a+y*b <= z+slack for x, y, z in constraints):
                vertices.add((a, b))
        if not vertices:
            raise InfeasibleCoefficientsError('No coefficients satisfy all export rounding intervals')
        return sorted(vertices)


def adsorption_basis(experiment, reference, correction='per_dose'):
    if correction not in CORRECTION_MODELS:
        raise ValueError('Unknown nonideality placement')
    # Verify identity, chronology, pressure/time/branch mapping before fitting.
    audit_pair(experiment, reference)
    with localcontext() as ctx:
        ctx.prec = 50
        qr, qd = experiment['q_records']
        kelvin = D('273.15')
        q_over_a = (D(qd['Pd'])/(D(qd['PdT'])+kelvin)
                    - D(qd['Pcd'])/(D(qd['PcdT'])+kelvin)) / (D(qd['Pcd'])-D(qr['Pcd']))
        scale = D(experiment['vd_cm3'])/D(experiment['mass_g'])
        u = w = group_dose = D(0)
        previous = None
        result = []
        for row in experiment['records']:
            if row['phase'] != 'adsorption':
                break
            p = D(row['Pcd'])
            if previous is None:
                previous = p
                continue
            manifold = scale*(D(row['Pd'])/(D(row['PdT'])+kelvin)
                             - p/(D(row['PcdT'])+kelvin))
            dose = manifold-scale*q_over_a*(p-previous)
            u += dose
            group_dose += dose
            if correction == 'per_dose':
                w += dose*p
            elif correction == 'after_cumulative':
                w = u*p
            elif correction == 'manifold_only':
                w += manifold*p
            elif correction == 'per_equilibrium_group' and row['Balance'] == '1':
                w += group_dose*p
            elif correction == 'previous_pressure':
                w += dose*previous
            elif correction == 'mean_pressure':
                w += dose*(p+previous)/2
            previous = p
            if row['Balance'] == '1':
                i = len(result)
                result.append((u, w, D(reference['data']['loading'][i]), D('0.000005')))
                group_dose = D(0)
        return result


def infeasible_point_subset(rows):
    """Reduce a contradiction to an inclusion-minimal subset of whole points.

    Export point IDs are one-based adsorption ordinals. Both coefficient
    nonnegativity constraints remain implicit. This is not a claim that the
    number of points is globally minimal.
    """
    if any(D(row[0]) <= 0 or D(row[1]) <= 0 for row in rows):
        raise ValueError('Subset certificate requires both positive basis columns per point')
    try:
        feasible_vertices(rows)
    except InfeasibleCoefficientsError:
        pass
    else:
        raise ValueError('Cannot certify contradiction in a feasible model')
    active = list(range(len(rows)))
    for index in range(len(rows)):
        trial = [i for i in active if i != index]
        if not trial:
            continue
        try:
            feasible_vertices([rows[i] for i in trial])
        except InfeasibleCoefficientsError:
            active = trial
    certificate = None
    if len(active) == 3:
        with localcontext() as ctx:
            ctx.prec = 50
            # These exact rational weights certify the emitted finite-decimal
            # basis, rather than claiming a tiny floating residual is zero for
            # unrestricted coefficients. Raw-to-basis calculations above are
            # still conditional on native values and 50-digit arithmetic.
            subset = [[Fraction(D(v)) for v in rows[i]] for i in active]
            u,v,w = subset
            weights = [v[0]*w[1]-w[0]*v[1],
                       w[0]*u[1]-u[0]*w[1],
                       u[0]*v[1]-v[0]*u[1]]
            normalizer = max(abs(k) for k in weights)
            if normalizer:
                weights = [k/normalizer for k in weights]
                discrepancy = abs(sum(k*r[2] for k,r in zip(weights,subset)))
                rounding_allowance = sum(abs(k)*r[3] for k,r in zip(weights,subset))
                if discrepancy > rounding_allowance:
                    def decimal_text(value):
                        return str(D(value.numerator)/D(value.denominator))
                    cancel_u = sum(k*r[0] for k,r in zip(weights,subset))
                    cancel_w = sum(k*r[1] for k,r in zip(weights,subset))
                    assert cancel_u == cancel_w == 0
                    certificate = dict(
                        weights=[decimal_text(k) for k in weights],
                        exact_rational_weights=[str(k) for k in weights],
                        weighted_basis_u=str(cancel_u),weighted_basis_w=str(cancel_w),
                        absolute_weighted_export=decimal_text(discrepancy),
                        maximum_rounding_allowance=decimal_text(rounding_allowance),
                        minimum_possible_max_error_cm3_g=decimal_text(discrepancy/sum(abs(k) for k in weights)),
                        interpretation='Exact rational weights cancel the emitted finite-decimal basis; exported values cannot cancel even within rounding. This excludes unrestricted real a,b for that basis.')
    return dict(adsorption_ordinals=[i+1 for i in active],
                inequalities=[dict(u=str(rows[i][0]),w=str(rows[i][1]),
                                   export=str(rows[i][2]),half_unit=str(rows[i][3])) for i in active],
                coefficient_constraints='a>=0, b>=0',
                inclusion_minimal=True, linear_contradiction_certificate=certificate)


def placement_audit(experiment, reference):
    with localcontext() as ctx:
        ctx.prec = 50
        a = D('273.15')/D('101.325')
        c = D(experiment['nonideality_factor'])
        models = {}
        for name, formula in CORRECTION_MODELS.items():
            rows = adsorption_basis(experiment, reference, name)
            residuals = [abs(a*(u+c*w)-y) for u,w,y,h in rows]
            item = dict(formula=formula, declared_constants_matching_points=sum(e<=r[3] for e,r in zip(residuals,rows)),
                        declared_constants_max_error_cm3_g=float(max(residuals)))
            try:
                vertices = feasible_vertices(rows)
            except InfeasibleCoefficientsError:
                item.update(compatible_after_refitting=False,
                            contradiction=infeasible_point_subset(rows))
            else:
                # A witness is a concrete reproducible set of coefficients;
                # compatibility alone does not prove this is the vendor model.
                x,y = vertices[0]
                item.update(compatible_after_refitting=True,witness_a=str(x),witness_b=str(y))
            # The closest rejected alternatives merit a second check that
            # does not require choosing nearest rounding over truncation.
            # +/- one full display unit contains both rounding errors, but
            # still does not cover hidden precision of the native inputs.
            wide = [(u,w,y,D('0.00001')) for u,w,y,h in rows]
            conservative = {'absolute_export_tolerance_cm3_g':'0.00001'}
            try:
                feasible_vertices(wide)
            except InfeasibleCoefficientsError:
                conservative.update(compatible_after_refitting=False,
                                    contradiction=infeasible_point_subset(wide))
            else:
                conservative['compatible_after_refitting'] = True
            item['conservative_full_unit_check'] = conservative
            models[name] = item
        return dict(source_sha256=experiment['source_sha256'],model=experiment['model'],
                    adsorption_points=len(rows),models=models,
                    definitions='d_j=M_j-F_j in the basis before STP conversion; M_j is manifold transfer and F_j is free-space change. Q=a*q_ratio_from_helium.',
                    inference='Model exclusions allow both a>=0 and b=a*c>=0 to be refitted jointly.',
                    scope='Same discovery sample, six specified formula placements only; not an exhaustive algorithm search or BK200C validation.',
                    assumptions=['Exact native inputs/mass/Vd and helium relation; nearest export rounding to five decimals.',
                                 'The two coefficients are constant across the 31 adsorption points.'])


def coefficient_audit(experiment, reference):
    with localcontext() as ctx:
        ctx.prec = 50
        rows = adsorption_basis(experiment, reference)
        vertices = feasible_vertices(rows)
        a = D('273.15')/D('101.325')
        c = D(experiment['nonideality_factor'])
        def error(a_value, c_value):
            residuals = [abs(a_value*(u+c_value*w)-y) for u,w,y,h in rows]
            return dict(max_error_cm3_g=float(max(residuals)),
                        matching_points=sum(e<=r[3] for e,r in zip(residuals,rows)))
        return dict(
            source_sha256=experiment['source_sha256'], model=experiment['model'],
            adsorption_points=len(rows), formula='V_i = a*U_i + b*W_i; b=a*c; Q=a*q_ratio_from_helium',
            coefficient_bounds={
                'a_stp_ratio':[str(min(v[0] for v in vertices)),str(max(v[0] for v in vertices))],
                'b_stp_times_nonideality':[str(min(v[1] for v in vertices)),str(max(v[1] for v in vertices))],
                'c_nonideality':[str(min(v[1]/v[0] for v in vertices)),str(max(v[1]/v[0] for v in vertices))]},
            vertices=[[str(x),str(y)] for x,y in vertices],
            declared_candidate={'a':str(a),'c':str(c),**error(a,c)},
            truncated_ratio_control={'a':'2.69578','c':str(c),**error(D('2.69578'),c)},
            scope='Conditional discovery-set bounds; not independent validation or BK200C validation.',
            assumptions=['The candidate dose formula is correct.',
                         'Native numbers, mass, Vd and helium calibration are exact inputs for this audit.',
                         'Exports use nearest rounding to five decimal places.',
                         'a and b are nonnegative. No desorption correction is inferred here.'],
            not_identified=['Alternative functional forms', 'Separate STP temperature and pressure constants',
                            'Measurement uncertainty', 'BK200C applicability', 'Micro200B applicability'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', type=Path)
    parser.add_argument('reference', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-placements', action='store_true')
    args = parser.parse_args()
    if args.output.resolve() in (args.native.resolve(),args.reference.resolve()):
        parser.error('Output must not overwrite input')
    audit = placement_audit if args.compare_placements else coefficient_audit
    report = audit(load_experiment(args.native),json.loads(args.reference.read_text(encoding='utf-8')))
    report['reference_sha256'] = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='vertices'},ensure_ascii=False))


if __name__ == '__main__':
    main()
