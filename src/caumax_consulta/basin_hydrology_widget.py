"""Display automatic basin-mean P0 and daily-rainfall spatial quantiles.

The controller computes statistics in background after the basin is resolved.
This widget owns portable presentation state, tied to the actual basin geometry,
and the screen/PDF interpolation of its six spatially averaged quantiles.
"""
import copy
import math
from pathlib import Path

from qgis.PyQt.QtCore import QBuffer, QIODevice, QPointF, QRectF, QSize, Qt, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPolygonF, QTextDocument
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHeaderView, QLabel, QPushButton,
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


def describe_precipitation_estimates(state, rich_text=False):
    state = _normalise(state)
    highlight = lambda text: '<b>'+text+'</b>' if rich_text else text
    means = _means(state)
    forward = _estimate_precipitation(state['input_period'], means, state['extrapolate'])
    inverse = _estimate_period(state['input_precipitation'], means, state['extrapolate'])
    if forward['flow'] is None:
        direct_text = 'Pd(T): no disponible para este periodo con las medias calculadas.'
    else:
        kind = {'exact':'media del cuantil de la capa', 'interpolated':'interpolación',
                'extrapolated':'extrapolación'}[forward['status']]
        direct_text = (f"Pd({_years(state['input_period'])} años) = "
                       f"{highlight(_number(forward['flow']))} mm ({kind}).")
    parts = [f"{highlight(_years(item['period']))} años" for item in inverse['solutions']]
    for interval in inverse['intervals']:
        low, high = interval['min_period'], interval['max_period']
        if high is None:
            parts.append(f'desde {highlight(_years(low))} años sin límite superior (meseta)')
        else:
            beginning = 'entre' if interval['min_inclusive'] else 'más de'
            parts.append(f'{beginning} {highlight(_years(low))} y {highlight(_years(high))} años (meseta)')
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
            'sobre la parte válida de la cuenca.')
    if not rich_text:
        note += (' Interpolación lineal de Pd media frente a ln(T), con la inversa sobre los mismos tramos. '
                 'No equivale a una frecuencia de tormenta areal. P₀ se presenta sin aplicar β '
                 'ni otros coeficientes correctores. '
                 + ('Extrapolación permitida.' if state['extrapolate']
                    else 'Sin extrapolación fuera de los cuantiles calculados.'))
    if any(mean is None for mean in means):
        note += ' Los periodos no calculados o sin datos válidos no se sustituyen por cero.'
    if any(result['status'] == 'partial' for result in state['rainfall_results'].values()):
        note += ' Hay coberturas parciales: las medias representan únicamente el área válida indicada.'
    from .raster_profiles import statistics_for_period
    statistics=statistics_for_period(state)
    if statistics is not None and forward['flow'] is not None:
        maximum,minimum,deviation = (('Máx','Mín','Desv. típ. espacial') if rich_text else
                                    ('Máximo','Mínimo','Desviación típica espacial'))
        deviation_separator = ' · ' if rich_text else '\n'
        direct_text=(f"Pd({_years(state['input_period'])} años): media {highlight(_number(statistics['mean_mm']))} mm · "
            f"mediana {_number(statistics['median_mm'])} mm\n"
            f"{maximum}: {_number(statistics['max_mm'])} mm · {minimum}: {_number(statistics['min_mm'])} mm{deviation_separator}"
            f"{deviation}: {_number(statistics['stddev_mm'])} mm")
        note += ' Los estadísticos de Pd(T) se obtienen interpolando por celda en ln(T), sobre la superficie válida común de las capas.'
    elif forward['flow'] is not None:
        direct_text += ' Mediana, extremos y desviación: pendientes de recalcular.'
    if rich_text:
        direct_text = direct_text.replace('\n','<br/>')
        inverse_text = inverse_text.replace('\n','<br/>')
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


def render_precipitation_plots(state,width=1100,height=500,font_family=None,*,fit_height=False,selected=None):
    """Pd uses the same integer ticks and measured margins as the Qci plots."""
    from .quantile_graphics import render
    prepared,known,forward,inverse=_precipitation_plot_inputs(state)
    return render(prepared,known,forward,inverse,width,height,font_family,quantity='Pd',unit='mm',fit_height=fit_height,selected=selected)


class _PrecipitationPlots(QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.state=None
        self.selected=None
        self.setMinimumHeight(263)

    def sizeHint(self):return QSize(640,263)

    def heightForWidth(self,width):
        # Another 10% reduction of the *current* Pd panel, preserving axis fonts.
        base=680 if width<580 else 360
        if self.state is None:return round(base*.729)
        from .quantile_graphics import geometry
        state,known,forward,inverse=_precipitation_plot_inputs(self.state)
        return round(geometry(state,known,forward,inverse,width,base)['height']*.729)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        height=self.heightForWidth(self.width())
        if self.minimumHeight()!=height:
            self.setMinimumHeight(height);self.updateGeometry()

    def _render_size(self):
        return max(320,self.width()),max(self.heightForWidth(self.width()),self.height())

    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton or self.state is None:
            return super().mousePressEvent(event)
        from .quantile_graphics import geometry, pick_linked_curve
        prepared,known,forward,inverse=_precipitation_plot_inputs(self.state)
        width,height=self._render_size()
        layout=geometry(prepared,known,forward,inverse,width,height,fit_height=True)
        picked=pick_linked_curve(layout,known,
            event.x()*layout['width']/max(1,self.width()),
            event.y()*layout['height']/max(1,self.height()),
            tolerance=12.)
        if picked is None:
            return super().mousePressEvent(event)
        self.selected=picked
        self.update()
        event.accept()

    def paintEvent(self,event):
        painter=QPainter(self)
        if self.state is None:
            painter.fillRect(self.rect(),QColor('white'))
        else:
            width,height=self._render_size()
            png=render_precipitation_plots(self.state,width,height,
                                          fit_height=True,selected=self.selected)
            painter.drawImage(self.rect(),QImage.fromData(png))
        painter.end()


class BasinHydrologyWidget(QWidget):
    cancel_requested = pyqtSignal()
    changed = pyqtSignal()
    po_clip_requested = pyqtSignal()
    po_export_requested = pyqtSignal()

    def __init__(self,parent=None):
        super().__init__(parent)
        self._analysis = None
        self._state = None
        self._updating = False
        self._busy = False
        self._po_export_ready = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.p0_group = QGroupBox('Umbral de Escorrentía (P₀)',self)
        self.rainfall_group = QGroupBox('Precipitación diaria máxima anual media en la cuenca',self)
        p0_layout = QVBoxLayout(self.p0_group)
        rainfall_layout = QVBoxLayout(self.rainfall_group)
        layout.addWidget(self.p0_group)
        layout.addWidget(self.rainfall_group)
        # P₀ and rainfall are evaluated automatically whenever the basin
        # changes; there are no manual calculation buttons in this panel.
        self.p0_label = QLabel('P₀ medio en la cuenca: pendiente de cálculo. β no aplicado.',self)
        self.p0_label.setTextFormat(Qt.RichText)
        self.p0_statistics_label = QLabel('Mediana, Máx, Mín y Desv. típ: no calculados.',self)
        self.p0_statistics_label.setWordWrap(True)
        self.coverage_label = QLabel('Cobertura: no calculada.',self)
        self.coverage_label.hide()
        self.p0_label.setWordWrap(True)
        self.coverage_label.setWordWrap(True)
        p0_row = QGridLayout()
        p0_row.addWidget(self.p0_label,0,0)
        p0_row.addWidget(self.p0_statistics_label,1,0)
        p0_row.addWidget(self.coverage_label,2,0)
        # Both selected model-2 icons belong beside the P₀ statistics,
        # inside the same group, never under any longitudinal graph.
        self.po_clip_button = QPushButton('Recorte capa Po', self.p0_group)
        self.po_export_button = QPushButton('Exportar capa Po', self.p0_group)
        for control, asset, tip in (
            (self.po_clip_button, 'recorte_po_modelo2.svg',
             'Recortar el ráster P₀ oficial utilizando la divisoria activa.'),
            (self.po_export_button, 'exportar_po_modelo2.svg',
             'Exportar como CONSULTA_Umbral Po Cuenca.tif a Capas_Generadas.')):
            control.setIcon(QIcon(str(Path(__file__).parent / 'assets' / asset)))
            control.setIconSize(QSize(26, 26))
            control.setToolTip(tip)
            control.setMinimumHeight(34)
            control.setEnabled(False)
        self.po_clip_button.clicked.connect(lambda checked=False: self.po_clip_requested.emit())
        self.po_export_button.clicked.connect(lambda checked=False: self.po_export_requested.emit())
        p0_row.addWidget(self.po_clip_button,0,1)
        p0_row.addWidget(self.po_export_button,1,1)
        p0_row.setColumnStretch(0,1)
        p0_layout.addLayout(p0_row)
        self.rainfall_table = QTableWidget(6,6,self)
        self.rainfall_table.setHorizontalHeader(CompactTableHeader(self.rainfall_table))
        self.rainfall_table.setHorizontalHeaderLabels(
            ['Periodo T (años)','Pd (media) [mm]','Pd mín [mm]','Pd máx [mm]',
             'Desv. típ. [mm]','Estado'])
        self.rainfall_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rainfall_table.setAlternatingRowColors(False)
        self.rainfall_table.setStyleSheet('QTableWidget { background-color:white; alternate-background-color:white; }')
        self.rainfall_table.verticalHeader().setVisible(False)
        row_height=max(self.rainfall_table.fontMetrics().height(),self.rainfall_table.fontMetrics().lineSpacing())+2
        self.rainfall_table.verticalHeader().setMinimumSectionSize(row_height)
        self.rainfall_table.verticalHeader().setDefaultSectionSize(row_height)
        self.rainfall_table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.rainfall_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rainfall_table.horizontalHeader().setSectionResizeMode(5,QHeaderView.ResizeToContents)
        self.rainfall_table.setFixedHeight(6*row_height+self.rainfall_table.horizontalHeader().sizeHint().height()+2*self.rainfall_table.frameWidth())
        self._rainfall_rows_timer=QTimer(self)
        self._rainfall_rows_timer.setSingleShot(True)
        self._rainfall_rows_timer.timeout.connect(self._fit_rainfall_rows)
        self.rainfall_table.horizontalHeader().sectionResized.connect(lambda *_:self._rainfall_rows_timer.start(0))
        rainfall_layout.addWidget(self.rainfall_table)
        controls = QGridLayout()
        self.period_spin = QDoubleSpinBox(self)
        self.period_spin.setDecimals(0)
        self.period_spin.setRange(2,1e9)
        self.period_spin.setValue(100)
        self.period_spin.setSuffix(' años')
        self.precipitation_spin = QDoubleSpinBox(self)
        self.precipitation_spin.setDecimals(2)
        self.precipitation_spin.setRange(0,1e9)
        self.precipitation_spin.setValue(100)
        self.precipitation_spin.setSuffix(' mm')
        control_width=max(110,self.precipitation_spin.fontMetrics().horizontalAdvance('9999,99 mm')+24)
        self.period_spin.setFixedWidth(control_width)
        self.precipitation_spin.setFixedWidth(control_width)
        self.forward_label,self.inverse_label = QLabel(self),QLabel(self)
        self.forward_label.setWordWrap(True)
        self.inverse_label.setWordWrap(True)
        result_font = QFont(self.font());result_font.setBold(False)
        for label in (self.forward_label,self.inverse_label):
            label.setTextFormat(Qt.RichText)
            label.setFont(result_font)
        controls.addWidget(QLabel('Consultar Pd(T)',self),0,0)
        controls.addWidget(self.period_spin,0,1)
        controls.addWidget(self.forward_label,0,3)
        controls.addWidget(QLabel('Consultar T(Pd)',self),1,0)
        controls.addWidget(self.precipitation_spin,1,1)
        controls.addWidget(self.inverse_label,1,3)
        controls.setColumnStretch(3,1)
        self.extrapolate_check = QCheckBox('Permitir\nextrapolación',self)
        self.extrapolate_check.setChecked(True)
        controls.addWidget(self.extrapolate_check,0,2,2,1,Qt.AlignVCenter)
        rainfall_layout.addLayout(controls)
        self.method_label = QLabel(self)
        self.method_label.setWordWrap(True)
        rainfall_layout.addWidget(self.method_label)
        self.plots = _PrecipitationPlots(self)
        rainfall_layout.addWidget(self.plots)
        self._statistics_layout_timer=QTimer(self)
        self._statistics_layout_timer.setSingleShot(True)
        self._statistics_layout_timer.timeout.connect(self._fit_statistic_labels)
        self.status_label = QLabel('Selecciona una cuenca para calcular sus datos hidrológicos.',self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.cancel_button = QPushButton('Cancelar cálculo',self)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(lambda checked=False:self.cancel_requested.emit())
        self.period_spin.valueChanged.connect(lambda value:self._changed('input_period',float(value)))
        self.precipitation_spin.valueChanged.connect(lambda value:self._changed('input_precipitation',float(value)))
        self.extrapolate_check.toggled.connect(lambda value:self._changed('extrapolate',bool(value)))
        self._refresh()
        QTimer.singleShot(0,self._fit_statistic_labels)

    def _fit_statistic_labels(self):
        """Keep the basin-mean statistics legible at narrow window widths.

        Only these result labels use a smaller local font when necessary. A
        very narrow dialog may wrap at the readable minimum; no value is lost.
        """
        base=QFont(self.font())
        base.setBold(False)
        for label in (self.p0_statistics_label,self.forward_label):
            width=max(1,label.width()-2)
            font=QFont(base)
            document=QTextDocument()
            document.setDocumentMargin(0)
            while True:
                document.setDefaultFont(font)
                if label.textFormat()==Qt.RichText:
                    document.setHtml(label.text())
                else:
                    document.setPlainText(label.text())
                document.setTextWidth(-1)
                if document.idealWidth()<=width:
                    break
                if font.pixelSize()>0:
                    minimum=min(base.pixelSize(),11)
                    if font.pixelSize()<=minimum:break
                    font.setPixelSize(font.pixelSize()-1)
                else:
                    minimum=min(base.pointSizeF(),8.)
                    if font.pointSizeF()<=minimum:break
                    font.setPointSizeF(max(minimum,font.pointSizeF()-.25))
            if label.font()!=font:
                label.setFont(font)

    def _fit_rainfall_rows(self):
        table=self.rainfall_table
        metrics=table.fontMetrics()
        minimum=max(metrics.height(),metrics.lineSpacing())+2
        table.verticalHeader().setMinimumSectionSize(minimum)
        table.verticalHeader().setDefaultSectionSize(minimum)
        for row in range(table.rowCount()):
            height=minimum
            for column in range(table.columnCount()):
                item=table.item(row,column)
                if item is not None:
                    bounds=metrics.boundingRect(0,0,max(1,table.columnWidth(column)-6),1000,
                                                Qt.AlignCenter|Qt.TextWordWrap,item.text())
                    height=max(height,bounds.height()+2)
            table.setRowHeight(row,height)
        # Unit suffixes can wrap the Pd column headers; keep the six rows
        # and the full header visible at every supported QGIS panel width.
        table.horizontalHeader()._fit_height()
        table.setFixedHeight(table.horizontalHeader().height() +
                             sum(table.rowHeight(row) for row in range(table.rowCount())) +
                             2 * table.frameWidth())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'_rainfall_rows_timer'):
            self._rainfall_rows_timer.start(0)
        if hasattr(self,'_statistics_layout_timer'):
            self._statistics_layout_timer.start(0)

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
                input_precipitation=100.,extrapolate=True,method=METHOD))
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

    def set_clip_export_available(self, available):
        self._po_export_ready = bool(available)
        self._refresh_po_actions()

    def _refresh_po_actions(self):
        p0 = self._state.get('p0_result') if self._state else None
        available = bool(p0 and p0.get('source_path') and
                         p0.get('status') in ('ok', 'partial'))
        self.po_clip_button.setEnabled(available and not self._busy)
        self.po_export_button.setEnabled(self._po_export_ready and not self._busy)

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
        elif kind == 'both':
            if not isinstance(result, dict) or set(result) != {'p0', 'rainfall'}:
                raise ValueError('Faltan resultados del cálculo hidrológico conjunto.')
            updated['p0_result'] = result['p0']
            updated['rainfall_results'] = result['rainfall']
        else:
            raise ValueError('Tipo de resultado hidrológico no reconocido.')
        # Publish P₀ and rainfall atomically: a canceled or failed task must
        # never display means from two different watershed geometries.
        self._apply(_normalise(updated))

    def set_busy(self,busy,text=''):
        self._busy = bool(busy)
        self._sync_busy()
        if text:
            self._set_status(str(text))
        elif self._busy:
            self._set_status('Calculando datos hidrológicos…')
        else:
            self._set_status(self._ready_status())

    def _set_status(self,text):
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _sync_busy(self):
        self.cancel_button.setVisible(self._busy)
        self._refresh_po_actions()

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
            return 'Calculando automáticamente P₀ y precipitación de la cuenca…'
        partial = any(result['status'] != 'ok' for result in
                      ([p0] if p0 is not None else [])+list(self._state['rainfall_results'].values()))
        return 'Revisa las coberturas parciales o los datos no disponibles.' if partial else ''

    def _refresh(self):
        self._sync_busy()
        p0 = self._state['p0_result'] if self._state is not None else None
        if p0 is None:
            self.p0_label.setText('P₀ medio en la cuenca: pendiente de cálculo. β no aplicado.')
            self.p0_statistics_label.setText('Mediana, Máx, Mín y Desv. típ: no calculados.')
            self.coverage_label.setText('Cobertura: no calculada.')
            self.p0_label.setToolTip('')
        else:
            mean = ('sin dato válido' if p0['mean_mm'] is None else
                    f"{p0['mean_mm']:.2f}".replace('.', ',')+' mm')
            self.p0_label.setText(f'P₀ medio en la cuenca: <span style="color:#C00000; font-weight:700">{mean}</span>. β no aplicado.')
            statistics = p0.get('statistics')
            if statistics is None:
                self.p0_statistics_label.setText('Estadísticos no disponibles para esta cuenca.')
            else:
                numbers = {key:f'{value:.2f}'.replace('.', ',') for key,value in statistics.items()}
                self.p0_statistics_label.setText(
                    f"Mediana: {numbers['median_mm']} mm · Máx: {numbers['max_mm']} mm · Mín: {numbers['min_mm']} mm · "
                    f"Desv. típ: {numbers['stddev_mm']} mm")
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
            self.method_label.setText('P₀ sin aplicar β. La precipitación se calcula automáticamente para la cuenca activa.')
            self.plots.state = None
        else:
            descriptions = describe_precipitation_estimates(self._state,rich_text=True)
            self.forward_label.setText(descriptions['forward_text'])
            self.inverse_label.setText(descriptions['inverse_text'])
            self.method_label.setText(descriptions['method_note'])
            self.plots.state = self.snapshot()
        self.plots.setMinimumHeight(self.plots.heightForWidth(self.plots.width()))
        self.plots.updateGeometry();self.plots.update()
        self._rainfall_rows_timer.start(0)
        self._statistics_layout_timer.start(0)
        if not self._busy:
            self._set_status(self._ready_status())
