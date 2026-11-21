"""Hypsometric samples preserve population statistics and curve orientation."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from osgeo import gdal, osr


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from caumax_consulta.hypsometry_data import build_hypsometric_curve
from caumax_consulta.terrain import analyze_basin


class HypsometryDataTests(unittest.TestCase):
    def test_curve_runs_from_highest_to_lowest_as_accumulated_area_increases(self):
        result = build_hypsometric_curve([10, 20, 50, 100], 2.4)
        fraction = np.asarray(result["fraction"])
        height = np.asarray(result["elevation_m"])
        self.assertEqual(result["sample_count"], 201)
        self.assertEqual(result["valid_count"], 4)
        self.assertEqual(len(height), 201)
        self.assertEqual((fraction[0], fraction[-1]), (0, 1))
        self.assertEqual((height[0], height[-1]), (100, 10))
        self.assertTrue(np.all(np.diff(fraction) > 0))
        self.assertTrue(np.all(np.diff(height) <= 0))
        self.assertEqual((fraction * result["area_km2"])[-1], 2.4)
        self.assertEqual(result["population"], "valid_cells")
        json.dumps(result, allow_nan=False)

    def test_exact_hi_uses_all_valid_cells_not_the_median_or_sampled_auc(self):
        elevations = np.array([0, 0, 0, 0, 0, 0, 0, 90], dtype=float)
        before = elevations.copy()
        result = build_hypsometric_curve(elevations, .8)
        self.assertEqual(np.median(elevations), 0)
        self.assertEqual(result["hi_exact"], .125)
        self.assertAlmostEqual(result["auc_approx"], 1 / 14, delta=.0001)
        self.assertGreater(abs(result["hi_exact"] - result["auc_approx"]), .04)
        np.testing.assert_array_equal(elevations, before)

    def test_analysis_hi_can_be_preserved_exactly_without_using_sampled_auc(self):
        result = build_hypsometric_curve([0, 0, 0, 100], 1, exact_hi=.2500000000001)
        self.assertEqual(result["hi_exact"], .2500000000001)

    def test_missing_elevations_are_excluded_without_changing_basin_area(self):
        result = build_hypsometric_curve([10, np.nan, 50, np.inf, -np.inf], .75)
        self.assertEqual(result["valid_count"], 2)
        self.assertEqual(result["area_km2"], .75)
        self.assertEqual(result["zmin_m"], 10)
        self.assertEqual(result["zmax_m"], 50)
        self.assertEqual(result["hi_exact"], .5)
        self.assertTrue(np.isfinite(result["elevation_m"]).all())
        json.dumps(result, allow_nan=False)

    def test_flat_basin_has_dimensional_curve_but_undefined_hi_and_normalized_auc(self):
        for elevations in ([74], [74, 74, 74]):
            with self.subTest(elevations=elevations):
                result = build_hypsometric_curve(elevations, 1, exact_hi=.5)
                self.assertEqual(result["elevation_m"], [74] * 201)
                self.assertEqual(result["zmin_m"], result["zmax_m"])
                self.assertIsNone(result["hi_exact"])
                self.assertIsNone(result["auc_approx"])
                json.dumps(result, allow_nan=False)

    def test_no_valid_cotas_returns_explicit_empty_curve_not_zero_hi(self):
        for elevations in ([], [np.nan, np.inf, -np.inf]):
            with self.subTest(elevations=elevations):
                result = build_hypsometric_curve(elevations, 1, exact_hi=.5)
                self.assertEqual(result["valid_count"], 0)
                self.assertEqual(result["sample_count"], 0)
                self.assertEqual(result["fraction"], [])
                self.assertEqual(result["elevation_m"], [])
                self.assertIsNone(result["hi_exact"])
                self.assertIsNone(result["auc_approx"])
                self.assertIsNone(result["zmin_m"])
                self.assertIsNone(result["zmax_m"])
                json.dumps(result, allow_nan=False)

    def test_terrain_diagnostics_reuse_valid_dem_population_and_scalar_hi(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            reference = osr.SpatialReference()
            reference.ImportFromEPSG(25830)
            for name, array, kind in (
                ("directions", [[2, 4, 8], [1, 0, 16], [128, 64, 32]], gdal.GDT_Byte),
                ("dem", [[0, 0, 0], [0, 0, 0], [0, 90, -9999]], gdal.GDT_Float32),
            ):
                dataset = gdal.GetDriverByName("GTiff").Create(str(folder / (name + ".tif")), 3, 3, 1, kind)
                dataset.SetProjection(reference.ExportToWkt())
                dataset.SetGeoTransform((100, 10, 0, 200, 0, -10))
                band = dataset.GetRasterBand(1)
                band.SetNoDataValue(255 if name == "directions" else -9999)
                band.WriteArray(np.asarray(array))
                dataset = None
            result = analyze_basin(folder / "directions.tif", folder / "dem.tif", 115, 185)
            curve = result.diagnostics["hypsometric_curve"]
            self.assertEqual(curve["valid_count"], 8)
            self.assertEqual(curve["area_km2"], .0009)
            self.assertEqual(curve["hi_exact"], result.values["hypsometric"])
            self.assertEqual(curve["hi_exact"], .125)
            self.assertEqual(curve["zmin_m"], 0)
            self.assertEqual(curve["zmax_m"], 90)
            self.assertEqual(result.values["zmedian_m"], 0)
            self.assertEqual(result.diagnostics["dem_missing_cells"], 1)
            json.dumps(curve, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
