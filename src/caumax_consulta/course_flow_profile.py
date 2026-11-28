"""Interactive SNCZI Qci along the existing D8 course, measured from outlet.

All Qci values come from the original CAUMAX/SNCZI rasters. The native D8
course supplies the sample centres and the cumulative chainage only. Raster
readout is cancellable and off the Qt GUI thread; missing cells remain missing.
"""
import base64
import hashlib
import struct
import math
from bisect import bisect_left
from pathlib import Path

from osgeo import gdal, ogr, osr
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsPointXY, QgsProject, QgsTask,
                       QgsWkbTypes)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsVertexMarker

from .flow_quantiles import PERIODS, estimate_flow


MAX_PROFILE_CELLS = 100_000


def _integer_tick_step(span, divisions=10):
    """Smallest readable integer interval for a positive axis span."""
    value = max(0., float(span))
    if not math.isfinite(value) or value <= 0.:
        return 1
    estimate = max(1., value / max(1, divisions))
    magnitude = 10 ** math.floor(math.log10(estimate))
    for multiplier in (1, 2, 5, 10):
        candidate = max(1, int(multiplier * magnitude))
        if candidate >= estimate - 1e-10:
            return candidate
    return max(1, int(10 * magnitude))


def _decimal_tick_step(span, divisions=6):
    """Readable 1/2/2.5/5/10-step ticks for specific discharge units."""
    value = max(0., float(span))
    if not math.isfinite(value) or value <= 0.:
        return 0.01
    target = value / max(1, divisions)
    magnitude = 10 ** math.floor(math.log10(target))
    for factor in (1, 2, 2.5, 5, 10):
        candidate = factor * magnitude
        if candidate >= target - 1e-12:
            return candidate
    return 10 * magnitude


def main_course_areas(analysis):
    """Decode exact contributing areas saved by the native D8 traversal."""
    packed = (getattr(analysis, 'diagnostics', {}) or {}).get('main_course_areas_b64')
    if not packed:
        return []
    try:
        raw = base64.b64decode(packed, validate=True)
        if not raw or len(raw) % 8:
            return []
        values = [value[0] for value in struct.iter_unpack('<d', raw)]
        if any(not math.isfinite(value) or value <= 0 for value in values):
            return []
        return values
    except (ValueError, TypeError, struct.error):
        return []


def profile_counts(flows):
    """Count cells with valid Q, missing Q and genuine Q=0 separately."""
    total = len(flows)
    missing = sum(value is None for value in flows)
    zeros = sum(value == 0 for value in flows if value is not None)
    return dict(total=total, valid=total - missing, missing=missing, zeros=zeros)


def profile_segments(flows):
    """Consecutive data and NoData runs; zero is a valid observation."""
    if not flows:
        return []
    segments, first, available = [], 0, flows[0] is not None
    for i, value in enumerate(flows[1:], 1):
        next_available = value is not None
        if next_available != available:
            segments.append((available, first, i - 1))
            first, available = i, next_available
    segments.append((available, first, len(flows) - 1))
    return segments


def _reference(wkt):
    srs = osr.SpatialReference()
    if not wkt or srs.ImportFromWkt(str(wkt)) != 0:
        raise ValueError('Falta el sistema de referencia del perfil SNCZI.')
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def course_centres(wkb, crs_wkt):
    """Actual D8 centres with cumulative distance in km, outlet at zero."""
    _reference(crs_wkt)
    geometry = ogr.CreateGeometryFromWkb(bytes(wkb))
    if (geometry is None or
            ogr.GT_Flatten(geometry.GetGeometryType()) != ogr.wkbLineString):
        raise ValueError('El curso principal no es una línea D8 válida.')
    count = geometry.GetPointCount()
    if count > MAX_PROFILE_CELLS:
        raise ValueError('Demasiadas celdas del curso para un perfil interactivo.')
    rows = []
    previous = None
    station = 0.0
    for i in range(count):
        x, y, _ = geometry.GetPoint(i)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError('El curso principal contiene coordenadas no finitas.')
        if previous is not None:
            segment = math.hypot(x - previous[0], y - previous[1])
            if not math.isfinite(segment) or segment <= 0:
                raise ValueError('El curso principal contiene tramos de longitud nula.')
            station += segment / 1000.
        rows.append((station, float(x), float(y)))
        previous = (x, y)
    return rows


def sample_course_quantiles(wkb, crs_wkt, paths, cancelled=lambda: False):
    """Sample each original official Qci raster at every course-cell centre.

    One per-raster cache prevents repeated GDAL I/O while 25-m D8 cells cross
    the same 500-m SNCZI flow cell. Source rasters are never changed.
    """
    centres = course_centres(wkb, crs_wkt)
    if len(paths) != len(PERIODS):
        raise ValueError('Se requieren seis rutas de caudales SNCZI.')
    source = _reference(crs_wkt)
    samplers = []
    for path in paths:
        raw = str(path or '').split('|', 1)[0]
        ds = gdal.Open(raw, gdal.GA_ReadOnly) if raw else None
        if ds is None or ds.RasterCount != 1:
            samplers.append(None)
            continue
        srs = _reference(ds.GetProjectionRef())
        transformation = (None if source.IsSame(srs)
                          else osr.CoordinateTransformation(source, srs))
        inverse = gdal.InvGeoTransform(ds.GetGeoTransform())
        band = ds.GetRasterBand(1)
        scale = band.GetScale()
        offset = band.GetOffset()
        scale = 1. if scale is None else float(scale)
        offset = 0. if offset is None else float(offset)
        if inverse is None or not math.isfinite(scale) or not math.isfinite(offset):
            samplers.append(None)
            continue
        samplers.append((ds, band, band.GetNoDataValue(), transformation,
                         inverse, scale, offset, {}))
    samples = []
    for i, (distance, x, y) in enumerate(centres):
        if i % 256 == 0 and cancelled():
            raise InterruptedError('Perfil SNCZI cancelado.')
        flows = []
        for sampler in samplers:
            if sampler is None:
                flows.append(None)
                continue
            ds, band, nodata, transform, inv, scale, offset, cache = sampler
            try:
                px, py = x, y
                if transform is not None:
                    px, py, _ = transform.TransformPoint(x, y)
                fx, fy = gdal.ApplyGeoTransform(inv, px, py)
                col, row = math.floor(fx), math.floor(fy)
                if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
                    flows.append(None)
                    continue
                key = (col, row)
                if key not in cache:
                    raw = float(band.ReadAsArray(col, row, 1, 1)[0, 0])
                    valid = (math.isfinite(raw)
                             and (nodata is None or raw != nodata)
                             and band.GetMaskBand().ReadAsArray(col, row, 1, 1)[0, 0] != 0)
                    value = raw * scale + offset if valid else None
                    cache[key] = (value if value is not None and
                                  math.isfinite(value) and value >= 0 else None)
                    if len(cache) > 20_000:
                        cache.clear()
                        cache[key] = value if value is not None and math.isfinite(value) and value >= 0 else None
                flows.append(cache[key])
            except (RuntimeError, ValueError, TypeError, OverflowError):
                flows.append(None)
        samples.append((distance, x, y, tuple(flows)))
    if cancelled():
        raise InterruptedError('Perfil SNCZI cancelado.')
    return samples


class _ProfileTask(QgsTask):
    def __init__(self, generation, wkb, crs, paths, callback):
        super().__init__('HidroCAUMAX: perfil de caudales SNCZI', QgsTask.CanCancel)
        self.generation, self.wkb, self.crs, self.paths = generation, wkb, crs, paths
        self.callback = callback
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = sample_course_quantiles(
                self.wkb, self.crs, self.paths, self.isCanceled)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)


class _ProfilePlot(QWidget):
    chosen = pyqtSignal(int)

    def __init__(self, parent, *, specific=False):
        super().__init__(parent)
        self.specific = bool(specific)
        self.rows = []
        self.flows = []
        self.selected = None
        self.chart = QRectF()
        self._ymin = 0.
        self._xstep = self._ystep = 1
        self.setMinimumHeight(260)

    @staticmethod
    def number(value):
        return f'{value:.2f}'.replace('.', ',')

    def show_profile(self, rows, flows, selected):
        self.rows, self.flows, self.selected = list(rows), list(flows), selected
        self.update()

    def frame(self):
        self.chart = QRectF(76, 26, max(50, self.width() - 98),
                            max(80, self.height() - 90))
        river_length = max(0., float(self.rows[-1][0])) if self.rows else 0.
        self._xstep = _integer_tick_step(river_length, divisions=11)
        length = max(self._xstep, math.ceil(river_length / self._xstep) * self._xstep)
        known = [float(q) for q in self.flows if q is not None and math.isfinite(float(q))]
        if not known:
            self._ymin, self._ystep = 0., 1
            return float(length), 1.
        low, high = min(known), max(known)
        if low == high:
            margin = (max(0.001, abs(low) * 0.05) if self.specific
                      else max(1., abs(low) * 0.05))
            low = max(0., low - margin)
            high += margin
        self._ystep = (_decimal_tick_step(high - low) if self.specific
                       else _integer_tick_step(high - low, divisions=6))
        self._ymin = max(0., math.floor(low / self._ystep) * self._ystep)
        ymax = math.ceil(high / self._ystep) * self._ystep
        if ymax <= self._ymin:
            ymax = self._ymin + self._ystep
        if ymax - self._ymin < 4 * self._ystep:
            ymax = self._ymin + 4 * self._ystep
        return float(length), float(ymax)

    def xy(self, i, length, ymax):
        rect = self.chart
        point_x = rect.left() + self.rows[i][0] / length * rect.width()
        q = self.flows[i]
        if q is None:
            return QPointF(point_x, rect.bottom())
        return QPointF(point_x,
                       rect.bottom() - (q - self._ymin) / (ymax - self._ymin) * rect.height())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.rows:
            return super().mousePressEvent(event)
        length, _ = self.frame()
        if not self.chart.contains(event.pos()):
            return super().mousePressEvent(event)
        distance = (event.x() - self.chart.left()) * length / self.chart.width()
        stations = [row[0] for row in self.rows]
        i = min(len(stations) - 1, bisect_left(stations, distance))
        if i and abs(stations[i-1] - distance) < abs(stations[i] - distance):
            i -= 1
        self.chosen.emit(i)
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.white)
        length, ymax = self.frame()
        chart = self.chart
        # Shaded bands indicate missing official Qci coverage, not Q=0.
        for available, first, last in profile_segments(self.flows):
            if available:
                continue
            start = (0. if first == 0 else
                     (self.rows[first - 1][0] + self.rows[first][0]) / 2)
            stop = (length if last == len(self.rows) - 1 else
                    (self.rows[last][0] + self.rows[last + 1][0]) / 2)
            x0 = chart.left() + start / length * chart.width()
            x1 = chart.left() + stop / length * chart.width()
            painter.fillRect(QRectF(x0, chart.top(), max(0., x1 - x0), chart.height()),
                             QColor(224, 231, 236, 115))
        tick_count = min(20, 1 + round((ymax - self._ymin) / self._ystep))
        for index in range(tick_count):
            tick = self._ymin + index * self._ystep
            y = chart.bottom() - (tick - self._ymin) / (ymax - self._ymin) * chart.height()
            painter.setPen(QPen(QColor('#DFE6EF'), 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor('#25384A'))
            if self.specific:
                precision = max(0, min(6, -math.floor(math.log10(self._ystep)) + 1))
                tick_label = f'{tick:.{precision}f}'.replace('.', ',')
            else:
                tick_label = str(round(tick))
            painter.drawText(QRectF(0, y - 10, chart.left() - 8, 20),
                             Qt.AlignRight | Qt.AlignVCenter, tick_label)
        for tick in range(0, int(length) + 1, self._xstep):
            x = chart.left() + tick / length * chart.width()
            painter.setPen(QPen(QColor('#DFE6EF'), 1))
            painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
            painter.setPen(QColor('#25384A'))
            painter.drawText(QRectF(x - 25, chart.bottom() + 4, 50, 19),
                             Qt.AlignCenter, str(tick))
        painter.setPen(QPen(QColor('#526C81'), 1))
        painter.drawRect(chart)
        painter.drawText(QRectF(chart.left(), self.height()-28, chart.width(), 23),
                         Qt.AlignCenter, 'Abscisado desde el cierre (km)')
        painter.save()
        painter.translate(17, chart.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-chart.height()/2, -13, chart.height(), 25),
                         Qt.AlignCenter, ('Aportación específica (m³/s·km²)' if self.specific
                                          else 'Caudal máximo (m³/s)'))
        painter.restore()
        # Join only consecutive cells with valid official Q; no interpolation.
        painter.setPen(QPen(QColor('#0080BB'), 2))
        for available, first, last in profile_segments(self.flows):
            if not available:
                continue
            line = QPainterPath()
            line.moveTo(self.xy(first, length, ymax))
            for i in range(first + 1, last + 1):
                line.lineTo(self.xy(i, length, ymax))
            painter.drawPath(line)
        # Blue markers: Q>0. Green markers: valid Q=0. NoData has no point.
        visible_points = set()
        for i, q in enumerate(self.flows):
            if q is None:
                continue
            p = self.xy(i, length, ymax)
            zero = q == 0
            key = (round(p.x()), round(p.y()), zero)
            if key in visible_points:
                continue
            visible_points.add(key)
            painter.setBrush(QColor('#168A69') if zero else QColor('#0080BB'))
            painter.setPen(QPen(QColor('#087353') if zero else QColor('#006D9C'), 1))
            painter.drawEllipse(p, 3.0 if zero else 2.0, 3.0 if zero else 2.0)
        if not any(q is not None for q in self.flows):
            painter.setPen(QColor('#65788A'))
            painter.drawText(chart, Qt.AlignCenter | Qt.TextWordWrap,
                             'Sin datos SNCZI para representar este curso.' if self.rows
                             else 'Preparando el perfil del curso principal…')
        if self.selected is not None and 0 <= self.selected < len(self.rows):
            i = self.selected
            p = self.xy(i, length, ymax)
            painter.setPen(QPen(QColor('#E47921'), 2, Qt.DashLine))
            painter.drawLine(QPointF(p.x(), chart.top()), QPointF(p.x(), chart.bottom()))
            if self.flows[i] is not None:
                painter.setBrush(QColor('#F9A139'))
                painter.setPen(QPen(QColor('#BD5B13'), 2))
                painter.drawEllipse(p, 5, 5)
            msg = 'Abscisado: ' + self.number(self.rows[i][0]) + ' km'
            q = self.flows[i]
            if self.specific:
                msg += ('\nAportación: sin dato' if q is None else
                        '\nAportación: ' + f'{q:.4f}'.replace('.', ',') + ' m³/s·km²')
            else:
                msg += ('\nQ: sin dato SNCZI (NoData)' if q is None else
                        '\nQ: ' + self.number(q) + ' m³/s' +
                        (' (dato válido)' if q == 0 else ''))
            metrics = painter.fontMetrics()
            lines = msg.split('\n')
            w = max(metrics.horizontalAdvance(line) for line in lines) + 14
            h = metrics.lineSpacing() * 2 + 10
            px = min(chart.right() - w - 3, max(chart.left() + 3, p.x() + 12))
            py = max(chart.top() + 3, min(chart.bottom() - h - 3, p.y() - h - 7))
            box = QRectF(px, py, w, h)
            painter.setPen(QPen(QColor('#CE6D1C'), 1))
            painter.setBrush(Qt.white)
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor('#25384A'))
            for row, line in enumerate(lines):
                painter.drawText(QRectF(px+7, py+3+row*metrics.lineSpacing(),
                                        w-14, metrics.lineSpacing()),
                                 Qt.AlignLeft | Qt.AlignVCenter, line)
        painter.end()


class CourseFlowProfile(QWidget):
    """Linked profile/map selection; the native CAUMAX query remains unchanged."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._iface = None
        self._canvas = None
        self._key = None
        self._task = None
        self._generation = 0
        self._rows, self._flows = [], []
        self._areas, self._specific_values = [], []
        self._selected = None
        self._crs = ''
        self._cell_width = self._cell_height = 0.
        self._period, self._extrapolate = 100., True
        self._map_tool = self._previous_tool = None
        self._marker = self._outline = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        title = QLabel('Caudal Máximo (Qci-n) a lo largo del curso principal', self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.graph = _ProfilePlot(self)
        self.graph.chosen.connect(self.select_index)
        layout.addWidget(self.graph)
        self.specific_title = QLabel('Aportación Específica (m³/s·km²)', self)
        specific_font = self.specific_title.font()
        specific_font.setBold(True)
        self.specific_title.setFont(specific_font)
        layout.addWidget(self.specific_title)
        self.specific_graph = _ProfilePlot(self, specific=True)
        self.specific_graph.chosen.connect(self.select_index)
        layout.addWidget(self.specific_graph)
        self.specific_title.hide()
        self.specific_graph.hide()
        self.coverage_label = QLabel('', self)
        self.coverage_label.setWordWrap(True)
        self.coverage_label.setToolTip(
            'Cada posición corresponde a una celda D8. Solo se representan '
            'los caudales válidos de los rásteres oficiales SNCZI.')
        layout.addWidget(self.coverage_label)
        row = QHBoxLayout()
        self.map_button = QPushButton('Elegir celda en el mapa', self)
        # 95% of the native button height, preserving font and interactions.
        self.map_button.ensurePolished()
        self.map_button.setFixedHeight(max(
            self.map_button.fontMetrics().height() + 4,
            round(self.map_button.sizeHint().height() * .95)))
        self.map_button.setCheckable(True)
        self.map_button.toggled.connect(self._toggle_map_pick)
        self.map_button.setToolTip(
            'Activa la selección de una celda del curso principal en el mapa QGIS.')
        self.label = QLabel('Selecciona un punto de la gráfica o del mapa.', self)
        self.label.setWordWrap(True)
        # Map selection button intentionally absent: the longitudinal plots
        # remain clickable, and the abscissa/Q value stays permanently visible.
        self.map_button.hide()
        row.addWidget(self.label, 1)
        layout.addLayout(row)
        self.hide()

    def _stop_task(self):
        self._generation += 1
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    def clear(self):
        self._stop_task()
        self._stop_map_pick()
        self._remove_marker()
        self._key = None
        self._rows, self._flows = [], []
        self._areas, self._specific_values = [], []
        self._selected = None
        self.graph.show_profile([], [], None)
        self.specific_graph.show_profile([], [], None)
        self.specific_graph.hide()
        self.specific_title.hide()
        self.coverage_label.clear()
        self.hide()

    def configure(self, analysis, state, iface):
        if analysis is None or not state or iface is None:
            self.clear()
            return
        wkb = bytes(analysis.main_course_wkb or b'')
        paths = tuple(str(path or '') for path in state.get('source_paths', ()))
        if not wkb or len(paths) != 6:
            self.clear()
            return
        crs = str(analysis.crs_wkt)
        areas = main_course_areas(analysis)
        key = hashlib.sha256(
            wkb + crs.encode() + repr(paths).encode()
            + repr((len(areas), areas[0] if areas else None)).encode()).hexdigest()
        self._iface, self._canvas = iface, iface.mapCanvas()
        self._crs = crs
        self._cell_width = float(analysis.cell_width)
        self._cell_height = float(analysis.cell_height)
        self._areas = areas
        self.show()
        self.set_period(state['input_period'], state['extrapolate'])
        if key == self._key:
            return
        self._stop_task()
        self._stop_map_pick()
        self._remove_marker()
        self._selected = None
        self._rows, self._flows = [], []
        self._specific_values = []
        self._key = key
        self.graph.show_profile([], [], None)
        self.specific_graph.hide()
        self.specific_title.hide()
        self.coverage_label.setText('Recuento de celdas: pendiente de lectura SNCZI.')
        if not any((Path(path.split('|', 1)[0]).is_file() or
                    path.startswith('/vsi')) for path in paths if path):
            # Portable saved consultations may lack the native source rasters.
            # Do not start a pointless background task or fabricate Q from
            # morphometry; allow a later source restore to retry this profile.
            self._key = None
            self.coverage_label.setText('Recuento no disponible: faltan los rásteres oficiales.')
            self.label.setText(
                'Rásteres de caudal SNCZI no disponibles; no es posible '
                'construir el perfil del curso principal.')
            return
        self.label.setText('Leyendo los caudales SNCZI a lo largo del curso…')
        task = _ProfileTask(self._generation, wkb, crs, paths, self._finished)
        self._task = task
        QgsApplication.taskManager().addTask(task)

    def _finished(self, task, success):
        if task is not self._task or task.generation != self._generation:
            return
        self._task = None
        if not success:
            self.label.setText(task.error or 'No se ha podido generar el perfil SNCZI.')
            self.coverage_label.setText('Recuento no disponible: error de lectura.')
            return
        self._rows = task.result
        self._refresh()

    def set_period(self, period, extrapolate):
        try:
            period = float(period)
        except (ValueError, TypeError):
            return
        if not math.isfinite(period) or period <= 0:
            return
        self._period, self._extrapolate = period, bool(extrapolate)
        if self._rows:
            self._refresh()

    def _refresh(self):
        self._flows = [estimate_flow(self._period, item[3], self._extrapolate)['flow']
                       for item in self._rows]
        self.graph.show_profile(self._rows, self._flows, self._selected)
        if len(self._areas) == len(self._rows) and self._rows:
            self._specific_values = [
                None if q is None or area <= 0 else q / area
                for q, area in zip(self._flows, self._areas)
            ]
            self.specific_graph.show_profile(
                self._rows, self._specific_values, self._selected)
            self.specific_title.show()
            self.specific_graph.show()
        else:
            self._specific_values = []
            self.specific_graph.hide()
            self.specific_title.hide()
        self._update_coverage()
        self._selection_text()
        self._update_marker()

    def _update_coverage(self):
        counts = profile_counts(self._flows)
        self.coverage_label.setText(
            f'T={self._period:g} años · Celdas del curso: {counts["total"]} · '
            f'Con caudal válido: {counts["valid"]} '
            f'(Q=0 en {counts["zeros"]}) · '
            f'Sin dato SNCZI: {counts["missing"]}. '
            'Azul: Q>0 · Verde: Q=0 válido · Gris: NoData (sin interpolar).')

    def snapshot(self):
        """Portable original Qci samples for every D8 course centre and return period."""
        if not self._rows or self._task is not None:
            return None
        return {
            'version': 1,
            'selected': self._selected,
            'rows': [
                [float(distance), float(x), float(y),
                 [None if q is None else float(q) for q in values]]
                for distance, x, y, values in self._rows
            ],
        }

    def restore_snapshot(self, snapshot):
        """Reopen the complete profile without needing the original Qci TIFFs."""
        self._stop_task()
        self._rows = [
            (float(row[0]), float(row[1]), float(row[2]), tuple(row[3]))
            for row in snapshot['rows']
        ]
        self._selected = snapshot.get('selected')
        self.show()
        self._refresh()

    def _selection_text(self):
        if self._selected is not None and 0 <= self._selected < len(self._rows):
            station = self._rows[self._selected][0]
            flow = self._flows[self._selected]
            specific = (self._specific_values[self._selected]
                        if self._specific_values else None)
            text = (f'Abscisado desde el cierre: {station:.2f} km · '
                    + ('Q: sin dato SNCZI (NoData)' if flow is None else
                       f'Q máx. T={self._period:g}: {flow:.2f} m³/s'
                       + (' (dato válido)' if flow == 0 else '')))
            if self._specific_values:
                text += (' · Aportación específica: sin dato' if specific is None else
                         f' · Aportación específica: {specific:.4f} m³/s·km²')
            self.label.setText(text)
        elif self._rows:
            good = sum(q is not None for q in self._flows)
            self.label.setText(
                f'Perfil SNCZI · T={self._period:g} años · '
                f'{good} de {len(self._rows)} celdas con caudal disponible.')

    def select_index(self, index):
        if not 0 <= index < len(self._rows):
            return
        self._selected = int(index)
        self.graph.show_profile(self._rows, self._flows, self._selected)
        if self._specific_values:
            self.specific_graph.show_profile(
                self._rows, self._specific_values, self._selected)
        self._selection_text()
        self._update_marker()

    def _toggle_map_pick(self, active):
        if not active:
            self._stop_map_pick()
            return
        if self._canvas is None or not self._rows:
            self.map_button.blockSignals(True)
            self.map_button.setChecked(False)
            self.map_button.blockSignals(False)
            return
        self._previous_tool = self._canvas.mapTool()
        self._map_tool = QgsMapToolEmitPoint(self._canvas)
        self._map_tool.canvasClicked.connect(self._map_clicked)
        self._canvas.mapToolSet.connect(self._tool_changed)
        self._canvas.setMapTool(self._map_tool)

    def _tool_changed(self, current, previous):
        if previous is self._map_tool and current is not self._map_tool:
            self._stop_map_pick(restore=False)

    def _stop_map_pick(self, restore=True):
        tool, self._map_tool = self._map_tool, None
        if self._canvas is not None:
            try:
                self._canvas.mapToolSet.disconnect(self._tool_changed)
            except (TypeError, RuntimeError):
                pass
        if tool is not None:
            try:
                tool.canvasClicked.disconnect(self._map_clicked)
            except (TypeError, RuntimeError):
                pass
            if (restore and self._canvas is not None and self._canvas.mapTool() is tool):
                if self._previous_tool is not None and not sip.isdeleted(self._previous_tool):
                    self._canvas.setMapTool(self._previous_tool)
                else:
                    self._canvas.unsetMapTool(tool)
            tool.deleteLater()
        self._previous_tool = None
        self.map_button.blockSignals(True)
        self.map_button.setChecked(False)
        self.map_button.blockSignals(False)

    def _map_clicked(self, point, button):
        if button != Qt.LeftButton or not self._rows:
            return
        try:
            operation = QgsCoordinateTransform(
                self._canvas.mapSettings().destinationCrs(),
                QgsCoordinateReferenceSystem.fromWkt(self._crs), QgsProject.instance())
            native = operation.transform(point)
            best = min(range(len(self._rows)), key=lambda i:
                       (self._rows[i][1] - native.x())**2 +
                       (self._rows[i][2] - native.y())**2)
            limit = 1.6 * math.hypot(self._cell_width, self._cell_height)
            if math.hypot(self._rows[best][1] - native.x(),
                          self._rows[best][2] - native.y()) <= limit:
                self.select_index(best)
            else:
                self.label.setText('Elige una celda sobre el curso principal D8.')
        except (ValueError, RuntimeError) as error:
            self.label.setText('No se puede seleccionar esa celda: ' + str(error))

    def _remove_marker(self):
        from .delineation_cleanup import remove_overlay
        marker, outline = self._marker, self._outline
        self._marker = self._outline = None
        remove_overlay(marker)
        remove_overlay(outline)

    def _update_marker(self):
        self._remove_marker()
        if self._canvas is None or self._selected is None or not self._rows:
            return
        try:
            from .delineation_cleanup import register_overlay
            row = self._rows[self._selected]
            station, x, y = row[:3]
            operation = QgsCoordinateTransform(
                QgsCoordinateReferenceSystem.fromWkt(self._crs),
                self._canvas.mapSettings().destinationCrs(), QgsProject.instance())
            point = operation.transform(QgsPointXY(x, y))
            marker = register_overlay(self._canvas, QgsVertexMarker(self._canvas))
            marker.setCenter(point)
            marker.setColor(QColor('#DE6918'))
            marker.setIconType(QgsVertexMarker.ICON_CROSS)
            marker.setIconSize(17)
            marker.setPenWidth(3)
            q = self._flows[self._selected]
            marker.setToolTip(
                'Abscisado desde el cierre: ' + f'{station:.2f} km · Q: '
                + ('sin dato SNCZI (NoData)' if q is None else
                   f'{q:.2f} m³/s' + (' (dato válido)' if q == 0 else '')))
            self._marker = marker
            outline = register_overlay(
                self._canvas, QgsRubberBand(self._canvas, QgsWkbTypes.PolygonGeometry))
            outline.setColor(QColor('#D55E0F'))
            outline.setFillColor(QColor(232, 116, 22, 38))
            outline.setWidth(2)
            vertices = [(x-self._cell_width/2, y-self._cell_height/2),
                        (x+self._cell_width/2, y-self._cell_height/2),
                        (x+self._cell_width/2, y+self._cell_height/2),
                        (x-self._cell_width/2, y+self._cell_height/2)]
            for cx, cy in vertices:
                outline.addPoint(operation.transform(QgsPointXY(cx, cy)), False)
            outline.addPoint(operation.transform(QgsPointXY(*vertices[0])), True)
            self._outline = outline
        except (RuntimeError, ValueError, TypeError):
            self._remove_marker()

    def dispose(self):
        self.clear()
        self._iface = self._canvas = None
