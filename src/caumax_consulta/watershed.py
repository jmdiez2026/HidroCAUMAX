"""Asynchronous basin calculation and preview for a native identify query."""
import math
import os
from pathlib import Path
import sys
import traceback

from qgis.PyQt.QtCore import QObject, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QFormLayout, QGroupBox, QLabel, QLineEdit, QProgressBar,
    QPushButton, QVBoxLayout,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsGeometry, QgsMessageLog, QgsPointXY, QgsProject, QgsRasterLayer,
    QgsTask, QgsWkbTypes,
)
from qgis.gui import QgsRubberBand, QgsVertexMarker

from .metrics import shape_metrics


def calculation_paths(query):
    """Resolve drainage/DEM from the CAUMAX configuration of this flow layer."""
    from .plugin import _normal_path
    layer = QgsProject.instance().mapLayer(query.layer_id)
    if layer is None:
        raise ValueError('La capa consultada ya no está en el proyecto.')
    source = _normal_path(layer.source())
    candidates = set()
    for name, module in tuple(sys.modules.items()):
        if module is None or not name.endswith('.layers'):
            continue
        if not all(hasattr(module, key) for key in ('getLayerPath', 'FLOWDIRS', 'MDT', f'FLOW_{query.period}')):
            continue
        try:
            if _normal_path(module.getLayerPath(getattr(module, f'FLOW_{query.period}'))) == source:
                candidates.add((str(module.getLayerPath(module.FLOWDIRS)), str(module.getLayerPath(module.MDT))))
        except (KeyError, ValueError, TypeError, RuntimeError):
            continue
    if len(candidates) != 1:
        raise ValueError('Activa CAUMAX y comprueba las capas MDT y Direcciones de flujo en Configurar capas de cálculo.')
    paths = next(iter(candidates))
    if not all(Path(path).is_file() for path in paths):
        raise ValueError('No se encuentran los archivos de direcciones de flujo y MDT configurados en CAUMAX.')
    return paths


def _geometry(result):
    geometry = QgsGeometry()
    geometry.fromWkb(bytes(result.geometry_wkb))
    if geometry.isEmpty():
        raise ValueError('La geometría calculada de la cuenca está vacía.')
    return geometry


class BasinTask(QgsTask):
    def __init__(self, paths, outlet, callback):
        super().__init__('CAUMAX: delimitar cuenca vertiente', QgsTask.CanCancel)
        self.paths = paths
        self.outlet = outlet
        self.callback = callback
        self.result = None
        self.error = ''
        self.error_trace = ''

    def run(self):
        try:
            from .basin import delineate
            self.result = delineate(*self.paths, *self.outlet, cancelled=self.isCanceled)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            self.error_trace = traceback.format_exc()
            return False

    def finished(self, success):
        self.callback(self, success)


class WatershedController(QObject):
    """One dialog, one current result, cancelable tasks and a small local cache."""
    def __init__(self, dialog, iface):
        super().__init__(dialog)
        self.dialog = dialog
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._disposed = False
        self._task = None
        self._key = None
        self._query = None
        self.result = None
        self._cache = {}
        self._rubber = self._marker = None

        self.group = QGroupBox('Cuenca vertiente', dialog)
        layout = QVBoxLayout(self.group)
        form = QFormLayout()
        self.fields = {}
        for key, title in (
            ('area', 'Área'), ('perimeter', 'Perímetro'),
            ('kc', 'Compacidad de Gravelius · Kc'), ('ic', 'Circularidad · Ic'),
            ('resolution', 'Resolución de delimitación'),
        ):
            field = QLineEdit(self.group)
            field.setReadOnly(True)
            field.setObjectName('cuenca_' + key)
            self.fields[key] = field
            form.addRow(title, field)
        self.fields['kc'].setToolTip('Kc = P / (2·√(π·A)); equivalente a ≈0,282095·P/√A. Adimensional.')
        self.fields['ic'].setToolTip('Ic = 4·π·A / P² = 1/Kc². Adimensional.')
        self.fields['perimeter'].setToolTip('Longitud de todos los contornos de la geometría exportada, incluidos los huecos. Sin suavizado.')
        layout.addLayout(form)
        self.status = QLabel(self.group)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self.group)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.save = QPushButton('Guardar cuenca…', self.group)
        self.save.setObjectName('btnGuardarCuencaIdentificada')
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        layout.addWidget(self.save)
        note = QLabel('Divisoria obtenida de la malla de drenaje de CAUMAX. Su resolución condiciona el perímetro y los índices de forma.', self.group)
        note.setWordWrap(True)
        layout.addWidget(note)
        dialog.layout().insertWidget(dialog.layout().count() - 1, self.group)
        dialog.combo.currentIndexChanged.connect(self.start)
        dialog.finished.connect(self.dispose)
        self.canvas.destinationCrsChanged.connect(self._preview)
        self.start()
        dialog.resize(max(dialog.width(), 610), dialog.sizeHint().height())

    def _clear(self):
        self.result = None
        self.save.setEnabled(False)
        for field in self.fields.values():
            field.setText('—')
        self._remove_preview()

    def _cancel(self):
        task, self._task = self._task, None
        if task is not None:
            try:
                task.cancel()
            except RuntimeError:
                pass

    def start(self, *args):
        if self._disposed:
            return
        self._cancel()
        self._clear()
        self.progress.hide()
        query = self.dialog.query
        self._query = query
        if query.cell is None or query.flow is None:
            self.status.setText('No se delimita una cuenca para una celda sin caudal válido.')
            return
        try:
            paths = calculation_paths(query)
            directions = QgsRasterLayer(paths[0], 'Direcciones de drenaje', 'gdal')
            if not directions.isValid() or not directions.crs().isValid():
                raise ValueError('La capa de direcciones de drenaje no tiene un sistema de referencia válido.')
            # q rasters and the drainage grid can have different extents.
            # Transfer coordinates, NEVER q raster row/column indices.
            point = QgsCoordinateTransform(query.crs, directions.crs(), QgsProject.instance()).transform(QgsPointXY(query.cell.x, query.cell.y))
            self._key = tuple((os.path.normcase(os.path.realpath(path)), Path(path).stat().st_mtime_ns) for path in paths) + (point.x(), point.y())
            if self._key in self._cache:
                self._show_result(self._cache[self._key])
                return
            self.status.setText('Delimitando la cuenca desde el centro de la celda… Cerrar esta ventana cancela el cálculo.')
            self.progress.show()
            task = BasinTask(paths, (point.x(), point.y()), self._completed)
            self._task = task
            QgsApplication.taskManager().addTask(task)
        except Exception as error:
            self.status.setText(str(error))
            self.progress.hide()

    def _completed(self, task, success):
        if self._disposed or task is not self._task:
            return
        self._task = None
        self.progress.hide()
        if not success:
            self.status.setText(task.error or 'Cálculo cancelado.')
            if task.error_trace:
                QgsMessageLog.logMessage(task.error_trace, 'CAUMAX · Consulta', Qgis.Warning)
            return
        try:
            if len(self._cache) >= 4:
                self._cache.pop(next(iter(self._cache)))
            self._cache[self._key] = task.result
            self._show_result(task.result)
        except Exception as error:
            self._clear()
            self.status.setText(str(error))

    def _show_result(self, result):
        shape = shape_metrics(result.area_m2, result.perimeter_m)
        self.result = result
        values = {
            'area': f'{result.area_m2 / 1e6:.6f} km²',
            'perimeter': f'{result.perimeter_m / 1000:.6f} km',
            'kc': f'{shape.compactness:.6f}', 'ic': f'{shape.circularity:.6f}',
            'resolution': f'{result.cell_width:g} × {result.cell_height:g} m',
        }
        for key, value in values.items():
            self.fields[key].setText(value)
        crs = QgsCoordinateReferenceSystem.fromWkt(result.crs_wkt)
        message = (f'Cierre de delimitación: X={result.outlet_x:.3f}; Y={result.outlet_y:.3f} '
                   f'({crs.authid() or crs.description()}).')
        if result.touches_boundary:
            message += ' La cuenca alcanza el borde de los datos: comprueba si está completa.'
        self.status.setText(message)
        self.save.setEnabled(True)
        self.progress.hide()
        self._preview()

    def _preview(self, *args):
        self._remove_preview()
        if self._disposed or self.result is None:
            return
        result = self.result
        crs = QgsCoordinateReferenceSystem.fromWkt(result.crs_wkt)
        transform = QgsCoordinateTransform(crs, self.canvas.mapSettings().destinationCrs(), QgsProject.instance())
        try:
            geometry = _geometry(result)
            geometry.transform(transform)
            rubber = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            rubber.setColor(QColor(200, 20, 20, 220))
            rubber.setFillColor(QColor(255, 40, 40, 45))
            rubber.setWidth(2)
            rubber.setToGeometry(geometry, None)
            self._rubber = rubber
            marker = QgsVertexMarker(self.canvas)
            marker.setCenter(transform.transform(QgsPointXY(result.outlet_x, result.outlet_y)))
            marker.setIconType(QgsVertexMarker.ICON_CROSS)
            marker.setColor(QColor(0, 70, 190))
            marker.setIconSize(12)
            marker.setPenWidth(3)
            self._marker = marker
        except Exception as error:
            self._remove_preview()
            self.status.setText(self.status.text() + '\nNo se pudo representar la vista previa: ' + str(error))

    def _remove_preview(self):
        for attr in ('_rubber', '_marker'):
            item = getattr(self, attr, None)
            if item is not None:
                try:
                    self.canvas.scene().removeItem(item)
                except RuntimeError:
                    pass
                setattr(self, attr, None)

    def _save(self):
        if not self._disposed and self.result is not None and self._query is not None:
            from .watershed_export import save_result
            save_result(self.dialog, self.result, self._query)

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._cancel()
        self._cache.clear()
        self._remove_preview()
        try:
            self.canvas.destinationCrsChanged.disconnect(self._preview)
        except (RuntimeError, TypeError):
            pass
