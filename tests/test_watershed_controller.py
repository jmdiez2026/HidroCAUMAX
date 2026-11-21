"""Isolated Qt/QGIS controller checks; never opens the user's QGIS project.

Run with QGIS's Python and QT_QPA_PLATFORM=offscreen. One real QgsTask test
exercises asynchronous completion; a controlled manager makes late/out-of-
order callbacks deterministic for the lifecycle tests.
"""
import math
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsPointXY, QgsProject, QgsRasterLayer,
)
from qgis.gui import QgsMapCanvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.basin import delineate
from caumax_consulta.grid import cell_at_point
from caumax_consulta.plugin import Query, QueryDialog
from caumax_consulta import watershed


class FakeIface:
    def __init__(self, window, canvas):
        self.window, self.canvas = window, canvas

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas


class HeldTaskManager:
    def __init__(self):
        self.tasks = []

    def addTask(self, task):
        self.tasks.append(task)
        return len(self.tasks)


class WatershedControllerTests(unittest.TestCase):
    GT = (341000, 500, 0, 4648000, 0, -500)

    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='caumax_controller_')
        self.folder = Path(self.temp.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        directions = np.zeros((5, 5), dtype=np.uint8)
        directions[1:4, 1:4] = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        self.directions = self.raster('directions.tif', directions, 255)
        self.dem = self.raster('dem.tif', np.arange(25).reshape(5, 5) + 100, -9999)
        self.q2_path = self.raster('q2.tif', np.full((5, 5), 12.0), -9999)
        self.q5_path = self.raster('q5.tif', np.full((5, 5), 27.0), -9999)
        self.layers = [QgsRasterLayer(str(self.q2_path), 'caudal dos'),
                       QgsRasterLayer(str(self.q5_path), 'caudal cinco')]
        for layer in self.layers:
            self.assertTrue(layer.isValid())
            QgsProject.instance().addMapLayer(layer)
        module = types.ModuleType('_caumax_controller_fixture.layers')
        module.FLOWDIRS, module.MDT = 'FLOWDIRS', 'MDT'
        paths = {'FLOWDIRS': str(self.directions), 'MDT': str(self.dem)}
        for period in (2, 5, 10, 25, 100, 500):
            key = f'FLOW_{period}'
            setattr(module, key, key)
            paths[key] = str(self.folder / f'q{period}.tif')
        module.getLayerPath = paths.__getitem__
        self.module_patch = patch.dict(sys.modules, {module.__name__: module})
        self.module_patch.start()
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(self.crs)
        self.window.setCentralWidget(self.canvas)
        self.iface = FakeIface(self.window, self.canvas)
        self.controllers, self.dialogs = [], []
        self.result = delineate(self.directions, self.dem, 342250, 4646750)

    def raster(self, name, array, nodata):
        array = np.asarray(array)
        path = self.folder / name
        kind = gdal.GDT_Byte if array.dtype == np.uint8 else gdal.GDT_Float32
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, kind)
        ds.SetGeoTransform(self.GT)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.WriteArray(array)
        band = ds = None
        return path

    def query(self, index=0, flow='default', x=342250, y=4646750):
        period = (2, 5)[index]
        if flow == 'default':
            flow = (12.0, 27.0)[index]
        layer = self.layers[index]
        extent = layer.extent()
        cell = cell_at_point(x, y, extent.xMinimum(), extent.yMinimum(),
                             extent.xMaximum(), extent.yMaximum(), 5, 5)
        return Query(period, flow, cell, layer.name(), layer.id(), self.crs)

    def create_controller(self, queries):
        dialog = QueryDialog(queries, self.window)
        self.dialogs.append(dialog)
        controller = watershed.WatershedController(dialog, self.iface)
        self.controllers.append(controller)
        dialog.watershed = controller
        dialog.show()
        return dialog, controller

    def held_manager(self):
        manager = HeldTaskManager()
        return manager, patch.object(watershed, 'QgsApplication',
                                     types.SimpleNamespace(taskManager=lambda: manager))

    def wait_for(self, condition, timeout=5000):
        stop = time.monotonic() + timeout / 1000
        while not condition() and time.monotonic() < stop:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertTrue(condition(), 'Timed out waiting for the QGIS worker callback.')

    def tearDown(self):
        for controller in self.controllers:
            controller.dispose()
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for layer in self.layers:
            QgsProject.instance().removeMapLayer(layer.id())
        self.layers.clear()
        self.module_patch.stop()
        self.temp.cleanup()

    def test_real_async_completion_exposes_matching_geometry_and_metrics(self):
        dialog, controller = self.create_controller([self.query()])
        self.assertFalse(controller.save.isEnabled())
        self.wait_for(lambda: controller.result is not None)
        self.assertEqual(controller.result.cell_count, 9)
        self.assertEqual(controller.fields['area'].text(), '2.250000 km²')
        self.assertEqual(controller.fields['perimeter'].text(), '6.000000 km')
        self.assertAlmostEqual(float(controller.fields['kc'].text()), 2 / math.sqrt(math.pi), places=5)
        self.assertAlmostEqual(float(controller.fields['ic'].text()), math.pi / 4, places=5)
        self.assertTrue(controller.save.isEnabled())
        self.assertIsNotNone(controller._rubber)
        self.assertAlmostEqual(controller._rubber.asGeometry().area(), 2250000)

    def test_nodata_does_not_schedule_or_leave_a_previous_basin(self):
        manager, context = self.held_manager()
        with context:
            dialog, controller = self.create_controller([self.query(), self.query(1, flow=None)])
            task = manager.tasks[0]
            self.assertTrue(task.run())
            task.finished(True)
            self.assertTrue(controller.save.isEnabled())
            dialog.combo.setCurrentIndex(1)
            self.assertEqual(len(manager.tasks), 1)
            self.assertIsNone(controller.result)
            self.assertFalse(controller.save.isEnabled())
            self.assertIsNone(controller._rubber)
            self.assertEqual(controller.fields['area'].text(), '—')
            self.assertIn('sin caudal válido', controller.status.text())

    def test_switching_return_period_keeps_basin_and_updates_the_flow_query(self):
        manager, context = self.held_manager()
        with context:
            dialog, controller = self.create_controller([self.query(), self.query(1)])
            task = manager.tasks[0]
            self.assertTrue(task.run())
            task.finished(True)
            previous_result = controller.result
            previous_area = controller.fields['area'].text()
            dialog.combo.setCurrentIndex(1)
            self.assertEqual(len(manager.tasks), 1, 'Same closure should reuse the computed basin.')
            self.assertIs(controller.result, previous_result)
            self.assertEqual(controller.fields['area'].text(), previous_area)
            self.assertEqual(controller._query.period, 5)
            self.assertEqual(controller._query.flow, 27.0)
            self.assertEqual(dialog.fields['flow'].text(), '27 m³/s')

    def test_out_of_order_completion_cannot_replace_new_query_result(self):
        manager, context = self.held_manager()
        with context:
            dialog, controller = self.create_controller(
                [self.query(), self.query(1, x=341250, y=4647750)])
            old = manager.tasks[0]
            dialog.combo.setCurrentIndex(1)
            current = manager.tasks[1]
            self.assertTrue(old.isCanceled())
            self.assertTrue(current.run())
            current.finished(True)
            self.assertEqual(controller.result.cell_count, 1)
            correct = controller.result
            # Model an already computed old result whose callback arrives late.
            old.result = self.result
            old.finished(True)
            self.assertIs(controller.result, correct)
            self.assertEqual(controller._query.period, 5)
            self.assertEqual(controller.result.outlet_x, 341250)
            self.assertEqual(controller.fields['area'].text(), '0.250000 km²')

    def test_closing_dialog_cancels_and_ignores_late_completion(self):
        manager, context = self.held_manager()
        with context:
            dialog, controller = self.create_controller([self.query()])
            task = manager.tasks[0]
            dialog.close()
            self.assertTrue(controller._disposed)
            self.assertTrue(task.isCanceled())
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            task.result = self.result
            task.finished(True)
            self.assertIsNone(controller.result)
            self.assertIsNone(controller._rubber)
            self.assertIsNone(controller._marker)

    def test_canvas_crs_change_reprojects_preview_and_outlet(self):
        manager, context = self.held_manager()
        with context:
            dialog, controller = self.create_controller([self.query()])
            task = manager.tasks[0]
            self.assertTrue(task.run())
            task.finished(True)
            source_geometry = QgsGeometry(controller._rubber.asGeometry())
            target = QgsCoordinateReferenceSystem('EPSG:3857')
            transform = QgsCoordinateTransform(self.crs, target, QgsProject.instance())
            expected = QgsGeometry(source_geometry)
            expected.transform(transform)
            self.canvas.setDestinationCrs(target)
            actual = controller._rubber.asGeometry().boundingBox()
            wanted = expected.boundingBox()
            for component in ('xMinimum', 'xMaximum', 'yMinimum', 'yMaximum'):
                self.assertAlmostEqual(getattr(actual, component)(), getattr(wanted, component)(), places=5)
            expected_point = transform.transform(QgsPointXY(342250, 4646750))
            self.assertAlmostEqual(controller._marker.center().x(), expected_point.x(), places=5)
            self.assertAlmostEqual(controller._marker.center().y(), expected_point.y(), places=5)
            self.assertNotAlmostEqual(actual.xMinimum(), source_geometry.boundingBox().xMinimum())


if __name__ == '__main__':
    unittest.main()
