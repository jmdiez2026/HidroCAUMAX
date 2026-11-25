"""True TauDEM CLI products, native D8 and explicit CAUMAX recoding.

No GRASS fallback. Filled elevations route flow only; analysis reads the original
DEM. Every subprocess is private and cancellable. No QGIS objects are created.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import numpy as np
from osgeo import gdal, ogr, osr

from . import basin as _b
from . import dem_hydrology as _h
from . import taudem_runtime as _runtime
from .grid import cell_at_point
from .terrain import analyze_basin

INSTALL_URL = "https://github.com/dtarb/TauDEM/releases/latest"
_TOOLS = ("pitremove", "d8flowdir", "aread8", "threshold", "streamnet")
_CLI_NAMES = {"pitremove": "PitRemove", "d8flowdir": "D8FlowDir", "aread8": "AreaD8",
              "threshold": "Threshold", "streamnet": "StreamNet"}
_VERSION = "taudem-native-products-v1"
_D8_VERSION = "taudem-pitremove-d8-native-v1"
_D8_TOOLS = ("pitremove", "d8flowdir")
_LUT = np.array([255, 1, 128, 64, 32, 16, 8, 4, 2], dtype=np.uint8)


@dataclass(frozen=True)
class TauDEMProducts:
    analysis: object
    dem_path: str
    filled_dem_path: str
    tau_d8_path: str
    caumax_d8_path: str
    slope_path: str
    accumulation_path: str
    stream_raster_path: str
    network_path: str
    threshold_cells: int
    threshold_area_m2: float
    metadata: dict


@dataclass(frozen=True)
class TauDEMDirections:
    dem_path: str
    filled_dem_path: str
    tau_d8_path: str
    caumax_d8_path: str
    slope_path: str
    cache_directory: str
    source_key: str
    cache_reused: bool
    metadata: dict


def probe_taudem(bin_dir=None, *, required_tools=None):
    """Find the real tools and MPI without changing environment or settings."""
    required_tools = _TOOLS if required_tools is None else tuple(required_tools)
    if not required_tools or any(tool not in _TOOLS for tool in required_tools):
        raise ValueError('Herramientas TauDEM solicitadas no válidas.')
    if bin_dir is None:
        try:
            bundled = _runtime.discover_bundled_runtime()
        except (OSError, ValueError) as error:
            return {"available": False, "reason": "No se puede usar el TauDEM integrado: " + str(error),
                    "executables": {}, "mpiexec": None, "engine": "TauDEM", "installation_url": INSTALL_URL}
        if bundled is not None:
            return dict(bundled, installation_url=INSTALL_URL)
    folders = []
    configured = ((bin_dir,) if bin_dir is not None else
                  (os.environ.get("TAUDEM_BIN"), os.environ.get("TAUDEM_PATH")))
    for value in configured:
        if value:
            root = Path(value).expanduser()
            folders.extend((root, root / "bin", root / "TauDEM5Exe"))
    if os.name == "nt" and bin_dir is None:
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(key)
            if base and Path(base).is_dir():
                for root in sorted(Path(base).glob("TauDEM*")):
                    folders.extend((root, root / "bin", root / "TauDEM5Exe"))
    executables, mpi = {}, None
    for folder in folders:
        if not folder.is_dir():
            continue
        names = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
        local_mpi = names.get("mpiexec.exe" if os.name == "nt" else "mpiexec")
        if mpi is None and local_mpi and (os.name == "nt" or os.access(local_mpi, os.X_OK)):
            mpi = str(local_mpi.resolve())
        for tool in _TOOLS:
            candidate = names.get(tool + (".exe" if os.name == "nt" else ""))
            if candidate and (os.name == "nt" or os.access(candidate, os.X_OK)):
                executables.setdefault(tool, str(candidate.resolve()))
    if bin_dir is None:
        for tool in _TOOLS:
            candidate = shutil.which(tool) or shutil.which(_CLI_NAMES[tool])
            if candidate:
                executables.setdefault(tool, str(Path(candidate).resolve()))
    mpi = mpi or shutil.which("mpiexec") or shutil.which("mpiexec.exe")
    if not mpi and os.name == "nt":
        candidates = []
        if os.environ.get("MSMPI_BIN"):
            candidates.append(Path(os.environ["MSMPI_BIN"]) / "mpiexec.exe")
        if os.environ.get("ProgramFiles"):
            candidates.append(Path(os.environ["ProgramFiles"]) / "Microsoft MPI" / "Bin" / "mpiexec.exe")
        mpi = next((str(path) for path in candidates if path.is_file()), None)
    missing = [tool for tool in required_tools if tool not in executables]
    if not mpi:
        missing.append("MPI/mpiexec")
    return {"available": not missing, "reason": "" if not missing else
            "TauDEM real no está disponible. Falta: " + ", ".join(missing) +
            ". Revisa el motor predeterminado y sus dependencias oficiales: " + INSTALL_URL,
            "executables": executables, "mpiexec": mpi, "engine": "TauDEM",
            "installation_url": INSTALL_URL, "bin_dir": str(bin_dir) if bin_dir is not None else None,
            "runtime": {"kind": "external"}}


def _fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(engine, tool, arguments, work, cancelled):
    _h._check(cancelled)
    engine = _runtime.ensure_runtime(engine, cancelled)
    command = [engine["mpiexec"], "-n", "1", engine["executables"][tool], *map(str, arguments)]
    env = _runtime.subprocess_environment(engine, work)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    logfile = work / (tool + ".log")
    started = time.monotonic()
    with logfile.open("wb") as output:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, cwd=str(work), env=env, **kwargs)
        job = _h._windows_job(process)
        try:
            while True:
                _h._check(cancelled)
                try:
                    status = process.wait(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _h._stop_process(process, job)
            raise
        finally:
            if job is not None:
                job[0].CloseHandle(job[1])
    _h._check(cancelled)
    text = logfile.read_text(encoding="utf-8", errors="replace")
    if status:
        raise ValueError(f"TauDEM {tool} falló (código{status}). No se usa otro motor.\n" + text[-9000:])
    version = re.search(r"\bversion\s+([^\s]+)", text, re.I)
    if version is None:
        raise ValueError(f"El ejecutable {tool} no identifica una versión TauDEM. "
                         "Revise la carpeta seleccionada; no se sustituye por otro motor.")
    return {"tool": tool, "seconds": time.monotonic() - started,
            "version": version.group(1), "returncode": status}


def _recode_tau_d8(native_path, destination, dem_path, cancelled):
    source = dem = target = None
    try:
        source, dem = _b._open_raster(native_path, "D8 TauDEM"), _b._open_raster(dem_path, "MDT original")
        _b._aligned(source, dem)
        target = gdal.GetDriverByName("GTiff").Create(str(destination), source.RasterXSize,
            source.RasterYSize, 1, gdal.GDT_Byte, ["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"])
        if target is None:
            raise ValueError("No se puede crear el D8 compatible con CAUMAX.")
        target.SetGeoTransform(source.GetGeoTransform())
        target.SetProjection(source.GetProjectionRef())
        target.SetMetadata({"HIDROCAUMAX_PRODUCT": "FLOW_DIRECTION", "FLOW_DIRECTION_ENCODING": "ESRI_D8",
                            "HIDROCAUMAX_ENGINE": "TauDEM", "HYDROLOGY_ENGINE": "TauDEM",
                            "D8_SOURCE_ENCODING": "TAUDEM_D8"})
        out, band = target.GetRasterBand(1), source.GetRasterBand(1)
        out.SetNoDataValue(255)
        nodata = band.GetNoDataValue()
        for top in range(0, source.RasterYSize, 128):
            _h._check(cancelled)
            values = _b._read(band, 0, top, source.RasterXSize, min(128, source.RasterYSize - top))
            valid = np.isfinite(values)
            if nodata is not None:
                valid &= values != nodata
            if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
                valid &= _b._read(band.GetMaskBand(), 0, top, values.shape[1], values.shape[0]) != 0
            original = dem.GetRasterBand(1)
            elevations = _b._read(original, 0, top, values.shape[1], values.shape[0])
            valid &= np.isfinite(elevations)
            if original.GetNoDataValue() is not None:
                valid &= elevations != original.GetNoDataValue()
            if not original.GetMaskFlags() & gdal.GMF_ALL_VALID:
                valid &= _b._read(original.GetMaskBand(), 0, top, values.shape[1], values.shape[0]) != 0
            if np.any(valid & ((values < 1) | (values > 8) | (values != np.trunc(values)))):
                raise ValueError("La salida TauDEM contiene códigos distintos de1..8.")
            coded = np.full(values.shape, 255, dtype=np.uint8)
            coded[valid] = _LUT[values[valid].astype(np.intp)]
            out.WriteArray(coded, 0, top)
        target.FlushCache()
    finally:
        source = dem = target = None


def _cached_directions(paths, manifest, dem, key, hashes, runtime):
    """Recognize this engine's aligned products, never a GRASS cache."""
    if not manifest.is_file() or not all(path.is_file() for path in paths):
        return None
    try:
        provenance = json.loads(manifest.read_text(encoding='utf-8'))
        steps = provenance.get('steps', [])
        if (provenance.get('source_key') != key or provenance.get('binary_sha256') != hashes
                or provenance.get('runtime') != runtime or len(steps) != 2
                or [step.get('tool') for step in steps] != list(_D8_TOOLS)
                or any(not step.get('version') for step in steps)):
            return None
        for path in paths:
            cached = _b._open_raster(path, 'producto TauDEM D8 en caché')
            try:
                _b._aligned(cached, dem)
                if path == paths[-1] and not (
                        cached.GetMetadataItem('HYDRO_VERSION') == _D8_VERSION
                        and cached.GetMetadataItem('SOURCE_KEY') == key
                        and cached.GetMetadataItem('HYDROLOGY_ENGINE') == 'TauDEM'
                        and cached.GetRasterBand(1).DataType == gdal.GDT_Byte
                        and cached.GetRasterBand(1).GetNoDataValue() == 255):
                    return None
            finally:
                cached = None
        return provenance
    except (OSError, ValueError, TypeError, AttributeError, RuntimeError):
        return None


def _prepare_d8_cache(engine, source, dem, cache_dir, cancelled, progress):
    """The shared, unchanged PitRemove/D8FlowDir sequence used by both paths."""
    hashes = {name: _fingerprint(engine['executables'][name]) for name in _D8_TOOLS}
    runtime = engine.get('runtime', {'kind': 'external'})
    key = hashlib.sha256(repr((_D8_VERSION, _h._source_key(dem, source), hashes, runtime)).encode()).hexdigest()
    requested = _h._cache_folder(Path(cache_dir).expanduser().resolve())
    directory_name = 'taudem_d8_' + key[:24]
    # Saved variants supply the existing D8's parent during reference rechecks.
    # It already names this exact source/engine key; do not nest the cache.
    base = requested if requested.name == directory_name else requested/directory_name
    base.mkdir(parents=True, exist_ok=True)
    filled, native, slope, coded = paths = tuple(base/name for name in (
        'filled.tif', 'd8_tau.tif', 'slope_tau.tif', 'd8_caumax.tif'))
    manifest = base/'source.json'
    provenance = _cached_directions(paths, manifest, dem, key, hashes, runtime)
    reused = provenance is not None
    if not reused:
        with tempfile.TemporaryDirectory(prefix='td_', dir=str(_h._runtime_folder()), ignore_cleanup_errors=True) as temporary:
            work = Path(temporary)
            staged = _h._stage_dem(source, dem, work, cancelled)
            progress('Preparando MDT con TauDEM PitRemove', 5)
            steps = [_run(engine, 'pitremove', ['-z', staged, '-fel', work/'filled.tif'], work, cancelled)]
            progress('Calculando direcciones TauDEM D8', 20)
            steps.append(_run(engine, 'd8flowdir', ['-fel', work/'filled.tif', '-p', work/'d8_tau.tif',
                                                  '-sd8', work/'slope_tau.tif'], work, cancelled))
            _recode_tau_d8(work/'d8_tau.tif', work/'d8_caumax.tif', source, cancelled)
            converted = gdal.Open(str(work/'d8_caumax.tif'), gdal.GA_Update)
            if converted is None:
                raise ValueError('No se puede identificar la caché D8 TauDEM generada.')
            converted.SetMetadataItem('HYDRO_VERSION', _D8_VERSION)
            converted.SetMetadataItem('SOURCE_KEY', key)
            converted.FlushCache()
            converted = None
            provenance = dict(source_key=key, steps=steps, binary_sha256=hashes, runtime=runtime)
            for target in paths:
                _h._check(cancelled)
                _h._publish_cache(work/target.name, target, cancelled)
            staged_manifest = work/'source.json'
            staged_manifest.write_text(json.dumps(provenance), encoding='utf-8')
            _h._publish_cache(staged_manifest, manifest, cancelled)
    _h._check(cancelled)
    metadata = dict(engine='TauDEM', version=provenance['steps'][0]['version'],
        source_url='https://github.com/dtarb/TauDEM', source_key=key, cache_reused=reused,
        binary_sha256=hashes, runtime=runtime, steps=provenance['steps'],
        flow_encoding='ESRI_D8', native_flow_encoding='TAUDEM_D8',
        conditioning='TauDEM PitRemove and D8FlowDir; filled DEM routes flow only',
        elevation_source='original DEM; not filled or resampled', d8_only=True)
    return TauDEMDirections(str(source), str(filled), str(native), str(coded), str(slope),
                           str(base), key, reused, metadata)


def prepare_d8(dem_path, cache_dir, *, bin_dir=None, cancelled=lambda: False,
               progress=lambda text, percent: None):
    """Prepare native D8 with the bundled engine, without a network or outlet.

    Only PitRemove, D8FlowDir and MPI are required. The caller performs its own
    containing-cell/reference-basin selection and analyses original elevations.
    """
    _h._check(cancelled)
    engine = probe_taudem(bin_dir, required_tools=_D8_TOOLS)
    if not engine['available']:
        raise ValueError(engine['reason'])
    if engine.get('private_runtime'):
        progress('Preparando TauDEM integrado y verificando sus archivos', 0)
    engine = _runtime.ensure_runtime(engine, cancelled)
    source = Path(dem_path).expanduser().resolve()
    dem = _b._open_raster(source, 'MDT TauDEM')
    try:
        _b._aligned(dem, dem)
        if dem.RasterXSize * dem.RasterYSize > _h.MAX_RASTER_CELLS:
            raise ValueError('El MDT supera el límite de celdas de esta versión; no se remuestrea.')
        return _prepare_d8_cache(engine, source, dem, cache_dir, cancelled, progress)
    finally:
        dem = None


def _outlet(path, x, y, wkt):
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    if ds is None:
        raise ValueError("No se puede crear el cierre de trabajo TauDEM.")
    reference = osr.SpatialReference(wkt=wkt)
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    layer = ds.CreateLayer("outlet", reference, ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    feature = ogr.Feature(layer.GetLayerDefn())
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint_2D(x, y)
    feature.SetGeometry(point)
    feature.SetField("id", 1)
    if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
        raise ValueError("No se puede escribir el cierre de trabajo TauDEM.")
    feature = layer = ds = None


class EmptyStreamNetworkError(ValueError):
    """TauDEM finished, but no drainage reaches were produced for this cutoff."""


def _threshold(area_m2, cell_area):
    area = _h._positive_finite(area_m2, "El umbral de red")
    cells = max(1, int(math.ceil(area / cell_area)))
    return cells, cells * cell_area


def _network(engine, filled, native, accumulation, outlet, threshold_cells, folder, work, cancelled, progress):
    stream, network = folder / "streams.tif", folder / "network.shp"
    if (folder / "complete.json").is_file() and stream.is_file() and network.is_file():
        vector = ogr.Open(str(network))
        valid = vector is not None and vector.GetLayer(0).GetFeatureCount() > 0
        vector = None
        if valid:
            return stream, network, []
    staging = work / ("network_" + str(threshold_cells))
    staging.mkdir(exist_ok=True)
    staged_stream, staged_network = staging / stream.name, staging / network.name
    progress("Definiendo red TauDEM", 75)
    steps = [_run(engine, "threshold", ["-ssa", accumulation, "-src", staged_stream,
                   "-thresh", threshold_cells], work, cancelled)]
    progress("Generando cauces TauDEM", 85)
    steps.append(_run(engine, "streamnet", ["-fel", filled, "-p", native,
        "-ad8", accumulation, "-src", staged_stream, "-o", outlet, "-lyrname", "outlet",
        "-ord", staging / "order.tif", "-tree", staging / "tree.dat",
        "-coord", staging / "coord.dat", "-net", staged_network,
        "-w", staging / "watersheds.tif", "-sw"], work, cancelled))
    vector = ogr.Open(str(staged_network))
    if vector is None or vector.GetLayer(0).GetFeatureCount() <= 0:
        raise EmptyStreamNetworkError("El umbral no produce cauces TauDEM en la cuenca. Elija un umbral menor.")
    vector = None
    _h._check(cancelled)
    folder.mkdir(parents=True, exist_ok=True)
    for source in sorted(staging.iterdir()):
        _h._check(cancelled)
        _h._publish_cache(source, folder / source.name, cancelled)
    (folder / "complete.json").write_text(json.dumps({"threshold_cells": threshold_cells}), encoding="utf-8")
    return stream, network, steps


def _preserved_outlet(directions_path, counts, transform, target_crs, x, y, parameters,
                      outlet_analysis, refinement_reference, cancelled):
    """Confirm the same basin from the original click; never move a manual cell.

    Different D8 engines can put the channel in adjacent cells. Automatic
    correspondence must pass both the coarse footprint and the accepted fine
    footprint. Its section is always selected from the original red point.
    """
    _h._check(cancelled)
    rows, cols = counts.shape
    bounds = (transform[0], transform[3] + rows * transform[5],
              transform[0] + cols * transform[1], transform[3])
    previous_snap = ((outlet_analysis.diagnostics or {}).get('outlet_snap') or {}
                     if outlet_analysis is not None else {})
    manual = (previous_snap.get('method') == 'manual_cell_d8'
              and previous_snap.get('explicit_user_choice') is True)
    effective_x, effective_y = (float(outlet_analysis.outlet_x), float(outlet_analysis.outlet_y)) \
        if outlet_analysis is not None else (float(x), float(y))
    convert = None
    if outlet_analysis is not None:
        source_crs = osr.SpatialReference()
        if source_crs.ImportFromWkt(str(outlet_analysis.crs_wkt)) != 0:
            raise ValueError('No se conserva el CRS del cierre aceptado para TauDEM.')
        source_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not source_crs.IsSame(target_crs):
            convert = osr.CoordinateTransformation(source_crs, target_crs)
            x, y, *_ = convert.TransformPoint(float(x), float(y))
            effective_x, effective_y, *_ = convert.TransformPoint(effective_x, effective_y)
    original = cell_at_point(float(x), float(y), *bounds, cols, rows)
    previous = cell_at_point(effective_x, effective_y, *bounds, cols, rows)
    effective = previous
    area = effective.width * effective.height
    reference_area, _, radius = parameters
    snap = dict(previous_snap) if manual else None
    selection = dict(method='manual_cell' if manual else 'containing_cell',
        original_x=float(x), original_y=float(y),
        previous_outlet_x=previous.x, previous_outlet_y=previous.y,
        previous_engine=(outlet_analysis.diagnostics or {}).get('dem_hydrology', {}).get('engine')
                        if outlet_analysis is not None else None,
        engine='TauDEM', crs_wkt=target_crs.ExportToWkt(), explicit_user_choice=manual,
        coarse_reference_checked=False, fine_reference_checked=False)
    if refinement_reference is not None and not manual:
        from .refinement_snap import select_reference_outlet
        try:
            effective, snap = select_reference_outlet(directions_path, counts,
                float(x), float(y), radius, refinement_reference, cancelled,
                reference_area_m2=reference_area)
            selection.update(method='original_point_reference_section', coarse_reference_checked=True)
            if outlet_analysis is not None:
                fine_reference = dict(geometry_wkb=outlet_analysis.geometry_wkb,
                    main_course_wkb=outlet_analysis.main_course_wkb, crs_wkt=outlet_analysis.crs_wkt,
                    cell_width=outlet_analysis.cell_width, cell_height=outlet_analysis.cell_height)
                checked, evidence = select_reference_outlet(directions_path, counts,
                    effective.x, effective.y, radius, fine_reference, cancelled,
                    reference_area_m2=outlet_analysis.area_m2)
                if (checked.row, checked.col) != (effective.row, effective.col):
                    raise ValueError('El cierre compatible con 500 m no conserva la cuenca fina aceptada.')
                selection.update(method='original_point_dual_reference_section', fine_reference_checked=True,
                    fine_reference_geometry_sha256=hashlib.sha256(bytes(outlet_analysis.geometry_wkb)).hexdigest(),
                    fine_reference_area_m2=float(outlet_analysis.area_m2),
                    fine_reference_raster_area_m2=evidence['reference_raster_area_m2'],
                    fine_intersection_area_m2=evidence['intersection_area_m2'],
                    fine_reference_coverage_fraction=evidence['reference_coverage_fraction'],
                    fine_candidate_overlap_fraction=evidence['candidate_overlap_fraction'],
                    fine_intersection_over_union=evidence['intersection_over_union'])
        except ValueError as error:
            raise ValueError('Las direcciones TauDEM no conservan la cuenca aceptada en una '
                'sección compatible. No se sustituye el delineado ni se cambia de cauce. ' + str(error)) from error
    count = int(counts[effective.row, effective.col])
    if count <= 0:
        raise ValueError('El cierre elegido no tiene dirección TauDEM válida (NoData); '
                         'no se publica una cuenca sin drenaje válido.')
    if manual and convert is not None:
        if 'manual_requested_x' in snap and 'manual_requested_y' in snap:
            sx, sy, *_ = convert.TransformPoint(float(snap['manual_requested_x']), float(snap['manual_requested_y']))
            snap['manual_requested_x'], snap['manual_requested_y'] = sx, sy
        if snap.get('manual_selection_scope') == 'raster_extent':
            snap.update(manual_raster_bounds=list(bounds), manual_raster_size=[cols, rows])
    if snap is not None:
        original_area = int(counts[original.row, original.col]) * area
        snap.update(original_x=float(x), original_y=float(y), original_row=original.row, original_col=original.col,
            snapped_x=effective.x, snapped_y=effective.y, snapped_row=effective.row, snapped_col=effective.col,
            status='unchanged' if (original.row, original.col) == (effective.row, effective.col) else 'snapped',
            distance_m=math.hypot(effective.x - x, effective.y - y), search_radius_m=radius,
            reference_area_m2=reference_area, minimum_upstream_area_m2=area,
            candidate_upstream_area_m2=count * area, original_cell_upstream_area_m2=original_area,
            area_change_vs_original_cell_m2=count * area - original_area,
            area_change_vs_reference_pct=100 * (count * area / reference_area - 1))
    selection.update(outlet_x=effective.x, outlet_y=effective.y,
        distance_from_original_m=math.hypot(effective.x - x, effective.y - y),
        outlet_preserved=(previous.row, previous.col) == (effective.row, effective.col),
        outlet_adjustment_m=math.hypot(effective.x - previous.x, effective.y - previous.y))
    if selection['coarse_reference_checked']:
        selection.update(coarse_reference_coverage_fraction=snap['reference_coverage_fraction'],
                         coarse_candidate_overlap_fraction=snap['candidate_overlap_fraction'])
    _h._check(cancelled)
    return effective, snap, selection


def prepare_taudem(dem_path, outlet_x, outlet_y, cache_dir, *, reference_area_m2,
                   reference_cell_width, reference_cell_height, threshold_area_m2=None,
                   outlet_analysis=None, refinement_reference=None,
                   bin_dir=None, cancelled=lambda: False, progress=lambda text, percent: None):
    """True TauDEM products at a fixed manual or spatially verified outlet.

    ``outlet_analysis`` supplies the accepted fine basin and input CRS. Manual
    cells stay fixed. Automatic sections use the original point and must match
    both this footprint and ``refinement_reference`` with TauDEM's own D8.
    The actual basin is always recomputed; no geometry or area is inherited.
    """
    engine = probe_taudem(bin_dir)
    if not engine["available"]:
        raise ValueError(engine["reason"])
    _h._check(cancelled)
    if engine.get("private_runtime"):
        progress("Preparando TauDEM integrado y verificando sus archivos", 0)
    engine = _runtime.ensure_runtime(engine, cancelled)
    source = Path(dem_path).expanduser().resolve()
    dem = _b._open_raster(source, "MDT TauDEM")
    try:
        transform, reference = _b._aligned(dem, dem)
        total = dem.RasterXSize * dem.RasterYSize
        if total > _h.MAX_RASTER_CELLS:
            raise ValueError("El MDT supera el límite de celdas de esta versión; no se remuestrea.")
        parameters = _h._snap_parameters(reference_area_m2, reference_cell_width,
                                         reference_cell_height, None, .9)
        directions = _prepare_d8_cache(engine, source, dem, cache_dir, cancelled, progress)
        hashes = {name: _fingerprint(path) for name, path in engine["executables"].items()}
        runtime = directions.metadata['runtime']
        base = Path(directions.cache_directory)
        product_key = hashlib.sha256(repr((_VERSION, hashes, runtime)).encode()).hexdigest()[:16]
        filled, native, slope, coded = map(Path, (directions.filled_dem_path,
            directions.tau_d8_path, directions.slope_path, directions.caumax_d8_path))
        provenance = directions.metadata
        steps = []
        with tempfile.TemporaryDirectory(prefix="td_", dir=str(_h._runtime_folder()), ignore_cleanup_errors=True) as temporary:
            work = Path(temporary)
            progress("Calculando aporte y verificando cierre", 35)
            counts, inclusive_path, reused = _h._accumulation(coded, cancelled,
                lambda fraction: progress("Calculando aporte y verificando cierre", 35 + 15 * fraction))
            effective, snap, selection = _preserved_outlet(coded, counts, transform, reference,
                outlet_x, outlet_y, parameters, outlet_analysis, refinement_reference, cancelled)
            del counts
            count, _, boundary, _ = _h._preflight_analysis(coded, effective, cancelled)
            cell_area = abs(transform[1] * transform[5])
            if snap is not None:
                if snap["candidate_upstream_area_m2"] != count * cell_area:
                    raise ValueError("La acumulación TauDEM no coincide con su cuenca.")
                snap["candidate_touches_boundary"] = bool(boundary)
            progress("Analizando cuenca TauDEM", 52)
            analysis = analyze_basin(coded, source, effective.x, effective.y, cancelled=cancelled, upstream_only=False,
                progress=lambda n: progress("Analizando cuenca TauDEM", 52 + min(10, 10 * n / max(count, 1))))
            selection_key = hashlib.sha256(repr((effective.row, effective.col)).encode()).hexdigest()[:16]
            selected_folder = base / ('products_' + product_key) / ("outlet_" + selection_key)
            selected_folder.mkdir(parents=True, exist_ok=True)
            closure = selected_folder / "outlet.gpkg"
            accumulation = selected_folder / "area_tau.tif"
            if not closure.is_file():
                _outlet(closure, effective.x, effective.y, reference.ExportToWkt())
            if not accumulation.is_file():
                progress("Acumulando cuenca con TauDEM AreaD8", 65)
                steps.append(_run(engine, "aread8", ["-p", native, "-ad8", work / "area_tau.tif",
                    "-o", closure, "-lyrname", "outlet"], work, cancelled))
                area_source = work / "area_tau.tif"
            else:
                area_source = accumulation
            area_ds = _b._open_raster(area_source, "área contribuyente TauDEM")
            _b._aligned(area_ds, dem)
            value = float(area_ds.GetRasterBand(1).ReadAsArray(effective.col, effective.row, 1, 1)[0, 0])
            area_nodata = area_ds.GetRasterBand(1).GetNoDataValue()
            area_ds = None
            if not math.isfinite(value) or value == area_nodata or value <= 0:
                raise ValueError("TauDEM detecta contaminación de borde en la cuenca; amplíe la cobertura del MDT.")
            if not math.isclose(value, count, rel_tol=1e-6, abs_tol=.01):
                raise ValueError("El aporte de AreaD8 no coincide con la delimitación TauDEM inclusiva.")
            if area_source != accumulation:
                _h._publish_cache(area_source, accumulation, cancelled)
            from .drainage_threshold import recommended_threshold, automatic_threshold_cells
            recommended = recommended_threshold(analysis.area_m2, cell_area)
            is_automatic = threshold_area_m2 is None
            candidates = (automatic_threshold_cells(analysis.area_m2, cell_area)
                          if is_automatic else (_threshold(threshold_area_m2, cell_area)[0],))
            attempted = []
            for cells in candidates:
                _h._check(cancelled)
                net_folder = selected_folder / ("network_" + str(cells))
                attempted.append(cells)
                try:
                    stream, network, network_steps = _network(engine, filled, native, accumulation,
                        closure, cells, net_folder, work, cancelled, progress)
                except EmptyStreamNetworkError:
                    if not is_automatic or cells == candidates[-1]:
                        raise ValueError('TauDEM no genera tramos conectados al cierre, incluso '
                            'con un umbral reducido. Revisa la correspondencia del cierre, '
                            'las direcciones D8 y la cobertura del MDT.') from None
                    progress('Umbral sin cauces; comprobando otro más pequeño', 87)
                    continue
                steps.extend(network_steps)
                break
            threshold_area = cells * cell_area
            # The automatic control must restore the last verified value,
            # not retry a larger cutoff that produced no streams.
            if is_automatic:
                recommended = threshold_area
        metadata = {"engine": "TauDEM", "version": provenance["steps"][0]["version"],
                    "binary_sha256": hashes, "runtime": runtime, "source_url": "https://github.com/dtarb/TauDEM",
                    "steps": provenance["steps"] + steps,
                    "flow_encoding": "ESRI_D8", "native_flow_encoding": "TAUDEM_D8",
                    "elevation_source": "original DEM; filled DEM routes flow only",
                    "threshold_area_m2": threshold_area, "threshold_cells": cells,
                    "recommended_threshold_area_m2": recommended, "automatic": threshold_area_m2 is None,
                    "threshold_policy": "provisional 0.5% basin area, min 8 cells, max 5%; bounded half-threshold retries if empty",
                    "threshold_candidates_cells": attempted,
                    "threshold_auto_reductions": len(attempted)-1 if is_automatic else 0,
                    "outlet_path": str(closure), "bin_dir": str(bin_dir) if bin_dir is not None else None,
                    "inclusive_accumulation_path": inclusive_path,
                    "edge_contamination_check": True, "outlet_selection": selection}
        diagnostic = dict(analysis.diagnostics)
        diagnostic.update(taudem_pipeline_executed=True, dem_hydrology=metadata)
        if snap is not None:
            diagnostic['outlet_snap'] = snap
        notes = tuple(note for note in analysis.notes if "No se ejecuta TauDEM" not in note)
        notes += ("Direcciones y red calculadas con TauDEM real: PitRemove, D8FlowDir, AreaD8, Threshold y StreamNet. "
                  "Las cotas, TRI y pendiente local siguen usando el MDT original, no el MDT rellenado.",)
        analysis = replace(analysis, selected_x=selection['original_x'], selected_y=selection['original_y'],
                           diagnostics=diagnostic, notes=notes)
        _h._check(cancelled)
        progress("Capas TauDEM preparadas", 100)
        return TauDEMProducts(analysis, str(source), str(filled), str(native), str(coded), str(slope),
            str(accumulation), str(stream), str(network), cells, threshold_area, metadata)
    finally:
        dem = None


def rebuild_network(products, threshold_area_m2, *, bin_dir=None,
                    cancelled=lambda: False, progress=lambda text, percent: None):
    """Reuse pit removal/directions/area, leaving Analysis and snap untouched."""
    engine = probe_taudem(bin_dir if bin_dir is not None else products.metadata.get("bin_dir"))
    if not engine["available"]:
        raise ValueError(engine["reason"])
    _h._check(cancelled)
    engine = _runtime.ensure_runtime(engine, cancelled)
    previous_runtime = products.metadata.get("runtime", {})
    if previous_runtime.get("sha256") and previous_runtime != engine.get("runtime"):
        raise ValueError("El runtime TauDEM ha cambiado; genere de nuevo las capas antes de modificar la red.")
    cells, area = _threshold(threshold_area_m2, products.analysis.cell_width * products.analysis.cell_height)
    folder = Path(products.accumulation_path).parent / ("network_" + str(cells))
    with tempfile.TemporaryDirectory(prefix="tn_", dir=str(_h._runtime_folder()), ignore_cleanup_errors=True) as temporary:
        stream, network, steps = _network(engine, products.filled_dem_path, products.tau_d8_path,
            products.accumulation_path, products.metadata["outlet_path"], cells, folder,
            Path(temporary), cancelled, progress)
    metadata = dict(products.metadata)
    metadata.update(threshold_area_m2=area, threshold_cells=cells, automatic=False)
    _h._check(cancelled)
    progress("Red TauDEM actualizada", 100)
    return replace(products, stream_raster_path=str(stream), network_path=str(network),
                   threshold_cells=cells, threshold_area_m2=area, metadata=metadata)
