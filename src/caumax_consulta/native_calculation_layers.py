"""Populate the native layer chooser without changing CAUMAX's source or formulas."""
import sys
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtWidgets import QApplication, QDialog
from qgis.core import Qgis

from .improved_storage import latest_pair, po_path


def _native_module(dialog):
    for cls in type(dialog).__mro__:
        if cls.__name__ == 'LayersDialog' and cls.__module__.endswith('.layers'):
            return sys.modules.get(cls.__module__)
    return None


class NativeCalculationLayers(QObject):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._running = False
        self._bindings = {}
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.scan)

    def start(self):
        self._running = True
        self.scan()
        self.timer.start()

    def stop(self):
        self._running = False
        self.timer.stop()
        for key in tuple(self._bindings):
            self._detach(key)

    def scan(self):
        if not self._running:
            return
        for dialog in QApplication.topLevelWidgets():
            try:
                module = _native_module(dialog)
                if (isinstance(dialog, QDialog) and dialog.isVisible() and module is not None
                        and id(dialog) not in self._bindings and hasattr(dialog, 'layerbuttons')
                        and hasattr(dialog, 'txtBasinSize') and hasattr(dialog, 'btnDefault')):
                    self._attach(dialog, module)
            except (AttributeError, RuntimeError):
                continue

    def _attach(self, dialog, module):
        key = id(dialog)
        defaults = lambda *args: self._apply(dialog, module)
        closed = lambda *args: self._detach(key)
        connections = ((dialog.btnDefault.clicked, defaults),
                       (dialog.finished, closed), (dialog.destroyed, closed))
        self._bindings[key] = connections
        for signal, slot in connections:
            signal.connect(slot)
        self._apply(dialog, module)

    @staticmethod
    def _apply(dialog, module):
        pair = latest_pair()
        original = module._defaultLayerPaths
        paths = {module.MDT: pair[0] if pair else original[module.MDT],
                 module.FLOWDIRS: pair[1] if pair else original[module.FLOWDIRS],
                 module.I1ID: original[module.I1ID], module.P0: po_path()}
        for key, path in paths.items():
            combo = dialog.layerbuttons[key][1]
            dialog._setCurrentLayerFromPath(combo, str(path))
            combo.setToolTip(str(path))
        dialog.txtBasinSize.setText('2')
        if pair is None and hasattr(dialog, 'bar'):
            dialog.bar.pushMessage('HidroCAUMAX',
                'Exporta el MDT y las direcciones D8 a ResolucionMejorada para usar una resolución mejorada. '
                'Por ahora se proponen el MDT y las direcciones originales.', level=Qgis.Info, duration=8)

    def _detach(self, key):
        for signal, slot in self._bindings.pop(key, ()):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
