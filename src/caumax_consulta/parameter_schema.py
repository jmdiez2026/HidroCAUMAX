"""One parameter catalogue for the screen and the PDF."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    precision: int
    definition: str


METRICS = (
    Metric('area_km2', 'Área de la cuenca', 'km²', 2, 'Número de celdas vertientes por la superficie de cada celda, sin suavizado.'),
    Metric('perimeter_km', 'Perímetro', 'km', 2, 'Longitud de todos los contornos de la divisoria original de celdas, incluidos sus huecos.'),
    Metric('cell_count', 'Número de celdas', '', 0, 'Celdas incluidas en el análisis del MDT activo. Con divisoria suavizada se cuentan las celdas intersectadas y las estadísticas se ponderan por su superficie dentro del contorno.'),
    Metric('cell_resolution', 'Resolución celda', '', 0, 'Anchura × altura de la celda de drenaje en metros; resolución original utilizada en la delimitación y el análisis.'),
    Metric('elongation_ratio', 'Relación de elongación', '', 2, 'Re = 2·√(A/π)/Lb; Lb es la mayor distancia recta del centro de cierre a un vértice exterior de la divisoria.'),
    Metric('kc', 'Compacidad de Gravelius · Kc', '', 2, 'Kc = P/(2·√(π·A)), con A en m² y P en m. Normalización exacta del círculo.'),
    Metric('ic', 'Índice de circularidad · Ic', '', 2, 'Ic = 4·π·A/P² = 1/Kc², con área y perímetro en unidades coherentes. Adimensional.'),
    Metric('melton', 'Índice de rugosidad de Melton', '', 2, 'Melton = (z máxima − z mínima)/√A, con desnivel en m y área en m².'),
    Metric('slope_d8_pct', 'Pendiente media de la cuenca (D8)', '%', 2, 'Media de las pendientes locales D8 según la definición de TauDEM: máximo descenso de cota dividido por la distancia horizontal a uno de ocho vecinos, incluida la distancia diagonal. Esta estadística utiliza las cotas originales del MDT; se excluyen NoData, bordes sin vecindario completo y mínimos de depresiones sin salida. La procedencia identifica el motor que generó las direcciones de drenaje.'),
    Metric('hypsometric', 'Índice hipsométrico', '', 2, 'HI = (z media − z mínima)/(z máxima − z mínima). La media se pondera por superficie cuando hay fracciones de celda. Indefinido cuando el relieve es cero.'),
    Metric('tri_m', 'Índice de Irregularidad del Terreno TRI (Riley)', 'm', 2, 'Media de √Σ(z centro − z vecino)² sobre los ocho vecinos de cada celda válida. Sin interpolar NoData.'),
    Metric('relief_m', 'Desnivel máximo (Hmáx - Hmín)', 'm', 2, 'Diferencia entre las cotas máxima y mínima válidas del MDT dentro de la cuenca.'),
    Metric('zmax_m', 'Altitud máxima (Hmáx)', 'm', 2, 'Máxima cota válida del MDT dentro de las celdas de la cuenca.'),
    Metric('zmin_m', 'Altitud mínima (Hmín)', 'm', 2, 'Mínima cota válida del MDT dentro de las celdas de la cuenca.'),
    Metric('zmedian_m', 'Altitud mediana (Hmed · P50)', 'm', 2, 'Percentil 50 de las cotas válidas de la cuenca; ponderado por superficie en la divisoria suavizada. La media de cotas se conserva para el índice hipsométrico.'),
    Metric('main_length_km', 'Longitud del curso principal total (L)', 'km', 2, 'Trayectoria D8 de mayor longitud desde el centro de cierre hasta una celda de cabecera. Longitud horizontal, incluidos los pasos diagonales.'),
    Metric('main_length_10_85_km', 'Longitud curso principal 10%-85% (L10-85)', 'km', 2, 'Tramo entre 0,10·L y 0,85·L, medido sobre el curso principal desde el cierre hacia la cabecera. Longitud igual a 0,75·L; no usa el suavizado cartográfico de la divisoria.'),
    Metric('tc_h', 'Tiempo de concentración (L)', 'h', 2, 'Fórmula del apartado 2.2.2.5 de 5.2-IC: Tc = 0,3·L^0,76·J^−0,19, con L en km, cotas en m y J = (Hcabecera − Hcierre)/(1000·L) en m/m. Se aplica al mismo curso principal de máxima longitud, no al máximo entre otras ramas. Tc ≤ 0,25 h requiere cálculo por tramos y se muestra como no disponible.'),
    Metric('tc_10_85_h', 'Tiempo de concentración (L10-85)', 'h', 2, 'Comparación de la misma fórmula sobre el tramo central: Tc10-85 = 0,3·L10-85^0,76·J10-85^−0,19; J10-85 = (H85 − H10)/L10-85, con longitud en km en la fórmula y en m al calcular J. No sustituye el Tc de la cuenca completa. Se aplica el mismo control de disponibilidad de 0,25 h.'),
)

BY_KEY = {metric.key: metric for metric in METRICS}


def format_value(key, value):
    """Spanish numeric text, without unit; undefined values are never zero."""
    if key == 'cell_resolution':
        try:
            width, height = (float(v) for v in value)
        except (TypeError, ValueError, OverflowError):
            return 'n.d.'
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            return 'n.d.'
        return f'{_short(width, 2)}mx{_short(height, 2)}m'
    if value is None:
        return 'n.d.'
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 'n.d.'
    if not math.isfinite(number):
        return 'n.d.'
    metric = BY_KEY[key]
    if metric.precision > 0 and 0 < number < 0.01:
        # A positive value below the display precision must not look like zero.
        # This is presentation only; the underlying measurements are unchanged.
        return '<0,01'
    return f'{number:.{metric.precision}f}'.replace('.', ',')


def _short(value, precision=1):
    if precision == 0:
        return f'{value:.0f}'
    return f'{value:.{precision}f}'.rstrip('0').rstrip('.').replace('.', ',')


def interpret_value(key, value, values=None):
    """Deterministic readings; classroom categories never change measured values."""
    values = values or {}
    if format_value(key, value) == 'n.d.':
        if key == 'hypsometric' and values.get('relief_m') == 0:
            return 'No definido en una cuenca sin desnivel.'
        return 'No evaluable; consulta las observaciones del cálculo.'
    if key == 'cell_resolution':
        width, height = (float(v) for v in value)
        if width * height < 10000:
            area_text = '<0,01' if width * height < .01 else _short(width * height, 2)
            return f'Cada celda representa {area_text} m²; condiciona el detalle del cálculo.'
        return f'Cada celda representa {_short(width * height / 10000, 2)} ha; condiciona el detalle del cálculo.'
    number = float(value)
    if key not in ('zmax_m', 'zmin_m', 'zmedian_m') and number < 0:
        return 'No evaluable: valor fuera del dominio del indicador.'
    if key == 'area_km2':
        return f'Superficie aportante equivalente a {_short(number * 100, 1)} ha.'
    if key == 'perimeter_km':
        return 'Longitud del límite de la cuenca; sensible a la resolución de la malla.'
    if key == 'cell_count':
        return 'Celdas de la malla que aportan superficie a la cuenca activa.'
    if key == 'elongation_ratio':
        area, length = _positive(values.get('area_km2')), _positive(values.get('main_length_km'))
        measured = f'Diámetro equivalente: {_short(number * 100)} % de Lb recta.'
        if area is None or length is None:
            return measured + ' La clasificación del tema necesita la longitud del cauce L.'
        course_ratio = 2 * math.sqrt(area / math.pi) / length
        if course_ratio > 1:
            return measured + f' Re(L)={_short(course_ratio, 2)}: fuera del intervalo docente hasta 1; revisar recorrido y cierre.'
        if course_ratio < .5:
            category, interval = 'Muy alargada', 'Re(L)<0,50'
        elif course_ratio < .7:
            category, interval = 'Alargada', '0,50≤Re(L)<0,70'
        elif course_ratio < .8:
            category, interval = 'Moderadamente alargada', '0,70≤Re(L)<0,80'
        elif course_ratio <= .9:
            category, interval = 'Subcircular', '0,80≤Re(L)≤0,90'
        else:
            category, interval = 'Circular', '0,90<Re(L)≤1'
        return f'{category} según el cauce: Re(L)={_short(course_ratio, 2)} ({interval}). ' + measured
    if key == 'kc':
        if number < 1:
            return 'No evaluable: Kc debe ser ≥1; revisar área y perímetro.'
        if number <= 1.25:
            category, interval, response = 'Compacta', '1≤Kc≤1,25', 'tendencia geométrica a concentrar aportaciones más simultáneas'
        elif number <= 1.5:
            category, interval, response = 'Transición', '1,25<Kc≤1,50', 'forma intermedia entre compacta y alargada'
        elif number <= 1.75:
            category, interval, response = 'Alargada y estrecha', '1,50<Kc≤1,75', 'tendencia geométrica a escalonar aportaciones'
        else:
            category, interval, response = 'Delgada, sinuosa o muy asimétrica', 'Kc>1,75', 'contrastar la forma con la divisoria y la red'
        return (f'{category} ({interval}): {response}. '
                f'Contorno {_short((number - 1) * 100)} % más largo que un círculo de igual área.')
    if key == 'ic':
        if not 0 < number <= 1:
            return 'No evaluable: Ic debe estar entre 0 y 1.'
        return f'Área equivalente al {_short(number * 100)} % del círculo de igual perímetro.'
    if key == 'melton':
        if number < .1:
            category, interval = 'muy baja', 'MRN<0,10'
        elif number <= .2:
            category, interval = 'moderada', '0,10≤MRN≤0,20'
        elif .3 <= number <= .6:
            category, interval = 'alta', '0,30≤MRN≤0,60'
        elif number > 4:
            category, interval = 'torrencial extrema', 'MRN>4'
        else:
            return (f'Desnivel igual al {_short(number * 100)} % de √área. '
                    'Intervalo sin clase en la tabla docente; contrastar con pendiente y Tc.')
        return (f'Clase docente «{category}» ({interval}); desnivel igual al {_short(number * 100)} % de √área. '
                'Lectura conjunta con pendiente y Tc.')
    if key == 'slope_d8_pct':
        return f'Descenso local medio de {_short(number, 2)} m por cada 100 m horizontales en D8.'
    if key == 'hypsometric':
        if not 0 <= number <= 1:
            return 'No evaluable: HI debe estar entre 0 y 1.'
        if number in (.35, .6):
            category = f'Límite entre clases docentes (HI={_short(number, 2)})'
        elif number < .35:
            category = 'Distribución baja: clase docente «vieja» (HI<0,35)'
        elif number < .6:
            category = 'Distribución intermedia: clase docente «madura» (0,35<HI<0,60)'
        else:
            category = 'Distribución alta: clase docente «joven» (HI>0,60)'
        return category + f'. Cota media al {_short(number * 100)} % del intervalo entre Hmín y Hmáx.'
    if key == 'tri_m':
        base = f'Contraste local medio (RMS): {_short(number / math.sqrt(8), 2)} m.'
        resolution = values.get('cell_resolution')
        try:
            width, height = map(float, resolution)
            valid = all(math.isfinite(n) and n > 0 for n in (width, height))
        except (TypeError, ValueError):
            valid = False
        if valid:
            return base + f' Ventana de {_short(3*width, 2)}×{_short(3*height, 2)} m: comparar terrenos a esta misma resolución.'
        return base + ' Comparar terrenos con igual resolución y algoritmo.'
    if key == 'relief_m':
        return 'Rango vertical entre las cotas extremas de la cuenca.'
    if key == 'zmax_m':
        return 'Extremo superior del relieve, según las cotas válidas del MDT.'
    if key == 'zmin_m':
        return 'Extremo inferior del relieve; puede diferir de la cota del cierre.'
    if key == 'zmedian_m':
        return 'El 50 % de la superficie válida tiene una altitud igual o inferior.'
    if key == 'main_length_km':
        return 'Recorrido D8 más largo hasta el cierre; base del cálculo de Tc(L).'
    if key == 'main_length_10_85_km':
        return '75 % central del curso principal; excluye sus tramos extremos.'
    if key == 'tc_h':
        if number <= .25:
            return 'Tc≤0,25 h: requiere revisar el cálculo por tramos antes de utilizar esta estimación.'
        return f'Unos {_short(number * 60, 0)} min para el curso completo; duración de referencia para evaluar la intensidad de lluvia.'
    if key == 'tc_10_85_h':
        return f'Unos {_short(number * 60, 0)} min para el tramo central; comparación con Tc(L).'
    return ''


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def interpretation_reference(key):
    """Provenance and applicability shown in tooltips and report definitions."""
    source = 'Tema Cuenca Hidrográfica (material docente facilitado por el usuario)'
    details = {
        'elongation_ratio': ('40', 'El tema usa Re(L)=2√(A/π)/L del cauce, diferente de Re(Lb) de la fila. Se deriva Re(L) solo si A y L son válidos. Límites compartidos: 0,50 y 0,70 y 0,80 inician clase; 0,90 pertenece a subcircular. Se clasifica sin redondear.'),
        'kc': ('41', 'Se conserva P/(2√(πA)); 0,282 es su aproximación. Los límites 1,25, 1,50 y 1,75 se incluyen en la clase inferior. Las tendencias de forma se interpretan manteniendo iguales las demás condiciones, no como un caudal calculado.'),
        'ic': ('41', 'Ic=1/Kc²: es una expresión del mismo contraste de forma, no una evidencia independiente.'),
        'melton': ('44', 'La tabla omite 0,20<MRN<0,30 y 0,60<MRN≤4. Se mantienen sin clase. Para 0,409 se usa la tabla (alta), no la etiqueta contradictoria del ejemplo (muy fuerte). La clase docente no determina por sí sola peligrosidad ni transporte de sedimentos.'),
        'hypsometric': ('54', 'Se conserva la media de cotas, no la mediana P50. 0,35 y 0,60 se presentan como límites. Joven/madura/vieja son analogías geomorfológicas docentes, no una datación ni una medida de erosión actual.'),
        'tri_m': ('55–56', 'Se calcula TRI de Riley (raíz de la suma de ocho cuadrados), promediado en la cuenca. La escala clásica y la escala local 0–4/4–7/7–10/>10 no son universales: esta última corresponde solo a la cuenca ejemplo. Se informa el contraste y la escala de la ventana sin trasladar esas clases a otras cuencas.'),
        'slope_d8_pct': ('45–49', 'Media de descensos locales D8; se conserva el algoritmo y el MDT del cálculo. El tema no proporciona clases numéricas de pendiente. No se sustituye con ella la pendiente longitudinal utilizada en Tc.'),
        'tc_h': ('38–39', 'Se interpreta el Tc del recorrido D8 de mayor longitud. Se conserva la fórmula y sus controles de disponibilidad; no se cambia automáticamente al tramo central.'),
        'tc_10_85_h': ('36–39', 'Tramo central del mismo recorrido, para comparación. Su selección como representativo requiere criterio del estudio; no reemplaza automáticamente Tc(L).'),
    }
    if key in details:
        pages, detail = details[key]
        return f'{source}, págs. {pages}. {detail}'
    return 'Lectura cuantitativa del dato calculado y de su definición; sin rangos categóricos inventados.'
