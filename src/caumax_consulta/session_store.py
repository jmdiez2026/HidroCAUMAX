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

import numpy as np
from osgeo import gdal, ogr, osr
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
# Same maximum as the fine-resolution engine; never allocate this many
# pixels together when verifying a portable refinement proof.
MAX_REFERENCE_CELLS = 60_000_000
REFERENCE_BLOCK_CELLS = 1_048_576
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


def _outlet_snap(diagnostics, basin, selected_x, selected_y, boundary, budget=None):
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
                'candidate_touches_boundary', 'tie_break', 'reference', 'policy',
                'selection_mode', 'original_direction', 'section_direction',
                'section_direction_source', 'section_source_cell', 'section_offset_m',
                'upstream_path', 'upstream_path_length_m', 'channel_filter',
                'local_median_upstream_area_m2', 'local_concentration_factor',
                'local_minimum_cells', 'reference_minimum_upstream_area_m2',
                'reference_threshold_met', 'area_change_vs_reference_pct',
                'area_change_vs_original_cell_m2', 'main_course_length_m',
                'main_course_cells', 'main_course_click_station_m',
                'main_course_selected_station_m', 'channel_anchor_row',
                'channel_anchor_col', 'channel_anchor_x', 'channel_anchor_y',
                'channel_anchor_upstream_area_m2', 'downstream_connection_path',
                'section_projection_x', 'section_projection_y',
                'association_lateral_distance_m', 'association_trace_length_m',
                'association_method', 'association_incoming_area_m2',
                'association_other_inflow_area_m2', 'explicit_user_choice',
                'manual_requested_x', 'manual_requested_y', 'manual_selection_scope',
                'manual_raster_bounds', 'manual_raster_size', 'projection_kind',
                'branch_click_station_m', 'branch_selected_station_m',
                'branch_path_length_m', 'branch_path_cells', 'branch_distance_m',
                'branch_runner_up_distance_m', 'branch_ambiguity_tolerance_m'}
    refinement = isinstance(snap, dict) and snap.get('method') == 'reference_basin_d8'
    if refinement:
        # The spatial proof has its own schema. Its fields must not become
        # optional, unchecked additions to the older snapping methods.
        extra = {'selection_mode', 'original_row', 'original_col', 'snapped_row',
                 'snapped_col', 'candidate_count', 'original_cell_upstream_area_m2',
                 'candidate_touches_boundary', 'tie_break', 'policy',
                 'area_change_vs_reference_pct', 'area_change_vs_original_cell_m2',
                 'reference_geometry_wkb_hex', 'reference_crs_wkt',
                 'reference_geometry_area_m2', 'reference_raster_area_m2',
                 'reference_grid_bounds', 'reference_grid_size',
                 'intersection_area_m2', 'reference_coverage_fraction',
                 'candidate_overlap_fraction', 'intersection_over_union',
                 'minimum_reference_coverage', 'minimum_candidate_overlap',
                 'reference_policy_version', 'projection_kind',
                 'reference_course_wkb_hex', 'branch_distance_m',
                 'branch_runner_up_distance_m', 'branch_ambiguity_tolerance_m'}
        refinement_optional = {'reference',
                               'section_source_cell', 'section_target_cell',
                               'section_projection_x', 'section_projection_y',
                               'section_offset_m', 'coarse_upstream_distance_m'}
        if snap.get('reference_policy_version') == 2:
            extra |= {'selection_policy', 'candidate_chain_cells'}
        _keys(snap, required | extra, 'ajuste por cuenca de referencia', refinement_optional)
    else:
        _keys(snap, required, 'ajuste del cierre', optional)
    upstream = snap['method'] == 'upstream_section_d8'
    manual = snap['method'] == 'manual_cell_d8'
    if snap['method'] not in ('nearest_main_channel_d8', 'upstream_section_d8', 'manual_cell_d8', 'reference_basin_d8') or snap['status'] not in ('snapped', 'unchanged'):
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
    extent_manual = manual and snap.get('manual_selection_scope') == 'raster_extent'
    if not extent_manual and distance > radius + 1e-5:
        _fail('ajuste del cierre', 'la distancia supera el radio de búsqueda')
    reference = _float(snap['reference_area_m2'], 'área de referencia', positive=True)
    minimum = _float(snap['minimum_upstream_area_m2'], 'área mínima de cauce', positive=True)
    candidate = _float(snap['candidate_upstream_area_m2'], 'área acumulada del cierre', positive=True)
    _close(candidate, basin.area_m2, 'área acumulada de la cuenca ajustada')
    if (not upstream and not manual and not refinement and minimum > reference) or candidate + max(1e-5, candidate * 1e-7) < minimum:
        _fail('ajuste del cierre', 'áreas de selección incompatibles')
    if 'minimum_fraction' in snap:
        fraction = _float(snap['minimum_fraction'], 'fracción mínima de cauce', positive=True)
        if fraction > 1:
            _fail('ajuste del cierre', 'fracción mínima superior a uno')
        if upstream or manual:
            _close(snap.get('reference_minimum_upstream_area_m2'), reference * fraction,
                   'umbral informativo de referencia')
            if _bool(snap.get('reference_threshold_met'), 'comparación del área de referencia') != (candidate >= reference * fraction):
                _fail('ajuste del cierre', 'comparación con el área de referencia incompatible')
        else:
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
    expected_tie = ('nearest_reference_chain_cell' if refinement and snap.get('reference_policy_version') == 2 else
                    'nearest_reference_section' if refinement else
                    'nearest_branch_geometry' if snap.get('selection_mode') in
                    ('upstream_nearest_branch', 'upstream_lateral_nearest_branch') else
                    'main_course_d8_order' if snap.get('selection_mode') in
                    ('upstream_main_course', 'upstream_lateral_main_course') else 'row_col')
    if 'tie_break' in snap and snap['tie_break'] != expected_tie:
        _fail('ajuste del cierre', 'desempate desconocido')
    for key in ('reference', 'policy'):
        if key in snap:
            _text(snap[key], 'ajuste ' + key, 4096)
    if upstream:
        _upstream_section_record(snap, basin, selected_x, selected_y)
    elif manual:
        _manual_outlet_record(snap, basin, selected_x, selected_y)
    elif refinement:
        _reference_basin_record(snap, basin, selected_x, selected_y,
                                [0] if budget is None else budget)
    return True


def _reference_wkb(value, label, allowed_types, budget, allow_empty=False):
    text = _text(value, label, MAX_WKB_BYTES * 2)
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        _fail(label, 'codificación hexadecimal no válida')
    if not raw or len(raw) * 2 != len(text):
        _fail(label, 'WKB vacío o codificación hexadecimal no canónica')
    return _wkb(raw, label, allowed_types, budget, allow_empty=allow_empty)


def _reference_mask_counts(reference, candidate, bounds, size, crs_wkt):
    """Rebuild the two centre-rasterized masks without opening source rasters.

    Strips contain at most REFERENCE_BLOCK_CELLS pixels even for a highly
    elongated grid. Total work is bounded by MAX_REFERENCE_CELLS, and the
    native geometries, rather than a smoothed display contour, define C/I.
    """
    cols, rows = size
    xmin, ymin, xmax, ymax = bounds
    width, height = (xmax - xmin) / cols, (ymax - ymin) / rows
    envelopes = (reference.GetEnvelope(), candidate.GetEnvelope())
    west, east = min(e[0] for e in envelopes), max(e[1] for e in envelopes)
    south, north = min(e[2] for e in envelopes), max(e[3] for e in envelopes)
    first_col = max(0, math.floor((west - xmin) / width) - 1)
    last_col = min(cols, math.ceil((east - xmin) / width) + 1)
    first_row = max(0, math.floor((ymax - north) / height) - 1)
    last_row = min(rows, math.ceil((ymax - south) / height) + 1)
    block_cols = min(last_col - first_col, REFERENCE_BLOCK_CELLS)
    block_rows = max(1, REFERENCE_BLOCK_CELLS // block_cols)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(crs_wkt)
    source = ogr.GetDriverByName('Memory').CreateDataSource('')
    layers = []
    for name, geometry in (('reference', reference), ('candidate', candidate)):
        layer = source.CreateLayer(name, srs=srs, geom_type=ogr.wkbUnknown)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)
        if layer.CreateFeature(feature) != 0:
            _fail('referencia espacial', 'no se puede reconstruir la geometría')
        feature = None
        layers.append(layer)
    totals = [0, 0, 0]
    raster = None
    try:
        for row in range(first_row, last_row, block_rows):
            for col in range(first_col, last_col, block_cols):
                nx, ny = min(block_cols, last_col - col), min(block_rows, last_row - row)
                raster = gdal.GetDriverByName('MEM').Create('', nx, ny, 2, gdal.GDT_Byte)
                if raster is None:
                    _fail('referencia espacial', 'no se puede reservar un bloque de comprobación')
                raster.SetGeoTransform((xmin + col * width, width, 0,
                                        ymax - row * height, 0, -height))
                raster.SetProjection(crs_wkt)
                for band, layer in enumerate(layers, 1):
                    layer.ResetReading()
                    if gdal.RasterizeLayer(raster, [band], layer, burn_values=[1],
                                           options=['ALL_TOUCHED=FALSE']) != 0:
                        _fail('referencia espacial', 'no se puede rasterizar la comprobación')
                ref = raster.GetRasterBand(1).ReadAsArray()
                chosen = raster.GetRasterBand(2).ReadAsArray()
                totals[0] += int(np.count_nonzero(ref))
                totals[1] += int(np.count_nonzero(chosen))
                totals[2] += int(np.count_nonzero(ref & chosen))
                ref = chosen = raster = None
    except RuntimeError as error:
        _fail('referencia espacial', str(error))
    finally:
        raster = layers = source = None
    return tuple(totals)


def _reference_basin_record(snap, basin, selected_x, selected_y, budget):
    """Validate a self-contained snapshot of the coarse reference and its grid."""
    from .grid import cell_at_point

    version = _int(snap['reference_policy_version'], 'versión de referencia', 1, 2)
    if snap['selection_mode'] != 'reference_basin_overlap':
        _fail('referencia espacial', 'método o versión desconocidos')
    for key in ('minimum_reference_coverage', 'minimum_candidate_overlap'):
        if _float(snap[key], 'referencia ' + key) != 0.5:
            _fail('referencia espacial', 'la política requiere mayoría espacial estricta')
    bounds, size = snap['reference_grid_bounds'], snap['reference_grid_size']
    if (not isinstance(bounds, list) or len(bounds) != 4
            or not isinstance(size, list) or len(size) != 2):
        _fail('malla de referencia', 'extensión o dimensiones incompletas')
    bounds = [_float(value, 'extensión de referencia') for value in bounds]
    cols, rows = (_int(value, 'dimensión de referencia', 1, MAX_REFERENCE_CELLS) for value in size)
    if (cols * rows > MAX_REFERENCE_CELLS
            or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
        _fail('malla de referencia', 'extensión inválida o demasiadas celdas')
    _point_close(bounds[2] - bounds[0], cols * basin.cell_width, 'ancho de malla de referencia')
    _point_close(bounds[3] - bounds[1], rows * basin.cell_height, 'alto de malla de referencia')
    _int(snap['candidate_count'], 'candidatos de refinamiento', 1, cols * rows)
    try:
        original = cell_at_point(selected_x, selected_y, *bounds, cols, rows)
        chosen = cell_at_point(basin.outlet_x, basin.outlet_y, *bounds, cols, rows)
    except ValueError:
        _fail('malla de referencia', 'el clic o el cierre queda fuera del MDT')
    if ((original.row, original.col) != (snap['original_row'], snap['original_col'])
            or (chosen.row, chosen.col) != (snap['snapped_row'], snap['snapped_col'])):
        _fail('malla de referencia', 'los índices no corresponden a la malla declarada')
    _point_close(chosen.x, basin.outlet_x, 'centro de referencia X')
    _point_close(chosen.y, basin.outlet_y, 'centro de referencia Y')
    reference_crs = _crs(snap['reference_crs_wkt'], 'CRS de referencia espacial', metre_projection=True)
    expected_crs = QgsCoordinateReferenceSystem.fromWkt(basin.crs_wkt)
    if reference_crs != expected_crs:
        source, expected = osr.SpatialReference(), osr.SpatialReference()
        source.ImportFromWkt(snap['reference_crs_wkt'])
        expected.ImportFromWkt(basin.crs_wkt)
        if not source.IsSame(expected):
            _fail('referencia espacial', 'la geometría no usa el CRS de la cuenca')
    reference = _reference_wkb(snap['reference_geometry_wkb_hex'], 'divisoria de referencia', {3, 6}, budget)
    candidate = ogr.CreateGeometryFromWkb(basin.geometry_wkb)
    _close(_float(snap['reference_geometry_area_m2'], 'área geométrica de referencia', positive=True),
           reference.GetArea(), 'área geométrica de referencia')
    for geometry in (reference, candidate):
        envelope = geometry.GetEnvelope()
        if (envelope[0] < bounds[0] - 1e-5 or envelope[1] > bounds[2] + 1e-5
                or envelope[2] < bounds[1] - 1e-5 or envelope[3] > bounds[3] + 1e-5):
            _fail('referencia espacial', 'la divisoria rebasa la malla declarada')
    cell_area = basin.cell_width * basin.cell_height
    _close(snap['minimum_upstream_area_m2'], cell_area, 'celda mínima de refinamiento')
    counts = []
    for key in ('reference_raster_area_m2', 'candidate_upstream_area_m2', 'intersection_area_m2'):
        area = _float(snap[key], 'referencia ' + key, positive=True)
        count = area / cell_area
        if not math.isclose(count, round(count), rel_tol=0, abs_tol=1e-6):
            _fail('referencia espacial', 'las áreas discretas deben corresponder a celdas completas')
        counts.append(int(round(count)))
    ref_count, candidate_count, intersection_count = counts
    if (not 0 < intersection_count <= min(ref_count, candidate_count)
            or max(ref_count, candidate_count) > cols * rows or candidate_count != basin.cell_count):
        _fail('referencia espacial', 'recuentos de celdas incompatibles')
    expected = (intersection_count / ref_count, intersection_count / candidate_count,
                intersection_count / (ref_count + candidate_count - intersection_count))
    for key, fraction in zip(('reference_coverage_fraction', 'candidate_overlap_fraction',
                              'intersection_over_union'), expected):
        value = _float(snap[key], 'referencia ' + key, positive=True)
        if value > 1 or not math.isclose(value, fraction, rel_tol=1e-9, abs_tol=1e-12):
            _fail('referencia espacial', 'fracción de coincidencia incompatible con las áreas')
    if expected[0] <= 0.5 or expected[1] <= 0.5:
        _fail('referencia espacial', 'no existe mayoría espacial en ambas cuencas')
    _close(snap['area_change_vs_original_cell_m2'],
           snap['candidate_upstream_area_m2'] - snap['original_cell_upstream_area_m2'],
           'variación de área tras refinamiento')
    _close(snap['area_change_vs_reference_pct'],
           100 * (snap['candidate_upstream_area_m2'] / snap['reference_area_m2'] - 1),
           'variación de área de referencia histórica')
    if version == 2:
        _reference_nearest_chain_record(snap, basin, reference, bounds, (cols, rows), budget)
    else:
        _reference_section_record(snap, basin, reference, bounds, (cols, rows), budget)
    # Recompute the proof, not merely its reported ratios. A valid older
    # reference snapshot remains valid after a later manual 500m choice.
    actual = _reference_mask_counts(reference, candidate, bounds, (cols, rows), snap['reference_crs_wkt'])
    if actual != tuple(counts):
        _fail('referencia espacial', 'la coincidencia declarada no corresponde a las geometrías rasterizadas')


def _reference_nearest_chain_record(snap, basin, reference, bounds, size, budget):
    """Validate the rational nearest-centre proof without reopening rasters."""
    from .basin import DIRECTIONS

    if (snap['selection_policy'] != 'nearest_reference_chain_cell'
            or snap['projection_kind'] != 'nearest_chain_cell'):
        _fail('cadena de referencia', 'política o proyección desconocidas')
    forbidden = {'section_source_cell', 'section_target_cell', 'section_projection_x',
                 'section_projection_y', 'section_offset_m', 'coarse_upstream_distance_m'}
    if forbidden & set(snap):
        _fail('cadena de referencia', 'la elección de centro no usa una sección ortogonal')
    raw_course = snap['reference_course_wkb_hex']
    if raw_course != '':
        course = _reference_wkb(raw_course, 'curso de referencia', {2}, budget)
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint_2D(*course.GetPoint_2D(0))
        if not reference.Intersects(point):
            _fail('cadena de referencia', 'el inicio del curso queda fuera de su cuenca')
    epsilon = max(basin.cell_width, basin.cell_height) * 1e-7
    if not math.isclose(_float(snap['branch_ambiguity_tolerance_m'], 'tolerancia numérica'),
                        epsilon, rel_tol=1e-10, abs_tol=1e-12):
        _fail('cadena de referencia', 'la tolerancia de desempate no es numérica')
    cols, rows = size
    chain = snap['candidate_chain_cells']
    if not isinstance(chain, list) or not 1 <= len(chain) <= min(cols * rows, MAX_METADATA_ITEMS):
        _fail('cadena de referencia', 'lista de celdas ausente o demasiado grande')
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    cell_area = basin.cell_width * basin.cell_height
    reference_count = round(snap['reference_raster_area_m2'] / cell_area)
    seen, candidates = set(), []
    previous = None
    for index, entry in enumerate(chain):
        if not isinstance(entry, list) or len(entry) != 5:
            _fail('cadena de referencia', 'celda D8 o recuentos incompletos')
        row = _int(entry[0], 'fila de cadena', 0, rows - 1)
        col = _int(entry[1], 'columna de cadena', 0, cols - 1)
        code = _int(entry[2], 'dirección de cadena', 0, 128)
        if code not in set(offsets) | {0} or (row, col) in seen:
            _fail('cadena de referencia', 'dirección desconocida o ciclo D8')
        seen.add((row, col))
        count = _int(entry[3], 'acumulación de cadena', 1, cols * rows)
        overlap = _int(entry[4], 'intersección de cadena', 0, min(count, reference_count))
        if previous is not None:
            pr, pc, direction, pcnt, poverlap = previous
            if direction not in offsets:
                _fail('cadena de referencia', 'una celda intermedia carece de salida D8')
            dr, dc = offsets[direction]
            if (pr + dr, pc + dc) != (row, col):
                _fail('cadena de referencia', 'las celdas no forman una única cadena D8')
            if count <= pcnt or not 0 <= overlap - poverlap <= count - pcnt:
                _fail('cadena de referencia', 'recuentos incompatibles con la acumulación D8')
        previous = row, col, code, count, overlap
        cx = bounds[0] + (col + .5) * basin.cell_width
        cy = bounds[3] - (row + .5) * basin.cell_height
        distance = math.hypot(cx - snap['original_x'], cy - snap['original_y'])
        if distance > snap['search_radius_m'] + epsilon:
            _fail('cadena de referencia', 'una celda queda fuera del radio de búsqueda')
        qualified = 2 * overlap > reference_count and 2 * overlap > count
        if index in (0, len(chain) - 1) and not qualified:
            _fail('cadena de referencia', 'los extremos no son candidatos admisibles')
        if qualified:
            candidates.append((distance, count, row, col, overlap))
        if (row, col) == (snap['original_row'], snap['original_col']):
            _close(count * cell_area, snap['original_cell_upstream_area_m2'], 'área acumulada original')
    if previous[2] in offsets:
        dr, dc = offsets[previous[2]]
        if (previous[0] + dr, previous[1] + dc) in seen:
            _fail('cadena de referencia', 'el extremo final forma un ciclo D8')
    if len(candidates) != snap['candidate_count']:
        _fail('cadena de referencia', 'el número de candidatos no coincide con la cadena')
    minimum = min(item[0] for item in candidates)
    chosen = min((item for item in candidates if item[0] <= minimum + epsilon),
                 key=lambda item: (item[1], item[2], item[3]))
    if chosen[2:4] != (snap['snapped_row'], snap['snapped_col']):
        _fail('cadena de referencia', 'el cierre no minimiza la distancia al punto original')
    _close(chosen[0], snap['distance_m'], 'distancia al centro más cercano')
    _close(chosen[0], snap['branch_distance_m'], 'distancia de la cadena al clic')
    _close(chosen[1] * cell_area, snap['candidate_upstream_area_m2'], 'área del centro más cercano')
    _close(chosen[4] * cell_area, snap['intersection_area_m2'], 'intersección del centro más cercano')
    others = [item[0] for item in candidates if item[2:4] != chosen[2:4]]
    runner = _float(snap['branch_runner_up_distance_m'], 'segunda distancia de cadena', nullable=True)
    if others:
        if runner is None:
            _fail('cadena de referencia', 'falta la segunda distancia de candidatos')
        _close(runner, min(others), 'segunda distancia de candidatos')
    elif runner is not None:
        _fail('cadena de referencia', 'hay una segunda distancia sin otro candidato')


def _reference_section_record(snap, basin, reference, bounds, size, budget):
    """Check a local D8 segment and its orthogonal/upstream section proof."""
    from .basin import DIRECTIONS

    course = None
    raw = snap['reference_course_wkb_hex']
    if raw != '':
        course = _reference_wkb(raw, 'curso de referencia', {2}, budget)
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint_2D(*course.GetPoint_2D(0))
        if not reference.Intersects(point):
            _fail('sección de referencia', 'el inicio del curso queda fuera de su cuenca')
    tolerance = max(1e-6, 0.05 * min(basin.cell_width, basin.cell_height))
    _point_close(snap['branch_ambiguity_tolerance_m'], tolerance, 'tolerancia de sección de referencia')
    distance = _float(snap['branch_distance_m'], 'distancia lateral de referencia')
    if distance < 0:
        _fail('sección de referencia', 'distancia lateral negativa')
    runner = _float(snap['branch_runner_up_distance_m'], 'segunda distancia de referencia', nullable=True)
    if runner is not None and (runner - distance <= tolerance or snap['candidate_count'] < 2):
        _fail('sección de referencia', 'las secciones guardadas son ambiguas')
    fields = {'section_source_cell', 'section_target_cell', 'section_projection_x',
              'section_projection_y', 'section_offset_m', 'coarse_upstream_distance_m'}
    if snap['projection_kind'] == 'containing_cell':
        if snap['status'] != 'unchanged' or fields & set(snap) or runner is not None:
            _fail('sección de referencia', 'la celda original no corresponde a este ajuste')
        _close(distance, snap['distance_m'], 'distancia a la celda original de referencia')
        return
    if (snap['projection_kind'] != 'orthogonal' or snap['status'] != 'snapped'
            or not fields.issubset(snap) or course is None):
        _fail('sección de referencia', 'falta una proyección del cierre movido')
    cols, rows = size
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    points = []
    for key in ('section_source_cell', 'section_target_cell'):
        cell = snap[key]
        if not isinstance(cell, list) or len(cell) != 3:
            _fail('sección de referencia', 'celda D8 incompleta')
        row = _int(cell[0], 'fila de sección', 0, rows - 1)
        col = _int(cell[1], 'columna de sección', 0, cols - 1)
        if type(cell[2]) is not int or cell[2] not in set(offsets) | {0}:
            _fail('sección de referencia', 'dirección D8 desconocida')
        points.append((bounds[0] + (col + .5) * basin.cell_width,
                       bounds[3] - (row + .5) * basin.cell_height))
    source, target = snap['section_source_cell'], snap['section_target_cell']
    if source[2] not in offsets:
        _fail('sección de referencia', 'el tramo no tiene salida D8')
    dy, dx = offsets[source[2]]
    if (source[0] + dy, source[1] + dx) != tuple(target[:2]):
        _fail('sección de referencia', 'las celdas no están conectadas por la dirección D8')
    if target[2] in offsets:
        dy, dx = offsets[target[2]]
        if (target[0] + dy, target[1] + dx) == tuple(source[:2]):
            _fail('sección de referencia', 'el tramo contiene un ciclo D8')
    (sx, sy), (tx, ty) = points
    vx, vy = sx - tx, sy - ty
    length = math.hypot(vx, vy)
    x, y = snap['original_x'], snap['original_y']
    epsilon = max(basin.cell_width, basin.cell_height) * 1e-7
    fraction = ((x - tx) * vx + (y - ty) * vy) / length ** 2
    if not -epsilon / length <= fraction <= 1 + epsilon / length:
        _fail('sección de referencia', 'la proyección cae fuera del segmento D8')
    fraction = min(1., max(0., fraction))
    chosen = target if fraction * length <= epsilon else source
    if tuple(chosen[:2]) != (snap['snapped_row'], snap['snapped_col']):
        _fail('sección de referencia', 'el cierre no es el primer centro aguas arriba')
    px, py = tx + fraction * vx, ty + fraction * vy
    _point_close(snap['section_projection_x'], px, 'proyección de referencia X')
    _point_close(snap['section_projection_y'], py, 'proyección de referencia Y')
    _close(distance, math.hypot(x - px, y - py), 'distancia lateral a la sección')
    offset = -((basin.outlet_x - x) * vx + (basin.outlet_y - y) * vy) / length
    _close(snap['section_offset_m'], offset, 'posición respecto a la sección')
    if offset > epsilon or any(math.hypot(cx - x, cy - y) > snap['search_radius_m'] + epsilon
                               for cx, cy in points):
        _fail('sección de referencia', 'tramo aguas abajo o fuera del radio permitido')
    start = course.GetPoint_2D(0)
    axis = None
    for index in range(1, course.GetPointCount()):
        end = course.GetPoint_2D(index)
        dx, dy = end[0] - start[0], end[1] - start[1]
        norm = math.hypot(dx, dy)
        if norm > 0:
            axis = dx / norm, dy / norm
            break
    if axis is None:
        _fail('sección de referencia', 'el curso original no define una dirección')
    upstream = (basin.outlet_x - x) * axis[0] + (basin.outlet_y - y) * axis[1]
    _close(snap['coarse_upstream_distance_m'], upstream, 'posición respecto al curso original')
    if upstream < -epsilon:
        _fail('sección de referencia', 'el cierre queda aguas abajo del curso original')


def _manual_outlet_record(snap, basin, selected_x, selected_y):
    """Validate explicit native-cell choices, preserving the older radius scope."""
    from .grid import cell_at_point

    required = {'selection_mode', 'explicit_user_choice', 'manual_requested_x',
                'manual_requested_y', 'original_row', 'original_col', 'snapped_row',
                'snapped_col', 'original_cell_upstream_area_m2',
                'area_change_vs_original_cell_m2', 'area_change_vs_reference_pct'}
    if (not required.issubset(snap) or snap['selection_mode'] != 'user_selected_cell'
            or not _bool(snap['explicit_user_choice'], 'elección explícita del cierre')):
        _fail('cierre manual', 'falta la elección explícita del usuario')
    x = _float(snap['manual_requested_x'], 'punto manual X')
    y = _float(snap['manual_requested_y'], 'punto manual Y')
    scope = snap.get('manual_selection_scope')
    if scope not in (None, 'raster_extent'):
        _fail('cierre manual', 'alcance de elección desconocido')
    if scope is None and math.hypot(x - selected_x, y - selected_y) > snap['search_radius_m'] + 1e-5:
        _fail('cierre manual', 'el punto elegido supera el radio permitido')
    if scope == 'raster_extent':
        bounds, size = snap.get('manual_raster_bounds'), snap.get('manual_raster_size')
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not isinstance(size, list) or len(size) != 2):
            _fail('cierre manual', 'falta la extensión o las dimensiones del MDT')
        bounds = [_float(value, 'extensión del MDT manual') for value in bounds]
        cols, rows = (_int(value, 'dimensión del MDT manual', 1) for value in size)
        _close((bounds[2] - bounds[0]) / cols, basin.cell_width, 'ancho de celda manual')
        _close((bounds[3] - bounds[1]) / rows, basin.cell_height, 'alto de celda manual')
        try:
            chosen = cell_at_point(x, y, *bounds, cols, rows)
            original = cell_at_point(selected_x, selected_y, *bounds, cols, rows)
        except ValueError:
            _fail('cierre manual', 'el punto elegido o el original queda fuera del MDT')
        if ((chosen.row, chosen.col) != (snap['snapped_row'], snap['snapped_col'])
                or (original.row, original.col) != (snap['original_row'], snap['original_col'])):
            _fail('cierre manual', 'las celdas no corresponden a la malla declarada')
        _point_close(chosen.x, basin.outlet_x, 'centro del cierre en el MDT X')
        _point_close(chosen.y, basin.outlet_y, 'centro del cierre en el MDT Y')
        envelope = ogr.CreateGeometryFromWkb(basin.geometry_wkb).GetEnvelope()
        if (envelope[0] < bounds[0] - 1e-5 or envelope[1] > bounds[2] + 1e-5
                or envelope[2] < bounds[1] - 1e-5 or envelope[3] > bounds[3] + 1e-5):
            _fail('cierre manual', 'la cuenca sobrepasa la extensión declarada del MDT')
    try:
        requested_cell = cell_at_point(
            x, y, basin.outlet_x - basin.cell_width / 2,
            basin.outlet_y - basin.cell_height / 2,
            basin.outlet_x + basin.cell_width / 2,
            basin.outlet_y + basin.cell_height / 2, 1, 1)
    except ValueError:
        _fail('cierre manual', 'el punto elegido no pertenece a la celda de cierre')
    _point_close(requested_cell.x, basin.outlet_x, 'centro del cierre manual X')
    _point_close(requested_cell.y, basin.outlet_y, 'centro del cierre manual Y')
    _close(snap['minimum_upstream_area_m2'], basin.cell_width * basin.cell_height,
           'celda mínima del cierre manual')
    original_area = _float(snap['original_cell_upstream_area_m2'], 'área de celda original', positive=True)
    candidate = snap['candidate_upstream_area_m2']
    _close(snap['area_change_vs_original_cell_m2'], candidate - original_area,
           'variación de área tras elección manual')
    _close(snap['area_change_vs_reference_pct'], 100 * (candidate / snap['reference_area_m2'] - 1),
           'variación de área de referencia manual')


def _upstream_section_record(snap, basin, selected_x, selected_y):
    """Validate the recorded D8 path and upstream section without source rasters."""
    from .basin import DIRECTIONS

    if snap.get('selection_mode') in ('upstream_lateral_main_course', 'upstream_lateral_nearest_branch'):
        _lateral_main_course_record(snap, basin, selected_x, selected_y)
        return
    required = {'selection_mode', 'original_direction', 'section_direction',
                'section_direction_source', 'section_source_cell', 'section_offset_m',
                'upstream_path', 'upstream_path_length_m', 'channel_filter',
                'original_row', 'original_col', 'snapped_row', 'snapped_col',
                'original_cell_upstream_area_m2', 'candidate_count',
                'area_change_vs_reference_pct', 'area_change_vs_original_cell_m2'}
    main_course = snap.get('selection_mode') == 'upstream_main_course'
    nearest = snap.get('selection_mode') == 'upstream_nearest_branch'
    if not required.issubset(snap) or snap['selection_mode'] not in ('upstream_only', 'upstream_main_course', 'upstream_nearest_branch'):
        _fail('sección aguas arriba', 'registro de la selección incompleto')
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    allowed = set(offsets) | {0}
    for key in ('original_direction', 'section_direction'):
        if type(snap[key]) is not int or snap[key] not in allowed:
            _fail('sección aguas arriba', 'dirección D8 desconocida')
    path = snap['upstream_path']
    if not isinstance(path, list) or not 1 <= len(path) <= MAX_METADATA_ITEMS:
        _fail('sección aguas arriba', 'trayectoria ausente o excesiva')
    seen, previous, length = set(), None, 0.0
    for step in path:
        if not isinstance(step, list) or len(step) != 3:
            _fail('sección aguas arriba', 'registro de trayectoria no válido')
        row, col = (_int(step[i], 'índice de trayectoria') for i in (0, 1))
        if type(step[2]) is not int or step[2] not in allowed or (row, col) in seen:
            _fail('sección aguas arriba', 'ciclo o dirección desconocida')
        seen.add((row, col))
        if previous is not None:
            dr, dc = offsets.get(previous[2], (0, 0))
            if previous[2] == 0 or (row, col) != (previous[0] + dr, previous[1] + dc):
                _fail('sección aguas arriba', 'las celdas no están conectadas por D8')
            length += math.hypot(dc * basin.cell_width, dr * basin.cell_height)
        x = basin.outlet_x + (col - snap['snapped_col']) * basin.cell_width
        y = basin.outlet_y - (row - snap['snapped_row']) * basin.cell_height
        if math.hypot(x - selected_x, y - selected_y) > snap['search_radius_m'] + 1e-5:
            _fail('sección aguas arriba', 'la trayectoria supera el radio de búsqueda')
        previous = step
    if (path[0][:2] != [snap['snapped_row'], snap['snapped_col']]
            or path[-1] != [snap['original_row'], snap['original_col'], snap['original_direction']]):
        _fail('sección aguas arriba', 'la trayectoria no enlaza cierre y celda original')
    _close(snap['upstream_path_length_m'], length, 'longitud de trayectoria aguas arriba')
    source = snap['section_source_cell']
    if not isinstance(source, list) or len(source) != 3:
        _fail('sección aguas arriba', 'falta la celda que orienta la sección')
    _int(source[0], 'fila de orientación')
    _int(source[1], 'columna de orientación')
    if type(source[2]) is not int or source[2] != snap['section_direction']:
        _fail('sección aguas arriba', 'dirección de orientación incompatible')
    kind, code = snap['section_direction_source'], snap['section_direction']
    if kind == 'cell_d8':
        if not code or code != snap['original_direction'] or source != path[-1]:
            _fail('sección aguas arriba', 'la sección no corresponde a la D8 original')
    elif kind == 'single_incoming_d8':
        dr, dc = offsets.get(code, (0, 0))
        if (snap['original_direction'] != 0 or not code
                or [source[0] + dr, source[1] + dc] != path[-1][:2]):
            _fail('sección aguas arriba', 'rama de entrada incompatible con la celda original')
    elif kind == 'main_course_incoming_d8':
        dr, dc = offsets.get(code, (0, 0))
        if (not main_course or not code
                or [source[0] + dr, source[1] + dc] != path[-1][:2]):
            _fail('sección aguas arriba', 'tramo principal de entrada incompatible con la celda original')
    elif kind == 'local_nearest_branch_d8':
        dr, dc = offsets.get(code, (0, 0))
        expected = path[0][:2] if source != path[0] else path[1][:2] if len(path) > 1 else None
        if not nearest or not code or [source[0] + dr, source[1] + dc] != expected:
            _fail('sección aguas arriba', 'segmento local incompatible con el cierre')
    elif kind == 'exact_center':
        if code or snap['original_direction'] or len(path) != 1:
            _fail('sección aguas arriba', 'cierre exacto incompatible')
        _point_close(selected_x, basin.outlet_x, 'cierre exacto X')
        _point_close(selected_y, basin.outlet_y, 'cierre exacto Y')
    else:
        _fail('sección aguas arriba', 'origen de orientación desconocido')
    dr, dc = offsets.get(code, (0, 0))
    vx, vy = dc * basin.cell_width, -dr * basin.cell_height
    norm = math.hypot(vx, vy)
    offset = (((basin.outlet_x - selected_x) * vx + (basin.outlet_y - selected_y) * vy) / norm
              if norm else 0.0)
    _close(snap['section_offset_m'], offset, 'posición respecto a la sección')
    if offset > max(basin.cell_width, basin.cell_height) * 1e-7:
        _fail('sección aguas arriba', 'el cierre queda aguas abajo de la sección marcada')
    if main_course:
        course_cells = _int(snap.get('main_course_cells'), 'celdas del curso principal de referencia', 1)
        course_length = _float(snap.get('main_course_length_m'), 'longitud del curso principal de referencia')
        if course_length < 0 or (course_cells == 1) != (course_length == 0):
            _fail('sección aguas arriba', 'longitud y celdas del curso principal incompatibles')
        if ((course_cells > 1) != (kind == 'main_course_incoming_d8')
                or course_length + 1e-5 < norm * (course_cells > 1)):
            _fail('sección aguas arriba', 'falta el primer tramo del curso principal')
        origin_x = basin.outlet_x + (snap['original_col'] - snap['snapped_col']) * basin.cell_width
        origin_y = basin.outlet_y - (snap['original_row'] - snap['snapped_row']) * basin.cell_height
        station = (-((selected_x - origin_x) * vx + (selected_y - origin_y) * vy) / norm
                   if norm else 0.0)
        _close(snap.get('main_course_click_station_m'), station, 'proyección sobre el tramo principal')
        moved = station > max(basin.cell_width, basin.cell_height) * 1e-7
        if (snap['candidate_count'] != 1 or len(path) != (2 if moved else 1)
                or (moved and (course_cells < 2 or path[0] != source))):
            _fail('sección aguas arriba', 'el cierre no es el primer centro del curso principal aguas arriba')
        if snap.get('tie_break') != 'main_course_d8_order':
            _fail('sección aguas arriba', 'desempate del curso principal desconocido')
    if nearest:
        _nearest_branch_record(snap, basin, selected_x, selected_y, path)
    area = _float(snap['original_cell_upstream_area_m2'], 'área de celda original', positive=True)
    candidate = snap['candidate_upstream_area_m2']
    if candidate > area + max(1e-5, area * 1e-7):
        _fail('sección aguas arriba', 'el cierre aguas arriba aumenta el área de su celda original')
    _close(snap['area_change_vs_original_cell_m2'], candidate - area, 'variación de área de celda original')
    _close(snap['area_change_vs_reference_pct'], 100 * (candidate / snap['reference_area_m2'] - 1),
           'variación de área de referencia')
    cell_area = basin.cell_width * basin.cell_height
    if snap['channel_filter'] == 'native_cell':
        _close(snap['minimum_upstream_area_m2'], cell_area, 'umbral de celda nativa')
    elif snap['channel_filter'] == 'local_concentration':
        background = _float(snap.get('local_median_upstream_area_m2'), 'mediana de aporte local', positive=True)
        if snap.get('local_concentration_factor') != 4.0 or type(snap.get('local_minimum_cells')) is not int or snap['local_minimum_cells'] != 9:
            _fail('sección aguas arriba', 'filtro de cauce local desconocido')
        _close(snap['minimum_upstream_area_m2'], max(9 * cell_area, 4 * background), 'umbral de cauce local')
    else:
        _fail('sección aguas arriba', 'filtro de cauce desconocido')


def _nearest_branch_record(snap, basin, selected_x, selected_y, path):
    """Validate the local segment/projection and the recorded ambiguity margin."""
    from .basin import DIRECTIONS, _branch_ambiguity_tolerance

    required = {'projection_kind', 'section_projection_x', 'section_projection_y',
                'branch_click_station_m', 'branch_selected_station_m', 'branch_path_length_m',
                'branch_path_cells', 'branch_distance_m', 'branch_runner_up_distance_m',
                'branch_ambiguity_tolerance_m'}
    label = 'ramal D8 local'
    if not required.issubset(snap) or snap.get('tie_break') != 'nearest_branch_geometry':
        _fail(label, 'registro de selección incompleto')
    source = snap['section_source_cell']
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    dr, dc = offsets.get(snap['section_direction'], (0, 0))
    vx, vy = dc * basin.cell_width, -dr * basin.cell_height
    norm = math.hypot(vx, vy)
    external = source != path[0]
    if external and tuple(source[:2]) in {tuple(step[:2]) for step in path}:
        _fail(label, 'la orientación crea un ciclo en la trayectoria')
    length = _float(snap['upstream_path_length_m'], 'trayectoria local')
    _close(snap['branch_selected_station_m'], length, 'estación del cierre local')
    _close(snap['branch_path_length_m'], length + (norm if external else 0), 'longitud del tramo local probado')
    if _int(snap['branch_path_cells'], 'celdas de la trayectoria local', 1) != len(path) + int(external):
        _fail(label, 'número de celdas del tramo local incompatible')
    station = _float(snap['branch_click_station_m'], 'estación proyectada local')
    kind = snap['projection_kind']
    tolerance = max(basin.cell_width, basin.cell_height) * 1e-7
    offset = (((basin.outlet_x - selected_x) * vx + (basin.outlet_y - selected_y) * vy) / norm
              if norm else 0.0)
    _close(snap['section_offset_m'], offset, 'posición respecto al segmento local')
    if offset > tolerance:
        _fail(label, 'el cierre queda aguas abajo del punto proyectado')
    if kind == 'orthogonal':
        if not norm or (external and [source[0] + dr, source[1] + dc] != path[0][:2]):
            _fail(label, 'el segmento no llega al cierre')
        if external:
            _close(station, length, 'proyección sobre vértice local')
        elif len(path) < 2 or station <= length - norm + tolerance or station > length + tolerance:
            _fail(label, 'no se eligió el primer centro aguas arriba')
        _close(offset, station - length, 'proyección ortogonal local')
        px = basin.outlet_x + (length - station) * vx / norm
        py = basin.outlet_y + (length - station) * vy / norm
    elif kind in ('root_upstream', 'native_headwater'):
        if (len(path) != 1 or station > tolerance or snap['status'] != 'unchanged'
                or (kind == 'root_upstream') != external):
            _fail(label, 'conservación del centro original incompatible')
        _close(station, offset, 'estación del centro original conservado')
        px, py = basin.outlet_x, basin.outlet_y
    else:
        _fail(label, 'tipo de proyección desconocido')
    _point_close(snap['section_projection_x'], px, 'proyección local X')
    _point_close(snap['section_projection_y'], py, 'proyección local Y')
    distance = math.hypot(px - selected_x, py - selected_y)
    _close(snap['branch_distance_m'], distance, 'distancia al segmento local')
    margin = _branch_ambiguity_tolerance(basin.cell_width, basin.cell_height)
    _close(snap['branch_ambiguity_tolerance_m'], margin, 'margen de ambigüedad local')
    runner = snap['branch_runner_up_distance_m']
    if runner is None:
        if snap['candidate_count'] != 1:
            _fail(label, 'falta la distancia al segundo cierre')
    elif (snap['candidate_count'] < 2
            or _float(runner, 'distancia al segundo cierre') - distance <= margin):
        _fail(label, 'la selección automática no resuelve la ambigüedad entre cierres')


def _lateral_main_course_record(snap, basin, selected_x, selected_y):
    """Validate lateral association and the actual upstream closure separately."""
    from .basin import DIRECTIONS

    label = 'asociación lateral al cauce'
    nearest = snap.get('selection_mode') == 'upstream_lateral_nearest_branch'
    length_key = 'branch_path_length_m' if nearest else 'main_course_length_m'
    cells_key = 'branch_path_cells' if nearest else 'main_course_cells'
    click_key = 'branch_click_station_m' if nearest else 'main_course_click_station_m'
    selected_key = 'branch_selected_station_m' if nearest else 'main_course_selected_station_m'
    required = {'original_row', 'original_col', 'original_direction',
                'snapped_row', 'snapped_col', 'section_direction',
                'section_direction_source', 'section_source_cell', 'section_offset_m',
                'channel_anchor_row', 'channel_anchor_col', 'channel_anchor_x',
                'channel_anchor_y', 'channel_anchor_upstream_area_m2',
                'downstream_connection_path', 'upstream_path', 'upstream_path_length_m',
                length_key, cells_key, click_key, selected_key, 'section_projection_x', 'section_projection_y',
                'association_lateral_distance_m', 'association_trace_length_m',
                'association_method', 'association_incoming_area_m2',
                'association_other_inflow_area_m2', 'channel_filter', 'candidate_count',
                'original_cell_upstream_area_m2', 'area_change_vs_original_cell_m2',
                'area_change_vs_reference_pct', 'tie_break'}
    if not required.issubset(snap):
        _fail(label, 'registro incompleto')
    if (snap['association_method'] != ('relative_inflow_join_nearest_branch' if nearest else 'relative_inflow_join')
            or snap['channel_filter'] != 'topological_receiver'
            or snap['tie_break'] != ('nearest_branch_geometry' if nearest else 'main_course_d8_order')):
        _fail(label, 'método de asociación desconocido')
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    allowed = set(offsets) | {0}
    for key in ('original_direction', 'section_direction'):
        if type(snap[key]) is not int or snap[key] not in allowed:
            _fail(label, 'dirección D8 desconocida')

    def centre(row, col):
        return (basin.outlet_x + (col - snap['snapped_col']) * basin.cell_width,
                basin.outlet_y - (row - snap['snapped_row']) * basin.cell_height)

    def path_record(key):
        path = snap[key]
        if not isinstance(path, list) or not 1 <= len(path) <= MAX_METADATA_ITEMS:
            _fail(label, 'trayectoria ausente o excesiva')
        seen, previous, length = set(), None, 0.0
        for step in path:
            if not isinstance(step, list) or len(step) != 3:
                _fail(label, 'registro de trayectoria no válido')
            row, col = (_int(step[i], 'índice de trayectoria') for i in (0, 1))
            if type(step[2]) is not int or step[2] not in allowed or (row, col) in seen:
                _fail(label, 'ciclo o dirección desconocida')
            seen.add((row, col))
            if previous is not None:
                dr, dc = offsets.get(previous[2], (0, 0))
                if not previous[2] or (row, col) != (previous[0] + dr, previous[1] + dc):
                    _fail(label, 'las celdas no están conectadas por D8')
                length += math.hypot(dc * basin.cell_width, dr * basin.cell_height)
            x, y = centre(row, col)
            if math.hypot(x - selected_x, y - selected_y) > snap['search_radius_m'] + 1e-5:
                _fail(label, 'la trayectoria supera el radio de búsqueda')
            previous = step
        return path, seen, length

    upstream, upstream_cells, upstream_length = path_record('upstream_path')
    connection, connection_cells, connection_length = path_record('downstream_connection_path')
    anchor = (_int(snap['channel_anchor_row'], 'fila del receptor'),
              _int(snap['channel_anchor_col'], 'columna del receptor'))
    if (upstream[0][:2] != [snap['snapped_row'], snap['snapped_col']]
            or connection[0] != [snap['original_row'], snap['original_col'], snap['original_direction']]
            or upstream[-1] != connection[-1] or tuple(upstream[-1][:2]) != anchor
            or len(connection) < 2 or upstream_cells & connection_cells != {anchor}):
        _fail(label, 'las trayectorias no unen dos ramas distintas en el receptor')
    anchor_x, anchor_y = centre(*anchor)
    _point_close(snap['channel_anchor_x'], anchor_x, 'receptor X')
    _point_close(snap['channel_anchor_y'], anchor_y, 'receptor Y')
    _close(snap['upstream_path_length_m'], upstream_length, 'trayectoria principal al receptor')
    _close(snap['association_trace_length_m'], connection_length, 'trayectoria de asociación')
    _close(snap[selected_key], upstream_length, 'estación del cierre principal')
    course_cells = _int(snap[cells_key], 'celdas del curso principal de referencia', 2)
    course_length = _float(snap[length_key], 'longitud del curso principal de referencia', positive=True)
    if course_cells < len(upstream) or course_length + 1e-5 < upstream_length:
        _fail(label, 'curso principal de referencia incompleto')
    source = snap['section_source_cell']
    if not isinstance(source, list) or len(source) != 3:
        _fail(label, 'falta la celda que orienta la sección')
    _int(source[0], 'fila de orientación')
    _int(source[1], 'columna de orientación')
    code = snap['section_direction']
    if (snap['section_direction_source'] != ('associated_nearest_branch' if nearest else 'associated_main_course')
            or not code or type(source[2]) is not int or source[2] != code):
        _fail(label, 'orientación del tramo principal incompatible')
    dr, dc = offsets[code]
    vx, vy = dc * basin.cell_width, -dr * basin.cell_height
    norm = math.hypot(vx, vy)
    tolerance = max(basin.cell_width, basin.cell_height) * 1e-7
    click_station = _float(snap[click_key], 'estación proyectada del clic')
    if source != upstream[0]:
        if ([source[0] + dr, source[1] + dc] != upstream[0][:2]
                or tuple(source[:2]) in (upstream_cells | connection_cells)
                or course_cells <= len(upstream) or course_length + 1e-5 < upstream_length + norm):
            _fail(label, 'el tramo entrante del vértice no prolonga el curso principal')
        _close(click_station, upstream_length, 'proyección exacta sobre un vértice principal')
    elif (len(upstream) < 2 or click_station <= upstream_length - norm + tolerance
            or click_station > upstream_length + tolerance):
        _fail(label, 'el cierre no es el primer centro aguas arriba de la proyección')
    offset = (((basin.outlet_x - selected_x) * vx + (basin.outlet_y - selected_y) * vy) / norm)
    _close(snap['section_offset_m'], offset, 'posición respecto al tramo principal')
    _close(offset, click_station - upstream_length, 'proyección ortogonal sobre el tramo principal')
    if offset > tolerance:
        _fail(label, 'el cierre queda aguas abajo de la sección proyectada')
    projection_x = basin.outlet_x + (upstream_length - click_station) * vx / norm
    projection_y = basin.outlet_y + (upstream_length - click_station) * vy / norm
    _point_close(snap['section_projection_x'], projection_x, 'proyección X')
    _point_close(snap['section_projection_y'], projection_y, 'proyección Y')
    _close(snap['association_lateral_distance_m'], math.hypot(selected_x - projection_x, selected_y - projection_y),
           'distancia lateral al cauce')
    if nearest:
        if snap.get('projection_kind') != 'orthogonal':
            _fail(label, 'se requiere una proyección ortogonal sobre el ramal local')
        _nearest_branch_record(snap, basin, selected_x, selected_y, upstream)
    cell_area = basin.cell_width * basin.cell_height
    _close(snap['minimum_upstream_area_m2'], cell_area, 'umbral de celda nativa')
    original_area = _float(snap['original_cell_upstream_area_m2'], 'área de celda original', positive=True)
    anchor_area = _float(snap['channel_anchor_upstream_area_m2'], 'área del receptor', positive=True)
    incoming_area = _float(snap['association_incoming_area_m2'], 'aporte de entrada del clic', positive=True)
    other_area = _float(snap['association_other_inflow_area_m2'], 'aporte de otra entrada', positive=True)
    candidate = snap['candidate_upstream_area_m2']
    if (incoming_area < original_area or other_area <= incoming_area
            or anchor_area + 1e-5 < incoming_area + other_area + cell_area
            or candidate > anchor_area + 1e-5):
        _fail(label, 'los aportes no justifican el receptor de la asociación')
    if len(upstream) == 1:
        _close(candidate, anchor_area, 'área del cierre sobre el receptor')
    _close(snap['area_change_vs_original_cell_m2'], candidate - original_area, 'variación de área de celda original')
    _close(snap['area_change_vs_reference_pct'], 100 * (candidate / snap['reference_area_m2'] - 1),
           'variación de área de referencia')


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


def _reference_text_limit(parent, key):
    # Large WKB strings are permitted only in the explicit spatial-proof
    # record. Ordinary diagnostics/provider strings keep their original cap.
    if (parent.get('method') == 'reference_basin_d8'
            and parent.get('selection_mode') == 'reference_basin_overlap'):
        if key in ('reference_geometry_wkb_hex', 'reference_course_wkb_hex'):
            return MAX_WKB_BYTES * 2
        if key == 'reference_crs_wkt':
            return 100_000
    return 16_384


def _metadata(value, encode=False, depth=0, budget=None, text_limit=16_384):
    """Bounded JSON metadata; preserve tuple/list distinctions declaratively."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 12 or budget[0] > MAX_METADATA_ITEMS:
        _fail('metadatos', 'demasiados elementos o niveles')
    recurse = lambda item, limit=16_384: _metadata(item, encode, depth + 1, budget, limit)
    if value is None or type(value) is bool:
        return value
    if isinstance(value, (int, float)):
        _float(value, 'metadatos numéricos')
        return value
    if isinstance(value, str):
        return _text(value, 'metadatos', text_limit)
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
        return {_text(key, 'clave de metadatos', 128): recurse(item, _reference_text_limit(value, key))
                for key, item in value.items()}
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
    native_raw = bdata['geometry_wkb']
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
    if not native_polygon.Contains(outlet):
        _fail('cierre', 'el centro de cierre debe estar dentro de la cuenca original')
    # The smoothed contour may be anchored exactly at the fixed outlet.
    # Preserve strict interior membership for the original cell basin, while
    # also accepting the boundary of its explicitly recorded active surface.
    if surface is not None and not polygon.Intersects(outlet):
        _fail('cierre', 'la divisoria activa debe conservar el centro de cierre')
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
                         cell_count=native_count, geometry_wkb=native_raw)
    has_snap = _outlet_snap(diagnostics, snap_basin, selected_x, selected_y,
                           native_polygon.GetEnvelope(), budget)
    analysis = Analysis(basin, values, notes, diagnostics, main_raw, grid_raw, selected_x, selected_y)

    query_fields = {field.name for field in fields(NativeQuery)} - {'crs'} | {'crs_wkt'}
    origin_fields = {'source_kind', 'rational_context'}
    _keys(qdata, query_fields - origin_fields, 'consulta nativa', origin_fields)
    qdata = dict(qdata)
    qdata.setdefault('source_kind', 'caumax_raster')
    qdata.setdefault('rational_context', None)
    if qdata['source_kind'] not in ('caumax_raster', 'rational'):
        _fail('origen de la consulta', 'origen desconocido')
    rational = qdata['source_kind'] == 'rational'
    outlet_proof = diagnostics.get('outlet_snap') or {}
    if (outlet_proof.get('method') == 'reference_basin_d8'
            and outlet_proof.get('reference_policy_version') == 2 and not rational):
        _fail('origen de la consulta', 'el cierre automático de distancia mínima requiere origen racional')
    if rational:
        from .rational_basin import validate_rational_context
        qdata['rational_context'] = validate_rational_context(qdata['rational_context'])
    elif qdata['rational_context'] is not None:
        _fail('origen de la consulta', 'contexto racional en una consulta de capas de caudales')
    qdata['flow'] = _float(qdata['flow'], 'caudal', nullable=rational)
    if qdata['flow'] is not None and qdata['flow'] < 0:
        _fail('caudal', 'valor negativo')
    if rational:
        qdata['period'] = _float(qdata['period'], 'periodo racional', nullable=True)
        if qdata['period'] is not None and not 2 <= qdata['period'] <= 500:
            _fail('periodo racional', 'fuera del intervalo 2–500 años')
        if qdata['flow'] is not None and qdata['period'] is None:
            _fail('caudal racional', 'falta el periodo de retorno del resultado')
        recorded = qdata['rational_context']['flow_result']
        if recorded is None:
            if qdata['flow'] is not None:
                _fail('caudal racional', 'el contexto registra un caudal pendiente')
        else:
            _close(qdata['flow'], recorded['flow'], 'caudal racional conservado')
            _close(qdata['period'], recorded['period'], 'periodo racional conservado')
    else:
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
    if rational:
        native_context = query.rational_context
        native_crs = _crs(native_context['crs_wkt'], 'CRS del cálculo racional', metre_projection=True)
        if query.crs != native_crs:
            _fail('consulta racional', 'el CRS no coincide con el cálculo nativo')
        for key in ('x', 'y'):
            _point_close(getattr(query, key), native_context['outlet'][key],
                         'punto racional original ' + key)
        if query.flow_layer_path:
            _fail('consulta racional', 'el caudal no procede de una capa de caudales')

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
    if getattr(query, 'source_kind', 'caumax_raster') == 'rational':
        _fail('cuantiles CAUMAX', 'las capas de caudales no son resultados del método racional')
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


def _simple_metadata(value, depth=0, budget=None, text_limit=16_384):
    """Extra provider/download metadata stays ordinary, bounded JSON data."""
    budget = [0] if budget is None else budget
    budget[0] += 1
    if depth > 12 or budget[0] > MAX_METADATA_ITEMS:
        _fail('opciones de resolución', 'metadatos excesivos')
    recurse = lambda item, limit=16_384: _simple_metadata(item, depth + 1, budget, limit)
    if value is None or type(value) is bool:
        return value
    if isinstance(value, (int, float)):
        _float(value, 'metadato de resolución')
        return value
    if isinstance(value, str):
        return _text(value, 'metadato de resolución', text_limit)
    if isinstance(value, list):
        return [recurse(item) for item in value]
    if isinstance(value, dict):
        return {_text(key, 'clave de resolución', 128): recurse(item, _reference_text_limit(value, key))
                for key, item in value.items()}
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
        if (restored.query.source_kind != top.query.source_kind
                or restored.query.rational_context != top.query.rational_context):
            _fail('consulta de la variante ' + key, 'el origen no coincide con la consulta del proyecto')
        for field in ('flow', 'x', 'y'):
            actual, expected = getattr(restored.query, field), getattr(top.query, field)
            if actual is None or expected is None:
                if actual is not None or expected is not None:
                    _fail('consulta ' + field + ' de ' + key, 'el resultado pendiente no coincide')
            else:
                _close(actual, expected, 'consulta ' + field + ' de ' + key)
        if restored.query.crs != top.query.crs:
            reference, expected = osr.SpatialReference(), osr.SpatialReference()
            reference.ImportFromWkt(restored.query.crs.toWkt())
            expected.ImportFromWkt(top.query.crs.toWkt())
            if not reference.IsSame(expected):
                _fail('consulta de la variante ' + key, 'CRS diferente de la consulta del proyecto')
        variants[key] = dict(analysis=restored.analysis, query=restored.query,
                             smoothing_state=restored.smoothing_state)
    rational = top.query.source_kind == 'rational'
    if rational:
        from .resolution_controller import base_variant_key
        reference_key = base_variant_key(variants, active)
    else:
        reference_key = '500' if '500' in variants else None
    if reference_key is not None:
        reference_analysis = variants[reference_key]['analysis']
        reference_area = (reference_analysis.diagnostics.get('analysis_surface') or {}).get(
            'original_area_m2', reference_analysis.area_m2)
        reference_snap = reference_analysis.diagnostics.get('outlet_snap') or {}
        if (reference_snap.get('method') == 'manual_cell_d8'
                and reference_snap.get('selection_mode') == 'user_selected_cell'
                and reference_snap.get('explicit_user_choice') is True):
            # This record has already passed the native-cell, area and delta
            # checks above. Explicitly moving the 500m closure changes its basin,
            # not the original statistical reference used by the other variants.
            reference_area = reference_snap['reference_area_m2']
        if rational:
            reference_area = top.query.rational_context['area_m2']
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
