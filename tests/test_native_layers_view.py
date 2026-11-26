"""Native load command gets one overview; other navigation stays untouched."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject, QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsMapCanvas
import qgis.utils

from caumax_consulta.native_layers_view import IBERIA_BOUNDS, NativeLayersView


class NativeLayersViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject.instance()
        self.canvas = QgsMapCanvas()
        self.canvas.setAttribute(Qt.WA_DontShowOnScreen)
        self.canvas.resize(1230, 790)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:25830'))
        self.canvas.setExtent(QgsRectangle(300000, 4700000, 320000, 4720000))
        self.original = QgsRectangle(self.canvas.extent())
        self.action = QAction('Cargar capas CAUMAX')
        self.loaded = 0
        self.layer = None
        self.group = None
        self.action.triggered.connect(self.native_load)
        self.registry = patch.dict(qgis.utils.plugins, {'caumax': SimpleNamespace(actionLoadProject=self.action)})
        self.registry.start()
        self.bridge = NativeLayersView(SimpleNamespace(mapCanvas=lambda: self.canvas))

    def tearDown(self):
        self.bridge.stop()
        self.registry.stop()
        if self.layer and not sip.isdeleted(self.layer):
            self.project.removeMapLayer(self.layer.id())
        if self.group and not sip.isdeleted(self.group):
            self.project.layerTreeRoot().removeChildNode(self.group)
        sip.delete(self.canvas)
        self.action.deleteLater()

    def native_load(self):
        self.loaded += 1
        if self.group is None:
            self.group = self.project.layerTreeRoot().addGroup('CAUMAX_CAPAS')
            self.layer = QgsVectorLayer('LineString?crs=EPSG:25830', 'Ríos', 'memory')
            self.project.addMapLayer(self.layer, False)
            self.group.addLayer(self.layer)

    def test_load_frames_iberia_after_loader_without_changing_crs_or_layers(self):
        self.bridge.start()
        self.assertEqual(self.canvas.extent(), self.original)
        self.action.trigger()
        self.app.processEvents()
        self.assertEqual(self.loaded, 1)
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
            self.canvas.mapSettings().destinationCrs(), self.project)
        target = transform.transformBoundingBox(QgsRectangle(*IBERIA_BOUNDS))
        actual = self.canvas.extent()
        self.assertTrue(actual.contains(target))
        self.assertAlmostEqual(actual.center().x(), target.center().x(), places=4)
        self.assertAlmostEqual(actual.center().y(), target.center().y(), places=4)
        self.assertEqual(self.canvas.mapSettings().destinationCrs().authid(), 'EPSG:25830')
        self.assertIs(self.project.mapLayer(self.layer.id()), self.layer)

    def test_existing_project_and_later_navigation_do_not_reset_zoom(self):
        self.native_load()
        self.bridge.start()
        self.app.processEvents()
        self.assertEqual(self.canvas.extent(), self.original)
        self.action.trigger()
        self.app.processEvents()
        self.canvas.setExtent(self.original)
        expected = QgsRectangle(self.canvas.extent())
        self.bridge._scan()
        self.app.processEvents()
        self.assertEqual(self.canvas.extent(), expected)

    def test_disabled_bridge_cancels_queued_zoom_and_keeps_native_loader(self):
        self.bridge.start()
        self.action.trigger()
        self.bridge.stop()
        self.app.processEvents()
        self.assertEqual(self.loaded, 1)
        self.assertEqual(self.canvas.extent(), self.original)
        self.action.trigger()
        self.app.processEvents()
        self.assertEqual(self.loaded, 2)
        self.assertEqual(self.canvas.extent(), self.original)

    def test_late_native_plugin_load_and_reload_are_bound_once(self):
        qgis.utils.plugins.pop('caumax')
        self.bridge.start()
        qgis.utils.plugins['caumax'] = SimpleNamespace(actionLoadProject=self.action)
        self.bridge._scan()
        self.bridge._scan()
        self.assertEqual(self.action.receivers(self.action.triggered), 2)
        replacement = QAction('Cargar capas CAUMAX')
        self.addCleanup(replacement.deleteLater)
        qgis.utils.plugins['caumax'] = SimpleNamespace(actionLoadProject=replacement)
        self.bridge._scan()
        self.assertEqual(self.action.receivers(self.action.triggered), 1)
        self.assertEqual(replacement.receivers(replacement.triggered), 1)
        self.bridge.stop()
        self.assertEqual(replacement.receivers(replacement.triggered), 0)

    def test_failed_load_and_reference_command_do_not_change_extent(self):
        self.action.triggered.disconnect(self.native_load)
        self.bridge.start()
        self.action.trigger()
        self.app.processEvents()
        self.assertEqual(self.canvas.extent(), self.original)
        reference_action = QAction('Cargar capas HidroCAUMAX')
        reference_action.trigger()
        self.app.processEvents()
        self.assertEqual(self.canvas.extent(), self.original)
        reference_action.deleteLater()
