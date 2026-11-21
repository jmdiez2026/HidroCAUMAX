"""Hypsometric plots shared by the QGIS dialog and the PDF (Qt only)."""
import math

from qgis.PyQt.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .parameter_schema import format_value


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def curve_plot_data(analysis, dimensionless=False):
    """Map the saved quantiles to axes; HI always comes from the full analysis."""
    values = getattr(analysis, 'values', {}) or {}
    hi = _finite(values.get('hypsometric'))
    result = {
        'available': False, 'hi': hi, 'x': [], 'y': [],
        'dimensionless': bool(dimensionless),
        'x_label': 'Área relativa a/A' if dimensionless else 'Área acumulada por encima de la cota (km²)',
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
    result.update(
        available=True, reason='',
        x=fraction if dimensionless else [f * area for f in fraction],
        y=[(h - minimum) / relief for h in heights] if dimensionless else heights,
        x_limits=(0.0, 1.0 if dimensionless else area),
        y_limits=(0.0, 1.0) if dimensionless else (minimum, maximum),
    )
    return result


def _tick(value):
    return f'{value:.4g}'.replace('.', ',')


def paint_hypsometric_curve(painter, bounds, data, font_family=None):
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
    title = 'Curva hipsométrica · ' + ('adimensional' if data['dimensionless'] else 'dimensional')
    title += ' · HI exacto: ' + format_value('hypsometric', data['hi'])
    painter.drawText(QRectF(bounds.left() + 12, bounds.top() + 8, bounds.width() - 24, pixels * 2),
                     Qt.AlignCenter, title)
    if not data['available']:
        painter.drawText(bounds.adjusted(30, pixels * 3, -30, -20),
                         Qt.AlignCenter | Qt.TextWordWrap, data['reason'])
        painter.restore()
        return
    chart = bounds.adjusted(max(80, pixels * 8), pixels * 3, -pixels * 2, -pixels * 5)
    if chart.width() <= 0 or chart.height() <= 0:
        painter.restore()
        return
    xmin, xmax = data['x_limits']
    ymin, ymax = data['y_limits']
    if ymax == ymin:
        padding = max(1.0, abs(ymin) * .005)
        ymin, ymax = ymin - padding, ymax + padding

    def point(x, y):
        return QPointF(chart.left() + (x - xmin) / (xmax - xmin) * chart.width(),
                       chart.bottom() - (y - ymin) / (ymax - ymin) * chart.height())

    for index in range(6):
        value = xmin + index / 5 * (xmax - xmin)
        position = point(value, ymin).x()
        painter.setPen(QPen(QColor('#dce4ec'), 1, Qt.DashLine))
        painter.drawLine(QPointF(position, chart.top()), QPointF(position, chart.bottom()))
        painter.setPen(QColor('#203449'))
        painter.drawText(QRectF(position - pixels * 3, chart.bottom() + 5, pixels * 6, pixels * 2),
                         Qt.AlignHCenter | Qt.AlignTop, _tick(value))
    for index in range(5):
        value = ymin + index / 4 * (ymax - ymin)
        position = point(xmin, value).y()
        painter.setPen(QPen(QColor('#dce4ec'), 1, Qt.DashLine))
        painter.drawLine(QPointF(chart.left(), position), QPointF(chart.right(), position))
        painter.setPen(QColor('#203449'))
        painter.drawText(QRectF(chart.left() - pixels * 5.5, position - pixels, pixels * 5, pixels * 2),
                         Qt.AlignRight | Qt.AlignVCenter, _tick(value))
    painter.setPen(QPen(QColor('#334155'), 1.2))
    painter.drawRect(chart)
    painter.save()
    painter.setClipRect(chart.adjusted(-1, -1, 1, 1))
    path = QPainterPath(point(data['x'][0], data['y'][0]))
    for x, y in zip(data['x'][1:], data['y'][1:]):
        path.lineTo(point(x, y))
    painter.setPen(QPen(QColor('#1383b5'), max(2.0, pixels / 6)))
    painter.drawPath(path)
    painter.restore()
    painter.setPen(QColor('#203449'))
    painter.drawText(QRectF(chart.left(), chart.bottom() + pixels * 2.5,
                           chart.width(), pixels * 2), Qt.AlignCenter, data['x_label'])
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
        paint_hypsometric_curve(painter, self.rect(), self.owner.plot_data())


class HypsometricWidget(QWidget):
    """Basin curve and display-mode control; it never reads or recalculates a DEM."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.analysis = None
        self.dimensionless_check = QCheckBox('Adimensional', self)
        self.dimensionless_check.setChecked(False)
        self.hi_label = QLabel(self)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.canvas = _CurveCanvas(self)
        header = QHBoxLayout()
        header.addWidget(self.hi_label)
        header.addStretch()
        header.addWidget(self.dimensionless_check)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.canvas)
        layout.addWidget(self.status_label)
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
        self.hi_label.setText('Índice hipsométrico (HI) exacto: ' + format_value('hypsometric', data['hi']))
        self.status_label.setText(
            (str(data['sample_count']) + ' puntos · ' + str(data['valid_count'])
             + (' cotas válidas. HI usa la media de cotas ponderada por superficie; la integral de la curva es aproximada.'
                if (getattr(self.analysis, 'diagnostics', {}) or {}).get('analysis_surface') else
                ' cotas válidas. HI usa la media aritmética de las cotas; la integral de la curva es aproximada.'))
            if data['available'] else data['reason']
        )
        self.canvas.update()
