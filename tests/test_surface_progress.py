"""Real terrain callbacks cannot turn visited cells into displayed percentages."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry

from caumax_consulta.surface_recalculation import SurfaceTask
from caumax_consulta.terrain import analyze_basin


class SurfaceProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hidrocaumax_progress_')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.rows, self.cols, self.step = 128, 64, 25
        self.x, self.y = 500012.5, 4700012.5
        self.directions = self.folder / 'directions.tif'
        self.dem = self.folder / 'dem.tif'
        directions = np.full((self.rows, self.cols), 4, dtype=np.uint8)
        directions[-1, :] = 16
        directions[-1, 0] = 0
        elevations = 100. + np.arange(self.rows, 0, -1)[:, None] + np.arange(self.cols)[None, :]
        for path, values, kind in ((self.directions, directions, gdal.GDT_Byte),
                                   (self.dem, elevations, gdal.GDT_Float64)):
            ds = gdal.GetDriverByName('GTiff').Create(str(path), self.cols, self.rows, 1, kind)
            ds.SetGeoTransform((500000., self.step, 0., 4700000. + self.rows*self.step, 0., -self.step))
            ds.SetProjection(self.crs.toWkt())
            ds.GetRasterBand(1).WriteArray(values)
            ds = None
        self.base = analyze_basin(self.directions, self.dem, self.x, self.y)
        self.assertEqual(self.base.cell_count, 8192)
        self.query = SimpleNamespace(directions_path=str(self.directions), dem_path=str(self.dem))
        self.flow_query = SimpleNamespace(x=self.x, y=self.y, period=100,
                                          flow=18., crs_wkt=self.crs.toWkt())
        self.sources = dict(p0=str(self.dem), rainfall={str(t): str(self.dem)
                            for t in (2, 5, 10, 25, 100, 500)})

    def run_task(self, base, surface=None):
        task = SurfaceTask(1, base, self.query, surface, 3, self.sources,
                           self.flow_query, [str(self.dem)]*6, lambda *args: None)
        emitted = []
        task.update.connect(lambda generation, text, percent: emitted.append((text, percent)))
        # A spy records every update before the ordinary time throttle; it
        # does not replace any calculations, callback conversions or signals.
        with patch.object(task, 'publish', wraps=task.publish) as publish:
            self.assertTrue(task.run(), task.error + '\n' + task.trace)
            calls = [call.args for call in publish.call_args_list]
        values = [percent for text, percent in calls]
        self.assertTrue(all(0 <= value <= 100 for value in values), values)
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 100)
        self.assertTrue(all(value < 100 for value in values[:-1]))
        self.assertTrue(all(0 <= percent <= 100 for text, percent in emitted))
        self.assertEqual(task.progress(), 100)
        morphology = [percent for text, percent in calls if 'morfología' in text]
        self.assertEqual(morphology[-1], 30)
        self.assertTrue(any(0 < value < 20 for value in morphology), morphology)
        self.assertEqual(morphology[-2], 20)
        return task.result['analysis'], morphology

    def test_native_cell_count_has_proportional_progress_and_unchanged_metrics(self):
        result, progress = self.run_task(self.base)
        self.assertAlmostEqual(next(value for value in progress if value > 0), 20*1000/8192)
        self.assertEqual(result.values, self.base.values)
        self.assertEqual(result.geometry_wkb, self.base.geometry_wkb)
        self.assertEqual(result.main_course_wkb, self.base.main_course_wkb)

    def test_smoothed_scan_and_return_to_original_use_their_respective_units(self):
        polygon = QgsGeometry.fromWkt(
            'MULTIPOLYGON (((500000 4700000,500800 4700000,500800 4703200,'
            '500000 4703200,500000 4700000)))')
        wkb = bytes(polygon.asWkb())
        expected = analyze_basin(self.directions, self.dem, self.x, self.y, surface_wkb=wkb)
        smoothed, progress = self.run_task(self.base, wkb)
        self.assertEqual(smoothed.values, expected.values)
        self.assertEqual(smoothed.geometry_wkb, expected.geometry_wkb)
        self.assertAlmostEqual(next(value for value in progress if value > 0), 20/128)
        self.assertEqual(smoothed.cell_count, 4096)
        self.assertEqual(smoothed.diagnostics['analysis_surface']['original_cell_count'], 8192)
        restored, progress = self.run_task(smoothed)
        self.assertAlmostEqual(next(value for value in progress if value > 0), 20*1000/8192)
        self.assertEqual(restored.values, self.base.values)
        self.assertEqual(restored.geometry_wkb, self.base.geometry_wkb)


if __name__ == '__main__':
    unittest.main()
