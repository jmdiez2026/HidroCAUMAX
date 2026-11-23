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
    Metric('main_length_10_85_km', 'Longitud del curso principal entre el 10% y el 85% (L10-85)', 'km', 2, 'Tramo entre 0,10·L y 0,85·L, medido sobre el curso principal desde el cierre hacia la cabecera. Longitud igual a 0,75·L; no usa el suavizado cartográfico de la divisoria.'),
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
            return 'No disponible'
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            return 'No disponible'
        return f'{_short(width, 2)}mx{_short(height, 2)}m'
    if value is None:
        return 'No disponible'
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 'No disponible'
    if not math.isfinite(number):
        return 'No disponible'
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
    """Short practical readings without unsupported flood-risk categories."""
    values = values or {}
    if format_value(key, value) == 'No disponible':
        if key == 'hypsometric' and values.get('relief_m') == 0:
            return 'No definido en una cuenca sin desnivel.'
        return 'No evaluable; consulta las observaciones del cálculo.'
    if key == 'cell_resolution':
        width, height = (float(v) for v in value)
        return f'Cada celda representa {_short(width * height / 10000, 2)} ha; condiciona el detalle del cálculo.'
    number = float(value)
    if key == 'area_km2':
        return f'Superficie aportante equivalente a {_short(number * 100, 1)} ha.'
    if key == 'perimeter_km':
        return 'Longitud del límite de la cuenca; sensible a la resolución de la malla.'
    if key == 'cell_count':
        return 'Celdas de la malla que aportan superficie a la cuenca activa.'
    if key == 'elongation_ratio':
        return f'Diámetro equivalente: {_short(number * 100)} % de Lb; un valor menor indica más alargamiento.'
    if key == 'kc':
        return f'Contorno {_short((number - 1) * 100)} % más largo que un círculo de igual área.'
    if key == 'ic':
        return f'Área equivalente al {_short(number * 100)} % del círculo de igual perímetro.'
    if key == 'melton':
        return f'Desnivel igual al {_short(number * 100)} % de √área; mayor valor, relieve relativo más marcado.'
    if key == 'slope_d8_pct':
        return f'Descenso local medio de {_short(number, 2)} m por cada 100 m horizontales en D8.'
    if key == 'hypsometric':
        return f'Cota media al {_short(number * 100)} % del intervalo entre Hmín y Hmáx.'
    if key == 'tri_m':
        return f'Contraste local medio (RMS): {_short(number / math.sqrt(8), 2)} m; mayor valor, terreno más irregular.'
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
        return f'Unos {_short(number * 60, 0)} min para el curso completo; estimación con la fórmula 5.2-IC.'
    if key == 'tc_10_85_h':
        return f'Unos {_short(number * 60, 0)} min para el tramo central; comparación con Tc(L).'
    return ''
