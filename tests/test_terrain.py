"""Analytical synthetic checks of the original-grid basin/terrain engine."""

import importlib
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr


_SOURCE = Path(__file__).resolve().parents[1] / "src" / "caumax_consulta"
_PACKAGE = types.ModuleType("_caumax_terrain_under_test")
_PACKAGE.__path__ = [str(_SOURCE)]
sys.modules[_PACKAGE.__name__] = _PACKAGE
terrain = importlib.import_module(_PACKAGE.__name__ + ".terrain")


class TerrainTests(unittest.TestCase):
    GT = (100.0, 10.0, 0.0, 200.0, 0.0, -10.0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def raster(self, array, directions=False, transform=None, nodata=None):
        self.sequence += 1
        destination = self.directory / (str(self.sequence) + ".tif")
        array = np.asarray(array)
        dataset = gdal.GetDriverByName("GTiff").Create(
            str(destination), array.shape[1], array.shape[0], 1,
            gdal.GDT_Byte if directions else gdal.GDT_Float32,
        )
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25830)
        dataset.SetProjection(reference.ExportToWkt())
        dataset.SetGeoTransform(transform or self.GT)
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue((255 if directions else -9999) if nodata is None else nodata)
        band.WriteArray(array)
        band.FlushCache()
        dataset.FlushCache()
        dataset = None
        return destination

    def analyze(self, directions, dem, row=1, col=1, transform=None, **kwargs):
        gt = transform or self.GT
        paths = self.raster(directions, True, gt), self.raster(dem, False, gt)
        x, y = gdal.ApplyGeoTransform(gt, col + 0.5, row + 0.5)
        return terrain.analyze_basin(*paths, x, y, **kwargs)

    def test_planar_dem_has_analytical_riley_tri_and_d8_slope(self):
        directions = np.zeros((5, 5), dtype=np.uint8)
        yy, xx = np.indices((5, 5))
        dem = 100 + 2 * xx + 3 * yy
        result = self.analyze(directions, dem, row=2, col=2)
        # Sum of squared differences = 6*a² + 6*b² for an unscaled 3x3 plane.
        self.assertAlmostEqual(result.values["tri_m"], math.sqrt(6 * (2 ** 2 + 3 ** 2)))
        self.assertAlmostEqual(result.values["slope_d8_pct"], 100 * 5 / math.sqrt(200))
        self.assertEqual(result.diagnostics["tri_valid_cells"], 1)
        self.assertEqual(result.area_m2, 100)
        self.assertEqual(result.values["cell_count"], 1)
        self.assertEqual(result.values["cell_resolution"], (10.0, 10.0))
        self.assertEqual(result.selected_x, 125)
        self.assertEqual(result.outlet_x, 125)

    def test_chain_profile_10_85_is_measured_from_outlet(self):
        directions = np.zeros((3, 7), dtype=np.uint8)
        directions[1, 1:5] = 1
        yy, xx = np.indices((3, 7))
        dem = 200 - 2 * xx
        result = self.analyze(directions, dem, row=1, col=5)
        self.assertAlmostEqual(result.values["main_length_km"], 0.04)
        self.assertAlmostEqual(result.values["main_length_10_85_km"], 0.03)
        self.assertAlmostEqual(result.diagnostics["z10_m"], 190.8)
        self.assertAlmostEqual(result.diagnostics["z85_m"], 196.8)
        self.assertAlmostEqual(result.values["main_slope_10_85"], 0.2)
        self.assertAlmostEqual(result.values["tc_path_length_km"], 0.04)
        self.assertAlmostEqual(result.values["tc_path_slope"], 0.2)
        self.assertIsNone(result.values["tc_h"])
        self.assertAlmostEqual(result.diagnostics["tc_candidate_h"], 0.3 * 0.04 ** 0.76 * 0.2 ** -0.19)
        self.assertIsNone(result.values["tc_10_85_h"])
        self.assertAlmostEqual(result.diagnostics["tc_10_85_candidate_h"],
                               0.3 * 0.03 ** 0.76 * 0.2 ** -0.19)
        self.assertTrue(any("0,25" in note for note in result.notes))
        line = ogr.CreateGeometryFromWkb(result.main_course_wkb)
        self.assertAlmostEqual(line.Length(), 40)
        self.assertEqual(line.GetPoint(0)[:2], (155, 185))
        self.assertEqual(line.GetPoint(line.GetPointCount() - 1)[:2], (115, 185))

    def test_area_perimeter_and_mask_are_not_recomputed_or_duplicated(self):
        directions = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        with patch.object(terrain._b, "_visit_upstream", wraps=terrain._b._visit_upstream) as visit:
            result = self.analyze(directions, np.arange(9).reshape(3, 3) + 100)
        self.assertEqual(visit.call_count, 1)
        self.assertEqual(result.cell_count, 9)
        self.assertEqual(result.area_m2, 900)
        self.assertEqual(result.perimeter_m, 120)
        polygon = ogr.CreateGeometryFromWkb(result.geometry_wkb)
        self.assertEqual(polygon.GetArea(), result.area_m2)
        self.assertAlmostEqual(result.values["kc"], 120 / (2 * math.sqrt(math.pi * 900)))
        self.assertAlmostEqual(result.values["ic"], 4 * math.pi * 900 / 120 ** 2)

    def test_flat_dem_returns_zero_relief_and_undefined_hi_slope_and_tc(self):
        directions = np.zeros((3, 5), dtype=np.uint8)
        directions[1, 1:3] = 1
        result = self.analyze(directions, np.full((3, 5), 100), row=1, col=3)
        self.assertEqual(result.values["relief_m"], 0)
        self.assertEqual(result.values["melton"], 0)
        self.assertEqual(result.values["tri_m"], 0)
        self.assertEqual(result.values["slope_d8_pct"], 0)
        self.assertIsNone(result.values["hypsometric"])
        self.assertIsNone(result.values["main_slope_10_85"])
        self.assertIsNone(result.values["tc_h"])
        self.assertIsNone(result.values["tc_10_85_h"])

    def test_median_excludes_nodata_and_does_not_replace_arithmetic_mean_in_hi(self):
        directions = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        dem = [[0, 0, 0], [0, 0, 0], [0, 90, -9999]]
        result = self.analyze(directions, dem)
        self.assertEqual(result.values["zmedian_m"], 0)
        self.assertEqual(result.values["zmean_m"], 11.25)
        self.assertEqual(result.z_mean, 11.25)
        self.assertEqual(result.values["hypsometric"], 0.125)
        self.assertEqual(result.diagnostics["dem_valid_cells"], 8)

    def test_relief_melton_hi_and_elongation_have_coherent_units(self):
        directions = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        result = self.analyze(directions, np.arange(100, 109).reshape(3, 3))
        self.assertEqual(result.values["relief_m"], 8)
        self.assertAlmostEqual(result.values["melton"], 8 / math.sqrt(900))
        self.assertAlmostEqual(result.values["hypsometric"], 0.5)
        expected_length = math.hypot(15, 15)
        self.assertAlmostEqual(result.values["basin_length_km"], expected_length / 1000)
        self.assertAlmostEqual(result.values["elongation_ratio"],
                               2 * math.sqrt(900 / math.pi) / expected_length)

    def test_triangulation_uses_actual_halo_outside_basin(self):
        directions = np.zeros((5, 5), dtype=np.uint8)
        dem = np.zeros((5, 5))
        dem[2, 2] = 10
        result = self.analyze(directions, dem, row=2, col=2)
        self.assertAlmostEqual(result.values["tri_m"], math.sqrt(8 * 100))
        self.assertAlmostEqual(result.values["slope_d8_pct"], 100)

    def test_nodata_neighbour_is_excluded_without_interpolation(self):
        directions = np.zeros((5, 5), dtype=np.uint8)
        dem = np.full((5, 5), 100.0)
        dem[1, 1] = -9999
        result = self.analyze(directions, dem, row=2, col=2)
        self.assertIsNone(result.values["tri_m"])
        self.assertIsNone(result.values["slope_d8_pct"])
        self.assertEqual(result.diagnostics["tri_excluded_cells"], 1)
        self.assertEqual(result.diagnostics["dem_missing_cells"], 0)

    def test_unfilled_strict_pit_is_nodata_for_d8_slope_but_not_riley_tri(self):
        directions = np.zeros((5, 5), dtype=np.uint8)
        dem = np.full((5, 5), 10.0)
        dem[2, 2] = 0
        result = self.analyze(directions, dem, row=2, col=2)
        self.assertAlmostEqual(result.values["tri_m"], math.sqrt(8 * 100))
        self.assertIsNone(result.values["slope_d8_pct"])
        self.assertEqual(result.diagnostics["tri_valid_cells"], 1)
        self.assertEqual(result.diagnostics["slope_valid_cells"], 0)
        self.assertEqual(result.diagnostics["slope_pit_excluded_cells"], 1)
        self.assertFalse(result.diagnostics["taudem_pipeline_executed"])

    def test_nodata_profile_does_not_bridge_gap_or_compute_tc_through_it(self):
        directions = np.zeros((3, 7), dtype=np.uint8)
        directions[1, 1:5] = 1
        yy, xx = np.indices((3, 7))
        dem = (200 - 2 * xx).astype(float)
        dem[1, 3] = -9999
        result = self.analyze(directions, dem, row=1, col=5)
        self.assertIsNone(result.values["main_slope_10_85"])
        self.assertEqual(result.diagnostics["dem_missing_cells"], 1)
        self.assertEqual(result.diagnostics["tc_nodata_paths"], 3)
        self.assertAlmostEqual(result.values["tc_path_length_km"], 0.04)
        self.assertIsNone(result.diagnostics["tc_candidate_h"])
        self.assertIsNone(result.values["tc_path_slope"])
        self.assertIsNone(result.values["tc_10_85_h"])
        self.assertIsNone(result.diagnostics["tc_10_85_candidate_h"])
        self.assertTrue(any("NoData" in note for note in result.notes))

    def test_all_dem_nodata_does_not_invent_terrain_values(self):
        result = self.analyze([[1, 0]], [[-9999, -9999]], row=0, col=1)
        self.assertEqual(result.cell_count, 2)
        for name in ("zmin_m", "zmax_m", "zmean_m", "zmedian_m", "tri_m", "slope_d8_pct",
                     "relief_m", "hypsometric", "melton", "tc_h", "tc_10_85_h"):
            with self.subTest(name=name):
                self.assertIsNone(result.values[name])

    def test_reported_tc_uses_longest_course_even_when_other_branch_has_higher_tc(self):
        directions = np.zeros((6, 8), dtype=np.uint8)
        directions[4, 1:6] = 1
        directions[1:4, 6] = 4
        dem = np.full((6, 8), 100.0)
        for col in range(1, 6):
            dem[4, col] = 100 + (6 - col) * 100
        for row in range(1, 4):
            dem[row, 6] = 100 + (4 - row) * 0.1
        result = self.analyze(directions, dem, row=4, col=6)
        self.assertAlmostEqual(result.values["main_length_km"], 0.05)
        self.assertAlmostEqual(result.values["tc_path_length_km"], 0.05)
        self.assertAlmostEqual(result.values["tc_path_slope"], 10)
        self.assertAlmostEqual(result.diagnostics["tc_path_length_km"], 0.05)
        self.assertAlmostEqual(result.diagnostics["tc_path_slope"], 10)
        self.assertEqual(result.diagnostics["tc_valid_paths"], 8)
        expected = 0.3 * 0.05 ** 0.76 * 10 ** -0.19
        self.assertIsNone(result.values["tc_h"])
        self.assertAlmostEqual(result.diagnostics["tc_candidate_h"], expected)
        self.assertGreater(result.diagnostics["max_tc_all_paths_candidate_h"], expected)
        self.assertAlmostEqual(result.diagnostics["max_tc_all_paths_length_km"], 0.03)

    def test_long_valid_route_has_tc_above_quarter_hour(self):
        directions = np.zeros((3, 13), dtype=np.uint8)
        directions[1, 1:11] = 1
        _, xx = np.indices((3, 13))
        dem = 200 - xx
        transform = (100, 100, 0, 200, 0, -100)
        result = self.analyze(directions, dem, row=1, col=11, transform=transform)
        expected = 0.3 * 1 ** 0.76 * 0.01 ** -0.19
        self.assertAlmostEqual(result.values["tc_h"], expected)
        self.assertAlmostEqual(result.values["tc_path_length_km"], 1)
        self.assertAlmostEqual(result.values["tc_10_85_h"], 0.3 * 0.75 ** 0.76 * 0.01 ** -0.19)

    def test_tc_10_85_uses_its_own_profile_slope_and_length(self):
        directions = np.zeros((3, 13), dtype=np.uint8)
        directions[1, 1:11] = 1
        profile = np.array([100, 101, 102, 104, 107, 111, 116, 122, 129, 137, 146])
        dem = np.full((3, 13), 200.0)
        dem[1, 1:12] = profile[::-1]
        result = self.analyze(directions, dem, row=1, col=11,
                              transform=(100, 100, 0, 200, 0, -100))
        j_total = 46 / 1000
        j_segment = (133 - 101) / 750
        self.assertAlmostEqual(result.values["tc_path_slope"], j_total)
        self.assertAlmostEqual(result.values["main_slope_10_85"], j_segment)
        self.assertAlmostEqual(result.values["tc_h"], 0.3 * 1 ** 0.76 * j_total ** -0.19)
        self.assertAlmostEqual(result.values["tc_10_85_h"], 0.3 * 0.75 ** 0.76 * j_segment ** -0.19)
        self.assertAlmostEqual(result.diagnostics["tc_10_85_path_length_km"], 0.75)
        self.assertAlmostEqual(result.diagnostics["tc_10_85_path_slope"], j_segment)
        self.assertNotAlmostEqual(result.values["tc_10_85_h"],
                                  result.values["tc_h"] * 0.75 ** 0.76)

    def test_diagonal_path_uses_actual_rectangular_cell_distance_and_multipart(self):
        transform = (100, 10, 0, 200, 0, -20)
        result = self.analyze([[2, 0], [0, 0]], [[110, 100], [100, 100]],
                              row=1, col=1, transform=transform)
        expected = math.hypot(10, 20)
        self.assertAlmostEqual(result.values["main_length_km"], expected / 1000)
        geometry = ogr.CreateGeometryFromWkb(result.geometry_wkb)
        self.assertEqual(geometry.GetGeometryCount(), 2)
        self.assertEqual(result.area_m2, 400)
        self.assertEqual(result.values["cell_resolution"], (10.0, 20.0))

    def test_compact_grid_has_runs_and_all_original_cell_edges(self):
        result = self.analyze([[2, 4, 8], [1, 0, 16], [128, 64, 32]], np.ones((3, 3)))
        geometry = ogr.CreateGeometryFromWkb(result.grid_wkb)
        self.assertEqual(geometry.GetGeometryName(), "MULTILINESTRING")
        self.assertEqual(geometry.GetGeometryCount(), 8)
        self.assertEqual(geometry.Length(), 240)

    def test_cycle_and_invalid_closure_are_rejected(self):
        for directions, message in (([[1, 16]], "ciclo"), ([[255]], "NoData"),
                                    ([[3]], "desconocido")):
            with self.subTest(directions=directions), self.assertRaisesRegex(ValueError, message):
                self.analyze(directions, np.ones(np.asarray(directions).shape), row=0, col=0)

    def test_cancel_before_datasets_open(self):
        with self.assertRaises(InterruptedError):
            terrain.analyze_basin("unused", "unused", 1, 1, cancelled=lambda: True)

    def test_actual_caumax_basin_keeps_verified_cell_area(self):
        data = (Path(os.environ.get("APPDATA", "")) / "QGIS" / "QGIS3" /
                "profiles" / "default" / "python" / "plugins" / "caumax" / "data")
        if not (data / "dir.tif").is_file() or not (data / "mdt.tif").is_file():
            self.skipTest("CAUMAX real no está instalado en este equipo")
        result = terrain.analyze_basin(data / "dir.tif", data / "mdt.tif", 341825, 4646700)
        self.assertEqual(result.cell_count, 720)
        self.assertEqual(result.area_m2, 180000000)
        self.assertEqual(ogr.CreateGeometryFromWkb(result.geometry_wkb).GetArea(), result.area_m2)
        self.assertGreater(result.values["main_length_km"], 0)
        self.assertEqual(result.diagnostics["dem_valid_cells"], result.cell_count)
        print("REAL_TERRAIN", result.values, result.diagnostics)


if __name__ == "__main__":
    unittest.main()
