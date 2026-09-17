"""Correctness safeguards for speed optimizations (no timing assertions)."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import numpy as np
from tristar_bet.halsey_dft import nonnegative_least_squares
from tristar_bet.quantachrome_dft import prepare_gai
from tristar_bet.dft_models import load_dft_model_kernel
from tristar_bet.dft_batch import DftBatchCalculator, _calculate


def reference_job():
    with np.load(Path(__file__).parent/'fixtures/quantachrome_native_reference.npz',allow_pickle=False) as f:
        case=next(c for c in json.loads(str(f['metadata']))['cases'] if c['kind']=='user_csv')
        points=f[case['key']+'_points'].copy()
    sample=SimpleNamespace(isotherm=[SimpleNamespace(index=i+1,phase='adsorption',
        relative_pressure=float(p),quantity_adsorbed_cm3_g_stp=float(q)*22.414,
        quantity_adsorbed_mmol_g=float(q)) for i,(p,q) in enumerate(points)],
        adsorptive_properties=None,method_options={})
    return sample,dict(model=case['model'],include_diagnostics=True)


class DftPerformanceTests(unittest.TestCase):
    def test_warm_start_resolves_stationarity_and_kkt(self):
        rng=np.random.default_rng(1729)
        for rank_deficient in [False,True]:
            for n in [3,12,25]:
                a=rng.normal(size=(2*n,n));b=rng.normal(size=2*n)
                if rank_deficient:a[:,-1]=a[:,0]
                seed=np.abs(rng.normal(size=n))
                original=seed.copy()
                cold=nonnegative_least_squares(a,b)
                warm=nonnegative_least_squares(a,b,initial=seed)
                np.testing.assert_array_equal(seed,original)
                self.assertTrue(np.all(warm>=0))
                np.testing.assert_allclose(a@warm,a@cold,atol=1e-10,rtol=1e-10)
                gradient=a.T@(a@warm-b)
                self.assertGreaterEqual(gradient.min(),-1e-9)
                self.assertLess(np.max(np.abs(warm*gradient)),1e-9)
        # All entries initially active does not imply convergence.
        np.testing.assert_allclose(nonnegative_least_squares(np.eye(3),np.array([2.,-1.,4.]),
            initial=np.ones(3)),[2.,0.,4.])

    def test_invalid_warm_start_rejected(self):
        for seed in [np.ones(2),np.array([-1.,0.,1.]),np.array([np.nan,0.,1.])]:
            with self.assertRaises(ValueError):
                nonnegative_least_squares(np.eye(3),np.ones(3),initial=seed)

    def test_warm_scan_preserves_all_candidates(self):
        sample,options=reference_job()
        points=np.array([[p.relative_pressure,p.quantity_adsorbed_mmol_g] for p in sample.isotherm])
        problem=prepare_gai(load_dft_model_kernel(options['model']),points)
        calls=[]
        original=np.linalg.lstsq
        def counted(*args,**kwargs):
            calls.append(1)
            return original(*args,**kwargs)
        with patch('numpy.linalg.lstsq',side_effect=counted):
            cold=problem.solve(solver=lambda a,b:nonnegative_least_squares(a,b))
            cold_count=len(calls);calls.clear()
            warm=problem.solve();warm_count=len(calls)
        self.assertLess(warm_count,cold_count)
        self.assertEqual(cold[1],warm[1])
        np.testing.assert_allclose(cold[0],warm[0],rtol=1e-8,atol=1e-10)
        self.assertEqual(len(warm[2]),21)
        np.testing.assert_allclose([r['selection_score'] for r in warm[2]],
                                   [r['selection_score'] for r in cold[2]],rtol=1e-8,atol=1e-12)

    def test_real_process_pool_order_and_reuse(self):
        sample,options=reference_job()
        jobs=[(sample,dict(options,automatic_regularization=False,regularization=a))
              for a in [.001,.003,.01,.03]]
        serial=list(map(_calculate,jobs))
        pool=DftBatchCalculator(max_workers=2)
        try:
            for _ in range(2):
                actual=pool.calculate(jobs)
                self.assertIsNone(pool.last_error)
                for expected,result in zip(serial,actual):
                    self.assertTrue(result.ok)
                    self.assertEqual(result.regularization,expected.regularization)
                    self.assertEqual(result.rows,expected.rows)
        finally:pool.close()
        self.assertIsNone(pool._pool)

    def test_warm_failure_retries_established_cold_solver(self):
        sample,options=reference_job()
        points=np.array([[p.relative_pressure,p.quantity_adsorbed_mmol_g] for p in sample.isotherm])
        problem=prepare_gai(load_dft_model_kernel(options['model']),points)
        expected=problem.solve()
        retried=[]
        def fail_warm(a,b,*,initial=None):
            if initial is not None:
                retried.append(1)
                raise RuntimeError('simulated warm cycling')
            return nonnegative_least_squares(a,b)
        with patch('tristar_bet.quantachrome_dft.nonnegative_least_squares',fail_warm):
            actual=problem.solve(solver=fail_warm)
        self.assertEqual(len(retried),20)
        self.assertEqual(actual[1],expected[1])
        np.testing.assert_allclose(actual[0],expected[0],rtol=1e-8,atol=1e-10)

    def test_worker_failure_does_not_publish_partial_results(self):
        pool=DftBatchCalculator()
        pool._pool=MagicMock()
        pool._pool.map.side_effect=RuntimeError('test worker failed')
        self.assertEqual(pool.calculate([reference_job()]*4),[None]*4)
        self.assertIn('test worker failed',pool.last_error)
        self.assertIsNone(pool._pool)

    def test_ui_batch_cache_visibility_and_no_repeat_work(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtWidgets
        from tristar_bet.ui.main_window import MainWindow
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window=MainWindow()
        samples=[reference_job()[0] for _ in range(10)]
        options=reference_job()[1]
        settings=window._default_dft_settings()
        settings.update(model=options['model'],automatic_regularization=True)
        window.results=samples
        window.visible_results=[True]*8+[False]*2
        window.active_index=0
        marker=SimpleNamespace(ok=True)
        try:
            with patch('os.cpu_count',return_value=8), \
                 patch.object(window,'_active_pore_volume_method',return_value='bjh'), \
                 patch.object(window,'_dft_settings_for_result',return_value=settings), \
                 patch('tristar_bet.dft_batch.DftBatchCalculator') as factory:
                worker=factory.return_value
                worker.calculate.side_effect=lambda jobs:[marker]*len(jobs)
                window._prefetch_dft_batch()
                self.assertEqual(len(worker.calculate.call_args.args[0]),8)
                self.assertEqual(len(window._dft_result_cache),8)
                window._prefetch_dft_batch()
                self.assertEqual(worker.calculate.call_count,1)
                cached=window._cached_dft_result(samples[0],analysis_type=settings['analysis_type'],
                    geometry=settings['geometry'],model=settings['model'],regularization=.3)
                self.assertIs(cached,marker)
                # A new coefficient in auto mode is not a new physical problem.
                settings['regularization']=.01
                window._prefetch_dft_batch()
                self.assertEqual(worker.calculate.call_count,1)
                settings['automatic_regularization']=False
                window._prefetch_dft_batch()
                self.assertEqual(worker.calculate.call_count,2)
                self.assertTrue(all(not options['automatic_regularization']
                    for _,options in worker.calculate.call_args.args[0]))
        finally:
            window.results=[]
            window.close()


if __name__=='__main__':unittest.main()
