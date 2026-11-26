"""Reversible, dialog-local access to terrain generation in Método racional.

The native calculator and installed CAUMAX files remain untouched. Coordinates
belong to the canvas CRS at entry time; a later canvas CRS change does not
reinterpret the numbers already present in the fields.
"""
from dataclasses import dataclass, field
import math
from pathlib import Path
import sys

from qgis.PyQt.QtCore import QEvent, QObject, QRect, QTimer, Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from qgis.core import QgsCoordinateReferenceSystem

from .rational_export import BUTTON_STYLE
from .caumax_bridge import _LogoBasinButton, BUTTON_STYLE as BASIN_BUTTON_STYLE
from .rational_export import BUTTON_NAME as EXPORT_BUTTON_NAME


BUTTON_NAME = 'btnGenerarCapasResolucionMejoradaHidroCAUMAX'
PANEL_NAME = 'HidroCAUMAXRationalLayersContainer'
BASIN_BUTTON_NAME = 'btnCuencaHidrograficaRacionalHidroCAUMAX'
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
    save_action: object = None
    native_save_button: object = None
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
    """Own only this bridge's controls and temporary body-scroll wrapper."""
    def __init__(self, iface, on_basin_requested=None, on_query_started=None,
                 on_basin_refined=None):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.on_basin_requested = on_basin_requested
        self.on_query_started = on_query_started
        self.on_basin_refined = on_basin_refined
        self._running = False
        self._bindings = {}
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.scan)

    def start(self):
        self._running = True
        self.scan()
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self._running = False
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
                self._sync_basin(binding)
                if binding.save_action is not None:
                    original = binding.native_save_button
                    binding.save_action.setEnabled(original is not None and original.isEnabled())
                if binding.button.isChecked():
                    self._refresh(binding)
                    self._fit_panel(binding)
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
        container = QWidget(dialog)
        container.setObjectName(PANEL_NAME)
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # The existing exporter keeps ownership of its save operation. A
        # proxy action in this row avoids reparenting/deleting the native button.
        native_save = dialog.findChild(QPushButton, EXPORT_BUTTON_NAME)
        save_action = _LogoBasinButton('Guardar cuenca...', container)
        save_action.setObjectName('btnGuardarCuencaRacionalHidroCAUMAX')
        save_action.setStyleSheet(BASIN_BUTTON_STYLE)
        save_action.setToolTip('Guardar la cuenca original calculada por CAUMAX.')
        save_action.setEnabled(native_save is not None and native_save.isEnabled())
        row.addWidget(save_action, 0, Qt.AlignLeft)
        if native_save is not None:
            # Keep the original exporter alive for the GEA-logo proxy, but take
            # its duplicate blue button out of the native action layout.
            # Hiding alone is not sufficient if Qt later re-shows the layout.
            dialog.horizontalLayout_2.removeWidget(native_save)
            native_save.hide()
        basin_button = _LogoBasinButton('Cuenca hidrográfica', container)
        basin_button.setObjectName(BASIN_BUTTON_NAME)
        basin_button.setStyleSheet(BASIN_BUTTON_STYLE)
        basin_button.setEnabled(False)
        basin_button.match_reference_buttons(
            candidate for candidate in (getattr(dialog, 'btnReport', None), getattr(dialog, 'btnClose', None))
            if isinstance(candidate, QPushButton))
        row.addWidget(basin_button, 0, Qt.AlignLeft)
        button = _LogoBasinButton('Generar capas resolución mejorada', container)
        button.setObjectName(BUTTON_NAME)
        button.setStyleSheet(BASIN_BUTTON_STYLE)
        button.setCheckable(True)
        button.setToolTip('Desplegar las opciones de generación para el punto de cálculo actual.')
        row.addWidget(button, 0, Qt.AlignLeft)
        row.addStretch()
        layout.addLayout(row)
        error = QLabel(container)
        error.setTextFormat(Qt.PlainText)
        error.setWordWrap(True)
        error.hide()
        layout.addWidget(error)
        scroll = QScrollArea(container)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scroll.hide()
        layout.addWidget(scroll)
        dialog.layout().addWidget(container)
        binding = _Binding(dialog, container, button, scroll, error, self._canvas_crs())
        from .native_decimal import RationalDecimalPresentation
        binding.decimal_presentation = RationalDecimalPresentation(dialog)
        binding.basin_button, binding.action_row = basin_button, row
        binding.save_action, binding.native_save_button = save_action, native_save
        key = id(dialog)
        self._bindings[key] = binding
        self._connect(binding, button.toggled, lambda checked: self._toggle(binding, checked))
        self._connect(binding, basin_button.clicked, lambda checked=False: self._request_basin(binding))
        if native_save is not None:
            self._connect(binding, save_action.clicked,
                          lambda checked=False, source=native_save: source.click())
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
        dialog.installEventFilter(self)
        try:
            self._accept_parameters(binding, observed=False)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pass
        self._sync_basin(binding)
        for action in (save_action, basin_button, button):
            action.match_reference_buttons(
                candidate for candidate in (getattr(dialog, 'btnReport', None),
                                            getattr(dialog, 'btnClose', None))
                if isinstance(candidate, QPushButton))
            QTimer.singleShot(0, action.install_logo)

    @staticmethod
    def _connect(binding, signal, slot):
        signal.connect(slot)
        binding.connections.append((signal, slot))

    @staticmethod
    def _move_item(item, layout, parent, index=None, stretch=0):
        widget, child_layout = item.widget(), item.layout()
        if widget is not None:
            hidden = widget.isHidden()
            widget.setParent(parent)
            widget.setVisible(not hidden)
        elif child_layout is not None:
            child_layout.setParent(layout)
        if index is None:
            layout.addItem(item)
            layout.setStretch(layout.count()-1, stretch)
        else:
            layout.insertItem(index, item)
            layout.setStretch(index, stretch)

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

    def _wrap_native_body(self, binding):
        root = binding.dialog.layout()
        footer = binding.dialog.horizontalLayout_2
        index = next((i for i in range(root.count()) if root.itemAt(i).layout() is footer), None)
        if index is None or index == 0:
            return
        host = QWidget(binding.dialog)
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(root.spacing())
        for unused in range(index):
            stretch = root.stretch(0)
            item = root.takeAt(0)
            binding.body_items.append((item, stretch))
            self._move_item(item, body, host, stretch=stretch)
        scroll = QScrollArea(binding.dialog)
        scroll.setObjectName('HidroCAUMAXRationalNativeBodyScroll')
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        scroll.setMinimumHeight(0)
        scroll.setWidget(host)
        root.insertWidget(0, scroll, 1)
        binding.body_scroll, binding.body_host = scroll, host

    def _restore_native_body(self, binding):
        if binding.body_scroll is None:
            return
        root, body = binding.dialog.layout(), binding.body_host.layout()
        root.removeWidget(binding.body_scroll)
        for index, (original_item, stretch) in enumerate(binding.body_items):
            item = body.takeAt(0)
            self._move_item(item, root, binding.dialog, index=index, stretch=stretch)
        binding.body_items.clear()
        binding.body_scroll.takeWidget()
        binding.body_host.deleteLater()
        binding.body_scroll.deleteLater()
        binding.body_host = binding.body_scroll = None

    def _toggle(self, binding, checked):
        if not checked:
            self._collapse(binding)
            return
        try:
            if binding.panel is None:
                from .rational_layers_panel import RationalLayersPanel
                binding.panel = RationalLayersPanel(self.iface, lambda: self._context(binding),
                    binding.scroll, on_basin_refined=lambda key, analysis, query:
                    self._notify_basin_refined(binding, key, analysis, query))
                binding.scroll.setWidget(binding.panel)
            binding.error.hide()
            binding.collapsed_geometry = QRect(binding.dialog.geometry())
            self._wrap_native_body(binding)
            binding.scroll.show()
            self._refresh(binding)
            self._fit_panel(binding, expand=True)
        except Exception as error:
            binding.button.setChecked(False)
            binding.error.setText('No se pudo abrir la generación de capas: ' + str(error))
            binding.error.show()

    def _fit_panel(self, binding, expand=False):
        if binding.fitting or binding.panel is None or not binding.button.isChecked():
            return
        binding.fitting = True
        try:
            dialog, scroll = binding.dialog, binding.scroll
            screen = dialog.screen() or QApplication.primaryScreen()
            available = screen.availableGeometry()
            if expand:
                # Recompute the old native minimum before shrinking: its
                # formerly unscrollable body may exceed the entire screen.
                dialog.layout().invalidate()
                dialog.layout().activate()
                height = min(available.height()-24, dialog.height()+max(160, binding.panel.sizeHint().height()))
                width = min(available.width()-24, dialog.width())
                dialog.resize(max(1, width), max(1, height))
                dialog.move(max(available.left()+8, min(dialog.x(), available.right()-dialog.width()-8)),
                            max(available.top()+8, min(dialog.y(), available.bottom()-dialog.height()-8)))
            root = dialog.layout()
            fixed = root.contentsMargins().top()+root.contentsMargins().bottom()
            fixed += root.spacing()*max(0, root.count()-1)
            for index in range(root.count()):
                item = root.itemAt(index)
                if item.widget() not in (binding.container, binding.body_scroll):
                    fixed += item.minimumSize().height()
            fixed += binding.action_row.sizeHint().height()+binding.container.layout().spacing()
            room = max(0, dialog.height()-fixed)
            preferred = max(120, binding.panel.sizeHint().height())
            panel_height = min(preferred, max(0, round(room*.45)))
            scroll.setFixedHeight(panel_height)
            root.activate()
            if expand:
                binding.auto_geometry = QRect(dialog.geometry())
        finally:
            binding.fitting = False

    def _collapse(self, binding):
        binding.scroll.hide()
        restore = (binding.collapsed_geometry is not None and binding.auto_geometry is not None
                   and binding.dialog.geometry() == binding.auto_geometry)
        self._restore_native_body(binding)
        binding.dialog.layout().activate()
        if restore:
            binding.dialog.setGeometry(binding.collapsed_geometry)
        binding.collapsed_geometry = binding.auto_geometry = None

    def eventFilter(self, watched, event):
        binding = self._bindings.get(id(watched))
        if binding is not None and event.type() == QEvent.Resize:
            self._fit_panel(binding)
        return super().eventFilter(watched, event)

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
        if binding.panel is not None:
            try:
                binding.panel.dispose()
            except RuntimeError:
                pass
        try:
            binding.dialog.removeEventFilter(self)
            self._collapse(binding)
            self._restore_native_layout(binding)
            self._restore_native_outline(binding)
            if binding.native_save_button is not None:
                from qgis.PyQt import sip
                if not sip.isdeleted(binding.native_save_button):
                    binding.dialog.horizontalLayout_2.insertWidget(2, binding.native_save_button)
                    binding.native_save_button.show()
            binding.dialog.layout().removeWidget(binding.container)
            binding.container.setParent(None)
            binding.container.deleteLater()
        except RuntimeError:
            pass
