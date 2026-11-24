"""CAUMAX quantiles at the original query point and transparent interpolation.

No flow is derived from a replacement MDT. Six source rasters are sampled once;
the portable snapshot supplies both the screen plots and the PDF offline.
"""
import copy
import importlib
import math
from pathlib import Path

from osgeo import gdal, osr
from qgis.PyQt.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, pyqtSignal, QEvent, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy, QStyle, QStyleOptionHeader,
)


PERIODS = (2, 5, 10, 25, 100, 500)
METHOD = 'piecewise_linear_q_log_t'


class CompactTableHeader(QHeaderView):
    """Keep the requested column labels readable when the dialog gets narrow."""
    def __init__(self, table):
        super().__init__(Qt.Horizontal, table)
        self._table = table
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_height)
        self.sectionResized.connect(lambda *_: self._fit_timer.start(0))
        self._fit_timer.start(0)

    def _fit_height(self):
        metrics = self.fontMetrics()
        height = super().sizeHint().height()
        for section in range(self.count()):
            text = str(self.model().headerData(section, Qt.Horizontal, Qt.DisplayRole) or '')
            bounds = metrics.boundingRect(0, 0, max(1, self.sectionSize(section)-8),
                                          1000, Qt.AlignCenter | Qt.TextWordWrap, text)
            height = max(height, bounds.height()+8)
        if self.height() != height:
            self.setFixedHeight(height)
        table_height = height + sum(self._table.rowHeight(row)
                                    for row in range(self._table.rowCount()))
        self._table.setFixedHeight(table_height + 2*self._table.frameWidth())
        self.viewport().update()

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logical_index
        option.text = ''
        painter.save()
        self.style().drawControl(QStyle.CE_Header, option, painter, self)
        painter.restore()
        painter.save()
        text = str(self.model().headerData(logical_index, Qt.Horizontal, Qt.DisplayRole) or '')
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect.adjusted(4, 4, -4, -4), Qt.AlignCenter | Qt.TextWordWrap, text)
        painter.restore()


def _finite(value, minimum=None, strict=False):
    if isinstance(value, bool):
        raise ValueError('Se esperaba un número.')
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and
            (number <= minimum if strict else number < minimum)):
        raise ValueError('Valor fuera del intervalo admisible.')
    return number


def _normalise(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1:
        raise ValueError('Versión de cuantiles no compatible.')
    if list(snapshot.get('periods', ())) != list(PERIODS) or snapshot.get('method') != METHOD:
        raise ValueError('Los periodos o el método no son los de CAUMAX.')
    flows = snapshot.get('flows', ())
    paths = snapshot.get('source_paths', ())
    if len(flows) != 6 or len(paths) != 6 or not all(isinstance(p, str) for p in paths):
        raise ValueError('Se requieren seis cuantiles y seis rutas de origen.')
    point = snapshot.get('point', {})
    if not isinstance(point.get('crs_wkt'), str) or type(snapshot.get('extrapolate')) is not bool:
        raise ValueError('Punto o extrapolación no válidos.')
    return dict(version=1, periods=list(PERIODS),
        flows=[None if value is None else _finite(value, 0) for value in flows],
        source_paths=list(paths),
        point=dict(x=_finite(point['x']), y=_finite(point['y']), crs_wkt=point['crs_wkt']),
        input_period=_finite(snapshot['input_period'], 1, strict=True),
        input_flow=_finite(snapshot['input_flow'], 0),
        extrapolate=snapshot['extrapolate'], method=METHOD)


def _known(flows):
    if len(flows) != 6:
        raise ValueError('Se requieren los seis periodos CAUMAX.')
    return [(float(period), _finite(flow, 0)) for period, flow in zip(PERIODS, flows)
            if flow is not None]


def estimate_flow(period, flows, extrapolate=False):
    """Linear Q interpolation in log(T); missing rasters never become zero."""
    period = _finite(period, 1, strict=True)
    known = _known(flows)
    for p, q in known:
        if period == p:
            return {'status':'exact', 'flow':q, 'extrapolated':False}
    if len(known) < 2:
        return {'status':'unavailable', 'flow':None, 'extrapolated':False}
    outside = period < known[0][0] or period > known[-1][0]
    if outside and not extrapolate:
        return {'status':'out_of_range', 'flow':None, 'extrapolated':False}
    pair = known[:2] if period < known[0][0] else known[-2:]
    if not outside:
        pair = next((a,b) for a,b in zip(known, known[1:]) if a[0] < period < b[0])
    (p1,q1),(p2,q2) = pair
    q = q1 + (q2-q1) * math.log(period/p1) / math.log(p2/p1)
    if q < 0 or not math.isfinite(q):
        return {'status':'out_of_range', 'flow':None, 'extrapolated':outside}
    return {'status':'extrapolated' if outside else 'interpolated',
            'flow':q, 'extrapolated':outside}


def estimate_period(flow, flows, extrapolate=False):
    """Return every inverse solution, including intervals for flat segments."""
    flow = _finite(flow, 0)
    known = _known(flows)
    solutions = []
    intervals = []
    tolerance = max(1.,abs(flow)) * 1e-12
    for p,q in known:
        if abs(flow-q) <= tolerance:
            solutions.append((p,False))
    for (p1,q1),(p2,q2) in zip(known, known[1:]):
        if q1 == q2:
            if abs(flow-q1) <= tolerance:
                intervals.append(dict(min_period=p1,max_period=p2,
                                      min_inclusive=True,max_inclusive=True,extrapolated=False))
            continue
        fraction = (flow-q1)/(q2-q1)
        if 0 <= fraction <= 1:
            solutions.append((math.exp(math.log(p1)+fraction*math.log(p2/p1)),False))
    if extrapolate and len(known) >= 2:
        for left,(p1,q1),(p2,q2) in ((True,*known[:2]),(False,*known[-2:])):
            if q1 == q2:
                if abs(flow-q1) <= tolerance:
                    intervals.append(dict(min_period=1. if left else p2,
                        max_period=p1 if left else None, min_inclusive=not left,
                        max_inclusive=True,extrapolated=True))
                continue
            logarithm = math.log(p1)+(flow-q1)/(q2-q1)*math.log(p2/p1)
            if 0 < logarithm < 709:
                p = math.exp(logarithm)
                if (left and 1 < p < p1) or (not left and p > p2):
                    solutions.append((p,True))
    intervals.sort(key=lambda value:value['min_period'])
    merged = []
    for interval in intervals:
        if merged and (merged[-1]['max_period'] is None or
                interval['min_period'] <= merged[-1]['max_period']*(1+1e-12)):
            previous = merged[-1]
            previous['max_period'] = (None if previous['max_period'] is None
                                     or interval['max_period'] is None
                                     else max(previous['max_period'],interval['max_period']))
            previous['extrapolated'] |= interval['extrapolated']
        else:
            merged.append(interval.copy())
    points = []
    for period,extra in sorted(solutions):
        if any(period >= interval['min_period']*(1-1e-12)
               and (interval['max_period'] is None or period <= interval['max_period']*(1+1e-12))
               for interval in merged):
            continue
        if not points or not math.isclose(period,points[-1]['period'],rel_tol=1e-10):
            points.append(dict(period=period,extrapolated=extra))
    count = len(points)+len(merged)
    return dict(status=('unavailable' if not count else 'interval' if merged
                        else 'multiple' if count>1 else 'unique'),
                solutions=points, intervals=merged,
                extrapolated=any(v['extrapolated'] for v in points+merged))


def _number(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.').replace('.',',')


def _years(value):
    return f'{value:.0f}'


def describe_estimates(snapshot):
    state = _normalise(snapshot)
    forward = estimate_flow(state['input_period'],state['flows'],state['extrapolate'])
    inverse = estimate_period(state['input_flow'],state['flows'],state['extrapolate'])
    if forward['flow'] is None:
        forward_text = 'Q(T): no disponible para este periodo con los cuantiles válidos.'
    else:
        kind = {'exact':'cuantil de la capa','interpolated':'interpolación',
                'extrapolated':'extrapolación'}[forward['status']]
        forward_text = f"Q({_years(state['input_period'])} años) = {_number(forward['flow'])} m³/s ({kind})."
    parts = [f"{_years(v['period'])} años" for v in inverse['solutions']]
    for value in inverse['intervals']:
        low,high = value['min_period'],value['max_period']
        if high is None:
            parts.append(f'desde {_years(low)} años sin límite superior (meseta)')
        else:
            parts.append(f"{'más de' if not value['min_inclusive'] else 'entre'} {_years(low)} y {_years(high)} años (meseta)")
    if not parts:
        inverse_text = 'T(Q): no disponible para este caudal con los cuantiles válidos.'
    else:
        prefix = 'T posibles' if inverse['status'] in ('multiple','interval') else 'T'
        inverse_text = f"{prefix} para Q = {_number(state['input_flow'])} m³/s: "+'; '.join(parts)+'.'
        if inverse['status'] in ('multiple','interval'):
            inverse_text += ' Solución no única.'
        if inverse['extrapolated']:
            inverse_text += ' Incluye extrapolación.'
    missing = sum(value is None for value in state['flows'])
    note = ('Interpolación lineal de Q frente a ln(T) entre los cuantiles disponibles; '
            'la inversa utiliza los mismos tramos. Datos del punto original consultado en CAUMAX. '
            + ('Extrapolación permitida.' if state['extrapolate'] else 'Sin extrapolación fuera de los cuantiles disponibles.'))
    if missing:
        note += f' {missing} capa(s) sin valor válido; no se sustituyen por cero.'
    return dict(forward_text=forward_text,inverse_text=inverse_text,method_note=note)


def _source_paths(query, paths=None):
    if paths is not None:
        if isinstance(paths, dict):
            return [str(paths.get(period,paths.get(str(period),paths.get('q'+str(period),
                        paths.get('FLOW_'+str(period),'')))) or '') for period in PERIODS]
        if len(paths) != 6:
            raise ValueError('Se requieren seis rutas de caudales.')
        return [str(path) if path is not None else '' for path in paths]
    result = ['']*6
    try:
        module = importlib.import_module('caumax.layers')
        for i,period in enumerate(PERIODS):
            result[i] = str(module.getLayerPath(getattr(module,'FLOW_'+str(period))))
    except (ImportError,AttributeError,KeyError):
        pass
    source = getattr(query,'flow_layer_path','')
    if source:
        parent = Path(str(source).split('|',1)[0]).parent
        result = [path or str(parent/('q'+str(period)+'.tif'))
                  for path,period in zip(result,PERIODS)]
    return result


def _query_wkt(query):
    crs = getattr(query,'crs',None)
    return crs.toWkt() if hasattr(crs,'toWkt') else str(getattr(query,'crs_wkt','') or crs or '')


def _sample(path,x,y,crs_wkt):
    if not path:
        return None
    try:
        dataset = gdal.Open(str(path).split('|',1)[0],gdal.GA_ReadOnly)
        if dataset is None or dataset.RasterCount < 1:
            return None
        source = osr.SpatialReference();source.ImportFromWkt(crs_wkt)
        target = osr.SpatialReference();target.ImportFromWkt(dataset.GetProjection())
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not source.IsSame(target):
            x,y,_ = osr.CoordinateTransformation(source,target).TransformPoint(x,y)
        px,py = gdal.ApplyGeoTransform(gdal.InvGeoTransform(dataset.GetGeoTransform()),x,y)
        col,row = math.floor(px),math.floor(py)
        if not (0<=col<dataset.RasterXSize and 0<=row<dataset.RasterYSize):
            return None
        band = dataset.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        raw = float(band.ReadAsArray(col,row,1,1)[0,0])
        if not math.isfinite(raw) or (nodata is not None and raw == nodata):
            return None
        if band.GetMaskBand().ReadAsArray(col,row,1,1)[0,0] == 0:
            return None
        # GDAL reads stored samples; scale/offset convert them to physical Q.
        # NoData and masks apply to the stored sample, before this conversion.
        scale, offset = band.GetScale(), band.GetOffset()
        scale = 1.0 if scale is None else float(scale)
        offset = 0.0 if offset is None else float(offset)
        if not math.isfinite(scale) or not math.isfinite(offset):
            return None
        value = raw * scale + offset
        return value if math.isfinite(value) and value >= 0 else None
    except (RuntimeError,TypeError,ValueError,OverflowError):
        return None


def sample_quantiles(query, paths=None):
    x,y = _finite(query.x),_finite(query.y)
    wkt = _query_wkt(query)
    sources = _source_paths(query,paths)
    return dict(version=1,periods=list(PERIODS),
        flows=[_sample(path,x,y,wkt) for path in sources],source_paths=sources,
        point=dict(x=x,y=y,crs_wkt=wkt),input_period=_finite(query.period,1,strict=True),
        input_flow=_finite(query.flow,0),extrapolate=True,method=METHOD)


def render_quantile_plots(snapshot,width=1100,height=500,font_family=None,*,fit_height=False):
    """Render the same responsive, integer-labelled Qci/T plots for screen and PDF."""
    from .quantile_graphics import render
    state=_normalise(snapshot)
    return render(state,_known(state['flows']),
                  estimate_flow(state['input_period'],state['flows'],state['extrapolate']),
                  estimate_period(state['input_flow'],state['flows'],state['extrapolate']),
                  width,height,font_family,fit_height=fit_height)


class _Plots(QWidget):
    def __init__(self,parent):
        super().__init__(parent);self.state=None;self.setMinimumHeight(324)
        policy=QSizePolicy(QSizePolicy.Expanding,QSizePolicy.Preferred)
        policy.setHeightForWidth(True);self.setSizePolicy(policy)
    def sizeHint(self):return QSize(640,324)
    def heightForWidth(self,width):
        base=680 if width<580 else 360
        if self.state is None:return round(base*.9)
        from .quantile_graphics import geometry
        state=self.state
        height=geometry(state,_known(state['flows']),
            estimate_flow(state['input_period'],state['flows'],state['extrapolate']),
            estimate_period(state['input_flow'],state['flows'],state['extrapolate']),width,base)['height']
        return round(height*.9)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        height=self.heightForWidth(self.width())
        if self.minimumHeight()!=height:
            self.setMinimumHeight(height);self.updateGeometry()
    def paintEvent(self,event):
        painter=QPainter(self)
        if self.state is not None:
            png=render_quantile_plots(self.state,max(320,self.width()),max(self.heightForWidth(self.width()),self.height()),fit_height=True)
            painter.drawImage(self.rect(),QImage.fromData(png))
        else:painter.fillRect(self.rect(),QColor('white'))
        painter.end()


class FlowQuantileWidget(QWidget):
    changed = pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self._state=None;self._updating=False
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.table=QTableWidget(6,2,self)
        self.table.setHorizontalHeader(CompactTableHeader(self.table))
        self.table.setHorizontalHeaderLabels(['Periodo de retorno T (años)','Caudal máximo instantáneo (Qci) [m3/s]'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        row_height=max(self.table.fontMetrics().height(),self.table.fontMetrics().lineSpacing())+2
        self.table.verticalHeader().setMinimumSectionSize(row_height)
        self.table.verticalHeader().setDefaultSectionSize(row_height)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setVisible(False);self.table.setAlternatingRowColors(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height()+6*row_height+2*self.table.frameWidth())
        self.table.setStyleSheet('QTableWidget { background-color:white; alternate-background-color:white; }')
        self._table_timer=QTimer(self);self._table_timer.setSingleShot(True)
        self._table_timer.timeout.connect(self._fit_table_columns)
        self.table.viewport().installEventFilter(self)
        layout.addWidget(self.table)
        controls=QGridLayout()
        self.period_spin=QDoubleSpinBox(self);self.period_spin.setDecimals(0)
        self.period_spin.setRange(2,1e9);self.period_spin.setValue(5)
        self.period_spin.setSuffix(' años')
        self.flow_spin=QDoubleSpinBox(self);self.flow_spin.setDecimals(2)
        self.flow_spin.setRange(0,1e12);self.flow_spin.setSuffix(' m³/s')
        control_width=max(110,self.flow_spin.fontMetrics().horizontalAdvance('9999,99 m³/s')+24)
        self.period_spin.setFixedWidth(control_width);self.flow_spin.setFixedWidth(control_width)
        self.forward_label=QLabel(self);self.inverse_label=QLabel(self)
        self.forward_label.setWordWrap(True);self.inverse_label.setWordWrap(True)
        controls.addWidget(QLabel('Consultar Q(T)',self),0,0);controls.addWidget(self.period_spin,0,1)
        controls.addWidget(self.forward_label,0,3)
        controls.addWidget(QLabel('Consultar T(Q)',self),1,0);controls.addWidget(self.flow_spin,1,1)
        controls.addWidget(self.inverse_label,1,3);controls.setColumnStretch(3,1)
        self.extrapolate_check=QCheckBox('Permitir\nextrapolación',self)
        self.extrapolate_check.setChecked(True)
        controls.addWidget(self.extrapolate_check,0,2,2,1,Qt.AlignVCenter)
        self.controls_layout=controls;layout.addLayout(controls)
        self.method_label=QLabel(self);self.method_label.hide()
        self.plots=_Plots(self);layout.addWidget(self.plots)
        self.period_spin.valueChanged.connect(lambda value:self._changed('input_period',float(value)))
        self.flow_spin.valueChanged.connect(lambda value:self._changed('input_flow',float(value)))
        self.extrapolate_check.toggled.connect(lambda value:self._changed('extrapolate',bool(value)))

    def set_query(self,query,paths=None,snapshot=None):
        self._query=query
        self.restore(snapshot if snapshot is not None else sample_quantiles(query,paths))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'_table_timer'):self._table_timer.start(0)

    def eventFilter(self,watched,event):
        if watched is self.table.viewport() and event.type()==QEvent.Resize:
            self._table_timer.start(0)
        return super().eventFilter(watched,event)

    def _fit_table_columns(self):
        width=self.table.viewport().width()
        self.table.setColumnWidth(0,round(width*.40))
        self.table.setColumnWidth(1,width-self.table.columnWidth(0))
        metrics=self.table.fontMetrics()
        minimum=max(metrics.height(),metrics.lineSpacing())+2
        self.table.verticalHeader().setMinimumSectionSize(minimum)
        self.table.verticalHeader().setDefaultSectionSize(minimum)
        for row in range(self.table.rowCount()):
            height=minimum
            for column in range(self.table.columnCount()):
                item=self.table.item(row,column)
                if item is not None:
                    bounds=metrics.boundingRect(0,0,max(1,self.table.columnWidth(column)-6),1000,
                                                Qt.AlignCenter|Qt.TextWordWrap,item.text())
                    height=max(height,bounds.height()+2)
            self.table.setRowHeight(row,height)
        self.table.horizontalHeader()._fit_height()

    def restore(self,snapshot):
        state=_normalise(snapshot)
        self._updating=True
        try:
            self._state=state
            for row,(period,flow,path) in enumerate(zip(PERIODS,state['flows'],state['source_paths'])):
                period_item=QTableWidgetItem(str(period));period_item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row,0,period_item)
                item=QTableWidgetItem('No disponible' if flow is None else _number(flow))
                item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(path);self.table.setItem(row,1,item)
            self._table_timer.start(0)
            self.period_spin.setMaximum(max(1e9,state['input_period']))
            self.flow_spin.setMaximum(max(1e12,state['input_flow']))
            self.period_spin.setValue(state['input_period']);self.flow_spin.setValue(state['input_flow'])
            self.extrapolate_check.setChecked(state['extrapolate'])
            self._refresh()
        finally:self._updating=False
        self.changed.emit()

    def snapshot(self):
        return copy.deepcopy(self._state)

    def _changed(self,key,value):
        if self._updating or self._state is None:return
        self._state[key]=value
        self._refresh();self.changed.emit()

    def _refresh(self):
        text=describe_estimates(self._state)
        self.forward_label.setText(text['forward_text']);self.inverse_label.setText(text['inverse_text'])
        self.method_label.setText(text['method_note'])
        self.extrapolate_check.setToolTip(text['method_note'])
        self.plots.state=self.snapshot()
        self.plots.setMinimumHeight(self.plots.heightForWidth(self.plots.width()))
        self.plots.updateGeometry();self.plots.update()
