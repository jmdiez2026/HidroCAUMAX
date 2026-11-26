"""Frame Iberia after the explicit native CAUMAX layer-loading command."""
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsCsException, QgsProject, QgsRectangle)
import qgis.utils


# Geographic frame inferred from the requested overview: mainland Iberia,
# Balearic Islands and a small surrounding margin. QGIS fits the canvas ratio.
IBERIA_BOUNDS = (-11.0, 35.6, 5.5, 44.2)


class NativeLayersView(QObject):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._running = False
        self._action = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(1000)
        self._scan_timer.timeout.connect(self._scan)
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._apply)

    def start(self):
        if self._running:
            return
        self._running = True
        self._scan()
        self._scan_timer.start()

    def stop(self):
        self._running = False
        self._scan_timer.stop()
        self._zoom_timer.stop()
        self._disconnect()

    def _disconnect(self):
        action, self._action = self._action, None
        if action is not None and not sip.isdeleted(action):
            try:
                action.triggered.disconnect(self._loaded)
            except (RuntimeError, TypeError):
                pass

    def _scan(self):
        if not self._running:
            return
        plugin = qgis.utils.plugins.get('caumax')
        action = getattr(plugin, 'actionLoadProject', None)
        if action is not None and sip.isdeleted(action):
            action = None
        if action is self._action:
            return
        self._zoom_timer.stop()
        self._disconnect()
        if action is not None:
            # Append an observer: do not replace CAUMAX's loader or its slots.
            action.triggered.connect(self._loaded)
            self._action = action

    def _loaded(self, *args):
        if self._running:
            # Wait for the native synchronous loader, including its group move.
            self._zoom_timer.start(0)

    def _apply(self):
        if not self._running or self._action is None or sip.isdeleted(self._action):
            return
        project = QgsProject.instance()
        group = project.layerTreeRoot().findGroup('CAUMAX_CAPAS')
        if group is None or not any(n.layer() and n.layer().isValid() for n in group.findLayers()):
            return
        canvas = self.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        if not crs.isValid():
            return
        try:
            transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),
                                               crs, project.transformContext())
            extent = transform.transformBoundingBox(QgsRectangle(*IBERIA_BOUNDS))
        except QgsCsException:
            return
        if extent.isEmpty() or not extent.isFinite():
            return
        canvas.setExtent(extent)
        canvas.refresh()
