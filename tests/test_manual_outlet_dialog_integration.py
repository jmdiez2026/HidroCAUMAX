"""Manual selection wiring: real controller/dialog with a held map selector."""
from copy import deepcopy
from dataclasses import replace
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QDialog
from qgis.core import QgsProject, QgsVectorLayer

import test_regression_resolution_save as fixture
from caumax_consulta.resolution_controller import ResolutionController
from caumax_consulta.basin_dialog import AnalysisTask, BasinDialog
from caumax_consulta.terrain import analyze_basin
from caumax_consulta.map_capture import capture_basin_map as real_capture_basin_map
from caumax_consulta.session_store import load_session


class FakeOutletSelectionDialog(QDialog):
    instances = []
    applied = pyqtSignal()

    def __init__(self, iface, context, parent=None, *, persistent=False):
        super().__init__(parent)
        self.context = deepcopy(context)
        self.point = None
        self.rejections = 0
        self.busy = False
        self.persistent = persistent
        self.bindings = []
        self.instances.append(self)

    def selection(self):
        return self.point

    def choose(self, x, y):
        self.point = (x, y)
        self.busy = True
        self.applied.emit()

    def set_busy(self, busy, message=''):
        self.busy = busy
        self.message = message

    def rebind(self, context):
        self.context = deepcopy(context)
        self.bindings.append(self.context)
        self.point = None
        self.busy = False

    def reject(self):
        self.point = None
        self.rejections += 1
        super().reject()


class ManualOutletDialogIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ResolutionSaveRegressionTests.setUpClass()

    def setUp(self):
        self.helper = fixture.ResolutionSaveRegressionTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.dialog = self.helper.dialog
        FakeOutletSelectionDialog.instances.clear()
        selector_patch = patch.dict(sys.modules, {
            'caumax_consulta.outlet_selection': SimpleNamespace(
                OutletSelectionDialog=FakeOutletSelectionDialog)})
        selector_patch.start()
        self.addCleanup(selector_patch.stop)
        warning = patch('caumax_consulta.basin_dialog.QMessageBox.warning')
        self.warning = warning.start()
        self.addCleanup(warning.stop)

    def configure(self, smoothed=False):
        self.helper.configure(smoothed=smoothed)
        previous = self.helper.controller
        dialog = self.dialog
        controller = ResolutionController(dialog.analysis, dialog.query,
            dialog._resolution_options, dialog._resolution_result,
            dialog._resolution_status, dialog._resolution_error, parent=dialog)
        controller.variants = previous.variants
        controller.options = previous.options
        dialog._resolution_controller = controller
        self.controller = controller
        dialog._cache_variant_state()
        dialog._resolution_options(controller.options)

    def open_selector(self):
        self.dialog._open_outlet_selection()
        self.warning.assert_not_called()
        self.assertIsNotNone(self.dialog._outlet_selector)
        return self.dialog._outlet_selector

    def finish_manual(self, x, y):
        dialog = self.dialog
        old = dialog.analysis
        selector = self.open_selector()
        selector.choose(x, y)
        task = self.helper.manager.tasks[-1]
        self.assertIs(dialog.analysis, old)
        success = task.run()
        self.assertTrue(success, task.error)
        native = task.payload['analysis']
        task.finished(success)
        self.assertEqual(dialog._base_geometry_wkb, native.geometry_wkb)
        self.assertEqual(self.controller.variants['25']['analysis'].outlet_x, native.outlet_x)
        self.assertEqual(self.controller.variants['25']['analysis'].outlet_y, native.outlet_y)
        dialog._smoothing_timer.stop()
        dialog._update_smoothing()
        self.helper.publish(self.helper.manager.tasks[-1])
        return native

    def test_same_key_manual_replacements_keep_new_base_and_restore_new_smoothed_state(self):
        self.configure(smoothed=True)
        dialog, controller = self.dialog, self.controller
        old_state = deepcopy(controller.variants['25']['smoothing_state'])
        original_x, original_y = self.helper.fixture.query.x, self.helper.fixture.query.y
        first = self.finish_manual(original_x, original_y + 25)
        selector = dialog._outlet_selector
        self.assertIsNotNone(selector)
        self.assertFalse(selector.busy)
        self.assertEqual((selector.context['initial_x'],selector.context['initial_y']),
                         (first.outlet_x,first.outlet_y))
        self.assertNotEqual(first.geometry_wkb, old_state['base_geometry_wkb'])
        second = self.finish_manual(original_x - 25, original_y)
        self.assertIs(dialog._outlet_selector,selector)
        self.assertEqual(len(FakeOutletSelectionDialog.instances),1)
        self.assertEqual(len(selector.bindings),2)
        self.assertEqual((selector.context['initial_x'],selector.context['initial_y']),
                         (second.outlet_x,second.outlet_y))
        self.assertNotEqual(second.geometry_wkb, first.geometry_wkb)
        current = controller.variants['25']
        self.assertEqual(current['smoothing_state']['base_geometry_wkb'], second.geometry_wkb)
        self.assertEqual(current['analysis'].geometry_wkb,
                         current['smoothing_state']['geometry_wkb']['suavizada'])
        variants = {key:dict(value, analysis=replace(value['analysis']),
                            smoothing_state=deepcopy(value['smoothing_state']))
                    for key,value in controller.variants.items()}
        snapshot = dict(active_key='25', options=deepcopy(controller.options), variants=variants)
        selector = self.open_selector()
        self.assertTrue(controller.restore(snapshot))
        self.assertIsNone(dialog._outlet_selector)
        self.assertEqual(selector.rejections, 1)
        self.assertEqual(dialog._base_geometry_wkb, second.geometry_wkb)
        self.assertEqual((dialog.analysis.outlet_x, dialog.analysis.outlet_y),
                         (second.outlet_x, second.outlet_y))
        self.assertEqual(controller.variants['25']['smoothing_state']['base_geometry_wkb'], second.geometry_wkb)
        dialog._cancel_surface()

    def test_ambiguous_context_opens_once_and_cancel_preserves_active_variant(self):
        self.configure()
        dialog, controller = self.dialog, self.controller
        old = dialog.analysis
        context = controller.selection_context()
        context.update(resolution_key='5', reason='Dos ramales posibles')
        controller.outlet_selection_context = context
        dialog._resolution_pending = True
        dialog._resolution_error('Dos ramales posibles')
        QTest.qWait(10)
        self.assertEqual(len(FakeOutletSelectionDialog.instances), 1)
        selector = dialog._outlet_selector
        self.assertEqual(selector.context['resolution_key'], '5')
        dialog._resolution_error('Dos ramales posibles')
        QTest.qWait(10)
        self.assertEqual(len(FakeOutletSelectionDialog.instances), 1)
        selector.reject()
        self.assertIsNone(dialog._outlet_selector)
        self.assertIs(dialog.analysis, old)
        self.assertEqual((dialog._active_resolution_key, controller.active_key), ('25', '25'))
        self.assertFalse(controller.busy)
        self.assertFalse(dialog._resolution_pending)

    def test_save_and_output_close_selector_and_ignore_late_acceptance(self):
        self.configure()
        dialog, controller = self.dialog, self.controller
        selector = self.open_selector()
        context = deepcopy(selector.context)
        ready = []
        with patch.object(controller, 'ensure_all', side_effect=lambda callback: ready.append(callback)), \
                patch.object(controller, 'select_outlet', wraps=controller.select_outlet) as select, \
                patch.object(dialog, '_prepare_resolution_states', side_effect=lambda complete: complete()), \
                patch.object(dialog, 'save_project') as save:
            dialog._choose_save_project()
            self.assertIsNone(dialog._outlet_selector)
            self.assertEqual(selector.rejections, 1)
            self.assertTrue(dialog._saving_project)
            selector.point = (dialog.analysis.outlet_x, dialog.analysis.outlet_y)
            dialog._apply_outlet_selection(selector, controller, context)
            select.assert_not_called()
            ready[0](controller.variants)
            self.assertFalse(dialog._saving_project)
            save.assert_called_once_with(str(self.helper.path))
        selector = self.open_selector()
        before = dialog.analysis
        dialog._state_for_output()
        self.assertIsNone(dialog._outlet_selector)
        self.assertEqual(selector.rejections, 1)
        self.assertIs(dialog.analysis, before)

    def test_selecting_unverified_variant_closes_selector_and_waits_for_reference_check(self):
        self.configure()
        dialog, controller = self.dialog, self.controller
        previous = dialog.analysis
        self.assertTrue(controller.needs_reference_check('5'))
        selector = self.open_selector()
        context = deepcopy(selector.context)
        with patch.object(controller, 'select_outlet', wraps=controller.select_outlet) as select:
            dialog._select_resolution('5')
            self.assertIsNone(dialog._outlet_selector)
            self.assertEqual(selector.rejections, 1)
            selector.point = (context['original_x'], context['original_y'])
            dialog._apply_outlet_selection(selector, controller, context)
            select.assert_not_called()
        # A cached automatic result is now checked against the reference
        # asynchronously. It must not replace the accepted basin prematurely.
        task = controller._task
        self.assertIsNotNone(task)
        self.assertEqual(task.key, '5')
        self.assertEqual(task.reference_signature, controller._reference_signature())
        self.assertTrue(controller.busy)
        self.assertTrue(dialog._resolution_pending)
        self.assertEqual(controller.requested_key, '5')
        self.assertEqual((dialog._active_resolution_key, controller.active_key), ('25', '25'))
        self.assertIs(dialog.analysis, previous)
        # Returning to the accepted variant cancels that verification and also
        # keeps a late completion from reviving the previous map selector.
        dialog._select_resolution('25')
        self.assertTrue(task.isCanceled())
        task.finished(False)
        self.assertFalse(controller.busy)
        self.assertFalse(dialog._resolution_pending)
        self.assertIsNone(dialog._outlet_selector)
        self.assertIs(dialog.analysis, previous)
        dialog._cancel_surface()

    def test_initial_500_uses_clicked_cell_then_allows_manual_adjustment_after_save_restore(self):
        coarse=self.helper.variant(500)
        center=(coarse['analysis'].outlet_x,coarse['analysis'].outlet_y)
        query=replace(coarse['query'],x=center[0]+220,y=center[1]+180)
        task=AnalysisTask(query,self.dialog._completed)
        self.dialog.query=query
        self.dialog._task=task
        with patch('caumax_consulta.terrain.analyze_basin',wraps=analyze_basin) as calculate:
            self.assertTrue(task.run(),task.error)
        calculate.assert_called_once()
        self.assertFalse(calculate.call_args.kwargs['upstream_only'])
        self.assertEqual((task.result.selected_x,task.result.selected_y),(query.x,query.y))
        self.assertEqual((task.result.outlet_x,task.result.outlet_y),center)
        self.assertEqual(task.result.geometry_wkb,coarse['analysis'].geometry_wkb)
        self.assertNotIn('outlet_snap',task.result.diagnostics)
        self.assertNotIn('outlet_review_pending',task.result.diagnostics)
        task.finished(True)
        controller=self.dialog._resolution_controller
        discovery=controller._task
        self.assertTrue(controller.busy)
        self.assertIn('centro de la celda del punto de consulta',self.dialog.context.text())
        QTest.qWait(10)
        self.assertIsNone(self.dialog._outlet_selector)
        self.dialog._queue_smoothing()
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()
        surface=self.helper.manager.tasks[-1]
        self.assertTrue(self.dialog._surface_pending)
        discovery.payload={'options':[],'engine':{'available':False,'reason':'Prueba sin red'}}
        discovery.finished(True)
        QTest.qWait(10)
        self.assertIsNone(self.dialog._outlet_selector)
        self.helper.publish(surface)
        QTest.qWait(10)
        self.assertIsNone(self.dialog._outlet_selector)
        self.assertEqual(FakeOutletSelectionDialog.instances,[])
        self.assertFalse(self.dialog._outlet_offer_pending)
        self.assertEqual((self.dialog._marker.center().x(),self.dialog._marker.center().y()),center)
        self.assertNotIn('pendiente de revisión',self.dialog.status.text())
        path=self.helper.fixture.folder/'containing_cell500.caumax.json'
        self.dialog.save_project(path)
        saved=load_session(path)
        self.assertEqual((saved.analysis.outlet_x,saved.analysis.outlet_y),center)
        self.assertEqual((saved.analysis.selected_x,saved.analysis.selected_y),(query.x,query.y))
        self.assertNotIn('outlet_review_pending',saved.analysis.diagnostics)
        self.dialog._restore_session(saved)
        self.assertNotIn('pendiente de revisión',self.dialog.status.text())
        self.assertEqual(self.dialog.outlet_adjust_button.text(),'Ajuste Punto de Cierre')
        selector=self.open_selector()
        self.assertEqual(selector.context['manual_selection_scope'],'raster_extent')
        self.assertEqual((selector.context['original_x'],selector.context['original_y']),(query.x,query.y))
        self.assertEqual((selector.context['initial_x'],selector.context['initial_y']),center)
        selector.choose(query.x,query.y+500)
        manual=self.helper.manager.tasks[-1]
        ok=manual.run()
        self.assertTrue(ok,manual.error)
        manual.finished(ok)
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()
        self.helper.publish(self.helper.manager.tasks[-1])
        self.assertNotIn('outlet_review_pending',self.dialog.analysis.diagnostics)
        self.assertNotIn('pendiente de revisión',' '.join(self.dialog.analysis.notes))
        self.assertNotIn('pendiente de revisión',self.dialog.status.text())
        self.assertEqual((self.dialog.analysis.outlet_x,self.dialog.analysis.outlet_y),
                         (center[0],center[1]+500))
        self.assertEqual((self.dialog.query.x,self.dialog.query.y),(query.x,query.y))
        self.assertIs(self.dialog._outlet_selector,selector)
        self.assertFalse(selector.busy)

    def test_initial_raster_error_or_cancellation_never_becomes_a_fallback_basin(self):
        for error,cancel in ((ValueError('MDT NoData'),False),
                             (InterruptedError(),True)):
            with self.subTest(cancel=cancel):
                task=AnalysisTask(self.dialog.query,lambda *_:None)
                if cancel:task.cancel()
                with patch('caumax_consulta.terrain.analyze_basin',side_effect=error) as calculate:
                    self.assertFalse(task.run())
                self.assertEqual(calculate.call_count,1)
                self.assertIsNone(task.result)

    def test_clear_owned_layers_survives_background_refresh_then_manual_choice_redraws(self):
        self.configure()
        dialog=self.dialog
        actions=dialog.outlet_adjust_button.parentWidget().layout()
        row=next(actions.itemAt(i).layout() for i in range(actions.count())
                 if actions.itemAt(i).layout() is not None
                 and actions.itemAt(i).layout().indexOf(dialog.outlet_adjust_button)>=0)
        actions_found = [row.itemAt(i).widget() for i in range(row.count())
                         if row.itemAt(i).widget() is not None]
        for control in (dialog.cell_grid_check, dialog.new_selection_button,
                        dialog.outlet_adjust_button, dialog.clear_delineation_button):
            self.assertIn(control, actions_found)
        self.assertTrue(all(not button.icon().isNull() for button in
                            (dialog.new_selection_button, dialog.outlet_adjust_button,
                             dialog.clear_delineation_button)))
        project=QgsProject.instance()
        user=QgsVectorLayer('Point?crs=EPSG:25830','Capa del usuario','memory')
        project.addMapLayer(user)
        user_id=user.id()
        before=set(project.mapLayers())
        self.assertIsNotNone(dialog._marker)
        dialog.clear_delineation_button.click()
        remaining=set(project.mapLayers())
        self.assertIn(user_id,remaining)
        self.assertLess(len(remaining),len(before))
        self.assertTrue(dialog._delineation_cleared)
        self.assertIsNone(dialog._marker)
        self.assertFalse(dialog.clear_delineation_button.isEnabled())
        dialog._display_analysis()
        dialog._sync_boundary_layers()
        dialog._preview()
        dialog._queue_smoothing()
        dialog._smoothing_timer.stop()
        dialog._update_smoothing()
        self.helper.publish(self.helper.manager.tasks[-1])
        self.assertEqual(set(project.mapLayers()),remaining)
        self.assertIsNone(dialog._marker)
        native=self.finish_manual(dialog.query.x,dialog.query.y+25)
        self.assertFalse(dialog._delineation_cleared)
        self.assertGreater(len(project.mapLayers()),len(remaining))
        self.assertIn(user_id,project.mapLayers())
        self.assertIsNotNone(dialog._marker)
        self.assertEqual((dialog.analysis.outlet_x,dialog.analysis.outlet_y),
                         (native.outlet_x,native.outlet_y))
        self.assertTrue(dialog.clear_delineation_button.isEnabled())

    def test_global_clear_cancels_other_initial_worker_and_ignores_queued_completion(self):
        self.configure()
        coarse=self.helper.variant(500)
        query=coarse['query']
        with patch.object(BasinDialog,'_start'):
            pending=BasinDialog(query,self.helper.fixture.iface)
        self.helper.fixture.dialogs.append(pending)
        pending.setAttribute(Qt.WA_DontShowOnScreen,True)
        task=AnalysisTask(query,pending._completed)
        pending._task=task
        self.assertIsNone(pending.analysis)
        self.assertFalse(pending._delineation_cleared)
        # Finish numerical work, but hold the callback as Qt can do while the
        # user presses Clear. Even a successful queued result must be ignored.
        self.assertTrue(task.run(),task.error)
        self.dialog.clear_delineation_button.click()
        remaining=set(QgsProject.instance().mapLayers())
        self.assertTrue(pending._delineation_cleared)
        self.assertFalse(pending._disposed)
        self.assertFalse(self.dialog._disposed)
        self.assertTrue(task.isCanceled())
        self.assertIsNone(pending._task)
        task.finished(True)
        QTest.qWait(10)
        self.assertIsNone(pending.analysis)
        self.assertIsNone(pending._resolution_controller)
        self.assertFalse(pending._smoothing_timer.isActive())
        self.assertTrue(pending._delineation_cleared)
        self.assertIsNone(pending._marker)
        self.assertIsNone(pending._outlet_selector)
        self.assertEqual(set(QgsProject.instance().mapLayers()),remaining)
        self.assertTrue(pending._surface_valid)
        self.assertFalse(pending._smoothing_ready)
        self.assertFalse(pending.report_button.isEnabled())
        self.assertFalse(pending.save_project_button.isEnabled())

    def test_clear_during_manual_worker_keeps_complete_geometry_for_outputs_without_redrawing(self):
        self.configure()
        dialog=self.dialog
        previous=dialog.analysis
        selector=self.open_selector()
        selector.choose(dialog.query.x,dialog.query.y+25)
        task=self.helper.manager.tasks[-1]
        # This new manual result has finished computing, but is not yet
        # accepted by the dialog when the user clears every drawing.
        self.assertTrue(task.run(),task.error)
        dialog._clear_delineation()
        remaining=set(QgsProject.instance().mapLayers())
        self.assertTrue(task.isCanceled())
        self.assertIsNone(self.controller._task)
        task.finished(True)
        QTest.qWait(10)
        self.assertIs(dialog.analysis,previous)
        self.assertFalse(dialog._resolution_pending)
        self.assertFalse(dialog._smoothing_timer.isActive())
        self.assertTrue(dialog._delineation_cleared)
        self.assertIsNone(dialog._marker)
        self.assertEqual(set(QgsProject.instance().mapLayers()),remaining)
        self.assertTrue(dialog._surface_valid)
        self.assertTrue(dialog._smoothing_ready)
        self.assertTrue(dialog.export_button.isEnabled())
        with patch('caumax_consulta.map_capture.capture_basin_map',side_effect=real_capture_basin_map):
            state=dialog._state_for_output()
        self.assertTrue(state['map_png'].startswith(b'\x89PNG'))
        self.assertEqual(set(QgsProject.instance().mapLayers()),remaining)
        with patch('caumax_consulta.reports.ask_basin_pdf') as report, \
                patch('caumax_consulta.smooth_export.choose_export_destination') as export:
            dialog._report()
            dialog._export()
            report.assert_called_once()
            export.assert_called_once()
        self.assertIsNone(dialog._outlet_selector)
        self.assertEqual(set(QgsProject.instance().mapLayers()),remaining)

    def test_closed_manual_selector_is_not_reoffered_by_its_late_error_and_can_be_reopened(self):
        self.configure()
        dialog,controller=self.dialog,self.controller
        original=dialog.analysis
        selector=self.open_selector()
        selector.choose(dialog.query.x,dialog.query.y+25)
        task=self.helper.manager.tasks[-1]
        context=deepcopy(selector.context)
        selector.reject()
        task.error='No se pudo leer la celda del cierre elegido.'
        task.selection_context=context
        task.finished(False)
        QTest.qWait(20)
        self.assertIsNone(dialog._outlet_selector)
        self.assertEqual(len(FakeOutletSelectionDialog.instances),1)
        self.assertIn('No se pudo leer',dialog.resolution_status.text())
        self.assertIs(dialog.analysis,original)
        self.assertTrue(dialog.outlet_adjust_button.isEnabled())
        native=self.finish_manual(dialog.query.x-25,dialog.query.y)
        self.assertEqual(len(FakeOutletSelectionDialog.instances),2)
        self.assertIsNotNone(dialog._outlet_selector)
        self.assertFalse(dialog._outlet_selector.busy)
        self.assertEqual((dialog.analysis.outlet_x,dialog.analysis.outlet_y),
                         (native.outlet_x,native.outlet_y))


if __name__ == '__main__':
    unittest.main()
