"""Durable, reusable watershed outlines backed by profile-local GeoPackages."""
import hashlib
import json
import os
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsField, QgsFillSymbol, QgsGeometry, QgsProject,
                       QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)

from .layer_order import create_result_group, ensure_results_above_official, ORDER_PROPERTY

OWNER_PROPERTY = 'HidroCAUMAX/boundary_owner'
KIND_PROPERTY = 'HidroCAUMAX/boundary_kind'
_NAMES = {'original': 'Divisoria original', 'suavizada': 'Divisoria suavizada'}


def storage_directory():
    """Use durable QGIS profile data, not the operating system temporary folder."""
    return Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'boundaries'


def boundary_key(analysis, query):
    crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
    query_crs = getattr(query, 'crs', crs)
    identity = [os.path.normcase(os.path.abspath(str(query.dem_path))),
                crs.authid() or crs.toWkt(), query_crs.authid() or query_crs.toWkt(),
                float(query.x), float(query.y), float(analysis.outlet_x), float(analysis.outlet_y),
                float(analysis.cell_width), float(analysis.cell_height)]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode('utf-8')).hexdigest()[:32]


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
    """One pair per query/DEM; dialog disposal never removes project layers."""
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else storage_directory()
        self.layers = {}
        self.key = None

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
        layers = self._existing(key)
        root = project.layerTreeRoot()
        group = next((node for node in root.children() if node.customProperty(OWNER_PROPERTY) == key), None)
        for kind, geometry in geometries.items():
            attributes = [kind, int(degree) if kind == 'suavizada' else 0,
                          analysis.cell_width, analysis.cell_height, analysis.outlet_x, analysis.outlet_y]
            layer = layers.get(kind)
            if layer is None:
                layer = self._open_or_create(key, kind, crs, geometry, attributes)
            self._populate(layer, geometry, attributes)
            layer.setName(_NAMES[kind] + (f' · grado {int(degree)}' if kind == 'suavizada' else ''))
            layers[kind] = layer
            if group is None:
                group = create_result_group(project,
                    f'HidroCAUMAX · Divisoria [X={analysis.outlet_x:g}; Y={analysis.outlet_y:g}; '
                    f'{analysis.cell_width:g}×{analysis.cell_height:g} m]', 20)
                group.setCustomProperty(OWNER_PROPERTY, key)
            if project.mapLayer(layer.id()) is None:
                project.addMapLayer(layer, False)
            if root.findLayer(layer.id()) is None:
                group.addLayer(layer)
        self.layers, self.key = layers, key
        self.set_visibility(visibility or {'original': True, 'suavizada': smooth is not None})
        ensure_results_above_official(project)
        return dict(layers)

    def set_visibility(self, flags):
        root = QgsProject.instance().layerTreeRoot()
        for kind, layer in self.layers.items():
            node = root.findLayer(layer.id())
            if node is not None and kind in flags:
                node.setItemVisibilityChecked(bool(flags[kind]))
