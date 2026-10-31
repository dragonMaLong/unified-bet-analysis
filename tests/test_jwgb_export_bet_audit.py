import copy
import json
from pathlib import Path
import unittest

from scripts.jwgb_export_bet_audit import _table, audit_export


FIXTURE = Path(__file__).parent / 'fixtures' / 'jwgb_local_export_bet.json'


class ExportBetAuditTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(FIXTURE.read_text(encoding='utf-8'))['cases']

    def test_three_independent_export_summaries_match_at_report_precision(self):
        for case in self.cases:
            with self.subTest(file=case['source_file']):
                report = audit_export(case)
                self.assertTrue(report['matches_report_rounding'])
                self.assertFalse(report['raw_reconstruction_validated'])
                self.assertLess(abs(report['area_error_m2_g']), .0005)
                # Two wrong-but-plausible calculation paths are distinguishable.
                self.assertGreater(abs(report['controls']['modern_constants_error']), .005)
                self.assertGreater(abs(report['controls']['fit_using_rounded_bet_table_error']), .0005)

    def test_fit_does_not_consume_report_area(self):
        case = copy.deepcopy(self.cases[0])
        first = audit_export(case)
        case['reported_area_m2_g'] += 100
        second = audit_export(case)
        self.assertEqual(first['fit'], second['fit'])
        self.assertFalse(second['matches_report_rounding'])

    def test_mismatched_bet_table_and_duplicate_ids_are_rejected(self):
        for mode in ('missing', 'pressure', 'uptake', 'duplicate'):
            case = copy.deepcopy(self.cases[0])
            if mode == 'missing':
                case['bet_table_rows'][0]['measurement'] = 999
            elif mode == 'pressure':
                case['bet_table_rows'][0]['relative_pressure'] += .01
            elif mode == 'uptake':
                case['bet_table_rows'][0]['candidate_cm3_g'] += 1
            else:
                case['adsorption_rows'].append(case['adsorption_rows'][0])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                audit_export(case)

    def test_table_locale_and_branch_separator(self):
        class Sheet:
            min_row = 1
            title = 'Isotherm'
            rows = [('sample', None, None), ('ID', 'p/p0', 'V (cm³/g STP)'),
                    (1, '0,1', '10,2'), ('---', '---', '---'), (1, '.1', '99')]

            def iter_rows(self, *, min_row=1, values_only=True):
                return iter(self.rows[min_row - 1:])

        sheet = Sheet()
        parsed = list(_table(sheet, ('ID', 'p/p0', 'V (cm3/g STP)')))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['candidate_cm3_g'], 10.2)
        sheet.rows = sheet.rows[:2] + [(1, 'bad', '10.2')]
        with self.assertRaisesRegex(ValueError, 'Malformed'):
            list(_table(sheet, ('ID', 'p/p0', 'V (cm3/g STP)')))


if __name__ == '__main__':
    unittest.main()
