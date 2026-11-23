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


def _upstream_section_cell(directions_band, transform, original_x, original_y,
                           radius=None, counts=None, require_channel=False,
                           cancelled=lambda: False, *, return_visit=False):
    """Use the first incoming segment of the displayed longest D8 course.

    A click belongs to the root cell. Its projection on that incoming segment
    determines whether its centre or the immediately upstream course centre is
    admissible. The outgoing direction at a confluence never orients this
    section, and a closer tributary never replaces the main course. Main-course
    length is determined over the complete contributing network before applying
    the local radius and channel filter. This is a raster policy, not a surveyed
    cross-section. ``return_visit`` lets terrain reuse an unchanged delineation.
    """
    _check_cancel(cancelled)
    rows, cols = directions_band.YSize, directions_band.XSize
    original = cell_at_point(original_x, original_y, transform[0],
                             transform[3] + rows * transform[5],
                             transform[0] + cols * transform[1], transform[3], cols, rows)
    radius = max(500.0, math.hypot(original.width, original.height)) if radius is None else float(radius)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError('El radio de ajuste debe ser positivo y finito.')
    if math.hypot(original.x - original_x, original.y - original_y) > radius:
        raise ValueError('El radio no alcanza el centro de la celda original; no se ajusta el cierre.')
    if counts is not None and counts.shape != (rows, cols):
        raise ValueError('La acumulación y las direcciones no comparten dimensiones.')
    if require_channel and counts is None:
        raise ValueError('Se requiere acumulación para identificar el cauce local.')
    visit = _visit_upstream(directions_band, original.row, original.col,
                            cancelled, lambda n: None,
                            path_cell_size=(original.width, original.height))
    main_path, main_length = visit[4:]
    original_code = main_path[0][2]
    if counts is not None:
        if int(counts[original.row, original.col]) != visit[2]:
            raise ValueError('La acumulación no coincide con la cuenca D8 de la celda original.')
        for downstream, upstream in zip(main_path, main_path[1:]):
            if not 0 < int(counts[upstream[0], upstream[1]]) < int(counts[downstream[0], downstream[1]]):
                raise ValueError('La acumulación no es coherente con la conexión D8 aguas arriba.')
    source_cell = main_path[1] if len(main_path) > 1 else main_path[0]
    section_code = source_cell[2]
    direction_source = 'main_course_incoming_d8' if len(main_path) > 1 else 'cell_d8'
    if not section_code:
        if math.hypot(original.x - original_x, original.y - original_y) > 1e-7:
            raise ValueError('No se puede orientar la sección: la celda terminal no tiene '
                             'un tramo principal de entrada. Marca su centro o un tramo D8 definido.')
        direction_source = 'exact_center'
    offsets = {code: (-dx * original.width, dy * original.height) for dx, dy, code in DIRECTIONS}
    vx, vy = offsets.get(section_code, (0.0, 0.0))  # downstream vector of incoming course
    norm = math.hypot(vx, vy)
    station = (-((original_x - original.x) * vx + (original_y - original.y) * vy) / norm
               if norm else 0.0)
    tolerance = max(original.width, original.height) * 1e-7
    selected_index = 1 if station > tolerance else 0
    if selected_index >= len(main_path):
        raise ValueError('No se encuentra un cierre del cauce principal aguas arriba de la sección '
                         'marcada: la celda no tiene antecesor D8. No se desplaza aguas abajo.')
    row, col, _ = main_path[selected_index]
    x = transform[0] + (col + 0.5) * transform[1]
    y = transform[3] + (row + 0.5) * transform[5]
    distance = math.hypot(x - original_x, y - original_y)
    if distance > radius + tolerance:
        raise ValueError(f'No se encuentra un cierre del cauce principal aguas arriba dentro de {radius:g} m. '
                         'Su primer centro anterior queda fuera del radio; no se cambia de ramal.')
    cell_area = original.width * original.height
    background, minimum = None, cell_area
    if require_channel:
        dc, dr = math.ceil(radius / original.width) + 1, math.ceil(radius / original.height) + 1
        left, right = max(0, original.col - dc), min(cols, original.col + dc + 1)
        top, bottom = max(0, original.row - dr), min(rows, original.row + dr + 1)
        window = (left, top, right - left, bottom - top)
        data = _read(directions_band, *window)
        valid = np.isfinite(data)
        nodata = directions_band.GetNoDataValue()
        if nodata is not None:
            valid &= data != nodata
        if not directions_band.GetMaskFlags() & gdal.GMF_ALL_VALID:
            valid &= _read(directions_band.GetMaskBand(), *window) != 0
        rr, cc = np.ogrid[top:bottom, left:right]
        xx = transform[0] + (cc + 0.5) * transform[1]
        yy = transform[3] + (rr + 0.5) * transform[5]
        distances = (xx - original_x) ** 2 + (yy - original_y) ** 2
        local = counts[top:bottom, left:right]
        population = local[(local > 0) & (distances <= radius * radius) & valid]
        if not population.size:
            raise ValueError('No hay acumulación D8 válida en el radio de búsqueda.')
        background = float(np.median(population)) * cell_area
        minimum = max(9 * cell_area, 4 * background)
        if int(counts[row, col]) * cell_area < minimum:
            raise ValueError('No se encuentra un cierre del cauce principal aguas arriba con aporte '
                             'concentrado (al menos 9 celdas y 4 veces la mediana local). '
                             'No se cambia de ramal ni se desplaza aguas abajo.')
    selected = cell_at_point(x, y, transform[0], transform[3] + rows * transform[5],
                             transform[0] + cols * transform[1], transform[3], cols, rows)
    path = list(reversed(main_path[:selected_index + 1]))
    offset = (((x - original_x) * vx + (y - original_y) * vy) / norm if norm else 0.0)
    metadata = dict(
        method='upstream_section_d8', selection_mode='upstream_main_course',
        original_x=float(original_x), original_y=float(original_y),
        snapped_x=float(x), snapped_y=float(y),
        distance_m=float(distance), search_radius_m=radius,
        status='unchanged' if selected_index == 0 else 'snapped',
        original_row=original.row, original_col=original.col, snapped_row=row, snapped_col=col,
        original_direction=original_code, section_direction=section_code,
        section_direction_source=direction_source, section_source_cell=list(source_cell),
        section_offset_m=float(offset), main_course_click_station_m=float(station),
        main_course_length_m=float(main_length), main_course_cells=len(main_path),
        upstream_path=path, candidate_count=1, tie_break='main_course_d8_order',
        upstream_path_length_m=float(norm if selected_index else 0.0),
        minimum_upstream_area_m2=float(minimum),
        channel_filter='local_concentration' if require_channel else 'native_cell',
        original_cell_upstream_area_m2=visit[2] * cell_area,
        policy='plugin: first centre upstream of click projection on the incoming longest D8 course; '
               'complete course uses the same strict-length DFS tie order as the displayed line; '
               'no alternative tributary and no downstream closure')
    if require_channel:
        metadata.update(local_median_upstream_area_m2=background,
                        local_concentration_factor=4.0, local_minimum_cells=9)
    if counts is not None:
        metadata['candidate_upstream_area_m2'] = int(counts[row, col]) * cell_area
    result = selected, metadata
    return (*result, visit[:4]) if return_visit else result


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
