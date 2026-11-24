"""Live Qt lifecycle and coordinate contracts for the native drainage panel."""
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip, uic
from qgis.PyQt.QtCore import QObject, QRect, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsPointXY
from qgis.gui import QgsMapCanvas

from caumax_consulta.rational_layers_bridge import BUTTON_NAME, PANEL_NAME, RationalLayersBridge


class _PointTool(QObject):
    canvasClicked = pyqtSignal(object, object)


class RationalMethodDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        self.txtX, self.txtY = QLineEdit(self.body), QLineEdit(self.body)
        body.addWidget(self.txtX)
        body.addWidget(self.txtY)
        root.addWidget(self.body)
        self.horizontalLayout_2 = QHBoxLayout()
        self.btnClose = QPushButton('Cerrar', self)
        self.horizontalLayout_2.addStretch()
        self.horizontalLayout_2.addWidget(self.btnClose)
        root.addLayout(self.horizontalLayout_2)
        self.basinCalc = None
        self.tool = _PointTool(self)
        self.tool.canvasClicked.connect(self.updatePoint)

    def updatePoint(self, point, *unused):
        self.txtX.setText(f'{point.x():.1f}')
        self.txtY.setText(f'{point.y():.1f}')


RationalMethodDialog.__module__ = '_hidro_rational_layers_test.rationalmethoddialog'


class HeldPanel(QWidget):
    def __init__(self, iface, context_getter, parent):
        super().__init__(parent)
        self.context_getter = context_getter
        self.refresh_context = Mock()
        self.dispose = Mock()
        layout = QVBoxLayout(self)
        label = QLabel('Generación capas resolución mejorada', self)
        label.setMinimumHeight(300)
        layout.addWidget(label)


class RationalLayersBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:25830'))
        self.iface = SimpleNamespace(mainWindow=lambda: self.window, mapCanvas=lambda: self.canvas)
        self.temp = tempfile.TemporaryDirectory()
        self.paths = {}
        for key in ('MDT', 'FLOWDIRS'):
            path = Path(self.temp.name)/(key+'.tif')
            path.write_bytes(b'Only the file path contract is read by the bridge.')
            self.paths[key] = str(path)
        native = ModuleType(RationalMethodDialog.__module__)
        native.RationalMethodDialog = RationalMethodDialog
        layers = ModuleType('_hidro_rational_layers_test.layers')
        layers.MDT, layers.FLOWDIRS = 'MDT', 'FLOWDIRS'
        layers.getLayerPath = self.paths.__getitem__
        panel_module = ModuleType('caumax_consulta.rational_layers_panel')
        panel_module.RationalLayersPanel = HeldPanel
        self.modules = patch.dict(sys.modules, {
            native.__name__: native, layers.__name__: layers,
            panel_module.__name__: panel_module,
        })
        self.modules.start()
        self.dialog = RationalMethodDialog(self.window)
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialog.txtX.setText('342324.8')
        self.dialog.txtY.setText('4709533.9')
        self.dialog.resize(650, 600)
        self.dialog.show()
        self.app.processEvents()
        self.bridge = RationalLayersBridge(self.iface)

    def tearDown(self):
        self.bridge.stop()
        self.dialog.close()
        sip.delete(self.dialog)
        self.modules.stop()
        self.temp.cleanup()
        self.canvas.setLayers([])
        sip.delete(self.canvas)
        sip.delete(self.window)
        self.app.processEvents()

    def attach(self, expand=False):
        self.bridge.start()
        binding = self.bridge._bindings[id(self.dialog)]
        if expand:
            binding.button.click()
            self.app.processEvents()
        return binding

    def test_lazy_idempotent_binding_and_independent_current_query(self):
        original_items = [self.dialog.layout().itemAt(i) for i in range(self.dialog.layout().count())]
        binding = self.attach()
        self.bridge.start()
        self.assertEqual(len(self.dialog.findChildren(QPushButton, BUTTON_NAME)), 1)
        self.assertIsNone(binding.panel)
        self.assertIsNone(self.dialog.basinCalc)
        self.assertEqual(self.dialog.layout().itemAt(len(original_items)).widget(), binding.container)
        binding.button.click()
        query = binding.panel.context_getter()
        self.assertEqual((query.x, query.y), (342324.8, 4709533.9))
        self.assertEqual(query.crs.authid(), 'EPSG:25830')
        self.assertEqual(query.dem_path, self.paths['MDT'])
        self.assertEqual(query.directions_path, self.paths['FLOWDIRS'])
        self.assertIsNone(self.dialog.basinCalc)
        panel = binding.panel
        self.bridge.stop()
        panel.dispose.assert_called_once()
        self.assertIsNone(self.dialog.findChild(QWidget, PANEL_NAME))
        self.assertEqual(self.dialog.layout().count(), len(original_items))
        for index, item in enumerate(original_items):
            self.assertIs(self.dialog.layout().itemAt(index), item)

    def test_input_edits_refresh_immediately_and_keep_new_paths_without_old_calculator(self):
        binding = self.attach(expand=True)
        self.dialog.basinCalc = SimpleNamespace(pt=QgsPointXY(1, 2), crs='old', area=1)
        binding.panel.refresh_context.reset_mock()
        self.dialog.txtX.setText('342325,25')
        binding.panel.refresh_context.assert_called_once()
        new_path = Path(self.temp.name)/'new-mdt.tif'
        new_path.write_bytes(b'new')
        self.paths['MDT'] = str(new_path)
        self.bridge.scan()
        self.assertEqual(binding.panel.refresh_context.call_count, 2)
        query = binding.panel.context_getter()
        self.assertEqual(query.x, 342325.25)
        self.assertEqual(query.dem_path, str(new_path))

    def test_exact_click_and_crs_at_entry_survive_canvas_crs_change(self):
        binding = self.attach(expand=True)
        point = QgsPointXY(342324.84321, 4709533.91234)
        self.dialog.tool.canvasClicked.emit(point, Qt.LeftButton)
        self.assertEqual(self.dialog.txtX.text(), '342324.8')
        query = binding.panel.context_getter()
        self.assertEqual((query.x, query.y), (point.x(), point.y()))
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        self.assertEqual(binding.panel.context_getter().crs.authid(), 'EPSG:25830')
        self.dialog.txtX.setText('-4.7')
        self.dialog.txtY.setText('41.6')
        query = binding.panel.context_getter()
        self.assertEqual(query.crs.authid(), 'EPSG:4326')
        self.assertEqual((query.x, query.y), (-4.7, 41.6))

    def test_invalid_fields_paths_and_crs_fail_before_generation(self):
        binding = self.attach(expand=True)
        for invalid in ('', 'nan', 'inf', 'texto'):
            self.dialog.txtX.setText(invalid)
            with self.assertRaises(ValueError):
                binding.panel.context_getter()
        self.dialog.txtX.setText('1')
        for name in ('MDT', 'FLOWDIRS'):
            old = self.paths[name]
            self.paths[name] = ''
            with self.assertRaisesRegex(ValueError, 'archivo'):
                binding.panel.context_getter()
            self.paths[name] = old
        binding.input_crs = QgsCoordinateReferenceSystem()
        with self.assertRaisesRegex(ValueError, 'referencia'):
            binding.panel.context_getter()

    def test_collapse_restores_exact_layout_items_and_native_actions(self):
        original = [self.dialog.layout().itemAt(i) for i in range(self.dialog.layout().count())]
        listener = Mock()
        self.dialog.btnClose.clicked.connect(listener)
        binding = self.attach(expand=True)
        self.assertIsNotNone(binding.body_scroll)
        self.assertIs(self.dialog.btnClose.parentWidget(), self.dialog)
        self.assertLess(self.dialog.btnClose.mapTo(self.dialog, self.dialog.btnClose.rect().center()).y(), binding.container.y())
        self.dialog.btnClose.click()
        listener.assert_called_once()
        for unused in range(2):
            binding.button.click()
            self.app.processEvents()
            for index, item in enumerate(original):
                self.assertIs(self.dialog.layout().itemAt(index), item)
            self.assertIs(self.dialog.body.parentWidget(), self.dialog)
            binding.button.click()
            self.app.processEvents()
        self.dialog.close()
        binding.panel.dispose.assert_called_once()
        self.assertFalse(self.bridge._bindings)

    def test_foreign_named_control_is_not_adopted(self):
        foreign = QPushButton('Ajeno', self.dialog)
        foreign.setObjectName(BUTTON_NAME)
        self.bridge.start()
        self.assertFalse(self.bridge._bindings)
        self.bridge.stop()
        self.assertIs(self.dialog.findChild(QPushButton, BUTTON_NAME), foreign)

    def test_direct_native_destruction_disposes_the_panel_without_finished_signal(self):
        self.bridge.start()
        victim = RationalMethodDialog(self.window)
        victim.setAttribute(Qt.WA_DontShowOnScreen)
        victim.show()
        self.bridge.scan()
        binding = self.bridge._bindings[id(victim)]
        binding.button.click()
        dispose = binding.panel.dispose
        victim_id = id(victim)
        sip.delete(victim)
        dispose.assert_called_once()
        self.assertNotIn(victim_id, self.bridge._bindings)

    def test_original_native_ui_fits_small_and_large_screens_and_restores_items(self):
        root = Path(__file__).resolve().parents[1]
        source = Path(os.environ.get('HIDROCAUMAX_CAUMAX_SOURCE', str(root.parent/'inputs/caumax/caumax')))
        ui = source/'rationalmethoddialog.ui'
        if not ui.is_file():
            self.skipTest('Fuentes originales de CAUMAX no disponibles')
        self.dialog.close()
        sip.delete(self.dialog)
        self.dialog = RationalMethodDialog.__new__(RationalMethodDialog)
        QDialog.__init__(self.dialog, self.window)
        uic.loadUi(str(ui), self.dialog)
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialog.show()
        self.app.processEvents()
        original = [self.dialog.layout().itemAt(i) for i in range(self.dialog.layout().count())]
        binding = self.attach()
        for width, height in ((800, 600), (1200, 900)):
            with self.subTest(screen=(width, height)):
                screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))
                with patch.object(self.dialog, 'screen', return_value=screen):
                    binding.button.click()
                    self.app.processEvents()
                    self.bridge._fit_panel(binding)
                    self.app.processEvents()
                    self.assertFalse(binding.scroll.isHidden())
                    self.assertGreater(binding.scroll.height(), 80)
                    self.assertLessEqual(self.dialog.height(), height)
                    self.assertGreaterEqual(binding.scroll.width(), binding.container.width()-2)
                    for button in (self.dialog.btnReport, self.dialog.btnCalculate, self.dialog.btnClose):
                        bottom = button.mapTo(self.dialog, button.rect().bottomRight()).y()
                        self.assertLess(bottom, self.dialog.height())
                        self.assertGreater(button.mapTo(self.dialog, button.rect().topLeft()).y(), 0)
                    binding.button.click()
                    self.app.processEvents()
                    for index, item in enumerate(original):
                        self.assertIs(self.dialog.layout().itemAt(index), item)


if __name__ == '__main__':
    unittest.main()
