"""Lossless-grid Po clipping to the current computed watershed polygon.

Never edits the native CAUMAX Po raster or any other official source. The
clipped GeoTIFF has the original pixel alignment and NoData outside the basin.
"""
import math
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from osgeo import gdal, ogr, osr
from qgis.core import QgsTask


EXPORT_FILENAME = 'CONSULTA_Umbral Po Cuenca.tif'
PD_EXPORT_FILENAME = 'CONSULTA_Pd-T CEDEX.tif'


def _reference(wkt, label):
    reference = osr.SpatialReference()
    if not wkt or reference.ImportFromWkt(str(wkt)) != 0:
        raise ValueError('CRS no válido: ' + label)
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def clip_po_to_basin(source_path, geometry_wkb, basin_crs_wkt, destination,
                     *, cancelled=lambda: False, kind='po'):
    """Clip Po to active watershed without changing pixel centres or values.

    A source-aligned window and a vector cutline retain the native raster grid.
    The background outside the polygon is NoData, not zero Po.
    """
    if kind not in ('po', 'pd'):
        raise ValueError('Tipo de capa hidrológica no reconocido.')
    label = 'P₀' if kind == 'po' else 'Pd-T CEDEX'
    if cancelled():
        raise InterruptedError(f'Recorte {label} cancelado.')
    source = str(source_path or '').split('|', 1)[0]
    target = Path(destination)
    if not source or not Path(source).is_file():
        raise ValueError('No se encuentra el ráster ' + label + ' utilizado en la consulta.')
    if target.suffix.lower() not in ('.tif', '.tiff'):
        raise ValueError('El recorte ' + label + ' requiere un archivo GeoTIFF.')
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError('El archivo de destino ya existe o su carpeta no está disponible.')
    if Path(source).resolve() == target.resolve():
        raise ValueError('El ráster original ' + label + ' no se puede sobrescribir.')

    src = gdal.Open(source, gdal.GA_ReadOnly)
    if src is None or src.RasterCount != 1:
        raise ValueError('El ráster ' + label + ' de origen no es válido.')
    transform = src.GetGeoTransform()
    if (len(transform) != 6 or transform[1] <= 0 or transform[5] >= 0
            or transform[2] != 0 or transform[4] != 0
            or not all(math.isfinite(v) for v in transform)):
        raise ValueError('El ráster ' + label + ' debe tener celdas ortogonales georreferenciadas.')
    source_wkt = src.GetProjectionRef()
    source_crs = _reference(source_wkt, 'ráster ' + label)
    basin_crs = _reference(basin_crs_wkt, 'divisoria activa')
    geom = ogr.CreateGeometryFromWkb(bytes(geometry_wkb))
    if (geom is None or geom.IsEmpty() or not geom.IsValid()
            or ogr.GT_Flatten(geom.GetGeometryType()) not in
            (ogr.wkbPolygon, ogr.wkbMultiPolygon)):
        raise ValueError('La divisoria activa no es un polígono válido.')
    if not basin_crs.IsSame(source_crs):
        if geom.Transform(osr.CoordinateTransformation(basin_crs, source_crs)) != 0:
            raise ValueError('No se ha podido reproyectar la divisoria al CRS de P₀.')
    inverse = gdal.InvGeoTransform(transform)
    if inverse is None:
        raise ValueError('El ráster ' + label + ' no tiene una transformación inversa válida.')
    xmin, xmax, ymin, ymax = geom.GetEnvelope()
    corners = [gdal.ApplyGeoTransform(inverse, x, y)
               for x in (xmin, xmax) for y in (ymin, ymax)]
    left = max(0, math.floor(min(p[0] for p in corners)))
    right = min(src.RasterXSize, math.ceil(max(p[0] for p in corners)))
    top = max(0, math.floor(min(p[1] for p in corners)))
    bottom = min(src.RasterYSize, math.ceil(max(p[1] for p in corners)))
    if left >= right or top >= bottom:
        raise ValueError('La divisoria no intersecta la cobertura de ' + label + '.')
    width, height = right - left, bottom - top
    xmin_out = transform[0] + left * transform[1]
    xmax_out = transform[0] + right * transform[1]
    ymax_out = transform[3] + top * transform[5]
    ymin_out = transform[3] + bottom * transform[5]
    temporary = target.parent / ('.' + target.stem + '_' + uuid4().hex + '.tif')
    try:
        with tempfile.TemporaryDirectory(prefix='hc-po-cutline-') as folder:
            cutline = Path(folder) / 'cuenca.gpkg'
            vector = ogr.GetDriverByName('GPKG').CreateDataSource(str(cutline))
            if vector is None:
                raise ValueError('No se pudo crear la máscara temporal de ' + label + '.')
            lyr = vector.CreateLayer('mascara', srs=source_crs,
                                     geom_type=ogr.wkbMultiPolygon)
            if lyr is None:
                raise ValueError('No se pudo preparar la máscara vectorial.')
            feature = ogr.Feature(lyr.GetLayerDefn())
            if ogr.GT_Flatten(geom.GetGeometryType()) == ogr.wkbPolygon:
                geom = ogr.ForceToMultiPolygon(geom)
            feature.SetGeometry(geom)
            if lyr.CreateFeature(feature) != 0:
                raise ValueError('No se pudo generar la divisoria de recorte.')
            feature = lyr = vector = None
            if cancelled():
                raise InterruptedError(f'Recorte {label} cancelado.')
            options = gdal.WarpOptions(
                format='GTiff', outputBounds=(xmin_out, ymin_out, xmax_out, ymax_out),
                width=width, height=height, dstSRS=source_wkt, resampleAlg='near',
                cutlineDSName=str(cutline), cutlineLayer='mascara',
                dstNodata=-9999., outputType=gdal.GDT_Float64,
                creationOptions=['TILED=YES', 'COMPRESS=LZW'],
                callback=lambda *_: not cancelled())
            result = gdal.Warp(str(temporary), src, options=options)
            if result is None:
                raise InterruptedError('No se ha completado el recorte ' + label + '.')
            result.GetRasterBand(1).SetNoDataValue(-9999.)
            result.SetMetadataItem('HIDROCAUMAX_PRODUCT', 'PO_BASIN_CLIP' if kind == 'po' else 'PD_BASIN_CLIP')
            result.SetMetadataItem('HIDROCAUMAX_SOURCE', source)
            result.FlushCache()
            result = None
        if cancelled():
            raise InterruptedError(f'Recorte {label} cancelado.')
        verified = gdal.Open(str(temporary), gdal.GA_ReadOnly)
        if verified is None or (verified.RasterXSize, verified.RasterYSize) != (width, height):
            raise ValueError('El TIFF recortado de ' + label + ' no se ha podido validar.')
        expected_transform = (xmin_out, transform[1], 0., ymax_out, 0., transform[5])
        if any(not math.isclose(x, y, rel_tol=0, abs_tol=1e-7)
               for x, y in zip(verified.GetGeoTransform(), expected_transform)):
            raise ValueError('El recorte ha alterado la alineación del original de ' + label + '.')
        verified = None
        os.replace(str(temporary), str(target))
        return target
    finally:
        src = None
        temporary.unlink(missing_ok=True)


def export_po_clip(source_path, destination):
    """Copy the generated raster to the user export folder, never overwrite."""
    source = Path(source_path)
    destination = Path(destination)
    if not source.is_file() or source.is_symlink():
        raise ValueError('Primero genera un recorte válido de la capa P₀.')
    if destination.suffix.lower() not in ('.tif', '.tiff'):
        raise ValueError('La exportación P₀ debe ser GeoTIFF.')
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError('Ya existe el GeoTIFF de destino o la carpeta no está disponible.')
    handle, staging = tempfile.mkstemp(prefix='.po-export-', suffix='.tif',
                                        dir=str(destination.parent))
    os.close(handle)
    staging = Path(staging)
    try:
        shutil.copyfile(source, staging)
        os.link(staging, destination)
        return destination
    finally:
        staging.unlink(missing_ok=True)


def export_pd_clip(source_path, destination):
    """Export a completed CEDEX rainfall GeoTIFF without changing its cells."""
    return export_po_clip(source_path, destination)


def publish_clipped_raster(path, kind='po', period=None, project=None):
    """Add the clipped raster at the top of QGIS, checked as visible."""
    from qgis.core import QgsProject, QgsRasterLayer
    from .generated_cleanup import register_dataset

    if kind not in ('po', 'pd') or (kind == 'pd' and period is None):
        raise ValueError('El producto ráster no está identificado.')
    name = ('HidroCAUMAX · Umbral P₀ · Recorte cuenca' if kind == 'po'
            else f'HidroCAUMAX · Precipitación Pd-T CEDEX T={int(period)} · Recorte cuenca')
    layer = QgsRasterLayer(str(path), name, 'gdal')
    if not layer.isValid():
        raise ValueError('QGIS no puede abrir el ráster recortado.')
    if kind == 'po':
        from .po_style import apply_po_style
        apply_po_style(layer)
    layer.setCustomProperty('HidroCAUMAX/hydrology_clipped', kind)
    if kind == 'pd':
        layer.setCustomProperty('HidroCAUMAX/return_period', int(period))
    project = project or QgsProject.instance()
    project.addMapLayer(layer)
    node = project.layerTreeRoot().findLayer(layer.id())
    if node is None:
        project.removeMapLayer(layer.id())
        raise RuntimeError('No se pudo incorporar la capa recortada al árbol de QGIS.')
    node.setItemVisibilityChecked(True)
    register_dataset(path, project=project)
    return layer


class PoClipTask(QgsTask):
    """Background GDAL clipping; QGIS layer publication stays on GUI thread."""

    def __init__(self, generation, source, geometry, crs_wkt, path, callback, *, kind='po'):
        if kind not in ('po', 'pd'):
            raise ValueError('Tipo de recorte no reconocido.')
        label = 'P₀' if kind == 'po' else 'Pd-T CEDEX'
        super().__init__('HidroCAUMAX: recortar ' + label + ' por la cuenca',
                         QgsTask.CanCancel)
        self.kind = kind
        self.generation = generation
        self.source = str(source)
        self.geometry = bytes(geometry)
        self.crs_wkt = str(crs_wkt)
        self.path = Path(path)
        self.callback = callback
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = clip_po_to_basin(self.source, self.geometry, self.crs_wkt,
                                           self.path, cancelled=self.isCanceled, kind=self.kind)
            return not self.isCanceled()
        except InterruptedError:
            self.error = 'Recorte ' + ('P₀' if self.kind == 'po' else 'Pd-T CEDEX') + ' cancelado.'
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)
