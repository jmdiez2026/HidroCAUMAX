"""Keep accepted fine/manual closures when a second D8 engine prepares products.

The TauDEM CLI outputs are controlled native rasters. All accumulation,
reference matching, delineation, CRS transforms and product IO are real GDAL.
These are synthetic drainage networks, not the user's unavailable terrain.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsApplication

from caumax_consulta import drainage_controller as controller_module
from caumax_consulta import taudem_engine as engine
from caumax_consulta.terrain import analyze_basin
import test_refinement_snap as refinement_fixtures


class TauDEMOutletConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.fixture = refinement_fixtures.RefinementSnapTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.dem, self.directions, self.reference, self.counts = self.fixture.valley(25)
        self.folder = self.fixture.fixture.folder
        self.click = tuple(self.fixture.fixture.point(self.dem, 18, 12))
        self.validated = self.fixture.analyze(self.dem, self.reference, self.click).analysis
        dataset = gdal.Open(str(self.directions))
        self.transform = dataset.GetGeoTransform()
        self.crs = engine._b._spatial_reference(dataset, 'test directions')
        self.codes = dataset.ReadAsArray()
        dataset = None
        self.parameters = (54e6, .9, 707.1067811865476)
        self.coarse = SimpleNamespace(**self.reference, area_m2=54e6)
        self.calls = []

    def raster(self, path, data, nodata, kind=gdal.GDT_Float32):
        data = np.asarray(data)
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), data.shape[1], data.shape[0], 1, kind)
        dataset.SetGeoTransform(self.transform)
        dataset.SetProjection(self.crs.ExportToWkt())
        dataset.GetRasterBand(1).SetNoDataValue(nodata)
        dataset.GetRasterBand(1).WriteArray(data)
        dataset = None

    def cli(self, runtime, tool, arguments, work, cancelled):
        self.calls.append(tool)
        options = {key: arguments[index + 1] for index, key in enumerate(arguments[:-1])
                   if isinstance(key, str) and key.startswith('-')}
        if tool == 'pitremove':
            source = gdal.Open(str(options['-z']))
            gdal.GetDriverByName('GTiff').CreateCopy(str(options['-fel']), source)
            source = None
        elif tool == 'd8flowdir':
            native = np.full(self.codes.shape, -32768, dtype=np.int16)
            for code, tau in ((1, 1), (128, 2), (64, 3), (32, 4), (16, 5), (8, 6), (4, 7), (2, 8)):
                native[self.codes == code] = tau
            self.raster(options['-p'], native, -32768, gdal.GDT_Int16)
            self.raster(options['-sd8'], np.zeros(self.codes.shape), -9999)
        elif tool == 'aread8':
            vector = ogr.Open(str(options['-o']))
            feature = vector.GetLayer(0).GetNextFeature()
            x, y = feature.GetGeometryRef().GetPoint_2D(0)
            feature = vector = None
            coded = Path(options['-p']).parent / 'd8_caumax.tif'
            directions = gdal.Open(str(coded))
            counts = engine._h._flow_accumulation(directions.GetRasterBand(1))
            cell = engine.cell_at_point(x, y, self.transform[0],
                self.transform[3] + self.codes.shape[0] * self.transform[5],
                self.transform[0] + self.codes.shape[1] * self.transform[1],
                self.transform[3], self.codes.shape[1], self.codes.shape[0])
            mask, window, _, _ = engine._b._visit_upstream(directions.GetRasterBand(1),
                cell.row, cell.col, cancelled, lambda n: None)
            left, top, width, height = window
            values = np.full(counts.shape, -1., dtype=np.float32)
            values[top:top + height, left:left + width][mask != 0] = \
                counts[top:top + height, left:left + width][mask != 0]
            directions = None
            self.raster(options['-ad8'], values, -1)
        elif tool == 'threshold':
            source = gdal.Open(str(options['-ssa']))
            values = source.ReadAsArray()
            source = None
            self.raster(options['-src'], (values >= float(options['-thresh'])).astype(np.uint8), 255, gdal.GDT_Byte)
        elif tool == 'streamnet':
            outlet = ogr.Open(str(options['-o']))
            feature = outlet.GetLayer(0).GetNextFeature()
            x, y = feature.GetGeometryRef().GetPoint_2D(0)
            feature = outlet = None
            vector = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(options['-net']))
            layer = vector.CreateLayer('network', self.crs, ogr.wkbLineString)
            feature = ogr.Feature(layer.GetLayerDefn())
            line = ogr.Geometry(ogr.wkbLineString)
            line.AddPoint_2D(x, y)
            line.AddPoint_2D(x, y + 25)
            feature.SetGeometry(line)
            layer.CreateFeature(feature)
            feature = layer = vector = None
        else:
            self.fail('Unexpected TauDEM command: ' + tool)
        return dict(tool=tool, seconds=0., version='synthetic-D8', returncode=0)

    def prepare(self, analysis, reference=True):
        binary = self.folder / 'fixture-binary'
        binary.write_text('Controlled TauDEM output; never executed.', encoding='utf-8')
        runtime = dict(available=True, executables={name: str(binary) for name in engine._TOOLS},
                       runtime={'kind': 'synthetic-test'})
        with patch.object(engine, 'probe_taudem', return_value=runtime), \
                patch.object(engine._runtime, 'ensure_runtime', side_effect=lambda current, cancelled: current), \
                patch.object(engine, '_run', side_effect=self.cli), \
                patch.object(engine._h, '_runtime_folder', return_value=self.folder), \
                patch.object(engine._h, '_nearest_main_channel', side_effect=AssertionError('No second snap')):
            return engine.prepare_taudem(self.dem, *self.click, self.folder / 'tau_cache',
                reference_area_m2=self.parameters[0], reference_cell_width=500, reference_cell_height=500,
                outlet_analysis=analysis, refinement_reference=self.reference if reference else None,
                threshold_area_m2=625)

    def assert_session_roundtrip(self, products):
        from caumax_consulta.caumax_bridge import NativeQuery
        from caumax_consulta.session_store import save_session, load_session
        from qgis.core import QgsCoordinateReferenceSystem
        query = NativeQuery(12., 100, *self.click, QgsCoordinateReferenceSystem('EPSG:25830'),
            products.caumax_d8_path, products.dem_path, 'Synthetic changed engine')
        destination = self.folder / 'taudem_changed_engine.caumax.json'
        save_session(destination, products.analysis, query,
                     dict(original_checked=True, smooth_checked=False, degree=1))
        with patch.object(gdal, 'Open', side_effect=AssertionError('Restore must not need source rasters')):
            restored = load_session(destination)
        self.assertEqual(restored.analysis, products.analysis)

    def test_validated_fine_closure_survives_new_d8_and_recomputed_geometry(self):
        self.assertNotEqual(self.click, (self.validated.outlet_x, self.validated.outlet_y))
        self.assertEqual(self.validated.cell_count, 276)
        self.codes[1, 2] = 64  # One native cell now leaves the basin; do not inherit its previous area.
        result = self.prepare(self.validated)
        self.assertEqual((result.analysis.outlet_x, result.analysis.outlet_y),
                         (self.validated.outlet_x, self.validated.outlet_y))
        self.assertEqual((result.analysis.selected_x, result.analysis.selected_y), self.click)
        self.assertEqual(result.analysis.cell_count, 275)
        self.assertEqual(result.analysis.area_m2, 275 * 625)
        self.assertNotEqual(result.analysis.geometry_wkb, self.validated.geometry_wkb)
        trusted = analyze_basin(result.caumax_d8_path, self.dem,
                               self.validated.outlet_x, self.validated.outlet_y)
        self.assertEqual(result.analysis.geometry_wkb, trusted.geometry_wkb)
        self.assertEqual(ogr.CreateGeometryFromWkb(result.analysis.main_course_wkb).GetPoint_2D(0),
                         (result.analysis.outlet_x, result.analysis.outlet_y))
        snap = result.analysis.diagnostics['outlet_snap']
        selection = result.metadata['outlet_selection']
        self.assertTrue(selection['outlet_preserved'])
        self.assertTrue(selection['coarse_reference_checked'])
        self.assertTrue(selection['fine_reference_checked'])
        self.assertGreater(snap['reference_coverage_fraction'], .5)
        self.assertEqual(snap['candidate_overlap_fraction'], 1)
        self.assert_session_roundtrip(result)

    def test_new_taudem_d8_cannot_replace_validated_basin_with_tiny_closure_or_another_river(self):
        self.codes[10, 10:30] = 1  # Split the footprint between two rivers; neither keeps a majority.
        with self.assertRaisesRegex(ValueError, 'no conservan la cuenca'):
            self.prepare(self.validated)
        self.assertNotIn('aread8', self.calls)
        self.assertNotIn('streamnet', self.calls)
        self.assertEqual(self.validated.cell_count, 276)

    def test_coarse_match_alone_cannot_override_an_incompatible_fine_footprint(self):
        point = self.fixture.fixture.point(self.dem, 18, 30)
        other = analyze_basin(self.directions, self.dem, *point)
        other = replace(other, selected_x=self.click[0], selected_y=self.click[1])
        with self.assertRaisesRegex(ValueError, 'no conservan la cuenca'):
            self.prepare(other)
        self.assertNotIn('aread8', self.calls)
        self.assertNotIn('streamnet', self.calls)

    def test_manual_choice_is_fixed_even_when_it_is_outside_coarse_reference(self):
        from caumax_consulta.manual_outlet import build_manual_outlet
        point = tuple(self.fixture.fixture.point(self.dem, 18, 30))
        context = dict(original_x=self.click[0], original_y=self.click[1],
            directions_path=str(self.directions), dem_path=str(self.dem),
            search_radius_m=self.parameters[2], reference_area_m2=self.parameters[0])
        manual = build_manual_outlet(context, *point)['analysis']
        result = self.prepare(manual)
        self.assertEqual((result.analysis.outlet_x, result.analysis.outlet_y), point)
        self.assertEqual((result.analysis.selected_x, result.analysis.selected_y), self.click)
        self.assertTrue(result.analysis.diagnostics['outlet_snap']['explicit_user_choice'])
        self.assertFalse(result.metadata['outlet_selection']['coarse_reference_checked'])
        self.assert_session_roundtrip(result)

    def test_default_outlet_uses_containing_cell_and_crs_transform_keeps_accepted_cell(self):
        click = self.click[0] + 4, self.click[1] - 3
        cell, snap, selection = engine._preserved_outlet(self.directions, self.counts, self.transform,
            self.crs, *click, self.parameters, None, None, lambda: False)
        self.assertEqual((cell.row, cell.col), (18, 12))
        self.assertIsNone(snap)
        self.assertEqual((selection['original_x'], selection['original_y']), click)
        alternate = osr.SpatialReference()
        alternate.ImportFromEPSG(25829)
        alternate.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = osr.CoordinateTransformation(self.crs, alternate)
        x, y, *_ = transform.TransformPoint(*self.click)
        cx, cy, *_ = transform.TransformPoint(self.validated.outlet_x, self.validated.outlet_y)
        geometry = ogr.CreateGeometryFromWkb(self.validated.geometry_wkb)
        course = ogr.CreateGeometryFromWkb(self.validated.main_course_wkb)
        geometry.Transform(transform)
        course.Transform(transform)
        accepted = SimpleNamespace(outlet_x=cx, outlet_y=cy, crs_wkt=alternate.ExportToWkt(), diagnostics={},
            geometry_wkb=bytes(geometry.ExportToWkb()), main_course_wkb=bytes(course.ExportToWkb()),
            cell_width=25, cell_height=25, area_m2=self.validated.area_m2)
        cell, snap, selection = engine._preserved_outlet(self.directions, self.counts, self.transform,
            self.crs, x, y, self.parameters, accepted, self.reference, lambda: False)
        self.assertEqual((cell.row, cell.col), (18, 10))
        self.assertAlmostEqual(snap['original_x'], self.click[0], places=5)
        self.assertAlmostEqual(snap['original_y'], self.click[1], places=5)

    def test_automatic_engine_shift_requires_both_footprints_and_survives_session_roundtrip(self):
        self.codes[1:21, 10] = 1
        self.codes[1:21, 11] = 4  # TauDEM's compatible channel lies one cell east.
        result = self.prepare(self.validated)
        expected = tuple(self.fixture.fixture.point(self.dem, 18, 11))
        self.assertEqual((result.analysis.outlet_x, result.analysis.outlet_y), expected)
        selection = result.metadata['outlet_selection']
        self.assertEqual(selection['method'], 'original_point_dual_reference_section')
        self.assertFalse(selection['outlet_preserved'])
        self.assertEqual(selection['outlet_adjustment_m'], 25)
        for key in ('coarse_reference_coverage_fraction', 'coarse_candidate_overlap_fraction',
                    'fine_reference_coverage_fraction', 'fine_candidate_overlap_fraction'):
            self.assertGreater(selection[key], .5)
        self.assert_session_roundtrip(result)

    def test_standalone_containing_cell_has_no_unsupported_snap_record(self):
        result = self.prepare(None, reference=False)
        self.assertEqual((result.analysis.outlet_x, result.analysis.outlet_y), self.click)
        self.assertNotIn('outlet_snap', result.analysis.diagnostics)
        self.assertEqual(result.metadata['outlet_selection']['method'], 'containing_cell')
        self.assert_session_roundtrip(result)

    def test_rational_policy_survives_products_with_incompatible_coarse_axis(self):
        from caumax_consulta.refinement_snap import NEAREST_CHAIN_SELECTION_POLICY
        # The coarse initial course points east while the qualified fine river
        # is west of the click. The legacy section rule rejects it, although
        # both footprints identify the same unique drainage chain.
        line = ogr.Geometry(ogr.wkbLineString)
        line.AddPoint_2D(*self.click)
        line.AddPoint_2D(self.click[0] + 500, self.click[1])
        reference = dict(self.reference, main_course_wkb=bytes(line.ExportToWkb()),
                         selection_policy=NEAREST_CHAIN_SELECTION_POLICY)
        accepted = self.fixture.analyze(self.dem, reference, self.click).analysis
        self.reference = controller_module._refinement_reference(accepted, self.coarse)
        result = self.prepare(accepted)
        self.assertEqual(result.analysis.diagnostics['outlet_snap']['selection_policy'],
                         NEAREST_CHAIN_SELECTION_POLICY)
        self.assertEqual((result.analysis.outlet_x, result.analysis.outlet_y),
                         (accepted.outlet_x, accepted.outlet_y))
        self.assertTrue(result.metadata['outlet_selection']['fine_reference_checked'])
        self.assertTrue(result.metadata['outlet_selection']['coarse_reference_checked'])

    def test_product_cache_distinguishes_closure_policy(self):
        reference = controller_module._refinement_reference(self.validated, self.coarse)
        changed = dict(reference, selection_policy='nearest_reference_chain_cell')
        self.assertNotEqual(
            controller_module._signature(self.dem, self.validated, self.coarse, reference, None),
            controller_module._signature(self.dem, self.validated, self.coarse, changed, None))

    def test_manual_change_at_same_red_click_invalidates_previous_product_cache(self):
        tasks, received = [], []
        manager = SimpleNamespace(addTask=tasks.append)
        controller = controller_module.DrainageController(
            lambda *args: received.append(args), lambda *args: None,
            lambda text: self.fail(text))
        self.addCleanup(controller.dispose)
        query = SimpleNamespace(dem_path=str(self.dem))
        product = lambda *args, **kwargs: SimpleNamespace(dem_path=str(self.dem), analysis=kwargs['outlet_analysis'])
        def finish():
            task = tasks[-1]
            success = task.run()
            task.finished(success)
            self.assertTrue(success, task.error)
        with patch.object(controller_module.QgsApplication, 'taskManager', return_value=manager), \
                patch.object(controller_module, '_state', return_value={}), \
                patch.object(engine, 'prepare_taudem', side_effect=product) as prepare, \
                patch.object(engine, 'rebuild_network', side_effect=lambda previous, *args, **kwargs: previous) as rebuild:
            controller.prepare('25', self.validated, query, self.coarse, self.folder, threshold_area_m2=625)
            finish()
            controller.prepare('25', self.validated, query, self.coarse, self.folder, threshold_area_m2=1250)
            finish()
            self.assertEqual(prepare.call_count, 1)
            self.assertEqual(rebuild.call_count, 1)
            point = self.fixture.fixture.point(self.dem, 17, 10)
            manual = analyze_basin(self.directions, self.dem, *point)
            manual = replace(manual, selected_x=self.click[0], selected_y=self.click[1],
                diagnostics=dict(manual.diagnostics, outlet_snap=dict(method='manual_cell_d8',
                    selection_mode='user_selected_cell', explicit_user_choice=True)))
            controller.prepare('25', manual, query, self.coarse, self.folder, threshold_area_m2=625)
            finish()
            self.assertEqual(prepare.call_count, 2)
            self.assertEqual(rebuild.call_count, 1)
            self.assertIs(prepare.call_args.kwargs['outlet_analysis'], manual)
            self.assertEqual(prepare.call_args.args[1:3], self.click)
            self.assertIs(received[-1][1].analysis, manual)


if __name__ == '__main__':
    unittest.main()
