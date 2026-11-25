"""Reusable, full-width controls for the improved-resolution drainage products."""
from html import escape

from qgis.PyQt.QtCore import QTimer, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QFontMetrics
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout,
    QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


BUTTON_STYLE = '''
QPushButton { background-color: #D9F0FF; color: #17374B;
 border: 1px solid #7EB4D0; border-radius: 3px; padding: 2px 4px;
 min-height: 0px; min-width: 0px; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:pressed { background-color: #B1DDF6; }
QPushButton:disabled { background-color: #EEF6FB; color: #8A9EA8; border-color: #C6DAE5; }
'''


class DrainageStatusLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setOpenExternalLinks(True)

    def setText(self, text):
        super().setText(escape(str(text)) + ' TauDEM y sus dependencias están incluidos '
            'para Windows de 64 bits. <a href="https://hydrology.usu.edu/taudem/taudem5/documentation.html">'
            'Documentación</a>.')


class DrainageControls(QGroupBox):
    """The four actions are 110% of their unscaled size at the chosen font.

    Layout uses this group's real width, with no dependency on a basin sidebar.
    Repeated resize events always measure the unscaled hint before applying the
    factor, so buttons never grow cumulatively.
    """
    prepare_requested = pyqtSignal()
    threshold_changed = pyqtSignal(float)
    automatic_changed = pyqtSignal(bool)
    visibility_changed = pyqtSignal(bool)
    export_requested = pyqtSignal(str)
    BUTTON_SCALE = 1.10

    def __init__(self, parent=None):
        super().__init__('Generación capas resolución mejorada', parent)
        # The scroll viewport supplies the horizontal size. Its next width
        # must not be constrained by fonts fitted for the previous viewport.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        # Font fitting runs after a resize. Do not let the previous, wider
        # button sizes become this widget's hard minimum before it can adapt.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.help_label = QLabel('Genera el MDT elegido, el ráster de Dirección de Flujo y el shape '
            'de la Red de Drenaje con el esquema D8 (TauDEM/Esri)', self)
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)
        row = QHBoxLayout()
        row.setSpacing(3)
        self.drainage_prepare_button = QPushButton('Generar capas D8', self)
        self.drainage_prepare_button.setStyleSheet(BUTTON_STYLE)
        self.threshold_label = QLabel('Umbral inicio corriente', self)
        self.drainage_threshold = QDoubleSpinBox(self)
        self.drainage_threshold.setDecimals(6)
        self.drainage_threshold.setRange(.000001, 1000000)
        self.drainage_threshold.setSuffix(' km²')
        self.drainage_threshold.setSingleStep(.000001)  # calibrated when basin area is known
        self.drainage_threshold.setToolTip(
            'Umbral de superficie contribuyente para iniciar los cauces D8. '
            'En automático se ensaya una proporción orientativa del área de cuenca; '
            'las flechas ajustan pasos pequeños y proporcionales. '
            'No determina el nacimiento real del cauce.')
        self.drainage_auto = QCheckBox('Automático', self)
        self.drainage_auto.setChecked(True)
        self.drainage_visible = QCheckBox('Mostrar red', self)
        self.drainage_visible.setChecked(True)
        for check in (self.drainage_auto, self.drainage_visible):
            check.setStyleSheet('QCheckBox { spacing: 2px; }')
        self._drainage_controls = (self.drainage_prepare_button, self.threshold_label,
            self.drainage_threshold, self.drainage_auto, self.drainage_visible)
        self._drainage_controls_layout = row
        for control in self._drainage_controls:
            row.addWidget(control)
        row.addStretch(1)
        layout.addLayout(row)
        density = QLabel('Umbral orientativo: 0,5 % de la cuenca, ajustado a la malla; '
            'si no se obtiene red, el modo automático ensaya umbrales menores. '
            'Las flechas cambian el 0,01 % del área (mínimo una celda). '
            'Un valor menor produce una red más densa; contrasta el resultado con los cauces reales.', self)
        density.setWordWrap(True)
        layout.addWidget(density)
        self._drainage_exports_widget = QWidget(self)
        self._drainage_exports_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        exports = QGridLayout(self._drainage_exports_widget)
        exports.setContentsMargins(0, 0, 0, 0)
        exports.setHorizontalSpacing(4)
        exports.setVerticalSpacing(3)
        self.dem_export_button = QPushButton('Exportar MDT (TIFF)…', self)
        self.directions_export_button = QPushButton('Exportar dirección de drenaje (TIFF)…', self)
        self.network_export_button = QPushButton('Exportar Red de Drenaje (SHP)…', self)
        for column, (title, button, kind) in enumerate((
            ('Modelo Digital Terreno', self.dem_export_button, 'dem'),
            ('Direcciones de Drenaje', self.directions_export_button, 'directions'),
            ('Red de drenaje', self.network_export_button, 'network'))):
            label = QLabel(title, self)
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            exports.addWidget(label, 0, column)
            button.setStyleSheet(BUTTON_STYLE + '\nQPushButton { padding: 2px 1px; }')
            button.clicked.connect(lambda checked=False, kind=kind: self.export_requested.emit(kind))
            exports.addWidget(button, 1, column, Qt.AlignHCenter)
            exports.setColumnStretch(column, 1)
        layout.addWidget(self._drainage_exports_widget)
        self.drainage_status = DrainageStatusLabel(self)
        self.drainage_status.setText('TauDEM predeterminado seleccionado. Pulsa Generar capas D8.')
        layout.addWidget(self.drainage_status)
        self.drainage_progress = QProgressBar(self)
        self.drainage_progress.setRange(0, 100)
        self.drainage_progress.hide()
        layout.addWidget(self.drainage_progress)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit_to_width)
        self.drainage_prepare_button.clicked.connect(lambda _=False: self.prepare_requested.emit())
        self.drainage_threshold.valueChanged.connect(self.threshold_changed)
        self.drainage_threshold.valueChanged.connect(lambda _=None: self._fit_timer.start(0))
        self.drainage_auto.toggled.connect(self.automatic_changed)
        self.drainage_visible.toggled.connect(self.visibility_changed)
        self._fitting = False
        self.button_base_sizes = {}
        self.fit_to_width()

    @property
    def product_buttons(self):
        return (self.dem_export_button, self.directions_export_button, self.network_export_button)

    def _scale_button(self, button):
        button.setMinimumSize(0, 0)
        button.setMaximumSize(16777215, 16777215)
        button.ensurePolished()
        size = button.sizeHint()
        self.button_base_sizes[button] = size
        button.setFixedSize(round(size.width() * self.BUTTON_SCALE),
                            round(size.height() * self.BUTTON_SCALE))

    def fit_to_width(self, width=None):
        if self._fitting:
            return
        self._fitting = True
        try:
            margins = self.layout().contentsMargins()
            available = max(1, (self.contentsRect().width() if width is None else width)
                            - margins.left() - margins.right())
            for controls, spacing, maximum in ((self._drainage_controls, 3, 12.),
                                               (self.product_buttons, 4, 8.5)):
                font = QFont(self.font())
                points = min(maximum, font.pointSizeF() if font.pointSizeF() > 0 else 10.)
                minimum = min(points, 7.5)
                while True:
                    font.setPointSizeF(points)
                    for control in controls:
                        control.setFont(font)
                        if isinstance(control, QPushButton):
                            self._scale_button(control)
                    if controls is self._drainage_controls:
                        self.drainage_threshold.lineEdit().setFont(font)
                        self.drainage_threshold.setFixedWidth(
                            QFontMetrics(font).horizontalAdvance(self.drainage_threshold.text()) + 26)
                    needed = sum(control.width() if isinstance(control, (QPushButton, QDoubleSpinBox))
                                 else control.sizeHint().width() for control in controls)
                    if needed + spacing * (len(controls)-1) <= available or points <= minimum:
                        break
                    points = max(minimum, points-.25)
            self.setMinimumHeight(0)
            height = self.layout().heightForWidth(self.width() if width is None else width)
            if height > 0:
                self.setMinimumHeight(height)
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_fit_timer'):
            self._fit_timer.start(0)
