"""Temporary, georeferenced altitude-cell selection for the hypsometric plot.

The source DEM and active basin geometry stay unchanged. Heavy raster work is
performed in a cancellable QGIS worker; only the selected cells are published
as a disposable 1/NoData raster on the main Qt thread.
"""
import math
import os
import tempfile
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsTask


MAX_WINDOW_CELLS = 60_000_000


def elevation_band(heights, index):
    """Midpoints to the adjacent sampled elevations; equal values share a band.

    A plotted quantile is not necessarily a DEM value. This reproducible
    interval identifies the nearest altitude class without inventing equality.
    """
    values = tuple(float(v) for v in heights)
    if not values or not 0 <= index < len(values) or any(
            not math.isfinite(v) for v in values):
        raise ValueError('Cuantil hipsométrico inválido.')
    if any(a < b for a, b in zip(values, values[1:])):
        raise ValueError('Los cuantiles de altitud deben estar ordenados.')
    selected = values[index]
    higher = next((v for v in reversed(values[:index]) if v > selected), None)
    lower = next((v for v in values[index+1:] if v < selected), None)
    if higher is None and lower is None:
        return selected, selected
    upper = math.inf if higher is None else selected + (higher - selected) / 2.
    bottom = -math.inf if lower is None else (selected + lower) / 2.
    return bottom, upper


def _reference(wkt):
    srs = osr.SpatialReference()
    if not wkt or srs.ImportFromWkt(str(wkt)) != 0:
        raise ValueError('El CRS de la selección hipsométrica no es válido.')
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def altitude_mask(dem_path, geometry_wkb, basin_crs_wkt, lower, upper,
                  *, cancelled=lambda: False):
    """Return a temporary GeoTIFF with exactly the eligible DEM cells.

    Pixel centres belong to the active geometry (including holes). The
    selection is computed on original native cells, never on a resampled DEM.
    """
    if not lower <= upper or any(math.isnan(v) for v in (lower, upper)):
        raise ValueError('Intervalo de cotas inválido.')
    dem = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if dem is None or dem.RasterCount != 1:
        raise ValueError('No se encuentra el MDT activo para señalar cotas.')
    polygon = ogr.CreateGeometryFromWkb(bytes(geometry_wkb))
    if (polygon is None or polygon.IsEmpty() or
            ogr.GT_Flatten(polygon.GetGeometryType()) not in (ogr.wkbPolygon, ogr.wkbMultiPolygon)):
        raise ValueError('La cuenca activa no tiene una geometría poligonal válida.')
    reference = _reference(basin_crs_wkt)
    raster_reference = _reference(dem.GetProjectionRef())
    if not reference.IsSame(raster_reference):
        if polygon.Transform(osr.CoordinateTransformation(reference, raster_reference)) != 0:
            raise ValueError('No se puede proyectar la cuenca sobre el MDT.')
    transform = dem.GetGeoTransform()
    inverse = gdal.InvGeoTransform(transform)
    if inverse is None or any(not math.isfinite(float(v)) for v in transform):
        raise ValueError('El MDT no tiene una malla georreferenciada válida.')
    xmin, xmax, ymin, ymax = polygon.GetEnvelope()
    corners = [gdal.ApplyGeoTransform(inverse, x, y)
               for x in (xmin, xmax) for y in (ymin, ymax)]
    left = max(0, math.floor(min(v[0] for v in corners)))
    right = min(dem.RasterXSize, math.ceil(max(v[0] for v in corners)))
    top = max(0, math.floor(min(v[1] for v in corners)))
    bottom = min(dem.RasterYSize, math.ceil(max(v[1] for v in corners)))
    width, height = right-left, bottom-top
    if width <= 0 or height <= 0:
        raise ValueError('La cuenca está fuera de la cobertura del MDT.')
    if width * height > MAX_WINDOW_CELLS:
        raise ValueError('La selección supera el límite seguro de celdas del MDT.')
    if cancelled():
        raise InterruptedError('Selección hipsométrica cancelada.')
    # The geometric mask is generated in-memory at the native DEM grid.
    mask_ds = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Byte)
    cropped = (transform[0] + left * transform[1] + top * transform[2],
               transform[1], transform[2],
               transform[3] + left * transform[4] + top * transform[5],
               transform[4], transform[5])
    mask_ds.SetGeoTransform(cropped)
    mask_ds.SetProjection(dem.GetProjectionRef())
    vector_ds = ogr.GetDriverByName('Memory').CreateDataSource('')
    layer = vector_ds.CreateLayer('cuenca', srs=raster_reference, geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(polygon)
    layer.CreateFeature(feature)
    feature = None
    if gdal.RasterizeLayer(mask_ds, [1], layer, burn_values=[1]) != 0:
        raise ValueError('No se ha podido rasterizar la cuenca activa.')
    coverage = mask_ds.ReadAsArray().astype(bool)
    if cancelled():
        raise InterruptedError('Selección hipsométrica cancelada.')
    band = dem.GetRasterBand(1)
    raw = band.ReadAsArray(left, top, width, height)
    if raw is None:
        raise ValueError('No se pueden leer las elevaciones del MDT.')
    raw = raw.astype(np.float64, copy=False)
    valid = np.isfinite(raw)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= raw != nodata
    if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        valid &= band.GetMaskBand().ReadAsArray(left, top, width, height) != 0
    scale, offset = band.GetScale(), band.GetOffset()
    scale = 1. if scale is None else float(scale)
    offset = 0. if offset is None else float(offset)
    if not math.isfinite(scale) or not math.isfinite(offset):
        raise ValueError('La escala de alturas del MDT no es válida.')
    values = raw * scale + offset
    selected = coverage & valid & np.isfinite(values)
    selected &= values >= lower
    selected &= values <= upper
    if cancelled():
        raise InterruptedError('Selección hipsométrica cancelada.')
    count = int(np.count_nonzero(selected))
    fd, path = tempfile.mkstemp(prefix='hidrocaumax_cota_', suffix='.tif')
    os.close(fd)
    try:
        output = gdal.GetDriverByName('GTiff').Create(
            path, width, height, 1, gdal.GDT_Byte, options=['COMPRESS=LZW', 'TILED=YES'])
        if output is None:
            raise ValueError('No se puede crear el ráster temporal de selección.')
        output.SetGeoTransform(cropped)
        output.SetProjection(dem.GetProjectionRef())
        output.GetRasterBand(1).SetNoDataValue(0)
        output.GetRasterBand(1).WriteArray(selected.astype(np.uint8))
        output.SetMetadataItem('HIDROCAUMAX_PRODUCT', 'HYPSOMETRIC_CELL_SELECTION')
        output.FlushCache()
        output = None
        if cancelled():
            raise InterruptedError('Selección hipsométrica cancelada.')
        return dict(path=path, count=count, window_cells=width*height)
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise
    finally:
        mask_ds = vector_ds = dem = None


class AltitudeSelectionTask(QgsTask):
    def __init__(self, generation, dem_path, geometry_wkb, crs_wkt,
                 lower, upper, callback):
        super().__init__('HidroCAUMAX: localizar celdas de la cota elegida', QgsTask.CanCancel)
        self.generation, self.callback = generation, callback
        self.dem_path, self.geometry_wkb = str(dem_path), bytes(geometry_wkb)
        self.crs_wkt, self.lower, self.upper = str(crs_wkt), lower, upper
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = altitude_mask(self.dem_path, self.geometry_wkb,
                                        self.crs_wkt, self.lower, self.upper,
                                        cancelled=self.isCanceled)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)
