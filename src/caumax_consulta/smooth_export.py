"""Choose, preview, and export the exact original/smoothed basin geometries.

Smoothing is cartographic. Terrain parameters always describe the original
cell basin; output geometry measurements are separately recorded.
"""
import math
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QVariant, QTimer, QStandardPaths
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFillSymbol,
    QgsGeometry, QgsLineSymbol, QgsPointXY, QgsProject, QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapToolPan

from .metrics import shape_metrics
from .watershed_export import make_layer, write_layer, _check_new_destination, suggested_filename


BUTTON_STYLE = '''
QPushButton { background-color: #D9F0FF; border: 1px solid #7EB4D0;
              border-radius: 3px; padding: 5px 12px; color: #123748; }
QPushButton:hover { background-color: #C7E8FF; }
QPushButton:disabled { background-color: #EDF5FA; color: #8195A1; }
'''

_EXTRA_METRICS = (
    ('elongation_ratio', 'ELONG_R'), ('melton', 'MELTON'),
    ('slope_d8_pct', 'SLOPE_D8'), ('hypsometric', 'HYP_IDX'),
    ('tri_m', 'TRI_M'), ('relief_m', 'RELIEF_M'),
    ('zmax_m', 'ZMAX_M'), ('zmin_m', 'ZMIN_M'), ('zmedian_m', 'ZMED_M'),
    ('main_length_km', 'LMAIN_KM'), ('main_length_10_85_km', 'L1085_KM'),
    ('tc_h', 'TC_H'), ('tc_10_85_h', 'TC1085_H'),
)

MAX_SMOOTHING_DEGREE = 15


def smoothing_settings(degree):
    """Keep levels 1–5 identical and increase rounding above level 5.

    A raw fifteen-pass Chaikin run creates 32768 times the original vertices.
    The extended levels instead increase the corner offset with five passes.
    Two final standard passes refine the resulting corners, keeping the
    vertex count bounded by four times that of the former maximum.
    """
    if (isinstance(degree, bool) or not isinstance(degree, int)
            or not 1 <= degree <= MAX_SMOOTHING_DEGREE):
        raise ValueError('El grado de suavizado debe ser un entero entre 1 y 15.')
    return min(degree, 5), 0.25 + 0.02 * max(0, degree - 5)


def basin_geometry(analysis):
    geometry = QgsGeometry()
    geometry.fromWkb(bytes(analysis.geometry_wkb))
    if geometry.isNull() or geometry.isEmpty():
        raise ValueError('La divisoria de la cuenca está vacía.')
    if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry:
        raise ValueError('La divisoria debe ser una geometría poligonal.')
    if not geometry.isMultipart():
        geometry.convertToMultiType()
    if not geometry.isGeosValid():
        raise ValueError('La divisoria original no es válida.')
    return geometry


def _topology(geometry):
    parts = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
    return len(parts), tuple(sorted(len(part) - 1 for part in parts))


def smoothed_geometry(original, iterations, closure=None):
    """Smooth at degree 1–15 while preserving parts, holes and the closure."""
    passes, offset = smoothing_settings(iterations)
    geometry = original.smooth(passes, offset, -1.0, 180.0)
    if iterations > 5:
        geometry = geometry.smooth(2, 0.25, -1.0, 180.0)
    if geometry.isNull() or geometry.isEmpty() or not geometry.isGeosValid():
        raise ValueError('Este grado de suavizado no produce una divisoria válida.')
    if not geometry.isMultipart():
        geometry.convertToMultiType()
    if _topology(geometry) != _topology(original):
        raise ValueError('El suavizado cambia las partes o huecos de la cuenca. Reduce el grado.')
    if closure is not None and not geometry.intersects(QgsGeometry.fromPointXY(closure)):
        raise ValueError('El suavizado deja fuera la sección de cierre. Reduce el grado.')
    return geometry


def suggested_iterations(original, closure=None):
    """Highest conservative degree 1..3 with at most 1% area change.

    If even the first degree exceeds that tolerance it remains the least
    invasive selectable degree, and the interface explicitly shows the change.
    """
    area = original.area()
    if area <= 0:
        raise ValueError('La cuenca no tiene superficie positiva.')
    accepted = []
    for count in range(1, 4):
        try:
            candidate = smoothed_geometry(original, count, closure)
        except ValueError:
            continue
        if abs(candidate.area() / area - 1.0) <= 0.01:
            accepted.append(count)
    return max(accepted) if accepted else 1


def _set_geometry(layer, geometry):
    features = list(layer.getFeatures())
    if len(features) != 1:
        raise ValueError('La capa de vista previa debe contener una única cuenca.')
    if not layer.dataProvider().changeGeometryValues({features[0].id(): geometry}):
        raise ValueError('No se pudo asignar la geometría de la vista previa.')
    layer.updateExtents()


def export_layer(analysis, query, geometry, kind, iterations=0):
    """Build export attributes around the already previewed geometry.

    AREA_KM2/PERIM_KM/KC_GRAV/IC_CIRC describe the output geometry. The
    *_ORIG fields and all terrain metrics continue to describe the cell basin.
    """
    if kind not in ('original', 'suavizada'):
        raise ValueError('Tipo de divisoria desconocido.')
    original = basin_geometry(analysis)
    if (not isinstance(geometry, QgsGeometry) or geometry.isNull()
            or geometry.isEmpty()
            or QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry
            or not geometry.isGeosValid()):
        raise ValueError('La geometría mostrada no es válida para exportar.')
    if _topology(geometry) != _topology(original):
        raise ValueError('La geometría mostrada tiene otra estructura de partes o huecos.')
    layer = make_layer(analysis, query)
    origin = 'Método racional' if getattr(query, 'source_kind', 'caumax_raster') == 'rational' else 'CAUMAX'
    layer.setName('Cuenca ' + origin + ' · ' + kind)
    provider = layer.dataProvider()
    extras = [QgsField(name, QVariant.Double, '', 32, 10) for _, name in _EXTRA_METRICS]
    extras.extend([
        QgsField('A_ORIG_KM', QVariant.Double, '', 32, 10),
        QgsField('P_ORIG_KM', QVariant.Double, '', 32, 10),
        QgsField('KC_ORIG', QVariant.Double, '', 32, 10),
        QgsField('IC_ORIG', QVariant.Double, '', 32, 10),
        QgsField('SMOOTH_N', QVariant.Double, '', 32, 10),
        QgsField('GEOM_TIPO', QVariant.String, '', 12),
    ])
    if not provider.addAttributes(extras):
        raise ValueError('No se pudieron añadir los parámetros de la cuenca.')
    layer.updateFields()
    _set_geometry(layer, geometry)
    feature = next(layer.getFeatures())
    surface = (getattr(analysis, 'diagnostics', {}) or {}).get('analysis_surface') or {}
    original_area = surface.get('original_area_m2', analysis.area_m2)
    original_perimeter = surface.get('original_perimeter_m', analysis.perimeter_m)
    original_shape = shape_metrics(original_area, original_perimeter)
    output_shape = shape_metrics(geometry.area(), geometry.length())
    values = getattr(analysis, 'values', {}) or {}
    updates = {name: values.get(key) for key, name in _EXTRA_METRICS}
    updates.update({
        'AREA_KM2': geometry.area() / 1e6,
        'PERIM_KM': geometry.length() / 1000,
        'KC_GRAV': output_shape.compactness, 'IC_CIRC': output_shape.circularity,
        'A_ORIG_KM': original_area / 1e6,
        'P_ORIG_KM': original_perimeter / 1000,
        'KC_ORIG': original_shape.compactness, 'IC_ORIG': original_shape.circularity,
        'SMOOTH_N': iterations if kind == 'suavizada' else 0,
        'GEOM_TIPO': kind,
    })
    changes = {layer.fields().indexOf(name): value for name, value in updates.items()}
    if not provider.changeAttributeValues({feature.id(): changes}):
        raise ValueError('No se pudieron completar los atributos de salida.')
    return layer


def _export_destinations(geometries, destination):
    """Choose predictable paths without writing or changing the project."""
    if not geometries:
        raise ValueError('Selecciona una divisoria mostrada en el mapa.')
    if set(geometries) - {'original', 'suavizada'}:
        raise ValueError('Tipo de divisoria desconocido.')
    path = Path(destination)
    if path.suffix.lower() not in ('.shp', '.gpkg'):
        raise ValueError('Selecciona un archivo .shp o .gpkg.')
    kinds = [kind for kind in ('original', 'suavizada') if kind in geometries]
    if len(kinds) == 1:
        return {kinds[0]: path}
    return {kind: path.with_name(path.stem + '_' + kind + path.suffix) for kind in kinds}


def export_previewed(analysis, query, geometries, destination, degree,
                     add_to_project=True):
    """Export the cached geometries already displayed on the QGIS map.

    This function never smooths, simplifies, transforms or renders a geometry.
    The caller supplies the visible snapshot, in the basin CRS, under the keys
    ``original`` and/or ``suavizada``. Every path and geometry is validated
    before the first write; layers enter the project only after all writes.
    """
    targets = _export_destinations(geometries, destination)
    if 'suavizada' in targets:
        try:
            value = float(degree)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('El grado de suavizado debe estar entre 0,5 y 45.') from error
        if isinstance(degree, bool) or not math.isfinite(value) or not 0.5 <= value <= 45:
            raise ValueError('El grado de suavizado debe estar entre 0,5 y 45.')
    else:
        value = 0.0
    for target in targets.values():
        _check_new_destination(target)
    layers = {
        kind: export_layer(analysis, query, geometries[kind], kind,
                           value if kind == 'suavizada' else 0.0)
        for kind in targets
    }
    saved = [write_layer(layers[kind], target) for kind, target in targets.items()]
    if add_to_project:
        from .layer_order import add_result_layer
        for layer in saved:
            add_result_layer(layer)
    return saved


def _suggested_basin_destination(analysis, query):
    """Use the persistent QGIS profile folder for CAUMAX basin exports."""
    from .improved_storage import generated_layers_directory
    if getattr(query, 'source_kind', 'caumax_raster') == 'rational':
        return generated_layers_directory() / suggested_filename(query)
    period = getattr(query, 'period', None)
    if period is None:
        period_suffix = ''
    else:
        period_suffix = '_T%s' % format(float(period), 'g')
    return generated_layers_directory() / (
        'CONSULTA_Divisoria_X%s_Y%s%s.shp' % (
            round(analysis.outlet_x), round(analysis.outlet_y), period_suffix))


def choose_export_destination(parent, analysis, query, geometries, degree):
    """Ask for one SHP/GPKG path and export the current visible snapshot.

    Return the saved layers, or an empty list after cancellation or an error.
    Errors are shown in the calling window; no additional preview is opened.
    """
    if not geometries:
        QMessageBox.warning(parent, 'CAUMAX · Exportar divisoria',
                            'Selecciona una divisoria mostrada en el mapa.')
        return []
    try:
        suggested = str(_suggested_basin_destination(analysis, query))
    except (OSError, ValueError, TypeError) as error:
        QMessageBox.critical(parent, 'Guardar divisoria',
                             'No se pudo preparar Capas_Generadas: ' + str(error))
        return []
    destination, selected = QFileDialog.getSaveFileName(
        parent, 'Guardar divisoria', suggested,
        'Shapefile (*.shp);;GeoPackage (*.gpkg)', options=QFileDialog.DontConfirmOverwrite,
    )
    if not destination:
        return []
    if not Path(destination).suffix:
        destination += '.gpkg' if '*.gpkg' in selected else '.shp'
    try:
        return export_previewed(analysis, query, geometries, destination, degree)
    except Exception as error:
        QMessageBox.critical(parent, 'CAUMAX · Exportar divisoria', str(error))
        return []


class SmoothExportDialog(QDialog):
    """Mandatory visible preview, then one destination choice and exact export."""
    def __init__(self, analysis, query, iface, parent=None):
        super().__init__(parent)
        self.analysis, self.query, self.iface = analysis, query, iface
        self.setWindowTitle('Exportar divisoria · Vista previa')
        self.setObjectName('CAUMAXExportarDivisoria')
        self.setStyleSheet(BUTTON_STYLE)
        self.original_geometry = basin_geometry(analysis)
        self.crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
        if not self.crs.isValid():
            raise ValueError('El sistema de referencia de la cuenca no es válido.')
        self.closure = QgsPointXY(analysis.outlet_x, analysis.outlet_y)
        self._smooth_cache = {}
        self.preview_geometries = {}
        self.preview_layers = []
        self._preview_ready = False
        self._initial_extent = True
        self.exported_paths = []
        self.saved_layers = []
        self._updating = False

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.original_check = QCheckBox('Original · contorno de celdas', self)
        self.original_check.setChecked(True)
        self.smooth_check = QCheckBox('Suavizada', self)
        self.iterations = QSpinBox(self)
        self.iterations.setRange(1, MAX_SMOOTHING_DEGREE)
        self.iterations.setValue(suggested_iterations(self.original_geometry, self.closure))
        self.iterations.setEnabled(False)
        self.iterations.setToolTip('Grado 1–15: los valores altos redondean más el contorno. La vista se actualiza antes de guardar.')
        controls.addWidget(self.original_check)
        controls.addWidget(self.smooth_check)
        controls.addWidget(QLabel('Grado:', self))
        controls.addWidget(self.iterations)
        controls.addStretch()
        layout.addLayout(controls)
        # Reserve the map and its caption as one block. QgsMapCanvas can
        # enforce its 300-pixel minimum after its parent layout has compressed
        # a too-short dialog, otherwise painting under the following label.
        preview_panel = QWidget(self)
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)
        self.canvas = QgsMapCanvas(preview_panel)
        self.canvas.setMinimumSize(500, 300)
        self.canvas.setCanvasColor(QColor('white'))
        self.canvas.setDestinationCrs(self.crs)
        self.canvas.enableAntiAliasing(True)
        self._pan = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self._pan)
        self.canvas.mapCanvasRefreshed.connect(self._rendered)
        preview_layout.addWidget(self.canvas, 1)
        self.legend = QLabel('Original: rojo y malla de celdas. Suavizada: contorno azul.', preview_panel)
        self.legend.setWordWrap(True)
        self.legend.setMinimumHeight(self.legend.fontMetrics().height() + 4)
        preview_layout.addWidget(self.legend)
        preview_panel.setMinimumHeight(300 + 8 + self.legend.minimumHeight())
        layout.addWidget(preview_panel, 1)
        self.delta = QLabel(self)
        self.delta.setWordWrap(True)
        layout.addWidget(self.delta)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        note = QLabel(
            'El suavizado es cartográfico. Los parámetros del informe principal siguen referidos '
            'a la cuenca original de celdas. La exportación conserva esos valores y distingue '
            'el área, perímetro, Kc e Ic de la geometría guardada. Si marcas ambas opciones, '
            'se guardan dos archivos con sufijos _original y _suavizada.', self)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=self)
        self.save_button = QPushButton('Guardar…', self)
        self.save_button.setEnabled(False)
        buttons.addButton(self.save_button, QDialogButtonBox.AcceptRole)
        self.save_button.clicked.connect(self._choose_destination)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.original_check.toggled.connect(self._update_preview)
        self.smooth_check.toggled.connect(self._update_preview)
        self.iterations.valueChanged.connect(self._update_preview)
        self._update_preview()
        self.resize(700, max(620, self.sizeHint().height()))

    def _smooth(self, iterations):
        if iterations not in self._smooth_cache:
            self._smooth_cache[iterations] = smoothed_geometry(self.original_geometry, iterations, self.closure)
        return self._smooth_cache[iterations]

    def selected_kinds(self):
        return tuple(kind for kind, box in (
            ('original', self.original_check), ('suavizada', self.smooth_check)
        ) if box.isChecked())

    def _preview_layer(self, geometry, kind):
        layer = make_layer(self.analysis, self.query)
        layer.setName(kind)
        _set_geometry(layer, geometry)
        style = {
            'color': '220,30,30,35' if kind == 'original' else '0,0,0,0',
            'outline_color': '200,20,20,220' if kind == 'original' else '0,90,190,255',
            'outline_width': '0.45' if kind == 'original' else '0.65',
        }
        layer.renderer().setSymbol(QgsFillSymbol.createSimple(style))
        return layer

    def _grid_layer(self):
        wkb = getattr(self.analysis, 'grid_wkb', b'')
        if not wkb:
            return None
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(wkb))
        if geometry.isEmpty():
            return None
        layer = QgsVectorLayer('MultiLineString', 'Celdas originales', 'memory')
        layer.setCrs(self.crs)
        feature = QgsFeature()
        feature.setGeometry(geometry)
        ok, _ = layer.dataProvider().addFeatures([feature])
        if not ok:
            raise ValueError('No se pudo representar la malla de la cuenca.')
        layer.updateExtents()
        layer.renderer().setSymbol(QgsLineSymbol.createSimple({'line_color': '200,20,20,110', 'line_width': '0.15'}))
        return layer

    def _update_preview(self, *args):
        if self._updating:
            return
        self._updating = True
        self._preview_ready = False
        self.save_button.setEnabled(False)
        self.iterations.setEnabled(self.smooth_check.isChecked())
        self.preview_geometries = {}
        try:
            kinds = self.selected_kinds()
            if not kinds:
                self.canvas.setLayers([])
                self.preview_layers = []
                self.delta.clear()
                self.status.setText('Selecciona al menos una divisoria para mostrarla antes de guardar.')
                return
            geometries = {}
            for kind in kinds:
                geometries[kind] = QgsGeometry(self.original_geometry if kind == 'original' else self._smooth(self.iterations.value()))
            layers = []
            if 'suavizada' in geometries:
                layers.append(self._preview_layer(geometries['suavizada'], 'suavizada'))
            if 'original' in geometries:
                grid = self._grid_layer()
                if grid is not None:
                    layers.append(grid)
                layers.append(self._preview_layer(geometries['original'], 'original'))
            self.preview_geometries = geometries
            self.preview_layers = layers
            self.canvas.setLayers(layers)
            if self._initial_extent:
                extent = self.original_geometry.boundingBox()
                extent.scale(1.15)
                self.canvas.setExtent(extent)
                self._initial_extent = False
            if 'suavizada' in geometries:
                smooth = geometries['suavizada']
                da = 100 * (smooth.area() / self.original_geometry.area() - 1)
                dp = 100 * (smooth.length() / self.original_geometry.length() - 1)
                self.delta.setText(f'Grado {self.iterations.value()}: variación de área {da:+.3f} %; perímetro {dp:+.3f} %.')
            else:
                self.delta.setText('Divisoria original: sin modificar la geometría de las celdas.')
            self.status.setText('Generando la vista previa…')
            self.canvas.refresh()
        except Exception as error:
            self.canvas.setLayers([])
            self.preview_layers = []
            self.preview_geometries = {}
            self.status.setText(str(error))
        finally:
            self._updating = False

    def _rendered(self):
        if self.preview_geometries and not self.canvas.isDrawing():
            self._preview_ready = True
            self.save_button.setEnabled(True)
            self.status.setText('Vista previa lista. Se guardará exactamente la geometría mostrada.')

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.canvas.refresh)

    def destinations(self, destination):
        return _export_destinations(dict.fromkeys(self.selected_kinds()), destination)

    def export_to(self, destination, add_to_project=True):
        """Save only an already rendered snapshot; never smooth again here."""
        if not self._preview_ready or not self.save_button.isEnabled():
            raise ValueError('Espera a que se muestre la vista previa antes de guardar.')
        targets = self.destinations(destination)
        if set(targets) != set(self.preview_geometries):
            raise ValueError('La selección ha cambiado. Espera a la nueva vista previa.')
        saved = export_previewed(self.analysis, self.query, self.preview_geometries,
                                destination, self.iterations.value(), add_to_project)
        self.saved_layers = saved
        self.exported_paths = [str(path) for path in targets.values()]
        return saved

    def _choose_destination(self):
        if not self._preview_ready:
            return
        try:
            suggested = str(_suggested_basin_destination(self.analysis, self.query))
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.critical(self, 'Guardar divisoria',
                                 'No se pudo preparar Capas_Generadas: ' + str(error))
            return
        destination, selected = QFileDialog.getSaveFileName(
            self, 'Guardar divisoria', suggested,
            'Shapefile (*.shp);;GeoPackage (*.gpkg)', options=QFileDialog.DontConfirmOverwrite,
        )
        if not destination:
            return
        if not Path(destination).suffix:
            destination += '.gpkg' if '*.gpkg' in selected else '.shp'
        try:
            self.export_to(destination)
        except Exception as error:
            QMessageBox.critical(self, 'CAUMAX · Exportar divisoria', str(error))
            return
        self.accept()

    def done(self, code):
        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self.preview_layers = []
        super().done(code)
