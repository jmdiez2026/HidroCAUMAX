"""Offline, verified TauDEM runtime private to the Windows user's profile.

Only the child processes receive the private DLL/data environment. This module
uses the standard library and never installs software or modifies global PATH.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import sys
import tempfile
import zipfile

BUNDLE_NAME = 'taudem-5.5.0-win64.zip'
TOOLS = {'pitremove': 'pitremove.exe', 'd8flowdir': 'd8flowdir.exe',
         'aread8': 'aread8.exe', 'threshold': 'threshold.exe', 'streamnet': 'streamnet.exe'}
_RECEIPT = '.complete.json'
_MAX_BYTES = 2 * 1024**3
_RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
_VC_DLLS = ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll')


def _check(cancelled):
    if cancelled():
        raise InterruptedError('Preparación de Esri-D8 cancelada.')


def _windows_x64():
    return os.name == 'nt' and platform.machine().lower() in ('amd64', 'x86_64')


def _digest(path, cancelled=lambda: False):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while True:
            _check(cancelled)
            block = stream.read(1024*1024)
            if not block:
                break
            result.update(block)
    return result.hexdigest()


def _relative(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('Ruta no válida en el motor Esri-D8.')
    raw = name.rstrip('/')
    parts = raw.split('/')
    if (PurePosixPath(raw).is_absolute() or any(part in ('', '.', '..') for part in parts)
            or any(any(c in part for c in '<>:"|?*') or part.endswith(('.', ' '))
                   or part.split('.')[0].lower() in _RESERVED for part in parts)):
        raise ValueError('Ruta insegura en el motor Esri-D8: ' + name)
    return raw


def _child(base, relative):
    target = base.joinpath(*_relative(relative).split('/'))
    current = target
    while current != base:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('El motor Esri-D8 no admite enlaces de archivos o carpetas.')
        current = current.parent
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('Una ruta del motor Esri-D8 sale de su carpeta privada.')
    return target


def _inventory(archive):
    members, seen, total = {}, set(), 0
    infos = archive.infolist()
    if len(infos) > 4096:
        raise ValueError('El motor Esri-D8 contiene demasiados archivos.')
    for item in infos:
        name = _relative(item.filename)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode))):
            raise ValueError('El motor Esri-D8 contiene un enlace o archivo especial.')
        if name.casefold() in seen:
            raise ValueError('El motor Esri-D8 contiene rutas duplicadas.')
        seen.add(name.casefold())
        if item.is_dir():
            continue
        total += item.file_size
        if total > _MAX_BYTES or item.file_size > 512*1024**2 or item.flag_bits & 1:
            raise ValueError('Tamaño o cifrado no admitido en el runtime TauDEM.')
        members[name] = item
    if 'manifest.json' not in members or members['manifest.json'].file_size > 4*1024**2:
        raise ValueError('Falta el manifiesto del motor Esri-D8.')
    manifest_bytes = archive.read('manifest.json')
    manifest = json.loads(manifest_bytes.decode('utf-8-sig'))
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('platform') != 'windows'
            or manifest.get('architecture') != 'x86_64' or manifest.get('taudem_version') != '5.5.0'):
        raise ValueError('El manifiesto del motor Esri-D8 no corresponde al paquete Windows x64.')
    files = manifest.get('files')
    if (not isinstance(files, dict) or set(files) != set(members)-{'manifest.json'}
            or any(name.casefold() == _RECEIPT for name in files)):
        raise ValueError('El inventario Esri-D8 no coincide con el paquete.')
    for name, checksum in files.items():
        _relative(name)
        if not isinstance(checksum, str) or re.fullmatch('[0-9a-fA-F]{64}', checksum) is None:
            raise ValueError('Hash no válido en el manifiesto Esri-D8.')
    lookup = {name.casefold(): name for name in files}
    if any('bin/'+name in lookup for name in _VC_DLLS):
        raise ValueError('Las DLL de Microsoft Visual C++ deben proceder del QGIS local, no del paquete Esri-D8.')
    required = ['bin/'+name for name in TOOLS.values()]
    required += ['bin/mpiexec.exe', 'bin/smpd.exe', 'bin/msmpi.dll', 'share/proj/proj.db']
    missing = [name for name in required if name not in lookup]
    if missing or not any(name.startswith('licenses/') and members[lookup[name]].file_size > 0 for name in lookup):
        raise ValueError('Motor Esri-D8 incompleto: ' + ', '.join(missing or ['licenses/']))
    return manifest, manifest_bytes, members, lookup


def _default_cache():
    local = Path(os.environ.get('LOCALAPPDATA') or (Path.home()/'AppData/Local'))
    return local/'HidroCAUMAX/runtime'


def _vc_runtime_directory(inherited=None, executable=None):
    """Find the C++ runtime already used by Windows/QGIS, never a PATH search."""
    env = os.environ if inherited is None else inherited
    windows = Path(env.get('SystemRoot') or env.get('SYSTEMROOT') or env.get('WINDIR') or r'C:\Windows')
    candidates = []
    if env.get('OSGEO4W_ROOT'):
        candidates.append(Path(env['OSGEO4W_ROOT'])/'bin')
    if env.get('QGIS_PREFIX_PATH'):
        prefix = Path(env['QGIS_PREFIX_PATH'])
        candidates.extend((prefix/'bin', prefix.parent.parent/'bin'))
    interpreter = Path(sys.executable if executable is None else executable)
    candidates.extend((interpreter.parent, interpreter.parent/'bin',
                       interpreter.parent.parent/'bin', interpreter.parent.parent.parent/'bin'))
    candidates.append(windows/'System32')
    for folder in candidates:
        if all((folder/name).is_file() for name in _VC_DLLS):
            return folder.resolve()
    return None


def discover_bundled_runtime(bundle_path=None, cache_base=None, cancelled=lambda: False):
    """Inspect the shipped archive without extracting it on the GUI thread."""
    if not _windows_x64():
        return None
    bundle = Path(bundle_path) if bundle_path is not None else Path(__file__).parent/'vendor'/BUNDLE_NAME
    if not bundle.is_file():
        return None
    _check(cancelled)
    checksum = _digest(bundle, cancelled)
    try:
        with zipfile.ZipFile(bundle) as archive:
            manifest, _, _, lookup = _inventory(archive)
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('El paquete para Esri-D8 integrado está dañado: ' + str(error)) from error
    base = Path(cache_base) if cache_base is not None else _default_cache()
    vc_folder = _vc_runtime_directory()
    if vc_folder is None:
        raise ValueError('No se encuentran las DLL de Microsoft Visual C++ de Windows/QGIS: '
                         + ', '.join(_VC_DLLS) + '. Revise la instalación de QGIS.')
    vc_hashes = {name: _digest(vc_folder/name, cancelled) for name in _VC_DLLS}
    identity = hashlib.sha256((checksum + json.dumps(vc_hashes, sort_keys=True)).encode()).hexdigest()
    root = base.resolve()/identity[:24]
    descriptor = {'bundle_path': str(bundle.resolve()), 'bundle_sha256': checksum,
                  'root': str(root), 'manifest': manifest, 'vc_runtime_dir': str(vc_folder),
                  'vc_sha256': vc_hashes}
    return {'available': True, 'reason': '', 'engine': 'TauDEM', 'private_runtime': descriptor,
            'runtime_prepared': False, 'bin_dir': str(root/'bin'),
            'executables': {tool: str(root/lookup['bin/'+filename]) for tool, filename in TOOLS.items()},
            'mpiexec': str(root/lookup['bin/mpiexec.exe']),
            'runtime': {'kind': 'bundled', 'version': manifest['taudem_version'],
                        'id': manifest.get('runtime_id', 'taudem-5.5.0-win64'), 'sha256': checksum,
                        'vc_sha256': vc_hashes}}


def _validate_cache(root, manifest, manifest_bytes, checksum, vc_hashes, cancelled):
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        raise ValueError('La carpeta Esri-D8 privada no puede ser un enlace.')
    verified_files = dict(manifest['files'], **{'bin/'+name: value for name, value in vc_hashes.items()})
    expected = set(verified_files) | {'manifest.json', _RECEIPT}
    actual = set()
    for path in root.rglob('*'):
        _check(cancelled)
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('La caché Esri-D8 contiene un enlace de archivos o carpetas.')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != expected:
        raise ValueError('La caché Esri-D8 está incompleta o contiene archivos ajenos.')
    receipt = json.loads(_child(root, _RECEIPT).read_text(encoding='utf-8'))
    if (not isinstance(receipt, dict) or receipt.get('bundle_sha256') != checksum
            or receipt.get('vc_sha256') != vc_hashes
            or _child(root, 'manifest.json').read_bytes() != manifest_bytes):
        raise ValueError('La caché Esri-D8 no corresponde al paquete integrado.')
    for name, expected_hash in verified_files.items():
        if _digest(_child(root, name), cancelled).lower() != expected_hash.lower():
            raise ValueError('Archivo de caché TauDEM dañado: ' + name)


def ensure_runtime(engine, cancelled=lambda: False):
    """Extract atomically in the worker, or verify/reuse a complete private cache."""
    descriptor = engine.get('private_runtime')
    if not descriptor or engine.get('runtime_prepared'):
        return engine
    _check(cancelled)
    bundle, root = Path(descriptor['bundle_path']), Path(descriptor['root'])
    checksum = descriptor['bundle_sha256']
    vc_hashes = descriptor['vc_sha256']
    vc_folder = Path(descriptor['vc_runtime_dir'])
    if _digest(bundle, cancelled) != checksum:
        raise ValueError('El paquete para Esri-D8 cambió después de comprobarlo.')
    for name, expected_hash in vc_hashes.items():
        if _digest(vc_folder/name, cancelled) != expected_hash:
            raise ValueError('Las DLL de Visual C++ cambiaron después de comprobarlas; repita el cálculo.')
    with zipfile.ZipFile(bundle) as archive:
        manifest, manifest_bytes, members, _ = _inventory(archive)
        if root.exists():
            _validate_cache(root, manifest, manifest_bytes, checksum, vc_hashes, cancelled)
        else:
            base = root.parent
            base.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=root.name+'.', dir=str(base)))
            try:
                for name, item in members.items():
                    _check(cancelled)
                    destination = _child(stage, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(item) as source, destination.open('xb') as target:
                        while True:
                            _check(cancelled)
                            block = source.read(1024*1024)
                            if not block:
                                break
                            target.write(block)
                            digest.update(block)
                    if name != 'manifest.json' and digest.hexdigest().lower() != manifest['files'][name].lower():
                        raise ValueError('Hash incorrecto en el paquete Esri-D8: ' + name)
                for name, expected_hash in vc_hashes.items():
                    destination = _child(stage, 'bin/'+name)
                    digest = hashlib.sha256()
                    with (vc_folder/name).open('rb') as source, destination.open('xb') as target:
                        while True:
                            _check(cancelled)
                            block = source.read(1024*1024)
                            if not block:
                                break
                            target.write(block)
                            digest.update(block)
                    if digest.hexdigest() != expected_hash:
                        raise ValueError('Las DLL de Visual C++ cambiaron durante su copia privada.')
                _check(cancelled)
                (stage/_RECEIPT).write_text(json.dumps({'bundle_sha256': checksum, 'vc_sha256': vc_hashes}), encoding='utf-8')
                try:
                    stage.rename(root)
                except OSError:
                    if not root.is_dir():
                        raise
                    _validate_cache(root, manifest, manifest_bytes, checksum, vc_hashes, cancelled)
            finally:
                if stage.exists():
                    # stage is our mkdtemp child; never remove an existing runtime.
                    if stage.resolve().parent != base.resolve() or stage.is_symlink():
                        raise ValueError('Ruta temporal Esri-D8 inesperada; no se elimina.')
                    shutil.rmtree(stage)
    result = dict(engine)
    result['runtime_prepared'] = True
    return result


def subprocess_environment(engine, work, inherited=None):
    """Return a child-only environment, with private native DLL and data paths."""
    env = dict(os.environ if inherited is None else inherited)
    env.update(TMPDIR=str(work), TMP=str(work), TEMP=str(work), UCX_VFS_ENABLE='n')
    descriptor = engine.get('private_runtime')
    if not descriptor:
        return env
    root = Path(descriptor['root'])
    for key in tuple(env):
        upper = key.upper()
        if (upper.startswith(('GDAL_', 'PROJ_', 'CPL_', 'OSR_', 'OGR_', 'QGIS_', 'QT_', 'PYTHON', 'MSMPI_', 'PMI_'))
                or upper in ('OSGEO4W_ROOT', 'GEOTIFF_CSV', 'PATH')):
            env.pop(key, None)
    windows = Path(env.get('SystemRoot') or env.get('SYSTEMROOT') or env.get('WINDIR') or r'C:\Windows')
    native_paths = [root/'bin', windows/'System32']
    if not all((root/'bin'/name).is_file() for name in _VC_DLLS):
        raise ValueError('Faltan las copias privadas del runtime Microsoft Visual C++.')
    native_paths.append(windows)
    env['PATH'] = os.pathsep.join(dict.fromkeys(str(path) for path in native_paths))
    env['MSMPI_BIN'] = str(root/'bin')
    env['MSMPI_LOCAL_ONLY'] = '1'
    env['PROJ_DATA'] = env['PROJ_LIB'] = str(root/'share/proj')
    env['PROJ_NETWORK'] = 'OFF'
    env['GDAL_DRIVER_PATH'] = str(root/'bin/gdalplugins')
    if (root/'share/gdal').is_dir():
        env['GDAL_DATA'] = str(root/'share/gdal')
    return env
