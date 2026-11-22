"""Basin window opened from CAUMAX's own maximum-flow query dialog."""
import traceback
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace

from qgis.PyQt.QtCore import Qt, QTimer, QSettings, QEvent
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QSpinBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QMessageBox,
    QHeaderView, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QScrollArea, QWidget,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsMessageLog, QgsPointXY, QgsProject, QgsRasterLayer, QgsTask, QgsWkbTypes,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker

from .parameter_schema import METRICS, format_value, interpret_value
from .metrics import shape_metrics
from .morphology_table import InterpretationDelegate


BLUE_BUTTON_STYLE = '''
QPushButton { background-color: #D9F0FF; color: #17374B; border: 1px solid #7EB4D0;
              border-radius: 3px; padding: 6px 14px; min-height: 20px; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:pressed { background-color: #B1DDF6; }
QPushButton:disabled { background-color: #EEF6FB; color: #8A9EA8; border-color: #C6DAE5; }
'''


class NotesBox(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFixedHeight(88)

    def setText(self, text):
        self.setPlainText(text)

    def text(self):
        return self.toPlainText()


class AnalysisTask(QgsTask):
    def __init__(self, query, callback, outlet=None):
        super().__init__('CAUMAX: cuenca y parámetros morfológicos', QgsTask.CanCancel)
        self.query, self.callback = query, callback
        self.outlet = tuple(outlet) if outlet is not None else (query.x, query.y)
        self.result = None
        self.error = self.trace = ''

    def run(self):
        try:
            from .terrain import analyze_basin
            self.result = analyze_basin(
                self.query.directions_path, self.query.dem_path,
                *self.outlet, cancelled=self.isCanceled,
            )
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error, self.trace = str(error), traceback.format_exc()
            return False

    def finished(self, success):
        self.callback(self, success)


class BasinDialog(QDialog):
    def __init__(self, query, iface, parent=None, saved_session=None):
        super().__init__(parent)
        self.setObjectName('CaumaxCuencaHidrograficaDialog')
        self.setWindowTitle('HidroCAUMAX v1.2.3 · Cuenca hidrográfica')
        self.setStyleSheet(BLUE_BUTTON_STYLE)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.query, self.iface = query, iface
        self.analysis = None
        self._base_geometry_wkb = None
        self._surface_task = None
        self._surface_generation = 0
        self._surface_pending = False
        self._surface_requested = False
        self._surface_valid = True
        self._surface_preferences = {}
        self._surface_flow_preferences = {}
        self._grid_visibility_by_resolution = {}
        self._disposed = False
        self._task = None
        self._rubber = self._grid = self._course = self._marker = self._smooth = None
        self._original_marker = self._snap_link = None
        self._parent_connections = []
        self.preview_geometries = {}
        self._smoothing_ready = False
        self._smoothing_error = ''
        self._smoothing_loading = False
        self._map_data = {}
        self._restored = False
        self._course_layers = None
        self._boundary_layers = None
        self._boundary_adopt_existing = True
        self._terrain_layers = None
        self._drainage_layers = None
        self._drainage_state = None
        self._base_drainage_state = None
        self._drainage_controller = None
        self._drainage_loading = False
        self._quantiles_loading = False
        self._basin_hydrology_controller = None
        self._basin_hydrology_loading = False
        self._drainage_timer = QTimer(self)
        self._drainage_timer.setSingleShot(True)
        self._drainage_timer.setInterval(500)
        self._drainage_timer.timeout.connect(self._prepare_drainage)
        self._resolution_controller = None
        self._active_resolution_key = None
        self._resolution_pending = False
        self._saving_project = False
        self._resolution_loading = False
        self._resolution_discovering = False
        self._smoothing_timer = QTimer(self)
        self._smoothing_timer.setSingleShot(True)
        self._smoothing_timer.setInterval(100)
        self._smoothing_timer.timeout.connect(self._update_smoothing)
        outer = QVBoxLayout(self)
        self.body_scroll = QScrollArea(self)
        self.body_scroll.setWidgetResizable(True)
        body = QWidget(self.body_scroll)
        layout = QVBoxLayout(body)
        self.body_scroll.setWidget(body)
        outer.addWidget(self.body_scroll, 1)
        self.context = QLabel(self)
        self.context.setTextFormat(Qt.PlainText)
        self.context.setWordWrap(True)
        self.context.setText(
            f'Punto seleccionado: X={query.x:.3f}; Y={query.y:.3f} · '
            f'{query.crs.authid() or query.crs.description()}\n'
            f'Caudal CAUMAX original: {query.flow:g} m³/s · T={query.period} años'
        )
        layout.addWidget(self.context)
        group = QGroupBox('Morfología de la Cuenca', self)
        self.morphology_group = group
        box = QVBoxLayout(group)
        self.table = QTableWidget(len(METRICS), 4, group)
        self.table.setHorizontalHeaderLabels(['Parámetro', 'Valor', 'Unidad', 'Interpretación'])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(True)
        self.table.setStyleSheet('''
            QTableWidget { background-color: white; color: #17202A; }
            QTableWidget::item { background-color: white; }
            QTableWidget::item:selected { background-color: #D9F0FF; color: #17202A; }
            QHeaderView::section { background-color: white; color: #17202A; padding: 4px; }
        ''')
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setMinimumSectionSize(20)
        self.table.setItemDelegateForColumn(3, InterpretationDelegate(self.table))
        self.table.setMinimumHeight(280)
        for row, metric in enumerate(METRICS):
            for column, text in enumerate((metric.label, 'Calculando…', metric.unit, 'Calculando…')):
                item = QTableWidgetItem(text)
                item.setToolTip(metric.definition)
                if column in (1, 2):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.timeout.connect(self._fit_rows)
        self.table.horizontalHeader().sectionResized.connect(lambda *args: self._row_timer.start(80))
        self.table.viewport().installEventFilter(self)
        box.addWidget(self.table)
        layout.addWidget(group)
        from .hypsometry import HypsometricWidget
        self.hypsometry_widget = HypsometricWidget(self)
        self.hypsometry_widget.dimensionless_check.toggled.connect(self._hypsometry_mode_changed)
        layout.addWidget(self.hypsometry_widget)
        from .flow_quantiles import FlowQuantileWidget
        self.quantiles_widget = FlowQuantileWidget(self)
        self.quantiles_widget.changed.connect(self._quantiles_changed)
        layout.addWidget(self.quantiles_widget)
        from .basin_hydrology_widget import BasinHydrologyWidget
        basin_hydrology_group = QGroupBox('P₀ y precipitación máxima diaria de la cuenca', self)
        basin_hydrology_layout = QVBoxLayout(basin_hydrology_group)
        self.basin_hydrology_widget = BasinHydrologyWidget(basin_hydrology_group)
        self.basin_hydrology_widget.p0_button.setToolTip(
            'Media por área de la capa P₀ configurada en CAUMAX sobre la divisoria activa, original o suavizada. '
            'La capa P₀ conserva su resolución nativa; no se aplica β.')
        self.basin_hydrology_widget.rainfall_button.setToolTip(
            'Medias por área de las capas de precipitación configuradas en CAUMAX. '
            'Conservan su resolución nativa; el MDT activo determina la divisoria de la cuenca.')
        self.basin_hydrology_widget.calculate_p0.connect(lambda: self._calculate_basin_hydrology('p0'))
        self.basin_hydrology_widget.calculate_rainfall.connect(lambda: self._calculate_basin_hydrology('rainfall'))
        self.basin_hydrology_widget.changed.connect(self._basin_hydrology_changed)
        self.basin_hydrology_widget.cancel_requested.connect(self._cancel_basin_hydrology)
        basin_hydrology_layout.addWidget(self.basin_hydrology_widget)
        layout.addWidget(basin_hydrology_group)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status = NotesBox(self)
        self.status.setText('Delimitando la cuenca y calculando sus parámetros…')
        layout.insertWidget(layout.indexOf(self.morphology_group)+1,self.status)
        smoothing = QGroupBox('Suavizado divisoria', self)
        smoothing_layout = QVBoxLayout(smoothing)
        controls = QHBoxLayout()
        self.original_check = QCheckBox('Original · contorno de celdas', smoothing)
        self.original_check.setChecked(True)
        self.smooth_check = QCheckBox('Suavizada', smoothing)
        self.smoothing_degree = QSpinBox(smoothing)
        self.smoothing_degree.setRange(1, 45)
        self.smoothing_degree.setSingleStep(1)
        self.smoothing_degree.setValue(3)
        self.smoothing_degree.setToolTip('Grado entero de 1 a 45. Recalcula automáticamente morfología, hipsometría, P₀ y Pd sobre la divisoria activa.')
        controls.addWidget(self.original_check)
        controls.addWidget(self.smooth_check)
        controls.addWidget(QLabel('Grado:', smoothing))
        controls.addWidget(self.smoothing_degree)
        controls.addStretch()
        smoothing_layout.addLayout(controls)
        self.smoothing_status = NotesBox(smoothing)
        self.smoothing_status.setFixedHeight(76)
        self.smoothing_status.setText('Original: rojo y malla de celdas. Suavizada: contorno azul en el mapa de QGIS.')
        smoothing_layout.addWidget(self.smoothing_status)
        self.export_button = QPushButton('Exportar shape', smoothing)
        self.export_button.setStyleSheet('QPushButton { padding: 2px 9px; min-height: 0px; }')
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        smoothing_layout.addWidget(self.export_button, 0, Qt.AlignLeft)
        for control in (self.original_check, self.smooth_check, self.smoothing_degree):
            control.setEnabled(False)
        self.original_check.toggled.connect(self._queue_smoothing)
        self.smooth_check.toggled.connect(self._queue_smoothing)
        self.smoothing_degree.valueChanged.connect(self._queue_smoothing)
        resolution_group = QGroupBox('Resolución MDT', self)
        self.resolution_group = resolution_group
        resolution_layout = QVBoxLayout(resolution_group)
        resolution_help = QLabel('La aplicación CAUMAX original utiliza MDT de tamaño de celda 500 m x 500 m. Aquí puede seleccionar una resolución mejorada con MDT oficiales (www.cnig.es). Todos los cálculos se actualizarán automáticamente', resolution_group)
        self.resolution_help = resolution_help
        resolution_help.setWordWrap(True)
        resolution_layout.addWidget(resolution_help)
        self._resolution_checks_layout = QGridLayout()
        resolution_layout.addLayout(self._resolution_checks_layout)
        self.cell_grid_check = QCheckBox('Malla de celdas', resolution_group)
        self.cell_grid_check.setChecked(True)
        self.cell_grid_check.toggled.connect(self._grid_visibility_changed)
        resolution_layout.addWidget(self.cell_grid_check)
        self.resolution_checkboxes = {}
        self._resolution_buttons = QButtonGroup(self)
        self._resolution_buttons.setExclusive(True)
        self.resolution_status = QLabel('El MDT original estará disponible al terminar el cálculo.', resolution_group)
        self.resolution_status.setWordWrap(True)
        resolution_layout.addWidget(self.resolution_status)
        self.resolution_progress = QProgressBar(resolution_group)
        self.resolution_progress.setRange(0, 100)
        self.resolution_progress.hide()
        resolution_layout.addWidget(self.resolution_progress)
        self.resolution_refresh_button = QPushButton('Actualizar opciones', resolution_group)
        self.resolution_refresh_button.setEnabled(False)
        self.resolution_refresh_button.clicked.connect(self._refresh_resolutions)
        self.resolution_refresh_button.hide()
        layout.insertWidget(0, resolution_group)
        layout.insertWidget(1, smoothing)
        drainage_group = QGroupBox('Capas hidrológicas para Método Racional', self)
        drainage_layout = QVBoxLayout(drainage_group)
        drainage_help = QLabel('Prepara el MDT elegido, sus direcciones D8 y la red de drenaje con TauDEM. Para el Método Racional de CAUMAX, exporta las direcciones en formato CAUMAX/ESRI.', drainage_group)
        drainage_help.setWordWrap(True)
        drainage_layout.addWidget(drainage_help)
        preparation = QHBoxLayout()
        self.drainage_prepare_button = QPushButton('Generar capas D8', drainage_group)
        self.drainage_prepare_button.clicked.connect(self._prepare_drainage)
        preparation.addWidget(self.drainage_prepare_button)
        self.taudem_folder_button = QPushButton('Carpeta de TauDEM…', drainage_group)
        self.taudem_folder_button.clicked.connect(self._choose_taudem_folder)
        preparation.addWidget(self.taudem_folder_button)
        self.drainage_cancel_button = QPushButton('Cancelar cálculo', drainage_group)
        self.drainage_cancel_button.clicked.connect(self._cancel_drainage)
        self.drainage_cancel_button.setEnabled(False)
        preparation.addWidget(self.drainage_cancel_button)
        drainage_layout.addLayout(preparation)
        threshold_line = QHBoxLayout()
        threshold_line.addWidget(QLabel('Umbral de inicio de corriente:', drainage_group))
        self.drainage_threshold = QDoubleSpinBox(drainage_group)
        self.drainage_threshold.setDecimals(6)
        self.drainage_threshold.setRange(.000001,1000000)
        self.drainage_threshold.setSuffix(' km²')
        self.drainage_threshold.valueChanged.connect(self._drainage_threshold_changed)
        threshold_line.addWidget(self.drainage_threshold)
        self.drainage_auto = QCheckBox('Automático', drainage_group)
        self.drainage_auto.setChecked(True)
        self.drainage_auto.toggled.connect(self._drainage_auto_changed)
        threshold_line.addWidget(self.drainage_auto)
        self.drainage_visible = QCheckBox('Mostrar red', drainage_group)
        self.drainage_visible.setChecked(True)
        self.drainage_visible.toggled.connect(self._drainage_visibility_changed)
        threshold_line.addWidget(self.drainage_visible)
        drainage_layout.addLayout(threshold_line)
        density_help = QLabel('Un umbral menor produce una red más densa. El valor automático es una propuesta inicial y puedes editarlo.', drainage_group)
        density_help.setWordWrap(True)
        drainage_layout.addWidget(density_help)
        exports = QGridLayout()
        exports.addWidget(QLabel('Modelo de elevaciones',drainage_group),0,0)
        self.dem_export_button = QPushButton('Exportar MDT (TIFF)…',drainage_group)
        self.dem_export_button.clicked.connect(lambda:self._export_hydrology('dem'))
        exports.addWidget(self.dem_export_button,0,2)
        exports.addWidget(QLabel('Dirección de flujo D8',drainage_group),1,0)
        self.direction_encoding = QComboBox(drainage_group)
        self.direction_encoding.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.direction_encoding.setMinimumContentsLength(9)
        self.direction_encoding.addItem('CAUMAX / ESRI · Método Racional','caumax')
        self.direction_encoding.addItem('TauDEM · códigos 1 a 8','taudem')
        self.direction_encoding.currentIndexChanged.connect(self._direction_encoding_changed)
        exports.addWidget(self.direction_encoding,1,1)
        self.directions_export_button = QPushButton('Exportar dirección (TIFF)…',drainage_group)
        self.directions_export_button.clicked.connect(lambda:self._export_hydrology('directions'))
        exports.addWidget(self.directions_export_button,1,2)
        exports.addWidget(QLabel('Red de drenaje',drainage_group),2,0)
        self.network_export_button = QPushButton('Exportar red (SHP)…',drainage_group)
        self.network_export_button.clicked.connect(lambda:self._export_hydrology('network'))
        exports.addWidget(self.network_export_button,2,2)
        drainage_layout.addLayout(exports)
        self.drainage_status = QLabel('Selecciona un MDT mejorado para preparar sus capas.',drainage_group)
        self.drainage_status.setWordWrap(True)
        drainage_layout.addWidget(self.drainage_status)
        self.drainage_progress = QProgressBar(drainage_group)
        self.drainage_progress.setRange(0,100)
        self.drainage_progress.hide()
        drainage_layout.addWidget(self.drainage_progress)
        taudem_help = QLabel('<a href="https://hydrology.usu.edu/taudem/taudem5/downloads.html">Instalación oficial de TauDEM y MPI</a>',drainage_group)
        taudem_help.setOpenExternalLinks(True)
        drainage_layout.addWidget(taudem_help)
        layout.addWidget(drainage_group)
        for label in drainage_group.findChildren(QLabel):
            label.setWordWrap(True)
        self._refresh_drainage_controls()
        reports = QGroupBox('Informe', self)
        report_layout = QHBoxLayout(reports)
        self.report_button = QPushButton('Informe (PDF)', reports)
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._report)
        report_layout.addWidget(self.report_button)
        self.save_project_button = QPushButton('Guardar proyecto', reports)
        self.save_project_button.setEnabled(False)
        self.save_project_button.setToolTip('Calcula y guarda todas las resoluciones disponibles de la consulta; la primera preparación puede tardar.')
        self.save_project_button.clicked.connect(self._choose_save_project)
        report_layout.addWidget(self.save_project_button)
        self.open_project_button = QPushButton('Abrir proyecto', reports)
        self.open_project_button.clicked.connect(self._choose_open_project)
        report_layout.addWidget(self.open_project_button)
        outer.addWidget(reports)
        buttons = QDialogButtonBox(self)
        self.close_button = buttons.addButton('Cerrar', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.close)
        outer.addWidget(buttons)
        self.finished.connect(self.dispose)
        self.iface.mapCanvas().destinationCrsChanged.connect(self._preview)
        self.iface.mapCanvas().extentsChanged.connect(self._preview)
        if parent is not None and hasattr(parent, 'finished') and saved_session is None:
            self._connect_parent(parent.finished, self.reject)
            for name in ('txtX', 'txtY'):
                field = getattr(parent, name, None)
                if field is not None:
                    self._connect_parent(field.textChanged, self._parent_query_changed)
            for button in getattr(parent, 'buttonsAndLayers', {}):
                self._connect_parent(button.toggled, self._parent_query_changed)
        self._qgis_window = getattr(self.iface, 'mainWindow', lambda: None)()
        self._placement_timer = QTimer(self)
        self._placement_timer.setSingleShot(True)
        self._placement_timer.timeout.connect(self._place_beside_map)
        if self._qgis_window is not None:
            self._qgis_window.installEventFilter(self)
        available = (self._qgis_window.frameGeometry() if self._qgis_window is not None
                     else QApplication.desktop().availableGeometry(self))
        self.resize(round(available.width() * .36), max(300, available.height() - 40))
        self._row_timer.start(0)
        if saved_session is None:
            self._start()
        else:
            self._restore_session(saved_session)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_row_timer'):
            self._row_timer.start(80)

    def showEvent(self, event):
        super().showEvent(event)
        self._placement_timer.start(0)

    def eventFilter(self, watched, event):
        if (not getattr(self, '_disposed', True)
                and watched is getattr(self, '_qgis_window', None)
                and event.type() in (QEvent.Move, QEvent.Resize, QEvent.WindowStateChange)):
            self._placement_timer.start(0)
        if (hasattr(self, '_row_timer') and watched is self.table.viewport()
                and event.type() == QEvent.Resize):
            self._row_timer.start(0)
        return super().eventFilter(watched, event)

    def _place_beside_map(self):
        if self._disposed or self._qgis_window is None or self._qgis_window.isMinimized():
            return
        available = QApplication.desktop().availableGeometry(self._qgis_window)
        target = self._qgis_window.frameGeometry().intersected(available)
        frame = self.frameGeometry()
        border_width = max(0, frame.width() - self.width())
        border_height = max(0, frame.height() - self.height())
        self.resize(max(1, round(target.width() * .36) - border_width),
                    max(1, target.height() - border_height))
        self.move(target.right() - self.frameGeometry().width() + 1, target.top())

    def _fit_rows(self):
        if self._disposed:
            return
        width = self.table.viewport().width()
        header = self.table.horizontalHeader()
        blocked = header.blockSignals(True)
        try:
            widths = [round(width * ratio) for ratio in (.46, .13, .09)]
            widths.append(max(20, width - sum(widths)))
            for column, value in enumerate(widths):
                self.table.setColumnWidth(column, value)
        finally:
            header.blockSignals(blocked)
        self.table.resizeRowsToContents()

    def _connect_parent(self, signal, slot):
        signal.connect(slot)
        self._parent_connections.append((signal, slot))

    def _parent_query_changed(self, *args):
        self.reject()

    def _start(self):
        try:
            directions = QgsRasterLayer(str(self.query.directions_path), 'Direcciones de drenaje', 'gdal')
            if not directions.isValid() or not directions.crs().isValid():
                raise ValueError('Las direcciones de drenaje no tienen un sistema de referencia válido.')
            point = QgsCoordinateTransform(self.query.crs, directions.crs(), QgsProject.instance()).transform(
                QgsPointXY(self.query.x, self.query.y))
            task = AnalysisTask(self.query, self._completed, (point.x(), point.y()))
            self._task = task
            QgsApplication.taskManager().addTask(task)
        except Exception as error:
            self._failed(str(error))

    def _failed(self, message):
        self.progress.hide()
        for row, metric in enumerate(METRICS):
            self.table.item(row, 1).setText(format_value(metric.key, None))
            self.table.item(row, 3).setText(interpret_value(metric.key, None))
        self.status.setText(message)
        self._row_timer.start(0)

    def _completed(self, task, success):
        if self._disposed or task is not self._task:
            return
        self._task = None
        self.progress.hide()
        if not success:
            self._failed(task.error or 'Cálculo cancelado.')
            if task.trace:
                QgsMessageLog.logMessage(task.trace, 'CAUMAX · Cuenca', Qgis.Warning)
            return
        self.analysis = task.result
        self._base_geometry_wkb = bytes(self.analysis.geometry_wkb)
        self._display_analysis()
        from .boundary_smoothing import suggested_degree
        original = QgsGeometry()
        original.fromWkb(bytes(self.analysis.geometry_wkb))
        try:
            degree = suggested_degree(original, self.analysis.cell_width, self.analysis.cell_height,
                                      QgsPointXY(self.analysis.outlet_x, self.analysis.outlet_y))
        except ValueError:
            degree = 3.0
        self.smoothing_degree.blockSignals(True)
        self.smoothing_degree.setValue(max(1, round(degree)))
        self.smoothing_degree.blockSignals(False)
        self._update_smoothing()
        self._setup_resolutions()

    def _display_analysis(self, quantile_snapshot=None):
        key = f'{min(self.analysis.cell_width, self.analysis.cell_height):g}'
        visible = self._grid_visibility_by_resolution.get(key, key in ('500', '25'))
        self.cell_grid_check.blockSignals(True)
        self.cell_grid_check.setChecked(visible)
        self.cell_grid_check.blockSignals(False)
        for widget in (self.table, self.hypsometry_widget, self.quantiles_widget):
            widget.setEnabled(not self._surface_pending)
        self.hypsometry_widget.set_analysis(self.analysis)
        self._basin_hydrology_loading = True
        self.basin_hydrology_widget.set_analysis(self.analysis)
        self._basin_hydrology_loading = False
        self._quantiles_loading=True
        self.quantiles_widget.set_query(self.query, snapshot=quantile_snapshot)
        self._quantiles_loading=False
        context = (
            f'Punto original de consulta: X={self.query.x:.3f}; Y={self.query.y:.3f} · '
            f'{self.query.crs.authid() or self.query.crs.description()}\n'
            f'Caudal CAUMAX original: {self.query.flow:g} m³/s · T={self.query.period} años\n'
            f'MDT activo: {self.analysis.cell_width:g} m × {self.analysis.cell_height:g} m')
        snap = self.analysis.diagnostics.get('outlet_snap')
        if snap:
            crs = QgsCoordinateReferenceSystem.fromWkt(self.analysis.crs_wkt)
            context += (
                f'\nCierre ajustado al cauce (snap): X={self.analysis.outlet_x:.3f}; '
                f'Y={self.analysis.outlet_y:.3f} · {crs.authid() or crs.description()}\n'
                f"Desplazamiento desde el punto original: {snap['distance_m']:.2f} m. "
                'Mapa: original naranja; cierre ajustado azul.')
        elif self.analysis.diagnostics.get('dem_hydrology'):
            context += '\nResultado guardado sin ajuste al cauce. Una nueva consulta aplica el snap automático.'
        self.context.setText(context)
        self.progress.hide()
        for row, metric in enumerate(METRICS):
            self.table.item(row, 1).setText(format_value(metric.key, self.analysis.values.get(metric.key)))
            interpretation = interpret_value(metric.key, self.analysis.values.get(metric.key), self.analysis.values)
            self.table.item(row, 3).setText(interpretation)
            self.table.item(row, 3).setToolTip(interpretation + '\n' + metric.definition)
        self._row_timer.start(0)
        crs = QgsCoordinateReferenceSystem.fromWkt(self.analysis.crs_wkt)
        summary = (
            f'Malla de drenaje: {self.analysis.cell_width:g} × {self.analysis.cell_height:g} m. '
            f'Centro de celda de cierre: X={self.analysis.outlet_x:.3f}; Y={self.analysis.outlet_y:.3f} '
            f'({crs.authid() or crs.description()}).'
        )
        if self.analysis.notes:
            summary += '\n' + '\n'.join(self.analysis.notes)
        if self.analysis.diagnostics.get('analysis_surface'):
            summary += '\nCálculos actualizados sobre la divisoria suavizada activa.'
        else:
            summary += '\nCálculos sobre la divisoria original de celdas.'
        self.status.setText(summary)
        self.report_button.setEnabled(True)
        self.save_project_button.setEnabled(True)
        self.original_check.setEnabled(True)
        self.smooth_check.setEnabled(True)
        self.smoothing_degree.setEnabled(self.smooth_check.isChecked())
        if self._terrain_layers is None:
            from .terrain_layers import TerrainLayers
            self._terrain_layers = TerrainLayers(self.iface, retain_variants=True)
        try:
            self._terrain_layers.update(self.analysis, self.query)
            if self._terrain_layers.note:
                self.status.setText(self.status.text() + '\n' + self._terrain_layers.note)
        except Exception as error:
            self.status.setText(self.status.text() + '\nNo se ha podido representar el relieve: ' + str(error))
        if self._course_layers is None:
            from .course_layers import CourseLayers
            self._course_layers = CourseLayers(self.iface)
        self._course_layers.update(self.analysis, self.query)
        if self._course_layers.note:
            self.status.setText(self.status.text() + '\n' + self._course_layers.note)
        self._display_drainage_layers()
        self._refresh_basin_hydrology_controls()

    def _setup_resolutions(self, resolution_state=None):
        from .resolution_controller import ResolutionController
        if self._resolution_controller is not None:
            self._resolution_controller.dispose()
        if resolution_state:
            variants = resolution_state['variants']
            base_key = '500' if '500' in variants else resolution_state['active_key']
            base = variants[base_key]
            base_analysis, base_query = base['analysis'], base['query']
        else:
            base_analysis, base_query = self.analysis, self.query
        controller = ResolutionController(base_analysis, base_query,
            self._resolution_options, self._resolution_result, self._resolution_status,
            self._resolution_error, parent=self)
        self._resolution_controller = controller
        self._active_resolution_key = resolution_state['active_key'] if resolution_state else controller.active_key
        self.resolution_refresh_button.setEnabled(True)
        if resolution_state:
            self._resolution_loading = True
            try:
                controller.restore(resolution_state)
            finally:
                self._resolution_loading = False
        else:
            self._resolution_options(controller.options)
            self._cache_variant_state()
            self._resolution_discovering = True
            self.save_project_button.setEnabled(False)
            controller.discover()

    def _hypsometry_mode_changed(self, *args):
        if self._smoothing_loading or self._disposed:
            return
        self._map_data = {}
        self._restored = False
        self._cache_variant_state()

    def _quantiles_changed(self,*args):
        if self._quantiles_loading or self._smoothing_loading or self._disposed:return
        self._cache_variant_state()

    def _basin_hydrology_changed(self, *args):
        if self._basin_hydrology_loading or self._smoothing_loading or self._disposed:
            return
        self._cache_variant_state()

    def _basin_hydrology_busy(self):
        return bool(self._surface_pending or
                    (self._basin_hydrology_controller and self._basin_hydrology_controller.busy))

    def _calculate_basin_hydrology(self, kind):
        if (self._disposed or self.analysis is None or self._saving_project
                or self._resolution_pending or self._surface_pending or not self._surface_valid):
            return
        if self._drainage_timer.isActive() or (self._drainage_controller and self._drainage_controller.busy):
            self.basin_hydrology_widget.set_busy(False, 'Espera a que termine la actualización de la cuenca y su red.')
            return
        try:
            from .basin_hydrology import source_paths
            if self._basin_hydrology_controller is None:
                from .basin_hydrology_controller import BasinHydrologyController
                self._basin_hydrology_controller = BasinHydrologyController(
                    self._basin_hydrology_ready, self._basin_hydrology_status,
                    self._basin_hydrology_failed, self)
            # CAUMAX's configured source paths are resolved on the GUI thread.
            self._basin_hydrology_controller.calculate(kind, self.analysis, source_paths(self.query))
            self._refresh_basin_hydrology_controls()
        except Exception as error:
            self._basin_hydrology_failed(str(error))

    def _refresh_basin_hydrology_controls(self):
        busy = self._basin_hydrology_busy()
        drainage_busy = bool(self._drainage_timer.isActive()
            or (self._drainage_controller and self._drainage_controller.busy))
        available = self.analysis is not None and not self._resolution_pending and self._surface_valid
        self.basin_hydrology_widget.set_busy(busy)
        if not busy:
            self.basin_hydrology_widget.setEnabled(available and not self._saving_project and not drainage_busy)
        else:
            self.basin_hydrology_widget.setEnabled(True)
        self.report_button.setEnabled(available and not busy and not drainage_busy and not self._saving_project)
        self.save_project_button.setEnabled(available and not busy and not drainage_busy
            and not self._saving_project and not self._resolution_discovering)
        self.resolution_refresh_button.setEnabled(not busy and not self._saving_project and not self._resolution_pending)
        self.export_button.setEnabled(available and self._smoothing_ready and not busy and not self._saving_project)
        self._refresh_drainage_controls()

    def _basin_hydrology_status(self, text, progress):
        if not self._disposed:
            self.basin_hydrology_widget.set_busy(True, f'{text} {progress:.0f} %')

    def _basin_hydrology_ready(self, fingerprint, kind, result):
        if self._disposed or self.analysis is None:
            return
        from .basin_hydrology import basin_fingerprint
        if fingerprint == basin_fingerprint(self.analysis):
            try:
                self.basin_hydrology_widget.set_result(kind, result)
                self._cache_variant_state()
            except Exception as error:
                self._basin_hydrology_failed(str(error))
                return
        self._refresh_basin_hydrology_controls()

    def _basin_hydrology_failed(self, text):
        if not self._disposed:
            self._refresh_basin_hydrology_controls()
            self.basin_hydrology_widget.set_busy(False, text)

    def _cancel_basin_hydrology(self):
        if self._surface_pending:
            self._cancel_surface()
            self._surface_valid = False
            self._refresh_basin_hydrology_controls()
            self.basin_hydrology_widget.set_busy(False, 'Recálculo cancelado. Cambia el grado para volver a calcular la cuenca.')
            return
        was_busy = self._basin_hydrology_busy()
        if self._basin_hydrology_controller is not None:
            self._basin_hydrology_controller.cancel()
        if was_busy and not self._disposed:
            self._refresh_basin_hydrology_controls()
            self.basin_hydrology_widget.set_busy(False, 'Cálculo cancelado; se conservan los resultados anteriores.')

    def _display_drainage_layers(self):
        if self._drainage_layers is None:
            from .drainage_layers import DrainageLayers
            self._drainage_layers = DrainageLayers(self.iface)
        visible = bool((self._drainage_state or {}).get('network_visible',
                       (self._drainage_state or {}).get('metadata',{}).get('visible',True)))
        self._drainage_layers.update(self.analysis,self._drainage_state,visible=visible)

    def _refresh_drainage_controls(self):
        if not hasattr(self,'drainage_prepare_button'):
            return
        analysis = self.analysis
        busy = bool(self._drainage_controller and self._drainage_controller.busy)
        improved = bool(analysis is not None and min(analysis.cell_width,analysis.cell_height)<500)
        allowed = improved and not self._resolution_pending and not self._saving_project and not self._basin_hydrology_busy()
        state = self._drainage_state
        exported_dem = (state or {}).get('metadata',{}).get('exports',{}).get('dem','')
        source_available = bool(analysis is not None and (Path(self.query.dem_path).is_file()
                                or (exported_dem and Path(exported_dem).is_file())))
        self._drainage_loading = True
        if analysis is not None and not busy:
            minimum = analysis.cell_width*analysis.cell_height
            recommended = max(100*minimum,.01*analysis.area_m2)
            chosen = float(state['threshold_area_m2']) if state else recommended
            self.drainage_threshold.setRange(minimum/1e6,max(analysis.area_m2*2,chosen,recommended)/1e6)
            self.drainage_threshold.setValue(chosen/1e6)
        if state and not busy:
            self.drainage_auto.setChecked(state['automatic'])
            self.drainage_visible.setChecked(state.get('network_visible',state.get('metadata',{}).get('visible',True)))
            index = self.direction_encoding.findData(state['flow_encoding'])
            self.direction_encoding.setCurrentIndex(max(0,index))
        elif not state and not busy:
            self.drainage_auto.setChecked(True)
            self.drainage_visible.setChecked(True)
            self.direction_encoding.setCurrentIndex(0)
        self._drainage_loading = False
        self.drainage_prepare_button.setEnabled(allowed and source_available and not busy)
        self.taudem_folder_button.setEnabled(not busy)
        self.drainage_cancel_button.setEnabled(busy)
        self.drainage_threshold.setEnabled(bool(state) and allowed and source_available and not busy)
        self.drainage_auto.setEnabled(bool(state) and allowed and source_available and not busy)
        self.drainage_visible.setEnabled(bool(state))
        self.direction_encoding.setEnabled(bool(state) and not busy)
        for button in (self.dem_export_button,self.directions_export_button,self.network_export_button):
            button.setEnabled(bool(state) and not busy and not self._resolution_pending)
        if not improved:
            self.drainage_status.setText('Selecciona un MDT mejorado para preparar sus capas.')
        elif state and not busy:
            self.drainage_status.setText(
                f"TauDEM · umbral {state['threshold_area_m2']/1e6:g} km² · "
                f"{len(state['features'])} tramos. Cada archivo se exporta por separado.")
        elif not busy:
            self.drainage_status.setText('Pulsa Generar capas D8. El MDT y las direcciones conservarán la misma malla.')

    def _choose_taudem_folder(self):
        initial = str(QSettings().value('HidroCAUMAX/taudem_bin',''))
        folder = QFileDialog.getExistingDirectory(self,'Carpeta de ejecutables TauDEM',initial)
        if folder:
            from .taudem_engine import probe_taudem
            info = probe_taudem(bin_dir=folder)
            if not info.get('available'):
                QMessageBox.warning(self,'TauDEM',info.get('reason','La carpeta no contiene las herramientas necesarias.'))
                return
            QSettings().setValue('HidroCAUMAX/taudem_bin',folder)
            self.drainage_status.setText('TauDEM disponible. Puedes generar las capas del MDT activo.')

    def _prepare_drainage(self, *args):
        self._drainage_timer.stop()
        if self._disposed or self.analysis is None or self._resolution_pending or self._saving_project or self._basin_hydrology_busy():
            return
        if min(self.analysis.cell_width,self.analysis.cell_height)>=500:
            return
        try:
            from .taudem_engine import probe_taudem
            bin_dir = str(QSettings().value('HidroCAUMAX/taudem_bin','')) or None
            info = probe_taudem(bin_dir=bin_dir)
            if not info.get('available'):
                self.drainage_status.setText(info.get('reason','Instala TauDEM y MPI o selecciona su carpeta.'))
                return
            if self._drainage_controller is None:
                from .drainage_controller import DrainageController
                self._drainage_controller = DrainageController(self._drainage_ready,
                    self._drainage_status,self._drainage_failed,self)
            controller = self._resolution_controller
            reference = controller.base_analysis if controller is not None else self.analysis
            key = self._active_resolution_key or str(self.analysis.cell_width)
            cache = controller.cache_dir/'taudem'/key if controller is not None else Path(self.query.dem_path).parent/'taudem'
            automatic = self.drainage_auto.isChecked()
            threshold = None if automatic else self.drainage_threshold.value()*1e6
            self.report_button.setEnabled(False)
            self.save_project_button.setEnabled(False)
            self.export_button.setEnabled(False)
            source_query=self.query
            if not Path(source_query.dem_path).is_file() and self._drainage_state:
                exported=self._drainage_state.get('metadata',{}).get('exports',{}).get('dem','')
                if exported and Path(exported).is_file():source_query=replace(source_query,dem_path=exported)
            self._drainage_controller.prepare(key,self.analysis,source_query,reference,cache,
                threshold_area_m2=threshold,automatic=automatic,
                encoding=self.direction_encoding.currentData(),bin_dir=bin_dir)
            self._refresh_drainage_controls()
        except Exception as error:
            self._drainage_failed(str(error))

    def _drainage_status(self,text,progress):
        if not self._disposed:
            self.drainage_status.setText(text)
            self.drainage_progress.setValue(int(progress))
            self.drainage_progress.show()

    def _drainage_ready(self,key,products,state):
        if self._disposed or key != (self._active_resolution_key or str(self.analysis.cell_width)):
            return
        previous = self.analysis
        live_visibility = (self._drainage_layers.visibility()
            if self._drainage_state is not None and self._drainage_layers is not None
            else {'network':self.drainage_visible.isChecked(),'directions':False})
        state['metadata']['visible'] = live_visibility['network']
        state['network_visible'] = live_visibility['network']
        state['directions_visible'] = live_visibility['directions']
        self._drainage_state = state
        self._base_drainage_state = deepcopy(state)
        self.analysis = products.analysis
        self._base_geometry_wkb = bytes(self.analysis.geometry_wkb)
        self.query = replace(self.query,dem_path=str(products.dem_path),directions_path=str(products.caumax_d8_path))
        controller = self._resolution_controller
        if controller is not None:
            controller.variants[key].update(analysis=self.analysis,query=self.query,smoothing_state=None)
        self._map_data = {}
        self._restored = False
        self._display_analysis()
        if (previous.geometry_wkb != self.analysis.geometry_wkb
                or previous.outlet_x != self.analysis.outlet_x or previous.outlet_y != self.analysis.outlet_y):
            self.preview_geometries = {}
        self._update_smoothing()
        self._refresh_drainage_controls()
        self.drainage_progress.hide()
        self.report_button.setEnabled(True)
        self.save_project_button.setEnabled(True)
        self._cache_variant_state()

        if self.smooth_check.isChecked():
            self._queue_smoothing()

    def _drainage_failed(self,text):
        if self._disposed:return
        self.drainage_progress.hide()
        self._refresh_drainage_controls()
        self.drainage_status.setText(text)
        self.report_button.setEnabled(self.analysis is not None)
        self.save_project_button.setEnabled(self.analysis is not None)
        self.export_button.setEnabled(self._smoothing_ready)

    def _cancel_drainage(self):
        self._drainage_timer.stop()
        if self._drainage_controller is not None:self._drainage_controller.cancel()
        if hasattr(self,'drainage_progress'):self.drainage_progress.hide()
        self._refresh_drainage_controls()
        if hasattr(self,'report_button'):
            self.report_button.setEnabled(self.analysis is not None)
            self.save_project_button.setEnabled(self.analysis is not None)
            self.export_button.setEnabled(self._smoothing_ready)

    def _drainage_threshold_changed(self,*args):
        if self._drainage_loading or not self._drainage_state:return
        self._drainage_loading=True
        self.drainage_auto.setChecked(False)
        self._drainage_loading=False
        self._mark_drainage_pending()
        self._drainage_timer.start()

    def _mark_drainage_pending(self):
        for name in ('report_button','save_project_button','export_button','network_export_button'):
            button=getattr(self,name,None)
            if button is not None:button.setEnabled(False)

    def _drainage_auto_changed(self,automatic):
        if self._drainage_loading or not self._drainage_state:return
        if automatic:
            self._drainage_loading=True
            self.drainage_threshold.setValue(self._drainage_state['recommended_threshold_area_m2']/1e6)
            self._drainage_loading=False
            self._mark_drainage_pending()
            self._drainage_timer.start()
        else:
            self._drainage_state['automatic']=False
            self._drainage_state['metadata']['automatic']=False
            self._cache_variant_state()

    def _drainage_visibility_changed(self,visible):
        if self._drainage_loading or not self._drainage_state:return
        self._drainage_state['metadata']['visible']=bool(visible)
        self._drainage_state['network_visible']=bool(visible)
        self._display_drainage_layers()
        self._map_data={};self._restored=False

    def _direction_encoding_changed(self,*args):
        if self._drainage_loading or not self._drainage_state:return
        self._drainage_state['flow_encoding']=self.direction_encoding.currentData()
        self._cache_variant_state()

    def _export_hydrology(self,kind):
        if self._drainage_state is None:return
        if self._drainage_timer.isActive():
            self._drainage_timer.stop()
            self._prepare_drainage()
            return
        if self._drainage_controller is not None and self._drainage_controller.busy:return
        state=self._drainage_state
        products=self._drainage_controller.products.get(self._active_resolution_key) if self._drainage_controller else None
        if products is None:
            paths={k:state[k] for k in ('dem_path','tau_d8_path','caumax_d8_path','network_path')}
            exported=state['metadata'].get('exports',{})
            for key,export_key in (('dem_path','dem'),('tau_d8_path','directions_taudem'),('caumax_d8_path','directions_caumax'),('network_path','network')):
                if not Path(paths[key]).is_file() and exported.get(export_key):paths[key]=exported[export_key]
            products=SimpleNamespace(analysis=self.analysis,metadata=state['metadata'],**paths)
        size=f'{self.analysis.cell_width:g}m'
        names={'dem':f'MDT_{size}.tif','directions':f'Direccion_D8_{self.direction_encoding.currentData()}_{size}.tif','network':f'Red_drenaje_{size}.shp'}
        filters='Shapefile (*.shp)' if kind=='network' else 'GeoTIFF (*.tif *.tiff)'
        destination,_=QFileDialog.getSaveFileName(self,'Exportar capa hidrológica',str(Path.home()/names[kind]),filters)
        if not destination:return
        try:
            from .hydro_export import export_dem,export_directions,export_network
            if kind=='dem':path=export_dem(products,destination)
            elif kind=='directions':path=export_directions(products,destination,encoding=self.direction_encoding.currentData())
            else:path=export_network(products,destination,snapshot=state)
            export_key='directions_'+self.direction_encoding.currentData() if kind=='directions' else kind
            state['metadata'].setdefault('exports',{})[export_key]=str(path)
            self.drainage_status.setText('Archivo guardado: '+str(path))
            self._cache_variant_state()
        except Exception as error:
            QMessageBox.critical(self,'Exportar capa hidrológica',str(error))

    def _resolution_options(self, options):
        if self._resolution_discovering:
            self._resolution_discovering = False
            if not self._saving_project and not self._resolution_pending and not self._basin_hydrology_busy():
                self.save_project_button.setEnabled(self.analysis is not None)
        while self._resolution_checks_layout.count():
            item = self._resolution_checks_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                self._resolution_buttons.removeButton(widget)
                widget.deleteLater()
        self.resolution_checkboxes = {}
        for index, option in enumerate(options):
            key = option['key']
            text = f"{option['resolution']:g} m × {option['resolution']:g} m"
            if option.get('provider') == 'CAUMAX':
                text += ' · CAUMAX original'
            box = QCheckBox(text, self)
            box.setToolTip(option.get('label','') + '\n' + option.get('status',''))
            box.setEnabled(bool(option.get('available')) and not self._saving_project)
            self._resolution_buttons.addButton(box)
            box.setChecked(key == self._active_resolution_key)
            box.clicked.connect(lambda checked=False, key=key: self._select_resolution(key))
            self._resolution_checks_layout.addWidget(box, index // 3, index % 3)
            self.resolution_checkboxes[key] = box

    def _resolution_status(self, text):
        if self._disposed:
            return
        self.resolution_status.setText(str(text))
        controller = self._resolution_controller
        if controller is not None:
            self.resolution_progress.setValue(int(controller.progress))
            self.resolution_progress.setVisible(controller.busy or self._saving_project)

    def _resolution_error(self, text):
        if self._disposed:
            return
        self._resolution_pending = False
        self._resolution_discovering = False
        self.resolution_status.setText(str(text))
        self.resolution_progress.hide()
        controller = self._resolution_controller
        if controller is not None:
            self._resolution_options(controller.options)
        if self._saving_project:
            return
        self.resolution_refresh_button.setEnabled(True)
        self.report_button.setEnabled(self.analysis is not None)
        self.save_project_button.setEnabled(self.analysis is not None)
        self.export_button.setEnabled(self._smoothing_ready)
        self._refresh_basin_hydrology_controls()

    def _refresh_resolutions(self):
        if self._resolution_controller is not None and not self._saving_project:
            self._resolution_discovering = True
            self.save_project_button.setEnabled(False)
            self._resolution_controller.discover()

    def _cache_variant_state(self):
        controller = self._resolution_controller
        key = self._active_resolution_key
        if (controller is not None and key in controller.variants and self.analysis is not None
                and self._surface_valid and not self._surface_pending):
            controller.variants[key]['analysis'] = self.analysis
            controller.variants[key]['smoothing_state'] = self.get_smoothing_state()

    def _select_resolution(self, key):
        if self._disposed or self._saving_project or self._resolution_controller is None:
            return
        self._cancel_drainage()
        self._cancel_basin_hydrology()
        self._cancel_surface()
        self._cache_variant_state()
        if key == self._active_resolution_key:
            if self._resolution_pending:
                self._resolution_controller.cancel()
                self._resolution_pending = False
                self.resolution_progress.hide()
                self.resolution_refresh_button.setEnabled(True)
                self.report_button.setEnabled(True)
                self.save_project_button.setEnabled(not self._resolution_discovering)
                self.export_button.setEnabled(self._smoothing_ready)
                self.resolution_status.setText('Se mantiene el MDT activo; descarga anterior cancelada.')
            self._queue_smoothing()
            return
        if self._resolution_pending and self._resolution_controller.requested_key == key:
            return
        self._resolution_pending = True
        self.basin_hydrology_widget.setEnabled(False)
        self.report_button.setEnabled(False)
        self.save_project_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.resolution_progress.show()
        self.resolution_refresh_button.setEnabled(False)
        self._resolution_controller.select(key)

    def _resolution_result(self, key, analysis, query):
        if self._disposed:
            return
        previous_hydrology = self.basin_hydrology_widget.snapshot() or {}
        previous_quantiles = self.quantiles_widget.snapshot() or {}
        self._cache_variant_state()
        controller = self._resolution_controller
        state = controller.variants[key].get('smoothing_state')
        self._drainage_state = deepcopy(state.get('drainage_state')) if state else None
        self._base_drainage_state = deepcopy((state or {}).get('base_drainage_state') or self._drainage_state)
        self.analysis, self.query = analysis, query
        self._base_geometry_wkb = bytes((state or {}).get('base_geometry_wkb', analysis.geometry_wkb))
        self._surface_valid = True
        self._active_resolution_key = key
        self._resolution_pending = False
        self.resolution_refresh_button.setEnabled(True)
        self._display_analysis()
        if state:
            self._apply_smoothing_snapshot(state)
        else:
            self._basin_hydrology_loading = self._quantiles_loading = True
            hydrology = self.basin_hydrology_widget.snapshot()
            hydrology.update({k:previous_hydrology[k] for k in
                ('input_period', 'input_precipitation', 'extrapolate') if k in previous_hydrology})
            self.basin_hydrology_widget.set_analysis(self.analysis, state=hydrology)
            quantiles = self.quantiles_widget.snapshot()
            quantiles.update({k:previous_quantiles[k] for k in
                ('input_period', 'input_flow', 'extrapolate') if k in previous_quantiles})
            self.quantiles_widget.restore(quantiles)
            self._basin_hydrology_loading = self._quantiles_loading = False
            self._map_data = {}
            self._restored = False
            from .boundary_smoothing import suggested_degree
            source = QgsGeometry()
            source.fromWkb(bytes(analysis.geometry_wkb))
            try:
                degree = suggested_degree(source,analysis.cell_width,analysis.cell_height,
                                          QgsPointXY(analysis.outlet_x,analysis.outlet_y))
                self.smoothing_degree.blockSignals(True)
                self.smoothing_degree.setValue(max(1, round(degree)))
                self.smoothing_degree.blockSignals(False)
            except ValueError:
                pass
            self._update_smoothing()
        self._resolution_options(controller.options)
        self.resolution_progress.hide()
        self._cache_variant_state()
        if not self._resolution_loading:
            self._queue_smoothing()

    def _resolution_metadata(self, key=None):
        controller = self._resolution_controller
        key = key or self._active_resolution_key
        if controller is None:
            return {}
        option = next((o for o in controller.options if o['key'] == key), {})
        return dict(active_key=key, label=option.get('label',''), resolution=option.get('resolution'),
                    provider=option.get('provider',''),
                    source=option.get('metadata_url') or option.get('url',''))

    def _queue_smoothing(self, *args):
        if self._smoothing_loading or self._disposed:
            return
        if self.analysis is None:
            return
        if self._base_geometry_wkb is None:
            self._base_geometry_wkb = bytes(self.analysis.geometry_wkb)
        self._cancel_drainage()
        if self._basin_hydrology_controller is not None:
            self._basin_hydrology_controller.cancel()
        self._cancel_surface()
        self._surface_pending = True
        self._surface_requested = True
        self._surface_valid = False
        snapshot = self.basin_hydrology_widget.snapshot() or {}
        if snapshot:
            self._surface_preferences = {k:snapshot[k] for k in
                ('input_period', 'input_precipitation', 'extrapolate')}
        self._surface_flow_preferences = {k:v for k,v in self.quantiles_widget.snapshot().items()
                                         if k in ('input_period', 'input_flow', 'extrapolate')}
        self._basin_hydrology_loading = True
        self.basin_hydrology_widget.set_analysis(None)
        self._basin_hydrology_loading = False
        for widget in (self.table, self.hypsometry_widget, self.quantiles_widget):
            widget.setEnabled(False)
        self._smoothing_ready = False
        self.export_button.setEnabled(False)
        self._map_data = {}
        self._restored = False
        self.smoothing_degree.setEnabled(self.analysis is not None and self.smooth_check.isChecked())
        self.smoothing_status.setText('Recalculando toda la cuenca con la divisoria activa…')
        self._refresh_basin_hydrology_controls()
        self._smoothing_timer.start()

    def _update_smoothing(self):
        if self._disposed or self.analysis is None:
            return
        self._smoothing_timer.stop()
        if self._boundary_layers is None:
            from .boundary_layers import BoundaryLayers
            self._boundary_layers = BoundaryLayers()
        if self._boundary_adopt_existing:
            self._boundary_adopt_existing = False
            existing = self._boundary_layers.existing_state(self.analysis, self.query)
            if existing:
                for kind, control in (('original', self.original_check), ('suavizada', self.smooth_check)):
                    if kind in existing['visibility']:
                        control.blockSignals(True)
                        control.setChecked(existing['visibility'][kind])
                        control.blockSignals(False)
                if isinstance(existing['degree'], (int, float)):
                    self.smoothing_degree.blockSignals(True)
                    self.smoothing_degree.setValue(int(existing['degree']))
                    self.smoothing_degree.blockSignals(False)
                if self.smooth_check.isChecked():
                    self._queue_smoothing()
                    self._smoothing_timer.stop()
        self._smoothing_ready = False
        self.export_button.setEnabled(False)
        self.preview_geometries = {}
        self._smoothing_error = ''
        try:
            from .boundary_smoothing import smooth_boundary
            original = QgsGeometry()
            original.fromWkb(bytes(self._base_geometry_wkb or self.analysis.geometry_wkb))
            if self.original_check.isChecked():
                self.preview_geometries['original'] = QgsGeometry(original)
            if self.smooth_check.isChecked():
                self.preview_geometries['suavizada'] = smooth_boundary(
                    original, self.smoothing_degree.value(), self.analysis.cell_width,
                    self.analysis.cell_height, QgsPointXY(self.analysis.outlet_x, self.analysis.outlet_y))
            self._sync_boundary_layers()
            self._preview()
            self._show_smoothing_summary()
            if self._surface_requested:
                self._start_surface_recalculation()
        except Exception as error:
            self._smoothing_error = str(error)
            self.preview_geometries = {}
            self._remove_preview()
            if self._boundary_layers is not None:
                self._boundary_layers.set_visibility({'original': self.original_check.isChecked(), 'suavizada': False})
            self.smoothing_status.setText('No se puede aplicar este grado: ' + str(error))
            if self._surface_requested:
                self._surface_pending = self._surface_requested = False
                self._surface_valid = False
                self._refresh_basin_hydrology_controls()

    def _cancel_surface(self):
        self._surface_generation += 1
        task, self._surface_task = self._surface_task, None
        if task is not None:
            task.cancel()
        self._smoothing_timer.stop()
        self._surface_pending = self._surface_requested = False

    def _start_surface_recalculation(self):
        from .surface_recalculation import SurfaceTask
        from .basin_hydrology import source_paths
        from .flow_quantiles import _source_paths
        self._surface_requested = False
        geometry = self.preview_geometries.get('suavizada') if self.smooth_check.isChecked() else None
        surface = bytes(geometry.asWkb()) if geometry is not None else None
        flow_query = SimpleNamespace(x=self.query.x, y=self.query.y, period=self.query.period,
            flow=self.query.flow, crs_wkt=self.query.crs.toWkt())
        task = SurfaceTask(self._surface_generation, self.analysis, self.query,
            surface, self.smoothing_degree.value(), source_paths(self.query),
            flow_query, _source_paths(self.query), self._surface_finished,
            drainage_source=deepcopy(self._base_drainage_state or self._drainage_state))
        task.update.connect(self._surface_status)
        self._surface_task = task
        QgsApplication.taskManager().addTask(task)

    def _surface_status(self, generation, text, percent):
        if self._disposed or generation != self._surface_generation:
            return
        self.smoothing_status.setText(f'{text} {percent:.0f} %')
        self.basin_hydrology_widget.set_busy(True, f'{text} {percent:.0f} %')
        self.resolution_status.setText(f'{text} {percent:.0f} %')
        self.resolution_progress.setValue(round(percent))
        self.resolution_progress.show()

    def _surface_finished(self, task, success):
        if (self._disposed or task is not self._surface_task
                or task.generation != self._surface_generation):
            return
        self._surface_task = None
        self._surface_pending = False
        if not success:
            self._surface_valid = False
            self._smoothing_error = task.error or 'Recálculo cancelado.'
            self.smoothing_status.setText('No se publican resultados parciales: ' + self._smoothing_error)
            self.resolution_status.setText(self._smoothing_error)
            self.resolution_progress.hide()
            self._refresh_basin_hydrology_controls()
            self.basin_hydrology_widget.set_busy(False, self._smoothing_error)
            if task.trace:
                QgsMessageLog.logMessage(task.trace, 'HidroCAUMAX', Qgis.Warning)
            return
        self.analysis = task.result['analysis']
        if self._base_drainage_state is None and self._drainage_state is not None:
            self._base_drainage_state = deepcopy(self._drainage_state)
        if task.result.get('drainage') is not None:
            self._drainage_state = task.result['drainage']
        self._surface_valid = True
        quantiles = dict(task.result['quantiles'], **self._surface_flow_preferences)
        self._display_analysis(quantile_snapshot=quantiles)
        self._basin_hydrology_loading = True
        state = self.basin_hydrology_widget.snapshot()
        state.update(self._surface_preferences)
        state['p0_result'], state['rainfall_results'] = task.result['p0'], task.result['rainfall']
        self.basin_hydrology_widget.set_analysis(self.analysis, state=state)
        self._basin_hydrology_loading = False
        for widget in (self.table, self.hypsometry_widget, self.quantiles_widget):
            widget.setEnabled(True)
        self._map_data = {}
        self._preview()
        self._show_smoothing_summary()
        self._refresh_basin_hydrology_controls()
        self._cache_variant_state()
        issues = []
        for label, result in [('P₀', task.result['p0'])] + [
                (f'Pd T={period}', result) for period,result in task.result['rainfall'].items()]:
            if result.get('status') != 'ok':
                issues.append(label + (' parcial' if result.get('status') == 'partial' else ' no disponible'))
        if any(value is None for value in quantiles['flows']):
            issues.append('cuantiles CAUMAX incompletos')
        self.resolution_status.setText(
            'Recálculo terminado con incidencias: ' + '; '.join(issues) + '.' if issues else
            f'MDT de {self.analysis.cell_width:g} m: todos los cálculos actualizados automáticamente.')
        self.resolution_progress.hide()

    def _grid_visibility_changed(self, checked):
        if self._smoothing_loading or self._disposed or self.analysis is None:
            return
        key = f'{min(self.analysis.cell_width, self.analysis.cell_height):g}'
        self._grid_visibility_by_resolution[key] = bool(checked)
        self._map_data = {}
        self._restored = False
        self._preview()
        self._cache_variant_state()

    def _geometry_stats(self):
        stats = {}
        base = QgsGeometry()
        base.fromWkb(bytes(self._base_geometry_wkb or self.analysis.geometry_wkb))
        for kind, geometry in self.preview_geometries.items():
            shape = shape_metrics(geometry.area(), geometry.length())
            stats[kind] = dict(area_km2=geometry.area()/1e6, perimeter_km=geometry.length()/1000,
                               kc=shape.compactness, ic=shape.circularity,
                               delta_area_pct=100*(geometry.area()/base.area()-1),
                               delta_perimeter_pct=100*(geometry.length()/base.length()-1))
        return stats

    def _show_smoothing_summary(self):
        lines = []
        stats = self._geometry_stats()
        for kind in ('suavizada','original'):
            if kind not in stats:
                continue
            values = stats[kind]
            if kind == 'original' and 'suavizada' in stats:
                continue  # The original measurements are already in the table above.
            label = 'Original' if kind == 'original' else f'Suavizada · grado {self.smoothing_degree.value():g}'
            lines.append(f"{label}: área {values['area_km2']:.3f} km²; perímetro {values['perimeter_km']:.3f} km; "
                         f"Kc {values['kc']:.4f}; Ic {values['ic']:.4f}.")
            if kind == 'suavizada':
                lines.append(f"Variación: área {values['delta_area_pct']:+.3f} %; perímetro {values['delta_perimeter_pct']:+.3f} %.")
        if self.smooth_check.isChecked():
            sigma = min(self.analysis.cell_width, self.analysis.cell_height) * self.smoothing_degree.value() / 15
            lines.append(f'Gauss gradual · escala {sigma:g} m · sección de cierre fija.')
        if not stats:
            lines.append('Selecciona al menos una divisoria para exportarla.')
        lines.append('Original: rojo y malla de celdas. Suavizada: contorno azul en QGIS.')
        if self._surface_pending:
            lines.append('Recalculando morfología, hipsometría, P₀ y Pd; espera al resultado completo.')
        elif self.analysis.diagnostics.get('analysis_surface'):
            lines.append('Morfología, hipsometría, P₀ y Pd recalculados sobre la divisoria suavizada activa.')
        else:
            lines.append('Cálculos sobre la divisoria original. Cambiar el grado recalcula automáticamente toda la cuenca.')
        self.smoothing_status.setText('\n'.join(lines))

    def get_smoothing_state(self):
        state = dict(original_checked=self.original_check.isChecked(), smooth_checked=self.smooth_check.isChecked(),
                    degree=self.smoothing_degree.value(),
                    base_geometry_wkb=bytes(self._base_geometry_wkb or self.analysis.geometry_wkb),
                    cell_grid_visible=self.cell_grid_check.isChecked(),
                    geometry_wkb={kind:bytes(geometry.asWkb()) for kind,geometry in self.preview_geometries.items()},
                    stats=self._geometry_stats(),
                    course_visibility=self._course_layers.visibility() if self._course_layers else {'main':True,'central':False},
                    resolution_metadata=self._resolution_metadata(), **self._map_data)
        state['hypsometric_dimensionless'] = self.hypsometry_widget.is_dimensionless()
        state['flow_quantiles'] = deepcopy(self.quantiles_widget.snapshot())
        if self.analysis is not None:
            hydrology = self.basin_hydrology_widget.snapshot()
            if hydrology is not None:
                state['basin_hydrology'] = deepcopy(hydrology)
        if self._drainage_state is not None:
            drainage = deepcopy(self._drainage_state)
            if self._drainage_layers is not None:
                visibility=self._drainage_layers.visibility()
                drainage['network_visible']=bool(visibility.get('network',True))
                drainage['directions_visible']=bool(visibility.get('directions',False))
                drainage['metadata']['visible']=drainage['network_visible']
            state['drainage_state'] = drainage
        if self._base_drainage_state is not None:
            state['base_drainage_state'] = deepcopy(self._base_drainage_state)
        return state

    def _geometry(self, wkb):
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(wkb))
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem.fromWkt(self.analysis.crs_wkt),
            self.iface.mapCanvas().mapSettings().destinationCrs(), QgsProject.instance(),
        )
        geometry.transform(transform)
        return geometry, transform

    def _sync_boundary_layers(self):
        """Keep real outlines independently of the temporary canvas overlays."""
        try:
            if self._boundary_layers is None:
                from .boundary_layers import BoundaryLayers
                self._boundary_layers = BoundaryLayers()
            original = QgsGeometry()
            original.fromWkb(bytes(self._base_geometry_wkb or self.analysis.geometry_wkb))
            self._boundary_layers.update(self.analysis, self.query, original,
                self.preview_geometries.get('suavizada'), degree=self.smoothing_degree.value(),
                visibility={'original': self.original_check.isChecked(), 'suavizada': self.smooth_check.isChecked()})
        except Exception as error:
            self.status.setText(self.status.text() + '\nNo se ha podido conservar la divisoria como capa: ' + str(error))

    def _preview(self, *args):
        self._remove_preview()
        self._smoothing_ready = False
        self.export_button.setEnabled(False)
        if self._disposed or self.analysis is None:
            return
        canvas = self.iface.mapCanvas()
        try:
            polygon, transform = self._geometry(self._base_geometry_wkb or self.analysis.geometry_wkb)
            if 'original' in self.preview_geometries:
                self._rubber = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
                self._rubber.setColor(QColor(190, 0, 0, 220))
                self._rubber.setFillColor(QColor(255, 35, 35, 51))
                self._rubber.setWidth(2)
                self._rubber.setToGeometry(polygon, None)
            if self.cell_grid_check.isChecked() and self.analysis.grid_wkb:
                mesh, _ = self._geometry(self.analysis.grid_wkb)
                self._grid = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
                self._grid.setColor(QColor(220, 0, 0, 120))
                self._grid.setWidth(1)
                self._grid.setToGeometry(mesh, None)
            if 'suavizada' in self.preview_geometries:
                smooth, _ = self._geometry(self.preview_geometries['suavizada'].asWkb())
                self._smooth = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
                self._smooth.setColor(QColor(0, 80, 210, 255))
                self._smooth.setFillColor(QColor(0, 80, 210, 0))
                self._smooth.setWidth(3)
                self._smooth.setToGeometry(smooth, None)
            self._marker = QgsVertexMarker(canvas)
            self._marker.setCenter(transform.transform(QgsPointXY(self.analysis.outlet_x, self.analysis.outlet_y)))
            self._marker.setIconType(QgsVertexMarker.ICON_CROSS)
            self._marker.setColor(QColor(0, 70, 180))
            self._marker.setIconSize(12)
            self._marker.setPenWidth(3)
            snap = self.analysis.diagnostics.get('outlet_snap')
            if snap:
                original_point = transform.transform(QgsPointXY(snap['original_x'], snap['original_y']))
                snapped_point = transform.transform(QgsPointXY(self.analysis.outlet_x, self.analysis.outlet_y))
                if snap['distance_m'] > 1e-9:
                    self._snap_link = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
                    self._snap_link.setColor(QColor(225, 120, 0, 230))
                    self._snap_link.setWidth(2)
                    self._snap_link.setLineStyle(Qt.DashLine)
                    self._snap_link.setToGeometry(QgsGeometry.fromPolylineXY([original_point, snapped_point]), None)
                self._original_marker = QgsVertexMarker(canvas)
                self._original_marker.setCenter(original_point)
                self._original_marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
                self._original_marker.setColor(QColor(225, 120, 0))
                self._original_marker.setIconSize(12)
                self._original_marker.setPenWidth(3)
            canvas.viewport().repaint()
            self._smoothing_ready = bool(self.preview_geometries)
            self.export_button.setEnabled(self._smoothing_ready and self._surface_valid and not self._basin_hydrology_busy()
                                          and not self._resolution_pending and not self._saving_project)
        except Exception as error:
            self._remove_preview()
            self.status.setText(self.status.text() + '\nNo se ha podido representar la vista previa: ' + str(error))

    def _remove_preview(self):
        for name in ('_rubber', '_grid', '_course', '_marker', '_smooth', '_original_marker', '_snap_link'):
            item = getattr(self, name, None)
            if item is not None:
                try:
                    self.iface.mapCanvas().scene().removeItem(item)
                except RuntimeError:
                    pass
                setattr(self, name, None)

    def _report(self):
        if self.analysis is not None and not self._disposed:
            from .reports import ask_basin_pdf
            try:
                state = self._state_for_output()
                ask_basin_pdf(self, self.analysis, self.query, smoothing_state=state)
            except Exception as error:
                if not self._disposed:
                    QMessageBox.critical(self, 'Informe de la cuenca', str(error))

    def _export(self):
        if (self.analysis is not None and not self._disposed and self._smoothing_ready
                and not self._resolution_pending and self._surface_valid and not self._basin_hydrology_busy()):
            from .smooth_export import choose_export_destination
            choose_export_destination(self, self.analysis, self.query, self.preview_geometries,
                                      self.smoothing_degree.value())

    def _state_for_output(self):
        if not self._surface_valid:
            raise ValueError('Espera al recálculo completo de la divisoria activa. ' + self._smoothing_error)
        if self._basin_hydrology_busy():
            raise ValueError('Espera a que termine el cálculo de P₀ o de precipitación media.')
        if self._drainage_timer.isActive():
            self._drainage_timer.stop()
            self._prepare_drainage()
            raise ValueError('Espera a que se actualice la red con el umbral elegido antes de guardar.')
        if self._drainage_controller is not None and self._drainage_controller.busy:
            raise ValueError('Espera a que termine la preparación de las capas hidrológicas.')
        if self._resolution_pending:
            raise ValueError('Espera a que termine el cálculo del MDT seleccionado.')
        if self._smoothing_timer.isActive():
            self._smoothing_timer.stop()
            self._update_smoothing()
        if self.analysis is None:
            raise ValueError('Todavía no hay una cuenca calculada.')
        if (self.original_check.isChecked() or self.smooth_check.isChecked()) and not self._smoothing_ready:
            raise ValueError('La divisoria actual no está disponible. ' + self._smoothing_error)
        if not self._restored or not self._map_data.get('map_png'):
            from .map_capture import capture_basin_map
            analysis, query = self.analysis, self.query
            snapshot = self.get_smoothing_state()
            captured = capture_basin_map(self.iface, analysis, query, snapshot)
            if self._disposed:
                raise InterruptedError('La consulta se cerró durante la captura del mapa.')
            current = self.get_smoothing_state()
            if (self.analysis is not analysis or self.query is not query
                    or any(current[key] != snapshot[key] for key in
                           ('original_checked','smooth_checked','degree','geometry_wkb','course_visibility','resolution_metadata','cell_grid_visible'))
                    or current.get('drainage_state') != snapshot.get('drainage_state')
                    or current.get('hypsometric_dimensionless') != snapshot.get('hypsometric_dimensionless')):
                raise ValueError('La consulta ha cambiado durante la captura. Vuelve a generar el informe o guardar el proyecto.')
            if current.get('flow_quantiles') != snapshot.get('flow_quantiles'):
                raise ValueError('La consulta ha cambiado durante la captura. Vuelve a generar el informe o guardar el proyecto.')
            if current.get('basin_hydrology') != snapshot.get('basin_hydrology'):
                raise ValueError('La precipitación o el P₀ han cambiado durante la captura. Vuelve a guardar.')
            self._map_data = captured
        return self.get_smoothing_state()

    def save_project(self, destination):
        from .session_store import save_session
        if self._resolution_discovering:
            raise ValueError('Espera a que termine la consulta de las resoluciones disponibles.')
        state = self._state_for_output()
        self._cache_variant_state()
        controller = self._resolution_controller
        if controller is None:
            return save_session(destination, self.analysis, self.query, state)
        missing = [o['key'] for o in controller.options if o.get('available') and o['key'] not in controller.variants]
        if missing:
            raise ValueError('Aún faltan resoluciones por calcular. Pulsa Guardar proyecto para preparar el conjunto completo.')
        resolution_state = self._resolution_bundle()
        return save_session(destination, self.analysis, self.query, state, resolution_state=resolution_state)

    def _resolution_bundle(self):
        controller = self._resolution_controller
        from .boundary_smoothing import smooth_boundary, suggested_degree
        from .map_capture import capture_basin_map
        import copy
        variants = {}
        for option in controller.options:
            if not option.get('available'):
                continue
            key = option['key']
            item = controller.variants[key]
            analysis, query = item['analysis'], item['query']
            state = copy.deepcopy(item.get('smoothing_state') or {})
            if not state:
                original = QgsGeometry()
                original.fromWkb(bytes(analysis.geometry_wkb))
                degree = max(1, round(suggested_degree(original,analysis.cell_width,analysis.cell_height,
                                          QgsPointXY(analysis.outlet_x,analysis.outlet_y))))
                geometries = {}
                if self.original_check.isChecked():
                    geometries['original'] = original
                if self.smooth_check.isChecked():
                    geometries['suavizada'] = smooth_boundary(original,degree,analysis.cell_width,
                        analysis.cell_height,QgsPointXY(analysis.outlet_x,analysis.outlet_y))
                stats = {}
                for kind,g in geometries.items():
                    indices=shape_metrics(g.area(),g.length())
                    stats[kind]=dict(area_km2=g.area()/1e6,perimeter_km=g.length()/1000,kc=indices.compactness,ic=indices.circularity,
                        delta_area_pct=100*(g.area()/analysis.area_m2-1),delta_perimeter_pct=100*(g.length()/analysis.perimeter_m-1))
                state=dict(original_checked=self.original_check.isChecked(),smooth_checked=self.smooth_check.isChecked(),
                    degree=degree,geometry_wkb={k:bytes(g.asWkb()) for k,g in geometries.items()},stats=stats,
                    course_visibility={'main':True,'central':False},cell_grid_visible=key in ('500','25'))
                state['hypsometric_dimensionless'] = self.hypsometry_widget.is_dimensionless()
                state['flow_quantiles'] = deepcopy(self.quantiles_widget.snapshot())
            state['resolution_metadata'] = self._resolution_metadata(key)
            if (not state.get('map_png')
                    or (key != self._active_resolution_key and Path(query.dem_path).is_file())):
                state.update(capture_basin_map(self.iface,analysis,query,state))
            item['smoothing_state'] = state
            variants[key]=dict(analysis=analysis,query=query,smoothing_state=state)
        return dict(active_key=self._active_resolution_key,options=controller.options,variants=variants)

    def load_project(self, source):
        from .session_store import load_session
        saved = load_session(source)
        self._restore_session(saved)
        return saved

    def _restore_session(self, saved):
        self._cancel_drainage()
        self._cancel_basin_hydrology()
        self._cancel_surface()
        self._surface_valid = True
        if self._drainage_controller is not None:
            self._drainage_controller.dispose()
            self._drainage_controller=None
        self._saving_project = False
        self._resolution_pending = False
        self._resolution_discovering = False
        if self._resolution_controller is not None:
            self._resolution_controller.dispose()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._smoothing_timer.stop()
        for signal, slot in self._parent_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._parent_connections.clear()
        self._remove_preview()
        self.analysis, self.query = saved.analysis, saved.query
        state = saved.smoothing_state
        self._drainage_state = deepcopy(state.get('drainage_state'))
        self._base_drainage_state = deepcopy(state.get('base_drainage_state') or self._drainage_state)
        self._display_analysis()
        self._apply_smoothing_snapshot(state)
        self._setup_resolutions(getattr(saved,'resolution_state',None))

    def _apply_smoothing_snapshot(self, state):
        self._boundary_adopt_existing = False
        self._smoothing_loading = True
        self._base_geometry_wkb = bytes(state.get('base_geometry_wkb', self.analysis.geometry_wkb))
        key = f'{min(self.analysis.cell_width, self.analysis.cell_height):g}'
        visible = bool(state.get('cell_grid_visible', key in ('500', '25')))
        self._grid_visibility_by_resolution[key] = visible
        self.cell_grid_check.setChecked(visible)
        self.original_check.setChecked(state['original_checked'])
        self.smooth_check.setChecked(state['smooth_checked'])
        self.smoothing_degree.setValue(max(1, round(state['degree'])))
        self.hypsometry_widget.set_dimensionless(state.get('hypsometric_dimensionless',False))
        if state.get('flow_quantiles'):
            self.quantiles_widget.restore(state['flow_quantiles'])
        self.basin_hydrology_widget.set_analysis(self.analysis, state=state.get('basin_hydrology'))
        self._smoothing_loading = False
        self._map_data = {key:state[key] for key in ('map_png','map_caption','map_extent') if key in state}
        # A saved PNG remains the offline fallback. When the source DEM is
        # present, the next report captures its current relief style.
        self._restored = not (self._terrain_layers is not None and self._terrain_layers.layers)
        self._drainage_state = deepcopy(state.get('drainage_state'))
        self._base_drainage_state = deepcopy(state.get('base_drainage_state') or self._drainage_state)
        self._display_drainage_layers()
        self._refresh_drainage_controls()
        self.preview_geometries = {}
        for kind, wkb in state.get('geometry_wkb',{}).items():
            geometry = QgsGeometry()
            geometry.fromWkb(bytes(wkb))
            self.preview_geometries[kind] = geometry
        if self._course_layers is not None:
            self._course_layers.update(self.analysis,self.query,visibility=state.get('course_visibility'))
        self._sync_boundary_layers()
        self._preview()
        self._show_smoothing_summary()
        if (self.smooth_check.isChecked()
                and float(state['degree']) != self.smoothing_degree.value()):
            self._queue_smoothing()

    def _choose_save_project(self):
        if self._basin_hydrology_busy():
            self.basin_hydrology_widget.set_busy(True, 'Espera a que termine el cálculo antes de guardar el proyecto.')
            return
        if (self._drainage_timer.isActive()
                or (self._drainage_controller is not None and self._drainage_controller.busy)):
            if self._drainage_timer.isActive():self._prepare_drainage()
            self.drainage_status.setText('Espera a que termine la actualización de la red antes de guardar el proyecto.')
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Guardar proyecto de consulta', str(Path.home()/'consulta_cuenca.caumax.json'),
            'Proyecto de consulta CAUMAX (*.caumax.json)')
        if destination:
            try:
                effective = destination if destination.lower().endswith('.json') else destination + '.caumax.json'
                if effective != destination and Path(effective).exists():
                    answer = QMessageBox.question(self, 'Guardar proyecto',
                        'Ya existe el archivo:\n' + effective + '\n¿Quieres sustituirlo?',
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if answer != QMessageBox.Yes:
                        return
                controller = self._resolution_controller
                if controller is not None:
                    self._cache_variant_state()
                    self._saving_project = True
                    self.basin_hydrology_widget.setEnabled(False)
                    self._refresh_drainage_controls()
                    self.save_project_button.setEnabled(False)
                    self.report_button.setEnabled(False)
                    self.resolution_refresh_button.setEnabled(False)
                    self._resolution_options(controller.options)
                    def ready(variants):
                        if self._disposed:
                            return
                        try:
                            if controller.last_errors:
                                raise ValueError('No se ha guardado un conjunto incompleto. ' + '; '.join(controller.last_errors.values()))
                            self.save_project(effective)
                            self.resolution_status.setText('Proyecto guardado con todas las resoluciones disponibles.')
                        except Exception as error:
                            if not self._disposed:
                                QMessageBox.critical(self,'Guardar proyecto',str(error))
                        finally:
                            if not self._disposed:
                                self._saving_project=False
                                self.basin_hydrology_widget.setEnabled(True)
                                self._refresh_drainage_controls()
                                self.save_project_button.setEnabled(True)
                                self.report_button.setEnabled(True)
                                self.resolution_refresh_button.setEnabled(True)
                                self._resolution_options(controller.options)
                                self.resolution_progress.hide()
                    controller.ensure_all(ready)
                else:
                    self.save_project(effective)
            except Exception as error:
                if not self._disposed:
                    QMessageBox.critical(self, 'Guardar proyecto', str(error))

    def _choose_open_project(self):
        source, _ = QFileDialog.getOpenFileName(
            self, 'Abrir proyecto de consulta', str(Path.home()),
            'Proyecto de consulta CAUMAX (*.caumax.json);;Archivos JSON (*.json)')
        if source:
            try:
                self.load_project(source)
            except Exception as error:
                QMessageBox.critical(self, 'Abrir proyecto', str(error))

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._placement_timer.stop()
        self._row_timer.stop()
        if self._qgis_window is not None:
            self._qgis_window.removeEventFilter(self)
        self._smoothing_timer.stop()
        self._cancel_surface()
        self._drainage_timer.stop()
        if self._basin_hydrology_controller is not None:
            self._basin_hydrology_controller.dispose()
        if self._drainage_controller is not None:
            self._drainage_controller.dispose()
        if self._resolution_controller is not None:
            self._resolution_controller.dispose()
        task, self._task = self._task, None
        if task is not None:
            try:
                task.cancel()
            except RuntimeError:
                pass
        self._remove_preview()
        for signal, slot in self._parent_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._parent_connections.clear()
        try:
            self.iface.mapCanvas().destinationCrsChanged.disconnect(self._preview)
            self.iface.mapCanvas().extentsChanged.disconnect(self._preview)
        except (RuntimeError, TypeError):
            pass
