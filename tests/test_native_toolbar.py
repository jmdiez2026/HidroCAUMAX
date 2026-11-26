"""Toolbar uses the exact menu actions, including busy/checked state and unload."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import QEvent, QObject, Qt, pyqtSignal
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QMainWindow, QToolBar
from qgis.core import QgsApplication

from caumax_consulta import native_plugin as native


class QgisStartup(QObject):
    initializationCompleted = pyqtSignal()


class ToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.window = QMainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.startup = QgisStartup()
        self.iface = SimpleNamespace(mainWindow=lambda: self.window,
            addToolBar=self.window.addToolBar, addPluginToMenu=Mock(), removePluginMenu=Mock(),
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

    def test_four_buttons_share_menu_actions_and_have_icons(self):
        p = self.plugin
        self.assertEqual(p._toolbar.objectName(), 'HidroCAUMAXToolbar')
        expected = [p.action, p.open_action, p.reference_action, p.update_action]
        self.assertEqual(p._toolbar.actions(), expected)
        for action in expected:
            self.assertIn(unittest.mock.call('HidroCAUMAX', action), self.iface.addPluginToMenu.call_args_list)
            self.assertFalse(action.icon().isNull(), action.text())
            self.assertTrue(action.toolTip())
            self.assertIs(p._toolbar.widgetForAction(action).defaultAction(), action)

    def test_clicks_and_checked_state_are_shared_with_the_menu(self):
        p = self.plugin
        button = p._toolbar.widgetForAction(p.action)
        self.assertTrue(button.isChecked())
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertFalse(p.action.isChecked())
        self.assertFalse(p._enabled)
        self.assertTrue(p._toolbar.isVisible())
        p.action.trigger()
        self.assertTrue(button.isChecked())
        self.assertTrue(p._enabled)
        p._updates.check.reset_mock()
        QTest.mouseClick(p._toolbar.widgetForAction(p.update_action), Qt.LeftButton)
        p._updates.check.assert_called_once_with(manual=True)

    def test_toolbar_visible_on_load_and_after_qgis_restores_hidden_state(self):
        toolbar = self.plugin._toolbar
        self.assertTrue(toolbar.isVisible())
        toolbar.hide()
        hidden_state = self.window.saveState()
        toolbar.show()
        self.assertTrue(self.window.restoreState(hidden_state))
        self.assertFalse(toolbar.isVisible())
        self.startup.initializationCompleted.emit()
        self.assertTrue(toolbar.isVisible())

    def test_reference_busy_state_disables_the_same_toolbar_button(self):
        p = self.plugin
        button = p._toolbar.widgetForAction(p.reference_action)
        p._map_services.busy_changed.emit(True)
        self.assertFalse(button.isEnabled())
        p._map_services.busy_changed.emit(False)
        self.assertTrue(button.isEnabled())

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
        self.assertEqual(self.startup.receivers(self.startup.initializationCompleted), 1)
