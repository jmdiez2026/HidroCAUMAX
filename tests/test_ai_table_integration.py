"""AI table updates use real QGIS results and a held, reviewed-response callback.

Only the asynchronous AI/network entry is simulated. The dialog, raster analyses,
surface recalculation, table, widget signals and output snapshot are real.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtGui import QTextDocument

import test_regression_resolution_save as fixture
from caumax_consulta.parameter_schema import METRICS


class AITableIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ResolutionSaveRegressionTests.setUpClass()

    def setUp(self):
        self.helper = fixture.ResolutionSaveRegressionTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.dialog = self.helper.dialog
        self.rows = {metric.key: f'Interpretación revisada de {metric.key}: texto completo de la respuesta.'
                     for metric in reversed(METRICS)}
        self.rows['area_km2'] += ' <img src="file:///not-a-report-image"> & <b>literal</b>'
        self.rows['kc'] = ('REVISAR: advertencia del programa. Texto recibido de IA: '
                           + 'Conclusión que requiere comprobación. '*20)

    def request(self):
        job = Mock()
        with patch('caumax_consulta.ai_analysis.request_interpretations',return_value=job) as show:
            self.assertIs(self.dialog._show_ai_analysis(),job)
        return show.call_args.kwargs['on_interpretations']

    def assert_empty(self):
        dialog = self.dialog
        self.assertEqual(dialog.table.horizontalHeaderItem(3).text(),'Interpretación')
        for row,metric in enumerate(METRICS):
            self.assertEqual(dialog.table.item(row,3).text(),'')
            self.assertEqual(dialog.table.item(row,3).toolTip(),'')
            self.assertEqual(dialog.table.item(row,0).toolTip(),metric.definition)

    def assert_generated(self, rows=None):
        rows = self.rows if rows is None else rows
        self.assertEqual(self.dialog.table.horizontalHeaderItem(3).text(),'Interpretación')
        self.assertEqual(self.dialog.table.rowCount(),19)
        for row,metric in enumerate(METRICS):
            self.assertEqual(self.dialog.table.item(row,3).text(),rows[metric.key])
            tooltip = self.dialog.table.item(row,3).toolTip()
            document = QTextDocument()
            document.setHtml(tooltip)
            self.assertEqual(document.toPlainText(),rows[metric.key])
            self.assertNotIn('<img',tooltip)
            self.assertNotIn('<b>',tooltip)

    def test_both_ai_buttons_deliver_rows_to_the_same_empty_interpretation_column(self):
        dialog=self.dialog
        self.assert_empty()
        self.assertIs(dialog.analysis_header_button,dialog.table.horizontalHeader().analysis_button)
        def complete(*args,**kwargs):
            self.assertTrue(kwargs['on_interpretations'](self.rows))
            kwargs['on_finished']()
            return Mock()
        for button in (dialog.analysis_header_button,dialog.analysis_ai_button):
            with patch('caumax_consulta.ai_analysis.request_interpretations',side_effect=complete) as show:
                button.click()
            show.assert_called_once()
            self.assert_generated()
        dialog._surface_valid=False
        dialog._refresh_ai_button()
        for button in (dialog.analysis_header_button,dialog.analysis_ai_button):
            self.assertFalse(button.isEnabled())
        dialog._surface_valid=True
        dialog._refresh_ai_button()
        for button in (dialog.analysis_header_button,dialog.analysis_ai_button):
            self.assertTrue(button.isEnabled())

    def test_real_request_updates_only_table_and_cleans_up_without_opening_a_window(self):
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from caumax_consulta import ai_analysis as ai
        from test_ai_analysis import FakeReply, FakeManager, response, structured
        dialog = self.dialog
        ai._CACHE.clear()
        ai._NEXT_REQUEST_AT = 0.
        self.addCleanup(ai._CACHE.clear)
        self.addCleanup(setattr,ai,'_NEXT_REQUEST_AT',0.)
        reply = FakeReply()
        manager = FakeManager(reply)
        windows = {id(widget) for widget in QApplication.topLevelWidgets()}
        measurements = [[dialog.table.item(row,col).text() for col in range(3)]
                        for row in range(len(METRICS))]
        with patch.object(ai.QTimer,'singleShot'), patch.object(ai,'QgsNetworkAccessManager',
                SimpleNamespace(instance=lambda:manager)):
            dialog.analysis_header_button.click()
            request = dialog._ai_request
            self.assertIsInstance(request,ai.InterpretationRequest)
            self.assertNotIsInstance(request,QDialog)
            self.assertFalse(dialog.analysis_header_button.isEnabled())
            self.assertFalse(dialog.analysis_ai_button.isEnabled())
            self.assertEqual({id(widget) for widget in QApplication.topLevelWidgets()},windows)
            request.start()
            self.assertEqual(len(manager.requests),1)
            reply.buffer.extend(response(structured()))
            reply.finished.emit()
            self.assertIsNone(dialog._ai_request)
            self.assertTrue(dialog.analysis_header_button.isEnabled())
            self.assertTrue(dialog.analysis_ai_button.isEnabled())
            self.assertTrue(all(dialog.table.item(row,3).text() for row in range(len(METRICS))))
            self.assertEqual({id(widget) for widget in QApplication.topLevelWidgets()},windows)
            # The upper button uses the same silent flow and a completed cache.
            dialog.analysis_ai_button.click()
            cached = dialog._ai_request
            cached.start()
            self.assertIsNone(dialog._ai_request)
            self.assertEqual(len(manager.requests),1)
        self.assertEqual(measurements,[[dialog.table.item(row,col).text() for col in range(3)]
                                      for row in range(len(METRICS))])
        self.helper.errors.assert_not_called()

    def test_data_change_cancels_pending_network_and_ignores_late_status_and_rows(self):
        dialog = self.dialog
        with patch('caumax_consulta.ai_analysis.request_interpretations',return_value=Mock()) as factory:
            request = dialog._show_ai_analysis()
        callbacks = factory.call_args.kwargs
        self.assertFalse(dialog.analysis_header_button.isEnabled())
        dialog.quantiles_widget.period_spin.setValue(dialog.quantiles_widget.period_spin.value()+100)
        request.cancel.assert_called_once()
        request.deleteLater.assert_called_once()
        self.assertIsNone(dialog._ai_request)
        self.assertFalse(callbacks['on_interpretations'](self.rows))
        callbacks['on_status']('Obsoleto')
        callbacks['on_finished']()
        self.assertNotEqual(dialog.analysis_header_button.toolTip(),'Obsoleto')
        self.assert_empty()

    def test_initial_and_failed_analysis_leave_interpretation_cells_empty(self):
        from caumax_consulta.basin_dialog import BasinDialog
        fixture=self.helper.fixture
        with patch.object(BasinDialog,'_start'),patch.object(BasinDialog,'_setup_resolutions'):
            initial=BasinDialog(fixture.query,fixture.iface)
        fixture.dialogs.append(initial)
        for row in range(len(METRICS)):
            self.assertEqual(initial.table.item(row,3).text(),'')
            self.assertEqual(initial.table.item(row,3).toolTip(),'')
        initial._failed('Cálculo no disponible en esta prueba.')
        self.assertEqual(initial.table.horizontalHeaderItem(3).text(),'Interpretación')
        for row in range(len(METRICS)):
            self.assertEqual(initial.table.item(row,1).text(),'n.d.')
            self.assertEqual(initial.table.item(row,3).text(),'')
            self.assertEqual(initial.table.item(row,3).toolTip(),'')

    def test_reviewed_rows_map_by_key_without_changing_measurements_or_report_snapshot(self):
        dialog = self.dialog
        self.assert_empty()
        cells = [[dialog.table.item(row,column).text() for column in range(3)]
                 for row in range(len(METRICS))]
        values = deepcopy(dialog.analysis.values)
        snapshot = dialog.get_smoothing_state()
        self.assertTrue(self.request()(self.rows))
        self.assert_generated()
        self.assertEqual(cells,[[dialog.table.item(row,column).text() for column in range(3)]
                                for row in range(len(METRICS))])
        self.assertEqual(dialog.analysis.values,values)
        self.assertEqual(dialog.get_smoothing_state(),snapshot)
        with patch('caumax_consulta.reports.ask_basin_pdf') as report:
            dialog._report()
        self.helper.errors.assert_not_called()
        report.assert_called_once_with(dialog,dialog.analysis,dialog.query,smoothing_state=snapshot)

    def test_resolution_change_clears_text_and_rejects_late_previous_response(self):
        self.helper.configure()
        dialog,controller = self.dialog,self.helper.controller
        controller.requested_key = None
        controller.select = Mock()
        callback = self.request()
        self.assertTrue(callback(self.rows))
        dialog._select_resolution('5')
        self.assert_empty()
        self.assertFalse(callback(self.rows))
        controller.select.assert_called_once_with('5')
        variant = controller.variants['5']
        dialog._resolution_loading = True
        try:
            dialog._resolution_result('5',variant['analysis'],variant['query'])
        finally:
            dialog._resolution_loading = False
        self.assert_empty()
        self.assertFalse(callback(self.rows))

    def test_manual_choice_invalidates_revision_even_before_analysis_is_replaced(self):
        dialog = self.dialog
        callback = self.request()
        self.assertTrue(callback(self.rows))
        selector = SimpleNamespace(selection=lambda:(dialog.analysis.outlet_x-25,dialog.analysis.outlet_y),
                                   reject=lambda:None)
        controller = SimpleNamespace(busy=False,variants={},_generation=1,dispose=lambda:None,
                                     select_outlet=Mock(return_value=True))
        dialog._resolution_controller = controller
        dialog._outlet_selector = selector
        analysis = dialog.analysis
        dialog._apply_outlet_selection(selector,controller,{'resolution_key':'25'})
        self.assert_empty()
        self.assertIs(dialog.analysis,analysis)
        self.assertFalse(callback(self.rows))
        # Ending/cancelling that worker must not resurrect an old response.
        dialog._resolution_pending = False
        self.assertFalse(callback(self.rows))

    def test_smoothing_change_clears_ai_before_real_surface_recalculation(self):
        dialog = self.dialog
        callback = self.request()
        self.assertTrue(callback(self.rows))
        original_values = deepcopy(dialog.analysis.values)
        dialog.smooth_check.setChecked(True)
        dialog.smoothing_degree.setValue(18)
        dialog._smoothing_timer.stop()
        self.assert_empty()
        self.assertFalse(callback(self.rows))
        dialog._update_smoothing()
        self.helper.publish(self.helper.manager.tasks[-1])
        self.assertNotEqual(dialog.analysis.values['area_km2'],original_values['area_km2'])
        self.assert_empty()
        self.assertFalse(callback(self.rows))
        self.assertTrue(self.request()(self.rows))
        self.assert_generated()

    def test_changed_values_and_hydrology_inputs_reject_same_object_response(self):
        dialog = self.dialog
        callback = self.request()
        analysis = dialog.analysis
        dialog.analysis.values['kc'] += .1
        self.assertIs(dialog.analysis,analysis)
        self.assertFalse(callback(self.rows))
        self.assert_empty()
        for spin in (dialog.quantiles_widget.period_spin,dialog.basin_hydrology_widget.period_spin):
            callback = self.request()
            self.assertTrue(callback(self.rows))
            spin.setValue(spin.value()+100)
            self.assert_empty()
            self.assertFalse(callback(self.rows))

    def test_visual_changes_keep_ai_and_allow_same_data_response(self):
        dialog = self.dialog
        callback = self.request()
        self.assertTrue(callback(self.rows))
        dialog.resize(650,900)
        dialog._fit_rows()
        dialog.cell_grid_check.setChecked(not dialog.cell_grid_check.isChecked())
        dialog.hypsometry_widget.dimensionless_check.setChecked(True)
        dialog._clear_local_delineation()
        self.assert_generated()
        self.assertTrue(callback(self.rows))
        self.assert_generated()

    def test_cache_callback_is_synchronous_and_error_or_partial_output_keeps_previous_text(self):
        dialog = self.dialog
        cached_job = Mock()
        def cached(*args,**kwargs):
            self.assertTrue(kwargs['on_interpretations'](self.rows))
            kwargs['on_finished']()
            return cached_job
        with patch('caumax_consulta.ai_analysis.request_interpretations',side_effect=cached):
            self.assertIs(dialog._show_ai_analysis(),cached_job)
        self.assert_generated()
        callback = self.request()
        self.assert_generated()  # No callback on cancellation or network failure.
        for invalid in ({},dict(self.rows,kc=''),{key:value for key,value in self.rows.items() if key!='ic'}):
            self.assertFalse(callback(invalid))
            self.assert_generated()
        with patch('caumax_consulta.ai_analysis.request_interpretations',side_effect=RuntimeError('Sin respuesta')):
            dialog._show_ai_analysis()
        self.helper.errors.assert_not_called()
        self.assertIn('Sin respuesta',dialog.analysis_header_button.toolTip())
        self.assertTrue(dialog.analysis_header_button.isEnabled())
        self.assert_generated()

    def test_loading_same_basin_with_other_saved_results_resets_ai(self):
        from caumax_consulta.session_store import save_session
        dialog = self.dialog
        callback = self.request()
        self.assertTrue(callback(self.rows))
        state = dialog.get_smoothing_state()
        state['flow_quantiles']['input_period'] = 200.
        state['basin_hydrology']['input_period'] = 300.
        path = self.helper.fixture.folder/'same-basin-other-results.caumax.json'
        save_session(path,dialog.analysis,dialog.query,state)
        with patch.object(dialog,'_setup_resolutions'):
            saved = dialog.load_project(path)
        self.assertIsNone(saved.resolution_state)
        self.assert_empty()
        self.assertEqual(dialog.quantiles_widget.period_spin.value(),200.)
        self.assertEqual(dialog.basin_hydrology_widget.period_spin.value(),300.)
        self.assertFalse(callback(self.rows))

    def test_newer_request_and_dispose_prevent_old_callback_from_touching_table(self):
        old = self.request()
        newer = self.request()
        replacement = {key:'Nueva '+text for key,text in self.rows.items()}
        self.assertTrue(newer(replacement))
        self.assertFalse(old(self.rows))
        self.assert_generated(replacement)
        self.dialog.dispose()
        self.assertFalse(newer(self.rows))
        self.assert_generated(replacement)


if __name__ == '__main__':
    unittest.main()
