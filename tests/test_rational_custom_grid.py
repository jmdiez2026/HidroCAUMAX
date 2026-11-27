"""Custom rational grids: differing extents must not move or truncate a basin."""
import os
from unittest import TestCase

import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle

from test_rational_basin import rational_fixture, _write
from caumax_consulta import rational_basin as rational
from caumax_consulta.basin_dialog import AnalysisTask
from caumax_consulta.terrain import analyze_basin


def custom_grid(f, resolution=25, dem_shape=(11, 10), directions_shape=(9, 9)):
    original_dem, original_dirs = f.calc.mdt[:9, :9], f.calc.dirs[:9, :9]
    dem = np.full(dem_shape, 180, dtype=np.float32)
    directions = np.zeros(directions_shape, dtype=np.int32)
    for target, source in ((dem, original_dem), (directions, original_dirs)):
        rows, cols = min(target.shape[0], source.shape[0]), min(target.shape[1], source.shape[1])
        target[:rows, :cols] = source[:rows, :cols]
    transform = (0, resolution, 0, 9 * resolution, 0, -resolution)
    f.calc.mdt, f.calc.dirs = dem, directions
    f.calc.geoTransform = f.transform = transform
    f.calc.basinCells = np.zeros(dem_shape, dtype=np.float64)
    f.calc.basinCells[1:8, 1:8] = 1
    f.calc.pt = QgsPointXY(4.5 * resolution, 4.5 * resolution)
    f.dialog.txtX.setText(f.calc.pt.x()); f.dialog.txtY.setText(f.calc.pt.y())
    f.calc.area = 49 * resolution ** 2
    f.calc.basinGeometry = [QgsGeometry.fromRect(
        QgsRectangle(resolution, resolution, 8 * resolution, 8 * resolution))]
    _write(f.paths['MDT'], dem, transform, f.reference)
    _write(f.paths['FLOWDIRS'], directions, transform, f.reference)


class RationalCustomGridTests(TestCase):
    def setUp(self):
        context = rational_fixture()
        self.f = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_user_resolutions_and_either_extent_preserve_exact_native_basin(self):
        f = self.f
        for resolution in (5, 25, 100, 500):
            for dem_shape, dirs_shape in (((11, 10), (9, 9)), ((9, 9), (11, 10)),
                                          ((11, 9), (9, 11))):
                with self.subTest(resolution=resolution, dem=dem_shape, directions=dirs_shape):
                    custom_grid(f, resolution, dem_shape, dirs_shape)
                    query = rational.capture_rational_query(f.dialog, flow_result={'flow': 10.29, 'period': 100})
                    task = AnalysisTask(query, lambda *_: None)
                    self.assertTrue(task.run(), task.error + '\n' + task.trace)
                    self.assertEqual(task.result.cell_count, 49)
                    self.assertEqual(task.result.area_m2, f.calc.area)
                    self.assertEqual(task.result.cell_width, resolution)
                    self.assertEqual((task.result.outlet_x, task.result.outlet_y),
                                     (f.calc.pt.x(), f.calc.pt.y()))
                    self.assertEqual(query.flow, 10.29)
                    self.assertIs(rational.validate_rational_analysis(task.result, query), task.result)

    def test_complete_source_revalidation_allows_touch_but_rejects_extra_dem_cell_edit(self):
        f = self.f; custom_grid(f)
        query = rational.capture_rational_query(f.dialog)
        task = AnalysisTask(query, lambda *_: None)
        self.assertTrue(task.run(), task.error)
        for path in f.paths.values():
            stamp = path.stat()
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
        self.assertIs(rational.validate_rational_analysis(task.result, query), task.result)
        f.calc.mdt[-1, -1] += 1  # Outside the smaller directions raster and the basin.
        _write(f.paths['MDT'], f.calc.mdt, f.transform, f.reference)
        with self.assertRaisesRegex(ValueError, 'han cambiado'):
            rational.validate_rational_analysis(task.result, query)

    def test_mask_outside_directions_coverage_is_rejected_before_opening(self):
        f = self.f; custom_grid(f, directions_shape=(7, 9))
        with self.assertRaisesRegex(ValueError, 'no cubren toda la cuenca'):
            rational.capture_rational_query(f.dialog)

    def test_analysis_rejects_basin_outside_dem_without_silently_clipping(self):
        f = self.f; custom_grid(f, dem_shape=(7, 9))
        with self.assertRaisesRegex(ValueError, 'MDT no cubre toda la cuenca'):
            analyze_basin(f.paths['FLOWDIRS'], f.paths['MDT'], f.calc.pt.x(), f.calc.pt.y(),
                          allow_different_extents=True)

    def test_different_extent_never_relaxes_origin_resolution_or_crs_checks(self):
        f = self.f; custom_grid(f)
        other_crs = osr.SpatialReference(); other_crs.ImportFromEPSG(25831)
        variants = (((25, 25, 0, 225, 0, -25), f.reference),
                    ((0, 5, 0, 225, 0, -5), f.reference),
                    (f.transform, other_crs))
        for transform, reference in variants:
            with self.subTest(transform=transform, crs=reference.GetAuthorityCode(None)):
                _write(f.paths['FLOWDIRS'], f.calc.dirs, transform, reference)
                with self.assertRaises(ValueError):
                    rational.capture_rational_query(f.dialog)
                with self.assertRaises(ValueError):
                    analyze_basin(f.paths['FLOWDIRS'], f.paths['MDT'], f.calc.pt.x(), f.calc.pt.y(),
                                  allow_different_extents=True)

    def test_dem_halo_keeps_real_neighbours_beyond_smaller_direction_extent(self):
        f = self.f; custom_grid(f, directions_shape=(8, 8))
        query = rational.capture_rational_query(f.dialog)
        task = AnalysisTask(query, lambda *_: None)
        self.assertTrue(task.run(), task.error)
        self.assertEqual(task.result.diagnostics['tri_valid_cells'], 49)
        self.assertEqual(task.result.diagnostics['tri_excluded_cells'], 0)

    def test_regular_raster_workflow_retains_strict_dimensions(self):
        f = self.f; custom_grid(f)
        with self.assertRaisesRegex(ValueError, 'misma malla y dimensiones'):
            analyze_basin(f.paths['FLOWDIRS'], f.paths['MDT'], f.calc.pt.x(), f.calc.pt.y())

    def test_source_dimension_change_after_capture_is_rejected(self):
        f = self.f; custom_grid(f)
        query = rational.capture_rational_query(f.dialog)
        task = AnalysisTask(query, lambda *_: None)
        self.assertTrue(task.run(), task.error)
        _write(f.paths['FLOWDIRS'], np.pad(f.calc.dirs, ((0, 1), (0, 1))), f.transform, f.reference)
        with self.assertRaisesRegex(ValueError, 'han cambiado'):
            rational.validate_rational_analysis(task.result, query)
