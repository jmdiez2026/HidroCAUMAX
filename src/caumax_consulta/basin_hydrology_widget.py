"""Display basin-mean P0 and daily-rainfall quantiles without reading rasters.

The controller computes spatial statistics only after a button request. This
widget owns portable presentation state, tied to the original basin geometry,
and the screen/PDF interpolation of its six spatially averaged quantiles.
"""
import copy
import math

from qgis.PyQt.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .flow_quantiles import CompactTableHeader, PERIODS, estimate_flow as _estimate_precipitation
from .flow_quantiles import estimate_period as _estimate_period


METHOD = 'piecewise_linear_p_log_t'
_NOT_GIVEN = object()
_REL_TOL, _ABS_TOL = 1e-7, 1e-5
_RESULT_KEYS = {'source_path', 'mean_mm', 'basin_area_m2', 'covered_area_m2',
                'valid_area_m2', 'coverage_fraction', 'valid_fraction',
                'pixel_count', 'status', 'notes'}
_STATE_KEYS = {'version', 'basin_fingerprint', 'basin_area_m2', 'p0_result',
               'rainfall_results', 'input_period', 'input_precipitation',
               'extrapolate', 'method'}


def _number(value):
    return f'{value:.2f}'.rstrip('0').rstrip('.').replace('.', ',')


def _years(value):
    return f'{value:.0f}'


def _tick_number(value):
    # Compact plot ticks keep the axis label clear; stored/table precision stays intact.
    return f'{value:.2f}'.rstrip('0').rstrip('.').replace('.', ',')


def _finite(value, minimum=0, strict=False):
    if isinstance(value, bool):
        raise ValueError('Se esperaba un número finito.')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('Se esperaba un número finito.') from error
    if not math.isfinite(number) or (number <= minimum if strict else number < minimum):
        raise ValueError('Valor fuera del intervalo admisible.')
    return number


def _fingerprint(analysis):
    # Lazy by design: loading this presentation module never requires the engine
    # or a source raster, including when a saved project is displayed offline.
    from .basin_hydrology import basin_fingerprint
    return basin_fingerprint(analysis)


def _area(analysis):
    value = getattr(analysis, 'area_m2', None)
    if value is None:
        value = getattr(getattr(analysis, 'basin', None), 'area_m2', None)
    return _finite(value, strict=True)


def _result(result, area):
    if (not isinstance(result, dict) or not _RESULT_KEYS <= set(result)
            or set(result) - _RESULT_KEYS - {'statistics','distribution'}):
        raise ValueError('El resultado hidrológico no contiene los campos esperados.')
    if (not isinstance(result['source_path'], str)
            or result['status'] not in ('ok', 'partial', 'unavailable')
            or not isinstance(result['notes'], list)
            or len(result['notes']) > 1000
            or not all(isinstance(note, str) for note in result['notes'])):
        raise ValueError('La fuente, el estado o las notas no son válidos.')
    basin = _finite(result['basin_area_m2'], strict=True)
    covered = _finite(result['covered_area_m2'])
    valid = _finite(result['valid_area_m2'])
    coverage = _finite(result['coverage_fraction'])
    fraction = _finite(result['valid_fraction'])
    if (not math.isclose(basin, area, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
            or valid > covered + max(area*_REL_TOL, _ABS_TOL)
            or covered > area + max(area*_REL_TOL, _ABS_TOL)
            or coverage > 1+_REL_TOL or fraction > 1+_REL_TOL
            or not math.isclose(coverage, covered/area, rel_tol=_REL_TOL, abs_tol=1e-9)
            or not math.isclose(fraction, valid/area, rel_tol=_REL_TOL, abs_tol=1e-9)):
        raise ValueError('Las superficies y las coberturas no corresponden a esta cuenca.')
    count = result['pixel_count']
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError('El número de píxeles no es válido.')
    if covered > 0 and count == 0:
        raise ValueError('La superficie cubierta requiere píxeles intersectados.')
    mean = result['mean_mm']
    full = math.isclose(valid, area, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    if result['status'] == 'unavailable':
        if mean is not None or valid != 0:
            raise ValueError('Un resultado no disponible debe carecer de media y área válida.')
    else:
        mean = _finite(mean)
        if (result['status'] == 'ok' and not full
                or result['status'] == 'partial' and (valid <= 0 or full)):
            raise ValueError('El estado no corresponde a la cobertura válida indicada.')
    normalised = dict(source_path=result['source_path'], mean_mm=mean,
                basin_area_m2=basin, covered_area_m2=covered, valid_area_m2=valid,
                coverage_fraction=coverage, valid_fraction=fraction,
                pixel_count=count, status=result['status'], notes=list(result['notes']))
    if 'statistics' in result:
        from .raster_statistics import normalise_statistics
        normalised['statistics'] = normalise_statistics(result['statistics'], mean)
    if 'distribution' in result:
        from .raster_profiles import normalise_profile
        normalised['distribution'] = normalise_profile(result['distribution'],mean,valid)
    return normalised


def _normalise(state):
    if (not isinstance(state, dict) or set(state) != _STATE_KEYS
            or type(state['version']) is not int or state['version'] != 1
            or state['method'] != METHOD):
        raise ValueError('Estado hidrológico de cuenca no compatible.')
    fingerprint = state['basin_fingerprint']
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(character not in '0123456789abcdef' for character in fingerprint)):
        raise ValueError('Falta la huella de la cuenca.')
    area = _finite(state['basin_area_m2'], strict=True)
    rainfall = state['rainfall_results']
    if (not isinstance(rainfall, dict)
            or any(period not in {str(p) for p in PERIODS} for period in rainfall)):
        raise ValueError('La precipitación debe corresponder a los seis periodos CAUMAX.')
    if type(state['extrapolate']) is not bool:
        raise ValueError('La opción de extrapolación no es válida.')
    return dict(version=1, basin_fingerprint=fingerprint, basin_area_m2=area,
                p0_result=None if state['p0_result'] is None else _result(state['p0_result'], area),
                rainfall_results={key:_result(value, area) for key,value in rainfall.items()},
                input_period=_finite(state['input_period'], 1, strict=True),
                input_precipitation=_finite(state['input_precipitation']),
                extrapolate=state['extrapolate'], method=METHOD)


def _means(state):
    return [state['rainfall_results'].get(str(period), {}).get('mean_mm') for period in PERIODS]


def describe_precipitation_estimates(state):
    state = _normalise(state)
    means = _means(state)
    forward = _estimate_precipitation(state['input_period'], means, state['extrapolate'])
    inverse = _estimate_period(state['input_precipitation'], means, state['extrapolate'])
    if forward['flow'] is None:
        direct_text = 'Pd(T): no disponible para este periodo con las medias calculadas.'
    else:
        kind = {'exact':'media del cuantil de la capa', 'interpolated':'interpolación',
                'extrapolated':'extrapolación'}[forward['status']]
        direct_text = (f"Pd({_years(state['input_period'])} años) = "
                       f"{_number(forward['flow'])} mm ({kind}).")
    parts = [f"{_years(item['period'])} años" for item in inverse['solutions']]
    for interval in inverse['intervals']:
        low, high = interval['min_period'], interval['max_period']
        if high is None:
            parts.append(f'desde {_years(low)} años sin límite superior (meseta)')
        else:
            beginning = 'entre' if interval['min_inclusive'] else 'más de'
            parts.append(f'{beginning} {_years(low)} y {_years(high)} años (meseta)')
    if parts:
        multiple = inverse['status'] in ('multiple', 'interval')
        inverse_text = (f"{'T posibles' if multiple else 'T'} para Pd media = "
                        f"{_number(state['input_precipitation'])} mm: " + '; '.join(parts) + '.')
        if multiple:
            inverse_text += ' Solución no única.'
        if inverse['extrapolated']:
            inverse_text += ' Incluye extrapolación.'
    else:
        inverse_text = 'T(Pd): no disponible para esta precipitación con las medias calculadas.'
    note = ('Promedio espacial de los cuantiles puntuales de precipitación máxima diaria '
            'sobre la parte válida de la cuenca. Interpolación lineal de Pd media frente '
            'a ln(T), con la inversa sobre los mismos tramos. No equivale a una frecuencia '
            'de tormenta areal. P₀ se presenta sin aplicar β ni otros coeficientes correctores. '
            + ('Extrapolación permitida.' if state['extrapolate']
               else 'Sin extrapolación fuera de los cuantiles calculados.'))
    if any(mean is None for mean in means):
        note += ' Los periodos no calculados o sin datos válidos no se sustituyen por cero.'
    if any(result['status'] == 'partial' for result in state['rainfall_results'].values()):
        note += ' Hay coberturas parciales: las medias representan únicamente el área válida indicada.'
    from .raster_profiles import statistics_for_period
    statistics=statistics_for_period(state)
    if statistics is not None and forward['flow'] is not None:
        direct_text=(f"Pd({_years(state['input_period'])} años): media {_number(statistics['mean_mm'])} mm · "
            f"mediana {_number(statistics['median_mm'])} mm\n"
            f"Máximo: {_number(statistics['max_mm'])} mm · Mínimo: {_number(statistics['min_mm'])} mm\n"
            f"Desviación típica: {_number(statistics['stddev_mm'])} mm ({kind}).")
        note += ' Los estadísticos de Pd(T) se obtienen interpolando por celda en ln(T), sobre la superficie válida común de las capas.'
    elif forward['flow'] is not None:
        direct_text += ' Mediana, extremos y desviación: pendientes de recalcular.'
    return dict(forward_text=direct_text, inverse_text=inverse_text, method_note=note)


def _precipitation_plot_inputs(state):
    state=_normalise(state)
    means=_means(state)
    known=[(float(period),mean) for period,mean in zip(PERIODS,means) if mean is not None]
    forward=_estimate_precipitation(state['input_period'],means,state['extrapolate'])
    inverse=_estimate_period(state['input_precipitation'],means,state['extrapolate'])
    from .raster_profiles import statistics_for_period
    statistics=statistics_for_period(state)
    if statistics is not None and forward['flow'] is not None:
        forward['flow']=statistics['mean_mm']
    return dict(state,input_flow=state['input_precipitation']),known,forward,inverse


def render_precipitation_plots(state,width=1100,height=500,font_family=None):
    """Pd uses the same integer ticks and measured margins as the Qci plots."""
    from .quantile_graphics import render
    prepared,known,forward,inverse=_precipitation_plot_inputs(state)
    return render(prepared,known,forward,inverse,width,height,font_family,quantity='Pd',unit='mm')


class _PrecipitationPlots(QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.state=None
        self.setMinimumHeight(360)

    def sizeHint(self):return QSize(640,360)

    def heightForWidth(self,width):
        base=680 if width<580 else 360
        if self.state is None:return base
        from .quantile_graphics import geometry
        state,known,forward,inverse=_precipitation_plot_inputs(self.state)
        return geometry(state,known,forward,inverse,width,base)['height']

    def resizeEvent(self,event):
        super().resizeEvent(event)
        height=self.heightForWidth(self.width())
        if self.minimumHeight()!=height:
            self.setMinimumHeight(height);self.updateGeometry()

    def paintEvent(self,event):
        painter=QPainter(self)
        if self.state is None:
            painter.fillRect(self.rect(),QColor('white'))
        else:
            png=render_precipitation_plots(self.state,max(320,self.width()),
                                         max(self.heightForWidth(self.width()),self.height()))
            painter.drawImage(self.rect(),QImage.fromData(png))
        painter.end()


class BasinHydrologyWidget(QWidget):
    calculate_p0 = pyqtSignal()
    calculate_rainfall = pyqtSignal()
    cancel_requested = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self._analysis = None
        self._state = None
        self._updating = False
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.p0_button = QPushButton('Calcular P₀ medio',self)
        self.rainfall_button = QPushButton('Calcular Precipitación diaria\nmáxima anual MEDIA (mm)',self)
        self.rainfall_button.setToolTip('Calcular Precipitación diaria máxima anual MEDIA (mm)')
        caption_lines = self.rainfall_button.text().split('\n')
        metrics = self.rainfall_button.fontMetrics()
        button_width = max(metrics.horizontalAdvance(line) for line in caption_lines)+22
        button_height = max(32,max(metrics.height(),metrics.lineSpacing())*2+6)
        for button in (self.p0_button,self.rainfall_button):
            button.setStyleSheet('QPushButton { padding:2px 9px; min-height:0px; }')
            button.setFixedSize(button_width,button_height)
        self.p0_label = QLabel('P₀ medio: no calculado. β no aplicado.',self)
        self.p0_label.setTextFormat(Qt.RichText)
        self.p0_statistics_label = QLabel('Mediana, máximo, mínimo y desviación típica: no calculados.',self)
        self.p0_statistics_label.setWordWrap(True)
        self.coverage_label = QLabel('Cobertura: no calculada.',self)
        self.p0_label.setWordWrap(True)
        self.coverage_label.setWordWrap(True)
        p0_row = QGridLayout()
        p0_row.addWidget(self.p0_button,0,0,3,1,Qt.AlignVCenter)
        p0_row.addWidget(self.p0_label,0,1)
        p0_row.addWidget(self.p0_statistics_label,1,1)
        p0_row.addWidget(self.coverage_label,2,1)
        p0_row.setColumnStretch(1,1)
        layout.addLayout(p0_row)
        layout.addWidget(self.rainfall_button,0,Qt.AlignLeft)
        self.rainfall_table = QTableWidget(6,6,self)
        self.rainfall_table.setHorizontalHeader(CompactTableHeader(self.rainfall_table))
        self.rainfall_table.setHorizontalHeaderLabels(
            ['Periodo T (años)','Pd (media)','Pd mínimo','Pd máximo','Desv. Típica','Estado'])
        self.rainfall_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rainfall_table.setAlternatingRowColors(False)
        self.rainfall_table.setStyleSheet('QTableWidget { background-color:white; alternate-background-color:white; }')
        self.rainfall_table.verticalHeader().setVisible(False)
        self.rainfall_table.verticalHeader().setDefaultSectionSize(24)
        self.rainfall_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rainfall_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents)
        self.rainfall_table.setFixedHeight(6*24+self.rainfall_table.horizontalHeader().sizeHint().height()+2*self.rainfall_table.frameWidth())
        layout.addWidget(self.rainfall_table)
        controls = QGridLayout()
        self.period_spin = QDoubleSpinBox(self)
        self.period_spin.setDecimals(0)
        self.period_spin.setRange(2,1e9)
        self.period_spin.setValue(100)
        self.period_spin.setSuffix(' años')
        self.precipitation_spin = QDoubleSpinBox(self)
        self.precipitation_spin.setDecimals(2)
        self.precipitation_spin.setRange(0,1e9)
        self.precipitation_spin.setSuffix(' mm')
        self.forward_label,self.inverse_label = QLabel(self),QLabel(self)
        self.forward_label.setWordWrap(True)
        self.inverse_label.setWordWrap(True)
        controls.addWidget(QLabel('Consultar Pd(T)',self),0,0)
        controls.addWidget(self.period_spin,0,1)
        controls.addWidget(self.forward_label,0,2)
        controls.addWidget(QLabel('Consultar T(Pd)',self),1,0)
        controls.addWidget(self.precipitation_spin,1,1)
        controls.addWidget(self.inverse_label,1,2)
        controls.setColumnStretch(2,1)
        self.extrapolate_check = QCheckBox('Permitir extrapolación',self)
        self.extrapolate_check.setChecked(True)
        controls.addWidget(self.extrapolate_check,2,0,1,3)
        layout.addLayout(controls)
        self.method_label = QLabel(self)
        self.method_label.setWordWrap(True)
        layout.addWidget(self.method_label)
        self.plots = _PrecipitationPlots(self)
        layout.addWidget(self.plots)
        self.status_label = QLabel('Selecciona una cuenca para calcular sus datos hidrológicos.',self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.cancel_button = QPushButton('Cancelar cálculo',self)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.p0_button.clicked.connect(lambda checked=False:self.calculate_p0.emit())
        self.rainfall_button.clicked.connect(lambda checked=False:self.calculate_rainfall.emit())
        self.cancel_button.clicked.connect(lambda checked=False:self.cancel_requested.emit())
        self.period_spin.valueChanged.connect(lambda value:self._changed('input_period',float(value)))
        self.precipitation_spin.valueChanged.connect(lambda value:self._changed('input_precipitation',float(value)))
        self.extrapolate_check.toggled.connect(lambda value:self._changed('extrapolate',bool(value)))
        self._refresh()
        QTimer.singleShot(0,self._fit_calculation_buttons)

    def _fit_calculation_buttons(self):
        try:
            for button in (self.p0_button,self.rainfall_button):button.ensurePolished()
            metrics=self.rainfall_button.fontMetrics()
            width=max(metrics.horizontalAdvance(line) for line in self.rainfall_button.text().split('\n'))+22
            height=max(32,max(metrics.height(),metrics.lineSpacing())*2+6)
            for button in (self.p0_button,self.rainfall_button):button.setFixedSize(width,height)
        except RuntimeError:
            return

    def set_analysis(self,analysis,state=_NOT_GIVEN):
        if analysis is None:
            self._analysis = None
            self._state = None
            self._refresh()
            self.changed.emit()
            return
        fingerprint,area = _fingerprint(analysis),_area(analysis)
        if state is not _NOT_GIVEN and state is not None:
            restored = _normalise(state)
            self._check_identity(restored,fingerprint,area)
        elif (state is _NOT_GIVEN and self._state is not None
              and self._state['basin_fingerprint'] == fingerprint):
            self._check_identity(self._state,fingerprint,area)
            self._analysis = analysis
            self._sync_busy()
            return
        else:
            restored = _normalise(dict(version=1,basin_fingerprint=fingerprint,basin_area_m2=area,
                p0_result=None,rainfall_results={},input_period=100.,
                input_precipitation=0.,extrapolate=True,method=METHOD))
        self._analysis = analysis
        self._apply(restored)

    @staticmethod
    def _check_identity(state,fingerprint,area):
        if (state['basin_fingerprint'] != fingerprint
                or not math.isclose(state['basin_area_m2'],area,rel_tol=_REL_TOL,abs_tol=_ABS_TOL)):
            raise ValueError('Los datos hidrológicos guardados pertenecen a otra cuenca.')

    def restore(self,state,analysis=None):
        restored = _normalise(state)
        current = analysis if analysis is not None else self._analysis
        if current is not None:
            self._check_identity(restored,_fingerprint(current),_area(current))
        if analysis is not None:
            self._analysis = analysis
        self._apply(restored)

    def _apply(self,state):
        self._updating = True
        try:
            self._state = state
            self.period_spin.setMaximum(max(1e9,state['input_period']))
            self.precipitation_spin.setMaximum(max(1e9,state['input_precipitation']))
            self.period_spin.setValue(state['input_period'])
            self.precipitation_spin.setValue(state['input_precipitation'])
            self.extrapolate_check.setChecked(state['extrapolate'])
            self._refresh()
        finally:
            self._updating = False
        self.changed.emit()

    def snapshot(self):
        return copy.deepcopy(self._state)

    def set_result(self,kind,result):
        if self._state is None:
            raise ValueError('Primero debe seleccionarse una cuenca.')
        updated = self.snapshot()
        if kind == 'p0':
            updated['p0_result'] = result
        elif kind == 'rainfall':
            updated['rainfall_results'] = result
        else:
            raise ValueError('Tipo de resultado hidrológico no reconocido.')
        self._apply(_normalise(updated))

    def set_busy(self,busy,text=''):
        self._busy = bool(busy)
        self._sync_busy()
        if text:
            self.status_label.setText(str(text))
        elif not self._busy:
            self.status_label.setText(self._ready_status())

    def _sync_busy(self):
        enabled = self._analysis is not None and not self._busy
        self.p0_button.setEnabled(enabled)
        self.rainfall_button.setEnabled(enabled)
        self.cancel_button.setVisible(self._busy)

    def _changed(self,key,value):
        if self._updating or self._state is None:
            return
        self._state[key] = value
        self._refresh()
        self.changed.emit()

    def _ready_status(self):
        if self._state is None:
            return 'Selecciona una cuenca para calcular sus datos hidrológicos.'
        count = len(self._state['rainfall_results'])
        p0 = self._state['p0_result']
        if p0 is None and not count:
            return 'Datos todavía no calculados. Pulsa el botón correspondiente.'
        partial = any(result['status'] != 'ok' for result in
                      ([p0] if p0 is not None else [])+list(self._state['rainfall_results'].values()))
        return (f"P₀: {'calculado' if p0 is not None else 'no calculado'} · "
                f'Precipitación: {count}/6 periodos calculados.'
                + (' Revisa las coberturas parciales o los datos no disponibles.' if partial else ''))

    def _refresh(self):
        self._sync_busy()
        p0 = self._state['p0_result'] if self._state is not None else None
        if p0 is None:
            self.p0_label.setText('P₀ medio: no calculado. β no aplicado.')
            self.p0_statistics_label.setText('Mediana, máximo, mínimo y desviación típica: no calculados.')
            self.coverage_label.setText('Cobertura: no calculada.')
            self.p0_label.setToolTip('')
        else:
            mean = ('sin dato válido' if p0['mean_mm'] is None else
                    f"{p0['mean_mm']:.2f}".replace('.', ',')+' mm')
            self.p0_label.setText(f'P₀ medio: <span style="color:#C00000; font-weight:700">{mean}</span>. β no aplicado.')
            statistics = p0.get('statistics')
            if statistics is None:
                self.p0_statistics_label.setText('Estadísticos no disponibles. Pulsa «Calcular P₀ medio» para obtenerlos.')
            else:
                numbers = {key:f'{value:.2f}'.replace('.', ',') for key,value in statistics.items()}
                self.p0_statistics_label.setText(
                    f"Mediana: {numbers['median_mm']} mm · Máximo: {numbers['max_mm']} mm · Mínimo: {numbers['min_mm']} mm\n"
                    f"Desviación típica: {numbers['stddev_mm']} mm")
            self.p0_statistics_label.setToolTip('Mediana y desviación típica poblacional ponderadas por el área válida de los píxeles dentro de la cuenca. No se aplica β.')
            self.coverage_label.setText(
                f"Cobertura ráster: {_number(p0['coverage_fraction']*100)} % · "
                f"Área válida: {_number(p0['valid_fraction']*100)} %.")
            self.p0_label.setToolTip(p0['source_path']+'\n'+'\n'.join(p0['notes']))
        rainfall = self._state['rainfall_results'] if self._state is not None else {}
        statuses = {'ok':'Completa','partial':'Parcial','unavailable':'Sin dato válido'}
        for row,period in enumerate(PERIODS):
            result = rainfall.get(str(period))
            values = [str(period),'No calculado','—','—','—','No calculado']
            if result is not None:
                statistics = result.get('statistics') or {}
                values = [str(period),'Sin dato válido' if result['mean_mm'] is None else _number(result['mean_mm'])]
                values.extend(_number(statistics[key]) if key in statistics else '—'
                              for key in ('min_mm','max_mm','stddev_mm'))
                values.append(statuses[result['status']])
            for col,value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setBackground(QColor('white'))
                item.setTextAlignment(Qt.AlignCenter)
                if result is not None:
                    item.setToolTip(result['source_path']+'\n'
                        +f"Cobertura ráster: {_number(result['coverage_fraction']*100)} % · Área válida: {_number(result['valid_fraction']*100)} %.\n"
                        +'\n'.join(result['notes']))
                self.rainfall_table.setItem(row,col,item)
        if self._state is None:
            self.forward_label.setText('Pd(T): no calculada.')
            self.inverse_label.setText('T(Pd): no calculado.')
            self.method_label.setText('P₀ sin aplicar β. La precipitación se calcula únicamente cuando se solicita.')
            self.plots.state = None
        else:
            descriptions = describe_precipitation_estimates(self._state)
            self.forward_label.setText(descriptions['forward_text'])
            self.inverse_label.setText(descriptions['inverse_text'])
            self.method_label.setText(descriptions['method_note'])
            self.plots.state = self.snapshot()
        self.plots.setMinimumHeight(self.plots.heightForWidth(self.plots.width()))
        self.plots.updateGeometry();self.plots.update()
        if not self._busy:
            self.status_label.setText(self._ready_status())
