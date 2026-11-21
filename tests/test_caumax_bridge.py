"""Isolated tests for native-dialog attachment and verified PDF opening.

No live QGIS instance, user project or real desktop PDF viewer is used.
"""
from dataclasses import FrozenInstanceError
import importlib
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QSize
from qgis.PyQt.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QMainWindow, QPushButton,
    QRadioButton, QVBoxLayout,
)
from qgis.core import QgsApplication, QgsPointXY, QgsRasterLayer
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta import caumax_bridge as bridge_module


class FileDialogFixture:
    destination = ''

    @staticmethod
    def getSaveFileName(*args, **kwargs):
        return FileDialogFixture.destination, 'PDF (*.pdf)'


class ExporterFixture:
    Success = 0
    status = 0
    contents = b'%PDF-1.7\nbridge fixture\n'
    error = None

    class PdfExportSettings:
        pass

    def __init__(self, layout):
        self.layout = layout

    def exportToPdf(self, path, settings):
        if self.error is not None:
            raise self.error
        if self.contents is not None:
            Path(path).write_bytes(self.contents)
        return self.status


_REPORT_NAMESPACE = {'QFileDialog': FileDialogFixture, 'QgsLayoutExporter': ExporterFixture}
exec('''
def createReport(self):
    filename, _ = QFileDialog.getSaveFileName(self, "Informe", "", "PDF")
    if not filename:
        return "cancelled"
    exporter = QgsLayoutExporter(object())
    exporter.exportToPdf(filename, QgsLayoutExporter.PdfExportSettings())
    self.report_calls.append(filename)
    return "completed"
''', _REPORT_NAMESPACE)


class MaxFlowDialog(QDialog):
    createReport = _REPORT_NAMESPACE['createReport']

    def __init__(self, parent=None):
        super().__init__(parent)
        self.report_calls = []
        self.txtX, self.txtY = QLineEdit(self), QLineEdit(self)
        self.txtResultFlow, self.txtResultReturnPeriod = QLineEdit(self), QLineEdit(self)
        self.btnCalculate, self.btnReport, self.btnClose = (
            QPushButton('Consultar', self), QPushButton('Informe PDF', self), QPushButton('Cerrar', self)
        )
        self.radio2, self.radio5 = QRadioButton('2', self), QRadioButton('5', self)
        self.buttonsAndLayers = {self.radio2: 'FLOW_2', self.radio5: 'FLOW_5'}
        layout = QVBoxLayout(self)
        for widget in (self.txtX, self.txtY, self.radio2, self.radio5,
                       self.txtResultFlow, self.txtResultReturnPeriod):
            layout.addWidget(widget)
        self.buttons_layout = QHBoxLayout()
        for widget in (self.btnCalculate, self.btnReport, self.btnClose):
            self.buttons_layout.addWidget(widget)
        layout.addLayout(self.buttons_layout)
        self.btnReport.clicked.connect(self.createReport)
        for widget in (self.txtX, self.txtY):
            widget.textChanged.connect(lambda: self.btnReport.setEnabled(False))
        for radio in self.buttonsAndLayers:
            radio.toggled.connect(lambda: self.btnReport.setEnabled(False))
        self.btnReport.setEnabled(False)


class FakeIface:
    def __init__(self, window, canvas):
        self.window, self.canvas = window, canvas

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='caumax_bridge_')
        self.folder = Path(self.temp.name)
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.window.setCentralWidget(self.canvas)
        self.iface = FakeIface(self.window, self.canvas)
        self.layers = {}
        self.paths = {}
        for period in (2, 5):
            path = self.folder / f'q{period}.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((340000, 500, 0, 4648000, 0, -500))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(25830)
            ds.SetProjection(srs.ExportToWkt())
            ds.GetRasterBand(1).WriteArray(np.full((5, 5), period * 10, dtype='float32'))
            ds = None
            key = f'FLOW_{period}'
            self.layers[key] = QgsRasterLayer(str(path), f'Caudal de prueba {period}')
            self.paths[key] = str(path)
        self.paths.update(MDT=str(self.folder / 'mdt.tif'), FLOWDIRS=str(self.folder / 'dir.tif'))
        layers_module = types.ModuleType('_caumax_bridge_fixture.layers')
        for key in self.paths:
            setattr(layers_module, key, key)
        layers_module.getLayerPath = self.paths.__getitem__
        layers_module.getLayer = self.layers.__getitem__
        self.native_module = types.ModuleType('_caumax_bridge_fixture.maxflowdialog')
        self.native_module.MaxFlowDialog = MaxFlowDialog
        self.native_module.getLayer = self.layers.__getitem__
        self.module_patch = patch.dict(sys.modules, {
            self.native_module.__name__: self.native_module,
            layers_module.__name__: layers_module,
        })
        self.module_patch.start()
        self.calls = []
        self.bridge = bridge_module.CaumaxBridge(self.iface, lambda d, q: self.calls.append((d, q)))
        self.dialog = MaxFlowDialog(self.window)
        self.dialog.show()
        self.opened = []
        self.opener_patch = patch.object(bridge_module, 'QDesktopServices',
                                        types.SimpleNamespace(openUrl=self.opened.append))
        self.opener_patch.start()
        FileDialogFixture.destination = ''
        ExporterFixture.status, ExporterFixture.error = 0, None
        ExporterFixture.contents = b'%PDF-1.7\nbridge fixture\n'

    def tearDown(self):
        self.bridge.stop()
        self.dialog.close()
        self.dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.layers.clear()
        self.opener_patch.stop()
        self.module_patch.stop()
        self.temp.cleanup()

    def valid_query(self, period=5, flow='27,4'):
        self.dialog.txtX.setText('341825.0')
        self.dialog.txtY.setText('4646700.0')
        (self.dialog.radio2 if period == 2 else self.dialog.radio5).setChecked(True)
        self.dialog.txtResultFlow.setText(flow)
        self.dialog.txtResultReturnPeriod.setText(str(period))
        self.dialog.lastValidPoint = QgsPointXY(341825, 4646700)
        self.dialog.btnReport.setEnabled(True)

    def basin_button(self):
        return self.dialog.findChild(QPushButton, bridge_module.BUTTON_NAME)

    def test_button_is_immediately_before_report_and_snapshot_keeps_native_point(self):
        self.valid_query()
        self.bridge.start()
        self.bridge.start()
        button = self.basin_button()
        self.assertIsNotNone(button)
        self.assertEqual(self.dialog.buttons_layout.indexOf(button) + 1,
                         self.dialog.buttons_layout.indexOf(self.dialog.btnReport))
        self.assertEqual(len(self.dialog.findChildren(QPushButton, bridge_module.BUTTON_NAME)), 1)
        self.assertIn('#D9F0FF', button.styleSheet())
        button.click()
        self.assertEqual(len(self.calls), 1)
        native, query = self.calls[0]
        self.assertIs(native, self.dialog)
        self.assertEqual((query.flow, query.period), (27.4, 5))
        self.assertEqual((query.x, query.y), (341825, 4646700))
        self.assertEqual((query.cell.x, query.cell.y), (341750, 4646750))
        self.assertEqual(query.crs.authid(), 'EPSG:25830')
        self.assertEqual(query.directions_path, self.paths['FLOWDIRS'])
        with self.assertRaises(FrozenInstanceError):
            query.x = 0
        self.dialog.txtX.setText('123')
        self.assertEqual(query.x, 341825)

    def test_enabled_change_and_changed_inputs_require_a_fresh_native_query(self):
        self.bridge.start()
        button = self.basin_button()
        self.assertFalse(button.isEnabled())
        self.valid_query()
        self.assertTrue(button.isEnabled(), 'EnabledChange must synchronize without a nonexistent signal.')
        self.dialog.txtX.setText('341826')
        self.assertFalse(button.isEnabled())
        self.dialog.btnReport.setEnabled(True)
        self.assertFalse(button.isEnabled(), 'Last successful point must still match the inputs.')
        self.valid_query()
        self.dialog.radio2.setChecked(True)
        self.dialog.btnReport.setEnabled(True)
        self.assertFalse(button.isEnabled(), 'An old T5 result must not become a T2 query.')
        self.assertFalse(self.calls)

    def test_logo_reserves_width_and_scales_label_and_height(self):
        self.valid_query()
        with patch.object(bridge_module.QTimer,'singleShot'):
            self.bridge.start()
        button=self.basin_button()
        self.app.processEvents()
        before_width=button.sizeHint().width()
        button.install_logo()
        self.app.processEvents()
        self.assertFalse(button.icon().isNull())
        self.assertGreater(button.sizeHint().width(),before_width)
        self.assertAlmostEqual(button.font().pointSizeF()/self.dialog.btnReport.font().pointSizeF(),1.1)
        self.assertEqual(button.height(),round(self.dialog.btnReport.height()*1.1))
        self.assertGreaterEqual(button.minimumWidth(),button.fontMetrics().horizontalAdvance(button.text())+button.iconSize().width()+4)
        self.assertEqual(button.text(),'Cuenca hidrográfica')
        self.assertLessEqual(button.iconSize().height(),button.height()-4)
        self.assertGreater(button.iconSize().width(),0)
        self.assertAlmostEqual(button.iconSize().width()/button.iconSize().height(),344/150,delta=.25)

    def test_only_successful_pdf_with_real_header_opens_the_exact_selected_path(self):
        self.valid_query()
        self.bridge.start()
        destination = self.folder / 'my chosen result.pdf'
        FileDialogFixture.destination = str(destination)
        self.dialog.btnReport.click()
        self.assertEqual(self.dialog.report_calls, [str(destination)])
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(os.path.normpath(self.opened[0].toLocalFile()), str(destination))
        self.assertIs(_REPORT_NAMESPACE['QFileDialog'], FileDialogFixture)
        self.assertIs(_REPORT_NAMESPACE['QgsLayoutExporter'], ExporterFixture)

    def test_cancel_failed_export_invalid_header_and_exception_never_open(self):
        self.valid_query()
        self.bridge.start()
        self.dialog.btnReport.click()  # Save dialog cancellation.
        self.assertFalse(self.opened)
        destination = self.folder / 'previous.pdf'
        destination.write_bytes(b'%PDF-1.7\nprevious successful report')
        FileDialogFixture.destination = str(destination)
        ExporterFixture.status, ExporterFixture.contents = 1, None
        self.dialog.btnReport.click()
        self.assertFalse(self.opened, 'A previous PDF must not mask a failed new export.')
        ExporterFixture.status, ExporterFixture.contents = 0, b'not a PDF'
        self.dialog.btnReport.click()
        self.assertFalse(self.opened)
        ExporterFixture.error = RuntimeError('fixture export failure')
        self.dialog.btnReport.click()
        self.assertFalse(self.opened)

    def test_stop_restores_only_original_slot_and_close_does_not_reattach(self):
        self.valid_query()
        other = []
        self.dialog.btnReport.clicked.connect(lambda: other.append('unrelated slot'))
        self.bridge.start()
        FileDialogFixture.destination = str(self.folder / 'restored.pdf')
        self.dialog.btnReport.click()
        self.assertEqual(len(other), 1)
        self.assertEqual(len(self.dialog.report_calls), 1)
        self.bridge.stop()
        self.bridge.stop()
        self.assertIsNone(self.basin_button())
        self.dialog.btnReport.click()
        self.assertEqual(len(other), 2)
        self.assertEqual(len(self.dialog.report_calls), 2, 'Native slot must be restored exactly once.')
        self.assertEqual(len(self.opened), 1, 'Stopping removes the automatic opening wrapper.')
        self.bridge.start()
        self.dialog.close()
        self.assertFalse(self.bridge._bindings)
        self.bridge.scan()
        self.assertFalse(self.bridge._bindings, 'Closed parent must not be kept alive by the watcher.')

    def test_clone_preserves_defaults_kwdefaults_and_closure(self):
        flag = []

        def make_method():
            namespace = dict(_REPORT_NAMESPACE)
            namespace['flag'] = flag
            exec('''
def outer():
    closed = flag
    def createReport(self, positional="default", *, keyword=7):
        closed.append((positional, keyword))
        filename, _ = QFileDialog.getSaveFileName(self)
        if filename:
            QgsLayoutExporter(None).exportToPdf(filename, QgsLayoutExporter.PdfExportSettings())
    return createReport
''', namespace)
            return namespace['outer']()

        function = make_method()
        bound = types.MethodType(function, self.dialog)
        cloned = bridge_module._clone_report(bound, bridge_module._ReportCapture())
        self.assertEqual(cloned.__func__.__defaults__, function.__defaults__)
        self.assertEqual(cloned.__func__.__kwdefaults__, function.__kwdefaults__)
        self.assertIs(cloned.__func__.__closure__, function.__closure__)
        self.assertIsNot(cloned.__func__.__globals__, function.__globals__)
        cloned()
        self.assertEqual(flag, [('default', 7)])

    def test_an_actual_native_dialog_class_is_recognized_without_installing_anything(self):
        installed = Path(os.environ.get('APPDATA', '')) / 'QGIS/QGIS3/profiles/default/python/plugins/caumax'
        if not (installed / 'maxflowdialog.py').is_file():
            self.skipTest('Installed CAUMAX is unavailable on this test host.')
        package = types.ModuleType('_caumax_bridge_installed')
        package.__path__ = [str(installed)]
        import qgis.utils
        previous_iface = qgis.utils.iface
        names_before = set(sys.modules)
        native = None
        try:
            qgis.utils.iface = self.iface
            sys.modules[package.__name__] = package
            module = importlib.import_module(package.__name__ + '.maxflowdialog')
            with patch.object(module.MaxFlowDialog, 'populateRivers', lambda self: None):
                native = module.MaxFlowDialog()
            native.show()
            native.txtX.setText('341825')
            native.txtY.setText('4646700')
            native.radioReturnPeriod5.setChecked(True)
            native.txtResultFlow.setText('29')
            native.txtResultReturnPeriod.setText('5')
            native.lastValidPoint = QgsPointXY(341825, 4646700)
            native.btnReport.setEnabled(True)
            with patch.object(bridge_module.QTimer,'singleShot'):
                self.bridge.start()
                self.bridge.scan()
            button = native.findChild(QPushButton, bridge_module.BUTTON_NAME)
            self.app.processEvents()
            dialog_size_before_logo=QSize(native.size())
            button.install_logo()
            self.app.processEvents()
            self.assertIsNotNone(button)
            self.assertFalse(button.icon().isNull())
            self.assertAlmostEqual(button.font().pointSizeF()/native.btnReport.font().pointSizeF(),1.1)
            self.assertEqual(button.height(),round(native.btnReport.height()*1.1))
            self.assertGreaterEqual(button.minimumWidth(),button.fontMetrics().horizontalAdvance(button.text())+button.iconSize().width()+4)
            self.assertGreaterEqual(native.width(),dialog_size_before_logo.width())
            self.assertTrue(button.isEnabled())
            self.assertIs(bridge_module.native_module(native), module)
            button.click()
            self.assertIs(self.calls[-1][0], native)
            self.assertEqual((self.calls[-1][1].x, self.calls[-1][1].y), (341825, 4646700))
        finally:
            self.bridge.stop()
            if native is not None:
                native.close()
                native.deleteLater()
            qgis.utils.iface = previous_iface
            for name in set(sys.modules) - names_before:
                if name == package.__name__ or name.startswith(package.__name__ + '.'):
                    sys.modules.pop(name, None)


if __name__ == '__main__':
    unittest.main()
