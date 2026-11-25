"""Native D8 correspondence at 5/25 m, including ambiguous and unsafe sections."""
import importlib
import math
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr

import test_dem_hydrology as fixtures

hydro = fixtures.hydro
refinement = importlib.import_module(hydro.__package__ + '.refinement_snap')


class RefinementSnapTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DemHydrologyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def valley(self, resolution=5, downstream_bend=False):
        codes = np.full((34, 40), 255, dtype=np.uint8)
        codes[1:21, 2:10] = 1
        codes[1:21, 10] = 4
        codes[1:21, 11:18] = 16
        codes[21, 10] = 0
        codes[1:31, 20:30] = 1
        codes[1:31, 30] = 4
        codes[1:31, 31:39] = 16
        codes[31, 30] = 0
        codes[17:19, 12:30] = 1
        codes[17, 12] = 4  # The clicked cell below has only two contributors.
        if downstream_bend:
            codes[19, 10:30] = 1  # A nearer segment south of the original section.
        dem, directions = self.fixture.prepared_grid(codes, resolution)
        dataset = gdal.Open(str(directions))
        transform = dataset.GetGeoTransform()
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for col, row in ((2, 1), (18, 1), (18, 21), (2, 21), (2, 1)):
            ring.AddPoint_2D(*gdal.ApplyGeoTransform(transform, col, row))
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        line = ogr.Geometry(ogr.wkbLineString)
        for row in (20, 1):
            line.AddPoint_2D(*self.fixture.point(dem, row, 10))
        reference = dict(geometry_wkb=bytes(polygon.ExportToWkb()), main_course_wkb=bytes(line.ExportToWkb()),
                         crs_wkt=dataset.GetProjectionRef(), cell_width=500, cell_height=500)
        counts = hydro._flow_accumulation(dataset.GetRasterBand(1))
        dataset = None
        return dem, directions, reference, counts

    def analyze(self, dem, reference, click):
        return hydro.analyze_dem(dem, *click, self.fixture.cache,
            reference_area_m2=54e6, reference_cell_width=500, reference_cell_height=500,
            refinement_reference=reference)

    def test_five_and_twenty_five_metre_refinement_reject_tiny_bank_and_larger_neighbour(self):
        for resolution in (5, 25):
            with self.subTest(resolution=resolution):
                dem, directions, reference, counts = self.valley(resolution)
                original = self.fixture.point(dem, 18, 12)
                click = original[0] + .3 * resolution, original[1] - .2 * resolution
                before = directions.read_bytes(), dem.read_bytes()
                result = self.analyze(dem, reference, click)
                snap = result.diagnostics['outlet_snap']
                self.assertEqual(int(counts[18, 12]), 2)
                self.assertGreater(int(counts[30, 30]), int(counts[18, 10]))
                self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 18, 10)))
                self.assertEqual((result.selected_x, result.selected_y), click)
                self.assertEqual(result.cell_count, 276)
                self.assertEqual(snap['intersection_area_m2'], result.area_m2)
                self.assertEqual(snap['reference_raster_area_m2'], 320 * resolution ** 2)
                self.assertEqual(snap['reference_area_m2'], 54e6)  # Historical area is not the matching denominator.
                self.assertGreater(snap['reference_coverage_fraction'], .5)
                self.assertEqual(snap['candidate_overlap_fraction'], 1)
                self.assertEqual(snap['projection_kind'], 'orthogonal')
                self.assertGreaterEqual(snap['coarse_upstream_distance_m'], 0)
                self.assertEqual(ogr.CreateGeometryFromWkb(result.main_course_wkb).GetPoint_2D(0),
                                 (result.outlet_x, result.outlet_y))
                self.assertEqual(before, (directions.read_bytes(), dem.read_bytes()))
                with patch.object(hydro, '_run_grass', side_effect=AssertionError('D8 must remain cached')):
                    again = self.analyze(dem, reference, click)
                self.assertTrue(again.metadata['accumulation_cache_reused'])
                self.assertEqual(again.diagnostics['outlet_snap'], snap)

    def test_click_already_on_matching_channel_keeps_its_containing_centre(self):
        dem, _, reference, _ = self.valley()
        centre = tuple(self.fixture.point(dem, 18, 10))
        click = centre[0] + 2.4, centre[1] - 2.4
        result = self.analyze(dem, reference, click)
        self.assertEqual((result.outlet_x, result.outlet_y), centre)
        snap = result.diagnostics['outlet_snap']
        self.assertEqual(snap['status'], 'unchanged')
        self.assertEqual(snap['projection_kind'], 'containing_cell')
        self.assertFalse(result.metadata['outlet_snapped_to_channel'])

    def nearest_reference(self, reference):
        return dict(reference, selection_policy=refinement.NEAREST_CHAIN_SELECTION_POLICY)

    def assert_nearest_proof(self, result, directions, reference, counts, click):
        """Enumerate actual upstream masks, independently of the chain chooser."""
        dataset = gdal.Open(str(directions))
        try:
            transform = dataset.GetGeoTransform()
            geometry = ogr.CreateGeometryFromWkb(reference['geometry_wkb'])
            reference_mask, reference_count, _ = refinement._reference_mask(dataset, geometry, lambda: False)
            choices = []
            for row, col in np.argwhere(counts > reference_count / 2):
                row, col = int(row), int(col)
                centre = gdal.ApplyGeoTransform(transform, col + .5, row + .5)
                distance = math.dist(centre, click)
                if distance > math.hypot(500, 500):
                    continue
                mask, window, count, _ = hydro._b._visit_upstream(
                    dataset.GetRasterBand(1), row, col, lambda: False, lambda n: None)
                left, top, width, height = window
                overlap = int(np.count_nonzero(mask & reference_mask[top:top + height, left:left + width]))
                if 2 * overlap > reference_count and 2 * overlap > count:
                    choices.append((distance, count, row, col, overlap))
        finally:
            dataset = None
        snap = result.diagnostics['outlet_snap']
        epsilon = max(result.cell_width, result.cell_height) * 1e-7
        minimum = min(item[0] for item in choices)
        chosen = min((item for item in choices if item[0] <= minimum + epsilon), key=lambda item: item[1:4])
        self.assertEqual((snap['snapped_row'], snap['snapped_col']), chosen[2:4])
        self.assertEqual(snap['candidate_count'], len(choices))
        self.assertEqual(snap['reference_policy_version'], 2)
        self.assertEqual(snap['projection_kind'], 'nearest_chain_cell')
        self.assertEqual(snap['tie_break'], refinement.NEAREST_CHAIN_SELECTION_POLICY)
        proof = snap['candidate_chain_cells']
        admissible = {tuple(item[:2]) for item in proof
                      if 2 * item[4] > reference_count and 2 * item[4] > item[3]}
        self.assertEqual(admissible, {item[2:4] for item in choices})
        self.assertEqual(proof[0][3], min(item[1] for item in choices))
        self.assertEqual(proof[-1][3], max(item[1] for item in choices))
        offsets = {code: (-dy, -dx) for dx, dy, code in hydro._b.DIRECTIONS}
        for first, second in zip(proof, proof[1:]):
            dr, dc = offsets[first[2]]
            self.assertEqual((first[0] + dr, first[1] + dc), tuple(second[:2]))
            self.assertLess(first[3], second[3])
            self.assertLessEqual(first[4], second[4])
        self.assertFalse(any(key.startswith('section_') for key in snap))
        self.assertNotIn('coarse_upstream_distance_m', snap)
        return snap

    def test_rational_nearest_qualified_chain_rejects_the_closer_larger_neighbour_at_5_and_25m(self):
        for resolution in (5, 25):
            with self.subTest(resolution=resolution):
                dem, directions, reference, counts = self.valley(resolution)
                reference = self.nearest_reference(reference)
                # The larger river at col30 is closer, but its footprint fails
                # the reference identity. A maximum-accumulation snap is wrong.
                click = tuple(self.fixture.point(dem, 18, 27))
                before = directions.read_bytes(), dem.read_bytes()
                result = self.analyze(dem, reference, click)
                self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 18, 10)))
                self.assertGreater(int(counts[18, 30]), result.cell_count)
                self.assert_nearest_proof(result, directions, reference, counts, click)
                self.assertEqual(before, (directions.read_bytes(), dem.read_bytes()))

    def test_rational_nearest_chain_does_not_impose_the_coarse_course_axis(self):
        for resolution in (5, 25):
            with self.subTest(resolution=resolution):
                dem, directions, reference, counts = self.valley(resolution)
                line = ogr.Geometry(ogr.wkbLineString)
                for col in (10, 17):
                    line.AddPoint_2D(*self.fixture.point(dem, 20, col))
                reference['main_course_wkb'] = bytes(line.ExportToWkb())
                click = tuple(self.fixture.point(dem, 18, 12))
                with self.assertRaises(hydro.OutletSelectionRequired) as caught:
                    self.analyze(dem, reference, click)
                self.assertEqual(caught.exception.selection_context['reason_code'], 'no_reference_section')
                reference = self.nearest_reference(reference)
                result = self.analyze(dem, reference, click)
                self.assert_nearest_proof(result, directions, reference, counts, click)
                self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 18, 10)))

    def test_rational_nearest_distance_is_not_rounded_by_the_branch_tolerance(self):
        dem, directions, reference, counts = self.valley()
        reference = self.nearest_reference(reference)
        x, y = self.fixture.point(dem, 18, 12)
        click = x, y - .501 * 5
        result = self.analyze(dem, reference, click)
        # The downstream centre is only slightly nearer. It must win despite
        # the much wider 5%-of-pixel tolerance used by the old section policy.
        self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 19, 10)))
        snap = self.assert_nearest_proof(result, directions, reference, counts, click)
        self.assertLess(snap['branch_runner_up_distance_m'] - snap['branch_distance_m'],
                        hydro._b._branch_ambiguity_tolerance(5, 5))
        self.assertGreater(snap['branch_runner_up_distance_m'] - snap['branch_distance_m'],
                           snap['branch_ambiguity_tolerance_m'])

    def test_rational_exact_distance_tie_chooses_upstream_on_the_same_chain(self):
        dem, directions, reference, counts = self.valley()
        reference = self.nearest_reference(reference)
        x, y = self.fixture.point(dem, 18, 12)
        click = x, y - .5 * 5
        result = self.analyze(dem, reference, click)
        self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 18, 10)))
        snap = self.assert_nearest_proof(result, directions, reference, counts, click)
        self.assertEqual(snap['branch_runner_up_distance_m'], snap['branch_distance_m'])

    def test_rational_containing_cell_still_records_the_complete_verified_chain_without_a_coarse_course(self):
        dem, directions, reference, counts = self.valley()
        reference = self.nearest_reference(dict(reference, main_course_wkb=b''))
        click = tuple(self.fixture.point(dem, 18, 10))
        result = self.analyze(dem, reference, click)
        snap = self.assert_nearest_proof(result, directions, reference, counts, click)
        self.assertEqual(snap['status'], 'unchanged')
        self.assertGreater(len(snap['candidate_chain_cells']), 1)

    def test_rational_nearest_policy_keeps_strict_bidirectional_overlap_and_rejects_unknown_modes(self):
        dem, directions, reference, counts = self.valley()
        original_reference = dict(reference)
        reference = self.nearest_reference(reference)
        dataset = gdal.Open(str(directions))
        try:
            mask, window, count, _ = hydro._b._visit_upstream(
                dataset.GetRasterBand(1), 21, 10, lambda: False, lambda n: None)
            full = np.zeros(counts.shape, dtype=np.uint8)
            left, top, width, height = window
            full[top:top + height, left:left + width] = mask
            both = full | np.roll(full, 20, axis=1)
            reference['geometry_wkb'], _ = hydro._b._polygonize(
                both, dataset.GetGeoTransform(), hydro._b._spatial_reference(dataset, 'direcciones'), lambda: False)
            self.assertEqual(int(np.count_nonzero(both)), 2 * count)
        finally:
            dataset = None
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(dem, reference, tuple(self.fixture.point(dem, 18, 12)))
        self.assertEqual(caught.exception.selection_context['reason_code'], 'no_reference_match')
        original_reference['selection_policy'] = 'unrecognised-nearest-policy'
        with self.assertRaisesRegex(ValueError, 'política'):
            self.analyze(dem, original_reference, tuple(self.fixture.point(dem, 18, 12)))

    def test_coarse_course_vetoes_a_nearer_downstream_bend_before_confluence(self):
        dem, _, reference, _ = self.valley(downstream_bend=True)
        click = tuple(self.fixture.point(dem, 18, 12))
        result = self.analyze(dem, reference, click)
        self.assertEqual((result.outlet_x, result.outlet_y), tuple(self.fixture.point(dem, 18, 10)))
        self.assertGreaterEqual(result.diagnostics['outlet_snap']['coarse_upstream_distance_m'], 0)

    def test_missing_reference_support_and_two_cell_drainage_require_manual_choice(self):
        _, _, reference, _ = self.valley()
        codes = np.full((34, 40), 255, dtype=np.uint8)
        codes[17, 12], codes[18, 12] = 4, 0
        dem, _ = self.fixture.prepared_grid(codes)
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(dem, reference, tuple(self.fixture.point(dem, 18, 12)))
        context = caught.exception.selection_context
        self.assertEqual(context['reason_code'], 'no_reference_match')
        self.assertEqual(context['dem_path'], str(dem))
        self.assertTrue(context['directions_path'])

    def test_reference_outside_dem_is_not_silently_clipped_to_a_tiny_denominator(self):
        dem, _, reference, _ = self.valley()
        polygon = ogr.CreateGeometryFromWkb(reference['geometry_wkb']).Buffer(1000)
        reference['geometry_wkb'] = bytes(polygon.ExportToWkb())
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(dem, reference, tuple(self.fixture.point(dem, 18, 12)))
        self.assertEqual(caught.exception.selection_context['reason_code'], 'reference_outside_dem')

    def test_equal_half_reference_in_two_disjoint_rivers_requires_manual_choice(self):
        dem, directions, reference, _ = self.valley()
        dataset = gdal.Open(str(directions))
        try:
            mask, window, count, _ = hydro._b._visit_upstream(
                dataset.GetRasterBand(1), 21, 10, lambda: False, lambda n: None)
            full = np.zeros((dataset.RasterYSize, dataset.RasterXSize), dtype=np.uint8)
            left, top, width, height = window
            full[top:top + height, left:left + width] = mask
            both = full | np.roll(full, 20, axis=1)
            reference['geometry_wkb'], _ = hydro._b._polygonize(
                both, dataset.GetGeoTransform(), hydro._b._spatial_reference(dataset, 'direcciones'), lambda: False)
            self.assertEqual(int(np.count_nonzero(both)), 2 * count)
        finally:
            dataset = None
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(dem, reference, tuple(self.fixture.point(dem, 18, 12)))
        self.assertEqual(caught.exception.selection_context['reason_code'], 'no_reference_match')

    def test_equally_near_sections_of_a_matching_meander_require_manual_choice(self):
        codes = np.full((25, 25), 255, dtype=np.uint8)
        codes[1:10, 5:10], codes[1:10, 10], codes[1:10, 11:15] = 1, 4, 16
        codes[10:15, 10] = 4
        codes[15, 10:14] = 1
        codes[11:16, 14] = 64
        codes[10, 14:18], codes[10, 18] = 1, 0
        codes[12, 12:14] = 1
        dem, directions = self.fixture.prepared_grid(codes)
        original = tuple(self.fixture.point(dem, 12, 12))
        reference_basin = hydro.analyze_basin(directions, dem, *self.fixture.point(dem, 10, 18))
        # A northward coarse reach does not distinguish the two sides of this
        # sub-cell meander. Both native sections have the same distance.
        line = ogr.Geometry(ogr.wkbLineString)
        for row in (10, 1):
            line.AddPoint_2D(*self.fixture.point(dem, row, 18))
        reference = dict(geometry_wkb=reference_basin.geometry_wkb,
            main_course_wkb=bytes(line.ExportToWkb()), crs_wkt=reference_basin.crs_wkt,
            cell_width=500, cell_height=500)
        with self.assertRaises(hydro.OutletSelectionRequired) as caught:
            self.analyze(dem, reference, original)
        self.assertEqual(caught.exception.selection_context['reason_code'], 'ambiguous_reference_section')

    def test_binary_weighted_accumulation_equals_explicit_native_intersections(self):
        _, directions, reference, counts = self.valley()
        dataset = gdal.Open(str(directions))
        try:
            geometry = ogr.CreateGeometryFromWkb(reference['geometry_wkb'])
            mask, total, _ = refinement._reference_mask(dataset, geometry, lambda: False)
            weighted = hydro._flow_accumulation(dataset.GetRasterBand(1), weights=mask)
            self.assertEqual(total, 320)
            for row, col in ((18, 12), (18, 10), (30, 30)):
                upstream, window, count, _ = hydro._b._visit_upstream(
                    dataset.GetRasterBand(1), row, col, lambda: False, lambda n: None)
                left, top, width, height = window
                expected = int(np.count_nonzero(upstream & mask[top:top + height, left:left + width]))
                self.assertEqual(int(weighted[row, col]), expected)
                self.assertEqual(int(counts[row, col]), count)
        finally:
            dataset = None

    def test_cancel_and_memory_limit_precede_extra_raster_work(self):
        dem, directions, reference, counts = self.valley()
        click = self.fixture.point(dem, 18, 12)
        with self.assertRaises(InterruptedError):
            refinement.select_reference_outlet(directions, counts, *click, 500, reference, lambda: True)
        with patch.object(hydro, '_require_accumulation_memory', side_effect=ValueError('memoria')):
            with self.assertRaisesRegex(ValueError, 'memoria'):
                refinement.select_reference_outlet(directions, counts, *click, 500, reference)


if __name__ == '__main__':
    unittest.main()
