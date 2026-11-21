"""Owned QGIS background layers for the active native DEM."""
from pathlib import Path
from uuid import uuid4

from qgis.PyQt import sip
from qgis.core import QgsProject

from .layer_order import create_result_group, ensure_results_above_official


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
        ensure_results_above_official(QgsProject.instance())
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
        project = QgsProject.instance()
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
            group = create_result_group(project,
                f'HidroCAUMAX · MDT {analysis.cell_width:g} × {analysis.cell_height:g} m · Relieve', 30)
            group.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            group.setExpanded(False)
            visible = not bool(self._variants)
            for index, layer in enumerate(layers):
                layer.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
                project.addMapLayer(layer, False)
                group.insertLayer(index, layer).setItemVisibilityChecked(visible)
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
        self._discard_layers()
        if self._group is None or sip.isdeleted(self._group):
            self._group = create_result_group(project, 'HidroCAUMAX · MDT y relieve', 30)
            self._group.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            self._group.setExpanded(False)
        self._group.setName(f'HidroCAUMAX · MDT {analysis.cell_width:g} × {analysis.cell_height:g} m · Relieve')
        for index, layer in enumerate(layers):
            layer.setCustomProperty('HidroCAUMAX/terrain_owner', self.owner_key)
            project.addMapLayer(layer, False)
            self._group.insertLayer(index, layer)
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
