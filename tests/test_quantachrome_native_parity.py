"""Portable regression: 26 engines, 52 synthetic + 20 real-input references."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from tristar_bet.analysis import dft_pore_distribution
from tristar_bet.dft_models import GAI_MODEL_SPECS,load_dft_model_kernel
from tristar_bet.quantachrome_dft import VALIDATED_GAI_MODELS,REPORT_VALIDATED_GAI_MODELS,prepare_gai

class NativeParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=np.load(Path(__file__).parent/'fixtures/quantachrome_native_reference.npz',allow_pickle=False)
        cls.cases=json.loads(str(cls.fixture['metadata']))['cases']

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_validation_coverage_is_explicit(self):
        self.assertEqual(len(VALIDATED_GAI_MODELS),26)
        self.assertEqual(len(REPORT_VALIDATED_GAI_MODELS),13)
        self.assertEqual(len(self.cases),72)
        self.assertEqual({c['model'] for c in self.cases},set(VALIDATED_GAI_MODELS))

    def test_all_native_references_through_production(self):
        for case in self.cases:
            with self.subTest(model=case['model'],case=case['key'],kind=case['kind']):
                points=self.fixture[case['key']+'_points'];ref=self.fixture[case['key']+'_reference']
                # Equilibrium selection was separately verified; this fixture
                # is the exact prepared branch passed to the native executable.
                sample=SimpleNamespace(isotherm=[SimpleNamespace(index=i+1,phase='adsorption',
                    relative_pressure=float(p),quantity_adsorbed_cm3_g_stp=float(q)*22.414,
                    quantity_adsorbed_mmol_g=float(q)) for i,(p,q) in enumerate(points)],
                    adsorptive_properties=None,method_options={})
                with patch('tristar_bet.analysis._dft_regularized_nonnegative_solution',
                           side_effect=AssertionError('legacy solver used')):
                    result=dft_pore_distribution(sample,model=case['model'],include_diagnostics=False)
                self.assertTrue(result.ok,result.status)
                self.assertEqual(result.solver_profile,'gai_native')
                self.assertTrue(result.automatic_regularization)
                self.assertEqual(len(result.rows),len(ref))
                w=np.array([r['pore_width_nm'] for r in result.rows])
                np.testing.assert_allclose(w,ref[:,0],atol=1.1e-5 if case['kind']=='user_csv' else 1e-9,rtol=0)
                for field,column in [('cumulative_pore_volume_cm3_g',1),('differential_pore_volume_cm3_g',2)]:
                    values=np.array([r[field] for r in result.rows])
                    self.assertTrue(np.all(np.isfinite(values)))
                    self.assertTrue(np.all(values>=0))
                    self.assertLess(np.linalg.norm(values-ref[:,column])/np.linalg.norm(ref[:,column]),1e-4)

    def test_special_profiles_and_manual_order(self):
        for mid in ['QK005','QK008','QK016','QK026']:
            spec=next(s for s in GAI_MODEL_SPECS if s.model_id==mid)
            case=next(c for c in self.cases if c['model']==spec.key)
            problem=prepare_gai(load_dft_model_kernel(spec.key),self.fixture[case['key']+'_points'])
            if mid=='QK005':np.testing.assert_array_equal(problem.volume_per_area,1)
            if mid in ['QK008','QK026']:
                self.assertLess(problem.report_limits[0],.35)
                self.assertEqual(problem.report_limits[1],1.5)
            if mid=='QK016':
                self.assertEqual(problem.pressure[-1],float(np.float32(.99)))
            order=problem.regularization_order;penalty=problem.penalty.copy()
            a,_,_=problem.solve(.001);b,_,_=problem.solve(.01)
            self.assertEqual(problem.regularization_order,order)
            np.testing.assert_array_equal(problem.penalty,penalty)
            self.assertGreater(np.linalg.norm(a-b),0)

if __name__=='__main__':unittest.main()
