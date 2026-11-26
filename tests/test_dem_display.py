"""Real native DEM renderers and visible terrain composition, without project edits."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.core import (Qgis, QgsApplication, QgsBilinearRasterResampler,
                       QgsColorRampShader, QgsCptCityColorRamp,
                       QgsHillshadeRenderer, QgsMapRendererParallelJob,
                       QgsMapSettings, QgsProject, QgsRasterDataProvider, QgsSingleBandPseudoColorRenderer)

RASTER_RESAMPLING = getattr(Qgis, 'RasterResamplingMethod', None)
if RASTER_RESAMPLING is None:
    RASTER_RESAMPLING = QgsRasterDataProvider.ResamplingMethod


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from caumax_consulta import dem_display as display


def legacy_cd_a(colours, low, high):
    """Recreate the actual 1.2.3 renderer from QGIS's installed cpt-city resource."""
    native = QgsCptCityColorRamp('jm/cd/cd-a', '')
    if not Path(native.fileName()).is_file():
        raise unittest.SkipTest('The legacy-project fixture requires the bundled cpt-city resource.')
    ramp = QgsColorRampShader(low, high)
    ramp.setSourceColorRamp(native)
    ramp.setColorRampType(QgsColorRampShader.Discrete)
    ramp.setClassificationMode(QgsColorRampShader.Continuous)
    ramp.classifyColorRamp()
    colours.renderer().shader().setRasterShaderFunction(ramp)
    colours.renderer().setClassificationMin(low)
    colours.renderer().setClassificationMax(high)
    colours.setCustomProperty('hidrocaumax/dem_visible_range_m', [float(low), float(high)])
    colours.removeCustomProperty(display.STYLE_PROPERTY)


def legacy_hillshade(shade):
    shade.renderer().setMultiDirectional(True)
    shade.renderer().setAzimuth(315.)
    shade.renderer().setAltitude(45.)
    shade.renderer().setZFactor(1.)
    shade.setOpacity(.45)
    shade.setBlendMode(QPainter.CompositionMode_Multiply)
    shade.brightnessFilter().setBrightness(0)
    shade.brightnessFilter().setContrast(0)
    shade.brightnessFilter().setGamma(1.)
    shade.setResamplingStage(Qgis.RasterResamplingStage.ResampleFilter)
    shade.dataProvider().enableProviderResampling(False)
    shade.resampleFilter().setZoomedInResampler(QgsBilinearRasterResampler())
    shade.removeCustomProperty(display.STYLE_PROPERTY)


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
        self.assertFalse(shade.renderer().multiDirectional())
        self.assertEqual(shade.renderer().azimuth(), 315.)
        self.assertEqual(shade.renderer().altitude(), display.HILLSHADE_ALTITUDE)
        self.assertEqual(shade.renderer().zFactor(), display.HILLSHADE_Z_FACTOR)
        self.assertEqual(shade.blendMode(), QPainter.CompositionMode_Multiply)
        self.assertAlmostEqual(shade.opacity(), display.HILLSHADE_OPACITY)
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
            self.assertEqual(layer.customProperty(display.STYLE_PROPERTY), display.STYLE_REVISION)
            self.assertEqual(layer.resamplingStage(), Qgis.RasterResamplingStage.Provider)
            self.assertEqual(layer.dataProvider().zoomedInResamplingMethod(), RASTER_RESAMPLING.CubicSpline)
            self.assertEqual(layer.dataProvider().zoomedOutResamplingMethod(), RASTER_RESAMPLING.CubicSpline)

    def test_provider_resampling_works_with_both_official_enum_locations(self):
        path = self.raster([[900, 950, 1000], [930, 970, 1050]])
        before = path.read_bytes()
        shade, _ = self.build(path)
        stage = Qgis.RasterResamplingStage
        old_api = SimpleNamespace(RasterResamplingStage=stage)
        new_api = SimpleNamespace(RasterResamplingStage=stage,RasterResamplingMethod=RASTER_RESAMPLING)
        old_provider = SimpleNamespace(ResamplingMethod=RASTER_RESAMPLING)
        for api,provider_api in ((old_api,old_provider),(new_api,SimpleNamespace())):
            with self.subTest(new_location=hasattr(api,'RasterResamplingMethod')):
                provider = shade.dataProvider()
                provider.setZoomedInResamplingMethod(RASTER_RESAMPLING.Nearest)
                provider.setZoomedOutResamplingMethod(RASTER_RESAMPLING.Nearest)
                with patch.object(display,'Qgis',api), patch.object(display,'QgsRasterDataProvider',provider_api):
                    self.assertTrue(display.smooth_terrain_display(shade))
                    self.assertFalse(display.smooth_terrain_display(shade))
                self.assertEqual(provider.zoomedInResamplingMethod(),RASTER_RESAMPLING.CubicSpline)
                self.assertEqual(provider.zoomedOutResamplingMethod(),RASTER_RESAMPLING.CubicSpline)
                self.assertEqual(shade.resamplingStage(),stage.Provider)
                self.assertEqual((shade.width(),shade.height()),(3,2))
        self.assertEqual(path.read_bytes(),before)

    def test_ramp_limits_exclude_nodata_and_use_finite_natural_endpoints(self):
        path = self.raster([[-9999, 875], [1000, 1971]])
        shade, colours = self.build(path)
        self.assertEqual(colours.customProperty("hidrocaumax/dem_range_m"), [875.0, 1971.0])
        ramp = colours.renderer().shader().rasterShaderFunction().colorRampItemList()
        self.assertEqual(ramp[0].value, 875.)
        self.assertEqual(ramp[-1].value, 1971.)
        self.assertTrue(all(math.isfinite(item.value) for item in ramp))
        self.assertEqual(ramp[0].color.name().upper(), display.NATURAL_RAMP[0][1])
        self.assertEqual(ramp[-1].color.name().upper(), display.NATURAL_RAMP[-1][1])
        self.assertEqual(colours.renderer().classificationMin(), 875)
        self.assertEqual(colours.renderer().classificationMax(), 1971)

    def test_natural_shader_has_gradual_transitions_instead_of_discrete_bands(self):
        _, colours = self.build(self.raster([[900, 1000], [1200, 1500]]))
        actual = colours.renderer().shader().rasterShaderFunction()
        self.assertEqual(actual.colorRampType(), QgsColorRampShader.Interpolated)
        self.assertFalse(actual.sourceColorRamp().isDiscrete())
        samples = [actual.shade(float(value)) for value in np.linspace(900, 1500, 1201)]
        self.assertTrue(all(sample[0] for sample in samples))
        rgb = np.asarray([sample[1:4] for sample in samples], dtype=int)
        self.assertGreater(len(np.unique(rgb, axis=0)), 150)
        self.assertLessEqual(int(np.abs(np.diff(rgb, axis=0)).max()), 2)
        # Dark vegetation at low elevations, warm earth in the middle,
        # and light neutral rock at the top, without cyan or white banding.
        self.assertGreater(rgb[0, 1], rgb[0, 0])
        self.assertLess(float(rgb[0].mean()), 100.)
        self.assertGreater(float(rgb[-1].mean() - rgb[0].mean()), 140.)
        self.assertGreater(rgb[650, 0], rgb[650, 2])
        self.assertLess(int(rgb[-1].max()-rgb[-1].min()), 20)

    def test_native_resolution_follows_each_real_active_dem(self):
        for resolution in (500, 25, 5, 2):
            path = self.raster(np.arange(25).reshape(5, 5) + 900, resolution=resolution)
            for layer in self.build(path):
                self.assertEqual(layer.customProperty(display.RESOLUTION_PROPERTY), [float(resolution)] * 2)
                self.assertEqual(layer.rasterUnitsPerPixelX(), resolution)
                self.assertEqual(layer.rasterUnitsPerPixelY(), resolution)

    def test_saved_project_palette_updates_even_when_visible_elevation_range_is_unchanged(self):
        path = self.raster([[900, 1000], [1200, 1500]])
        _, colours = self.build(path)
        legacy_cd_a(colours, 900, 1500)
        ramp = colours.renderer().shader().rasterShaderFunction()
        renderer = colours.renderer()
        original = path.read_bytes()
        self.assertEqual(ramp.colorRampType(), QgsColorRampShader.Discrete)
        self.assertTrue(display.apply_terrain_style(colours))
        self.assertIs(colours.renderer(), renderer)
        actual = ramp.colorRampItemList()
        self.assertEqual([item.color.name().upper() for item in actual],
                         [colour for _, colour in display.NATURAL_RAMP])
        self.assertEqual(ramp.colorRampType(), QgsColorRampShader.Interpolated)
        self.assertEqual((actual[0].value, actual[-1].value), (900., 1500.))
        self.assertFalse(display.apply_terrain_style(colours))
        self.assertFalse(display.apply_visible_elevation_range(colours, 900, 1500))
        self.assertEqual(path.read_bytes(), original)

    def test_actual_terrain_render_is_coloured_and_new_shading_increases_relief_contrast(self):
        yy, xx = np.indices((33, 33))
        data = 1000 + 180 * np.sin(xx / 8) * np.cos(yy / 9) + xx * 2
        layers = self.build(self.raster(data))
        colour_only = self.render([layers[1]])
        shaded = self.render(layers)
        self.assertGreater(np.unique(shaded.reshape(-1, 3), axis=0).shape[0], 100)
        self.assertGreater(np.count_nonzero(np.any(shaded != colour_only, axis=2)), 30000)
        self.assertLess(float(shaded.mean()), float(colour_only.mean()))
        legacy = layers[0].clone()
        self.layers.append(legacy)
        legacy_hillshade(legacy)
        previous = self.render([legacy, layers[1]])
        weights = np.array([.2126, .7152, .0722])
        current_luma = shaded[20:-20, 20:-20] @ weights
        previous_luma = previous[20:-20, 20:-20] @ weights
        current_contrast = float(np.percentile(current_luma, 95)-np.percentile(current_luma, 5))
        previous_contrast = float(np.percentile(previous_luma, 95)-np.percentile(previous_luma, 5))
        self.assertGreater(current_contrast, previous_contrast*1.05)

    def test_qgz_migrates_legacy_colour_and_shade_once_preserving_ids_visibility_and_manual_settings(self):
        path = self.raster(np.arange(81).reshape(9, 9)+900)
        source_before = path.read_bytes()
        shade, colours = self.build(path)
        legacy_hillshade(shade)
        legacy_cd_a(colours, 900, 980)
        old_ids = {'hillshade': shade.id(), 'hypsometry': colours.id()}
        saved = self.folder/'legacy123.qgz'
        project = QgsProject()
        reopened = QgsProject()
        final = QgsProject()
        try:
            project.addMapLayer(shade)
            project.addMapLayer(colours)
            project.layerTreeRoot().findLayer(shade.id()).setItemVisibilityChecked(False)
            project.layerTreeRoot().findLayer(colours.id()).setItemVisibilityChecked(True)
            self.assertTrue(project.write(str(saved)))
            project.clear()
            self.assertTrue(reopened.read(str(saved)))
            migrated = {}
            for role, layer_id in old_ids.items():
                layer = reopened.mapLayer(layer_id)
                self.assertIsNotNone(layer)
                renderer = layer.renderer()
                was_visible = reopened.layerTreeRoot().findLayer(layer_id).itemVisibilityChecked()
                self.assertTrue(display.apply_terrain_style(layer))
                self.assertIs(layer.renderer(), renderer)
                self.assertEqual(layer.id(), layer_id)
                self.assertEqual(reopened.layerTreeRoot().findLayer(layer_id).itemVisibilityChecked(), was_visible)
                self.assertEqual(layer.customProperty(display.STYLE_PROPERTY), display.STYLE_REVISION)
                migrated[role] = layer
            shade, colours = migrated['hillshade'], migrated['hypsometry']
            self.assertEqual(shade.renderer().zFactor(), display.HILLSHADE_Z_FACTOR)
            self.assertAlmostEqual(shade.opacity(), display.HILLSHADE_OPACITY)
            self.assertFalse(shade.renderer().multiDirectional())
            self.assertEqual(colours.renderer().shader().rasterShaderFunction().colorRampType(), QgsColorRampShader.Interpolated)
            self.assertEqual(colours.customProperty('hidrocaumax/dem_visible_range_m'), [900., 980.])
            # After the one-time migration, respect subsequent cartographic edits.
            shade.renderer().setZFactor(1.7)
            shade.renderer().setAltitude(58.)
            shade.renderer().setAzimuth(225.)
            shade.renderer().setMultiDirectional(True)
            shade.setOpacity(.37)
            shade.setBlendMode(QPainter.CompositionMode_Screen)
            shade.brightnessFilter().setBrightness(-6)
            shade.brightnessFilter().setContrast(4)
            shade.dataProvider().setZoomedInResamplingMethod(RASTER_RESAMPLING.Nearest)
            colours.setOpacity(.63)
            self.assertFalse(display.apply_terrain_style(shade))
            self.assertFalse(display.apply_terrain_style(colours))
            revised = self.folder/'migrated124.qgz'
            self.assertTrue(reopened.write(str(revised)))
            reopened.clear()
            self.assertTrue(final.read(str(revised)))
            shade = final.mapLayer(old_ids['hillshade'])
            colours = final.mapLayer(old_ids['hypsometry'])
            self.assertFalse(display.apply_terrain_style(shade))
            self.assertFalse(display.apply_terrain_style(colours))
            self.assertAlmostEqual(shade.opacity(), .37)
            self.assertAlmostEqual(colours.opacity(), .63)
            self.assertEqual(shade.renderer().zFactor(), 1.7)
            self.assertEqual((shade.renderer().azimuth(), shade.renderer().altitude()), (225., 58.))
            self.assertTrue(shade.renderer().multiDirectional())
            self.assertEqual(shade.blendMode(), QPainter.CompositionMode_Screen)
            self.assertEqual(shade.brightnessFilter().brightness(), -6)
            self.assertEqual(shade.brightnessFilter().contrast(), 4)
            self.assertEqual(shade.dataProvider().zoomedInResamplingMethod(), RASTER_RESAMPLING.Nearest)
            self.assertFalse(final.layerTreeRoot().findLayer(shade.id()).itemVisibilityChecked())
            self.assertTrue(final.layerTreeRoot().findLayer(colours.id()).itemVisibilityChecked())
        finally:
            project.clear()
            reopened.clear()
            final.clear()
        self.assertEqual(path.read_bytes(), source_before)

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
