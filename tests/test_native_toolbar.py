"""Automatic tools and shared toolbar actions, including busy state and unload."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import QEvent, QObject, Qt, pyqtSignal
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QMainWindow, QToolBar
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer

from caumax_consulta import native_plugin as native
from caumax_consulta import map_services as maps


class QgisStartup(QObject):
    initializationCompleted = pyqtSignal()


class ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        for name in ('restore_exports', 'preserve_exports'):
            storage = patch('caumax_consulta.improved_storage.' + name)
            storage.start(); self.addCleanup(storage.stop)
        project = QgsProject.instance()
        before = set(project.mapLayers())
        self.addCleanup(lambda: project.removeMapLayers(list(set(project.mapLayers()) - before)))
        self.window = QMainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.startup = QgisStartup()
        self.messages = Mock()
        self.iface = SimpleNamespace(mainWindow=lambda: self.window,
            addToolBar=self.window.addToolBar, addPluginToMenu=Mock(), removePluginMenu=Mock(),
            messageBar=lambda: self.messages,
            initializationCompleted=self.startup.initializationCompleted)
        self.services = []
        for name in ('CaumaxBridge', 'RationalExportBridge', 'RationalInputsBridge',
                     'RationalLayersBridge', 'VisibleDemController', 'UpdateNotifier'):
            replacement = patch.object(native, name)
            self.services.append(replacement.start())
            self.addCleanup(replacement.stop)
        self.plugin = native.NativeCaumaxPlugin(self.iface)
        self.plugin.initGui()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.plugin.unload()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_six_buttons_share_menu_actions_and_help_is_last(self):
        p = self.plugin
        self.assertEqual(p._toolbar.objectName(), 'HidroCAUMAXToolbar')
        expected = [p.action, p.reference_action, p.open_action, p.clear_layers_action, p.update_action, p.help_action]
        self.assertEqual(p._toolbar.toolButtonStyle(), Qt.ToolButtonTextBesideIcon)
        self.assertEqual([a.iconText() for a in expected], ['HidroCAUMAX v.1.0', 'Capas', 'Abrir cuenca', 'Limpiar capas', 'Actualizar', 'Ayuda'])
        self.assertEqual(p._toolbar.actions(), expected)
        for action in expected:
            self.assertIn(unittest.mock.call('HidroCAUMAX', action), self.iface.addPluginToMenu.call_args_list)
            self.assertFalse(action.icon().isNull(), action.text())
            self.assertTrue(action.toolTip())
            self.assertIs(p._toolbar.widgetForAction(action).defaultAction(), action)

    def test_delete_button_calls_session_cleanup_and_shows_a_temporary_count(self):
        p = self.plugin
        with patch.object(native, 'clear_delineations', return_value=7) as cleanup:
            QTest.mouseClick(p._toolbar.widgetForAction(p.clear_layers_action), Qt.LeftButton)
        cleanup.assert_called_once_with(close_dialogs=False, include_exports=True, include_references=True)
        self.assertIn('7 capas', self.messages.pushMessage.call_args.args[1])
        self.assertEqual(self.messages.pushMessage.call_args.kwargs['duration'], 5)
        self.assertTrue(p.clear_layers_action.isEnabled())

    def test_delete_action_removes_every_owned_kind_but_keeps_other_layers(self):
        from caumax_consulta.delineation_cleanup import OWNER_PROPERTIES
        project = QgsProject.instance()
        before = set(project.mapLayers())
        self.addCleanup(lambda: project.removeMapLayers(list(set(project.mapLayers()) - before)))
        owned = []
        for index, key in enumerate(OWNER_PROPERTIES):
            layer = QgsVectorLayer('Point?crs=EPSG:25830', f'Resultado antiguo {index}', 'memory')
            layer.setCustomProperty(key, f'cuenca-{index}')
            project.addMapLayer(layer)
            owned.append(layer.id())
        foreign = QgsVectorLayer('Point?crs=EPSG:25830', 'HidroCAUMAX · MDT 5 m', 'memory')
        reference = QgsVectorLayer('Point?crs=EPSG:25830', 'Red estaciones AEMET', 'memory')
        reference.setCustomProperty('HidroCAUMAX/service_id', 'aemet')
        for layer in (foreign, reference):
            project.addMapLayer(layer)
        from caumax_consulta.layer_order import add_result_layer
        from caumax_consulta.delineation_cleanup import clear_delineations
        exported = QgsVectorLayer('Point?crs=EPSG:25830', 'Cuenca exportada', 'memory')
        add_result_layer(exported, project)
        exported_id = exported.id()
        clear_delineations()
        self.assertIs(project.mapLayer(exported_id), exported)
        # Restore the four owned kinds to exercise the full toolbar sweep.
        for index, key in enumerate(OWNER_PROPERTIES):
            layer = QgsVectorLayer('Point?crs=EPSG:25830', f'Resultado {index}', 'memory')
            layer.setCustomProperty(key, f'cuenca-{index}')
            project.addMapLayer(layer)
            owned.append(layer.id())
        self.plugin.clear_layers_action.trigger()
        self.assertTrue(all(project.mapLayer(key) is None for key in owned))
        self.assertIs(project.mapLayer(foreign.id()), foreign)
        self.assertIs(project.mapLayer(reference.id()), reference)
        self.assertIsNone(project.mapLayer(exported_id))
        self.assertIn('5 capas', self.messages.pushMessage.call_args.args[1])
        self.plugin.clear_layers_action.trigger()
        self.assertIn('No hay capas', self.messages.pushMessage.call_args.args[1])

    def test_logo_restores_tools_and_repeated_clicks_never_disable_them(self):
        p = self.plugin
        button = p._toolbar.widgetForAction(p.action)
        self.assertFalse(p.action.isCheckable())
        self.assertTrue(p._enabled)
        p.set_enabled(False)
        self.assertFalse(p._enabled)
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertTrue(p._enabled)
        for _ in range(3):
            p.action.trigger()
            self.assertTrue(p._enabled)
        for service in (p._bridge, p._rational, p._rational_inputs,
                        p._rational_layers, p._visible_dem):
            self.assertTrue(service.start.called)
        p._updates.check.reset_mock()
        QTest.mouseClick(p._toolbar.widgetForAction(p.update_action), Qt.LeftButton)
        p._updates.check.assert_called_once_with(manual=True)

    def test_clean_button_removes_references_and_resets_capas_action(self):
        project = QgsProject.instance()
        group = project.layerTreeRoot().addGroup(maps.ROOT_TITLE)
        group.setCustomProperty(maps.ROOT_PROPERTY, 'v1')
        reference = QgsVectorLayer('Point?crs=EPSG:25830', 'Referencia', 'memory')
        reference.setCustomProperty(maps.SERVICE_PROPERTY, 'aemet')
        reference.setCustomProperty(maps.LAYER_PROPERTY, 'stations')
        project.addMapLayer(reference, False); group.addLayer(reference)
        self.assertTrue(self.plugin.reference_action.isChecked())
        self.plugin.clear_layers_action.trigger()
        self.assertFalse(project.mapLayers())
        self.assertFalse(project.layerTreeRoot().findGroups())
        self.assertFalse(self.plugin.reference_action.isChecked())
        self.assertIn('1 capas', self.messages.pushMessage.call_args.args[1])

    def test_toolbar_visible_on_load_and_after_qgis_restores_hidden_state(self):
        toolbar = self.plugin._toolbar
        self.assertTrue(toolbar.isVisible())
        toolbar.hide()
        hidden_state = self.window.saveState()
        toolbar.show()
        self.assertTrue(self.window.restoreState(hidden_state))
        self.assertFalse(toolbar.isVisible())
        self.plugin.set_enabled(False)
        self.startup.initializationCompleted.emit()
        self.assertTrue(toolbar.isVisible())
        self.assertTrue(self.plugin._enabled)

    def test_reference_action_tracks_real_project_state_and_external_removals(self):
        p = self.plugin
        button = p._toolbar.widgetForAction(p.reference_action)
        project = QgsProject.instance()
        self.assertTrue(p.reference_action.isCheckable())
        self.assertFalse(p.reference_action.isChecked())
        foreign = QgsVectorLayer('Point?crs=EPSG:25830', 'Google Satellite', 'memory')
        project.addMapLayer(foreign)
        self.assertFalse(p.reference_action.isChecked())
        owned = QgsVectorLayer('Point?crs=EPSG:25830', 'Referencia renombrada', 'memory')
        owned.setCustomProperty(maps.SERVICE_PROPERTY, 'aemet')
        owned.setCustomProperty(maps.LAYER_PROPERTY, 'stations')
        project.addMapLayer(owned)
        self.assertTrue(p.reference_action.isChecked())
        self.assertTrue(button.isChecked())
        self.assertIn('Retirar', button.toolTip())
        project.removeMapLayer(owned.id())
        self.assertFalse(p.reference_action.isChecked())
        self.assertIn('Cargar', button.toolTip())
        # An emitted bool alone must never fabricate a busy or present state.
        p._map_services.busy_changed.emit(True)
        self.assertTrue(button.isEnabled())
        self.assertFalse(button.isChecked())

    def test_reference_button_can_cancel_then_reload_and_remove_from_toolbar(self):
        p = self.plugin
        loader = p._map_services
        button = p._toolbar.widgetForAction(p.reference_action)
        project = QgsProject.instance()
        loader.services = (next(s for s in maps.WMS_SERVICES if s.service_id == 'corine'),)
        loader.layer_factory = Mock(side_effect=lambda uri, title, provider:
            QgsVectorLayer('Point?crs=EPSG:25830', title, 'memory'))
        manager = SimpleNamespace(addTask=Mock())
        with patch.object(maps.QgsApplication, 'taskManager', return_value=manager), patch.object(loader, '_bar', return_value=None):
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertTrue(loader.busy)
            self.assertTrue(button.isEnabled())
            self.assertTrue(button.isChecked())
            self.assertIn('Cancelar', button.toolTip())
            canceled = next(iter(loader._tasks))
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertTrue(canceled.isCanceled())
            self.assertFalse(loader.busy)
            self.assertFalse(button.isChecked())
            # A retry while the provider is retiring resets the optimistic QAction check.
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertEqual(manager.addTask.call_count, 1)
            self.assertFalse(button.isChecked())
            self.assertFalse(canceled.run())
            canceled.finished(False)
            for _ in range(2):
                QTest.mouseClick(button, Qt.LeftButton)
                task = next(iter(loader._tasks))
                self.assertTrue(task.run())
                task.finished(True)
                self.assertTrue(button.isChecked())
                self.assertIn('Retirar', button.toolTip())
                self.assertTrue(loader.has_references)
                QTest.mouseClick(button, Qt.LeftButton)
                self.assertFalse(loader.has_references)
                self.assertFalse(button.isChecked())
                self.assertFalse(project.mapLayers())
        self.assertEqual(loader.layer_factory.call_count, 2)

    def test_unload_removes_toolbar_and_reload_does_not_duplicate_it(self):
        p = self.plugin
        toolbar = p._toolbar
        p.unload()
        self.assertIsNone(p._toolbar)
        self.assertFalse(toolbar.isVisible())
        self.assertFalse(toolbar.actions())
        self.assertEqual(self.startup.receivers(self.startup.initializationCompleted), 0)
        self.startup.initializationCompleted.emit()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(self.window.findChildren(QToolBar, 'HidroCAUMAXToolbar'))
        self.plugin = native.NativeCaumaxPlugin(self.iface)
        self.plugin.initGui()
        self.assertEqual(len(self.window.findChildren(QToolBar, 'HidroCAUMAXToolbar')), 1)
        self.assertTrue(self.plugin._toolbar.isVisible())
        self.assertTrue(self.plugin._enabled)
        self.assertEqual(self.startup.receivers(self.startup.initializationCompleted), 1)
