"""Plugin entry point: extend CAUMAX's own dialog, not QGIS identify."""
from qgis.PyQt.QtCore import QObject, Qt, QStandardPaths
from qgis.PyQt.QtGui import QIcon, QImage, QPixmap
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
from .clean_layers_ui import confirm_cleanup
from .map_services import ReferenceLayerLoader
from .native_layers_view import NativeLayersView


def _hidrocaumax_icons(path):
    """Retain the original logo and provide a deliberately subdued variant."""
    logo = QPixmap(str(path))
    if logo.isNull():
        return QIcon(str(path)), QIcon(str(path))
    image = logo.toImage().convertToFormat(QImage.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            luminance = round(0.2126 * color.red() + 0.7152 * color.green()
                              + 0.0722 * color.blue())
            grey = round(0.42 * luminance + 0.58 * 180)
            color.setRed(max(0, grey - 8))
            color.setGreen(grey)
            color.setBlue(min(255, grey + 12))
            color.setAlpha(round(color.alpha() * 0.60))
            image.setPixelColor(x, y, color)
    normal, muted = QIcon(logo), QIcon(QPixmap.fromImage(image))
    # QAction remains clickable for 'Restore tools' even when the plugin's
    # tools are stopped. Use an explicit muted icon in that state, and also
    # honor genuine QWidget disabled rendering when Qt requests it.
    normal.addPixmap(QPixmap.fromImage(image), QIcon.Disabled)
    return normal, muted


class NativeCaumaxPlugin(QObject):
    MENU = 'HidroCAUMAX'

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self._dialogs = {}
        self._saved_dialogs = []
        self._query_history = []  # Preserve earlier maximum-flow and rational closure points.
        self._manual = None
        self._enabled = False
        self._bridge = CaumaxBridge(iface, self._open_basin, self._new_query_started)
        self._rational = RationalExportBridge(iface)
        self._rational_inputs = RationalInputsBridge(iface)
        self._rational_frequency = RationalFrequencyBridge(iface, self)
        self._rational_layers = RationalLayersBridge(iface, self._open_basin,
            self._new_query_started, self._rational_basin_refined,
            self._rational_frequency.request_calculation,
            self._save_complete_basin)
        self._rational_inputs.calculation_started.connect(self._rational_layers.calculation_started)
        self._rational_inputs.calculation_finished.connect(self._rational_layers.calculation_finished)
        self._rational_inputs.calculation_started.connect(self._rational_frequency.calculation_started)
        self._rational_inputs.calculation_finished.connect(self._rational_frequency.calculation_finished)
        self._visible_dem = VisibleDemController(iface, self)
        self._updates = UpdateNotifier(iface, self)
        self._map_services = ReferenceLayerLoader(iface, self)
        self._native_layers_view = NativeLayersView(iface, self)

    @staticmethod
    def _tidy_legacy_layers():
        from .improved_layers import remove_improved_folder_group
        from .boundary_layers import deduplicate_boundaries
        from .course_layers import deduplicate_course_groups
        from .drainage_layers import (migrate_drainage_display_names,
                                      adopt_legacy_published_drainage)
        from .terrain_layers import remove_native_terrain
        remove_improved_folder_group()
        deduplicate_boundaries()
        deduplicate_course_groups()
        migrate_drainage_display_names()
        adopt_legacy_published_drainage()
        remove_native_terrain()

    def initGui(self):
        from .improved_storage import prepare_export_directory
        try:
            prepare_export_directory(legacy_alias=Path(__file__).resolve().parent.parent.name == 'plugins')
        except OSError as error:
            self.iface.messageBar().pushMessage('HidroCAUMAX',
                'No se ha podido preparar la carpeta de exportación: ' + str(error), level=Qgis.Warning, duration=10)
        self.action = QAction('Restaurar herramientas HidroCAUMAX', self.iface.mainWindow())
        self.action.triggered.connect(self._activate_tools)
        self.iface.addPluginToMenu(self.MENU, self.action)
        self.open_action = QAction('Abrir proyecto de cuenca…', self.iface.mainWindow())
        self.open_action.triggered.connect(self._open_saved_project)
        self.iface.addPluginToMenu(self.MENU, self.open_action)
        self.reference_action = QAction('Cargar o retirar capas HidroCAUMAX', self.iface.mainWindow())
        self.reference_action.setCheckable(True)
        self.reference_action.triggered.connect(self._map_services.toggle)
        self._map_services.state_changed.connect(self._sync_reference_action)
        self.iface.addPluginToMenu(self.MENU, self.reference_action)
        self.clear_layers_action = QAction('Limpiar resultados y referencias de HidroCAUMAX', self.iface.mainWindow())
        self.clear_layers_action.triggered.connect(self._clear_generated_layers)
        self.iface.addPluginToMenu(self.MENU, self.clear_layers_action)
        self.update_action = QAction('Buscar actualizaciones…', self.iface.mainWindow())
        self.update_action.triggered.connect(self._check_updates)
        self.iface.addPluginToMenu(self.MENU, self.update_action)
        self.help_action = QAction('Ayuda · Manual de usuario', self.iface.mainWindow())
        self.help_action.triggered.connect(self._show_help)
        self.iface.addPluginToMenu(self.MENU, self.help_action)
        self._create_toolbar()
        self._sync_reference_action()
        self._tidy_legacy_layers()
        self._legacy_project_signal = getattr(self.iface, 'projectRead', None)
        if self._legacy_project_signal is not None:
            self._legacy_project_signal.connect(self._tidy_legacy_layers)
        self.set_enabled(True)

    def _sync_reference_action(self):
        loading = self._map_services.busy
        present = self._map_services.has_references
        self.reference_action.setChecked(loading or present)
        tip = ('Cancelar la carga y retirar las capas de referencia HidroCAUMAX' if loading else
               'Retirar de QGIS las capas de referencia HidroCAUMAX' if present else
               'Cargar las capas de referencia HidroCAUMAX')
        self.reference_action.setToolTip(tip)
        self.reference_action.setStatusTip(tip)

    def _create_toolbar(self):
        """Share the menu actions so busy and trigger states agree."""
        create = getattr(self.iface, 'addToolBar', None)
        self._toolbar = (create(self.MENU) if callable(create)
                         else self.iface.mainWindow().addToolBar(self.MENU))
        self._toolbar.setObjectName('HidroCAUMAXToolbar')
        self._toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._active_icon, self._inactive_icon = _hidrocaumax_icons(
            Path(__file__).parent / 'assets' / 'hidrocaumax.png')
        self.action.setIcon(self._active_icon if self._enabled else self._inactive_icon)
        self.open_action.setIcon(QgsApplication.getThemeIcon('/mActionFileOpen.svg'))
        self.reference_action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'load_reference_layers.svg')))
        self.clear_layers_action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'clear_generated_layers.svg')))
        self.update_action.setIcon(QgsApplication.getThemeIcon('/mActionRefresh.svg'))
        self.help_action.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'help_manual.svg')))
        for action, caption, tip in (
                (self.action, 'HidroCAUMAX v.1.0',
                 'Herramientas activas automáticamente. Pulsar para restaurar los botones de HidroCAUMAX.'),
                (self.reference_action, 'Capas', 'Cargar las capas de referencia HidroCAUMAX'),
                (self.open_action, 'Abrir cuenca', 'Abrir un proyecto de cuenca guardado'),
                (self.clear_layers_action, 'Limpiar capas',
                 'Eliminar resultados HidroCAUMAX de QGIS, disco, copias de seguridad y cachés temporales.'),
                (self.update_action, 'Actualizar', 'Descargar, verificar e instalar una nueva versión de HidroCAUMAX'),
                (self.help_action, 'Ayuda', 'Abrir el manual de usuario incluido en PDF')):
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

    def _show_help(self, *args):
        if self._manual is None:
            from .manual_dialog import ManualDialog
            self._manual = ManualDialog(self.iface.mainWindow())
        self._manual.show()
        self._manual.raise_()
        self._manual.activateWindow()

    def _clear_generated_layers(self, *args):
        # Both Clean buttons must use the exact same reviewed destructive
        # operation; a toolbar-only visual reset would leave disk caches.
        self.clear_layers_action.setEnabled(False)
        try:
            message = confirm_cleanup(self.iface.mainWindow(), iface=self.iface)
            if message:
                warning = 'No se han podido eliminar' in message
                self.iface.messageBar().pushMessage(
                    'HidroCAUMAX', message,
                    level=Qgis.Warning if warning else Qgis.Info, duration=10)
        finally:
            self.clear_layers_action.setEnabled(True)

    def _save_complete_basin(self, native_dialog):
        """Portable save, independent of temporary QGIS/GDAL product paths."""
        from .basin_archive import save_complete_basin, EXTENSION
        folder = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation) or str(Path.home())
        suggested = str(Path(folder) / 'Cuenca_HidroCAUMAX.hidrocuenca')
        path, _ = QFileDialog.getSaveFileName(
            native_dialog, 'Guardar CUENCA completa', suggested,
            'Cuenca completa HidroCAUMAX (*.hidrocuenca)')
        if not path:
            return
        if not Path(path).suffix:
            path += EXTENSION
        if Path(path).exists():
            QMessageBox.warning(native_dialog, 'Guardar CUENCA',
                                'Ya existe ese archivo. Elige otro nombre para no sobrescribir la cuenca guardada.')
            return
        contexts = list(getattr(self, '_query_history', ()))
        contexts.extend(getattr(dialog, 'query', None)
                        for dialog in list(self._dialogs.values()) + self._saved_dialogs)
        saved, manifest = save_complete_basin(
            path, iface=self.iface, native_dialog=native_dialog,
            queries=contexts,
            sessions=list(self._dialogs.values()) + self._saved_dialogs)
        QMessageBox.information(
            native_dialog, 'Guardar CUENCA',
            'Cuenca completa guardada en:\n' + str(saved) +
            '\nCapas incorporadas: ' + str(len(manifest['layers'])) +
            '\nArchivos de ResolucionMejorada: ' +
            str(len(manifest['improved_files'])) +
            '\nPuedes recuperarla con Abrir cuenca de la barra de QGIS.')

    def _open_saved_project(self):
        self._updates.check()
        source, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(), 'Abrir proyecto de consulta', str(Path.home()),
            'Cuenca completa HidroCAUMAX (*.hidrocuenca);;Proyecto de consulta CAUMAX (*.caumax.json);;Archivos JSON (*.json)')
        if not source:
            return
        try:
            if source.lower().endswith('.hidrocuenca'):
                from .basin_archive import open_complete_basin
                manifest, folder = open_complete_basin(source, iface=self.iface)
                QMessageBox.information(
                    self.iface.mainWindow(), 'Abrir cuenca',
                    'Cuenca recuperada: ' + str(len(manifest['layers'])) +
                    ' capas con sus estilos y ' +
                    str(len(manifest['improved_files'])) +
                    ' archivos de resolución mejorada.\n' +
                    'Datos disponibles en: ' + str(folder))
                return
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
        # Keep the restore action enabled; the icon communicates whether the
        # tools are currently active instead of disabling their restore button.
        if hasattr(self, 'action') and self._active_icon is not None:
            self.action.setIcon(self._active_icon if enabled else self._inactive_icon)
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
        # A map pick launched inside HidroCAUMAX must keep the previous basin
        # on screen until CAUMAX confirms a *valid* new Qci query. Invalid
        # clicks leave the original consultation intact.
        bridge = getattr(self, '_bridge', None)
        if (bridge is not None
                and bridge.auto_reselection_pending(native_dialog)):
            return
        if self._enabled:
            clear_delineations(close_dialogs=True,
                               preserve_published_drainage=True)

    def _open_basin(self, native_dialog, query):
        if not self._enabled:
            return
        history = getattr(self, '_query_history', None)
        if isinstance(history, list):
            history.append(query)
            if len(history) > 40:
                del history[:-40]
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
            clear_delineations(close_dialogs=True,
                               preserve_published_drainage=True)
        dialog = BasinDialog(query, self.iface, native_dialog)
        dialog._native_query = query
        # New selection invokes the original native CAUMAX map tool. Never
        # substitute a locally computed flow for CAUMAX's validated result.
        bridge = getattr(self, '_bridge', None)
        if (getattr(query, 'source_kind', 'caumax_raster') != 'rational'
                and bridge is not None
                and hasattr(dialog, 'set_new_selection_handler')):
            dialog.set_new_selection_handler(
                lambda native=native_dialog: bridge.start_auto_reselection(native),
                lambda native=native_dialog: bridge.auto_reselection_pending(native))
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
        signal = getattr(self, '_legacy_project_signal', None)
        if signal is not None:
            signal.disconnect(self._tidy_legacy_layers)
            self._legacy_project_signal = None
        if self._manual is not None:
            self._manual.close()
            self._manual.deleteLater()
            self._manual = None
        if hasattr(self, 'help_action'):
            try:
                self.help_action.triggered.disconnect(self._show_help)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.help_action)
            self.help_action.deleteLater()
        from .improved_storage import preserve_exports
        try:
            preserve_exports()
        except OSError as error:
            self.iface.messageBar().pushMessage('HidroCAUMAX',
                'No se ha podido conservar la carpeta ResolucionMejorada: ' + str(error),
                level=Qgis.Warning, duration=10)
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
                self.reference_action.triggered.disconnect(self._map_services.toggle)
                self._map_services.state_changed.disconnect(self._sync_reference_action)
            except (RuntimeError, TypeError):
                pass
            self.iface.removePluginMenu(self.MENU, self.reference_action)
            self.reference_action.deleteLater()
