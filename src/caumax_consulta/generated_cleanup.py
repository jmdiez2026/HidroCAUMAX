"""Reviewable removal of generated datasets; never infer ownership from names."""
import json
from pathlib import Path
import tempfile

from osgeo import gdal
from qgis.core import QgsProject

from .delineation_cleanup import _owned, clear_delineations

REGISTRY = 'HidroCAUMAX/generated_files_v1'
SHAPE_SUFFIXES = ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx', '.qpj', '.shp.xml')


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
    variables[REGISTRY] = json.dumps(records)
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


def deletion_plan(project=None, roots=None):
    project = project or QgsProject.instance()
    files, layers, protected = {}, {}, []
    from .map_services import reference_layers, references_loading, is_reference_group
    references = {layer.id(): layer.name() for layer in reference_layers(project)}
    reference_files = _reference_files(project)
    from .improved_storage import generated_files
    for path in generated_files():
        files[str(path.resolve())] = _stamp(path)
    records = _registry(project)
    for name, stamp in records.items():
        path = Path(name)
        if _plain_file(path) and _stamp(path) == stamp:
            files[str(path.resolve())] = stamp
    # Private generated caches also identify products from previous sessions.
    for root in cache_roots() if roots is None else roots:
        root = Path(root)
        if not root.is_dir() or any(p.is_symlink() or
                (hasattr(p, 'is_junction') and p.is_junction()) for p in (root, *root.parents)):
            continue
        for path in root.rglob('*'):
            if _plain_file(path) and path.suffix.lower() in ('.tif', '.tiff', '.shp', '.gpkg'):
                for file in _family(path):
                    if _plain_file(file):
                        files[str(file.resolve())] = _stamp(file)
    for layer in project.mapLayers().values():
        if layer.id() in references:
            layers[layer.id()] = layer.name()
            continue  # Reference data are never candidates for disk deletion.
        raw = layer.source().split('|', 1)[0]
        path = Path(raw) if raw else None
        disk = path is not None and _plain_file(path)
        generated = disk and str(path.resolve()) not in reference_files and (
            str(path.resolve()) in files or _generated_raster(path))
        if _owned(layer) or generated:
            layers[layer.id()] = layer.name()
            if generated:
                for file in _family(path):
                    if _plain_file(file):
                        files[str(file.resolve())] = _stamp(file)
            elif disk:
                protected.append(str(path))
    # Even an erroneous old export record or a shared source cannot make a
    # reference raster (notably the supplied P0) a disposable generated file.
    for name in reference_files:
        files.pop(name, None)
    protected = sorted(set(protected) | reference_files)
    return dict(files=files, layers=layers, protected=protected,
                include_references=True, references=references, reference_files=sorted(reference_files),
                references_loading=references_loading(project),
                reference_groups=[group.name() for group in project.layerTreeRoot().findGroups(recursive=True)
                                  if is_reference_group(group) and not group.children()])


def delete_generated(plan, project=None):
    """Execute exactly the reviewed file snapshot, reporting partial failures."""
    project = project or QgsProject.instance()
    before = set(project.mapLayers())
    protected_references = set(plan.get('reference_files', ())) | _reference_files(project)
    # Cancel all participating calculations and release QGIS raster handles
    # before unlinking files. Late task callbacks cannot reinsert cleared layers.
    clear_delineations(project=project, include_references=plan.get('include_references', False))
    project.removeMapLayers([key for key in plan['layers'] if project.mapLayer(key)])
    removed, failed = [], []
    for name, stamp in plan['files'].items():
        if name in protected_references:
            continue
        path = Path(name)
        try:
            if not path.exists():
                continue
            if not _plain_file(path) or _stamp(path) != stamp:
                failed.append(name + ': ha cambiado desde la confirmación')
                continue
            path.unlink()
            removed.append(name)
        except OSError as error:
            failed.append(name + ': ' + str(error))
    records = _registry(project)
    for name in set(removed) | protected_references:
        records.pop(name, None)
    _save_registry(project, records)
    return dict(removed=removed, failed=failed, layers=len(before - set(project.mapLayers())))
