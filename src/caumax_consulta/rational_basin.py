"""Capture the completed native rational basin without moving its closure.

The native calculator retains its raster arrays after settings change. A path
alone is therefore not provenance: both configured rasters are compared with
those arrays, in bounded blocks, before a query is accepted. The JSON context
stores spatial/content hashes and native scalars, never arrays or background
polygons. Its declarative validator also works with offline saved projects.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsCoordinateReferenceSystem, QgsGeometry

from . import basin as _basin
from .caumax_bridge import NativeQuery, _number
from .grid import cell_at_point


_VERSION = 1
_MAX_AREA = 50_000_000.0
_MAX_JSON_BYTES = 40_000
_HASH = re.compile(r'^[0-9a-f]{64}$')
_PERIODS = (2, 5, 10, 25, 100, 500)
_NATIVE_FIELDS = {
    'tc_h': 'concentrationTime', 'length_m': 'maxDistance',
    'outlet_elevation_m': 'minH', 'farthest_elevation_m': 'maxH',
    'p0_mm': 'p0', 'i1id': 'i1id',
}
_CONTEXT_KEYS = {
    'version', 'fingerprint', 'mask_sha256', 'geometry_sha256', 'area_m2',
    'cell_count', 'window', 'geotransform', 'raster_shape', 'crs_wkt',
    'outlet', 'sources', 'native', 'flow_status', 'flow_result',
}


def _finite(value, label, *, minimum=None, maximum=None, optional=False):
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} debe ser un número finito.')
    value = float(value)
    if (not math.isfinite(value) or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)):
        raise ValueError(f'{label} está fuera de los límites válidos.')
    return value


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > 2**53:
        raise ValueError(f'{label} no es un entero válido.')
    return int(value)


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f'El esquema de {label} no es compatible.')


def _reference(wkt):
    if not isinstance(wkt, str) or not wkt or len(wkt) > 16_384:
        raise ValueError('La cuenca racional no tiene un CRS válido.')
    reference = osr.SpatialReference()
    try:
        valid = reference.ImportFromWkt(wkt) == 0
    except RuntimeError:
        valid = False
    if not valid or not reference.IsProjected() or not math.isclose(reference.GetLinearUnits(), 1.0):
        raise ValueError('El CRS de la cuenca racional debe ser proyectado y estar en metros.')
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _transform(value):
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise ValueError('La transformación del MDT racional no es válida.')
    result = [_finite(item, 'Transformación del MDT') for item in value]
    if result[1] <= 0 or result[5] >= 0 or result[2] or result[4]:
        raise ValueError('El MDT racional debe tener una malla norte-arriba sin rotación.')
    if not math.isclose(result[1], -result[5], rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError('La malla utilizada por CAUMAX debe tener celdas cuadradas.')
    return result


def _same_number(first, second):
    if first is None or second is None:
        return first is second
    return first == second or (math.isnan(float(first)) and math.isnan(float(second)))


def _canonical_path(value):
    if not isinstance(value, (str, os.PathLike)) or not os.fspath(value):
        raise ValueError('No se encuentra la ruta del ráster utilizado por CAUMAX.')
    return str(Path(value).expanduser().resolve())


def _identity(context):
    spatial = {key: context[key] for key in
               ('mask_sha256', 'window', 'geotransform', 'raster_shape', 'crs_wkt')}
    return hashlib.sha256(json.dumps(spatial, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def _mask_hash(mask):
    return hashlib.sha256(np.asarray(mask, dtype=np.uint8).tobytes(order='C')).hexdigest()


def _geometry_hash(geometry_wkb):
    geometry = ogr.CreateGeometryFromWkb(bytes(geometry_wkb))
    if (geometry is None or geometry.IsEmpty() or not geometry.IsValid()
            or ogr.GT_Flatten(geometry.GetGeometryType()) not in (ogr.wkbPolygon, ogr.wkbMultiPolygon)):
        raise ValueError('La geometría de la cuenca racional no es válida.')
    # Force one geometry type before normalizing ring and multipart ordering.
    geometry = ogr.ForceToMultiPolygon(geometry)
    return hashlib.sha256(bytes(geometry.Normalize().ExportToWkb(ogr.wkbNDR))).hexdigest()


def validate_rational_context(context):
    """Return a bounded JSON-only v1 copy, without reading any source file."""
    try:
        encoded = json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > _MAX_JSON_BYTES:
            raise ValueError('El contexto racional contiene demasiados metadatos.')
        result = json.loads(encoded)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError('El contexto racional debe ser JSON finito y acotado.') from error
    _keys(result, _CONTEXT_KEYS, 'la cuenca racional')
    if type(result['version']) is not int or result['version'] != _VERSION:
        raise ValueError('La versión del contexto racional no es compatible.')
    for key in ('fingerprint', 'mask_sha256', 'geometry_sha256'):
        if not isinstance(result[key], str) or not _HASH.fullmatch(result[key]):
            raise ValueError('La identidad de la cuenca racional no es válida.')
    result['area_m2'] = _finite(result['area_m2'], 'Área nativa', minimum=0, maximum=_MAX_AREA)
    if result['area_m2'] == 0:
        raise ValueError('La cuenca racional debe tener superficie positiva.')
    result['cell_count'] = _integer(result['cell_count'], 'Número de celdas', 1)
    shape = result['raster_shape']
    window = result['window']
    if not isinstance(shape, list) or len(shape) != 2 or not isinstance(window, list) or len(window) != 4:
        raise ValueError('Las dimensiones de la cuenca racional no son válidas.')
    result['raster_shape'] = shape = [_integer(v, 'Dimensión del ráster', 1) for v in shape]
    result['window'] = window = [_integer(v, 'Ventana de la cuenca', int(i >= 2)) for i, v in enumerate(window)]
    xoff, yoff, width, height = window
    if xoff + width > shape[1] or yoff + height > shape[0] or result['cell_count'] > width * height:
        raise ValueError('La máscara de la cuenca está fuera de la malla original.')
    result['geotransform'] = transform = _transform(result['geotransform'])
    _reference(result['crs_wkt'])
    expected_area = result['cell_count'] * transform[1] * -transform[5]
    if not math.isclose(result['area_m2'], expected_area, rel_tol=1e-10, abs_tol=1e-5):
        raise ValueError('El área no coincide con la máscara de celdas racional.')
    outlet = result['outlet']
    _keys(outlet, {'x', 'y', 'row', 'col', 'center_x', 'center_y'}, 'el cierre racional')
    for key in ('x', 'y', 'center_x', 'center_y'):
        outlet[key] = _finite(outlet[key], 'Coordenada de cierre')
    for key in ('row', 'col'):
        outlet[key] = _integer(outlet[key], 'Índice de cierre')
    cell = cell_at_point(outlet['x'], outlet['y'], transform[0], transform[3] + shape[0] * transform[5],
                         transform[0] + shape[1] * transform[1], transform[3], shape[1], shape[0])
    if (cell.row != outlet['row'] or cell.col != outlet['col']
            or not math.isclose(cell.x, outlet['center_x'], rel_tol=0, abs_tol=1e-7)
            or not math.isclose(cell.y, outlet['center_y'], rel_tol=0, abs_tol=1e-7)
            or not xoff <= cell.col < xoff + width or not yoff <= cell.row < yoff + height):
        raise ValueError('El punto de cierre no coincide con la cuenca racional.')
    _keys(result['sources'], {'dem', 'directions'}, 'las fuentes racionales')
    for source in result['sources'].values():
        _keys(source, {'path', 'sha256', 'dtype', 'size_bytes', 'mtime_ns'}, 'una fuente racional')
        if (not isinstance(source['path'], str) or not source['path'].strip()
                or len(source['path']) > 4096 or '\x00' in source['path']
                or not (Path(source['path']).is_absolute() or PureWindowsPath(source['path']).is_absolute())):
            raise ValueError('La ruta de una fuente racional no es válida.')
        if not isinstance(source['sha256'], str) or not _HASH.fullmatch(source['sha256']):
            raise ValueError('La huella de una fuente racional no es válida.')
        if not isinstance(source['dtype'], str) or not re.fullmatch(r'[<>=|][biuf][1248]', source['dtype']):
            raise ValueError('El tipo de datos de una fuente racional no es válido.')
        source['size_bytes'] = _integer(source['size_bytes'], 'Tamaño de fuente', 1)
        # Nanosecond timestamps legitimately exceed the exact JSON-double range.
        if isinstance(source['mtime_ns'], bool) or not isinstance(source['mtime_ns'], int) or not 0 <= source['mtime_ns'] < 2**63:
            raise ValueError('La fecha de una fuente racional no es válida.')
    native = result['native']
    _keys(native, {'area_m2', 'rainfall_mm', *_NATIVE_FIELDS}, 'los parámetros nativos')
    native['area_m2'] = _finite(native['area_m2'], 'Área nativa', minimum=0, maximum=_MAX_AREA)
    if native['area_m2'] != result['area_m2']:
        raise ValueError('Las superficies nativas no coinciden.')
    for key in _NATIVE_FIELDS:
        native[key] = _finite(native[key], 'Parámetro nativo', optional=True,
                              minimum=None if key.endswith('elevation_m') else 0)
    if not isinstance(native['rainfall_mm'], dict) or not set(native['rainfall_mm']).issubset(map(str, _PERIODS)):
        raise ValueError('Los períodos de precipitación nativa no son válidos.')
    native['rainfall_mm'] = {key: _finite(value, 'Precipitación nativa', minimum=0)
                             for key, value in native['rainfall_mm'].items()}
    if result['flow_status'] == 'pending':
        if result['flow_result'] is not None:
            raise ValueError('Un caudal pendiente no puede tener un resultado.')
    elif result['flow_status'] == 'calculated':
        _keys(result['flow_result'], {'flow', 'period'}, 'el resultado racional')
        result['flow_result'] = {
            'flow': _finite(result['flow_result']['flow'], 'Caudal racional', minimum=0),
            'period': _finite(result['flow_result']['period'], 'Período racional', minimum=2, maximum=500),
        }
    else:
        raise ValueError('El estado del caudal racional no es válido.')
    if _identity(result) != result['fingerprint']:
        raise ValueError('La huella no coincide con el contexto de la cuenca racional.')
    return result


def _layer_provider(dialog, calculator):
    for obj in (calculator, dialog):
        for cls in type(obj).__mro__:
            name = cls.__module__
            for candidate in (name, name.rsplit('.', 1)[0] + '.layers'):
                module = sys.modules.get(candidate)
                if (module is not None and callable(getattr(module, 'getLayerPath', None))
                        and hasattr(module, 'MDT') and hasattr(module, 'FLOWDIRS')):
                    return module
    raise ValueError('No se encuentra la configuración nativa de MDT y direcciones de CAUMAX.')


def _verified_source(path, array, nodata, transform, reference, shape):
    """Compare every loaded value, not only the basin crop or sampled cells."""
    path = _canonical_path(path)
    before = Path(path).stat()
    dataset = _basin._open_raster(path, 'cálculo racional')
    try:
        if shape is None:
            # Revalidation hashes the source's own dimensions. The stored hash
            # still rejects any change, including outside the basin window.
            shape = (dataset.RasterYSize, dataset.RasterXSize)
        if (dataset.RasterYSize, dataset.RasterXSize) != tuple(shape):
            raise ValueError('Las dimensiones de las capas han cambiado; vuelve a calcular las variables intermedias.')
        if not np.allclose(dataset.GetGeoTransform(), transform, rtol=0, atol=1e-9):
            raise ValueError('La malla de las capas ha cambiado; vuelve a calcular las variables intermedias.')
        if not reference.IsSame(_reference(dataset.GetProjection())):
            raise ValueError('El CRS de las capas no coincide con el utilizado por CAUMAX.')
        band = dataset.GetRasterBand(1)
        if array is not None and not _same_number(band.GetNoDataValue(), nodata):
            raise ValueError('El NoData de las capas ha cambiado; vuelve a calcular las variables intermedias.')
        digest = hashlib.sha256()
        dtype = None
        block_rows = max(1, min(256, 1_048_576 // shape[1]))
        for offset in range(0, shape[0], block_rows):
            count = min(block_rows, shape[0] - offset)
            data = band.ReadAsArray(0, offset, shape[1], count)
            if data is None:
                raise ValueError('No se puede verificar la fuente utilizada por CAUMAX.')
            if dtype is None:
                dtype = data.dtype.str
                # A NoData/CRS/grid edit can change terrain results even when
                # the raw sample bytes are identical.
                header = [dtype, list(shape), list(dataset.GetGeoTransform()),
                          _reference(dataset.GetProjection()).ExportToWkt(),
                          repr(band.GetNoDataValue()), repr(band.GetScale()), repr(band.GetOffset())]
                digest.update(json.dumps(header, separators=(',', ':')).encode('ascii'))
            if array is not None and (data.dtype != array.dtype or not np.array_equal(data, array[offset:offset + count], equal_nan=True)):
                raise ValueError('Las capas configuradas no corresponden a la cuenca calculada; vuelve a calcular las variables intermedias.')
            digest.update(data.tobytes(order='C'))
    finally:
        dataset = None
    after = Path(path).stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Una capa cambió durante la captura de la cuenca racional.')
    return {'path': path, 'sha256': digest.hexdigest(), 'dtype': dtype,
            'size_bytes': after.st_size, 'mtime_ns': after.st_mtime_ns}


def _period(dialog, flow_result):
    ordinary = getattr(dialog, 'chkOrdinaryMaxFlood', None)
    ordinary = bool(ordinary is not None and ordinary.isChecked())
    if flow_result is not None:
        _keys(flow_result, {'flow', 'period'}, 'el resultado racional explícito')
        period = _finite(flow_result['period'], 'Período racional', minimum=2, maximum=500)
        if not ordinary and not math.isclose(_number(dialog.comboReturnPeriod.currentText()), period, rel_tol=0, abs_tol=1e-7):
            raise ValueError('El período ha cambiado desde el último cálculo racional.')
        return period
    if ordinary:
        return None
    return _finite(_number(dialog.comboReturnPeriod.currentText()), 'Período racional', minimum=2, maximum=500)


def capture_rational_query(dialog, *, flow_result=None, parameters_validated=False):
    """Freeze one verified native basin; Q is pending unless explicitly supplied.

    ``parameters_validated`` is reserved for the cooperative bridge after it
    observed successful intermediate calculation. It allows T edits (which
    disable the native Calculate button) without accepting stale XY or rasters.
    """
    calculator = getattr(dialog, 'basinCalc', None)
    button = getattr(dialog, 'btnCalculate', None)
    if calculator is None or (not parameters_validated and (button is None or not button.isEnabled())):
        raise ValueError("Primero pulsa 'Calcular variables intermedias' en CAUMAX.")
    point = getattr(calculator, 'pt', None)
    if point is None:
        raise ValueError('No existe un punto de cálculo racional válido.')
    x, y = _finite(float(point.x()), 'Coordenada X'), _finite(float(point.y()), 'Coordenada Y')
    for entered, actual in ((dialog.txtX.text(), x), (dialog.txtY.text(), y)):
        if not math.isclose(_number(entered), actual, rel_tol=0, abs_tol=1e-7):
            raise ValueError('Las coordenadas han cambiado; vuelve a calcular las variables intermedias.')
    dem, directions = np.asarray(calculator.mdt), np.asarray(calculator.dirs)
    mask = np.asarray(calculator.basinCells)
    if (dem.ndim != 2 or dem.size == 0 or directions.ndim != 2
            or directions.size == 0 or mask.shape != dem.shape):
        raise ValueError('La máscara racional no corresponde a las capas cargadas.')
    if mask.dtype.kind not in 'biuf' or not np.all((mask == 0) | (mask == 1)):
        raise ValueError('La máscara racional debe contener exclusivamente celdas 0 y 1.')
    row_indices = np.flatnonzero(np.any(mask != 0, axis=1))
    col_indices = np.flatnonzero(np.any(mask != 0, axis=0))
    if not row_indices.size or not col_indices.size:
        raise ValueError('La cuenca racional no contiene celdas.')
    yoff, last_row = int(row_indices[0]), int(row_indices[-1])
    xoff, last_col = int(col_indices[0]), int(col_indices[-1])
    if last_row >= directions.shape[0] or last_col >= directions.shape[1]:
        raise ValueError('Las direcciones de drenaje no cubren toda la cuenca racional calculada.')
    window = [xoff, yoff, last_col - xoff + 1, last_row - yoff + 1]
    crop = np.asarray(mask[yoff:last_row + 1, xoff:last_col + 1], dtype=np.uint8)
    count = int(np.count_nonzero(crop))
    transform = _transform(calculator.geoTransform)
    reference = _reference(calculator.crs)
    crs_wkt = reference.ExportToWkt()
    area = _finite(float(calculator.area), 'Área nativa', minimum=0, maximum=_MAX_AREA)
    if area <= 0 or not math.isclose(area, count * transform[1] * -transform[5], rel_tol=1e-10, abs_tol=1e-5):
        raise ValueError('El área nativa no coincide con la máscara de la cuenca racional.')
    cell = cell_at_point(x, y, transform[0], transform[3] + dem.shape[0] * transform[5],
                         transform[0] + dem.shape[1] * transform[1], transform[3], dem.shape[1], dem.shape[0])
    if mask[cell.row, cell.col] != 1:
        raise ValueError('El punto de cálculo no pertenece a la cuenca racional.')
    local_transform = (transform[0] + xoff * transform[1], transform[1], 0,
                       transform[3] + yoff * transform[5], 0, transform[5])
    geometry_wkb, _ = _basin._polygonize(crop, local_transform, reference, lambda: False)
    native = [QgsGeometry(geom) for geom in getattr(calculator, 'basinGeometry', ())
              if not geom.isEmpty() and len(geom.asPolygon()) == 1]
    expected = QgsGeometry()
    expected.fromWkb(geometry_wkb)
    if not native or not expected.isGeosEqual(QgsGeometry.unaryUnion(native)):
        raise ValueError('La divisoria nativa no coincide con su máscara; vuelve a calcular las variables intermedias.')
    provider = _layer_provider(dialog, calculator)
    try:
        sources = {
            'dem': _verified_source(provider.getLayerPath(provider.MDT), dem, getattr(calculator, 'nodataMdt', None), transform, reference, dem.shape),
            'directions': _verified_source(provider.getLayerPath(provider.FLOWDIRS), directions, getattr(calculator, 'nodataDirs', None), transform, reference, directions.shape),
        }
    except (OSError, RuntimeError) as error:
        raise ValueError('No se pudieron verificar las capas originales de la cuenca racional.') from error
    period = _period(dialog, flow_result)
    flow = None if flow_result is None else _finite(flow_result['flow'], 'Caudal racional', minimum=0)
    native_values = {key: (None if getattr(calculator, attribute, None) is None else float(getattr(calculator, attribute)))
                     for key, attribute in _NATIVE_FIELDS.items()}
    native_values.update(area_m2=area, rainfall_mm={str(int(key)): float(value)
                          for key, value in getattr(calculator, 'rain', {}).items()})
    context = {
        'version': _VERSION, 'mask_sha256': _mask_hash(crop), 'geometry_sha256': _geometry_hash(geometry_wkb),
        'area_m2': area, 'cell_count': count, 'window': window, 'geotransform': transform,
        'raster_shape': list(dem.shape), 'crs_wkt': crs_wkt,
        'outlet': {'x': x, 'y': y, 'row': cell.row, 'col': cell.col, 'center_x': cell.x, 'center_y': cell.y},
        'sources': sources, 'native': native_values,
        'flow_status': 'pending' if flow is None else 'calculated',
        'flow_result': None if flow is None else {'flow': flow, 'period': period},
    }
    context['fingerprint'] = _identity(context)
    context = validate_rational_context(context)
    return NativeQuery(flow=flow, period=period, x=x, y=y,
                       crs=QgsCoordinateReferenceSystem.fromWkt(crs_wkt),
                       directions_path=sources['directions']['path'], dem_path=sources['dem']['path'],
                       layername='Cuenca · Método racional', cell=cell,
                       source_kind='rational', rational_context=context)


def validate_rational_analysis(analysis, query):
    """Reject any changed initial basin before it can replace the native one."""
    if getattr(query, 'source_kind', 'caumax_raster') != 'rational':
        return analysis
    context = validate_rational_context(query.rational_context)
    reference = _reference(context['crs_wkt'])
    if not reference.IsSame(_reference(analysis.crs_wkt)):
        raise ValueError('El análisis no conserva el CRS de la cuenca racional.')
    for key, value in (('x', query.x), ('y', query.y)):
        if not math.isclose(float(value), context['outlet'][key], rel_tol=0, abs_tol=1e-7):
            raise ValueError('El punto consultado no coincide con la cuenca racional capturada.')
    result = context['flow_result']
    if ((result is None and query.flow is not None)
            or (result is not None and (query.flow != result['flow'] or query.period != result['period']))):
        raise ValueError('El caudal no corresponde al resultado racional capturado.')
    for kind, path in (('dem', query.dem_path), ('directions', query.directions_path)):
        source = context['sources'][kind]
        if _canonical_path(path) != source['path']:
            raise ValueError('El análisis está utilizando otras capas de cálculo racional.')
        try:
            stat = Path(path).stat()
            if (stat.st_size, stat.st_mtime_ns) != (source['size_bytes'], source['mtime_ns']):
                current = _verified_source(path, None, None, context['geotransform'], reference, None)
                if current['sha256'] != source['sha256']:
                    raise ValueError('Las capas han cambiado desde la captura de la cuenca racional.')
        except OSError as error:
            raise ValueError('No se encuentran las capas originales de la cuenca racional.') from error
    if (analysis.cell_count != context['cell_count']
            or not math.isclose(analysis.area_m2, context['area_m2'], rel_tol=1e-10, abs_tol=1e-5)
            or not math.isclose(analysis.cell_width, context['geotransform'][1], rel_tol=0, abs_tol=1e-9)
            or not math.isclose(analysis.cell_height, -context['geotransform'][5], rel_tol=0, abs_tol=1e-9)
            or not math.isclose(analysis.outlet_x, context['outlet']['center_x'], rel_tol=0, abs_tol=1e-7)
            or not math.isclose(analysis.outlet_y, context['outlet']['center_y'], rel_tol=0, abs_tol=1e-7)):
        raise ValueError('El análisis cambiaría el cierre, el área o las celdas de la cuenca racional.')
    if _geometry_hash(analysis.geometry_wkb) != context['geometry_sha256']:
        raise ValueError('La geometría del análisis no coincide con la cuenca racional original.')
    xoff, yoff, width, height = context['window']
    transform = context['geotransform']
    raster = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform((transform[0] + xoff * transform[1], transform[1], 0,
                            transform[3] + yoff * transform[5], 0, transform[5]))
    raster.SetProjection(context['crs_wkt'])
    vector = ogr.GetDriverByName('Memory').CreateDataSource('')
    layer = vector.CreateLayer('basin', reference, ogr.wkbMultiPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.ForceToMultiPolygon(ogr.CreateGeometryFromWkb(bytes(analysis.geometry_wkb))))
    layer.CreateFeature(feature)
    if gdal.RasterizeLayer(raster, [1], layer, burn_values=[1]) != 0:
        raise ValueError('No se pudo comprobar la máscara de la cuenca racional.')
    mask = raster.GetRasterBand(1).ReadAsArray()
    if int(np.count_nonzero(mask)) != context['cell_count'] or _mask_hash(mask) != context['mask_sha256']:
        raise ValueError('El análisis no conserva la máscara exacta de la cuenca racional.')
    return analysis
