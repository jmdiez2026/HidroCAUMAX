"""Generate native-resolution D8 from a local metric DEM using bundled GRASS.

GRASS r.watershed's least-cost drainage resolves flats and depressions without
replacing the source elevations. ``-s -m`` selects single-flow D8 and disk-backed
processing. Analysis always reads the ORIGINAL DEM, never a filled/resampled DEM.
No QGIS project, provider registry, environment or user profile is modified.

References: https://grass.osgeo.org/grass-stable/manuals/r.watershed.html
and https://grass.osgeo.org/grass-stable/manuals/grass.html
"""

from dataclasses import dataclass, replace
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
from osgeo import gdal

from . import basin as _b
from .grid import cell_at_point
from .terrain import Analysis, analyze_basin


_VERSION = "grass-watershed-sfd-native-v1"
_ACCUMULATION_VERSION = "inclusive-esri-d8-topological-v1"
_SNAP_METHOD = "nearest_main_channel_d8"
_SNAP_REFERENCE = "https://www.hec.usace.army.mil/confluence/hmsdocs/hmsguides/hms-modeling-applications/hec-hms-example-applications/generating-a-flow-forecast-for-international-locations"
MAX_RASTER_CELLS = 60_000_000
# Includes terrain arrays, path bookkeeping and a geometry allowance. Resources
# are checked against 55% of current available RAM, never against a coarse grid.
_BYTES_PER_WINDOW_CELL = 160
_FIXED_MEMORY_BYTES = 256 * 1024 ** 2
GRASS_MEMORY_MB = 512
_GRASS_TO_ESRI = np.array([0, 128, 64, 32, 16, 8, 4, 2, 1], dtype=np.uint8)


@dataclass(frozen=True)
class DemResult:
    analysis: Analysis
    directions_path: str
    dem_path: str
    metadata: dict

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "analysis"), name)


def availability_engine():
    """Return available/name/path/reason plus isolated-launch configuration.

    Probing is read-only and does not load a Processing provider or start GRASS.
    Windows uses the Python launcher directly, avoiding cmd.exe/batch quoting.
    """
    roots = []
    for variable in ("OSGEO4W_ROOT", "QGIS_PREFIX_PATH"):
        value = os.environ.get(variable)
        if value:
            location = Path(value)
            roots.extend((location, *location.parents))
    roots.extend((Path(sys.prefix), *Path(sys.prefix).parents))
    # QGIS normally supplies OSGEO4W_ROOT; support its usual standalone installs.
    if os.name == "nt":
        for drive in ("C:", "D:"):
            folder = Path(drive + "/Program Files")
            if folder.is_dir():
                roots.extend(sorted(folder.glob("QGIS *"), reverse=True))
    seen = set()
    for root in roots:
        if str(root) in seen:
            continue
        seen.add(str(root))
        bases = []
        if (root / "bin" / "r.watershed.exe").is_file():
            bases.append(root)
        grass_apps = root / "apps" / "grass"
        if grass_apps.is_dir():
            bases.extend(sorted(grass_apps.glob("grass*"), reverse=True))
        for base in bases:
            scripts = sorted((base / "etc").glob("grass*.py"))
            python = root / "bin" / "python3.exe"
            if not python.is_file():
                python = next((p / "bin" / "python3.exe" for p in base.parents
                               if (p / "bin" / "python3.exe").is_file()), python)
            if (scripts and python.is_file() and
                    (base / "bin" / "r.watershed.exe").is_file() and
                    (base / "bin" / "r.external.exe").is_file()):
                return {"available": True, "name": "GRASS r.watershed D8 (-s -m)",
                        "path": str(base), "reason": "", "python": str(python),
                        "launcher": str(scripts[0]), "osgeo_root": str(python.parent.parent)}
    grass = shutil.which("grass")
    if os.name != "nt" and grass:
        return {"available": True, "name": "GRASS r.watershed D8 (-s -m)",
                "path": grass, "launcher": grass, "python": sys.executable,
                "reason": ""}
    return {"available": False, "name": "GRASS r.watershed D8 (-s -m)",
            "path": "", "reason": "No se encuentra GRASS con r.watershed en esta instalación de QGIS."}


def _check(cancelled):
    if cancelled():
        raise InterruptedError("Análisis hidrológico del MDT cancelado.")


def _available_memory():
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_phys", ctypes.c_ulonglong), ("available_phys", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                        ("extended_virtual", ctypes.c_ulonglong)]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.available_phys)
        return None
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, KeyError):
        return None


def _memory_feasibility(raster_cells, window_cells, available_bytes=None):
    available = _available_memory() if available_bytes is None else int(available_bytes)
    estimate = (_FIXED_MEMORY_BYTES + 2 * raster_cells +
                _BYTES_PER_WINDOW_CELL * window_cells)
    budget = int(available * 0.55) if available is not None else None
    feasible = (raster_cells <= MAX_RASTER_CELLS and window_cells <= MAX_RASTER_CELLS and
                budget is not None and estimate <= budget)
    if raster_cells > MAX_RASTER_CELLS or window_cells > MAX_RASTER_CELLS:
        reason = f"Se supera el máximo de {MAX_RASTER_CELLS:,} celdas de esta versión."
    elif budget is None:
        reason = "No se puede determinar la memoria disponible para proteger el análisis."
    elif not feasible:
        reason = (f"El análisis estima {estimate / 1024 ** 3:.2f} GiB; su presupuesto "
                  f"es {budget / 1024 ** 3:.2f} GiB (55 % de la RAM disponible).")
    else:
        reason = "Memoria estimada dentro del presupuesto disponible."
    return {"feasible": feasible, "estimated_bytes": estimate, "available_bytes": available,
            "budget_bytes": budget, "raster_cells": raster_cells,
            "analysis_window_cells": window_cells, "reason": reason}


def check_feasibility(dem_path, available_bytes=None, window_cells=None):
    """Read-only RAM estimate; absent window_cells assumes the full DEM bbox.

    A worst-case failure does not prove that a smaller actual basin cannot fit.
    analyze_dem checks again using the exact upstream bounding window. Pass
    available_bytes only to apply a caller-selected budget or deterministic test.
    """
    dataset = _b._open_raster(dem_path, "MDT")
    try:
        transform, _ = _b._aligned(dataset, dataset)
        total = dataset.RasterXSize * dataset.RasterYSize
        window = total if window_cells is None else int(window_cells)
        if window <= 0 or window > total:
            raise ValueError("El número de celdas de la ventana debe estar entre 1 y el total del MDT.")
        result = _memory_feasibility(total, window, available_bytes)
        result.update({"worst_case": window_cells is None,
                       "native_resolution": (transform[1], -transform[5]),
                       "engine": availability_engine()})
        return result
    finally:
        dataset = None


def _source_key(dataset, dem_path):
    files = []
    for filename in sorted(set(dataset.GetFileList() or [str(dem_path)])):
        source = Path(filename)
        if not source.is_file():
            raise ValueError("El MDT debe depender de archivos locales disponibles.")
        stat = source.stat()
        files.append((str(source.resolve()), stat.st_size, stat.st_mtime_ns))
    description = {"version": _VERSION, "files": files,
                   "size": (dataset.RasterXSize, dataset.RasterYSize),
                   "transform": dataset.GetGeoTransform(), "crs": dataset.GetProjectionRef(),
                   "dtype": dataset.GetRasterBand(1).DataType}
    return hashlib.sha256(json.dumps(description, sort_keys=True).encode("utf-8")).hexdigest()


def _runtime_folder():
    """Short private job root, independent of host-virtualized TEMP paths."""
    if os.name == "nt":
        root = Path.home() / "AppData" / "Local" / "Temp" / "hc_gis"
    else:
        root = Path(tempfile.gettempdir()) / "hc_gis"
    root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Existing 8.3 aliases also support user names that contain non-ASCII
        # characters, when the volume provides them. No shell is involved.
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(root), buffer, len(buffer))
        if length and length < len(buffer):
            root = Path(buffer.value)
        if len(str(root)) > 120 or any(c in str(root) for c in "#!%"):
            raise ValueError("No se puede preparar una ruta temporal corta compatible con GRASS.")
    return root


def _cache_folder(requested):
    if os.name == "nt" and (len(str(requested)) > 170 or
                              any(c in str(requested) for c in "#!%")):
        selected = _runtime_folder() / "cache"
    else:
        selected = requested
    selected.mkdir(parents=True, exist_ok=True)
    return selected


def _copy_cancelled(source, target, cancelled):
    with source.open("rb") as reader, target.open("xb") as writer:
        while True:
            _check(cancelled)
            chunk = reader.read(8 * 1024 ** 2)
            if not chunk:
                break
            writer.write(chunk)


def _publish_cache(converted, destination, cancelled):
    try:
        os.replace(converted, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
    # A user-selected cache can be on another volume. Publish only a complete
    # file, with the final rename performed within the destination filesystem.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix="d8_", suffix=".part",
                                         dir=str(destination.parent), delete=False) as writer:
            temporary = Path(writer.name)
            with converted.open("rb") as reader:
                while True:
                    _check(cancelled)
                    chunk = reader.read(8 * 1024 ** 2)
                    if not chunk:
                        break
                    writer.write(chunk)
        _check(cancelled)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _stage_dem(source, dataset, work, cancelled):
    """Expose exactly the same pixels at an ASCII job filename for GRASS.

    GeoTIFFs and their conventional mask/PAM sidecars use hard links when
    possible, otherwise a cancellable byte copy. Other formats are losslessly
    translated to a self-contained GeoTIFF without a resampling operation.
    """
    _check(cancelled)
    target = work / "dem.tif"
    files = [Path(name) for name in (dataset.GetFileList() or [str(source)])]
    suffixes = (".aux.xml", ".msk", ".ovr", ".msk.aux.xml")
    conventional = all(path == source or
                       (str(path).startswith(str(source)) and
                        str(path)[len(str(source)):] in suffixes) for path in files)
    if dataset.GetDriver().ShortName == "GTiff" and conventional:
        for path in files:
            _check(cancelled)
            renamed = target if path == source else Path(str(target) + str(path)[len(str(source)):])
            try:
                os.link(path, renamed)
            except OSError:
                _copy_cancelled(path, renamed, cancelled)
    else:
        interrupted = [False]

        def callback(fraction, message, data):
            if cancelled():
                interrupted[0] = True
                return 0
            return 1

        translated = None
        try:
            translated = gdal.Translate(str(target), dataset, format="GTiff",
                                        creationOptions=["TILED=YES", "COMPRESS=LZW", "BIGTIFF=IF_SAFER"],
                                        callback=callback)
        except RuntimeError:
            if interrupted[0]:
                raise InterruptedError("Preparación del MDT cancelada.")
            raise
        if interrupted[0]:
            raise InterruptedError("Preparación del MDT cancelada.")
        if translated is None:
            raise ValueError("No se ha podido preparar el MDT para la sesión GRASS.")
        translated = None
    _check(cancelled)
    return target


def _windows_job(process):
    """Own the complete private GRASS tree, including Python-launcher children."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t),
                    ("maximum", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                    ("peak_job", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        return None
    if not kernel.AssignProcessToJobObject(job, int(process._handle)):
        kernel.CloseHandle(job)
        return None
    return kernel, job


def _stop_process(process, job=None):
    if job is not None:
        job[0].TerminateJobObject(job[1], 1)
    elif process.poll() is not None:
        return
    elif os.name == "nt":
        # Kill only the process tree created for this private GRASS session.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    elif job is None:
        import signal
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


_WORKER = '''import sys
import grass.script as gs
dem, output, memory = sys.argv[1:]
gs.run_command("r.external", input=dem, output="original_dem", quiet=True)
gs.run_command("g.region", raster="original_dem", quiet=True)
gs.run_command("r.watershed", flags="sm", elevation="original_dem",
               drainage="drainage", memory=int(memory))
gs.run_command("r.out.gdal", input="drainage", output=output,
               format="GTiff", type="Int16", nodata=-32768,
               createopt="COMPRESS=LZW,TILED=YES", quiet=True)
'''


def _run_grass(engine, dem_path, destination, work, cancelled):
    script = work / "compute_d8.py"
    script.write_text(_WORKER, encoding="utf-8")
    (work / "config").mkdir(exist_ok=True)
    env = os.environ.copy()
    # The Windows launcher also consults the actual home for migration checks.
    # Config/output are still redirected to the private session directory.
    env.setdefault("HOME", str(Path.home()))
    env.update({"GRASS_CONFIG_DIR": str(work / "config"), "GRASS_GUI": "text",
                "GRASS_MESSAGE_FORMAT": "plain", "GRASS_COMPRESS_NULLS": "1",
                "TMPDIR": str(work), "TMP": str(work), "TEMP": str(work)})
    for stale in ("GISRC", "GIS_LOCK", "GRASS_REGION", "WIND_OVERRIDE", "GRASS_BATCH_JOB"):
        env.pop(stale, None)
    if os.name == "nt":
        base, root = Path(engine["path"]), Path(engine["osgeo_root"])
        env.update({"GISBASE": str(base), "GRASS_PYTHON": engine["python"],
                    "GRASS_COMPATIBILITY_TEST": "0", "OSGEO4W_ROOT": str(root),
                    "GRASS_PROJSHARE": str(root / "share" / "proj"),
                    "FONTCONFIG_FILE": str(base / "etc" / "fonts.conf")})
        env["PYTHONPATH"] = str(base / "etc" / "python") + os.pathsep + env.get("PYTHONPATH", "")
        env["PATH"] = os.pathsep.join((str(base / "bin"), str(base / "lib"),
                                       str(root / "bin"), env.get("PATH", "")))
        command = [engine["python"], engine["launcher"]]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        command = [engine["launcher"]]
        kwargs = {"start_new_session": True}
    # --exec is already headless. Explicit --text triggers a first-session
    # GRASS8.5 bug when its deliberately empty private config has no prior rc.
    command.extend(["--tmp-project", str(dem_path), "--exec",
                    engine["python"], str(script), str(dem_path), str(destination),
                    str(GRASS_MEMORY_MB)])
    log = work / "grass.log"
    _check(cancelled)
    with log.open("wb") as stream:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   env=env, cwd=str(work), **kwargs)
        job = _windows_job(process)
        try:
            while True:
                _check(cancelled)
                try:
                    status = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _stop_process(process, job)
            raise
        finally:
            if job is not None:
                job[0].CloseHandle(job[1])
    _check(cancelled)
    if status != 0 or not destination.is_file():
        detail = log.read_bytes()[-6000:].decode("utf-8", errors="replace")
        raise ValueError("GRASS no ha podido calcular las direcciones D8 del MDT.\n" + detail)


def _convert_directions(raw_path, destination, dem, key, cancelled):
    raw = result = None
    try:
        raw = _b._open_raster(raw_path, "direcciones GRASS")
        transform, _ = _b._aligned(raw, dem)
        result = gdal.GetDriverByName("GTiff").Create(
            str(destination), raw.RasterXSize, raw.RasterYSize, 1, gdal.GDT_Byte,
            options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
        )
        if result is None:
            raise ValueError("No se puede crear la caché de direcciones D8.")
        result.SetGeoTransform(dem.GetGeoTransform())
        result.SetProjection(dem.GetProjectionRef())
        result.SetMetadata({"HYDRO_VERSION": _VERSION, "SOURCE_KEY": key,
                            "D8_CONVENTION": "ESRI E1 SE2 S4 SW8 W16 NW32 N64 NE128",
                            "DEM_CONDITIONING": "GRASS r.watershed least-cost SFD; original DEM unchanged"})
        band = raw.GetRasterBand(1)
        target = result.GetRasterBand(1)
        target.SetNoDataValue(255)
        nodata = band.GetNoDataValue()
        for top in range(0, raw.RasterYSize, 128):
            _check(cancelled)
            data = _b._read(band, 0, top, raw.RasterXSize, min(128, raw.RasterYSize - top))
            valid = np.isfinite(data)
            if nodata is not None:
                valid &= data != nodata
            if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
                valid &= _b._read(band.GetMaskBand(), 0, top, data.shape[1], data.shape[0]) != 0
            if np.any(valid & ((data < -8) | (data > 8) | (data != np.trunc(data)))):
                raise ValueError("GRASS ha producido códigos de dirección no reconocidos.")
            converted = np.full(data.shape, 255, dtype=np.uint8)
            positive = valid & (data > 0)
            converted[positive] = _GRASS_TO_ESRI[data[positive].astype(np.intp)]
            # Negative GRASS directions leave the domain; zero is a terminal sink.
            converted[valid & (data <= 0)] = 0
            target.WriteArray(converted, 0, top)
        result.FlushCache()
        _check(cancelled)
    finally:
        raw = result = None


def _valid_cache(path, dem, key):
    if not path.is_file():
        return False
    cached = None
    try:
        cached = _b._open_raster(path, "direcciones en caché")
        _b._aligned(cached, dem)
        return (cached.GetMetadataItem("SOURCE_KEY") == key and
                cached.GetMetadataItem("HYDRO_VERSION") == _VERSION and
                cached.GetRasterBand(1).DataType == gdal.GDT_Byte and
                cached.GetRasterBand(1).GetNoDataValue() == 255)
    except (ValueError, RuntimeError):
        return False
    finally:
        cached = None


def _flow_accumulation(band, cancelled=lambda: False, progress=lambda fraction: None):
    """Exact inclusive upstream counts on an acyclic ESRI D8 forest.

    Zero is a valid terminal; NoData contributes zero. Outward directions end
    at the data boundary. A topological queue detects even disconnected cycles.
    UInt32 is exact under MAX_RASTER_CELLS; flat chunks bound index temporaries
    for unusually wide rasters as well as ordinary rectangular DEM grids.
    """
    _check(cancelled)
    progress(0.0)
    raw = _b._read(band)
    rows, cols = raw.shape
    total = rows * cols
    if total > MAX_RASTER_CELLS:
        raise ValueError("La malla de acumulación supera el límite de celdas de esta versión.")
    valid = np.isfinite(raw)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= raw != nodata
    if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        valid &= _b._read(band.GetMaskBand()) != 0
    data = raw if raw.dtype == np.uint8 else np.zeros(raw.shape, dtype=np.uint8)
    raw_flat, data_flat, valid_flat = raw.ravel(), data.ravel(), valid.ravel()
    chunk = 262144
    for start in range(0, total, chunk):
        _check(cancelled)
        stop = min(total, start + chunk)
        block, flags = raw_flat[start:stop], valid_flat[start:stop]
        if np.any(flags & ~np.isin(block, tuple(_b._VALID_CODES))):
            raise ValueError("Las direcciones contienen un código D8 desconocido.")
        if data is not raw:
            data_flat[start:stop][flags] = block[flags].astype(np.uint8)
    del raw, raw_flat
    downstream = np.full(total, -1, dtype=np.int32)
    incoming = np.zeros(total, dtype=np.uint8)
    counts = valid_flat.astype(np.uint32)
    queue = np.empty(total, dtype=np.int32)
    for start in range(0, total, chunk):
        _check(cancelled)
        stop = min(total, start + chunk)
        block, flags = data_flat[start:stop], valid_flat[start:stop]
        # DIRECTIONS gives upstream offsets: reverse them for downstream edges.
        for upstream_dc, upstream_dr, code in _b.DIRECTIONS:
            source = np.flatnonzero((block == code) & flags) + start
            rr, cc = np.divmod(source, cols)
            target_r, target_c = rr - upstream_dr, cc - upstream_dc
            inside = ((target_r >= 0) & (target_r < rows) &
                      (target_c >= 0) & (target_c < cols))
            source = source[inside]
            target_r, target_c = target_r[inside], target_c[inside]
            good = valid[target_r, target_c]
            source = source[good]
            target = target_r[good] * cols + target_c[good]
            downstream[source] = target
            np.add.at(incoming, target, 1)
        progress(0.15 * stop / max(total, 1))
    head = tail = 0
    for start in range(0, total, chunk):
        _check(cancelled)
        stop = min(total, start + chunk)
        sources = np.flatnonzero(valid_flat[start:stop] & (incoming[start:stop] == 0))
        queue[tail:tail + len(sources)] = sources + start
        tail += len(sources)
    valid_count = int(np.count_nonzero(valid))
    while head < tail:
        source = int(queue[head])
        head += 1
        target = int(downstream[source])
        if target >= 0:
            counts[target] += counts[source]
            incoming[target] -= 1
            if incoming[target] == 0:
                queue[tail] = target
                tail += 1
        if head % 8192 == 0:
            _check(cancelled)
            progress(0.15 + 0.85 * head / max(valid_count, 1))
    _check(cancelled)
    if head != valid_count:
        raise ValueError("La red D8 contiene un ciclo; no se puede calcular su acumulación ni ajustar el cierre.")
    progress(1.0)
    return counts.reshape(rows, cols)


def _require_accumulation_memory(total, bytes_per_cell):
    available = _available_memory()
    estimate = _FIXED_MEMORY_BYTES + bytes_per_cell * total
    if available is None or estimate > int(available * 0.55):
        raise ValueError("No hay memoria suficiente para la acumulación D8 a resolución original "
                         f"(estimación {estimate / 1024 ** 3:.2f} GiB; presupuesto55 % de la RAM "
                         "disponible). No se remuestrea el MDT.")


def _accumulation(directions_path, cancelled, progress):
    """Read or atomically publish reusable UInt32 inclusive accumulation."""
    dataset = cached = target_ds = None
    temporary = None
    try:
        dataset = _b._open_raster(directions_path, "direcciones para acumulación")
        total = dataset.RasterXSize * dataset.RasterYSize
        if total > MAX_RASTER_CELLS:
            raise ValueError("La malla de acumulación supera el límite de celdas de esta versión.")
        source_stat = Path(directions_path).stat()
        description = (_ACCUMULATION_VERSION, str(Path(directions_path).resolve()),
                       source_stat.st_size, source_stat.st_mtime_ns,
                       dataset.GetMetadataItem("SOURCE_KEY"))
        key = hashlib.sha256(repr(description).encode("utf-8")).hexdigest()
        destination = Path(directions_path).parent / ("acc_" + key[:32] + ".tif")
        _require_accumulation_memory(total, 6)  # Includes a cached count read.
        if destination.is_file():
            try:
                cached = _b._open_raster(destination, "acumulación en caché")
                _b._aligned(cached, dataset)
                if (cached.GetMetadataItem("ACCUMULATION_KEY") == key and
                        cached.GetRasterBand(1).DataType == gdal.GDT_UInt32 and
                        cached.GetRasterBand(1).GetNoDataValue() == 0):
                    _check(cancelled)
                    counts = _b._read(cached.GetRasterBand(1))
                    _check(cancelled)
                    if int(np.max(counts, initial=0)) > counts.size:
                        raise ValueError("La acumulación en caché contiene valores imposibles.")
                    progress(1.0)
                    return counts, str(destination), True
            except (RuntimeError, ValueError):
                pass
            finally:
                cached = None
        _require_accumulation_memory(total, 22)
        counts = _flow_accumulation(dataset.GetRasterBand(1), cancelled, progress)
        with tempfile.NamedTemporaryFile(prefix="ac_", suffix=".tif", delete=False,
                                         dir=str(destination.parent)) as stream:
            temporary = Path(stream.name)
        target_ds = gdal.GetDriverByName("GTiff").Create(
            str(temporary), dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_UInt32,
            options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"])
        if target_ds is None:
            raise ValueError("No se puede crear la caché de acumulación D8.")
        target_ds.SetGeoTransform(dataset.GetGeoTransform())
        target_ds.SetProjection(dataset.GetProjectionRef())
        target_ds.SetMetadata({"ACCUMULATION_KEY": key, "ACCUMULATION_VERSION": _ACCUMULATION_VERSION,
                               "COUNT_CONVENTION": "inclusive upstream cells; NoData=0"})
        target_ds.GetRasterBand(1).SetNoDataValue(0)
        for top in range(0, dataset.RasterYSize, 128):
            _check(cancelled)
            target_ds.GetRasterBand(1).WriteArray(counts[top:top + 128], 0, top)
        target_ds.FlushCache()
        target_ds = None
        _check(cancelled)
        os.replace(temporary, destination)
        return counts, str(destination), False
    except MemoryError as error:
        raise ValueError("No se pudo reservar memoria para la acumulación D8; no se ajustó el cierre.") from error
    finally:
        dataset = cached = target_ds = None
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _positive_finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} debe ser un número positivo finito.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} debe ser un número positivo finito.") from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} debe ser un número positivo finito.")
    return number


def _snap_parameters(reference_area_m2, reference_cell_width, reference_cell_height,
                     snap_radius_m, snap_minimum_fraction):
    area = _positive_finite(reference_area_m2, "El área original de referencia")
    width = _positive_finite(reference_cell_width, "La anchura de la celda original")
    height = _positive_finite(reference_cell_height, "La altura de la celda original")
    fraction = _positive_finite(snap_minimum_fraction, "La fracción mínima de aporte")
    if fraction > 1:
        raise ValueError("La fracción mínima de aporte no puede superar1.")
    radius = (max(500.0, math.hypot(width, height)) if snap_radius_m is None else
              _positive_finite(snap_radius_m, "El radio de búsqueda"))
    return area, fraction, radius


def _nearest_main_channel(counts, transform, original_x, original_y,
                          reference_area_m2, minimum_fraction, radius,
                          cancelled=lambda: False, *, directions_path=None):
    """Nearest connected D8 ancestor on or upstream of the exact section.

    The coarse area is a recorded comparison, never a qualification that may
    push the outlet downstream. Native directions are mandatory: proximity or
    accumulation alone cannot establish the permitted upstream relationship.
    """
    _check(cancelled)
    if directions_path is None:
        raise ValueError("Se requieren las direcciones D8 para garantizar un cierre en la misma sección o aguas arriba.")
    dataset = _b._open_raster(directions_path, "direcciones para ajustar la sección")
    try:
        actual_transform = _b._geotransform(dataset, "direcciones para ajustar la sección")
        _b._spatial_reference(dataset, "direcciones para ajustar la sección")
        if ((dataset.RasterYSize, dataset.RasterXSize) != counts.shape
                or not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
                           for a, b in zip(actual_transform, transform))):
            raise ValueError("Las direcciones y la acumulación no comparten la misma malla.")
        effective, diagnostic = _b._upstream_section_cell(
            dataset.GetRasterBand(1), transform, original_x, original_y, radius,
            counts=counts, require_channel=True, cancelled=cancelled)
    finally:
        dataset = None
    candidate = diagnostic['candidate_upstream_area_m2']
    original = diagnostic['original_cell_upstream_area_m2']
    diagnostic.update(
        reference_area_m2=float(reference_area_m2), minimum_fraction=float(minimum_fraction),
        reference_minimum_upstream_area_m2=float(reference_area_m2 * minimum_fraction),
        reference_threshold_met=bool(candidate >= reference_area_m2 * minimum_fraction),
        area_change_vs_reference_pct=float(100 * (candidate / reference_area_m2 - 1)),
        area_change_vs_original_cell_m2=float(candidate - original),
        reference=_SNAP_REFERENCE)
    _check(cancelled)
    return effective, diagnostic


def _preflight_analysis(directions_path, selected, cancelled):
    """Bound terrain.py's RAM demand without moving or coarsening the outlet."""
    dataset = _b._open_raster(directions_path, "direcciones")
    try:
        mask, window, count, boundary = _b._visit_upstream(
            dataset.GetRasterBand(1), selected.row, selected.col, cancelled, lambda n: None)
        del mask
        window_cells = window[2] * window[3]
        feasibility = _memory_feasibility(dataset.RasterXSize * dataset.RasterYSize, window_cells)
        if not feasibility["feasible"]:
            raise ValueError(
                f"La cuenca ocupa una ventana de {window_cells:,} celdas reales "
                f"({selected.width:g} × {selected.height:g} m). " + feasibility["reason"] +
                " Las direcciones ya están en caché. Libere memoria o elija un MDT "
                "descargado expresamente a otra resolución; no se remuestrea automáticamente.")
        return count, window_cells, boundary, feasibility
    finally:
        dataset = None


def analyze_dem(dem_path, outlet_x, outlet_y, cache_dir,
                cancelled=lambda: False, progress=lambda n: None, *,
                reference_area_m2=None, reference_cell_width=None, reference_cell_height=None,
                snap_enabled=False, snap_radius_m=None, snap_minimum_fraction=0.9):
    """Return DemResult from a real, native-resolution local DEM.

    DEM: north-up, unrotated, metric projected grid with metre elevations.
    Without snap_enabled the containing input cell is used. With snapping,
    only connected D8 ancestors on or upstream of the original section qualify.
    The nearest qualified centre is selected inside max(500m, reference-cell
    diagonal); the coarse basin area remains an informative comparison.
    The local channel filter and radius are explicit plugin policy.
    No candidate raises ValueError; Analysis.selected_x/y retain input coordinates
    and basin.outlet is effective. Source heights are never modified.
    ``progress`` receives percentages 0..100. Cancellation raises InterruptedError;
    invalid input, unavailable engine and resource limits raise explicit ValueError.
    Source rasters are read-only. Cached D8 is versioned by all source file stats.
    New cache files are published only after successful generation/conversion.
    """
    _check(cancelled)
    progress(0)
    if not isinstance(snap_enabled, bool):
        raise ValueError("La activación del ajuste debe ser booleana.")
    snap_parameters = (_snap_parameters(reference_area_m2, reference_cell_width,
                                        reference_cell_height, snap_radius_m, snap_minimum_fraction)
                       if snap_enabled else None)
    source = Path(dem_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("El MDT debe ser un archivo local disponible.")
    dataset = _b._open_raster(source, "MDT")
    try:
        transform, reference = _b._aligned(dataset, dataset)
        cols, rows = dataset.RasterXSize, dataset.RasterYSize
        if cols * rows > MAX_RASTER_CELLS:
            raise ValueError(f"El MDT contiene {cols * rows:,} celdas; el límite de lectura "
                             f"de esta versión es {MAX_RASTER_CELLS:,}. No se reduce su resolución.")
        selected = cell_at_point(outlet_x, outlet_y, transform[0],
                                 transform[3] + rows * transform[5],
                                 transform[0] + cols * transform[1], transform[3], cols, rows)
        band = dataset.GetRasterBand(1)
        value = float(_b._read(band, selected.col, selected.row, 1, 1)[0, 0])
        valid = math.isfinite(value)
        if band.GetNoDataValue() is not None:
            valid &= value != band.GetNoDataValue()
        if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
            valid &= bool(_b._read(band.GetMaskBand(), selected.col, selected.row, 1, 1)[0, 0])
        if not valid:
            raise ValueError("La celda seleccionada no tiene cota MDT válida (NoData); no se desplaza el cierre.")
        key = _source_key(dataset, source)
        requested_cache = Path(cache_dir).expanduser().resolve()
        cache = _cache_folder(requested_cache)
        directions = cache / ("d8_" + key[:32] + ".tif")
        reused = _valid_cache(directions, dataset, key)
        engine = availability_engine()
        progress(5)
        if not reused:
            if not engine["available"]:
                raise ValueError(engine["reason"])
            # Windows may release a killed child's inherited log handle shortly
            # after the parent exits. Cleanup retries must not replace cancellation.
            temporary_dir = tempfile.TemporaryDirectory(prefix="g_", dir=str(_runtime_folder()),
                                                         ignore_cleanup_errors=True)
            try:
                temporary = temporary_dir.name
                work = Path(temporary)
                raw, converted = work / "grass_drainage.tif", work / "esri_d8.tif"
                staged_source = _stage_dem(source, dataset, work, cancelled)
                progress(10)
                _run_grass(engine, staged_source, raw, work, cancelled)
                progress(55)
                _convert_directions(raw, converted, dataset, key, cancelled)
                _check(cancelled)
                _publish_cache(converted, directions, cancelled)
            finally:
                for attempt in range(20):
                    try:
                        shutil.rmtree(temporary_dir.name)
                        break
                    except FileNotFoundError:
                        break
                    except OSError:
                        if attempt < 19:
                            time.sleep(0.1)
                temporary_dir.cleanup()
        progress(65)
        snap = None
        accumulation_path = None
        accumulation_reused = False
        effective = selected
        if snap_enabled:
            counts, accumulation_path, accumulation_reused = _accumulation(
                directions, cancelled, lambda fraction: progress(65 + min(8, int(fraction * 8))))
            effective, snap = _nearest_main_channel(
                counts, transform, outlet_x, outlet_y, *snap_parameters, cancelled=cancelled,
                directions_path=directions)
            del counts
            progress(74)
        count, window_cells, boundary, feasibility = _preflight_analysis(directions, effective, cancelled)
        if snap is not None:
            expected_area = count * abs(transform[1] * transform[5])
            if snap["candidate_upstream_area_m2"] != expected_area:
                raise ValueError("La acumulación D8 no coincide con la cuenca del cierre ajustado; "
                                 "no se publica un resultado incoherente.")
            snap["candidate_touches_boundary"] = bool(boundary)
        metadata = {"engine": "GRASS r.watershed", "engine_flags": "-s -m",
                    "engine_path": engine.get("path", ""), "cache_reused": reused,
                    "conditioning": "least-cost drainage of depressions and flats",
                    "elevation_source": "original DEM; not filled or resampled",
                    "native_resolution": (selected.width, selected.height),
                    "raster_cells": cols * rows, "analysis_window_cells": window_cells,
                    "basin_cells": count, "raster_boundary_touched": boundary,
                    "source_key": key, "outlet_snapped_to_channel": bool(snap_enabled),
                    "memory_feasibility": feasibility,
                    "cache_directory": str(cache), "requested_cache_directory": str(requested_cache),
                    "accumulation_path": accumulation_path,
                    "accumulation_cache_reused": accumulation_reused,
                    "reference": "https://grass.osgeo.org/grass-stable/manuals/r.watershed.html"}
    finally:
        dataset = None
    _check(cancelled)
    progress(75)
    calculation_x, calculation_y = ((effective.x, effective.y) if snap_enabled else (outlet_x, outlet_y))
    analysis = analyze_basin(str(directions), str(source), calculation_x, calculation_y,
                             cancelled=cancelled,
                             progress=lambda n: progress(75 + min(24, int(24 * n / max(count, 1)))))
    diagnostic = dict(analysis.diagnostics)
    diagnostic["dem_hydrology"] = metadata
    if snap is not None:
        diagnostic["outlet_snap"] = snap
    notes = (*analysis.notes,
             "Las direcciones D8 se recalculan con GRASS r.watershed (-s -m) sobre el MDT "
             "a su resolución original, resolviendo depresiones y planicies por búsqueda de coste. "
             "Las cotas, el TRI y la pendiente local se calculan sobre el MDT original, sin rellenarlo ni remuestrearlo.")
    if snap is not None:
        notes += (f"Cierre en la misma sección o aguas arriba: "
                  f"({snap['snapped_x']:.3f}, {snap['snapped_y']:.3f}), "
                  f"a {snap['distance_m']:.2f} m del punto original. "
                  "Solo se admiten antecesores D8 conectados en el semiplano aguas arriba "
                  "de la sección local, perpendicular a la D8 del punto original. "
                  f"Radio {snap['search_radius_m']:.2f} m; se elige el centro válido más próximo. "
                  f"Área fina {snap['candidate_upstream_area_m2'] / 1e6:g} km²; "
                  f"referencia original {snap['reference_area_m2'] / 1e6:g} km². "
                  "La referencia no obliga a aumentar el área ni permite desplazar el cierre aguas abajo. "
                  "El filtro de cauce (9 celdas y 4 veces la mediana local) es un criterio del plugin; "
                  "la sección raster no sustituye un levantamiento topográfico.",)
    analysis = replace(analysis, diagnostics=diagnostic, notes=notes,
                       selected_x=float(outlet_x), selected_y=float(outlet_y))
    _check(cancelled)
    progress(100)
    return DemResult(analysis, str(directions), str(source), metadata)
