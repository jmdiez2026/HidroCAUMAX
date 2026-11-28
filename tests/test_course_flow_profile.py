"""Actual SNCZI raster sampling by D8 chainage and graph selection."""
import math
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
