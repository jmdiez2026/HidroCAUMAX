"""A4 basin reports, written atomically and opened only after a valid PDF exists."""

from datetime import datetime
import base64
import html
import math
import os
from pathlib import Path
import tempfile

from qgis.PyQt.QtCore import QStandardPaths, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QFontDatabase, QImage, QTextDocument
from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import QgsCoordinateReferenceSystem


class PdfOpenError(RuntimeError):
    """The PDF was saved successfully, but its viewer could not be requested."""

    def __init__(self, path):
        self.path = Path(path)
        super().__init__(
            'El PDF se ha guardado correctamente, pero no se pudo abrir automáticamente: '
            + str(self.path)
        )


def _text(value):
    return html.escape(str(value), quote=True)


def _number(value, precision=3):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 'No disponible'
    if not math.isfinite(number):
        return 'No disponible'
    return f'{number:.{precision}f}'


def _crs_text(crs):
    if crs is None or not crs.isValid():
        return 'No disponible'
    authority = crs.authid()
    description = crs.description()
    return (authority + ' - ' + description) if authority else description


def _smoothing_report_html(state, analysis=None):
    """Describe the supplied preview snapshot without changing basin results."""
    if state is None:
        return ''
    from .parameter_schema import format_value

    original_checked = bool(state.get('original_checked', False))
    smooth_checked = bool(state.get('smooth_checked', False))
    degree = _number(state.get('degree'), 3)
    if degree != 'No disponible':
        degree = degree.rstrip('0').rstrip('.').replace('.', ',')
    stats = state.get('stats') or {}
    smooth_stats = stats.get('suavizada', stats.get('smooth', stats.get('smoothed', {}))) or {}
    rows = []
    for label, selected, current in (
        ('Original', original_checked, stats.get('original') or {}),
        ('Suavizada', smooth_checked, smooth_stats),
    ):
        if not selected:
            continue
        cells = [_text(label)]
        cells.extend(_text(format_value(key, current.get(key)))
                     for key in ('area_km2', 'perimeter_km', 'kc', 'ic'))
        cells.extend(_text(_number(current.get(key), 3).replace('.', ','))
                     for key in ('delta_area_pct', 'delta_perimeter_pct'))
        rows.append('<tr bgcolor="#ffffff"><td>' + '</td><td align="right">'.join(cells) + '</td></tr>')
    table = (
        '<table class="results" width="100%" border="1" bordercolor="#d6dee5" cellspacing="0" cellpadding="3">'
        '<thead><tr bgcolor="#ffffff"><th align="left">Divisoria seleccionada</th>'
        '<th>Área (km²)</th><th>Perímetro (km)</th><th>Kc</th><th>Ic</th>'
        '<th>Variación área (%)</th><th>Variación perímetro (%)</th></tr></thead>'
        + ''.join(rows) + '</table>'
    ) if rows else '<p class="small">No hay divisorias seleccionadas.</p>'
    active = bool(analysis is not None and (getattr(analysis, 'diagnostics', {}) or {}).get('analysis_surface'))
    explanation = ('La morfología, hipsometría, P₀ y Pd se han recalculado sobre la divisoria suavizada activa.'
                   if active else 'Estas medidas describen el contorno cartográfico seleccionado; los 19 parámetros '
                   'de la cuenca original y sus cálculos sobre el terreno se mantienen.')
    return (
        '<h2>Suavizado de la divisoria</h2><p class="small">Original: '
        + ('seleccionada' if original_checked else 'sin seleccionar')
        + '; Suavizada: ' + ('seleccionada' if smooth_checked else 'sin seleccionar')
        + '; Grado de suavizado: <b>' + _text(degree) + '</b>.</p>'
        + table
        + '<p class="small">Fuente de la comparación: geometrías de la vista previa actual, '
          'en el CRS de delimitación. Las variaciones se refieren a la divisoria original. '
        + explanation + '</p>'
    )


def _map_report_html(state):
    """Embed the exact map snapshot; the PDF has no temporary image dependency."""
    if state is None or state.get('map_png') is None:
        return ''
    data = bytes(state['map_png'])
    image = QImage.fromData(data, 'PNG')
    if image.isNull() or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('La captura del mapa no es una imagen PNG válida.')
    scale = min(1.0, 820 / image.width(), 430 / image.height())
    width, height = max(1, round(image.width() * scale)), max(1, round(image.height() * scale))
    source = 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
    caption = state.get('map_caption') or 'Cuenca completa y contexto de su localización.'
    return (
        '<h2 style="page-break-before: always;">Mapa de la cuenca completa</h2>'
        '<p align="center"><img src="' + source + '" width="' + str(width)
        + '" height="' + str(height) + '"></p><p class="small">'
        + _text(caption) + '</p>'
    )


def _resolution_report_html(analysis, query, state):
    """Report only the active terrain model; alternatives are not results."""
    metadata = (state or {}).get('resolution_metadata') or {}
    rational = getattr(query, 'source_kind', 'caumax_raster') == 'rational'
    if not metadata:
        return ''
    effective = (_number(analysis.cell_width, 2) + ' × '
                 + _number(analysis.cell_height, 2)).replace('.', ',') + ' m'
    nominal = _number(metadata.get('resolution'), 2).replace('.', ',') + ' m'
    detail = ''
    try:
        resolution = max(abs(float(analysis.cell_width)), abs(float(analysis.cell_height)))
        if math.isfinite(resolution) and resolution > 0:
            detail = (
                'Mayor detalle espacial que la malla original CAUMAX de 500 m.' if resolution < 500
                else 'Malla original CAUMAX de 500 m.' if resolution == 500
                else 'Malla más gruesa que la original CAUMAX de 500 m.'
            )
    except (TypeError, ValueError, OverflowError):
        pass
    flow_note = (
        'El caudal del método racional corresponde al cálculo nativo conservado; '
        'permanece pendiente si no se había calculado. Cambiar el MDT, el cierre o el '
        'suavizado no recalcula ese caudal.' if rational else
        'El caudal consultado (' + _number(getattr(query, 'flow', None), 6)
        + ' m³/s) es el dato CAUMAX original; no se recalcula al descargar o seleccionar otro MDT.')
    if rational:
        context = getattr(query, 'rational_context', None) or {}
        transform = context.get('geotransform') or []
        if len(transform) == 6:
            detail = ('La cuenca nativa del método racional se delineó con celdas de '
                      + _number(abs(transform[1]), 2) + ' × '
                      + _number(abs(transform[5]), 2) + ' m.')
    return (
        '<h2>Resolución MDT seleccionada</h2><p class="small"><b>'
        + _text(metadata.get('label') or 'MDT activo') + '</b> · Resolución nominal: '
        + _text(nominal) + '; malla efectiva del análisis: ' + _text(effective) + '. '
        + _text(detail) + '<br>Proveedor: ' + _text(metadata.get('provider') or 'No disponible')
        + '. Fuente: ' + _text(metadata.get('source') or 'No disponible')
        + '.<br>Los resultados y el mapa corresponden exclusivamente al MDT activo. '
        + _text(flow_note) + '</p>'
    )


def _outlet_snap_report_html(analysis):
    """Explain the effective outlet using the engine's recorded decision."""
    from .parameter_schema import format_value

    snap = (getattr(analysis, 'diagnostics', {}) or {}).get('outlet_snap') or {}
    if not snap:
        return ''

    def area(key):
        try:
            value = float(snap[key]) / 1e6
        except (KeyError, TypeError, ValueError, OverflowError):
            value = None
        return _text(format_value('area_km2', value)) + ' km²'

    status = {
        'snapped': 'El cálculo utiliza una celda de cauce distinta de la celda del punto original.',
        'unchanged': 'Se mantiene la misma celda de cierre; las coordenadas indican el centro de esa celda.',
    }.get(snap.get('status'), '')
    mode = snap.get('selection_mode')
    criteria = {
        'reference_basin_overlap': (
            'Refinamiento del cierre mediante la red D8 del MDT seleccionado y la cuenca original '
            'como referencia espacial. Se exige correspondencia mayoritaria en ambas direcciones '
            'antes de elegir el centro próximo al punto consultado sobre el mismo cauce. '
            'No se impone el área de 500 m ni se sustituye el cálculo por un polígono suavizado. '
            'Si la correspondencia o la sección no son inequívocas, se solicita elección manual.'),
        'user_selected_cell': (
            'Cierre elegido expresamente por el usuario en el mapa o mediante desplazamiento por celdas. '
            'Se ha recalculado toda la cuenca y su curso principal desde ese centro. Esta elección manual '
            'puede situarse en cualquier orientación respecto al punto original; no se presenta como '
            'una selección automática aguas arriba.'),
        'upstream_main_course': (
            'Criterio de la consulta guardada en 1.2.6, basado en el curso D8 de máxima longitud: se utiliza su primer tramo aguas arriba '
            'para situar la sección del punto original. Si el centro de la celda queda aguas abajo '
            'de esa sección, se elige el siguiente centro del mismo curso y se vuelve a delimitar la cuenca.'),
        'upstream_lateral_main_course': (
            'Criterio lateral de la consulta guardada en 1.2.6: se sigue la conexión D8 del punto original para identificar '
            'el cauce receptor mediante los aportes relativos de sus confluencias. El punto original se '
            'proyecta sobre ese curso y el cierre es su primer centro de celda en la sección proyectada '
            'o aguas arriba. El receptor de la conexión lateral no se adopta automáticamente como cierre. '
            'No se exige un porcentaje del área de la malla de 500 m ni un umbral fijo de acumulación local.'),
        'upstream_nearest_branch': (
            'Criterio guardado de proximidad al ramal D8 conectado: se comparan las distancias del clic a los '
            'segmentos entrantes antes de elegir la celda. El centro se conserva cuando es admisible '
            'o se toma el primero sobre la sección proyectada o aguas arriba del ramal elegido. '
            'La longitud total de otro río no determina el cierre. El curso de máxima longitud '
            'se calcula dentro de la cuenca resultante. Una diferencia de proximidad no concluyente '
            'entre ramales requiere selección manual.'),
        'upstream_lateral_nearest_branch': (
            'Criterio guardado de asociación al ramal próximo del MDT: la conexión D8 y los aportes relativos identifican '
            'el receptor; después se comparan los segmentos conectados y se elige el ramal más '
            'próximo al clic, sin dar prioridad al de mayor longitud. El cierre es el primer centro '
            'sobre la sección proyectada o aguas arriba de ese ramal. Una asociación ambigua '
            'requiere selección manual; el área de la malla de 500 m es solo una referencia.'),
        'upstream_only': (
            'Criterio de la consulta guardada en 1.2.5: antecesores D8 dentro del semiplano '
            'definido por la dirección local; se seleccionó el centro más cercano que cumplía '
            'los filtros de acumulación. Las consultas nuevas aplican el criterio de cierre de la versión actual.'),
    }
    criterion = criteria.get(mode,
        'Criterio de la consulta guardada: celda D8 más próxima dentro del radio cuya área '
        'aportante cumple el umbral mínimo.')
    current = mode in ('upstream_main_course', 'upstream_lateral_main_course', 'user_selected_cell',
                       'upstream_nearest_branch', 'upstream_lateral_nearest_branch', 'reference_basin_overlap')
    manual_extent = mode == 'user_selected_cell' and snap.get('manual_selection_scope') == 'raster_extent'
    radius_label = ('radio del snap automático (no limita esta elección manual)' if manual_extent
                    else 'radio de búsqueda')
    if manual_extent:
        criterion += ' La selección manual está limitada por la extensión con datos válidos del MDT, no por el radio automático.'
    details = ''
    if not current:
        details += (' <b>Área mínima local:</b> ' if mode == 'upstream_only' else ' <b>Área mínima:</b> ')
        details += area('minimum_upstream_area_m2') + ';'
    details += ' <b>área de referencia:</b> ' + area('reference_area_m2')
    details += '; <b>área aportante del cierre:</b> ' + area('candidate_upstream_area_m2')
    if mode == 'reference_basin_overlap':
        details += ('; <b>referencia espacial del refinamiento:</b> ' + area('reference_geometry_area_m2')
                    + '; <b>área de la celda original:</b> ' + area('original_cell_upstream_area_m2'))
        for key, label in (('reference_coverage_fraction', 'parte de la referencia captada'),
                           ('candidate_overlap_fraction', 'parte de la cuenca fina coincidente')):
            fraction = snap.get(key)
            if isinstance(fraction, (int, float)) and math.isfinite(fraction):
                details += '; <b>' + label + ':</b> ' + _text(_number(fraction * 100, 2).replace('.', ',')) + ' %'
    if mode in ('upstream_lateral_main_course', 'upstream_lateral_nearest_branch'):
        details += ('; <b>área de la celda original:</b> ' + area('original_cell_upstream_area_m2')
                    + '; <b>área del receptor:</b> ' + area('channel_anchor_upstream_area_m2')
                    + '. La celda original puede estar en una ladera y aportar menos superficie que el cauce elegido')
    return (
        '<h2>Ajuste de la sección de cierre</h2><p class="small">'
        '<b>Punto original:</b> X = ' + _text(_number(snap.get('original_x')))
        + '; Y = ' + _text(_number(snap.get('original_y')))
        + '. <b>Cierre de cálculo (centro de celda):</b> X = ' + _text(_number(snap.get('snapped_x')))
        + '; Y = ' + _text(_number(snap.get('snapped_y')))
        + '. Coordenadas en el CRS de delimitación.<br>'
        '<b>Desplazamiento:</b> ' + _text(_number(snap.get('distance_m'), 2).replace('.', ','))
        + ' m; <b>' + _text(radius_label) + ':</b> ' + _text(_number(snap.get('search_radius_m'), 2).replace('.', ','))
        + ' m. ' + _text(status)
        + '<br>' + _text(criterion) + details
        + '.<br>El curso principal y la cuenca se calculan desde el mismo cierre. La posición se aproxima '
          'a centros de celda y no constituye una sección topográfica exacta.'
        + '<br>El caudal CAUMAX original se conserva en el punto consultado; '
          'el ajuste modifica la sección de cierre del análisis de la cuenca.</p>'
    )


def _hypsometric_report_html(analysis, state):
    from .hypsometry import curve_plot_data, render_hypsometric_png

    dimensionless = bool((state or {}).get('hypsometric_dimensionless', False))
    data = curve_plot_data(analysis, dimensionless)
    mode = 'adimensional' if dimensionless else 'dimensional'
    hi = _text('No disponible' if data['hi'] is None else
               f"{data['hi']:.2f}".rstrip('0').rstrip('.').replace('.', ','))
    if not data['available']:
        return ('<h2>Curva hipsométrica</h2><p class="small">Modo ' + mode
                + '. HI exacto: ' + hi + '. ' + _text(data['reason']) + '</p>')
    png = render_hypsometric_png(analysis, dimensionless, font_family=_pdf_font_family())
    source = 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
    axes = ('Área relativa a/A; altitud relativa (h−Hmín)/(Hmáx−Hmín).'
            if dimensionless else data['x_label'] + '; altitud (m).')
    return (
        '<h2 style="page-break-before: always;">Curva hipsométrica</h2>'
        '<p align="center"><img src="' + source + '" width="820" height="384"></p>'
        '<p class="small">Modo ' + mode + '. ' + _text(axes)
        + ' <b>HI exacto: ' + hi + '</b>, calculado con la media aritmética de todas las cotas válidas. '
        + 'La curva representa ' + str(data['sample_count']) + ' cuantiles de '
        + str(data['valid_count']) + ' cotas válidas; su integral aproximada no sustituye el HI exacto.</p>'
    )


def _flow_quantiles_report_html(state):
    """Use the portable panel snapshot and its shared interpolation renderer."""
    snapshot = (state or {}).get('flow_quantiles')
    if snapshot is None:
        return ''
    from .flow_quantiles import describe_estimates, render_quantile_plots

    estimates = describe_estimates(snapshot)
    font_family = _pdf_font_family()
    png = bytes(render_quantile_plots(snapshot, width=1100, height=500, font_family=font_family))
    if not png.startswith(b'\x89PNG\r\n\x1a\n') or QImage.fromData(png, 'PNG').isNull():
        raise ValueError('No se han podido dibujar los gráficos de caudales.')
    point = snapshot.get('point') or {}
    crs = QgsCoordinateReferenceSystem.fromWkt(str(point.get('crs_wkt') or ''))
    flows, paths = snapshot.get('flows') or [], snapshot.get('source_paths') or []
    rows = []
    for index, period in enumerate(snapshot.get('periods') or []):
        flow = flows[index] if index < len(flows) else None
        path = paths[index] if index < len(paths) else ''
        rows.append(
            '<tr bgcolor="#ffffff"><td align="center">' + _text(_number(period, 0))
            + '</td><td align="center">' + _text(_number(flow, 2).replace('.', ','))
            + '</td><td>' + _text(Path(str(path)).name if path else 'No disponible') + '</td></tr>'
        )
    source = 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
    plot_image=QImage.fromData(png,'PNG')
    plot_height=round(820*plot_image.height()/plot_image.width())
    extrapolation = ('permitida para consultas fuera del intervalo de las capas'
                     if snapshot.get('extrapolate', False) else 'desactivada')
    return (
        '<h2 style="page-break-before: always;">Caudales originales y estimaciones de periodo de retorno</h2>'
        '<p class="small">Datos de las seis capas CAUMAX en el <b>punto original</b>: X = '
        + _text(_number(point.get('x'))) + '; Y = ' + _text(_number(point.get('y')))
        + '. ' + _text(_crs_text(crs))
        + '. Estas consultas no recalculan el caudal con el nuevo MDT ni con el cierre ajustado.</p>'
        '<table class="results" width="100%" border="1" bordercolor="#d6dee5" cellspacing="0" cellpadding="3">'
        '<thead><tr bgcolor="#ffffff"><th align="center">Periodo de retorno T (años)</th>'
        '<th align="center">Caudal máximo instantáneo (Qci) [m³/s]</th><th align="left">Capa</th></tr></thead>'
        + ''.join(rows) + '</table>'
        '<p class="small"><b>T introducido:</b> '
        + _text(_number(snapshot.get('input_period'), 0).replace('.', ',')) + ' años. '
        + _text(estimates.get('forward_text') or 'Estimación no disponible.')
        + '<br><b>Q introducido:</b> '
        + _text(_number(snapshot.get('input_flow'), 2).replace('.', ',')) + ' m³/s. '
        + _text(estimates.get('inverse_text') or 'Estimación no disponible.')
        + '<br><b>Extrapolación:</b> ' + extrapolation + '.</p>'
        '<p class="small">' + _text(estimates.get('method_note') or '')
        + ' Los resultados interpolados o extrapolados son estimaciones a partir de las capas originales.</p>'
        '<p align="center"><img src="' + source + '" width="820" height="' + str(plot_height) + '"></p>'
    )


def _basin_hydrology_report_html(analysis, state):
    """Present saved polygon statistics without reopening their raster sources."""
    snapshot = (state or {}).get('basin_hydrology')
    if snapshot is None:
        return ''
    if snapshot.get('p0_result') is None and not snapshot.get('rainfall_results'):
        return ('<h2>P₀ y precipitación máxima diaria de la cuenca</h2>'
                '<p class="small">No calculados para esta cuenca. '
                'El informe no consulta los rásteres ni genera valores por defecto.</p>')
    from .basin_hydrology import basin_fingerprint
    from .parameter_schema import format_value

    title = '<h2 style="page-break-before: always;">P₀ y precipitación máxima diaria de la cuenca</h2>'
    try:
        area = float(analysis.area_m2)
        matched = (
            math.isfinite(area) and area > 0
            and snapshot.get('basin_fingerprint') == basin_fingerprint(analysis)
            and math.isclose(float(snapshot['basin_area_m2']), area, rel_tol=1e-7, abs_tol=1e-5)
        )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        matched = False
    if not matched:
        return (title + '<p class="small">Resultados no disponibles para la cuenca activa: '
                'la geometría, el CRS o el área no corresponden a los resultados guardados. '
                'No se muestran medias de otra divisoria.</p>')

    from .basin_hydrology_widget import describe_precipitation_estimates, render_precipitation_plots

    rainfall = snapshot.get('rainfall_results') or {}
    estimates = describe_precipitation_estimates(snapshot) if rainfall else {}
    notes, rows = [], []
    entries = [('P₀ bruto', snapshot.get('p0_result'))]
    if rainfall:
        entries.extend(('Pd diaria · T = ' + str(period) + ' años', rainfall.get(str(period)))
                       for period in (2, 5, 10, 25, 100, 500))
    for label, stats in entries:
        if stats is None:
            cells = [label, 'No calculado', '—', '—', '—', 'Sin calcular', '—']
        else:
            status = stats.get('status')
            mean = None if status == 'unavailable' else stats.get('mean_mm')
            coverage, valid = stats.get('coverage_fraction'), stats.get('valid_fraction')
            source = str(stats.get('source_path') or '').replace('\\', '/')
            cells = [
                label, _number(mean, 2).replace('.', ','),
                _number(None if coverage is None else float(coverage) * 100, 2).replace('.', ','),
                _number(None if valid is None else float(valid) * 100, 2).replace('.', ','),
                _number(stats.get('pixel_count'), 0),
                {'ok': 'Disponible', 'partial': 'Parcial', 'unavailable': 'No disponible'}.get(status, 'No disponible'),
                Path(source).name if source else 'No disponible',
            ]
            notes.extend(label + ': ' + str(note) for note in stats.get('notes', ()) if str(note).strip())
        formatted = [_text(cell) for cell in cells]
        if label == 'P₀ bruto' and stats is not None and stats.get('mean_mm') is not None:
            formatted[1] = '<b style="color:#C00000">' + formatted[1] + '</b>'
        rows.append('<tr bgcolor="#ffffff"><td>' + '</td><td>'.join(formatted) + '</td></tr>')
    p0 = snapshot.get('p0_result') or {}
    p0_statistics = p0.get('statistics')
    statistics_html = ''
    if p0_statistics is not None:
        statistics_html = '<p><b>Estadísticos de P₀ (mm):</b> ' + ' · '.join(
            label + ': ' + _text(_number(p0_statistics[key], 2).replace('.', ','))
            for label, key in (('Mediana', 'median_mm'), ('Máximo', 'max_mm'),
                               ('Mínimo', 'min_mm'), ('Desviación típica', 'stddev_mm'))) + '</p>'
        statistics_html += '<p class="small">Mediana y desviación típica poblacional ponderadas por el área válida de los píxeles intersectados. No se aplica β.</p>'
    rainfall_statistics=[]
    for period in (2,5,10,25,100,500):
        result=rainfall.get(str(period)) or {}
        detail=result.get('statistics')
        if detail is not None:
            rainfall_statistics.append('<b>T = '+str(period)+' años:</b> '+ ' · '.join(
                label+': '+_text(_number(detail[key],2).replace('.',','))+' mm'
                for label,key in (('Mínimo','min_mm'),('Máximo','max_mm'),('Desviación típica','stddev_mm'))))
    if rainfall_statistics:
        statistics_html += '<p><b>Estadísticos de Pd:</b><br>'+'<br>'.join(rainfall_statistics)+'</p>'
    if any(isinstance(value, dict) and value.get('mean_mm') is not None
           and value.get('status') in ('ok', 'partial') for value in rainfall.values()):
        png = bytes(render_precipitation_plots(snapshot, width=1100, height=500,
                                               font_family=_pdf_font_family()))
        if not png.startswith(b'\x89PNG\r\n\x1a\n') or QImage.fromData(png, 'PNG').isNull():
            raise ValueError('No se han podido dibujar los gráficos de precipitación.')
        image_source = 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
        plot_image=QImage.fromData(png,'PNG')
        plot_height=round(820*plot_image.height()/plot_image.width())
        plots = '<p align="center"><img src="' + image_source + '" width="820" height="' + str(plot_height) + '"></p>'
    elif rainfall:
        plots = ('<p class="small">Gráficos no disponibles: no hay medias de precipitación '
                 'calculadas con valores válidos.</p>')
    else:
        plots = ('<p class="small">Precipitación máxima diaria: no calculada para los seis periodos. '
                 'No se han generado estimaciones ni gráficos.</p>')
    observation = ('<p class="small"><b>Observaciones de las fuentes:</b><br>'
                   + '<br>'.join(_text(note) for note in dict.fromkeys(notes)) + '</p>') if notes else ''
    queries = (
        '<p class="small"><b>T introducido:</b> '
        + _text(_number(snapshot.get('input_period'), 0).replace('.', ',')) + ' años. '
        + _text(estimates.get('forward_text') or 'Estimación no disponible.')
        + '<br><b>Precipitación introducida:</b> '
        + _text(_number(snapshot.get('input_precipitation'), 2).replace('.', ',')) + ' mm. '
        + _text(estimates.get('inverse_text') or 'Estimación no disponible.')
        + '<br><b>Extrapolación:</b> '
        + ('permitida fuera del intervalo de las medias disponibles' if snapshot.get('extrapolate', False) else 'desactivada')
        + '.</p><p class="small">' + _text(estimates.get('method_note') or '') + '</p>'
    ) if rainfall else ''
    return (
        title + ('<p class="small"><b>Divisoria suavizada activa:</b> '
                 if (getattr(analysis, 'diagnostics', {}) or {}).get('analysis_surface')
                 else '<p class="small"><b>Polígono original sin suavizar de la cuenca activa:</b> ')
        + _text(format_value('area_km2', area / 1e6)) + ' km²; malla '
        + _text(_number(analysis.cell_width, 2).replace('.', ',')) + ' × '
        + _text(_number(analysis.cell_height, 2).replace('.', ','))
        + ' m. Se presentan los resultados guardados de esa divisoria, sin releer los rásteres.</p>'
        '<table class="results" width="100%" border="1" bordercolor="#d6dee5" cellspacing="0" cellpadding="3">'
        '<thead><tr bgcolor="#ffffff"><th width="23%" align="left">Dato</th>'
        '<th width="12%">Media (mm)</th><th width="12%">Cobertura (%)</th>'
        '<th width="12%">Área válida (%)</th><th width="9%">Píxeles</th>'
        '<th width="14%">Estado</th><th width="18%" align="left">Fuente</th></tr></thead>'
        + ''.join(rows) + '</table>' + statistics_html
        + '<p class="small">La media pondera el área de intersección de cada píxel con el polígono '
        'sin suavizar, medida en el CRS métrico de la cuenca, y utiliza solo los valores válidos. '
        'Cobertura = área cubierta / área total de la cuenca; área válida = área con dato válido / área total. '
        'Los píxeles intersectados incluyen los NoData; no se sustituyen los datos ausentes por cero.</p>'
        '<p class="small"><b>P₀ bruto, sin aplicar β.</b> No se aplica reducción areal a la precipitación. '
        'Pd,T es la media espacial de los cuantiles puntuales del mapa para T; '
        'no es el cuantil de una serie observada de tormentas medias sobre toda la cuenca.</p>'
        + observation + queries + plots
    )


def build_report_html(analysis, query, smoothing_state=None):
    """Build the same metrics/units used by the on-screen parameter schema."""
    from .parameter_schema import METRICS, format_value, interpret_value

    values = analysis.values
    basin_crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
    query_crs = getattr(query, 'crs', basin_crs)
    x = getattr(query, 'x', getattr(analysis, 'selected_x', None))
    y = getattr(query, 'y', getattr(analysis, 'selected_y', None))
    has_resolution = bool((smoothing_state or {}).get('resolution_metadata'))
    rational = getattr(query, 'source_kind', 'caumax_raster') == 'rational'
    flow = getattr(query, 'flow', None)
    period = getattr(query, 'period', None)
    metadata = [
        ('Fecha', datetime.now().strftime('%d/%m/%Y %H:%M')),
        ('Origen de la cuenca' if rational else 'Capa de caudales',
         'Método racional de CAUMAX · cuenca nativa ≤50 km²' if rational
         else getattr(query, 'layername', 'CAUMAX')),
        ('Caudal del método racional' if rational else
         'Caudal CAUMAX original' if has_resolution else 'Caudal consultado',
         'Pendiente de cálculo' if rational and flow is None else _number(flow, 6) + ' m³/s'),
        ('Periodo de retorno', 'Pendiente' if period is None else
         (_number(period, 6).rstrip('0').rstrip('.') if rational else _number(period, 0)) + ' años'),
        ('Punto seleccionado', 'X = ' + _number(x) + '; Y = ' + _number(y)),
        ('CRS del punto seleccionado', _crs_text(query_crs)),
        ('Centro efectivo de la celda de cierre',
         'X = ' + _number(analysis.outlet_x) + '; Y = ' + _number(analysis.outlet_y)),
        ('CRS de delimitación', _crs_text(basin_crs)),
        ('Resolución de delimitación',
         _number(analysis.cell_width, 2) + ' x ' + _number(analysis.cell_height, 2) + ' m'),
        ('Direcciones de drenaje', Path(getattr(query, 'directions_path', '')).name or 'CAUMAX'),
        ('Modelo del terreno', Path(getattr(query, 'dem_path', '')).name or 'CAUMAX'),
    ]
    metadata_rows = ''.join(
        '<tr><td width="38%"><b>' + _text(label) + '</b></td><td>' + _text(value) + '</td></tr>'
        for label, value in metadata
    )
    result_rows, definitions = [], []
    for metric in METRICS:
        rendered = format_value(metric.key, values.get(metric.key))
        interpretation = interpret_value(metric.key, values.get(metric.key), values)
        result_rows.append(
            '<tr bgcolor="#ffffff"><td width="29%">' + _text(metric.label)
            + '</td><td width="11%" align="right"><b>' + _text(rendered)
            + '</b></td><td width="8%">' + _text(metric.unit or '-')
            + '</td><td width="52%">' + _text(interpretation) + '</td></tr>'
        )
        definitions.append('<p><b>' + _text(metric.label) + '.</b> ' + _text(metric.definition) + '</p>')
    notes = list(getattr(analysis, 'notes', ()) or ())
    diagnostics = getattr(analysis, 'diagnostics', {}) or {}
    for key, diagnostic, label in (
        ('tc_h', 'tc_candidate_h', 'Tc(L)'),
        ('tc_10_85_h', 'tc_10_85_candidate_h', 'Tc(L10-85)'),
    ):
        candidate = diagnostics.get(diagnostic)
        if values.get(key) is None and candidate is not None:
            notes.append(
                'El valor candidato de la fórmula de ' + label + ' es ' + _number(candidate, 6)
                + ' h. No se presenta como un Tc normativo válido; requiere verificar '
                  'la aplicabilidad y, cuando corresponda, calcular el recorrido por tramos.'
            )
    if getattr(analysis, 'touches_boundary', False):
        notes.append('La cuenca alcanza el borde de los datos. Debe comprobarse si la delimitación está completa.')
    unique_notes = list(dict.fromkeys(str(note) for note in notes if str(note).strip()))
    notes_html = (
        '<h2>Observaciones del cálculo</h2><ul class="small">'
        + ''.join('<li>' + _text(note) + '</li>' for note in unique_notes) + '</ul>'
    ) if unique_notes else ''
    course_rows = []
    for key, label, unit in (
        ('basin_length_km', 'Longitud recta máxima cierre-divisoria utilizada en Re', 'km'),
        ('zmean_m', 'Media aritmética de cotas utilizada en el índice hipsométrico', 'm'),
        ('main_length_km', 'Longitud total L del curso principal utilizada en Tc(L)', 'km'),
        ('tc_path_slope', 'Pendiente del curso principal utilizada en Tc(L)', 'm/m'),
        ('main_length_10_85_km', 'Longitud L10-85 utilizada en Tc(L10-85)', 'km'),
        ('main_slope_10_85', 'Pendiente del mismo tramo utilizada en Tc(L10-85)', 'm/m'),
    ):
        course_rows.append(
            '<tr><td width="70%">' + _text(label) + '</td><td align="right">'
            + _number(values.get(key), 6) + '</td><td>' + unit + '</td></tr>'
        )
    smoothing_html = _smoothing_report_html(smoothing_state, analysis=analysis)
    map_html = _map_report_html(smoothing_state)
    resolution_html = _resolution_report_html(analysis, query, smoothing_state)
    outlet_snap_html = _outlet_snap_report_html(analysis)
    hypsometric_html = _hypsometric_report_html(analysis, smoothing_state)
    quantiles_html = '' if rational else _flow_quantiles_report_html(smoothing_state)
    basin_hydrology_html = _basin_hydrology_report_html(analysis, smoothing_state)
    source_summary = ('Cuenca y resultado nativos: método racional de CAUMAX 3.0. '
                      'El caudal conserva las entradas del cálculo original y no se recalcula al modificar '
                      'la resolución, el cierre o el suavizado en este diálogo. ' if rational else
                      'Caudal original: CAUMAX 3.0, CEDEX / MITECO. ')
    resolution_metadata = (smoothing_state or {}).get('resolution_metadata') or {}
    engine = str((diagnostics.get('dem_hydrology') or {}).get('engine') or '').strip()
    if resolution_metadata:
        source_summary += ('MDT activo: ' + str(resolution_metadata.get('provider') or 'proveedor indicado')
                           + '; fuente y resolución detalladas en «Resolución MDT seleccionada». ')
    elif engine:
        source_summary += 'MDT activo: archivo indicado en los datos de consulta. '
    else:
        source_summary += 'MDT original: CAUMAX. '
    if engine:
        source_summary += 'Direcciones D8: ' + engine + ' sobre el MDT activo.'
    elif resolution_metadata and str(resolution_metadata.get('active_key', '500')) != '500':
        source_summary += 'Direcciones D8: procedencia no registrada en esta consulta.'
    else:
        source_summary += 'Direcciones D8 originales: CAUMAX.'
    title = 'INFORME DE CONSULTA' if smoothing_state is not None else 'CUENCA HIDROGRÁFICA'
    surface_description = (
        'Los parámetros corresponden a la divisoria SUAVIZADA activa. Morfología, hipsometría, P₀ y Pd '
        'se recalculan automáticamente al cambiar el grado. Las estadísticas del terreno se ponderan '
        'por el área exacta de intersección de cada celda. Los recorridos D8 se vuelven a medir con '
        'las direcciones nativas, sin alterar el MDT ni los caudales oficiales del punto.'
        if diagnostics.get('analysis_surface') else
        'Los parámetros corresponden a la cuenca ORIGINAL de celdas. '
        'La resolución del modelo condiciona la delimitación, el perímetro y los índices. '
        'Hmed es la altitud mediana (percentil 50); el índice hipsométrico utiliza la media aritmética de cotas.')
    if rational:
        surface_description = surface_description.replace(
            'los caudales oficiales del punto', 'el resultado nativo del método racional')
    return '''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body {font-size: 9pt; color: #1e2d38;}
h1 {font-size: 19pt; color: #154e72; margin-bottom: 3pt;}
h2 {font-size: 12pt; color: #154e72; margin-top: 14pt; margin-bottom: 5pt;}
p {margin-top: 5pt; margin-bottom: 5pt;}
table {border-collapse: collapse; background-color: white;}
th {background-color: white; color: #1e2d38;}
td {vertical-align: top;}
.results {font-size: 8.5pt;}
.small {font-size: 8pt; color: #415562;}
.method {font-size: 8pt;}
</style></head><body>
<h1>''' + title + '''</h1>
<p><b>HidroCAUMAX v1.2.12 - Parámetros morfológicos e hidrológicos</b></p>
<table width="100%" cellspacing="0" cellpadding="3">''' + metadata_rows + '''</table>
''' + resolution_html + '''
''' + outlet_snap_html + '''
<h2>Morfología de la Cuenca</h2>
<table class="results" width="100%" border="1" bordercolor="#d6dee5" cellspacing="0" cellpadding="4">
<thead><tr bgcolor="#ffffff"><th align="left">Parámetro</th><th align="right">Valor</th><th align="left">Unidad</th><th align="left">Interpretación</th></tr></thead>
''' + ''.join(result_rows) + '''</table>
<p class="small">''' + _text(surface_description) + '''</p>
''' + hypsometric_html + '''
''' + quantiles_html + '''
''' + basin_hydrology_html + '''
''' + notes_html + '''
<div class="method"><h2>Definiciones y criterios de cálculo</h2>
''' + ''.join(definitions) + '''
<h2>Curso principal y tiempo de concentración</h2>
<table width="100%" cellspacing="0" cellpadding="4">''' + ''.join(course_rows) + '''</table>
<p>Los percentiles 10 % y 85 % son distancias recorridas desde la sección de cierre,
con interpolación sobre el perfil longitudinal D8. El curso principal es el recorrido D8
de mayor longitud. Tc(L) se calcula sobre su longitud total y su propia pendiente;
Tc(L10-85) se calcula sobre el tramo comprendido entre el 10 % y el 85 % y la pendiente
de ese mismo tramo. Ninguno se sustituye por el máximo entre otras ramas.
Identificar el Tc global normativo exige tantear los recorridos candidatos; el curso
de mayor longitud no garantiza por sí solo el mayor tiempo.</p>
<p><b>Norma 5.2-IC, apartado 2.2.2.5:</b> Tc = 0,3 x Lc^0,76 x Jc^(-0,19),
con Tc en horas, Lc en km y Jc en m/m. En esta comparación se aplica la misma fórmula
a los dos recorridos indicados, con sus respectivas longitudes y pendientes.
Tc(L10-85) es una comparación sobre ese tramo; no sustituye al Tc de la cuenca completa.
Si un valor candidato resulta menor o igual que 0,25 h, no se presenta
como Tc válido de cuenca principal: deben verificarse los tramos de flujo difuso y canalizado.
Las pendientes nulas, los desniveles no positivos y los recorridos con cotas ausentes no
se sustituyen por valores arbitrarios.</p>
''' + smoothing_html + '''
<h2>Fuentes y referencia</h2>
<p class="small">''' + _text(source_summary) + '''<br>
BOE-A-2016-2405, Orden FOM/298/2016, Norma 5.2-IC de Drenaje superficial, apartado 2.2.2.5.<br>
https://www.boe.es/buscar/doc.php?id=BOE-A-2016-2405<br>
TRI de Riley: raíz de la suma de los cuadrados de las diferencias de cota entre una celda
y sus ocho vecinos; se promedian los valores válidos de la cuenca.<br>
https://gdal.org/en/stable/programs/gdal_raster_tri.html</p>
</div>''' + map_html + '''</body></html>'''


def _pdf_font_family():
    """Do not let an empty offscreen Qt font database silently print boxes."""
    database = QFontDatabase()
    families = set(database.families())
    preferred = ('Segoe UI', 'Arial', 'DejaVu Sans', 'Liberation Sans')
    for family in preferred:
        if family in families:
            _load_bold_font(database, family)
            return family
    candidates = [
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeui.ttf',
        Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf',
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'),
    ]
    for candidate in candidates:
        if candidate.is_file():
            font_id = database.addApplicationFont(str(candidate))
            loaded = database.applicationFontFamilies(font_id)
            if loaded:
                _load_bold_font(database, loaded[0])
                return loaded[0]
    if families:
        return sorted(families)[0]
    raise OSError('No se ha podido cargar una fuente legible para generar el PDF.')


def _load_bold_font(database, family):
    if any(database.bold(family, style) for style in database.styles(family)):
        return
    windows = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
    candidates = {
        'Segoe UI': (windows / 'segoeuib.ttf',),
        'Arial': (windows / 'arialbd.ttf',),
        'DejaVu Sans': (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),),
        'Liberation Sans': (Path('/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'),),
    }
    for path in candidates.get(family, ()):
        if path.is_file():
            database.addApplicationFont(str(path))


def _write_document(path, content):
    """Qt-only PDF backend: available in the same Qt 5 runtime as QGIS."""
    printer = QPrinter(QPrinter.HighResolution)
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setDocName('HidroCAUMAX v1.2.12 - Cuenca hidrográfica')
    printer.setCreator('HidroCAUMAX v1.2.12')
    printer.setFontEmbeddingEnabled(True)
    printer.setPaperSize(QPrinter.A4)
    printer.setOrientation(QPrinter.Landscape)
    printer.setPageMargins(13, 13, 13, 13, QPrinter.Millimeter)
    document = QTextDocument()
    document.setDefaultFont(QFont(_pdf_font_family(), 9))
    document.setHtml(content)
    document.print_(printer)
    if printer.printerState() == QPrinter.Error:
        raise OSError('El sistema de impresión no pudo generar el PDF.')
    # Release Qt's PDF device before validation, replacement, or viewer launch.
    del document
    del printer


def _valid_pdf(path):
    try:
        with open(path, 'rb') as stream:
            header = stream.read(5)
            stream.seek(max(0, os.fstat(stream.fileno()).st_size - 2048))
            ending = stream.read()
        return header == b'%PDF-' and b'%%EOF' in ending and Path(path).stat().st_size > 100
    except OSError:
        return False


def generate_basin_pdf(destination, analysis, query, open_after=True, smoothing_state=None):
    """Write a complete PDF atomically and then request the desktop PDF viewer.

    Existing destinations are replaced only after a valid new PDF is complete.
    A PdfOpenError explicitly retains the successfully generated path.
    """
    destination = Path(os.fspath(destination)).expanduser()
    if destination.suffix.lower() != '.pdf':
        destination = Path(str(destination) + '.pdf')
    destination = destination.absolute()
    if not destination.parent.is_dir():
        raise ValueError('La carpeta de destino del PDF no existe.')
    if destination.is_dir():
        raise ValueError('El destino del PDF es una carpeta.')
    content = build_report_html(analysis, query, smoothing_state=smoothing_state)
    with tempfile.NamedTemporaryFile(prefix='.caumax_informe_', suffix='.pdf',
                                     dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        _write_document(temporary, content)
        if not _valid_pdf(temporary):
            raise OSError('No se generó un archivo PDF completo y válido.')
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    if open_after and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination))):
        raise PdfOpenError(destination)
    return destination


def ask_basin_pdf(parent, analysis, query, smoothing_state=None):
    """Select a report filename and keep save/open outcomes distinct."""
    directory = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    suggested = os.path.join(directory, 'Informe_cuenca_HidroCAUMAX.pdf')
    destination, _ = QFileDialog.getSaveFileName(
        parent, 'Informe de la cuenca hidrográfica', suggested, 'Archivos PDF (*.pdf)',
    )
    if not destination:
        return None
    selected = Path(destination)
    effective = selected if selected.suffix.lower() == '.pdf' else Path(str(selected) + '.pdf')
    # The native chooser cannot confirm a different filename created by our
    # extension normalization when its returned path did not end in .pdf.
    if effective != selected and effective.exists():
        answer = QMessageBox.question(
            parent, 'CAUMAX - Sustituir PDF',
            'Ya existe el archivo PDF:\n' + str(effective) + '\n¿Quieres sustituirlo?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return None
    try:
        return generate_basin_pdf(effective, analysis, query, open_after=True,
                                  smoothing_state=smoothing_state)
    except PdfOpenError as error:
        QMessageBox.warning(parent, 'CAUMAX - PDF guardado', str(error))
        return error.path
    except Exception as error:
        QMessageBox.critical(parent, 'CAUMAX - Informe PDF', str(error))
        return None
