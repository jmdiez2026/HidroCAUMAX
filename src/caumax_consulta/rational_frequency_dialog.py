"""Linked table/plot and reversible result-window integration for CAUMAX."""
import math

from qgis.PyQt.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QColorDialog, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QToolTip, QVBoxLayout, QWidget,
)
from qgis.PyQt.QtCore import QLocale

from .native_decimal import format_decimal
from .quantile_graphics import integer_log_ticks
from .rational_frequency import FrequencyPoint, capture_frequency


def display(value, decimals=3):
    return format_decimal(value, f'.{decimals}f').rstrip('0').rstrip(',')


def integer_ticks(low, high, count=9):
    raw = max(1, (high - low) / max(1, count - 1))
    magnitude = 10 ** math.floor(math.log10(raw))
    step = max(v * magnitude for v in (1, 2, 5, 10) if v * magnitude <= raw)
    step = max(1, int(step))
    return list(range(math.ceil(low / step) * step, math.floor(high / step) * step + 1, step))


class FrequencyPlot(QWidget):
    selected = pyqtSignal(float)

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.points = model.points
        self.selected_period = model.accepted_period
        self.labels = False
        self.grid = True
        self.logarithmic = True
        self.color = QColor('#236c9c')
        self.line_width = 2
        self.line_style = Qt.SolidLine
        self._drag = None
        self.setMinimumSize(400, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('Gráfico de periodo de retorno y caudal punta')
        self.reset_view()

    def transform(self, period):
        return math.log10(period) if self.logarithmic else period

    def reset_view(self):
        known = self.model.known
        low, high = min(p for p, q in known), max(p for p, q in known)
        if low == high:
            low, high = max(2, low / 2), min(500, high * 2)
        self.view = [self.transform(low), self.transform(high), 0,
                     max(5., math.ceil(max(q for p, q in known) * 1.15))]
        self.update()

    def plot_rect(self):
        metrics = self.fontMetrics()
        ylabels = integer_ticks(self.view[2], self.view[3]) or [0]
        left = max(metrics.horizontalAdvance(str(v)) for v in ylabels) + 33
        return QRectF(left, 22, max(1, self.width() - left - 24), max(1, self.height() - 83))

    def coordinate(self, period, flow):
        rect = self.plot_rect()
        x0, x1, y0, y1 = self.view
        return QPointF(rect.left() + (self.transform(period) - x0) / (x1 - x0) * rect.width(),
                       rect.bottom() - (flow - y0) / (y1 - y0) * rect.height())

    def ticks(self):
        x0, x1, y0, y1 = self.view
        if self.logarithmic:
            low, high = 10 ** x0, 10 ** x1
            # log10/10** round-off must not replace an exact 500-year endpoint
            # by a 499-year tick or hide the 2-year endpoint.
            low = round(low) if math.isclose(low, round(low), abs_tol=1e-9) else low
            high = round(high) if math.isclose(high, round(high), abs_tol=1e-9) else high
            xticks = integer_log_ticks(low, high, max(6, min(12, self.width() // 65)))
        else:
            xticks = integer_ticks(x0, x1, max(6, self.width() // 65))
        return xticks, integer_ticks(y0, y1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor('white'))
        painter.setFont(self.font())
        rect = self.plot_rect()
        metrics = self.fontMetrics()
        xticks, yticks = self.ticks()
        for p in xticks:
            x = self.coordinate(p, self.view[2]).x()
            if self.grid:
                painter.setPen(QPen(QColor('#e2e7ec'), 1))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(QColor('#303d48'))
            text = str(p)
            painter.drawLine(QPointF(x, rect.bottom()), QPointF(x, rect.bottom() + 4))
            painter.drawText(QRectF(x - metrics.horizontalAdvance(text) / 2 - 2,
                                   rect.bottom() + 6, metrics.horizontalAdvance(text) + 4, 20),
                             Qt.AlignCenter, text)
        for q in yticks:
            y = self.coordinate(self.model.known[0][0], q).y()
            if self.grid:
                painter.setPen(QPen(QColor('#e2e7ec'), 1))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor('#303d48'))
            painter.drawText(QRectF(20, y - 10, rect.left() - 28, 20), Qt.AlignRight | Qt.AlignVCenter, str(q))
        painter.setPen(QPen(QColor('#677581'), 1))
        painter.drawRect(rect)
        painter.drawText(QRectF(rect.left(), rect.bottom() + 32, rect.width(), 24),
                         Qt.AlignCenter, 'Periodo de retorno (T) · años')
        painter.save()
        painter.translate(14, rect.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-rect.height() / 2, -12, rect.height(), 24),
                         Qt.AlignCenter, 'Caudal punta (m³/s)')
        painter.restore()
        painter.save()
        painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
        curve = QPainterPath()
        known = self.model.known
        for index, ((p1, q1), (p2, q2)) in enumerate(zip(known, known[1:])):
            if not index:
                curve.moveTo(self.coordinate(p1, q1))
            for step in range(1, 33):
                fraction = step / 32
                period = math.exp(math.log(p1) + fraction * math.log(p2 / p1))
                curve.lineTo(self.coordinate(period, q1 + fraction * (q2 - q1)))
        painter.setPen(QPen(self.color, self.line_width, self.line_style))
        painter.drawPath(curve)
        for point in self.points:
            if point.flow is None:
                continue
            selected = math.isclose(point.period, self.selected_period, rel_tol=1e-10)
            pos = self.coordinate(point.period, point.flow)
            painter.setPen(QPen(QColor('#b84d12') if selected else self.color, 2))
            painter.setBrush(QColor('#ffe0b2') if selected else QColor('white'))
            radius = 6 if selected else 4
            painter.drawEllipse(pos, radius, radius)
        painter.restore()
        if self.labels:
            # Test placements against preceding labels and the plot bounds.
            occupied = []
            for point in sorted(self.points, key=lambda p: p.period != self.selected_period):
                if point.flow is None:
                    continue
                pos = self.coordinate(point.period, point.flow)
                if not rect.contains(pos):
                    continue
                text = f'{display(point.period)}; {display(point.flow, 2)}'
                width = metrics.horizontalAdvance(text) + 8
                height = metrics.height() + 4
                for dx, dy in ((8, -height - 4), (8, 5), (-width - 8, -height - 4),
                               (-width - 8, 5), (8, -2 * height - 4)):
                    label = QRectF(pos.x() + dx, pos.y() + dy, width, height)
                    if rect.contains(label) and not any(label.intersects(other) for other in occupied):
                        painter.fillRect(label, QColor(255, 255, 255, 225))
                        painter.setPen(QColor('#303d48'))
                        painter.drawText(label, Qt.AlignCenter, text)
                        occupied.append(label)
                        break

    def nearest(self, position):
        candidates = [(math.hypot(self.coordinate(p.period, p.flow).x() - position.x(),
                                 self.coordinate(p.period, p.flow).y() - position.y()), p)
                      for p in self.points if p.flow is not None and
                      self.plot_rect().contains(self.coordinate(p.period, p.flow))]
        distance, point = min(candidates, key=lambda item: item[0]) if candidates else (100, None)
        return point if distance <= 10 else None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            point = self.nearest(event.pos())
            if point is not None:
                self.selected.emit(point.period)
            elif self.plot_rect().contains(event.pos()):
                self._drag = (event.pos(), list(self.view))
                self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag:
            initial, view = self._drag
            rect = self.plot_rect()
            dx = (event.pos().x() - initial.x()) / rect.width() * (view[1] - view[0])
            dy = (event.pos().y() - initial.y()) / rect.height() * (view[3] - view[2])
            dx = min(dx, view[0] - (0 if self.logarithmic else 1))
            dy = max(dy, -view[2])
            self.view = [view[0] - dx, view[1] - dx, view[2] + dy, view[3] + dy]
            # Bound arbitrary dragging to avoid overflow in logarithmic axes.
            if self.logarithmic and self.view[1] > 6:
                self.view[:2] = view[:2]
            self.update()
        else:
            point = self.nearest(event.pos())
            if point:
                QToolTip.showText(event.globalPos(),
                    f'T = {display(point.period)} años · Q = {display(point.flow, 2)} m³/s', self)
            else:
                QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        rect = self.plot_rect()
        pos = event.position() if hasattr(event, 'position') else event.posF()
        if not rect.contains(pos):
            event.ignore()
            return
        factor = .8 if event.angleDelta().y() > 0 else 1.25
        x0, x1, y0, y1 = self.view
        span_x = min(6 if self.logarithmic else 5000, max(.08 if self.logarithmic else 5, (x1 - x0) * factor))
        span_y = max(5, (y1 - y0) * factor)
        fx, fy = (pos.x() - rect.left()) / rect.width(), (rect.bottom() - pos.y()) / rect.height()
        nx = max(0 if self.logarithmic else 1, x0 + fx * (x1 - x0 - span_x))
        if self.logarithmic:
            nx = min(nx, 6 - span_x)
        ny = max(0, y0 + fy * (y1 - y0 - span_y))
        self.view = [nx, nx + span_x, ny, ny + span_y]
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.reset_view()


class RationalFrequencyDialog(QDialog):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.setObjectName('hidrocaumax_rational_frequency')
        self.setWindowTitle('HidroCAUMAX · Periodo de retorno y caudal punta')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.model = model
        self.rows = {p.period: p for p in model.points}
        self.setLocale(QLocale(QLocale.Spanish, QLocale.Spain))
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        description = QLabel('Caudales del método racional para la cuenca calculada. '
            'Seleccione una fila o un punto para resaltarlo en ambos.', self)
        description.setWordWrap(True)
        layout.addWidget(description)
        splitter = QSplitter(Qt.Horizontal, self)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(['Periodo de retorno\nT (años)', 'Caudal punta\n(m³/s)'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumWidth(230)
        self.table.setMinimumHeight(220)
        self.plot = FrequencyPlot(model, self)
        splitter.addWidget(self.table)
        splitter.addWidget(self.plot)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 735])
        layout.addWidget(splitter, 1)
        controls = QHBoxLayout()
        self.labels_check = QCheckBox('Mostrar etiquetas', self)
        self.grid_check = QCheckBox('Cuadrícula', self)
        self.grid_check.setChecked(True)
        self.log_check = QCheckBox('Escala logarítmica de T', self)
        self.log_check.setChecked(True)
        self.color_button = QPushButton('Color…', self)
        self.style_combo = QComboBox(self)
        for text, value in [('Línea continua', Qt.SolidLine), ('Línea discontinua', Qt.DashLine), ('Línea de puntos', Qt.DotLine)]:
            self.style_combo.addItem(text, value)
        self.width_spin = QSpinBox(self)
        self.width_spin.setRange(1, 5)
        self.width_spin.setValue(2)
        reset = QPushButton('Restablecer vista', self)
        for widget in (self.labels_check, self.grid_check, self.log_check, self.color_button,
                       self.style_combo, QLabel('Grosor:', self), self.width_spin, reset):
            controls.addWidget(widget)
        controls.addStretch(1)
        layout.addLayout(controls)
        help_label = QLabel('Rueda: ampliar o reducir · Arrastrar: desplazar · Doble clic: restablecer. '
                            'Los ejes muestran marcas enteras; los puntos conservan sus decimales.', self)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        queries = QHBoxLayout()
        self.period_spin = self._spin(2, 500, model.accepted_period)
        self.flow_spin = self._spin(0, max(1e6, model.accepted_flow * 100), model.accepted_flow)
        self.flow_spin.setDecimals(2)
        self.forward_result, self.inverse_result = QLabel(self), QLabel(self)
        self.forward_result.setWordWrap(True)
        self.inverse_result.setWordWrap(True)
        self.forward_button = QPushButton('Estimar caudal', self)
        self.inverse_button = QPushButton('Estimar periodo', self)
        queries.addWidget(self._query_box('Periodo → caudal', 'Periodo de retorno (T) · años',
                                         self.period_spin, self.forward_button, self.forward_result))
        queries.addWidget(self._query_box('Caudal → periodo', 'Caudal punta (m³/s)',
                                         self.flow_spin, self.inverse_button, self.inverse_result))
        layout.addLayout(queries)
        self.method_label = QLabel('Estimaciones: interpolación lineal del caudal respecto a log(T), '
            'entre los valores disponibles, incluido el resultado aceptado por CAUMAX. '
            'No se extrapola ni se modifica el cálculo principal.', self)
        self.method_label.setWordWrap(True)
        layout.addWidget(self.method_label)
        unavailable = [p for p in model.points if p.flow is None]
        if unavailable:
            warning = QLabel('Periodos sin caudal: ' + ', '.join(display(p.period) for p in unavailable)
                             + ' años. ' + ' '.join(dict.fromkeys(p.note for p in unavailable)), self)
            warning.setWordWrap(True)
            layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.button(QDialogButtonBox.Close).setText('Cerrar')
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.table.itemSelectionChanged.connect(self._table_selected)
        self.plot.selected.connect(self.select_period)
        self.labels_check.toggled.connect(lambda value: self._plot_setting('labels', value))
        self.grid_check.toggled.connect(lambda value: self._plot_setting('grid', value))
        self.log_check.toggled.connect(self._set_scale)
        self.width_spin.valueChanged.connect(lambda value: self._plot_setting('line_width', value))
        self.style_combo.currentIndexChanged.connect(lambda _: self._plot_setting('line_style', self.style_combo.currentData()))
        self.color_button.clicked.connect(self._choose_color)
        reset.clicked.connect(self.plot.reset_view)
        self.forward_button.clicked.connect(self.estimate_forward)
        self.inverse_button.clicked.connect(self.estimate_inverse)
        self._refresh(model.accepted_period)
        available = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(1120, available.width() - 40), min(700, available.height() - 60))

    def _spin(self, low, high, value):
        spin = QDoubleSpinBox(self)
        spin.setLocale(self.locale())
        spin.setDecimals(3)
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setKeyboardTracking(False)
        return spin

    def _query_box(self, title, caption, spin, button, result):
        box = QGroupBox(title, self)
        grid = QGridLayout(box)
        grid.addWidget(QLabel(caption, box), 0, 0)
        grid.addWidget(spin, 0, 1)
        grid.addWidget(button, 1, 1)
        grid.addWidget(result, 1, 0)
        grid.setColumnStretch(0, 1)
        return box

    def _plot_setting(self, key, value):
        setattr(self.plot, key, value)
        self.plot.update()

    def _set_scale(self, checked):
        self.plot.logarithmic = checked
        self.plot.reset_view()

    def _choose_color(self):
        color = QColorDialog.getColor(self.plot.color, self, 'Color de la curva')
        if color.isValid():
            self._plot_setting('color', color)

    def _refresh(self, selected):
        self.table.blockSignals(True)
        ordered = sorted(self.rows.values(), key=lambda p: p.period)
        self.table.setRowCount(len(ordered))
        for row, point in enumerate(ordered):
            values = (display(point.period), '—' if point.flow is None else display(point.flow, 2))
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, point.period)
                item.setToolTip(point.note or f'T = {display(point.period)} · Q = {display(point.flow, 2)}')
                if column < 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
        self.plot.points = tuple(ordered)
        self.table.blockSignals(False)
        self.select_period(selected)

    def select_period(self, period):
        for row in range(self.table.rowCount()):
            if math.isclose(self.table.item(row, 0).data(Qt.UserRole), period, rel_tol=1e-10):
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
                self.plot.selected_period = period
                self.plot.update()
                return

    def _table_selected(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            self.plot.selected_period = self.table.item(row, 0).data(Qt.UserRole)
            self.plot.update()

    def _add(self, period, flow, origin):
        existing = next((p for p in self.rows if math.isclose(p, period, rel_tol=1e-10)), None)
        if existing is not None:
            if self.rows[existing].flow is not None:
                return existing
            period = existing
        self.rows[period] = FrequencyPoint(period, flow, origin)
        return period

    def estimate_forward(self):
        try:
            period = self.period_spin.value()
            flow = self.model.flow_at(period)
            self.forward_result.setText(f'Caudal punta (m³/s): {display(flow, 2)}')
            period = self._add(period, flow, 'Consulta T → Q')
            self._refresh(period)
        except ValueError as error:
            self.forward_result.setText(str(error))

    def estimate_inverse(self):
        try:
            flow = self.flow_spin.value()
            periods, intervals = self.model.periods_at(flow)
            texts = [display(period) for period in periods]
            texts += [f'{display(a)}–{display(b)}' for a, b in intervals]
            self.inverse_result.setText('Periodo de retorno (T): ' + '; '.join(texts) + ' años'
                + (' (intervalo de soluciones)' if intervals else ' (varias soluciones)' if len(periods) > 1 else ''))
            selected = None
            for period in periods + tuple(p for pair in intervals for p in pair):
                selected = self._add(period, flow, 'Consulta Q → T')
            self._refresh(selected)
        except ValueError as error:
            self.inverse_result.setText(str(error))


class RationalFrequencyBridge(QObject):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.dialogs = {}

    def calculation_started(self, owner, stage):
        previous = self.dialogs.get(id(owner))
        if previous is not None:
            previous.close()

    def calculation_finished(self, owner, stage, event):
        if stage != 'flow':
            return
        try:
            model = capture_frequency(owner, event)
            if model is None:
                return
            self.calculation_started(owner, stage)
            dialog = RationalFrequencyDialog(model, owner)
            self.dialogs[id(owner)] = dialog
            dialog.finished.connect(lambda _, key=id(owner), current=dialog: self._forget(key, current))
            dialog.destroyed.connect(lambda _, key=id(owner), current=dialog: self._forget(key, current))
            owner.finished.connect(dialog.close)
            dialog.show()
            dialog.raise_()
        except Exception as error:
            # An optional presentation must never break the accepted native Q
            # or propagate an exception out of a Qt signal into QGIS.
            self.iface.messageBar().pushWarning('HidroCAUMAX',
                'No se pudo abrir la tabla de caudales: ' + str(error))

    def _forget(self, key, dialog):
        if self.dialogs.get(key) is dialog:
            self.dialogs.pop(key, None)

    def stop(self):
        for dialog in tuple(self.dialogs.values()):
            dialog.close()
        self.dialogs.clear()
