"""Adapt owned DEM colours to the visible map, independently of hydrology."""
import math
import weakref

import numpy as np
from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.core import (QgsApplication, QgsCoordinateTransform, QgsCsException, QgsProject,
                       QgsRasterLayer, QgsSingleBandPseudoColorRenderer, QgsTask)

from .dem_display import ROLE_PROPERTY, apply_visible_elevation_range, smooth_terrain_display


def visible_elevation_range(source, bounds, cancelled=lambda: False):
    """Exact valid-cell extrema in the visible raster window, read in tiles.

    The CRS transformation runs on the GUI thread before this worker starts.
    Independent GDAL handles and disabled PAM avoid modifying a layer/provider
    or writing statistics sidecars. Memory is bounded by a 512 x 512 tile.
    """
    previous_pam = gdal.GetThreadLocalConfigOption('GDAL_PAM_ENABLED')
    gdal.SetThreadLocalConfigOption('GDAL_PAM_ENABLED', 'NO')
    dataset = None
    try:
        dataset = gdal.Open(str(source), gdal.GA_ReadOnly)
        if dataset is None or dataset.RasterCount < 1 or cancelled():
            return None
        transform = dataset.GetGeoTransform()
        inverse = gdal.InvGeoTransform(transform)
        if inverse is None:
            return None
        xmin, ymin, xmax, ymax = bounds
        pixels = [gdal.ApplyGeoTransform(inverse, x, y)
                  for x in (xmin, xmax) for y in (ymin, ymax)]
        # A tiny tolerance keeps exactly aligned boundaries from pulling in an
        # adjacent cell because of the CRS transform's floating point roundoff.
        x0 = max(0, math.floor(min(x for x, _ in pixels)+1e-7))
        x1 = min(dataset.RasterXSize, math.ceil(max(x for x, _ in pixels)-1e-7))
        y0 = max(0, math.floor(min(y for _, y in pixels)+1e-7))
        y1 = min(dataset.RasterYSize, math.ceil(max(y for _, y in pixels)-1e-7))
        if x1 <= x0 or y1 <= y0:
            return None
        band = dataset.GetRasterBand(1)
        nodata = band.GetNoDataValue()
        mask = None if band.GetMaskFlags() & gdal.GMF_ALL_VALID else band.GetMaskBand()
        low, high = math.inf, -math.inf
        for row in range(y0, y1, 512):
            for col in range(x0, x1, 512):
                if cancelled():
                    return None
                width, height = min(512, x1-col), min(512, y1-row)
                values = band.ReadAsArray(col, row, width, height)
                if values is None:
                    continue
                valid = np.isfinite(values)
                if nodata is not None:
                    valid &= values != nodata
                if mask is not None:
                    valid &= mask.ReadAsArray(col, row, width, height) != 0
                if np.any(valid):
                    low = min(low, float(values[valid].min()))
                    high = max(high, float(values[valid].max()))
        return (low, high) if math.isfinite(low) and math.isfinite(high) else None
    finally:
        dataset = None
        gdal.SetThreadLocalConfigOption('GDAL_PAM_ENABLED', previous_pam)


class _VisibleRangeTask(QgsTask):
    def __init__(self, controller, layer_id, serial, signature):
        super().__init__('Actualizar colores del MDT visible', QgsTask.CanCancel)
        self._owner = weakref.ref(controller)
        self.layer_id, self.serial, self.signature = layer_id, serial, signature
        self.result = None

    def run(self):
        try:
            self.result = visible_elevation_range(self.signature[0], self.signature[1], self.isCanceled)
            return not self.isCanceled()
        except (OSError, RuntimeError, ValueError, TypeError, OverflowError):
            return False

    def finished(self, success):
        owner = self._owner()
        if owner is not None and not sip.isdeleted(owner):
            owner._completed(self, success)


class VisibleDemController(QObject):
    """One canvas listener for the plugin lifetime, with cancellable reads."""
    def __init__(self, iface, parent=None, debounce_ms=300):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.refresh)
        self._started = False
        self._serial = 0
        self._signals, self._tasks, self._applied = [], set(), {}

    def start(self):
        if self._started:
            return
        self._started = True
        for name in ('extentsChanged', 'destinationCrsChanged', 'layersChanged'):
            signal = getattr(self.canvas, name, None)
            if signal is not None:
                signal.connect(self.schedule)
                self._signals.append(signal)
        self.schedule()

    def stop(self):
        self._started = False
        self._timer.stop()
        self._serial += 1
        for signal in self._signals:
            try:
                signal.disconnect(self.schedule)
            except (TypeError, RuntimeError):
                pass
        self._signals.clear()
        for task in list(self._tasks):
            task.cancel()
        self._applied.clear()

    def schedule(self, *unused):
        if not self._started:
            return
        self._serial += 1
        for task in list(self._tasks):
            task.cancel()
        self._timer.start()

    def _visible_layers(self):
        for layer in self.canvas.layers():
            if (isinstance(layer, QgsRasterLayer) and layer.isValid()
                    and layer.customProperty(ROLE_PROPERTY) == 'hypsometry'
                    and isinstance(layer.renderer(), QgsSingleBandPseudoColorRenderer)
                    and layer.opacity() > 0 and layer.isInScaleRange(self.canvas.scale())):
                yield layer

    def refresh(self):
        self._timer.stop()
        if not self._started:
            return
        for layer in self.canvas.layers():
            smooth_terrain_display(layer)
        current_ids = set()
        for layer in self._visible_layers():
            current_ids.add(layer.id())
            try:
                transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(),
                                                   layer.crs(), QgsProject.instance().transformContext())
                extent = transform.transformBoundingBox(self.canvas.extent()).intersect(layer.extent())
                if extent.isEmpty():
                    continue
                bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
                if not all(math.isfinite(value) for value in bounds):
                    continue
                signature = (layer.source().split('|', 1)[0], bounds)
                if self._applied.get(layer.id()) == signature:
                    continue
                task = _VisibleRangeTask(self, layer.id(), self._serial, signature)
                self._tasks.add(task)
                QgsApplication.taskManager().addTask(task)
            except (RuntimeError, ValueError, TypeError, OverflowError, QgsCsException):
                continue
        self._applied = {key: value for key, value in self._applied.items() if key in current_ids}

    def _completed(self, task, success):
        self._tasks.discard(task)
        if not success or not self._started or task.serial != self._serial:
            return
        layer = next((item for item in self._visible_layers() if item.id() == task.layer_id), None)
        if layer is None:
            return
        self._applied[layer.id()] = task.signature
        if task.result is not None and apply_visible_elevation_range(layer, *task.result):
            view = self.iface.layerTreeView() if hasattr(self.iface, 'layerTreeView') else None
            if view is not None:
                view.refreshLayerSymbology(layer.id())
