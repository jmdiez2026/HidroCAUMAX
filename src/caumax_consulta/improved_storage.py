"""Installed export folder, verified raster pairs and update-safe preservation."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from osgeo import gdal, osr
from qgis.core import QgsApplication


PLUGIN_ROOT = Path(__file__).resolve().parent
FOLDER_NAME = 'ResolucionMejorada'
PO_FILENAME = 'Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif'
PAIR_INDEX = '.hidrocaumax-calculation-pairs.json'
BACKUP_INDEX = '.hidrocaumax-preserved-files.json'
GENERATED_INDEX = '.hidrocaumax-generated-files.json'


def export_directory():
    folder = PLUGIN_ROOT / FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def po_path():
    return PLUGIN_ROOT / FOLDER_NAME / PO_FILENAME


def backup_directory():
    return Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / FOLDER_NAME


def _plain(path):
    return path.is_file() and not any(p.is_symlink() or
        (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents))


def _stamp(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def _read(path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.hc-index-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_missing(source, target):
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in target.parents):
        raise OSError('La carpeta de conservación contiene un enlace: ' + str(target.parent))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        if not target.exists():
            created = False
            try:
                with source.open('rb') as src, target.open('xb') as dst:
                    created = True
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                shutil.copystat(source, target)
            except OSError:
                if created:
                    target.unlink(missing_ok=True)
                raise


def preserve_exports():
    """Keep generated files outside the directory QGIS replaces on updates."""
    folder, backup = export_directory(), backup_directory()
    previous = _read(backup / BACKUP_INDEX, [])
    entries = []
    for source in folder.rglob('*'):
        if not _plain(source) or source.name == PO_FILENAME:
            continue
        relative = source.relative_to(folder)
        # Skip partial exports still in their private staging directory.
        if any(p.startswith(('.hidrocaumax_export_', '.hc-index-')) for p in relative.parts):
            continue
        target = backup / relative
        if target.exists() and (not _plain(target) or _stamp(target) != _stamp(source)):
            # Only our backup copy is replaced; the user's source is untouched.
            if not _plain(target):
                raise OSError('La copia de conservación no es un archivo normal: ' + str(target))
            target.unlink()
        _copy_missing(source, target)
        entries.append(relative.as_posix())
    # Restore only this current snapshot, so explicit cleaning cannot resurrect files.
    if entries or backup.exists():
        _write(backup / BACKUP_INDEX, entries)
    # Explicitly removed exports must not survive as invisible backup copies.
    for name in previous if isinstance(previous, list) else []:
        if not isinstance(name, str) or name in entries:
            continue
        relative = Path(name)
        if relative.drive or relative.is_absolute() or '..' in relative.parts or ':' in name:
            continue
        target = backup / relative
        if _plain(target) and target.resolve().is_relative_to(backup.resolve()):
            target.unlink()


def prepare_export_directory(legacy_alias=False):
    """Prepare the installed folder without recovering old calculation results."""
    folder = export_directory()
    # The compatibility alias lives outside ResolucionMejorada. Never populate
    # this folder from a previous snapshot merely because QGIS has started.
    if legacy_alias and po_path().is_file():
        _copy_missing(po_path(), PLUGIN_ROOT / 'data' / PO_FILENAME)
    return folder


def restore_exports(legacy_alias=False):
    """Explicit recovery helper only; plugin startup must not call this."""
    folder, backup = prepare_export_directory(legacy_alias), backup_directory()
    entries = _read(backup / BACKUP_INDEX, [])
    for relative in entries if isinstance(entries, list) else []:
        if not isinstance(relative, str):
            continue
        path = Path(relative)
        if path.drive or path.is_absolute() or '..' in path.parts or ':' in relative or path.name == PO_FILENAME:
            continue
        source, target = backup / path, folder / path
        if _plain(source) and not target.exists():
            _copy_missing(source, target)


def _compatible(dem_path, directions_path):
    dem = directions = None
    try:
        if not _plain(dem_path) or not _plain(directions_path):
            return False
        dem, directions = gdal.Open(str(dem_path)), gdal.Open(str(directions_path))
        if dem is None or directions is None or dem.RasterCount != 1 or directions.RasterCount != 1:
            return False
        if dem.GetMetadataItem('HIDROCAUMAX_PRODUCT') != 'ELEVATION':
            return False
        if (directions.GetMetadataItem('HIDROCAUMAX_PRODUCT') != 'FLOW_DIRECTION' or
                directions.GetMetadataItem('FLOW_DIRECTION_ENCODING') != 'ESRI_D8'):
            return False
        from .hydro_export import _aligned, _reference
        _aligned(dem, directions)
        crs = osr.SpatialReference()
        crs.ImportFromEPSG(25830)
        crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        return bool(_reference(dem.GetProjectionRef()).IsSame(crs))
    except (RuntimeError, ValueError, OSError):
        return False
    finally:
        dem = directions = None


def record_exports(exports):
    """Called after successful exports from one processing result, never by name."""
    folder = export_directory().resolve()
    if exports.get('dem') and exports.get('directions_caumax'):
        dem, directions = (Path(exports[k]).resolve() for k in ('dem', 'directions_caumax'))
        if dem.parent == folder and directions.parent == folder and _compatible(dem, directions):
            records = _read(folder / PAIR_INDEX, [])
            records = records if isinstance(records, list) else []
            pair = dict(dem=dem.name, directions=directions.name,
                        dem_stamp=_stamp(dem), directions_stamp=_stamp(directions),
                        exported_ns=time.time_ns())
            records = [r for r in records if isinstance(r, dict) and
                       (r.get('dem'), r.get('directions')) != (dem.name, directions.name)]
            previous = _read(folder / PAIR_INDEX, [])
            same = [r for r in previous if isinstance(r, dict) and
                    all(r.get(k) == pair[k] for k in ('dem', 'directions', 'dem_stamp', 'directions_stamp'))] if isinstance(previous, list) else []
            if not same:
                _write(folder / PAIR_INDEX, records + [pair])
    # Persist only explicit successful exports, never files merely opened.
    from .generated_cleanup import _family
    owned = _read(folder / GENERATED_INDEX, {})
    owned = owned if isinstance(owned, dict) else {}
    for name in exports.values():
        path = Path(name).resolve()
        if path.parent != folder or path.name == PO_FILENAME:
            continue
        for member in _family(path):
            if _plain(member):
                owned[member.name] = _stamp(member)
    if owned:
        _write(folder / GENERATED_INDEX, owned)
    preserve_exports()


def generated_files():
    folder = export_directory()
    owned = _read(folder / GENERATED_INDEX, {})
    for name, stamp in owned.items() if isinstance(owned, dict) else ():
        if not name or Path(name).name != name or ':' in name or name == PO_FILENAME:
            continue
        path = folder / name
        if _plain(path) and _stamp(path) == stamp:
            yield path


def latest_pair():
    folder = export_directory()
    records = _read(folder / PAIR_INDEX, [])
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        names = [record.get(k) for k in ('dem', 'directions')]
        if any(not isinstance(n, str) or not n or ':' in n or Path(n).name != n for n in names):
            continue
        dem, directions = (folder / n for n in names)
        try:
            if (_stamp(dem) == record.get('dem_stamp') and
                    _stamp(directions) == record.get('directions_stamp') and _compatible(dem, directions)):
                return dem, directions
        except OSError:
            continue
    return None
