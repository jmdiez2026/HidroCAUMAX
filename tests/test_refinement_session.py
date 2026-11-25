"""Portable spatial correspondence proofs use native masks, not area matching."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
from osgeo import gdal, ogr
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY

from caumax_consulta import dem_hydrology, session_store
from caumax_consulta.boundary_smoothing import smooth_boundary
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.grid import cell_at_point
from caumax_consulta.manual_outlet import build_manual_outlet
from caumax_consulta.refinement_snap import select_reference_outlet
from caumax_consulta.terrain import analyze_basin


class RefinementSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='refinement_session_')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.state = dict(original_checked=True, smooth_checked=False, degree=1)
        self.path = self.folder / 'refinement.caumax.json'

    def rasters(self, name, size=11, resolution=25):
        codes = np.full((size, size), 4, dtype=np.uint8)
        middle = size // 2
        codes[:, :middle] = 1
        codes[:, middle + 1:] = 16
        codes[-1, middle] = 0
        elevations = np.tile(np.arange(size, 0, -1)[:, None], (1, size)).astype(np.float32)
        paths = []
        for suffix, data, kind, nodata in (('d8', codes, gdal.GDT_Byte, 255),
                                           ('dem', elevations, gdal.GDT_Float32, -9999)):
            path = self.folder / f'{name}_{suffix}.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(path), size, size, 1, kind)
            ds.SetProjection(self.crs.toWkt())
            ds.SetGeoTransform((0, resolution, 0, size * resolution, 0, -resolution))
            ds.GetRasterBand(1).SetNoDataValue(nodata)
            ds.GetRasterBand(1).WriteArray(data)
            ds = None
            paths.append(path)
        return tuple(paths)

    def query(self, point, directions, dem, size=11, resolution=25):
        extent = size * resolution
        return NativeQuery(10, 100, *point, self.crs, str(directions), str(dem),
                           'Synthetic refinement',
                           cell=cell_at_point(*point, 0, 0, extent, extent, size, size))

    def refined(self, directions, dem, point, reference, historical=1e6, radius=707.1067811865476):
        ds = gdal.Open(str(directions))
        counts = dem_hydrology._flow_accumulation(ds.GetRasterBand(1))
        ds = None
        cell, record = select_reference_outlet(directions, counts, *point, radius, reference,
                                               reference_area_m2=historical)
        analysis = analyze_basin(directions, dem, cell.x, cell.y)
        record['candidate_touches_boundary'] = analysis.touches_boundary
        return replace(analysis, selected_x=point[0], selected_y=point[1],
                       diagnostics={**analysis.diagnostics, 'outlet_snap': record})

    def rational_nearest(self, fixture):
        """Independent fine drainage with a long qualifying chain near the click."""
        directions, dem = self.rasters('rational_fine', size=180, resolution=25)
        codes = np.zeros((180, 180), dtype=np.uint8)
        codes[20:160, 20:89] = 1
        codes[20:160, 90:160] = 16
        codes[20:159, 89] = 4
        ds = gdal.Open(str(directions), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(codes)
        ds = None
        reference = dict(geometry_wkb=fixture.analysis.geometry_wkb,
                         main_course_wkb=fixture.analysis.main_course_wkb,
                         crs_wkt=fixture.analysis.crs_wkt,
                         selection_policy='nearest_reference_chain_cell')
        query = replace(fixture.query, directions_path=str(directions), dem_path=str(dem))
        analysis = self.refined(directions, dem, (query.x, query.y), reference,
                                historical=fixture.analysis.area_m2)
        return analysis, query, directions, dem

    def fixture(self, *, unchanged=False, inset=False, vertices=0):
        directions, dem = self.rasters('fine')
        original = analyze_basin(directions, dem, 137.5, 112.5)
        geometry = ogr.CreateGeometryFromWkb(original.geometry_wkb)
        if inset:
            geometry = ogr.CreateGeometryFromWkt(
                'POLYGON ((1 100,274 100,274 275,1 275,1 100))')
        if vertices:
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for first, last in (((0, 100), (275, 100)), ((275, 100), (275, 275)),
                                 ((275, 275), (0, 275)), ((0, 275), (0, 100))):
                for step in range(vertices):
                    fraction = step / vertices
                    ring.AddPoint_2D(first[0] + fraction * (last[0] - first[0]),
                                     first[1] + fraction * (last[1] - first[1]))
            ring.CloseRings()
            geometry = ogr.Geometry(ogr.wkbPolygon)
            geometry.AddGeometry(ring)
        reference = dict(geometry_wkb=bytes(geometry.ExportToWkb()),
                         main_course_wkb=original.main_course_wkb, crs_wkt=self.crs.toWkt())
        point = (137.1, 110) if unchanged else (122.4, 110)
        analysis = self.refined(directions, dem, point, reference)
        return analysis, self.query(point, directions, dem), directions, dem

    def save(self, analysis, query, state=None, bundle=None):
        session_store.save_session(self.path, analysis, query,
                                   self.state if state is None else state, bundle)

    def test_real_moved_and_containing_cell_proofs_roundtrip_without_source_rasters(self):
        for unchanged in (False, True):
            with self.subTest(unchanged=unchanged):
                active, query, directions, dem = self.fixture(unchanged=unchanged)
                record = active.diagnostics['outlet_snap']
                self.assertEqual(record['status'], 'unchanged' if unchanged else 'snapped')
                self.assertEqual(record['projection_kind'], 'containing_cell' if unchanged else 'orthogonal')
                self.save(active, query)
                directions.unlink()
                dem.unlink()
                with patch.object(gdal, 'Open', side_effect=AssertionError('No source rasters')):
                    restored = session_store.load_session(self.path)
                self.assertEqual(restored.analysis, active)
                self.assertEqual(restored.query, query)

    def test_discrete_overlap_is_not_vector_intersection_or_historical_area(self):
        active, query, _, _ = self.fixture(inset=True)
        snap = active.diagnostics['outlet_snap']
        reference = ogr.CreateGeometryFromWkb(bytes.fromhex(snap['reference_geometry_wkb_hex']))
        candidate = ogr.CreateGeometryFromWkb(active.geometry_wkb)
        self.assertNotEqual(reference.Intersection(candidate).GetArea(), snap['intersection_area_m2'])
        self.assertNotEqual(snap['reference_geometry_area_m2'], snap['reference_raster_area_m2'])
        self.assertNotEqual(snap['reference_area_m2'], snap['reference_geometry_area_m2'])
        self.save(active, query)
        self.assertEqual(session_store.load_session(self.path).analysis, active)

    def test_forged_geometry_or_self_consistent_overlap_counts_are_rejected_atomically(self):
        active, query, _, _ = self.fixture()
        self.save(active, query)
        previous = self.path.read_bytes()
        base = active.diagnostics['outlet_snap']
        changed_geometry = ogr.CreateGeometryFromWkt(
            'POLYGON ((0 75,275 75,275 250,0 250,0 75))')
        forged_counts = deepcopy(base)
        forged_counts['intersection_area_m2'] -= active.cell_width * active.cell_height
        intersection = forged_counts['intersection_area_m2']
        ref, candidate = forged_counts['reference_raster_area_m2'], forged_counts['candidate_upstream_area_m2']
        forged_counts.update(reference_coverage_fraction=intersection / ref,
                             candidate_overlap_fraction=intersection / candidate,
                             intersection_over_union=intersection / (ref + candidate - intersection))
        corruptions = (
            {**base, 'reference_geometry_wkb_hex': bytes(changed_geometry.ExportToWkb()).hex()},
            forged_counts,
        )
        for record in corruptions:
            invalid = replace(active, diagnostics={**active.diagnostics, 'outlet_snap': record})
            with self.subTest(record=record['intersection_area_m2']), self.assertRaisesRegex(ValueError, 'rasterizadas'):
                self.save(invalid, query)
            self.assertEqual(self.path.read_bytes(), previous)

    def test_grid_crs_section_and_numeric_corruption_fail_before_raster_work(self):
        active, query, _, _ = self.fixture()
        base = active.diagnostics['outlet_snap']
        corruptions = (
            ('reference_grid_size', [1_000_000_000, 1_000_000_000]),
            ('reference_grid_size', [11.0, 11]),
            ('reference_grid_bounds', [0, 0, 276, 275]),
            ('reference_crs_wkt', QgsCoordinateReferenceSystem('EPSG:25831').toWkt()),
            ('reference_geometry_wkb_hex', '00not-wkb'),
            ('reference_geometry_area_m2', base['reference_geometry_area_m2'] + 100),
            ('reference_raster_area_m2', base['reference_raster_area_m2'] + .1),
            ('reference_coverage_fraction', .9),
            ('candidate_count', 0),
            ('reference_policy_version', True),
            ('minimum_reference_coverage', .49),
            ('original_row', base['original_row'] + 1),
            ('snapped_x', base['snapped_x'] + .01),
            ('section_source_cell', [base['snapped_row'], base['snapped_col'], 16]),
            ('section_projection_x', base['section_projection_x'] + 1),
            ('section_offset_m', 10),
            ('coarse_upstream_distance_m', -1),
            ('branch_runner_up_distance_m', base['branch_distance_m'] + .1),
            ('projection_kind', 'containing_cell'),
            ('area_change_vs_reference_pct', 0),
            ('unknown_proof', 1),
        )
        with patch.object(session_store, '_reference_mask_counts', side_effect=AssertionError('Must preflight first')) as rasterize:
            for key, value in corruptions:
                invalid = replace(active, diagnostics={**active.diagnostics,
                                  'outlet_snap': {**base, key: value}})
                with self.subTest(key=key), self.assertRaises(ValueError):
                    self.save(invalid, query)
            rasterize.assert_not_called()

    def test_exact_half_overlap_is_rejected_and_block_memory_is_bounded(self):
        active, query, _, _ = self.fixture()
        base = active.diagnostics['outlet_snap']
        cell_area = active.cell_width * active.cell_height
        record = {**base, 'reference_raster_area_m2': 78 * cell_area,
                  'intersection_area_m2': 39 * cell_area,
                  'reference_coverage_fraction': .5, 'candidate_overlap_fraction': 39 / 77,
                  'intersection_over_union': 39 / (78 + 77 - 39)}
        invalid = replace(active, diagnostics={**active.diagnostics, 'outlet_snap': record})
        with patch.object(session_store, '_reference_mask_counts') as rasterize:
            with self.assertRaisesRegex(ValueError, 'mayoría espacial'):
                self.save(invalid, query)
            rasterize.assert_not_called()
        with patch.object(session_store, 'REFERENCE_BLOCK_CELLS', 9):
            self.save(active, query)
            self.assertEqual(session_store.load_session(self.path).analysis, active)

    def test_large_bounded_wkb_metadata_does_not_raise_other_text_limits(self):
        active, query, _, _ = self.fixture(vertices=180)
        self.assertGreater(len(active.diagnostics['outlet_snap']['reference_geometry_wkb_hex']), 16_384)
        self.save(active, query)
        self.assertEqual(session_store.load_session(self.path).analysis, active)
        # The provider/options copy uses the same narrow proof exception.
        copied = session_store._simple_metadata({'outlet_snap': active.diagnostics['outlet_snap']})
        self.assertEqual(copied['outlet_snap'], active.diagnostics['outlet_snap'])
        invalid = replace(active, diagnostics={**active.diagnostics, 'unrelated': 'x' * 16_385})
        with self.assertRaises(ValueError):
            self.save(invalid, query)
        with self.assertRaises(ValueError):
            session_store._simple_metadata({'unrelated': 'x' * 16_385})

    def test_active_smoothed_surface_validates_against_its_native_candidate(self):
        active, query, directions, dem = self.fixture()
        geometry = QgsGeometry()
        geometry.fromWkb(active.geometry_wkb)
        smoothed = smooth_boundary(geometry, 30, active.cell_width, active.cell_height,
                                   QgsPointXY(active.outlet_x, active.outlet_y))
        smooth = analyze_basin(directions, dem, active.outlet_x, active.outlet_y,
                               surface_wkb=bytes(smoothed.asWkb()))
        smooth = replace(smooth, selected_x=active.selected_x, selected_y=active.selected_y,
                         diagnostics={**smooth.diagnostics, 'outlet_snap': active.diagnostics['outlet_snap'],
                         'analysis_surface': dict(kind='smoothed', degree=30,
                             original_area_m2=active.area_m2,
                             original_perimeter_m=active.perimeter_m,
                             original_cell_count=active.cell_count)})
        state = dict(original_checked=True, smooth_checked=True, degree=30,
                     base_geometry_wkb=active.geometry_wkb,
                     geometry_wkb=dict(original=active.geometry_wkb, suavizada=bytes(smoothed.asWkb())))
        self.assertNotEqual(smooth.area_m2, active.area_m2)
        self.save(smooth, query, state)
        self.assertEqual(session_store.load_session(self.path).analysis, smooth)

    def test_fine_snapshot_survives_later_manual_500_with_historical_reference(self):
        coarse_d8, coarse_dem = self.rasters('coarse', 5, 500)
        fine_d8, fine_dem = self.rasters('fine', 100, 25)
        point = (1242.2, 1402.7)
        original = analyze_basin(coarse_d8, coarse_dem, *point)
        context = dict(dem_path=str(coarse_dem), directions_path=str(coarse_d8),
                       original_x=point[0], original_y=point[1],
                       search_radius_m=707.1067811865476, reference_area_m2=original.area_m2)
        first = build_manual_outlet(context, 1250, 1750)['analysis']
        reference = dict(geometry_wkb=first.geometry_wkb, main_course_wkb=first.main_course_wkb,
                         crs_wkt=first.crs_wkt)
        fine = self.refined(fine_d8, fine_dem, point, reference, historical=original.area_m2)
        later = build_manual_outlet(context, 1250, 750)['analysis']
        self.assertEqual(len({first.area_m2, later.area_m2, original.area_m2}), 3)
        snap = fine.diagnostics['outlet_snap']
        self.assertEqual(snap['reference_geometry_area_m2'], first.area_m2)
        self.assertEqual(snap['reference_area_m2'], original.area_m2)
        coarse_query = self.query(point, coarse_d8, coarse_dem, 5, 500)
        fine_query = replace(coarse_query, directions_path=str(fine_d8), dem_path=str(fine_dem))
        bundle = dict(active_key='25', options=[
            dict(key='500', resolution=500., label='500m', provider='Synthetic', available=True),
            dict(key='25', resolution=25., label='25m', provider='Synthetic', available=True)],
            variants={
                '500': dict(analysis=later, query=coarse_query, smoothing_state=self.state),
                '25': dict(analysis=fine, query=fine_query, smoothing_state=self.state)})
        self.save(fine, fine_query, bundle=bundle)
        for path in (coarse_d8, coarse_dem, fine_d8, fine_dem):
            path.unlink()
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.analysis, fine)
        self.assertEqual(restored.resolution_state['variants']['500']['analysis'], later)
        self.assertEqual(restored.analysis.diagnostics['outlet_snap']['reference_area_m2'], original.area_m2)

    def test_actual_126_legacy_document_keeps_its_original_selection(self):
        source = Path(__file__).with_name('fixtures') / 'outlet_main_course_v126.json'
        document = json.loads(source.read_text(encoding='utf-8'))
        restored = session_store.load_session(source)
        self.assertEqual(restored.analysis.diagnostics['outlet_snap']['selection_mode'], 'upstream_main_course')
        self.save(restored.analysis, restored.query, restored.smoothing_state)
        saved = session_store.load_session(self.path)
        self.assertEqual(saved.analysis, restored.analysis)
        self.assertEqual(json.loads(self.path.read_text())['analysis']['diagnostics']['outlet_snap'],
                         document['analysis']['diagnostics']['outlet_snap'])

    def test_rational_nearest_chain_proof_roundtrips_without_source_rasters(self):
        from test_rational_basin import rational_fixture

        with rational_fixture() as fixture:
            active, query, directions, dem = self.rational_nearest(fixture)
            record = active.diagnostics['outlet_snap']
            self.assertEqual(record['reference_policy_version'], 2)
            self.assertGreater(record['candidate_count'], 20)
            self.assertEqual((active.outlet_x, active.outlet_y), (2237.5, 2237.5))
            self.assertFalse(active.touches_boundary)
            self.save(active, query)
            for path in (directions, dem, *fixture.paths.values()):
                path.unlink()
            with patch.object(gdal, 'Open', side_effect=AssertionError('No source rasters')):
                restored = session_store.load_session(self.path)
            self.assertEqual(restored.analysis, active)
            self.assertEqual(restored.query, query)
            self.assertEqual(restored.query.source_kind, 'rational')

    def test_rational_nearest_chain_corruption_is_rejected_before_raster_work(self):
        from test_rational_basin import rational_fixture

        with rational_fixture() as fixture:
            active, query, _, _ = self.rational_nearest(fixture)
            self.save(active, query)
            previous = self.path.read_bytes()
            base = active.diagnostics['outlet_snap']
            bad_connection = deepcopy(base['candidate_chain_cells'])
            bad_connection[0][2] = 1
            bad_accumulation = deepcopy(base['candidate_chain_cells'])
            bad_accumulation[1][3] = bad_accumulation[0][3]
            bad_overlap = deepcopy(base['candidate_chain_cells'])
            bad_overlap[1][4] = bad_overlap[0][4] - 1
            terminal_cycle = deepcopy(base['candidate_chain_cells'])
            terminal_cycle[-1][2] = 64
            corruptions = (
                ('selection_policy', 'nearest_reference_section'),
                ('projection_kind', 'orthogonal'),
                ('reference_policy_version', True),
                ('candidate_chain_cells', []),
                ('candidate_chain_cells', bad_connection),
                ('candidate_chain_cells', bad_accumulation),
                ('candidate_chain_cells', bad_overlap),
                ('candidate_chain_cells', terminal_cycle),
                ('candidate_chain_cells', base['candidate_chain_cells'][1:]),
                ('reference_course_wkb_hex', None),
                ('candidate_count', base['candidate_count'] - 1),
                ('branch_ambiguity_tolerance_m', 1.25),
                ('branch_distance_m', base['branch_distance_m'] + 1),
                ('branch_runner_up_distance_m', None),
                ('section_projection_x', active.outlet_x),
            )
            with patch.object(session_store, '_reference_mask_counts',
                              side_effect=AssertionError('Must preflight first')) as rasterize:
                for key, value in corruptions:
                    invalid = replace(active, diagnostics={**active.diagnostics,
                                      'outlet_snap': {**base, key: value}})
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        self.save(invalid, query)
                    self.assertEqual(self.path.read_bytes(), previous)
                rasterize.assert_not_called()
            raster_query = replace(query, source_kind='caumax_raster', rational_context=None,
                                   flow=10)
            with self.assertRaisesRegex(ValueError, 'racional'):
                self.save(active, raster_query)
            self.assertEqual(self.path.read_bytes(), previous)

    def test_larger_valid_basin_cannot_replace_the_nearest_rational_closure(self):
        from test_rational_basin import rational_fixture

        with rational_fixture() as fixture:
            active, query, directions, dem = self.rational_nearest(fixture)
            base = active.diagnostics['outlet_snap']
            row, col, _, count, overlap = base['candidate_chain_cells'][1]
            x, y = (col + .5) * 25, 4500 - (row + .5) * 25
            larger = analyze_basin(directions, dem, x, y)
            self.assertGreater(larger.area_m2, active.area_m2)
            distance = ((query.x - x) ** 2 + (query.y - y) ** 2) ** .5
            area, intersection = count * 625, overlap * 625
            ref_area = base['reference_raster_area_m2']
            record = {**base, 'snapped_row': row, 'snapped_col': col,
                      'snapped_x': x, 'snapped_y': y,
                      'candidate_upstream_area_m2': area, 'intersection_area_m2': intersection,
                      'distance_m': distance, 'branch_distance_m': distance,
                      'branch_runner_up_distance_m': base['distance_m'],
                      'reference_coverage_fraction': intersection / ref_area,
                      'candidate_overlap_fraction': intersection / area,
                      'intersection_over_union': intersection / (ref_area + area - intersection),
                      'area_change_vs_original_cell_m2': area - base['original_cell_upstream_area_m2'],
                      'area_change_vs_reference_pct': 100 * (area / base['reference_area_m2'] - 1),
                      'candidate_touches_boundary': larger.touches_boundary}
            larger = replace(larger, selected_x=query.x, selected_y=query.y,
                             diagnostics={**larger.diagnostics, 'outlet_snap': record})
            with patch.object(session_store, '_reference_mask_counts',
                              side_effect=AssertionError('Must preflight first')):
                with self.assertRaisesRegex(ValueError, 'minimiza'):
                    self.save(larger, query)


if __name__ == '__main__':
    unittest.main()
