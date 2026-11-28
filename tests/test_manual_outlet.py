"""Explicit native-cell closure: real GDAL/OGR, no engine or map mutations."""

import importlib
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest

import numpy as np
from osgeo import gdal, ogr, osr

_SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'caumax_consulta'
_PACKAGE = types.ModuleType('_caumax_manual_outlet_under_test')
_PACKAGE.__path__ = [str(_SOURCE)]
sys.modules[_PACKAGE.__name__] = _PACKAGE
manual = importlib.import_module(_PACKAGE.__name__ + '.manual_outlet')


class ManualOutletTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='manual_outlet_')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def fixture(self, resolution=5):
        rows, cols = np.indices((7, 11))
        codes = np.where(rows < 3, 4, np.where(rows > 3, 64, 1)).astype(np.uint8)
        codes[3, -1] = 0
        heights = 200 - cols + abs(rows - 3)
        transform = (300000, resolution, 0, 4700000, 0, -resolution)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25830)
        paths = []
        for name, data, dtype, nodata in [('directions.tif', codes, gdal.GDT_Byte, 255),
                                          ('dem.tif', heights, gdal.GDT_Float64, -9999)]:
            path = self.folder / name
            dataset = gdal.GetDriverByName('GTiff').Create(str(path), 11, 7, 1, dtype)
            dataset.SetGeoTransform(transform)
            dataset.SetProjection(reference.ExportToWkt())
            band = dataset.GetRasterBand(1)
            band.SetNoDataValue(nodata)
            band.WriteArray(data)
            dataset = None
            paths.append(str(path))
        x, y = gdal.ApplyGeoTransform(transform, 3.5, 3.5)
        return dict(directions_path=paths[0], dem_path=paths[1], original_x=x, original_y=y,
                    search_radius_m=4 * resolution, reference_area_m2=1000 * resolution ** 2,
                    metadata={'engine': 'Existing native D8', 'source_key': 'synthetic-source'}), transform

    def test_manual_downstream_recomputes_basin_and_course_at_native_five_and_500_metres(self):
        for resolution in (5, 500):
            with self.subTest(resolution=resolution):
                context, transform = self.fixture(resolution)
                context['metadata'].update(outlet_snap={'method': 'obsolete-auto'},
                                           coverage_warning='Obsolete warning')
                original_bytes = [Path(context[key]).read_bytes() for key in ('directions_path', 'dem_path')]
                target = gdal.ApplyGeoTransform(transform, 5.5, 3.5)
                progress = []
                payload = manual.build_manual_outlet(context, target[0] + 0.1, target[1] - 0.1,
                                                      publish=progress.append)
                analysis = payload['analysis']
                snap = analysis.diagnostics['outlet_snap']
                self.assertEqual(analysis.cell_width, resolution)
                self.assertEqual((analysis.selected_x, analysis.selected_y),
                                 (context['original_x'], context['original_y']))
                self.assertEqual((analysis.outlet_x, analysis.outlet_y), tuple(target))
                self.assertEqual(analysis.cell_count, 42)
                self.assertEqual(analysis.area_m2, 42 * resolution ** 2)
                self.assertEqual(snap['original_cell_upstream_area_m2'], 28 * resolution ** 2)
                self.assertEqual(snap['area_change_vs_original_cell_m2'], 14 * resolution ** 2)
                self.assertEqual(snap['method'], 'manual_cell_d8')
                self.assertEqual(snap['selection_mode'], 'user_selected_cell')
                self.assertIs(snap['explicit_user_choice'], True)
                course = ogr.CreateGeometryFromWkb(analysis.main_course_wkb)
                self.assertEqual(course.GetPoint_2D(0), tuple(target))
                basin = ogr.CreateGeometryFromWkb(analysis.geometry_wkb)
                self.assertEqual(basin.GetArea(), analysis.area_m2)
                self.assertEqual(payload['metadata']['source_key'], 'synthetic-source')
                self.assertEqual(payload['metadata']['outlet_snap'], snap)
                self.assertIsNot(payload['metadata']['outlet_snap'], snap)
                self.assertEqual(context['metadata']['outlet_snap'], {'method': 'obsolete-auto'})
                self.assertNotEqual(payload['metadata'].get('coverage_warning'), 'Obsolete warning')
                self.assertNotIn('manual_outlet_selected', context['metadata'])
                self.assertEqual(progress[0], 0)
                self.assertEqual(progress[-1], 100)
                self.assertEqual(progress, sorted(progress))
                self.assertEqual(original_bytes,
                                 [Path(context[key]).read_bytes() for key in ('directions_path', 'dem_path')])

    def test_manual_upstream_and_lateral_choices_are_permitted_explicitly(self):
        context, transform = self.fixture()
        for row, col, expected_cells in ((3, 1, 14), (1, 3, 2), (5, 3, 2)):
            with self.subTest(row=row, col=col):
                target = gdal.ApplyGeoTransform(transform, col + 0.5, row + 0.5)
                analysis = manual.build_manual_outlet(context, *target)['analysis']
                self.assertEqual(analysis.cell_count, expected_cells)
                self.assertEqual((analysis.outlet_x, analysis.outlet_y), tuple(target))
                self.assertTrue(analysis.diagnostics['outlet_snap']['explicit_user_choice'])

    def test_refined_manual_outlet_rejects_lateral_catchment_without_explicit_override(self):
        """A D8-valid hillslope must not be presented as the same refined basin."""
        context, transform = self.fixture()
        main = gdal.ApplyGeoTransform(transform, 5.5, 3.5)
        reference = manual.build_manual_outlet(context, *main)['analysis']
        context['reference_geometry_wkb_hex'] = bytes(reference.geometry_wkb).hex()
        context['reference_crs_wkt'] = reference.crs_wkt
        context['verify_reference_overlap'] = True
        verified = manual.build_manual_outlet(context, *main)
        self.assertTrue(verified['metadata']['manual_reference_overlap']
                        ['reference_correspondence_valid'])
        self.assertFalse(verified['analysis'].diagnostics['outlet_snap']
                         ['reference_mismatch_override'])
        hillside = gdal.ApplyGeoTransform(transform, 3.5, 1.5)
        with self.assertRaisesRegex(ValueError, 'cuenca diferente'):
            manual.build_manual_outlet(context, *hillside)
        context['allow_reference_mismatch'] = True
        other = manual.build_manual_outlet(context, *hillside)
        self.assertEqual(other['analysis'].cell_count, 2)
        self.assertTrue(other['analysis'].diagnostics['outlet_snap']
                        ['reference_mismatch_override'])
        self.assertFalse(other['metadata']['manual_reference_overlap']
                         ['reference_correspondence_valid'])

    def test_fine_manual_outlet_refuses_raster_edge_without_changing_sources(self):
        context, transform = self.fixture()
        context['require_complete_coverage'] = True
        point = gdal.ApplyGeoTransform(transform, 5.5, 3.5)
        with self.assertRaisesRegex(ValueError, 'borde del MDT refinado'):
            manual.build_manual_outlet(context, *point)
        self.assertFalse(context['metadata'].get('manual_outlet_selected', False))

    def test_manual_choice_can_exceed_automatic_radius_within_the_native_extent(self):
        context, _ = self.fixture()
        context['search_radius_m'] = 8
        for dx in (7.6, 9, 30):
            with self.subTest(dx=dx):
                analysis = manual.build_manual_outlet(
                    context, context['original_x'] + dx, context['original_y'])['analysis']
                snap = analysis.diagnostics['outlet_snap']
                self.assertGreater(snap['distance_m'], snap['search_radius_m'])
                self.assertEqual(snap['manual_selection_scope'], 'raster_extent')
                self.assertEqual(snap['manual_raster_size'], [11, 7])
                self.assertEqual(snap['manual_raster_bounds'], [300000, 4699965, 300055, 4700000])
                self.assertEqual((analysis.selected_x, analysis.selected_y),
                                 (context['original_x'], context['original_y']))

    def test_manual_2745_metre_adjustment_is_not_limited_to_707_metres(self):
        context, _ = self.fixture(resolution=500)
        context['search_radius_m'] = math.hypot(500, 500)
        analysis = manual.build_manual_outlet(context, context['original_x'] + 2745,
                                              context['original_y'])['analysis']
        snap = analysis.diagnostics['outlet_snap']
        self.assertEqual(snap['distance_m'], 2500)
        self.assertGreater(snap['distance_m'], snap['search_radius_m'])
        self.assertEqual(snap['manual_selection_scope'], 'raster_extent')
        self.assertEqual(snap['manual_raster_bounds'], [300000, 4696500, 305500, 4700000])
        course = ogr.CreateGeometryFromWkb(analysis.main_course_wkb)
        self.assertEqual(course.GetPoint_2D(0), (analysis.outlet_x, analysis.outlet_y))

    def test_manual_choice_cannot_leave_the_native_raster_extent(self):
        context, _ = self.fixture()
        for x, y in ((299999, context['original_y']), (300056, context['original_y']),
                     (context['original_x'], 4700001), (context['original_x'], 4699964)):
            with self.subTest(x=x, y=y), self.assertRaisesRegex(ValueError, 'fuera'):
                manual.build_manual_outlet(context, x, y)

    def test_manual_choice_rejects_direction_and_elevation_nodata(self):
        for key, nodata in (('directions_path', 255), ('dem_path', -9999)):
            with self.subTest(source=key):
                context, transform = self.fixture()
                dataset = gdal.Open(context[key], gdal.GA_Update)
                dataset.GetRasterBand(1).WriteArray(np.array([[nodata]]), 5, 3)
                dataset = None
                target = gdal.ApplyGeoTransform(transform, 5.5, 3.5)
                with self.assertRaisesRegex(ValueError, 'NoData'):
                    manual.build_manual_outlet(context, *target)

    def test_manual_cancellation_and_invalid_coordinates_return_no_payload(self):
        context, _ = self.fixture()
        with self.assertRaises(InterruptedError):
            manual.build_manual_outlet(context, context['original_x'], context['original_y'],
                                       cancelled=lambda: True)
        with self.assertRaisesRegex(ValueError, 'finitas'):
            manual.build_manual_outlet(context, math.nan, context['original_y'])
        cancel = [False]

        def progress(percent):
            if percent >= 10:
                cancel[0] = True

        with self.assertRaises(InterruptedError):
            manual.build_manual_outlet(context, context['original_x'], context['original_y'],
                                       cancelled=lambda: cancel[0], publish=progress)


if __name__ == '__main__':
    unittest.main()
