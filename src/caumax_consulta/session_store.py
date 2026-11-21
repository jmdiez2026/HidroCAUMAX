"""Portable, bounded, declarative consultation sessions; no terrain recomputation.

The .caumax.json format is a consultation document, not a QGIS .qgz project.
Only JSON primitives and bounded base64 WKB/PNG are read. Existing targets
are replaced atomically, after the complete new document passes validation.
"""
import base64
import binascii
from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import zlib

from osgeo import ogr, osr
from qgis.core import QgsCoordinateReferenceSystem

from .basin import BasinResult
from .caumax_bridge import NativeQuery
from .grid import Cell
from .metrics import shape_metrics
from .parameter_schema import METRICS
from .terrain import Analysis

SCHEMA = 'caumax-consulta'
SCHEMA_VERSION = 1
RESOLUTION_SCHEMA_VERSION = 2
MAX_RESOLUTION_VARIANTS = 6
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_WKB_BYTES = 8 * 1024 * 1024
MAX_PNG_BYTES = 5 * 1024 * 1024
MAX_GEOMETRY_POINTS = 500_000
MAX_METADATA_ITEMS = 50_000
_VALUE_KEYS = {item.key for item in METRICS} | {
    'zmean_m', 'basin_length_km', 'main_slope_10_85',
    'tc_path_length_km', 'tc_path_slope',
}
_STAT_KEYS = {'area_km2', 'perimeter_km', 'kc', 'ic',
              'delta_area_pct', 'delta_perimeter_pct'}
_STATE_REQUIRED = {'original_checked', 'smooth_checked', 'degree'}
_STATE_OPTIONAL = {'geometry_wkb', 'stats', 'map_png', 'map_caption', 'map_extent',
                   'course_visibility', 'resolution_metadata', 'drainage_state',
                   'hypsometric_dimensionless', 'flow_quantiles', 'basin_hydrology',
                   'base_geometry_wkb', 'cell_grid_visible', 'base_drainage_state'}


@dataclass(frozen=True)
class SavedSession:
    analysis: Analysis
    query: NativeQuery
    smoothing_state: dict
    resolution_state: object = None


def _fail(label, detail):
    raise ValueError(f'Proyecto de consulta no válido: {label}: {detail}.')


def _keys(value, expected, label, optional=()):
    if not isinstance(value, dict) or not expected.issubset(value) or set(value) - expected - set(optional):
        _fail(label, 'campos ausentes o desconocidos')


def _float(value, label, positive=False, nullable=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(label, 'se esperaba un número')
    try:
        value = float(value)
    except (ValueError, OverflowError):
        _fail(label, 'número no representable')
    if not math.isfinite(value) or (positive and value <= 0):
        _fail(label, 'número no finito o fuera de rango')
    return value


def _int(value, label, minimum=0, maximum=1_000_000_000):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(label, 'entero fuera de rango')
    return value


def _text(value, label, maximum=16_384):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        _fail(label, 'texto no válido o demasiado largo')
    return value


def _bool(value, label):
    if type(value) is not bool:
        _fail(label, 'se esperaba un booleano')
    return value


def _crs(wkt, label, metre_projection=False):
    wkt = _text(wkt, label, 100_000)
    crs = QgsCoordinateReferenceSystem.fromWkt(wkt)
    if not crs.isValid():
        _fail(label, 'sistema de referencia no válido')
    if metre_projection:
        reference = osr.SpatialReference()
        if (reference.ImportFromWkt(wkt) != 0 or not reference.IsProjected()
                or not math.isclose(reference.GetLinearUnits(), 1, rel_tol=0, abs_tol=1e-12)):
            _fail(label, 'la cuenca debe estar en un CRS proyectado y métrico')
    return crs


def _close(actual, expected, label):
    actual, expected = _float(actual, label), _float(expected, label)
    if not math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-5):
        _fail(label, 'el valor no corresponde a la geometría o a sus metadatos')


def _point_close(actual, expected, label):
    if not math.isclose(_float(actual, label), _float(expected, label),
                        rel_tol=0, abs_tol=1e-5):
        _fail(label, 'coordenadas incompatibles con el punto guardado')


def _outlet_snap(diagnostics, basin, selected_x, selected_y, boundary):
    """A moved outlet is accepted only with a complete, coherent audit record."""
    snap = diagnostics.get('outlet_snap')
    tolerance = max(basin.cell_width, basin.cell_height) * 1e-7
    same_cell = (abs(selected_x - basin.outlet_x) <= basin.cell_width / 2 + tolerance
                 and abs(selected_y - basin.outlet_y) <= basin.cell_height / 2 + tolerance)
    if snap is None:
        if not same_cell:
            _fail('punto seleccionado', 'no corresponde a la celda de cierre y falta el ajuste a cauce')
        return False
    required = {'original_x', 'original_y', 'snapped_x', 'snapped_y', 'distance_m',
                'search_radius_m', 'minimum_upstream_area_m2', 'reference_area_m2',
                'candidate_upstream_area_m2', 'method', 'status'}
    optional = {'minimum_fraction', 'original_row', 'original_col', 'snapped_row',
                'snapped_col', 'candidate_count', 'original_cell_upstream_area_m2',
                'candidate_touches_boundary', 'tie_break', 'reference', 'policy'}
    _keys(snap, required, 'ajuste del cierre', optional)
    if snap['method'] != 'nearest_main_channel_d8' or snap['status'] not in ('snapped', 'unchanged'):
        _fail('ajuste del cierre', 'método o estado desconocido')
    for key in required - {'method', 'status'}:
        _float(snap[key], 'ajuste ' + key)
    for key, expected in (('original_x', selected_x), ('original_y', selected_y),
                          ('snapped_x', basin.outlet_x), ('snapped_y', basin.outlet_y)):
        _point_close(snap[key], expected, 'ajuste ' + key)
    for offset in ((basin.outlet_x - boundary[0]) / basin.cell_width - 0.5,
                   (boundary[3] - basin.outlet_y) / basin.cell_height - 0.5):
        if not math.isclose(offset, round(offset), rel_tol=0, abs_tol=1e-7):
            _fail('ajuste del cierre', 'el cierre ajustado no es un centro de celda')
    distance = math.hypot(selected_x - basin.outlet_x, selected_y - basin.outlet_y)
    _close(snap['distance_m'], distance, 'distancia de ajuste')
    radius = _float(snap['search_radius_m'], 'radio de ajuste', positive=True)
    if distance > radius + 1e-5:
        _fail('ajuste del cierre', 'la distancia supera el radio de búsqueda')
    reference = _float(snap['reference_area_m2'], 'área de referencia', positive=True)
    minimum = _float(snap['minimum_upstream_area_m2'], 'área mínima de cauce', positive=True)
    candidate = _float(snap['candidate_upstream_area_m2'], 'área acumulada del cierre', positive=True)
    _close(candidate, basin.area_m2, 'área acumulada de la cuenca ajustada')
    if minimum > reference or candidate + max(1e-5, candidate * 1e-7) < minimum:
        _fail('ajuste del cierre', 'áreas de selección incompatibles')
    if 'minimum_fraction' in snap:
        fraction = _float(snap['minimum_fraction'], 'fracción mínima de cauce', positive=True)
        if fraction > 1:
            _fail('ajuste del cierre', 'fracción mínima superior a uno')
        _close(minimum, reference * fraction, 'umbral de cauce')
    indices = {'original_row', 'original_col', 'snapped_row', 'snapped_col'}
    if indices & set(snap):
        if not indices.issubset(snap):
            _fail('ajuste del cierre', 'índices de celda incompletos')
        for key in indices:
            _int(snap[key], 'ajuste ' + key)
        origin_x = basin.outlet_x + (snap['original_col'] - snap['snapped_col']) * basin.cell_width
        origin_y = basin.outlet_y - (snap['original_row'] - snap['snapped_row']) * basin.cell_height
        if (abs(selected_x - origin_x) > basin.cell_width / 2 + tolerance
                or abs(selected_y - origin_y) > basin.cell_height / 2 + tolerance):
            _fail('ajuste del cierre', 'el punto original no corresponde a sus índices de celda')
        same_cell = (snap['original_row'] == snap['snapped_row']
                     and snap['original_col'] == snap['snapped_col'])
    if (snap['status'] == 'unchanged') != same_cell:
        _fail('ajuste del cierre', 'el estado no corresponde al cambio de celda')
    if 'candidate_count' in snap:
        _int(snap['candidate_count'], 'número de candidatos', 1)
    if 'original_cell_upstream_area_m2' in snap:
        original_area = _float(snap['original_cell_upstream_area_m2'], 'área acumulada original', positive=True)
        if snap['status'] == 'unchanged':
            _close(original_area, candidate, 'área acumulada original sin cambio de celda')
    if 'candidate_touches_boundary' in snap:
        if _bool(snap['candidate_touches_boundary'], 'borde del cierre ajustado') != basin.touches_boundary:
            _fail('ajuste del cierre', 'el indicador de borde no corresponde a la cuenca')
    if 'tie_break' in snap and snap['tie_break'] != 'row_col':
        _fail('ajuste del cierre', 'desempate desconocido')
    for key in ('reference', 'policy'):
        if key in snap:
            _text(snap[key], 'ajuste ' + key, 4096)
    return True


def _query_point_in_basin_crs(query_crs, basin_crs, x, y):
    if query_crs == basin_crs:
        return x, y
    try:
        source, target = osr.SpatialReference(), osr.SpatialReference()
        source.ImportFromWkt(query_crs.toWkt())
        target.ImportFromWkt(basin_crs.toWkt())
        for reference in (source, target):
            reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        point = osr.CoordinateTransformation(source, target).TransformPoint(x, y)
        return _float(point[0], 'consulta transformada X'), _float(point[1], 'consulta transformada Y')
    except (RuntimeError, TypeError, OverflowError) as error:
        _fail('consulta transformada', str(error))


def _binary(value, label, maximum):
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        _fail(label, 'base64 ausente o demasiado grande')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        _fail(label, 'base64 dañado')
    if not raw or len(raw) > maximum:
        _fail(label, 'tamaño binario fuera de rango')
    return raw


def _base64(value, label, maximum):
    if not isinstance(value, (bytes, bytearray, memoryview)) or not 0 < len(value) <= maximum:
        _fail(label, 'datos binarios ausentes o demasiado grandes')
    return base64.b64encode(bytes(value)).decode('ascii')


def _wkb(raw, label, allowed_types, budget, allow_empty=False, allow_z=False,
         allow_degenerate_lines=False):
    """Preflight all WKB counts before invoking GDAL/GEOS allocations."""
    view = memoryview(raw)

    def parse(offset, types_allowed, depth=0, expected_dimensions=None):
        if depth > 3 or offset + 5 > len(view):
            _fail(label, 'WKB truncado o excesivamente anidado')
        endian_byte = view[offset]
        if endian_byte not in (0, 1):
            _fail(label, 'orden de bytes WKB desconocido')
        endian = '<' if endian_byte else '>'
        kind = struct.unpack_from(endian + 'I', view, offset + 1)[0]
        dimensions = 2
        if allow_z and kind in (1002, 1005, 0x80000002, 0x80000005):
            kind = kind - 1000 if kind in (1002, 1005) else kind & 0x7fffffff
            dimensions = 3
        if kind not in types_allowed:
            _fail(label, 'tipo de geometría WKB inesperado')
        if expected_dimensions is not None and dimensions != expected_dimensions:
            _fail(label, 'dimensionalidad WKB incompatible entre partes')
        offset += 5

        def count(position):
            if position + 4 > len(view):
                _fail(label, 'contador WKB truncado')
            number = struct.unpack_from(endian + 'I', view, position)[0]
            if number > MAX_GEOMETRY_POINTS:
                _fail(label, 'contador WKB excesivo')
            return number, position + 4

        def points(position, number, ring=False):
            budget[0] += number
            stride = dimensions * 8
            if budget[0] > MAX_GEOMETRY_POINTS or position + number * stride > len(view):
                _fail(label, 'coordenadas WKB truncadas o demasiados vértices')
            first = last = None
            for index in range(number):
                xy = struct.unpack_from(endian + 'd' * dimensions, view, position + index * stride)
                if not all(math.isfinite(v) for v in xy):
                    _fail(label, 'coordenadas WKB no finitas')
                first = xy if index == 0 else first
                last = xy
            if ring and (number < 4 or first != last):
                _fail(label, 'anillo poligonal abierto o incompleto')
            return position + number * stride

        number, offset = count(offset)
        if kind == 2:  # LineString
            if number < 2 and not (allow_empty and number == 0):
                _fail(label, 'línea incompleta')
            return points(offset, number)
        if number == 0 and not allow_empty:
            _fail(label, 'geometría vacía')
        if kind == 3:  # Polygon: rings, then points.
            for _ in range(number):
                size, offset = count(offset)
                offset = points(offset, size, ring=True)
            return offset
        for _ in range(number):  # MultiLineString / MultiPolygon.
            offset = parse(offset, {2} if kind == 5 else {3}, depth + 1, dimensions)
        return offset

    if parse(0, set(allowed_types)) != len(raw):
        _fail(label, 'bytes sobrantes después del WKB')
    try:
        geometry = ogr.CreateGeometryFromWkb(raw)
        native_line = (allow_degenerate_lines and geometry is not None
                       and ogr.GT_Flatten(geometry.GetGeometryType()) in (2, 5))
        if (geometry is None or (geometry.IsEmpty() and not allow_empty)
                or (not native_line and not geometry.IsValid())):
            _fail(label, 'geometría vacía o no válida')
    except RuntimeError as error:
        _fail(label, str(error))
    return geometry


def _topology(geometry):
    parts = [geometry] if geometry.GetGeometryType() == ogr.wkbPolygon else list(geometry)
    return len(parts), tuple(sorted(part.GetGeometryCount() - 1 for part in parts))


def _png(raw):
    if len(raw) < 45 or raw[:8] != b'\x89PNG\r\n\x1a\n':
        _fail('captura GIS', 'firma PNG no válida')
    offset, saw_data, count = 8, False, 0
    while offset + 12 <= len(raw):
        size, = struct.unpack_from('>I', raw, offset)
        kind = raw[offset + 4:offset + 8]
        stop = offset + 12 + size
        if stop > len(raw) or count > 10_000:
            _fail('captura GIS', 'estructura PNG truncada o excesiva')
        content = raw[offset + 8:offset + 8 + size]
        crc, = struct.unpack_from('>I', raw, offset + 8 + size)
        if zlib.crc32(kind + content) & 0xffffffff != crc:
            _fail('captura GIS', 'CRC PNG incorrecto')
        if count == 0:
            if kind != b'IHDR' or size != 13:
                _fail('captura GIS', 'cabecera PNG incompleta')
            width, height = struct.unpack_from('>II', content)
            if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 8_000_000):
                _fail('captura GIS', 'dimensiones PNG excesivas')
        saw_data |= kind == b'IDAT'
        if kind == b'IEND':
            if size or stop != len(raw) or not saw_data:
                _fail('captura GIS', 'terminación PNG no válida')
            return
        offset, count = stop, count + 1
    _fail('captura GIS', 'falta el final PNG')


def _metadata(value, encode=False, depth=0, budget=None):
    """Bounded JSON metadata; preserve tuple/list distinctions declaratively."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 12 or budget[0] > MAX_METADATA_ITEMS:
        _fail('metadatos', 'demasiados elementos o niveles')
    recurse = lambda item: _metadata(item, encode, depth + 1, budget)
    if value is None or type(value) is bool:
        return value
    if isinstance(value, (int, float)):
        _float(value, 'metadatos numéricos')
        return value
    if isinstance(value, str):
        return _text(value, 'metadatos')
    if encode and isinstance(value, tuple):
        return {'__tuple__': [recurse(item) for item in value]}
    if isinstance(value, list):
        return [recurse(item) for item in value]
    if isinstance(value, dict):
        if not encode and set(value) == {'__tuple__'}:
            if not isinstance(value['__tuple__'], list):
                _fail('metadatos', 'tupla no válida')
            return tuple(recurse(item) for item in value['__tuple__'])
        if '__tuple__' in value:
            _fail('metadatos', 'clave reservada')
        return {_text(key, 'clave de metadatos', 128): recurse(item) for key, item in value.items()}
    _fail('metadatos', 'tipo no permitido')


def _stats(geometry, area, perimeter):
    measured_area = float(geometry.GetArea())
    boundary = geometry.Boundary()
    measured_perimeter = float(boundary.Length())
    shape = shape_metrics(measured_area, measured_perimeter)
    return dict(area_km2=measured_area / 1e6, perimeter_km=measured_perimeter / 1000,
                kc=shape.compactness, ic=shape.circularity,
                delta_area_pct=100 * (measured_area / area - 1),
                delta_perimeter_pct=100 * (measured_perimeter / perimeter - 1))


def _document(analysis, query, state):
    if not isinstance(analysis, Analysis) or not isinstance(query, NativeQuery):
        _fail('consulta', 'se requieren Analysis y NativeQuery')
    basin = asdict(analysis.basin)
    basin['geometry_wkb'] = _base64(basin['geometry_wkb'], 'divisoria', MAX_WKB_BYTES)
    query_dict = {field.name: getattr(query, field.name) for field in fields(NativeQuery)
                  if field.name not in ('crs', 'cell')}
    query_dict['crs_wkt'] = query.crs.toWkt()
    query_dict['cell'] = asdict(query.cell) if query.cell is not None else None
    if not isinstance(state, dict):
        _fail('suavizado', 'se esperaba un objeto')
    state = dict(state)
    state['geometry_wkb'] = {
        name: _base64(raw, 'vista previa', MAX_WKB_BYTES)
        for name, raw in state.get('geometry_wkb', {}).items()
    }
    if 'base_geometry_wkb' in state:
        state['base_geometry_wkb'] = _base64(state['base_geometry_wkb'], 'divisoria original', MAX_WKB_BYTES)
    state.setdefault('stats', {})
    if 'map_png' in state:
        state['map_png'] = _base64(state['map_png'], 'captura GIS', MAX_PNG_BYTES)
    for key in ('drainage_state', 'base_drainage_state'):
        if key in state:
            state[key] = _drainage_state(state[key], encode=True)
    values = dict(analysis.values)
    if isinstance(values.get('cell_resolution'), tuple):
        values['cell_resolution'] = list(values['cell_resolution'])
    return {
        'schema': SCHEMA, 'version': SCHEMA_VERSION,
        'analysis': dict(basin=basin, values=values, notes=list(analysis.notes),
                         diagnostics=_metadata(analysis.diagnostics, encode=True),
                         main_course_wkb=_base64(analysis.main_course_wkb, 'curso principal', MAX_WKB_BYTES),
                         grid_wkb=_base64(analysis.grid_wkb, 'malla', MAX_WKB_BYTES),
                         selected_x=analysis.selected_x, selected_y=analysis.selected_y),
        'query': query_dict, 'smoothing_state': state,
    }


def _decode_single(document):
    _keys(document, {'schema', 'version', 'analysis', 'query', 'smoothing_state'}, 'documento')
    if document['schema'] != SCHEMA or type(document['version']) is not int or document['version'] != SCHEMA_VERSION:
        _fail('formato', 'esquema o versión desconocidos')
    data, qdata, state = document['analysis'], document['query'], document['smoothing_state']
    _keys(state, _STATE_REQUIRED, 'estado de suavizado', _STATE_OPTIONAL)
    _keys(data, {field.name for field in fields(Analysis)}, 'análisis')
    bdata = data['basin']
    _keys(bdata, {field.name for field in fields(BasinResult)}, 'cuenca')
    bdata = dict(bdata)
    budget = [0]
    bdata['geometry_wkb'] = _binary(bdata['geometry_wkb'], 'divisoria', MAX_WKB_BYTES)
    polygon = _wkb(bdata['geometry_wkb'], 'divisoria', {3, 6}, budget)
    basin_crs = _crs(bdata['crs_wkt'], 'CRS de cuenca', metre_projection=True)
    for key in ('area_m2', 'perimeter_m', 'cell_width', 'cell_height'):
        bdata[key] = _float(bdata[key], key, positive=True)
    for key in ('outlet_x', 'outlet_y'):
        bdata[key] = _float(bdata[key], key)
    bdata['cell_count'] = _int(bdata['cell_count'], 'número de celdas', 1, 100_000_000)
    bdata['touches_boundary'] = _bool(bdata['touches_boundary'], 'borde de los datos')
    for key in ('z_min', 'z_max', 'z_mean'):
        bdata[key] = _float(bdata[key], key, nullable=True)
    diagnostics = _metadata(data['diagnostics'])
    if not isinstance(diagnostics, dict):
        _fail('diagnósticos', 'se esperaba un objeto')
    surface = diagnostics.get('analysis_surface')
    native_polygon = polygon
    native_area, native_perimeter = bdata['area_m2'], bdata['perimeter_m']
    native_count = bdata['cell_count']
    if surface is not None:
        _keys(surface, {'kind', 'degree', 'original_area_m2', 'original_perimeter_m',
                        'original_cell_count'}, 'superficie activa')
        if surface['kind'] != 'smoothed' or not state['smooth_checked']:
            _fail('superficie activa', 'divisoria suavizada no seleccionada')
        degree = _int(surface['degree'], 'grado activo', 1, 45)
        _close(state['degree'], degree, 'grado de la superficie activa')
        if 'base_geometry_wkb' not in state:
            _fail('superficie activa', 'falta la divisoria original de referencia')
        native_area = _float(surface['original_area_m2'], 'área original', positive=True)
        native_perimeter = _float(surface['original_perimeter_m'], 'perímetro original', positive=True)
        native_count = _int(surface['original_cell_count'], 'celdas originales', 1, 100_000_000)
        _close(native_area, native_count*bdata['cell_width']*bdata['cell_height'], 'área original de celdas')
        if bdata['area_m2'] > bdata['cell_count']*bdata['cell_width']*bdata['cell_height'] + .01:
            _fail('superficie activa', 'área mayor que las celdas intersectadas')
    else:
        _close(bdata['area_m2'], bdata['cell_count'] * bdata['cell_width'] * bdata['cell_height'], 'área de celdas')
    if 'base_geometry_wkb' in state:
        native_raw = _binary(state['base_geometry_wkb'], 'divisoria original', MAX_WKB_BYTES)
        native_polygon = _wkb(native_raw, 'divisoria original', {3, 6}, budget)
        _close(native_polygon.GetArea(), native_area, 'área de divisoria original')
        _close(native_polygon.Boundary().Length(), native_perimeter, 'perímetro de divisoria original')
        if _topology(native_polygon) != _topology(polygon):
            _fail('superficie activa', 'partes o huecos incompatibles con la divisoria original')
        if surface is None and not native_polygon.Equals(polygon):
            _fail('divisoria original', 'no corresponde a la cuenca del análisis')
    _close(polygon.GetArea(), bdata['area_m2'], 'área poligonal')
    _close(polygon.Boundary().Length(), bdata['perimeter_m'], 'perímetro poligonal')
    outlet = ogr.Geometry(ogr.wkbPoint)
    outlet.AddPoint_2D(bdata['outlet_x'], bdata['outlet_y'])
    if not polygon.Contains(outlet):
        _fail('cierre', 'el centro de cierre debe estar dentro de la cuenca original')
    zmin, zmax, zmean = (bdata[key] for key in ('z_min', 'z_max', 'z_mean'))
    if ((zmin is None) != (zmax is None) or (zmean is None) != (zmin is None)
            or (zmin is not None and not zmin <= zmean <= zmax)):
        _fail('altitudes', 'extremos y media incompatibles')
    basin = BasinResult(**bdata)
    values = data['values']
    _keys(values, _VALUE_KEYS, 'valores del análisis')
    values = dict(values)
    resolution = values['cell_resolution']
    if not isinstance(resolution, list) or len(resolution) != 2:
        _fail('resolución', 'se requieren anchura y altura')
    values['cell_resolution'] = tuple(_float(v, 'resolución', positive=True) for v in resolution)
    for key in values.keys() - {'cell_resolution'}:
        values[key] = _float(values[key], key, nullable=True)
    _close(values['area_km2'], basin.area_m2 / 1e6, 'área del análisis')
    _close(values['perimeter_km'], basin.perimeter_m / 1000, 'perímetro del análisis')
    _close(values['cell_count'], basin.cell_count, 'celdas del análisis')
    values['cell_count'] = basin.cell_count
    for actual, expected in zip(values['cell_resolution'], (basin.cell_width, basin.cell_height)):
        _close(actual, expected, 'resolución de análisis')
    shape = shape_metrics(basin.area_m2, basin.perimeter_m)
    _close(values['kc'], shape.compactness, 'Kc')
    _close(values['ic'], shape.circularity, 'Ic')
    for key, expected in (('zmin_m', zmin), ('zmax_m', zmax), ('zmean_m', zmean)):
        if expected is None:
            if values[key] is not None:
                _fail(key, 'cota incompatible con el MDT guardado')
        else:
            _close(values[key], expected, key)
    if values['zmedian_m'] is not None and (zmin is None or not zmin <= values['zmedian_m'] <= zmax):
        _fail('mediana', 'P50 fuera del intervalo de cotas')
    if not isinstance(data['notes'], list) or len(data['notes']) > 10_000:
        _fail('observaciones', 'lista no válida')
    notes = tuple(_text(note, 'observación') for note in data['notes'])
    if 'hypsometric_curve' in diagnostics:
        _hypsometric_curve(diagnostics['hypsometric_curve'], basin, values)
    main_raw = _binary(data['main_course_wkb'], 'curso principal', MAX_WKB_BYTES)
    grid_raw = _binary(data['grid_wkb'], 'malla', MAX_WKB_BYTES)
    main = _wkb(main_raw, 'curso principal', {2}, budget, allow_empty=True)
    grid = _wkb(grid_raw, 'malla', {2, 5}, budget)
    _close(main.Length(), _float(values['main_length_km'], 'longitud del curso') * 1000, 'longitud del curso')
    if not main.IsEmpty():
        first = main.GetPoint(0)
        _close(first[0], basin.outlet_x, 'inicio del curso X')
        _close(first[1], basin.outlet_y, 'inicio del curso Y')
    boundary = polygon.GetEnvelope()
    envelope = grid.GetEnvelope()
    tolerance = max(basin.cell_width, basin.cell_height) * (1.0000001 if surface else 1e-7)
    if not (boundary[0] - tolerance <= envelope[0] <= envelope[1] <= boundary[1] + tolerance
            and boundary[2] - tolerance <= envelope[2] <= envelope[3] <= boundary[3] + tolerance):
        _fail('malla', 'líneas fuera de la extensión de la cuenca')
    selected_x = _float(data['selected_x'], 'punto seleccionado X')
    selected_y = _float(data['selected_y'], 'punto seleccionado Y')
    snap_basin = replace(basin, area_m2=native_area, perimeter_m=native_perimeter,
                         cell_count=native_count)
    has_snap = _outlet_snap(diagnostics, snap_basin, selected_x, selected_y, native_polygon.GetEnvelope())
    analysis = Analysis(basin, values, notes, diagnostics, main_raw, grid_raw, selected_x, selected_y)

    query_fields = {field.name for field in fields(NativeQuery)} - {'crs'} | {'crs_wkt'}
    _keys(qdata, query_fields, 'consulta nativa')
    qdata = dict(qdata)
    qdata['flow'] = _float(qdata['flow'], 'caudal')
    if qdata['flow'] < 0:
        _fail('caudal', 'valor negativo')
    qdata['period'] = _int(qdata['period'], 'periodo', 1)
    for key in ('x', 'y'):
        qdata[key] = _float(qdata[key], 'consulta ' + key)
    query_crs = _crs(qdata.pop('crs_wkt'), 'CRS de consulta')
    qdata['crs'] = query_crs
    if has_snap:
        original_x, original_y = _query_point_in_basin_crs(query_crs, basin_crs, qdata['x'], qdata['y'])
        _point_close(original_x, selected_x, 'consulta original X')
        _point_close(original_y, selected_y, 'consulta original Y')
    elif query_crs == basin_crs:
        _close(qdata['x'], selected_x, 'consulta X')
        _close(qdata['y'], selected_y, 'consulta Y')
    for key in ('directions_path', 'dem_path', 'layername', 'layer_id', 'flow_layer_path'):
        qdata[key] = _text(qdata[key], key)
    if qdata['cell'] is not None:
        cell = qdata['cell']
        _keys(cell, {field.name for field in fields(Cell)}, 'celda del caudal')
        cell = dict(cell)
        for key in ('row', 'col'):
            cell[key] = _int(cell[key], 'celda ' + key)
        for key in ('x', 'y', 'width', 'height'):
            cell[key] = _float(cell[key], 'celda ' + key, positive=key in ('width', 'height'))
        if (abs(qdata['x'] - cell['x']) > cell['width'] / 2 + 1e-5
                or abs(qdata['y'] - cell['y']) > cell['height'] / 2 + 1e-5):
            _fail('celda consultada', 'el punto nativo queda fuera de su celda')
        qdata['cell'] = Cell(**cell)
    query = NativeQuery(**qdata)

    _keys(state, _STATE_REQUIRED, 'estado de suavizado', _STATE_OPTIONAL)
    state = dict(state)
    if 'base_geometry_wkb' in state:
        state['base_geometry_wkb'] = native_raw
    for key in ('original_checked', 'smooth_checked'):
        state[key] = _bool(state[key], key)
    if 'cell_grid_visible' in state:
        state['cell_grid_visible'] = _bool(state['cell_grid_visible'], 'malla de celdas visible')
    state['degree'] = _float(state['degree'], 'grado de suavizado')
    if not 0.5 <= state['degree'] <= 45:
        _fail('grado de suavizado', 'fuera del intervalo 0,5–45')
    previews = state.get('geometry_wkb', {})
    if not isinstance(previews, dict) or set(previews) - {'original', 'suavizada'}:
        _fail('vista previa', 'tipos de divisoria desconocidos')
    decoded_previews, expected_stats = {}, {}
    for name, encoded in previews.items():
        raw = _binary(encoded, 'vista ' + name, MAX_WKB_BYTES)
        geometry = _wkb(raw, 'vista ' + name, {3, 6}, budget)
        if _topology(geometry) != _topology(native_polygon) or not geometry.Intersects(outlet):
            _fail('vista ' + name, 'partes, huecos o cierre incompatibles')
        if name == 'original' and not geometry.Equals(native_polygon):
            _fail('vista original', 'no corresponde a la cuenca de celdas')
        if name == 'suavizada' and surface and not geometry.Equals(polygon):
            _fail('vista suavizada', 'no corresponde a la superficie del análisis')
        decoded_previews[name] = raw
        expected_stats[name] = _stats(geometry, native_area, native_perimeter)
    state['geometry_wkb'] = decoded_previews
    recorded_stats = state.get('stats', {})
    if not isinstance(recorded_stats, dict) or set(recorded_stats) - {'original', 'suavizada'}:
        _fail('estadísticas de vista', 'tipos de divisoria desconocidos')
    if 'original' in recorded_stats and 'original' not in expected_stats:
        expected_stats['original'] = _stats(native_polygon, native_area, native_perimeter)
    validated_stats = {}
    for name, metrics in recorded_stats.items():
        if name not in expected_stats:
            _fail('estadísticas de vista', 'falta la geometría correspondiente')
        _keys(metrics, _STAT_KEYS, 'estadísticas ' + name)
        validated_stats[name] = {key: _float(value, 'estadística ' + key) for key, value in metrics.items()}
        for key, actual in validated_stats[name].items():
            _close(actual, expected_stats[name][key], 'estadística ' + key)
    for name, metrics in expected_stats.items():
        validated_stats.setdefault(name, metrics)
    state['stats'] = validated_stats
    if 'map_png' in state:
        state['map_png'] = _binary(state['map_png'], 'captura GIS', MAX_PNG_BYTES)
        _png(state['map_png'])
    if 'map_caption' in state:
        state['map_caption'] = _text(state['map_caption'], 'leyenda de captura')
    if 'map_extent' in state:
        extent = state['map_extent']
        _keys(extent, {'xmin', 'ymin', 'xmax', 'ymax', 'crs_wkt'}, 'extensión de captura')
        extent = dict(extent)
        for key in ('xmin', 'ymin', 'xmax', 'ymax'):
            extent[key] = _float(extent[key], 'extensión ' + key)
        if not extent['xmin'] < extent['xmax'] or not extent['ymin'] < extent['ymax']:
            _fail('extensión de captura', 'límites invertidos o vacíos')
        _crs(extent['crs_wkt'], 'CRS de captura')
        state['map_extent'] = extent
    visibility = state.get('course_visibility', {'main': True, 'central': False})
    _keys(visibility, {'main', 'central'}, 'visibilidad de los cursos')
    state['course_visibility'] = {
        key: _bool(value, 'visibilidad ' + key) for key, value in visibility.items()
    }
    resolution_metadata = state.get('resolution_metadata', {})
    if not isinstance(resolution_metadata, dict):
        _fail('metadatos de resolución activa', 'se esperaba un objeto')
    if resolution_metadata:
        _keys(resolution_metadata, {'active_key', 'label', 'resolution', 'provider', 'source'},
              'metadatos de resolución activa')
        resolution_metadata = dict(resolution_metadata)
        for key in ('active_key', 'label', 'provider', 'source'):
            resolution_metadata[key] = _text(resolution_metadata[key], 'resolución ' + key)
        resolution_metadata['resolution'] = _float(resolution_metadata['resolution'],
                                                    'resolución activa', positive=True)
        _close(resolution_metadata['resolution'], min(basin.cell_width, basin.cell_height),
               'metadatos de resolución activa')
    state['resolution_metadata'] = resolution_metadata
    for key in ('drainage_state', 'base_drainage_state'):
        if key in state:
            state[key] = _drainage_state(state[key], budget=budget)
    state['hypsometric_dimensionless'] = _bool(state.get('hypsometric_dimensionless', False),
                                              'curva hipsométrica adimensional')
    if 'flow_quantiles' in state:
        state['flow_quantiles'] = _flow_quantiles(state['flow_quantiles'], query)
    if 'basin_hydrology' in state:
        state['basin_hydrology'] = _basin_hydrology(state['basin_hydrology'], analysis)
    return SavedSession(analysis, query, state)


def _basin_hydrology(value, analysis):
    """Validate portable, already computed P0/rainfall; never open rasters."""
    from .basin_hydrology import basin_fingerprint

    required = {'version', 'basin_fingerprint', 'basin_area_m2', 'p0_result',
                'rainfall_results', 'input_period', 'input_precipitation',
                'extrapolate', 'method'}
    _keys(value, required, 'hidrología media de cuenca')
    if type(value['version']) is not int or value['version'] != 1:
        _fail('hidrología media de cuenca', 'versión desconocida')
    fingerprint = _text(value['basin_fingerprint'], 'firma de cuenca', 64)
    if (len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint)
            or fingerprint != basin_fingerprint(analysis)):
        _fail('hidrología media de cuenca', 'la firma no corresponde a la geometría y CRS activos')
    area = _float(value['basin_area_m2'], 'área hidrológica de cuenca', positive=True)
    _close(area, analysis.area_m2, 'área hidrológica de cuenca')
    result = dict(value)
    result['basin_area_m2'] = area
    result['p0_result'] = (None if value['p0_result'] is None else
                           _basin_raster_stats(value['p0_result'], area))
    rainfall = value['rainfall_results']
    if not isinstance(rainfall, dict) or set(rainfall) - {'2', '5', '10', '25', '100', '500'}:
        _fail('precipitaciones medias', 'periodos desconocidos')
    result['rainfall_results'] = {
        period: _basin_raster_stats(stats, area) for period, stats in rainfall.items()
    }
    result['input_period'] = _float(value['input_period'], 'periodo de precipitación', positive=True)
    result['input_precipitation'] = _float(value['input_precipitation'], 'precipitación de entrada')
    if result['input_period'] <= 1 or result['input_precipitation'] < 0:
        _fail('precipitación media', 'periodo ≤1 o precipitación negativa')
    result['extrapolate'] = _bool(value['extrapolate'], 'extrapolación de precipitación')
    if value['method'] != 'piecewise_linear_p_log_t':
        _fail('precipitación media', 'método desconocido')
    return result


def _basin_raster_stats(value, area):
    required = {'source_path', 'mean_mm', 'basin_area_m2', 'covered_area_m2',
                'valid_area_m2', 'coverage_fraction', 'valid_fraction',
                'pixel_count', 'status', 'notes'}
    _keys(value, required, 'estadísticas de ráster en cuenca', {'statistics','distribution'})
    result = dict(value)
    result['source_path'] = _text(value['source_path'], 'ráster hidrológico de origen')
    result['basin_area_m2'] = _float(value['basin_area_m2'], 'área de estadística', positive=True)
    _close(result['basin_area_m2'], area, 'área de estadística')
    for key in ('covered_area_m2', 'valid_area_m2', 'coverage_fraction', 'valid_fraction'):
        result[key] = _float(value[key], 'estadística ' + key)
        if result[key] < 0:
            _fail('estadísticas de cuenca', 'área o fracción negativa')
    covered, valid = result['covered_area_m2'], result['valid_area_m2']
    tolerance = max(1e-5, area * 1e-7)
    if covered > area + tolerance or valid > covered + tolerance:
        _fail('estadísticas de cuenca', 'las áreas válida, cubierta y total son incompatibles')
    for key, expected in (('coverage_fraction', covered / area), ('valid_fraction', valid / area)):
        if (result[key] > 1 + 1e-7
                or not math.isclose(result[key], expected, rel_tol=1e-7, abs_tol=1e-9)):
            _fail('estadísticas de cuenca', 'la fracción no corresponde a su área')
    result['pixel_count'] = _int(value['pixel_count'], 'píxeles intersectados', 0)
    if covered > 0 and result['pixel_count'] == 0:
        _fail('estadísticas de cuenca', 'área cubierta sin píxeles intersectados')
    mean = result['mean_mm'] = _float(value['mean_mm'], 'media hidrológica', nullable=True)
    if mean is not None and mean < 0:
        _fail('estadísticas de cuenca', 'media negativa')
    status = value['status']
    full = math.isclose(valid, area, rel_tol=1e-7, abs_tol=1e-5)
    if status == 'unavailable':
        if mean is not None or valid != 0:
            _fail('estadísticas no disponibles', 'deben carecer de media y área válida')
    elif status == 'ok':
        if mean is None or not full:
            _fail('estadísticas completas', 'falta cobertura válida completa')
    elif status == 'partial':
        if mean is None or valid <= 0 or full:
            _fail('estadísticas parciales', 'se requiere área válida positiva e incompleta')
    else:
        _fail('estadísticas de cuenca', 'estado desconocido')
    if not isinstance(value['notes'], list) or len(value['notes']) > 1000:
        _fail('observaciones hidrológicas', 'lista ausente o excesiva')
    result['notes'] = [_text(note, 'observación hidrológica') for note in value['notes']]
    if 'statistics' in value:
        from .raster_statistics import normalise_statistics
        result['statistics'] = normalise_statistics(value['statistics'], mean)
    if 'distribution' in value:
        from .raster_profiles import normalise_profile
        result['distribution'] = normalise_profile(value['distribution'],mean,valid)
    return result


def _drainage_state(value, encode=False, budget=None):
    """Portable line network; raster paths are references, never opened here."""
    required = {'metadata', 'threshold_area_m2', 'recommended_threshold_area_m2',
                'automatic', 'flow_encoding', 'dem_path', 'tau_d8_path',
                'caumax_d8_path', 'network_path', 'network_crs_wkt', 'features'}
    optional = {'fields', 'network_visible', 'directions_visible'}
    _keys(value, required, 'red de drenaje', optional)
    result = dict(value)
    metadata_budget = [0]
    if not isinstance(value['metadata'], dict):
        _fail('metadatos TauDEM', 'se esperaba un objeto')
    result['metadata'] = _simple_metadata(value['metadata'], budget=metadata_budget)
    for key in ('threshold_area_m2', 'recommended_threshold_area_m2'):
        result[key] = _float(value[key], 'drenaje ' + key, positive=True)
    result['automatic'] = _bool(value['automatic'], 'umbral automático de drenaje')
    if value['flow_encoding'] not in ('caumax', 'taudem'):
        _fail('direcciones de drenaje', 'codificación desconocida')
    for key in ('dem_path', 'tau_d8_path', 'caumax_d8_path', 'network_path'):
        result[key] = _text(value[key], 'drenaje ' + key)
    _crs(value['network_crs_wkt'], 'CRS de red de drenaje')
    field_types = {}
    if 'fields' in value:
        fields = value['fields']
        if not isinstance(fields, list) or len(fields) > 256:
            _fail('campos de red de drenaje', 'lista ausente o excesiva')
        copied = []
        for field in fields:
            _keys(field, {'name', 'type', 'width', 'precision'}, 'campo de drenaje')
            name = _text(field['name'], 'nombre de campo de drenaje', 128)
            kind = field['type']
            if not name or name in field_types or kind not in ('int', 'int64', 'real', 'str'):
                _fail('campo de drenaje', 'nombre duplicado, vacío o tipo desconocido')
            field_types[name] = kind
            copied.append(dict(name=name, type=kind,
                               width=_int(field['width'], 'anchura de campo', 0, 4096),
                               precision=_int(field['precision'], 'decimales de campo', 0, 30)))
        result['fields'] = copied
    features = value['features']
    if not isinstance(features, list) or len(features) > MAX_METADATA_ITEMS:
        _fail('red de drenaje', 'lista de tramos ausente o excesiva')
    geometry_budget = [0] if budget is None else budget
    total_bytes, copied = 0, []
    for feature in features:
        _keys(feature, {'wkb', 'attributes'}, 'tramo de drenaje')
        remaining = MAX_WKB_BYTES - total_bytes
        if encode:
            encoded = _base64(feature['wkb'], 'tramo de drenaje', remaining)
            raw = bytes(feature['wkb'])
        else:
            raw = _binary(feature['wkb'], 'tramo de drenaje', remaining)
            encoded = None
        total_bytes += len(raw)
        # StreamNet may retain zero-length junction/headwater links. Preserve
        # their native records after the bounded coordinate/count preflight.
        _wkb(raw, 'tramo de drenaje', {2, 5}, geometry_budget, allow_z=True,
             allow_degenerate_lines=True)
        attributes = feature['attributes']
        if not isinstance(attributes, dict) or len(attributes) > 256:
            _fail('atributos de drenaje', 'objeto ausente o excesivo')
        attributes = _simple_metadata(attributes, budget=metadata_budget)
        if 'fields' in value and set(attributes) != set(field_types):
            _fail('atributos de drenaje', 'no corresponden a los campos guardados')
        for name, item in attributes.items():
            if not name or (item is not None and type(item) not in (bool, int, float, str)):
                _fail('atributo de drenaje', 'se requieren valores escalares JSON')
            if item is None or 'fields' not in value:
                continue
            kind = field_types[name]
            if kind == 'str':
                _text(item, 'atributo ' + name)
            elif kind in ('int', 'int64'):
                bits = 32 if kind == 'int' else 64
                _int(item, 'atributo ' + name, -(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
            else:
                _float(item, 'atributo ' + name)
        copied.append(dict(wkb=encoded if encode else raw, attributes=attributes))
    result['features'] = copied
    for key in ('network_visible', 'directions_visible'):
        if key in value:
            result[key] = _bool(value[key], 'visibilidad ' + key)
    return result


def _hypsometric_curve(value, basin, values):
    required = {'fraction', 'elevation_m', 'area_km2', 'valid_count', 'zmin_m', 'zmax_m',
                'hi_exact', 'auc_approx', 'sample_count', 'method', 'population'}
    _keys(value, required, 'curva hipsométrica')
    _close(_float(value['area_km2'], 'área de curva', positive=True), basin.area_m2 / 1e6,
           'área de curva hipsométrica')
    count = _int(value['valid_count'], 'cotas de curva', 0, basin.cell_count)
    samples = _int(value['sample_count'], 'muestras de curva', 0, 501)
    if samples not in (0, 201, 501) or value['population'] not in ('valid_cells', 'valid_cell_area'):
        _fail('curva hipsométrica', 'muestras o población desconocidas')
    _text(value['method'], 'método de curva', 4096)
    fractions, elevations = value['fraction'], value['elevation_m']
    if (not isinstance(fractions, list) or not isinstance(elevations, list)
            or len(fractions) != samples or len(elevations) != samples):
        _fail('curva hipsométrica', 'arrays incompatibles con el número de muestras')
    if not samples:
        if count or any(value[key] is not None for key in ('zmin_m', 'zmax_m', 'hi_exact', 'auc_approx')):
            _fail('curva hipsométrica', 'curva vacía con cotas o índices')
        if basin.z_min is not None:
            _fail('curva hipsométrica', 'faltan muestras de las cotas guardadas')
        return
    if not count:
        _fail('curva hipsométrica', 'muestras sin cotas válidas')
    fractions = [_float(item, 'fracción de curva') for item in fractions]
    elevations = [_float(item, 'cota de curva') for item in elevations]
    if (fractions[0] != 0 or fractions[-1] != 1
            or any(not 0 <= item <= 1 for item in fractions)
            or any(a >= b for a, b in zip(fractions, fractions[1:]))
            or any(a < b for a, b in zip(elevations, elevations[1:]))):
        _fail('curva hipsométrica', 'fracciones o cotas no ordenadas')
    zmin = _float(value['zmin_m'], 'mínimo de curva')
    zmax = _float(value['zmax_m'], 'máximo de curva')
    _close(zmin, basin.z_min, 'mínimo de curva')
    _close(zmax, basin.z_max, 'máximo de curva')
    _close(elevations[0], zmax, 'cabecera de curva')
    _close(elevations[-1], zmin, 'pie de curva')
    if zmin == zmax:
        if value['hi_exact'] is not None or value['auc_approx'] is not None:
            _fail('curva hipsométrica', 'índice indefinido para relieve nulo')
    else:
        hi = _float(value['hi_exact'], 'HI de curva')
        auc = _float(value['auc_approx'], 'área aproximada de curva')
        if not 0 <= hi <= 1 or not 0 <= auc <= 1:
            _fail('curva hipsométrica', 'índices fuera de cero a uno')
        _close(hi, values['hypsometric'], 'HI exacto de curva')


def _flow_quantiles(value, query):
    required = {'version', 'periods', 'flows', 'source_paths', 'point',
                'input_period', 'input_flow', 'extrapolate', 'method'}
    _keys(value, required, 'cuantiles CAUMAX')
    if type(value['version']) is not int or value['version'] != 1:
        _fail('cuantiles CAUMAX', 'versión desconocida')
    periods = value['periods']
    if (not isinstance(periods, list) or any(type(item) is not int for item in periods)
            or periods != [2, 5, 10, 25, 100, 500]):
        _fail('cuantiles CAUMAX', 'periodos distintos del catálogo original')
    flows, paths = value['flows'], value['source_paths']
    if not isinstance(flows, list) or len(flows) != 6 or not isinstance(paths, list) or len(paths) != 6:
        _fail('cuantiles CAUMAX', 'se requieren seis caudales y rutas de origen')
    flows = [_float(item, 'caudal de cuantil', nullable=True) for item in flows]
    if any(item is not None and item < 0 for item in flows):
        _fail('cuantiles CAUMAX', 'caudal negativo')
    paths = [_text(item, 'ruta de cuantil') for item in paths]
    point = value['point']
    _keys(point, {'x', 'y', 'crs_wkt'}, 'punto original de cuantiles')
    point = dict(point)
    point['x'], point['y'] = (_float(point[key], 'punto de cuantiles ' + key) for key in ('x', 'y'))
    point_crs = _crs(point['crs_wkt'], 'CRS de cuantiles')
    x, y = _query_point_in_basin_crs(point_crs, query.crs, point['x'], point['y'])
    _point_close(x, query.x, 'punto original de cuantiles X')
    _point_close(y, query.y, 'punto original de cuantiles Y')
    period = _float(value['input_period'], 'periodo de entrada', positive=True)
    flow = _float(value['input_flow'], 'caudal de entrada')
    if period <= 1 or flow < 0:
        _fail('cuantiles CAUMAX', 'periodo ≤1 o caudal negativo')
    extrapolate = _bool(value['extrapolate'], 'extrapolación de cuantiles')
    if value['method'] != 'piecewise_linear_q_log_t':
        _fail('cuantiles CAUMAX', 'método desconocido')
    return dict(version=1, periods=list(periods), flows=flows, source_paths=paths, point=point,
                input_period=period, input_flow=flow, extrapolate=extrapolate,
                method=value['method'])


def _simple_metadata(value, depth=0, budget=None):
    """Extra provider/download metadata stays ordinary, bounded JSON data."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 12 or budget[0] > MAX_METADATA_ITEMS:
        _fail('opciones de resolución', 'metadatos excesivos')
    recurse = lambda item: _simple_metadata(item, depth + 1, budget)
    if value is None or type(value) is bool:
        return value
    if isinstance(value, (int, float)):
        _float(value, 'metadato de resolución')
        return value
    if isinstance(value, str):
        return _text(value, 'metadato de resolución')
    if isinstance(value, list):
        return [recurse(item) for item in value]
    if isinstance(value, dict):
        return {_text(key, 'clave de resolución', 128): recurse(item) for key, item in value.items()}
    _fail('opciones de resolución', 'solo se admiten metadatos JSON simples')


def _resolution_header(state):
    _keys(state, {'active_key', 'options', 'variants'}, 'conjunto de resoluciones')
    active = _text(state['active_key'], 'resolución activa', 64)
    if not active:
        _fail('resolución activa', 'clave vacía')
    options = state['options']
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_RESOLUTION_VARIANTS:
        _fail('opciones de resolución', 'se admiten entre una y seis opciones')
    required = {'key', 'resolution', 'label', 'provider', 'available'}
    copied, keys = [], set()
    for option in options:
        if not isinstance(option, dict) or not required.issubset(option):
            _fail('opción de resolución', 'faltan campos de descripción o disponibilidad')
        option = _simple_metadata(option)
        key = _text(option['key'], 'clave de resolución', 64)
        if not key or key in keys:
            _fail('opciones de resolución', 'clave vacía o duplicada')
        keys.add(key)
        option['resolution'] = _float(option['resolution'], 'resolución', positive=True)
        option['label'] = _text(option['label'], 'etiqueta de resolución', 2048)
        option['provider'] = _text(option['provider'], 'proveedor de resolución', 2048)
        option['available'] = _bool(option['available'], 'disponibilidad de resolución')
        copied.append(option)
    variants = state['variants']
    if not isinstance(variants, dict) or len(variants) > MAX_RESOLUTION_VARIANTS:
        _fail('variantes de resolución', 'se admiten como máximo seis resultados')
    for key in variants:
        if not isinstance(key, str) or key not in keys:
            _fail('variante de resolución', 'clave no descrita en las opciones')
    by_key = {option['key']: option for option in copied}
    if active not in by_key or not by_key[active]['available'] or active not in variants:
        _fail('resolución activa', 'no tiene un resultado disponible completo')
    if any(option['available'] and option['key'] not in variants for option in copied):
        _fail('resolución disponible', 'falta su análisis completo; calcula todas las opciones antes de guardar')
    return active, copied, variants


def _resolution_document(state):
    active, options, variants = _resolution_header(state)
    packed = {}
    for key, variant in variants.items():
        _keys(variant, {'analysis', 'query', 'smoothing_state'}, 'variante ' + key)
        data = _document(variant['analysis'], variant['query'], variant['smoothing_state'])
        packed[key] = {name: data[name] for name in ('analysis', 'query', 'smoothing_state')}
    return dict(active_key=active, options=options, variants=packed)


def _decode(document):
    """Version 1 is one result; version 2 includes all computed resolutions."""
    if not isinstance(document, dict) or type(document.get('version')) is not int:
        _fail('formato', 'versión ausente o no válida')
    if document['version'] == SCHEMA_VERSION:
        return _decode_single(document)
    if document['version'] != RESOLUTION_SCHEMA_VERSION:
        _fail('formato', 'esquema o versión desconocidos')
    required = {'schema', 'version', 'analysis', 'query', 'smoothing_state', 'resolution_state'}
    _keys(document, required, 'documento multirresolución')
    if document['schema'] != SCHEMA:
        _fail('formato', 'esquema desconocido')
    base = {key: document[key] for key in ('schema', 'analysis', 'query', 'smoothing_state')}
    base['version'] = SCHEMA_VERSION
    top = _decode_single(base)
    active, options, packed = _resolution_header(document['resolution_state'])
    variants = {}
    option_by_key = {option['key']: option for option in options}
    for key, variant in packed.items():
        _keys(variant, {'analysis', 'query', 'smoothing_state'}, 'variante ' + key)
        restored = _decode_single(dict(schema=SCHEMA, version=SCHEMA_VERSION, **variant))
        _close(min(restored.analysis.cell_width, restored.analysis.cell_height),
               option_by_key[key]['resolution'], 'resolución de la variante ' + key)
        metadata = restored.smoothing_state['resolution_metadata']
        if metadata and metadata['active_key'] != key:
            _fail('metadatos de la variante ' + key, 'clave de resolución diferente')
        if restored.query.period != top.query.period:
            _fail('consulta de la variante ' + key, 'el periodo no coincide con la consulta del proyecto')
        for field in ('flow', 'x', 'y'):
            _close(getattr(restored.query, field), getattr(top.query, field), 'consulta ' + field + ' de ' + key)
        if restored.query.crs != top.query.crs:
            reference, expected = osr.SpatialReference(), osr.SpatialReference()
            reference.ImportFromWkt(restored.query.crs.toWkt())
            expected.ImportFromWkt(top.query.crs.toWkt())
            if not reference.IsSame(expected):
                _fail('consulta de la variante ' + key, 'CRS diferente de la consulta del proyecto')
        variants[key] = dict(analysis=restored.analysis, query=restored.query,
                             smoothing_state=restored.smoothing_state)
    if '500' in variants:
        reference_analysis = variants['500']['analysis']
        reference_area = (reference_analysis.diagnostics.get('analysis_surface') or {}).get(
            'original_area_m2', reference_analysis.area_m2)
        for key, variant in variants.items():
            snap = variant['analysis'].diagnostics.get('outlet_snap')
            if snap is not None:
                _close(snap['reference_area_m2'], reference_area,
                       'área de referencia de la variante ' + key)
    selected = variants[active]
    if (selected['analysis'] != top.analysis or selected['query'] != top.query
            or selected['smoothing_state'] != top.smoothing_state):
        _fail('resolución activa', 'los resultados superiores no coinciden con su variante')
    state = dict(active_key=active, options=options, variants=variants)
    return SavedSession(top.analysis, top.query, top.smoothing_state, state)


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('JSON', 'clave duplicada')
        result[key] = value
    return result


def save_session(destination, analysis, query, smoothing_state, resolution_state=None):
    """Validate and atomically replace one user-chosen .caumax.json document."""
    destination = Path(destination)
    if not destination.parent.is_dir() or destination.is_dir() or destination.is_symlink():
        raise ValueError('El destino del proyecto de consulta no es un archivo local válido.')
    try:
        document = _document(analysis, query, smoothing_state)
        if resolution_state is not None:
            document['version'] = RESOLUTION_SCHEMA_VERSION
            document['resolution_state'] = _resolution_document(resolution_state)
        # Both read and write use the identical validation path. Fill missing
        # statistics from geometry measurements, never from terrain/rasters.
        checked = _decode(document)
        normalized = _document(checked.analysis, checked.query, checked.smoothing_state)
        if checked.resolution_state is not None:
            normalized['version'] = RESOLUTION_SCHEMA_VERSION
            normalized['resolution_state'] = _resolution_document(checked.resolution_state)
            for key, variant in normalized['resolution_state']['variants'].items():
                variant['query']['crs_wkt'] = document['resolution_state']['variants'][key]['query']['crs_wkt']
        # QGIS may normalize unnamed custom-CRS descriptions on construction;
        # retain the exact original WKT as part of the portable document.
        normalized['query']['crs_wkt'] = document['query']['crs_wkt']
    except (TypeError, AttributeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError('Los datos no forman un proyecto de consulta válido.') from error
    try:
        data = json.dumps(normalized, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError('No se puede serializar el proyecto de consulta.') from error
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('El proyecto de consulta supera el límite de 128 MiB.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.' + destination.name + '.',
                                         suffix='.tmp', dir=str(destination.parent), delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def load_session(path):
    """Recover all generated data without opening the original rasters."""
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('El proyecto de consulta supera el límite de 128 MiB.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('El proyecto de consulta supera el límite de 128 MiB.')
    try:
        document = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_no_duplicates,
                              parse_constant=lambda value: _fail('JSON', 'número no finito'))
        return _decode(document)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, OverflowError) as error:
        raise ValueError('El archivo no contiene un proyecto de consulta válido.') from error
