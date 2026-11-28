"""Hypsometric plots shared by the QGIS dialog and the PDF (Qt only)."""
import math
from pathlib import Path

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _area_axis(area_km2):
    """Readable integer ticks without rounding or replacing the curve samples."""
    scale, unit = (1e6, 'm²') if area_km2 < 1 else (1.0, 'km²')
    extent = area_km2 * scale
    target = max(1.0, extent / 10)
    magnitude = 10 ** math.floor(math.log10(target))
    step = min((int(factor * magnitude) for factor in (1, 2, 5, 10)),
               key=lambda candidate: abs(math.log(candidate / target)))
    maximum = max(step, math.ceil(extent / step) * step)
    return scale, maximum, tuple(range(0, maximum + 1, step)), unit


def _altitude_axis(minimum, maximum):
    """Integer metre marks; display bounds never alter the sampled elevations."""
    if minimum == maximum:
        padding = max(1.0, abs(minimum) * .005)
        minimum, maximum = minimum - padding, maximum + padding
    target = max(1.0, (maximum - minimum) / 8)
    magnitude = 10 ** math.floor(math.log10(target))
    step = min((int(factor * magnitude) for factor in (1, 2, 5, 10)),
               key=lambda candidate: abs(math.log(candidate / target)))
    first = math.floor(minimum / step) * step
    last = max(first + step, math.ceil(maximum / step) * step)
    return (first, last), tuple(range(first, last + 1, step))


def curve_plot_data(analysis, dimensionless=False):
    """Map the saved quantiles to axes; HI always comes from the full analysis."""
    values = getattr(analysis, 'values', {}) or {}
    hi = _finite(values.get('hypsometric'))
    result = {
        'available': False, 'hi': hi, 'x': [], 'y': [],
        'dimensionless': bool(dimensionless),
        'x_label': 'Área relativa (a/A)' if dimensionless else 'Área acumulada por encima de la cota (km²)',
        'y_label': 'Altitud relativa (h−Hmín)/ΔH' if dimensionless else 'Altitud (m)',
        'reason': 'Curva no disponible: esta consulta no contiene la distribución de cotas. Recalcula la cuenca para obtenerla.',
    }
    curve = (getattr(analysis, 'diagnostics', {}) or {}).get('hypsometric_curve') or {}
    try:
        fraction = [_finite(v) for v in curve['fraction']]
        heights = [_finite(v) for v in curve['elevation_m']]
        area = _finite(curve['area_km2'])
        valid_count = int(curve['valid_count'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return result
    if (len(fraction) < 2 or len(heights) != len(fraction) or valid_count < 1
            or area is None or area <= 0
            or any(v is None for v in fraction + heights)):
        return result
    if (not math.isclose(fraction[0], 0, abs_tol=1e-9)
            or not math.isclose(fraction[-1], 1, abs_tol=1e-9)
            or any(a > b for a, b in zip(fraction, fraction[1:]))
            or any(a < b for a, b in zip(heights, heights[1:]))):
        result['reason'] = 'Curva no disponible: la distribución de cotas guardada no es válida.'
        return result
    minimum, maximum = heights[-1], heights[0]
    relief = maximum - minimum
    result.update(valid_count=valid_count, sample_count=len(fraction), area_km2=area,
                  zmin_m=minimum, zmax_m=maximum)
    if dimensionless and relief == 0:
        result.update(hi=None, reason='Curva adimensional no definida: la cuenca no tiene desnivel (Hmáx = Hmín).')
        return result
    if dimensionless:
        scale, axis_maximum, ticks = 1.0, 1.0, tuple(i / 10 for i in range(11))
        y_display_limits, y_ticks = (0.0, 1.0), ticks
    else:
        scale, axis_maximum, ticks, unit = _area_axis(area)
        result['x_label'] = f'Área acumulada por encima de la cota ({unit})'
        y_display_limits, y_ticks = _altitude_axis(minimum, maximum)
    result.update(
        available=True, reason='',
        x=fraction if dimensionless else [f * area for f in fraction],
        y=[(h - minimum) / relief for h in heights] if dimensionless else heights,
        x_limits=(0.0, 1.0 if dimensionless else area),
        x_display_limits=(0.0, axis_maximum), x_scale=scale, x_ticks=ticks,
        y_limits=(0.0, 1.0) if dimensionless else (minimum, maximum),
        y_display_limits=y_display_limits, y_ticks=y_ticks,
    )
    return result


def _tick(value):
    number = _finite(value)
    if number is None:
        return 'No disponible'
    # Display rounding only: the full-precision data and exact HI are retained.
    text = f'{number:.2f}'.rstrip('0').rstrip('.')
    return ('0' if text == '-0' else text).replace('.', ',')


def _title(hi):
    value = 'No disponible' if hi is None else f'{hi:.2f}'.replace('.', ',')
    return 'Índice hipsométrico (HI) = ' + value



def curve_chart_rect(bounds):
    """Use exactly the same geometry for rendering and mouse hit testing."""
    bounds = QRectF(bounds)
    pixels = max(11, min(18, round(bounds.width() / 70)))
    return bounds.adjusted(max(80, pixels * 8), pixels * 3,
                           -pixels * 2, -pixels * 4)


def curve_plot_point(data, bounds, index):
    """Screen coordinate of one saved quantile in either curve mode."""
    chart = curve_chart_rect(bounds)
    xmin, xmax = data['x_display_limits']
    ymin, ymax = data['y_display_limits']
    return QPointF(chart.left() +
                   (data['x'][index] * data['x_scale'] - xmin) / (xmax - xmin) * chart.width(),
                   chart.bottom() -
                   (data['y'][index] - ymin) / (ymax - ymin) * chart.height())


def paint_hypsometric_curve(painter, bounds, data, font_family=None, control_width=0):
    """Paint the same scientific axes for the on-screen widget and PNG export."""
    bounds = QRectF(bounds)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.fillRect(bounds, QColor('white'))
    pixels = max(11, min(18, round(bounds.width() / 70)))
    font = QFont(font_family or painter.font().family())
    font.setPixelSize(pixels)
    painter.setFont(font)
    painter.setPen(QColor('#203449'))
    painter.drawText(QRectF(bounds.left() + 12, bounds.top() + 8,
                           bounds.width() - 24 - control_width, pixels * 2),
                     Qt.AlignCenter, _title(data['hi']))
    if not data['available']:
        painter.drawText(bounds.adjusted(30, pixels * 3, -30, -20),
                         Qt.AlignCenter | Qt.TextWordWrap, data['reason'])
        painter.restore()
        return
    chart = curve_chart_rect(bounds)
    if chart.width() <= 0 or chart.height() <= 0:
        painter.restore()
        return
    xmin, xmax = data['x_display_limits']
    ymin, ymax = data['y_display_limits']

    def point(x, y):
        return QPointF(chart.left() + (x - xmin) / (xmax - xmin) * chart.width(),
                       chart.bottom() - (y - ymin) / (ymax - ymin) * chart.height())

    ticks = data['x_ticks']
    tick_label = (lambda value: f'{value:.2f}'.replace('.', ',')) if data['dimensionless'] else _tick
    labels = [tick_label(value) for value in ticks]
    spacing = chart.width() / max(1, len(ticks)-1)
    text_width = max(painter.fontMetrics().horizontalAdvance(text) for text in labels)
    label_stride = max(1, math.ceil((text_width + 8) / spacing))
    for index, value in enumerate(ticks):
        position = point(value, ymin).x()
        painter.setPen(QPen(QColor('#dce4ec'), 1, Qt.DashLine))
        painter.drawLine(QPointF(position, chart.top()), QPointF(position, chart.bottom()))
        painter.setPen(QColor('#203449'))
        if index % label_stride == 0 or index == len(ticks)-1:
            # Keep the last label clear of a preceding one on narrow canvases.
            if (index != len(ticks)-1 and index > 0
                    and len(ticks)-1-index < label_stride):
                continue
            label_width = max(pixels * 3, text_width + 4)
            painter.drawText(QRectF(position-label_width/2, chart.bottom()+4,
                                   label_width, pixels*1.5),
                             Qt.AlignHCenter | Qt.AlignTop, labels[index])
    for value in data['y_ticks']:
        position = point(xmin, value).y()
        painter.setPen(QPen(QColor('#dce4ec'), 1, Qt.DashLine))
        painter.drawLine(QPointF(chart.left(), position), QPointF(chart.right(), position))
        painter.setPen(QColor('#203449'))
        painter.drawText(QRectF(chart.left() - pixels * 5.5, position - pixels, pixels * 5, pixels * 2),
                         Qt.AlignRight | Qt.AlignVCenter, tick_label(value))
    painter.setPen(QPen(QColor('#334155'), 1.2))
    painter.drawRect(chart)
    painter.save()
    painter.setClipRect(chart.adjusted(-1, -1, 1, 1))
    path = QPainterPath(point(data['x'][0]*data['x_scale'], data['y'][0]))
    for x, y in zip(data['x'][1:], data['y'][1:]):
        path.lineTo(point(x*data['x_scale'], y))
    painter.setPen(QPen(QColor('#1383b5'), max(2.0, pixels / 6)))
    painter.drawPath(path)
    painter.restore()
    painter.setPen(QColor('#203449'))
    painter.drawText(QRectF(chart.left(), chart.bottom() + pixels * 1.9,
                           chart.width(), pixels * 1.6), Qt.AlignCenter, data['x_label'])
    painter.save()
    painter.translate(bounds.left() + pixels * 1.1, chart.center().y())
    painter.rotate(-90)
    painter.drawText(QRectF(-chart.height() / 2, -pixels, chart.height(), pixels * 2),
                     Qt.AlignCenter, data['y_label'])
    painter.restore()
    painter.restore()


def render_hypsometric_png(analysis, dimensionless=False, width=1280, height=600, font_family=None):
    """Render a self-contained PNG without an interactive widget or new library."""
    image = QImage(int(width), int(height), QImage.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    try:
        paint_hypsometric_curve(painter, QRectF(0, 0, width, height),
                                curve_plot_data(analysis, dimensionless), font_family)
    finally:
        painter.end()
    stream = QBuffer()
    stream.open(QIODevice.WriteOnly)
    if not image.save(stream, 'PNG'):
        raise OSError('No se ha podido dibujar la curva hipsométrica.')
    return bytes(stream.data())


class _CurveCanvas(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setMinimumSize(320, 260)

    def paintEvent(self, event):
        painter = QPainter(self)
        data = self.owner.plot_data()
        paint_hypsometric_curve(painter, self.rect(), data,
                                control_width=self.owner.dimensionless_check.width() + 12)
        i = self.owner._selected_index
        if data['available'] and i is not None and 0 <= i < len(data['x']):
            point = curve_plot_point(data, self.rect(), i)
            chart = curve_chart_rect(self.rect())
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(QColor('#DC6F13'), 1.5, Qt.DashLine))
            painter.drawLine(QPointF(point.x(), chart.top()),
                             QPointF(point.x(), chart.bottom()))
            painter.setPen(QPen(QColor('#C15E12'), 2))
            painter.setBrush(QColor('#FDA23A'))
            painter.drawEllipse(point, 5, 5)
            z = self.owner.analysis.diagnostics['hypsometric_curve']['elevation_m'][i]
            area = data['x'][i]
            label = (f'Altitud: {z:.2f} m · Área: {area:.3f}'
                     + (' (a/A)' if data['dimensionless'] else ' km²'))
            metrics = painter.fontMetrics()
            width = min(chart.width() - 8, metrics.horizontalAdvance(label) + 18)
            height = metrics.lineSpacing() + 10
            px = min(chart.right() - width - 3, max(chart.left() + 3, point.x() + 10))
            py = max(chart.top() + 3, min(chart.bottom() - height - 3,
                                          point.y() - height - 10))
            painter.setPen(QPen(QColor('#C15E12'), 1))
            painter.setBrush(QColor('white'))
            painter.drawRoundedRect(QRectF(px, py, width, height), 4, 4)
            painter.setPen(QColor('#203449'))
            painter.drawText(QRectF(px + 7, py + 4, width - 14, height - 8),
                             Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        data = self.owner.plot_data()
        if not data['available']:
            return super().mousePressEvent(event)
        chart = curve_chart_rect(self.rect())
        if not chart.adjusted(-8, -8, 8, 8).contains(event.pos()):
            return super().mousePressEvent(event)
        i = min(range(len(data['x'])), key=lambda index:
                (curve_plot_point(data, self.rect(), index).x() - event.x())**2 +
                (curve_plot_point(data, self.rect(), index).y() - event.y())**2)
        point = curve_plot_point(data, self.rect(), i)
        if (point.x() - event.x())**2 + (point.y() - event.y())**2 <= 23**2:
            self.owner.select_quantile(i)
            event.accept()
        else:
            super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self.owner, 'dimensionless_check'):
            return
        check = self.owner.dimensionless_check
        pixels = max(11, min(18, round(self.width() / 70)))
        font = QFont(self.font())
        font.setPixelSize(pixels)
        check.setFont(font)
        check.adjustSize()
        check.move(max(12, self.width() - check.width() - 12),
                   8 + max(0, (2 * pixels - check.height()) // 2))


class HypsometricWidget(QWidget):
    """Basin curve and display-mode control; it never reads or recalculates a DEM."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.analysis = None
        self.canvas = _CurveCanvas(self)
        self.dimensionless_check = QCheckBox('Adimensional', self.canvas)
        self.dimensionless_check.setChecked(False)
        self.dimensionless_check.setStyleSheet('QCheckBox { background-color: white; }')
        # Retain these informational attributes for callers, without extra rows.
        self.hi_label = QLabel(self)
        self.status_label = QLabel(self)
        self.hi_label.hide()
        self.status_label.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.dimensionless_check.toggled.connect(self._refresh)
        self._refresh()

    def set_analysis(self, analysis):
        self.analysis = analysis
        self._refresh()

    def set_dimensionless(self, value):
        self.dimensionless_check.setChecked(bool(value))

    def is_dimensionless(self):
        return self.dimensionless_check.isChecked()

    def plot_data(self):
        return curve_plot_data(self.analysis, self.is_dimensionless())

    def _refresh(self, *unused):
        data = self.plot_data()
        self.hi_label.setText(_title(data['hi']))
        self.status_label.setText('' if data['available'] else data['reason'])
        self.canvas.update()
