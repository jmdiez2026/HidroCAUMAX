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
from .terrain import analyze_basin

INSTALL_URL = "https://github.com/dtarb/TauDEM/releases/latest"
_TOOLS = ("pitremove", "d8flowdir", "aread8", "threshold", "streamnet")
_CLI_NAMES = {"pitremove": "PitRemove", "d8flowdir": "D8FlowDir", "aread8": "AreaD8",
              "threshold": "Threshold", "streamnet": "StreamNet"}
_VERSION = "taudem-native-products-v1"
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


def probe_taudem(bin_dir=None):
    """Find the real tools and MPI without changing environment or settings."""
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
    missing = [tool for tool in _TOOLS if tool not in executables]
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
            if np.any(valid & ((values < 1) | (values > 8) | (values != np.trunc(values)))):
                raise ValueError("La salida TauDEM contiene códigos distintos de1..8.")
            coded = np.full(values.shape, 255, dtype=np.uint8)
            coded[valid] = _LUT[values[valid].astype(np.intp)]
            out.WriteArray(coded, 0, top)
        target.FlushCache()
    finally:
        source = dem = target = None


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
    staging = work / "network"
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
        raise ValueError("El umbral no produce cauces TauDEM en la cuenca. Elija un umbral menor.")
    vector = None
    _h._check(cancelled)
    folder.mkdir(parents=True, exist_ok=True)
    for source in sorted(staging.iterdir()):
        _h._check(cancelled)
        _h._publish_cache(source, folder / source.name, cancelled)
    (folder / "complete.json").write_text(json.dumps({"threshold_cells": threshold_cells}), encoding="utf-8")
    return stream, network, steps


def prepare_taudem(dem_path, outlet_x, outlet_y, cache_dir, *, reference_area_m2,
                   reference_cell_width, reference_cell_height, threshold_area_m2=None,
                   bin_dir=None, cancelled=lambda: False, progress=lambda text, percent: None):
    """True TauDEM→snap→Analysis→outlet-restricted stream products."""
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
        hashes = {name: _fingerprint(path) for name, path in engine["executables"].items()}
        runtime = engine.get("runtime", {"kind": "external"})
        key = hashlib.sha256(repr((_VERSION, _h._source_key(dem, source), hashes, runtime)).encode()).hexdigest()[:24]
        base = _h._cache_folder(Path(cache_dir).expanduser().resolve()) / ("taudem_" + key)
        base.mkdir(parents=True, exist_ok=True)
        filled, native, slope, coded = (base / name for name in ("filled.tif", "d8_tau.tif", "slope_tau.tif", "d8_caumax.tif"))
        manifest = base / "source.json"
        steps = []
        with tempfile.TemporaryDirectory(prefix="td_", dir=str(_h._runtime_folder()), ignore_cleanup_errors=True) as temporary:
            work = Path(temporary)
            if not all(path.is_file() for path in (filled, native, slope, coded, manifest)):
                staged = _h._stage_dem(source, dem, work, cancelled)
                progress("Preparando MDT con TauDEM PitRemove", 5)
                steps.append(_run(engine, "pitremove", ["-z", staged, "-fel", work / "filled.tif"], work, cancelled))
                progress("Calculando direcciones TauDEM D8", 20)
                steps.append(_run(engine, "d8flowdir", ["-fel", work / "filled.tif", "-p", work / "d8_tau.tif",
                                                        "-sd8", work / "slope_tau.tif"], work, cancelled))
                _recode_tau_d8(work / "d8_tau.tif", work / "d8_caumax.tif", source, cancelled)
                for target in (filled, native, slope, coded):
                    _h._check(cancelled)
                    _h._publish_cache(work / target.name, target, cancelled)
                manifest.write_text(json.dumps({"steps": steps, "binary_sha256": hashes, "runtime": runtime}), encoding="utf-8")
            provenance = json.loads(manifest.read_text(encoding="utf-8"))
            steps = []
            progress("Calculando aporte y ajustando cierre", 35)
            counts, inclusive_path, reused = _h._accumulation(coded, cancelled,
                lambda fraction: progress("Calculando aporte y ajustando cierre", 35 + 15 * fraction))
            effective, snap = _h._nearest_main_channel(counts, transform, outlet_x, outlet_y,
                                                       *parameters, cancelled=cancelled,
                                                       directions_path=coded)
            del counts
            count, _, boundary, _ = _h._preflight_analysis(coded, effective, cancelled)
            cell_area = abs(transform[1] * transform[5])
            if snap["candidate_upstream_area_m2"] != count * cell_area:
                raise ValueError("La acumulación TauDEM no coincide con su cuenca.")
            snap["candidate_touches_boundary"] = bool(boundary)
            progress("Analizando cuenca TauDEM", 52)
            analysis = analyze_basin(coded, source, effective.x, effective.y, cancelled=cancelled,
                progress=lambda n: progress("Analizando cuenca TauDEM", 52 + min(10, 10 * n / max(count, 1))))
            selection_key = hashlib.sha256(repr((effective.row, effective.col)).encode()).hexdigest()[:16]
            selected_folder = base / ("outlet_" + selection_key)
            selected_folder.mkdir(exist_ok=True)
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
            recommended = max(100 * cell_area, .01 * analysis.area_m2)
            cells, threshold_area = _threshold(recommended if threshold_area_m2 is None else threshold_area_m2, cell_area)
            net_folder = selected_folder / ("network_" + str(cells))
            stream, network, network_steps = _network(engine, filled, native, accumulation,
                closure, cells, net_folder, work, cancelled, progress)
            steps.extend(network_steps)
        metadata = {"engine": "TauDEM", "version": provenance["steps"][0]["version"],
                    "binary_sha256": hashes, "runtime": runtime, "source_url": "https://github.com/dtarb/TauDEM",
                    "steps": provenance["steps"] + steps,
                    "flow_encoding": "ESRI_D8", "native_flow_encoding": "TAUDEM_D8",
                    "elevation_source": "original DEM; filled DEM routes flow only",
                    "threshold_area_m2": threshold_area, "threshold_cells": cells,
                    "recommended_threshold_area_m2": recommended, "automatic": threshold_area_m2 is None,
                    "threshold_policy": "plugin heuristic max(100 native cells, 1% of TauDEM basin area)",
                    "outlet_path": str(closure), "bin_dir": str(bin_dir) if bin_dir is not None else None,
                    "inclusive_accumulation_path": inclusive_path,
                    "edge_contamination_check": True}
        diagnostic = dict(analysis.diagnostics)
        diagnostic.update(outlet_snap=snap, taudem_pipeline_executed=True, dem_hydrology=metadata)
        notes = tuple(note for note in analysis.notes if "No se ejecuta TauDEM" not in note)
        notes += ("Direcciones y red calculadas con TauDEM real: PitRemove, D8FlowDir, AreaD8, Threshold y StreamNet. "
                  "Las cotas, TRI y pendiente local siguen usando el MDT original, no el MDT rellenado.",)
        analysis = replace(analysis, selected_x=float(outlet_x), selected_y=float(outlet_y),
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
