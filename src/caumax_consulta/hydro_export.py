"""Export native TauDEM products without overwrites or coordinate changes."""

import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr


_TAU_TO_ESRI = np.array([0, 1, 128, 64, 32, 16, 8, 4, 2], dtype=np.uint8)
_SHAPE_SUFFIXES = {'.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx',
                   '.fbn', '.fbx', '.ain', '.aih', '.ixs', '.mxs', '.xml', '.qpj',
                   '.idm', '.ind', '.shp.xml', '.shp.aux.xml', '.dbf.aux.xml'}


def _destination(destination, vector=False):
    path = Path(destination).expanduser().absolute()
    extensions = ('.shp',) if vector else ('.tif', '.tiff')
    if path.suffix.lower() not in extensions:
        raise ValueError('El destino debe ser un shapefile .shp.' if vector else
                         'El destino debe ser un GeoTIFF .tif o .tiff.')
    if not path.parent.is_dir():
        raise ValueError('La carpeta de destino no existe.')
    names = {path.name.lower()}
    if vector:
        names.update(path.stem.lower() + suffix for suffix in _SHAPE_SUFFIXES)
    else:
        names.update(path.name.lower() + suffix for suffix in ('.aux.xml', '.ovr', '.msk'))
        names.update(path.stem.lower() + suffix for suffix in ('.tfw', '.tifw', '.wld', '.rrd'))
    for sibling in path.parent.iterdir():
        lower = sibling.name.lower()
        if (lower in names or (vector and lower.startswith(path.stem.lower() + '.')
                               and lower.endswith('.atx'))):
            raise ValueError('Ya existe el archivo de destino o uno de sus archivos auxiliares. Elige otro nombre.')
    return path


def _open_raster(path):
    try:
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    except RuntimeError as error:
        raise ValueError('No se puede abrir el ráster de origen: ' + str(path)) from error
    if dataset is None or dataset.RasterCount != 1:
        raise ValueError('El producto debe ser un ráster disponible de una sola banda.')
    return dataset


def _reference(wkt):
    reference = osr.SpatialReference()
    if not wkt or reference.ImportFromWkt(wkt) != 0:
        raise ValueError('El producto no tiene un CRS válido.')
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _aligned(first, second):
    if ((first.RasterXSize, first.RasterYSize) != (second.RasterXSize, second.RasterYSize)
            or not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
                       for a, b in zip(first.GetGeoTransform(), second.GetGeoTransform()))
            or not _reference(first.GetProjectionRef()).IsSame(_reference(second.GetProjectionRef()))):
        raise ValueError('Los productos no comparten malla, dimensiones, resolución y CRS.')


def _nodata_equal(first, second):
    return (first == second or (first is not None and second is not None
                               and math.isnan(first) and math.isnan(second)))


def _blocks(dataset):
    # Keep temporaries bounded even for unusually wide rasters.
    cols, rows = dataset.RasterXSize, dataset.RasterYSize
    width = min(cols, 1024)
    height = max(1, 262144 // max(width, 1))
    for top in range(0, rows, height):
        for left in range(0, cols, width):
            yield left, top, min(width, cols-left), min(height, rows-top)


def _valid(band, data, window):
    flags = np.isfinite(data)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        flags &= data != nodata
    if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        flags &= band.GetMaskBand().ReadAsArray(*window) != 0
    return flags


def _check_directions(products, encoding):
    dem = _open_raster(products.dem_path)
    native = _open_raster(products.tau_d8_path)
    caumax = _open_raster(products.caumax_d8_path)
    _aligned(dem, native)
    _aligned(dem, caumax)
    if encoding == 'caumax':
        target = osr.SpatialReference()
        target.ImportFromEPSG(25830)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not _reference(dem.GetProjectionRef()).IsSame(target):
            raise ValueError('La exportación para CAUMAX requiere el CRS original EPSG:25830. '
                             'No se reproyectan códigos de dirección.')
    nb, cb, eb = native.GetRasterBand(1), caumax.GetRasterBand(1), dem.GetRasterBand(1)
    for window in _blocks(native):
        n, c, elevations = (band.ReadAsArray(*window) for band in (nb, cb, eb))
        if n is None or c is None or elevations is None:
            raise ValueError('No se pueden leer las direcciones y el MDT completos.')
        # The CAUMAX raster is deliberately masked where the *original* DEM
        # is NoData. PitRemove/D8FlowDir can nonetheless assign a TauDEM D8
        # direction to such edge cells. Compare the two encodings only on the
        # real, unaltered DEM footprint (as _recode_tau_d8 does).
        dem_valid = _valid(eb, elevations, window)
        native_valid = _valid(nb, n, window) & dem_valid
        caumax_valid = _valid(cb, c, window)
        if not np.array_equal(native_valid, caumax_valid):
            raise ValueError('El D8 CAUMAX/ESRI no conserva la máscara válida del MDT original.')
        values = n[native_valid]
        if np.any(~np.isin(values, np.arange(1, 9))):
            raise ValueError('Las direcciones nativas TauDEM deben usar códigos enteros 1–8.')
        if not np.array_equal(c[native_valid], _TAU_TO_ESRI[values.astype(np.uint8)]):
            raise ValueError('La codificación ESRI no corresponde a las ocho direcciones TauDEM.')
    return native if encoding == 'taudem' else caumax


def _metadata(products, product, encoding=None):
    source = dict(getattr(products, 'metadata', {}) or {})
    if 'taudem' not in str(source.get('engine', '')).lower():
        raise ValueError('El producto no acredita que el procesamiento se haya ejecutado con TauDEM.')
    result = {
        'HIDROCAUMAX_PRODUCT': product,
        'HIDROCAUMAX_ENGINE': 'TauDEM',
        'HIDROCAUMAX_PROVENANCE': json.dumps(source, ensure_ascii=False, sort_keys=True, default=str),
    }
    if encoding is not None:
        result['FLOW_DIRECTION_ENCODING'] = 'ESRI_D8' if encoding == 'caumax' else 'TAUDEM_D8'
    return result


def _publish(staged, destination, vector=False):
    """Publish complete staged files with atomic, exclusive hard links.

    A shapefile is a file family: each member is exclusive and failures roll
    back only links created by this operation. Existing files are never removed.
    """
    _destination(destination, vector)
    if vector:
        files = sorted(staged.parent.iterdir(), key=lambda p: p.suffix.lower() == '.shp')
    else:
        files = sorted(staged.parent.iterdir(), key=lambda p: p == staged)
    linked = []
    try:
        for source in files:
            if not source.is_file():
                continue
            target = destination.parent / source.name
            os.link(source, target)
            linked.append((source, target))
    except OSError as error:
        for source, target in reversed(linked):
            try:
                if target.exists() and os.path.samefile(source, target):
                    target.unlink()
            except OSError:
                pass
        raise ValueError('No se puede publicar la exportación sin sobrescribir archivos existentes: '
                         + str(error)) from error


def _check_copy(source, copied):
    if copied.GetDriver().ShortName != 'GTiff':
        raise ValueError('La copia no es un GeoTIFF.')
    _aligned(source, copied)
    first, second = source.GetRasterBand(1), copied.GetRasterBand(1)
    if (first.DataType != second.DataType or not _nodata_equal(first.GetNoDataValue(), second.GetNoDataValue())
            or first.GetUnitType() != second.GetUnitType()
            or first.GetScale() != second.GetScale() or first.GetOffset() != second.GetOffset()
            or first.GetMetadata() != second.GetMetadata()):
        raise ValueError('La copia no conserva el tipo, NoData o unidades de la banda.')
    for window in _blocks(source):
        a, b = first.ReadAsArray(*window), second.ReadAsArray(*window)
        if a is None or b is None or not np.array_equal(a, b, equal_nan=True):
            raise ValueError('La copia no conserva los valores originales del ráster.')
        if not np.array_equal(first.GetMaskBand().ReadAsArray(*window),
                              second.GetMaskBand().ReadAsArray(*window)):
            raise ValueError('La copia no conserva la máscara del ráster.')


def _raster_export(products, source, destination, product, encoding, add_to_project):
    target = _destination(destination)
    tags = _metadata(products, product, encoding)
    with tempfile.TemporaryDirectory(prefix='.hidrocaumax_export_', dir=target.parent) as directory:
        stage = Path(directory) / target.name
        copied = None
        try:
            copied = gdal.GetDriverByName('GTiff').CreateCopy(
                str(stage), source, strict=1,
                options=['COMPRESS=LZW', 'TILED=YES', 'BIGTIFF=IF_SAFER'])
            if copied is None:
                raise ValueError('No se puede crear la copia GeoTIFF.')
            for key, value in tags.items():
                copied.SetMetadataItem(key, value)
            copied.FlushCache()
            if hasattr(copied, 'Close'):
                copied.Close()
            copied = None
            copied = _open_raster(stage)
            _check_copy(source, copied)
            if any(copied.GetMetadataItem(key) != value for key, value in tags.items()):
                raise ValueError('La copia no conserva la procedencia del procesamiento.')
        finally:
            # Tracebacks or validation callbacks may still reference this Dataset.
            # Close its native handle before TemporaryDirectory removes the file.
            if copied is not None and hasattr(copied, 'Close'):
                copied.Close()
            copied = None
        _publish(stage, target)
    # Verify the *published* GeoTIFF on disk, not only the temporary staging
    # copy. A reported success must be a readable, complete file at the
    # destination the user selected.
    published = None
    try:
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('El GeoTIFF no se ha creado en la carpeta elegida.')
        published = _open_raster(target)
        _check_copy(source, published)
        if any(published.GetMetadataItem(key) != value for key, value in tags.items()):
            raise ValueError('El GeoTIFF guardado no conserva los metadatos de procedencia.')
    finally:
        published = None
    if add_to_project:
        from qgis.core import QgsProject, QgsRasterLayer
        layer = QgsRasterLayer(str(target), target.stem, 'gdal')
        if not layer.isValid():
            raise ValueError('El GeoTIFF está guardado pero QGIS no puede abrirlo.')
        from .layer_order import add_result_layer
        add_result_layer(layer)
    from .generated_cleanup import register_dataset
    register_dataset(target)
    return target


def export_dem(products, destination, add_to_project=False):
    """Copy the original active DEM, preserving its cells, CRS and NoData."""
    source = _open_raster(products.dem_path)
    try:
        _reference(source.GetProjectionRef())
        return _raster_export(products, source, destination, 'ELEVATION', None, add_to_project)
    finally:
        if hasattr(source, 'Close'):
            source.Close()
        source = None


def export_directions(products, destination, encoding='caumax', add_to_project=False):
    """Export the validated TauDEM native or CAUMAX/ESRI direction raster."""
    normalized = str(encoding).strip().lower()
    normalized = {'esri': 'caumax', 'esri_d8': 'caumax', 'taudem_d8': 'taudem',
                  'native': 'taudem'}.get(normalized, normalized)
    if normalized not in ('caumax', 'taudem'):
        raise ValueError('Selecciona codificación Esri-D8 o D8 nativa.')
    source = _check_directions(products, normalized)
    try:
        return _raster_export(products, source, destination, 'FLOW_DIRECTION', normalized, add_to_project)
    finally:
        if hasattr(source, 'Close'):
            source.Close()
        source = None


def _check_network(source, copied):
    a, b = source.GetLayer(0), copied.GetLayer(0)
    if (source.GetLayerCount() != 1 or copied.GetLayerCount() != 1
            or a.GetFeatureCount() != b.GetFeatureCount()
            or a.GetSpatialRef() is None or b.GetSpatialRef() is None
            or not a.GetSpatialRef().IsSame(b.GetSpatialRef())):
        raise ValueError('La copia de la red no conserva entidades o CRS.')
    fields_a, fields_b = a.GetLayerDefn(), b.GetLayerDefn()
    if (fields_a.GetFieldCount() != fields_b.GetFieldCount()
            or any((fields_a.GetFieldDefn(i).GetName(), fields_a.GetFieldDefn(i).GetType(),
                    fields_a.GetFieldDefn(i).GetPrecision())
                   != (fields_b.GetFieldDefn(i).GetName(), fields_b.GetFieldDefn(i).GetType(),
                       fields_b.GetFieldDefn(i).GetPrecision())
                   for i in range(fields_a.GetFieldCount()))):
        raise ValueError('La copia de la red no conserva los atributos TauDEM.')
    a.ResetReading()
    b.ResetReading()
    for first in a:
        second = b.GetNextFeature()
        ga, gb = first.GetGeometryRef(), second.GetGeometryRef() if second else None
        if (second is None or list(first.items().items()) != list(second.items().items())
                or (ga is None) != (gb is None)
                or (ga is not None and bytes(ga.ExportToWkb()) != bytes(gb.ExportToWkb()))):
            raise ValueError('La copia de la red no conserva geometrías y atributos.')


def _stage_network(network_path, stage):
    source = copied = None
    try:
        source = ogr.Open(str(network_path), 0)
        if source is None or source.GetLayerCount() != 1:
            raise ValueError('La red de drenaje no es una capa disponible.')
        layer = source.GetLayer(0)
        if (ogr.GT_Flatten(layer.GetGeomType()) not in (ogr.wkbLineString, ogr.wkbMultiLineString)
                or layer.GetSpatialRef() is None):
            raise ValueError('La red debe contener líneas con un CRS válido.')
        layer = None
        copied = gdal.VectorTranslate(str(stage), str(network_path), format='ESRI Shapefile',
                                      layerCreationOptions=['ENCODING=UTF-8'])
        if copied is None:
            raise ValueError('No se puede crear el shapefile de la red.')
        copied = None
        copied = ogr.Open(str(stage), 0)
        if copied is None:
            raise ValueError('No se puede verificar el shapefile de la red.')
        _check_network(source, copied)
    finally:
        copied = source = None


def _stage_snapshot(snapshot, stage):
    from .drainage_layers import build_network_layer
    from qgis.core import QgsCoordinateTransformContext, QgsVariantUtils, QgsVectorFileWriter
    layer = build_network_layer(snapshot)
    if not layer.isValid():
        raise ValueError('La red de la sesión no es válida.')
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName, options.fileEncoding = 'ESRI Shapefile', 'UTF-8'
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(stage), QgsCoordinateTransformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise ValueError('No se puede guardar la red de la sesión: ' + str(result[1]))
    copied = None
    try:
        copied = ogr.Open(str(stage), 0)
        if copied is None or copied.GetLayer(0).GetFeatureCount() != layer.featureCount():
            raise ValueError('La copia de la red de la sesión está incompleta.')
        target = copied.GetLayer(0)
        if (target.GetSpatialRef() is None
                or not _reference(layer.crs().toWkt()).IsSame(target.GetSpatialRef())
                or [field.GetName() for field in target.schema] != layer.fields().names()):
            raise ValueError('La red de la sesión no conserva el CRS o los campos originales.')
        for original in layer.getFeatures():
            saved = target.GetNextFeature()
            attributes = [None if QgsVariantUtils.isNull(value) else value
                          for value in original.attributes()]
            geometry = ogr.CreateGeometryFromWkb(bytes(original.geometry().asWkb()))
            if (saved is None or attributes != [saved.GetField(i) for i in range(len(attributes))]
                    or bytes(geometry.ExportToWkb()) != bytes(saved.GetGeometryRef().ExportToWkb())):
                raise ValueError('La red de la sesión no conserva sus geometrías o atributos.')
    finally:
        copied = None


def export_network(products, destination, add_to_project=False, *, snapshot=None):
    """Export the real network; a validated offline snapshot may supply it."""
    target = _destination(destination, True)
    network_path = getattr(products, 'network_path', '')
    network_path = Path(network_path) if network_path else None
    with tempfile.TemporaryDirectory(prefix='.hidrocaumax_export_', dir=target.parent) as directory:
        stage = Path(directory) / target.name
        if network_path is not None and network_path.is_file():
            _stage_network(network_path, stage)
        elif snapshot is not None:
            _stage_snapshot(snapshot, stage)
        else:
            raise ValueError('La red de drenaje no está disponible; calcula el producto o abre una sesión que la conserve.')
        required = {stage.name} | {stage.with_suffix(suffix).name for suffix in ('.shx', '.dbf', '.prj')}
        if not required.issubset({path.name for path in stage.parent.iterdir()}):
            raise ValueError('El shapefile de la red está incompleto.')
        _publish(stage, target, True)
    if add_to_project:
        from qgis.core import QgsLineSymbol, QgsProject, QgsVectorLayer
        layer = QgsVectorLayer(str(target), 'Red de drenaje Esri-D8', 'ogr')
        if not layer.isValid():
            raise ValueError('La red está guardada pero QGIS no puede abrirla.')
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({
            'line_color': '132,200,234,255', 'line_width': '0.7',
        }))
        from .layer_order import add_result_layer
        add_result_layer(layer)
    from .generated_cleanup import register_dataset
    register_dataset(target)
    return target
