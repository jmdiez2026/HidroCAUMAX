"""A fixed outlet on the smoothed contour remains valid through save/load.

Run with the Python interpreter that ships QGIS and GDAL, for example:
QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m unittest discover -s tests \
    -p test_regression_outlet_boundary.py -v
"""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from osgeo import gdal, ogr
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY

from caumax_consulta.boundary_smoothing import smooth_boundary
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.grid import Cell
from caumax_consulta.session_store import load_session, save_session
from caumax_consulta.terrain import analyze_basin
from caumax_consulta.terrain_surface import surface_window


class OutletBoundaryRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QgsApplication.instance()
        if cls.application is None:
            cls.application = QgsApplication([], False)
            cls.application.initQgis()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hidrocaumax_outlet_')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.dem = self.directory / 'dem.tif'
        self.directions = self.directory / 'directions.tif'
        self._raster(self.dem, [[120, 120, 120], [110, 110, 110], [100, 100, 100]])
        self._raster(self.directions, [[2, 4, 8], [2, 4, 8], [1, 0, 16]])
        self.original = analyze_basin(str(self.directions), str(self.dem), 750, 250)
        self.original_geometry = QgsGeometry()
        self.original_geometry.fromWkb(self.original.geometry_wkb)
        self.smoothed = smooth_boundary(self.original_geometry, 30, 500, 500,
                                        QgsPointXY(750, 250))
        self.query = NativeQuery(
            10, 100, 750, 250, self.crs, str(self.directions), str(self.dem),
            'Synthetic flow', cell=Cell(2, 1, 750, 250, 500, 500))

    def _raster(self, path, values):
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 3, 3, 1, gdal.GDT_Float64)
        dataset.SetProjection(self.crs.toWkt())
        dataset.SetGeoTransform((0, 500, 0, 1500, 0, -500))
        dataset.GetRasterBand(1).WriteArray(np.asarray(values, dtype=np.float64))
        dataset.FlushCache()
        dataset = None

    def _active_analysis(self):
        active = analyze_basin(str(self.directions), str(self.dem), 750, 250,
                               surface_wkb=bytes(self.smoothed.asWkb()))
        diagnostics = dict(active.diagnostics)
        diagnostics['analysis_surface'] = dict(
            kind='smoothed', degree=30,
            original_area_m2=self.original.area_m2,
            original_perimeter_m=self.original.perimeter_m,
            original_cell_count=self.original.cell_count)
        return replace(active, diagnostics=diagnostics)

    def _smoothed_state(self):
        return dict(original_checked=True, smooth_checked=True, degree=30,
                    base_geometry_wkb=self.original.geometry_wkb,
                    geometry_wkb=dict(original=self.original.geometry_wkb,
                                      suavizada=bytes(self.smoothed.asWkb())))

    def test_anchored_outlet_accepts_exact_surface_area(self):
        polygon = ogr.CreateGeometryFromWkb(bytes(self.smoothed.asWkb()))
        point = ogr.CreateGeometryFromWkt('POINT (750 250)')
        self.assertTrue(polygon.Intersects(point))
        self.assertFalse(polygon.Contains(point))
        dataset = gdal.Open(str(self.dem))
        result = surface_window(dataset, bytes(self.smoothed.asWkb()), (750, 250),
                                lambda: False, lambda value: None)
        self.assertAlmostEqual(float(result[4].sum()), self.smoothed.area(), places=6)
        self.assertAlmostEqual(result[5], self.smoothed.area(), places=6)
        dataset = None

    def test_active_surface_rejects_exterior_outlet(self):
        dataset = gdal.Open(str(self.dem))
        with self.assertRaisesRegex(ValueError, 'conservar el centro de cierre'):
            surface_window(dataset, bytes(self.smoothed.asWkb()), (-1, 250),
                           lambda: False, lambda value: None)
        dataset = None

    def test_smoothed_analysis_roundtrip_preserves_outlet_and_results(self):
        active = self._active_analysis()
        path = self.directory / 'anchored.caumax.json'
        save_session(path, active, self.query, self._smoothed_state())
        restored = load_session(path)
        self.assertEqual(restored.analysis, active)
        self.assertEqual((restored.analysis.outlet_x, restored.analysis.outlet_y), (750, 250))
        self.assertEqual(restored.smoothing_state['base_geometry_wkb'], self.original.geometry_wkb)
        self.assertEqual(restored.smoothing_state['geometry_wkb']['suavizada'], bytes(self.smoothed.asWkb()))

    def test_original_cell_basin_still_requires_interior_outlet(self):
        boundary_outlet = replace(self.original,
                                  basin=replace(self.original.basin, outlet_y=0))
        state = dict(original_checked=True, smooth_checked=False, degree=30,
                     geometry_wkb=dict(original=self.original.geometry_wkb))
        with self.assertRaisesRegex(ValueError, 'dentro de la cuenca original'):
            save_session(self.directory / 'invalid_original.caumax.json',
                         boundary_outlet, self.query, state)

    def test_session_rejects_outlet_outside_active_surface(self):
        active = self._active_analysis()
        # This point remains strictly inside the original square, but is
        # outside the active smoothed surface.
        point = ogr.CreateGeometryFromWkt('POINT (250 250)')
        polygon = ogr.CreateGeometryFromWkb(bytes(self.smoothed.asWkb()))
        self.assertFalse(polygon.Intersects(point))
        invalid = replace(active, basin=replace(active.basin, outlet_x=250))
        with self.assertRaisesRegex(ValueError, 'divisoria activa debe conservar'):
            save_session(self.directory / 'invalid_active.caumax.json',
                         invalid, self.query, self._smoothed_state())


if __name__ == '__main__':
    unittest.main()
