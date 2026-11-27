"""No blocking message boxes after the user confirms cleaning."""
import unittest
from unittest.mock import MagicMock, patch, sentinel

from caumax_consulta import clean_layers_ui as ui


class CleanupDialogTests(unittest.TestCase):
    def test_post_cleanup_failures_use_qgis_log_not_a_new_message_box(self):
        plan = dict(layers={'layer': 'Divisoria anterior'},
                    files={'/tmp/obsolete.gpkg': [42, 1, 1]},
                    reference_groups=[], references_loading=False,
                    protected=[])
        result = dict(layers=1, removed=[], failed=['/tmp/obsolete.gpkg: bloqueado'])
        fake_box = MagicMock()
        fake_box.addButton.side_effect = [sentinel.remove, sentinel.cancel]
        fake_box.clickedButton.return_value = sentinel.remove
        with (patch.object(ui, 'QMessageBox') as box,
              patch.object(ui, 'QgsMessageLog') as log,
              patch('caumax_consulta.generated_cleanup.deletion_plan', return_value=plan),
              patch('caumax_consulta.generated_cleanup.delete_generated', return_value=result),
              patch('caumax_consulta.improved_storage.preserve_exports',
                    side_effect=OSError('sin espacio'))):
            box.return_value = fake_box
            message = ui.confirm_cleanup(None)
        self.assertIn('No se han podido eliminar 1 archivos', message)
        self.assertEqual(fake_box.exec.call_count, 1)  # confirmation BEFORE deleting
        self.assertEqual(log.logMessage.call_count, 2)  # errors logged, not modal
        box.warning.assert_not_called()
        box.information.assert_not_called()
        box.critical.assert_not_called()

    def test_cancel_never_deletes_and_has_no_followup(self):
        plan = dict(layers={'a': 'D8 antiguo'}, files={},
                    reference_groups=[], references_loading=False, protected=[])
        fake_box = MagicMock()
        fake_box.addButton.side_effect = [sentinel.remove, sentinel.cancel]
        fake_box.clickedButton.return_value = sentinel.cancel
        with (patch.object(ui, 'QMessageBox') as box,
              patch('caumax_consulta.generated_cleanup.deletion_plan', return_value=plan),
              patch('caumax_consulta.generated_cleanup.delete_generated') as delete):
            box.return_value = fake_box
            self.assertIsNone(ui.confirm_cleanup(None))
        self.assertEqual(fake_box.exec.call_count, 1)
        delete.assert_not_called()
        box.warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
