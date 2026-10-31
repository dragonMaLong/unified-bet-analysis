import copy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from scripts.jwgb_paired_probe import audit_pair, bet_hypothesis, bet_diagnostics, closure_hypothesis, reconstruct
from tristar_bet import load_file


FIXTURE = Path(__file__).parent / 'fixtures/jwgb_sync440_paired.json'


class PairedJwgbTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.experiment, self.reference = fixture['experiment'], fixture['reference']

    def test_real_same_run_adsorption_matches_but_desorption_does_not(self):
        report = audit_pair(self.experiment, self.reference)
        self.assertTrue(all(report['metadata_checks'].values()))
        self.assertEqual(report['dose_count'], 74)
        self.assertEqual(report['branch_summary']['adsorption']['matching_points'], 31)
        self.assertLess(report['branch_summary']['adsorption']['max_absolute_error_cm3_g'], 0.000005)
        self.assertEqual(report['branch_summary']['desorption']['matching_points'], 6)
        self.assertEqual(report['branch_summary']['desorption']['first_mismatch_id'], '7')
        self.assertGreater(report['branch_summary']['desorption']['max_absolute_error_cm3_g'], 2)
        self.assertFalse(report['all_points_match'])
        self.assertFalse(report['production_quantitative_import_enabled'])
        self.assertGreater(report['control_adsorption_max_error_cm3_g']['rounded_q'], 0.018)
        self.assertGreater(report['control_adsorption_max_error_cm3_g']['no_nonideal_correction'], 4.7)

    def test_intermediate_doses_cannot_be_dropped(self):
        complete = reconstruct(self.experiment)
        self.assertEqual(complete['rows'][0]['record_id'], '1_7')
        self.assertEqual(complete['rows'][0]['cumulative_doses'], 7)
        incomplete = copy.deepcopy(self.experiment)
        incomplete['records'] = [r for r in incomplete['records'] if r['Balance'] == '1']
        wrong = reconstruct(incomplete)
        self.assertGreater(abs(wrong['rows'][0]['candidate_cm3_g'] - complete['rows'][0]['candidate_cm3_g']), 60)

    def test_inferred_closure_reproduces_all_fifty_exported_points(self):
        report = audit_pair(self.experiment, self.reference)
        closure = report['closure_hypothesis']
        self.assertEqual(closure['matching_points'], 50)
        self.assertTrue(closure['all_points_match'])
        self.assertLess(closure['max_absolute_error_cm3_g'], 0.000005)
        self.assertAlmostEqual(closure['endpoint_gap_cm3_g'], 1.5522051606148182, places=9)
        # Four points need an additional floor; the last one is closed by construction.
        self.assertEqual(closure['floor_point_count'], 4)
        floored = [r['record_id'] for r in closure['rows'] if r['adsorption_floor_applied']]
        self.assertEqual(floored, ['15', '16', '17', '18'])
        self.assertFalse(report['all_points_match'])  # Raw mismatch remains visible.
        self.assertFalse(report['production_quantitative_import_enabled'])

    def test_closure_does_not_consume_expected_uptake_or_change_raw_balance(self):
        raw = reconstruct(self.experiment)['rows']
        original = copy.deepcopy(raw)
        stripped = [{k: row[k] for k in ('phase', 'relative_pressure', 'candidate_cm3_g')} for row in raw]
        first = closure_hypothesis(stripped)
        altered = copy.deepcopy(self.reference)
        altered['data']['loading'][40] += 1
        report = audit_pair(self.experiment, altered)
        second = report['closure_hypothesis']
        self.assertEqual([r['closure_candidate_cm3_g'] for r in first['rows']],
                         [r['closure_candidate_cm3_g'] for r in second['rows']])
        self.assertFalse(second['all_points_match'])
        self.assertEqual(raw, original)

    def test_threshold_is_bounded_but_not_uniquely_identified(self):
        rows = reconstruct(self.experiment)['rows']
        at_eight = closure_hypothesis(rows, .8)
        at_eight_two = closure_hypothesis(rows, .82)
        self.assertEqual(at_eight['rows'], at_eight_two['rows'])
        at_seven_five = closure_hypothesis(rows, .75)
        self.assertNotEqual(at_eight['rows'], at_seven_five['rows'])
        report = audit_pair(self.experiment, self.reference)
        limits = report['closure_hypothesis']['discovery_set_threshold_interval']
        self.assertGreater(.8, limits['lower_exclusive'])
        self.assertLessEqual(.8, limits['upper_inclusive'])

    def test_closure_rejects_extrapolation_and_unobserved_gap_direction(self):
        rows = reconstruct(self.experiment)['rows']
        for value in (-1, 0, 1, float('nan')):
            with self.subTest(threshold=value), self.assertRaises(ValueError):
                closure_hypothesis(rows, value)
        changed = copy.deepcopy(rows)
        changed[-1]['relative_pressure'] = 0.000001
        with self.assertRaisesRegex(ValueError, 'extrapolation'):
            closure_hypothesis(changed)
        changed = copy.deepcopy(rows)
        changed[-1]['candidate_cm3_g'] = 1e6
        with self.assertRaisesRegex(ValueError, 'Negative closure'):
            closure_hypothesis(changed)

    def test_bet_hypothesis_matches_independent_export_summary(self):
        fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
        expected = fixture['bet_report']
        report = bet_hypothesis(reconstruct(self.experiment)['rows'], expected['fit_point_ids'])
        for field in ('vm_cm3_g', 'c', 'surface_area_m2_g'):
            with self.subTest(field=field):
                self.assertLessEqual(abs(report[field] - expected[field]), .000005)
        # An extra boundary point materially changes the fit; selection is explicit.
        different = bet_hypothesis(reconstruct(self.experiment)['rows'], list(range(1, 8)))
        self.assertGreater(abs(different['surface_area_m2_g'] - expected['surface_area_m2_g']), .2)

    def test_consistency_diagnostic_detects_monolayer_outside_fit_range(self):
        # Exact BET isotherms: perfect regression alone cannot validate the range.
        for c, expected in ((100, True), (2.24, False)):
            rows = [{'phase': 'adsorption', 'measurement': i,
                     'relative_pressure': x,
                     'candidate_cm3_g': 27 * c * x / ((1 - x) * (1 + (c - 1) * x))}
                    for i, x in enumerate((.05, .10, .15, .20), 1)]
            fit = bet_hypothesis(rows, [1, 2, 3, 4])
            report = bet_diagnostics(rows, fit)
            self.assertAlmostEqual(fit['vm_cm3_g'], 27, places=10)
            self.assertEqual(report['monolayer_quantity_in_selected_range'], expected)
            self.assertEqual(report['three_checks_pass'], expected)

    def test_pairing_does_not_accept_reordered_or_unrelated_results(self):
        for key, changed in [('pressure', 1), ('time_point', '00:00:00'),
                             ('measurement', 2), ('branch', 1), ('pressure_relative', 0.2)]:
            reference = copy.deepcopy(self.reference)
            reference['data'][key][0] = changed
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'mapping'):
                audit_pair(self.experiment, reference)
        reference = copy.deepcopy(self.reference)
        reference['meta']['sample'] = 'different test'
        with self.assertRaisesRegex(ValueError, 'metadata'):
            audit_pair(self.experiment, reference)

    def test_branch_reference_and_calibration_guards(self):
        experiment = copy.deepcopy(self.experiment)
        next(r for r in experiment['records'] if r['phase'] == 'desorption')['Pcd'] = '100'
        with self.assertRaisesRegex(ValueError, 'endpoint'):
            reconstruct(experiment)
        experiment = copy.deepcopy(self.experiment)
        experiment['q_records'].append(experiment['q_records'][-1])
        with self.assertRaisesRegex(ValueError, 'one helium'):
            reconstruct(experiment)

    def test_application_reads_all_doses_but_only_emits_balanced_pressure_points(self):
        e = self.experiment
        root = ET.Element('doc', Ver='1')
        general = ET.SubElement(ET.SubElement(root, 'General', Ver='1'), 'GeneralConfig')
        ET.SubElement(general, 'AdsorbGas').text = 'N2'
        ET.SubElement(general, 'AdsorbTemp').text = '77,35'
        station = ET.SubElement(ET.SubElement(root, 'StationConfig', Ver='1'), 'Station')
        ET.SubElement(station, 'Model').text = e['model']
        config = ET.SubElement(root, 'Config', Ver='1')
        ET.SubElement(ET.SubElement(config, 'Base'), 'PressUnits').text = '0'
        sample = ET.SubElement(config, 'SampleInfo')
        ET.SubElement(sample, 'SampleName').text = e['sample']
        ET.SubElement(sample, 'SampleWeight').text = str(e['mass_g'])
        data = ET.SubElement(root, 'Data', Ver='1')
        info = ET.SubElement(data, 'BaseInfo')
        ET.SubElement(info, 'StartTime').text = e['start']
        ET.SubElement(info, 'EndTime').text = e['end']
        branches = {phase: ET.SubElement(data, tag) for phase, tag in
                    [('adsorption', 'Adsorb'), ('desorption', 'Doff')]}
        for row in e['records']:
            ET.SubElement(branches[row['phase']], 'Item', {k: v for k, v in row.items() if k != 'phase'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paired.jwgbt'
            path.write_bytes(ET.tostring(root, encoding='utf-8'))
            result = load_file(path)
        self.assertEqual(result.point_count, 49)  # App omits duplicated branch reference.
        self.assertEqual(len(result.method_options['jwgb_native_pressure_records']), 76)
        self.assertEqual(result.isotherm[0].index, 1)
        self.assertAlmostEqual(result.isotherm[0].absolute_pressure_mmHg * 101.325 / 760,
                               self.reference['data']['pressure'][0], places=8)
        self.assertTrue(all(p.quantity_adsorbed_cm3_g_stp is None for p in result.isotherm))


if __name__ == '__main__':
    unittest.main()
