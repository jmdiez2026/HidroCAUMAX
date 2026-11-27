"""Reviewable removal of generated datasets; never infer ownership from names."""
import gc
import json
import os
from pathlib import Path
import sys
import tempfile

from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import QgsApplication, QgsProject

from .delineation_cleanup import _owned, clear_delineations

REGISTRY = 'HidroCAUMAX/generated_files_v1'
SHAPE_SUFFIXES = ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.qpj', '.qml', '.shp.xml')


def _plain_file(path):
    path = Path(path).absolute()
    return (path.is_file() and not any(p.is_symlink() or
            (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents)))


def _stamp(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def _family(path):
    path = Path(path)
    if path.suffix.lower() == '.shp':
        return [path.with_suffix(s) for s in SHAPE_SUFFIXES if path.with_suffix(s).is_file()]
    if path.suffix.lower() == '.gpkg':
        return [p for p in (path, Path(str(path) + '-wal'), Path(str(path) + '-shm')) if p.is_file()]
    return [p for p in (path, Path(str(path) + '.aux.xml'), Path(str(path) + '.ovr'),
                        Path(str(path) + '.msk')) if p.is_file()]


def _registry(project):
    try:
        value = json.loads(project.customVariables().get(REGISTRY, '{}'))
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save_registry(project, records):
    variables = project.customVariables()
    if records:
        variables[REGISTRY] = json.dumps(records)
    else:
        variables.pop(REGISTRY, None)
    project.setCustomVariables(variables)


def register_dataset(path, project=None):
    """Call only after successful creation, never when merely opening a file."""
    project = project or QgsProject.instance()
    records = _registry(project)
    for file in _family(path):
        if _plain_file(file):
            records[str(file.resolve())] = _stamp(file)
    _save_registry(project, records)


def cache_roots():
    # Do not include TauDEM executables, original CAUMAX data or arbitrary
    # parent directories. Earlier QGIS sessions use the ordinary Windows TEMP.
    from .boundary_layers import storage_directory
    return tuple({storage_directory(), Path(tempfile.gettempdir()) / 'HidroCAUMAX' / 'dem_variants',
        Path.home() / 'AppData/Local/Temp/HidroCAUMAX/dem_variants',
        Path.home() / 'AppData/Local/Temp/hc_gis/cache'})


def _generated_raster(path):
    if path.suffix.lower() not in ('.tif', '.tiff') or not _plain_file(path):
        return False
    dataset = None
    try:
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
        if dataset is None:
            return False
        tags = dataset.GetMetadata()
        return bool(tags.get('HIDROCAUMAX_PRODUCT') or
                    (tags.get('CAUMAX_DEM_KEY') and tags.get('MDT_PROVIDER')) or
                    (tags.get('SOURCE_KEY') and tags.get('HYDRO_VERSION')))
    except RuntimeError:
        return False
    finally:
        dataset = None


def _reference_files(project):
    from .map_services import reference_layers
    protected = set()
    for layer in reference_layers(project):
        if layer.providerType() not in ('gdal', 'ogr'):
            continue
        path = Path(layer.source().split('|', 1)[0])
        if _plain_file(path):
            protected.update(str(file.resolve()) for file in _family(path))
    return protected


def _protected_sources(project):
    """Neither bundled P0 nor native CAUMAX default datasets are disposable."""
    from .improved_storage import po_path
    protected = _reference_files(project)
    reference = po_path()
    for path in (reference, Path(str(reference) + '.aux.xml')):
        if _plain_file(path):
            protected.add(str(path.resolve()))
    module = sys.modules.get('caumax.layers')
    defaults = getattr(module, '_defaultLayerPaths', {}) if module else {}
    for value in defaults.values() if isinstance(defaults, dict) else ():
        try:
            path = Path(os.fspath(value))
            if _plain_file(path):
                protected.update(str(p.resolve()) for p in _family(path))
        except (OSError, ValueError, TypeError):
            pass
    return protected


def _owned_storage_roots():
    """Only private result stores; not the CAUMAX data or TauDEM runtime."""
    from .improved_storage import po_path, backup_directory
    return (po_path().parent, backup_directory(), *cache_roots())


def _private_files(root):
    root = Path(root)
    if (not root.is_dir() or root.is_symlink() or
            any(parent.is_symlink() or
                (hasattr(parent, 'is_junction') and parent.is_junction())
                for parent in root.parents)):
        return
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        # Windows junctions must not let cleanup escape a private cache.
        dirs[:] = [name for name in dirs if not (
            (Path(current) / name).is_symlink() or
            (hasattr(Path(current) / name, 'is_junction') and
             (Path(current) / name).is_junction()))]
        for name in names:
            path = Path(current) / name
            if _plain_file(path):
                yield path


def _retire_native_rational_results():
    """Release CAUMAX's original in-memory basin and on-canvas result items.

    Explicit 'Limpiar capas' only; switching basin or resolution must not
    discard native CAUMAX results.
    """
    for dialog in QApplication.topLevelWidgets():
        try:
            if not any(cls.__module__.endswith('.rationalmethoddialog')
                       and cls.__name__ == 'RationalMethodDialog'
                       for cls in type(dialog).__mro__):
                continue
            dialog.basinCalc = None
            for name in ('rubberBand', 'outletMarker', 'maxDistanceMarker'):
                item = getattr(dialog, name, None)
                if item is not None and not sip.isdeleted(item):
                    item.hide()
                    sip.delete(item)
                if hasattr(dialog, name):
                    setattr(dialog, name, None)
            for name in ('btnCalculate', 'btnReport'):
                button = getattr(dialog, name, None)
                if button is not None:
                    button.setEnabled(False)
            for name in ('txtResultFlow', 'txtResultReturnPeriod',
                         'txtArea', 'txtConcentrationTime', 'txtDistance',
                         'txtHighest', 'txtLowest'):
                field = getattr(dialog, name, None)
                if field is not None and hasattr(field, 'clear'):
                    field.clear()
        except (RuntimeError, AttributeError):
            pass


def _reset_deleted_native_sources(files):
    """Do not leave CAUMAX settings or cached providers pointing at deleted TIFFs."""
    module = sys.modules.get('caumax.layers')
    active = getattr(module, '_layerPaths', None) if module else None
    defaults = getattr(module, '_defaultLayerPaths', None) if module else None
    cache = getattr(module, '_layers', None) if module else None
    settings = QSettings()
    for name in ('MDT', 'FLOWDIRS'):
        key = getattr(module, name, name) if module else name
        setting = '/caumax/' + str(key)
        selected = active.get(key) if isinstance(active, dict) else settings.value(setting)
        try:
            path = str(Path(os.fspath(selected)).resolve()) if selected else ''
        except (OSError, ValueError, TypeError):
            path = ''
        if path not in files:
            continue
        default = defaults.get(key) if isinstance(defaults, dict) else None
        if isinstance(active, dict):
            if default:
                active[key] = default
            else:
                active.pop(key, None)
        if isinstance(cache, dict):
            cache.pop(key, None)
        if default:
            settings.setValue(setting, default)
        else:
            settings.remove(setting)
    settings.sync()


def _cancel_plugin_tasks():
    """Cancel remaining HidroCAUMAX background work, never another plugin's tasks."""
    try:
        manager = QgsApplication.taskManager()
        for task in tuple(manager.tasks()):
            if type(task).__module__.startswith('caumax_consulta.'):
                task.cancel()
    except (AttributeError, RuntimeError):
        pass


def _remove_empty_owned_directories(roots):
    for root in roots:
        root = Path(root)
        if (not root.is_dir() or root.is_symlink() or
                any(parent.is_symlink() for parent in root.parents)):
            continue
        # Never delete a parent like %TEMP%, QGIS profile, or plugin sources:
        # remove only empty subfolders and the exact owned root.
        directories = []
        for current, subdirs, names in os.walk(root, topdown=True, followlinks=False):
            subdirs[:] = [name for name in subdirs if not (
                (Path(current) / name).is_symlink() or
                (hasattr(Path(current) / name, 'is_junction') and
                 (Path(current) / name).is_junction()))]
            directories.extend(Path(current) / name for name in subdirs)
        directories.sort(key=lambda p: len(p.parts), reverse=True)
        for directory in (*directories, root):
            try:
                directory.rmdir()
            except OSError:
                # A nonempty root (notably the bundled P0) is expected.
                pass
    return None


def deletion_plan(project=None, roots=None):
    """Preview everything the explicit *destructive* cleanup will remove.

    Unlike a new-query delineation reset, this includes files in the private
    plugin export folder, its retained backup and full temporary cache trees.
    Using roots=[] intentionally limits a test/inspection to registered files.
    """
    project = project or QgsProject.instance()
    files, layers, protected = {}, {}, []
    from .map_services import reference_layers, references_loading, is_reference_group
    references = {layer.id(): layer.name() for layer in reference_layers(project)}
    references_files = _reference_files(project)
    source_protection = _protected_sources(project)
    from .improved_storage import generated_files
    for path in generated_files():
        files[str(path.resolve())] = _stamp(path)
    records = _registry(project)
    for name, stamp in records.items():
        path = Path(name)
        if _plain_file(path) and _stamp(path) == stamp:
            files[str(path.resolve())] = stamp
    # All extensions count in private cache/export stores, including worker
    # staging files, indexes, hidden backups and sidecars (not just TIFF/SHP).
    owned_roots = _owned_storage_roots() if roots is None else tuple(roots)
    for root in owned_roots:
        for path in _private_files(root):
            files[str(path.resolve())] = _stamp(path)
    for layer in project.mapLayers().values():
        if layer.id() in references:
            layers[layer.id()] = layer.name()
            continue
        raw = layer.source().split('|', 1)[0]
        path = Path(raw) if raw else None
        disk = path is not None and _plain_file(path)
        generated = disk and str(path.resolve()) not in source_protection and (
            str(path.resolve()) in files or _generated_raster(path))
        if _owned(layer) or generated:
            layers[layer.id()] = layer.name()
            if generated:
                for file in _family(path):
                    if _plain_file(file):
                        files[str(file.resolve())] = _stamp(file)
            elif disk:
                protected.append(str(path))
    # Never infer ownership of official P0 or CAUMAX defaults from file names,
    # tags or an erroneous index. They always survive on disk.
    for name in source_protection:
        files.pop(name, None)
    protected = sorted(set(protected) | source_protection)
    return dict(files=files, layers=layers, protected=protected,
                owned_roots=tuple(str(p) for p in owned_roots),
                include_references=True, references=references,
                reference_files=sorted(references_files),
                references_loading=references_loading(project),
                reference_groups=[group.name() for group in project.layerTreeRoot().findGroups(recursive=True)
                                  if is_reference_group(group) and not group.children()])


def delete_generated(plan, project=None, canvas=None):
    """Delete generated QGIS layers, disk exports, backup and temporary caches.

    File stamps protect changed paths against accidentally removing newer
    user data. Locked files are counted as failures, never falsely reported
    as successfully deleted; an immediately empty map is still guaranteed.
    """
    project = project or QgsProject.instance()
    before = set(project.mapLayers())
    protected = _protected_sources(project) | set(plan.get('reference_files', ()))
    selected = set(plan['files']) - protected
    # Cancel producers BEFORE removing providers. Worker callbacks are
    # invalidated by the controllers so late task completion cannot redraw.
    clear_delineations(close_dialogs=True, project=project, include_exports=True,
                       include_references=plan.get('include_references', False))
    _cancel_plugin_tasks()
    if project is QgsProject.instance():
        # The D8 panel may stay open after Clear. Explicitly drop its cached
        # analysis, resolution variants and ndarray products; next generation
        # is reactivated by the user, never by a background timer.
        from .delineation_cleanup import _participants
        for participant in tuple(_participants):
            try:
                purge = getattr(participant, 'purge_generated_memory', None)
                if callable(purge):
                    purge()
            except RuntimeError:
                pass
    # Workers can finish staging a file between the initial review and their
    # cancellation. A second scan is confined to the same *approved private*
    # directories; never extend to arbitrary external files after confirmation.
    files = dict(plan['files'])
    for root in plan.get('owned_roots', ()):
        for path in _private_files(root):
            key = str(path.resolve())
            if key not in files and key not in protected:
                try:
                    files[key] = _stamp(path)
                except OSError:
                    pass
    selected = set(files) - protected
    if project is QgsProject.instance():
        _retire_native_rational_results()
        _reset_deleted_native_sources(selected)
    from .improved_layers import remove_improved_folder_group
    remove_improved_folder_group(project)
    project.removeMapLayers([key for key in plan['layers'] if project.mapLayer(key)])
    gc.collect()  # Release Python-held GDAL/OGR providers before Windows unlink.
    removed, failed = [], []
    for name, stamp in files.items():
        if name in protected:
            continue
        path = Path(name)
        try:
            if not path.exists():
                continue
            if not _plain_file(path) or _stamp(path) != stamp:
                failed.append(name + ': modificado desde la confirmación')
                continue
            path.unlink()
            removed.append(name)
        except OSError as error:
            failed.append(name + ': ' + str(error))
    # GDAL/OGR can create an .aux.xml, WAL or staging file while releasing
    # a raster provider. Sweep newly materialized files once more, but only
    # under the exact private directories approved for this cleanup. Never
    # override a changed-file warning for paths in the reviewed snapshot.
    attempted = set(files)
    for root in plan.get('owned_roots', ()):
        for path in _private_files(root):
            name = str(path.resolve())
            if name in attempted or name in protected:
                continue
            attempted.add(name)
            try:
                path.unlink()
                removed.append(name)
            except OSError as error:
                failed.append(name + ': ' + str(error))
    records = _registry(project)
    for name in set(removed) | protected:
        records.pop(name, None)
    _save_registry(project, records)
    _remove_empty_owned_directories(plan.get('owned_roots', ()))
    from .delineation_cleanup import _refresh_canvases
    if canvas is not None:
        try:
            canvas.stopRendering()
            canvas.clearCache()
            canvas.refresh()
            canvas.scene().update()
            canvas.viewport().update()
        except RuntimeError:
            pass
    _refresh_canvases()
    return dict(removed=removed, failed=failed,
                layers=len(before - set(project.mapLayers())))
