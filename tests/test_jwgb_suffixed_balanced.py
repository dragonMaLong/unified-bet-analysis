import json
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from tristar_bet import load_file
from tristar_bet.jwgb_native import JwgbNativeParseError


class SuffixedBalancedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.jwgbt'
        fixture = json.loads((Path(__file__).parent / 'fixtures/jwgb_bk200c_suffixed_balanced.json').read_text(encoding='utf-8'))
        self.xml = fixture['xml']
        self.path.write_text(self.xml, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_both_balanced_records_are_retained_and_all_doses_accumulate(self):
        result = load_file(self.path)
        self.assertEqual(result.point_count, 38)
        self.assertEqual(result.method_options['quantity_status'], 'reconstructed_unvalidated')
        ads = [p for p in result.isotherm if p.phase == 'adsorption']
        self.assertEqual(len(ads), 22)
        self.assertEqual(len({p.index for p in ads}), 22)
        self.assertGreater(ads[-2].relative_pressure, 1)
        self.assertLess(ads[-1].relative_pressure, 1)
        sources = result.method_options['jwgb_native_point_sources']
        self.assertEqual([s['record_id'] for s in sources[20:22]], ['21_1', '21_2'])
        self.assertEqual([s['index'] for s in sources[20:22]], [21, 22])
        root = ET.fromstring(self.xml)
        total = 0.
        previous = None
        quantity = {}
        q = result.method_options['jwgb_reconstruction_q']
        mass = result.sample.sample_mass_g
        # Independent signed-dose sum, including BOTH suffixed records and the
        # subsequent desorption branch. Dropping either dose changes this check.
        for branch in ('Adsorb', 'Doff'):
            for row in root.findall(f'Data/{branch}/Item'):
                pd, pc = float(row.get('Pd')), float(row.get('Pcd'))
                if pd == -.00001:
                    previous = pc
                    continue
                dose = (273.15 / 101.325) * (pd/(float(row.get('PdT'))+273.15)-pc/(float(row.get('PcdT'))+273.15))
                # No dose non-ideality multiplier: BK200C same-run export match.
                total += 30 * (dose-q*(pc-previous))
                previous = pc
                quantity[(branch, row.get('ID'))] = total/mass
        raw = {(r['phase'], r['record_id']): r.get('raw_candidate_cm3_g', r['candidate_cm3_g'])
               for r in result.method_options['jwgb_reconstruction_rows']}
        for point, source in zip(result.isotherm, sources):
            branch = 'Adsorb' if point.phase == 'adsorption' else 'Doff'
            expected = quantity[(branch, source['record_id'])]
            if point.phase == 'adsorption':
                self.assertAlmostEqual(point.quantity_adsorbed_cm3_g_stp, expected, places=10)
            else:
                # Desorption is closed onto adsorption afterwards; the signed
                # dose sum is checked on the pre-closure value.
                self.assertAlmostEqual(raw[(point.phase, source['record_id'])], expected, places=10)
                self.assertGreaterEqual(point.quantity_adsorbed_cm3_g_stp, expected - 1e-12)

    def test_inconsistent_nonzero_subindex_still_rejected(self):
        root = ET.fromstring(self.xml)
        root.find("Data/Adsorb/Item[@ID='21_1']").set('SubIndex', '2')
        self.path.write_bytes(ET.tostring(root, encoding='utf-8'))
        with self.assertRaisesRegex(JwgbNativeParseError, 'SubIndex'):
            load_file(self.path)

    def test_duplicate_full_ids_still_rejected(self):
        root = ET.fromstring(self.xml)
        root.find("Data/Adsorb/Item[@ID='21_2']").set('ID', '21_1')
        self.path.write_bytes(ET.tostring(root, encoding='utf-8'))
        with self.assertRaisesRegex(JwgbNativeParseError, '重复 ID'):
            load_file(self.path)
