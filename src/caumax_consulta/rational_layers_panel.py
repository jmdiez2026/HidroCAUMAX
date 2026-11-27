"""Improved-resolution products embedded in the native rational-method dialog."""
from pathlib import Path

from qgis.PyQt.QtCore import QTimer, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QSpinBox, QWidget, QBoxLayout,
)
from qgis.core import QgsGeometry, QgsPointXY

from .drainage_layers import DrainageLayers, prune_managed_drainage
from .boundary_layers import BoundaryLayers, deduplicate_boundaries
from .drainage_panel import DrainageControls
from .drainage_threshold import manual_step, recommended_threshold
from .rational_layers_controller import RationalLayersController


class RationalLayersPanel(DrainageControls):
    """Own this point's calculations without consulting a native basinCalc."""
    content_changed = pyqtSignal()

    def __init__(self, iface, context_getter, parent=None, on_basin_refined=None):
        super().__init__(parent)
        self.setObjectName('hidrocaumax_rational_layers_panel')
        self.iface = iface
        self._context_getter = context_getter
        self._disposed = self._loading = self._cleared = False
        self._queued_prepare = False
        self._generation_running = False
        self.help_label.setText('Genera las tres capas necesarias para ejecutar CAUMAX con resolución mayor de la original 500x500m (esquema D8-TauDEM/Esri): (1) ráster MDT, (2) ráster Direcciones de flujo, y (3) shape Red de Drenaje.')
        self.layout().setSpacing(10)
        self._drainage_controls_layout.setSpacing(8)
        self._drainage_exports_widget.layout().setHorizontalSpacing(8)
        self.cancel_button = QPushButton('Cancelar', self)
        self.cancel_button.setObjectName('hidrocaumax_cancel_d8')
        self.cancel_button.hide()
        self.layout().removeWidget(self.drainage_progress)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.drainage_progress, 1)
        progress_row.addWidget(self.cancel_button)
        self.layout().addLayout(progress_row)
        self.cancel_button.clicked.connect(self._cancel_generation)
        from .clean_layers_ui import clean_button
        self.delete_layers_button = clean_button(self)
        self.delete_layers_button.setObjectName('hidrocaumax_delete_generated_layers')
        self.delete_layers_button.clicked.connect(self._delete_generated_layers)
        self._products = self._state = self._selector = None
        self._basin = None
        self._outlines = {}
        self._outline_choices = {}
        self._outline_loading = False
        self._queued_resolution = None
        self._resolution_explicit = False
        self._on_basin_refined = on_basin_refined
        self._layers = DrainageLayers(iface)
        self._boundary_layers = BoundaryLayers()
        # A saved QGIS project can still contain managed results from previous
        # panels. Opening the new panel does not resurrect old TauDEM groups.
        prune_managed_drainage()
        deduplicate_boundaries(storage_roots=(self._boundary_layers.directory,))
        self._outline_timer = QTimer(self)
        self._outline_timer.setSingleShot(True)
        self._outline_timer.setInterval(130)
        self._outline_timer.timeout.connect(self._draw_outlines)
        resolution_row = QWidget(self)
        row = QHBoxLayout(resolution_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(QLabel('Resolución MDT', self))
        self.resolution_combo = QComboBox(self)
        self.resolution_combo.setObjectName('hidrocaumax_improved_mdt_resolution')
        self.resolution_combo.setMinimumContentsLength(8)
        self.resolution_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.resolution_combo.setFixedWidth(self.fontMetrics().horizontalAdvance('500×500 m') + 38)
        row.addWidget(self.resolution_combo)
        self.outlet_button = QPushButton('Ajustar punto de cierre', self)
        self.outlet_button.setToolTip('Corrección manual opcional del cierre del MDT seleccionado.')
        row.addWidget(self.outlet_button)
        row.addStretch(1)
        row.addWidget(self.delete_layers_button, 0, Qt.AlignRight)
        self.layout().insertWidget(1, resolution_row)
        self.context_label = QLabel(self)
        self.context_label.setWordWrap(True)
        self.layout().insertWidget(2, self.context_label)
        outline_row = QWidget(self)
        outline_layout = QHBoxLayout(outline_row)
        outline_layout.setContentsMargins(0, 0, 0, 0)
        outline_layout.setSpacing(10)
        self.original_check = QCheckBox('Original · contorno de celdas', self)
        self.original_check.setChecked(True)
        self.smooth_check = QCheckBox('Suavizada', self)
        outline_layout.addWidget(self.original_check)
        outline_layout.addWidget(self.smooth_check)
        outline_layout.addWidget(QLabel('Grado:', self))
        self.smoothing_degree = QSpinBox(self)
        self.smoothing_degree.setRange(1, 45)
        self.smoothing_degree.setValue(3)
        self.smoothing_degree.setToolTip('Suavizado gaussiano de la divisoria; conserva el cierre y la topología.')
        outline_layout.addWidget(self.smoothing_degree)
        self.export_boundary_button = QPushButton('Exportar shape', self)
        outline_layout.addWidget(self.export_boundary_button)
        outline_layout.addStretch(1)
        self.layout().insertWidget(3, outline_row)
        self._controller = RationalLayersController(self._options, self._ready,
            self._status, self._failed, self, on_basin=self._basin_ready)
        self._threshold_timer = QTimer(self)
        self._threshold_timer.setSingleShot(True)
        self._threshold_timer.setInterval(450)
        self._threshold_timer.timeout.connect(self._prepare)
        self.resolution_combo.currentIndexChanged.connect(self._resolution_changed)
        self.outlet_button.clicked.connect(lambda _=False: self._offer_selection())
        self.original_check.toggled.connect(self._outline_changed)
        self.smooth_check.toggled.connect(self._outline_changed)
        self.smoothing_degree.valueChanged.connect(self._outline_changed)
        self.export_boundary_button.clicked.connect(self._export_boundary)
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
                self._discard_result(clear_boundary=True)
            self.context_label.setText('Punto de cálculo pendiente.')
            self._status(str(error), 0)
            return False
        changed = self._controller.set_context(query)
        if changed:
            self._cleared = False
            self._queued_prepare = False
            self._discard_result(clear_boundary=True)
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
                if not 0 < float(option['resolution']) <= 500:
                    continue
                key = str(option['key'])
                index = self.resolution_combo.count()
                resolution = format(float(option['resolution']), 'g').replace('.', ',')
                label = (f'{resolution}×{resolution} m · original CAUMAX'
                         if key == '500' else f'{resolution}×{resolution} m')
                self.resolution_combo.addItem(label, key)
                available = bool(option.get('available'))
                self.resolution_combo.setItemData(index, available, Qt.UserRole + 1)
                self.resolution_combo.setItemData(index, option.get('status', ''), Qt.ToolTipRole)
                self.resolution_combo.model().item(index).setEnabled(available)
            available_indices = [index for index in range(self.resolution_combo.count())
                                 if self.resolution_combo.itemData(index, Qt.UserRole + 1)]
            # Keep an explicit user choice (including 500 m). For an untouched
            # selector, prefer 25 m as soon as official coverage is discovered.
            # The initially available 500 m baseline is not an implicit lock.
            chosen = self.resolution_combo.findData(previous) if self._resolution_explicit else -1
            if chosen not in available_indices:
                chosen = self.resolution_combo.findData('25')
            if chosen not in available_indices:
                chosen = self.resolution_combo.findData('500')
            if chosen not in available_indices:
                # Display the pending 25 m size instead of a blank selector;
                # its disabled state prevents selection before verification.
                chosen = (available_indices[0] if available_indices else
                          self.resolution_combo.findData('25'))
            self.resolution_combo.setCurrentIndex(chosen)
        finally:
            self.resolution_combo.blockSignals(False)
            self._loading = False
        self._default_threshold()
        self._update_controls()
        if self._queued_prepare and self._selected_available():
            QTimer.singleShot(0, self._resume_prepare)
        elif (self._selected_available()
              and (self._basin is None or self._basin[0] != self._selected_key())
              and self._queued_resolution != self._selected_key()):
            # During discovery 500 m is the only initially enabled option.
            # Avoid accidentally cancelling discovery just to display it.
            # A deliberate 500 m selection is always honoured, however.
            awaiting_coverage = any(
                item.get('status') == 'Pendiente de consultar cobertura'
                for item in options if str(item.get('key')) != '500')
            key = self._selected_key()
            if key != '500' or self._resolution_explicit or not awaiting_coverage:
                self._queued_resolution = key
                QTimer.singleShot(0, lambda selected=key: self._start_delineation(selected))

    def _default_threshold(self):
        if self._state is not None or not self.drainage_auto.isChecked():
            return
        key = self._selected_key()
        reference = self._controller.base_analysis
        if key is None or reference is None or key == '500':
            # The original 500 m entry is informational and its native basin
            # is not a TauDEM network product.
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

    def _start_delineation(self, key=None):
        if self._disposed or self._cleared:
            return
        if key is not None and key != self._queued_resolution:
            return  # An explicit cancellation invalidated this queued callback.
        key = key or self._selected_key()
        if key != self._selected_key() or not self._selected_available():
            return
        self._queued_resolution = key
        label = ('Recuperando divisoria original de CAUMAX (500 m)…' if key == '500'
                 else f'Delineando automáticamente la cuenca con MDT de {key} m…')
        self._status(label, 0)
        self._controller.select_resolution(key)
        self._update_controls()

    def _resolution_changed(self, *args):
        self._generation_running = False
        if self._loading or self._disposed:
            return
        self._resolution_explicit = True
        self._threshold_timer.stop()
        # The selection itself cancels stale fine work. Do not cancel a fresh
        # background coverage discovery when the user selects 500 m.
        previous = self._products
        self._discard_result()
        # Only the selected resolution may have a TauDEM group in QGIS.
        retired = (() if previous is None else tuple(path for path in
                   (getattr(previous, 'dem_path', None),
                    getattr(previous, 'caumax_d8_path', None),
                    getattr(previous, 'tau_d8_path', None)) if path))
        prune_managed_drainage(retire_paths=retired)
        self._default_threshold()
        self._start_delineation()

    def _update_controls(self):
        if self._disposed:
            return
        busy = self._controller.busy
        context = self._controller.query is not None
        ready = context and self._selected_available()
        # Permit choosing another MDT during delineation: the old task cancels.
        self.resolution_combo.setEnabled(context and self.resolution_combo.count() > 0
                                         and not (self._controller.drainage is not None
                                                  and self._controller.drainage.busy))
        self.outlet_button.setEnabled(context and not busy and
                                      self._controller.resolution is not None and
                                      self._selected_key() != '500')
        self.outlet_button.setToolTip(
            'La referencia de 500 m conserva el cierre original de CAUMAX.'
            if self._selected_key() == '500' else
            'Corrección manual opcional del cierre del MDT seleccionado.')
        can_outline = self._basin is not None and not busy and self._basin[0] == self._selected_key()
        for control in (self.original_check, self.smooth_check, self.smoothing_degree):
            control.setEnabled(can_outline)
        self.export_boundary_button.setEnabled(can_outline and bool(self._outlines))
        # An explicit click can retry a failed reference or interrupted
        # coverage discovery; periodic context checks still never restart it.
        # The 500 m option is a native CAUMAX reference, not a TauDEM
        # high-resolution product. The delineation and smoothing remain usable.
        refined = self._selected_key() != '500'
        no_fine_coverage = not any(
            option.get('available') and str(option.get('key')) != '500'
            for option in self._controller.options)
        # If official fine coverage is temporarily unavailable, keep the
        # existing Generate action usable to retry its discovery from 500 m.
        self.drainage_prepare_button.setEnabled(
            context and not busy and (refined or no_fine_coverage))
        self.drainage_threshold.setEnabled(ready and not busy and refined)
        self.drainage_auto.setEnabled(ready and not busy and refined)
        self.drainage_visible.setEnabled(self._state is not None and not busy)
        exportable = self._state is not None and self._products is not None and not busy
        exportable = exportable and not self._threshold_timer.isActive()
        for button in self.product_buttons:
            button.setEnabled(exportable)
        self.drainage_progress.setVisible(busy)
        self.cancel_button.setVisible(self._generation_running and busy)
        self.delete_layers_button.setEnabled(not busy)
        self.content_changed.emit()

    def fit_to_width(self, width=None):
        super().fit_to_width(width)
        # Export captions differ greatly in length. Equal columns clip the
        # direction button even when the complete row fits the window.
        exports = self._drainage_exports_widget.layout()
        margins = self.layout().contentsMargins()
        available = (self.width() if width is None else width) - margins.left() - margins.right()
        if not hasattr(self, '_export_headings'):
            self._export_headings = [exports.itemAtPosition(0, col).widget() for col in range(3)]
        stacked = sum(button.width() for button in self.product_buttons) + 16 > available
        if getattr(self, '_export_stacked', None) != stacked:
            for widget in (*self._export_headings, *self.product_buttons):
                exports.removeWidget(widget)
            for col in range(3):
                exports.setColumnStretch(col, 0)
            for index, (label, button) in enumerate(zip(self._export_headings, self.product_buttons)):
                exports.addWidget(label, 2 * index if stacked else 0, 0 if stacked else index)
                exports.addWidget(button, 2 * index + 1 if stacked else 1,
                                  0 if stacked else index, Qt.AlignHCenter)
            self._export_stacked = stacked
        if not stacked:
            for column, button in enumerate(self.product_buttons):
                exports.setColumnStretch(column, max(1, button.width()))
        required = sum(widget.sizeHint().width() for widget in self._drainage_controls) + 32
        self._drainage_controls_layout.setDirection(
            QBoxLayout.TopToBottom if required > available else QBoxLayout.LeftToRight)
        self.layout().invalidate()
        height = self.layout().heightForWidth(self.width() if width is None else width)
        if height > 0:
            self.setMinimumHeight(height)

    def _cancel_generation(self):
        self._generation_running = False
        self._queued_prepare = False
        self._queued_resolution = None
        self._threshold_timer.stop()
        self._close_selector()
        self._controller.cancel()
        self._products = self._state = None
        self._status('Generación de capas D8 cancelada. Puedes volver a generar las capas.', 0)

    def _delete_generated_layers(self):
        from .clean_layers_ui import confirm_cleanup
        message = confirm_cleanup(self)
        if message:
            self._status(message, 0)

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
        if self._selected_key() == '500':
            resolution = self._controller.resolution
            if (resolution is not None and not self._controller.busy and
                    not any(option.get('available') and str(option.get('key')) != '500'
                            for option in self._controller.options)):
                self._generation_running = True
                self._cleared = False
                self._queued_prepare = True
                self._status('Consultando de nuevo la cobertura de MDT mejorados…', 0)
                resolution.discover()
                self._update_controls()
            else:
                self._status('500×500 m: cuenca original CAUMAX. Para generar D8 elige 25 o 5 m.', 100)
            return
        self.refresh_context()
        if self._controller.query is None:
            return
        if self._controller.busy:
            return
        self._generation_running = True
        if self._controller.resolution is None and not self._controller.busy:
            # Clear may have cancelled the first reference task. A new explicit
            # Generate request can restart it; periodic bridge scans cannot.
            query = self._controller.query
            self._controller.set_context(None)
            self._queued_prepare = True
            self._controller.set_context(query)
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

    def _basin_ready(self, key, analysis, query):
        """Display the verified fine basin as soon as ResolutionController has it."""
        if self._disposed or self._cleared or key != self._selected_key():
            return
        if self.refresh_context() or self._controller.query is None:
            return
        self._basin = (key, analysis, query)
        self._outline_loading = True
        try:
            choice = self._outline_choices.get(key)
            if choice is None:
                saved = self._boundary_layers.existing_state(analysis, query) or {}
                flags = saved.get('visibility', {})
                choice = (flags.get('original', True), flags.get('suavizada', False),
                          saved.get('degree'))
            original, smooth, degree = choice
            if degree is None:
                from .boundary_smoothing import suggested_degree
                geometry = QgsGeometry()
                geometry.fromWkb(bytes(analysis.geometry_wkb))
                try:
                    degree = max(1, round(suggested_degree(geometry,
                        analysis.cell_width, analysis.cell_height,
                        QgsPointXY(analysis.outlet_x, analysis.outlet_y))))
                except ValueError:
                    degree = 3
            for control, value in ((self.original_check, original),
                                   (self.smooth_check, smooth)):
                control.setChecked(bool(value))
            self.smoothing_degree.setValue(int(degree))
        finally:
            self._outline_loading = False
        self._draw_outlines()
        # Generate was explicitly requested: make the downloaded native-grid
        # MDT visible in QGIS immediately. StreamNet and TauDEM exports can
        # finish later; a network error must not hide a valid elevation model.
        pending = self._controller._pending
        if pending is not None and pending[0] == key and key != '500':
            try:
                self._layers.display_dem(analysis, query.dem_path)
                prune_managed_drainage(keep_owner=self._layers.owner_key,
                                      protected_paths=(query.dem_path,))
            except (RuntimeError, OSError, TypeError, ValueError) as error:
                self._status('No se puede cargar el MDT seleccionado en QGIS: '
                             + str(error), 0)
        if self._on_basin_refined is not None:
            self._on_basin_refined(key, analysis, query)

    def _outline_changed(self, *args):
        if self._outline_loading or self._disposed or self._basin is None:
            return
        self._outline_timer.start()

    def _draw_outlines(self):
        if self._disposed or self._cleared or self._basin is None:
            return
        key, analysis, query = self._basin
        if key != self._selected_key():
            return
        try:
            from .boundary_smoothing import smooth_boundary
            original = QgsGeometry()
            original.fromWkb(bytes(analysis.geometry_wkb))
            geometries = {}
            if self.original_check.isChecked():
                geometries['original'] = QgsGeometry(original)
            smooth = None
            if self.smooth_check.isChecked():
                smooth = smooth_boundary(original, self.smoothing_degree.value(),
                    analysis.cell_width, analysis.cell_height,
                    QgsPointXY(analysis.outlet_x, analysis.outlet_y))
                geometries['suavizada'] = smooth
            self._boundary_layers.update(analysis, query, original, smooth,
                degree=self.smoothing_degree.value(),
                visibility={'original': self.original_check.isChecked(),
                            'suavizada': self.smooth_check.isChecked()})
            self._outlines = geometries
            self._outline_choices[key] = (self.original_check.isChecked(),
                self.smooth_check.isChecked(), self.smoothing_degree.value())
            prefix = ('Divisoria original CAUMAX · 500 m' if key == '500'
                      else f'Divisoria MDT mejorado · {key} m')
            area = format(analysis.area_m2 / 1e6, '.3f').replace('.', ',')
            self._status(f'{prefix.replace("0.5", "0,5")}: {area} km². '
                         'Contorno original y suavizado disponibles en QGIS.', 100)
        except (RuntimeError, ValueError, TypeError) as error:
            self._status('No se puede mostrar/suavizar la divisoria: ' + str(error), 0)
        self._update_controls()

    def _export_boundary(self, *args):
        if self._basin is None or not self._outlines or self._controller.busy:
            return
        from .smooth_export import choose_export_destination
        from .grid import cell_at_point
        from dataclasses import replace
        from osgeo import gdal
        _, analysis, query = self._basin
        raster = gdal.Open(str(query.directions_path), gdal.GA_ReadOnly)
        if raster is None:
            self._status('No se puede exportar: falta el ráster D8 seleccionado.', 0)
            return
        try:
            transform = raster.GetGeoTransform()
            cell = cell_at_point(analysis.outlet_x, analysis.outlet_y,
                transform[0], transform[3] + raster.RasterYSize * transform[5],
                transform[0] + raster.RasterXSize * transform[1], transform[3],
                raster.RasterXSize, raster.RasterYSize)
        finally:
            raster = None
        # Native rational Q/T values are not recalculated by the improved MDT.
        # Never invent them for an independent geometry export.
        export_query = replace(query, cell=cell)
        choose_export_destination(self, analysis, export_query,
                                  self._outlines, self.smoothing_degree.value())

    def _ready(self, key, products, state):
        if self._disposed or self._cleared or key != self._selected_key():
            return
        # Native fields or source files may change while the worker completes.
        if self.refresh_context() or self._controller.query is None:
            return
        self._generation_running = False
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
            self._layers.update(analysis, state, reset_presentation=True)
            prune_managed_drainage(keep_owner=self._layers.owner_key,
                                  protected_paths=tuple(path for path in
                                      (getattr(products, 'dem_path', None),
                                       getattr(products, 'caumax_d8_path', None),
                                       getattr(products, 'tau_d8_path', None))
                                      if path))
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
        self._generation_running = False
        self._queued_prepare = False
        self._status(text, 0)
        # A failed automatic match never silently changes to a manually chosen
        # basin. The optional button remains available for explicit review.

    def _offer_selection(self):
        if (self._disposed or self._cleared or self._selector is not None
                or self._controller.busy):
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
        from .improved_storage import export_directory, record_exports
        destination, _ = QFileDialog.getSaveFileName(self, 'Exportar capa hidrológica',
            str(export_directory() / names[kind]), filters)
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
            if Path(path).is_file():
                try:
                    record_exports(state['metadata']['exports'])
                except OSError as error:
                    self._status('Archivo guardado: ' + str(path) +
                        '. No se ha podido conservar para futuras actualizaciones: ' + str(error), 100)
        except Exception as error:
            self._failed(str(error))

    def _discard_result(self, clear_boundary=False, remove_boundaries=False):
        self._threshold_timer.stop()
        self._close_selector()
        self._products = self._state = None
        self._layers.remove()
        if clear_boundary:
            self._outline_timer.stop()
            self._basin = None
            self._outlines = {}
            self._outline_choices.clear()
            self._queued_resolution = None
            if remove_boundaries:
                self._boundary_layers.remove()
        self._update_controls()

    def _clear_local_delineation(self):
        if self._disposed:
            return
        self._cleared = True
        self._generation_running = False
        self._queued_prepare = False
        self._controller.cancel()
        self._discard_result(clear_boundary=True, remove_boundaries=True)
        self._status('Delineado borrado. Selecciona otra resolución o pulsa Generar capas D8.', 0)

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._queued_prepare = False
        self._threshold_timer.stop()
        self._outline_timer.stop()
        self._close_selector()
        self._controller.dispose()
        from .delineation_cleanup import unregister_participant
        unregister_participant(self)
