"""Automatic interpretation replaces the former AI table integration.

The dialog, raster analyses, surface recalculation and persistence are real;
only scheduling, choosers and map capture use the existing fixture controls.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtGui import QTextDocument
from qgis.PyQt.QtWidgets import QPushButton

import test_regression_resolution_save as fixture
from caumax_consulta.parameter_schema import METRICS, interpret_value


class AutomaticTableIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ResolutionSaveRegressionTests.setUpClass()

    def setUp(self):
        self.helper = fixture.ResolutionSaveRegressionTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.dialog = self.helper.dialog

    def texts(self):
        return [self.dialog.table.item(row, 3).text() for row in range(len(METRICS))]

    def assert_current(self):
        values = dict(self.dialog.analysis.values)
        values['cell_resolution'] = (self.dialog.analysis.cell_width, self.dialog.analysis.cell_height)
        self.assertEqual(self.texts(), [interpret_value(m.key, values.get(m.key), values) for m in METRICS])
        self.assertTrue(all(self.texts()))

    def test_initial_analysis_fills_every_row_without_button_or_network_request(self):
        with patch('caumax_consulta.ai_analysis.request_interpretations') as request:
            self.dialog._display_analysis()
            self.dialog.quantiles_widget.period_spin.setValue(200)
            self.dialog.basin_hydrology_widget.period_spin.setValue(200)
            self.assert_current()
        request.assert_not_called()
        self.assertFalse(any('Análisis IA' in b.text() for b in self.dialog.findChildren(QPushButton)))
        self.assertFalse(hasattr(self.dialog, '_show_ai_analysis'))

    def test_initial_and_failed_analysis_never_retain_comments(self):
        from caumax_consulta.basin_dialog import BasinDialog
        f = self.helper.fixture
        with patch.object(BasinDialog, '_start'), patch.object(BasinDialog, '_setup_resolutions'):
            initial = BasinDialog(f.query, f.iface)
        f.dialogs.append(initial)
        self.assertTrue(all(not initial.table.item(row, 3).text() for row in range(len(METRICS))))
        self.dialog._failed('No se ha podido completar la cuenca.')
        self.dialog.quantiles_widget.period_spin.setValue(200)
        self.dialog._render_interpretations()
        self.assertTrue(all(not text for text in self.texts()))
        self.dialog._display_analysis()
        self.assert_current()

    def test_local_interpretation_keeps_measurements_and_report_snapshot_unchanged(self):
        dialog = self.dialog
        values = deepcopy(dialog.analysis.values)
        cells = [[dialog.table.item(row, col).text() for col in range(3)] for row in range(len(METRICS))]
        snapshot = dialog.get_smoothing_state()
        dialog._render_interpretations()
        self.assert_current()
        self.assertEqual(dialog.analysis.values, values)
        self.assertEqual(cells, [[dialog.table.item(row, col).text() for col in range(3)] for row in range(len(METRICS))])
        with patch('caumax_consulta.reports.ask_basin_pdf') as report:
            dialog._report()
        report.assert_called_once_with(dialog, dialog.analysis, dialog.query, smoothing_state=snapshot)
        self.helper.errors.assert_not_called()

    def test_resolution_change_clears_then_interprets_the_new_real_analysis(self):
        self.helper.configure()
        dialog, controller = self.dialog, self.helper.controller
        before = self.texts()
        controller.requested_key = None
        controller.select = Mock()
        dialog._select_resolution('5')
        self.assertTrue(all(not text for text in self.texts()))
        controller.select.assert_called_once_with('5')
        variant = controller.variants['5']
        dialog._resolution_loading = True
        try:
            dialog._resolution_result('5', variant['analysis'], variant['query'])
        finally:
            dialog._resolution_loading = False
        self.assert_current()
        self.assertNotEqual(self.texts(), before)

    def test_outlet_change_clears_before_worker_replaces_analysis(self):
        dialog = self.dialog
        selector = SimpleNamespace(selection=lambda:(dialog.analysis.outlet_x-25, dialog.analysis.outlet_y), reject=lambda:None)
        controller = SimpleNamespace(busy=False, variants={}, _generation=1, dispose=lambda:None,
                                     select_outlet=Mock(return_value=True))
        dialog._resolution_controller = controller
        dialog._outlet_selector = selector
        old = dialog.analysis
        dialog._apply_outlet_selection(selector, controller, {'resolution_key':'25'})
        self.assertIs(dialog.analysis, old)
        self.assertTrue(all(not text for text in self.texts()))
        dialog._render_interpretations()
        self.assertTrue(all(not text for text in self.texts()))

    def test_real_smoothed_surface_gets_its_own_interpretations(self):
        before = self.texts()
        dialog = self.dialog
        dialog.smooth_check.setChecked(True)
        dialog.smoothing_degree.setValue(18)
        dialog._smoothing_timer.stop()
        self.assertTrue(all(not text for text in self.texts()))
        dialog._update_smoothing()
        self.helper.publish(self.helper.manager.tasks[-1])
        self.assert_current()
        self.assertNotEqual(self.texts(), before)

    def test_tooltips_preserve_full_text_and_escape_html(self):
        text = 'Lectura <b>literal</b> & criterio\nsegunda línea'
        with patch('caumax_consulta.basin_dialog.interpret_value', return_value=text), \
             patch('caumax_consulta.basin_dialog.interpretation_reference', return_value='Referencia de prueba'):
            self.dialog._render_interpretations()
        tooltip = self.dialog.table.item(0, 3).toolTip()
        self.assertNotIn('<b>', tooltip)
        document = QTextDocument(); document.setHtml(tooltip)
        self.assertIn('<b>literal</b>', document.toPlainText())
        self.assertIn('Referencia de prueba', document.toPlainText())

    def test_saved_project_regenerates_comments_and_does_not_store_ai_text(self):
        from caumax_consulta.session_store import save_session
        dialog = self.dialog
        before = self.texts()
        path = self.helper.fixture.folder/'automatic.caumax.json'
        save_session(path, dialog.analysis, dialog.query, dialog.get_smoothing_state())
        dialog.table.item(0, 3).setText('Comentario obsoleto')
        with patch.object(dialog, '_setup_resolutions'):
            dialog.load_project(path)
        self.assertEqual(self.texts(), before)
        self.assert_current()


if __name__ == '__main__':
    unittest.main()
