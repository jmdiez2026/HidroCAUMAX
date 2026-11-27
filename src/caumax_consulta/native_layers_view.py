"""Frame Iberia after the explicit native CAUMAX layer-loading command."""
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsProject, QgsRectangle, QgsRasterLayer,
                       QgsCategorizedSymbolRenderer, QgsDataSourceUri, QgsVectorLayer,
                       QgsRenderContext, QgsWkbTypes,
                       QgsUnitTypes)
import qgis.utils


# Geographic frame inferred from the requested overview: mainland Iberia,
# Balearic Islands and a small surrounding margin. QGIS fits the canvas ratio.
IBERIA_BOUNDS = (-11.0, 35.6, 5.5, 44.2)
TERRAIN_URL = 'https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}'
TERRAIN_PROPERTY = 'HidroCAUMAX/startup_terrain'
# The stock CAUMAX red10km.qml classifies "sup" = 1 (10–50 km²) and
# "sup" = 2 (>50 km²). The second category's original width is 0.50 mm.
# Change ONLY that category: 150 % × 0.50 = 0.75 mm.
NETWORK_PINK = '#e000d4'  # vivid violet-pink
NETWORK_ORIGINAL_WIDTH_MM = 0.50
NETWORK_WIDTH_FACTOR = 1.50
NETWORK_LINE_WIDTH_MM = NETWORK_ORIGINAL_WIDTH_MM * NETWORK_WIDTH_FACTOR
NETWORK_STYLE_REVISION = 'caumax-red10-sup2-violetpink-150pct-v2'


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


def _normal_name(label):
    """Match QGIS' superscript ², ordinary 2 and spaces without locale bias."""
    return ''.join(str(label).casefold().split()).replace('²', '2').replace('^2', '2')


def _correct_network_symbol(symbol):
    """Compare the *actual* renderer; native CAUMAX may reset it at any time."""
    return (symbol is not None
            and symbol.color().name().lower() == NETWORK_PINK
            and abs(symbol.width() - NETWORK_LINE_WIDTH_MM) < 1e-6
            and symbol.symbolLayerCount() > 0
            and symbol.symbolLayer(0).widthUnit() == QgsUnitTypes.RenderMillimeters)


def _styled_network_symbol(symbol):
    symbol = symbol.clone()
    symbol.setColor(QColor(NETWORK_PINK))
    symbol.setWidth(NETWORK_LINE_WIDTH_MM)
    symbol.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    return symbol


def style_query_network(project):
    """Style only CAUMAX's "Red > 50 km²" category in "Red 10 km²".

    The CAUMAX QLR/QML does not register a separate layer named Red > 50 km²:
    the target is category value 2 of its "sup" classified vector layer.
    Keep the distinct 10–50 km² category, geometries, legend and attributes.
    Older standalone layers are supported for existing projects.
    """
    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or layer.geometryType() != QgsWkbTypes.LineGeometry:
            continue
        name = _normal_name(layer.name())
        renderer = layer.renderer()
        if renderer is None:
            continue
        if name == 'red10km2' and isinstance(renderer, QgsCategorizedSymbolRenderer):
            if renderer.classAttribute().strip().casefold() != 'sup':
                continue
            targets = [
                i for i, category in enumerate(renderer.categories())
                if (str(category.value()) == '2'
                    and _normal_name(category.label()) == 'red>50km2'
                    and category.symbol() is not None)
            ]
            if not targets:
                continue
            if any(not _correct_network_symbol(renderer.categories()[i].symbol())
                   for i in targets):
                modified = renderer.clone()
                for i in targets:
                    modified.updateCategorySymbol(
                        i, _styled_network_symbol(modified.categories()[i].symbol()))
                layer.setRenderer(modified)
                layer.triggerRepaint()
        elif name == 'red>50km2':
            # Compatibility with older imported projects where this category
            # was saved as its own line layer.
            symbols = list(renderer.symbols(QgsRenderContext()))
            if not symbols:
                continue
            if any(not _correct_network_symbol(symbol) for symbol in symbols):
                modified = renderer.clone()
                for symbol in modified.symbols(QgsRenderContext()):
                    symbol.setColor(QColor(NETWORK_PINK))
                    symbol.setWidth(NETWORK_LINE_WIDTH_MM)
                    symbol.setWidthUnit(QgsUnitTypes.RenderMillimeters)
                layer.setRenderer(modified)
                layer.triggerRepaint()
        else:
            continue
        if layer.customProperty('HidroCAUMAX/query_network_style', '') != NETWORK_STYLE_REVISION:
            layer.setCustomProperty('HidroCAUMAX/query_network_style', NETWORK_STYLE_REVISION)
        layer.setCustomProperty('HidroCAUMAX/query_network_pink', True)

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
