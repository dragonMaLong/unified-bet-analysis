import json
from pathlib import Path
import unittest

from scripts.jwgb_patent_q_probe import audit_experiment, equivalent_q, patent_delta
from tristar_bet.jwgb_reconstruction import STP_RATIO


class PatentQNotationTests(unittest.TestCase):
    def test_no_adsorption_synthetic_balance_requires_the_correct_q_scale(self):
        # Equal-temperature, nonadsorbing chamber: transfer 40 pressure units
        # into a chamber whose pressure rises by 50; geometric ratio is .8.
        normalized = STP_RATIO/300*.8
        self.assertAlmostEqual(equivalent_q(normalized,300),.8,places=14)
        self.assertAlmostEqual(patent_delta(100,60,10,30,300,.8),0,places=14)
        self.assertGreater(abs(patent_delta(100,60,10,30,300,normalized)),10)

    def test_derived_mapping_preserves_common_temperature_balance(self):
        for t in (290,300,310):
            q=.0085
            expected=30*(STP_RATIO*(100-60)/t-q*(60-10))
            actual=patent_delta(100,60,10,30,t,equivalent_q(q,t))
            self.assertAlmostEqual(actual,expected,places=12)

    def test_real_export_rejects_literal_substitution_without_certifying_bk(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        report=audit_experiment(fixture['experiment'],fixture['reference'])
        self.assertEqual(report['adsorption_points'],31)
        self.assertEqual(report['comparisons']['current_candidate_cm3_g']['matching_five_decimal_exports'],31)
        for name in ('literal_patent_with_stored_q_cm3_g','q_scale_only_control_cm3_g'):
            self.assertEqual(report['comparisons'][name]['matching_five_decimal_exports'],0)
            self.assertGreater(report['comparisons'][name]['max_absolute_error_cm3_g'],1700)
        self.assertTrue(report['same_run_reference_available'])
        self.assertFalse(report['bk200c_quantitative_validation_passed'])

    def test_absent_reference_does_not_generate_validation_claim(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        report=audit_experiment(fixture['experiment'])
        self.assertFalse(report['same_run_reference_available'])
        self.assertEqual(report['comparisons'],{})
        self.assertTrue(all('vendor_export_cm3_g' not in row for row in report['rows']))

    def test_invalid_temperature_and_nonfinite_input_are_rejected(self):
        for t in (0,-1,float('nan')):
            with self.assertRaises(ValueError):
                equivalent_q(.0085,t)
            with self.assertRaises(ValueError):
                patent_delta(100,60,10,30,t,.8)


if __name__=='__main__':
    unittest.main()
