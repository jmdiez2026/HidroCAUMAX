"""Identify a refined D8 outlet by spatial correspondence with its coarse basin.

This is a conservative, explicit plugin policy, not a hydrological standard:
both basins must share strictly more than half of their rasterized footprints.
The native D8 topology then identifies one ancestral chain. Distance to its
real segments selects an upstream centre; area is never a target to reproduce.
Only GDAL/OGR and NumPy are used, including in QGIS worker threads.
"""
import math

import numpy as np
from osgeo import gdal, ogr, osr

from . import basin as _b
from .grid import cell_at_point
from .outlet_errors import OutletSelectionRequired


POLICY_VERSION = 1
MINIMUM_REFERENCE_COVERAGE = .5
MINIMUM_CANDIDATE_OVERLAP = .5
POLICY = ('plugin: strictly more than half of both native raster footprints must overlap; '
          'qualifying outlets must form one connected ancestral D8 chain; keep the original '
          'cell when qualified, otherwise use its nearest unambiguous orthogonal section '
          'and first upstream centre; the incoming coarse course excludes downstream '
          'closures; no reference-area target; unresolved correspondence requires manual choice')


def _review(message, reason):
    raise OutletSelectionRequired(message + ' Elige el cierre en el mapa; no se publica '
                                  'automáticamente una cuenca distinta.', {'reason_code': reason})


def _geometry(wkb, source, target, label, *, optional=False):
    if optional and not wkb:
        return None
    try:
        geometry = ogr.CreateGeometryFromWkb(bytes(wkb))
    except (TypeError, ValueError, RuntimeError):
        geometry = None
    if geometry is None or geometry.IsEmpty() or not geometry.IsValid():
        _review('No se conserva una geometría válida de ' + label + '.', 'invalid_reference')
    if not source.IsSame(target):
        try:
            if geometry.Transform(osr.CoordinateTransformation(source, target)) != ogr.OGRERR_NONE:
                raise ValueError()
        except (RuntimeError, ValueError):
            _review('No se puede transformar la geometría de referencia al MDT.', 'invalid_reference')
    return geometry


def _reference_mask(dataset, geometry, cancelled):
    """Rasterize by cell centre; NoData does not shrink the reference denominator."""
    transform = dataset.GetGeoTransform()
    cols, rows = dataset.RasterXSize, dataset.RasterYSize
    bounds = [transform[0], transform[3] + rows * transform[5],
              transform[0] + cols * transform[1], transform[3]]
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    epsilon = min(transform[1], -transform[5]) * 1e-7
    if (xmin < bounds[0] - epsilon or ymin < bounds[1] - epsilon
            or xmax > bounds[2] + epsilon or ymax > bounds[3] + epsilon):
        _review('El MDT no cubre la cuenca original usada como referencia.', 'reference_outside_dem')
    memory = vector = None
    interrupted = [False]
    def callback(fraction, message, data):
        if cancelled():
            interrupted[0] = True
            return 0
        return 1
    try:
        memory = gdal.GetDriverByName('MEM').Create('', cols, rows, 1, gdal.GDT_Byte)
        memory.SetGeoTransform(transform)
        memory.SetProjection(dataset.GetProjectionRef())
        vector = ogr.GetDriverByName('Memory').CreateDataSource('')
        reference = _b._spatial_reference(dataset, 'direcciones')
        layer = vector.CreateLayer('reference', srs=reference, geom_type=ogr.wkbUnknown)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        feature = None
        try:
            status = gdal.RasterizeLayer(memory, [1], layer, burn_values=[1],
                                         options=['ALL_TOUCHED=FALSE'], callback=callback)
        except RuntimeError:
            if interrupted[0]:
                raise InterruptedError('Comprobación de la cuenca de referencia cancelada.')
            raise
        _b._check_cancel(cancelled)
        if status != gdal.CE_None:
            raise ValueError('No se puede rasterizar la cuenca de referencia.')
        mask = _b._read(memory.GetRasterBand(1))
        count = int(np.count_nonzero(mask))
        if count == 0:
            _review('La referencia no contiene centros de la malla fina.', 'empty_reference')
        return mask, count, bounds
    finally:
        memory = vector = None


def _course_axis(course):
    if course is None or ogr.GT_Flatten(course.GetGeometryType()) != ogr.wkbLineString:
        return None
    for index in range(1, course.GetPointCount()):
        start, end = course.GetPoint_2D(0), course.GetPoint_2D(index)
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length > 0:
            return dx / length, dy / length
    return None


def _select_chain(data, window, transform, counts, overlap, reference_count,
                  original, x, y, radius, axis, cancelled):
    left, top, width, height = window
    step_x, step_y = transform[1], -transform[5]
    epsilon = max(step_x, step_y) * 1e-7
    ambiguity = _b._branch_ambiguity_tolerance(step_x, step_y)
    local_counts = counts[top:top + height, left:left + width].astype(np.uint64)
    local_overlap = overlap[top:top + height, left:left + width].astype(np.uint64)
    qualified = ((2 * local_overlap > reference_count) &
                 (2 * local_overlap > local_counts) & (local_counts > 0))
    candidates = set()
    def centre(cell):
        return transform[0] + (cell[1] + .5) * step_x, transform[3] - (cell[0] + .5) * step_y
    def distance(cell):
        cx, cy = centre(cell)
        return math.hypot(cx - x, cy - y)
    def entry(cell):
        return [cell[0], cell[1], int(data[cell[0] - top, cell[1] - left])]
    for rr, cc in np.argwhere(qualified):
        cell = int(rr + top), int(cc + left)
        if distance(cell) <= radius + epsilon:
            candidates.add(cell)
    if not candidates:
        _review('Ningún cierre próximo comparte la mayoría espacial de la cuenca de referencia.',
                'no_reference_match')
    origin = original.row, original.col
    if origin in candidates:
        return dict(cell=origin, projection_kind='containing_cell',
                    candidate_count=len(candidates), ambiguity=ambiguity,
                    lateral_distance=distance(origin), runner_up=None)
    if axis is None:
        _review('Falta el tramo inicial del curso de referencia para verificar la sección.',
                'missing_reference_course')
    offsets = {code: (-dy, -dx) for dx, dy, code in _b.DIRECTIONS}
    # More than half of one reference footprint cannot belong to disjoint
    # catchments. Verify their ancestry rather than joining branches at a fork.
    first = min(candidates, key=lambda cell: int(counts[cell]))
    last = max(candidates, key=lambda cell: int(counts[cell]))
    chain, visited, current = [], set(), first
    while True:
        _b._check_cancel(cancelled)
        if current in visited:
            raise ValueError('La cadena de correspondencia D8 contiene un ciclo.')
        visited.add(current)
        chain.append(current)
        row, col, code = entry(current)
        if current == last:
            if code:
                dr, dc = offsets[code]
                target = row + dr, col + dc
                if (top <= target[0] < top + height and left <= target[1] < left + width
                        and counts[target] > counts[current] and distance(target) <= radius + epsilon):
                    chain.append(target)
            break
        if not code:
            _review('Los cierres compatibles no forman una misma cadena D8.', 'ambiguous_reference_chain')
        dr, dc = offsets[code]
        target = row + dr, col + dc
        if (not (top <= target[0] < top + height and left <= target[1] < left + width)
                or distance(target) > radius + epsilon or counts[target] <= counts[current]):
            _review('La conexión entre cierres compatibles sale de la zona de revisión.',
                    'ambiguous_reference_chain')
        current = target
    if not candidates.issubset(visited):
        _review('Hay varias ramas sin una correspondencia D8 única.', 'ambiguous_reference_chain')
    projections = {}
    for source, target in zip(chain, chain[1:]):
        if source not in candidates:
            continue
        sx, sy = centre(source)
        tx, ty = centre(target)
        vx, vy = sx - tx, sy - ty
        length = math.hypot(vx, vy)
        raw = ((x - tx) * vx + (y - ty) * vy) / length ** 2
        if not -epsilon / length <= raw <= 1 + epsilon / length:
            continue
        fraction = min(1., max(0., raw))
        selected = target if fraction * length <= epsilon else source
        if selected not in candidates:
            continue
        cx, cy = centre(selected)
        coarse_upstream = (cx - x) * axis[0] + (cy - y) * axis[1]
        if coarse_upstream < -epsilon:
            continue
        projection = tx + fraction * vx, ty + fraction * vy
        lateral = math.hypot(projection[0] - x, projection[1] - y)
        item = dict(cell=selected, projection_kind='orthogonal', source=entry(source),
                    target=entry(target), projection=projection,
                    offset=-((cx - x) * vx + (cy - y) * vy) / length,
                    coarse_upstream=coarse_upstream, lateral_distance=lateral)
        rank = lateral, distance(selected), selected
        if selected not in projections or rank < projections[selected][0]:
            projections[selected] = rank, item
    if not projections:
        _review('La red compatible queda aguas abajo de la sección original o no permite '
                'una proyección local sobre su curso.', 'no_reference_section')
    ranked = sorted(projections.values(), key=lambda pair: pair[0])
    best = ranked[0][1]
    runner = ranked[1][1]['lateral_distance'] if len(ranked) > 1 else None
    if runner is not None and runner - best['lateral_distance'] <= ambiguity:
        _review('Dos cierres de la red compatible resultan geométricamente indistinguibles.',
                'ambiguous_reference_section')
    best.update(candidate_count=len(candidates), ambiguity=ambiguity, runner_up=runner)
    return best


def select_reference_outlet(directions_path, counts, x, y, radius, reference,
                            cancelled=lambda: False, *, reference_area_m2=None):
    """Return (native Cell, portable evidence) or require an explicit user choice."""
    from .dem_hydrology import _flow_accumulation, _positive_finite, _require_accumulation_memory
    _b._check_cancel(cancelled)
    radius = _positive_finite(radius, 'El radio de correspondencia')
    dataset = _b._open_raster(directions_path, 'direcciones del refinamiento')
    try:
        transform = _b._geotransform(dataset, 'direcciones del refinamiento')
        target = _b._spatial_reference(dataset, 'direcciones del refinamiento')
        rows, cols = dataset.RasterYSize, dataset.RasterXSize
        if counts.shape != (rows, cols):
            raise ValueError('La acumulación y el MDT refinado no comparten dimensiones.')
        _require_accumulation_memory(rows * cols, 32)
        if not isinstance(reference, dict):
            _review('No se conserva la cuenca de referencia para el refinamiento.', 'invalid_reference')
        source = osr.SpatialReference()
        if (source.ImportFromWkt(str(reference.get('crs_wkt', ''))) != 0
                or not source.IsProjected() or not math.isclose(source.GetLinearUnits(), 1., rel_tol=1e-8)):
            _review('El CRS de la cuenca de referencia no es métrico.', 'invalid_reference')
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        geometry = _geometry(reference.get('geometry_wkb'), source, target, 'la cuenca original')
        if ogr.GT_Flatten(geometry.GetGeometryType()) not in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
            _review('La referencia no es una superficie de cuenca.', 'invalid_reference')
        course = _geometry(reference.get('main_course_wkb'), source, target,
                           'curso principal original', optional=True)
        mask, reference_count, bounds = _reference_mask(dataset, geometry, cancelled)
        original = cell_at_point(x, y, *bounds, cols, rows)
        if counts[original.row, original.col] <= 0:
            raise ValueError('La celda original no tiene dirección D8 válida (NoData).')
        overlap = _flow_accumulation(dataset.GetRasterBand(1), cancelled, weights=mask)
        del mask
        _b._check_cancel(cancelled)
        dx, dy = math.ceil(radius / original.width) + 1, math.ceil(radius / original.height) + 1
        left, right = max(0, original.col - dx), min(cols, original.col + dx + 1)
        top, bottom = max(0, original.row - dy), min(rows, original.row + dy + 1)
        window = (left, top, right - left, bottom - top)
        data = _b._read(dataset.GetRasterBand(1), *window)
        selected = _select_chain(data, window, transform, counts, overlap, reference_count,
                                 original, float(x), float(y), radius, _course_axis(course), cancelled)
        row, col = selected['cell']
        cx, cy = transform[0] + (col + .5) * original.width, transform[3] - (row + .5) * original.height
        cell = cell_at_point(cx, cy, *bounds, cols, rows)
        area = original.width * original.height
        candidate_area = int(counts[row, col]) * area
        original_area = int(counts[original.row, original.col]) * area
        intersection = int(overlap[row, col]) * area
        raster_area = reference_count * area
        historic_area = _positive_finite(reference_area_m2 if reference_area_m2 is not None
                                         else geometry.GetArea(), 'El área de referencia')
        diagnostic = dict(method='reference_basin_d8', selection_mode='reference_basin_overlap',
            reference_policy_version=POLICY_VERSION, policy=POLICY, tie_break='nearest_reference_section',
            status='unchanged' if (row, col) == (original.row, original.col) else 'snapped',
            original_x=float(x), original_y=float(y), original_row=original.row, original_col=original.col,
            snapped_x=cell.x, snapped_y=cell.y, snapped_row=row, snapped_col=col,
            distance_m=float(math.hypot(cx - x, cy - y)), search_radius_m=radius,
            original_cell_upstream_area_m2=original_area, candidate_upstream_area_m2=candidate_area,
            reference_area_m2=historic_area, minimum_upstream_area_m2=area,
            area_change_vs_original_cell_m2=candidate_area - original_area,
            area_change_vs_reference_pct=100 * (candidate_area / historic_area - 1),
            reference_geometry_wkb_hex=bytes(geometry.ExportToWkb()).hex(),
            reference_course_wkb_hex=bytes(course.ExportToWkb()).hex() if course is not None else '',
            reference_crs_wkt=target.ExportToWkt(), reference_grid_bounds=bounds,
            reference_grid_size=[cols, rows], reference_geometry_area_m2=float(geometry.GetArea()),
            reference_raster_area_m2=raster_area, intersection_area_m2=intersection,
            reference_coverage_fraction=intersection / raster_area,
            candidate_overlap_fraction=intersection / candidate_area,
            intersection_over_union=intersection / (raster_area + candidate_area - intersection),
            minimum_reference_coverage=MINIMUM_REFERENCE_COVERAGE,
            minimum_candidate_overlap=MINIMUM_CANDIDATE_OVERLAP,
            candidate_count=selected['candidate_count'], projection_kind=selected['projection_kind'],
            branch_distance_m=float(selected['lateral_distance']),
            branch_runner_up_distance_m=selected['runner_up'], branch_ambiguity_tolerance_m=selected['ambiguity'])
        if selected['projection_kind'] == 'orthogonal':
            diagnostic.update(section_source_cell=selected['source'], section_target_cell=selected['target'],
                section_projection_x=float(selected['projection'][0]), section_projection_y=float(selected['projection'][1]),
                section_offset_m=float(selected['offset']), coarse_upstream_distance_m=float(selected['coarse_upstream']))
        _b._check_cancel(cancelled)
        return cell, diagnostic
    except MemoryError as error:
        raise ValueError('No hay memoria suficiente para comprobar la correspondencia entre cuencas.') from error
    finally:
        dataset = None
