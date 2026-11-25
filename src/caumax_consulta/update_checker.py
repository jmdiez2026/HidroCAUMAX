"""Read the public update index asynchronously; never download or install a ZIP.

One checker coalesces checks only while its request is running. A later check
always contacts the repository again, with no session or persistent TTL.
The module has no access to a project, geometry, query, credentials or widgets.
"""
import configparser
from pathlib import Path
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from qgis.PyQt.QtCore import QObject, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.core import Qgis, QgsNetworkAccessManager


PLUGIN_ID = 'caumax_consulta'
REPOSITORY_URL = 'https://jmdiez2026.github.io/HidroCAUMAX/plugins.xml'
RELEASES_URL = 'https://github.com/jmdiez2026/HidroCAUMAX/releases'
MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT_MS = 15000
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


def version_tuple(value):
    """The packaging contract uses stable major.minor.patch versions only."""
    if not isinstance(value, str) or len(value) > 32 or not _VERSION.fullmatch(value):
        raise ValueError('La versión de HidroCAUMAX no tiene un formato válido.')
    return tuple(map(int, value.split('.')))


def installed_version():
    metadata = configparser.ConfigParser(interpolation=None)
    with Path(__file__).with_name('metadata.txt').open(encoding='utf-8-sig') as stream:
        metadata.read_file(stream)
    value = metadata.get('general', 'version').strip()
    version_tuple(value)
    return value


def running_qgis_version():
    number = Qgis.QGIS_VERSION_INT
    version = (number // 10000, number // 100 % 100, number % 100)
    # Match QGIS's plugin manager: X.99 development builds target (X+1).0.
    return (version[0] + 1, 0, 0) if version[1] == 99 else version


def _qgis_bound(value, maximum=False):
    if not re.fullmatch(r'[0-9]{1,3}\.[0-9]{1,3}(?:\.[0-9]{1,3})?', value):
        raise ValueError('El índice no especifica una compatibilidad QGIS válida.')
    parts = tuple(map(int, value.split('.')))
    # QGIS treats a two-component maximum as including patch releases .0–.99.
    return parts + ((99 if maximum else 0),) if len(parts) == 2 else parts


def _field(entry, name, required=True):
    elements = entry.findall(name)
    if len(elements) > 1 or (required and len(elements) != 1):
        raise ValueError('El índice contiene metadatos incompletos o duplicados.')
    if not elements:
        return ''
    if len(elements[0]):
        raise ValueError('El índice contiene un campo de metadatos no válido.')
    return (elements[0].text or '').strip()


def _flag(entry, name, required=False):
    value = _field(entry, name, required).lower()
    if not value and not required:
        return False
    if value not in ('true', 'false'):
        raise ValueError('El índice contiene un indicador de publicación no válido.')
    return value == 'true'


def parse_update_feed(raw, current_version, qgis_version):
    """Return (status, update dict or None), validating only our plugin entries."""
    current = version_tuple(current_version)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('El índice de actualizaciones supera el tamaño permitido.')
    try:
        document = raw.decode('utf-8-sig')
    except UnicodeError as error:
        raise ValueError('El índice de actualizaciones no es XML UTF-8 válido.') from error
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', document, re.IGNORECASE):
        raise ValueError('El índice de actualizaciones no puede contener DTD ni entidades.')
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise ValueError('El índice de actualizaciones no es XML válido.') from error
    if root.tag != 'plugins':
        raise ValueError('El documento recibido no es un índice de complementos QGIS.')

    compatible = []
    stable_found = False
    newer_incompatible = False
    for entry in root.findall('pyqgis_plugin'):
        if entry.findtext('file_name', '').strip() != PLUGIN_ID + '.zip':
            continue
        name = entry.get('name', '').strip()
        if name != 'HidroCAUMAX' and not name.startswith('HidroCAUMAX v'):
            continue
        # Reject ambiguous fields before trusting the identity or channel.
        _field(entry, 'file_name')
        if (_flag(entry, 'experimental', required=True)
                or _flag(entry, 'deprecated') or _flag(entry, 'disabled')):
            continue
        version = entry.get('version', '').strip()
        numeric = version_tuple(version)
        visible_name = 'HidroCAUMAX v1.0' if version == '1.0.0' else 'HidroCAUMAX v' + version
        if name not in ('HidroCAUMAX', visible_name):
            raise ValueError('El nombre del complemento no coincide con su versión.')
        child_version = _field(entry, 'version', required=False)
        if child_version and child_version != version:
            raise ValueError('El índice contiene versiones contradictorias.')
        download_url = _field(entry, 'download_url')
        tag = 'v1.0' if version == '1.0.0' else 'v' + version
        expected_url = (RELEASES_URL + '/download/' + tag
                        + '/HidroCAUMAX_v' + version.replace('.', '_') + '.zip')
        # Exact comparison rejects other hosts/repos, HTTP, credentials, ports,
        # traversal, encoded paths, query strings and fragments.
        if download_url != expected_url:
            raise ValueError('El índice contiene una dirección de descarga no autorizada.')
        minimum = _qgis_bound(_field(entry, 'qgis_minimum_version'))
        maximum = _qgis_bound(_field(entry, 'qgis_maximum_version'), maximum=True)
        if minimum > maximum:
            raise ValueError('El índice contiene un intervalo QGIS no válido.')
        stable_found = True
        if minimum <= tuple(qgis_version) <= maximum:
            compatible.append((numeric, dict(
                plugin_id=PLUGIN_ID, installed_version=current_version, version=version,
                download_url=download_url, release_url=RELEASES_URL + '/tag/' + tag,
                repository_url=REPOSITORY_URL)))
        elif numeric > current:
            newer_incompatible = True
    newer = [item for item in compatible if item[0] > current]
    if newer:
        return 'available', max(newer, key=lambda item: item[0])[1]
    if newer_incompatible or (stable_found and not compatible):
        return 'incompatible', None
    return ('current' if compatible else 'not_found'), None


def network_request():
    parsed = urlsplit(REPOSITORY_URL)
    if (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment) != (
            'https', 'jmdiez2026.github.io', '/HidroCAUMAX/plugins.xml', '', ''):
        raise ValueError('El repositorio de actualizaciones debe usar su dirección HTTPS fija.')
    request = QNetworkRequest(QUrl(REPOSITORY_URL))
    request.setRawHeader(b'Accept', b'application/xml, text/xml')
    request.setRawHeader(b'User-Agent', b'HidroCAUMAX/update-check')
    request.setRawHeader(b'Cache-Control', b'no-cache, no-store')
    request.setAttribute(QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.AlwaysNetwork)
    request.setAttribute(QNetworkRequest.CacheSaveControlAttribute, False)
    request.setAttribute(QNetworkRequest.CookieLoadControlAttribute, QNetworkRequest.Manual)
    request.setAttribute(QNetworkRequest.CookieSaveControlAttribute, QNetworkRequest.Manual)
    request.setAttribute(QNetworkRequest.AuthenticationReuseAttribute, QNetworkRequest.Manual)
    request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.ManualRedirectPolicy)
    return request


class UpdateChecker(QObject):
    available = pyqtSignal(object)
    completed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reply = None
        self._raw = bytearray()
        self._generation = 0
        self._disposed = False
        self._installed = None
        self._qgis_version = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)

    @property
    def busy(self):
        return self._reply is not None

    def check(self):
        """Start a GET, or return False while already running/after dispose."""
        if self._disposed or self.busy:
            return False
        self._generation += 1
        generation = self._generation
        self._raw.clear()
        try:
            self._installed = installed_version()
            self._qgis_version = running_qgis_version()
            reply = QgsNetworkAccessManager.instance().get(network_request())
            self._reply = reply
            reply.setParent(self)
            reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
            reply.readyRead.connect(lambda: self._read(reply, generation))
            reply.finished.connect(lambda: self._finished(reply, generation))
            self._timer.start(TIMEOUT_MS)
            return True
        except Exception:
            self._complete('error', 'No se ha podido iniciar la comprobación de actualizaciones.', abort=True)
            return False

    def _current(self, reply, generation):
        return not self._disposed and self._reply is reply and self._generation == generation

    def _consume(self, reply):
        remaining = MAX_RESPONSE_BYTES + 1 - len(self._raw)
        if remaining > 0:
            chunk = reply.read(remaining)
            if chunk:
                self._raw.extend(bytes(chunk))
        return len(self._raw) <= MAX_RESPONSE_BYTES

    def _read(self, reply, generation):
        if not self._current(reply, generation):
            return
        try:
            if not self._consume(reply):
                self._complete('error', 'El índice de actualizaciones supera el tamaño permitido.', abort=True)
        except RuntimeError:
            self._complete('error', 'No se ha podido leer el índice de actualizaciones.', abort=True)

    def _finished(self, reply, generation):
        if not self._current(reply, generation):
            return
        try:
            if not self._consume(reply):
                raise ValueError('El índice de actualizaciones supera el tamaño permitido.')
            if (reply.error() != QNetworkReply.NoError
                    or reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) != 200):
                raise ValueError('No se ha podido consultar el repositorio de actualizaciones.')
            if reply.url().toString() != REPOSITORY_URL:
                raise ValueError('La respuesta no procede del repositorio de actualizaciones autorizado.')
            status, info = parse_update_feed(bytes(self._raw), self._installed, self._qgis_version)
            details = {
                'available': 'Hay una versión estable compatible de HidroCAUMAX más reciente.',
                'current': 'No hay una versión estable compatible más reciente.',
                'incompatible': 'La versión publicada no es compatible con este QGIS.',
                'not_found': 'El índice no contiene una versión estable de HidroCAUMAX.',
            }
            self._complete(status, details[status], info=info)
        except (ValueError, RuntimeError) as error:
            self._complete('error', str(error))

    def _complete(self, status, detail, info=None, abort=False):
        # Detach first: abort() can synchronously emit finished(). A new check
        # started by a signal recipient must not be cleared by this old result.
        reply, self._reply = self._reply, None
        self._generation += 1
        self._timer.stop()
        self._raw.clear()
        if reply is not None:
            try:
                if abort:
                    reply.abort()
                reply.deleteLater()
            except RuntimeError:
                pass
        if info is not None and not self._disposed:
            self.available.emit(info)
        self.completed.emit(status, detail)

    def _timeout(self):
        if self.busy:
            self._complete('timeout', 'La comprobación de actualizaciones agotó el tiempo de espera.', abort=True)

    def cancel(self):
        if self.busy:
            self._complete('cancelled', 'Comprobación de actualizaciones cancelada.', abort=True)

    def dispose(self):
        self._disposed = True
        self.cancel()
