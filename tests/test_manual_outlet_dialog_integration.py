"""Manual selection wiring: real controller/dialog with a held map selector."""
from copy import deepcopy
from dataclasses import replace
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDialog

import test_regression_resolution_save as fixture
from caumax_consulta.resolution_controller import ResolutionController


class FakeOutletSelectionDialog(QDialog):
    instances = []

    def __init__(self, iface, context, parent=None):
        super().__init__(parent)
        self.context = deepcopy(context)
        self.point = None
        self.rejections = 0
        self.instances.append(self)

    def selection(self):
        return self.point

    def choose(self, x, y):
        self.point = (x, y)
        self.accept()

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
        self.assertNotEqual(first.geometry_wkb, old_state['base_geometry_wkb'])
        second = self.finish_manual(original_x - 25, original_y)
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

    def test_switching_variant_invalidates_the_previous_selector(self):
        self.configure()
        dialog, controller = self.dialog, self.controller
        selector = self.open_selector()
        context = deepcopy(selector.context)
        with patch.object(controller, 'select_outlet', wraps=controller.select_outlet) as select:
            dialog._select_resolution('5')
            self.assertIsNone(dialog._outlet_selector)
            self.assertEqual(selector.rejections, 1)
            selector.point = (context['original_x'], context['original_y'])
            dialog._apply_outlet_selection(selector, controller, context)
            select.assert_not_called()
        self.assertEqual((dialog._active_resolution_key, controller.active_key), ('5', '5'))
        self.assertEqual(dialog.analysis.cell_width, 5)
        dialog._cancel_surface()


if __name__ == '__main__':
    unittest.main()
