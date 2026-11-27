"""Durable, reusable watershed outlines backed by profile-local GeoPackages."""
import hashlib
import json
import math
from pathlib import Path
import time

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsField, QgsFillSymbol, QgsGeometry, QgsProject,
                       QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)

from .layer_order import activate_result_group, create_result_group, ensure_results_above_official, ORDER_PROPERTY

OWNER_PROPERTY = 'HidroCAUMAX/boundary_owner'
KIND_PROPERTY = 'HidroCAUMAX/boundary_kind'
QUERY_PROPERTY = 'HidroCAUMAX/boundary_query'
UPDATED_PROPERTY = 'HidroCAUMAX/boundary_updated_ns'
_NAMES = {'original': 'Divisoria original', 'suavizada': 'Divisoria suavizada'}


def boundary_display_name(analysis):
    """Short, spatial-resolution-only name for our QGIS watershed layers."""
    return (f'HidroCAUMAX · Divisoria '
            f'{float(analysis.cell_width):g}x{float(analysis.cell_height):g} m')


def storage_directory():
    """Use durable QGIS profile data, not the operating system temporary folder."""
    return Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'boundaries'


def boundary_key(analysis, query):
    crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
    query_crs = getattr(query, 'crs', crs)
    # A downloaded/clipped/TauDEM MDT can have a different cache path while
    # representing the same calculation point and resolution.
    identity = [crs.authid() or crs.toWkt(), query_crs.authid() or query_crs.toWkt(),
                float(query.x), float(query.y), float(analysis.outlet_x), float(analysis.outlet_y),
                float(analysis.cell_width), float(analysis.cell_height)]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode('utf-8')).hexdigest()[:32]


def _is_25m(width, height):
    try:
        return math.isclose(abs(float(width)), 25.) and math.isclose(abs(float(height)), 25.)
    except (TypeError, ValueError):
        return False


def _managed_25m(project):
    for layer in project.mapLayers().values():
        if (not isinstance(layer, QgsVectorLayer) or not layer.isValid()
                or not layer.customProperty(OWNER_PROPERTY, '')
                or layer.customProperty(KIND_PROPERTY) not in _NAMES
                or any(layer.fields().indexFromName(name) < 0 for name in ('RES_X_M', 'RES_Y_M'))
                or layer.featureCount() != 1):
            continue
        feature = next(layer.getFeatures(), None)
        if feature is not None and _is_25m(feature['RES_X_M'], feature['RES_Y_M']):
            yield layer


def _keep_current_25m(project, preferred):
    """Keep the last 25 m result, including only its optional smoothed outline."""
    root = project.layerTreeRoot()
    candidates = list(_managed_25m(project))
    if any(layer.isEditable() for layer in candidates):
        return 0
    def priority(layer):
        try:
            updated = int(layer.customProperty(UPDATED_PROPERTY, '0'))
        except (TypeError, ValueError):
            updated = 0
        node = root.findLayer(layer.id())
        return (layer.id() not in preferred, -updated,
                not bool(node and node.isVisible()), layer.customProperty(KIND_PROPERTY) != 'original')
    candidates.sort(key=priority)
    if not candidates:
        return 0
    current = candidates[0].customProperty(OWNER_PROPERTY)
    kept, removed = {}, []
    for layer in candidates:
        kind = layer.customProperty(KIND_PROPERTY)
        if layer.customProperty(OWNER_PROPERTY) != current or kind in kept:
            removed.append(layer.id())
        else:
            kept[kind] = layer
    project.removeMapLayers(removed)
    # A saved project can contain multiple legend nodes for the same layer ID.
    for layer in kept.values():
        nodes = root.findLayers()
        nodes = [node for node in nodes if node.layerId() == layer.id()]
        nodes.sort(key=lambda node: (node.parent().customProperty(OWNER_PROPERTY) != current,
                                    not node.isVisible()))
        for node in nodes[1:]:
            node.parent().removeChildNode(node)
    return len(removed)


def deduplicate_boundaries(project=None, preferred_ids=()):
    """Retire owned copies and older 25 m results, keeping all GeoPackages."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    preferred = set(preferred_ids)
    candidates = [layer for layer in project.mapLayers().values()
                  if isinstance(layer, QgsVectorLayer) and layer.isValid()
                  and not layer.isEditable() and layer.customProperty(OWNER_PROPERTY, '')
                  and layer.customProperty(KIND_PROPERTY) in _NAMES]
    def priority(layer):
        node = root.findLayer(layer.id())
        return (layer.id() not in preferred, not bool(node and node.isVisible()))
    candidates.sort(key=priority)
    kept = {}
    duplicates = []
    fields = ('TIPO', 'GRADO', 'RES_X_M', 'RES_Y_M', 'X_CIERRE', 'Y_CIERRE')
    for layer in candidates:
        if any(layer.fields().indexFromName(name) < 0 for name in fields):
            continue
        records = list(layer.getFeatures())
        if len(records) != 1:
            continue
        feature = records[0]
        if _is_25m(feature['RES_X_M'], feature['RES_Y_M']):
            continue  # The current 25 m query is selected below, even if its geometry differs.
        geometry = QgsGeometry(feature.geometry())
        if geometry.isNull() or geometry.isEmpty():
            continue
        geometry.convertToMultiType()
        geometry.normalize()
        signature = (layer.crs().authid() or layer.crs().toWkt(),
                     str(layer.customProperty(KIND_PROPERTY)),
                     tuple(feature[name] for name in fields), bytes(geometry.asWkb()))
        query = layer.customProperty(QUERY_PROPERTY, '')
        equivalent = next((other for other in kept.get(signature, [])
                           if not query or not other.customProperty(QUERY_PROPERTY, '')
                           or query == other.customProperty(QUERY_PROPERTY)), None)
        if equivalent is not None:
            duplicates.append(layer.id())
        else:
            kept.setdefault(signature, []).append(layer)
    if duplicates:
        project.removeMapLayers(duplicates)
    removed_25m = _keep_current_25m(project, preferred)
    for group in reversed(root.findGroups()):
        if group.customProperty(OWNER_PROPERTY, '') and not group.children():
            group.parent().removeChildNode(group)
    return len(duplicates) + removed_25m


def boundary_symbol(kind):
    return QgsFillSymbol.createSimple({
        'style': 'no', 'outline_style': 'solid',
        'outline_color': '190,0,0,220' if kind == 'original' else '0,80,210,255',
        'outline_width': '0.5' if kind == 'original' else '0.75',
        'outline_width_unit': 'MM', 'joinstyle': 'round'})


def _polygon(geometry):
    result = QgsGeometry(geometry)
    if (result.isNull() or result.isEmpty()
            or QgsWkbTypes.geometryType(result.wkbType()) != QgsWkbTypes.PolygonGeometry
            or not result.isGeosValid()):
        raise ValueError('La divisoria debe ser un polígono válido y no vacío.')
    result.convertToMultiType()
    return result


class BoundaryLayers:
    """Durable results; only the current 25 m query stays loaded in QGIS."""
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else storage_directory()
        self.layers = {}
        self.key = None
        self._result_keys = set()

    @staticmethod
    def _existing(key):
        return {str(layer.customProperty(KIND_PROPERTY)): layer
                for layer in QgsProject.instance().mapLayers().values()
                if isinstance(layer, QgsVectorLayer) and layer.isValid()
                and layer.customProperty(OWNER_PROPERTY) == key
                and layer.customProperty(KIND_PROPERTY) in _NAMES}

    def existing_state(self, analysis, query):
        """Read legend choices before a newly opened dialog applies defaults."""
        layers = self._existing(boundary_key(analysis, query))
        if not layers:
            return None
        root = QgsProject.instance().layerTreeRoot()
        flags = {kind: bool(root.findLayer(layer.id()).itemVisibilityChecked())
                 for kind, layer in layers.items() if root.findLayer(layer.id()) is not None}
        degree = None
        smooth = layers.get('suavizada')
        if smooth is not None:
            feature = next(smooth.getFeatures(), None)
            if feature is not None:
                degree = feature['GRADO']
        return dict(visibility=flags, degree=degree)

    def _open_or_create(self, key, kind, crs, geometry, attributes):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (key + '_' + kind + '.gpkg')
        if not path.exists():
            memory = QgsVectorLayer('MultiPolygon', _NAMES[kind], 'memory')
            memory.setCrs(crs)
            memory.dataProvider().addAttributes([
                QgsField('TIPO', QVariant.String), QgsField('GRADO', QVariant.Int),
                QgsField('RES_X_M', QVariant.Double), QgsField('RES_Y_M', QVariant.Double),
                QgsField('X_CIERRE', QVariant.Double), QgsField('Y_CIERRE', QVariant.Double)])
            memory.updateFields()
            feature = QgsFeature(memory.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(attributes)
            ok, _ = memory.dataProvider().addFeatures([feature])
            if not ok:
                raise ValueError('No se puede preparar la divisoria persistente.')
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = 'divisoria'
            options.fileEncoding = 'UTF-8'
            options.forceMulti = True
            result = QgsVectorFileWriter.writeAsVectorFormatV3(
                memory, str(path), QgsProject.instance().transformContext(), options)
            if result[0] != QgsVectorFileWriter.NoError:
                raise ValueError('No se puede guardar la divisoria persistente: ' + str(result[1]))
        layer = QgsVectorLayer(str(path) + '|layername=divisoria', _NAMES[kind], 'ogr')
        if not layer.isValid() or layer.crs() != crs:
            raise ValueError('No se puede abrir la divisoria persistente con su CRS.')
        layer.setCustomProperty(OWNER_PROPERTY, key)
        layer.setCustomProperty(KIND_PROPERTY, kind)
        layer.setCustomProperty(ORDER_PROPERTY, 20)
        layer.renderer().setSymbol(boundary_symbol(kind))
        return layer

    @staticmethod
    def _populate(layer, geometry, attributes):
        previous = list(layer.getFeatures())
        if (len(previous) == 1 and bytes(previous[0].geometry().asWkb()) == bytes(geometry.asWkb())
                and previous[0].attributes()[1:] == attributes):
            return
        if layer.isEditable():
            raise ValueError('Finaliza la edición de la divisoria antes de actualizarla.')
        if not layer.startEditing():
            raise ValueError('No se puede editar la divisoria persistente.')
        try:
            # GeoPackage adds its primary-key field before our attributes.
            indices = [layer.fields().indexFromName(name) for name in
                       ('TIPO', 'GRADO', 'RES_X_M', 'RES_Y_M', 'X_CIERRE', 'Y_CIERRE')]
            if -1 in indices:
                raise ValueError('Los atributos de la divisoria guardada no son compatibles.')
            if len(previous) == 1:
                fid = previous[0].id()
                ok = layer.changeGeometry(fid, geometry)
                ok = all(layer.changeAttributeValue(fid, index, value)
                         for index, value in zip(indices, attributes)) and ok
            else:
                ok = layer.deleteFeatures([feature.id() for feature in previous])
                feature = QgsFeature(layer.fields())
                feature.setGeometry(geometry)
                for index, value in zip(indices, attributes):
                    feature.setAttribute(index, value)
                ok = layer.addFeature(feature) and ok
            if not ok or not layer.commitChanges():
                raise ValueError('No se puede actualizar la divisoria: ' + '; '.join(layer.commitErrors()))
        except Exception:
            layer.rollBack()
            raise
        layer.updateExtents()
        layer.triggerRepaint()

    def update(self, analysis, query, original, smooth=None, *, degree=0, visibility=None):
        project = QgsProject.instance()
        key = boundary_key(analysis, query)
        crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
        if not crs.isValid():
            raise ValueError('El CRS de la divisoria no es válido.')
        geometries = {'original': _polygon(original)}
        if smooth is not None:
            geometries['suavizada'] = _polygon(smooth)
        self._result_keys.add(key)
        layers = self._existing(key)
        if _is_25m(analysis.cell_width, analysis.cell_height):
            if any(layer.isEditable() and layer not in layers.values()
                   for layer in _managed_25m(project)):
                raise ValueError('Finaliza la edición de la divisoria de 25 m antes de sustituirla.')
        root = project.layerTreeRoot()
        group = next((node for node in root.children() if node.customProperty(OWNER_PROPERTY) == key), None)
        title = boundary_display_name(analysis)
        updated = str(time.time_ns())
        for kind, geometry in geometries.items():
            attributes = [kind, int(degree) if kind == 'suavizada' else 0,
                          analysis.cell_width, analysis.cell_height, analysis.outlet_x, analysis.outlet_y]
            layer = layers.get(kind)
            if layer is None:
                layer = self._open_or_create(key, kind, crs, geometry, attributes)
            self._populate(layer, geometry, attributes)
            layer.setCustomProperty(QUERY_PROPERTY, key)
            layer.setCustomProperty(UPDATED_PROPERTY, updated)
            layer.setName(title + (f' · suavizada (grado {int(degree)})'
                                   if kind == 'suavizada' else ''))
            layers[kind] = layer
        # Prepare every geometry before publishing a replacement in the tree.
        # A failed new result must leave the previous loaded result intact.
        if group is None:
            group = create_result_group(project, title, 20)
            group.setCustomProperty(OWNER_PROPERTY, key)
        else:
            group.setName(title)
        for layer in layers.values():
            if project.mapLayer(layer.id()) is None:
                project.addMapLayer(layer, False)
            if root.findLayer(layer.id()) is None:
                group.addLayer(layer).setExpanded(False)
        self.layers, self.key = layers, key
        self.set_visibility(visibility or {'original': True, 'suavizada': smooth is not None})
        deduplicate_boundaries(project, [layer.id() for layer in layers.values()])
        activate_result_group(project, group, OWNER_PROPERTY)
        ensure_results_above_official(project)
        return dict(layers)

    def set_visibility(self, flags):
        root = QgsProject.instance().layerTreeRoot()
        for kind, layer in self.layers.items():
            if sip.isdeleted(layer):
                continue
            node = root.findLayer(layer.id())
            if node is not None and kind in flags:
                node.setItemVisibilityChecked(bool(flags[kind]))

    def remove(self):
        """Remove this result's outlines, including earlier outlet/resolution variants.

        Keep the stored GeoPackages and unrelated project layers. Groups that
        contain layers added by the user remain in the tree, even if they also
        used to contain one of our outlines.
        """
        project = QgsProject.instance()
        keys = self._result_keys | ({self.key} if self.key else set())
        ids = [layer.id() for layer in project.mapLayers().values()
               if isinstance(layer, QgsVectorLayer)
               and layer.customProperty(OWNER_PROPERTY) in keys
               and layer.customProperty(KIND_PROPERTY) in _NAMES]
        if ids:
            project.removeMapLayers(ids)
        # findGroups also includes groups the user has moved below another one.
        for group in reversed(project.layerTreeRoot().findGroups()):
            if (not sip.isdeleted(group) and group.customProperty(OWNER_PROPERTY) in keys
                    and not group.children()):
                parent = group.parent()
                if parent is not None:
                    parent.removeChildNode(group)
        self.layers = {}
        self.key = None
        self._result_keys.clear()
        return len(ids)
