"""Synthetic native-grid checks; no QGIS project, profile or installed DEM writes."""

import importlib
import errno
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr


_SOURCE = Path(__file__).resolve().parents[1] / "src" / "caumax_consulta"
_PACKAGE = types.ModuleType("_caumax_dem_hydrology_under_test")
_PACKAGE.__path__ = [str(_SOURCE)]
sys.modules[_PACKAGE.__name__] = _PACKAGE
hydro = importlib.import_module(_PACKAGE.__name__ + ".dem_hydrology")


class DemHydrologyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hydro_tests_")
        self.folder = Path(self.temp.name)
        self.sequence = 0
        self.cache = self.folder / "cache"
        self.cache.mkdir()
        self.generated_paths = set()

    def tearDown(self):
        for path in self.generated_paths:
            path.unlink(missing_ok=True)
        self.temp.cleanup()

    def raster(self, values, resolution=5.0, epsg=25830, transform=None, nodata=-9999):
        self.sequence += 1
        path = self.folder / f"dem_{self.sequence}.tif"
        data = np.asarray(values, dtype=np.float64)
        dataset = gdal.GetDriverByName("GTiff").Create(
            str(path), data.shape[1], data.shape[0], 1, gdal.GDT_Float64)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(epsg)
        dataset.SetProjection(reference.ExportToWkt())
        dataset.SetGeoTransform(transform or (300000, resolution, 0, 4700000, 0, -resolution))
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.SetUnitType("m")
        band.WriteArray(data)
        dataset.FlushCache()
        dataset = None
        return path

    def point(self, path, row, col):
        dataset = gdal.Open(str(path))
        transform = dataset.GetGeoTransform()
        dataset = None
        return gdal.ApplyGeoTransform(transform, col + 0.5, row + 0.5)

    def analyze(self, path, row, col, **kwargs):
        result = hydro.analyze_dem(path, *self.point(path, row, col), self.cache, **kwargs)
        self.generated_paths.add(Path(result.directions_path))
        if result.metadata.get("accumulation_path"):
            self.generated_paths.add(Path(result.metadata["accumulation_path"]))
        return result

    def accumulation(self, codes, **kwargs):
        dataset = gdal.Open(str(self.raster(codes, nodata=255)))
        try:
            return hydro._flow_accumulation(dataset.GetRasterBand(1), **kwargs)
        finally:
            dataset = None

    def channel_fixture(self, *, small_join=True, unrelated=False):
        codes = np.full((40 if unrelated else 15, 35), 255, dtype=np.uint8)
        codes[8, :34] = 1
        codes[8, 34] = 0
        for col in range(2, 21):
            codes[1:8, col] = 4
        codes[6:8, 26] = 4  # Lateral clicked cell drains to the river two rows below.
        if small_join:
            codes[4:6, 25] = 4
            codes[6, 25] = 2  # Three-cell hillslope joins before the actual river.
        if unrelated:
            codes[35, :34] = 1
            codes[35, 34] = 0
            codes[10:35, :] = 4  # Much larger, but not connected to the click's trace.
        return codes

    def snap_codes(self, codes, row, col, *, dx=0.0, dy=0.0, radius=250.0, reference=389e6):
        path = self.raster(codes, nodata=255)
        dataset = gdal.Open(str(path))
        transform = dataset.GetGeoTransform()
        counts = hydro._flow_accumulation(dataset.GetRasterBand(1))
        dataset = None
        x, y = self.point(path, row, col)
        result = hydro._nearest_main_channel(counts, transform, x + dx, y + dy,
                                             reference, .9, radius, directions_path=path)
        return (*result, counts, path)

    def prepared_grid(self, codes, resolution=5):
        """Real native DEM/D8 pair; skip only the external GRASS generation."""
        row, col = np.indices(codes.shape)
        dem = self.raster(np.where(codes == 255, -9999, 1000 - row + abs(col - 5)),
                          resolution=resolution)
        source = gdal.Open(str(dem))
        key = hydro._source_key(source, dem)
        source = None
        direction_source = self.raster(codes, resolution=resolution, nodata=255)
        directions = self.cache / ('d8_' + key[:32] + '.tif')
        cached = gdal.Translate(str(directions), str(direction_source), outputType=gdal.GDT_Byte)
        cached.SetMetadata({'SOURCE_KEY': key, 'HYDRO_VERSION': hydro._VERSION})
        cached = None
        return dem, directions

    def test_default_outlet_is_containing_cell_centre_at_every_native_resolution(self):
        codes = self.branching_receiver()
        for resolution in (500, 25, 5, 2, .5):
            with self.subTest(resolution=resolution):
                dem, directions = self.prepared_grid(codes, resolution)
                centre = tuple(self.point(dem, 14, 5))
                click = centre[0] + .39 * resolution, centre[1] + .49 * resolution
                with (patch.object(hydro, '_run_grass', side_effect=AssertionError('D8 cache exists')),
                      patch.object(hydro, '_nearest_main_channel', side_effect=AssertionError('No channel snap')),
                      patch.object(hydro, '_accumulation', side_effect=AssertionError('No accumulation needed'))):
                    result = hydro.analyze_dem(dem, *click, self.cache,
                        reference_area_m2=389e6, reference_cell_width=500, reference_cell_height=500)
                self.assertEqual((result.selected_x, result.selected_y), click)
                self.assertEqual((result.outlet_x, result.outlet_y), centre)
                self.assertEqual((result.cell_width, result.cell_height), (resolution, resolution))
                self.assertEqual(result.cell_count, 11)  # Receiver plus all three incoming branches.
                self.assertEqual(result.area_m2, 11 * resolution ** 2)
                self.assertAlmostEqual(ogr.CreateGeometryFromWkb(result.geometry_wkb).GetArea(),
                                       result.area_m2)
                course = ogr.CreateGeometryFromWkb(result.main_course_wkb)
                self.assertEqual(course.GetPoint_2D(0), centre)
                self.assertNotIn('outlet_snap', result.diagnostics)
                self.assertFalse(result.metadata['outlet_snapped_to_channel'])
                self.assertIsNone(result.metadata['accumulation_path'])
                self.assertTrue(result.metadata['cache_reused'])
                self.assertEqual(result.directions_path, str(directions))

    def test_default_cell_does_not_require_a_nearby_or_concentrated_channel(self):
        diffuse = np.full((15, 15), 4, dtype=np.uint8)
        diffuse[-1, :] = 0
        for codes, row, col, count in ((self.channel_fixture(), 6, 26, 1),
                                      (diffuse, 7, 7, 8)):
            with self.subTest(count=count):
                dem, _ = self.prepared_grid(codes)
                centre = tuple(self.point(dem, row, col))
                click = centre[0] + 1.4, centre[1] - 1.7
                result = hydro.analyze_dem(dem, *click, self.cache)
                self.assertEqual((result.outlet_x, result.outlet_y), centre)
                self.assertEqual((result.selected_x, result.selected_y), click)
                self.assertEqual(result.cell_count, count)
                self.assertNotIn('outlet_snap', result.diagnostics)

    def test_default_reuses_legacy_d8_cache_without_reusing_the_adjusted_outlet(self):
        dem, directions = self.prepared_grid(self.channel_fixture())
        centre = tuple(self.point(dem, 6, 26))
        click = centre[0] - 1.5, centre[1]
        legacy = hydro.analyze_dem(dem, *click, self.cache, snap_enabled=True,
            reference_area_m2=389e6, reference_cell_width=500, reference_cell_height=500)
        self.assertNotEqual((legacy.outlet_x, legacy.outlet_y), centre)
        self.assertIn('outlet_snap', legacy.diagnostics)
        self.assertTrue(Path(legacy.metadata['accumulation_path']).is_file())
        with (patch.object(hydro, '_run_grass', side_effect=AssertionError('Reuse native D8')),
              patch.object(hydro, '_nearest_main_channel', side_effect=AssertionError('No inherited snap')),
              patch.object(hydro, '_accumulation', side_effect=AssertionError('Ignore legacy accumulation'))):
            current = hydro.analyze_dem(dem, *click, self.cache)
        self.assertEqual(current.directions_path, str(directions))
        self.assertTrue(current.metadata['cache_reused'])
        self.assertEqual((current.outlet_x, current.outlet_y), centre)
        self.assertEqual((current.selected_x, current.selected_y), click)
        self.assertEqual(current.cell_count, 1)
        self.assertNotIn('outlet_snap', current.diagnostics)
        self.assertNotIn('outlet_snap', current.metadata)
        self.assertIsNone(current.metadata['accumulation_path'])

    def test_lateral_click_crosses_small_hillslope_join_before_associating_the_river(self):
        selected, snap, counts, _ = self.snap_codes(self.channel_fixture(), 6, 26)
        self.assertEqual((selected.row, selected.col), (8, 26))
        self.assertEqual((snap['channel_anchor_row'], snap['channel_anchor_col']), (8, 26))
        self.assertEqual(snap['selection_mode'], 'upstream_lateral_nearest_branch')
        self.assertEqual(snap['downstream_connection_path'], [[6, 26, 4], [7, 26, 4], [8, 26, 1]])
        self.assertEqual(snap['upstream_path'], [[8, 26, 1]])
        self.assertEqual(snap['branch_click_station_m'], 0.0)
        self.assertEqual(snap['branch_selected_station_m'], 0.0)
        self.assertEqual(snap['section_projection_x'], selected.x)
        self.assertEqual(snap['section_projection_y'], selected.y)
        self.assertAlmostEqual(snap['association_lateral_distance_m'], 10.0)
        self.assertEqual(snap['association_incoming_area_m2'], 5 * 25)
        self.assertGreater(snap['association_other_inflow_area_m2'], 100 * 25)
        self.assertGreater(snap['candidate_upstream_area_m2'], snap['original_cell_upstream_area_m2'])
        self.assertEqual(snap['candidate_upstream_area_m2'], int(counts[8, 26]) * 25)
        self.assertFalse(snap['reference_threshold_met'])  # Never force the original 90 % area.

    def test_lateral_section_selects_upstream_centre_instead_of_downstream_receiver(self):
        selected, snap, counts, _ = self.snap_codes(self.channel_fixture(), 6, 26, dx=-1.5)
        self.assertEqual((selected.row, selected.col), (8, 25))
        self.assertEqual(snap['upstream_path'], [[8, 25, 1], [8, 26, 1]])
        self.assertAlmostEqual(snap['branch_click_station_m'], 1.5)
        self.assertAlmostEqual(snap['branch_selected_station_m'], 5.0)
        self.assertAlmostEqual(snap['section_offset_m'], -3.5)
        self.assertLess(snap['candidate_upstream_area_m2'], snap['channel_anchor_upstream_area_m2'])
        self.assertEqual(snap['candidate_upstream_area_m2'], int(counts[8, 25]) * 25)

    def branching_receiver(self, diagonal_cells=6):
        codes = np.full((22, 22), 255, dtype=np.uint8)
        codes[14, 5] = 1
        codes[14, 6] = 0
        codes[13, 4] = 2  # Bank pixel connects diagonally to the same receiver.
        codes[11:14, 5] = 4  # Short north branch immediately beside the click.
        for step in range(1, diagonal_cells + 1):
            codes[14 - step, 5 + step] = 8  # Longer, larger NE branch farther from the click.
        return codes

    def test_fine_receiver_preserves_the_nearer_branch_before_comparing_global_lengths(self):
        codes = self.branching_receiver()
        selected, snap, counts, directions = self.snap_codes(codes, 13, 4, dx=2.4, dy=-1.5)
        self.assertEqual((selected.row, selected.col), (13, 5))
        self.assertEqual(snap['upstream_path'], [[13, 5, 4], [14, 5, 1]])
        self.assertEqual(snap['section_source_cell'], [13, 5, 4])
        self.assertLess(int(counts[13, 5]), int(counts[13, 6]))
        self.assertAlmostEqual(snap['distance_m'], math.hypot(2.6, 1.5))
        self.assertLessEqual(snap['section_offset_m'], 0)
        dem = self.raster(200 - np.indices(codes.shape)[0])
        analysis = hydro.analyze_basin(directions, dem, selected.x, selected.y)
        course = ogr.CreateGeometryFromWkb(analysis.main_course_wkb)
        self.assertEqual(course.GetPoint_2D(0), (selected.x, selected.y))
        self.assertEqual(analysis.cell_count, 3)
        self.assertEqual(analysis.area_m2, 3 * 25)
        self.assertAlmostEqual(course.Length(), 10)
        self.assertTrue(all(course.GetPoint_2D(i)[0] == selected.x for i in range(course.GetPointCount())))

    def test_extending_the_farther_branch_does_not_change_the_fine_outlet(self):
        choices = []
        for diagonal_cells in (6, 12):
            selected, snap, _, _ = self.snap_codes(self.branching_receiver(diagonal_cells),
                                                   13, 4, dx=2.4, dy=-1.5)
            choices.append((selected.row, selected.col, snap['section_source_cell']))
        self.assertEqual(choices, [(13, 5, [13, 5, 4]), (13, 5, [13, 5, 4])])

    def test_existing_clicked_tributary_is_not_silently_reclassified_as_lateral_drainage(self):
        codes = self.branching_receiver()
        codes[13, 4] = 255
        with self.assertRaisesRegex(hydro.OutletSelectionRequired, 'rama geométricamente más próxima'):
            self.snap_codes(codes, 13, 5, dy=-1.5)

    def test_geometric_branch_ambiguity_is_not_resolved_by_different_contributions(self):
        # North and NE segments are equidistant on their angle bisector, but
        # their contributing areas differ. Geometry must still request review.
        codes = self.branching_receiver()
        codes[13, 4] = 255
        codes[15, 5] = 64  # A separate lateral trace enters the receiver from south.
        path = self.raster(codes, nodata=255)
        dataset = gdal.Open(str(path))
        transform = dataset.GetGeoTransform()
        counts = hydro._flow_accumulation(dataset.GetRasterBand(1))
        anchor = self.point(path, 14, 5)
        # Exercise the branch geometry directly: the lateral trace's root is
        # excluded by its incoming cell, not by the contributions of N and NE.
        x, y = anchor[0] + (math.sqrt(2) - 1) * 4, anchor[1] + 4
        try:
            for shift in (0.0, 0.1):
                with self.subTest(shift=shift), self.assertRaises(hydro.OutletSelectionRequired) as raised:
                    hydro._b._nearest_upstream_segment(
                        dataset.GetRasterBand(1), transform, x + shift, y, 14, 5, 250,
                        counts=counts, exclude_incoming=((15, 5),))
                self.assertEqual(raised.exception.selection_context['branch_ambiguity_tolerance_m'], .25)
        finally:
            dataset = None

    def test_large_unconnected_channel_is_not_selected_by_accumulation(self):
        selected, snap, counts, _ = self.snap_codes(self.channel_fixture(unrelated=True), 6, 26)
        self.assertGreater(int(counts[35, 34]), int(counts[8, 26]))
        self.assertEqual((selected.row, selected.col), (8, 26))
        self.assertEqual(snap['channel_anchor_row'], 8)

    def test_already_aligned_river_and_headwater_need_no_nine_cell_gate(self):
        for col in (0, 1, 6):
            with self.subTest(col=col):
                selected, snap, counts, _ = self.snap_codes([[1, 1, 1, 1, 1, 1, 0]], 0, col)
                self.assertEqual((selected.row, selected.col), (0, col))
                self.assertEqual(snap['selection_mode'], 'upstream_nearest_branch')
                self.assertEqual(snap['status'], 'unchanged')
                self.assertEqual(snap['candidate_upstream_area_m2'], (col + 1) * 25)
                self.assertEqual(snap['minimum_upstream_area_m2'], 25)

    def test_shared_confluence_projection_represents_one_closure(self):
        codes = np.full((7, 11), 255, dtype=np.uint8)
        codes[5, :5] = 1
        codes[5, 6:] = 16
        codes[5, 5] = 0
        codes[4, 5] = 4
        # Both segments project exactly onto the same confluence centre. There
        # is only one closure, so selecting that common centre is unambiguous.
        selected, snap, _, _ = self.snap_codes(codes, 4, 5)
        self.assertEqual((selected.row, selected.col), (5, 5))
        self.assertEqual(snap['candidate_count'], 1)

    def test_unprojectable_receiver_does_not_fall_back_to_tiny_lateral_basin(self):
        codes = np.full((7, 12), 255, dtype=np.uint8)
        codes[5, :10] = 1
        codes[5, 10] = 0
        codes[4, 11] = 8  # Click NE of the terminal: foot lies beyond its east endpoint.
        with self.assertRaisesRegex(ValueError, 'no se acepta.*pequeña cuenca lateral'):
            self.snap_codes(codes, 4, 11)

    def test_competing_relative_and_absolute_inflows_do_not_certify_a_tiny_basin(self):
        codes = np.full((9, 14), 255, dtype=np.uint8)
        codes[7, :10] = 1  # Ten river cells before the receiving node.
        codes[7, 10] = 0
        codes[5:7, 10] = 4  # Original cell contributes one, then five after the small join.
        codes[6, 7:10] = 1  # Three-cell minor branch, relative inflow 3 / 1.
        # River ratio 10 / 5 is smaller despite its larger contribution. The
        # two associations are geometrically plausible: neither is certified.
        with self.assertRaisesRegex(ValueError, 'asociación es ambigua'):
            self.snap_codes(codes, 5, 10, dx=-1.0)

    def test_receiver_search_preserves_mask_nodata_and_cancellation(self):
        path = self.raster(self.channel_fixture(), nodata=255)
        dataset = gdal.Open(str(path), gdal.GA_Update)
        band = dataset.GetRasterBand(1)
        band.CreateMaskBand(gdal.GMF_PER_DATASET)
        flags = np.full((15, 35), 255, dtype=np.uint8)
        flags[6, 26] = 0
        band.GetMaskBand().WriteArray(flags)
        dataset.FlushCache()
        counts = hydro._flow_accumulation(band)
        transform = dataset.GetGeoTransform()
        dataset = None
        x, y = self.point(path, 6, 26)
        with self.assertRaisesRegex(ValueError, 'original.*D8.*NoData'):
            hydro._nearest_main_channel(counts, transform, x, y, 1000, .9, 250, directions_path=path)
        with self.assertRaises(InterruptedError):
            hydro._nearest_main_channel(counts, transform, x, y, 1000, .9, 250,
                                         cancelled=lambda: True, directions_path=path)

    def test_fine_pipeline_keeps_native_five_metre_basin_course_and_outlet_together(self):
        codes = self.channel_fixture()
        row, col = np.indices(codes.shape)
        dem = self.raster(np.where(codes == 255, -9999, 200 - col + abs(row - 8)), resolution=5)
        source = gdal.Open(str(dem))
        key = hydro._source_key(source, dem)
        source = None
        direction_source = self.raster(codes, nodata=255)
        directions = self.cache / ('d8_' + key[:32] + '.tif')
        cached = gdal.Translate(str(directions), str(direction_source), outputType=gdal.GDT_Byte)
        cached.SetMetadata({'SOURCE_KEY': key, 'HYDRO_VERSION': hydro._VERSION})
        cached = None
        x, y = self.point(dem, 6, 26)
        with patch.object(hydro, '_run_grass', side_effect=AssertionError('Prepared D8 must be reused')):
            result = hydro.analyze_dem(dem, x - 1.5, y, self.cache,
                                       reference_area_m2=389e6, reference_cell_width=500,
                                       reference_cell_height=500, snap_enabled=True)
        snap = result.diagnostics['outlet_snap']
        self.assertEqual(result.cell_width, 5)
        self.assertEqual(result.cell_height, 5)
        self.assertEqual((result.selected_x, result.selected_y), (x - 1.5, y))
        self.assertEqual(result.area_m2, result.cell_count * 25)
        self.assertGreater(result.cell_count, 100)
        self.assertEqual(result.area_m2, snap['candidate_upstream_area_m2'])
        self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.point(dem, 8, 25)))
        geometry = ogr.CreateGeometryFromWkb(result.geometry_wkb)
        self.assertAlmostEqual(geometry.GetArea(), result.area_m2)
        course = ogr.CreateGeometryFromWkb(result.main_course_wkb)
        self.assertEqual(course.GetPoint_2D(0), (result.outlet_x, result.outlet_y))
        for index in range(course.GetPointCount()):
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint_2D(*course.GetPoint_2D(index))
            self.assertTrue(geometry.Contains(point))
        self.assertTrue(result.metadata['cache_reused'])
        self.assertEqual(result.metadata['native_resolution'], (5, 5))
        with patch.object(hydro, '_nearest_main_channel', side_effect=ValueError('Sección ambigua')):
            with self.assertRaises(hydro.OutletSelectionRequired) as raised:
                hydro.analyze_dem(dem, x - 1.5, y, self.cache,
                                  reference_area_m2=389e6, reference_cell_width=500,
                                  reference_cell_height=500, snap_enabled=True)
        context = raised.exception.selection_context
        self.assertEqual(str(raised.exception), 'Sección ambigua')
        self.assertEqual((context['original_x'], context['original_y']), (x - 1.5, y))
        self.assertEqual((context['cell_width'], context['cell_height']), (5, 5))
        self.assertEqual(context['directions_path'], str(directions))
        self.assertEqual(context['dem_path'], str(dem))
        self.assertTrue(Path(context['metadata']['accumulation_path']).is_file())
        with patch.object(hydro, '_accumulation', side_effect=ValueError('Red D8 inválida')):
            with self.assertRaises(ValueError) as failed:
                hydro.analyze_dem(dem, x - 1.5, y, self.cache,
                                  reference_area_m2=389e6, reference_cell_width=500,
                                  reference_cell_height=500, snap_enabled=True)
        self.assertNotIsInstance(failed.exception, hydro.OutletSelectionRequired)

    def test_diffuse_parallel_drainage_is_not_accepted_as_an_automatic_channel(self):
        codes = np.full((15, 15), 4, dtype=np.uint8)
        codes[-1, :] = 0
        with self.assertRaisesRegex(ValueError, 'no distingue un cauce concentrado'):
            self.snap_codes(codes, 7, 7)

    def test_inclusive_accumulation_obeys_all_eight_esri_codes_and_nodata(self):
        result = self.accumulation([[2, 4, 8], [1, 0, 16], [128, 64, 32]])
        expected = np.ones((3, 3), dtype=np.uint32)
        expected[1, 1] = 9
        np.testing.assert_array_equal(result, expected)
        np.testing.assert_array_equal(self.accumulation([[1, 4, 0], [0, 0, 255]]),
                                      [[1, 2, 1], [1, 3, 0]])

    def test_accumulation_rejects_disconnected_cycle_and_unknown_code(self):
        with self.assertRaisesRegex(ValueError, "ciclo"):
            self.accumulation([[0, 255, 1, 16]])
        with self.assertRaisesRegex(ValueError, "código"):
            self.accumulation([[3]])

    def test_accumulation_wide_single_row_crosses_flat_chunk_boundary_exactly(self):
        codes = np.ones((1, 262150), dtype=np.uint8)
        codes[0, -1] = 0
        counts = self.accumulation(codes)
        self.assertEqual(int(counts[0, -1]), 262150)
        self.assertEqual(int(counts[0, 262144]), 262145)

    def test_accumulation_checks_cancellation_during_topological_queue(self):
        codes = np.ones((1, 20000), dtype=np.uint8)
        codes[0, -1] = 0
        cancel = [False]

        def progress(fraction):
            if fraction > 0.2:
                cancel[0] = True

        with self.assertRaises(InterruptedError):
            self.accumulation(codes, cancelled=lambda: cancel[0], progress=progress)

    def test_accumulation_cache_reused_and_low_ram_checked_before_read(self):
        path = self.raster([[1, 1, 0]], nodata=255)
        first, cache, reused = hydro._accumulation(path, lambda: False, lambda n: None)
        self.generated_paths.add(Path(cache))
        self.assertFalse(reused)
        with patch.object(hydro, "_flow_accumulation", side_effect=AssertionError("Must reuse cache")):
            again, again_path, reused = hydro._accumulation(path, lambda: False, lambda n: None)
        self.assertTrue(reused)
        self.assertEqual(again_path, cache)
        np.testing.assert_array_equal(first, again)
        with patch.object(hydro, "_available_memory", return_value=1), \
                patch.object(hydro._b, "_read", side_effect=AssertionError("RAM checked before reading")):
            with self.assertRaisesRegex(ValueError, "memoria"):
                hydro._accumulation(path, lambda: False, lambda n: None)

    def test_snap_requires_directions_to_verify_upstream_relationship(self):
        counts = np.zeros((3, 5), dtype=np.uint32)
        counts[1, 2], counts[1, 1], counts[1, 4] = 1, 90, 1000
        with self.assertRaisesRegex(ValueError, "direcciones D8.*aguas arriba"):
            hydro._nearest_main_channel(
                counts, (100, 10, 0, 200, 0, -10), 125, 185, 10000, .9, 50)

    def test_snap_rejects_original_direction_nodata_even_if_channel_nearby(self):
        codes = np.zeros((3, 5), dtype=np.uint8)
        codes[1, 2] = 255
        transform = (100, 10, 0, 200, 0, -10)
        path = self.raster(codes, transform=transform, nodata=255)
        dataset = gdal.Open(str(path))
        counts = hydro._flow_accumulation(dataset.GetRasterBand(1))
        dataset = None
        with self.assertRaisesRegex(ValueError, "original.*D8"):
            hydro._nearest_main_channel(counts, transform, 125, 185, 10000, .9, 50,
                                         directions_path=path)

    def test_snap_policy_uses_original_diagonal_and_validates_reference(self):
        area, fraction, radius = hydro._snap_parameters(389e6, 500, 500, None, .9)
        self.assertEqual(area, 389e6)
        self.assertEqual(fraction, .9)
        self.assertAlmostEqual(radius, math.hypot(500, 500))
        self.assertEqual(hydro._snap_parameters(1000, 25, 25, None, .9)[2], 500)
        for values in ((0, 500, 500, None, .9), (389e6, True, 500, None, .9),
                       (389e6, 500, 500, 0, .9), (389e6, 500, 500, None, 1.1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                hydro._snap_parameters(*values)

    def test_cache_publication_handles_another_volume_atomically(self):
        converted = self.folder / "converted.tif"
        converted.write_bytes(b"unchanged native-grid directions")
        destination = self.cache / "result.tif"
        real_replace = os.replace
        calls = []

        def replace(source, target):
            calls.append((source, target))
            if len(calls) == 1:
                raise OSError(errno.EXDEV, "another volume")
            return real_replace(source, target)

        with patch.object(hydro.os, "replace", side_effect=replace):
            hydro._publish_cache(converted, destination, lambda: False)
        self.assertEqual(destination.read_bytes(), converted.read_bytes())
        self.assertEqual(list(self.cache.iterdir()), [destination])

    def test_engine_probe_reports_bundled_compiled_engine(self):
        engine = hydro.availability_engine()
        self.assertIn("available", engine)
        self.assertIn("reason", engine)
        if engine["available"] and os.name == "nt":
            self.assertTrue((Path(engine["path"]) / "bin" / "r.watershed.exe").is_file())
            self.assertTrue(Path(engine["python"]).is_file())

    def test_conversion_exact_esri_codes_terminals_and_nodata(self):
        raw = self.raster([[1, 2, 3, 4, 5, 6, 7, 8, -1, -8, 0, -9999]])
        dem = gdal.Open(str(raw))
        target = self.cache / "converted.tif"
        hydro._convert_directions(raw, target, dem, "test-key", lambda: False)
        dem = None
        generated = gdal.Open(str(target))
        np.testing.assert_array_equal(generated.ReadAsArray(),
                                      [[128, 64, 32, 16, 8, 4, 2, 1, 0, 0, 0, 255]])
        self.assertEqual(generated.GetRasterBand(1).GetNoDataValue(), 255)
        self.assertEqual(generated.GetMetadataItem("SOURCE_KEY"), "test-key")
        generated = None

    def test_unknown_grass_direction_is_rejected(self):
        raw = self.raster([[9]])
        dem = gdal.Open(str(raw))
        try:
            with self.assertRaisesRegex(ValueError, "códigos"):
                hydro._convert_directions(raw, self.cache / "bad.tif", dem, "x", lambda: False)
        finally:
            dem = None

    def test_invalid_crs_rotation_nodata_and_outside_are_rejected_before_engine(self):
        path = self.raster(np.ones((3, 3)), epsg=4326)
        with self.assertRaisesRegex(ValueError, "proyectado"):
            self.analyze(path, 1, 1)
        rotated = self.raster(np.ones((3, 3)), transform=(300000, 5, 1, 4700000, 0, -5))
        with self.assertRaisesRegex(ValueError, "rotación"):
            self.analyze(rotated, 1, 1)
        path = self.raster([[1, 1, 1], [1, -9999, 1], [1, 1, 1]])
        with self.assertRaisesRegex(ValueError, "NoData"):
            self.analyze(path, 1, 1)
        with self.assertRaisesRegex(ValueError, "fuera"):
            hydro.analyze_dem(path, 0, 0, self.cache)

    def test_missing_engine_is_an_explicit_error(self):
        path = self.raster(np.ones((3, 3)))
        with patch.object(hydro, "availability_engine", return_value={
                "available": False, "reason": "GRASS no disponible", "path": ""}):
            with self.assertRaisesRegex(ValueError, "GRASS no disponible"):
                self.analyze(path, 1, 1)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_initial_cancellation_creates_no_cache(self):
        path = self.raster(np.ones((3, 3)))
        with self.assertRaises(InterruptedError):
            self.analyze(path, 1, 1, cancelled=lambda: True)
        self.assertEqual(list(self.cache.iterdir()), [])

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_long_host_temp_path_and_hash_exclamation_use_short_isolated_job(self):
        row, col = np.indices((7, 9))
        path = self.raster(200 - col * 2 + np.abs(row - 3) * 4, resolution=25)
        padding = max(1, 210 - len(str(self.folder / "special_#!" / "nested_" / "original_mdt.tif")))
        long_folder = self.folder / "special_#!" / ("nested_" + "x" * padding)
        long_folder.mkdir(parents=True)
        long_source = long_folder / "original_mdt.tif"
        long_source.write_bytes(path.read_bytes())
        self.assertGreaterEqual(len(str(long_source)), 210)
        requested_cache = long_folder / ("cache_" + "c" * 30)
        observed = []
        real_run = hydro._run_grass
        environment = {key: os.environ.get(key) for key in ("TEMP", "TMP", "TMPDIR")}

        def checked_run(engine, staged, destination, work, cancelled):
            observed.append(str(work))
            self.assertNotEqual(staged, long_source)
            self.assertEqual(staged.name, "dem.tif")
            if os.name == "nt":
                self.assertLess(len(str(work)), 140)
                self.assertFalse(any(c in str(work) for c in "#!%"))
            original = gdal.Open(str(long_source))
            linked = gdal.Open(str(staged))
            np.testing.assert_array_equal(original.ReadAsArray(), linked.ReadAsArray())
            self.assertEqual(original.GetGeoTransform(), linked.GetGeoTransform())
            original = linked = None
            real_run(engine, staged, destination, work, cancelled)

        try:
            with patch.object(hydro, "_run_grass", side_effect=checked_run):
                result = hydro.analyze_dem(long_source, *self.point(long_source, 3, 8), requested_cache)
            self.assertEqual(result.dem_path, str(long_source.resolve()))
            self.assertEqual(result.cell_width, 25)
            self.assertEqual(result.area_m2, result.cell_count * 625)
            self.assertEqual(len(observed), 1)
            self.assertFalse(Path(observed[0]).exists())
            self.assertEqual(environment, {key: os.environ.get(key) for key in environment})
            self.assertEqual(long_source.read_bytes(), path.read_bytes())
            if os.name == "nt":
                self.assertLess(len(result.directions_path), 170)
        finally:
            if "result" in locals():
                Path(result.directions_path).unlink(missing_ok=True)

    def test_dynamic_memory_budget_accepts_large_five_metre_grid_when_ram_allows(self):
        small = hydro._memory_feasibility(37_000_000, 15_600_000, 32 * 1024 ** 3)
        self.assertTrue(small["feasible"])
        self.assertGreater(small["estimated_bytes"], 2 * 1024 ** 3)
        low = hydro._memory_feasibility(37_000_000, 15_600_000, 2 * 1024 ** 3)
        self.assertFalse(low["feasible"])
        self.assertIn("55 %", low["reason"])
        self.assertFalse(hydro._memory_feasibility(60_000_001, 10, 64 * 1024 ** 3)["feasible"])

    def test_feasibility_reads_resolution_without_reading_dem_pixels(self):
        path = self.raster(np.ones((5, 7)), resolution=2)
        result = hydro.check_feasibility(path, available_bytes=8 * 1024 ** 3)
        self.assertTrue(result["feasible"])
        self.assertEqual(result["native_resolution"], (2, 2))
        self.assertEqual(result["raster_cells"], 35)
        self.assertTrue(result["worst_case"])
        self.assertEqual(list(self.cache.iterdir()), [])

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_real_grass_native_2m_grid_analysis_and_cache_reuse(self):
        row, col = np.indices((7, 9))
        data = 200 - col * 2 + np.abs(row - 3) * 4
        data[0, 0] = -9999
        path = self.raster(data, resolution=2)
        original = path.read_bytes()
        progress = []
        result = self.analyze(path, 3, 8, progress=progress.append)
        self.assertEqual(result.cell_width, 2)
        self.assertEqual(result.cell_height, 2)
        self.assertGreater(result.cell_count, 1)
        self.assertEqual(result.area_m2, 4 * result.cell_count)
        self.assertEqual(ogr.CreateGeometryFromWkb(result.geometry_wkb).GetArea(), result.area_m2)
        self.assertEqual(result.dem_path, str(path.resolve()))
        self.assertEqual(result.metadata["native_resolution"], (2, 2))
        self.assertFalse(result.metadata["cache_reused"])
        generated = gdal.Open(result.directions_path)
        self.assertEqual((generated.RasterXSize, generated.RasterYSize), (9, 7))
        self.assertEqual(generated.GetGeoTransform(), (300000, 2, 0, 4700000, 0, -2))
        self.assertEqual(generated.ReadAsArray()[0, 0], 255)
        generated = None
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(progress[0], 0)
        self.assertEqual(progress[-1], 100)
        self.assertEqual(progress, sorted(progress))
        with patch.object(hydro, "_run_grass", side_effect=AssertionError("Cache must be reused")):
            x, y = self.point(path, 3, 8)
            again = hydro.analyze_dem(path, x + 0.9, y - 0.9, self.cache)
        self.assertTrue(again.metadata["cache_reused"])
        self.assertEqual(again.directions_path, result.directions_path)
        self.assertEqual(again.cell_count, result.cell_count)
        self.assertEqual((again.selected_x, again.selected_y), (x + 0.9, y - 0.9))
        self.assertEqual((again.outlet_x, again.outlet_y), (x, y))

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_conflicting_legacy_snap_retains_original_point_and_reuses_accumulation(self):
        row, col = np.indices((7, 9))
        path = self.raster(200 - col * 2 + np.abs(row - 3) * 4, resolution=2)
        x, y = self.point(path, 2, 5)
        progress = []
        # This click has an incoming D8 branch. The current branch protection
        # requires an explicit choice instead of the historical lateral snap.
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(path, 2, 5, reference_area_m2=80,
                         reference_cell_width=500, reference_cell_height=500,
                         snap_enabled=True, progress=progress.append)
        context = caught.exception.selection_context
        self.assertIn('ramal', str(caught.exception))
        self.assertEqual((context['original_x'], context['original_y']), (x, y))
        self.assertTrue(Path(context['directions_path']).is_file())
        self.assertEqual(progress, sorted(progress))
        self.assertTrue(Path(context['metadata']['accumulation_path']).is_file())
        with patch.object(hydro, "_flow_accumulation", side_effect=AssertionError("Accumulation must be cached")):
            with self.assertRaises(hydro.OutletSelectionRequired) as repeated:
                self.analyze(path, 2, 5, reference_area_m2=80,
                             reference_cell_width=500, reference_cell_height=500, snap_enabled=True)
        again = repeated.exception.selection_context
        self.assertTrue(again['metadata']['accumulation_cache_reused'])
        self.assertEqual(again['directions_path'], context['directions_path'])
        self.assertEqual((again['original_x'], again['original_y']), (x, y))

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_cancelling_running_grass_kills_private_session_and_publishes_no_cache(self):
        path = self.raster(np.ones((3, 3)))
        started = time.monotonic()
        # A deliberately slow private worker makes process cancellation observable
        # without relying on the size/performance of a real terrain computation.
        with patch.object(hydro, "_WORKER", "import time; time.sleep(60)\n"):
            with self.assertRaises(InterruptedError):
                self.analyze(path, 1, 1, cancelled=lambda: time.monotonic() - started > 2)
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual(list(self.cache.iterdir()), [])

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_real_grass_depression_routes_out_without_replacing_original_low_elevation(self):
        values = np.full((9, 9), 100.0)
        values[1:-1, 1:-1] = 20
        values[4, 4] = 0  # strict depression, deliberately not an output DEM fill
        values[4, 8] = 10  # the only low domain outlet
        path = self.raster(values, resolution=25)
        result = self.analyze(path, 4, 8)
        self.assertEqual(result.z_min, 0)
        self.assertEqual(result.values["zmin_m"], 0)
        self.assertEqual(result.area_m2, result.cell_count * 625)
        directions = gdal.Open(result.directions_path)
        codes = directions.ReadAsArray()
        directions = None
        offsets = {1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1),
                   16: (0, -1), 32: (-1, -1), 64: (-1, 0), 128: (-1, 1)}
        row, col, seen = 4, 4, set()
        while (row, col) != (4, 8):
            self.assertNotIn((row, col), seen)
            seen.add((row, col))
            self.assertIn(int(codes[row, col]), offsets)
            dy, dx = offsets[int(codes[row, col])]
            row, col = row + dy, col + dx
            self.assertTrue(0 <= row < 9 and 0 <= col < 9)
        self.assertIn("original DEM", result.metadata["elevation_source"])
        self.assertEqual(result.selected_x, self.point(path, 4, 8)[0])

    @unittest.skipUnless(hydro._grass_availability()["available"], "GRASS no instalado")
    def test_real_grass_flat_has_finite_acyclic_drainage_and_no_invented_tc(self):
        values = np.full((7, 7), 100.0)
        values[3, 6] = 99
        path = self.raster(values, resolution=5)
        result = self.analyze(path, 3, 6)
        self.assertGreater(result.cell_count, 10)
        self.assertEqual(result.values["relief_m"], 1)
        self.assertEqual(result.area_m2, result.cell_count * 25)
        self.assertTrue(result.touches_boundary)
        self.assertIn("dem_hydrology", result.diagnostics)
        self.assertIsNone(result.values["tc_h"])
        self.assertIsNone(result.values["tc_10_85_h"])


if __name__ == "__main__":
    unittest.main()
