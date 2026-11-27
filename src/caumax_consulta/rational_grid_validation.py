"""Read-only preflight of user-selected rational MDT and Esri-D8 rasters.

CAUMAX's original rational dialog uses its mutable /caumax/ layer settings,
whereas the maximum-flow map now always uses the official 500 m pair.
This module never replaces those settings, moves an outlet, or resamples D8.
Only explicit native 25/5 m calculations are checked by this preflight.
"""
import math
from pathlib import Path

import numpy as np
from osgeo import gdal, osr


_FINE = (25., 5.)
_CODES = (1, 2, 4, 8, 16, 32, 64, 128)
_INFLOW = ((-1, 0, 1), (-1, -1, 2), (0, -1, 4),
           (1, -1, 8), (1, 0, 16), (1, 1, 32),
           (0, 1, 64), (-1, 1, 128))


def _open(path, description):
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None or dataset.RasterCount != 1:
        raise ValueError(
            'No se puede abrir el ' + description + ' configurado en Método racional: '
            + str(path) + '. Revisa la ruta del TIFF en Configurar capas de cálculo.')
    return dataset


def _grid(dataset, label):
    transform = tuple(dataset.GetGeoTransform())
    if (len(transform) != 6 or any(not math.isfinite(float(v)) for v in transform)
            or transform[1] <= 0 or transform[5] >= 0
            or transform[2] != 0 or transform[4] != 0):
        raise ValueError('El ' + label + ' no posee una malla métrica norte-arriba válida.')
    reference = osr.SpatialReference()
    if reference.ImportFromWkt(dataset.GetProjectionRef() or '') != 0:
        raise ValueError('El ' + label + ' no tiene sistema de referencia reconocido.')
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if (not reference.IsProjected()
            or not math.isclose(reference.GetLinearUnits(), 1., rel_tol=0, abs_tol=1e-7)):
        raise ValueError('El ' + label + ' debe tener un CRS proyectado en metros.')
    return transform, reference


def _cell(dataset, transform, x, y, label):
    inverse = gdal.InvGeoTransform(transform)
    if inverse is None:
        raise ValueError('No se puede invertir la georreferenciación del ' + label + '.')
    col, row = gdal.ApplyGeoTransform(inverse, x, y)
    col, row = math.floor(col), math.floor(row)
    if not (0 <= col < dataset.RasterXSize and 0 <= row < dataset.RasterYSize):
        xmin, xmax = transform[0], transform[0] + dataset.RasterXSize * transform[1]
        ymax, ymin = transform[3], transform[3] + dataset.RasterYSize * transform[5]
        raise ValueError(
            f'El punto X={x:.2f}; Y={y:.2f} no está cubierto por el {label}. '
            f'Cobertura del TIFF: X={xmin:.2f}–{xmax:.2f}; '
            f'Y={ymin:.2f}–{ymax:.2f}. '
            'Configura los MDT/D8 exportados para ESTE punto de cálculo '
            'o genera las capas de resolución mejorada de esta zona.')
    return col, row


def _valid(band, values, window):
    valid = np.isfinite(values)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= values != nodata
    if not (band.GetMaskFlags() & gdal.GMF_ALL_VALID):
        mask = band.GetMaskBand().ReadAsArray(*window)
        if mask is None:
            raise ValueError('No se puede leer la máscara de validez del ráster.')
        valid &= mask != 0
    return valid


def _inflow_at_closure(directions, col, row):
    band = directions.GetRasterBand(1)
    for dx, dy, code in _INFLOW:
        nx, ny = col + dx, row + dy
        if not (0 <= nx < directions.RasterXSize and 0 <= ny < directions.RasterYSize):
            continue
        value = band.ReadAsArray(nx, ny, 1, 1)
        if value is not None and int(value[0, 0]) == code:
            return True
    return False


def verify_rational_climate_crs(dem_path, climate_paths):
    """Reject mixed-reference P0/I1Id/rainfall maps used without reprojection.

    The native BasinCalculator samples climatic rasters with raw UTM X/Y and
    their own geotransforms; it never reprojects between their source CRSs.
    Different pixel sizes/extents are legitimate, different CRSs are not.
    """
    sources = tuple(climate_paths or ())
    if not sources:
        return
    dem = _open(dem_path, 'MDT')
    _, dem_crs = _grid(dem, 'MDT')
    dem = None
    for path in sources:
        raster = _open(path, 'capa climática')
        _, raster_crs = _grid(raster, 'capa climática')
        raster = None
        if not raster_crs.IsSame(dem_crs):
            raise ValueError(
                'El ráster climático ' + Path(path).name +
                ' tiene un CRS diferente del MDT. CAUMAX usa las coordenadas '
                'UTM EPSG:25830 sin reproyección en P₀, I1/Id y precipitaciones. '
                'Configura fuentes climáticas en EPSG:25830 antes de calcular.')



def verify_fine_rational_pair(dem_path, d8_path, x, y):
    """Verify real 25/5 m sources and the exact rational calculation outlet.

    Returns None if the MDT is not 25 or 5 m; otherwise an inspection result.
    Raises ValueError for invalid sources, masks, CRS or outlet coverage.
    D8 and MDT may have different dimensions if they share the same origin,
    pixel size, CRS and cover the selected outlet; CAUMAX's detailed basin
    validator later checks whether the entire delineation fits both extents.
    """
    if not all(math.isfinite(float(v)) for v in (x, y)):
        raise ValueError('El punto de cálculo tiene coordenadas no finitas.')
    dem = directions = None
    try:
        dem = _open(dem_path, 'MDT')
        dem_transform, dem_crs = _grid(dem, 'MDT')
        resolution = abs(dem_transform[1])
        if not any(math.isclose(resolution, size, rel_tol=0, abs_tol=1e-7)
                   and math.isclose(-dem_transform[5], size, rel_tol=0, abs_tol=1e-7)
                   for size in _FINE):
            return None
        size = round(resolution)
        directions = _open(d8_path, 'D8 ESRI')
        direction_transform, d8_crs = _grid(directions, 'D8')
        if (not dem_crs.IsSame(d8_crs)
                or any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-7)
                       for a, b in zip(dem_transform, direction_transform))):
            raise ValueError(
                f'El MDT y el D8 de {size} m no tienen la misma malla y CRS. '
                'Configura la pareja exportada en una misma generación; '
                'no mezcles archivos de zonas o resoluciones distintas.')
        expected = osr.SpatialReference()
        expected.ImportFromEPSG(25830)
        expected.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not dem_crs.IsSame(expected):
            raise ValueError('El Método racional original trabaja en EPSG:25830; '
                             'los MDT/D8 configurados deben estar en ese CRS.')
        dem_col, dem_row = _cell(dem, dem_transform, float(x), float(y), 'MDT')
        d8_col, d8_row = _cell(directions, direction_transform,
                                float(x), float(y), 'D8')
        if (dem_col, dem_row) != (d8_col, d8_row):
            raise ValueError('El MDT y el D8 no identifican la misma celda de cierre.')
        dem_band = dem.GetRasterBand(1)
        sample = dem_band.ReadAsArray(dem_col, dem_row, 1, 1)
        if sample is None or not _valid(dem_band, sample, (dem_col, dem_row, 1, 1))[0, 0]:
            raise ValueError(
                f'La celda del MDT {size} m en X={float(x):.2f}; Y={float(y):.2f} '
                'es NoData o no tiene cota válida. Selecciona un cierre con '
                'cota MDT real o utiliza un MDT con cobertura en ese punto.')
        d8_band = directions.GetRasterBand(1)
        encoding = (directions.GetMetadataItem('FLOW_DIRECTION_ENCODING') or '').upper()
        if encoding and encoding != 'ESRI_D8':
            raise ValueError('El ráster D8 seleccionado declara codificación '
                             + encoding + '; Método racional necesita ESRI_D8.')
        product = directions.GetMetadataItem('HIDROCAUMAX_PRODUCT') or ''
        if product in ('FLOW_DIRECTION', 'CAUMAX_FLOW_DIRECTIONS'):
            if encoding != 'ESRI_D8' or d8_band.GetNoDataValue() != 255:
                raise ValueError('El D8 de HidroCAUMAX no conserva codificación '
                                 'ESRI_D8 y NoData=255.')
        # Full raster validation is intentionally done in bounded stripes.
        rows = max(1, min(128, 524288 // max(1, directions.RasterXSize)))
        valid_direction_count = 0
        for top in range(0, directions.RasterYSize, rows):
            height = min(rows, directions.RasterYSize-top)
            window = (0, top, directions.RasterXSize, height)
            values = d8_band.ReadAsArray(*window)
            if values is None:
                raise ValueError('No se puede leer completamente el ráster de direcciones.')
            valid_directions = _valid(d8_band, values, window)
            accepted = _CODES if product in ('FLOW_DIRECTION', 'CAUMAX_FLOW_DIRECTIONS') else (*_CODES, 0)
            if np.any(valid_directions & ~np.isin(values, accepted)):
                offending = values[valid_directions & ~np.isin(values, accepted)][0]
                raise ValueError(
                    f'El D8 de {size} m contiene el código {offending:g}, '
                    'ajeno a ESRI-D8 (1,2,4,8,16,32,64,128); '
                    'comprueba que no has elegido el D8 nativo de TauDEM.')
            valid_direction_count += int(np.count_nonzero(
                valid_directions & np.isin(values, _CODES)))
            # Compare the corresponding DEM footprint only over its real overlap.
            overlap_width = min(directions.RasterXSize, dem.RasterXSize)
            overlap_height = min(top + height, dem.RasterYSize) - top
            if overlap_width > 0 and overlap_height > 0:
                dw = (0, top, overlap_width, overlap_height)
                elevations = dem_band.ReadAsArray(*dw)
                if elevations is None:
                    raise ValueError('No se puede comparar la máscara del D8 con el MDT.')
                valid_dem = _valid(dem_band, elevations, dw)
                valid_code = valid_directions[:overlap_height, :overlap_width]
                if np.any(valid_code & ~valid_dem):
                    raise ValueError('El D8 marca flujo en celdas NoData del MDT; '
                                     'los TIFF no forman una pareja coherente.')
        if not valid_direction_count:
            raise ValueError(f'El D8 de {size} m no tiene ninguna dirección ESRI válida.')
        close_sample = d8_band.ReadAsArray(d8_col, d8_row, 1, 1)
        if close_sample is None:
            raise ValueError('No se ha podido leer el D8 en el punto de cierre.')
        if not _valid(d8_band, close_sample, (d8_col, d8_row, 1, 1))[0, 0]:
            if not _inflow_at_closure(directions, d8_col, d8_row):
                raise ValueError(
                    f'En X={float(x):.2f}; Y={float(y):.2f} el D8 {size} m '
                    'es NoData y no recibe flujo de celdas vecinas. '
                    'Selecciona un cierre conectado con la red D8, '
                    'sin desplazar automáticamente tu punto.')
        return dict(resolution_m=size, dem_path=str(Path(dem_path)),
                    directions_path=str(Path(d8_path)),
                    col=dem_col, row=dem_row, elevation_m=float(sample[0, 0]),
                    direction_codes=valid_direction_count)
    finally:
        dem = directions = None
