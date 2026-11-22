"""Native raster styling follows the map without changing DEM or hydrology."""
import hashlib
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt import sip
from qgis.PyQt.QtTest import QTest
from qgis.core import (Qgis, QgsColorRampShader, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject, QgsRectangle)
from qgis.gui import QgsMapCanvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from caumax_consulta import dem_display as display
from caumax_consulta.visible_dem import (BASIN_BOUNDS_PROPERTY, BASIN_CRS_PROPERTY,
                                       VisibleDemController, layer_basin_extent,
                                       reference_extent, visible_elevation_range)
import test_dem_display as fixture


class VisibleDemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DemDisplayTests.setUpClass()
        cls.app = fixture.DemDisplayTests.app

    def setUp(self):
        self.fixture = fixture.DemDisplayTests()
        self.fixture.setUp()
        self.canvas = QgsMapCanvas()
        self.canvas.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.canvas.resize(400, 400)
        self.canvas.show()
        self.controller = VisibleDemController(SimpleNamespace(mapCanvas=lambda: self.canvas), debounce_ms=15)
        self.left = QgsRectangle(300000, 4699900, 300100, 4700000)
        self.right = QgsRectangle(300100, 4699900, 300200, 4700000)

    def tearDown(self):
        self.controller.stop()
        self.wait(lambda: not self.controller._tasks)
        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self.canvas.clearCache()
        self.canvas.close()
        sip.delete(self.canvas)
        self.app.processEvents()
        try:
            self.fixture.tearDown()
        except PermissionError as error:
            # QGIS/GDAL may retain pooled raster handles after canvas disposal
            # on Windows. Only disposable test-directory cleanup is optional.
            if os.name != 'nt' or getattr(error, 'winerror', None) != 32:
                raise

    def wait(self, predicate):
        deadline = time.monotonic()+8
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate(), 'La actualización de estilo no terminó.')

    def layers(self, data=None):
        data = (np.hstack((np.arange(16).reshape(4, 4)+100,
                           np.arange(16).reshape(4, 4)+1000)) if data is None else data)
        path = self.fixture.raster(data)
        shade, colours = self.fixture.build(path)
        self.canvas.setDestinationCrs(colours.crs())
        self.canvas.setLayers([shade, colours])
        return path, shade, colours

    def show_range(self, colours, extent, expected):
        self.canvas.setExtent(extent)
        self.canvas.refresh()
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == list(expected))

    def test_visible_extrema_exclude_nodata_and_do_not_modify_source_or_sidecars(self):
        data = np.hstack((np.arange(16, dtype=float).reshape(4, 4)+100,
                          np.arange(16, dtype=float).reshape(4, 4)+1000))
        data[0, 0] = -9999
        data[1, 0] = np.nan
        path, _, _ = self.layers(data)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        files = sorted(p.name for p in path.parent.iterdir())
        self.assertEqual(visible_elevation_range(path, (300000, 4699900, 300100, 4700000)), (101., 115.))
        self.assertEqual(visible_elevation_range(path, (300100, 4699900, 300200, 4700000)), (1000., 1015.))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), files)

    def test_pan_updates_existing_layer_and_legend_preserving_palette_and_hillshade(self):
        _, shade, colours = self.layers()
        initial_source_range = colours.customProperty('hidrocaumax/dem_range_m')
        self.canvas.setExtent(self.left)
        self.controller.start()
        self.controller.start()  # Enabling twice must not add more listeners.
        self.assertEqual(len(self.controller._signals), 3)
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [100., 115.])
        self.show_range(colours, self.right, (1000., 1015.))
        ramp = colours.renderer().shader().rasterShaderFunction().colorRampItemList()
        self.assertEqual([item.color.name().upper() for item in ramp], [colour for _, colour in display.NATURAL_RAMP])
        self.assertEqual((colours.renderer().classificationMin(), colours.renderer().classificationMax()),
                         (1000., 1015.))
        self.assertEqual((ramp[0].value, ramp[-1].value), (1000., 1015.))
        self.assertTrue(all(math.isfinite(item.value) for item in ramp))
        self.assertTrue(ramp[0].label.startswith('1000'))
        self.assertTrue(ramp[-1].label.endswith('1015 m'))
        self.assertEqual(colours.customProperty('hidrocaumax/dem_range_m'), initial_source_range)
        self.assertEqual(colours.opacity(), 1)
        self.assertAlmostEqual(shade.opacity(), display.HILLSHADE_OPACITY)
        self.assertFalse(shade.renderer().multiDirectional())
        self.assertEqual(shade.renderer().zFactor(), display.HILLSHADE_Z_FACTOR)
        for layer in (shade, colours):
            self.assertEqual(layer.resamplingStage(), Qgis.RasterResamplingStage.Provider)
            self.assertEqual(layer.dataProvider().zoomedInResamplingMethod(), Qgis.RasterResamplingMethod.CubicSpline)

    def test_canvas_crs_changes_are_transformed_to_the_raster(self):
        _, _, colours = self.layers()
        self.controller.start()
        self.show_range(colours, self.left, (100., 115.))
        target = QgsCoordinateReferenceSystem('EPSG:3857')
        transform = QgsCoordinateTransform(colours.crs(), target, QgsProject.instance().transformContext())
        projected = transform.transformBoundingBox(QgsRectangle(300105, 4699905, 300195, 4699995))
        self.canvas.setDestinationCrs(target)
        self.show_range(colours, projected, (1000., 1015.))

    def test_hidden_layer_is_untouched_and_showing_it_recomputes_current_extent(self):
        _, shade, colours = self.layers()
        self.controller.start()
        self.show_range(colours, self.left, (100., 115.))
        self.canvas.setLayers([shade])
        self.canvas.setExtent(self.right)
        QTest.qWait(100)
        self.assertEqual(colours.customProperty('hidrocaumax/dem_visible_range_m'), [100., 115.])
        self.canvas.setLayers([shade, colours])
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [1000., 1015.])
        self.controller.stop()
        self.assertEqual(self.controller._signals, [])
        self.assertEqual(self.controller._layer_signals, {})
        self.canvas.setExtent(self.left)
        QTest.qWait(100)
        self.assertEqual(colours.customProperty('hidrocaumax/dem_visible_range_m'), [1000., 1015.])

    def test_basin_neighbourhood_limits_overview_but_respects_close_zoom_and_pan(self):
        basin = QgsRectangle(0, 0, 100, 200)
        overview = QgsRectangle(-1000, -1000, 2000, 2000)
        self.assertEqual(reference_extent(overview, basin), QgsRectangle(-10, -20, 110, 220))
        close = QgsRectangle(20, 30, 40, 50)
        self.assertEqual(reference_extent(close, basin), close)
        partial = QgsRectangle(50, 50, 500, 500)
        self.assertEqual(reference_extent(partial, basin), QgsRectangle(50, 50, 110, 220))
        away = QgsRectangle(1000, 1000, 2000, 2000)
        self.assertEqual(reference_extent(away, basin), QgsRectangle(1440, 1380, 1560, 1620))
        self.assertEqual(reference_extent(overview), overview)

    def test_overview_ignores_distant_peak_and_new_basin_updates_without_panning(self):
        _, shade, colours = self.layers()
        colours.setCustomProperty(BASIN_BOUNDS_PROPERTY, [300010, 4699910, 300090, 4699990])
        colours.setCustomProperty(BASIN_CRS_PROPERTY, colours.crs().toWkt())
        self.canvas.setExtent(QgsRectangle(299900, 4699800, 300300, 4700200))
        self.controller.start()
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [100., 115.])
        self.assertEqual(colours.customProperty('hidrocaumax/dem_reference_bounds'),
                         [300002., 4699902., 300098., 4699998.])
        # Same canvas and same DEM, but a newly computed basin in the highlands.
        colours.setCustomProperty(BASIN_BOUNDS_PROPERTY, [300110, 4699910, 300190, 4699990])
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [1000., 1015.])
        self.assertEqual(shade.renderer().zFactor(), display.HILLSHADE_Z_FACTOR)
        # Invalid persisted context must fall back safely to the visible range.
        colours.setCustomProperty(BASIN_BOUNDS_PROPERTY, [float('nan'), 0, 0, 0])
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [100., 1015.])

    def test_basin_reference_crs_is_independent_of_canvas_and_raster_crs(self):
        _, _, colours = self.layers()
        target = QgsCoordinateReferenceSystem('EPSG:3857')
        transform = QgsCoordinateTransform(colours.crs(), target, QgsProject.instance().transformContext())
        # Leave room for the conservative envelope of two CRS transformations.
        basin = QgsRectangle(300020, 4699920, 300080, 4699980)
        projected = transform.transformBoundingBox(basin)
        colours.setCustomProperty(BASIN_BOUNDS_PROPERTY,
                                  [projected.xMinimum(), projected.yMinimum(),
                                   projected.xMaximum(), projected.yMaximum()])
        colours.setCustomProperty(BASIN_CRS_PROPERTY, target.toWkt())
        restored = layer_basin_extent(colours)
        self.assertIsNotNone(restored)
        self.assertLess(abs(restored.center().x()-basin.center().x()), .01)
        self.assertLess(abs(restored.center().y()-basin.center().y()), .01)
        self.canvas.setExtent(QgsRectangle(299900, 4699800, 300300, 4700200))
        self.controller.start()
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [100., 115.])

    def test_reopened_layer_with_same_id_rebinds_context_and_migrates_cached_palette(self):
        _, shade, colours = self.layers()
        colours.setCustomProperty(BASIN_BOUNDS_PROPERTY, [300010, 4699910, 300090, 4699990])
        colours.setCustomProperty(BASIN_CRS_PROPERTY, colours.crs().toWkt())
        self.canvas.setExtent(QgsRectangle(299900, 4699800, 300300, 4700200))
        self.controller.start()
        self.wait(lambda: colours.customProperty('hidrocaumax/dem_visible_range_m') == [100., 115.])
        previous_signature = self.controller._applied[colours.id()]
        previous_extent = QgsRectangle(self.canvas.extent())
        replacement = colours.clone()
        self.fixture.layers.append(replacement)
        self.assertTrue(replacement.setId(colours.id()))
        self.assertIsNot(replacement, colours)
        self.assertEqual(replacement.source(), colours.source())
        fixture.legacy_cd_a(replacement, 100, 115)
        # Project reload can remove and restore the same saved layer ID before
        # the debounce fires. The new object has the same source and bounds.
        self.canvas.setLayers([shade])
        self.canvas.setLayers([shade, replacement])
        self.assertEqual(self.controller._applied[colours.id()], previous_signature)
        self.wait(lambda: [item.color.name().upper() for item in
                           replacement.renderer().shader().rasterShaderFunction().colorRampItemList()]
                         == [colour.upper() for _, colour in display.NATURAL_RAMP])
        self.assertEqual(replacement.renderer().shader().rasterShaderFunction().colorRampType(),
                         QgsColorRampShader.Interpolated)
        self.assertIs(self.controller._layer_signals[replacement.id()](), replacement)
        self.assertEqual(self.canvas.extent(), previous_extent)
        replacement.setCustomProperty(BASIN_BOUNDS_PROPERTY,
                                      [300110, 4699910, 300190, 4699990])
        self.wait(lambda: replacement.customProperty('hidrocaumax/dem_visible_range_m')
                         == [1000., 1015.])
        self.assertEqual(self.canvas.extent(), previous_extent)

    def test_cached_same_extent_migrates_both_renderers_once_and_respects_later_manual_shading(self):
        path, shade, colours = self.layers()
        source_before = path.read_bytes()
        self.controller.start()
        self.show_range(colours, self.left, (100., 115.))
        self.wait(lambda: not self.controller._tasks)
        signature = self.controller._applied[colours.id()]
        extent = QgsRectangle(self.canvas.extent())
        ids = (shade.id(), colours.id())
        renderers = (shade.renderer(), colours.renderer())
        fixture.legacy_hillshade(shade)
        fixture.legacy_cd_a(colours, 100, 115)
        # A matching viewport signature must not skip the style migration or
        # trigger a second raster scan solely to apply the visual revision.
        with patch('caumax_consulta.visible_dem.QgsApplication.taskManager') as manager:
            self.controller.refresh()
        manager.assert_not_called()
        self.assertEqual(self.controller._applied[colours.id()], signature)
        self.assertEqual((shade.id(), colours.id()), ids)
        self.assertIs(shade.renderer(), renderers[0])
        self.assertIs(colours.renderer(), renderers[1])
        self.assertEqual(self.canvas.extent(), extent)
        self.assertEqual(colours.renderer().shader().rasterShaderFunction().colorRampType(), QgsColorRampShader.Interpolated)
        self.assertEqual(shade.renderer().zFactor(), display.HILLSHADE_Z_FACTOR)
        self.assertAlmostEqual(shade.opacity(), display.HILLSHADE_OPACITY)
        for layer in (shade, colours):
            self.assertEqual(layer.customProperty(display.STYLE_PROPERTY), display.STYLE_REVISION)
        shade.setOpacity(.32)
        shade.renderer().setZFactor(2.25)
        shade.renderer().setAltitude(50.)
        shade.brightnessFilter().setBrightness(-4)
        shade.brightnessFilter().setContrast(7)
        shade.dataProvider().setZoomedInResamplingMethod(Qgis.RasterResamplingMethod.Nearest)
        colours.setOpacity(.6)
        self.controller.refresh()
        self.show_range(colours, self.right, (1000., 1015.))
        self.assertAlmostEqual(shade.opacity(), .32)
        self.assertEqual(shade.renderer().zFactor(), 2.25)
        self.assertEqual(shade.renderer().altitude(), 50.)
        self.assertEqual(shade.brightnessFilter().brightness(), -4)
        self.assertEqual(shade.brightnessFilter().contrast(), 7)
        self.assertEqual(shade.dataProvider().zoomedInResamplingMethod(), Qgis.RasterResamplingMethod.Nearest)
        self.assertAlmostEqual(colours.opacity(), .6)
        self.assertEqual(path.read_bytes(), source_before)

    def test_empty_outside_and_flat_windows_keep_valid_uniform_rendering(self):
        data = np.hstack((np.full((4, 4), -9999), np.full((4, 4), 1200)))
        path, _, colours = self.layers(data)
        self.assertIsNone(visible_elevation_range(path, (300000, 4699900, 300100, 4700000)))
        self.assertIsNone(visible_elevation_range(path, (200000, 200000, 200100, 200100)))
        self.controller.start()
        self.show_range(colours, self.right, (1200., 1200.))
        self.assertTrue(math.isfinite(colours.renderer().classificationMin()))
        self.assertLess(colours.renderer().classificationMin(), colours.renderer().classificationMax())
        self.canvas.setExtent(self.left)
        QTest.qWait(100)
        self.assertEqual(colours.customProperty('hidrocaumax/dem_visible_range_m'), [1200., 1200.])


if __name__ == '__main__':
    unittest.main()
