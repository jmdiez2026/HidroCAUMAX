"""Capture the charts that actually have results when the user saves or prints.

The screenshots are optional portable artefacts. Computed values remain
authoritative in the validated JSON; charts never replace missing calculations.
"""
from qgis.PyQt.QtCore import QBuffer, QIODevice, Qt
from qgis.PyQt.QtGui import QImage, QPainter

MAX_CHART_PNG_BYTES = 5 * 1024 * 1024


def _render_widget_png(widget):
    """Render the complete graph, not a crop of the current scroll viewport."""
    width, height = int(widget.width()), int(widget.height())
    if not (64 <= width <= 4096 and 64 <= height <= 2048):
        raise ValueError('El gráfico no tiene dimensiones válidas para el PDF.')
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    try:
        widget.render(painter)
    finally:
        painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise OSError('No se pudo conservar una captura PNG de la gráfica.')
    payload = bytes(buffer.data())
    if len(payload) > MAX_CHART_PNG_BYTES:
        raise ValueError('Una captura de gráfica supera el tamaño permitido.')
    return payload


def capture_available_charts(dialog):
    """Return all currently computed graph surfaces in on-screen order."""
    items = []
    hypso = getattr(dialog, 'hypsometry_widget', None)
    if hypso is not None and hypso.plot_data().get('available'):
        items.append(('hypsometric', 'Curva Hipsométrica', hypso.canvas))
    quantiles = getattr(dialog, 'quantiles_widget', None)
    if quantiles is not None and quantiles.snapshot() is not None:
        items.append(('qci_return', 'Gráficos Qci–T y T–Qci', quantiles.plots))
        course = quantiles.course_profile
        if getattr(course, '_rows', None):
            items.append(('main_course_qci',
                          'Caudal Máximo (Qci-n) a lo largo del curso principal',
                          course.graph))
        # Future computed longitudinal panels should be captured as well.
        for key, title, attribute in (
            ('specific_contribution', 'Aportación Específica (m³/s·km²)', 'specific_graph'),
        ):
            extra = getattr(course, attribute, None)
            # A fully computed panel must appear in the report even when
            # its parent is temporarily hidden by scrolling or a reopened
            # consultation has not yet been shown on the desktop.
            computed = getattr(course, '_specific_values', ())
            if (extra is not None and computed
                    and len(computed) == len(getattr(course, '_rows', ()))):
                items.append((key, title, extra))
    hydrology = getattr(dialog, 'basin_hydrology_widget', None)
    if hydrology is not None:
        state = hydrology.snapshot() or {}
        rainfall = state.get('rainfall_results') or {}
        if any(isinstance(v, dict) and v.get('mean_mm') is not None
               and v.get('status') in ('ok', 'partial')
               for v in rainfall.values()):
            items.append(('basin_precipitation',
                          'Precipitación máxima diaria: gráficos Pd–T y T–Pd',
                          hydrology.plots))
    return [
        {'key': key, 'title': title, 'png': _render_widget_png(widget)}
        for key, title, widget in items
    ]
