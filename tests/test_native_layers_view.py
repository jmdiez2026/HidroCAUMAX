"""Native load command gets one overview; other navigation stays untouched."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QAction
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject, QgsRectangle, QgsVectorLayer, QgsUnitTypes,
                       QgsCategorizedSymbolRenderer, QgsRendererCategory,
                       QgsLineSymbol, QgsRenderContext)
from qgis.gui import QgsMapCanvas
import qgis.utils

from caumax_consulta.native_layers_view import (IBERIA_BOUNDS, NativeLayersView, terrain_background,
    TERRAIN_PROPERTY, style_query_network, NETWORK_PINK, NETWORK_LINE_WIDTH_MM,
    NETWORK_STYLE_REVISION)


class NativeLayersViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject.instance()
        self.before = set(self.project.mapLayers())
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
        self.project.removeMapLayers([key for key in self.project.mapLayers() if key not in self.before and key != (self.layer.id() if self.layer else '')])
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

    def test_terrain_switch_is_idempotent_and_keeps_other_layers_and_extent(self):
        self.native_load()
        ign=QgsVectorLayer('Point?crs=EPSG:25830','Mapas raster del IGN','memory')
        self.project.addMapLayer(ign,False)
        ign_node=self.group.addLayer(ign)
        self.assertTrue(terrain_background(self.project,self.group))
        first=[layer for layer in self.project.mapLayers().values() if layer.customProperty(TERRAIN_PROPERTY,False)]
        self.assertEqual(len(first),1)
        self.assertFalse(ign_node.itemVisibilityChecked())
        self.assertTrue(self.project.layerTreeRoot().findLayer(first[0].id()).itemVisibilityChecked())
        self.assertTrue(self.project.layerTreeRoot().findLayer(self.layer.id()).itemVisibilityChecked())
        self.assertEqual(self.canvas.extent(),self.original)
        terrain_background(self.project,self.group)
        self.assertEqual(len([layer for layer in self.project.mapLayers().values() if layer.customProperty(TERRAIN_PROPERTY,False)]),1)
        satellite=QgsVectorLayer('Point?crs=EPSG:25830','Google Satellite','memory')
        satellite.setCustomProperty('HidroCAUMAX/service_id','google_satellite')
        self.project.addMapLayer(satellite,False)
        self.group.addLayer(satellite).setItemVisibilityChecked(False)
        terrain_background(self.project,self.group)
        root=self.project.layerTreeRoot()
        self.assertEqual(root.children()[-2].layer(),first[0])
        self.assertEqual(root.children()[-1].layer(),satellite)
        self.assertFalse(root.children()[-1].itemVisibilityChecked())
        terrain_background(self.project,self.group)
        self.assertEqual(len([n for n in root.findLayers() if n.layerId()==satellite.id()]),1)

    @staticmethod
    def _categorized_native_network(name='Red 10 km²'):
        """CAUMAX red10km.qml: sup=1 is 10–50, sup=2 is >50 km²."""
        layer = QgsVectorLayer('MultiLineString?crs=EPSG:25830', name, 'memory')
        below = QgsLineSymbol.createSimple({
            'line_color': '35,35,35,255', 'line_width': '0.5',
            'line_width_unit': 'MM'})
        above = QgsLineSymbol.createSimple({
            'line_color': '192,223,255,255', 'line_width': '0.5',
            'line_width_unit': 'MM'})
        layer.setRenderer(QgsCategorizedSymbolRenderer('sup', [
            QgsRendererCategory('1', below, 'Red 10  - 50 km²'),
            QgsRendererCategory('2', above, 'Red > 50 km²'),
        ]))
        return layer

    def test_real_caumax_categorized_red50_is_only_one_styled_at_150_percent(self):
        official = self._categorized_native_network()
        other = QgsVectorLayer('LineString?crs=EPSG:25830', 'Ríos', 'memory')
        self.project.addMapLayer(official)
        self.project.addMapLayer(other)
        untouched = official.renderer().categories()[0].symbol()
        original_color = untouched.color().name()
        original_width = untouched.width()
        original_other_color = other.renderer().symbol().color().name()
        target_width = official.renderer().categories()[1].symbol().width()
        self.assertAlmostEqual(target_width, 0.50)
        style_query_network(self.project)
        renderer = official.renderer()
        self.assertIsInstance(renderer, QgsCategorizedSymbolRenderer)
        self.assertEqual(renderer.classAttribute(), 'sup')
        self.assertEqual(len(renderer.categories()), 2)
        first, second = [entry.symbol() for entry in renderer.categories()]
        self.assertEqual(first.color().name(), original_color)
        self.assertAlmostEqual(first.width(), original_width)
        self.assertEqual(second.color().name(), NETWORK_PINK)
        self.assertAlmostEqual(second.width(), target_width * 1.5)
        self.assertEqual(second.symbolLayer(0).widthUnit(),
                         QgsUnitTypes.RenderMillimeters)
        self.assertEqual(other.renderer().symbol().color().name(), original_other_color)
        self.assertEqual(official.customProperty('HidroCAUMAX/query_network_style'),
                         NETWORK_STYLE_REVISION)
        style_query_network(self.project)
        self.assertAlmostEqual(official.renderer().categories()[1].symbol().width(),
                               target_width * 1.5)
        # Native loader may replace renderer while leaving customProperties.
        reset = official.renderer().clone()
        reset.updateCategorySymbol(1, QgsLineSymbol.createSimple({
            'line_color':'192,223,255,255', 'line_width':'0.5',
            'line_width_unit':'MM'}))
        official.setRenderer(reset)
        style_query_network(self.project)
        first, second = [entry.symbol() for entry in official.renderer().categories()]
        self.assertEqual(first.color().name(), original_color)
        self.assertAlmostEqual(first.width(), original_width)
        self.assertEqual(second.color().name(), NETWORK_PINK)
        self.assertAlmostEqual(second.width(), target_width * 1.5)

    def test_caumax_red50_category_is_styled_on_layer_load(self):
        self.bridge.start()
        self.project.addMapLayer(self._categorized_native_network('Red 10 km2'))
        self.app.processEvents()
        actual = next(layer for layer in self.project.mapLayers().values()
                      if layer.name() == 'Red 10 km2')
        first, second = [item.symbol() for item in actual.renderer().categories()]
        self.assertAlmostEqual(first.width(), 0.5)
        self.assertEqual(first.color().name(), '#232323')
        self.assertEqual(second.color().name(), NETWORK_PINK)
        self.assertAlmostEqual(second.width(), 0.75)
        self.assertEqual(actual.customProperty('HidroCAUMAX/query_network_style'),
                         NETWORK_STYLE_REVISION)

    def test_only_named_50km_network_gets_pink_and_keeps_width_and_geometry(self):
        pink=QgsVectorLayer('LineString?crs=EPSG:25830','Red > 50 km²','memory')
        other=QgsVectorLayer('LineString?crs=EPSG:25830','Red 10 km²','memory')
        width=pink.renderer().symbol().width()
        color=other.renderer().symbol().color().name()
        for layer in (pink,other): self.project.addMapLayer(layer)
        style_query_network(self.project)
        self.assertEqual(pink.renderer().symbol().color().name(),NETWORK_PINK)
        self.assertAlmostEqual(pink.renderer().symbol().width(), NETWORK_LINE_WIDTH_MM)
        self.assertEqual(pink.renderer().symbol().symbolLayer(0).widthUnit(),
                         QgsUnitTypes.RenderMillimeters)
        self.assertEqual(pink.customProperty('HidroCAUMAX/query_network_style'),
                         NETWORK_STYLE_REVISION)
        self.assertNotEqual(pink.renderer().symbol().width(), width)
        self.assertEqual(other.renderer().symbol().color().name(),color)
        self.assertEqual(pink.featureCount(),0)
        # A layer already painted by the older pink-only release must receive
        # the new width when HidroCAUMAX is updated.
        pink.setCustomProperty('HidroCAUMAX/query_network_style', '')
        pink.setCustomProperty('HidroCAUMAX/query_network_pink', True)
        pink.renderer().symbol().setWidth(.26)
        style_query_network(self.project)
        self.assertAlmostEqual(pink.renderer().symbol().width(), NETWORK_LINE_WIDTH_MM)
        style_query_network(self.project)
        self.assertAlmostEqual(pink.renderer().symbol().width(), NETWORK_LINE_WIDTH_MM)
        # Native CAUMAX/QLR loading can overwrite a layer's renderer while its
        # HidroCAUMAX customProperty marker remains intact.
        renderer = pink.renderer().clone()
        renderer.symbol().setColor(QColor('#377eb8'))
        renderer.symbol().setWidth(0.25)
        pink.setRenderer(renderer)
        style_query_network(self.project)
        self.assertEqual(pink.renderer().symbol().color().name(), NETWORK_PINK)
        self.assertAlmostEqual(pink.renderer().symbol().width(), NETWORK_LINE_WIDTH_MM)

    def test_standalone_network_added_after_plugin_start_gets_violet_pink_075_mm(self):
        self.bridge.start()
        network = QgsVectorLayer('LineString?crs=EPSG:25830',
                                 'Red > 50 km2', 'memory')
        self.project.addMapLayer(network)
        self.app.processEvents()
        self.assertEqual(network.renderer().symbol().color().name(), NETWORK_PINK)
        self.assertAlmostEqual(network.renderer().symbol().width(), NETWORK_LINE_WIDTH_MM)
        self.assertEqual(network.renderer().symbol().symbolLayer(0).widthUnit(),
                         QgsUnitTypes.RenderMillimeters)
        self.assertEqual(network.customProperty('HidroCAUMAX/query_network_style'),
                         NETWORK_STYLE_REVISION)
