"""Update notices and explicit handoff to the QGIS plugin manager."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qgis.PyQt.QtCore import QObject, QSettings, pyqtSignal
from qgis.PyQt.QtWidgets import QDialog, QLineEdit, QMainWindow, QPushButton
from qgis.core import QgsApplication, QgsSettings
from qgis.gui import QgsMessageBar

ui = importlib.import_module('caumax_consulta.update_ui')
native = importlib.import_module('caumax_consulta.native_plugin')

INFO = dict(plugin_id='caumax_consulta', installed_version='1.2.6', version='1.2.7',
            download_url='https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.2.7/HidroCAUMAX_v1_2_7.zip',
            release_url='https://github.com/jmdiez2026/HidroCAUMAX/releases/tag/v1.2.7',
            repository_url=ui.REPOSITORY_URL)


class FakeChecker(QObject):
    available = pyqtSignal(object)
    completed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.starts = 0
        self.disposed = False
        self.cancelled = False

    def check(self):
        if self.busy or self.disposed:
            return False
        self.busy = True
        self.starts += 1
        return True

    def finish(self, status='available', detail='Resultado comprobado.', info=None):
        self.busy = False
        if status == 'available':
            self.available.emit(dict(INFO if info is None else info))
        self.completed.emit(status, detail)

    def cancel(self):
        if self.busy:
            self.cancelled = True
            self.finish('cancelled')

    def dispose(self):
        self.disposed = True
        self.cancel()


class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hidrocaumax-update-ui-')
        self.addCleanup(self.temporary.cleanup)
        self.settings = QgsSettings(str(Path(self.temporary.name)/'repositories.ini'), QSettings.IniFormat)
        self.window = QMainWindow()
        self.bar = QgsMessageBar(self.window)
        self.iface = SimpleNamespace(mainWindow=lambda:self.window, messageBar=lambda:self.bar)
        self.updater = Mock(busy=False)
        for mocked in (patch.object(ui, 'UpdateChecker', FakeChecker),
                       patch.object(ui, 'QgsSettings', lambda:self.settings),
                       patch.object(ui, 'controller', return_value=self.updater)):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.notifier = ui.UpdateNotifier(self.iface, self.window)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.notifier.dispose)
        self.manager = SimpleNamespace(reloadAndExportData=Mock(), showPluginManagerWhenReady=Mock())
        installer = patch.dict(sys.modules, {'pyplugin_installer':SimpleNamespace(instance=lambda:self.manager)})
        installer.start()
        self.addCleanup(installer.stop)

    def values(self):
        return {key:self.settings.value(key) for key in self.settings.allKeys()}

    def seed_repository(self, name, url, enabled=True, **extra):
        prefix='app/plugin_repositories/'+name+'/'
        self.settings.setValue(prefix+'url', url)
        self.settings.setValue(prefix+'enabled', enabled)
        for key,value in extra.items():
            self.settings.setValue(prefix+key, value)

    def test_automatic_check_has_no_modal_manager_settings_write_or_current_notice(self):
        before = self.values()
        self.notifier.check()
        self.notifier.checker.finish('current', 'No hay una versión más reciente.')
        self.assertEqual(self.bar.items(), [])
        self.assertEqual(self.values(), before)
        self.manager.reloadAndExportData.assert_not_called()
        self.manager.showPluginManagerWhenReady.assert_not_called()

    def test_update_notice_is_once_per_version_but_every_finished_check_runs_again(self):
        for expected in (1,2,3):
            self.notifier.check()
            self.notifier.checker.finish()
            self.assertEqual(self.notifier.checker.starts, expected)
            self.assertEqual(len(self.bar.items()), 1)
        item = self.bar.currentItem()
        self.assertIn('1.2.7', item.text())
        self.assertEqual(item.findChild(QPushButton).text(), 'Actualizar')
        self.manager.reloadAndExportData.assert_not_called()
        self.assertEqual(self.values(), {})

    def test_new_version_replaces_only_our_update_notice(self):
        other = self.bar.createMessage('Another plugin', 'Keep this message')
        self.bar.pushWidget(other)
        self.notifier.check()
        self.notifier.checker.finish()
        self.notifier.check()
        self.notifier.checker.finish(info=dict(INFO, version='1.2.8'))
        self.assertIn(other, self.bar.items())
        self.assertEqual(len(self.bar.items()), 2)
        self.assertIn('1.2.8', self.bar.currentItem().text())

    def test_manual_check_during_automatic_request_shares_get_and_reports_result(self):
        self.notifier.check()
        self.assertFalse(self.notifier.check(manual=True))
        self.assertEqual(self.notifier.checker.starts, 1)
        self.notifier.checker.finish('current', 'Versión actual comprobada.')
        self.assertIn('Versión actual comprobada.', self.bar.currentItem().text())

    def test_manual_errors_are_explicit_and_automatic_errors_are_silent(self):
        self.notifier.check()
        self.notifier.checker.finish('error', 'Sin conexión.')
        self.assertEqual(self.bar.items(), [])
        self.notifier.check(manual=True)
        self.notifier.checker.finish('timeout', 'Tiempo de espera agotado.')
        self.assertIn('Tiempo de espera agotado.', self.bar.currentItem().text())

    def test_manual_available_starts_verified_updater_without_manager(self):
        self.notifier.check()
        self.notifier.checker.finish()
        self.notifier.check(manual=True)
        self.notifier.checker.finish()
        self.assertEqual(len(self.bar.items()), 0)
        self.updater.start.assert_called_once_with(INFO)
        self.manager.showPluginManagerWhenReady.assert_not_called()

    def test_update_click_rechecks_and_installs_without_manager_or_repository_edit(self):
        self.notifier.check()
        self.notifier.checker.finish()
        self.assertEqual(self.values(), {})
        self.bar.currentItem().findChild(QPushButton).click()
        self.notifier.checker.finish()
        self.updater.start.assert_called_once_with(INFO)
        self.manager.reloadAndExportData.assert_not_called()
        self.manager.showPluginManagerWhenReady.assert_not_called()
        self.assertEqual(self.values(), {})

    def test_existing_repository_settings_are_preserved_including_disabled_and_auth(self):
        self.seed_repository('Personal name', ui.REPOSITORY_URL, enabled=False, valid=False, authcfg='sample')
        self.seed_repository('Company', 'https://company.example/plugins.xml', enabled=True, authcfg='other')
        before = self.values()
        self.notifier.open_manager()
        self.assertEqual(self.values(), before)
        self.assertIn('desactivado', self.bar.currentItem().text())
        self.manager.showPluginManagerWhenReady.assert_called_once_with(0)

    def test_repository_name_collision_gets_new_name_without_modifying_existing(self):
        self.seed_repository('HidroCAUMAX', 'https://unrelated.example/plugins.xml')
        before = self.values()
        name, enabled, created = ui.ensure_repository(self.settings)
        self.assertEqual((name, enabled, created), ('HidroCAUMAX (2)', True, True))
        after = self.values()
        self.assertTrue(all(after[key] == value for key,value in before.items()))
        self.assertEqual(after['app/plugin_repositories/HidroCAUMAX (2)/url'], ui.REPOSITORY_URL)

    def test_manager_filter_is_optional_and_uses_public_plugin_name(self):
        dialog = QDialog(self.window)
        dialog.setObjectName('QgsPluginManagerBase')
        field = QLineEdit(dialog)
        field.setObjectName('leFilter')
        self.notifier.open_manager()
        self.assertEqual(field.text(), 'HidroCAUMAX')
        self.assertFalse(self.notifier._filter_timer.isActive())
        dialog.deleteLater()

    def test_manager_error_is_nonmodal_and_does_not_try_to_install(self):
        self.manager.reloadAndExportData.side_effect = RuntimeError('unavailable')
        self.notifier.open_manager()
        self.assertIn('No se ha podido abrir el gestor', self.bar.currentItem().text())
        self.manager.showPluginManagerWhenReady.assert_not_called()

    def test_unload_cancels_disconnects_late_callbacks_and_removes_only_our_notices(self):
        other = self.bar.createMessage('Other', 'Preserve')
        self.bar.pushWidget(other)
        self.notifier.check()
        self.notifier.checker.finish()
        self.notifier.check(manual=True)
        self.notifier.dispose()
        self.assertTrue(self.notifier.checker.disposed)
        self.assertTrue(self.notifier.checker.cancelled)
        self.notifier.checker.finish()
        self.assertEqual(self.bar.items(), [other])
        self.assertFalse(self.notifier.check(manual=True))
        self.notifier.open_manager()
        self.manager.showPluginManagerWhenReady.assert_not_called()


class NativeWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.window = QMainWindow()
        self.addCleanup(self.window.deleteLater)
        self.iface = SimpleNamespace(mainWindow=lambda:self.window,
                                     addPluginToMenu=Mock(), removePluginMenu=Mock())
        self.notifier = Mock()
        for name in ('CaumaxBridge','RationalExportBridge','RationalInputsBridge','VisibleDemController'):
            mocked = patch.object(native, name)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(native, 'UpdateNotifier', return_value=self.notifier)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.plugin = native.NativeCaumaxPlugin(self.iface)
        self.plugin.initGui()
        self.addCleanup(self.plugin.unload)

    def test_loading_activation_and_manual_menu_are_wired(self):
        self.notifier.check.assert_called_once_with()
        self.assertEqual(self.plugin.update_action.text(), 'Buscar actualizaciones…')
        self.plugin.update_action.trigger()
        self.notifier.check.assert_called_with(manual=True)

    def test_reusing_existing_basin_window_still_checks_updates(self):
        query = SimpleNamespace(x=1, y=2, period=100, flow=3, directions_path='d', dem_path='m',
                                crs=SimpleNamespace(toWkt=lambda:'crs'))
        parent = object()
        previous = SimpleNamespace(query=query, show=Mock(), raise_=Mock(), activateWindow=Mock())
        self.plugin._dialogs[parent] = previous
        self.notifier.check.reset_mock()
        self.plugin._open_basin(parent, query)
        self.plugin._open_basin(parent, query)
        self.assertEqual(self.notifier.check.call_count, 2)
        self.assertEqual(previous.show.call_count, 2)
        self.plugin._dialogs.clear()

    def test_opening_saved_project_checks_even_before_file_dialog_choice(self):
        self.notifier.check.reset_mock()
        with patch.object(native.QFileDialog, 'getOpenFileName', return_value=('', '')):
            self.plugin._open_saved_project()
        self.notifier.check.assert_called_once_with()

    def test_disable_cancels_and_unload_disposes_update_checker_and_menu(self):
        self.plugin.set_enabled(False)
        self.notifier.cancel.assert_called()
        self.plugin.unload()
        self.notifier.dispose.assert_called_once()
        self.assertIn(unittest.mock.call(self.plugin.MENU, self.plugin.update_action),
                      self.iface.removePluginMenu.call_args_list)


if __name__ == '__main__':
    unittest.main()
