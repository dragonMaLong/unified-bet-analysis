import json
from pathlib import Path
import unittest

from scripts.jwgb_project_probe import audit_q, audit_uptake_hypothesis
from tristar_bet.jwgb_project import decode_jwgb_project


def encoded(xml):
    return bytes(character - index % 48 for index, character in enumerate(xml.hex().encode('ascii')))


class JwgbProjectTests(unittest.TestCase):
    def test_observed_header_and_complete_xml(self):
        xml = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               b'<doc Ver="1"><Config Ver="1"/><Data Ver="1"/></doc>\n')
        data = encoded(xml)
        # Fixed prefix independently copied from the public Pan fiber file.
        self.assertEqual(data[:16], bytes.fromhex('336231633333305d2e5a28252b292826'))
        result = decode_jwgb_project(data)
        self.assertEqual(result.xml, xml)
        self.assertEqual(result.root.tag, 'doc')
        self.assertEqual(result.source_bytes, len(data))

    def test_rejects_corruption_truncation_and_unsafe_xml(self):
        valid = b'<doc Ver="1"><Config Ver="1"/><Data Ver="1"/></doc>'
        values = [b'', encoded(valid)[:-1], encoded(valid)[:-4],
                  b'\xff' + encoded(valid)[1:], encoded(valid + b'garbage'),
                  encoded(b'<!DOCTYPE doc [<!ENTITY x "test">]>' + valid),
                  encoded(valid.replace(b'Ver="1"', b'Ver="2"', 1)),
                  encoded(b'<doc Ver="1"/>'), encoded(b'\xff' + valid)]
        for value in values:
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                decode_jwgb_project(value)

    def test_public_calibration_evidence_distinguishes_temperature_models(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/jwgb_public_q_calibration.json').read_text(encoding='utf-8'))
        self.assertEqual(len(fixture['files']), 5)
        matches = dict.fromkeys(('separate', 'before', 'after', 'mean'), 0)
        for sample in fixture['files']:
            self.assertEqual(sample['software_version'], '10.06.00.72')
            report = audit_q(sample['q_records'], sample['stored_q'])
            for model, result in report.items():
                matches[model] += result['within_storage_rounding']
        self.assertEqual(matches, {'separate': 5, 'before': 4, 'after': 4, 'mean': 4})

    def test_q_audit_rejects_nonpositive_calibration_and_unknown_precision(self):
        rows = [{'Pd': '-0.00001', 'Pcd': '0'},
                {'Pd': '60', 'Pcd': '30', 'PdT': '25', 'PcdT': '25'}]
        for q in ('NaN', '0.01', '-0.010000', '0.000000'):
            with self.subTest(q=q), self.assertRaises(ValueError):
                audit_q(rows, q)
        rows[1]['Pcd'] = '0'
        with self.assertRaises(ValueError):
            audit_q(rows, '0.010000')

    def test_public_uptake_counterexamples_are_not_promoted_to_validated_results(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/jwgb_public_uptake_cases.json').read_text(encoding='utf-8'))
        full, first_dose = [audit_uptake_hypothesis(**{
            key: case[key] for key in ('records', 'vd', 'q', 'mass', 'bet_low', 'bet_high')
        }) for case in fixture['cases']]
        self.assertEqual(full['candidate_bet']['status'], 'nonphysical_bet_fit')
        self.assertIsNone(full['candidate_bet']['surface_area_m2_g'])
        self.assertFalse(full['quantitative_validation_passed'])
        self.assertEqual(first_dose['negative_adsorption_points'], 1)
        self.assertAlmostEqual(first_dose['minimum_candidate_uptake'], -0.29200148160738615)
        self.assertFalse(first_dose['quantitative_validation_passed'])


if __name__ == '__main__':
    unittest.main()
