"""D8 basin delineation using GDAL/OGR/NumPy, without QGIS objects.

Each call owns its datasets and can run in a worker. The CAUMAX D8 convention
is retained. DEM heights are interpreted in metres; missing heights do not
invalidate a basin. The returned outlet is the centre of the selected cell.
"""

from dataclasses import dataclass
import math
from typing import Optional

import numpy as np
from osgeo import gdal, ogr, osr

from .grid import cell_at_point


# (neighbour column offset, neighbour row offset, direction flowing towards us)
DIRECTIONS = (
    (-1, 0, 1), (-1, -1, 2), (0, -1, 4), (1, -1, 8),
    (1, 0, 16), (1, 1, 32), (0, 1, 64), (-1, 1, 128),
)
_VALID_CODES = frozenset((0, *(item[2] for item in DIRECTIONS)))


@dataclass(frozen=True)
class BasinResult:
    geometry_wkb: bytes
    crs_wkt: str
    area_m2: float
    perimeter_m: float
    z_min: Optional[float]
    z_max: Optional[float]
    z_mean: Optional[float]
    cell_count: int
    cell_width: float
    cell_height: float
    outlet_x: float
    outlet_y: float
    touches_boundary: bool


def _check_cancel(cancelled):
    if cancelled():
        raise InterruptedError("Delimitación de la cuenca cancelada.")


def _open_raster(path, label):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount < 1:
        raise ValueError(f"No se puede abrir el ráster de {label}.")
    if dataset.RasterXSize <= 0 or dataset.RasterYSize <= 0:
        raise ValueError(f"El ráster de {label} está vacío.")
    return dataset


def _spatial_reference(dataset, label):
    wkt = dataset.GetProjectionRef()
    reference = osr.SpatialReference()
    if not wkt or reference.ImportFromWkt(wkt) != 0:
        raise ValueError(f"El ráster de {label} no tiene un CRS válido.")
    if not reference.IsProjected() or not math.isclose(
        reference.GetLinearUnits(), 1.0, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError(f"El CRS de {label} debe ser proyectado y estar en metros.")
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _geotransform(dataset, label):
    transform = dataset.GetGeoTransform(can_return_null=True)
    if transform is None or len(transform) != 6 or not all(
        math.isfinite(item) for item in transform
    ):
        raise ValueError(f"El ráster de {label} no tiene una transformación afín válida.")
    if transform[2] != 0 or transform[4] != 0:
        raise ValueError("No se admiten rásteres con rotación o cizallamiento.")
    if transform[1] <= 0 or transform[5] >= 0:
        raise ValueError("Los rásteres deben tener columnas hacia el este y filas hacia el sur.")
    return transform


def _aligned(directions_ds, dem_ds):
    directions_srs = _spatial_reference(directions_ds, "direcciones")
    dem_srs = _spatial_reference(dem_ds, "MDT")
    if not directions_srs.IsSame(dem_srs):
        raise ValueError("Los CRS del MDT y de las direcciones no son equivalentes.")
    transform = _geotransform(directions_ds, "direcciones")
    dem_transform = _geotransform(dem_ds, "MDT")
    if (directions_ds.RasterXSize, directions_ds.RasterYSize) != (
        dem_ds.RasterXSize, dem_ds.RasterYSize
    ):
        raise ValueError("El MDT y las direcciones deben tener la misma malla y dimensiones.")
    tolerance = min(transform[1], -transform[5]) * 1e-9
    if any(not math.isclose(a, b, rel_tol=0.0, abs_tol=tolerance)
           for a, b in zip(transform, dem_transform)):
        raise ValueError("El MDT y las direcciones no están alineados en la misma malla.")
    unit = (dem_ds.GetRasterBand(1).GetUnitType() or "").strip().lower()
    if unit and unit not in ("m", "meter", "meters", "metre", "metres"):
        raise ValueError("Las cotas declaradas del MDT deben estar en metros.")
    return transform, directions_srs


def _read(band, *window):
    array = band.ReadAsArray(*window)
    if array is None or array.ndim != 2 or array.dtype.kind not in "biuf":
        raise ValueError("No se puede leer una banda numérica del ráster.")
    return array


def _branch_ambiguity_tolerance(width, height):
    """Internal resolution policy, not a hydrological standard."""
    return max(1e-6, 0.05 * min(width, height))


def _nearest_upstream_segment(directions_band, transform, original_x, original_y,
                               anchor_row, anchor_col, radius, cancelled=lambda: False,
                               *, counts=None, exclude_incoming=(), allow_root_upstream=False):
    """Identify a local D8 branch geometrically before calculating its course.

    The graph is restricted to real ancestors connected inside the original
    search circle. Excluded immediate inflows remove their complete subtrees.
    Different closures within 5% of the smaller cell side are ambiguous; neither
    total course length nor contributing area is a branch-ranking criterion.
    """
    from .outlet_errors import OutletSelectionRequired

    _check_cancel(cancelled)
    rows, cols = directions_band.YSize, directions_band.XSize
    width, height = transform[1], -transform[5]
    radius = float(radius)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('El radio de ajuste debe ser positivo y finito.')
    point = cell_at_point(original_x, original_y, transform[0], transform[3] - rows * height,
                         transform[0] + cols * width, transform[3], cols, rows)
    if counts is not None and counts.shape != (rows, cols):
        raise ValueError('La acumulación y las direcciones no comparten dimensiones.')
    root = int(anchor_row), int(anchor_col)
    epsilon = max(width, height) * 1e-7
    ambiguity = _branch_ambiguity_tolerance(width, height)
    context = dict(original_x=float(original_x), original_y=float(original_y),
                   search_radius_m=radius, branch_ambiguity_tolerance_m=ambiguity)

    def centre(cell):
        return transform[0] + (cell[1] + .5) * width, transform[3] - (cell[0] + .5) * height

    def distance(cell):
        x, y = centre(cell)
        return math.hypot(x - original_x, y - original_y)

    if not (0 <= root[0] < rows and 0 <= root[1] < cols) or distance(root) > radius + epsilon:
        raise OutletSelectionRequired('El centro de referencia queda fuera del radio de ajuste; elige el cierre.',
                                      dict(context, reason_code='no_candidate'))
    dc, dr = math.ceil(radius / width) + 1, math.ceil(radius / height) + 1
    left, right = max(0, point.col - dc), min(cols, point.col + dc + 1)
    top, bottom = max(0, point.row - dr), min(rows, point.row + dr + 1)
    window = (left, top, right - left, bottom - top)
    data = _read(directions_band, *window)
    valid = np.isfinite(data)
    nodata = directions_band.GetNoDataValue()
    if nodata is not None:
        valid &= data != nodata
    if not directions_band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        valid &= _read(directions_band.GetMaskBand(), *window) != 0
    if not valid[root[0] - top, root[1] - left]:
        raise ValueError('La celda de referencia no tiene dirección D8 válida (NoData).')
    if data[root[0] - top, root[1] - left] not in _VALID_CODES:
        raise ValueError('La celda de referencia tiene un código D8 desconocido.')
    if counts is not None and int(counts[root]) <= 0:
        raise ValueError('La acumulación de la celda de referencia no es válida.')
    has_native_incoming = any(
        top <= root[0] + dy < bottom and left <= root[1] + dx < right
        and valid[root[0] + dy - top, root[1] + dx - left]
        and data[root[0] + dy - top, root[1] + dx - left] == code
        for dx, dy, code in DIRECTIONS)
    excluded = {tuple(cell) for cell in exclude_incoming}
    parents, stations, pending = {root: None}, {root: 0.0}, [root]
    while pending:
        _check_cancel(cancelled)
        current = pending.pop()
        for dx, dy, code in DIRECTIONS:
            neighbour = current[0] + dy, current[1] + dx
            row, col = neighbour
            if (not (top <= row < bottom and left <= col < right)
                    or (current == root and neighbour in excluded)
                    or not valid[row - top, col - left] or data[row - top, col - left] != code
                    or distance(neighbour) > radius + epsilon):
                continue
            if neighbour in parents:
                raise ValueError('La red D8 local contiene un ciclo; no se ajusta el cierre.')
            if counts is not None and not 0 < int(counts[neighbour]) < int(counts[current]):
                raise ValueError('La acumulación no es coherente con la conexión D8 local.')
            parents[neighbour] = current
            stations[neighbour] = stations[current] + math.hypot(dx * width, dy * height)
            pending.append(neighbour)

    def entry(cell):
        return [cell[0], cell[1], int(data[cell[0] - top, cell[1] - left])]

    candidates = {}
    for number, (source, target) in enumerate(parents.items()):
        if number % 1000 == 0:
            _check_cancel(cancelled)
        if target is None:
            continue
        ux, uy = centre(source)
        dx, dy = centre(target)
        vx, vy = ux - dx, uy - dy
        length = math.hypot(vx, vy)
        raw = ((original_x - dx) * vx + (original_y - dy) * vy) / (length * length)
        if -epsilon / length <= raw <= 1 + epsilon / length:
            fraction = min(1.0, max(0.0, raw))
            selected = target if fraction * length <= epsilon else source
            projection = dx + fraction * vx, dy + fraction * vy
            click_station = stations[target] + fraction * length
            kind = 'orthogonal'
        elif raw < 0 and target == root and allow_root_upstream:
            selected, projection = root, (dx, dy)
            click_station, kind = raw * length, 'root_upstream'
        else:
            continue
        sx, sy = centre(selected)
        offset = -((sx - original_x) * vx + (sy - original_y) * vy) / length
        if offset > epsilon:
            continue
        lateral = math.hypot(original_x - projection[0], original_y - projection[1])
        candidate = dict(selected=selected, source=entry(source), target=entry(target),
                         projection=projection, projection_kind=kind,
                         selected_station=stations[selected], click_station=click_station,
                         lateral_distance=lateral, offset=offset,
                         branch_path_length_m=stations[source])
        score = lateral, source[0], source[1]
        if selected not in candidates or score < candidates[selected][0]:
            candidates[selected] = score, candidate
    if not candidates:
        # A real headwater has no entering segment. Keep its own centre only
        # when the local direction places it at/above the original point.
        if len(parents) == 1 and not has_native_incoming and allow_root_upstream:
            code = entry(root)[2]
            offsets = {value: (-dx * width, dy * height) for dx, dy, value in DIRECTIONS}
            vx, vy = offsets.get(code, (0.0, 0.0))
            norm = math.hypot(vx, vy)
            x, y = centre(root)
            offset = ((x - original_x) * vx + (y - original_y) * vy) / norm if norm else 0.0
            if (code or distance(root) <= epsilon) and offset <= epsilon:
                candidate = dict(selected=root, source=entry(root), target=None,
                    projection=(x, y), projection_kind='native_headwater',
                    selected_station=0.0, click_station=offset, lateral_distance=distance(root),
                    offset=offset, branch_path_length_m=0.0)
                candidates[root] = (distance(root), root[0], root[1]), candidate
        if not candidates:
            raise OutletSelectionRequired('No se encuentra un segmento D8 local que permita situar el cierre '
                'en la sección o aguas arriba dentro del radio. Elige el tramo o la celda en el mapa.',
                dict(context, reason_code='no_candidate'))
    ranked = sorted(candidates.values(), key=lambda item: item[0])
    best = ranked[0][1]
    runner = ranked[1][1]['lateral_distance'] if len(ranked) > 1 else None
    context.update(branch_distance_m=best['lateral_distance'], branch_runner_up_distance_m=runner)
    if runner is not None and runner - best['lateral_distance'] <= ambiguity:
        raise OutletSelectionRequired(
            f'Hay dos cierres D8 locales con proximidad indistinguible: {best["lateral_distance"]:.2f} y '
            f'{runner:.2f} m al clic (margen {ambiguity:g} m). Elige el ramal en el mapa; '
            'no se decide por longitud ni por área de cuenca.', dict(context, reason_code='ambiguous_branch'))
    path, current = [], best['selected']
    while current is not None:
        path.append(entry(current))
        current = parents[current]
    # Do not bypass a much closer native ancestor merely because its local
    # segment would require an invalid endpoint clamp. Such a geometry needs
    # an explicit choice, rather than an invented vertical/diagonal connection.
    if best['selected'] != root:
        downstream = {tuple(step[:2]) for step in path}
        alternatives = [cell for cell in parents if cell not in downstream]
        if alternatives:
            closest_cell = min(alternatives, key=distance)
            if distance(closest_cell) + ambiguity < distance(best['selected']):
                raise OutletSelectionRequired(
                    'El segmento D8 más cercano y la celda aportante más próxima señalan posiciones distintas. '
                    'La conexión local no permite decidir el cierre sin desplazarlo respecto al tramo; '
                    'elige la celda en el mapa.', dict(context, reason_code='geometric_conflict'))
    x, y = centre(best['selected'])
    cell = cell_at_point(x, y, transform[0], transform[3] - rows * height,
                         transform[0] + cols * width, transform[3], cols, rows)
    best.update(cell=cell, path=path, best_distance_m=best['lateral_distance'],
                runner_up_distance_m=runner, ambiguity_tolerance_m=ambiguity,
                candidate_count=len(candidates),
                branch_path_cells=len(path) + int(best['source'] != path[0]))
    return best


def _upstream_section_cell(directions_band, transform, original_x, original_y,
                           radius=None, counts=None, require_channel=False,
                           cancelled=lambda: False, *, return_visit=False):
    """Choose the nearest unambiguous local D8 branch, then its upstream centre."""
    rows, cols = directions_band.YSize, directions_band.XSize
    original = cell_at_point(original_x, original_y, transform[0],
                             transform[3] + rows * transform[5],
                             transform[0] + cols * transform[1], transform[3], cols, rows)
    radius = max(500.0, math.hypot(original.width, original.height)) if radius is None else float(radius)
    picked = _nearest_upstream_segment(directions_band, transform, original_x, original_y,
        original.row, original.col, radius, cancelled, counts=counts, allow_root_upstream=True)
    selected = picked['cell']
    visit = _visit_upstream(directions_band, original.row, original.col, cancelled, lambda n: None)
    cell_area = original.width * original.height
    if counts is not None and int(counts[original.row, original.col]) != visit[2]:
        raise ValueError('La acumulación no coincide con la cuenca D8 de la celda original.')
    if require_channel:
        raise ValueError('El filtro de cauce debe verificarse antes de seleccionar el ramal local.')
    original_code = picked['path'][-1][2]
    source = picked['source']
    kind = picked['projection_kind']
    metadata = dict(
        method='upstream_section_d8', selection_mode='upstream_nearest_branch',
        original_x=float(original_x), original_y=float(original_y),
        snapped_x=float(selected.x), snapped_y=float(selected.y),
        distance_m=float(math.hypot(selected.x - original_x, selected.y - original_y)), search_radius_m=radius,
        status='unchanged' if (selected.row, selected.col) == (original.row, original.col) else 'snapped',
        original_row=original.row, original_col=original.col, snapped_row=selected.row, snapped_col=selected.col,
        original_direction=original_code, section_direction=source[2], section_source_cell=source,
        section_direction_source=('exact_center' if not source[2] else 'cell_d8')
            if kind == 'native_headwater' else 'local_nearest_branch_d8',
        section_projection_x=float(picked['projection'][0]), section_projection_y=float(picked['projection'][1]),
        projection_kind=kind, section_offset_m=float(picked['offset']),
        upstream_path=picked['path'], upstream_path_length_m=float(picked['selected_station']),
        branch_click_station_m=float(picked['click_station']),
        branch_selected_station_m=float(picked['selected_station']),
        branch_path_length_m=float(picked['branch_path_length_m']), branch_path_cells=picked['branch_path_cells'],
        branch_distance_m=float(picked['best_distance_m']), branch_runner_up_distance_m=picked['runner_up_distance_m'],
        branch_ambiguity_tolerance_m=float(picked['ambiguity_tolerance_m']),
        candidate_count=picked['candidate_count'], tie_break='nearest_branch_geometry',
        minimum_upstream_area_m2=cell_area, channel_filter='native_cell',
        original_cell_upstream_area_m2=visit[2] * cell_area,
        policy='plugin: identify the nearest local D8 segment before computing the final longest course; '
               'distinct closures within 5% of the smaller cell side require explicit review; '
               'no global-length or contributing-area branch ranking')
    if counts is not None:
        metadata['candidate_upstream_area_m2'] = int(counts[selected.row, selected.col]) * cell_area
    result = selected, metadata
    return (*result, visit) if return_visit else result


def _visit_upstream(directions_band, outlet_row, outlet_col, cancelled, progress, *,
                    path_cell_size=None):
    directions = _read(directions_band)
    rows, cols = directions.shape
    if directions_band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        visited = np.zeros(directions.shape, dtype=np.uint8)
    else:
        visited = _read(directions_band.GetMaskBand()).astype(np.uint8, copy=False)
        # State 0 = unvisited, 1 = basin, 2 = invalid/no-data.
        np.equal(visited, 0, out=visited)
        visited *= np.uint8(2)
    _check_cancel(cancelled)
    value = directions[outlet_row, outlet_col]
    if visited[outlet_row, outlet_col] == 2 or not np.isfinite(value):
        raise ValueError("La celda de cierre no tiene datos de dirección (NoData).")
    if value not in _VALID_CODES:
        raise ValueError("La celda de cierre tiene un código de dirección D8 desconocido.")
    nodata = directions_band.GetNoDataValue()
    if nodata is not None and value == nodata:
        raise ValueError("La celda de cierre no tiene datos de dirección (NoData).")
    steps = tuple(step for step in DIRECTIONS if step[2] != nodata)
    visited[outlet_row, outlet_col] = 1
    root_index = outlet_row * cols + outlet_col
    pending = [root_index] if path_cell_size is None else [(root_index, 0.0)]
    longest, longest_length = root_index, 0.0
    count = 0
    minimum_row = maximum_row = outlet_row
    minimum_col = maximum_col = outlet_col
    touches_boundary = False
    while pending:
        item = pending.pop()
        index, length = (item, 0.0) if path_cell_size is None else item
        row, col = divmod(index, cols)
        if length > longest_length:
            longest, longest_length = index, length
        count += 1
        minimum_row, maximum_row = min(minimum_row, row), max(maximum_row, row)
        minimum_col, maximum_col = min(minimum_col, col), max(maximum_col, col)
        touches_boundary |= row == 0 or col == 0 or row == rows - 1 or col == cols - 1
        if count % 1000 == 0:
            progress(count)
            _check_cancel(cancelled)
        for dx, dy, expected in steps:
            neighbour_row, neighbour_col = row + dy, col + dx
            if not (0 <= neighbour_row < rows and 0 <= neighbour_col < cols):
                continue
            if (visited[neighbour_row, neighbour_col] == 2 or
                    directions[neighbour_row, neighbour_col] != expected):
                continue
            if visited[neighbour_row, neighbour_col] == 1:
                raise ValueError("La red de direcciones D8 contiene un ciclo en la cuenca.")
            visited[neighbour_row, neighbour_col] = 1
            neighbour = neighbour_row * cols + neighbour_col
            if path_cell_size is None:
                pending.append(neighbour)
            else:
                pending.append((neighbour, length + math.hypot(
                    dx * path_cell_size[0], dy * path_cell_size[1])))
    _check_cancel(cancelled)
    window = (minimum_col, minimum_row,
              maximum_col - minimum_col + 1, maximum_row - minimum_row + 1)
    mask = np.empty((window[3], window[2]), dtype=np.uint8)
    np.equal(visited[minimum_row:maximum_row + 1, minimum_col:maximum_col + 1], 1, out=mask)
    progress(count)
    _check_cancel(cancelled)
    result = mask, window, count, bool(touches_boundary)
    if path_cell_size is None:
        return result
    # Reconstruct by following native D8 downstream; no second distance array.
    # Strict > and the same DIRECTIONS/LIFO order reproduce terrain._paths ties.
    offsets = {code: (-dy, -dx) for dx, dy, code in DIRECTIONS}
    path, index = [], longest
    while True:
        if len(path) % 1000 == 0:
            _check_cancel(cancelled)
        row, col = divmod(index, cols)
        code = int(directions[row, col])
        path.append([row, col, code])
        if index == root_index:
            break
        dr, dc = offsets[code]
        index = (row + dr) * cols + col + dc
    path.reverse()
    return (*result, path, float(longest_length))


def _polygonize(mask, transform, reference, cancelled):
    memory_raster = gdal.GetDriverByName("MEM").Create(
        "", mask.shape[1], mask.shape[0], 1, gdal.GDT_Byte
    )
    memory_raster.SetGeoTransform(transform)
    memory_raster.SetProjection(reference.ExportToWkt())
    source = memory_raster.GetRasterBand(1)
    source.WriteArray(mask)
    source.SetNoDataValue(0)
    vector = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = vector.CreateLayer("basin", srs=reference, geom_type=ogr.wkbPolygon)
    interrupted = [False]

    def on_progress(fraction, message, data):
        if cancelled():
            interrupted[0] = True
            return 0
        return 1

    try:
        # Explicit mask excludes zero/background polygons and preserves holes.
        status = gdal.Polygonize(source, source, layer, -1, [], callback=on_progress)
    except RuntimeError:
        if interrupted[0]:
            raise InterruptedError("Delimitación de la cuenca cancelada.")
        raise
    if interrupted[0]:
        raise InterruptedError("Delimitación de la cuenca cancelada.")
    _check_cancel(cancelled)
    if status != gdal.CE_None:
        raise ValueError("No se ha podido generar el contorno de la cuenca.")
    geometry = ogr.Geometry(ogr.wkbMultiPolygon)
    for feature in layer:
        _check_cancel(cancelled)
        polygon = feature.GetGeometryRef()
        if polygon is None or polygon.IsEmpty():
            continue
        if geometry.AddGeometry(polygon) != ogr.OGRERR_NONE:
            raise ValueError("No se ha podido construir la geometría de la cuenca.")
    if geometry.IsEmpty():
        raise ValueError("El contorno calculado de la cuenca está vacío.")
    boundary = geometry.Boundary()
    if boundary is None:
        raise ValueError("No se ha podido medir el perímetro de la cuenca.")
    return bytes(geometry.ExportToWkb()), float(boundary.Length())


def _elevations(dem_band, window, basin_mask, cancelled):
    _check_cancel(cancelled)
    dem = _read(dem_band, *window)
    valid = (basin_mask != 0) & np.isfinite(dem)
    if not (dem_band.GetMaskFlags() & gdal.GMF_ALL_VALID):
        valid &= _read(dem_band.GetMaskBand(), *window) != 0
    nodata = dem_band.GetNoDataValue()
    if nodata is not None:
        valid &= dem != nodata
    values = dem[valid]
    _check_cancel(cancelled)
    if values.size == 0:
        return None, None, None
    return (float(np.min(values)), float(np.max(values)),
            float(np.mean(values, dtype=np.float64)))


def delineate(directions_path, dem_path, outlet_x, outlet_y,
              cancelled=lambda: False, progress=lambda n: None, *, upstream_only=False):
    """Delineate the exact upstream cell set of a selected CAUMAX grid cell.

    Both rasters must have equivalent projected metre CRSs and the same,
    north-up, unrotated affine grid. Direction 0 is accepted as a sink when
    it is not NoData. A NoData closure, unknown closure code, or reachable
    D8 cycle raises ValueError. Cancellation raises InterruptedError.

    ``progress`` receives processed cell counts, initially 0, approximately
    every 1000 cells, and at completion. WKB is always MultiPolygon, including
    interior holes. ``touches_boundary`` flags a possibly truncated basin.
    Heights exclude DEM NoData/nonfinite cells; all three are None if absent.
    """
    directions_ds = dem_ds = None
    try:
        _check_cancel(cancelled)
        progress(0)
        directions_ds = _open_raster(directions_path, "direcciones")
        dem_ds = _open_raster(dem_path, "MDT")
        transform, reference = _aligned(directions_ds, dem_ds)
        cols, rows = directions_ds.RasterXSize, directions_ds.RasterYSize
        selected = cell_at_point(
            outlet_x, outlet_y, transform[0], transform[3] + rows * transform[5],
            transform[0] + cols * transform[1], transform[3], cols, rows
        )
        original_visit = None
        if upstream_only:
            selected, snap, original_visit = _upstream_section_cell(
                directions_ds.GetRasterBand(1), transform, outlet_x, outlet_y,
                cancelled=cancelled, return_visit=True)
            if snap['status'] != 'unchanged':
                original_visit = None
        if original_visit is None:
            mask, window, count, touches_boundary = _visit_upstream(
                directions_ds.GetRasterBand(1), selected.row, selected.col, cancelled, progress
            )
        else:
            mask, window, count, touches_boundary = original_visit
            progress(count)
        _check_cancel(cancelled)
        window_transform = (
            transform[0] + window[0] * transform[1], transform[1], 0.0,
            transform[3] + window[1] * transform[5], 0.0, transform[5]
        )
        geometry_wkb, perimeter = _polygonize(mask, window_transform, reference, cancelled)
        elevations = _elevations(dem_ds.GetRasterBand(1), window, mask, cancelled)
        determinant = abs(transform[1] * transform[5] - transform[2] * transform[4])
        area = count * determinant
        if not math.isfinite(area) or not math.isfinite(perimeter):
            raise ValueError("El área o el perímetro calculados no son finitos.")
        return BasinResult(
            geometry_wkb, reference.ExportToWkt(), area, perimeter,
            *elevations, count, selected.width, selected.height,
            selected.x, selected.y, touches_boundary
        )
    except RuntimeError as error:
        raise ValueError(f"No se ha podido delimitar la cuenca: {error}") from error
    finally:
        directions_ds = dem_ds = None
