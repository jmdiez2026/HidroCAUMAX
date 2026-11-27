"""Portable self-contained HidroCAUMAX basin archives (.hidrocuenca).

Archives preserve QGIS-generated vector/raster layers (including in-memory
Esri-D8 networks, closure points, boundaries and styles), the complete set of
files in ResolucionMejorada, original consultation coordinates and presentation
state. No unrelated QGIS/user data is bundled. Legacy .caumax.json is unchanged.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
                       QgsProject, QgsRasterLayer, QgsVectorFileWriter, QgsVectorLayer)

EXTENSION = '.hidrocuenca'
SCHEMA = 'hidrocaumax-complete-basin'
MAX_ENTRIES = 6000
MAX_INPUT_BYTES = 12 * 1024**3
OWNERS = (
    'HidroCAUMAX/course_owner',
    'HidroCAUMAX/drainage_owner',
    'HidroCAUMAX/boundary_owner',
    'HidroCAUMAX/terrain_owner',
    'HidroCAUMAX/export_owner',
    'HidroCAUMAX/closure_point',
)


def _safe_key(path):
    value = PurePosixPath(str(path))
    if (value.is_absolute() or not value.parts or
            any(item in ('', '.', '..') for item in value.parts)
            or ':' in value.parts[0] or '\\' in str(path)):
        raise ValueError('El archivo de cuenca contiene una ruta no permitida.')
    return str(value)


def _owned(layer):
    return any(layer.customProperty(key, None) not in (None, '', False) for key in OWNERS)


def _primitive(value):
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _properties(layer):
    data = {}
    for key in layer.customPropertyKeys():
        if key.startswith('HidroCAUMAX/'):
            value = _primitive(layer.customProperty(key))
            if value is not None:
                data[key] = value
    return data


def _point(x, y, kind, crs='EPSG:25830'):
    try:
        x, y = float(x), float(y)
        if math.isfinite(x) and math.isfinite(y):
            return dict(x=x, y=y, kind=kind, crs=crs)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _query_points(native_dialog=None, queries=()):
    points = []
    if native_dialog is not None:
        try:
            from .rational_layers_bridge import _number
            point = _point(_number(native_dialog.txtX.text()),
                           _number(native_dialog.txtY.text()), 'metodo_racional')
            if point:
                points.append(point)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
    for query in queries:
        if query is None:
            continue
        crs = getattr(query, 'crs', None)
        point = _point(getattr(query, 'x', None), getattr(query, 'y', None),
                       getattr(query, 'source_kind', 'consulta_capas'),
                       crs.authid() if crs is not None and crs.isValid() else 'EPSG:25830')
        if point and point not in points:
            points.append(point)
    return points


def _native_values(dialog):
    """Retain the complete visible parameter/result fields of Método racional."""
    if dialog is None:
        return {}
    values = {}
    for key in ('txtX', 'txtY', 'txtArea', 'txtConcentrationTime',
                'txtDistance', 'txtHighest', 'txtLowest', 'txtRainfall',
                'txtIntensityFactor', 'txtI1Id', 'txtIntensity', 'txtP0',
                'txtP0Corrector', 'txtP0CorrectorReturnPeriod',
                'txtP0Corrected', 'txtUniformity', 'txtResultFlow',
                'txtResultReturnPeriod', 'txtCorrectedRainfall'):
        try:
            widget = getattr(dialog, key, None)
            if widget is not None and hasattr(widget, 'text'):
                values[key] = str(widget.text())[:2048]
        except RuntimeError:
            pass
    return values


def _selected_layers(project):
    root = project.layerTreeRoot()
    selected = []
    for node in root.findLayers():
        layer = node.layer()
        if layer is None or not layer.isValid() or not _owned(layer):
            continue
        group = node.parent()
        owner = group.customProperty('HidroCAUMAX/drainage_owner', '') if group else ''
        selected.append((layer, group, node, owner))
    return selected


def _digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _add_file(archive, path, member, digests, total):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Falta un archivo necesario de la cuenca: ' + str(path))
    member = _safe_key(member)
    size = path.stat().st_size
    total[0] += size
    if total[0] > MAX_INPUT_BYTES or len(digests) >= MAX_ENTRIES:
        raise ValueError('La cuenca supera el límite de seguridad de archivos o volumen.')
    archive.write(path, member)
    digests[member] = dict(sha256=_digest(path), size=size)
    return member


def save_complete_basin(destination, *, project=None, iface=None,
                        native_dialog=None, queries=(), sessions=()):
    """Atomically save all HidroCAUMAX results plus full improved directory."""
    from .improved_storage import export_directory
    destination = Path(destination).expanduser().absolute()
    if destination.suffix.lower() != EXTENSION:
        raise ValueError('Guardar CUENCA requiere un archivo .hidrocuenca.')
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError('El destino ya existe o no es una carpeta válida.')
    project = project or QgsProject.instance()
    improved = export_directory().resolve()
    # User archives must survive the destructive "Limpiar capas" action, which
    # deliberately clears ResolucionMejorada and its private backups.
    if improved == destination.parent or improved in destination.parents:
        raise ValueError('Guarda CUENCA fuera de ResolucionMejorada para que Limpiar capas no borre el archivo.')
    with tempfile.TemporaryDirectory(prefix='.hidrocuenca-stage-', dir=destination.parent) as tmp:
        stage = Path(tmp)
        zip_path = stage / 'complete.tmp'
        digests = {}
        total = [0]
        manifest = dict(schema=SCHEMA, version=1, application='HidroCAUMAX',
                        project_crs=project.crs().authid(),
                        points=_query_points(native_dialog, queries),
                        rational_values=_native_values(native_dialog),
                        layers=[], improved_files=[], native_sessions=[],
                        unavailable_layers=[])
        if iface is not None:
            try:
                extent = iface.mapCanvas().extent()
                manifest['extent'] = [extent.xMinimum(), extent.yMinimum(),
                                      extent.xMaximum(), extent.yMaximum()]
            except (AttributeError, RuntimeError):
                pass
        try:
            with ZipFile(zip_path, 'w', compression=ZIP_DEFLATED, compresslevel=5,
                         allowZip64=True) as archive:
                for index, (layer, group, node, _) in enumerate(_selected_layers(project)):
                    info = dict(name=layer.name(), order=index,
                                group=group.name() if group is not None else '',
                                group_properties=dict(
                                    (key, _primitive(group.customProperty(key)))
                                    for key in ('HidroCAUMAX/drainage_owner',
                                                'HidroCAUMAX/drainage_published',
                                                'HidroCAUMAX/boundary_owner',
                                                'HidroCAUMAX/course_owner',
                                                'HidroCAUMAX/terrain_owner',
                                                'HidroCAUMAX/order_rank')
                                    if group is not None and
                                    _primitive(group.customProperty(key)) is not None),
                                visible=node.itemVisibilityChecked(),
                                group_visible=group.itemVisibilityChecked() if group else True,
                                properties=_properties(layer), crs=layer.crs().authid())
                    if isinstance(layer, QgsVectorLayer):
                        source = stage / ('feature_%04d.gpkg' % index)
                        options = QgsVectorFileWriter.SaveVectorOptions()
                        options.driverName = 'GPKG'
                        options.fileEncoding = 'UTF-8'
                        options.layerName = 'features'
                        result = QgsVectorFileWriter.writeAsVectorFormatV3(
                            layer, str(source), project.transformContext(), options)
                        if result[0] != QgsVectorFileWriter.NoError:
                            raise ValueError('No se pudo guardar la capa ' + layer.name()
                                             + ': ' + str(result[1]))
                        info['type'] = 'vector'
                    elif isinstance(layer, QgsRasterLayer):
                        source = Path(layer.source().split('|', 1)[0])
                        if layer.providerType() != 'gdal' or not source.is_file():
                            manifest['unavailable_layers'].append(layer.name())
                            continue
                        info['type'] = 'raster'
                    else:
                        continue
                    info['data'] = _add_file(
                        archive, source, 'layers/%04d.%s' %
                        (index, 'gpkg' if info['type'] == 'vector' else
                         source.suffix.lower().lstrip('.') or 'tif'), digests, total)
                    style = stage / ('style_%04d.qml' % index)
                    layer.saveNamedStyle(str(style))
                    if style.is_file():
                        info['style'] = _add_file(
                            archive, style, 'styles/%04d.qml' % index, digests, total)
                    manifest['layers'].append(info)
                # A session document also contains morphometry, hydrology,
                # hypsometry, original/refined basin geometry, selected flow
                # and return period. Keep each valid session alongside QGIS
                # layer sources; a partially computed session is not invented.
                from .session_store import save_session
                for index, dialog in enumerate(sessions):
                    analysis = getattr(dialog, 'analysis', None)
                    query = getattr(dialog, 'query', None)
                    if analysis is None or query is None:
                        continue
                    native_path = stage / ('analysis_%04d.caumax.json' % index)
                    try:
                        save_session(native_path, analysis, query,
                                     dialog.get_smoothing_state())
                        member = _add_file(
                            archive, native_path, 'sessions/%04d.caumax.json' % index,
                            digests, total)
                        manifest['native_sessions'].append(member)
                    except (ValueError, AttributeError, RuntimeError, TypeError) as error:
                        manifest['unavailable_layers'].append(
                            'Descripción de cuenca no serializable: ' + str(error)[:250])
                if improved.is_dir():
                    for file in sorted(improved.rglob('*')):
                        if (not file.is_file() or file.is_symlink()
                                or EXTENSION in file.suffix.lower()
                                or 'CuencasGuardadas' in file.relative_to(improved).parts):
                            continue
                        relative = file.relative_to(improved).as_posix()
                        member = _add_file(archive, file, 'improved/' + relative,
                                           digests, total)
                        manifest['improved_files'].append(member)
                manifest['checksums'] = digests
                archive.writestr('manifest.json', json.dumps(
                    manifest, ensure_ascii=False, allow_nan=False, sort_keys=True))
            os.link(zip_path, destination)
        except Exception:
            if destination.exists():
                destination.unlink()
            raise
    return destination, manifest


def _validate_zip(archive):
    files = archive.infolist()
    if len(files) > MAX_ENTRIES + 1 or sum(x.file_size for x in files) > MAX_INPUT_BYTES:
        raise ValueError('El archivo de cuenca supera los límites admitidos.')
    seen = set()
    for info in files:
        name = _safe_key(info.filename)
        if (name in seen or info.is_dir()
                or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK):
            raise ValueError('El archivo contiene rutas duplicadas o enlaces prohibidos.')
        seen.add(name)
    if 'manifest.json' not in seen:
        raise ValueError('No existe el manifiesto de cuenca.')
    manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA
            or manifest.get('version') != 1
            or not isinstance(manifest.get('checksums'), dict)
            or not isinstance(manifest.get('layers'), list)):
        raise ValueError('El archivo no es una cuenca HidroCAUMAX compatible.')
    if seen != set(manifest['checksums']) | {'manifest.json'}:
        raise ValueError('La cuenca no contiene exactamente los archivos declarados.')
    for name, description in manifest['checksums'].items():
        _safe_key(name)
        if (not isinstance(description, dict)
                or description.get('size') != archive.getinfo(name).file_size
                or not isinstance(description.get('sha256'), str)
                or len(description['sha256']) != 64):
            raise ValueError('Inventario de archivos de cuenca incompatible.')
    return manifest


def open_complete_basin(source, *, project=None, iface=None):
    """Validate/extract atomically and load saved QGIS layers and styles."""
    from .improved_storage import export_directory
    from .delineation_cleanup import clear_delineations
    from .layer_order import ensure_results_above_official
    source = Path(source)
    project = project or QgsProject.instance()
    destination_root = export_directory() / 'CuencasGuardadas'
    destination_root.mkdir(parents=True, exist_ok=True)
    target = destination_root / (source.stem[:48] + '_' + uuid4().hex[:12])
    with ZipFile(source, 'r') as archive:
        manifest = _validate_zip(archive)
        target.mkdir()
        try:
            for name, checksum in manifest['checksums'].items():
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(name) as original, destination.open('xb') as output:
                    for part in iter(lambda: original.read(1024 * 1024), b''):
                        output.write(part)
                        digest.update(part)
                if digest.hexdigest() != checksum['sha256']:
                    raise ValueError('Archivo de cuenca alterado: ' + name)
            loaded = []
            for item in manifest['layers']:
                if not isinstance(item, dict):
                    raise ValueError('Registro de capa inválido.')
                member = _safe_key(item['data'])
                if member not in manifest['checksums']:
                    raise ValueError('Falta un archivo de capa.')
                path = target / member
                if item['type'] == 'vector':
                    layer = QgsVectorLayer(str(path), str(item['name']), 'ogr')
                elif item['type'] == 'raster':
                    layer = QgsRasterLayer(str(path), str(item['name']), 'gdal')
                else:
                    raise ValueError('Tipo de capa no compatible.')
                if not layer.isValid():
                    raise ValueError('No se puede cargar la capa archivada: ' + str(item['name']))
                style = item.get('style')
                if style is not None:
                    if _safe_key(style) not in manifest['checksums']:
                        raise ValueError('Estilo de capa inexistente.')
                    layer.loadNamedStyle(str(target / style))
                for key, value in item.get('properties', {}).items():
                    if str(key).startswith('HidroCAUMAX/'):
                        layer.setCustomProperty(key, value)
                loaded.append((item, layer))
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    # All source files and layer providers are verified before changing QGIS.
    clear_delineations(close_dialogs=True, include_exports=True)
    groups = {}
    root = project.layerTreeRoot()
    for item, layer in loaded:
        group_name = str(item.get('group') or 'HidroCAUMAX · Cuenca recuperada')
        if group_name not in groups:
            group = root.insertGroup(0, group_name)
            for key, value in item.get('group_properties', {}).items():
                if key.startswith('HidroCAUMAX/') and value is not None:
                    group.setCustomProperty(key, value)
            group.setItemVisibilityChecked(bool(item.get('group_visible', True)))
            groups[group_name] = group
        project.addMapLayer(layer, False)
        node = groups[group_name].addLayer(layer)
        node.setItemVisibilityChecked(bool(item.get('visible', True)))
        node.setExpanded(False)
    ensure_results_above_official(project)
    if iface is not None and 'extent' in manifest:
        try:
            from qgis.core import QgsRectangle
            from qgis.core import QgsCoordinateReferenceSystem
            crs = QgsCoordinateReferenceSystem(manifest.get('project_crs', ''))
            if crs.isValid():
                iface.mapCanvas().setDestinationCrs(crs)
            extent = QgsRectangle(*manifest['extent'])
            if not extent.isEmpty() and extent.isFinite():
                iface.mapCanvas().setExtent(extent)
            iface.mapCanvas().clearCache()
            iface.mapCanvas().refresh()
        except (TypeError, ValueError, RuntimeError):
            pass
    return manifest, target
