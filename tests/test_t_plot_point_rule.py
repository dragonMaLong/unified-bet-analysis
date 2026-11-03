"""The t-Plot table, fit line and highlighted points always use the same point set.

Micromeritics files store a thickness range and a P/P0 window for the t-Plot table.  Up to
this fix the sample table applied the window as a hidden filter while the plot and its fit
line did not, so dragging the interval to small thicknesses drew a fit on the plot but left
the table value empty.

Rule now: while the file's own interval is in use, every consumer selects the vendor's
points (thickness range AND P/P0 window, computed on the points themselves).  Once the user
moves the interval, every consumer selects the points inside the interval.  For ordinary
files the shown default interval contains exactly the vendor points, so moving it changes
the value only when a point enters or leaves.  Edge cases (empty vendor selection, flat
reference curves where points on both sides of the window share one thickness) keep the
vendor's own point set instead of an interval approximation of it.
"""

import os
import unittest
from dataclasses import replace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5 import QtWidgets

from tristar_bet.analysis import (
    t_plot_analysis,
    t_plot_analysis_by_thickness,
    thickness_nm,
    vendor_t_plot_default_thickness_range,
)
from tristar_bet.models import IsothermPoint
from tristar_bet.ui.main_window import MainWindow
from tristar_bet.ui.plots import plot_t_plot_selection, replace_t_plot_fit_line

from qt_window_support import isolated_main_window
from test_detail_result_tables import sample
from test_micromeritics_report_settings import ReferenceInterpolationTests

WINDOW = (0.0556245, 0.7757905)
STORED_THICKNESS = (0.30, 0.50)
PRESSURES = [0.0005, 0.001, 0.002, 0.004, 0.007, 0.011, 0.016, 0.022, 0.03, 0.04, 0.05,
             0.062, 0.075, 0.09, 0.105, 0.12, 0.14, 0.16, 0.19, 0.22, 0.26, 0.3, 0.35, 0.4]


def vendor_sample():
    points = []
    for index, p in enumerate(PRESSURES, start=1):
        q = 120.0 * p / ((1.0 - p) * (0.01 + p)) + 40.0 * p
        points.append(IsothermPoint(index, 'adsorption', 0, p * 760.0, p, q, 760.0, index * 60, q, q / 22.414))
    result = replace(sample('vendor-t-plot'), isotherm=points)
    result.method_options.update(
        use_vendor_t_plot_settings=True,
        use_stored_fit_ranges=True,
        vendor_t_plot_thickness_method='harkins_jura',
        stored_t_plot_thickness_min_nm=STORED_THICKNESS[0],
        stored_t_plot_thickness_max_nm=STORED_THICKNESS[1],
        stored_t_plot_pressure_window_min=WINDOW[0],
        stored_t_plot_pressure_window_max=WINDOW[1],
    )
    return result


def vendor_points(result, method='harkins_jura', params=None):
    lo_p = result.method_options['stored_t_plot_pressure_window_min']
    hi_p = result.method_options['stored_t_plot_pressure_window_max']
    lo_t = result.method_options['stored_t_plot_thickness_min_nm']
    hi_t = result.method_options['stored_t_plot_thickness_max_nm']
    selected = []
    for point in result.isotherm:
        thickness = thickness_nm(point.relative_pressure, method, params)
        if lo_p <= point.relative_pressure <= hi_p and thickness is not None and lo_t <= thickness <= hi_t:
            selected.append(point.index)
    return selected


def empty_window_sample():
    # Saved P/P0 window and thickness range do not overlap: the vendor table is empty.
    result = vendor_sample()
    result.method_options.update(stored_t_plot_pressure_window_min=0.001, stored_t_plot_pressure_window_max=0.01)
    return result


def plateau_sample():
    # Reference curve with flat segments: points just below the window edge have the same
    # thickness as points inside it, so no thickness interval equals the P/P0 window.
    base = vendor_sample()
    pressures = [0.9, 0.905, 0.91, 0.915, 0.92, 0.925, 0.93, 0.94, 0.95, 0.96, 0.97]
    result = replace(base, isotherm=[
        replace(base.isotherm[0], index=i + 1, relative_pressure=p, quantity_adsorbed_cm3_g_stp=20.0 + 10.0 * p)
        for i, p in enumerate(pressures)
    ])
    result.method_options.update(
        vendor_t_plot_thickness_method='reference',
        vendor_t_plot_thickness_params={'reference_points': ReferenceInterpolationTests.TABLE,
                                        'interpolation': 'micromeritics_akima'},
        stored_t_plot_pressure_window_min=0.92,
        stored_t_plot_pressure_window_max=0.98,
        stored_t_plot_thickness_min_nm=1.30,
        stored_t_plot_thickness_max_nm=3.00,
    )
    return result


def t(p):
    return thickness_nm(p, 'harkins_jura', None)


def indices(analysis):
    return [int(row['point_index']) for row in analysis.rows]


class VendorDefaultIntervalTests(unittest.TestCase):
    def test_default_interval_selects_exactly_the_vendor_points(self):
        result = vendor_sample()
        lower, upper = vendor_t_plot_default_thickness_range(result)
        self.assertAlmostEqual(lower, max(STORED_THICKNESS[0], t(WINDOW[0])))
        self.assertAlmostEqual(upper, STORED_THICKNESS[1])
        vendor_points = [
            point.index for point in result.isotherm
            if WINDOW[0] <= point.relative_pressure <= WINDOW[1]
            and STORED_THICKNESS[0] <= t(point.relative_pressure) <= STORED_THICKNESS[1]
        ]
        self.assertGreaterEqual(len(vendor_points), 3)
        default = t_plot_analysis(result)
        self.assertEqual(default.status, 'ok')
        self.assertEqual(indices(default), vendor_points)
        self.assertEqual(indices(t_plot_analysis_by_thickness(result, lower, upper)), vendor_points)

    def test_without_window_the_saved_thickness_range_is_used(self):
        result = vendor_sample()
        del result.method_options['stored_t_plot_pressure_window_min']
        del result.method_options['stored_t_plot_pressure_window_max']
        self.assertEqual(vendor_t_plot_default_thickness_range(result), STORED_THICKNESS)

    def test_moving_the_interval_only_changes_with_points_crossing_it(self):
        result = vendor_sample()
        lower, upper = vendor_t_plot_default_thickness_range(result)
        thicknesses = sorted(t(p) for p in PRESSURES)
        previous = None
        start = lower
        while start > thicknesses[0]:
            analysis = t_plot_analysis_by_thickness(result, start, upper)
            expected = [i for i, p in enumerate(PRESSURES, start=1) if start <= t(p) <= upper]
            self.assertEqual(indices(analysis), expected, start)
            if previous is not None and indices(previous) == expected:
                self.assertEqual(analysis.external_surface_area_m2_g, previous.external_surface_area_m2_g)
            previous = analysis
            start -= 0.004

    def test_empty_vendor_selection_stays_empty(self):
        result = empty_window_sample()
        self.assertEqual(vendor_points(result), [])
        default = t_plot_analysis(result)
        self.assertNotEqual(default.status, 'ok')
        self.assertEqual(default.point_count, 0)

    def test_flat_reference_curve_keeps_the_vendor_point_set(self):
        result = plateau_sample()
        params = result.method_options['vendor_t_plot_thickness_params']
        expected = vendor_points(result, 'reference', params)
        self.assertEqual(len(expected), 7)
        self.assertEqual(indices(t_plot_analysis(result)), expected)

    def test_small_thickness_interval_gives_a_result(self):
        result = vendor_sample()
        analysis = t_plot_analysis_by_thickness(result, t(0.001) - 1e-9, t(0.011) + 1e-9)
        self.assertEqual(analysis.status, 'ok')
        self.assertEqual(analysis.point_count, 5)
        self.assertIsNotNone(analysis.external_surface_area_m2_g)


class SampleTableMatchesPlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window_with(self, result):
        window = isolated_main_window(self, MainWindow)
        window.results = [result]
        window.visible_results = [True]
        window.sample_colors = ['#123456']
        window.active_index = 0
        return window

    def plot_selection(self, window, result, interval):
        # The provider call made by the fit line / highlighted points (plots.py), with the
        # full P/P0 data range the t-Plot tab passes when no pressure region is set.
        settings = window._t_plot_settings_for_result(result)
        return window._cached_t_plot_thickness_analysis(
            result, interval[0], interval[1], 0.0, 1.0,
            dict(settings['thickness_params']), str(settings['thickness_method']),
        )

    def test_default_selection_is_the_vendor_set_in_table_and_plot(self):
        for result, method_params in [
            (vendor_sample(), ('harkins_jura', None)),
            (plateau_sample(), None),
            (empty_window_sample(), ('harkins_jura', None)),
        ]:
            if method_params is None:
                method_params = ('reference', result.method_options['vendor_t_plot_thickness_params'])
            window = self.window_with(result)
            expected = vendor_points(result, *method_params)
            interval = window._t_plot_fit_range_for_result(result)
            table = window._t_plot_analysis_for_result(result)
            plot = self.plot_selection(window, result, interval)
            self.assertEqual(indices(table), expected)
            self.assertEqual(indices(plot), expected)
            self.assertEqual(table.status == 'ok', len(expected) >= 3)

    def test_moved_interval_uses_its_points_in_table_and_plot(self):
        result = plateau_sample()
        window = self.window_with(result)
        interval = window._t_plot_fit_range_for_result(result)
        window.custom_t_plot_fit_ranges[id(result)] = interval
        params = result.method_options['vendor_t_plot_thickness_params']
        inside = [p.index for p in result.isotherm
                  if interval[0] <= thickness_nm(p.relative_pressure, 'reference', params) <= interval[1]]
        self.assertEqual(indices(window._t_plot_analysis_for_result(result)), inside)
        self.assertEqual(indices(self.plot_selection(window, result, interval)), inside)
        # Back to the file's default: the vendor set again (no stale cached selection).
        del window.custom_t_plot_fit_ranges[id(result)]
        self.assertEqual(indices(window._t_plot_analysis_for_result(result)), vendor_points(result, 'reference', params))

    def test_disjoint_pressure_windows_do_not_draw_a_fit_or_selected_points(self):
        result = plateau_sample()
        window = self.window_with(result)
        interval = window._t_plot_fit_range_for_result(result)
        settings = window._t_plot_settings_for_result(result)
        for p_min, p_max in [(0.80, 0.91), (0.99, 1.0)]:
            with self.subTest(pressure_window=(p_min, p_max)):
                options = dict(data_p_min=p_min, data_p_max=p_max,
                               thickness_params=settings['thickness_params'],
                               thickness_method=settings['thickness_method'],
                               analysis_provider=window._cached_t_plot_thickness_analysis)
                line, fit = replace_t_plot_fit_line(window.t_plot, None, result, *interval, **options)
                self.assertEqual(fit.status, 'not_enough_points')
                self.assertEqual(fit.point_count, 0)
                self.assertEqual(fit.rows, [])
                self.assertIsNone(fit.external_surface_area_m2_g)
                self.assertEqual((fit.pressure_min, fit.pressure_max), interval)
                self.assertIsNone(line)
                self.assertIsNone(plot_t_plot_selection(window.t_plot, result, *interval, **options))
        # An empty plot selection must not replace the file's default table result.
        self.assertEqual(indices(window._t_plot_analysis_for_result(result)), [5, 6, 7, 8, 9, 10, 11])

    def test_touching_and_overlapping_pressure_windows_keep_their_points(self):
        result = plateau_sample()
        window = self.window_with(result)
        interval = window._t_plot_fit_range_for_result(result)
        settings = window._t_plot_settings_for_result(result)
        for pressure_window, expected in [((0.80, 0.91), []), ((0.80, 0.92), [5]),
                                           ((0.93, 0.96), [7, 8, 9, 10]), ((0.0, 1.0), [5, 6, 7, 8, 9, 10, 11])]:
            with self.subTest(pressure_window=pressure_window):
                fit = window._cached_t_plot_thickness_analysis(
                    result, *interval, *pressure_window,
                    settings['thickness_params'], settings['thickness_method'],
                )
                # Too few points return a count but intentionally omit fit rows.
                self.assertEqual(fit.point_count, len(expected))
                self.assertEqual(fit.ok, len(expected) >= 3)
                if fit.ok:
                    self.assertEqual(indices(fit), expected)

    def test_table_value_uses_the_points_inside_the_dragged_interval(self):
        result = vendor_sample()
        window = self.window_with(result)
        default_range = window._t_plot_fit_range_for_result(result)
        self.assertEqual(default_range, vendor_t_plot_default_thickness_range(result))
        self.assertEqual(indices(window._t_plot_analysis_for_result(result)), indices(t_plot_analysis(result)))
        for interval in [(0.20, 0.30), (t(0.001) - 1e-9, t(0.011) + 1e-9), (0.30, 0.50)]:
            window.custom_t_plot_fit_ranges[id(result)] = interval
            shown = window._t_plot_analysis_for_result(result)
            expected = t_plot_analysis_by_thickness(result, *interval)
            self.assertEqual(shown.status, 'ok', interval)
            self.assertEqual(indices(shown), indices(expected), interval)
            self.assertEqual(shown.external_surface_area_m2_g, expected.external_surface_area_m2_g)


if __name__ == '__main__':
    unittest.main()
