"""Desorption closure and dose non-ideality, pinned to two vendor same-run pairs.

BK200C sample 1 + 1(1).xlsx and Sync440A CBV712 + public JSON export. Both are
discovery data: the 'floor at every pressure' part of the rule was adopted
after the old Sync-only rule failed two BK points (kept below as a control).
"""
import copy
import json
from pathlib import Path
import unittest

from tristar_bet.jwgb_reconstruction import close_desorption, reconstruct

FIXTURES = Path(__file__).parent / 'fixtures'
HALF_UNIT = 0.000005 + 1e-10


def bk_rows(key='candidate_nonideal_off_cm3_g'):
    data = json.loads((FIXTURES / 'jwgb_bk200c_sample1_vendor_pair.json').read_text(encoding='utf-8'))
    rows = [{'phase': r['phase'], 'is_reference': r['is_reference'], 'record_id': r['native_id'],
             'relative_pressure': r['relative_pressure'], 'candidate_cm3_g': r[key]} for r in data['rows']]
    return rows, [r['export_cm3_g'] for r in data['rows']]


def matches(rows, expected):
    return sum(abs(r['candidate_cm3_g'] - e) <= HALF_UNIT for r, e in zip(rows, expected))


class BK200CVendorPairTests(unittest.TestCase):
    def test_adsorption_matches_only_without_nonideality(self):
        off, expected = bk_rows()
        on, _ = bk_rows('candidate_nonideal_on_cm3_g')
        ads = [i for i, r in enumerate(off) if r['phase'] == 'adsorption']
        self.assertEqual(len(ads), 21)
        self.assertEqual(sum(abs(off[i]['candidate_cm3_g'] - expected[i]) <= HALF_UNIT for i in ads), 21)
        self.assertEqual(sum(abs(on[i]['candidate_cm3_g'] - expected[i]) <= HALF_UNIT for i in ads), 0)

    def test_closure_reproduces_all_38_export_points(self):
        rows, expected = bk_rows()
        self.assertEqual(matches(rows, expected), 22)  # 21 adsorption + peak reference; 0/16 desorption
        closed, info = close_desorption(rows)
        self.assertTrue(info['applied'])
        self.assertEqual(matches(closed, expected), 38)

    def test_old_sync_only_rule_without_high_pressure_floor_fails_two_points(self):
        rows, expected = bk_rows()
        closed, _ = close_desorption(rows)
        old = [dict(c, candidate_cm3_g=r['candidate_cm3_g'])
               if c['phase'] == 'desorption' and c['relative_pressure'] >= .8 else c
               for c, r in zip(closed, rows)]
        self.assertEqual(matches(old, expected), 36)


class Sync440PairTests(unittest.TestCase):
    def test_same_closure_reproduces_all_50_sync_points(self):
        fixture = json.loads((FIXTURES / 'jwgb_sync440_paired.json').read_text(encoding='utf-8'))
        expected = [float(str(v).replace(',', '.')) for v in fixture['reference']['data']['loading']]
        rows = reconstruct(fixture['experiment'])['rows']  # Sync: non-ideality ON matches
        closed, info = close_desorption(rows)
        self.assertTrue(info['applied'])
        self.assertGreater(info['shifted_points'], 0)
        self.assertEqual(len(closed), 50)
        self.assertEqual(matches(closed, expected), 50)


class ClosureRefusalTests(unittest.TestCase):
    def test_uncovered_inputs_are_returned_unchanged(self):
        rows, _ = bk_rows()
        negative = copy.deepcopy(rows)
        negative[-1]['candidate_cm3_g'] += 5
        below = copy.deepcopy(rows)
        below[-1]['relative_pressure'] = 0.001
        no_ref = [r for r in copy.deepcopy(rows) if not r['is_reference']]
        for case, reason in ((negative, 'negative_gap_not_observed'),
                             (below, 'desorption_outside_adsorption_range'),
                             (no_ref, 'incomplete_branches')):
            out, info = close_desorption(case)
            self.assertFalse(info['applied'])
            self.assertEqual(info['reason'], reason)
            self.assertEqual(out, case)


if __name__ == '__main__':
    unittest.main()
