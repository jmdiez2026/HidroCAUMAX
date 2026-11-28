"""PyQGIS tests for native DEM altitude classes and clickable curve semantics."""
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
from osgeo import gdal, osr
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt.QtCore import QPoint, Qt
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication, QgsGeometry, QgsRectangle
from caumax_consulta import hypsometric_selection as hs
from caumax_consulta.hypsometry import (
    HypsometricWidget, curve_plot_point, curve_plot_data)
from caumax_consulta.hypsometry_data import build_hypsometric_curve
from types import SimpleNamespace


class AltitudeMapSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        srs = osr.SpatialReference(); srs.ImportFromEPSG(25830)
        self.wkt = srs.ExportToWkt()
        self.path = Path(self.temp.name) / 'mdt.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(self.path),5,5,1,gdal.GDT_Float32)
        ds.SetProjection(self.wkt)
        ds.SetGeoTransform((500000,25,0,4700000,0,-25))
        ds.GetRasterBand(1).SetNoDataValue(-9999)
        ds.GetRasterBand(1).WriteArray(np.arange(25,dtype=np.float32).reshape(5,5))
        ds = None
        rect = QgsRectangle(500025,4699900,500100,4699975)
        self.basin = bytes(QgsGeometry.fromRect(rect).asWkb())

    def test_equal_altitudes_are_one_class_and_quantiles_do_not_invent_cell_values(self):
        self.assertEqual(hs.elevation_band((5,5,5),1),(5.,5.))
        self.assertEqual(hs.elevation_band((9,7,7,7,2),2),(4.5,8.))
        with self.assertRaises(ValueError):
            hs.elevation_band((1,2),1)

    def test_selected_cota_is_a_georeferenced_native_dem_cell_mask(self):
        before = self.path.read_bytes()
        result = hs.altitude_mask(str(self.path),self.basin,self.wkt,7,15)
        self.addCleanup(Path(result['path']).unlink, missing_ok=True)
        self.assertEqual(result['count'],5)
        image = gdal.Open(result['path'])
        self.assertEqual(image.RasterXSize,3)
        self.assertEqual(image.RasterYSize,3)
        np.testing.assert_array_equal(image.ReadAsArray(),
            np.array([[0,1,1],[1,1,1],[0,0,0]],dtype=np.uint8))
        self.assertEqual(image.GetRasterBand(1).GetNoDataValue(),0)
        self.assertEqual(image.GetProjectionRef(), self.wkt)
        image = None
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(InterruptedError):
            hs.altitude_mask(str(self.path),self.basin,self.wkt,7,15,cancelled=lambda:True)

    def test_curve_click_and_dimensionless_view_refer_to_same_physical_altitude(self):
        heights=np.arange(20,dtype=np.float64)
        sample=SimpleNamespace(
            values={'hypsometric':.5},
            diagnostics={'hypsometric_curve':build_hypsometric_curve(heights,2.0,.5)})
        widget=HypsometricWidget()
        self.addCleanup(widget.close)
        widget.resize(700,340)
        widget.set_analysis(sample)
        widget.show()
        self.app.processEvents()
        index=60
        data=widget.plot_data()
        p=curve_plot_point(data,widget.canvas.rect(),index)
        QTest.mouseClick(widget.canvas,Qt.LeftButton,Qt.NoModifier,
                         QPoint(round(p.x()),round(p.y())))
        self.assertIsNotNone(widget._selected_index)
        selected=widget._selected_index
        elevation=sample.diagnostics['hypsometric_curve']['elevation_m'][selected]
        self.assertIn(f'{elevation:.2f} m', widget.selection_label.text())
        widget.set_dimensionless(True)
        self.assertEqual(widget._selected_index,selected)
        self.assertIn(f'{elevation:.2f} m', widget.selection_label.text())
        self.assertEqual(widget.plot_data()['y_label'],'Altitud relativa (h−Hmín)/ΔH')


if __name__ == '__main__':
    unittest.main()
