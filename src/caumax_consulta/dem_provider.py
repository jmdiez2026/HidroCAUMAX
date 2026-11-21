"""Native-resolution terrain downloads from IGN/CNIG's official coverages API.

The public catalogue currently advertises 25 m and 5 m terrain collections.
Products merely listed by CNIG (2 m / 0.5 m) are never presented as downloadable
until the API itself advertises and successfully serves them at the location.
No QGIS object or installed CAUMAX file is changed by this worker-safe module.
"""
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import numpy as np
from osgeo import gdal, osr


API_BASE = 'https://api-coverages.idee.es'
CATALOGUE_URL = API_BASE + '/collections?f=json'
CNIG_CATALOGUE_URL = 'https://centrodedescargas.cnig.es/CentroDescargas/modelos-digitales-elevaciones'
PROVIDER = 'IGN/CNIG · API-Coverages IDEE'
TARGET_RESOLUTIONS = (25.0, 5.0, 2.0, 0.5)
# The deployed API limits each side to 500 native pixels (MDT25:12,500 m).
# Leave room for the source margin and minor reprojection expansion.
TILE_PIXELS = 480
MAX_SPLIT_DEPTH = 16
MAX_CELLS = 64000000
MAX_TILE_BYTES = 64000000
HTTP_TIMEOUT = 30
NODATA = -9999.0
_CATALOGUE_CACHE = None
_COLLECTION = re.compile(r'^EL\.ElevationGridCoverage_(25830|4083)_(\d+(?:\.\d+)?)_(PB|C)$')
_LOG = logging.getLogger(__name__)


class DemServiceError(ConnectionError):
    """Keep HTTP diagnostics privately, including size-limit information."""
    def __init__(self, url, status_code, description, details=''):
        self.request_url = str(url)
        self.status_code = int(status_code)
        self.description = str(description)
        self.details = str(details)
        limit = re.search(r'bbox\s+(width|height).*?larger than allowed:\s*([0-9.]+)',
                          self.description, flags=re.I)
        self.limit_axis = limit.group(1).lower() if limit else None
        self.max_span_m = float(limit.group(2)) if limit else None
        self.is_size_limit = self.status_code == 413 or limit is not None
        super().__init__(f'El servicio oficial del MDT ha respondido HTTP {self.status_code}: {self.description}')


@dataclass(frozen=True)
class ResolutionOption:
    resolution_m: float
    title: str
    provider: str
    url: str
    collection_id: str
    source_crs_wkt: str
    available: bool
    status: str
    metadata_url: str

    @property
    def resolution(self):
        return self.resolution_m

    @property
    def reason(self):
        return self.status


def _check(cancelled):
    if cancelled():
        raise InterruptedError('Descarga del MDT cancelada.')


def _bounds(value):
    if isinstance(value, dict):
        value = tuple(value[k] for k in ('xmin', 'ymin', 'xmax', 'ymax'))
    elif hasattr(value, 'xMinimum'):
        value = (value.xMinimum(), value.yMinimum(), value.xMaximum(), value.yMaximum())
    try:
        result = tuple(float(v) for v in value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('La extensión de descarga no es válida.') from error
    if len(result) != 4 or not all(math.isfinite(v) for v in result):
        raise ValueError('La extensión necesita cuatro coordenadas finitas.')
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError('La extensión de descarga debe tener anchura y altura positivas.')
    return result


def _reference(wkt, metric=False):
    reference = osr.SpatialReference()
    try:
        valid = bool(wkt) and reference.ImportFromWkt(str(wkt)) == 0
    except (TypeError, RuntimeError):
        valid = False
    if not valid:
        raise ValueError('El MDT necesita un sistema de referencia válido.')
    if metric and (not reference.IsProjected() or not math.isclose(reference.GetLinearUnits(), 1.0, abs_tol=1e-10)):
        raise ValueError('La descarga de cálculo necesita un CRS proyectado y métrico.')
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _epsg(code):
    reference = osr.SpatialReference()
    reference.ImportFromEPSG(int(code))
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _transform_bounds(bounds, source, target):
    if source.IsSame(target):
        return bounds
    transform = osr.CoordinateTransformation(source, target)
    points = []
    for fraction in np.linspace(0, 1, 17):
        x = bounds[0] + fraction * (bounds[2] - bounds[0])
        y = bounds[1] + fraction * (bounds[3] - bounds[1])
        points.extend((transform.TransformPoint(x, bounds[1]), transform.TransformPoint(x, bounds[3]),
                       transform.TransformPoint(bounds[0], y), transform.TransformPoint(bounds[2], y)))
    if not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in points):
        raise ValueError('No se puede transformar la extensión de descarga.')
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def _official_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'api-coverages.idee.es' or parsed.username or parsed.password:
        raise ValueError('La descarga no corresponde al servicio oficial de coberturas.')
    return url


def _open_url(url):
    request = Request(_official_url(url), headers={'User-Agent':'CAUMAX-Cuenca/0.6', 'Accept-Language':'es'})
    try:
        return urlopen(request, timeout=HTTP_TIMEOUT)
    except HTTPError as error:
        try:
            details = error.read(8192).decode('utf-8','replace')
        except OSError:
            details = ''
        try:
            body = json.loads(details)
            description = body.get('description') or body.get('message') or str(error)
        except (ValueError,AttributeError):
            description = re.sub(r'<[^>]+>',' ',details).strip() or str(error)
        description = ' '.join(str(description).split())[:800]
        _LOG.debug('MDT HTTP error %s URL=%s response=%s',error.code,url,details)
        raise DemServiceError(url,error.code,description,details) from error
    except (URLError, TimeoutError, OSError) as error:
        raise ConnectionError('No se ha podido consultar el servicio oficial del MDT: ' + str(error)) from error


def _json(url, cancelled):
    _check(cancelled)
    with _open_url(url) as response:
        payload = response.read(8000001)
    _check(cancelled)
    if len(payload) > 8000000:
        raise ValueError('El catálogo de MDT excede el tamaño admitido.')
    try:
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('El servicio de MDT no ha devuelto metadatos JSON válidos.') from error


def _catalogue(cancelled):
    global _CATALOGUE_CACHE
    _check(cancelled)
    if _CATALOGUE_CACHE is not None and time.monotonic() - _CATALOGUE_CACHE[0] < 900:
        return _CATALOGUE_CACHE[1]
    result = _json(CATALOGUE_URL, cancelled)
    collections = result.get('collections') if isinstance(result, dict) else None
    if not isinstance(collections, list):
        raise ValueError('El servicio oficial no ha devuelto su catálogo de colecciones.')
    _CATALOGUE_CACHE = (time.monotonic(), collections)
    return collections


def _coverage_url(option, native_bounds):
    match = _COLLECTION.fullmatch(option.collection_id)
    if not match:
        raise ValueError('La colección no es un MDT nativo admitido.')
    if urlsplit(_official_url(option.url)).path.rstrip('/') != '/collections/'+option.collection_id+'/coverage':
        raise ValueError('El enlace GeoTIFF no corresponde a la colección MDT anunciada.')
    params = {'f':'COG', 'bbox':','.join(f'{v:.10f}' for v in native_bounds),
              # The official OpenAPI describes a URI, but the deployed API
              # currently accepts the numeric EPSG code (verified on 5 m).
              'bbox-crs':match.group(1)}
    return _official_url(option.url.split('?', 1)[0]) + '?' + urlencode(params)


def _fetch_tiff(url, destination, cancelled, on_fraction=lambda n:None):
    _check(cancelled)
    with _open_url(url) as response:
        content_type = str(response.headers.get('Content-Type', '')).lower()
        if any(kind in content_type for kind in ('xml', 'json', 'html')):
            raise ValueError('El servicio oficial ha devuelto un error en lugar de un GeoTIFF.')
        try:
            expected = int(response.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            expected = 0
        if expected > MAX_TILE_BYTES:
            raise ValueError('Un recorte del MDT excede el tamaño admitido.')
        total = 0
        with Path(destination).open('wb') as stream:
            while True:
                _check(cancelled)
                block = response.read(65536)
                if not block:
                    break
                if total == 0 and block[:4] not in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'):
                    raise ValueError('La respuesta de descarga no es un TIFF válido.')
                total += len(block)
                if total > MAX_TILE_BYTES:
                    raise ValueError('Un recorte del MDT excede el tamaño admitido.')
                stream.write(block)
                if expected:
                    on_fraction(min(1.0, total / expected))
            stream.flush()
            os.fsync(stream.fileno())
    if total == 0 or (expected and total != expected):
        raise ValueError('La descarga del GeoTIFF está incompleta.')
    _check(cancelled)


def _open_raster(path):
    with Path(path).open('rb') as stream:
        if stream.read(4) not in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'):
            raise ValueError('El archivo de elevaciones no es un TIFF válido.')
    try:
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    except RuntimeError as error:
        raise ValueError('El servicio no ha producido un ráster de elevaciones válido.') from error
    if dataset is None or dataset.RasterCount != 1 or dataset.RasterXSize <= 0 or dataset.RasterYSize <= 0:
        raise ValueError('El GeoTIFF de elevaciones no es válido o no contiene una única banda.')
    return dataset


def _native_metadata(path, option):
    dataset = _open_raster(path)
    try:
        reference = _reference(dataset.GetProjection(), metric=True)
        native = _reference(option.source_crs_wkt, metric=True)
        gt = dataset.GetGeoTransform()
        step = option.resolution_m
        # The live 5 m endpoint labels otherwise identical UTM30 terrain as
        # EPSG:3042 (N-E axes), while its collection advertises EPSG:25830.
        # GDAL geotransforms remain traditional X=easting/Y=northing. Accept
        # this axis-order variant only when datum, metric units and the full
        # standard UTM zone agree; never accept another datum or projection.
        equivalent = (reference.IsSame(native) or
                      (reference.GetUTMZone() != 0 and reference.GetUTMZone() == native.GetUTMZone()
                       and reference.IsSameGeogCS(native)
                       and math.isclose(reference.GetLinearUnits(),native.GetLinearUnits(),abs_tol=1e-12)))
        if (not equivalent or gt[2] != 0 or gt[4] != 0
                or not math.isclose(gt[1], step, rel_tol=1e-6)
                or not math.isclose(gt[5], -step, rel_tol=1e-6)):
            raise ValueError('La resolución o el CRS realmente descargados no coinciden con el MDT anunciado; no se remuestrea un MDT más grueso.')
    finally:
        dataset = None


def _valid_values(array, nodata):
    valid = np.isfinite(array)
    if nodata is not None:
        valid &= array != nodata
    return valid


def _probe(option, bounds, target_reference, cancelled):
    source_reference = _reference(option.source_crs_wkt, metric=True)
    native = _transform_bounds(bounds, target_reference, source_reference)
    x, y = (native[0] + native[2]) / 2, (native[1] + native[3]) / 2
    radius = 2 * option.resolution_m
    with tempfile.TemporaryDirectory(prefix='caumax_mdt_probe_') as folder:
        tile = Path(folder) / 'probe.tif'
        _fetch_tiff(_coverage_url(option, (x-radius, y-radius, x+radius, y+radius)), tile, cancelled)
        _native_metadata(tile, option)
        dataset = _open_raster(tile)
        try:
            band = dataset.GetRasterBand(1)
            valid = _valid_values(band.ReadAsArray(), band.GetNoDataValue())
            if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
                valid &= band.GetMaskBand().ReadAsArray() != 0
            available = bool(np.any(valid))
        finally:
            band = dataset = None
    return available


def discover_resolutions(bounds, crs_wkt, cancelled=lambda:False, progress=lambda n:None):
    """List native options, verifying a small real terrain crop at the centre.

    Coverage of every requested output cell is checked during download. A
    metadata/network error is not disguised as absence of terrain coverage.
    """
    bounds = _bounds(bounds)
    target = _reference(crs_wkt, metric=True)
    geographic = _transform_bounds(bounds, target, _epsg(4326))
    progress(0)
    collections = _catalogue(cancelled)
    options = []
    for index, resolution in enumerate(TARGET_RESOLUTIONS):
        _check(cancelled)
        candidates = []
        for collection in collections:
            match = _COLLECTION.fullmatch(str(collection.get('id', '')))
            if not match or float(match.group(2)) != resolution:
                continue
            spatial = collection.get('extent', {}).get('spatial', {})
            for box in spatial.get('bbox', []):
                if (len(box) == 4 and box[0] <= geographic[0] <= geographic[2] <= box[2]
                        and box[1] <= geographic[1] <= geographic[3] <= box[3]):
                    candidates.append((collection, match))
                    break
        if not candidates:
            status = ('Producto del catálogo CNIG sin colección de descarga automática anunciada; cobertura local no verificada.'
                      if resolution in (2.0, .5) else 'La extensión solicitada no está cubierta por una colección métrica anunciada.')
            options.append(ResolutionOption(resolution, f'MDT {resolution:g} m', PROVIDER, '', '', '', False,
                                            status, CNIG_CATALOGUE_URL))
            progress(100*(index+1)/len(TARGET_RESOLUTIONS))
            continue
        collection, match = candidates[0]
        coverage = next((link.get('href') for link in collection.get('links', [])
                         if 'coverage' in str(link.get('rel', '')) and 'tiff' in str(link.get('type', '')).lower()), None)
        if not coverage:
            raise ValueError('Una colección MDT anunciada no publica su enlace GeoTIFF.')
        option = ResolutionOption(resolution, f'MDT {resolution:g} m · {"Canarias" if match.group(3)=="C" else "Península y Baleares"}',
                                  PROVIDER, _official_url(coverage), collection['id'], _epsg(match.group(1)).ExportToWkt(),
                                  True, '', API_BASE+'/collections/'+collection['id']+'?f=json')
        try:
            present = _probe(option, bounds, target, cancelled)
            status = ('Resolución nativa y elevaciones comprobadas en el centro; se validará toda la extensión al descargar.'
                      if present else 'El servicio devuelve NoData en el centro de la extensión; no se ofrece como cobertura comprobada.')
        except (ConnectionError, ValueError) as error:
            present = False
            status = 'No se ha podido comprobar esta resolución: ' + str(error)
        options.append(ResolutionOption(**{**vars(option), 'available':present, 'status':status}))
        progress(100*(index+1)/len(TARGET_RESOLUTIONS))
    return options


def _aligned_grid(bounds, step):
    left = math.floor(bounds[0]/step)*step
    bottom = math.floor(bounds[1]/step)*step
    right = math.ceil(bounds[2]/step)*step
    top = math.ceil(bounds[3]/step)*step
    cols, rows = int(round((right-left)/step)), int(round((top-bottom)/step))
    if cols <= 0 or rows <= 0 or cols*rows > MAX_CELLS:
        raise ValueError('La extensión excede el tamaño de cálculo admitido para esta resolución. Reduce la zona o elige una malla más gruesa.')
    return (left,bottom,right,top), cols, rows


def _validate_output(path, reference, bounds, cols, rows, step, fingerprint, cancelled):
    dataset = _open_raster(path)
    band = None
    try:
        actual = _reference(dataset.GetProjection(), metric=True)
        gt = dataset.GetGeoTransform()
        expected = (bounds[0],step,0,bounds[3],0,-step)
        if (not actual.IsSame(reference) or dataset.RasterXSize != cols or dataset.RasterYSize != rows
                or any(not math.isclose(a,b,rel_tol=0,abs_tol=1e-7) for a,b in zip(gt,expected))
                or dataset.GetMetadataItem('CAUMAX_DEM_KEY') != fingerprint):
            raise ValueError('El MDT descargado no conserva la extensión, CRS o resolución previstos.')
        band = dataset.GetRasterBand(1)
        for row in range(0,rows,256):
            _check(cancelled)
            array = band.ReadAsArray(0,row,cols,min(256,rows-row))
            if array is None or not bool(_valid_values(array,band.GetNoDataValue()).all()):
                raise ValueError('El MDT contiene NoData o zonas sin cobertura en la extensión solicitada; no se usa una cuenca potencialmente incompleta.')
            if (not band.GetMaskFlags() & gdal.GMF_ALL_VALID
                    and not bool((band.GetMaskBand().ReadAsArray(0,row,cols,min(256,rows-row)) != 0).all())):
                raise ValueError('El MDT tiene celdas enmascaradas sin cobertura válida.')
    finally:
        band = dataset = None


def download_dem(option, bounds, crs_wkt, destination, cancelled=lambda:False, progress=lambda n:None):
    """Download native tiles and install one validated aligned metric GeoTIFF.

    Reprojection/alignment uses nearest-neighbour at the advertised native
    resolution, never a finer invented resolution. Every output cell must
    have a valid elevation; partial/no-data coverage is rejected explicitly.
    """
    _check(cancelled)
    if not isinstance(option, ResolutionOption) or not option.available:
        raise ValueError('Esta resolución no tiene una descarga automática comprobada para la zona.')
    match = _COLLECTION.fullmatch(option.collection_id)
    if (not match or float(match.group(2)) != option.resolution_m
            or option.resolution_m not in TARGET_RESOLUTIONS):
        raise ValueError('La opción de MDT no corresponde a una resolución nativa admitida.')
    target = _reference(crs_wkt, metric=True)
    source = _reference(option.source_crs_wkt, metric=True)
    step = option.resolution_m
    aligned, cols, rows = _aligned_grid(_bounds(bounds),step)
    destination = Path(destination).expanduser().absolute()
    if destination.suffix.lower() not in ('.tif','.tiff') or not destination.parent.is_dir():
        raise ValueError('El destino del MDT debe ser un GeoTIFF en una carpeta existente.')
    _official_url(option.url)
    key_data = {'collection':option.collection_id,'resolution_m':step,'bounds':aligned,'crs_wkt':target.ExportToWkt()}
    fingerprint = hashlib.sha256(json.dumps(key_data,sort_keys=True).encode('utf-8')).hexdigest()
    cache_folder = destination.parent/'.caumax_dem_cache'
    cache = cache_folder/(fingerprint+'.tif')
    progress(0)
    for existing in (destination,cache):
        if existing.is_file():
            try:
                _validate_output(existing,target,aligned,cols,rows,step,fingerprint,cancelled)
            except ValueError:
                continue
            if existing != destination:
                with tempfile.NamedTemporaryFile(prefix='.caumax_mdt_',suffix='.tif',dir=destination.parent,delete=False) as stream:
                    copied = Path(stream.name)
                try:
                    shutil.copyfile(existing,copied)
                    _check(cancelled)
                    os.replace(copied,destination)
                finally:
                    copied.unlink(missing_ok=True)
            progress(100)
            return destination
    total_cells = cols*rows
    pending = [(col,row,min(TILE_PIXELS,cols-col),min(TILE_PIXELS,rows-row),0)
               for row in range(0,rows,TILE_PIXELS) for col in range(0,cols,TILE_PIXELS)]
    pending.reverse()
    with tempfile.TemporaryDirectory(prefix='.caumax_mdt_',dir=destination.parent) as folder:
        temporary = Path(folder)/'result.tif'
        output = gdal.GetDriverByName('GTiff').Create(str(temporary),cols,rows,1,gdal.GDT_Float32,
            options=['TILED=YES','COMPRESS=DEFLATE','PREDICTOR=3','BIGTIFF=IF_SAFER'])
        if output is None:
            raise ValueError('No se puede crear el GeoTIFF del MDT.')
        output.SetProjection(target.ExportToWkt())
        output.SetGeoTransform((aligned[0],step,0,aligned[3],0,-step))
        output.SetMetadataItem('CAUMAX_DEM_KEY',fingerprint)
        output.SetMetadataItem('MDT_PROVIDER',PROVIDER)
        output.SetMetadataItem('MDT_COLLECTION',option.collection_id)
        output.SetMetadataItem('MDT_SOURCE_URL',option.url)
        output.SetMetadataItem('MDT_NATIVE_RESOLUTION_M',str(step))
        output.SetMetadataItem('MDT_VALID_PIXEL_FRACTION','1')
        band = output.GetRasterBand(1)
        band.SetNoDataValue(NODATA)
        band.Fill(NODATA)
        completed_cells = size_retries = completed_tiles = 0
        try:
            while pending:
                _check(cancelled)
                col,row,width,height,depth = pending.pop()
                tile_bounds = (aligned[0]+col*step, aligned[3]-(row+height)*step,
                               aligned[0]+(col+width)*step, aligned[3]-row*step)
                native = _transform_bounds(tile_bounds,target,source)
                padded = (native[0]-step,native[1]-step,native[2]+step,native[3]+step)
                tile = Path(folder)/'source.tif'
                try:
                    _fetch_tiff(_coverage_url(option,padded),tile,cancelled,
                                lambda fraction:progress(95*(completed_cells+.8*fraction*width*height)/total_cells))
                except DemServiceError as error:
                    if not error.is_size_limit or depth >= MAX_SPLIT_DEPTH or (width==1 and height==1):
                        raise
                    _check(cancelled)
                    split_width = error.limit_axis=='width' or (error.limit_axis is None and width>=height)
                    if (split_width and width>1) or height==1:
                        first = width//2
                        children = [(col,row,first,height,depth+1),(col+first,row,width-first,height,depth+1)]
                    else:
                        first = height//2
                        children = [(col,row,width,first,depth+1),(col,row+first,width,height-first,depth+1)]
                    pending.extend(reversed(children))
                    size_retries += 1
                    _LOG.debug('MDT tile split (%s,%s,%s,%s) after %s',col,row,width,height,error.description)
                    continue
                _native_metadata(tile,option)
                try:
                    warped = gdal.Warp('',str(tile),format='MEM',outputBounds=tile_bounds,width=width,height=height,
                        dstSRS=target.ExportToWkt(),resampleAlg='near',dstNodata=NODATA,outputType=gdal.GDT_Float32,
                        callback=lambda fraction,message,data:0 if cancelled() else 1)
                except RuntimeError as error:
                    _check(cancelled)
                    raise ValueError('No se puede alinear el MDT descargado.') from error
                _check(cancelled)
                if warped is None:
                    raise ValueError('No se puede alinear el MDT descargado.')
                array = warped.ReadAsArray()
                if array is None or not bool(_valid_values(array,NODATA).all()):
                    warped = None
                    raise ValueError('El MDT contiene NoData o zonas sin cobertura en la extensión solicitada.')
                band.WriteArray(array,col,row)
                warped = None
                tile.unlink()
                completed_cells += width*height
                completed_tiles += 1
                progress(95*completed_cells/total_cells)
            output.SetMetadataItem('MDT_DOWNLOAD_TILES',str(completed_tiles))
            output.SetMetadataItem('MDT_TILE_SIZE_RETRIES',str(size_retries))
        finally:
            band = None
            warped = None
            try:
                output.FlushCache()
            finally:
                output = None
        _check(cancelled)
        _validate_output(temporary,target,aligned,cols,rows,step,fingerprint,cancelled)
        _check(cancelled)
        os.replace(temporary,destination)
    # Cache failures never invalidate an already successful validated download.
    try:
        cache_folder.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='mdt_',suffix='.tif',dir=cache_folder,delete=False) as stream:
            stage = Path(stream.name)
        try:
            shutil.copyfile(destination,stage)
            os.replace(stage,cache)
        finally:
            stage.unlink(missing_ok=True)
    except OSError:
        pass
    progress(100)
    return destination
