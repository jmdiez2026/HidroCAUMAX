"""Observe native identify results without replacing the QGIS map tool.

QGIS 3.34--3.44 keeps the actual QgsMapLayer QObject in Qt.UserRole on
top-level items of IdentifyResultsDock/lstResults. These internal object
names are checked at runtime; layer names and translated value text are
never used to decide whether a layer is a CAUMAX flow raster.
"""

from dataclasses import dataclass
import math
import os
import sys

from qgis.PyQt.QtCore import QObject, QPoint, QEvent, Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QFormLayout, QLabel, QLineEdit, QTreeWidget, QVBoxLayout,
)
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsPointXY, QgsProject, QgsProviderRegistry, QgsRasterLayer,
)

from .grid import cell_at_point


PERIODS = (2, 5, 10, 25, 100, 500)


def _normal_path(value):
    """Normalize a local dataset path, preserving case on case-sensitive hosts."""
    if not isinstance(value, (str, bytes, os.PathLike)):
        return None
    value = os.fsdecode(value).split("|", 1)[0]
    if not value:
        return None
    return os.path.normcase(os.path.realpath(os.path.expanduser(value)))


def configured_flow_paths():
    """Return {normalized path: T}, using already loaded CAUMAX configuration.

    An ambiguous mapping (one dataset assigned to different periods) is
    excluded rather than inventing a period. This does not import or activate
    another plugin, change settings, or identify files by their basename.
    """
    candidates = {}
    for name, module in tuple(sys.modules.items()):
        if module is None or not name.endswith(".layers"):
            continue
        get_path = getattr(module, "getLayerPath", None)
        if not callable(get_path) or not all(
            hasattr(module, "FLOW_%s" % period) for period in PERIODS
        ):
            continue
        for period in PERIODS:
            try:
                path = _normal_path(get_path(getattr(module, "FLOW_%s" % period)))
            except (KeyError, TypeError, ValueError, OSError, RuntimeError):
                continue
            if path:
                candidates.setdefault(path, set()).add(period)
    return {path: next(iter(values)) for path, values in candidates.items() if len(values) == 1}


def flow_period(layer, paths=None):
    """Return T only for an exact configured CAUMAX raster dataset path."""
    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return None
    paths = configured_flow_paths() if paths is None else paths
    try:
        exact = paths.get(_normal_path(layer.source()))
        if exact is not None:
            return exact
        uri = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source())
        source = uri.get("path", layer.source())
        if uri.get("vsiPrefix"):
            source = str(uri["vsiPrefix"]) + str(source)
        return paths.get(_normal_path(source))
    except (RuntimeError, TypeError, ValueError, OSError):
        return None


@dataclass(frozen=True)
class Query:
    period: int
    flow: object
    cell: object
    layername: str
    layer_id: str
    crs: QgsCoordinateReferenceSystem
    error: str = ""


def read_query(layer, period, map_point, map_crs):
    """Sample band 1 at the exact cell centre, after map-to-raster transform.

    Invalid or NoData values remain None. Query.cell is None if locating the
    cell failed; errors are shown without substituting a zero flow value.
    """
    crs = QgsCoordinateReferenceSystem(layer.crs())
    cell = None
    value = None
    error = ""
    try:
        if not crs.isValid() or not map_crs.isValid():
            raise ValueError("No se puede localizar la celda: sistema de referencia no válido.")
        point = QgsPointXY(map_point)
        if map_crs != crs:
            point = QgsCoordinateTransform(
                map_crs, crs, QgsProject.instance().transformContext()
            ).transform(point)
        extent = layer.extent()
        cell = cell_at_point(
            point.x(), point.y(), extent.xMinimum(), extent.yMinimum(),
            extent.xMaximum(), extent.yMaximum(), layer.width(), layer.height(),
        )
        sample, valid = layer.dataProvider().sample(QgsPointXY(cell.x, cell.y), 1)
        if valid and sample is not None and math.isfinite(float(sample)):
            value = float(sample)
        else:
            error = "Sin datos en esta celda."
    except Exception as exc:
        # Projection/provider errors should not abort QGIS's native identify.
        error = str(exc) or "No se pudo consultar esta celda."
    return Query(period, value, cell, layer.name(), layer.id(), crs, error)


class QueryDialog(QDialog):
    """Non-modal, read-only results; all values belong to the selected query."""

    def __init__(self, queries, parent=None):
        super().__init__(parent)
        self.setObjectName("CaumaxConsultaCeldaDialog")
        self.setWindowTitle("CAUMAX · Caudal y cuenca vertiente")
        self.setModal(False)
        self.queries = tuple(queries)
        self.combo = QComboBox(self)
        self.combo.setObjectName("comboCapasCAUMAX")
        for query in self.queries:
            self.combo.addItem("%s · T = %s años" % (query.layername, query.period))
        self.combo.setVisible(len(self.queries) > 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.combo)
        form = QFormLayout()
        self.fields = {}
        for key, label in (
            ("layer", "Capa consultada"), ("flow", "Caudal"),
            ("period", "Periodo de retorno"), ("x", "Centro de celda · X"),
            ("y", "Centro de celda · Y"), ("crs", "Sistema de referencia"),
            ("row", "Fila (desde 1)"), ("col", "Columna (desde 1)"),
        ):
            field = QLineEdit(self)
            field.setObjectName("resultado_" + key)
            field.setReadOnly(True)
            self.fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel("El punto de cierre es el centro de la celda consultada.", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self._show_query)
        self._show_query(0)
        self.resize(570, self.sizeHint().height())

    @property
    def query(self):
        return self.queries[self.combo.currentIndex()]

    def _show_query(self, index):
        if not (0 <= index < len(self.queries)):
            return
        query = self.queries[index]
        values = {
            "layer": query.layername,
            "flow": "Sin datos" if query.flow is None else "%.10g m³/s" % query.flow,
            "period": "%s años" % query.period,
            "crs": "%s · %s" % (query.crs.authid(), query.crs.description()),
            "x": "—", "y": "—", "row": "—", "col": "—",
        }
        if query.cell is not None:
            values.update(x="%.8f" % query.cell.x, y="%.8f" % query.cell.y,
                          row=str(query.cell.row + 1), col=str(query.cell.col + 1))
        for key, value in values.items():
            self.fields[key].setText(value)
        self.status.setText(query.error)
        self.status.setVisible(bool(query.error))


class CaumaxConsultaPlugin(QObject):
    MENU = "CAUMAX auxiliar"

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.enabled = True
        self._unloaded = False
        self._tree = None
        self._model = None
        self._pending = None
        self._tree_at_click = None
        self._dirty = False
        self._press_pos = None
        self._dialog = None
        self._sequence = 0
        self._discovery_timer = QTimer(self)
        self._discovery_timer.setInterval(300)
        self._discovery_timer.timeout.connect(self._scan_tree)
        self._after_timer = QTimer(self)
        self._after_timer.setSingleShot(True)
        self._after_timer.timeout.connect(self._after_identify)
        self._read_timer = QTimer(self)
        self._read_timer.setSingleShot(True)
        self._read_timer.timeout.connect(self._read_pending)

    def initGui(self):
        self.action = QAction(
            QgsApplication.getThemeIcon("/mActionIdentify.svg"),
            "Consulta emergente de celdas CAUMAX", self.iface.mainWindow(),
        )
        self.action.setObjectName("actionCaumaxConsultaCelda")
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.setToolTip("Mostrar el caudal CAUMAX al usar Identificar objetos espaciales")
        self.action.toggled.connect(self.set_enabled)
        self.iface.addPluginToMenu(self.MENU, self.action)
        self.iface.addToolBarIcon(self.action)
        self.canvas.viewport().installEventFilter(self)
        self._scan_tree()
        self._discovery_timer.start()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if hasattr(self, "action") and self.action.isChecked() != self.enabled:
            self.action.setChecked(self.enabled)
        if not self.enabled:
            self._discard_pending()
            self._close_dialog()

    def _is_identifying(self):
        return (not self._unloaded and self.enabled
                and self.iface.actionIdentify().isChecked())

    def eventFilter(self, obj, event):
        if obj is not self.canvas.viewport() or not self._is_identifying():
            self._press_pos = None
            return False
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._discard_pending()
            self._close_dialog()
            self._scan_tree()
            self._press_pos = QPoint(event.pos())
        elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            pressed, self._press_pos = self._press_pos, None
            if pressed is None or (event.pos() - pressed).manhattanLength() > QApplication.startDragDistance():
                return False
            self._sequence += 1
            point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos())
            self._pending = (
                self._sequence, QgsPointXY(point),
                QgsCoordinateReferenceSystem(self.canvas.mapSettings().destinationCrs()),
            )
            self._dirty = False
            self._tree_at_click = self._tree
            self._after_timer.start(0)
        return False

    def _detach_tree(self):
        if self._model is not None:
            for signal, slot in (("rowsInserted", self._on_rows_inserted),
                                 ("modelReset", self._on_model_reset)):
                try:
                    getattr(self._model, signal).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self._tree = self._model = None

    def _scan_tree(self):
        if self._unloaded:
            return
        dock = self.iface.mainWindow().findChild(QDockWidget, "IdentifyResultsDock")
        tree = dock.findChild(QTreeWidget, "lstResults") if dock is not None else None
        if tree is self._tree:
            return
        self._detach_tree()
        if tree is None:
            return
        self._tree, self._model = tree, tree.model()
        self._model.rowsInserted.connect(self._on_rows_inserted)
        self._model.modelReset.connect(self._on_model_reset)
        # The dock can be created and fully filled during the first native
        # click, before our post-event callback discovers its model.
        if self._pending is not None and tree is not self._tree_at_click and tree.topLevelItemCount():
            self._dirty = True
            self._read_timer.start(0)

    def _on_rows_inserted(self, *_args):
        if self._pending is not None:
            self._dirty = True
            self._read_timer.start(0)

    def _on_model_reset(self, *_args):
        # Clearing results is not evidence of a new identification result.
        self._dirty = False
        self._read_timer.stop()

    def _after_identify(self):
        if self._pending is None or not self._is_identifying():
            self._discard_pending()
            return
        self._scan_tree()
        if self._dirty:
            self._read_timer.start(0)
        elif QApplication.activePopupWidget() is not None:
            # Native LayerSelection can keep an event loop open until the
            # user selects its layer; do not reuse old tree entries meanwhile.
            self._after_timer.start(100)
        else:
            self._discard_pending()

    def _read_pending(self):
        if self._pending is None or not self._dirty or not self._is_identifying():
            return
        if QApplication.activePopupWidget() is not None:
            self._read_timer.start(100)
            return
        pending = self._pending
        tree = self._tree
        self._discard_pending()
        if tree is None:
            return
        paths = configured_flow_paths()
        queries, seen = [], set()
        for index in range(tree.topLevelItemCount()):
            layer = tree.topLevelItem(index).data(0, Qt.UserRole)
            period = flow_period(layer, paths)
            if period is None or layer.id() in seen:
                continue
            seen.add(layer.id())
            queries.append(read_query(layer, period, pending[1], pending[2]))
        if queries:
            self._close_dialog()
            self._dialog = QueryDialog(queries, self.iface.mainWindow())
            from .watershed import WatershedController
            self._dialog.watershed = WatershedController(self._dialog, self.iface)
            self._dialog.show()
            self._dialog.raise_()

    def _discard_pending(self):
        self._pending = None
        self._dirty = False
        self._press_pos = None
        self._after_timer.stop()
        self._read_timer.stop()

    def _close_dialog(self):
        if self._dialog is not None:
            try:
                controller = getattr(self._dialog, 'watershed', None)
                if controller is not None:
                    controller.dispose()
                self._dialog.close()
                self._dialog.deleteLater()
            except RuntimeError:
                pass
            self._dialog = None

    def unload(self):
        self._unloaded = True
        self._discovery_timer.stop()
        self._discard_pending()
        self._close_dialog()
        self.canvas.viewport().removeEventFilter(self)
        self._detach_tree()
        if hasattr(self, "action"):
            self.action.toggled.disconnect(self.set_enabled)
            self.iface.removePluginMenu(self.MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
