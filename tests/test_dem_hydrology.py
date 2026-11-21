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

    def test_snap_chooses_nearest_main_channel_not_largest_accumulation(self):
        counts = np.zeros((3, 5), dtype=np.uint32)
        counts[1, 2], counts[1, 1], counts[1, 4] = 1, 90, 1000
        effective, snap = hydro._nearest_main_channel(
            counts, (100, 10, 0, 200, 0, -10), 125, 185, 10000, .9, 50)
        self.assertEqual((effective.row, effective.col), (1, 1))
        self.assertEqual((snap["snapped_x"], snap["snapped_y"]), (115, 185))
        self.assertEqual(snap["distance_m"], 10)
        self.assertEqual(snap["candidate_upstream_area_m2"], 9000)
        self.assertEqual(snap["candidate_count"], 2)
        self.assertEqual(snap["status"], "snapped")

    def test_snap_equal_distance_uses_row_column_not_larger_area(self):
        counts = np.zeros((3, 5), dtype=np.uint32)
        counts[0, 2], counts[2, 2], counts[1, 2] = 90, 1000, 1
        effective, snap = hydro._nearest_main_channel(
            counts, (100, 10, 0, 200, 0, -10), 125, 185, 10000, .9, 50)
        self.assertEqual((effective.row, effective.col), (0, 2))
        self.assertEqual(snap["tie_break"], "row_col")
        self.assertEqual(snap["candidate_upstream_area_m2"], 9000)

    def test_snap_no_candidate_or_outside_radius_never_uses_global_fallback(self):
        counts = np.zeros((3, 5), dtype=np.uint32)
        counts[1, 1], counts[1, 2] = 1000, 1
        original = counts.copy()
        with self.assertRaisesRegex(ValueError, "No se encuentra"):
            hydro._nearest_main_channel(counts, (100, 10, 0, 200, 0, -10),
                                         125, 185, 10000, .9, 9)
        np.testing.assert_array_equal(counts, original)

    def test_snap_rejects_original_direction_nodata_even_if_channel_nearby(self):
        counts = np.full((3, 5), 1000, dtype=np.uint32)
        counts[1, 2] = 0
        with self.assertRaisesRegex(ValueError, "original.*D8"):
            hydro._nearest_main_channel(counts, (100, 10, 0, 200, 0, -10),
                                         125, 185, 10000, .9, 50)

    def test_snap_unchanged_means_same_cell_and_keeps_actual_click_distance(self):
        counts = np.full((3, 5), 100, dtype=np.uint32)
        effective, snap = hydro._nearest_main_channel(
            counts, (100, 10, 0, 200, 0, -10), 125.25, 185.5, 10000, .9, 50)
        self.assertEqual((effective.row, effective.col), (1, 2))
        self.assertEqual(snap["status"], "unchanged")
        self.assertAlmostEqual(snap["distance_m"], math.hypot(.25, .5))
        self.assertEqual((snap["original_x"], snap["original_y"]), (125.25, 185.5))

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

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
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

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
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

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
    def test_snapped_analysis_retains_original_point_and_reuses_accumulation(self):
        row, col = np.indices((7, 9))
        path = self.raster(200 - col * 2 + np.abs(row - 3) * 4, resolution=2)
        x, y = self.point(path, 2, 5)
        progress = []
        result = self.analyze(path, 2, 5, reference_area_m2=80,
                              reference_cell_width=500, reference_cell_height=500,
                              snap_enabled=True, progress=progress.append)
        snap = result.diagnostics["outlet_snap"]
        self.assertEqual((result.selected_x, result.selected_y), (x, y))
        self.assertEqual((result.outlet_x, result.outlet_y), (snap["snapped_x"], snap["snapped_y"]))
        self.assertEqual(snap["candidate_upstream_area_m2"], result.area_m2)
        self.assertGreaterEqual(result.area_m2, 72)
        self.assertEqual(snap["method"], "nearest_main_channel_d8")
        self.assertEqual(snap["status"], "snapped")
        self.assertEqual(snap["candidate_touches_boundary"], result.touches_boundary)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)
        self.assertTrue(Path(result.metadata["accumulation_path"]).is_file())
        with patch.object(hydro, "_flow_accumulation", side_effect=AssertionError("Accumulation must be cached")):
            again = self.analyze(path, 2, 5, reference_area_m2=80,
                                  reference_cell_width=500, reference_cell_height=500, snap_enabled=True)
        self.assertTrue(again.metadata["accumulation_cache_reused"])
        self.assertEqual(again.diagnostics["outlet_snap"], snap)

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
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

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
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

    @unittest.skipUnless(hydro.availability_engine()["available"], "GRASS no instalado")
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
