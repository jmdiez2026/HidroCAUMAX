"""Persistent QGIS layers for the original D8 course and its 10–85% reach."""

from bisect import bisect_right
import gc
import math
from pathlib import Path
import tempfile
from uuid import uuid4
from weakref import WeakSet

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsGeometry,
    QgsApplication, QgsLineSymbol, QgsMessageLog, Qgis, QgsPointXY, QgsProject,
    QgsSimpleLineSymbolLayer, QgsVectorLayer, QgsWkbTypes,
)

from .layer_order import activate_result_group, create_result_group, ensure_results_above_official


_KINDS = ('main', 'central')
_DEFAULT_VISIBILITY = {'main': True, 'central': False}
_OWNER_PROPERTY = 'HidroCAUMAX/course_owner'
_CONTROLLERS = WeakSet()
_NAMES = {
    'main': 'Curso principal total (L)',
    'central': 'Tramo del curso principal 10–85 % (0,75 L)',
}


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



def _legacy_course_layer(layer):
    """Recognize untagged old plugin memory results by their exact schema.

    A name alone is not proof of ownership and must never delete user layers.
    """
    if not isinstance(layer, QgsVectorLayer) or layer.providerType() != 'memory':
        return False
    if layer.name() not in _NAMES.values():
        return False
    return {'TIPO', 'L_KM', 'RES_X_M', 'RES_Y_M', 'X_CIERRE', 'Y_CIERRE',
            'T_ANOS', 'Q_M3S'} <= set(layer.fields().names())


def _private_course_files(layer):
    """Locate only course-owned private disk results; current courses are memory.

    Never retire shared DEM/D8 rasters or files outside these *course-specific*
    plugin stores, even when a group happens to display them.
    """
    if layer.providerType() not in ('ogr', 'gdal'):
        return ()
    raw = layer.source().split('|', 1)[0]
    if not raw:
        return ()
    source = Path(raw)
    if not source.is_file() or source.is_symlink():
        return ()
    roots = (
        Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'courses',
        Path(tempfile.gettempdir()) / 'HidroCAUMAX' / 'courses',
    )
    try:
        path = source.resolve()
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            return ()
        if any(part.is_symlink() or
               (hasattr(part, 'is_junction') and part.is_junction())
               for part in (source, *source.parents)):
            return ()
        from .generated_cleanup import _family
        return tuple(member for member in _family(source)
                     if member.is_file() and not member.is_symlink())
    except (OSError, ValueError):
        return ()


def retire_other_course_groups(project=None, *, keep_group=None,
                               keep_layer_ids=(), iface=None):
    """Keep the just-generated course; permanently remove older QGIS results.

    A course group currently consists of two QGIS memory layers, so it has no
    per-course DEM, D8 or disk cache to delete. The optional private-file
    branch is for older plugin-owned on-disk course representations only.
    It cannot remove shared input rasters or manually exported files.
    """
    project = QgsProject.instance() if project is None else project
    root = project.layerTreeRoot()
    retained = set(keep_layer_ids)
    old_groups = []
    for group in root.findGroups(recursive=True):
        if group is keep_group:
            continue
        tagged = bool(group.customProperty(_OWNER_PROPERTY, ''))
        legacy = (group.name().startswith('HidroCAUMAX · Curso principal')
                  and any(_legacy_course_layer(node.layer())
                          for node in group.findLayers()
                          if node.layer() is not None))
        if tagged or legacy:
            old_groups.append(group)

    ids = set()
    for layer in project.mapLayers().values():
        if (layer.id() not in retained
                and layer.customProperty(_OWNER_PROPERTY, '')):
            ids.add(layer.id())
    for group in old_groups:
        for node in group.findLayers():
            layer = node.layer()
            if (layer is not None and layer.id() not in retained
                    and (_legacy_course_layer(layer) or
                         layer.customProperty(_OWNER_PROPERTY, ''))):
                ids.add(layer.id())
    if not ids and not old_groups:
        return 0
    obsolete_layers = [project.mapLayer(layer_id) for layer_id in ids]
    obsolete_layers = [layer for layer in obsolete_layers if layer is not None]
    old_owners = {layer.customProperty(_OWNER_PROPERTY, '')
                  for layer in obsolete_layers}
    old_owners.update(group.customProperty(_OWNER_PROPERTY, '')
                      for group in old_groups)

    # There is no course-specific cache in current code; for legacy file-backed
    # course outputs, remove only privately owned paths not referenced elsewhere.
    disk = {path for layer in obsolete_layers for path in _private_course_files(layer)}
    surviving_paths = set()
    for layer in project.mapLayers().values():
        if layer.id() not in ids and layer.providerType() in ('ogr', 'gdal'):
            try:
                source = Path(layer.source().split('|', 1)[0]).resolve()
                surviving_paths.add(source)
                if source.is_file():
                    from .generated_cleanup import _family
                    surviving_paths.update(member.resolve() for member in _family(source))
            except (OSError, ValueError):
                pass
    if ids:
        project.removeMapLayers(sorted(ids))
    for group in reversed(old_groups):
        if sip.isdeleted(group):
            continue
        parent = group.parent()
        if parent is None:
            continue
        # A user layer accidentally placed inside an owned group is retained,
        # but moved out. Its data must never be treated as a course result.
        position = parent.children().index(group)
        for child in list(group.children()):
            parent.insertChildNode(position, child.clone())
            position += 1
            group.removeChildNode(child)
        parent.removeChildNode(group)

    for controller in tuple(_CONTROLLERS):
        if controller.owner_key in old_owners and controller._group is not keep_group:
            controller.layers = {}
            controller._group = None
            controller._visibility = dict(_DEFAULT_VISIBILITY)
            controller.note = ''
    # Discard stale Python references before trying to unlink old OGR sources
    # on Windows, where open handles would otherwise prevent their deletion.
    obsolete_layers.clear()
    gc.collect()
    removed_files = []
    for path in disk - surviving_paths:
        try:
            path.unlink()
            removed_files.append(str(path.resolve()))
        except OSError as error:
            QgsMessageLog.logMessage(
                'No se pudo borrar un curso principal antiguo: '
                + str(path) + ' (' + str(error) + ')',
                'HidroCAUMAX', Qgis.Warning)
    if removed_files:
        from .generated_cleanup import _registry, _save_registry
        registry = _registry(project)
        for path in removed_files:
            registry.pop(path, None)
        _save_registry(project, registry)
    # A fully retired private course store must not leave an empty directory
    # behind. Never touch shared dem_variants, Esri-D8 or CAUMAX source roots.
    private_roots = (
        Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'courses',
        Path(tempfile.gettempdir()) / 'HidroCAUMAX' / 'courses',
    ) if disk else ()
    for root_dir in private_roots:
        try:
            if root_dir.is_dir() and not root_dir.is_symlink():
                root_dir.rmdir()
        except OSError:
            pass
    if iface is not None:
        try:
            canvas = iface.mapCanvas()
            if canvas is not None:
                canvas.stopRendering()
                canvas.clearCache()
                canvas.refresh()
                canvas.scene().update()
                canvas.viewport().update()
        except (AttributeError, RuntimeError, AssertionError):
            pass
    return len(ids)


def deduplicate_course_groups(project=None):
    """Migrate older projects: the lowest owned course group is the newest.

    New generations use an explicit keep_group, so position is relevant only
    for already duplicated saved projects without creation timestamps.
    """
    project = QgsProject.instance() if project is None else project
    groups = [
        group for group in project.layerTreeRoot().findGroups(recursive=True)
        if group.customProperty(_OWNER_PROPERTY, '')
    ]
    if len(groups) < 2:
        return 0
    latest = groups[-1]
    ids = {node.layerId() for node in latest.findLayers()}
    return retire_other_course_groups(
        project, keep_group=latest, keep_layer_ids=ids)


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
        _CONTROLLERS.add(self)

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
        cell = getattr(query, 'cell', None)
        cell_text = (f'{cell.row + 1},{cell.col + 1}' if cell is not None else '—')
        cell_kind = 'MDT' if getattr(query, 'source_kind', 'caumax_raster') == 'rational' else 'Q'
        self._group.setName(
            f'HidroCAUMAX · Curso principal [X={analysis.outlet_x:g}; Y={analysis.outlet_y:g}; '
            f'celda {cell_kind} {cell_text}; {analysis.cell_width:g}×{analysis.cell_height:g} m]')
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
        # Never leave previous Curso principal groups hidden in the tree.
        # The newest successfully published calculation replaces every older
        # course result, independently of whether it originated in rational
        # or maximum-flow CAUMAX tools.
        retire_other_course_groups(project, keep_group=self._group,
                                   keep_layer_ids=(layer.id() for layer in self.layers.values()),
                                   iface=self.iface)
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
