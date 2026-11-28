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
from qgis.PyQt.QtCore import Qt
from qgis.gui import QgsMapCanvas, QgsMapToolEmitPoint

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
        # CAUMAX ships an independent immutable original pair; the rational
        # layer chooser may point to completely different 25/5 m sources.
        self.original_mdt = self._reference_grid(
            'original_mdt_500.tif', 500, 340000, 4648000)
        self.original_d8 = self._reference_grid(
            'original_d8_500.tif', 500, 340000, 4648000)
        layers_module._defaultLayerPaths = {
            'MDT': self.original_mdt, 'FLOWDIRS': self.original_d8,
        }
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
        self.assertEqual((query.dem_path, query.directions_path),
                         (self.original_mdt, self.original_d8))
        with self.assertRaises(FrozenInstanceError):
            query.x = 0
        self.dialog.txtX.setText('123')
        self.assertEqual(query.x, 341825)

    def _reference_grid(self, name, cell, x0, y0, *, nodata=None):
        """Create official and fine grid fixtures without CAUMAX settings."""
        path = self.folder / name
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 10, 10, 1,
                                                 gdal.GDT_Float32)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        ds.SetProjection(srs.ExportToWkt())
        ds.SetGeoTransform((x0, cell, 0, y0, 0, -cell))
        band = ds.GetRasterBand(1)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        band.Fill(128 if 'd8' in name else 250)
        ds = None
        return str(path)

    def test_new_selection_uses_original_caumax_map_tool_then_reopens_after_valid_flow(self):
        self.valid_query()
        self.bridge.start()
        # A previous basin was already opened before Nueva selección.
        self.basin_button().click()
        self.assertEqual(len(self.calls), 1)
        self.calls.clear()
        self.dialog.btnGetPoint = QPushButton('Seleccionar en mapa', self.dialog)
        self.dialog.btnGetPoint.setCheckable(True)
        self.dialog.tool = QgsMapToolEmitPoint(self.canvas)
        previous = self.canvas.mapTool()
        self.dialog.btnGetPoint.clicked.connect(
            lambda checked: self.canvas.setMapTool(self.dialog.tool)
            if checked else self.canvas.setMapTool(previous))
        seen = []
        def native_point(point, button):
            seen.append((point.x(), point.y()))
            self.dialog.txtX.setText(f'{point.x():.1f}')
            self.dialog.txtY.setText(f'{point.y():.1f}')
            self.dialog.btnReport.setEnabled(False)
            self.dialog.txtResultReturnPeriod.setText('5')
            self.dialog.txtResultFlow.setText('35.2')
            self.dialog.lastValidPoint = QgsPointXY(point)
            self.dialog.btnReport.setEnabled(True)
        self.dialog.tool.canvasClicked.connect(native_point)
        started = []
        self.bridge.on_query_started = started.append
        self.assertTrue(self.bridge.start_auto_reselection(self.dialog))
        self.assertTrue(self.dialog.btnGetPoint.isChecked())
        self.assertIs(self.canvas.mapTool(), self.dialog.tool)
        self.assertTrue(self.bridge.auto_reselection_pending(self.dialog))
        self.assertFalse(started)
        self.assertFalse(self.calls)
        # The point signal comes from native CAUMAX; no synthetic new-Q
        # computation is allowed in the HidroCAUMAX integration.
        self.dialog.tool.canvasClicked.emit(QgsPointXY(341325, 4646200), Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(len(seen), 1)
        self.assertEqual(started, [self.dialog])
        self.assertEqual(len(self.calls), 1)
        native, query = self.calls[0]
        self.assertIs(native, self.dialog)
        self.assertEqual((query.x, query.y, query.period, query.flow),
                         (341325, 4646200, 5, 35.2))
        self.assertFalse(self.bridge.auto_reselection_pending(self.dialog))
        # A second ordinary CAUMAX point must not trigger the one-shot handoff.
        self.dialog.tool.canvasClicked.emit(QgsPointXY(341825, 4646700), Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(len(self.calls), 1)

    def test_new_selection_keeps_previous_basin_when_caumax_has_no_valid_flow(self):
        self.valid_query()
        self.bridge.start()
        self.basin_button().click()
        self.calls.clear()
        self.dialog.btnGetPoint = QPushButton('Seleccionar en mapa', self.dialog)
        self.dialog.btnGetPoint.setCheckable(True)
        self.dialog.tool = QgsMapToolEmitPoint(self.canvas)
        self.dialog.btnGetPoint.clicked.connect(
            lambda checked: self.canvas.setMapTool(self.dialog.tool) if checked else None)
        def invalid_point(point, button):
            self.dialog.txtX.setText(f'{point.x():.1f}')
            self.dialog.txtY.setText(f'{point.y():.1f}')
            self.dialog.btnReport.setEnabled(False)
        self.dialog.tool.canvasClicked.connect(invalid_point)
        self.assertTrue(self.bridge.start_auto_reselection(self.dialog))
        self.dialog.tool.canvasClicked.emit(QgsPointXY(342325, 4646200), Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(self.calls)
        self.assertTrue(self.bridge.auto_reselection_pending(self.dialog))
        self.dialog.btnGetPoint.click()
        self.assertFalse(self.bridge.auto_reselection_pending(self.dialog))
        self.assertFalse(self.calls)
        self.bridge.stop()
        self.assertFalse(self.bridge._bindings)

    def test_native_map_query_recovers_official_500m_when_fine_grid_outside(self):
        """Regression: 25/5 m rational custom settings must not strand Q(T)."""
        self.valid_query(period=5, flow='18')
        module = sys.modules['_caumax_bridge_fixture.layers']
        original = dict(self.paths)
        dem500 = self._reference_grid('official_dem_500.tif', 500, 340000, 4648000)
        d8500 = self._reference_grid('official_d8_500.tif', 500, 340000, 4648000)
        module._defaultLayerPaths = {'MDT': dem500, 'FLOWDIRS': d8500}
        for resolution in (25, 5):
            with self.subTest(resolution=resolution):
                custom_dem = self._reference_grid(
                    f'user_dem_{resolution}.tif', resolution, 500000, 4800000)
                custom_d8 = self._reference_grid(
                    f'user_d8_{resolution}.tif', resolution, 500000, 4800000)
                self.paths.update(MDT=custom_dem, FLOWDIRS=custom_d8)
                query = bridge_module.make_native_query(self.dialog, self.native_module)
                self.assertEqual((query.dem_path, query.directions_path),
                                 (dem500, d8500))
                self.assertEqual((query.x, query.y, query.flow, query.period),
                                 (341825, 4646700, 18, 5))
                self.assertEqual((self.paths['MDT'], self.paths['FLOWDIRS']),
                                 (custom_dem, custom_d8))
        self.paths.update(original)

    def test_recovered_500m_raster_delineates_without_off_grid_error(self):
        # Real GDAL/QGIS analysis of a native max-flow query on a river point
        # while Method rational points to a completely different 5 m tile.
        from caumax_consulta.basin_dialog import AnalysisTask
        self.valid_query(period=5, flow='18')
        module = sys.modules['_caumax_bridge_fixture.layers']
        official_dem = self._reference_grid('national_dem_500.tif', 500, 340000, 4648000)
        official_d8 = self._reference_grid('national_d8_500.tif', 500, 340000, 4648000)
        module._defaultLayerPaths = {'MDT': official_dem, 'FLOWDIRS': official_d8}
        self.paths.update(
            MDT=self._reference_grid('far_5m_dem.tif', 5, 500000, 4800000),
            FLOWDIRS=self._reference_grid('far_5m_d8.tif', 5, 500000, 4800000),
        )
        query = bridge_module.make_native_query(self.dialog, self.native_module)
        task = AnalysisTask(query, lambda *_: None)
        self.assertTrue(task.run(), task.error + '\n' + task.trace)
        self.assertGreater(task.result.cell_count, 0)
        self.assertEqual(task.result.cell_width, 500)
        self.assertEqual((query.x, query.y), (341825, 4646700))
        self.assertEqual(query.flow, 18)

    def test_map_query_always_ignores_even_valid_fine_rational_grids(self):
        """Fine 25/5 m coverage must never leak into a max-flow query."""
        self.valid_query(period=5, flow='29')
        module = sys.modules['_caumax_bridge_fixture.layers']
        module._defaultLayerPaths = {
            'MDT': self._reference_grid('orig_dem.tif', 500, 340000, 4648000),
            'FLOWDIRS': self._reference_grid('orig_d8.tif', 500, 340000, 4648000),
        }
        for cell in (25, 5):
            with self.subTest(cell=cell):
                fine = (
                    self._reference_grid(f'fine_dem_{cell}.tif', cell, 341825 - cell * 5, 4646700 + cell * 5),
                    self._reference_grid(f'fine_d8_{cell}.tif', cell, 341825 - cell * 5, 4646700 + cell * 5),
                )
                self.paths.update(MDT=fine[0], FLOWDIRS=fine[1])
                query = bridge_module.make_native_query(self.dialog, self.native_module)
                self.assertEqual((query.dem_path, query.directions_path),
                                 (module._defaultLayerPaths['MDT'],
                                  module._defaultLayerPaths['FLOWDIRS']))
                self.assertEqual((query.x, query.y), (341825, 4646700))
                self.assertEqual((self.paths['MDT'], self.paths['FLOWDIRS']), fine)

    def test_changing_rational_grid_paths_does_not_change_the_native_map_query(self):
        self.valid_query()
        module = sys.modules['_caumax_bridge_fixture.layers']
        official = (
            self._reference_grid('native_dem_same_point.tif', 500, 340000, 4648000),
            self._reference_grid('native_d8_same_point.tif', 500, 340000, 4648000),
        )
        fine = (
            self._reference_grid('fine_dem_same_point.tif', 25, 341700, 4646825),
            self._reference_grid('fine_d8_same_point.tif', 25, 341700, 4646825),
        )
        module._defaultLayerPaths = {'MDT': official[0], 'FLOWDIRS': official[1]}
        self.paths.update(MDT=official[0], FLOWDIRS=official[1])
        self.bridge.start()
        self.basin_button().click()
        self.assertEqual(self.calls[-1][1].dem_path, official[0])
        self.assertEqual(len(self.calls), 1)
        self.paths.update(MDT=fine[0], FLOWDIRS=fine[1])
        self.bridge.scan()
        self.basin_button().click()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.calls[-1][1].dem_path,
                          self.calls[-1][1].directions_path), official)
        self.assertEqual((self.paths['MDT'], self.paths['FLOWDIRS']), fine)
        self.assertEqual((self.calls[-1][1].x, self.calls[-1][1].y),
                         (341825, 4646700))
        self.assertFalse(self.bridge._bindings[id(self.dialog)].query_dirty)

    def test_invalid_custom_and_official_grids_give_explicit_error(self):
        self.valid_query()
        module = sys.modules['_caumax_bridge_fixture.layers']
        module._defaultLayerPaths = {
            'MDT': self._reference_grid('other_500_dem.tif', 500, 100000, 4400000),
            'FLOWDIRS': self._reference_grid('other_500_d8.tif', 500, 100000, 4400000),
        }
        self.paths.update(
            MDT=self._reference_grid('other_25_dem.tif', 25, 100000, 4400000),
            FLOWDIRS=self._reference_grid('other_25_d8.tif', 25, 100000, 4400000),
        )
        with self.assertRaisesRegex(ValueError, 'originales de CAUMAX'):
            bridge_module.make_native_query(self.dialog, self.native_module)


    def test_native_max_flow_does_not_consult_mutable_rational_layer_paths(self):
        # A successful CAUMAX Q(T) result requires only the original 500 m
        # pair. This prevents the regression even when custom rasters are
        # valid at the query point or the mutable config is inaccessible.
        self.valid_query()
        module = sys.modules['_caumax_bridge_fixture.layers']
        with patch.object(module, 'getLayerPath',
                          side_effect=AssertionError('Se ha leído la configuración racional')):
            result = bridge_module.make_native_query(self.dialog, self.native_module)
        self.assertEqual((result.dem_path, result.directions_path),
                         (self.original_mdt, self.original_d8))

    def test_missing_original_grid_does_not_fall_back_to_fine_rational_tiff(self):
        self.valid_query()
        module = sys.modules['_caumax_bridge_fixture.layers']
        fine = (
            self._reference_grid('alone_fine_dem_5.tif', 5, 341800, 4646725),
            self._reference_grid('alone_fine_d8_5.tif', 5, 341800, 4646725),
        )
        self.paths.update(MDT=fine[0], FLOWDIRS=fine[1])
        originals = module._defaultLayerPaths
        module._defaultLayerPaths = {'MDT': str(self.folder/'missing-original-dem.tif'),
                                     'FLOWDIRS': str(self.folder/'missing-original-d8.tif')}
        with self.assertRaisesRegex(ValueError, 'originales de CAUMAX'):
            bridge_module.make_native_query(self.dialog, self.native_module)
        self.assertEqual((self.paths['MDT'], self.paths['FLOWDIRS']), fine)
        module._defaultLayerPaths = originals

    def test_alternating_25_and_5_m_rational_settings_keeps_identical_originals(self):
        self.valid_query()
        initial = bridge_module.make_native_query(self.dialog, self.native_module)
        for resolution in (25, 5, 25, 500):
            with self.subTest(resolution=resolution):
                dem = self._reference_grid(
                    f'rational_dem_{resolution}.tif', resolution,
                    341825 - 5*resolution, 4646700 + 5*resolution)
                d8 = self._reference_grid(
                    f'rational_d8_{resolution}.tif', resolution,
                    341825 - 5*resolution, 4646700 + 5*resolution)
                self.paths.update(MDT=dem, FLOWDIRS=d8)
                new = bridge_module.make_native_query(self.dialog, self.native_module)
                self.assertEqual((new.dem_path, new.directions_path),
                                 (initial.dem_path, initial.directions_path))
                self.assertEqual((new.flow, new.period, new.x, new.y),
                                 (initial.flow, initial.period, initial.x, initial.y))

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

    def test_new_point_cleans_once_before_a_valid_result_and_polling_does_not_clean(self):
        self.valid_query()
        started = []
        self.bridge.on_query_started = started.append
        self.bridge.start()
        self.app.processEvents()
        self.assertEqual(started, [self.dialog])
        started.clear()
        # A new click arrives as separate X and Y signals. Cleanup must happen
        # on the first one, even while CAUMAX's native result is invalid.
        self.dialog.txtX.setText('341900')
        self.assertEqual(started, [self.dialog])
        self.assertFalse(self.basin_button().isEnabled())
        self.dialog.txtY.setText('4646800')
        for _ in range(3):
            self.bridge.scan()
            self.app.processEvents()
        self.assertEqual(started, [self.dialog])
        self.dialog.lastValidPoint = QgsPointXY(341900, 4646800)
        self.dialog.btnReport.setEnabled(True)
        self.app.processEvents()
        self.basin_button().click()
        self.bridge.scan()
        self.assertEqual(len(started), 1)
        self.assertEqual((self.calls[-1][1].x, self.calls[-1][1].y), (341900, 4646800))
        # A later point invalidates this result immediately, without needing
        # to press the extended basin button first.
        self.dialog.txtX.setText('341901')
        self.assertEqual(len(started), 2)

    def test_idle_native_query_does_not_reopen_caumax_raster_every_scan(self):
        # Reproduces continuous "Calculando..." load while the user is idle.
        # Once CAUMAX accepted the result, our observer must not repeatedly
        # construct QGIS raster providers to reconfirm the same coordinates.
        self.valid_query()
        with patch.object(self.native_module, 'getLayer',
                          wraps=self.native_module.getLayer) as reader:
            self.bridge.start()
            initial_reads = reader.call_count
            self.assertEqual(initial_reads, 1)
            for _ in range(30):
                self.bridge.scan()
            self.assertEqual(reader.call_count, initial_reads)
            self.dialog.txtResultFlow.setText('28,5')
            self.assertEqual(reader.call_count, initial_reads + 1)
            for _ in range(10):
                self.bridge.scan()
            self.assertEqual(reader.call_count, initial_reads + 1)
            self.dialog.btnReport.setEnabled(False)
            self.assertFalse(self.basin_button().isEnabled())
            self.dialog.btnReport.setEnabled(True)
            self.assertTrue(self.basin_button().isEnabled())

    def test_repeat_native_query_cleans_but_period_changes_and_reopening_do_not(self):
        self.valid_query()
        started = []
        self.bridge.on_query_started = started.append
        self.bridge.start()
        self.app.processEvents()
        started.clear()
        self.basin_button().click()
        self.basin_button().click()
        self.dialog.txtResultFlow.setText('30')
        self.bridge.scan()
        self.assertEqual(started, [])
        self.dialog.btnCalculate.click()
        self.assertEqual(started, [self.dialog])
        self.app.processEvents()
        self.dialog.radio2.setChecked(True)
        self.bridge.scan()
        self.assertEqual(started, [self.dialog])
        self.bridge.stop()
        self.dialog.txtX.setText('341999')
        self.app.processEvents()
        self.assertEqual(started, [self.dialog])

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
        self.assertEqual(button.height(),round(round(self.dialog.btnReport.height()*1.1)*1.1))
        self.assertGreaterEqual(button.minimumWidth(),button.fontMetrics().horizontalAdvance(button.text())+button.iconSize().width()+4)
        self.assertEqual(button.text(),'Análisis de la Cuenca')
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
            self.assertEqual(button.height(),round(round(native.btnReport.height()*1.1)*1.1))
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

    def test_gea_logo_is_muted_when_disabled_and_colorful_when_enabled(self):
        from qgis.PyQt.QtGui import QIcon, QPixmap
        original = QPixmap(str(bridge_module.LOGO_PATH))
        self.assertFalse(original.isNull())
        icon = bridge_module._gea_state_icon(original)
        size = QSize(100, 40)
        normal = icon.pixmap(size, QIcon.Normal)
        disabled = icon.pixmap(size, QIcon.Disabled)
        self.assertFalse(normal.isNull())
        self.assertFalse(disabled.isNull())
        normal_image, disabled_image = normal.toImage(), disabled.toImage()
        pixels = [(x, y) for y in range(min(normal_image.height(), disabled_image.height()))
                  for x in range(min(normal_image.width(), disabled_image.width()))
                  if normal_image.pixelColor(x, y).alpha() > 0
                  and disabled_image.pixelColor(x, y).alpha() > 0]
        self.assertTrue(pixels)
        self.assertTrue(any(normal_image.pixelColor(x, y).rgb() !=
                            disabled_image.pixelColor(x, y).rgb()
                            for x, y in pixels))


if __name__ == '__main__':
    unittest.main()
