"""Plugin entry point: extend CAUMAX's own dialog, not QGIS identify."""
from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox
from pathlib import Path

from .caumax_bridge import CaumaxBridge
from .basin_dialog import BasinDialog
from .rational_export import RationalExportBridge
from .rational_inputs import RationalInputsBridge
from .rational_layers_bridge import RationalLayersBridge
from .visible_dem import VisibleDemController
from .update_ui import UpdateNotifier
from .delineation_cleanup import clear_delineations
from .map_services import ReferenceLayerLoader


class NativeCaumaxPlugin(QObject):
    MENU = 'HidroCAUMAX'

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self._dialogs = {}
        self._saved_dialogs = []
        self._enabled = False
        self._bridge = CaumaxBridge(iface, self._open_basin, self._new_query_started)
        self._rational = RationalExportBridge(iface)
        self._rational_inputs = RationalInputsBridge(iface)
        self._rational_layers = RationalLayersBridge(iface, self._open_basin, self._new_query_started)
        self._rational_inputs.calculation_started.connect(self._rational_layers.calculation_started)
        self._rational_inputs.calculation_finished.connect(self._rational_layers.calculation_finished)
        self._visible_dem = VisibleDemController(iface, self)
        self._updates = UpdateNotifier(iface, self)
        self._map_services = ReferenceLayerLoader(iface, self)

    def initGui(self):
        self.action = QAction('Activar herramientas HidroCAUMAX', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self.set_enabled)
        self.iface.addPluginToMenu(self.MENU, self.action)
        self.open_action = QAction('Abrir proyecto de cuenca…', self.iface.mainWindow())
        self.open_action.triggered.connect(self._open_saved_project)
        self.iface.addPluginToMenu(self.MENU, self.open_action)
        self.reference_action = QAction('Cargar capas HidroCAUMAX', self.iface.mainWindow())
        self.reference_action.triggered.connect(self._map_services.load)
        self._map_services.busy_changed.connect(self.reference_action.setDisabled)
        self.iface.addPluginToMenu(self.MENU, self.reference_action)
        self.update_action = QAction('Buscar actualizaciones…', self.iface.mainWindow())
        self.update_action.triggered.connect(self._check_updates)
        self.iface.addPluginToMenu(self.MENU, self.update_action)
        self.set_enabled(True)

    def _check_updates(self, *args):
        self._updates.check(manual=True)

    def _open_saved_project(self):
        self._updates.check()
        source, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(), 'Abrir proyecto de consulta', str(Path.home()),
            'Proyecto de consulta CAUMAX (*.caumax.json);;Archivos JSON (*.json)')
        if not source:
            return
        try:
            from .session_store import load_session
            saved = load_session(source)
            dialog = BasinDialog(saved.query, self.iface, self.iface.mainWindow(), saved_session=saved)
            self._saved_dialogs.append(dialog)
            dialog.finished.connect(lambda result, current=dialog: self._forget_saved(current))
            dialog.show()
            dialog.raise_()
        except Exception as error:
            QMessageBox.critical(self.iface.mainWindow(), 'Abrir proyecto', str(error))

    def _forget_saved(self, dialog):
        if dialog in self._saved_dialogs:
            self._saved_dialogs.remove(dialog)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if hasattr(self, 'action') and self.action.isChecked() != enabled:
            self.action.setChecked(enabled)
        self._enabled = enabled
        if enabled:
            self._bridge.start()
            self._rational.start()
            self._rational_inputs.start()
            self._rational_layers.start()
            self._visible_dem.start()
            self._updates.check()
        else:
            self._updates.cancel()
            self._close_dialogs()
            self._bridge.stop()
            self._rational.stop()
            self._rational_inputs.stop()
            self._rational_layers.stop()
            self._visible_dem.stop()

    @staticmethod
    def _key(query):
        context = getattr(query, 'rational_context', None) or {}
        return (getattr(query, 'source_kind', 'caumax_raster'), context.get('fingerprint'),
                query.x, query.y, query.period, query.flow,
                str(query.directions_path), str(query.dem_path), query.crs.toWkt())

    def _new_query_started(self, native_dialog):
        if self._enabled:
            clear_delineations(close_dialogs=True)

    def _open_basin(self, native_dialog, query):
        if not self._enabled:
            return
        self._updates.check()
        previous = self._dialogs.get(native_dialog)
        if previous is not None:
            try:
                if self._key(getattr(previous, '_native_query', previous.query)) == self._key(query):
                    previous.show()
                    previous.raise_()
                    previous.activateWindow()
                    return
            except RuntimeError:
                self._dialogs.pop(native_dialog, None)
        # Also cover layers from closed dialogs or a restored QGIS project.
        # Dispose live computations before deleting their published results.
        # Cleanup owns only HidroCAUMAX results and MaxFlowDialog's tracked
        # marker. A rational dialog's authoritative rubberBand/basinCalc is
        # deliberately neither registered nor removed here.
        clear_delineations(close_dialogs=True)
        dialog = BasinDialog(query, self.iface, native_dialog)
        dialog._native_query = query
        self._dialogs[native_dialog] = dialog
        dialog.finished.connect(lambda result, parent=native_dialog, current=dialog: self._forget(parent, current))
        dialog.destroyed.connect(lambda *args, parent=native_dialog, current=dialog: self._forget(parent, current))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _forget(self, parent, dialog):
        if self._dialogs.get(parent) is dialog:
            self._dialogs.pop(parent, None)

    def _close_dialogs(self):
        dialogs = list(self._dialogs.values()) + list(self._saved_dialogs)
        self._dialogs.clear()
        self._saved_dialogs.clear()
        for dialog in dialogs:
            try:
                dialog.dispose()
                dialog.close()
            except RuntimeError:
                pass

    def unload(self):
        self._enabled = False
        self._map_services.dispose()
        self._updates.dispose()
        self._close_dialogs()
        self._bridge.stop()
        self._rational.stop()
        self._rational_inputs.stop()
        self._rational_layers.stop()
        self._visible_dem.stop()
        if hasattr(self, 'action'):
            try:
                self.action.toggled.disconnect(self.set_enabled)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.action)
            self.action.deleteLater()
        if hasattr(self, 'open_action'):
            try:
                self.open_action.triggered.disconnect(self._open_saved_project)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.open_action)
            self.open_action.deleteLater()
        if hasattr(self, 'update_action'):
            try:
                self.update_action.triggered.disconnect(self._check_updates)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.update_action)
            self.update_action.deleteLater()
        if hasattr(self, 'reference_action'):
            try:
                self.reference_action.triggered.disconnect(self._map_services.load)
                self._map_services.busy_changed.disconnect(self.reference_action.setDisabled)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.reference_action)
            self.reference_action.deleteLater()
