"""Shared basin tools for native CAUMAX raster and rational-method contexts."""
import traceback
from html import escape
from pathlib import Path
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace

from qgis.PyQt.QtCore import Qt, QTimer, QSettings, QEvent, QSize
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QSpinBox, QFileDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QMessageBox,
    QHeaderView, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QScrollArea, QSizePolicy, QWidget,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsMessageLog, QgsPointXY, QgsProject, QgsRasterLayer, QgsTask, QgsWkbTypes,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker

from .parameter_schema import (
    METRICS, format_value, interpret_value, interpretation_reference,
    morphology_table_label, morphology_table_value,
)
from .drainage_threshold import manual_step, recommended_threshold
from .metrics import shape_metrics
from .morphology_table import (InterpretationDelegate, InterpretationHeader,
                               compact_parameter_width, fit_morphology_rows)
from .drainage_panel import DrainageControls, DrainageStatusLabel
from .flow_quantiles import is_rational_query, rational_flow_text
from .native_decimal import format_decimal
from .consultation_paths import (
    CONSULTATION_FILTER, consultation_directory, default_consultation_path,
)


BLUE_BUTTON_STYLE = '''
QPushButton { background-color: #D9F0FF; color: #17374B; border: 1px solid #7EB4D0;
              border-radius: 3px; padding: 6px 14px; min-height: 20px; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:pressed { background-color: #B1DDF6; }
QPushButton:disabled { background-color: #EEF6FB; color: #8A9EA8; border-color: #C6DAE5; }
'''


def _compact_resolution(width, height=None):
    height = width if height is None else height
    number = lambda value: format(float(value), 'g').replace('.', ',')
    return f'{number(width)}mx{number(height)}m'


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
                *self.outlet, cancelled=self.isCanceled, upstream_only=False,
                allow_different_extents=is_rational_query(self.query),
            )
            if is_rational_query(self.query):
                from .rational_basin import validate_rational_analysis
                self.result = validate_rational_analysis(self.result, self.query)
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
        self.setWindowTitle('HidroCAUMAX v.1.0 · Cuenca hidrográfica')
        self.setStyleSheet(BLUE_BUTTON_STYLE)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.query, self.iface = query, iface
        self.analysis = None
        self._interpretations_valid = False
        self._base_geometry_wkb = None
        self._pending_external_basin = None
        self._outlet_selector = None
        self._outlet_apply_pending = False
        self._outlet_offer_pending = False
        self._outlet_manual_token = None
        self._outlet_dismissed_token = None
        self._delineation_cleared = False
        self._new_selection_handler = None
        self._new_selection_pending = None
        self._surface_task = None
        self._surface_generation = 0
        self._surface_pending = False
        self._surface_requested = False
        self._surface_valid = True
        self._surface_preferences = {}
        self._surface_flow_preferences = {}
        self._save_surface_task = None
        self._save_surface_generation = 0
        self._save_surface_queue = []
        self._save_surface_results = {}
        self._save_surface_callback = None
        self._save_surface_controller = None
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
        self._legacy_area_generation = 0
        self._legacy_area_task = None
        self._po_clip_generation = 0
        self._po_clip_task = None
        self._po_clip_path = None
        self._po_clip_fingerprint = None
        self._po_clip_source = None
        self._pd_clip_generation = 0
        self._pd_clip_task = None
        self._pd_clip_path = None
        self._pd_clip_fingerprint = None
        self._pd_clip_period = None
        self._pd_clip_source = None
        self._auto_hydrology_timer = QTimer(self)
        self._auto_hydrology_timer.setSingleShot(True)
        self._auto_hydrology_timer.timeout.connect(self._calculate_auto_basin_hydrology)
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
        self.context_header = QWidget(body)
        context_header_layout = QHBoxLayout(self.context_header)
        context_header_layout.setContentsMargins(0, 0, 0, 0)
        context_header_layout.setSpacing(12)
        self.context_mdt = QLabel('MDT activo: calculando…', self.context_header)
        self.context_point = QLabel(self.context_header)
        for label in (self.context_mdt, self.context_point):
            label.setTextFormat(Qt.PlainText)
            label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.context_mdt.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        context_header_layout.addWidget(self.context_mdt)
        context_header_layout.addWidget(self.context_point, 1)
        self.context_header.installEventFilter(self)
        self.context = QLabel(self)
        self.context.setTextFormat(Qt.PlainText)
        self.context.setWordWrap(True)
        group = QGroupBox('Morfología de la Cuenca', self)
        self.morphology_group = group
        box = QVBoxLayout(group)
        self.table = QTableWidget(len(METRICS), 3, group)
        self.table.setHorizontalHeaderLabels(['Parámetro', 'Valor', 'Interpretación'])
        self.table.setHorizontalHeader(InterpretationHeader(self.table))
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
        self.table.setItemDelegateForColumn(2, InterpretationDelegate(self.table))
        self.table.setMinimumHeight(280)
        for row, metric in enumerate(METRICS):
            for column, text in enumerate((morphology_table_label(metric),
                                          'Calculando…', '')):
                item = QTableWidgetItem(text)
                item.setToolTip('' if column == 2 else metric.definition)
                if column == 1:
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
        self.basin_hydrology_widget = BasinHydrologyWidget(body)
        # P₀ and all six precipitation means are computed automatically in
        # background. The active basin/closure is the only calculation input.
        self.basin_hydrology_widget.changed.connect(self._basin_hydrology_changed)
        self.basin_hydrology_widget.cancel_requested.connect(self._cancel_basin_hydrology)
        self.basin_hydrology_widget.po_clip_requested.connect(self._clip_po_to_basin)
        self.basin_hydrology_widget.po_export_requested.connect(self._export_po_clip)
        self.basin_hydrology_widget.pd_clip_requested.connect(self._clip_pd_to_basin)
        self.basin_hydrology_widget.pd_export_requested.connect(self._export_pd_clip)
        self.basin_hydrology_widget.pd_period_changed.connect(self._pd_period_changed)
        layout.addWidget(self.basin_hydrology_widget)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.status = NotesBox(self)
        self.status.setText('Delimitando la cuenca y calculando sus parámetros…')
        # Keep diagnostics for reports and saved results, without a visible notes box.
        self.status.hide()
        smoothing = QGroupBox('Suavizado divisoria', self)
        smoothing_layout = QVBoxLayout(smoothing)
        controls = QHBoxLayout()
        self.original_check = QCheckBox('Contorno', smoothing)
        self.original_check.setChecked(True)
        self.original_check.setToolTip('Mostrar la divisoria original y su relleno de celdas.')
        self.transparency_label = QLabel('Transparencia', smoothing)
        self.contour_transparency = QSlider(Qt.Horizontal, smoothing)
        self.contour_transparency.setRange(0, 100)
        self.contour_transparency.setValue(80)
        self.contour_transparency.setFixedWidth(94)
        self.contour_transparency.setToolTip('Transparencia del relleno de la cuenca: 80 %')
        self.contour_transparency.valueChanged.connect(self._contour_transparency_changed)
        self.smooth_check = QCheckBox('Suavizada', smoothing)
        self.smoothing_degree = QSpinBox(smoothing)
        self.smoothing_degree.setRange(1, 45)
        self.smoothing_degree.setSingleStep(1)
        self.smoothing_degree.setValue(3)
        self.smoothing_degree.setToolTip('Grado entero de 1 a 45. Recalcula automáticamente morfología, hipsometría, P₀ y Pd sobre la divisoria activa.')
        degree_label = QLabel('Grado:', smoothing)
        # The blue cross is the accepted outlet of the currently displayed
        # basin, not the yellow originally clicked D8 selection.
        self.export_closure_button = QPushButton('Guardar Cierre', smoothing)
        self.export_closure_button.setToolTip(
            'Guardar la posición exacta de la cruz azul como SHP de punto '
            'con círculo verde intenso y añadirla a QGIS.')
        self.export_button = QPushButton('Guardar Divisoria', smoothing)
        self.export_button.setToolTip(
            'Guardar los polígonos de la divisoria activa como SHP o GPKG.')
        # User-selected SVG designs: crosshair + red outlet + green save (2);
        # green watershed boundary + blue save (3). 16 px fits both buttons.
        for button, icon_name in (
                (self.export_closure_button, 'guardar_cierre_modelo2.svg'),
                (self.export_button, 'guardar_divisoria_modelo3.svg')):
            button.setIcon(QIcon(str(Path(__file__).parent / 'assets' / icon_name)))
            button.setIconSize(QSize(16, 16))
        for export_control in (self.export_closure_button, self.export_button):
            export_control.setStyleSheet(
                'QPushButton { padding: 2px 3px; min-height: 0px; }')
            export_font = export_control.font()
            export_font.setPointSizeF(8.)
            export_control.setFont(export_font)
            export_control.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            export_control.setEnabled(False)
        self.export_closure_button.clicked.connect(self._export_green_closure)
        self.export_button.clicked.connect(self._export)
        self._smoothing_controls = (
            self.original_check, self.transparency_label, self.contour_transparency,
            self.smooth_check, degree_label,
            self.smoothing_degree, self.export_closure_button, self.export_button)
        self._smoothing_controls_layout = controls
        controls.setSpacing(4)
        for control in self._smoothing_controls:
            controls.addWidget(control)
        controls.addStretch()
        smoothing_layout.addLayout(controls)
        self.smoothing_status = QLabel(smoothing)
        self.smoothing_status.setTextFormat(Qt.PlainText)
        self.smoothing_status.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.smoothing_status.setWordWrap(True)
        self.smoothing_status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.smoothing_status.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Maximum)
        self.smoothing_status.setContentsMargins(6,4,6,4)
        self.smoothing_status.setStyleSheet('QLabel { background-color: #FFFFFF; border: 1px solid #C8CDD2; }')
        self.smoothing_status.setText('Original: rojo y malla de celdas. Suavizada: contorno azul en el mapa de QGIS.')
        smoothing_layout.addWidget(self.smoothing_status)
        for control in (self.original_check, self.smooth_check, self.smoothing_degree):
            control.setEnabled(False)
        self.original_check.toggled.connect(self._queue_smoothing)
        self.smooth_check.toggled.connect(self._queue_smoothing)
        self.smoothing_degree.valueChanged.connect(self._queue_smoothing)
        resolution_group = QGroupBox('Resolución MDT', self)
        self.resolution_group = resolution_group
        resolution_layout = QVBoxLayout(resolution_group)
        resolution_help = QLabel('La aplicación CAUMAX original utiliza MDT de tamaño de celda 500mx500m. Aquí puede seleccionar una resolución mejorada con MDT oficiales (www.cnig.es). Todos los cálculos se actualizarán automáticamente', resolution_group)
        self.resolution_help = resolution_help
        self._update_context()
        resolution_help.setWordWrap(True)
        resolution_layout.addWidget(resolution_help)
        self._resolution_checks_widget = QWidget(resolution_group)
        self._resolution_checks_layout = QGridLayout(self._resolution_checks_widget)
        self._resolution_checks_layout.setContentsMargins(0, 0, 0, 0)
        self._resolution_checks_layout.setHorizontalSpacing(10)
        self._resolution_layout_columns = None
        self._resolution_checks_widget.installEventFilter(self)
        resolution_layout.addWidget(self._resolution_checks_widget)
        self.cell_grid_check = QCheckBox('Malla de celdas', resolution_group)
        self.cell_grid_check.setChecked(True)
        self.cell_grid_check.toggled.connect(self._grid_visibility_changed)
        resolution_actions = QGridLayout()
        resolution_actions.setContentsMargins(0, 0, 0, 0)
        resolution_actions.setHorizontalSpacing(4)
        resolution_actions.setVerticalSpacing(3)
        resolution_actions.addWidget(self.cell_grid_check, 0, 0)
        self.new_selection_button = QPushButton('Nueva selección', resolution_group)
        self.new_selection_button.setObjectName('HidroCAUMAXNuevaSeleccion')
        self.new_selection_button.setToolTip(
            'Activa Seleccionar en mapa de CAUMAX; cuando se consulta el nuevo '
            'punto válido, vuelve a calcular automáticamente la cuenca.')
        self.new_selection_button.clicked.connect(self._new_selection)
        self.new_selection_button.setEnabled(False)
        self.outlet_adjust_button = QPushButton('Ajuste Punto de Cierre', resolution_group)
        self.outlet_adjust_button.setToolTip(
            'Ajustar manualmente el punto de cierre con las direcciones o un clic en '
            'el mapa; recalcula toda la cuenca al aplicar.')
        self.outlet_adjust_button.setEnabled(False)
        self.outlet_adjust_button.clicked.connect(self._open_outlet_selection)
        self.clear_delineation_button = QPushButton('Borrar Delineado', resolution_group)
        self.clear_delineation_button.setToolTip(
            'Retirar todas las capas de divisoria, MDT y curso principal generadas '
            'en esta sesión; conserva los cálculos y las capas del usuario.')
        self.clear_delineation_button.setEnabled(False)
        self.clear_delineation_button.clicked.connect(self._clear_delineation)
        # Native SVG icons chosen by the user. The same QGIS blue style,
        # exact font, width and 105%-height apply to all three actions.
        styles = (
            (self.new_selection_button, 'nueva_seleccion_mapa_renovar.svg'),
            (self.outlet_adjust_button, 'ajuste_cierre_modelo4.svg'),
            (self.clear_delineation_button, 'borrar_delineado_modelo1.svg'),
        )
        self._resolution_action_buttons = tuple(button for button, _ in styles)
        for button, icon_name in styles:
            button.setStyleSheet('QPushButton { background-color: #D9F0FF; '
                'border: 1px solid #7EB4D0; border-radius: 3px; '
                'color: #17374B; padding: 2px 6px; min-height: 0px; }'
                'QPushButton:hover { background-color: #C7E8FF; }'
                'QPushButton:pressed { background-color: #B1DDF6; }'
                'QPushButton:disabled { background-color: #EEF6FB; '
                'color: #8A9EA8; border-color: #C6DAE5; }')
            button.setIcon(QIcon(str(Path(__file__).parent / 'assets' / icon_name)))
            button.setIconSize(QSize(16, 16))
            button.ensurePolished()
        uniform_width = max(button.sizeHint().width()
                            for button in self._resolution_action_buttons)
        uniform_height = max(button.sizeHint().height()
                             for button in self._resolution_action_buttons)
        for button in self._resolution_action_buttons:
            button.setFixedSize(uniform_width, max(
                button.fontMetrics().height() + 4, round(uniform_height * 1.05)))
        self._resolution_actions_layout = resolution_actions
        self._resolution_action_positions = 'wide'
        for index, button in enumerate(self._resolution_action_buttons):
            resolution_actions.addWidget(button, 0, index + 1)
        resolution_actions.setColumnStretch(4, 1)
        resolution_layout.addLayout(resolution_actions)
        self.resolution_checkboxes = {}
        self._resolution_buttons = QButtonGroup(self)
        self._resolution_buttons.setExclusive(True)
        self.resolution_status = QLabel('El MDT original estará disponible al terminar el cálculo.', resolution_group)
        self.resolution_status.setWordWrap(True)
        resolution_layout.addWidget(self.resolution_status)
        self.context_frame = QFrame(resolution_group)
        self.context_frame.setObjectName('HidroCAUMAXResolutionContext')
        self.context_frame.setStyleSheet('QFrame#HidroCAUMAXResolutionContext { '
            'background-color: #FFFFFF; border: 1px solid #C8CDD2; }')
        context_layout = QVBoxLayout(self.context_frame)
        context_layout.setContentsMargins(6,6,6,6)
        context_layout.setSpacing(3)
        context_layout.addWidget(self.context_header)
        context_layout.addWidget(self.context)
        resolution_layout.addWidget(self.context_frame)
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
        # Historical projects still carry drainage snapshots. Keep their state
        # adapters, but the controls are now shown only in Método racional.
        drainage_group = DrainageControls(self)
        self.drainage_group = drainage_group
        for name in ('drainage_prepare_button','drainage_threshold','drainage_auto',
                     'drainage_visible','dem_export_button','directions_export_button',
                     'network_export_button','drainage_status','drainage_progress',
                     '_drainage_controls','_drainage_controls_layout','_drainage_exports_widget'):
            setattr(self,name,getattr(drainage_group,name))
        drainage_group.prepare_requested.connect(self._prepare_drainage)
        drainage_group.threshold_changed.connect(self._drainage_threshold_changed)
        drainage_group.automatic_changed.connect(self._drainage_auto_changed)
        drainage_group.visibility_changed.connect(self._drainage_visibility_changed)
        drainage_group.export_requested.connect(self._export_hydrology)
        drainage_group.hide()
        self._refresh_drainage_controls()
        reports = QGroupBox('Informe', self)
        reports.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Fixed)
        report_layout = QHBoxLayout(reports)
        report_layout.setContentsMargins(6,4,6,4)
        report_layout.setSpacing(4)
        # Save and open the current consultation first; PDF output is last.
        # Keep the existing save/open/report handlers and closing controls.
        self.save_project_button = QPushButton('Guardar Consulta', reports)
        self.save_project_button.setEnabled(False)
        self.save_project_button.setToolTip('Calcula y guarda todas las resoluciones disponibles de la consulta; la primera preparación puede tardar.')
        self.save_project_button.clicked.connect(self._choose_save_project)
        report_layout.addWidget(self.save_project_button)
        self.open_project_button = QPushButton('Abrir Consulta', reports)
        self.open_project_button.clicked.connect(self._choose_open_project)
        report_layout.addWidget(self.open_project_button)
        self.report_button = QPushButton('Informe (PDF)', reports)
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self._report)
        report_layout.addWidget(self.report_button)
        buttons = QDialogButtonBox(reports)
        buttons.setSizePolicy(QSizePolicy.Maximum,QSizePolicy.Fixed)
        self.close_button = buttons.addButton('Cerrar', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.close)
        # Compact vector icons reproduce the three selected designs without
        # changing the text, positions or native save/open/report connections.
        for button, icon_name in (
                (self.save_project_button, 'guardar_consulta_modelo3.svg'),
                (self.open_project_button, 'abrir_consulta_modelo2.svg'),
                (self.report_button, 'informe_pdf_modelo1.svg')):
            button.setIcon(QIcon(str(Path(__file__).parent / 'assets' / icon_name)))
            button.setIconSize(QSize(16, 16))
        # Each of the four buttons is exactly 90% of its own original
        # (pre-compact-style) QGIS height. Keep the font size unchanged.
        report_buttons = (self.save_project_button, self.open_project_button,
                          self.report_button, self.close_button)
        for button in report_buttons:
            # The live button may inherit a compact Qt minimum size from an
            # earlier style. Measure a fresh *native* button before setting
            # 90%: otherwise Qt 5 can calculate an 8 px tall control.
            baseline = QPushButton(button.text(), reports)
            baseline.setFont(button.font())
            baseline.setIcon(button.icon())
            baseline.setIconSize(button.iconSize())
            baseline.ensurePolished()
            original_height = baseline.sizeHint().height()
            baseline.setParent(None)
            baseline.deleteLater()
            button.setStyleSheet('QPushButton { padding: 3px 6px; }')
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            button.setFixedHeight(max(1, round(original_height * 0.90)))
        self.close_button.setStyleSheet('QPushButton { background-color: #FADBD8; color: #742A2A; '
            'border: 1px solid #D69A9A; border-radius: 3px; padding: 3px 6px; } '
            'QPushButton:hover { background-color: #F5C6C2; } '
            'QPushButton:pressed { background-color: #EDB0AA; } '
            'QPushButton:disabled { background-color: #F8ECEA; color: #A79390; border-color: #E3CBC7; }')
        report_layout.addStretch()
        report_layout.addWidget(buttons)
        outer.addWidget(reports)
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
        from .delineation_cleanup import register_dialog
        register_dialog(self)
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
        if (hasattr(self, '_row_timer')
                and watched in (self.table.viewport(), getattr(self, '_resolution_checks_widget', None),
                                getattr(self, 'context_header', None))
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
        self._layout_resolution_options()
        self._fit_resolution_actions()
        self._fit_context_header()
        self._fit_smoothing_controls()
        self._fit_drainage_controls()
        width = self.table.viewport().width()
        header = self.table.horizontalHeader()
        blocked = header.blockSignals(True)
        try:
            # Remove the separate unit column and give its space to comments.
            # Parameter names may wrap but must never be truncated.
            metrics = self.table.fontMetrics()
            value_labels = [self.table.item(row, 1).text()
                            for row in range(self.table.rowCount())]
            value_width = max(
                metrics.horizontalAdvance('Valor') + 18,
                *(metrics.horizontalAdvance(text) + 16 for text in value_labels))
            value_width = min(value_width, max(72, round(width * .22)))
            name_width = compact_parameter_width(self.table, width)
            widths = (name_width, value_width,
                      max(20, width - name_width - value_width))
            for column, value in enumerate(widths):
                self.table.setColumnWidth(column, value)
        finally:
            header.blockSignals(blocked)
        fit_morphology_rows(self.table)

    def _fit_resolution_actions(self):
        """Keep three equally sized buttons complete on narrow QGIS windows."""
        layout = self._resolution_actions_layout
        buttons = self._resolution_action_buttons
        # QGIS may change the inherited font after the dialog is first shown.
        # Compare *native* button sizeHints in the current font/style, never
        # multiply the already enlarged fixed height on successive resizes.
        natural = []
        for button in buttons:
            sample = QPushButton(button.text(), self.resolution_group)
            sample.setFont(button.font())
            sample.setStyleSheet(button.styleSheet())
            sample.setIcon(button.icon())
            sample.setIconSize(button.iconSize())
            sample.ensurePolished()
            natural.append(sample.sizeHint())
            sample.setParent(None)
            sample.deleteLater()
        width = max(hint.width() for hint in natural)
        height = max(round(max(hint.height() for hint in natural) * 1.05),
                     max(button.fontMetrics().height() + 4 for button in buttons))
        for button in buttons:
            if button.width() != width or button.height() != height:
                button.setFixedSize(width, height)
        cell = self.cell_grid_check
        margins = self.body_scroll.widget().layout().contentsMargins()
        section = self.resolution_group.layout().contentsMargins()
        available = max(1, self.body_scroll.viewport().width() -
            margins.left() - margins.right() - section.left() - section.right())
        space = layout.horizontalSpacing()
        needed = cell.sizeHint().width() + 3 * buttons[0].width() + 3 * space
        if needed <= available:
            mode = 'wide'
        elif 3 * buttons[0].width() + 2 * space <= available:
            mode = 'wrapped'
        else:
            mode = 'narrow'
        if mode == self._resolution_action_positions:
            return
        self._resolution_action_positions = mode
        for button in buttons:
            layout.removeWidget(button)
        if mode == 'wide':
            for index, button in enumerate(buttons):
                layout.addWidget(button, 0, index + 1)
        elif mode == 'wrapped':
            for index, button in enumerate(buttons):
                layout.addWidget(button, 1, index)
        else:
            # At unusually large Windows font scaling, preserve all labels.
            layout.addWidget(buttons[0], 1, 0)
            layout.addWidget(buttons[1], 2, 0)
            layout.addWidget(buttons[2], 2, 1)

    def set_new_selection_handler(self, handler, pending=None):
        """The native host starts and reports the active CAUMAX map pick."""
        self._new_selection_handler = handler
        self._new_selection_pending = pending
        self.new_selection_button.setEnabled(callable(handler) and not self._disposed)

    def _new_selection(self, *args):
        if self._disposed or not callable(self._new_selection_handler):
            return
        if self._saving_project:
            self.resolution_status.setText(
                'Espera a que termine el guardado antes de iniciar una nueva selección.')
            return
        try:
            if self._new_selection_handler():
                self.resolution_status.setText(
                    'Nueva selección activa: haz clic en el mapa de QGIS. '
                    'CAUMAX consultará el caudal y HidroCAUMAX recalculará '
                    'automáticamente la nueva cuenca.')
            else:
                self.resolution_status.setText(
                    'No se pudo activar la selección de punto en CAUMAX.')
        except (RuntimeError, ValueError, AttributeError) as error:
            self.resolution_status.setText('No se puede seleccionar: ' + str(error))

    def _fit_smoothing_controls(self):
        """Fit both export buttons without truncating them on narrow dialogs."""
        self.original_check.setText('Contorno')
        self.original_check.setToolTip('Divisoria original · contorno de celdas')
        group = self.export_button.parentWidget()
        margins = (self.body_scroll.widget().layout().contentsMargins(),
                   group.contentsMargins(),group.layout().contentsMargins())
        available = self.body_scroll.viewport().width()-sum(m.left()+m.right() for m in margins)
        font = QFont(self.font())
        points = font.pointSizeF() if font.pointSizeF() > 0 else 10.
        minimum = min(points,8.)
        while True:
            font.setPointSizeF(points)
            for control in self._smoothing_controls:
                if control.font() != font:
                    control.setFont(font)
            needed = sum(control.sizeHint().width() for control in self._smoothing_controls)
            needed += self._smoothing_controls_layout.spacing()*(len(self._smoothing_controls)-1)
            if needed <= available:
                break
            if points <= minimum:
                # Shrink only the slider if the panel is narrow. Labels remain
                # intact, including Guardar Cierre and Guardar Divisoria.
                remaining = max(40, self.contour_transparency.width() - max(0, needed - available))
                self.contour_transparency.setFixedWidth(remaining)
                break
            points = max(minimum,points-.25)
        # Keep the existing type size. Measure the unmodified compact QGIS
        # button at the final font, then increase its natural height by 5%.
        # Do not compound the increase when the dialog is resized again.
        for button in (self.export_closure_button, self.export_button):
            native = QPushButton(button.text(), group)
            native.setFont(button.font())
            native.setStyleSheet(button.styleSheet())
            native.setIcon(button.icon())
            native.setIconSize(button.iconSize())
            native.ensurePolished()
            original_height = native.sizeHint().height()
            native.setParent(None)
            native.deleteLater()
            requested = max(button.fontMetrics().height() + 4,
                            round(original_height * 1.05))
            if button.minimumHeight() != requested or button.maximumHeight() != requested:
                button.setFixedHeight(requested)

    def _fit_drainage_controls(self):
        # The visible, full-width instance now belongs to Método racional.
        if not self.drainage_group.isHidden():
            self.drainage_group.fit_to_width()

    def _update_context(self):
        query,analysis = self.query,self.analysis
        coordinates = lambda value: format_decimal(value, '.3f').rstrip('0').rstrip(',')
        self.context_point.setText(
            f'Punto original de consulta: X={coordinates(query.x)}; Y={coordinates(query.y)} · '
            f'{query.crs.authid() or query.crs.description()}')
        if analysis is not None:
            self.context_mdt.setText('MDT activo: '+_compact_resolution(analysis.cell_width,analysis.cell_height))
        if is_rational_query(query):
            native = getattr(query, 'rational_context', None) or {}
            transform = native.get('geotransform')
            resolution = (_compact_resolution(abs(float(transform[1])), abs(float(transform[5])))
                          if isinstance(transform, (list, tuple)) and len(transform) == 6 else 'nativo')
            self.resolution_help.setText(
                'El método racional utiliza el MDT ' + resolution + '. Aquí puede seleccionar '
                'otros MDT oficiales (www.cnig.es) para actualizar la morfología, la hipsometría '
                'y las medias de P₀ y Pd de la cuenca. El caudal conserva su cálculo nativo.')
            context = ('Origen: Método racional · Red de drenaje ≤50 km²\n'
                       + rational_flow_text(query))
        else:
            self.resolution_help.setText(
                'La aplicación CAUMAX original utiliza MDT de tamaño de celda 500mx500m. '
                'Aquí puede seleccionar una resolución mejorada con MDT oficiales (www.cnig.es). '
                'Todos los cálculos se actualizarán automáticamente')
            context = f'Caudal CAUMAX original: {format_decimal(query.flow)} m³/s · T={query.period} años'
        snap = analysis.diagnostics.get('outlet_snap') if analysis is not None else None
        if snap:
            crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
            context += (
                f"\nCierre de cálculo (centro de celda): X={format_decimal(analysis.outlet_x, '.3f')}; "
                f"Y={format_decimal(analysis.outlet_y, '.3f')} · {crs.authid() or crs.description()}\n"
                f"Desplazamiento desde el punto original: {format_decimal(snap['distance_m'], '.2f')} m. "
                'Mapa: original naranja; cierre ajustado azul.')
            context += ('\nCierre aproximado a centro de celda; no es una sección topográfica exacta. '
                        'La referencia sigue siendo el punto original.')
        elif analysis is not None:
            crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
            context += (
                f'\nCierre automático (centro de la celda del punto de consulta): '
                f"X={format_decimal(analysis.outlet_x, '.3f')}; Y={format_decimal(analysis.outlet_y, '.3f')} · "
                f'{crs.authid() or crs.description()}. '
                'Puedes modificarlo con «Ajuste Punto de Cierre».')
        self.context.setText(context)
        self._fit_context_header()

    def _fit_context_header(self):
        """Keep both complete identifiers on one line whenever space permits."""
        available = self.context_header.contentsRect().width()-12
        font = QFont(self.font())
        points = font.pointSizeF()
        if points <= 0:
            points = 10.
            font.setPointSizeF(points)
        minimum = min(points, max(8., points*.70))
        labels = (self.context_mdt,self.context_point)
        def required():
            metrics = QFontMetrics(font)
            return sum(metrics.horizontalAdvance(label.text()) for label in labels)+4
        while required() > available and points > minimum:
            points = max(minimum, points-.25)
            font.setPointSizeF(points)
        for label in labels:
            if label.font() != font:
                label.setFont(font)
            label.setToolTip(label.text())
        self.context_mdt.setMinimumWidth(QFontMetrics(font).horizontalAdvance(self.context_mdt.text())+2)
        # Very narrow windows retain every coordinate by wrapping the right
        # label; ordinary widths display both labels in the same first line.
        self.context_point.setWordWrap(required() > available)

    def _render_interpretations(self):
        """Use the same local, current-value interpretation as the PDF."""
        ready = (not self._disposed and self._interpretations_valid and self.analysis is not None
                 and self._surface_valid and not self._surface_pending
                 and not self._resolution_pending and self._task is None)
        values = dict(self.analysis.values) if ready else {}
        if ready:
            values['cell_resolution'] = (self.analysis.cell_width, self.analysis.cell_height)
        self.table.horizontalHeaderItem(2).setText('Interpretación')
        for row, metric in enumerate(METRICS):
            text = interpret_value(metric.key, values.get(metric.key), values) if ready else ''
            self.table.item(row, 2).setText(text)
            detail = text + '\n\n' + interpretation_reference(metric.key) if text else ''
            self.table.item(row, 2).setToolTip(
                '<qt>' + escape(detail).replace('\n', '<br>') + '</qt>' if detail else '')
        self._row_timer.start(0)

    def _clear_interpretations(self):
        """Do not retain comments from an earlier basin while recalculating."""
        self._interpretations_valid = False
        for row in range(len(METRICS)):
            self.table.item(row, 2).setText('')
            self.table.item(row, 2).setToolTip('')
        self._row_timer.start(0)

    def _connect_parent(self, signal, slot):
        signal.connect(slot)
        self._parent_connections.append((signal, slot))

    def _parent_query_changed(self, *args):
        # The native point picker changes X, Y and the selected flow in several
        # Qt signals. While our Nueva selección owns the picker, preserve this
        # window and its previous results until CAUMAX confirms a *valid*
        # complete query. The host then replaces it atomically.
        if callable(self._new_selection_pending) and self._new_selection_pending():
            return
        self.reject()

    def _start(self):
        # Yesterday's resolution choices are populated only after a valid
        # initial basin; keep the controls visible while it is being computed.
        if not self.resolution_checkboxes:
            self._resolution_options([
                {'key': key, 'resolution': float(key), 'available': False,
                 'label': 'MDT ' + key + ' m',
                 'status': 'Preparando cuenca de referencia'}
                for key in ('500', '25', '5')
            ])
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
        self._clear_interpretations()
        self.progress.hide()
        self.context_mdt.setText('MDT activo: cálculo no disponible')
        for row, metric in enumerate(METRICS):
            self.table.item(row, 1).setText(morphology_table_value(metric.key, None))
        self.status.setText(message)
        self.resolution_status.setText(message)
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
        if self._pending_external_basin is not None:
            pending, self._pending_external_basin = self._pending_external_basin, None
            self.adopt_refined_basin(*pending)
        # New native basin: no clicks needed. Refined/smoothed transitions
        # compute these statistics as part of their complete SurfaceTask.
        self._auto_hydrology_timer.start(0)

    def _display_analysis(self, quantile_snapshot=None):
        self._cancel_legacy_area_recovery()
        self._invalidate_po_clip()
        self._invalidate_pd_clip()
        self._interpretations_valid = True
        key = f'{min(self.analysis.cell_width, self.analysis.cell_height):g}'
        visible = self._grid_visibility_by_resolution.get(key, key in ('500', '25'))
        self.cell_grid_check.blockSignals(True)
        self.cell_grid_check.setChecked(visible)
        self.cell_grid_check.blockSignals(False)
        for widget in (self.table, self.hypsometry_widget, self.quantiles_widget):
            widget.setEnabled(not self._surface_pending)
        self.hypsometry_widget.set_analysis(self.analysis)
        self.hypsometry_widget.bind_qgis_map(
            self.iface, getattr(self.query, 'dem_path', ''))
        self._basin_hydrology_loading = True
        self.basin_hydrology_widget.set_analysis(self.analysis)
        self._basin_hydrology_loading = False
        self._quantiles_loading=True
        self.quantiles_widget.set_query(self.query, snapshot=quantile_snapshot)
        if self._surface_pending:
            self.quantiles_widget.course_profile.clear()
        else:
            self.quantiles_widget.bind_basin(self.analysis, self.iface)
        self._quantiles_loading=False
        self._update_context()
        self.progress.hide()
        for row, metric in enumerate(METRICS):
            self.table.item(row, 1).setText(morphology_table_value(metric.key, self.analysis.values.get(metric.key)))
        self._render_interpretations()
        crs = QgsCoordinateReferenceSystem.fromWkt(self.analysis.crs_wkt)
        summary = (
            f'Malla de drenaje: {format_decimal(self.analysis.cell_width)} × {format_decimal(self.analysis.cell_height)} m. '
            f"Centro de celda de cierre: X={format_decimal(self.analysis.outlet_x, '.3f')}; Y={format_decimal(self.analysis.outlet_y, '.3f')} "
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
        if not self._delineation_cleared:
            if self._terrain_layers is None:
                from .terrain_layers import TerrainLayers
                self._terrain_layers = TerrainLayers(self.iface, retain_variants=True)
            try:
                self._terrain_layers.update(self.analysis, self.query)
                if self._terrain_layers.note:
                    self.status.setText(self.status.text() + '\n' + self._terrain_layers.note)
            except Exception as error:
                self.status.setText(self.status.text() + '\nNo se ha podido representar el relieve: ' + str(error))
            # A generated ESRI-D8 result replaces the previous course group.
            # Do not recreate "Curso principal" during report, smoothing or
            # redraw while the matching D8 result remains active.
            if self._drainage_state is None:
                if self._course_layers is None:
                    from .course_layers import CourseLayers
                    self._course_layers = CourseLayers(self.iface)
                self._course_layers.update(self.analysis, self.query)
                if self._course_layers.note:
                    self.status.setText(self.status.text() + '\n' + self._course_layers.note)
        self._display_drainage_layers()
        self._refresh_basin_hydrology_controls()

    def _setup_resolutions(self, resolution_state=None):
        self._close_outlet_selection()
        from .resolution_controller import ResolutionController, base_variant_key
        if self._resolution_controller is not None:
            self._resolution_controller.dispose()
        if resolution_state:
            variants = resolution_state['variants']
            base_key = base_variant_key(variants, resolution_state['active_key'])
            base = variants[base_key]
            base_analysis, base_query = base['analysis'], base['query']
        else:
            base_analysis, base_query = self.analysis, self.query
        controller = ResolutionController(base_analysis, base_query,
            self._resolution_options, self._resolution_result, self._resolution_status,
            self._resolution_error, parent=self)
        self._resolution_controller = controller
        self._outlet_manual_token = self._outlet_dismissed_token = None
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
        if self.quantiles_widget.snapshot() is not None:
            state = self.quantiles_widget.snapshot()
            self.quantiles_widget.course_profile.set_period(
                state['input_period'], state['extrapolate'])
        self._render_interpretations()
        self._cache_variant_state()

    def _basin_hydrology_changed(self, *args):
        if self._basin_hydrology_loading or self._smoothing_loading or self._disposed:
            return
        self._render_interpretations()
        self._cache_variant_state()

    def _basin_hydrology_busy(self):
        return bool(self._surface_pending or
                    (self._basin_hydrology_controller and self._basin_hydrology_controller.busy))

    def _calculate_auto_basin_hydrology(self):
        """Start one joint job only for a stable, uncached native basin.

        Existing saved consultations remain usable without source rasters:
        restoring them does not trigger a new job. When an outlet or surface
        changes, the full SurfaceTask recalculates both metrics together.
        """
        if (self._disposed or self.analysis is None or self._surface_pending
                or self._resolution_pending or not self._surface_valid):
            return
        state = self.basin_hydrology_widget.snapshot() or {}
        if (state.get('p0_result') is not None
                and len(state.get('rainfall_results') or {}) == 6):
            return
        if self._basin_hydrology_controller and self._basin_hydrology_controller.busy:
            return
        self._calculate_basin_hydrology('both')

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
        self._set_export_enabled(available and self._smoothing_ready and not busy and not self._saving_project)
        self._refresh_drainage_controls()
        self._refresh_outlet_button()

    def _cancel_legacy_area_recovery(self):
        """Cancel out-of-date D8 area reconstruction when the basin changes."""
        self._legacy_area_generation += 1
        task, self._legacy_area_task = self._legacy_area_task, None
        if task is not None:
            task.cancel()

    def _start_legacy_area_recovery(self):
        """Recover real D8 upstream areas from original rasters when needed.

        For historic consultations without these data, only official original
        D8 and MDT files are accepted. Missing files leave saved Qci intact.
        """
        from .course_flow_profile import main_course_areas
        from .legacy_course_areas import LegacyD8AreaTask
        if (self._disposed or self.analysis is None or self._surface_pending
                or self._resolution_pending or is_rational_query(self.query)
                or not self.analysis.main_course_wkb or main_course_areas(self.analysis)):
            return
        if self._legacy_area_task is not None:
            return
        direction = str(getattr(self.query, 'directions_path', '') or '').split('|', 1)[0]
        dem = str(getattr(self.query, 'dem_path', '') or '').split('|', 1)[0]
        if not Path(direction).is_file() or not Path(dem).is_file():
            profile = self.quantiles_widget.course_profile
            profile._area_note = ('La aportación específica requiere las capas '
                                  'D8 y MDT originales para esta consulta antigua.')
            if profile._rows:
                profile._update_coverage()
            return
        task = LegacyD8AreaTask(
            self._legacy_area_generation, self.analysis, direction, dem,
            self._legacy_area_finished)
        self._legacy_area_task = task
        QgsApplication.taskManager().addTask(task)

    def _legacy_area_finished(self, task, success):
        """Publish only areas matching the unchanged active native D8 course."""
        if (self._disposed or task is not self._legacy_area_task
                or task.generation != self._legacy_area_generation
                or task.analysis is not self.analysis):
            return
        self._legacy_area_task = None
        profile = self.quantiles_widget.course_profile
        if not success:
            profile._area_note = (
                'Aportación específica no calculada: ' +
                (task.error or 'recálculo D8 cancelado.'))
            if profile._rows:
                profile._update_coverage()
            return
        from .course_flow_profile import main_course_areas
        from types import SimpleNamespace
        # The worker has verified every native D8 centre. Preserve all old
        # morphometry, Qci rasters/samples and saved statistics byte-for-byte.
        diagnostics = dict(self.analysis.diagnostics)
        diagnostics['main_course_areas_b64'] = task.result
        proposed = SimpleNamespace(diagnostics=diagnostics)
        if len(main_course_areas(proposed)) != len(
                __import__('osgeo').ogr.CreateGeometryFromWkb(
                    bytes(self.analysis.main_course_wkb)).GetPoints()):
            profile._area_note = 'Las áreas D8 recuperadas no coinciden con el curso.'
            return
        self.analysis = replace(self.analysis, diagnostics=diagnostics)
        profile.set_upstream_areas(
            self.analysis, note='Áreas D8 originales recuperadas de la consulta antigua.')
        if self._resolution_controller is not None:
            key = self._active_resolution_key
            if key in self._resolution_controller.variants:
                self._resolution_controller.variants[key]['analysis'] = self.analysis
        self._cache_variant_state()

    def _invalidate_po_clip(self):
        """A new basin invalidates the previously computed P₀ crop."""
        self._po_clip_generation += 1
        task, self._po_clip_task = self._po_clip_task, None
        if task is not None:
            task.cancel()
        self._po_clip_path = None
        self._po_clip_fingerprint = None
        self._po_clip_source = None
        if hasattr(self, 'basin_hydrology_widget'):
            self.basin_hydrology_widget.set_clip_export_available(False)

    def _clip_po_to_basin(self):
        """Compute the P₀ raster crop in a QGIS worker, publish on GUI thread."""
        if (self._disposed or self.analysis is None or self._resolution_pending
                or self._saving_project or self._basin_hydrology_busy()
                or not self._surface_valid):
            return
        from .basin_hydrology import basin_fingerprint
        from .po_clip import PoClipTask
        from uuid import uuid4
        state = self.basin_hydrology_widget.snapshot() or {}
        p0 = state.get('p0_result') or {}
        source = str(p0.get('source_path') or '').split('|', 1)[0]
        if not source or not Path(source).is_file():
            QMessageBox.warning(
                self, 'Recorte capa Po',
                'El ráster P₀ de esta consulta no está disponible en disco.')
            return
        try:
            self._invalidate_po_clip()
            folder = Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'PoRecortes'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('Po_Cuenca_' + uuid4().hex + '.tif')
            fingerprint = basin_fingerprint(self.analysis)
            task = PoClipTask(self._po_clip_generation, source,
                              self.analysis.geometry_wkb, self.analysis.crs_wkt,
                              path, self._po_clip_finished)
            self._po_clip_task = task
            self._po_clip_fingerprint = fingerprint
            self.basin_hydrology_widget.po_clip_button.setEnabled(False)
            self.basin_hydrology_widget.set_clip_export_available(False)
            self.basin_hydrology_widget.po_clip_button.setToolTip(
                'Recortando P₀ a la divisoria activa…')
            QgsApplication.taskManager().addTask(task)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'Recorte capa Po', str(error))

    def _po_clip_finished(self, task, success):
        """Accept only the latest valid basin crop and keep its QGIS layer."""
        from .basin_hydrology import basin_fingerprint
        stale = (self._disposed or task is not self._po_clip_task
                 or task.generation != self._po_clip_generation
                 or self.analysis is None
                 or basin_fingerprint(self.analysis) != self._po_clip_fingerprint)
        if stale:
            task.path.unlink(missing_ok=True)
            return
        self._po_clip_task = None
        self.basin_hydrology_widget.po_clip_button.setToolTip(
            'Recortar el ráster P₀ oficial utilizando la divisoria activa.')
        self.basin_hydrology_widget._refresh_po_actions()
        if not success:
            task.path.unlink(missing_ok=True)
            QMessageBox.warning(
                self, 'Recorte capa Po',
                task.error or 'No se pudo completar el recorte P₀.')
            return
        try:
            from .po_clip import publish_clipped_raster
            publish_clipped_raster(task.result, kind='po')
            self._po_clip_path = Path(task.result)
            self._po_clip_source = task.source
            self.basin_hydrology_widget.set_clip_export_available(True)
            self.iface.messageBar().pushMessage(
                'HidroCAUMAX', 'Recorte de P₀ visible en QGIS: ' + str(task.result),
                level=Qgis.Info, duration=8)
        except (ValueError, RuntimeError, OSError) as error:
            self._po_clip_path = None
            self.basin_hydrology_widget.set_clip_export_available(False)
            QMessageBox.warning(self, 'Recorte capa Po', str(error))

    def _export_po_clip(self):
        from .po_clip import EXPORT_FILENAME, export_po_clip
        from .improved_storage import generated_layers_directory
        path = self._po_clip_path
        if (self._disposed or path is None or not path.is_file()
                or self.analysis is None):
            QMessageBox.warning(
                self, 'Exportar capa Po',
                'Primero genera el recorte de P₀ correspondiente a la cuenca activa.')
            return
        try:
            suggested = generated_layers_directory() / EXPORT_FILENAME
        except OSError as error:
            QMessageBox.warning(self, 'Exportar capa Po', str(error))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Exportar Umbral Po de la Cuenca', str(suggested),
            'GeoTIFF (*.tif)')
        if not destination:
            return
        if not Path(destination).suffix:
            destination += '.tif'
        try:
            target = export_po_clip(path, destination)
            self.iface.messageBar().pushMessage(
                'HidroCAUMAX', 'Umbral Po exportado: ' + str(target),
                level=Qgis.Info, duration=8)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'Exportar capa Po', str(error))

    def _invalidate_pd_clip(self):
        """Drop stale Pd clipping output when basin/source/period changes."""
        self._pd_clip_generation += 1
        task, self._pd_clip_task = self._pd_clip_task, None
        if task is not None:
            task.cancel()
        self._pd_clip_path = None
        self._pd_clip_fingerprint = None
        self._pd_clip_period = None
        self._pd_clip_source = None
        if hasattr(self, 'basin_hydrology_widget'):
            self.basin_hydrology_widget.set_pd_clipping(False)
            self.basin_hydrology_widget.set_pd_clip_export_available(False)

    def _pd_period_changed(self, period):
        """Selecting another official CEDEX raster invalidates its old export."""
        self._invalidate_pd_clip()

    def _clip_pd_to_basin(self, period):
        """Crop the selected real CEDEX Pd-T raster to the active geometry."""
        if (self._disposed or self.analysis is None or self._resolution_pending
                or self._saving_project or self._basin_hydrology_busy()
                or not self._surface_valid):
            return
        from .basin_hydrology import basin_fingerprint
        from .flow_quantiles import PERIODS
        from .po_clip import PoClipTask
        from uuid import uuid4

        period = int(period)
        if period not in PERIODS:
            QMessageBox.warning(self, 'Recorte capa Pd-T CEDEX',
                                'El período de retorno seleccionado no es oficial.')
            return
        state = self.basin_hydrology_widget.snapshot() or {}
        result = (state.get('rainfall_results') or {}).get(str(period)) or {}
        source = str(result.get('source_path') or '').split('|', 1)[0]
        if (result.get('status') not in ('ok', 'partial')
                or not source or not Path(source).is_file()):
            QMessageBox.warning(self, 'Recorte capa Pd-T CEDEX',
                f'No se encuentra una capa CEDEX válida para T={period} años.')
            return
        try:
            self._invalidate_pd_clip()
            folder = Path(QgsApplication.qgisSettingsDirPath()) / 'HidroCAUMAX' / 'PdRecortes'
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / f'Pd_T{period}_Cuenca_{uuid4().hex}.tif'
            fingerprint = basin_fingerprint(self.analysis)
            task = PoClipTask(
                self._pd_clip_generation, source, self.analysis.geometry_wkb,
                self.analysis.crs_wkt, destination, self._pd_clip_finished, kind='pd')
            task.selected_period = period
            self._pd_clip_task = task
            self._pd_clip_fingerprint = fingerprint
            self._pd_clip_period = period
            self._pd_clip_source = source
            self.basin_hydrology_widget.set_pd_clipping(True)
            self.basin_hydrology_widget.set_pd_clip_export_available(False)
            QgsApplication.taskManager().addTask(task)
        except (OSError, ValueError, RuntimeError) as error:
            self._invalidate_pd_clip()
            QMessageBox.warning(self, 'Recorte capa Pd-T CEDEX', str(error))

    def _pd_clip_finished(self, task, success):
        """Only show the requested period with the correct basin fingerprint."""
        from .basin_hydrology import basin_fingerprint
        state = self.basin_hydrology_widget.snapshot() or {}
        period = getattr(task, 'selected_period', None)
        current = (state.get('rainfall_results') or {}).get(str(period)) or {}
        stale = (self._disposed or task is not self._pd_clip_task
                 or task.generation != self._pd_clip_generation
                 or self.analysis is None
                 or period != self._pd_clip_period
                 or period != self.basin_hydrology_widget.selected_pd_period()
                 or (self.analysis is not None and
                     basin_fingerprint(self.analysis) != self._pd_clip_fingerprint)
                 or str(current.get('source_path') or '').split('|', 1)[0]
                    != self._pd_clip_source)
        if stale:
            task.path.unlink(missing_ok=True)
            return
        self._pd_clip_task = None
        self.basin_hydrology_widget.set_pd_clipping(False)
        if not success:
            task.path.unlink(missing_ok=True)
            QMessageBox.warning(self, 'Recorte capa Pd-T CEDEX',
                task.error or 'No se pudo completar el recorte Pd-T.')
            return
        try:
            from .po_clip import publish_clipped_raster
            publish_clipped_raster(task.result, kind='pd', period=period)
            self._pd_clip_path = Path(task.result)
            self.basin_hydrology_widget.set_pd_clip_export_available(True, period)
            self.iface.messageBar().pushMessage(
                'HidroCAUMAX',
                f'Pd-T CEDEX T={period} años: ráster recortado y visible en QGIS.',
                level=Qgis.Info, duration=8)
        except (ValueError, RuntimeError, OSError) as error:
            self._pd_clip_path = None
            self.basin_hydrology_widget.set_pd_clip_export_available(False)
            QMessageBox.warning(self, 'Recorte capa Pd-T CEDEX', str(error))

    def _export_pd_clip(self):
        """Export the generated selected CEDEX raster as a GeoTIFF."""
        from .basin_hydrology import basin_fingerprint
        from .po_clip import PD_EXPORT_FILENAME, export_pd_clip
        from .improved_storage import generated_layers_directory
        period = self.basin_hydrology_widget.selected_pd_period()
        state = self.basin_hydrology_widget.snapshot() or {}
        current = (state.get('rainfall_results') or {}).get(str(period)) or {}
        path = self._pd_clip_path
        if (self._disposed or path is None or not path.is_file()
                or self.analysis is None or self._pd_clip_period != period
                or basin_fingerprint(self.analysis) != self._pd_clip_fingerprint
                or str(current.get('source_path') or '').split('|', 1)[0]
                   != self._pd_clip_source):
            QMessageBox.warning(
                self, 'Exportar capa Pd-T CEDEX',
                'Primero recorta la capa Pd-T del período seleccionado.')
            return
        try:
            suggested = generated_layers_directory() / PD_EXPORT_FILENAME
        except OSError as error:
            QMessageBox.warning(self, 'Exportar capa Pd-T CEDEX', str(error))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, f'Exportar Pd-T CEDEX T={period} años', str(suggested),
            'GeoTIFF (*.tif)')
        if not destination:
            return
        if not Path(destination).suffix:
            destination += '.tif'
        try:
            saved = export_pd_clip(path, destination)
            self.iface.messageBar().pushMessage(
                'HidroCAUMAX',
                f'Pd-T CEDEX T={period} exportado: {saved}',
                level=Qgis.Info, duration=8)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, 'Exportar capa Pd-T CEDEX', str(error))

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
        self._auto_hydrology_timer.stop()
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

    def _normalise_drainage_encoding(self):
        # Portable projects may remember the old TauDEM display choice. Every
        # current display/export uses the CAUMAX/ESRI direction model.
        for state in (self._drainage_state,self._base_drainage_state):
            if state is not None:
                state['flow_encoding'] = 'caumax'

    def _display_drainage_layers(self):
        self._normalise_drainage_encoding()
        if self._delineation_cleared:
            return
        if self._drainage_layers is None:
            from .drainage_layers import DrainageLayers
            self._drainage_layers = DrainageLayers(self.iface)
        visible = bool((self._drainage_state or {}).get('network_visible',
                       (self._drainage_state or {}).get('metadata',{}).get('visible',True)))
        published = self._drainage_layers.update(
            self.analysis, self._drainage_state, visible=visible)
        if (self._drainage_state is not None and
                all(published.get(kind) is not None
                    for kind in ('network', 'directions', 'dem'))):
            self._drainage_layers.publish()
        if self._drainage_state is not None:
            # Publication is complete: purge every earlier course group (also
            # from a previous maximum-flow or rational query) and leave only
            # this ESRI-D8 result. Do not remove any shared MDT/D8 source.
            from .course_layers import retire_other_course_groups
            retire_other_course_groups(iface=self.iface)

    def _refresh_drainage_controls(self):
        if not hasattr(self,'drainage_prepare_button'):
            return
        self._normalise_drainage_encoding()
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
            recommended = (float(state['recommended_threshold_area_m2']) if state else
                recommended_threshold(analysis.area_m2, minimum))
            chosen = float(state['threshold_area_m2']) if state else recommended
            self.drainage_threshold.setRange(minimum/1e6,max(analysis.area_m2*2,chosen,recommended)/1e6)
            self.drainage_threshold.setSingleStep(manual_step(analysis.area_m2, minimum)/1e6)
            self.drainage_threshold.setValue(chosen/1e6)
        if state and not busy:
            self.drainage_auto.setChecked(state['automatic'])
            self.drainage_visible.setChecked(state.get('network_visible',state.get('metadata',{}).get('visible',True)))
        elif not state and not busy:
            self.drainage_auto.setChecked(True)
            self.drainage_visible.setChecked(True)
        self._drainage_loading = False
        self.drainage_prepare_button.setEnabled(allowed and source_available and not busy)
        self.drainage_threshold.setEnabled(bool(state) and allowed and source_available and not busy)
        self.drainage_auto.setEnabled(bool(state) and allowed and source_available and not busy)
        self.drainage_visible.setEnabled(bool(state))
        for button in (self.dem_export_button,self.directions_export_button,self.network_export_button):
            button.setEnabled(bool(state) and not busy and not self._resolution_pending)
        if not improved:
            self.drainage_status.setText('Selecciona un MDT mejorado para preparar sus capas.')
        elif state and not busy:
            self.drainage_status.setText(
                f"Esri-D8 · umbral {format_decimal(state['threshold_area_m2']/1e6)} km² · "
                f"{len(state['features'])} tramos. Cada archivo se exporta por separado.")
        elif not busy:
            self.drainage_status.setText('Esri-D8 seleccionado. Pulsa Generar capas D8.')

    def _prepare_drainage(self, *args):
        self._drainage_timer.stop()
        if self._disposed or self.analysis is None or self._resolution_pending or self._saving_project or self._basin_hydrology_busy():
            return
        if min(self.analysis.cell_width,self.analysis.cell_height)>=500:
            return
        try:
            from .taudem_engine import probe_taudem
            info = probe_taudem(bin_dir=None)
            if not info.get('available'):
                self.drainage_status.setText(info.get('reason','No se pueden preparar las herramientas de Esri-D8. Comprueba la instalación del complemento.'))
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
            self._set_export_enabled(False)
            source_query=self.query
            if not Path(source_query.dem_path).is_file() and self._drainage_state:
                exported=self._drainage_state.get('metadata',{}).get('exports',{}).get('dem','')
                if exported and Path(exported).is_file():source_query=replace(source_query,dem_path=exported)
            self._drainage_controller.prepare(key,self.analysis,source_query,reference,cache,
                threshold_area_m2=threshold,automatic=automatic,
                encoding='caumax',bin_dir=None)
            # As soon as generation has been queued, clear the former 500 m
            # display and its private outline files. Preserve source rasters.
            if self._drainage_controller.busy:
                from .improved_result_cleanup import retire_500m_results
                retire_500m_results(self.iface)
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
        self._close_outlet_selection()
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
        # At this point the actual ESRI-D8 result has been registered. Keep
        # only this published generation, not obsolete earlier D8 groups.
        if (self._drainage_layers is not None and self._drainage_layers._group
                is not None and
                self._drainage_layers._group.customProperty(
                    'HidroCAUMAX/drainage_published', False)):
            from .drainage_layers import prune_managed_drainage
            prune_managed_drainage(
                keep_owner=self._drainage_layers.owner_key,
                protected_paths=tuple(path for path in
                    (getattr(products, 'dem_path', None),
                     getattr(products, 'caumax_d8_path', None),
                     getattr(products, 'tau_d8_path', None)) if path),
                preserve_published=False)
        if (previous.geometry_wkb != self.analysis.geometry_wkb
                or previous.outlet_x != self.analysis.outlet_x or previous.outlet_y != self.analysis.outlet_y):
            self.preview_geometries = {}
        self._update_smoothing()
        # The improved analysis and its layers are now available; retire only
        # old plugin-owned 500 m display results, never source DEM/D8 rasters.
        from .improved_result_cleanup import retire_500m_results
        retired = retire_500m_results(self.iface)
        if retired:
            QgsMessageLog.logMessage(
                f'Retiradas {retired} capas de resultados de 500x500 m.',
                'HidroCAUMAX', Qgis.Info)
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
        self._set_export_enabled(self._smoothing_ready)

    def _cancel_drainage(self):
        self._drainage_timer.stop()
        if self._drainage_controller is not None:self._drainage_controller.cancel()
        if hasattr(self,'drainage_progress'):self.drainage_progress.hide()
        self._refresh_drainage_controls()
        if hasattr(self,'report_button'):
            self.report_button.setEnabled(self.analysis is not None)
            self.save_project_button.setEnabled(self.analysis is not None)
            self._set_export_enabled(self._smoothing_ready)

    def _drainage_threshold_changed(self,*args):
        if self._drainage_loading or not self._drainage_state:return
        self._drainage_loading=True
        self.drainage_auto.setChecked(False)
        self._drainage_loading=False
        self._mark_drainage_pending()
        self._drainage_timer.start()

    def _mark_drainage_pending(self):
        for name in ('report_button','save_project_button','export_button',
                     'export_closure_button','network_export_button'):
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
        names={'dem':f'MDT_{size}.tif','directions':f'Direccion_D8_caumax_{size}.tif','network':f'Red_drenaje_{size}.shp'}
        filters='Shapefile (*.shp)' if kind=='network' else 'GeoTIFF (*.tif *.tiff)'
        from .improved_storage import export_directory, record_exports
        destination,_=QFileDialog.getSaveFileName(self,'Exportar capa hidrológica',str(export_directory()/names[kind]),filters)
        if not destination:return
        try:
            from .hydro_export import export_dem,export_directions,export_network
            if kind=='dem':path=export_dem(products,destination)
            elif kind=='directions':path=export_directions(products,destination,encoding='caumax')
            else:path=export_network(products,destination,snapshot=state)
            export_key='directions_caumax' if kind=='directions' else kind
            state['metadata'].setdefault('exports',{})[export_key]=str(path)
            self.drainage_status.setText('Archivo guardado: '+str(path))
            self._cache_variant_state()
            if Path(path).is_file():
                try:
                    record_exports(state['metadata']['exports'])
                except OSError as error:
                    self.drainage_status.setText('Archivo guardado: '+str(path)+
                        '. No se ha podido conservar para futuras actualizaciones: '+str(error))
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
        self._resolution_layout_columns = None
        for index, option in enumerate(options):
            key = option['key']
            text = _compact_resolution(option['resolution'])
            box = QCheckBox(text, self._resolution_checks_widget)
            box.setMinimumWidth(box.sizeHint().width())
            box.setToolTip(option.get('label','') + '\n' + option.get('status',''))
            box.setEnabled(bool(option.get('available')) and not self._saving_project)
            self._resolution_buttons.addButton(box)
            box.setChecked(key == self._active_resolution_key)
            box.clicked.connect(lambda checked=False, key=key: self._select_resolution(key))
            self.resolution_checkboxes[key] = box
        self._layout_resolution_options()
        self._refresh_outlet_button()

    def _layout_resolution_options(self):
        """Use one row when possible and wrap whole labels without clipping."""
        boxes = list(self.resolution_checkboxes.values())
        if not boxes:
            return
        available = max(1,self._resolution_checks_widget.contentsRect().width())
        spacing = self._resolution_checks_layout.horizontalSpacing()
        widths = [box.minimumSizeHint().width() for box in boxes]
        columns = len(boxes)
        while columns > 1:
            required = sum(max(widths[column::columns]) for column in range(columns))
            if required+spacing*(columns-1) <= available:
                break
            columns -= 1
        if columns == self._resolution_layout_columns:
            return
        while self._resolution_checks_layout.count():
            self._resolution_checks_layout.takeAt(0)
        for column in range(max(len(boxes),self._resolution_checks_layout.columnCount())):
            self._resolution_checks_layout.setColumnStretch(column,0)
            self._resolution_checks_layout.setColumnMinimumWidth(column,0)
        for index,box in enumerate(boxes):
            self._resolution_checks_layout.addWidget(box,index//columns,index%columns,Qt.AlignLeft)
        for column in range(columns):
            self._resolution_checks_layout.setColumnStretch(column,1)
        self._resolution_layout_columns = columns

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
        self._set_export_enabled(self._smoothing_ready)
        self._refresh_basin_hydrology_controls()
        if self._outlet_apply_pending:
            self._resume_outlet_selection(str(text))
        if (controller is not None and getattr(controller, 'outlet_selection_context', None)
                and self._outlet_dismissed_token != (controller,getattr(controller,'_generation',None))):
            self._outlet_offer_pending = True
            QTimer.singleShot(0, self._offer_outlet_selection)

    def _refresh_outlet_button(self):
        controller = self._resolution_controller
        self.outlet_adjust_button.setEnabled(bool(controller is not None and not getattr(controller, 'busy', False)
            and not self._resolution_pending and not self._saving_project
            and not self._basin_hydrology_busy()
            and not (self._drainage_controller and self._drainage_controller.busy)))
        self.clear_delineation_button.setEnabled(bool(self.analysis is not None
            and not self._disposed and not self._saving_project and not self._delineation_cleared))
        if self._outlet_offer_pending:
            QTimer.singleShot(0, self._offer_outlet_selection)

    def _offer_outlet_selection(self):
        controller = self._resolution_controller
        if (self._outlet_offer_pending and not self._disposed and controller is not None
                and getattr(controller, 'outlet_selection_context', None)
                and not controller.busy and not self._saving_project
                and not self._resolution_pending and not self._basin_hydrology_busy()
                and not (self._drainage_controller and self._drainage_controller.busy)):
            self._outlet_offer_pending = False
            self._open_outlet_selection()

    def _close_outlet_selection(self):
        if self._outlet_apply_pending and self._outlet_manual_token is not None:
            self._outlet_dismissed_token = self._outlet_manual_token
        self._outlet_apply_pending = self._outlet_offer_pending = False
        dialog, self._outlet_selector = self._outlet_selector, None
        if dialog is not None:
            dialog.reject()

    def _open_outlet_selection(self, *args):
        controller = self._resolution_controller
        if (self._disposed or controller is None or controller.busy or self._saving_project
                or self._resolution_pending or self._basin_hydrology_busy()):
            return
        if self._outlet_selector is not None:
            self._outlet_selector.raise_()
            self._outlet_selector.activateWindow()
            return
        try:
            from .outlet_selection import OutletSelectionDialog
            context = controller.selection_context()
            dialog = OutletSelectionDialog(self.iface, context, self, persistent=True)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self._outlet_selector = dialog
            self._outlet_dismissed_token = None
            dialog.applied.connect(lambda: self._apply_outlet_selection(dialog, controller, dialog.context))
            def finished(*args):
                if self._outlet_selector is dialog:
                    if self._outlet_apply_pending and self._outlet_manual_token is not None:
                        self._outlet_dismissed_token = self._outlet_manual_token
                    self._outlet_selector = None
                    self._outlet_apply_pending = False
            dialog.finished.connect(finished)
            dialog.show()
        except Exception as error:
            QMessageBox.warning(self, 'Ajuste Punto de Cierre', str(error))

    def _apply_outlet_selection(self, dialog, controller, context):
        if (self._disposed or controller is not self._resolution_controller
                or self._outlet_selector is not dialog or self._saving_project
                or self._resolution_pending or controller.busy):
            return
        point = dialog.selection()
        if point is None:
            return
        self._clear_interpretations()
        self._outlet_apply_pending = True
        self._outlet_manual_token = None
        self._delineation_cleared = False
        self._cache_variant_state()
        self._cancel_drainage()
        self._cancel_basin_hydrology()
        self._cancel_surface()
        self._resolution_pending = True
        self._clear_interpretations()
        self.resolution_progress.show()
        self._refresh_basin_hydrology_controls()
        try:
            if not controller.select_outlet(context, *point):
                self._resolution_pending = False
                self._refresh_basin_hydrology_controls()
                self._resume_outlet_selection()
            else:
                self._outlet_manual_token = (controller,controller._generation)
        except Exception as error:
            self._resolution_error(str(error))

    def _resume_outlet_selection(self, message=''):
        dialog, controller = self._outlet_selector, self._resolution_controller
        if (not self._outlet_apply_pending or dialog is None or controller is None
                or self._disposed or controller.busy or self._resolution_pending
                or self._basin_hydrology_busy()
                or (self._drainage_controller and self._drainage_controller.busy)):
            return
        try:
            dialog.rebind(controller.selection_context())
            dialog.set_busy(False, message)
        except Exception as error:
            dialog.set_busy(True, 'No se puede reabrir el cierre aplicado: ' + str(error))
        self._outlet_apply_pending = False

    def _clear_delineation(self):
        from .delineation_cleanup import clear_delineations
        clear_delineations()

    def _clear_local_delineation(self):
        if self._disposed:
            return
        self._delineation_cleared = True
        self._close_outlet_selection()
        self._outlet_offer_pending = False
        self._cancel_save_surfaces()
        self._saving_project = False
        self._cancel_surface()
        self._drainage_timer.stop()
        for controller in (self._resolution_controller, self._drainage_controller,
                           self._basin_hydrology_controller):
            if controller is not None:
                controller.cancel()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._resolution_pending = self._resolution_discovering = False
        self.progress.hide()
        self.resolution_progress.hide()
        if hasattr(self, 'drainage_progress'):
            self.drainage_progress.hide()
        self._remove_preview()
        for layers in (self._course_layers,self._boundary_layers,
                       self._terrain_layers,self._drainage_layers):
            if layers is not None:
                layers.remove()
        self._map_data = {}
        self._restored = False
        self.resolution_status.setText('Delineados de la sesión retirados. Los cálculos se conservan. Puedes usar «Ajuste Punto de Cierre» o elegir otra resolución para dibujar de nuevo esta cuenca.')
        self._refresh_basin_hydrology_controls()
        self._refresh_outlet_button()

    def _refresh_resolutions(self):
        self._close_outlet_selection()
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
        self._close_outlet_selection()
        if self._disposed or self._saving_project or self._resolution_controller is None:
            return
        self._delineation_cleared = False
        self._cancel_drainage()
        self._cancel_basin_hydrology()
        self._cancel_surface()
        self._cache_variant_state()
        if (key == self._active_resolution_key
                and not self._resolution_controller.needs_reference_check(key)):
            if self._resolution_pending:
                self._resolution_controller.cancel()
                self._resolution_pending = False
                self.resolution_progress.hide()
                self.resolution_refresh_button.setEnabled(True)
                self.report_button.setEnabled(True)
                self.save_project_button.setEnabled(not self._resolution_discovering)
                self._set_export_enabled(self._smoothing_ready)
                self.resolution_status.setText('Se mantiene el MDT activo; descarga anterior cancelada.')
            self._queue_smoothing()
            return
        if self._resolution_pending and self._resolution_controller.requested_key == key:
            return
        self._clear_interpretations()
        self._resolution_pending = True
        self._render_interpretations()
        self.basin_hydrology_widget.setEnabled(False)
        self.report_button.setEnabled(False)
        self.save_project_button.setEnabled(False)
        self._set_export_enabled(False)
        self.resolution_progress.show()
        self.resolution_refresh_button.setEnabled(False)
        self._resolution_controller.select(key)

    def _resolution_result(self, key, analysis, query):
        if self._disposed:
            return
        if not self._outlet_apply_pending:
            self._close_outlet_selection()
        previous_hydrology = self.basin_hydrology_widget.snapshot() or {}
        previous_quantiles = self.quantiles_widget.snapshot() or {}
        # A manual outlet can replace the current resolution in the controller.
        # Do not write the previous basin/smoothing back over that new result.
        if key != self._active_resolution_key or analysis is self.analysis:
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
            if quantiles is not None:
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
        if state:
            self._start_legacy_area_recovery()
        if not self._resolution_loading:
            self._queue_smoothing()
        self._resume_outlet_selection()

    def adopt_refined_basin(self, key, analysis, source_query):
        """Reuse the panel's verified fine delineation in this rational dialog.

        Preserve this dialog's native CAUMAX flow, period and cell provenance.
        Never import a new flow estimate from the terrain-only panel.
        """
        if self._disposed or not is_rational_query(getattr(self, '_native_query', self.query)):
            return False
        controller = self._resolution_controller
        if controller is None or self.analysis is None:
            self._pending_external_basin = (key, analysis, source_query)
            return False
        try:
            # The native calculator may have rounded its display point to 0.1 m.
            if (abs(float(source_query.x) - float(controller.base_query.x)) > 0.11
                    or abs(float(source_query.y) - float(controller.base_query.y)) > 0.11
                    or source_query.crs.toWkt() != controller.base_query.crs.toWkt()):
                return False
            key = str(key)
            if key not in {item['key'] for item in controller.options}:
                return False
            if key == controller._base_key:
                # The panel shows the 500 m reference for information. Restore
                # our actual verified native rational basin; never replace it
                # with the parallel terrain controller's re-polygonization.
                if self._active_resolution_key != key:
                    return bool(controller.select(key))
                return True
            if (self._active_resolution_key == key and self.analysis is analysis):
                return True
            # Independently check that the imported high-resolution watershed
            # overlaps the actual native Rational-method 500 m reference.
            base_crs = QgsCoordinateReferenceSystem.fromWkt(controller.base_analysis.crs_wkt)
            fine_crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
            original = QgsGeometry()
            original.fromWkb(bytes(controller.base_analysis.geometry_wkb))
            refined = QgsGeometry()
            refined.fromWkb(bytes(analysis.geometry_wkb))
            if fine_crs != base_crs:
                refined.transform(QgsCoordinateTransform(fine_crs, base_crs,
                                                          QgsProject.instance()))
            overlap = original.intersection(refined)
            common_area = overlap.area() if not overlap.isNull() else 0
            if (original.area() <= 0 or refined.area() <= 0
                    or common_area <= .5 * original.area()
                    or common_area <= .5 * refined.area()):
                self.resolution_status.setText(
                    'La divisoria refinada no acredita correspondencia espacial '
                    'con la cuenca racional original. Selecciona el MDT aquí para recalcular.')
                return False
            query = replace(controller.base_query, dem_path=source_query.dem_path,
                            directions_path=source_query.directions_path)
            self._cancel_basin_hydrology()
            self._cancel_drainage()
            self._cancel_surface()
            self._cache_variant_state()
            controller.variants[key] = dict(analysis=analysis, query=query,
                smoothing_state=None, metadata={'provider': 'HidroCAUMAX',
                'source': 'panel racional; pendiente de comprobación D8 frente a la base nativa'})
            # Do not pretend the raster-chain proof against the native CAUMAX
            # base was executed. An explicit later selection reruns that proof.
            controller._automatic_references.pop(key, None)
            for item in controller.options:
                if item['key'] == key:
                    item['available'] = True
                    item['status'] = 'Divisoria importada; revalidar al seleccionar de nuevo'
            self._resolution_result(key, analysis, query)
            return True
        except (AttributeError, RuntimeError, TypeError, ValueError, OSError) as error:
            self.resolution_status.setText('No se ha podido sincronizar la cuenca: ' + str(error))
            return False

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
        if self._smoothing_loading or self._disposed or self._saving_project:
            return
        if self.analysis is None:
            return
        self._clear_interpretations()
        if self._base_geometry_wkb is None:
            self._base_geometry_wkb = bytes(self.analysis.geometry_wkb)
        self._cancel_drainage()
        self.hypsometry_widget._cancel_selection()
        self.quantiles_widget.course_profile.clear()
        if self._basin_hydrology_controller is not None:
            self._basin_hydrology_controller.cancel()
        self._cancel_surface()
        self._surface_pending = True
        self._surface_requested = True
        self._surface_valid = False
        self._clear_interpretations()
        snapshot = self.basin_hydrology_widget.snapshot() or {}
        if snapshot:
            self._surface_preferences = {k:snapshot[k] for k in
                ('input_period', 'input_precipitation', 'extrapolate')}
        self._surface_flow_preferences = {k:v for k,v in (self.quantiles_widget.snapshot() or {}).items()
                                         if k in ('input_period', 'input_flow', 'extrapolate')}
        self._basin_hydrology_loading = True
        self.basin_hydrology_widget.set_analysis(None)
        self._basin_hydrology_loading = False
        for widget in (self.table, self.hypsometry_widget, self.quantiles_widget):
            widget.setEnabled(False)
        self._smoothing_ready = False
        self._set_export_enabled(False)
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
        self._set_export_enabled(False)
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
            flow=self.query.flow, crs_wkt=self.query.crs.toWkt(),
            source_kind=getattr(self.query,'source_kind','caumax_raster'))
        task = SurfaceTask(self._surface_generation, self.analysis, self.query,
            surface, self.smoothing_degree.value(), source_paths(self.query),
            flow_query, [] if is_rational_query(self.query) else _source_paths(self.query), self._surface_finished,
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
            self._resume_outlet_selection(self._smoothing_error)
            return
        if not self._outlet_apply_pending and not self._outlet_offer_pending:
            self._close_outlet_selection()
        self.analysis = task.result['analysis']
        if self._base_drainage_state is None and self._drainage_state is not None:
            self._base_drainage_state = deepcopy(self._drainage_state)
        if task.result.get('drainage') is not None:
            self._drainage_state = task.result['drainage']
        self._surface_valid = True
        quantiles = (dict(task.result['quantiles'], **self._surface_flow_preferences)
                     if task.result.get('quantiles') is not None else None)
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
        if quantiles is not None and any(value is None for value in quantiles['flows']):
            issues.append('cuantiles CAUMAX incompletos')
        self.resolution_status.setText(
            'Recálculo terminado con incidencias: ' + '; '.join(issues) + '.' if issues else
            f'MDT de {format_decimal(self.analysis.cell_width)} m: todos los cálculos actualizados automáticamente.')
        self.resolution_progress.hide()
        self._resume_outlet_selection()

    def _contour_transparency_changed(self, value):
        """Adjust only map overlay opacity; never recalculate the watershed."""
        self.contour_transparency.setToolTip(
            f'Transparencia del relleno de la cuenca: {value} %')
        if self._disposed or self._smoothing_loading:
            return
        overlay = getattr(self, '_rubber', None)
        if overlay is not None:
            try:
                overlay.setFillColor(QColor(255, 35, 35, round(255 * (100 - value) / 100)))
                self.iface.mapCanvas().viewport().update()
            except RuntimeError:
                pass
        self._map_data = {}
        self._restored = False
        if self.analysis is not None:
            self._cache_variant_state()

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
        number = lambda value, spec='g': format(value,spec).replace('.',',')
        stats = self._geometry_stats()
        for kind in ('suavizada','original'):
            if kind not in stats:
                continue
            values = stats[kind]
            if kind == 'original' and 'suavizada' in stats:
                continue  # The original measurements are already in the table above.
            label = 'Original' if kind == 'original' else f'Suavizada · grado {self.smoothing_degree.value():g}'
            lines.append(f"{label}: área {number(values['area_km2'],'.3f')} km²; "
                         f"perímetro {number(values['perimeter_km'],'.3f')} km; "
                         f"Kc {number(values['kc'],'.4f')}; Ic {number(values['ic'],'.4f')}.")
            if kind == 'suavizada':
                lines.append(f"Variación: área {number(values['delta_area_pct'],'+.3f')} %; "
                             f"perímetro {number(values['delta_perimeter_pct'],'+.3f')} %.")
        if self.smooth_check.isChecked():
            sigma = min(self.analysis.cell_width, self.analysis.cell_height) * self.smoothing_degree.value() / 15
            lines.append(f'Gauss gradual · escala {number(sigma)} m · sección de cierre fija.')
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

    def _snapshot_hydrology_clips(self):
        """Only persist clips still tied to the active geometry and sources."""
        if self.analysis is None:
            return None
        from .basin_hydrology import basin_fingerprint
        fingerprint = basin_fingerprint(self.analysis)
        hydrology = self.basin_hydrology_widget.snapshot() or {}
        saved = {'po': None, 'pd': None}
        p0 = hydrology.get('p0_result') or {}
        source = str(p0.get('source_path') or '').split('|', 1)[0]
        if (self._po_clip_path is not None and self._po_clip_path.is_file()
                and self._po_clip_fingerprint == fingerprint
                and self._po_clip_source and source == self._po_clip_source
                and p0.get('status') in ('ok', 'partial')):
            saved['po'] = dict(path=str(self._po_clip_path.absolute()),
                               source_path=source, basin_fingerprint=fingerprint)
        period = self._pd_clip_period
        rain = ((hydrology.get('rainfall_results') or {}).get(str(period)) or {})
        source = str(rain.get('source_path') or '').split('|', 1)[0]
        if (self._pd_clip_path is not None and self._pd_clip_path.is_file()
                and self._pd_clip_fingerprint == fingerprint
                and period in (2, 5, 10, 25, 100, 500)
                and self._pd_clip_source and source == self._pd_clip_source
                and rain.get('status') in ('ok', 'partial')):
            saved['pd'] = dict(path=str(self._pd_clip_path.absolute()),
                               source_path=source, basin_fingerprint=fingerprint,
                               period=int(period))
        return saved if any(saved.values()) else None

    def _restore_hydrology_clips(self, state):
        """Restore existing hydrology TIFFs as visible layers, without recomputing.

        An absent, moved or replaced file is reported but never stops opening
        the rest of the portable consultation.
        """
        clips = state.get('hydrology_clips')
        if not clips or self.analysis is None or self._disposed:
            return
        from .basin_hydrology import basin_fingerprint
        from .po_clip import inspect_clipped_raster, publish_clipped_raster

        fingerprint = basin_fingerprint(self.analysis)
        issues = []
        for kind in ('po', 'pd'):
            record = clips.get(kind)
            if record is None:
                continue
            try:
                path = inspect_clipped_raster(
                    record['path'], kind, record['source_path'],
                    fingerprint=fingerprint)
                period = record.get('period') if kind == 'pd' else None
                if kind == 'pd' and period != self.basin_hydrology_widget.selected_pd_period():
                    raise ValueError('El período seleccionado no coincide con el recorte Pd-T.')
                publish_clipped_raster(path, kind=kind, period=period)
                if kind == 'po':
                    self._po_clip_path = path
                    self._po_clip_source = record['source_path']
                    self._po_clip_fingerprint = fingerprint
                    self.basin_hydrology_widget.set_clip_export_available(True)
                else:
                    self._pd_clip_path = path
                    self._pd_clip_source = record['source_path']
                    self._pd_clip_fingerprint = fingerprint
                    self._pd_clip_period = period
                    self.basin_hydrology_widget.set_pd_clip_export_available(True, period)
            except (ValueError, RuntimeError, OSError) as error:
                issues.append(('P₀' if kind == 'po' else 'Pd-T CEDEX') +
                              ': ' + str(error))
        if issues:
            try:
                self.iface.messageBar().pushMessage(
                    'HidroCAUMAX', 'No se han recuperado algunos recortes: ' +
                    '; '.join(issues), level=Qgis.Warning, duration=12)
            except (RuntimeError, AttributeError):
                self.status.setText(
                    self.status.text() + '\nRecortes no recuperados: ' + '; '.join(issues))

    def get_smoothing_state(self):
        self._normalise_drainage_encoding()
        state = dict(original_checked=self.original_check.isChecked(), smooth_checked=self.smooth_check.isChecked(),
                    contour_transparency=self.contour_transparency.value(),
                    degree=self.smoothing_degree.value(),
                    base_geometry_wkb=bytes(self._base_geometry_wkb or self.analysis.geometry_wkb),
                    cell_grid_visible=self.cell_grid_check.isChecked(),
                    geometry_wkb={kind:bytes(geometry.asWkb()) for kind,geometry in self.preview_geometries.items()},
                    stats=self._geometry_stats(),
                    course_visibility=self._course_layers.visibility() if self._course_layers else {'main':True,'central':False},
                    resolution_metadata=self._resolution_metadata(), **self._map_data)
        state['hypsometric_dimensionless'] = self.hypsometry_widget.is_dimensionless()
        hypso_selection = self.hypsometry_widget.selection_state()
        state['hypsometric_selected_index'] = hypso_selection['selected_index']
        state['hypsometric_opacity'] = hypso_selection['opacity']
        state['pd_clip_period'] = self.basin_hydrology_widget.selected_pd_period()
        clips = self._snapshot_hydrology_clips()
        if clips is not None:
            state['hydrology_clips'] = clips
        quantiles = self.quantiles_widget.snapshot()
        if quantiles is not None:
            state['flow_quantiles'] = deepcopy(quantiles)
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
        if self._delineation_cleared:
            return
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
        from .delineation_cleanup import register_overlay
        if self._delineation_cleared:
            self._remove_preview()
            # Removing the live drawing does not invalidate calculated geometry.
            self._smoothing_ready = bool(self.preview_geometries)
            self._set_export_enabled(self._smoothing_ready and self._surface_valid
                and not self._basin_hydrology_busy() and not self._resolution_pending
                and not self._saving_project)
            return
        self._remove_preview()
        self._smoothing_ready = False
        self._set_export_enabled(False)
        if self._disposed or self.analysis is None:
            return
        canvas = self.iface.mapCanvas()
        try:
            polygon, transform = self._geometry(self._base_geometry_wkb or self.analysis.geometry_wkb)
            if 'original' in self.preview_geometries:
                self._rubber = register_overlay(canvas, QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry))
                self._rubber.setColor(QColor(190, 0, 0, 220))
                self._rubber.setFillColor(QColor(
                    255, 35, 35, round(255 * (100 - self.contour_transparency.value()) / 100)))
                self._rubber.setWidth(2)
                self._rubber.setToGeometry(polygon, None)
            if self.cell_grid_check.isChecked() and self.analysis.grid_wkb:
                mesh, _ = self._geometry(self.analysis.grid_wkb)
                self._grid = register_overlay(canvas, QgsRubberBand(canvas, QgsWkbTypes.LineGeometry))
                self._grid.setColor(QColor(220, 0, 0, 120))
                self._grid.setWidth(1)
                self._grid.setToGeometry(mesh, None)
            if 'suavizada' in self.preview_geometries:
                smooth, _ = self._geometry(self.preview_geometries['suavizada'].asWkb())
                self._smooth = register_overlay(canvas, QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry))
                self._smooth.setColor(QColor(0, 80, 210, 255))
                self._smooth.setFillColor(QColor(0, 80, 210, 0))
                self._smooth.setWidth(3)
                self._smooth.setToGeometry(smooth, None)
            self._marker = register_overlay(canvas, QgsVertexMarker(canvas))
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
                    self._snap_link = register_overlay(canvas, QgsRubberBand(canvas, QgsWkbTypes.LineGeometry))
                    self._snap_link.setColor(QColor(225, 120, 0, 230))
                    self._snap_link.setWidth(2)
                    self._snap_link.setLineStyle(Qt.DashLine)
                    self._snap_link.setToGeometry(QgsGeometry.fromPolylineXY([original_point, snapped_point]), None)
                self._original_marker = register_overlay(canvas, QgsVertexMarker(canvas))
                self._original_marker.setCenter(original_point)
                self._original_marker.setIconType(QgsVertexMarker.ICON_CIRCLE)
                self._original_marker.setColor(QColor(225, 120, 0))
                self._original_marker.setIconSize(12)
                self._original_marker.setPenWidth(3)
            canvas.viewport().repaint()
            self._smoothing_ready = bool(self.preview_geometries)
            self._set_export_enabled(self._smoothing_ready and self._surface_valid and not self._basin_hydrology_busy()
                                          and not self._resolution_pending and not self._saving_project)
        except Exception as error:
            self._remove_preview()
            self.status.setText(self.status.text() + '\nNo se ha podido representar la vista previa: ' + str(error))

    def _remove_preview(self):
        from .delineation_cleanup import PREVIEW_ATTRIBUTES, remove_overlay
        for name in PREVIEW_ATTRIBUTES:
            item = getattr(self, name, None)
            setattr(self, name, None)
            remove_overlay(item)

    def _report(self):
        if self.analysis is not None and not self._disposed:
            from .reports import ask_basin_pdf
            try:
                state = self._state_for_output()
                ask_basin_pdf(self, self.analysis, self.query, smoothing_state=state)
            except Exception as error:
                if not self._disposed:
                    QMessageBox.critical(self, 'Informe de la cuenca', str(error))

    def _set_export_enabled(self, enabled):
        """Keep green closure and basin polygon actions synchronized."""
        self.export_button.setEnabled(bool(enabled))
        self.export_closure_button.setEnabled(bool(enabled))

    def _export_green_closure(self, *args):
        if (self.analysis is None or self._disposed or not self._smoothing_ready
                or self._resolution_pending or not self._surface_valid
                or self._basin_hydrology_busy() or self._saving_project):
            return
        from .closure_layers import export_green_course_closure, suggested_course_closure_path
        analysis = self.analysis
        try:
            suggested = suggested_course_closure_path(analysis)
        except OSError as error:
            QMessageBox.critical(self, 'Guardar Cierre',
                                 'No se pudo crear Capas_Generadas: ' + str(error))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Exportar Cierre · cruz azul',
            str(suggested), 'Shapefile (*.shp)',
            options=QFileDialog.DontConfirmOverwrite)
        if not destination:
            return
        if not Path(destination).suffix:
            destination += '.shp'
        try:
            path, saved = export_green_course_closure(analysis, destination)
            self.status.setText(
                'Cierre de la cruz azul exportado como punto verde: ' + str(path))
            try:
                self.iface.messageBar().pushMessage(
                    'HidroCAUMAX', 'Cierre verde guardado y cargado: ' + str(path),
                    level=Qgis.Info, duration=7)
            except (AttributeError, RuntimeError):
                pass
        except Exception as error:
            QMessageBox.critical(self, 'Exportar Cierre', str(error))

    def _export(self):
        if (self.analysis is not None and not self._disposed and self._smoothing_ready
                and not self._resolution_pending and self._surface_valid and not self._basin_hydrology_busy()):
            from .smooth_export import choose_export_destination
            choose_export_destination(self, self.analysis, self.query, self.preview_geometries,
                                      self.smoothing_degree.value())

    def _state_for_output(self):
        self._close_outlet_selection()
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
                           ('original_checked','smooth_checked','degree','geometry_wkb','course_visibility','resolution_metadata','cell_grid_visible','contour_transparency'))
                    or current.get('drainage_state') != snapshot.get('drainage_state')
                    or current.get('hypsometric_dimensionless') != snapshot.get('hypsometric_dimensionless')
                    or current.get('hypsometric_selected_index') != snapshot.get('hypsometric_selected_index')
                    or current.get('hypsometric_opacity') != snapshot.get('hypsometric_opacity')):
                raise ValueError('La consulta ha cambiado durante la captura. Vuelve a generar el informe o guardar el proyecto.')
            if current.get('flow_quantiles') != snapshot.get('flow_quantiles'):
                raise ValueError('La consulta ha cambiado durante la captura. Vuelve a generar el informe o guardar el proyecto.')
            if current.get('basin_hydrology') != snapshot.get('basin_hydrology'):
                raise ValueError('La precipitación o el P₀ han cambiado durante la captura. Vuelve a guardar.')
            self._map_data = captured
        # Save every currently available graph, including the longitudinal
        # Qci profile not previously included in exported consultation PDFs.
        from .report_charts import capture_available_charts
        state = self.get_smoothing_state()
        state['chart_snapshots'] = capture_available_charts(self)
        course = self.quantiles_widget.course_profile
        profile = course.snapshot() if not is_rational_query(self.query) else None
        if profile is not None:
            state['course_flow_profile'] = profile
        return state

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

    def _new_resolution_state(self, key, analysis):
        """Presentation settings for an unvisited variant, before its calculation."""
        from .boundary_smoothing import smooth_boundary, suggested_degree
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
        for kind,geometry in geometries.items():
            indices = shape_metrics(geometry.area(),geometry.length())
            stats[kind] = dict(area_km2=geometry.area()/1e6,perimeter_km=geometry.length()/1000,
                kc=indices.compactness,ic=indices.circularity,
                delta_area_pct=100*(geometry.area()/analysis.area_m2-1),
                delta_perimeter_pct=100*(geometry.length()/analysis.perimeter_m-1))
        return dict(original_checked=self.original_check.isChecked(),smooth_checked=self.smooth_check.isChecked(),
            contour_transparency=self.contour_transparency.value(),degree=degree,base_geometry_wkb=bytes(analysis.geometry_wkb),
            geometry_wkb={kind:bytes(geometry.asWkb()) for kind,geometry in geometries.items()},stats=stats,
            course_visibility={'main':True,'central':False},cell_grid_visible=key in ('500','25'),
            hypsometric_dimensionless=self.hypsometry_widget.is_dimensionless(),
            hypsometric_selected_index=None,
            hypsometric_opacity=self.hypsometry_widget.opacity_slider.value(),
            pd_clip_period=self.basin_hydrology_widget.selected_pd_period(),
            resolution_metadata=self._resolution_metadata(key))

    def _cancel_save_surfaces(self):
        self._save_surface_generation += 1
        task,self._save_surface_task = self._save_surface_task,None
        if task is not None:
            task.cancel()
        self._save_surface_queue = []
        self._save_surface_results = {}
        self._save_surface_callback = None
        self._save_surface_controller = None

    def _prepare_resolution_states(self, callback):
        """Complete unvisited variants in workers; publish only a complete batch."""
        self._cancel_save_surfaces()
        controller = self._resolution_controller
        self._save_surface_controller = controller
        self._save_surface_callback = callback
        try:
            from .basin_hydrology import source_paths
            from .flow_quantiles import _source_paths
            flow_preferences = {key:value for key,value in (self.quantiles_widget.snapshot() or {}).items()
                                if key in ('input_period','input_flow','extrapolate')}
            hydrology = self.basin_hydrology_widget.snapshot() or {}
            hydro_preferences = dict(input_period=hydrology.get('input_period',100.),
                input_precipitation=hydrology.get('input_precipitation',0.),
                extrapolate=hydrology.get('extrapolate',True))
            for option in controller.options:
                if not option.get('available'):
                    continue
                key = option['key']
                variant = controller.variants[key]
                if variant.get('smoothing_state'):
                    continue
                analysis,query = variant['analysis'],variant['query']
                state = self._new_resolution_state(key,analysis)
                self._save_surface_queue.append(dict(key=key,analysis=analysis,query=query,state=state,
                    sources=source_paths(query),
                    flow_sources=[] if is_rational_query(query) else _source_paths(query),
                    flow_preferences=dict(flow_preferences),hydro_preferences=dict(hydro_preferences)))
            self._next_save_surface()
        except Exception as error:
            self._finish_save_surfaces(str(error))

    def _next_save_surface(self):
        if self._disposed:
            return
        if not self._save_surface_queue:
            self._finish_save_surfaces()
            return
        from .surface_recalculation import SurfaceTask
        item = self._save_surface_queue.pop(0)
        query,state = item['query'],item['state']
        flow_query = SimpleNamespace(x=query.x,y=query.y,period=query.period,
            flow=query.flow,crs_wkt=query.crs.toWkt(),
            source_kind=getattr(query,'source_kind','caumax_raster'))
        surface = state['geometry_wkb'].get('suavizada') if state['smooth_checked'] else None
        task = SurfaceTask(self._save_surface_generation,item['analysis'],query,surface,state['degree'],
            item['sources'],flow_query,item['flow_sources'],self._save_surface_finished)
        task.save_item = item
        task.update.connect(self._save_surface_status)
        self._save_surface_task = task
        self.resolution_status.setText('Preparando resultados completos del MDT de '+item['key']+' m…')
        self.resolution_progress.setValue(0)
        self.resolution_progress.show()
        QgsApplication.taskManager().addTask(task)

    def _save_surface_status(self, generation, text, percent):
        if self._disposed or generation != self._save_surface_generation or self._save_surface_task is None:
            return
        key = self._save_surface_task.save_item['key']
        self.resolution_status.setText(f'MDT de {str(key).replace(".", ",")} m: {text} {percent:.0f} %')
        self.resolution_progress.setValue(round(percent))

    def _save_surface_finished(self, task, success):
        if (self._disposed or task is not self._save_surface_task
                or task.generation != self._save_surface_generation):
            return
        self._save_surface_task = None
        if not success:
            self._finish_save_surfaces(task.error or 'Preparación del proyecto cancelada; no se ha guardado.')
            return
        try:
            from .basin_hydrology import basin_fingerprint
            from .basin_hydrology_widget import METHOD, _normalise
            item,result = task.save_item,task.result
            analysis,state = result['analysis'],item['state']
            if result.get('quantiles') is not None:
                state['flow_quantiles'] = dict(result['quantiles'],**item['flow_preferences'])
            state['basin_hydrology'] = _normalise(dict(version=1,
                basin_fingerprint=basin_fingerprint(analysis),basin_area_m2=analysis.area_m2,
                p0_result=result['p0'],rainfall_results=result['rainfall'],method=METHOD,
                **item['hydro_preferences']))
            self._save_surface_results[item['key']] = dict(analysis=analysis,
                query=item['query'],smoothing_state=state)
            self._next_save_surface()
        except Exception as error:
            self._finish_save_surfaces(str(error))

    def _finish_save_surfaces(self, error=None):
        controller = self._save_surface_controller
        prepared,callback = self._save_surface_results,self._save_surface_callback
        self._cancel_save_surfaces()
        if self._disposed:
            return
        if controller is not self._resolution_controller:
            error = error or 'El proyecto ha cambiado durante su preparación; no se ha guardado.'
        if not error:
            for key,variant in prepared.items():
                controller.variants[key].update(variant)
        if callback is not None:
            callback(error)

    def _resolution_bundle(self):
        controller = self._resolution_controller
        from .map_capture import capture_basin_map
        variants = {}
        for option in controller.options:
            if not option.get('available'):
                continue
            key = option['key']
            item = controller.variants[key]
            analysis,query = item['analysis'],item['query']
            state = deepcopy(item.get('smoothing_state') or {})
            for name in ('drainage_state','base_drainage_state'):
                if state.get(name) is not None:
                    state[name]['flow_encoding'] = 'caumax'
            if not state:
                raise ValueError('Faltan los resultados completos del MDT de '+key+
                                 ' m. Pulsa Guardar proyecto para preparar todas las resoluciones.')
            state['resolution_metadata'] = self._resolution_metadata(key)
            if (not state.get('map_png')
                    or (key != self._active_resolution_key and Path(query.dem_path).is_file())):
                state.update(capture_basin_map(self.iface,analysis,query,state))
            item['smoothing_state'] = state
            variants[key] = dict(analysis=analysis,query=query,smoothing_state=state)
        return dict(active_key=self._active_resolution_key,options=controller.options,variants=variants)

    def load_project(self, source):
        from .session_store import load_session
        saved = load_session(source)
        self._restore_session(saved)
        return saved

    def _restore_session(self, saved):
        self._clear_interpretations()
        self._cancel_save_surfaces()
        self._saving_project = False
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
        self._delineation_cleared = False
        state = saved.smoothing_state
        self._drainage_state = deepcopy(state.get('drainage_state'))
        self._base_drainage_state = deepcopy(state.get('base_drainage_state') or self._drainage_state)
        self._display_analysis(quantile_snapshot=state.get('flow_quantiles'))
        self._apply_smoothing_snapshot(state)
        if state.get('course_flow_profile') and not is_rational_query(self.query):
            self.quantiles_widget.course_profile.restore_snapshot(state['course_flow_profile'])
        self._start_legacy_area_recovery()
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
        self.contour_transparency.setValue(state.get('contour_transparency', 80))
        self.smooth_check.setChecked(state['smooth_checked'])
        self.smoothing_degree.setValue(max(1, round(state['degree'])))
        self.hypsometry_widget.set_dimensionless(state.get('hypsometric_dimensionless',False))
        self.hypsometry_widget.restore_selection_state(
            state.get('hypsometric_selected_index'),
            state.get('hypsometric_opacity', 70))
        if is_rational_query(self.query):
            self.quantiles_widget.set_query(self.query)
        elif state.get('flow_quantiles'):
            self.quantiles_widget.restore(state['flow_quantiles'])
        self.basin_hydrology_widget.set_analysis(self.analysis, state=state.get('basin_hydrology'))
        selected_pd = state.get('pd_clip_period', 100)
        index = self.basin_hydrology_widget.pd_period_combo.findData(selected_pd)
        if index >= 0:
            self.basin_hydrology_widget.pd_period_combo.setCurrentIndex(index)
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
        if (self._course_layers is not None and not self._delineation_cleared
                and self._drainage_state is None):
            self._course_layers.update(self.analysis,self.query,visibility=state.get('course_visibility'))
        self._sync_boundary_layers()
        self._preview()
        self._show_smoothing_summary()
        self._restore_hydrology_clips(state)
        if (self.smooth_check.isChecked()
                and float(state['degree']) != self.smoothing_degree.value()):
            self._queue_smoothing()

    def _export_consultation_layers(self):
        """Export the current closure and basin without prompting for two paths.

        The JSON consultation remains authoritative and is never rolled back
        because an optional SHP could not be written. Existing files are never
        overwritten; callers receive separate warnings for each export.
        """
        from .closure_layers import export_green_course_closure, suggested_course_closure_path
        from .smooth_export import export_previewed, _suggested_basin_destination, basin_geometry
        from qgis.core import QgsGeometry

        errors, saved = [], []
        analysis, query = self.analysis, self.query
        closure = suggested_course_closure_path(analysis)
        try:
            if closure.exists():
                errors.append('Guardar Cierre: ya existe ' + closure.name)
            else:
                export_green_course_closure(analysis, closure)
                saved.append(closure.name)
        except Exception as error:
            errors.append('Guardar Cierre: ' + str(error))

        try:
            destination = _suggested_basin_destination(analysis, query)
            # Preserve the selected original/smoothed contours when available.
            # When neither is checked, still export the unmodified basin.
            geometries = {
                kind: QgsGeometry(geometry)
                for kind, geometry in self.preview_geometries.items()
                if kind in ('original', 'suavizada') and not geometry.isNull()
            }
            if not geometries:
                geometries = {'original': basin_geometry(analysis)}
            from .smooth_export import _export_destinations
            targets = _export_destinations(geometries, destination)
            existing = [path.name for path in targets.values() if path.exists()]
            if existing:
                errors.append('Guardar Divisoria: ya existe ' + ', '.join(existing))
            else:
                export_previewed(analysis, query, geometries, destination,
                                 self.smoothing_degree.value())
                saved.extend(path.name for path in targets.values())
        except Exception as error:
            errors.append('Guardar Divisoria: ' + str(error))
        return saved, errors

    def _finish_save_consultation(self, destination):
        """Save the complete JSON first; then export the two default SHPs."""
        saved_path = self.save_project(destination)
        try:
            exported, errors = self._export_consultation_layers()
        except Exception as error:
            exported, errors = [], ['Exportaciones automáticas: ' + str(error)]
        if errors:
            QMessageBox.warning(
                self, 'Guardar Consulta',
                'Consulta guardada en:\n' + str(saved_path) +
                '\n\nArchivos SHP: ' +
                (', '.join(exported) if exported else 'ninguno nuevo') +
                '\n\nIncidencias:\n' + '\n'.join(errors))
        else:
            self.status.setText(
                'Consulta guardada con cierre y divisoria en Capas_Generadas.')
        return saved_path

    def _choose_save_project(self):
        self._close_outlet_selection()
        if self._basin_hydrology_busy():
            self.basin_hydrology_widget.set_busy(True, 'Espera a que termine el cálculo antes de guardar el proyecto.')
            return
        if (self._drainage_timer.isActive()
                or (self._drainage_controller is not None and self._drainage_controller.busy)):
            if self._drainage_timer.isActive():self._prepare_drainage()
            self.drainage_status.setText('Espera a que termine la actualización de la red antes de guardar el proyecto.')
            return
        try:
            # Always use the current QGIS profile (default on the user's
            # installation), not the Python home or plugin installation path.
            suggested = default_consultation_path()
        except OSError as error:
            QMessageBox.critical(
                self, 'Guardar Consulta',
                'No se puede crear la carpeta de consultas de HidroCAUMAX: ' + str(error))
            return
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Guardar Consulta', str(suggested), CONSULTATION_FILTER)
        if destination:
            try:
                # A plain .json filename is a fully validated, portable
                # CAUMAX consultation document, not a shallow data export.
                effective = destination if destination.lower().endswith('.json') else destination + '.json'
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
                    self.original_check.setEnabled(False)
                    self.smooth_check.setEnabled(False)
                    self.smoothing_degree.setEnabled(False)
                    self._set_export_enabled(False)
                    self.basin_hydrology_widget.setEnabled(False)
                    self._refresh_drainage_controls()
                    self.save_project_button.setEnabled(False)
                    self.report_button.setEnabled(False)
                    self.resolution_refresh_button.setEnabled(False)
                    self._resolution_options(controller.options)
                    def complete(error=None):
                        if self._disposed:
                            return
                        try:
                            if error:
                                raise ValueError(error)
                            self._finish_save_consultation(effective)
                            self.resolution_status.setText('Proyecto guardado con todas las resoluciones disponibles.')
                        except Exception as error:
                            if not self._disposed:
                                QMessageBox.critical(self,'Guardar proyecto',str(error))
                        finally:
                            if not self._disposed:
                                self._saving_project=False
                                self.original_check.setEnabled(True)
                                self.smooth_check.setEnabled(True)
                                self.smoothing_degree.setEnabled(self.smooth_check.isChecked())
                                self.basin_hydrology_widget.setEnabled(True)
                                self._refresh_drainage_controls()
                                self.save_project_button.setEnabled(True)
                                self.report_button.setEnabled(True)
                                self.resolution_refresh_button.setEnabled(True)
                                self._resolution_options(controller.options)
                                self.resolution_progress.hide()
                                self._refresh_basin_hydrology_controls()
                    def ready(variants):
                        if self._disposed:
                            return
                        if controller.last_errors:
                            complete('No se ha guardado un conjunto incompleto. '+
                                     '; '.join(controller.last_errors.values()))
                        else:
                            self._prepare_resolution_states(complete)
                    controller.ensure_all(ready)
                else:
                    self._finish_save_consultation(effective)
            except Exception as error:
                if not self._disposed:
                    QMessageBox.critical(self, 'Guardar proyecto', str(error))

    def _choose_open_project(self):
        try:
            folder = consultation_directory()
        except OSError as error:
            QMessageBox.critical(
                self, 'Abrir Consulta',
                'No se puede abrir la carpeta de consultas de HidroCAUMAX: ' + str(error))
            return
        # The *.json filter also includes historic *.caumax.json sessions;
        # never hide the requested default .json consultation files.
        source, _ = QFileDialog.getOpenFileName(
            self, 'Abrir Consulta', str(folder), CONSULTATION_FILTER)
        if source:
            try:
                self.load_project(source)
            except Exception as error:
                QMessageBox.critical(self, 'Abrir proyecto', str(error))

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._cancel_legacy_area_recovery()
        self._invalidate_po_clip()
        self._invalidate_pd_clip()
        self.hypsometry_widget.dispose()
        self.quantiles_widget.course_profile.dispose()
        from .delineation_cleanup import unregister_dialog
        unregister_dialog(self)
        self._close_outlet_selection()
        self._cancel_save_surfaces()
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
