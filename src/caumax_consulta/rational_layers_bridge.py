"""Reversible, dialog-local access to terrain generation in Método racional.

The native calculator and installed CAUMAX files remain untouched. Coordinates
belong to the canvas CRS at entry time; a later canvas CRS change does not
reinterpret the numbers already present in the fields.
"""
from dataclasses import dataclass, field
import math
from pathlib import Path
import sys

from qgis.PyQt.QtCore import QObject, QTimer, Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from qgis.core import QgsCoordinateReferenceSystem

from .rational_export import (BUTTON_STYLE, BUTTON_NAME as SAVE_BUTTON_NAME,
    ACTION_FRAME_NAME, ACTION_BUTTON_STYLE, EXPORT_OWNER_PROPERTY, _place_export_button)
from .caumax_bridge import LOGO_PATH


BUTTON_NAME = 'btnGenerarCapasResolucionMejoradaHidroCAUMAX'
PANEL_NAME = 'HidroCAUMAXRationalLayersContainer'
BASIN_BUTTON_NAME = 'btnCuencaHidrograficaRacionalHidroCAUMAX'
FREQUENCY_BUTTON_NAME = 'btnCalcularFrecuenciaHidroCAUMAX'
CLEAN_BUTTON_NAME = 'btnLimpiarCapasHidroCAUMAX'
_FLOW_INPUTS = ('txtArea', 'txtConcentrationTime', 'txtI1Id', 'txtRainfall',
                'txtIntensityFactor', 'txtP0', 'txtP0Corrector', 'txtUniformity',
                'txtCorrectedRainfall', 'txtP0CorrectorReturnPeriod', 'txtRainfallCorrection')


@dataclass(frozen=True)
class RationalTerrainQuery:
    x: float
    y: float
    crs: QgsCoordinateReferenceSystem
    dem_path: str
    directions_path: str
    source_kind: str = 'rational'
    flow: float | None = None
    period: float | None = None
    cell: object = None


def _native_module(dialog):
    for cls in type(dialog).__mro__:
        if cls.__name__ == 'RationalMethodDialog' and cls.__module__.endswith('.rationalmethoddialog'):
            return sys.modules.get(cls.__module__)
    return None


def _number(text):
    text = str(text).strip().replace('\u00a0', '').replace(' ', '')
    if ',' in text:
        text = text.replace('.', '').replace(',', '.')
    try:
        value = float(text)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('Selecciona un punto de cálculo o introduce sus coordenadas X e Y.') from error
    if not math.isfinite(value):
        raise ValueError('Las coordenadas del punto de cálculo deben ser finitas.')
    return value


def _layer_paths(dialog):
    module = _native_module(dialog)
    layers = sys.modules.get(module.__name__.rsplit('.', 1)[0] + '.layers') if module is not None else None
    if layers is None or not all(hasattr(layers, name) for name in ('getLayerPath', 'MDT', 'FLOWDIRS')):
        raise ValueError('CAUMAX no expone la configuración activa del MDT y las direcciones de flujo.')
    paths = []
    for key, label in ((layers.MDT, 'MDT'), (layers.FLOWDIRS, 'direcciones de flujo')):
        try:
            raw = layers.getLayerPath(key)
            if not raw:
                raise ValueError
            path = Path(raw).expanduser()
            if not path.is_file():
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ValueError(f'No se encuentra el archivo de {label} configurado en CAUMAX.') from error
        paths.append(str(path.resolve()))
    return tuple(paths)


@dataclass
class _Binding:
    dialog: object
    container: object
    button: object
    scroll: object
    error: object
    input_crs: object
    connections: list = field(default_factory=list)
    panel: object = None
    layers_dialog: object = None
    exact_point: object = None
    exact_fields: object = None
    body_scroll: object = None
    body_host: object = None
    body_items: list = field(default_factory=list)
    native_compact_layouts: list = field(default_factory=list)
    collapsed_geometry: object = None
    auto_geometry: object = None
    fitting: bool = False
    basin_button: object = None
    frequency_button: object = None
    clean_button: object = None
    native_height: object = None
    native_width: object = None
    fitted_height: object = None
    fitted_width: object = None
    decimal_presentation: object = None
    hidden_native_outline: object = None
    native_outline_visibility: object = None
    action_row: object = None
    basin_ready: bool = False
    parameters_validated: bool = False
    basin_stamp: object = None
    source_signature: object = None
    generation: int = 0
    calculating: set = field(default_factory=set)
    parameter_start: object = None
    flow_start: object = None
    flow_result: object = None
    flow_signature: object = None
    query_opened: bool = False
    basin_message: str = 'Primero calcula las variables intermedias en Método racional.'


class RationalLayersBridge(QObject):
    """Own the native buttons and an independent, modeless D8-generation window."""
    def __init__(self, iface, on_basin_requested=None, on_query_started=None,
                 on_basin_refined=None, on_frequency_requested=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.on_basin_requested = on_basin_requested
        self.on_query_started = on_query_started
        self.on_frequency_requested = on_frequency_requested
        self.on_basin_refined = on_basin_refined
        self._running = False
        self._bindings = {}
        from .recommended_p0 import RecommendedP0Presentation
        self._recommended_p0 = RecommendedP0Presentation(self)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.scan)

    def start(self):
        self._running = True
        self._recommended_p0.start()
        self.scan()
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self._running = False
        self._recommended_p0.stop()
        self.timer.stop()
        for key in tuple(self._bindings):
            self._detach(key)

    def _canvas_crs(self):
        return QgsCoordinateReferenceSystem(self.iface.mapCanvas().mapSettings().destinationCrs())

    @staticmethod
    def _fields(dialog):
        return dialog.txtX.text(), dialog.txtY.text()

    def _context(self, binding):
        if not self._running or self._bindings.get(id(binding.dialog)) is not binding:
            raise ValueError('El diálogo de generación de capas ya está cerrado.')
        fields = self._fields(binding.dialog)
        x, y = map(_number, fields)
        crs = QgsCoordinateReferenceSystem(binding.input_crs)
        if binding.exact_point is not None and binding.exact_fields == fields:
            x, y, point_crs = binding.exact_point
            crs = QgsCoordinateReferenceSystem(point_crs)
        if not crs.isValid():
            raise ValueError('El punto de cálculo no tiene un sistema de referencia válido.')
        dem, directions = _layer_paths(binding.dialog)
        return RationalTerrainQuery(x, y, crs, dem, directions)

    def _coordinates_changed(self, binding):
        binding.exact_point = binding.exact_fields = None
        binding.input_crs = self._canvas_crs()
        self._invalidate_basin(binding, 'Las coordenadas han cambiado. Vuelve a calcular las variables intermedias.')
        self._refresh(binding)

    def _point_clicked(self, binding, point, *unused):
        try:
            x, y = float(point.x()), float(point.y())
            entered = tuple(map(_number, self._fields(binding.dialog)))
            # The native slot runs first and rounds the click to one decimal.
            if not all(math.isfinite(value) for value in (x, y)) or any(
                    abs(a-b) > .051 for a, b in zip(entered, (x, y))):
                return
            binding.input_crs = self._canvas_crs()
            binding.exact_point = (x, y, QgsCoordinateReferenceSystem(binding.input_crs))
            binding.exact_fields = self._fields(binding.dialog)
            self._refresh(binding)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return

    def _refresh(self, binding):
        if binding.panel is not None:
            binding.panel.refresh_context()

    @staticmethod
    def _source_signature(dialog):
        result = []
        for path in _layer_paths(dialog):
            info = Path(path).stat()
            result.append((path, info.st_size, info.st_mtime_ns))
        return tuple(result)

    @staticmethod
    def _basin_stamp(dialog):
        calculator = getattr(dialog, 'basinCalc', None)
        point = getattr(calculator, 'pt', None)
        mask = getattr(calculator, 'basinCells', None)
        geometries = getattr(calculator, 'basinGeometry', None)
        if calculator is None or point is None or mask is None or geometries is None:
            raise ValueError('Todavía no hay una cuenca calculada por Método racional.')
        x, y = float(point.x()), float(point.y())
        entered = tuple(map(_number, (dialog.txtX.text(), dialog.txtY.text())))
        if not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-7) for a, b in zip(entered, (x, y))):
            raise ValueError('El punto actual no coincide con la cuenca calculada. Recalcula las variables intermedias.')
        area = float(calculator.area)
        if not math.isfinite(area) or not 0 < area <= 50e6:
            raise ValueError('La cuenca calculada debe tener una superficie positiva de hasta 50 km².')
        return (id(calculator), id(mask), id(geometries), x, y, area,
                tuple(getattr(mask, 'shape', ())), tuple(calculator.geoTransform))

    def _flow_signature(self, binding):
        dialog = binding.dialog
        fields = tuple((name, getattr(dialog, name).text()) for name in _FLOW_INPUTS
                       if callable(getattr(getattr(dialog, name, None), 'text', None)))
        period = getattr(dialog, 'comboReturnPeriod', None)
        ordinary = getattr(dialog, 'chkOrdinaryMaxFlood', None)
        return (binding.generation, self._fields(dialog), self._source_signature(dialog), fields,
                period.currentText() if period is not None else None,
                ordinary.isChecked() if ordinary is not None else None)

    def _notify_query_changed(self, binding):
        if binding.query_opened:
            binding.query_opened = False
            if self.on_query_started is not None:
                self.on_query_started(binding.dialog)

    def _invalidate_flow(self, binding):
        binding.flow_result = binding.flow_signature = None
        self._notify_query_changed(binding)

    def _invalidate_basin(self, binding, message):
        binding.basin_ready = binding.parameters_validated = False
        binding.basin_stamp = binding.source_signature = None
        binding.generation += 1
        binding.basin_message = message
        self._invalidate_flow(binding)
        if binding.basin_button is not None:
            binding.basin_button.setEnabled(False)
            binding.basin_button.setToolTip(message)

    def _hydrology_changed(self, binding):
        if not binding.calculating:
            self._invalidate_flow(binding)
            self._sync_basin(binding)

    def _accept_parameters(self, binding, *, observed):
        button = getattr(binding.dialog, 'btnCalculate', None)
        if button is None or not button.isEnabled():
            raise ValueError('Primero completa el cálculo de variables intermedias en Método racional.')
        binding.basin_stamp = self._basin_stamp(binding.dialog)
        binding.source_signature = self._source_signature(binding.dialog)
        binding.basin_ready = True
        binding.parameters_validated = observed
        binding.basin_message = ('Abrir la cuenca delineada por Método racional. '
                                 'El caudal queda pendiente hasta completar su cálculo.')

    def _sync_basin(self, binding):
        calculate = getattr(binding.dialog, 'btnCalculate', None)
        binding.frequency_button.setEnabled(self.on_frequency_requested is not None
            and calculate is not None and calculate.isEnabled() and not binding.calculating)
        if binding.calculating:
            binding.basin_button.setEnabled(False)
            return
        if binding.basin_ready:
            try:
                if (self._source_signature(binding.dialog) != binding.source_signature
                        or self._basin_stamp(binding.dialog) != binding.basin_stamp):
                    raise ValueError('La cuenca o las capas de cálculo han cambiado. Recalcula las variables intermedias.')
                if binding.flow_result is not None and self._flow_signature(binding) != binding.flow_signature:
                    self._invalidate_flow(binding)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
                self._invalidate_basin(binding, str(error))
        binding.basin_button.setEnabled(binding.basin_ready and self.on_basin_requested is not None)
        binding.basin_button.setToolTip(binding.basin_message)

    def calculation_started(self, dialog, stage):
        """Receive the reversible native-calculation observer's start signal."""
        binding = self._bindings.get(id(dialog))
        if binding is None or not self._running:
            return
        if stage == 'parameters':
            self._invalidate_basin(binding, 'Calculando las variables intermedias de la cuenca…')
            try:
                binding.parameter_start = (binding.generation, self._fields(dialog), self._source_signature(dialog))
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                binding.parameter_start = None
        elif stage == 'flow':
            self._invalidate_flow(binding)
            try:
                self._sync_basin(binding)
                binding.flow_start = self._flow_signature(binding) if binding.basin_ready else None
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                binding.flow_start = None
        else:
            return
        binding.calculating.add(stage)
        binding.basin_button.setEnabled(False)
        binding.frequency_button.setEnabled(False)

    def calculation_finished(self, dialog, stage, event):
        binding = self._bindings.get(id(dialog))
        if binding is None or not self._running:
            return
        binding.calculating.discard(stage)
        try:
            if stage == 'parameters':
                current = (binding.generation, self._fields(dialog), self._source_signature(dialog))
                if (event.get('completed') and event.get('ready_writes')
                        and event['ready_writes'][-1] is True and current == binding.parameter_start):
                    self._accept_parameters(binding, observed=True)
                else:
                    self._invalidate_basin(binding, 'El cálculo de variables intermedias no se ha completado. Vuelve a calcularlo.')
                binding.parameter_start = None
            elif stage == 'flow':
                signature = self._flow_signature(binding)
                flow_writes, period_writes = event.get('flow_writes', []), event.get('period_writes', [])
                if (binding.basin_ready and event.get('completed') and flow_writes and period_writes
                        and signature == binding.flow_start
                        and str(flow_writes[-1]) == dialog.txtResultFlow.text()
                        and str(period_writes[-1]) == dialog.txtResultReturnPeriod.text()):
                    flow, period = _number(flow_writes[-1]), _number(period_writes[-1])
                    if flow >= 0 and 1 < period <= 500:
                        binding.flow_result = dict(flow=flow, period=period)
                        binding.flow_signature = signature
                binding.flow_start = None
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            if stage == 'parameters':
                self._invalidate_basin(binding, str(error))
            else:
                self._invalidate_flow(binding)
        self._sync_basin(binding)
        # CAUMAX intentionally blocks textChanged on I1/Id and intensity.
        # Their numeric values still change, so refresh the comma overlay.
        if binding.decimal_presentation is not None:
            binding.decimal_presentation.sync()

    def _request_basin(self, binding):
        if not self._running or self._bindings.get(id(binding.dialog)) is not binding:
            return
        self._sync_basin(binding)
        if not binding.basin_ready or binding.calculating or self.on_basin_requested is None:
            return
        try:
            from .rational_basin import capture_rational_query
            query = capture_rational_query(binding.dialog, flow_result=binding.flow_result,
                                           parameters_validated=binding.parameters_validated)
            self._sync_basin(binding)
            if not binding.basin_ready:
                raise ValueError(binding.basin_message)
            self.on_basin_requested(binding.dialog, query)
            self._compact_native_layout(binding)
            binding.query_opened = True
            binding.error.hide()
        except Exception as error:
            binding.error.setText('No se pudo abrir la cuenca de Método racional: ' + str(error))
            binding.error.show()

    def scan(self):
        if not self._running:
            return
        for key, binding in tuple(self._bindings.items()):
            try:
                self._group_save_button(binding)
                self._sync_basin(binding)
                if binding.decimal_presentation is not None:
                    binding.decimal_presentation.sync()
                if binding.layers_dialog is not None and binding.layers_dialog.isVisible():
                    self._refresh(binding)
            except RuntimeError:
                self._detach(key)
        for dialog in QApplication.topLevelWidgets():
            try:
                if (not isinstance(dialog, QDialog) or not dialog.isVisible()
                        or _native_module(dialog) is None or id(dialog) in self._bindings
                        or not isinstance(dialog.layout(), QVBoxLayout)
                        or not all(hasattr(dialog, name) for name in ('horizontalLayout_2', 'txtX', 'txtY'))
                        or dialog.findChild(QPushButton, BUTTON_NAME) is not None):
                    continue
                self._attach(dialog)
            except (AttributeError, RuntimeError, TypeError):
                continue

    def _attach(self, dialog):
        native_height = dialog.height()
        native_width = dialog.width()
        container = QWidget(dialog)
        container.setObjectName(PANEL_NAME)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(container)
        frame.setObjectName(ACTION_FRAME_NAME)
        frame.setStyleSheet(f'QFrame#{ACTION_FRAME_NAME} {{ border: 1px solid #7EB4D0; '
                           'border-radius: 3px; background-color: #F2F9FD; }}')
        row = QHBoxLayout(frame)
        row.setContentsMargins(5, 4, 5, 4)
        row.setSpacing(4)
        logo = QLabel(frame)
        logo.setObjectName('HidroCAUMAXSharedGEALogo')
        logo.setToolTip('GEA · Universidad de Valladolid')
        pixmap = QPixmap(str(LOGO_PATH))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(24, Qt.SmoothTransformation))
        logo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row.addWidget(logo, 0, Qt.AlignVCenter)
        frequency_button = QPushButton('Calcular', frame)
        frequency_button.setObjectName(FREQUENCY_BUTTON_NAME)
        frequency_button.setStyleSheet(ACTION_BUTTON_STYLE)
        frequency_button.setToolTip('Calcular y mostrar la tabla y el gráfico de caudales '
                                   'para varios periodos de retorno.')
        frequency_button.setEnabled(False)
        row.addWidget(frequency_button, 0, Qt.AlignLeft)
        basin_button = QPushButton('Cuenca hidrográfica', frame)
        basin_button.setObjectName(BASIN_BUTTON_NAME)
        basin_button.setStyleSheet(ACTION_BUTTON_STYLE)
        basin_button.setEnabled(False)
        row.addWidget(basin_button, 0, Qt.AlignLeft)
        button = QPushButton('Generar capas resolución mejorada', frame)
        button.setObjectName(BUTTON_NAME)
        button.setStyleSheet(ACTION_BUTTON_STYLE)
        button.setToolTip('Abrir en una ventana independiente las opciones de generación para el punto actual.')
        row.addWidget(button, 0, Qt.AlignLeft)
        row.addStretch()
        from .clean_layers_ui import clean_button
        clean = clean_button(frame)
        clean.setObjectName(CLEAN_BUTTON_NAME)
        row.addWidget(clean, 0, Qt.AlignRight)
        layout.addWidget(frame)
        error = QLabel(container)
        error.setTextFormat(Qt.PlainText)
        error.setWordWrap(True)
        error.hide()
        layout.addWidget(error)
        # Do not enlarge or rearrange the native CAUMAX window: the full D8
        # section belongs to its own independently resizable dialog.
        dialog.layout().addWidget(container)
        binding = _Binding(dialog, container, button, None, error, self._canvas_crs())
        from .native_decimal import RationalDecimalPresentation
        binding.decimal_presentation = RationalDecimalPresentation(dialog)
        binding.basin_button, binding.action_row = basin_button, row
        binding.frequency_button = frequency_button
        binding.clean_button = clean
        self._group_save_button(binding)
        binding.native_height = native_height
        binding.native_width = native_width
        key = id(dialog)
        self._bindings[key] = binding
        self._connect(binding, frequency_button.clicked,
                      lambda checked=False: self._request_frequency(binding))
        self._connect(binding, button.clicked, lambda checked=False: self._open_layers_dialog(binding))
        self._connect(binding, basin_button.clicked, lambda checked=False: self._request_basin(binding))
        self._connect(binding, clean.clicked, lambda checked=False: self._request_cleanup(binding))
        for field_widget in (dialog.txtX, dialog.txtY):
            self._connect(binding, field_widget.textChanged, lambda *unused: self._coordinates_changed(binding))
        for name in _FLOW_INPUTS:
            signal = getattr(getattr(dialog, name, None), 'textChanged', None)
            if signal is not None:
                self._connect(binding, signal, lambda *unused: self._hydrology_changed(binding))
        for name, signal_name in (('comboReturnPeriod', 'currentTextChanged'), ('chkOrdinaryMaxFlood', 'toggled')):
            signal = getattr(getattr(dialog, name, None), signal_name, None)
            if signal is not None:
                self._connect(binding, signal, lambda *unused: self._hydrology_changed(binding))
        canvas_signal = getattr(self.iface.mapCanvas(), 'destinationCrsChanged', None)
        if canvas_signal is not None:
            self._connect(binding, canvas_signal, lambda *unused: self._refresh(binding))
        click_signal = getattr(getattr(dialog, 'tool', None), 'canvasClicked', None)
        if click_signal is not None:
            self._connect(binding, click_signal, lambda point, *rest: self._point_clicked(binding, point, *rest))
        closed = lambda *unused: self._detach(key)
        self._connect(binding, dialog.finished, closed)
        self._connect(binding, dialog.destroyed, closed)
        try:
            self._accept_parameters(binding, observed=False)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        self._sync_basin(binding)
        # Let Qt refresh the nested group boxes' cached minimum sizes before
        # the queued height fit; fitting in the same turn keeps empty space.
        self._compact_native_layout(binding)
        QTimer.singleShot(0, lambda: self._fit_native_height(binding))

    @staticmethod
    def _group_save_button(binding):
        button = binding.dialog.findChild(QPushButton, SAVE_BUTTON_NAME)
        # A foreign or historical standalone export controller stays untouched.
        if button is not None and button.property(EXPORT_OWNER_PROPERTY):
            _place_export_button(binding.dialog, button)

    def _request_frequency(self, binding):
        if (self._running and self._bindings.get(id(binding.dialog)) is binding
                and self.on_frequency_requested is not None and not binding.calculating):
            self.on_frequency_requested(binding.dialog)

    def _request_cleanup(self, binding):
        from .clean_layers_ui import confirm_cleanup
        message = confirm_cleanup(binding.dialog)
        if message:
            binding.error.setText(message)
            binding.error.show()

    def _fit_native_height(self, binding):
        if not self._running or self._bindings.get(id(binding.dialog)) is not binding:
            return
        try:
            self._compact_native_layout(binding)
            dialog = binding.dialog
            dialog.layout().activate()
            # Native expanding spacers have a large preferred height; their
            # minimum excludes that empty space without changing any widget.
            height = max(1, dialog.minimumSizeHint().height())
            dialog.resize(binding.native_width, height)
            binding.fitted_height = dialog.height()
            binding.fitted_width = dialog.width()
        except RuntimeError:
            pass

    @staticmethod
    def _connect(binding, signal, slot):
        signal.connect(slot)
        binding.connections.append((signal, slot))

    def _compact_native_layout(self, binding):
        """Reduce vertical whitespace of native CAUMAX layouts, reversibly.

        Do not change the native widgets' input values, font sizes or heights.
        Only inherited layout spacing/margins change, and everything is restored
        when the native dialog is detached.
        """
        if binding.native_compact_layouts:
            return
        root = binding.dialog.layout()
        seen = set()

        def visit(layout):
            if layout is None or id(layout) in seen:
                return
            seen.add(id(layout))
            m = layout.contentsMargins()
            margins = (m.left(), m.top(), m.right(), m.bottom())
            binding.native_compact_layouts.append((layout, layout.spacing(), margins))
            layout.setSpacing(min(3, max(0, layout.spacing())))
            if layout is not root:
                layout.setContentsMargins(margins[0], min(margins[1], 2),
                                          margins[2], min(margins[3], 2))
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget()
                if widget is binding.container:
                    continue
                child = item.layout() or (widget.layout() if widget is not None else None)
                if child is not None:
                    visit(child)

        visit(binding.body_host.layout() if binding.body_host is not None else root)
        if binding.body_host is not None:
            visit(root)
        root.invalidate()
        root.activate()

    def _restore_native_layout(self, binding):
        for layout, spacing, margins in reversed(binding.native_compact_layouts):
            try:
                layout.setSpacing(spacing)
                layout.setContentsMargins(*margins)
            except RuntimeError:
                pass
        binding.native_compact_layouts.clear()

    def _notify_basin_refined(self, binding, key, analysis, query):
        if self._bindings.get(id(binding.dialog)) is not binding:
            return
        # CAUMAX retains its native red polygon after a fine delineation.
        # Temporarily hide it so QGIS shows the selected improved watershed,
        # not the superposition of a coarse and a fine basin. Preserve the
        # user's native visibility and restore it on returning to 500 m.
        original = getattr(binding.dialog, 'rubberBand', None)
        try:
            if (original is not None and callable(getattr(original, 'setVisible', None))
                    and callable(getattr(original, 'isVisible', None))):
                if str(key) == '500':
                    self._restore_native_outline(binding)
                else:
                    if binding.hidden_native_outline is not original:
                        self._restore_native_outline(binding)
                        binding.hidden_native_outline = original
                        binding.native_outline_visibility = bool(original.isVisible())
                    original.setVisible(False)
        except RuntimeError:
            binding.hidden_native_outline = binding.native_outline_visibility = None
        if self.on_basin_refined is not None:
            self.on_basin_refined(binding.dialog, key, analysis, query)

    @staticmethod
    def _restore_native_outline(binding):
        original, previous = binding.hidden_native_outline, binding.native_outline_visibility
        binding.hidden_native_outline = binding.native_outline_visibility = None
        if original is not None and previous is not None:
            try:
                original.setVisible(bool(previous))
            except RuntimeError:
                pass

    def _open_layers_dialog(self, binding):
        """Open the complete D8 tools in a separate, non-modal QGIS window."""
        if not self._running or self._bindings.get(id(binding.dialog)) is not binding:
            return
        from .improved_layers import remove_improved_folder_group
        remove_improved_folder_group()
        existing = binding.layers_dialog
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                binding.layers_dialog = binding.panel = binding.scroll = None
        window = None
        try:
            # Qt.Window gives a real floating dialog even with a QDialog parent.
            # Non-modal is intentional: users must be able to inspect the QGIS
            # map and use the manual outlet-selection tools while it is open.
            from .rational_layers_dialog import RationalLayersDialog
            window = RationalLayersDialog(binding.dialog, Qt.Window)
            window.setObjectName('HidroCAUMAXRationalLayersDialog')
            window.setWindowTitle('HidroCAUMAX · Generación capas resolución mejorada')
            window.setModal(False)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            layout = QVBoxLayout(window)
            layout.setContentsMargins(9, 9, 9, 9)
            scroll = QScrollArea(window)
            scroll.setObjectName('HidroCAUMAXRationalLayersScroll')
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            from .rational_layers_panel import RationalLayersPanel
            panel = RationalLayersPanel(
                self.iface, lambda: self._context(binding), scroll,
                on_basin_refined=lambda key, analysis, query:
                    self._notify_basin_refined(binding, key, analysis, query))
            scroll.setWidget(panel)
            layout.addWidget(scroll, 1)
            footer = QHBoxLayout()
            footer.addStretch(1)
            close_button = QPushButton('Cerrar', window)
            close_button.setObjectName('HidroCAUMAXRationalLayersClose')
            close_button.clicked.connect(window.close)
            footer.addWidget(close_button)
            layout.addLayout(footer)
            binding.layers_dialog, binding.scroll, binding.panel = window, scroll, panel
            window.finished.connect(lambda *unused: self._layers_closed(binding, window))
            window.fit_content(panel, scroll)
            binding.error.hide()
            window.show()
            self._refresh(binding)
        except Exception as error:
            if window is not None:
                try:
                    window.close()
                except RuntimeError:
                    pass
            if binding.panel is not None:
                self._layers_closed(binding, binding.layers_dialog)
            binding.error.setText('No se pudo abrir la generación de capas: ' + str(error))
            binding.error.show()

    @staticmethod
    def _layers_closed(binding, window):
        """Cancel workers and release the panel whenever its window closes."""
        if binding.layers_dialog is not window:
            return
        panel = binding.panel
        binding.layers_dialog = binding.scroll = binding.panel = None
        if panel is not None:
            try:
                panel.dispose()
            except RuntimeError:
                pass

    def _detach(self, key):
        binding = self._bindings.pop(key, None)
        if binding is None:
            return
        if binding.decimal_presentation is not None:
            binding.decimal_presentation.dispose()
        for signal, slot in binding.connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if binding.layers_dialog is not None:
            try:
                binding.layers_dialog.close()
            except RuntimeError:
                pass
        if binding.panel is not None:
            self._layers_closed(binding, binding.layers_dialog)
        try:
            restore_height = binding.dialog.height() == binding.fitted_height
            restore_width = binding.dialog.width() == binding.fitted_width
            button = binding.container.findChild(QPushButton, SAVE_BUTTON_NAME)
            if button is not None and button.property(EXPORT_OWNER_PROPERTY):
                _place_export_button(binding.dialog, button, grouped=False)
            self._restore_native_layout(binding)
            self._restore_native_outline(binding)
            binding.dialog.layout().removeWidget(binding.container)
            binding.container.setParent(None)
            binding.container.deleteLater()
            if restore_height and binding.native_height is not None:
                binding.dialog.layout().activate()
                binding.dialog.resize(binding.native_width if restore_width else binding.dialog.width(),
                                      binding.native_height)
        except RuntimeError:
            pass
