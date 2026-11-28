"""Actual SNCZI raster sampling by D8 chainage and graph selection."""
import math
import base64
import struct
from types import SimpleNamespace
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
from osgeo import gdal, osr
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtTest import QSignalSpy, QTest
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY
from caumax_consulta import course_flow_profile as cp


class CourseFlowProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        srs = osr.SpatialReference(); srs.ImportFromEPSG(25830)
        self.wkt = srs.ExportToWkt()
        points = [QgsPointXY(500012.5, 4699987.5),
                  QgsPointXY(500037.5, 4699987.5),
                  QgsPointXY(500062.5, 4699962.5)]
        self.line = bytes(QgsGeometry.fromPolylineXY(points).asWkb())

    def raster(self, name, values, nodata=-9999):
        path = Path(self.temp.name) / name
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 3, 3, 1, gdal.GDT_Float32)
        ds.SetProjection(self.wkt)
        ds.SetGeoTransform((500000, 25, 0, 4700000, 0, -25))
        ds.GetRasterBand(1).SetNoDataValue(nodata)
        ds.GetRasterBand(1).WriteArray(np.asarray(values, dtype=np.float32))
        ds = None
        return str(path)

    def test_chainage_uses_native_course_from_outlet_not_straight_distance(self):
        centres = cp.course_centres(self.line, self.wkt)
        self.assertEqual(len(centres), 3)
        self.assertEqual(centres[0], (0., 500012.5, 4699987.5))
        self.assertAlmostEqual(centres[1][0], .025)
        self.assertAlmostEqual(centres[2][0], .025 + math.hypot(25, 25)/1000)
        self.assertNotAlmostEqual(centres[-1][0], math.hypot(50, 25)/1000)

    def test_real_snczi_samples_have_native_values_per_d8_cell(self):
        paths = []
        for period in cp.PERIODS:
            raster = np.zeros((3, 3), dtype=np.float32)
            raster[0, 0], raster[0, 1], raster[1, 2] = period, 2*period, 3*period
            paths.append(self.raster(f'q{period}.tif', raster))
        output = cp.sample_course_quantiles(self.line, self.wkt, paths)
        self.assertEqual(len(output), 3)
        self.assertEqual(output[0][3], tuple(float(v) for v in cp.PERIODS))
        self.assertEqual(output[1][3], tuple(float(v)*2 for v in cp.PERIODS))
        self.assertEqual(output[2][3], tuple(float(v)*3 for v in cp.PERIODS))
        self.assertEqual(cp.estimate_flow(100, output[2][3])['flow'], 300.)

    def test_nodata_and_missing_source_never_become_zero(self):
        raster = np.zeros((3, 3), dtype=np.float32)
        raster[0, 0] = 0.
        raster[0, 1] = -9999.
        paths = [self.raster('q2.tif', raster)] + ['']*5
        result = cp.sample_course_quantiles(self.line, self.wkt, paths)
        self.assertEqual(result[0][3][0], 0.)
        self.assertIsNone(result[1][3][0])
        self.assertEqual(result[2][3], (0.,None,None,None,None,None))
        with self.assertRaises(InterruptedError):
            cp.sample_course_quantiles(self.line, self.wkt, paths, lambda: True)

    def test_profile_segments_never_bridge_nodata_and_keep_zero(self):
        flows = [0., None, 2.5, None, None, 0., 4.]
        self.assertEqual(cp.profile_counts(flows), {
            'total': 7, 'valid': 4, 'missing': 3, 'zeros': 2})
        self.assertEqual(cp.profile_segments(flows), [
            (True, 0, 0), (False, 1, 1), (True, 2, 2),
            (False, 3, 4), (True, 5, 6)])
        self.assertEqual(cp.profile_segments([]), [])
        self.assertEqual(cp.profile_segments([None, None]), [(False, 0, 1)])
        self.assertEqual(cp.profile_segments([0., 5.]), [(True, 0, 1)])

    def test_coverage_remains_visible_after_selecting_zero_or_missing(self):
        widget = cp.CourseFlowProfile()
        self.addCleanup(widget.close)
        widget._rows = [(0., 1., 1., (0.,)*6),
                        (.025, 2., 2., (None,)*6),
                        (.050, 3., 3., (5.,)*6)]
        widget._flows = [0., None, 5.]
        widget._period = 100.
        widget._update_coverage()
        summary = widget.coverage_label.text()
        self.assertIn('Celdas del curso: 3', summary)
        self.assertIn('Con caudal válido: 2', summary)
        self.assertIn('Q=0 en 1', summary)
        self.assertIn('Sin dato SNCZI: 1', summary)
        widget.select_index(0)
        self.assertIn('0.00 m³/s (dato válido)', widget.label.text())
        widget.select_index(1)
        self.assertIn('sin dato SNCZI (NoData)', widget.label.text())
        self.assertEqual(widget.coverage_label.text(), summary)

    def test_single_valid_qci_from_user_screenshot_uses_nonzero_integer_y_ticks(self):
        plot = cp._ProfilePlot(None)
        self.addCleanup(plot.close)
        plot.resize(680, 280)
        plot.show()
        self.app.processEvents()
        rows = [(0.,1.,1.,(None,)*6),
                (1.,2.,2.,(18.06,)*6),
                (12.,3.,3.,(None,)*6),
                (22.62,4.,4.,(None,)*6)]
        flows = [None, 18.06, None, None]
        plot.show_profile(rows, flows, 1)
        extent, ymax = plot.frame()
        self.assertGreater(plot._ymin, 0)
        self.assertLessEqual(plot._ymin, 18)
        self.assertGreater(ymax, 18.06)
        self.assertIsInstance(plot._xstep, int)
        self.assertIsInstance(plot._ystep, int)
        self.assertEqual(plot._xstep, 2)
        self.assertGreaterEqual(extent, 22.62)
        # 0, 2, 4, ..., 24 km: more labelled divisions than the old 6 ticks.
        self.assertGreater(int(extent // plot._xstep) + 1, 6)
        self.assertEqual(cp.profile_counts(flows), {
            'total': 4, 'valid': 1, 'missing': 3, 'zeros': 0})

    def test_integer_flow_axes_not_pinned_to_zero_and_decimal_specific_axis(self):
        plot = cp._ProfilePlot(None)
        self.addCleanup(plot.close)
        plot.resize(640, 280)
        plot.show()
        self.app.processEvents()
        rows = [(0., 1., 1., (1.,)*6), (11.4, 2., 2., (2.,)*6),
                (26.8, 3., 3., (3.,)*6)]
        plot.show_profile(rows, [135., 145., 160.], None)
        length, ymax = plot.frame()
        self.assertGreater(plot._ymin, 0)
        self.assertIsInstance(plot._xstep, int)
        self.assertIsInstance(plot._ystep, int)
        self.assertGreaterEqual(length, rows[-1][0])
        self.assertGreater(ymax, max(plot.flows))
        specific = cp._ProfilePlot(None, specific=True)
        self.addCleanup(specific.close)
        specific.resize(640, 280)
        specific.show_profile(rows, [.016, .018, .020], None)
        specific.frame()
        self.assertGreater(specific._ymin, 0)
        self.assertLess(specific._ystep, 1.)

    def test_specific_discharge_uses_each_native_upstream_area_and_syncs_selection(self):
        widget = cp.CourseFlowProfile()
        self.addCleanup(widget.close)
        areas = [.30, .20, .10]
        analysis = SimpleNamespace(diagnostics={
            'main_course_areas_b64': base64.b64encode(
                struct.pack('<3d', *areas)).decode('ascii')})
        self.assertEqual(cp.main_course_areas(analysis), areas)
        widget._areas = cp.main_course_areas(analysis)
        widget._rows = [(0.,1.,1.,(30.,)*6),
                        (.025,2.,2.,(None,)*6),
                        (.050,3.,3.,(5.,)*6)]
        widget._period = 100.
        widget._refresh()
        self.assertEqual(widget._specific_values[0], 100.)
        self.assertIsNone(widget._specific_values[1])
        self.assertEqual(widget._specific_values[2], 50.)
        widget.select_index(2)
        self.assertEqual(widget.graph.selected, 2)
        self.assertEqual(widget.specific_graph.selected, 2)
        self.assertIn('Aportación específica: 50.0000', widget.label.text())

    def test_chart_click_selects_the_same_sample_index(self):
        plot = cp._ProfilePlot(None)
        self.addCleanup(plot.close)
        plot.resize(640, 280)
        plot.show()
        self.app.processEvents()
        rows = [(0.,1.,1.,(1,)*6),(.025,2.,2.,(2,)*6),
                (.060355,3.,3.,(3,)*6)]
        plot.show_profile(rows, [1.,2.,3.], None)
        chosen = QSignalSpy(plot.chosen)
        length, ymax = plot.frame()
        point = plot.xy(1, length, ymax)
        QTest.mouseClick(plot, Qt.LeftButton, Qt.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0][0], 1)


if __name__ == '__main__':
    unittest.main()
