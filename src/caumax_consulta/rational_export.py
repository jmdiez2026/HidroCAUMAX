"""Lifecycle-managed adaptation of the user's rational-method basin exporter.

The original standalone script is kept verbatim in extras and is not executed
here. This bridge exports exactly the same polygon filter that CAUMAX paints
red. It does not recalculate a basin, read edited coordinate fields or assume
EPSG:25830: BasinCalculator.crs is the WKT read from its actual MDT dataset.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path

from qgis.PyQt.QtCore import QObject, QStandardPaths, QTimer, QVariant
from qgis.PyQt.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsFeature,
    QgsField, QgsFillSymbol, QgsGeometry, QgsProject, QgsVectorFileWriter, QgsVectorLayer,
)
import qgis.utils

from .watershed_export import _check_new_destination


BUTTON_NAME = 'btnExportarCuencaAuxiliar'
BUTTON_STYLE = '''QPushButton { background-color: #D9F0FF; color: #173D52;
border: 1px solid #7EB4D0; border-radius: 3px; padding: 5px 9px; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:disabled { background-color: #EAF3F8; color: #7A8790; }'''
_TITLE = 'HidroCAUMAX v1.0.4 · Guardar cuenca'


@dataclass(frozen=True)
class _Snapshot:
    geometries: tuple
    crs: QgsCoordinateReferenceSystem
    x: float
    y: float
    area_km2: float


def _snapshot(dialog):
    calculator = getattr(dialog, 'basinCalc', None)
    if calculator is None or getattr(dialog, 'rubberBand', None) is None:
        raise ValueError("Primero pulsa 'Calcular variables intermedias' en CAUMAX.")
    geometries = tuple(QgsGeometry(geometry) for geometry in
                       (getattr(calculator, 'basinGeometry', None) or ())
                       if not geometry.isEmpty() and len(geometry.asPolygon()) == 1)
    if not geometries:
        raise ValueError('No se encontraron polígonos exportables en la delimitación roja.')
    outlet = getattr(calculator, 'pt', None)
    if outlet is None:
        raise ValueError('No se encontró el punto utilizado para calcular la cuenca.')
    try:
        x, y, area = float(outlet.x()), float(outlet.y()), float(calculator.area)
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('El cierre o la superficie calculados por CAUMAX no son válidos.') from error
    if not all(math.isfinite(value) for value in (x, y, area)) or area <= 0:
        raise ValueError('El cierre y la superficie de CAUMAX deben ser finitos y la superficie positiva.')
    source_crs = getattr(calculator, 'crs', None)
    crs = (QgsCoordinateReferenceSystem(source_crs)
           if isinstance(source_crs, QgsCoordinateReferenceSystem)
           else QgsCoordinateReferenceSystem.fromWkt(source_crs or ''))
    if not crs.isValid():
        raise ValueError('No se encuentra el sistema de referencia del MDT utilizado en el cálculo.')
    return _Snapshot(geometries, crs, x, y, area / 1e6)


def _make_layer(snapshot):
    layer = QgsVectorLayer('Polygon', 'Cuenca CAUMAX · Método racional', 'memory')
    if not layer.isValid():
        raise ValueError('No se pudo crear la capa vectorial temporal.')
    layer.setCrs(snapshot.crs)
    provider = layer.dataProvider()
    fields = [QgsField('PARTE', QVariant.Int)]
    fields.extend(QgsField(name, QVariant.Double, '', 32, 10)
                  for name in ('X_SALIDA', 'Y_SALIDA', 'AREA_TOT', 'AREA_POL'))
    if not provider.addAttributes(fields):
        raise ValueError('No se pudieron crear los atributos de la cuenca.')
    layer.updateFields()
    features = []
    for number, geometry in enumerate(snapshot.geometries, 1):
        feature = QgsFeature(layer.fields())
        feature.setGeometry(geometry)
        feature.setAttributes([number, snapshot.x, snapshot.y, snapshot.area_km2,
                               geometry.area() / 1e6])
        features.append(feature)
    ok, added = provider.addFeatures(features)
    if not ok or len(added) != len(features):
        raise ValueError('No se pudieron incorporar todos los polígonos originales.')
    layer.updateExtents()
    return layer


def make_rational_layer(dialog):
    """Build an unregistered layer from a coherent copy of the visible result."""
    return _make_layer(_snapshot(dialog))


def _write(layer, destination):
    path, extension = _check_new_destination(destination)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG' if extension == '.gpkg' else 'ESRI Shapefile'
    options.fileEncoding = 'UTF-8'
    options.layerName = path.stem
    # Preserve the original per-polygon feature geometries, without union,
    # forceMulti, reprojection, smoothing, or topology repair.
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(path), QgsCoordinateTransformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise ValueError('No se pudo guardar la cuenca: ' + str(result[1] or result[0]))
    actual_path = result[2] or str(path)
    uri = actual_path + ('|layername=' + (result[3] or path.stem) if extension == '.gpkg' else '')
    saved = QgsVectorLayer(uri, path.stem, 'ogr')
    if not saved.isValid() or saved.featureCount() != layer.featureCount():
        raise ValueError('El archivo guardado no contiene todos los polígonos de CAUMAX.')
    saved.renderer().setSymbol(QgsFillSymbol.createSimple({
        'color': '255,0,0,90', 'outline_color': '220,0,0,255', 'outline_width': '0.3',
    }))
    return saved


@dataclass
class _Binding:
    dialog: object
    button: object
    connections: tuple


class RationalExportBridge(QObject):
    """Watch native rational dialogs every 400 ms; own only our own buttons."""

    BUTTON_NAME = BUTTON_NAME

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self._running = False
        self._bindings = {}
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.scan)

    def start(self):
        self._running = True
        if not self.timer.isActive():
            self.timer.start()
        self.scan()

    def stop(self):
        self._running = False
        self.timer.stop()
        for key in tuple(self._bindings):
            self._detach(key)

    @staticmethod
    def _legacy_active():
        controller = getattr(qgis.utils, '_caumax_export_controller', None)
        timer = getattr(controller, 'timer', None)
        try:
            return timer is not None and timer.isActive()
        except RuntimeError:
            return False

    @staticmethod
    def _rational_dialogs():
        for dialog in QApplication.topLevelWidgets():
            try:
                native = any(cls.__name__ == 'RationalMethodDialog' and
                             cls.__module__.endswith('.rationalmethoddialog')
                             for cls in type(dialog).__mro__)
                if (isinstance(dialog, QDialog) and native and dialog.isVisible() and all(
                        hasattr(dialog, name) for name in
                        ('basinCalc', 'horizontalLayout_2', 'txtX', 'txtY', 'rubberBand'))):
                    yield dialog
            except RuntimeError:
                continue

    @staticmethod
    def _available(dialog):
        calculator = getattr(dialog, 'basinCalc', None)
        return (calculator is not None and getattr(dialog, 'rubberBand', None) is not None
                and getattr(calculator, 'pt', None) is not None
                and any(not geometry.isEmpty() and len(geometry.asPolygon()) == 1
                        for geometry in (getattr(calculator, 'basinGeometry', None) or ())))

    def scan(self):
        if not self._running:
            return
        for key, binding in tuple(self._bindings.items()):
            try:
                binding.button.setEnabled(self._available(binding.dialog))
            except RuntimeError:
                self._detach(key)
        legacy_active = self._legacy_active()
        for dialog in self._rational_dialogs():
            if id(dialog) in self._bindings:
                continue
            # A previous standalone controller keeps running unchanged. Do not
            # connect, restyle, delete, stop or otherwise adopt its button.
            if legacy_active or dialog.findChild(QPushButton, BUTTON_NAME) is not None:
                continue
            button = QPushButton('Guardar cuenca…', dialog)
            button.setObjectName(BUTTON_NAME)
            button.setStyleSheet(BUTTON_STYLE)
            button.setToolTip('Guardar los polígonos rojos y el cierre calculados por CAUMAX')
            dialog.horizontalLayout_2.insertWidget(2, button)
            key = id(dialog)
            clicked = lambda checked=False, current=dialog: self.export_dialog(current)
            closed = lambda *args, current=key: self._detach(current)
            button.clicked.connect(clicked)
            dialog.finished.connect(closed)
            dialog.destroyed.connect(closed)
            self._bindings[key] = _Binding(dialog, button, (
                (button.clicked, clicked), (dialog.finished, closed), (dialog.destroyed, closed)))
            button.setEnabled(self._available(dialog))

    def _detach(self, key):
        binding = self._bindings.pop(key, None)
        if binding is None:
            return
        for signal, slot in binding.connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        try:
            binding.dialog.horizontalLayout_2.removeWidget(binding.button)
            binding.button.setParent(None)
            binding.button.deleteLater()
        except RuntimeError:
            pass

    def export_dialog(self, dialog):
        """Capture the computed result before the nested save-file dialog."""
        try:
            snapshot = _snapshot(dialog)
        except (ValueError, TypeError, AttributeError, RuntimeError) as error:
            QMessageBox.warning(dialog, _TITLE, str(error))
            return None
        directory = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.expanduser('~')
        suggested = str(Path(directory) / f'Cuenca_CAUMAX_{round(snapshot.x)}_{round(snapshot.y)}.shp')
        destination, selected_filter = QFileDialog.getSaveFileName(
            dialog, _TITLE, suggested, 'ESRI Shapefile (*.shp);;GeoPackage (*.gpkg)',
            options=QFileDialog.DontConfirmOverwrite,
        )
        if not destination:
            return None
        if not Path(destination).suffix:
            destination += '.gpkg' if '*.gpkg' in selected_filter else '.shp'
        try:
            saved = _write(_make_layer(snapshot), destination)
            from .layer_order import add_result_layer
            if add_result_layer(saved) is None:
                raise ValueError('La cuenca se ha guardado, pero no se pudo añadir al proyecto.')
        except Exception as error:
            QMessageBox.critical(dialog, _TITLE, str(error))
            return None
        polygon_area = sum(geometry.area() for geometry in snapshot.geometries) / 1e6
        QMessageBox.information(
            dialog, _TITLE,
            f'Cuenca guardada en:\n{destination}\n\n'
            f'Superficie calculada por CAUMAX: {snapshot.area_km2:.3f} km²\n'
            f'Superficie de los polígonos: {polygon_area:.3f} km²\n'
            f'Punto de salida: X={snapshot.x:.3f}; Y={snapshot.y:.3f}\n'
            f'Sistema de referencia: {snapshot.crs.authid() or snapshot.crs.description()}',
        )
        return saved
