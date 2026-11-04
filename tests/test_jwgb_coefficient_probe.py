import copy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import unittest

from scripts.jwgb_coefficient_probe import (
    InfeasibleCoefficientsError, adsorption_basis, coefficient_audit,
    feasible_vertices, infeasible_point_subset, placement_audit,
)


class CoefficientIntervalTests(unittest.TestCase):
    def test_independent_rectangle_has_exact_expected_corners(self):
        vertices = feasible_vertices([(1,0,2,'.1'),(0,1,3,'.2')])
        self.assertEqual(set(vertices), {(Decimal(a),Decimal(b))
                         for a in ('1.9','2.1') for b in ('2.8','3.2')})

    def test_inconsistent_rounded_observations_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'No coefficients'):
            feasible_vertices([(1,0,2,'.1'),(1,0,4,'.1'),(0,1,3,'.2')])

    def test_unbounded_basis_is_not_reported_as_identified(self):
        with self.assertRaisesRegex(ValueError,'bounded'):
            feasible_vertices([(1,0,2,'.1')])

    def test_real_pair_excludes_truncated_ratio_even_with_variable_nonideality(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        result = coefficient_audit(fixture['experiment'],fixture['reference'])
        lo,hi = map(Decimal,result['coefficient_bounds']['a_stp_ratio'])
        candidate = Decimal(result['declared_candidate']['a'])
        self.assertLess(lo,candidate)
        self.assertGreater(hi,candidate)
        self.assertLess(Decimal('2.69578'),lo)
        self.assertEqual(result['declared_candidate']['matching_points'],31)
        self.assertEqual(result['truncated_ratio_control']['matching_points'],0)
        self.assertIn('BK200C applicability',result['not_identified'])

    def test_incompatible_export_point_cannot_be_hidden_by_refitting_coefficients(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        reference = copy.deepcopy(fixture['reference'])
        reference['data']['loading'][0] += .001
        with self.assertRaisesRegex(ValueError,'No coefficients'):
            coefficient_audit(fixture['experiment'],reference)

    def test_three_point_contradiction_has_analytic_linear_certificate(self):
        rows = [(1,1,4,'.01'),(2,1,6,'.01'),(3,1,9,'.01')]
        certificate = infeasible_point_subset(rows)
        self.assertEqual(certificate['adsorption_ordinals'],[1,2,3])
        linear = certificate['linear_contradiction_certificate']
        self.assertEqual(Decimal(linear['weighted_basis_u']),0)
        self.assertEqual(Decimal(linear['weighted_basis_w']),0)
        self.assertEqual(Decimal(linear['minimum_possible_max_error_cm3_g']),Decimal('.25'))
        self.assertGreater(Decimal(linear['absolute_weighted_export']),Decimal(linear['maximum_rounding_allowance']))
        for i in range(3):
            self.assertTrue(feasible_vertices([r for j,r in enumerate(rows) if j!=i]))

    def test_real_pair_alternative_placements_have_recheckable_contradictions(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        result = placement_audit(fixture['experiment'],fixture['reference'])
        self.assertTrue(result['models']['per_dose']['compatible_after_refitting'])
        self.assertEqual(result['models']['per_dose']['declared_constants_matching_points'],31)
        for name,item in result['models'].items():
            if name=='per_dose':
                continue
            with self.subTest(model=name):
                self.assertFalse(item['compatible_after_refitting'])
                proof = item['contradiction']
                rows = [tuple(row[k] for k in ('u','w','export','half_unit')) for row in proof['inequalities']]
                with self.assertRaises(InfeasibleCoefficientsError):
                    feasible_vertices(rows)
                for i in range(len(rows)):
                    self.assertTrue(feasible_vertices([r for j,r in enumerate(rows) if j!=i]))
                linear = proof['linear_contradiction_certificate']
                self.assertIsNotNone(linear)
                self.assertLess(abs(Decimal(linear['weighted_basis_u'])),Decimal('1e-40'))
                self.assertLess(abs(Decimal(linear['weighted_basis_w'])),Decimal('1e-40'))
                self.assertGreater(Decimal(linear['minimum_possible_max_error_cm3_g']),Decimal('.000005'))
                weights = [Fraction(k) for k in linear['exact_rational_weights']]
                exact = [[Fraction(v) for v in r] for r in rows]
                self.assertEqual(sum(k*r[0] for k,r in zip(weights,exact)),0)
                self.assertEqual(sum(k*r[1] for k,r in zip(weights,exact)),0)
                self.assertGreater(abs(sum(k*r[2] for k,r in zip(weights,exact))),
                                   sum(abs(k)*r[3] for k,r in zip(weights,exact)))
                wide = item['conservative_full_unit_check']
                self.assertFalse(wide['compatible_after_refitting'])
                self.assertGreater(Decimal(wide['contradiction']['linear_contradiction_certificate']['minimum_possible_max_error_cm3_g']),Decimal('.00001'))

    def test_placement_comparison_rejects_mismatched_reference_and_unknown_model(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError,'Unknown nonideality'):
            adsorption_basis(fixture['experiment'],fixture['reference'],'invented')
        fixture['reference']['data']['pressure'][0] += .1
        with self.assertRaisesRegex(ValueError,'mapping differs'):
            placement_audit(fixture['experiment'],fixture['reference'])


if __name__ == '__main__':
    unittest.main()
