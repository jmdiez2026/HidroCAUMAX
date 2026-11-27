"""Live Qt lifecycle and coordinate contracts for the native drainage panel."""
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip, uic
from qgis.PyQt.QtCore import QEvent, QObject, QRect, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QFontDatabase, QIcon, QPixmap
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QVBoxLayout, QWidget,
)
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvas

from caumax_consulta.rational_layers_bridge import BASIN_BUTTON_NAME, BUTTON_NAME, FREQUENCY_BUTTON_NAME, PANEL_NAME, RationalLayersBridge


class _PointTool(QObject):
    canvasClicked = pyqtSignal(object, object)


class RationalMethodDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.body = QWidget(self)
        body = QVBoxLayout(self.body)
        self.txtX, self.txtY = QLineEdit(self.body), QLineEdit(self.body)
        self.txtX.setObjectName('txtX')
        self.txtY.setObjectName('txtY')
        body.addWidget(self.txtX)
        body.addWidget(self.txtY)
        root.addWidget(self.body)
        self.horizontalLayout_2 = QHBoxLayout()
        self.btnClose = QPushButton('Cerrar', self)
        self.horizontalLayout_2.addStretch()
        self.horizontalLayout_2.addWidget(self.btnClose)
        root.addLayout(self.horizontalLayout_2)
        self.basinCalc = None
        self.btnCalculate = QPushButton('Calcular caudal', self)
        self.btnCalculate.hide()
        self.txtP0, self.txtResultFlow, self.txtResultReturnPeriod = (QLineEdit(text,self)
                                                                  for text in ('12','27','100'))
        for field in (self.txtP0,self.txtResultFlow,self.txtResultReturnPeriod):
            field.hide()
        self.comboReturnPeriod = QComboBox(self)
        self.comboReturnPeriod.addItems(['5','100'])
        self.comboReturnPeriod.setCurrentText('100')
        self.comboReturnPeriod.hide()
        self.comboReturnPeriod.currentTextChanged.connect(lambda *_:self.btnCalculate.setEnabled(False))
        self.chkOrdinaryMaxFlood = QCheckBox(self)
        self.chkOrdinaryMaxFlood.hide()
        self.tool = _PointTool(self)
        self.tool.canvasClicked.connect(self.updatePoint)

    def updatePoint(self, point, *unused):
        self.txtX.setText(f'{point.x():.1f}')
        self.txtY.setText(f'{point.y():.1f}')


RationalMethodDialog.__module__ = '_hidro_rational_layers_test.rationalmethoddialog'


class HeldPanel(QWidget):
    def __init__(self, iface, context_getter, parent, on_basin_refined=None):
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
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts/segoeui.ttf'
        if font_path.is_file():
            QFontDatabase.addApplicationFont(str(font_path))

    def setUp(self):
        self.window = QMainWindow()
        # Match the native QGIS Windows dialog and avoid a missing offscreen
        # system font inflating the action row after its queued logo update.
        self.window.setFont(QFont('Segoe UI', 8))
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
        loader = patch('caumax_consulta.improved_layers.load_improved_layers', return_value={'failed': []})
        self.load_improved = loader.start()
        self.addCleanup(loader.stop)
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

    def test_improved_folder_is_not_loaded_when_opening_or_reopening_d8_dialog(self):
        binding = self.attach(expand=True)
        self.load_improved.assert_not_called()
        first = binding.layers_dialog
        binding.button.click()
        self.load_improved.assert_not_called()
        self.assertIs(binding.layers_dialog, first)

    def test_red_clean_button_is_last_at_right_and_uses_shared_cleanup(self):
        binding = self.attach()
        self.app.processEvents()
        clean = binding.clean_button
        self.assertIs(binding.action_row.itemAt(binding.action_row.count()-1).widget(), clean)
        self.assertEqual(clean.text(), 'Limpiar capas')
        self.assertFalse(clean.icon().isNull())
        self.assertIn('#FADBD8', clean.styleSheet())
        self.assertLess(binding.button.geometry().right(), clean.x())
        with patch('caumax_consulta.clean_layers_ui.confirm_cleanup', return_value='Limpieza terminada') as cleanup:
            clean.click()
        cleanup.assert_called_once_with(self.dialog)
        self.assertEqual(binding.error.text(), 'Limpieza terminada')

    def test_separate_gea_calculate_leaves_native_button_unchanged_and_cleans_up(self):
        button = self.dialog.btnCalculate
        original_size = QSize(19, 13)
        button.setIconSize(original_size)
        text, style = button.text(), button.styleSheet()
        clicked = Mock()
        button.clicked.connect(clicked)
        self.assertTrue(button.icon().isNull())
        requested = Mock()
        self.bridge.on_frequency_requested = requested
        binding = self.attach()
        self.app.processEvents()
        self.assertIs(self.dialog.btnCalculate, button)
        self.assertTrue(button.icon().isNull())
        self.assertEqual(button.iconSize(), original_size)
        self.assertTrue(binding.frequency_button.icon().isNull())
        self.assertEqual(binding.frequency_button.text(), 'Calcular')
        self.assertEqual(binding.action_row.indexOf(binding.frequency_button), 1)
        self.assertEqual((button.text(), button.styleSheet()), (text, style))
        button.setEnabled(True)
        button.click()
        clicked.assert_called_once()
        self.bridge.scan()
        binding.frequency_button.click()
        requested.assert_called_once_with(self.dialog)
        clicked.assert_called_once()
        button.setEnabled(False)
        self.bridge.scan()
        self.assertFalse(binding.frequency_button.isEnabled())
        self.bridge.stop()
        self.assertIsNone(self.dialog.findChild(QPushButton, FREQUENCY_BUTTON_NAME))
        self.assertTrue(button.icon().isNull())
        self.assertEqual(button.iconSize(), original_size)
        # Preserve a pre-existing icon too, rather than always clearing it.
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.red)
        original_icon = QIcon(pixmap)
        button.setIcon(original_icon)
        self.attach()
        self.bridge.stop()
        self.assertEqual(button.icon().cacheKey(), original_icon.cacheKey())
        self.assertEqual(button.iconSize(), original_size)

    def test_four_blue_buttons_share_one_gea_logo_and_restore_export_on_detach(self):
        from caumax_consulta.rational_export import RationalExportBridge, BUTTON_NAME as SAVE_NAME
        self.dialog.rubberBand = None
        exporter = RationalExportBridge(self.iface)
        try:
            exporter.start()
            button = self.dialog.findChild(QPushButton, SAVE_NAME)
            self.assertIsNotNone(button)
            binding = self.attach()
            self.app.processEvents()
            self.assertTrue(button.icon().isNull())
            self.assertTrue(button.isVisible())
            self.assertEqual(self.dialog.horizontalLayout_2.indexOf(button), -1)
            self.assertEqual(binding.action_row.indexOf(button), 2)
            self.assertEqual(binding.action_row.indexOf(binding.basin_button), 3)
            self.assertEqual(binding.action_row.indexOf(binding.button), 4)
            self.assertEqual(len(self.dialog.findChildren(QPushButton, SAVE_NAME)), 1)
            self.assertEqual(binding.container.findChildren(QPushButton, SAVE_NAME), [button])
            logos = binding.container.findChildren(QLabel, 'HidroCAUMAXSharedGEALogo')
            self.assertEqual(len(logos), 1)
            self.assertFalse(logos[0].pixmap().isNull())
            buttons = [binding.frequency_button, button, binding.basin_button, binding.button]
            self.assertEqual(len({b.geometry().center().y() for b in buttons}), 1)
            for b in buttons:
                self.assertTrue(b.icon().isNull())
                self.assertIn('#D9F0FF', b.styleSheet())
                self.assertGreaterEqual(b.width(), b.sizeHint().width())
            self.assertLessEqual(binding.button.geometry().right(), binding.button.parentWidget().width())
            clicked = Mock()
            with patch.object(exporter, 'export_dialog', clicked):
                button.setEnabled(True)
                button.click()
            clicked.assert_called_once_with(self.dialog)
            self.bridge.stop()
            self.assertTrue(button.isVisible())
            self.assertGreaterEqual(self.dialog.horizontalLayout_2.indexOf(button), 0)
            self.assertFalse(button.icon().isNull())
        finally:
            exporter.stop()
        self.assertIsNone(self.dialog.findChild(QPushButton, SAVE_NAME))

    def test_exporter_loaded_after_strip_joins_once_and_can_unload_first(self):
        from caumax_consulta.rational_export import RationalExportBridge, BUTTON_NAME as SAVE_NAME
        self.dialog.rubberBand = None
        binding = self.attach()
        exporter = RationalExportBridge(self.iface)
        try:
            exporter.start()
            button = self.dialog.findChild(QPushButton, SAVE_NAME)
            for _ in range(3):
                self.bridge.scan()
                exporter.scan()
            self.app.processEvents()
            self.assertEqual(binding.action_row.indexOf(button), 2)
            self.assertEqual(len(self.dialog.findChildren(QPushButton, SAVE_NAME)), 1)
            exporter.stop()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
            self.bridge.scan()
            self.assertIsNone(self.dialog.findChild(QPushButton, SAVE_NAME))
            self.assertTrue(binding.container.isVisible())
        finally:
            exporter.stop()

    def test_native_height_fits_content_preserves_width_and_restores_on_unload(self):
        self.app.processEvents()
        original = self.dialog.size()
        original_spacing = self.dialog.layout().spacing()
        binding = self.attach()
        self.app.processEvents()
        self.assertGreaterEqual(self.dialog.width(), original.width())
        self.assertLessEqual(binding.clean_button.geometry().right(), binding.clean_button.parentWidget().width())
        self.assertLess(self.dialog.height(), original.height())
        self.bridge.stop()
        self.app.processEvents()
        self.assertEqual(self.dialog.size(), original)
        self.assertEqual(self.dialog.layout().spacing(), original_spacing)

    def complete_parameters(self, binding):
        self.bridge.calculation_started(self.dialog,'parameters')
        x,y=map(float,(self.dialog.txtX.text(),self.dialog.txtY.text()))
        self.dialog.basinCalc=SimpleNamespace(
            pt=QgsPointXY(x,y),area=5625.,basinCells=np.ones((3,3),dtype=np.uint8),
            basinGeometry=[QgsGeometry.fromWkt('POLYGON ((0 0,75 0,75 75,0 75,0 0))')],
            geoTransform=(x-37.5,25.,0.,y+37.5,0.,-25.))
        self.dialog.btnCalculate.setEnabled(True)
        self.bridge.calculation_finished(self.dialog,'parameters',dict(completed=True,ready_writes=[False,True]))

    def test_native_decimal_presentation_keeps_native_python_inputs_unchanged(self):
        binding = self.attach()
        editor = self.dialog.findChild(QLineEdit, 'hidrocaumax_decimal_txtX')
        self.assertIsNotNone(editor)
        self.assertEqual(editor.text(), '342324,8')
        self.assertEqual(self.dialog.txtX.text(), '342324.8')
        self.assertIsNotNone(binding.decimal_presentation)
        self.bridge.stop()
        self.assertIsNone(self.dialog.findChild(QLineEdit, 'hidrocaumax_decimal_txtX'))
        self.assertEqual(self.dialog.txtX.text(), '342324.8')

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
        self.assertEqual(self.dialog.txtX.text(), '342324.84321')
        self.assertEqual(self.dialog.txtY.text(), '4709533.91234')
        query = binding.panel.context_getter()
        self.assertEqual((query.x, query.y), (point.x(), point.y()))
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        self.assertEqual(binding.panel.context_getter().crs.authid(), 'EPSG:25830')
        self.dialog.txtX.setText('-4.7')
        self.dialog.txtY.setText('41.6')
        with self.assertRaisesRegex(ValueError, 'UTM EPSG:25830'):
            binding.panel.context_getter()
        geo_point = QgsPointXY(-4.7, 41.6)
        from qgis.core import QgsCoordinateTransform, QgsProject
        expected = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem('EPSG:4326'),
            QgsCoordinateReferenceSystem('EPSG:25830'),
            QgsProject.instance()).transform(geo_point)
        self.dialog.tool.canvasClicked.emit(geo_point, Qt.LeftButton)
        query = binding.panel.context_getter()
        self.assertEqual(query.crs.authid(), 'EPSG:25830')
        self.assertAlmostEqual(query.x, expected.x(), places=7)
        self.assertAlmostEqual(query.y, expected.y(), places=7)
        self.assertAlmostEqual(float(self.dialog.txtX.text()), query.x, places=8)
        self.assertAlmostEqual(float(self.dialog.txtY.text()), query.y, places=8)

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
        with patch.object(self.bridge, '_native_crs', return_value=QgsCoordinateReferenceSystem()):
            with self.assertRaisesRegex(ValueError, 'EPSG:25830'):
                binding.panel.context_getter()

    def test_independent_d8_dialog_preserves_native_layout_and_reopens(self):
        original = [self.dialog.layout().itemAt(i)
                    for i in range(self.dialog.layout().count())]
        listener = Mock()
        self.dialog.btnClose.clicked.connect(listener)
        binding = self.attach()
        QTest.qWait(30)  # Settle queued logo sizes before measuring the parent.
        native_geometry = QRect(self.dialog.geometry())
        native_body = self.dialog.body
        binding.button.click()
        self.app.processEvents()
        window = binding.layers_dialog
        panel = binding.panel
        self.assertIsInstance(window, QDialog)
        self.assertTrue(window.isVisible())
        self.assertFalse(window.isModal())
        self.assertEqual(window.objectName(), 'HidroCAUMAXRationalLayersDialog')
        self.assertIs(binding.scroll.parentWidget(), window)
        self.assertIs(native_body.parentWidget(), self.dialog)
        self.assertIsNone(binding.body_scroll)
        self.assertEqual(self.dialog.geometry(), native_geometry)
        for index, item in enumerate(original):
            self.assertIs(self.dialog.layout().itemAt(index), item)

        # Clicking twice focuses the same dialog, it does not duplicate it.
        binding.button.click()
        self.app.processEvents()
        self.assertIs(binding.layers_dialog, window)
        window.close()
        self.app.processEvents()
        panel.dispose.assert_called_once()
        self.assertIsNone(binding.layers_dialog)
        self.assertIsNone(binding.panel)

        binding.button.click()
        self.app.processEvents()
        self.assertIsNotNone(binding.panel)
        self.assertIsNot(binding.layers_dialog, window)
        next_panel = binding.panel
        self.dialog.btnClose.click()
        listener.assert_called_once()
        self.dialog.close()
        next_panel.dispose.assert_called_once()
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

    def test_independent_d8_window_fits_screens_without_resizing_native_ui(self):
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
        original = [self.dialog.layout().itemAt(i)
                    for i in range(self.dialog.layout().count())]
        original_width = self.dialog.width()
        binding = self.attach()
        self.app.processEvents()
        self.app.processEvents()
        # Nested native group boxes update their cached minimum on a later
        # layout event. The fit must use that settled size, not leave a gap.
        self.assertEqual(self.dialog.width(), original_width)
        self.assertEqual(self.dialog.height(), self.dialog.minimumSizeHint().height())
        native_geometry = QRect(self.dialog.geometry())
        for width, height in ((800, 600), (1200, 900)):
            with self.subTest(screen=(width, height)):
                screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))
                with patch.object(self.dialog, 'screen', return_value=screen):
                    binding.button.click()
                    self.app.processEvents()
                    window = binding.layers_dialog
                    self.assertIsNotNone(window)
                    self.assertTrue(window.isVisible())
                    self.assertLessEqual(window.width(), width)
                    self.assertLessEqual(window.height(), height)
                    # This fixture contains only a short label; the window now
                    # follows its content instead of keeping a fixed 560 px height.
                    self.assertGreater(binding.scroll.height(), 40)
                    self.assertGreater(binding.scroll.width(), 300)
                    self.assertEqual(self.dialog.geometry(), native_geometry)
                    for index, item in enumerate(original):
                        self.assertIs(self.dialog.layout().itemAt(index), item)
                    window.close()
                    self.app.processEvents()
                    self.assertIsNone(binding.layers_dialog)

    def test_basin_button_precedes_generation_and_opens_native_basin_before_flow_exists(self):
        requested=Mock();self.bridge.on_basin_requested=requested
        binding=self.attach()
        self.assertFalse(binding.basin_button.isEnabled())
        self.complete_parameters(binding)
        self.app.processEvents()
        self.assertTrue(binding.basin_button.isEnabled())
        self.assertEqual(binding.basin_button.text(),'Descripción cuenca')
        self.assertTrue(binding.basin_button.icon().isNull())
        self.assertEqual(binding.action_row.indexOf(binding.basin_button),4)
        self.assertEqual(binding.action_row.indexOf(binding.button),5)
        self.assertEqual(binding.closure_button.text(), 'Guardar Cierre')
        self.assertEqual(binding.complete_button.text(), 'Guardar CUENCA')
        self.assertEqual(binding.button.text(), 'Mejorar resolución')
        self.assertTrue(binding.button.icon().isNull())
        self.assertEqual(len(self.dialog.findChildren(QPushButton,BASIN_BUTTON_NAME)),1)
        query=object();calculator=self.dialog.basinCalc
        with patch('caumax_consulta.rational_basin.capture_rational_query',return_value=query) as capture:
            binding.basin_button.click()
        capture.assert_called_once_with(self.dialog,flow_result=None,parameters_validated=True)
        requested.assert_called_once_with(self.dialog,query)
        self.assertIs(self.dialog.basinCalc,calculator)
        self.assertIsNone(binding.panel)

    def test_only_new_accepted_flow_with_unchanged_inputs_is_attached_to_the_basin(self):
        self.bridge.on_basin_requested=Mock()
        binding=self.attach();self.complete_parameters(binding)
        self.bridge.calculation_started(self.dialog,'flow')
        self.bridge.calculation_finished(self.dialog,'flow',dict(completed=True,flow_writes=[],period_writes=[]))
        self.assertIsNone(binding.flow_result)
        self.assertTrue(binding.basin_ready)
        self.assertEqual(self.dialog.txtResultFlow.text(),'27')
        self.bridge.calculation_started(self.dialog,'flow')
        self.bridge.calculation_finished(self.dialog,'flow',dict(completed=True,flow_writes=['27'],period_writes=['100']))
        self.assertEqual(binding.flow_result,dict(flow=27.,period=100.))
        with patch('caumax_consulta.rational_basin.capture_rational_query',return_value=object()) as capture:
            binding.basin_button.click()
        self.assertEqual(capture.call_args.kwargs['flow_result'],dict(flow=27.,period=100.))
        changed=Mock();self.bridge.on_query_started=changed
        self.dialog.txtP0.setText('13')
        changed.assert_called_once_with(self.dialog)
        self.assertIsNone(binding.flow_result)
        self.assertTrue(binding.basin_ready)
        self.dialog.comboReturnPeriod.setCurrentText('5')
        self.assertFalse(self.dialog.btnCalculate.isEnabled())
        self.assertTrue(binding.basin_button.isEnabled())
        with patch('caumax_consulta.rational_basin.capture_rational_query',return_value=object()) as capture:
            binding.basin_button.click()
        capture.assert_called_once_with(self.dialog,flow_result=None,parameters_validated=True)
        self.dialog.txtResultReturnPeriod.setText('5')
        self.bridge.calculation_started(self.dialog,'flow')
        self.dialog.txtP0.setText('14')
        self.bridge.calculation_finished(self.dialog,'flow',dict(completed=True,flow_writes=['27'],period_writes=['5']))
        self.assertIsNone(binding.flow_result)
        self.assertTrue(binding.basin_ready)

    def test_recalculation_coordinates_and_source_changes_invalidate_the_native_basin(self):
        self.bridge.on_basin_requested=Mock()
        changed=Mock();self.bridge.on_query_started=changed
        binding=self.attach();self.complete_parameters(binding)
        binding.query_opened=True
        self.bridge.calculation_started(self.dialog,'parameters')
        changed.assert_called_once_with(self.dialog)
        self.assertFalse(binding.basin_button.isEnabled())
        self.bridge.calculation_finished(self.dialog,'parameters',dict(completed=True,ready_writes=[False]))
        self.assertFalse(binding.basin_ready)
        self.bridge.scan()
        self.assertFalse(binding.basin_button.isEnabled())
        self.complete_parameters(binding)
        self.dialog.txtX.setText('342400')
        self.assertFalse(binding.basin_ready)
        self.complete_parameters(binding)
        binding.query_opened=True;changed.reset_mock()
        replacement=Path(self.temp.name)/'new-source.tif'
        replacement.write_bytes(b'New configured source')
        self.paths['MDT']=str(replacement)
        self.bridge.scan()
        changed.assert_called_once_with(self.dialog)
        self.assertFalse(binding.basin_ready)
        self.assertFalse(binding.basin_button.isEnabled())
        self.assertTrue(binding.button.isEnabled())

    def test_repeated_attachment_and_close_remove_both_owned_controls(self):
        self.bridge.on_basin_requested=Mock()
        binding=self.attach();self.complete_parameters(binding)
        self.bridge.scan();self.bridge.start()
        self.assertEqual(len(self.dialog.findChildren(QPushButton,BASIN_BUTTON_NAME)),1)
        self.bridge.stop()
        self.assertIsNone(self.dialog.findChild(QPushButton,BASIN_BUTTON_NAME))
        self.assertIsNone(self.dialog.findChild(QPushButton,BUTTON_NAME))


if __name__ == '__main__':
    unittest.main()
