"""Native raster styling follows the map without changing DEM or hydrology."""
import hashlib
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest

import numpy as np
from qgis.PyQt.QtCore import Qt
from qgis.PyQt import sip
from qgis.PyQt.QtTest import QTest
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject, QgsRectangle)
from qgis.gui import QgsMapCanvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from caumax_consulta import dem_display as display
from caumax_consulta.visible_dem import VisibleDemController, visible_elevation_range
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
        self.assertEqual((ramp[0].value, ramp[-1].value), (1000., 1015.))
        self.assertEqual((ramp[0].label, ramp[-1].label), ('1000 m', '1015 m'))
        self.assertEqual(colours.customProperty('hidrocaumax/dem_range_m'), initial_source_range)
        self.assertEqual(colours.opacity(), 1)
        self.assertAlmostEqual(shade.opacity(), display.HILLSHADE_OPACITY)
        self.assertTrue(shade.renderer().multiDirectional())
        self.assertEqual(shade.renderer().zFactor(), 1)
        self.assertEqual(shade.resampleFilter().zoomedInResampler().type(), 'bilinear')
        self.assertEqual(colours.resampleFilter().zoomedInResampler().type(), 'bilinear')

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
        self.canvas.setExtent(self.left)
        QTest.qWait(100)
        self.assertEqual(colours.customProperty('hidrocaumax/dem_visible_range_m'), [1000., 1015.])

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
