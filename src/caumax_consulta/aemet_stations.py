"""A dated, attributed AEMET station snapshot backed by a real local shapefile.

The historical map and the official OpenData selectors are separate evidence.
Absence from the selectors is never described as proof of unavailable data.
"""
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from osgeo import ogr, osr
from qgis.core import (QgsApplication, QgsCategorizedSymbolRenderer, QgsMarkerSymbol,
                       QgsRendererCategory, QgsVectorLayer)


DATA_DIRECTORY = Path(__file__).resolve().parent / 'data'
STATIONS_PATH = DATA_DIRECTORY / 'aemet_stations.json'
INVENTORY_PATH = DATA_DIRECTORY / 'aemet_opendata.json'
PROVENANCE_PATH = DATA_DIRECTORY / 'aemet_provenance.json'
STATUS_CONFIRMED = 'SI'
STATUS_ABSENT = 'NO_CONSTA'
STATUS_UNKNOWN = 'SIN_COMPR'
STATUS_LABELS = {
    STATUS_CONFIRMED: 'OpenData · identificada',
    STATUS_ABSENT: 'No figura en inventarios consultados',
    STATUS_UNKNOWN: 'Sin comprobar',
}
_COLORS = {STATUS_CONFIRMED: '33,113,181,255',
           STATUS_ABSENT: '230,153,0,255', STATUS_UNKNOWN: '135,135,135,255'}
_SIDE_CARS = ('.shp', '.shx', '.dbf', '.prj', '.cpg')


def storage_directory():
    return Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'reference_layers' / 'aemet'


def _read_json(path):
    raw = Path(path).read_bytes()
    document = json.loads(raw.decode('utf-8'))
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        raise ValueError('El inventario de estaciones tiene un formato no reconocido.')
    return document, raw


def _inventory(path):
    """A missing or incomplete selector only permits positive classifications."""
    try:
        document, raw = _read_json(path)
        entries = document.get('stations')
        if not isinstance(entries, list):
            raise ValueError('Falta la lista de estaciones OpenData.')
        matches = {}
        for entry in entries:
            indicative = str(entry.get('indicative', '')).strip().upper()
            climatology, observation = entry.get('climatology') is True, entry.get('observation') is True
            if not indicative or not (climatology or observation):
                raise ValueError('El selector OpenData contiene una entrada incompleta.')
            previous = matches.get(indicative, (False, False))
            matches[indicative] = (previous[0] or climatology, previous[1] or observation)
        return document, matches, raw
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
        return {'complete': False, 'retrieved_at': '', 'scope': 'Inventarios OpenData no disponibles.'}, {}, b''


def station_status(indicative, matches, complete):
    found = matches.get(str(indicative).strip().upper())
    if found is not None:
        return STATUS_CONFIRMED, '; '.join(name for name, present in
            (('Climatología', found[0]), ('Observación', found[1])) if present)
    return (STATUS_ABSENT if complete else STATUS_UNKNOWN), ''


def _validated_stations(document):
    if document.get('crs') != 'EPSG:4326' or not isinstance(document.get('stations'), list):
        raise ValueError('Las estaciones deben ser puntos geográficos EPSG:4326.')
    seen = set()
    for station in document['stations']:
        indicative = str(station.get('indicative', '')).strip().upper()
        if not indicative or indicative in seen:
            raise ValueError('El inventario contiene un indicativo vacío o duplicado.')
        seen.add(indicative)
        longitude, latitude = float(station['longitude']), float(station['latitude'])
        if not (math.isfinite(longitude) and math.isfinite(latitude)
                and -180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError(f'Coordenadas geográficas no válidas: {indicative}.')
        if not any(variable in station.get('variables', '') for variable in ('p', 'P', 'M')):
            raise ValueError(f'La estación {indicative} no documenta una variable de precipitación.')
        altitude = station.get('altitude_m')
        if altitude is not None and not math.isfinite(float(altitude)):
            raise ValueError(f'Altitud no válida: {indicative}.')
        yield dict(station, indicative=indicative, longitude=longitude, latitude=latitude)
    if not seen:
        raise ValueError('El inventario no contiene estaciones con precipitación documentada.')


def _create_shapefile(folder, stations, inventory, matches, cancelled):
    driver = ogr.GetDriverByName('ESRI Shapefile')
    source = driver.CreateDataSource(str(folder))
    if source is None:
        raise OSError('No se puede crear el shapefile de estaciones AEMET.')
    crs = osr.SpatialReference(); crs.ImportFromEPSG(4326)
    layer = source.CreateLayer('aemet_precipitacion', srs=crs, geom_type=ogr.wkbPoint,
                               options=['ENCODING=UTF-8'])
    if layer is None:
        source = None
        raise OSError('No se puede crear la capa de puntos AEMET.')
    try:
        for name, kind, width in (
                ('INDICATIVO', ogr.OFTString, 16), ('NOMBRE', ogr.OFTString, 200),
                ('PROVINCIA', ogr.OFTString, 64), ('TIPO', ogr.OFTString, 100),
                ('VARIABLES', ogr.OFTString, 48), ('ALTITUD_M', ogr.OFTReal, 12),
                ('OPENDATA', ogr.OFTString, 12), ('OD_LISTAS', ogr.OFTString, 64),
                ('OD_FECHA', ogr.OFTString, 10), ('BASE_ANO', ogr.OFTInteger, 4)):
            field = ogr.FieldDefn(name, kind); field.SetWidth(width)
            if kind == ogr.OFTReal:
                field.SetPrecision(2)
            if layer.CreateField(field) != 0:
                raise OSError(f'No se puede crear el campo {name} del shapefile AEMET.')
        counts = Counter()
        for station in stations:
            if cancelled():
                raise InterruptedError('Carga de estaciones AEMET cancelada.')
            status, lists = station_status(station['indicative'], matches, inventory.get('complete') is True)
            counts[status] += 1
            feature = ogr.Feature(layer.GetLayerDefn())
            values = {'INDICATIVO': station['indicative'], 'NOMBRE': station.get('name', ''),
                      'PROVINCIA': station.get('province', ''), 'TIPO': '; '.join(station.get('types', [])),
                      'VARIABLES': station['variables'], 'ALTITUD_M': station.get('altitude_m'),
                      'OPENDATA': status, 'OD_LISTAS': lists,
                      'OD_FECHA': str(inventory.get('retrieved_at', ''))[:10], 'BASE_ANO': 2017}
            for name, value in values.items():
                if value is not None:
                    feature.SetField(name, value)
            point = ogr.Geometry(ogr.wkbPoint)
            point.AddPoint_2D(station['longitude'], station['latitude'])
            feature.SetGeometry(point)
            if layer.CreateFeature(feature) != 0:
                raise OSError(f'No se puede guardar la estación {station["indicative"]}.')
            feature = None
    finally:
        layer = source = None
    return dict(counts)


def materialize_station_shapefile(directory=None, *, stations_path=STATIONS_PATH,
                                  inventory_path=INVENTORY_PATH, provenance_path=PROVENANCE_PATH,
                                  cancelled=lambda: False):
    """Publish a complete shapefile directory, keeping older project paths valid."""
    document, station_bytes = _read_json(stations_path)
    stations = list(_validated_stations(document))
    inventory, matches, inventory_bytes = _inventory(inventory_path)
    provenance_bytes = Path(provenance_path).read_bytes()
    provenance = json.loads(provenance_bytes.decode('utf-8'))
    if not isinstance(provenance, dict):
        raise ValueError('Falta la procedencia del inventario histórico AEMET.')
    digest = hashlib.sha256(b'aemet-shapefile-v2\0' + station_bytes + b'\0'
                            + inventory_bytes + b'\0' + provenance_bytes).hexdigest()
    directory = storage_directory() if directory is None else Path(directory)
    target = directory / digest[:24]
    path = target / 'aemet_precipitacion.shp'
    if cancelled():
        raise InterruptedError('Carga de estaciones AEMET cancelada.')
    if not target.exists():
        directory.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='aemet_', dir=str(directory)))
        try:
            counts = _create_shapefile(staging, stations, inventory, matches, cancelled)
            manifest = {'dataset_sha256': digest, 'station_count': len(stations), 'status_counts': counts,
                        'opendata_retrieved_at': inventory.get('retrieved_at', ''),
                        'opendata_complete': inventory.get('complete') is True,
                        'opendata_scope': inventory.get('scope', ''),
                        'opendata_inventory_sha256': hashlib.sha256(inventory_bytes).hexdigest(),
                        'opendata_inventory': inventory, 'provenance': provenance}
            (staging / 'provenance.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            if cancelled():
                raise InterruptedError('Carga de estaciones AEMET cancelada.')
            try:
                os.rename(staging, target)
            except FileExistsError:
                # Another QGIS window may have prepared the identical snapshot.
                pass
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if not all(path.with_suffix(suffix).is_file() for suffix in _SIDE_CARS):
        raise OSError('El shapefile AEMET guardado está incompleto; conserve o retire su carpeta antes de repetir la carga.')
    manifest = json.loads((target / 'provenance.json').read_text(encoding='utf-8'))
    if manifest.get('dataset_sha256') != digest:
        raise ValueError('La procedencia del shapefile AEMET no coincide con el inventario empaquetado.')
    return path, manifest


def station_renderer():
    categories = []
    for status, label in STATUS_LABELS.items():
        symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': _COLORS[status],
            'outline_color': '55,55,55,255', 'outline_width': '0.18', 'size': '2.2'})
        categories.append(QgsRendererCategory(status, symbol, label))
    return QgsCategorizedSymbolRenderer('OPENDATA', categories)


def create_station_layer(title='Estaciones AEMET · precipitación (inventario histórico)', directory=None,
                         *, stations_path=STATIONS_PATH, inventory_path=INVENTORY_PATH,
                         provenance_path=PROVENANCE_PATH, cancelled=lambda: False):
    path, manifest = materialize_station_shapefile(directory, stations_path=stations_path,
        inventory_path=inventory_path, provenance_path=provenance_path, cancelled=cancelled)
    layer = QgsVectorLayer(str(path), title, 'ogr')
    if (not layer.isValid() or layer.featureCount() != manifest['station_count']
            or layer.crs().authid() != 'EPSG:4326'):
        raise ValueError('El shapefile AEMET no contiene todos los puntos esperados.')
    layer.setRenderer(station_renderer())
    for name, alias in {'INDICATIVO': 'Indicativo AEMET', 'NOMBRE': 'Nombre de estación',
            'PROVINCIA': 'Provincia', 'TIPO': 'Tipos en el inventario histórico',
            'VARIABLES': 'Variables documentadas (códigos de la fuente)', 'ALTITUD_M': 'Altitud (m)',
            'OPENDATA': 'OpenData · estado en inventarios consultados',
            'OD_LISTAS': 'Selectores oficiales en los que figura', 'OD_FECHA': 'Fecha de consulta OpenData',
            'BASE_ANO': 'Año de publicación de la copia histórica'}.items():
        layer.setFieldAlias(layer.fields().indexFromName(name), alias)
    layer.setDisplayExpression('"NOMBRE" || \' (\' || "INDICATIVO" || \')\'')
    counts = manifest['status_counts']
    date = str(manifest.get('opendata_retrieved_at') or 'sin comprobar')[:10]
    metadata = layer.metadata()
    metadata.setTitle(title)
    metadata.setAbstract(
        f'Red histórica de {manifest["station_count"]} estaciones con precipitación documentada. '
        'Fuente: AEMET; copia publicada por Esri España en 2017, asociada al catálogo '
        'MITECO de 16/04/2016. © Ministerio para la Transición Ecológica (MITECO). '
        'No acredita que las estaciones sigan activas ni identidad exacta con el ZIP original del catálogo. '
        'Se unen los tipos y variables de registros con el mismo indicativo; se incluyen variables p, P o M. '
        f'Cruce con selectores oficiales de AEMET OpenData (climatología y observación), consulta {date}: '
        f'{counts.get(STATUS_CONFIRMED, 0)} identificadas; {counts.get(STATUS_ABSENT, 0)} no figuran; '
        f'{counts.get(STATUS_UNKNOWN, 0)} sin comprobar. '
        'La presencia identifica la estación en los inventarios consultados; no garantiza una variable, '
        'período o serie concreta. La ausencia no demuestra que carezca de datos abiertos. '
        f'Archivo SHP persistente: {path}. Para trasladar el proyecto, copie también esta carpeta '
        'con sus archivos .shp, .shx, .dbf, .prj, .cpg y provenance.json, o exporte la capa '
        'mediante «Guardar objetos como…» a la carpeta del proyecto y use esa copia.')
    layer.setMetadata(metadata)
    layer.setCustomProperty('HidroCAUMAX/aemet_dataset_sha256', manifest['dataset_sha256'])
    layer.setCustomProperty('HidroCAUMAX/aemet_provenance', str(path.parent / 'provenance.json'))
    return layer
