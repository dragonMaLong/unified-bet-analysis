"""Counterexamples prevent empirical balance fits being treated as calibration."""
import numpy as np
import pytest

from tristar_bet.jwgb_balance import (
    KPA_CM3_K_TO_STP, balance_residuals, diagnostic_notice,
    fit_free_space, free_space_diagnostic,
)


def test_pressure_polynomial_adsorption_is_not_identifiable_from_fit():
    pressure = np.linspace(1, 90, 30)
    calibrated_q = 0.0085
    empty_balance = calibrated_q * pressure + 1e-6 * pressure**2
    # Nonzero, positive uptake is completely absorbed by the fitted coefficients.
    uptake = 0.002 * pressure + 2e-6 * pressure**2
    measured_balance = empty_balance + uptake
    coefficients = fit_free_space(pressure, measured_balance)
    assert np.all(uptake > 0)
    assert coefficients[0] == pytest.approx(calibrated_q + 0.002)
    assert np.max(np.abs(balance_residuals(pressure, measured_balance, coefficients))) < 1e-12


def test_reversible_adsorption_can_pass_desorption_holdout_without_validating_q():
    records = []
    previous_total = 0.0
    for phase, pressures in [('adsorption', range(10, 91, 10)),
                             ('desorption', range(80, 9, -10))]:
        for pressure in pressures:
            # True Q=.0085; an additional reversible uptake .002*P is present.
            total = (0.0085 + 0.002) * pressure + 1e-6 * pressure**2
            pd = pressure + (total - previous_total) * 298.15 / KPA_CM3_K_TO_STP
            records.append(dict(phase=phase, Pd=pd, Pcd=pressure,
                                PdT=25.0, PcdT=25.0, P0=100.0))
            previous_total = total
    diagnostic = free_space_diagnostic(records, vd=30, stored_q=.0085, mass=.1)
    assert diagnostic['compatible_with_quadratic_balance']
    assert diagnostic['apparent_q_differs_from_stored']
    assert diagnostic['apparent_q'] == pytest.approx(.0105)
    assert diagnostic['desorption_holdout_rms_cm3_g'] < 1e-10
    assert diagnostic['adsorption_identifiable'] is False
    assert 'no_detectable_adsorption' not in diagnostic
    notice = diagnostic_notice(diagnostic)
    assert '不能据此判定没有吸附' in notice
    assert '不能据此替换文件 Q' in notice
