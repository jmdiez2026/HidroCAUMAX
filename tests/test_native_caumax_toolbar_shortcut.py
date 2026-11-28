"""Regression tests for the CAUMAX menu action invoked by the HidroCAUMAX logo."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QAction, QMainWindow, QMenu

from caumax_consulta.native_plugin import NativeCaumaxPlugin


class NativeCaumaxToolbarShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.window = QMainWindow()
        self.addCleanup(self.window.close)
        self.plugin_menu = QMenu('Complementos', self.window)
        self.caumax_menu = self.plugin_menu.addMenu('&CAUMAX')
        self.called = []
        self.native_action = QAction('CARGAR CAPAS CAUMAX', self.window)
        self.native_action.triggered.connect(lambda *_: self.called.append('native'))
        self.caumax_menu.addAction(self.native_action)
        self.plugin = SimpleNamespace(iface=SimpleNamespace(
            pluginMenu=lambda: self.plugin_menu,
            mainWindow=lambda: self.window))

    def test_exact_native_menu_label_is_triggered(self):
        NativeCaumaxPlugin._load_native_caumax_layers(self.plugin)
        self.assertEqual(self.called, ['native'])

    def test_legacy_native_menu_label_is_triggered(self):
        self.native_action.setText('CARGAR CAPAS DE CAUMAX')
        NativeCaumaxPlugin._load_native_caumax_layers(self.plugin)
        self.assertEqual(self.called, ['native'])

    def test_disabled_native_action_is_not_triggered(self):
        self.native_action.setEnabled(False)
        with patch('caumax_consulta.native_plugin.QMessageBox.warning') as warning:
            NativeCaumaxPlugin._load_native_caumax_layers(self.plugin)
        self.assertEqual(self.called, [])
        warning.assert_called_once()

    def test_unrelated_menu_action_is_not_triggered(self):
        self.native_action.setText('CONFIGURAR CAPAS CAUMAX')
        with patch('caumax_consulta.native_plugin.QMessageBox.warning') as warning:
            NativeCaumaxPlugin._load_native_caumax_layers(self.plugin)
        self.assertEqual(self.called, [])
        warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
