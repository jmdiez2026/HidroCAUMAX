"""Improved-resolution products embedded in the native rational-method dialog."""
from pathlib import Path

from qgis.PyQt.QtCore import QTimer, Qt
from qgis.PyQt.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLabel, QWidget

from .drainage_layers import DrainageLayers
from .drainage_panel import DrainageControls
from .drainage_threshold import manual_step, recommended_threshold
from .rational_layers_controller import RationalLayersController


class RationalLayersPanel(DrainageControls):
    """Own this point's calculations without consulting a native basinCalc."""

    def __init__(self, iface, context_getter, parent=None):
        super().__init__(parent)
        self.setObjectName('hidrocaumax_rational_layers_panel')
        self.iface = iface
        self._context_getter = context_getter
        self._disposed = self._loading = self._cleared = False
        self._queued_prepare = False
        self._products = self._state = self._selector = None
        self._layers = DrainageLayers(iface)
        resolution_row = QWidget(self)
        row = QHBoxLayout(resolution_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        row.addWidget(QLabel('Resolución MDT', self))
        self.resolution_combo = QComboBox(self)
        self.resolution_combo.setObjectName('hidrocaumax_improved_mdt_resolution')
        self.resolution_combo.setMinimumContentsLength(14)
        row.addWidget(self.resolution_combo)
        row.addStretch(1)
        self.layout().insertWidget(1, resolution_row)
        self.context_label = QLabel(self)
        self.context_label.setWordWrap(True)
        self.layout().insertWidget(2, self.context_label)
        self._controller = RationalLayersController(self._options, self._ready,
            self._status, self._failed, self)
        self._threshold_timer = QTimer(self)
        self._threshold_timer.setSingleShot(True)
        self._threshold_timer.setInterval(450)
        self._threshold_timer.timeout.connect(self._prepare)
        self.resolution_combo.currentIndexChanged.connect(self._resolution_changed)
        self.prepare_requested.connect(self._prepare)
        self.threshold_changed.connect(self._threshold_changed)
        self.automatic_changed.connect(self._automatic_changed)
        self.visibility_changed.connect(self._visibility_changed)
        self.export_requested.connect(self._export)
        from .delineation_cleanup import register_participant
        register_participant(self)
        self.destroyed.connect(self.dispose)
        self._update_controls()
        self.refresh_context()

    def _selected_key(self):
        return self.resolution_combo.currentData()

    def _selected_available(self):
        index = self.resolution_combo.currentIndex()
        return index >= 0 and bool(self.resolution_combo.itemData(index, Qt.UserRole + 1))

    def refresh_context(self):
        """An unchanged point never restarts work, including after Clear."""
        if self._disposed:
            return False
        try:
            query = self._context_getter()
            if query is None:
                raise ValueError('Selecciona un punto de cálculo para preparar sus capas.')
        except (AttributeError, TypeError, ValueError, OSError) as error:
            changed = self._controller.set_context(None)
            if changed:
                self._discard_result()
            self.context_label.setText('Punto de cálculo pendiente.')
            self._status(str(error), 0)
            return False
        changed = self._controller.set_context(query)
        if changed:
            self._cleared = False
            self._queued_prepare = False
            self._discard_result()
        self.context_label.setText(
            ('Punto de cálculo: X={:.2f}; Y={:.2f} · {}').format(
                query.x, query.y, query.crs.authid() or 'CRS del proyecto').replace('.', ','))
        self._update_controls()
        return changed

    def _options(self, options):
        if self._disposed:
            return
        previous = self._selected_key()
        self._loading = True
        self.resolution_combo.blockSignals(True)
        try:
            self.resolution_combo.clear()
            for option in options:
                if not 0 < float(option['resolution']) < 500:
                    continue
                key = str(option['key'])
                index = self.resolution_combo.count()
                resolution = format(float(option['resolution']), 'g').replace('.', ',')
                self.resolution_combo.addItem(f'{resolution}×{resolution} m', key)
                available = bool(option.get('available'))
                self.resolution_combo.setItemData(index, available, Qt.UserRole + 1)
                self.resolution_combo.setItemData(index, option.get('status', ''), Qt.ToolTipRole)
                self.resolution_combo.model().item(index).setEnabled(available)
            available_indices = [index for index in range(self.resolution_combo.count())
                                 if self.resolution_combo.itemData(index, Qt.UserRole + 1)]
            chosen = self.resolution_combo.findData(previous)
            if chosen not in available_indices:
                chosen = self.resolution_combo.findData('25')
            if chosen not in available_indices:
                chosen = available_indices[0] if available_indices else -1
            self.resolution_combo.setCurrentIndex(chosen)
        finally:
            self.resolution_combo.blockSignals(False)
            self._loading = False
        self._default_threshold()
        self._update_controls()
        if self._queued_prepare and self._selected_available():
            QTimer.singleShot(0, self._resume_prepare)

    def _default_threshold(self):
        if self._state is not None or not self.drainage_auto.isChecked():
            return
        key = self._selected_key()
        reference = self._controller.base_analysis
        if key is None or reference is None:
            return
        cell_area = float(key) ** 2
        recommended = recommended_threshold(reference.area_m2, cell_area)
        self._loading = True
        try:
            self.drainage_threshold.setRange(cell_area / 1e6,
                max(reference.area_m2 * 2, recommended) / 1e6)
            self.drainage_threshold.setSingleStep(manual_step(reference.area_m2, cell_area) / 1e6)
            self.drainage_threshold.setValue(recommended / 1e6)
        finally:
            self._loading = False

    def _resolution_changed(self, *args):
        if self._loading or self._disposed:
            return
        self._threshold_timer.stop()
        self._controller.cancel()
        self._discard_result()
        self._default_threshold()
        self._status('TauDEM predeterminado seleccionado. Pulsa Generar capas D8.', 0)

    def _update_controls(self):
        if self._disposed:
            return
        busy = self._controller.busy
        context = self._controller.query is not None
        ready = context and self._selected_available()
        self.resolution_combo.setEnabled(not busy and self.resolution_combo.count() > 0)
        # An explicit click can retry a failed reference or interrupted
        # coverage discovery; periodic context checks still never restart it.
        self.drainage_prepare_button.setEnabled(context and not busy)
        self.drainage_threshold.setEnabled(ready and not busy)
        self.drainage_auto.setEnabled(ready and not busy)
        self.drainage_visible.setEnabled(self._state is not None and not busy)
        exportable = self._state is not None and self._products is not None and not busy
        exportable = exportable and not self._threshold_timer.isActive()
        for button in self.product_buttons:
            button.setEnabled(exportable)
        self.drainage_progress.setVisible(busy)

    def _status(self, text, progress=0):
        if self._disposed:
            return
        self.drainage_status.setText(str(text))
        self.drainage_progress.setValue(max(0, min(100, round(float(progress)))))
        self._update_controls()

    def _prepare(self):
        if self._disposed:
            return
        self._threshold_timer.stop()
        self.refresh_context()
        if self._controller.query is None:
            return
        if self._controller.resolution is None and not self._controller.busy:
            # Clear may have cancelled the first reference task. A new explicit
            # Generate request can restart it; periodic bridge scans cannot.
            query = self._controller.query
            self._controller.set_context(None)
            self._queued_prepare = True
            self._controller.set_context(query)
            return
        if self._controller.busy:
            return
        if not self._selected_available():
            self._queued_prepare = True
            self._controller.resolution.discover()
            self._update_controls()
            return
        self._queued_prepare = False
        self._cleared = False
        self._close_selector()
        self._products = self._state = None
        automatic = self.drainage_auto.isChecked()
        threshold = None if automatic else self.drainage_threshold.value() * 1e6
        self._controller.prepare(self._selected_key(), threshold, automatic)
        self._update_controls()

    def _resume_prepare(self):
        if self._queued_prepare and not self._disposed and not self._controller.busy:
            self._prepare()

    def _ready(self, key, products, state):
        if self._disposed or self._cleared or key != self._selected_key():
            return
        # Native fields or source files may change while the worker completes.
        if self.refresh_context() or self._controller.query is None:
            return
        self._products, self._state = products, state
        self._loading = True
        try:
            analysis = products.analysis
            minimum = analysis.cell_width * analysis.cell_height
            threshold = float(state['threshold_area_m2'])
            self.drainage_threshold.setRange(minimum / 1e6,
                max(analysis.area_m2 * 2, threshold,
                    state['recommended_threshold_area_m2']) / 1e6)
            self.drainage_threshold.setSingleStep(manual_step(analysis.area_m2, minimum) / 1e6)
            self.drainage_threshold.setValue(threshold / 1e6)
            self.drainage_auto.setChecked(bool(state['automatic']))
            state['network_visible'] = self.drainage_visible.isChecked()
            state['directions_visible'] = False
            state['metadata']['visible'] = state['network_visible']
            self._layers.update(analysis, state)
        except Exception as error:
            self._failed(str(error))
            return
        finally:
            self._loading = False
        value = format(threshold / 1e6, 'g').replace('.', ',')
        self._status(f'TauDEM · umbral {value} km² · {len(state["features"])} tramos. '
                     'Cada archivo se exporta por separado.', 100)

    def _failed(self, text):
        if self._disposed:
            return
        self._status(text, 0)
        if self._controller.selection_context() is not None:
            QTimer.singleShot(0, self._offer_selection)

    def _offer_selection(self):
        if self._disposed or self._cleared or self._selector is not None:
            return
        context = self._controller.selection_context()
        if context is None:
            return
        try:
            from .outlet_selection import OutletSelectionDialog
            dialog = OutletSelectionDialog(self.iface, context, self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self._selector = dialog
            dialog.accepted.connect(lambda: self._apply_selection(dialog, context))
            dialog.finished.connect(lambda _: self._selection_finished(dialog))
            dialog.show()
        except Exception as error:
            self._status(str(error), 0)

    def _apply_selection(self, dialog, context):
        if self._disposed or self._selector is not dialog:
            return
        point = dialog.selection()
        if point is not None:
            self._controller.select_outlet(context, *point)
            self._update_controls()

    def _selection_finished(self, dialog):
        if self._selector is dialog:
            self._selector = None

    def _close_selector(self):
        dialog, self._selector = self._selector, None
        if dialog is not None:
            dialog.reject()

    def _threshold_changed(self, *args):
        if self._loading or self._disposed:
            return
        self._loading = True
        try:
            self.drainage_auto.setChecked(False)
        finally:
            self._loading = False
        if self._state is not None:
            self._threshold_timer.start()
        self._update_controls()

    def _automatic_changed(self, automatic):
        if self._loading or self._disposed:
            return
        if automatic and self._state is not None:
            self._loading = True
            try:
                self.drainage_threshold.setValue(
                    self._state['recommended_threshold_area_m2'] / 1e6)
            finally:
                self._loading = False
            self._threshold_timer.start()
        elif automatic:
            self._default_threshold()
        elif self._state is not None:
            self._state['automatic'] = self._state['metadata']['automatic'] = False
        self._update_controls()

    def _visibility_changed(self, visible):
        if self._loading or self._disposed or self._state is None or self._products is None:
            return
        self._state['network_visible'] = bool(visible)
        self._state['metadata']['visible'] = bool(visible)
        self._layers.update(self._products.analysis, self._state)

    def _export(self, kind):
        if (self._disposed or self._controller.busy or self._threshold_timer.isActive()
                or self.refresh_context() or self._state is None or self._products is None):
            return
        products, state = self._products, self._state
        size = f'{products.analysis.cell_width:g}m'
        names = {'dem': f'MDT_{size}.tif',
                 'directions': f'Direccion_D8_caumax_{size}.tif',
                 'network': f'Red_drenaje_{size}.shp'}
        filters = 'Shapefile (*.shp)' if kind == 'network' else 'GeoTIFF (*.tif *.tiff)'
        destination, _ = QFileDialog.getSaveFileName(self, 'Exportar capa hidrológica',
            str(Path.home() / names[kind]), filters)
        if not destination:
            return
        try:
            from .hydro_export import export_dem, export_directions, export_network
            if kind == 'dem':
                path = export_dem(products, destination)
            elif kind == 'directions':
                path = export_directions(products, destination, encoding='caumax')
            else:
                path = export_network(products, destination, snapshot=state)
            export_key = 'directions_caumax' if kind == 'directions' else kind
            state['metadata'].setdefault('exports', {})[export_key] = str(path)
            self._status('Archivo guardado: ' + str(path), 100)
        except Exception as error:
            self._failed(str(error))

    def _discard_result(self):
        self._threshold_timer.stop()
        self._close_selector()
        self._products = self._state = None
        self._layers.remove()
        self._update_controls()

    def _clear_local_delineation(self):
        if self._disposed:
            return
        self._cleared = True
        self._queued_prepare = False
        self._controller.cancel()
        self._discard_result()
        self._status('Delineado borrado. Pulsa Generar capas D8 para volver a generarlo.', 0)

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._queued_prepare = False
        self._threshold_timer.stop()
        self._close_selector()
        self._controller.dispose()
        from .delineation_cleanup import unregister_participant
        unregister_participant(self)
