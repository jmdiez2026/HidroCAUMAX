"""Owned QGIS background layers for the active native DEM."""
import math
from pathlib import Path
from uuid import uuid4

from qgis.PyQt import sip
from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry, QgsProject, QgsWkbTypes

from .layer_order import create_result_group, ensure_results_above_official
from .dem_display import apply_terrain_style, RESOLUTION_PROPERTY


_BASIN_BOUNDS_PROPERTY = 'hidrocaumax/dem_basin_bounds'
_BASIN_CRS_PROPERTY = 'hidrocaumax/dem_basin_crs'


def _native_resolution(width, height):
    return math.isclose(abs(float(width)), 500.) and math.isclose(abs(float(height)), 500.)


def remove_native_terrain(project=None):
    """Retire our obsolete 500 m background, preserving source data and user layers."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    owner = 'HidroCAUMAX/terrain_owner'
    ids = []
    for layer in project.mapLayers().values():
        resolution = layer.customProperty(RESOLUTION_PROPERTY, [])
        try:
            native = len(resolution) == 2 and _native_resolution(*resolution)
        except (TypeError, ValueError):
            native = False
        if layer.customProperty(owner, '') and native:
            ids.append(layer.id())
    groups = [g for g in root.findGroups() if g.customProperty(owner, '') and (
        any(n.layerId() in ids for n in g.findLayers()) or
        g.name() == 'HidroCAUMAX · MDT 500 × 500 m · Relieve')]
    project.removeMapLayers(ids)
    for group in reversed(groups):
        if sip.isdeleted(group) or group.parent() is None:
            continue
        parent = group.parent()
        index = parent.children().index(group)
        for child in list(group.children()):
            parent.insertChildNode(index, child.clone())
            index += 1
        parent.removeChildNode(group)
    return len(ids)


def _basin_context(analysis):
    """Return the active basin bounds in their own CRS, never a guessed extent."""
    raw = getattr(analysis, 'geometry_wkb', None)
    wkt = getattr(analysis, 'crs_wkt', None)
    if raw is None or not wkt:
        return None
    try:
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(raw))
        if (geometry.isNull() or geometry.isEmpty()
                or QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry
                or not geometry.isGeosValid()
                or not QgsCoordinateReferenceSystem.fromWkt(str(wkt)).isValid()):
            return None
        extent = geometry.boundingBox()
        bounds = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
        if (not all(math.isfinite(value) for value in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            return None
        return bounds, str(wkt)
    except (RuntimeError, TypeError, ValueError, OverflowError):
        return None


def _set_basin_context(layers, context):
    if context is None:
        return
    bounds, wkt = context
    for layer in layers:
        if sip.isdeleted(layer):
            continue
        for key, value in ((_BASIN_CRS_PROPERTY, wkt), (_BASIN_BOUNDS_PROPERTY, bounds)):
            if layer.customProperty(key) != value:
                # QGIS emits customPropertyChanged(key) and persists the value
                # in project files. Listeners can debounce both context fields.
                layer.setCustomProperty(key, value)


class TerrainLayers:
    """Keep the active colored DEM and hillshade as normal project layers."""

    def __init__(self, iface, retain_variants=False):
        self.iface = iface
        self.owner_key = uuid4().hex
        self.layers = []
        self._group = None
        self._signature = None
        self.note = ''
        self.retain_variants = bool(retain_variants)
        self._variants = {}

    def _discard_layers(self):
        project = QgsProject.instance()
        for layer in self.layers:
            if (not sip.isdeleted(layer)
                    and layer.customProperty('HidroCAUMAX/terrain_owner') == self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        self.layers = []
        self._signature = None

    def update(self, analysis, query):
        project = QgsProject.instance()
        ensure_results_above_official(project)
        context = _basin_context(analysis)
        # The user may retain a visible background at another resolution. Its
        # styling window must follow the active basin, without changing visibility.
        existing = list(self.layers)
        for variant in self._variants.values():
            existing.extend(variant['layers'])
        for layer in existing:
            if not sip.isdeleted(layer) and project.mapLayer(layer.id()) is layer:
                apply_terrain_style(layer)
        _set_basin_context((layer for layer in existing
                            if not sip.isdeleted(layer)
                            and layer.customProperty('HidroCAUMAX/terrain_owner') == self.owner_key
                            and project.mapLayer(layer.id()) is layer), context)
        if _native_resolution(analysis.cell_width, analysis.cell_height):
            remove_native_terrain(project)
            self.layers, self._group, self._signature, self.note = [], None, None, ''
            return []
        path = Path(query.dem_path)
        self.note = ''
        if not path.is_file():
            if not self.retain_variants:
                self._discard_layers()
            else:
                self.layers, self._group, self._signature = [], None, None
            self.note = ('La consulta conserva los resultados y su captura; el MDT no está '
                         'disponible para volver a dibujar el relieve en el mapa.')
            return []
        stat = path.stat()
        elevation_range = (analysis.z_min, analysis.z_max)
        signature = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, elevation_range)
        if self.retain_variants:
            # A selected calculation surface does not change the user's map
            # background or the visibility they chose in the layer tree.
            key = signature[:3]
            previous = self._variants.get(key)
            if previous and all(not sip.isdeleted(layer) and project.mapLayer(layer.id()) is layer
                                for layer in previous['layers']):
                self.layers, self._group, self._signature = previous['layers'], previous['group'], signature
                return list(self.layers)
            from .dem_display import build_terrain_layers
            layers = build_terrain_layers(str(path), elevation_range=elevation_range)
            _set_basin_context(layers, context)
            group = create_result_group(project,
                f'HidroCAUMAX · MDT {analysis.cell_width:g} × {analysis.cell_height:g} m · Relieve', 30)
            group.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            group.setExpanded(False)
            visible = False
            for index, layer in enumerate(layers):
                layer.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
                project.addMapLayer(layer, False)
                node = group.insertLayer(index, layer)
                node.setItemVisibilityChecked(visible)
                node.setExpanded(False)
            group.setItemVisibilityChecked(visible)
            self._variants[key] = dict(layers=layers, group=group)
            self.layers, self._group, self._signature = layers, group, signature
            ensure_results_above_official(project)
            return list(layers)
        if (signature == self._signature and len(self.layers) == 2
                and all(not sip.isdeleted(layer) and project.mapLayer(layer.id()) is layer
                        for layer in self.layers)):
            return list(self.layers)
        from .dem_display import build_terrain_layers
        # Build first so a bad raster does not remove a valid current background.
        layers = build_terrain_layers(str(path), elevation_range=elevation_range)
        _set_basin_context(layers, context)
        self._discard_layers()
        if self._group is None or sip.isdeleted(self._group):
            self._group = create_result_group(project, 'HidroCAUMAX · MDT y relieve', 30)
            self._group.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            self._group.setExpanded(False)
            self._group.setItemVisibilityChecked(False)
        self._group.setName(f'HidroCAUMAX · MDT {analysis.cell_width:g} × {analysis.cell_height:g} m · Relieve')
        for index, layer in enumerate(layers):
            layer.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            project.addMapLayer(layer, False)
            node = self._group.insertLayer(index, layer)
            node.setItemVisibilityChecked(False)
            node.setExpanded(False)
        self.layers = layers
        self._signature = signature
        ensure_results_above_official(project)
        return list(layers)

    def remove(self):
        """Explicit cleanup; normal dialog closure keeps the GIS result."""
        if self.retain_variants:
            for item in list(self._variants.values()):
                self.layers, self._group = item['layers'], item['group']
                self._discard_layers()
                if self._group is not None and not sip.isdeleted(self._group) and not self._group.children():
                    self._group.parent().removeChildNode(self._group)
            self._variants.clear()
            self._group = None
            return
        self._discard_layers()
        if self._group is not None and not sip.isdeleted(self._group) and not self._group.children():
            self._group.parent().removeChildNode(self._group)
        self._group = None
