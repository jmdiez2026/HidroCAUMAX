"""Persistent QGIS layers for the original D8 course and its 10–85% reach."""

from bisect import bisect_right
import math
from uuid import uuid4

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsGeometry,
    QgsLineSymbol, QgsPointXY, QgsProject, QgsSimpleLineSymbolLayer, QgsVectorLayer, QgsWkbTypes,
)

from .layer_order import activate_result_group, create_result_group, ensure_results_above_official


_KINDS = ('main', 'central')
_DEFAULT_VISIBILITY = {'main': True, 'central': False}
_OWNER_PROPERTY = 'HidroCAUMAX/course_owner'
_COURSE_PREFIX = 'HidroCAUMAX · Curso principal '
_NAMES = {
    'main': 'Curso principal total (L)',
    'central': 'Tramo del curso principal 10–85 % (0,75 L)',
}


def _course_group_name(width, height):
    """Short, stable legend title without point coordinates or cell indices."""
    x, y = float(width), float(height)
    if not all(math.isfinite(value) and value > 0 for value in (x, y)):
        raise ValueError('La resolución del curso principal debe ser positiva.')
    return f'{_COURSE_PREFIX}{x:g}x{y:g}'


def migrate_course_display_names(project=None):
    """Rename old plugin-owned groups on QGIS project load; never touch data.

    Older projects embedded outlet X/Y and Q/MDT cell in their legend.
    Prefer the exact resolution recorded in an owned layer feature; if its
    in-memory data did not survive project reopening, recover only the
    resolution suffix of the old owned group title. Never touch user groups.
    """
    project = project or QgsProject.instance()
    changed = 0
    for group in project.layerTreeRoot().findGroups(recursive=True):
        if sip.isdeleted(group):
            continue
        owner = group.customProperty(_OWNER_PROPERTY, '')
        if not owner or not group.name().startswith('HidroCAUMAX · Curso principal'):
            continue
        wanted = None
        for node in group.findLayers():
            layer = node.layer()
            if (layer is None or sip.isdeleted(layer)
                    or layer.customProperty(_OWNER_PROPERTY, '') != owner
                    or not isinstance(layer, QgsVectorLayer)
                    or not {'RES_X_M', 'RES_Y_M'}.issubset(layer.fields().names())):
                continue
            feature = next(layer.getFeatures(), None)
            if feature is None:
                continue
            try:
                wanted = _course_group_name(feature['RES_X_M'], feature['RES_Y_M'])
                break
            except (TypeError, ValueError):
                continue
        if wanted is None:
            # Empty memory layers restored from QGIS can still have an old
            # generated title such as '[X=...; Y=...; celda Q ...; 500×500 m]'.
            # Only plugin-owned groups are eligible for this fallback.
            suffix = group.name().rsplit(';', 1)[-1].strip()
            if suffix.endswith(' m]'):
                dimensions = suffix[:-3].replace('×', 'x').split('x')
                if len(dimensions) == 2:
                    try:
                        wanted = _course_group_name(*map(float, dimensions))
                    except (TypeError, ValueError):
                        pass
        if wanted is not None and group.name() != wanted:
            group.setName(wanted)
            changed += 1
    return changed


def course_symbol(kind):
    """One legible light-blue course style shared by GIS layers and reports."""
    if kind not in _KINDS:
        raise ValueError('Tipo de curso principal desconocido.')
    style = 'solid' if kind == 'main' else 'dash'
    common = {'line_width_unit': 'MM', 'line_style': style,
              'capstyle': 'round', 'joinstyle': 'round'}
    if kind == 'central':
        common.update({'use_custom_dash': '1', 'customdash': '4;2', 'customdash_unit': 'MM'})
    symbol = QgsLineSymbol.createSimple(dict(common, line_color='22,68,107,255', line_width='1.5'))
    foreground = QgsSimpleLineSymbolLayer.create(dict(common,
        line_color='101,199,255,255' if kind == 'main' else '166,231,255,255', line_width='1.1'))
    symbol.appendSymbolLayer(foreground)
    return symbol


commoncourse_symbol = course_symbol


def _point_at(points, cumulative, distance):
    if distance <= 0:
        return QgsPointXY(points[0])
    if distance >= cumulative[-1]:
        return QgsPointXY(points[-1])
    index = bisect_right(cumulative, distance) - 1
    fraction = ((distance - cumulative[index]) /
                (cumulative[index + 1] - cumulative[index]))
    first, last = points[index], points[index + 1]
    return QgsPointXY(first.x() + fraction * (last.x() - first.x()),
                      first.y() + fraction * (last.y() - first.y()))


def _course_geometries(analysis):
    """Copy the 2D course and interpolate the two fractional chainages."""
    geometry = QgsGeometry()
    wkb = getattr(analysis, 'main_course_wkb', b'')
    if not wkb:
        return {'main': None, 'central': None}
    geometry.fromWkb(bytes(wkb))
    if geometry.isNull():
        raise ValueError('No se puede leer el trazado del curso principal.')
    if QgsWkbTypes.flatType(geometry.wkbType()) != QgsWkbTypes.LineString:
        raise ValueError('El curso principal debe ser una única línea.')
    if geometry.isEmpty():
        return {'main': None, 'central': None}
    points = [QgsPointXY(point) for point in geometry.asPolyline()]
    if not all(math.isfinite(point.x()) and math.isfinite(point.y()) for point in points):
        raise ValueError('El curso principal contiene coordenadas no finitas.')
    cumulative = [0.0]
    for first, last in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + math.hypot(last.x() - first.x(), last.y() - first.y()))
    if len(points) < 2 or cumulative[-1] == 0:
        return {'main': None, 'central': None}
    if (not math.isclose(points[0].x(), analysis.outlet_x, rel_tol=0, abs_tol=1e-7)
            or not math.isclose(points[0].y(), analysis.outlet_y, rel_tol=0, abs_tol=1e-7)):
        raise ValueError('El curso principal debe comenzar en el centro de la celda de cierre.')
    main = QgsGeometry.fromPolylineXY(points)
    expected = (getattr(analysis, 'values', {}) or {}).get('main_length_km')
    if expected is not None and not math.isclose(main.length() / 1000, float(expected),
                                                rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError('La longitud del trazado no coincide con L del análisis.')
    start, stop = .10 * cumulative[-1], .85 * cumulative[-1]
    central_points = [_point_at(points, cumulative, start)]
    central_points.extend(QgsPointXY(point) for point, distance in zip(points, cumulative)
                          if start < distance < stop)
    central_points.append(_point_at(points, cumulative, stop))
    central = QgsGeometry.fromPolylineXY(central_points)
    return {'main': main, 'central': central}


class CourseLayers:
    """Keep two owned, durable memory layers without changing the map view.

    Normal updates retain layer IDs and legend checks. The optional visibility
    mapping restores explicit session choices. Destruction or dialog closure
    does not remove the results; removal is an explicit operation.
    """

    def __init__(self, iface, owner_key=None):
        self.iface = iface
        self.owner_key = str(owner_key) if owner_key is not None else uuid4().hex
        self.layers = {}
        self._group = None
        self._visibility = dict(_DEFAULT_VISIBILITY)
        self.note = ''

    def visibility(self):
        project = QgsProject.instance()
        for kind, layer in self.layers.items():
            if not sip.isdeleted(layer):
                node = project.layerTreeRoot().findLayer(layer.id())
                if node is not None:
                    self._visibility[kind] = node.itemVisibilityChecked()
        return dict(self._visibility)

    def _new_layer(self, kind, crs):
        layer = QgsVectorLayer('LineString', _NAMES[kind], 'memory')
        if not layer.isValid():
            raise ValueError('No se puede crear la capa del curso principal.')
        layer.setCrs(crs)
        layer.setCustomProperty(_OWNER_PROPERTY, self.owner_key)
        fields = [QgsField('TIPO', QVariant.String, '', 24)]
        fields.extend(QgsField(name, QVariant.Double, '', 32, 10) for name in (
            'L_KM', 'RES_X_M', 'RES_Y_M', 'X_CIERRE', 'Y_CIERRE', 'T_ANOS', 'Q_M3S',
        ))
        if not layer.dataProvider().addAttributes(fields):
            raise ValueError('No se pueden crear los atributos del curso principal.')
        layer.updateFields()
        layer.renderer().setSymbol(course_symbol(kind))
        return layer

    def _populate(self, layer, geometry, attributes, crs):
        provider = layer.dataProvider()
        previous = list(layer.getFeatures())
        if geometry is None:
            if previous and not provider.deleteFeatures([feature.id() for feature in previous]):
                raise ValueError('No se puede vaciar la capa del curso principal.')
        elif len(previous) == 1:
            feature = previous[0]
            changes = dict(enumerate(attributes))
            if (not provider.changeGeometryValues({feature.id(): geometry})
                    or not provider.changeAttributeValues({feature.id(): changes})):
                raise ValueError('No se puede actualizar la capa del curso principal.')
        else:
            if previous and not provider.deleteFeatures([feature.id() for feature in previous]):
                raise ValueError('No se puede actualizar la capa del curso principal.')
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(attributes)
            added, _ = provider.addFeatures([feature])
            if not added:
                raise ValueError('No se puede representar el curso principal.')
        layer.setCrs(crs)
        layer.updateExtents()
        layer.triggerRepaint()

    def update(self, analysis, query, visibility=None):
        """Update both original-grid courses in place and return their layers."""
        crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
        if not crs.isValid():
            raise ValueError('El sistema de referencia del curso principal no es válido.')
        geometries = _course_geometries(analysis)
        flags = self.visibility()
        if visibility is not None:
            flags.update({kind: bool(visibility[kind]) for kind in _KINDS if kind in visibility})
        project = QgsProject.instance()
        ensure_results_above_official(project)
        new_layers = {}
        for kind in _KINDS:
            layer = self.layers.get(kind)
            if layer is None or sip.isdeleted(layer) or project.mapLayer(layer.id()) is not layer:
                layer = self._new_layer(kind, crs)
                new_layers[kind] = layer
            geometry = geometries[kind]
            attributes = [
                'total' if kind == 'main' else 'tramo_10_85',
                geometry.length() / 1000 if geometry is not None else None,
                analysis.cell_width, analysis.cell_height, analysis.outlet_x, analysis.outlet_y,
                query.period, query.flow,
            ]
            self._populate(layer, geometry, attributes, crs)
            self.layers[kind] = layer
        if self._group is None or sip.isdeleted(self._group):
            self._group = create_result_group(project, 'HidroCAUMAX · Curso principal', 0)
            self._group.setCustomProperty(_OWNER_PROPERTY, self.owner_key)
        self._group.setName(_course_group_name(analysis.cell_width, analysis.cell_height))
        for kind in _KINDS:
            layer = self.layers[kind]
            if kind in new_layers:
                project.addMapLayer(layer, False)
                self._group.insertLayer(0, layer)
            node = project.layerTreeRoot().findLayer(layer.id())
            if node is None:
                node = self._group.insertLayer(0, layer)
            node.setItemVisibilityChecked(flags[kind])
        self._visibility = flags
        activate_result_group(project, self._group, _OWNER_PROPERTY)
        ensure_results_above_official(project)
        self.note = ('La cuenca no contiene un curso principal de longitud positiva; '
                     'las dos capas están vacías.' if geometries['main'] is None else '')
        return dict(self.layers)

    def remove(self):
        """Explicitly remove only this instance's layers and its empty group."""
        project = QgsProject.instance()
        for layer in self.layers.values():
            if (not sip.isdeleted(layer)
                    and layer.customProperty(_OWNER_PROPERTY) == self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        self.layers = {}
        if (self._group is not None and not sip.isdeleted(self._group)
                and not self._group.children()):
            parent = self._group.parent()
            if parent is not None:
                parent.removeChildNode(self._group)
        self._group = None
, group.name())
            if match:
                try:
                    wanted = _course_group_name(*map(float, match.groups()))
                except ValueError:
                    pass
        if wanted is not None and group.name() != wanted:
            group.setName(wanted)
            changed += 1
    return changed


def course_symbol(kind):
    """One legible light-blue course style shared by GIS layers and reports."""
    if kind not in _KINDS:
        raise ValueError('Tipo de curso principal desconocido.')
    style = 'solid' if kind == 'main' else 'dash'
    common = {'line_width_unit': 'MM', 'line_style': style,
              'capstyle': 'round', 'joinstyle': 'round'}
    if kind == 'central':
        common.update({'use_custom_dash': '1', 'customdash': '4;2', 'customdash_unit': 'MM'})
    symbol = QgsLineSymbol.createSimple(dict(common, line_color='22,68,107,255', line_width='1.5'))
    foreground = QgsSimpleLineSymbolLayer.create(dict(common,
        line_color='101,199,255,255' if kind == 'main' else '166,231,255,255', line_width='1.1'))
    symbol.appendSymbolLayer(foreground)
    return symbol


commoncourse_symbol = course_symbol


def _point_at(points, cumulative, distance):
    if distance <= 0:
        return QgsPointXY(points[0])
    if distance >= cumulative[-1]:
        return QgsPointXY(points[-1])
    index = bisect_right(cumulative, distance) - 1
    fraction = ((distance - cumulative[index]) /
                (cumulative[index + 1] - cumulative[index]))
    first, last = points[index], points[index + 1]
    return QgsPointXY(first.x() + fraction * (last.x() - first.x()),
                      first.y() + fraction * (last.y() - first.y()))


def _course_geometries(analysis):
    """Copy the 2D course and interpolate the two fractional chainages."""
    geometry = QgsGeometry()
    wkb = getattr(analysis, 'main_course_wkb', b'')
    if not wkb:
        return {'main': None, 'central': None}
    geometry.fromWkb(bytes(wkb))
    if geometry.isNull():
        raise ValueError('No se puede leer el trazado del curso principal.')
    if QgsWkbTypes.flatType(geometry.wkbType()) != QgsWkbTypes.LineString:
        raise ValueError('El curso principal debe ser una única línea.')
    if geometry.isEmpty():
        return {'main': None, 'central': None}
    points = [QgsPointXY(point) for point in geometry.asPolyline()]
    if not all(math.isfinite(point.x()) and math.isfinite(point.y()) for point in points):
        raise ValueError('El curso principal contiene coordenadas no finitas.')
    cumulative = [0.0]
    for first, last in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + math.hypot(last.x() - first.x(), last.y() - first.y()))
    if len(points) < 2 or cumulative[-1] == 0:
        return {'main': None, 'central': None}
    if (not math.isclose(points[0].x(), analysis.outlet_x, rel_tol=0, abs_tol=1e-7)
            or not math.isclose(points[0].y(), analysis.outlet_y, rel_tol=0, abs_tol=1e-7)):
        raise ValueError('El curso principal debe comenzar en el centro de la celda de cierre.')
    main = QgsGeometry.fromPolylineXY(points)
    expected = (getattr(analysis, 'values', {}) or {}).get('main_length_km')
    if expected is not None and not math.isclose(main.length() / 1000, float(expected),
                                                rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError('La longitud del trazado no coincide con L del análisis.')
    start, stop = .10 * cumulative[-1], .85 * cumulative[-1]
    central_points = [_point_at(points, cumulative, start)]
    central_points.extend(QgsPointXY(point) for point, distance in zip(points, cumulative)
                          if start < distance < stop)
    central_points.append(_point_at(points, cumulative, stop))
    central = QgsGeometry.fromPolylineXY(central_points)
    return {'main': main, 'central': central}


class CourseLayers:
    """Keep two owned, durable memory layers without changing the map view.

    Normal updates retain layer IDs and legend checks. The optional visibility
    mapping restores explicit session choices. Destruction or dialog closure
    does not remove the results; removal is an explicit operation.
    """

    def __init__(self, iface, owner_key=None):
        self.iface = iface
        self.owner_key = str(owner_key) if owner_key is not None else uuid4().hex
        self.layers = {}
        self._group = None
        self._visibility = dict(_DEFAULT_VISIBILITY)
        self.note = ''

    def visibility(self):
        project = QgsProject.instance()
        for kind, layer in self.layers.items():
            if not sip.isdeleted(layer):
                node = project.layerTreeRoot().findLayer(layer.id())
                if node is not None:
                    self._visibility[kind] = node.itemVisibilityChecked()
        return dict(self._visibility)

    def _new_layer(self, kind, crs):
        layer = QgsVectorLayer('LineString', _NAMES[kind], 'memory')
        if not layer.isValid():
            raise ValueError('No se puede crear la capa del curso principal.')
        layer.setCrs(crs)
        layer.setCustomProperty(_OWNER_PROPERTY, self.owner_key)
        fields = [QgsField('TIPO', QVariant.String, '', 24)]
        fields.extend(QgsField(name, QVariant.Double, '', 32, 10) for name in (
            'L_KM', 'RES_X_M', 'RES_Y_M', 'X_CIERRE', 'Y_CIERRE', 'T_ANOS', 'Q_M3S',
        ))
        if not layer.dataProvider().addAttributes(fields):
            raise ValueError('No se pueden crear los atributos del curso principal.')
        layer.updateFields()
        layer.renderer().setSymbol(course_symbol(kind))
        return layer

    def _populate(self, layer, geometry, attributes, crs):
        provider = layer.dataProvider()
        previous = list(layer.getFeatures())
        if geometry is None:
            if previous and not provider.deleteFeatures([feature.id() for feature in previous]):
                raise ValueError('No se puede vaciar la capa del curso principal.')
        elif len(previous) == 1:
            feature = previous[0]
            changes = dict(enumerate(attributes))
            if (not provider.changeGeometryValues({feature.id(): geometry})
                    or not provider.changeAttributeValues({feature.id(): changes})):
                raise ValueError('No se puede actualizar la capa del curso principal.')
        else:
            if previous and not provider.deleteFeatures([feature.id() for feature in previous]):
                raise ValueError('No se puede actualizar la capa del curso principal.')
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(attributes)
            added, _ = provider.addFeatures([feature])
            if not added:
                raise ValueError('No se puede representar el curso principal.')
        layer.setCrs(crs)
        layer.updateExtents()
        layer.triggerRepaint()

    def update(self, analysis, query, visibility=None):
        """Update both original-grid courses in place and return their layers."""
        crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
        if not crs.isValid():
            raise ValueError('El sistema de referencia del curso principal no es válido.')
        geometries = _course_geometries(analysis)
        flags = self.visibility()
        if visibility is not None:
            flags.update({kind: bool(visibility[kind]) for kind in _KINDS if kind in visibility})
        project = QgsProject.instance()
        ensure_results_above_official(project)
        new_layers = {}
        for kind in _KINDS:
            layer = self.layers.get(kind)
            if layer is None or sip.isdeleted(layer) or project.mapLayer(layer.id()) is not layer:
                layer = self._new_layer(kind, crs)
                new_layers[kind] = layer
            geometry = geometries[kind]
            attributes = [
                'total' if kind == 'main' else 'tramo_10_85',
                geometry.length() / 1000 if geometry is not None else None,
                analysis.cell_width, analysis.cell_height, analysis.outlet_x, analysis.outlet_y,
                query.period, query.flow,
            ]
            self._populate(layer, geometry, attributes, crs)
            self.layers[kind] = layer
        if self._group is None or sip.isdeleted(self._group):
            self._group = create_result_group(project, 'HidroCAUMAX · Curso principal', 0)
            self._group.setCustomProperty(_OWNER_PROPERTY, self.owner_key)
        self._group.setName(_course_group_name(analysis.cell_width, analysis.cell_height))
        for kind in _KINDS:
            layer = self.layers[kind]
            if kind in new_layers:
                project.addMapLayer(layer, False)
                self._group.insertLayer(0, layer)
            node = project.layerTreeRoot().findLayer(layer.id())
            if node is None:
                node = self._group.insertLayer(0, layer)
            node.setItemVisibilityChecked(flags[kind])
        self._visibility = flags
        activate_result_group(project, self._group, _OWNER_PROPERTY)
        ensure_results_above_official(project)
        self.note = ('La cuenca no contiene un curso principal de longitud positiva; '
                     'las dos capas están vacías.' if geometries['main'] is None else '')
        return dict(self.layers)

    def remove(self):
        """Explicitly remove only this instance's layers and its empty group."""
        project = QgsProject.instance()
        for layer in self.layers.values():
            if (not sip.isdeleted(layer)
                    and layer.customProperty(_OWNER_PROPERTY) == self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        self.layers = {}
        if (self._group is not None and not sip.isdeleted(self._group)
                and not self._group.children()):
            parent = self._group.parent()
            if parent is not None:
                parent.removeChildNode(self._group)
        self._group = None
