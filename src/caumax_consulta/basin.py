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


def _visit_upstream(directions_band, outlet_row, outlet_col, cancelled, progress):
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
    pending = [outlet_row * cols + outlet_col]
    count = 0
    minimum_row = maximum_row = outlet_row
    minimum_col = maximum_col = outlet_col
    touches_boundary = False
    while pending:
        row, col = divmod(pending.pop(), cols)
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
            pending.append(neighbour_row * cols + neighbour_col)
    _check_cancel(cancelled)
    window = (minimum_col, minimum_row,
              maximum_col - minimum_col + 1, maximum_row - minimum_row + 1)
    mask = np.empty((window[3], window[2]), dtype=np.uint8)
    np.equal(visited[minimum_row:maximum_row + 1, minimum_col:maximum_col + 1], 1, out=mask)
    progress(count)
    _check_cancel(cancelled)
    return mask, window, count, bool(touches_boundary)


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
              cancelled=lambda: False, progress=lambda n: None):
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
        mask, window, count, touches_boundary = _visit_upstream(
            directions_ds.GetRasterBand(1), selected.row, selected.col, cancelled, progress
        )
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
