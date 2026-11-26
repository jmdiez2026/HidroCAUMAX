"""Plugin entry point: extend CAUMAX's own dialog, not QGIS identify."""
from qgis.PyQt.QtCore import QObject, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox
from qgis.core import Qgis, QgsApplication
from pathlib import Path

from .caumax_bridge import CaumaxBridge
from .basin_dialog import BasinDialog
from .rational_export import RationalExportBridge
from .rational_inputs import RationalInputsBridge
from .rational_frequency_dialog import RationalFrequencyBridge
from .rational_layers_bridge import RationalLayersBridge
from .visible_dem import VisibleDemController
from .update_ui import UpdateNotifier
from .delineation_cleanup import clear_delineations
from .map_services import ReferenceLayerLoader
from .native_layers_view import NativeLayersView


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
        self._rational_frequency = RationalFrequencyBridge(iface, self)
        self._rational_layers = RationalLayersBridge(iface, self._open_basin,
            self._new_query_started, self._rational_basin_refined,
            self._rational_frequency.request_calculation)
        self._rational_inputs.calculation_started.connect(self._rational_layers.calculation_started)
        self._rational_inputs.calculation_finished.connect(self._rational_layers.calculation_finished)
        self._rational_inputs.calculation_started.connect(self._rational_frequency.calculation_started)
        self._rational_inputs.calculation_finished.connect(self._rational_frequency.calculation_finished)
        self._visible_dem = VisibleDemController(iface, self)
        self._updates = UpdateNotifier(iface, self)
        self._map_services = ReferenceLayerLoader(iface, self)
        self._native_layers_view = NativeLayersView(iface, self)

    def initGui(self):
        self.action = QAction('Restaurar herramientas HidroCAUMAX', self.iface.mainWindow())
        self.action.triggered.connect(self._activate_tools)
        self.iface.addPluginToMenu(self.MENU, self.action)
        self.open_action = QAction('Abrir proyecto de cuenca…', self.iface.mainWindow())
        self.open_action.triggered.connect(self._open_saved_project)
        self.iface.addPluginToMenu(self.MENU, self.open_action)
        self.reference_action = QAction('Cargar capas HidroCAUMAX', self.iface.mainWindow())
        self.reference_action.triggered.connect(self._map_services.load)
        self._map_services.busy_changed.connect(self.reference_action.setDisabled)
        self.iface.addPluginToMenu(self.MENU, self.reference_action)
        self.clear_layers_action = QAction('Eliminar capas creadas por HidroCAUMAX', self.iface.mainWindow())
        self.clear_layers_action.triggered.connect(self._clear_generated_layers)
        self.iface.addPluginToMenu(self.MENU, self.clear_layers_action)
        self.update_action = QAction('Buscar actualizaciones…', self.iface.mainWindow())
        self.update_action.triggered.connect(self._check_updates)
        self.iface.addPluginToMenu(self.MENU, self.update_action)
        self._create_toolbar()
        self.set_enabled(True)

    def _create_toolbar(self):
        """Share the menu actions so busy and trigger states agree."""
        create = getattr(self.iface, 'addToolBar', None)
        self._toolbar = (create(self.MENU) if callable(create)
                         else self.iface.mainWindow().addToolBar(self.MENU))
        self._toolbar.setObjectName('HidroCAUMAXToolbar')
        self._toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'hidrocaumax.png')))
        self.open_action.setIcon(QgsApplication.getThemeIcon('/mActionFileOpen.svg'))
        self.reference_action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'load_reference_layers.svg')))
        self.clear_layers_action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'clear_generated_layers.svg')))
        self.update_action.setIcon(QgsApplication.getThemeIcon('/mActionRefresh.svg'))
        for action, caption, tip in (
                (self.reference_action, 'Cargar capas', 'Cargar las capas de referencia HidroCAUMAX'),
                (self.action, 'Herramientas HidroCAUMAX',
                 'Herramientas activas automáticamente. Pulsar para restaurar los botones de HidroCAUMAX.'),
                (self.open_action, 'Abrir cuenca', 'Abrir un proyecto de cuenca guardado'),
                (self.clear_layers_action, 'Eliminar capas',
                 'Retirar de QGIS todas las capas generadas por HidroCAUMAX. Conserva los archivos en disco.'),
                (self.update_action, 'Actualizar', 'Buscar actualizaciones de HidroCAUMAX')):
            action.setIconText(caption)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            self._toolbar.addAction(action)
        self._show_toolbar()
        # QGIS can restore a previously hidden toolbar after loading plugins.
        self._toolbar_startup_signal = getattr(self.iface, 'initializationCompleted', None)
        if self._toolbar_startup_signal is not None:
            self._toolbar_startup_signal.connect(self._complete_startup)

    def _activate_tools(self, *args):
        # Re-scan existing dialogs as well as those opened after plugin loading.
        # The logo restores the tools; it cannot accidentally turn them off.
        self.set_enabled(True)

    def _complete_startup(self):
        self._activate_tools()
        self._show_toolbar()

    def _show_toolbar(self):
        toolbar = getattr(self, '_toolbar', None)
        if toolbar is not None:
            toolbar.show()

    def _remove_toolbar(self):
        signal = getattr(self, '_toolbar_startup_signal', None)
        if signal is not None:
            signal.disconnect(self._complete_startup)
            self._toolbar_startup_signal = None
        toolbar = getattr(self, '_toolbar', None)
        if toolbar is not None:
            self._toolbar = None
            self.iface.mainWindow().removeToolBar(toolbar)
            toolbar.clear()
            toolbar.hide()
            toolbar.deleteLater()

    def _check_updates(self, *args):
        self._updates.check(manual=True)

    def _clear_generated_layers(self, *args):
        # Clear live work first so its late results cannot repopulate the map.
        # Ownership markers also find previous queries and reopened projects.
        self.clear_layers_action.setEnabled(False)
        try:
            count = clear_delineations(close_dialogs=False, include_exports=True)
            message = (f'Se han retirado {count} capas generadas por HidroCAUMAX. '
                       'Los archivos se conservan en disco.' if count else
                       'No hay capas generadas por HidroCAUMAX cargadas en el proyecto.')
            self.iface.messageBar().pushMessage('HidroCAUMAX', message, level=Qgis.Info, duration=5)
        finally:
            self.clear_layers_action.setEnabled(True)

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
        self._enabled = enabled
        if enabled:
            self._bridge.start()
            self._rational.start()
            self._rational_inputs.start()
            self._rational_layers.start()
            self._visible_dem.start()
            self._native_layers_view.start()
            self._updates.check()
        else:
            self._updates.cancel()
            self._close_dialogs()
            self._bridge.stop()
            self._rational.stop()
            self._rational_inputs.stop()
            self._rational_frequency.stop()
            self._rational_layers.stop()
            self._visible_dem.stop()
            self._native_layers_view.stop()

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
        if getattr(query, 'source_kind', 'caumax_raster') == 'rational':
            # The native calculator is already the source of this reference.
            # Do not erase its active improved-D8 panel and owned outlines when
            # opening the detailed basin dialog for the *same* native point.
            if previous is not None:
                try:
                    previous.dispose()
                    previous.close()
                except RuntimeError:
                    pass
        else:
            clear_delineations(close_dialogs=True)
        dialog = BasinDialog(query, self.iface, native_dialog)
        dialog._native_query = query
        self._dialogs[native_dialog] = dialog
        if getattr(query, 'source_kind', None) == 'rational':
            binding = self._rational_layers._bindings.get(id(native_dialog))
            basin = getattr(getattr(binding, 'panel', None), '_basin', None)
            if basin is not None:
                dialog.adopt_refined_basin(*basin)
        dialog.finished.connect(lambda result, parent=native_dialog, current=dialog: self._forget(parent, current))
        dialog.destroyed.connect(lambda *args, parent=native_dialog, current=dialog: self._forget(parent, current))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _rational_basin_refined(self, native_dialog, key, analysis, query):
        """Keep an already-open detailed basin window on the panel's MDT."""
        dialog = self._dialogs.get(native_dialog)
        if dialog is not None and getattr(query, 'source_kind', None) == 'rational':
            dialog.adopt_refined_basin(key, analysis, query)

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
        self._remove_toolbar()
        self._map_services.dispose()
        self._updates.dispose()
        self._close_dialogs()
        self._bridge.stop()
        self._rational.stop()
        self._rational_inputs.stop()
        self._rational_frequency.stop()
        self._rational_layers.stop()
        self._visible_dem.stop()
        self._native_layers_view.stop()
        if hasattr(self, 'action'):
            try:
                self.action.triggered.disconnect(self._activate_tools)
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
        if hasattr(self, 'clear_layers_action'):
            try:
                self.clear_layers_action.triggered.disconnect(self._clear_generated_layers)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.clear_layers_action)
            self.clear_layers_action.deleteLater()
        if hasattr(self, 'reference_action'):
            try:
                self.reference_action.triggered.disconnect(self._map_services.load)
                self._map_services.busy_changed.disconnect(self.reference_action.setDisabled)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.reference_action)
            self.reference_action.deleteLater()
