"""Add basin access and verified PDF opening to CAUMAX's own flow dialog.

CAUMAX functions, globals, and installed files are never modified. Only the
original report slot on each observed dialog is temporarily replaced, with
an equivalent function using a private globals dictionary for that call.
"""
from dataclasses import dataclass
import functools
import math
import os
from pathlib import Path
import sys
import types
from typing import Optional, Union

from qgis.PyQt.QtCore import QObject, QEvent, QSize, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QFontInfo, QIcon, QPixmap
from qgis.PyQt.QtWidgets import QApplication, QBoxLayout, QDialog, QPushButton
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsMessageLog, QgsRasterLayer,
)

from .grid import cell_at_point


BUTTON_NAME = 'btnCuencaHidrograficaAuxiliar'
LOGO_PATH = Path(__file__).resolve().parent / 'assets' / 'gea_uva.png'
BUTTON_STYLE = '''
QPushButton { background-color: #D9F0FF; border: 1px solid #7EB4D0;
 color: #173D52; border-radius: 3px; padding: 0px 9px; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:disabled { color: #7A8790; background-color: #EAF3F8; }
'''


class _LogoBasinButton(QPushButton):
    """Keep the label and logo size while making the existing button 10% taller."""
    SCALE = 1.10
    HEIGHT_SCALE = 1.10

    def __init__(self, text, parent=None):
        self._reference_buttons = ()
        self._logo = QPixmap()
        super().__init__(text, parent)

    def match_reference_buttons(self, buttons):
        self._reference_buttons = tuple(buttons)
        for button in self._reference_buttons:
            button.ensurePolished()
            button.installEventFilter(self)
        self._update_relative_size()

    def _update_relative_size(self):
        try:
            if not self._reference_buttons:
                return
            font = QFont(self._reference_buttons[0].font())
            points = font.pointSizeF()
            if points <= 0:
                points = QFontInfo(font).pointSizeF()
            font.setPointSizeF(points * self.SCALE)
            self.setFont(font)
            reference_height = max(
                button.height() if button.isVisible() else button.sizeHint().height()
                for button in self._reference_buttons)
            original_height = max(1, round(reference_height * self.SCALE))
            self.setFixedHeight(max(1, round(original_height * self.HEIGHT_SCALE)))
            if not self._logo.isNull():
                height = max(1, min(self.fontMetrics().height(), original_height - 4))
                width = max(1, round(height * self._logo.width() / self._logo.height()))
                self.setIconSize(QSize(width, height))
            # Native Qt painting keeps the font unchanged; the width includes
            # the complete label, logo, spacing and stylesheet padding.
            self.setMinimumWidth(super().sizeHint().width())
            self.updateGeometry()
        except RuntimeError:
            # A queued callback may outlive an already closed native dialog.
            return

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.FontChange, QEvent.StyleChange)
                or (event.type() == QEvent.Resize
                    and event.size().height() != event.oldSize().height())):
            QTimer.singleShot(0, self._update_relative_size)
        return super().eventFilter(watched, event)

    def install_logo(self):
        """Load the logo and reserve enough width for it and the full label."""
        try:
            if not self._logo.isNull():
                return
            logo = QPixmap(str(LOGO_PATH))
            if logo.isNull():
                return
            self._logo = logo
            self.setIcon(QIcon(logo))
            self._update_relative_size()
        except RuntimeError:
            # A queued callback may outlive an already closed native dialog.
            return


@dataclass(frozen=True)
class NativeQuery:
    flow: Optional[float]
    period: Optional[Union[int, float]]
    x: float
    y: float
    crs: QgsCoordinateReferenceSystem
    directions_path: str
    dem_path: str
    layername: str
    layer_id: str = ''
    flow_layer_path: str = ''
    cell: object = None
    source_kind: str = 'caumax_raster'
    rational_context: object = None


def _number(text):
    text = str(text).strip().replace('\u00a0', '').replace(' ', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    try:
        value = float(text)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('El resultado de CAUMAX no contiene un número válido.') from error
    if not math.isfinite(value):
        raise ValueError('El resultado de CAUMAX no es finito.')
    return value


def native_module(dialog):
    """Resolve an actual loaded MaxFlowDialog class, not a window title."""
    for name, module in tuple(sys.modules.items()):
        if module is None or not name.endswith('.maxflowdialog'):
            continue
        cls = getattr(module, 'MaxFlowDialog', None)
        if isinstance(cls, type) and isinstance(dialog, cls):
            return module
    return None


def _layers_module(module):
    package = module.__name__.rsplit('.', 1)[0]
    layers = sys.modules.get(package + '.layers')
    if layers is None:
        raise ValueError('No se encuentra la configuración activa de capas de CAUMAX.')
    if not all(hasattr(layers, name) for name in ('getLayerPath', 'MDT', 'FLOWDIRS')):
        raise ValueError('La versión de CAUMAX no expone sus capas de cálculo.')
    return layers


def make_native_query(dialog, module=None):
    """Freeze the successful native query; edited input fields are only checked.

    x/y always come from lastValidPoint, never from subsequently edited text.
    Cell metadata is optional and does not move or round that native point.
    """
    module = native_module(dialog) if module is None else module
    if module is None or not dialog.btnReport.isEnabled():
        raise ValueError('Primero consulta un caudal válido en CAUMAX.')
    point = getattr(dialog, 'lastValidPoint', None)
    if point is None:
        raise ValueError('Todavía no existe un punto consultado válido.')
    x, y = float(point.x()), float(point.y())
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError('El punto consultado no tiene coordenadas válidas.')
    for entered, actual in ((dialog.txtX.text(), x), (dialog.txtY.text(), y)):
        if not math.isclose(_number(entered), actual, rel_tol=0, abs_tol=1e-7):
            raise ValueError('Las coordenadas han cambiado: vuelve a pulsar Consultar.')
    selected = [(button, key) for button, key in dialog.buttonsAndLayers.items()
                if button.isChecked()]
    if len(selected) != 1:
        raise ValueError('Selecciona un único periodo de retorno y consulta el caudal.')
    button, layer_key = selected[0]
    period_value = _number(dialog.txtResultReturnPeriod.text())
    if not period_value.is_integer() or period_value not in (2, 5, 10, 25, 100, 500):
        raise ValueError('El periodo de retorno consultado no es válido.')
    period = int(period_value)
    if _number(button.text()) != period:
        raise ValueError('El periodo de retorno ha cambiado: vuelve a pulsar Consultar.')
    flow = _number(dialog.txtResultFlow.text())
    layers = _layers_module(module)
    expected = getattr(layers, f'FLOW_{period}', None)
    if expected is not None and expected != layer_key:
        raise ValueError('La capa seleccionada no coincide con el periodo consultado.')
    get_layer = getattr(module, 'getLayer', None) or getattr(layers, 'getLayer', None)
    if not callable(get_layer):
        raise ValueError('CAUMAX no expone la capa del caudal consultado.')
    raster = get_layer(layer_key)
    if not isinstance(raster, QgsRasterLayer) or not raster.isValid():
        raise ValueError('La capa ráster del caudal consultado no es válida.')
    crs = QgsCoordinateReferenceSystem(raster.crs())
    if not crs.isValid():
        raise ValueError('La capa consultada no tiene un sistema de referencia válido.')
    cell = None
    extent = raster.extent()
    try:
        cell = cell_at_point(x, y, extent.xMinimum(), extent.yMinimum(),
                             extent.xMaximum(), extent.yMaximum(),
                             raster.width(), raster.height())
    except ValueError:
        # The successful native query remains authoritative. A cell estimate
        # must not silently replace its point with another point.
        pass
    return NativeQuery(
        flow, period, x, y, crs,
        os.fspath(layers.getLayerPath(layers.FLOWDIRS)),
        os.fspath(layers.getLayerPath(layers.MDT)),
        raster.name(), raster.id(), raster.source(), cell,
    )


class _ReportCapture:
    def __init__(self):
        self.selected = []
        self.exports = []


class _FileDialogProxy:
    def __init__(self, original, capture):
        self.original, self.capture = original, capture

    def getSaveFileName(self, *args, **kwargs):
        result = self.original.getSaveFileName(*args, **kwargs)
        if result and result[0]:
            self.capture.selected.append(os.fspath(result[0]))
        return result

    def __getattr__(self, name):
        return getattr(self.original, name)


class _ExporterProxy:
    def __init__(self, exporter, capture):
        self.exporter, self.capture = exporter, capture

    def exportToPdf(self, filename, *args, **kwargs):
        result = self.exporter.exportToPdf(filename, *args, **kwargs)
        self.capture.exports.append((os.fspath(filename), result))
        return result

    def __getattr__(self, name):
        return getattr(self.exporter, name)


class _ExporterFactory:
    def __init__(self, original, capture):
        self.original, self.capture = original, capture

    def __call__(self, *args, **kwargs):
        return _ExporterProxy(self.original(*args, **kwargs), self.capture)

    def __getattr__(self, name):
        return getattr(self.original, name)


def _clone_report(original, capture):
    function = getattr(original, '__func__', None)
    owner = getattr(original, '__self__', None)
    if not isinstance(function, types.FunctionType) or owner is None:
        raise ValueError('El informe de esta versión de CAUMAX no es compatible con la apertura automática.')
    namespace = function.__globals__.copy()
    if ('QFileDialog' not in namespace or 'QgsLayoutExporter' not in namespace
            or not {'QFileDialog', 'QgsLayoutExporter'}.issubset(function.__code__.co_names)):
        raise ValueError('El informe de esta versión de CAUMAX no expone el exportador esperado.')
    namespace['QFileDialog'] = _FileDialogProxy(namespace['QFileDialog'], capture)
    namespace['QgsLayoutExporter'] = _ExporterFactory(namespace['QgsLayoutExporter'], capture)
    clone = types.FunctionType(function.__code__, namespace, function.__name__,
                               function.__defaults__, function.__closure__)
    clone.__kwdefaults__ = (None if function.__kwdefaults__ is None
                           else function.__kwdefaults__.copy())
    clone.__annotations__ = function.__annotations__.copy()
    clone.__dict__.update(function.__dict__)
    clone.__module__, clone.__qualname__ = function.__module__, function.__qualname__
    clone.__doc__ = function.__doc__
    return types.MethodType(clone, owner)


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def report_with_auto_open(original, notify=lambda text: None,
                          allow_open=lambda: True, opener=None):
    """Return a targeted Qt slot preserving the original report implementation.

    opener is injectable for isolated tests. A selected destination, exporter
    Success and a real %PDF header are all required; old files never suffice.
    """
    _clone_report(original, _ReportCapture())  # Check compatibility before disconnecting.

    @functools.wraps(original)
    def wrapped(_checked=False):
        capture = _ReportCapture()
        try:
            clone = _clone_report(original, capture)
            result = clone()
            exporter_class = original.__func__.__globals__['QgsLayoutExporter']
            if allow_open() and capture.selected:
                selected = {_canonical(path) for path in capture.selected}
                for path, status in reversed(capture.exports):
                    if status != exporter_class.Success or _canonical(path) not in selected:
                        continue
                    try:
                        with open(path, 'rb') as pdf:
                            valid = pdf.read(4) == b'%PDF'
                    except OSError:
                        valid = False
                    if valid:
                        opened = (opener or QDesktopServices.openUrl)(QUrl.fromLocalFile(os.path.abspath(path)))
                        if opened is False:
                            notify('El PDF está guardado, pero el sistema no ha podido abrir su visor: ' + os.path.abspath(path))
                        break
            return result
        except Exception as error:
            notify('No se ha podido completar el informe de CAUMAX: ' + str(error))
            return None

    return wrapped


@dataclass
class _Binding:
    dialog: object
    module: object
    button: object
    layout: object
    connections: list
    original_report: object = None
    wrapper: object = None
    query: object = None
    query_dirty: bool = False
    settle_pending: bool = False


class CaumaxBridge(QObject):
    """Watch native MaxFlowDialog instances; start/stop are idempotent."""
    def __init__(self, iface, on_basin_requested, on_query_started=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.on_basin_requested = on_basin_requested
        self.on_query_started = on_query_started
        self._running = False
        self._bindings = {}
        self._notified = set()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.scan)

    def _notify(self, message):
        if message in self._notified:
            return
        self._notified.add(message)
        QgsMessageLog.logMessage(message, 'CAUMAX · Consulta', Qgis.Info)
        try:
            self.iface.messageBar().pushMessage('CAUMAX', message, level=Qgis.Info, duration=8)
        except (AttributeError, RuntimeError):
            pass

    def start(self):
        if self._running:
            return
        self._running = True
        self.scan()
        self._timer.start()

    def stop(self):
        self._running = False
        self._timer.stop()
        for key in tuple(self._bindings):
            self._detach(key)

    @staticmethod
    def _report_layout(dialog):
        layouts = [dialog.layout()] + dialog.findChildren(QBoxLayout)
        for layout in layouts:
            if isinstance(layout, QBoxLayout) and layout.indexOf(dialog.btnReport) >= 0:
                return layout
        return None

    def scan(self):
        if not self._running:
            return
        for key, binding in tuple(self._bindings.items()):
            try:
                self._sync(key)
            except RuntimeError:
                self._detach(key)
        for dialog in QApplication.topLevelWidgets():
            try:
                if (not isinstance(dialog, QDialog) or not dialog.isVisible()
                        or id(dialog) in self._bindings):
                    continue
                module = native_module(dialog)
                if module is None:
                    continue
                required = ('btnReport', 'btnCalculate', 'buttonsAndLayers',
                            'txtResultFlow', 'txtResultReturnPeriod', 'txtX', 'txtY')
                if not all(hasattr(dialog, name) for name in required):
                    self._notify('La ventana de consulta de esta versión de CAUMAX no es compatible con la ampliación.')
                    continue
                if dialog.findChild(QPushButton, BUTTON_NAME) is not None:
                    continue
                self._attach(dialog, module)
            except RuntimeError:
                continue

    def _attach(self, dialog, module):
        layout = self._report_layout(dialog)
        if layout is None:
            self._notify('No se encuentra la botonera de consulta de CAUMAX; se conserva la ventana original.')
            return
        button = _LogoBasinButton('Cuenca hidrográfica', dialog)
        button.setObjectName(BUTTON_NAME)
        button.setStyleSheet(BUTTON_STYLE)
        button.setToolTip('Delimitar la cuenca del último punto consultado correctamente en CAUMAX')
        button.setEnabled(False)
        button.match_reference_buttons(
            layout.itemAt(index).widget()
            for index in range(layout.indexOf(dialog.btnReport), layout.count())
            if isinstance(layout.itemAt(index).widget(), QPushButton))
        layout.insertWidget(layout.indexOf(dialog.btnReport), button)
        key = id(dialog)
        binding = _Binding(dialog, module, button, layout, [])
        self._bindings[key] = binding
        dialog.btnReport.installEventFilter(self)

        def connect(signal, slot):
            signal.connect(slot)
            binding.connections.append((signal, slot))

        connect(button.clicked, lambda _checked=False, key=key: self._request(key))
        connect(dialog.finished, lambda *_args, key=key: self._detach(key))
        connect(dialog.destroyed, lambda *_args, key=key: self._detach(key))
        for widget in (dialog.txtX, dialog.txtY):
            connect(widget.textChanged, lambda *_args, key=key: self._begin_query(key))
        for widget in (dialog.txtResultFlow, dialog.txtResultReturnPeriod):
            connect(widget.textChanged, lambda *_args, key=key: self._sync(key))
        for radio in dialog.buttonsAndLayers:
            connect(radio.toggled, lambda *_args, key=key: self._sync(key))
        connect(dialog.btnCalculate.clicked, lambda *_args, key=key: self._begin_query(key))
        try:
            original = dialog.createReport
            wrapper = report_with_auto_open(
                original, self._notify,
                allow_open=lambda key=key: self._running and key in self._bindings,
            )
            # Never disconnect unrelated plugins' or CAUMAX's other slots.
            dialog.btnReport.clicked.disconnect(original)
            try:
                dialog.btnReport.clicked.connect(wrapper)
            except Exception:
                dialog.btnReport.clicked.connect(original)
                raise
            binding.original_report, binding.wrapper = original, wrapper
        except (AttributeError, TypeError, ValueError, RuntimeError) as error:
            self._notify('Se conserva el informe original: ' + str(error))
        # A newly opened native query is a new point context, even if CAUMAX
        # filled its coordinates before this bridge attached to the window.
        self._begin_query(key)
        QTimer.singleShot(0, button.install_logo)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.EnabledChange:
            for key, binding in tuple(self._bindings.items()):
                if obj is binding.dialog.btnReport:
                    self._sync(key)
                    break
        return False

    def _sync(self, key):
        binding = self._bindings.get(key)
        if binding is None or not self._running:
            return
        try:
            binding.query = make_native_query(binding.dialog, binding.module)
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
            binding.query = None
        binding.button.setEnabled(binding.query is not None)
        if binding.query is not None and binding.query_dirty and not binding.settle_pending:
            binding.settle_pending = True
            QTimer.singleShot(0, lambda key=key: self._settle_query(key))

    def _begin_query(self, key):
        """Invalidate old results once for the native X/Y update sequence."""
        binding = self._bindings.get(key)
        if binding is None or not self._running:
            return
        if not binding.query_dirty:
            # Set before closing windows: Qt callbacks can re-enter the bridge.
            binding.query_dirty = True
            if self.on_query_started is not None:
                self.on_query_started(binding.dialog)
        self._sync(key)

    def _settle_query(self, key):
        """Rearm after both coordinates and the native result are consistent."""
        binding = self._bindings.get(key)
        if binding is None or not self._running:
            return
        binding.settle_pending = False
        try:
            make_native_query(binding.dialog, binding.module)
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError):
            return
        binding.query_dirty = False

    def _request(self, key):
        binding = self._bindings.get(key)
        if binding is None or not self._running:
            return
        self._sync(key)
        if binding.query is None:
            self._notify('Vuelve a consultar un caudal válido antes de abrir la cuenca hidrográfica.')
            return
        # Opening the result establishes this valid query immediately; a new
        # coordinate signal must invalidate it even before the next event turn.
        binding.query_dirty = False
        try:
            self.on_basin_requested(binding.dialog, binding.query)
        except Exception as error:
            self._notify('No se pudo abrir la cuenca hidrográfica: ' + str(error))

    def _detach(self, key):
        binding = self._bindings.pop(key, None)
        if binding is None:
            return
        try:
            binding.dialog.btnReport.removeEventFilter(self)
            if binding.wrapper is not None:
                binding.dialog.btnReport.clicked.disconnect(binding.wrapper)
                binding.dialog.btnReport.clicked.connect(binding.original_report)
        except (RuntimeError, TypeError):
            pass
        for signal, slot in binding.connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        binding.connections.clear()
        try:
            binding.layout.removeWidget(binding.button)
            binding.button.setParent(None)
            binding.button.deleteLater()
        except RuntimeError:
            pass
