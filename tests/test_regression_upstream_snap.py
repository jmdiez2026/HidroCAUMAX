"""Never move a new watershed outlet downstream of the clicked D8 section."""
from dataclasses import replace
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem

from caumax_consulta import basin, dem_hydrology, session_store
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.grid import cell_at_point
from caumax_consulta.terrain import analyze_basin


class UpstreamSnapRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='upstream_snap_')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.sequence = 0
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')

    def raster(self, data, transform, name='directions', nodata=255):
        self.sequence += 1
        data = np.asarray(data)
        path = self.folder / f'{name}_{self.sequence}.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(path), data.shape[1], data.shape[0], 1, gdal.GDT_Float64)
        ds.SetProjection(self.crs.toWkt())
        ds.SetGeoTransform(transform)
        ds.GetRasterBand(1).SetNoDataValue(nodata)
        ds.GetRasterBand(1).WriteArray(data)
        ds = None
        return path

    @staticmethod
    def valley(size=31):
        codes = np.full((size, size), 4, dtype=np.uint8)
        middle = size // 2
        codes[:, :middle] = 1
        codes[:, middle + 1:] = 16
        codes[-1, middle] = 0
        return codes

    def snap(self, codes, point, transform, radius=707.1067811865476):
        path = self.raster(codes, transform)
        ds = gdal.Open(str(path))
        counts = dem_hydrology._flow_accumulation(ds.GetRasterBand(1))
        ds = None
        effective, record = dem_hydrology._nearest_main_channel(
            counts, transform, *point, 52.25e6, .9, radius,
            directions_path=path)
        return effective, record, path

    def test_coarse_center_153m_downstream_is_replaced_and_basin_recomputed(self):
        transform = (0, 500, 0, 2500, 0, -500)
        directions = self.raster(self.valley(5), transform)
        dem = self.raster(np.tile(np.arange(5, 0, -1)[:, None], (1, 5)) * 100,
                          transform, 'dem', -9999)
        original = analyze_basin(directions, dem, 1242.2, 1402.7)
        active = analyze_basin(directions, dem, 1242.2, 1402.7, upstream_only=True)
        self.assertEqual((original.outlet_x, original.outlet_y), (1250, 1250))
        self.assertEqual((active.outlet_x, active.outlet_y), (1250, 1750))
        self.assertEqual((active.selected_x, active.selected_y), (1242.2, 1402.7))
        self.assertEqual(original.area_m2, 3.75e6)
        self.assertEqual(active.area_m2, 2.5e6)
        self.assertNotEqual(active.geometry_wkb, original.geometry_wkb)
        self.assertLess(active.diagnostics['outlet_snap']['section_offset_m'], 0)
        query = NativeQuery(10, 100, active.selected_x, active.selected_y, self.crs,
                            str(directions), str(dem), 'Synthetic flow',
                            cell=cell_at_point(active.selected_x, active.selected_y,
                                               0, 0, 2500, 2500, 5, 5))
        state = dict(original_checked=True, smooth_checked=False, degree=1)
        target = self.folder / 'coarse.caumax.json'
        session_store.save_session(target, active, query, state)
        directions.unlink()
        dem.unlink()
        self.assertEqual(session_store.load_session(target).analysis, active)

    def test_fine_area_need_not_match_coarse_area_and_never_moves_downstream(self):
        transform = (300000, 25, 0, 4700000, 0, -25)
        point = (300387.5, 4699620)
        effective, record, _ = self.snap(self.valley(), point, transform)
        self.assertEqual((effective.row, effective.col), (14, 15))
        self.assertLess(record['candidate_upstream_area_m2'], .9 * 52.25e6)
        self.assertFalse(record['reference_threshold_met'])
        self.assertEqual(record['selection_mode'], 'upstream_only')
        self.assertLess(record['section_offset_m'], 0)
        self.assertLess(record['area_change_vs_original_cell_m2'], 0)

    def test_same_cell_is_kept_if_its_center_is_upstream_of_exact_click(self):
        transform = (0, 25, 0, 775, 0, -25)
        effective, record, _ = self.snap(self.valley(), (387.5, 380), transform)
        self.assertEqual((effective.row, effective.col), (15, 15))
        self.assertEqual(record['status'], 'unchanged')
        self.assertLess(record['section_offset_m'], 0)

    def test_diffuse_hillslope_is_not_silently_accepted_as_stream(self):
        codes = np.full((31, 31), 4, dtype=np.uint8)
        codes[-1, :] = 0
        with self.assertRaisesRegex(ValueError, 'aporte concentrado'):
            self.snap(codes, (387.5, 395), (0, 25, 0, 775, 0, -25))

    def test_no_upstream_candidate_within_radius_does_not_move_downstream(self):
        with self.assertRaisesRegex(ValueError, 'No se encuentra.*aguas arriba'):
            self.snap(self.valley(), (387.5, 395), (0, 25, 0, 775, 0, -25), radius=10)

    def test_nearer_disconnected_branch_cannot_capture_the_point(self):
        codes = np.zeros((31, 31), dtype=np.uint8)
        codes[:15, 14] = 4
        codes[14, 14] = 2  # Main branch feeds the original cell diagonally.
        codes[15, 15] = 4
        codes[:20, 16] = 4  # Nearby separate branch never drains to that cell.
        effective, record, _ = self.snap(codes, (397.5, 392.5), (0, 25, 0, 775, 0, -25))
        self.assertEqual((effective.row, effective.col), (14, 14))
        self.assertEqual(record['upstream_path'], [[14, 14, 2], [15, 15, 4]])

    def test_diagonal_corner_uses_d8_section_not_north_south_assumption(self):
        codes = np.zeros((5, 5), dtype=np.uint8)
        codes[1, 1] = codes[2, 2] = 2
        path = self.raster(codes, (0, 500, 0, 2500, 0, -500))
        ds = gdal.Open(str(path))
        selected, record = basin._upstream_section_cell(ds.GetRasterBand(1), ds.GetGeoTransform(),
                                                         1200, 1300)
        ds = None
        self.assertEqual((selected.row, selected.col), (1, 1))
        self.assertLess(record['section_offset_m'], 0)

    def test_meander_ancestor_in_downstream_halfplane_is_excluded(self):
        codes = np.zeros((9, 9), dtype=np.uint8)
        codes[4, 4] = 4
        codes[4, 5] = 16
        codes[5, 5] = 64
        codes[5, 6] = 16
        codes[4, 6] = 4
        codes[3, 6] = 4
        path = self.raster(codes, (0, 25, 0, 225, 0, -25))
        ds = gdal.Open(str(path))
        selected, record = basin._upstream_section_cell(ds.GetRasterBand(1), ds.GetGeoTransform(), 120, 115)
        ds = None
        self.assertEqual((selected.row, selected.col), (3, 6))
        self.assertLess(record['section_offset_m'], 0)

    def test_connected_cycle_and_original_nodata_are_rejected(self):
        for codes, point, message in (([[1, 16]], (5, 5), 'ciclo'),
                                      ([[255, 0]], (5, 5), 'NoData')):
            path = self.raster(codes, (0, 10, 0, 10, 0, -10))
            ds = gdal.Open(str(path))
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                basin._upstream_section_cell(ds.GetRasterBand(1), ds.GetGeoTransform(), *point)
            ds = None

    def test_connected_confluence_uses_distance_then_row_column_not_larger_area(self):
        codes = np.zeros((7, 7), dtype=np.uint8)
        codes[3, 3] = 4
        codes[2, 2], codes[2, 4] = 2, 8
        codes[0:2, 2] = 4  # Left branch has more contributing cells.
        transform = (0, 10, 0, 70, 0, -10)
        path = self.raster(codes, transform)
        ds = gdal.Open(str(path))
        counts = dem_hydrology._flow_accumulation(ds.GetRasterBand(1))
        nearest, _ = basin._upstream_section_cell(ds.GetRasterBand(1), transform, 39, 37,
                                                   counts=counts)
        tied, _ = basin._upstream_section_cell(ds.GetRasterBand(1), transform, 35, 37,
                                                counts=counts)
        ds = None
        self.assertEqual((nearest.row, nearest.col), (2, 4))
        self.assertLess(int(counts[2, 4]), int(counts[2, 2]))
        self.assertEqual((tied.row, tied.col), (2, 2))

    def test_dem_engine_reuses_native_d8_and_publishes_the_upstream_selection(self):
        transform = (0, 25, 0, 775, 0, -25)
        dem = self.raster(np.tile(np.arange(31, 0, -1)[:, None], (1, 31)), transform, 'dem', -9999)
        cache = self.folder / 'cache'
        cache.mkdir()
        ds = gdal.Open(str(dem))
        key = dem_hydrology._source_key(ds, dem)
        ds = None
        path = cache / ('d8_' + key[:32] + '.tif')
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 31, 31, 1, gdal.GDT_Byte)
        ds.SetGeoTransform(transform)
        ds.SetProjection(self.crs.toWkt())
        ds.SetMetadata({'SOURCE_KEY': key, 'HYDRO_VERSION': dem_hydrology._VERSION})
        ds.GetRasterBand(1).SetNoDataValue(255)
        ds.GetRasterBand(1).WriteArray(self.valley())
        ds = None
        with patch.object(dem_hydrology, '_cache_folder', return_value=cache), \
                patch.object(dem_hydrology, '_run_grass', side_effect=AssertionError('Reuse native D8')):
            result = dem_hydrology.analyze_dem(
                dem, 387.5, 395, cache, reference_area_m2=52.25e6,
                reference_cell_width=500, reference_cell_height=500, snap_enabled=True)
        snap = result.analysis.diagnostics['outlet_snap']
        self.assertEqual((result.outlet_x, result.outlet_y), (387.5, 412.5))
        self.assertEqual((result.selected_x, result.selected_y), (387.5, 395))
        self.assertEqual(snap['candidate_upstream_area_m2'], result.area_m2)
        self.assertEqual(snap['candidate_touches_boundary'], result.touches_boundary)
        self.assertLess(snap['section_offset_m'], 0)

    def test_fine_snapshot_roundtrips_and_rejects_corrupt_upstream_proof(self):
        transform = (0, 25, 0, 775, 0, -25)
        point = (387.5, 395)
        selected, record, directions = self.snap(self.valley(), point, transform)
        dem = self.raster(np.tile(np.arange(31, 0, -1)[:, None], (1, 31)), transform, 'dem', -9999)
        active = analyze_basin(directions, dem, selected.x, selected.y)
        record['candidate_touches_boundary'] = active.touches_boundary
        active = replace(active, selected_x=point[0], selected_y=point[1],
                         diagnostics={**active.diagnostics, 'outlet_snap': record})
        query = NativeQuery(10, 100, *point, self.crs, str(directions), str(dem), 'Synthetic',
                            cell=cell_at_point(*point, 0, 0, 775, 775, 31, 31))
        state = dict(original_checked=True, smooth_checked=False, degree=1)
        target = self.folder / 'fine.caumax.json'
        session_store.save_session(target, active, query, state)
        self.assertEqual(session_store.load_session(target).analysis, active)
        for key, bad in (('section_offset_m', 1), ('upstream_path_length_m', 0),
                         ('upstream_path', [[14, 15, 64], [15, 15, 4]]),
                         ('area_change_vs_original_cell_m2', 10),
                         ('reference_threshold_met', True)):
            invalid = replace(active, diagnostics={**active.diagnostics,
                              'outlet_snap': {**record, key: bad}})
            with self.subTest(key=key), self.assertRaises(ValueError):
                session_store.save_session(target, invalid, query, state)
        self.assertEqual(session_store.load_session(target).analysis, active)


if __name__ == '__main__':
    unittest.main()
