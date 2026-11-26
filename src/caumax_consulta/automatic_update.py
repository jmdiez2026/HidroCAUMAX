"""Explicit, verified updates with a reversible file transaction and QGIS reload."""
import configparser
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from uuid import uuid4
import zipfile

from qgis.PyQt.QtCore import QObject, QTimer
from qgis.core import Qgis, QgsApplication, QgsTask, QgsMessageLog

from .update_checker import PLUGIN_ID, RELEASES_URL, version_tuple, _qgis_bound, running_qgis_version

ROOT = Path(__file__).resolve().parent
MAX_ZIP = 150 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024


def _safe_path(name):
    parts = PurePosixPath(name).parts
    if (not parts or parts[0] != PLUGIN_ID or len(parts) < 2 or '\\' in name
            or ':' in name or name.startswith('/') or any(p in ('.', '..') for p in name.split('/'))
            or any(p.endswith((' ', '.')) for p in parts)):
        raise ValueError('El paquete contiene una ruta no válida.')
    return Path(*parts[1:])


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != 'https' or target.username or target.password or target.port not in (None, 443)
                or target.hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
                or (target.hostname == 'github.com' and not target.path.startswith('/jmdiez2026/HidroCAUMAX/releases/'))):
            raise ValueError('La descarga ha redirigido a un destino no autorizado.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, target, limit, cancelled):
    start, size = time.monotonic(), 0
    request = Request(url, headers={'User-Agent': 'HidroCAUMAX/update', 'Cache-Control': 'no-cache'})
    with build_opener(_Redirects()).open(request, timeout=30) as response, target.open('xb') as stream:
        while True:
            if cancelled():
                raise InterruptedError('Actualización cancelada antes de instalar.')
            if time.monotonic() - start > 300:
                raise TimeoutError('La descarga ha agotado el tiempo de espera.')
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('La descarga supera el tamaño permitido.')
            stream.write(chunk)


def validate_package(package, manifest, info, stage):
    """Validate identity, hash, paths and every member before extracting any file."""
    version = info['version']
    expected = RELEASES_URL + '/download/v' + version + '/HidroCAUMAX_v' + version.replace('.', '_') + '.zip'
    if (info['download_url'] != expected or manifest.get('download_url') != expected
            or manifest.get('plugin_id') != PLUGIN_ID or manifest.get('version') != version
            or manifest.get('repository') != 'jmdiez2026/HidroCAUMAX'
            or manifest.get('experimental') is not False
            or manifest.get('bytes') != package.stat().st_size
            or manifest.get('sha256') != _hash(package)):
        raise ValueError('La identidad, el tamaño o el SHA-256 del paquete no coincide con la publicación.')
    files = manifest.get('source_files', {})
    if not isinstance(files, dict) or not {PLUGIN_ID+'/__init__.py', PLUGIN_ID+'/metadata.txt'} <= files.keys():
        raise ValueError('Falta el inventario de archivos del complemento.')
    with zipfile.ZipFile(package) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if (len(members) > 10000 or sum(item.file_size for item in members) > MAX_EXPANDED
                or len(set(n.casefold() for n in names)) != len(names) or set(names) != set(files)):
            raise ValueError('El inventario del ZIP no coincide con la publicación.')
        for item in members:
            _safe_path(item.filename)
            if (item.is_dir() or item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16)
                    or not re.fullmatch('[0-9a-f]{64}', str(files[item.filename]))
                    or hashlib.sha256(archive.read(item)).hexdigest() != files[item.filename]):
                raise ValueError('Archivo inválido en el paquete: ' + item.filename)
        metadata = configparser.ConfigParser(interpolation=None)
        metadata.read_string(archive.read(PLUGIN_ID+'/metadata.txt').decode('utf-8-sig'))
        general = metadata['general']
        if (general['version'] != version or general.get('experimental', '').lower() != 'false'
                or not _qgis_bound(general['qgisMinimumVersion']) <= running_qgis_version()
                <= _qgis_bound(general['qgisMaximumVersion'], maximum=True)):
            raise ValueError('La versión o la compatibilidad de QGIS del ZIP no es válida.')
        for item in members:
            destination = stage / _safe_path(item.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(archive.read(item))
    return tuple(_safe_path(name) for name in names)


class FileTransaction:
    """Replace only packaged files; never remove user exports or unrelated files."""
    def __init__(self, root, stage, backup, files):
        self.root, self.stage, self.backup = map(Path, (root, stage, backup))
        self.files, self.changed = files, []

    def apply(self):
        for relative in self.files:
            target, source = self.root / relative, self.stage / relative
            if not target.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('Un archivo sale de la carpeta de instalación.')
            for ancestor in (target, *target.parents):
                if ancestor.is_symlink() or getattr(ancestor, 'is_junction', lambda: False)():
                    raise ValueError('No se actualizan instalaciones con enlaces de archivos.')
                if ancestor == self.root:
                    break
            if target.exists() and _hash(target) == _hash(source):
                continue  # Identical rasters may be open in the current project.
            existed = target.exists()
            if existed:
                saved = self.backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.hcu-' + uuid4().hex[:12] + '.tmp')
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
                self.changed.append((relative, existed))
            finally:
                temporary.unlink(missing_ok=True)

    def rollback(self):
        failures = []
        for relative, existed in reversed(self.changed):
            try:
                target = self.root / relative
                if existed:
                    shutil.copy2(self.backup / relative, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as error:
                failures.append(str(error))
        if failures:
            raise OSError('No se ha podido completar la restauración. Copia conservada en ' + str(self.backup))


def work_pending():
    for task in QgsApplication.taskManager().activeTasks():
        if type(task).__module__.startswith('caumax_consulta.') and not isinstance(task, DownloadTask):
            return True
    from .delineation_cleanup import _dialogs, _participants
    for owner in tuple(_dialogs) + tuple(_participants):
        try:
            if any(getattr(owner, key, None) for key in ('_task', '_surface_task', '_save_surface_task', '_saving_project', '_resolution_pending', '_generation_running', '_loading', '_queued_prepare')):
                return True
            if any(getattr(owner, key, None) is not None and getattr(owner, key).isActive()
                    for key in ('_drainage_timer', '_resolution_timer')):
                return True
        except RuntimeError:
            continue
    return False


class DownloadTask(QgsTask):
    def __init__(self, info, folder, callback):
        super().__init__('HidroCAUMAX · Descargar actualización', QgsTask.CanCancel)
        self.info, self.folder, self.callback = dict(info), Path(folder), callback
        self.error, self.files = '', ()

    def run(self):
        try:
            version_tuple(self.info['version'])
            base = RELEASES_URL + '/download/v' + self.info['version'] + '/'
            expected = base + 'HidroCAUMAX_v' + self.info['version'].replace('.', '_') + '.zip'
            if expected != self.info['download_url']:
                raise ValueError('Dirección de descarga no autorizada.')
            self.folder.mkdir(parents=True, exist_ok=False)
            manifest_path, package = self.folder/'release_manifest.json', self.folder/'package.zip'
            _download(base+'release_manifest.json', manifest_path, 2*1024*1024, self.isCanceled)
            _download(expected, package, MAX_ZIP, self.isCanceled)
            self.files = validate_package(package, json.loads(manifest_path.read_text(encoding='utf-8-sig')),
                                          self.info, self.folder/'stage')
            return not self.isCanceled()
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)


def _load_without_modal_error(utils):
    """Keep our own startup failures nonmodal so rollback runs immediately."""
    original = utils.showException
    def report(*args, **kwargs):
        if len(args) > 3 and PLUGIN_ID in str(args[3]):
            detail = ''.join(traceback.format_exception(*args[:3]))
            QgsMessageLog.logMessage(str(args[3]) + '\n' + detail, 'HidroCAUMAX', Qgis.Critical)
        else:
            original(*args, **kwargs)
    utils.showException = report
    try:
        return utils.loadPlugin(PLUGIN_ID) and utils.startPlugin(PLUGIN_ID)
    finally:
        if utils.showException is report:
            utils.showException = original


class AutomaticUpdater(QObject):
    """Parented to QGIS, so unloading this plugin cannot destroy the transaction."""
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface, self.task, self.installing = iface, None, False
        self.notice = None

    @property
    def busy(self):
        return self.task is not None or self.installing

    def message(self, text, warning=False, duration=10):
        self.last_detail = text
        QgsMessageLog.logMessage(text, 'HidroCAUMAX', Qgis.Warning if warning else Qgis.Info)
        bar = self.iface.messageBar()
        if self.notice is not None:
            try:
                bar.popWidget(self.notice)
            except RuntimeError:
                pass
        self.notice = bar.createMessage('HidroCAUMAX', text)
        bar.pushWidget(self.notice, Qgis.Warning if warning else Qgis.Info, duration)

    def start(self, info):
        if self.busy:
            return False
        if work_pending():
            self.message('Termina o cancela la generación de capas antes de actualizar.', True)
            return False
        # Leave room for the bundled raster's long filename on Windows.
        folder = Path(QgsApplication.qgisSettingsDirPath())/'HidroCAUMAX'/'updates'/uuid4().hex[:12]
        self.task = DownloadTask(info, folder, self._downloaded)
        self.message('Descargando y verificando la actualización…', duration=0)
        QgsApplication.taskManager().addTask(self.task)
        return True

    def cancel(self):
        if self.task is not None:
            self.task.cancel()

    def _downloaded(self, task, success):
        if task is not self.task:
            return
        self.task = None
        if not success or task.isCanceled():
            self.message(task.error or 'Actualización cancelada antes de instalar.', True)
            return
        if work_pending():
            self.message('Descarga comprobada. Termina o cancela el trabajo y vuelve a pulsar Actualizar.', True)
            return
        # Leave the task's finished callback before unloading its defining module.
        self.installing = True
        QTimer.singleShot(0, lambda: self._install(task))

    def _install(self, task):
        from qgis import utils
        from .improved_storage import preserve_exports
        transaction = FileTransaction(ROOT, task.folder/'stage', task.folder/'backup', task.files)
        self.installing = True
        unloaded = False
        try:
            if work_pending():
                raise RuntimeError('Hay un cálculo pendiente. Vuelve a actualizar al terminar.')
            preserve_exports()
            if not utils.isPluginLoaded(PLUGIN_ID) or not utils.unloadPlugin(PLUGIN_ID):
                raise RuntimeError('QGIS no ha podido descargar la versión anterior.')
            unloaded = True
            transaction.apply()
            importlib.invalidate_caches()
            utils.updateAvailablePlugins()
            if not _load_without_modal_error(utils):
                raise RuntimeError('QGIS no ha podido activar la versión nueva.')
            if utils.pluginMetadata(PLUGIN_ID, 'version') != task.info['version']:
                raise RuntimeError('QGIS no confirma la versión instalada.')
            self.message('Actualización completada. Versión instalada: ' + task.info['version'] + '.')
        except Exception as error:
            recovery = ''
            if unloaded:
                try:
                    if utils.isPluginLoaded(PLUGIN_ID):
                        utils.unloadPlugin(PLUGIN_ID)
                    elif PLUGIN_ID in getattr(utils, '_plugin_modules', {}):
                        utils._unloadPluginModules(PLUGIN_ID)
                    transaction.rollback()
                    importlib.invalidate_caches()
                    utils.updateAvailablePlugins()
                    if not _load_without_modal_error(utils):
                        raise RuntimeError('Reinicia QGIS para cargar la versión restaurada.')
                    recovery = ' Se ha restaurado la versión anterior.'
                except Exception as restore_error:
                    recovery = ' ' + str(restore_error)
            self.message('No se ha completado la actualización: ' + str(error) + recovery, True, 0)
        finally:
            self.installing = False
            if getattr(self.iface, '_hidrocaumax_updater', None) is self:
                del self.iface._hidrocaumax_updater


def controller(iface):
    instance = getattr(iface, '_hidrocaumax_updater', None)
    if instance is None:
        instance = AutomaticUpdater(iface)
        iface._hidrocaumax_updater = instance
    return instance
