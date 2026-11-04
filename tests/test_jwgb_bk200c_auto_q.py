import json
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from scripts.jwgb_bk200c_auto_q_probe import audit_file
from tristar_bet.analysis import bet_analysis
from tristar_bet.jwgb_native import load_jwgb_native


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_auto_q.json').read_text(encoding='utf-8'))


class AutoQTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = []
        for i, fixture in enumerate(FIXTURES):
            path = Path(self.temp.name) / f'{i}.jwgbt'
            path.write_text(fixture['xml'], encoding='utf-8')
            self.paths.append(path)

    def tearDown(self):
        self.temp.cleanup()

    def test_auto_q_reaches_calculation_and_retains_invalid_bet_without_fabricating_area(self):
        for path, counts in zip(self.paths, [(21, 16), (20, 15)]):
            with self.subTest(path=path.name):
                result = load_jwgb_native(path)
                self.assertEqual(tuple(sum(p.phase == phase for p in result.isotherm)
                                       for phase in ('adsorption', 'desorption')), counts)
                self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is not None for p in result.isotherm))
                self.assertEqual(result.method_options['quantity_status'], 'reconstructed_unvalidated')
                self.assertEqual(result.method_options['jwgb_reconstruction_calibration_source'], 'helium_calibration_records')
                self.assertEqual(sum(p.phase == 'adsorption' and .08 <= p.relative_pressure <= .3
                                     for p in result.isotherm), 4)
                for fit in (bet_analysis(result), bet_analysis(result, .08, .3)):
                    self.assertEqual(fit.status, 'invalid_monolayer_capacity')
                    self.assertIsNone(fit.surface_area_m2_g)
                    self.assertEqual(fit.point_count, 4)
                # A generic file with absent quantities still follows the ordinary analysis path.
                ordinary = replace(result, method_options={}, isotherm=[
                    replace(p, quantity_adsorbed_cm3_g_stp=None) for p in result.isotherm])
                self.assertEqual(bet_analysis(ordinary).status, 'not_enough_points')

    def test_calibration_matches_but_both_precision_controls_have_invalid_bet(self):
        for path, q, order, negatives in zip(
            self.paths, [.010520399711102841, .009975239798677153],
            ['before_adsorption', 'after_adsorption'], [2, 0],
        ):
            with self.subTest(path=path.name):
                report = audit_file(path)
                self.assertAlmostEqual(report['recomputed_q'], q, places=14)
                self.assertTrue(report['q_matches_six_decimals'])
                self.assertEqual(report['calibration_order'], order)
                self.assertFalse(report['bk200c_quantitative_validation_passed'])
                self.assertFalse(report['same_run_vendor_uptake_available'])
                self.assertEqual(report['production_bet'], report['controls']['calibrated_q']['fit'])
                for control in report['controls'].values():
                    fit = control['fit']
                    self.assertEqual(fit['status'], 'invalid_monolayer_capacity')
                    self.assertIsNone(fit['surface_area_m2_g'])
                    self.assertLess(fit['slope'] + fit['intercept'], 0)
                    self.assertEqual([r['point_index'] for r in fit['rows']], [2, 3, 4, 5])
                    self.assertEqual(control['negative_point_count'], negatives)

    def test_probe_rejects_mismatched_calibration_and_dates(self):
        for field, value in [('Config/QValue', '0.008500'),
                             ('Data/Q/Item', 'outside_run')]:
            with self.subTest(field=field):
                root = ET.fromstring(FIXTURES[0]['xml'])
                if field == 'Data/Q/Item':
                    root.find(field).set('Day', '2026-10-12')
                else:
                    root.find(field).text = value
                self.paths[0].write_text(ET.tostring(root, encoding='unicode'), encoding='utf-8')
                with self.assertRaises(ValueError):
                    audit_file(self.paths[0])
                result = load_jwgb_native(self.paths[0])
                self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in result.isotherm))
                self.assertNotIn('jwgb_reconstructed', result.method_options)

    def test_incomplete_or_fast_calibration_is_not_silently_replaced_by_stored_q(self):
        for change in ('missing_dose', 'fast', 'unknown_mode'):
            with self.subTest(change=change):
                root = ET.fromstring(FIXTURES[0]['xml'])
                if change == 'missing_dose':
                    q = root.find('Data/Q')
                    q.remove(q[-1])
                elif change == 'fast':
                    root.find('Config/FastTestQ').text = '1'
                else:
                    root.find('Config/AutoTestQ').text = '2'
                self.paths[0].write_text(ET.tostring(root, encoding='unicode'), encoding='utf-8')
                result = load_jwgb_native(self.paths[0])
                self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in result.isotherm))
                self.assertIsNone(bet_analysis(result).surface_area_m2_g)

    def test_pressure_only_metadata_does_not_suppress_explicit_research_quantities(self):
        result = load_jwgb_native(self.paths[1])
        research = replace(result, method_options={
            **result.method_options, 'quantity_status': 'unavailable_native_pressure_only'})
        self.assertEqual(bet_analysis(research, .08, .3).status, 'invalid_monolayer_capacity')


if __name__ == '__main__':
    unittest.main()
