"""Analytical area-weighted climate means; no QGIS or project access."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from osgeo import gdal, ogr, osr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from caumax_consulta import basin_hydrology as hydro


class BasinHydrologyTests(unittest.TestCase):
    GT = (0, 500, 0, 1000, 0, -500)
    KEYS = {"source_path", "mean_mm", "basin_area_m2", "covered_area_m2", "valid_area_m2",
            "coverage_fraction", "valid_fraction", "pixel_count", "status", "notes"}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="basin_hydrology_")
        self.folder = Path(self.temp.name)
        self.sequence = 0
        self.reference = osr.SpatialReference()
        self.reference.ImportFromEPSG(25830)
        self.reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    def tearDown(self):
        self.temp.cleanup()

    def raster(self, values, *, transform=None, epsg=25830, nodata=-9999,
               mask=None, scale=None, offset=None):
        self.sequence += 1
        path = self.folder / (str(self.sequence) + ".tif")
        values = np.asarray(values, dtype=np.float64)
        dataset = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[1], values.shape[0], 1, gdal.GDT_Float64)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(epsg)
        dataset.SetProjection(reference.ExportToWkt())
        dataset.SetGeoTransform(transform or self.GT)
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.SetUnitType("mm")
        if scale is not None:
            band.SetScale(scale)
        if offset is not None:
            band.SetOffset(offset)
        band.WriteArray(values)
        if mask is not None:
            band.CreateMaskBand(gdal.GMF_PER_DATASET)
            band.GetMaskBand().WriteArray(np.asarray(mask, dtype=np.uint8))
        dataset.FlushCache()
        dataset = None
        return path

    def analysis(self, geometry, reference=None):
        if isinstance(geometry, str):
            geometry = ogr.CreateGeometryFromWkt(geometry)
        return SimpleNamespace(geometry_wkb=bytes(geometry.ExportToWkb()),
                               crs_wkt=(reference or self.reference).ExportToWkt(),
                               area_m2=geometry.GetArea())

    def rectangle(self, xmin, ymin, xmax, ymax):
        return self.analysis(f"POLYGON(({xmin} {ymin},{xmax} {ymin},{xmax} {ymax},"
                             f"{xmin} {ymax},{xmin} {ymin}))")

    def test_exact_partial_pixel_weights_not_centre_or_equal_pixel_average(self):
        path = self.raster([[10, 30]])
        result = hydro.basin_raster_mean(self.rectangle(0, 500, 750, 1000), path)
        self.assertEqual(set(result), self.KEYS)
        self.assertAlmostEqual(result["mean_mm"], (10 * 250000 + 30 * 125000) / 375000)
        self.assertEqual(result["basin_area_m2"], 375000)
        self.assertEqual(result["covered_area_m2"], 375000)
        self.assertEqual(result["valid_area_m2"], 375000)
        self.assertEqual(result["pixel_count"], 2)
        self.assertEqual(result["status"], "ok")

    def test_multipart_hole_and_zero_value_preserve_exact_valid_area(self):
        analysis = self.analysis("MULTIPOLYGON(((0 500,500 500,500 1000,0 1000,0 500),"
                                 "(100 600,100 700,200 700,200 600,100 600)),"
                                 "((750 0,1000 0,1000 250,750 250,750 0)))")
        result = hydro.basin_raster_mean(analysis, self.raster([[0, 10], [20, 30]]))
        self.assertEqual(result["pixel_count"], 2)
        self.assertEqual(result["valid_area_m2"], 302500)
        self.assertAlmostEqual(result["mean_mm"], 30 * 62500 / 302500)
        self.assertEqual(result["valid_fraction"], 1)

    def test_nodata_nan_negative_and_mask_excluded_zero_remains_valid(self):
        path = self.raster([[0, -9999, -2, np.nan, 40]], mask=[[255, 255, 255, 255, 0]])
        result = hydro.basin_raster_mean(self.rectangle(0, 500, 2500, 1000), path)
        self.assertEqual(result["mean_mm"], 0)
        self.assertEqual(result["pixel_count"], 5)
        self.assertEqual(result["coverage_fraction"], 1)
        self.assertEqual(result["valid_fraction"], .2)
        self.assertEqual(result["valid_area_m2"], 250000)
        self.assertEqual(result["status"], "partial")

    def test_scale_offset_applies_before_physical_negative_filter(self):
        analysis = self.rectangle(0, 500, 1500, 1000)
        result = hydro.basin_raster_mean(analysis, self.raster([[0, 1, -1]], scale=2, offset=10))
        self.assertEqual(result["mean_mm"], 10)
        self.assertEqual(result["status"], "ok")
        physical = hydro.basin_raster_mean(analysis, self.raster([[0, 1, -1]], scale=2, offset=0))
        self.assertEqual(physical["mean_mm"], 1)
        self.assertAlmostEqual(physical["valid_fraction"], 2 / 3)
        self.assertEqual(physical["status"], "partial")

    def test_partial_spatial_coverage_and_all_nodata_are_distinct(self):
        analysis = self.rectangle(0, 500, 1000, 1000)
        partial = hydro.basin_raster_mean(analysis, self.raster([[7]]))
        self.assertEqual(partial["mean_mm"], 7)
        self.assertEqual(partial["coverage_fraction"], .5)
        self.assertEqual(partial["valid_fraction"], .5)
        self.assertEqual(partial["status"], "partial")
        absent = hydro.basin_raster_mean(analysis, self.raster([[-9999, -9999]]))
        self.assertIsNone(absent["mean_mm"])
        self.assertEqual(absent["pixel_count"], 2)
        self.assertEqual(absent["covered_area_m2"], 500000)
        self.assertEqual(absent["valid_area_m2"], 0)
        self.assertEqual(absent["status"], "unavailable")

    def test_rotated_affine_grid_uses_true_pixel_quadrilaterals(self):
        transform = (0, 500, 100, 1000, 50, -500)
        points = [gdal.ApplyGeoTransform(transform, col, row) for col, row in ((0, 0), (1.5, 0), (1.5, 1), (0, 1), (0, 0))]
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for point in points:
            ring.AddPoint_2D(*point)
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        result = hydro.basin_raster_mean(self.analysis(polygon), self.raster([[10, 20]], transform=transform))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["basin_area_m2"], 382500)
        self.assertAlmostEqual(result["mean_mm"], 40 / 3)

    def test_geographic_source_reprojects_to_metric_basin_without_ignoring_crs(self):
        geo = osr.SpatialReference()
        geo.ImportFromEPSG(4326)
        geo.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        to_metric = osr.CoordinateTransformation(geo, self.reference)
        x, y, _ = to_metric.TransformPoint(-3.0025, 41.9975)
        analysis = self.rectangle(x - 100, y - 100, x + 100, y + 100)
        path = self.raster([[12]], transform=(-3.005, .005, 0, 42, 0, -.005), epsg=4326)
        result = hydro.basin_raster_mean(analysis, path)
        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["mean_mm"], 12)
        self.assertEqual(result["pixel_count"], 1)
        self.assertTrue(any("CRS distinto" in note for note in result["notes"]))

    def test_cancellation_during_work_is_not_converted_to_unavailable(self):
        path = self.raster(np.ones((1, 1000)))
        cancelled = [False]

        def progress(percent):
            if percent > .1:
                cancelled[0] = True

        with self.assertRaises(InterruptedError):
            hydro.basin_raster_mean(self.rectangle(0, 500, 500000, 1000), path,
                                    cancelled=lambda: cancelled[0], progress=progress)
        with self.assertRaises(InterruptedError):
            hydro.basin_raster_mean(self.rectangle(0, 0, 1, 1), "missing", cancelled=lambda: True)

    def test_missing_source_limit_and_invalid_geometry_never_publish_false_mean(self):
        analysis = self.rectangle(0, 500, 1000, 1000)
        for path in ("", self.folder / "absent.tif"):
            result = hydro.basin_raster_mean(analysis, path)
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["mean_mm"])
            self.assertTrue(result["notes"])
        with patch.object(hydro, "MAX_CANDIDATE_CELLS", 1):
            result = hydro.basin_raster_mean(analysis, self.raster([[10, 20]]))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["mean_mm"])
        self.assertIn("límite", result["notes"][0])
        invalid = self.analysis("POLYGON((0 0,1000 1000,0 1000,1000 0,0 0))")
        self.assertEqual(hydro.basin_raster_mean(invalid, self.raster([[10]]))["status"], "unavailable")

    def test_source_values_and_geometry_are_unchanged(self):
        analysis = self.rectangle(0, 500, 1000, 1000)
        path = self.raster([[10, 20]])
        before, geometry = path.read_bytes(), analysis.geometry_wkb
        progress = []
        result = hydro.basin_raster_mean(analysis, path, progress=progress.append)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(analysis.geometry_wkb, geometry)
        self.assertEqual(result["mean_mm"], 15)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(progress[-1], 100)

    def test_fingerprint_exact_geometry_crs_no_raster_io(self):
        analysis = self.rectangle(0, 0, 10, 20)
        with patch.object(gdal, "Open", side_effect=AssertionError("Fingerprint must not read rasters")):
            value = hydro.basin_fingerprint(analysis)
        self.assertEqual(len(value), 64)
        expected = hashlib.sha256()
        for part in (analysis.geometry_wkb, analysis.crs_wkt.encode("utf-8")):
            expected.update(len(part).to_bytes(8, "big"))
            expected.update(part)
        self.assertEqual(value, expected.hexdigest())
        changed = self.rectangle(0, 0, 11, 20)
        self.assertNotEqual(value, hydro.basin_fingerprint(changed))
        changed.geometry_wkb = analysis.geometry_wkb
        changed.crs_wkt += " "
        self.assertNotEqual(value, hydro.basin_fingerprint(changed))

    def test_sources_respect_configured_missing_and_explicit_overrides(self):
        module = SimpleNamespace(P0="P0", RAIN_2="RAIN_2",
                                 getLayerPath=lambda key: {"P0": "/configured/missing-p0.tif", "RAIN_2": ""}[key],
                                 getLayer=Mock(side_effect=AssertionError("No project/layer access")))
        query = SimpleNamespace(dem_path=str(self.folder / "cache/mdt25.tif"), flow_layer_path="")
        with patch.dict(sys.modules, {"caumax.layers": module}):
            result = hydro.source_paths(query)
            self.assertEqual(result["p0"], "/configured/missing-p0.tif")
            self.assertEqual(result["rainfall"]["2"], "")
            self.assertEqual(result["rainfall"]["5"], str(self.folder / "cache/t5.tif"))
            explicit = hydro.source_paths(query, {"p0": "chosen-p0.tif", "rainfall": {"2": "chosen-t2.tif"}})
            self.assertEqual(explicit["p0"], "chosen-p0.tif")
            self.assertEqual(explicit["rainfall"]["2"], "chosen-t2.tif")
            module.getLayer.assert_not_called()

    def test_unconfigured_sources_fall_back_to_original_flow_folder_before_cached_dem(self):
        original = self.folder / "original"
        original.mkdir()
        flow = original / "q5.tif"
        flow.touch()
        query = SimpleNamespace(dem_path=str(self.folder / "cache/mdt25.tif"), flow_layer_path=str(flow))
        result = hydro.source_paths(query, {"P0": "explicit-native-p0.tif"})
        self.assertEqual(result["p0"], "explicit-native-p0.tif")
        self.assertEqual(result["rainfall"]["500"], str(original / "t500.tif"))
        query.flow_layer_path = str(flow) + "|option:bad"
        self.assertEqual(hydro.source_paths(query)["rainfall"]["5"], str(self.folder / "cache/t5.tif"))

    @unittest.skipUnless((ROOT / "work/ui_snap_final/cuenca_snap.caumax.json").is_file() and
                        (ROOT.parent / "inputs/caumax/caumax/data/p0.tif").is_file(),
                         "Replay25m y datos climáticos reales no disponibles")
    def test_real_25m_basin_replay_with_original_500m_p0_and_six_rainfall_grids(self):
        saved = json.loads((ROOT / "work/ui_snap_final/cuenca_snap.caumax.json").read_text())
        basin = dict(saved["analysis"]["basin"])
        basin["geometry_wkb"] = base64.b64decode(basin["geometry_wkb"], validate=True)
        analysis = SimpleNamespace(**basin)
        self.assertEqual(analysis.cell_width, 25)
        data = ROOT.parent / "inputs/caumax/caumax/data"
        paths = [data / "p0.tif", *(data / f"t{period}.tif" for period in hydro.PERIODS)]
        if not all(path.is_file() for path in paths):
            self.skipTest("Falta algún ráster climático real")
        results = [hydro.basin_raster_mean(analysis, path) for path in paths]
        for result in results:
            self.assertEqual(result["status"], "ok", result["notes"])
            self.assertEqual(result["valid_fraction"], 1)
            self.assertEqual(result["coverage_fraction"], 1)
            self.assertGreater(result["pixel_count"], 1000)
            self.assertGreater(result["mean_mm"], 0)
            self.assertEqual(result["basin_area_m2"], 390983125)
        rainfall = [result["mean_mm"] for result in results[1:]]
        self.assertEqual(rainfall, sorted(rainfall))
        print("REAL_BASIN_HYDROLOGY", {path.name: {"mean_mm": result["mean_mm"],
              "pixel_count": result["pixel_count"], "valid_fraction": result["valid_fraction"]}
              for path, result in zip(paths, results)}, flush=True)


if __name__ == "__main__":
    unittest.main()
