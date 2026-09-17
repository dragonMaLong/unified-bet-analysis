"""Regression against six ASiQwin exports, with no installed vendor software.

Input points and reference numbers are separate fixture sections. Production
code may read only the input isotherm and bundled GAI kernel, never references.
"""
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tristar_bet.analysis import dft_pore_distribution
from tristar_bet.dft_models import GAI_MODEL_SPECS, load_dft_model_kernel, _read_model_file, _parse_model_file
from tristar_bet.quantachrome_dft import (
    VALIDATED_GAI_MODELS, central_derivative, merge_equilibrium_branches, prepare_gai,
)


class QuantachromeDftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((Path(__file__).parent/'fixtures/quantachrome_dft_reference.json').read_text(encoding='utf-8'))

    def sample(self, name='MCM41', scale=1.):
        points=[]
        for phase in ['adsorption','desorption']:
            for p,q in self.data['samples'][name][phase]:
                points.append(SimpleNamespace(index=len(points)+1,phase=phase,relative_pressure=p,
                    quantity_adsorbed_cm3_g_stp=q*22.414*scale,quantity_adsorbed_mmol_g=q*scale))
        return SimpleNamespace(isotherm=points,adsorptive_properties=None,method_options={})

    def test_six_production_profiles_match_blind_reference_curves(self):
        for ref in self.data['references']:
            with self.subTest(model=ref['model']):
                with patch('tristar_bet.analysis._dft_regularized_nonnegative_solution',
                           side_effect=AssertionError('legacy solver called')):
                    result=dft_pore_distribution(self.sample(ref['sample']),model=ref['model'])
                self.assertTrue(result.ok,result.status)
                self.assertEqual(result.solver_profile,'gai_native')
                self.assertTrue(result.automatic_regularization)
                table=np.array(ref['table'])
                self.assertEqual(len(result.rows),len(table))
                widths=np.array([r['pore_width_nm'] for r in result.rows])
                np.testing.assert_allclose(widths,table[:,0],atol=5.1e-5,rtol=0)
                for field,column in [('differential_pore_volume_cm3_g',3),('cumulative_pore_volume_cm3_g',1)]:
                    calculated=np.array([r[field] for r in result.rows])
                    error=np.linalg.norm(calculated-table[:,column])/np.linalg.norm(table[:,column])
                    self.assertLess(error,1e-4)  # 0.01% whole-curve relative L2
                    self.assertTrue(np.all(calculated>=0))
                self.assertEqual(len(result.diagnostic_rows),21)
                chosen=min(result.diagnostic_rows,key=lambda r:r['selection_score'])
                self.assertEqual(chosen['regularization'],result.regularization)
                rms=np.sqrt(np.mean([(r['quantity_adsorbed_mmol_g']-r['model_quantity_adsorbed_mmol_g'])**2
                                     for r in result.fit_rows]))
                self.assertAlmostEqual(rms,chosen['rms_error_mmol_g'],places=12)

    def test_model_metadata_records_real_operator_and_geometry(self):
        second_order=[]
        for spec in GAI_MODEL_SPECS:
            k=load_dft_model_kernel(spec.key)
            self.assertIsNotNone(k.gai,spec.key)
            self.assertEqual(len(k.gai.transition_pressures),len(k.pore_widths_nm))
            if k.gai.option('S')==1:
                second_order.append(spec.model_id)
            self.assertTrue(np.all(np.isin(k.gai.geometry_divisors,[2.,4.,6.])))
        self.assertEqual(len(second_order),11)
        mixed=load_dft_model_kernel('n2_77_carbon_slit_cylinder_sphere_qsdft_ads')
        self.assertEqual(set(mixed.gai.geometry_divisors),{2.,4.,6.})

    def test_report_crop_does_not_remove_unreported_cumulative_volume(self):
        k=load_dft_model_kernel('n2_77_carbon_slit_qsdft_eq')
        sample=self.data['samples']['60115DML-1']
        p=prepare_gai(k,merge_equilibrium_branches(np.array(sample['adsorption']),np.array(sample['desorption'])))
        self.assertLess(p.widths[0],p.report_limits[0])
        x=np.zeros(len(p.widths));x[0]=10
        rows=p.distribution(x,.001,'equilibrium')
        self.assertGreater(rows[0]['cumulative_pore_volume_cm3_g'],0)
        self.assertEqual(rows[0]['incremental_pore_volume_cm3_g'],0)
        self.assertTrue(all(r['differential_pore_volume_cm3_g']==0 for r in rows))

    def test_qsdft_uses_stored_stride_and_second_difference_boundary_rows(self):
        k=load_dft_model_kernel('n2_77_carbon_slit_qsdft_eq')
        p=prepare_gai(k,np.array(self.data['samples']['60115DML-1']['adsorption']))
        self.assertEqual(p.regularization_order,2)
        np.testing.assert_array_equal(p.penalty[0,:3],[-2,1,0])
        np.testing.assert_array_equal(p.penalty[-1,-3:],[0,1,-2])
        self.assertTrue(all(any(abs(w-k.pore_widths_nm[1::2])<1e-12) for w in p.widths))

    def test_equilibrium_uses_low_adsorption_and_keeps_first_true_desorption(self):
        ads=np.array([[.01,1],[.03,2],[.2,3],[.98,10]])
        des=np.array([[.05,2.1],[.3,4],[.96,9]])
        merged=merge_equilibrium_branches(ads,des)
        np.testing.assert_array_equal(merged,np.vstack((ads[:2],des)))
        np.testing.assert_array_equal(merge_equilibrium_branches(ads,np.vstack((des,ads[-1]))),merged)
        np.testing.assert_array_equal(merge_equilibrium_branches(ads,np.empty((0,2))),ads)

    def test_manual_regularization_is_explicit_and_changes_solution(self):
        model='n2_77_silica_cylinder_nldft_ads'
        a=dft_pore_distribution(self.sample(),model=model,regularization=0,automatic_regularization=False,include_diagnostics=False)
        b=dft_pore_distribution(self.sample(),model=model,regularization=.01,automatic_regularization=False,include_diagnostics=False)
        self.assertTrue(a.ok and b.ok)
        self.assertFalse(a.automatic_regularization)
        self.assertEqual(a.regularization,0)
        self.assertEqual(b.regularization,.01)
        self.assertNotEqual(a.rows,b.rows)

    def test_invalid_input_and_solver_failure_do_not_silently_fall_back(self):
        model='n2_77_silica_cylinder_nldft_ads'
        with patch('tristar_bet.quantachrome_dft.GaiProblem.solve',side_effect=RuntimeError('no convergence')):
            result=dft_pore_distribution(self.sample(),model=model)
            self.assertEqual(result.status,'solver_not_converged')
            self.assertFalse(result.rows)
        kernel=load_dft_model_kernel(model)
        for points in [np.array([[.2,1]]*6),np.array([[1e-12,1],[2e-12,2],[3e-12,3]])]:
            with self.assertRaises(ValueError):
                prepare_gai(kernel,points)

    def test_central_derivative_and_endpoint_conventions(self):
        x=np.array([1.,2.,4.,8.]);v=np.array([5.,6.,10.,12.])
        np.testing.assert_allclose(central_derivative(x,v),[1.,5/3,1.,.5])

    def test_truncated_gai_tail_does_not_destroy_primary_matrix_or_invent_metadata(self):
        k=load_dft_model_kernel('n2_77_silica_cylinder_nldft_ads')
        raw=_read_model_file(k.spec.file_name)
        truncated=_parse_model_file(k.spec,raw[:-1000])
        self.assertIsNotNone(truncated)
        self.assertIsNone(truncated.gai)
        np.testing.assert_array_equal(k.kernel,truncated.kernel)

    def test_unvalidated_models_do_not_use_new_solver(self):
        model='n2_nldft_carbon_slit'  # a non-GAI Micromeritics model
        self.assertNotIn(model,VALIDATED_GAI_MODELS)
        with patch('tristar_bet.quantachrome_dft.prepare_gai',side_effect=AssertionError('unvalidated profile')):
            result=dft_pore_distribution(self.sample(),model=model,include_diagnostics=False)
        self.assertEqual(result.solver_profile,'generic')
        self.assertTrue(result.ok)

    def test_ui_auto_mode_and_cache_preserve_per_sample_choice(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        os.environ.setdefault('PYQTGRAPH_QT_LIB','PyQt5')
        from PyQt5 import QtWidgets
        from tristar_bet.ui.main_window import MainWindow
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window=MainWindow()
        try:
            settings=window._default_dft_settings()
            settings.update(model='n2_77_silica_cylinder_nldft_ads',geometry='cylinder')
            window._apply_dft_settings(settings)
            window._sync_dft_controls_from_state()
            self.assertFalse(window.dft_auto_regularization_checkbox.isHidden())
            self.assertTrue(window.dft_regularization_slider.isEnabled())
            self.assertTrue(window._current_dft_settings_snapshot()['automatic_regularization'])
            sample=self.sample()
            kwargs=dict(analysis_type='dft_pore',geometry='cylinder',model=settings['model'],regularization=.316)
            first=window._cached_dft_result(sample,**kwargs)
            self.assertIs(first,window._cached_dft_result(sample,include_diagnostics=True,**kwargs))
            # Auto cache is independent of the displayed coefficient. Moving
            # the knob to the chosen value must not trigger another inversion.
            chosen_kwargs={**kwargs,'regularization':first.regularization}
            self.assertIs(first,window._cached_dft_result(sample,**chosen_kwargs))
            window.custom_dft_settings[id(sample)]=dict(settings)
            with patch.object(window,'active_result',return_value=sample):
                window._refresh_dft_diagnostics()
                self.assertEqual(window.dft_regularization_slider.value(),first.regularization)
                self.assertEqual(window._dft_settings_for_result(sample)['regularization'],first.regularization)
                self.assertTrue(window.dft_auto_regularization_checkbox.isChecked())
                self.assertTrue(window._dft_diagnostic_line.movable)
                with patch.object(window,'_refresh_dft_regularization_dependents'):
                    window.dft_regularization_slider.setValue(.003,emit=True)
                self.assertFalse(window.dft_auto_regularization_checkbox.isChecked())
                self.assertFalse(window._dft_settings_for_result(sample)['automatic_regularization'])
                self.assertEqual(window._dft_settings_for_result(sample)['regularization'],.003)
            settings['automatic_regularization']=False
            window.custom_dft_settings[id(sample)]=settings
            second=window._cached_dft_result(sample,**kwargs)
            self.assertIsNot(first,second)
            self.assertFalse(second.automatic_regularization)
            self.assertEqual(second.regularization,.316)
            window._apply_dft_settings(settings)
            window._sync_dft_controls_from_state()
            self.assertTrue(window.dft_regularization_slider.isEnabled())
        finally:
            window.close()

    def test_ui_auto_availability_defaults_and_checkbox_layout(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        os.environ.setdefault('PYQTGRAPH_QT_LIB','PyQt5')
        from PyQt5 import QtWidgets
        from tristar_bet.ui.main_window import MainWindow, RegularizationSlider
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window=MainWindow()
        try:
            checkbox=window.dft_auto_regularization_checkbox
            for spec in GAI_MODEL_SPECS:
                window.dft_model=spec.key
                window.dft_automatic_regularization=True
                window._sync_dft_auto_controls()
                supported=spec.key in VALIDATED_GAI_MODELS
                self.assertFalse(checkbox.isHidden())
                self.assertEqual(checkbox.isEnabled(),supported)
                self.assertEqual(checkbox.isChecked(),supported)
            window.dft_model='micromeritics_mod005'
            window._sync_dft_auto_controls()
            self.assertFalse(checkbox.isEnabled())
            self.assertFalse(checkbox.isChecked())
            # Selecting a supported model again defaults to auto, even after
            # manually adjusting the previous model.
            window.dft_automatic_regularization=False
            with patch.object(window,'_finish_dft_option_change'):
                with patch.object(window.dft_model_combo,'currentData',return_value='n2_77_carbon_slit_qsdft_eq'):
                    window._on_dft_model_changed()
            window._sync_dft_auto_controls()
            self.assertTrue(checkbox.isChecked())
            self.assertFalse(hasattr(window,'dft_auto_regularization_label'))
            group=window.dft_regularization_slider.parentWidget()
            group.resize(410,160)
            group.layout().activate()
            auto_rect=checkbox.geometry()
            all_rect=window.dft_regularization_apply_all_checkbox.geometry()
            slider_rect=window.dft_regularization_slider.geometry()
            self.assertEqual(auto_rect.center().y(),all_rect.center().y())
            self.assertLess(auto_rect.right(),all_rect.left())
            self.assertLess(max(auto_rect.bottom(),all_rect.bottom()),slider_rect.top())
            self.assertEqual(float(RegularizationSlider._format_value(1e-6)),1e-6)
        finally:
            window.close()

    def test_apply_all_manual_keeps_models_and_disables_auto(self):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PyQt5 import QtWidgets
        from tristar_bet.ui.main_window import MainWindow
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        window=MainWindow()
        try:
            samples=[self.sample(),self.sample('60115DML-1')]
            models=['n2_77_silica_cylinder_nldft_ads','n2_77_carbon_slit_qsdft_eq']
            window.results=samples
            for sample,model in zip(samples,models):
                window.custom_dft_settings[id(sample)]={'model':model,'automatic_regularization':True}
            window._apply_dft_regularization_to_all(.004)
            for sample,model in zip(samples,models):
                settings=window._dft_settings_for_result(sample)
                self.assertEqual(settings['model'],model)
                self.assertEqual(settings['regularization'],.004)
                self.assertFalse(settings['automatic_regularization'])
        finally:
            window.results=[]
            window.close()


if __name__=='__main__':
    unittest.main()
