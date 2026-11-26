"""Use CAUMAX's regional mean and localize its recommendation window."""
import math
import re

from qgis.PyQt.QtCore import QEvent, QObject
from qgis.PyQt.QtWidgets import QApplication, QMessageBox


TITLE = 'Valores recomendados del coeficiente corrector de P0'


def apply_recommended_mean(dialog):
    # Read the same native fields and point as showRecommendedValues().
    method = getattr(dialog, 'showRecommendedValues', None) or dialog.factorsFromRainfall
    namespace = method.__func__.__globals__
    point = dialog.outletPoint()
    if point is None:
        raise ValueError('Selecciona un punto de cálculo válido para obtener el coeficiente de P0.')
    sample, zones = namespace['getValueAtPoint'], namespace['ZONES']
    if sample(zones, point, namespace['ZONE_NAME_FIELD']) is None:
        raise ValueError('El punto no pertenece a una región con coeficiente de P0 recomendado.')
    raw = sample(zones, point, namespace['ZONE_BETA'])
    try:
        value = float(raw)
    except (ValueError, TypeError):
        raise ValueError('No hay un valor medio recomendado de P0 para este punto.')
    if not math.isfinite(value) or value < 0:
        raise ValueError('El valor medio recomendado de P0 no es válido.')
    # Match the displayed two-decimal recommendation. The existing comma
    # presentation keeps float-compatible dot text in the native calculator.
    dialog.txtP0Corrector.setText(f'{value:.2f}')
    return value


class RecommendedP0Presentation(QObject):
    def start(self):
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def stop(self):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def eventFilter(self, widget, event):
        if (event.type() == QEvent.Show and isinstance(widget, QMessageBox)
                and widget.windowTitle() == TITLE and 'Valor medio:' in widget.text()):
            widget.setText(re.sub(r'(?<=\d)\.(?=\d)', ',', widget.text()))
        return False
