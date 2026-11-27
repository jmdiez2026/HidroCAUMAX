"""Frame Iberia after the explicit native CAUMAX layer-loading command."""
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsProject, QgsRectangle, QgsRasterLayer,
                       QgsDataSourceUri, QgsVectorLayer, QgsRenderContext, QgsWkbTypes,
                       QgsUnitTypes, QgsCategorizedSymbolRenderer)
import qgis.utils


# Geographic frame inferred from the requested overview: mainland Iberia,
# Balearic Islands and a small surrounding margin. QGIS fits the canvas ratio.
IBERIA_BOUNDS = (-11.0, 35.6, 5.5, 44.2)
TERRAIN_URL = 'https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}'
TERRAIN_PROPERTY = 'HidroCAUMAX/startup_terrain'
NETWORK_PINK = '#ff1493'
NETWORK_LINE_WIDTH_MM = 1.30
NETWORK_STYLE_REVISION = 'red50-pink-1.30mm-v1'


def place_satellite_below_terrain(project):
    root = project.layerTreeRoot()
    satellite = next((layer for layer in project.mapLayers().values()
        if layer.customProperty('HidroCAUMAX/service_id', '') == 'google_satellite'), None)
    if satellite is None:
        return
    node = root.findLayer(satellite.id())
    terrain = next((layer for layer in project.mapLayers().values()
        if layer.customProperty(TERRAIN_PROPERTY, False)
        or (layer.providerType() == 'wms' and QgsDataSourceUri(layer.source()).param('url') == TERRAIN_URL)), None)
    terrain_node = root.findLayer(terrain.id()) if terrain is not None else None
    target = root.children().index(terrain_node)+1 if terrain_node is not None and terrain_node.parent() is root else len(root.children())
    placed = node is not None and node.parent() is root and (
        root.children().index(node) == target if terrain_node is not None else root.children()[-1] is node)
    if node is not None and not placed:
        clone = node.clone()
        root.insertChildNode(target, clone)
        node.parent().removeChildNode(node)
    if terrain is not None and root.hasCustomLayerOrder():
        order = [layer for layer in root.customLayerOrder() if layer.id() != satellite.id()]
        position = next((i+1 for i, layer in enumerate(order) if layer.id() == terrain.id()), len(order))
        root.setCustomLayerOrder(order[:position]+[satellite]+order[position:])


def _network_symbol_matches(symbol):
    """Style is defined by the actual painted line, not a saved flag."""
    return bool(symbol and symbol.color().name().lower() == NETWORK_PINK
                and abs(symbol.width() - NETWORK_LINE_WIDTH_MM) <= 1e-6
                and symbol.symbolLayerCount() > 0
                and symbol.symbolLayer(0).widthUnit() == QgsUnitTypes.RenderMillimeters)


def _paint_network_symbol(symbol):
    symbol.setColor(QColor(NETWORK_PINK))
    symbol.setWidth(NETWORK_LINE_WIDTH_MM)
    symbol.setWidthUnit(QgsUnitTypes.RenderMillimeters)


def style_query_network(project):
    """Style only CAUMAX's >50 km² *category* in its Red 10 km² layer.

    CAUMAX provides one OGR layer named «Red 10 km²» with two categorized
    symbols by attribute 'sup'. The >50 km² entry is category value '2',
    not an independent QgsVectorLayer. Older HidroCAUMAX revisions targeted
    a nonexistent map-layer name; the actual blue category was left unchanged.
    """
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer.geometryType() != QgsWkbTypes.LineGeometry:
            continue
        name = ''.join(layer.name().lower().split()).replace('²', '2').replace('^2', '2')
        renderer = layer.renderer()
        if renderer is None:
            continue
        if name == 'red10km2' and isinstance(renderer, QgsCategorizedSymbolRenderer):
            if renderer.classAttribute().strip().lower() != 'sup':
                continue
            categories = renderer.categories()
            for index, category in enumerate(categories):
                label = ''.join(category.label().lower().split()).replace('²', '2').replace('^2', '2')
                if label != 'red>50km2' or str(category.value()) != '2':
                    continue
                symbol = category.symbol()
                if symbol is None:
                    break
                if _network_symbol_matches(symbol):
                    layer.setCustomProperty('HidroCAUMAX/query_network_style',
                                            NETWORK_STYLE_REVISION)
                    break
                new_renderer = renderer.clone()
                new_symbol = symbol.clone()
                _paint_network_symbol(new_symbol)
                new_renderer.updateCategorySymbol(index, new_symbol)
                layer.setRenderer(new_renderer)
                layer.setCustomProperty('HidroCAUMAX/query_network_pink', True)
                layer.setCustomProperty('HidroCAUMAX/query_network_style',
                                        NETWORK_STYLE_REVISION)
                layer.triggerRepaint()
                break
            continue
        # Older user projects can contain an independent line layer with
        # this name; retain compatibility without changing unrelated networks.
        if name != 'red>50km2':
            continue
        symbols = list(renderer.symbols(QgsRenderContext()))
        if not symbols:
            continue
        if all(_network_symbol_matches(symbol) for symbol in symbols):
            layer.setCustomProperty('HidroCAUMAX/query_network_style',
                                    NETWORK_STYLE_REVISION)
            continue
        new_renderer = renderer.clone()
        for symbol in new_renderer.symbols(QgsRenderContext()):
            _paint_network_symbol(symbol)
        layer.setRenderer(new_renderer)
        layer.setCustomProperty('HidroCAUMAX/query_network_pink', True)
        layer.setCustomProperty('HidroCAUMAX/query_network_style',
                                NETWORK_STYLE_REVISION)
        layer.triggerRepaint()

def terrain_background(project, native_group):
    """Switch the native overview background without changing extent or CRS."""
    root = project.layerTreeRoot()
    terrain = next((layer for layer in project.mapLayers().values()
        if layer.customProperty(TERRAIN_PROPERTY, False)
        or (layer.providerType() == 'wms' and QgsDataSourceUri(layer.source()).param('url') == TERRAIN_URL)), None)
    if terrain is None:
        uri = QgsDataSourceUri()
        for key, value in (('type','xyz'),('url',TERRAIN_URL),('zmin','0'),('zmax','19'),('crs','EPSG:3857')):
            uri.setParam(key,value)
        terrain = QgsRasterLayer(bytes(uri.encodedUri()).decode(), 'Google Terrain', 'wms')
        if not terrain.isValid():
            return False
        terrain.setCustomProperty(TERRAIN_PROPERTY, True)
        terrain.setAttribution('© Google y proveedores cartográficos')
        terrain.setAttributionUrl('https://maps.google.com')
        project.addMapLayer(terrain, False)
        root.addLayer(terrain)
    node = root.findLayer(terrain.id())
    if node is not None:
        node.setItemVisibilityChecked(True)
        node.setExpanded(False)
    for original in native_group.findLayers():
        if original.layer() and original.layer().name() == 'Mapas raster del IGN':
            original.setItemVisibilityChecked(False)
    if root.hasCustomLayerOrder():
        order = [layer for layer in root.customLayerOrder() if layer.id() != terrain.id()]
        root.setCustomLayerOrder(order + [terrain])
    place_satellite_below_terrain(project)
    return True


class NativeLayersView(QObject):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._running = False
        self._action = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(1000)
        self._scan_timer.timeout.connect(self._scan)
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._apply)
        self._style_timer = QTimer(self)
        self._style_timer.setSingleShot(True)
        self._style_timer.timeout.connect(self._style_loaded_network)

    def start(self):
        if self._running:
            return
        self._running = True
        QgsProject.instance().layersAdded.connect(self._schedule_style)
        self._scan()
        self._scan_timer.start()

    def stop(self):
        self._running = False
        self._scan_timer.stop()
        self._zoom_timer.stop()
        self._style_timer.stop()
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_style)
        except (RuntimeError, TypeError):
            pass
        self._disconnect()

    def _disconnect(self):
        action, self._action = self._action, None
        if action is not None and not sip.isdeleted(action):
            try:
                action.triggered.disconnect(self._loaded)
            except (RuntimeError, TypeError):
                pass

    def _schedule_style(self, *args):
        if self._running:
            # Let CAUMAX finish setting up its default renderer first.
            self._style_timer.start(0)

    def _style_loaded_network(self):
        if self._running:
            style_query_network(QgsProject.instance())

    def _scan(self):
        if not self._running:
            return
        style_query_network(QgsProject.instance())
        plugin = qgis.utils.plugins.get('caumax')
        action = getattr(plugin, 'actionLoadProject', None)
        if action is not None and sip.isdeleted(action):
            action = None
        if action is self._action:
            return
        self._zoom_timer.stop()
        self._disconnect()
        if action is not None:
            # Append an observer: do not replace CAUMAX's loader or its slots.
            action.triggered.connect(self._loaded)
            self._action = action

    def _loaded(self, *args):
        if self._running:
            # Wait for the native synchronous loader, including its group move.
            self._zoom_timer.start(0)

    def _apply(self):
        if not self._running or self._action is None or sip.isdeleted(self._action):
            return
        project = QgsProject.instance()
        group = project.layerTreeRoot().findGroup('CAUMAX_CAPAS')
        if group is None or not any(n.layer() and n.layer().isValid() for n in group.findLayers()):
            return
        terrain_background(project, group)
        style_query_network(project)
        canvas = self.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            return
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
                                               crs, project.transformContext())
            extent = transform.transformBoundingBox(QgsRectangle(*IBERIA_BOUNDS))
        except QgsCsException:
            return
        if extent.isEmpty() or not extent.isFinite():
            return
        canvas.setExtent(extent)
        canvas.refresh()
