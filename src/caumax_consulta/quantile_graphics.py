"""Responsive Qci/T graphics with integer ticks and measured label margins."""
import math
from qgis.PyQt.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QApplication


def integer_log_ticks(low, high, count=12):
    first, last = math.ceil(low), math.floor(high)
    if first > last:
        return []
    values = {first, last} | {value for value in (2, 5, 10, 25, 100, 500) if first <= value <= last}
    while len(values) < count:
        ordered = sorted(values)
        gaps = sorted(((math.log(b / a), a, b) for a, b in zip(ordered, ordered[1:])
                       if b - a > 1), reverse=True)
        if not gaps:
            break
        _, a, b = gaps[0]
        values.add(min(b - 1, max(a + 1, round(math.sqrt(a * b)))))
    return sorted(values)


def geometry(state, known, forward, inverse, width, height, font_family=None, *, fit_height=False):
    width = max(320, int(width))
    font = QFont(font_family or QApplication.font().family())
    font.setPixelSize(round(max(10, min(18, int(width / 65))) * 1.10))
    metrics = QFontMetricsF(font)
    line = math.ceil(metrics.height())
    periods = [period for period, _ in known] or [2., 500.]
    if forward['flow'] is not None:
        periods.append(state['input_period'])
    periods += [item['period'] for item in inverse['solutions'] if item['period'] <= 1e9]
    periods += [item[key] for item in inverse['intervals'] for key in ('min_period', 'max_period')
                if item[key] is not None and 1 < item[key] <= 1e9]
    tmin, tmax = min(periods), max(periods)
    if tmin == tmax:
        tmin, tmax = max(1.001, tmin * .8), tmax * 1.2
    flows = [value for _, value in known]
    if forward['flow'] is not None:
        flows.append(forward['flow'])
    if inverse['solutions'] or inverse['intervals']:
        flows.append(state['input_flow'])
    raw_max = max(flows or [1.])
    raw_max = raw_max * 1.12 if raw_max > 0 else 1.
    step = max(1, math.ceil(raw_max / 9))
    qmax = step * 9 if raw_max >= 9 else max(1, math.ceil(raw_max))
    qticks = list(range(0, qmax + 1, step))
    tticks = integer_log_ticks(tmin, tmax)
    log_range = math.log(tmax / tmin)
    fractions = [math.log(value / tmin) / log_range for value in tticks]
    smallest = min((b - a for a, b in zip(fractions, fractions[1:])), default=1.)
    axis_height = max((len(qticks) - 1) * (line + 3), math.ceil((line + 3) / smallest))
    stacked = width < 580
    rows = 2 if stacked else 1
    top, bottom, legend, gap = line + 12, line * 2 + 12, 0, 14
    # Screen widgets can reduce their established height without rescaling
    # fonts or changing tick values; exported figures retain their minima.
    minimum_axis = line * 2 if fit_height else axis_height
    height = max(int(height), rows * (minimum_axis + top + bottom) + legend + (gap if stacked else 0))
    panels = ([QRectF(0, 0, width, (height - legend - gap) / 2),
               QRectF(0, (height - legend + gap) / 2, width, (height - legend - gap) / 2)]
              if stacked else [QRectF(0, 0, (width - gap) / 2, height - legend),
                               QRectF((width + gap) / 2, 0, (width - gap) / 2, height - legend)])
    axes, ticks = [], []
    for index, panel in enumerate(panels):
        ys = qticks if index == 0 else tticks
        label_width = max((metrics.horizontalAdvance(str(value)) for value in ys), default=10)
        left = line + label_width + 14
        endpoint = metrics.horizontalAdvance(str(round(tmax) if index == 0 else qmax)) / 2 + 6
        rect = QRectF(panel.left() + left, panel.top() + top,
                      panel.width() - left - endpoint, panel.height() - top - bottom)
        axes.append(rect)
        xs = [value for value in (2, 5, 10, 25, 100, 500) if tmin <= value <= tmax] if index == 0 else sorted({round(qmax * i / 4) for i in range(5)})
        if not xs:
            xs = sorted({math.ceil(tmin), math.floor(tmax)})
        positions = [(math.log(value / tmin) / log_range if index == 0 else value / qmax) * rect.width()
                     for value in xs]
        fitted, right_edge = [], -math.inf
        for value, position in zip(xs, positions):
            half = metrics.horizontalAdvance(str(value)) / 2
            if position - half >= right_edge + 5:
                fitted.append(value)
                right_edge = position + half
        ticks.append((fitted, ys))
    return dict(width=width, height=height, font=font, line=line, panels=panels, axes=axes,
                ticks=ticks, qmax=qmax, tmin=tmin, tmax=tmax, legend=legend, stacked=stacked)



def plot_curve_point(layout, period, flow, panel_index):
    """Location of a Q(T) pair in either reciprocal chart, as drawn by render."""
    rect = layout['axes'][panel_index]
    t = math.log(float(period) / layout['tmin']) / math.log(layout['tmax'] / layout['tmin'])
    q = float(flow) / layout['qmax']
    return QPointF(rect.left() + (t if panel_index == 0 else q) * rect.width(),
                   rect.bottom() - (q if panel_index == 0 else t) * rect.height())


def pick_linked_curve(layout, known, pixel_x, pixel_y, tolerance=12.):
    """Pick only a visible blue curve, never the surrounding empty chart.

    Returns the same (return period, Q/Pd) pair regardless of whether the
    user clicked Q(T) or its reciprocal T(Q) panel. Interpolate Q linearly
    with log T, which is exactly how both the plot and Q(T) calculations work.
    """
    if not known:
        return None
    cursor = QPointF(float(pixel_x), float(pixel_y))
    closest = None
    threshold = float(tolerance) ** 2
    for panel_index, axis in enumerate(layout['axes']):
        if not axis.adjusted(-tolerance, -tolerance,
                             tolerance, tolerance).contains(cursor):
            continue
        coords = [plot_curve_point(layout, p, q, panel_index)
                  for p, q in known]
        if len(known) == 1:
            candidates = [(known[0][0], known[0][1],
                           (coords[0].x() - cursor.x()) ** 2
                           + (coords[0].y() - cursor.y()) ** 2)]
        else:
            candidates = []
            for (p1, q1), (p2, q2), a, b in zip(
                    known, known[1:], coords, coords[1:]):
                dx, dy = b.x() - a.x(), b.y() - a.y()
                denominator = dx * dx + dy * dy
                fraction = max(0., min(1., (
                    ((cursor.x() - a.x()) * dx + (cursor.y() - a.y()) * dy)
                    / denominator) if denominator > 0 else 0.))
                px, py = a.x() + dx * fraction, a.y() + dy * fraction
                deviation = (cursor.x() - px) ** 2 + (cursor.y() - py) ** 2
                period = p1 * math.exp(math.log(p2 / p1) * fraction)
                flow = q1 + (q2 - q1) * fraction
                candidates.append((period, flow, deviation))
        for period, flow, distance_sq in candidates:
            if distance_sq <= threshold and (
                    closest is None or distance_sq < closest[2]):
                closest = (period, flow, distance_sq)
    return None if closest is None else closest[:2]


def _draw_linked_selection(painter, layout, selected, quantity, unit):
    """Mark and label the *same* selected pair on both reciprocal axes."""
    if selected is None:
        return
    try:
        period, flow = float(selected[0]), float(selected[1])
    except (TypeError, ValueError, IndexError):
        return
    if (not math.isfinite(period) or not math.isfinite(flow)
            or period < layout['tmin'] or period > layout['tmax']
            or flow < 0 or flow > layout['qmax']):
        return
    painter.setRenderHint(QPainter.Antialiasing, True)
    metrics = painter.fontMetrics()
    fmt = lambda value: f'{value:.2f}'.replace('.', ',')
    lines = (f'T: {fmt(period)} años', f'{quantity}: {fmt(flow)} {unit}')
    width = max(metrics.horizontalAdvance(line) for line in lines) + 16
    height = 2 * math.ceil(metrics.height()) + 10
    for index, rect in enumerate(layout['axes']):
        point = plot_curve_point(layout, period, flow, index)
        painter.setPen(QPen(QColor('#DA741D'), 1.3, Qt.DashLine))
        painter.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))
        painter.drawLine(QPointF(rect.left(), point.y()), QPointF(rect.right(), point.y()))
        painter.setPen(QPen(QColor('#BF6014'), 2))
        painter.setBrush(QColor('#FFAE51'))
        painter.drawEllipse(point, 5, 5)
        # Keep the entire label inside the data frame even on narrow panels.
        box_width = min(float(width), max(1., rect.width() - 6))
        box_height = min(float(height), max(1., rect.height() - 6))
        left = min(rect.right() - box_width - 3,
                   max(rect.left() + 3, point.x() + 11))
        top = min(rect.bottom() - box_height - 3,
                  max(rect.top() + 3, point.y() - box_height - 9))
        box = QRectF(left, top, box_width, box_height)
        painter.setBrush(QColor('#FFFFFF'))
        painter.setPen(QPen(QColor('#C96A19'), 1))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QColor('#243746'))
        for row, text in enumerate(lines):
            painter.drawText(QRectF(left + 7, top + 4 + row * metrics.height(),
                                    max(1., box_width - 14), metrics.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, text)


def render(state, known, forward, inverse, width, height, font_family=None,
           quantity='Qci', unit='m³/s', *, fit_height=False, selected=None):
    layout = geometry(state, known, forward, inverse, width, height, font_family, fit_height=fit_height)
    width, height, line = layout['width'], layout['height'], layout['line']
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(layout['font'])
    metrics = QFontMetricsF(layout['font'])

    def point(period, flow, index):
        rect = layout['axes'][index]
        t = math.log(period / layout['tmin']) / math.log(layout['tmax'] / layout['tmin'])
        q = flow / layout['qmax']
        return QPointF(rect.left() + (t if index == 0 else q) * rect.width(),
                       rect.bottom() - (q if index == 0 else t) * rect.height())

    for index, (panel, rect, tick_sets) in enumerate(zip(layout['panels'], layout['axes'], layout['ticks'])):
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor('#233F52'))
        painter.drawText(QRectF(rect.left(), panel.top() + 2, rect.width(), line + 2),
                         Qt.AlignCenter, f'{quantity} vs. T' if index == 0 else f'T vs. {quantity}')
        for value in tick_sets[1]:
            position = point(layout['tmin'], value, 0).y() if index == 0 else point(value, 0, 1).y()
            painter.setPen(QPen(QColor('#DDE5EA'), 1))
            painter.drawLine(QPointF(rect.left(), position), QPointF(rect.right(), position))
            painter.setPen(QColor('#4B5B65'))
            painter.drawText(QRectF(panel.left() + line + 8, position - line / 2,
                                    rect.left() - panel.left() - line - 13, line),
                             Qt.AlignRight | Qt.AlignVCenter, str(value))
        for value in tick_sets[0]:
            position = point(value, 0, 0).x() if index == 0 else point(layout['tmin'], value, 1).x()
            painter.setPen(QPen(QColor('#DDE5EA'), 1))
            painter.drawLine(QPointF(position, rect.top()), QPointF(position, rect.bottom()))
            painter.setPen(QColor('#4B5B65'))
            text_width = metrics.horizontalAdvance(str(value)) + 4
            painter.drawText(QRectF(position - text_width / 2, rect.bottom() + 3, text_width, line),
                             Qt.AlignCenter, str(value))
        painter.setPen(QPen(QColor('#627B8A'), 1.2))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor('#147EB5'), 2.2))
        if len(known) > 1:
            painter.drawPolyline(QPolygonF([point(period, flow, index) for period, flow in known]))
        painter.setBrush(QColor('#147EB5'))
        for period, flow in known:
            painter.drawEllipse(point(period, flow, index), 4, 4)
        painter.setPen(QColor('#4B5B65'))
        if len(known) < 2:
            painter.drawText(rect.adjusted(8, 8, -8, -8), Qt.AlignCenter | Qt.TextWordWrap,
                             'No disponible: se necesitan al menos dos cuantiles.' if not known else
                             'Un cuantil disponible; no hay tramo para interpolar.')
        painter.drawText(QRectF(rect.left(), rect.bottom() + line + 7, rect.width(), line + 2),
                         Qt.AlignCenter, 'T [años] · log' if index == 0 else f'{quantity} [{unit}]')
        painter.save()
        painter.translate(panel.left() + line / 2 + 3, rect.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-rect.height() / 2, -line / 2, rect.height(), line), Qt.AlignCenter,
                         f'{quantity} [{unit}]' if index == 0 else 'T [años] · log')
        painter.restore()
    painter.setPen(QPen(QColor('#D46E10'), 2.4))
    painter.setBrush(QColor('#F29B38'))
    if forward['flow'] is not None:
        painter.drawEllipse(point(state['input_period'], forward['flow'], 0), 6, 6)
    for solution in inverse['solutions']:
        if layout['tmin'] <= solution['period'] <= layout['tmax']:
            painter.drawEllipse(point(solution['period'], state['input_flow'], 1), 6, 6)
    for interval in inverse['intervals']:
        low, high = max(layout['tmin'], interval['min_period']), min(layout['tmax'], interval['max_period'] or layout['tmax'])
        if low <= high:
            painter.drawLine(point(low, state['input_flow'], 1), point(high, state['input_flow'], 1))
    _draw_linked_selection(painter, layout, selected, quantity, unit)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(buffer.data())
