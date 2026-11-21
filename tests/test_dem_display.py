"""Real native DEM renderers and visible terrain composition, without project edits."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.core import (QgsApplication, QgsHillshadeRenderer, QgsMapRendererParallelJob,
                       QgsMapSettings, QgsProject, QgsSingleBandPseudoColorRenderer)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from caumax_consulta import dem_display as display


class DemDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dem_display_")
        self.folder = Path(self.temp.name)
        self.layers = []

    def tearDown(self):
        for layer in self.layers:
            if not sip.isdeleted(layer):
                sip.delete(layer)
        self.temp.cleanup()

    def raster(self, data, resolution=25, epsg=25830):
        data = np.asarray(data, dtype=np.float32)
        path = self.folder / (str(len(list(self.folder.iterdir()))) + ".tif")
        ds = gdal.GetDriverByName("GTiff").Create(str(path), data.shape[1], data.shape[0], 1, gdal.GDT_Float32)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(epsg)
        ds.SetProjection(reference.ExportToWkt())
        ds.SetGeoTransform((300000, resolution, 0, 4700000, 0, -resolution))
        band = ds.GetRasterBand(1)
        band.SetUnitType("m")
        band.SetNoDataValue(-9999)
        band.WriteArray(data)
        ds.FlushCache()
        ds = None
        return path

    def build(self, path, **kwargs):
        layers = display.build_terrain_layers(path, **kwargs)
        self.layers.extend(layers)
        return layers

    def render(self, layers):
        settings = QgsMapSettings()
        settings.setOutputSize(QSize(240, 240))
        settings.setBackgroundColor(QColor("white"))
        settings.setDestinationCrs(layers[0].crs())
        settings.setExtent(layers[0].extent())
        settings.setLayers(layers)
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        self.assertFalse(job.errors())
        image = job.renderedImage()
        return np.array([[image.pixelColor(x, y).getRgb()[:3] for x in range(image.width())]
                         for y in range(image.height())], dtype=np.uint8)

    def test_factory_native_layers_order_styles_sources_and_no_project_registration(self):
        path = self.raster([[875, 900, 1000], [1200, 1500, 1971]])
        before = set(QgsProject.instance().mapLayers())
        shade, colours = self.build(path)
        self.assertEqual(set(QgsProject.instance().mapLayers()), before)
        self.assertIsInstance(shade.renderer(), QgsHillshadeRenderer)
        self.assertTrue(shade.renderer().multiDirectional())
        self.assertEqual(shade.renderer().zFactor(), 1)
        self.assertEqual(shade.blendMode(), QPainter.CompositionMode_Multiply)
        self.assertAlmostEqual(shade.opacity(), .45)
        self.assertIsInstance(colours.renderer(), QgsSingleBandPseudoColorRenderer)
        self.assertEqual(colours.opacity(), 1)
        for layer, role in ((shade, "hillshade"), (colours, "hypsometry")):
            self.assertTrue(layer.isValid())
            self.assertEqual(Path(layer.source()), path.resolve())
            self.assertEqual(layer.customProperty(display.ROLE_PROPERTY), role)
            self.assertEqual(layer.customProperty(display.SOURCE_PROPERTY), str(path.resolve()))
            self.assertEqual(layer.customProperty(display.RESOLUTION_PROPERTY), [25.0, 25.0])
            self.assertEqual(layer.width(), 3)
            self.assertEqual(layer.height(), 2)
            self.assertIn("25×25 m", layer.name())

    def test_ramp_limits_exclude_nodata_and_ends_in_stone_not_white_snow(self):
        path = self.raster([[-9999, 875], [1000, 1971]])
        shade, colours = self.build(path)
        self.assertEqual(colours.customProperty("hidrocaumax/dem_range_m"), [875.0, 1971.0])
        ramp = colours.renderer().shader().rasterShaderFunction().colorRampItemList()
        self.assertEqual(len(ramp), len(display.NATURAL_RAMP))
        self.assertEqual(ramp[0].color.name().upper(), "#477754")
        self.assertEqual(ramp[-1].color.name().upper(), "#DED8CB")
        self.assertTrue(all(max(item.color.red(), item.color.green(), item.color.blue()) < 230 for item in ramp))

    def test_native_resolution_follows_each_real_active_dem(self):
        for resolution in (500, 25, 5, 2):
            path = self.raster(np.arange(25).reshape(5, 5) + 900, resolution=resolution)
            for layer in self.build(path):
                self.assertEqual(layer.customProperty(display.RESOLUTION_PROPERTY), [float(resolution)] * 2)
                self.assertEqual(layer.rasterUnitsPerPixelX(), resolution)
                self.assertEqual(layer.rasterUnitsPerPixelY(), resolution)

    def test_actual_terrain_render_is_coloured_and_multidirectional_shading_changes_pixels(self):
        yy, xx = np.indices((33, 33))
        data = 1000 + 180 * np.sin(xx / 8) * np.cos(yy / 9) + xx * 2
        layers = self.build(self.raster(data))
        colour_only = self.render([layers[1]])
        shaded = self.render(layers)
        self.assertGreater(np.unique(shaded.reshape(-1, 3), axis=0).shape[0], 100)
        self.assertGreater(np.count_nonzero(np.any(shaded != colour_only, axis=2)), 30000)
        self.assertLess(float(shaded.mean()), float(colour_only.mean()))

    def test_factory_and_render_leave_source_bytes_affine_and_sidecars_unchanged(self):
        path = self.raster(np.arange(81).reshape(9, 9) + 900)
        original = path.read_bytes()
        files_before = sorted(p.name for p in self.folder.iterdir())
        pam_before = gdal.GetThreadLocalConfigOption("GDAL_PAM_ENABLED")
        layers = self.build(path)
        self.render(layers)
        for layer in layers:
            sip.delete(layer)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), files_before)
        self.assertEqual(gdal.GetThreadLocalConfigOption("GDAL_PAM_ENABLED"), pam_before)

    def test_known_analysis_range_avoids_sampling_and_missing_range_falls_back(self):
        path = self.raster(np.arange(25).reshape(5, 5) + 900)
        with patch.object(display.np, "isfinite", side_effect=AssertionError("Must not sample pixels")):
            layers = self.build(path, elevation_range=(905, 915))
        self.assertEqual(layers[1].customProperty("hidrocaumax/dem_range_m"), [905.0, 915.0])
        self.assertEqual(self.build(path, elevation_range=(None, None))[1].customProperty(
            "hidrocaumax/dem_range_m"), [900.0, 924.0])

    def test_flat_terrain_stays_uniform_and_missing_invalid_dem_is_explicit(self):
        layers = self.build(self.raster(np.full((9, 9), 1200)))
        self.assertEqual(layers[1].customProperty("hidrocaumax/dem_range_m"), [1200.0, 1200.0])
        pixels = self.render([layers[1]])
        self.assertEqual(np.unique(pixels[40:-40, 40:-40].reshape(-1, 3), axis=0).shape[0], 1)
        with self.assertRaises(ValueError):
            self.build(self.folder / "absent.tif")
        with self.assertRaisesRegex(ValueError, "cotas válidas"):
            self.build(self.raster(np.full((3, 3), -9999)))
        with self.assertRaisesRegex(ValueError, "proyectado"):
            self.build(self.raster(np.ones((3, 3)), epsg=4326))


if __name__ == "__main__":
    unittest.main()
