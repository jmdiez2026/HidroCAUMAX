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


BUTTON_NAME = 'btnGenerarCapasResolucionMejoradaHidroCAUMAX'
PANEL_NAME = 'HidroCAUMAXRationalLayersContainer'


@dataclass(frozen=True)
class RationalTerrainQuery:
    x: float
    y: float
    crs: QgsCoordinateReferenceSystem
    dem_path: str
    directions_path: str


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
    collapsed_geometry: object = None
    auto_geometry: object = None
    fitting: bool = False


class RationalLayersBridge(QObject):
    """Own only this bridge's controls and temporary body-scroll wrapper."""
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
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

    def scan(self):
        if not self._running:
            return
        for key, binding in tuple(self._bindings.items()):
            try:
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
        button = QPushButton('Generar capas resolución mejorada', container)
        button.setObjectName(BUTTON_NAME)
        button.setStyleSheet(BUTTON_STYLE)
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
        key = id(dialog)
        self._bindings[key] = binding
        self._connect(binding, button.toggled, lambda checked: self._toggle(binding, checked))
        for field_widget in (dialog.txtX, dialog.txtY):
            self._connect(binding, field_widget.textChanged, lambda *unused: self._coordinates_changed(binding))
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
                binding.panel = RationalLayersPanel(self.iface, lambda: self._context(binding), binding.scroll)
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
            fixed += binding.button.sizeHint().height()+binding.container.layout().spacing()
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
            binding.dialog.layout().removeWidget(binding.container)
            binding.container.setParent(None)
            binding.container.deleteLater()
        except RuntimeError:
            pass
