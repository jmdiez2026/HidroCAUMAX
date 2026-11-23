"""Choose a native D8 cell explicitly, without changing or recalculating data."""
import math

from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QVBoxLayout,
)
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker

from .basin import _aligned, _open_raster, _read, _VALID_CODES
from .grid import cell_at_point


class OutletSelectionDialog(QDialog):
    """Nonmodal selector; selection() is populated only after valid acceptance.

    ``original_x/y`` always define the distance reference. Optional
    ``initial_x/y`` start at an already-computed outlet without moving that
    reference. Both coordinate pairs are in the native projected DEM CRS.
    """

    applied = pyqtSignal()

    def __init__(self, iface, context, parent=None, *, persistent=False):
        super().__init__(parent)
        self.iface = iface
        self.context = dict(context)
        self.canvas = iface.mapCanvas()
        self._dem = self._directions = None
        self._marker = self._map_tool = self._previous_tool = None
        self._picking = self._closed = False
        self._busy = False
        self.persistent = bool(persistent)
        self._candidate = self._selection = None
        self._row = self._col = None
        self._load_context(context)

        self.setWindowTitle('HidroCAUMAX · Elegir cierre de la cuenca')
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        introduction = QLabel(
            'Selecciona la celda donde debe comenzar el curso principal. '
            'El cálculo se actualizará únicamente al aplicar el cierre.', self)
        introduction.setWordWrap(True)
        introduction.setTextFormat(Qt.PlainText)
        layout.addWidget(introduction)
        reason = QLabel(str(context.get('reason') or ''), self)
        reason.setTextFormat(Qt.PlainText)
        reason.setWordWrap(True)
        reason.setVisible(bool(reason.text()))
        self.reason_label = reason
        layout.addWidget(reason)
        reference = QLabel(
            f"MDT {context.get('resolution_key','')} m · {self.crs.authid() or 'CRS nativo'}\n"
            f'Punto original: X={self.original_x:.3f}; Y={self.original_y:.3f}', self)
        reference.setTextFormat(Qt.PlainText)
        reference.setWordWrap(True)
        self.reference_label = reference
        layout.addWidget(reference)
        self.coordinate_label = QLabel('Centro propuesto: sin selección válida.', self)
        self.distance_label = QLabel(f'Radio máximo: {self.radius:g} m.', self)
        self.status_label = QLabel(self)
        for label in (self.coordinate_label,self.distance_label,self.status_label):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        step_row = QHBoxLayout()
        step_row.addWidget(QLabel('Paso (celdas):', self))
        self.step = QSpinBox(self)
        self._set_step_range()
        self.step.setValue(1)
        step_row.addWidget(self.step)
        step_row.addStretch()
        layout.addLayout(step_row)
        arrows = QGridLayout()
        self.north_button = QPushButton('Norte', self)
        self.south_button = QPushButton('Sur', self)
        self.east_button = QPushButton('Este', self)
        self.west_button = QPushButton('Oeste', self)
        for button,row,col,dr,dc in (
                (self.north_button,0,1,-1,0), (self.south_button,2,1,1,0),
                (self.east_button,1,2,0,1), (self.west_button,1,0,0,-1)):
            arrows.addWidget(button,row,col)
            button.clicked.connect(lambda checked=False,dr=dr,dc=dc:self.move_cell(dr,dc))
        self.pick_button = QPushButton('Elegir en el mapa', self)
        self.pick_button.clicked.connect(self.start_picking)
        arrows.addWidget(self.pick_button,1,1)
        layout.addLayout(arrows)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Cancel).setText('Cancelar')
        self.apply_button = buttons.addButton('Aplicar cierre',QDialogButtonBox.AcceptRole)
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.canvas.mapToolSet.connect(self._tool_changed)
        self.canvas.destinationCrsChanged.connect(self._refresh_marker)
        initial_x = context.get('initial_x', self.original_x)
        initial_y = context.get('initial_y', self.original_y)
        self._propose_point(initial_x,initial_y)

    def _load_context(self, context):
        """Open a complete replacement before releasing the previous rasters."""
        x, y = float(context['original_x']), float(context['original_y'])
        radius = float(context['search_radius_m'])
        scope = context.get('manual_selection_scope', 'local_radius')
        if scope not in ('local_radius', 'raster_extent'):
            raise ValueError('El ámbito de la selección manual no es válido.')
        if not all(math.isfinite(v) for v in (x,y,radius)) or radius <= 0:
            raise ValueError('El punto original y el radio de selección deben ser finitos; el radio debe ser positivo.')
        dem = directions = None
        try:
            dem = _open_raster(context['dem_path'], 'MDT')
            directions = _open_raster(context['directions_path'], 'direcciones')
            transform, _ = _aligned(directions, dem)
            crs = QgsCoordinateReferenceSystem.fromWkt(context['crs_wkt'])
            if not crs.isValid() or crs != QgsCoordinateReferenceSystem.fromWkt(dem.GetProjectionRef()):
                raise ValueError('El CRS del contexto no coincide con el MDT.')
            self.context = dict(context)
            self.original_x,self.original_y,self.radius = x,y,radius
            self.manual_selection_scope = scope
            self._transform,self.crs = transform,crs
            self._rows,self._cols = dem.RasterYSize,dem.RasterXSize
            self._dem,self._directions = dem,directions
        finally:
            dem = directions = None

    def _set_step_range(self):
        maximum = max(self._rows,self._cols)
        if self.manual_selection_scope != 'raster_extent':
            maximum = math.ceil(min(maximum, 2*self.radius/min(self._transform[1],-self._transform[5])))
        self.step.setRange(1,max(1,min(2147483647,int(maximum))))

    def set_busy(self, busy, message=''):
        """Keep the modeless window while a full recalculation is pending."""
        if self._closed:
            return
        self._busy = bool(busy)
        if busy:
            self._stop_picking()
        for control in (self.north_button,self.south_button,self.east_button,
                        self.west_button,self.step,self.pick_button):
            control.setEnabled(not busy)
        self.apply_button.setEnabled(not busy and self._candidate is not None)
        if busy:
            self.status_label.setText(message or 'Recalculando la cuenca desde el cierre elegido…')
        elif message:
            self.status_label.setText(str(message))

    def rebind(self, context):
        """Resume from the applied result, never from an old captured context."""
        if self._closed:
            return
        self._stop_picking()
        self._load_context(context)
        self._remove_marker()
        self._candidate = self._selection = None
        self._row = self._col = None
        self._set_step_range()
        self.reason_label.setText(str(context.get('reason') or ''))
        self.reason_label.setVisible(bool(self.reason_label.text()))
        self.reference_label.setText(
            f"MDT {context.get('resolution_key','')} m · {self.crs.authid() or 'CRS nativo'}\n"
            f'Punto original: X={self.original_x:.3f}; Y={self.original_y:.3f}')
        self.set_busy(False)
        self._propose_point(context.get('initial_x',self.original_x),
                            context.get('initial_y',self.original_y))

    def selection(self):
        return self._selection

    def _cell_at(self, x, y):
        gt = self._transform
        return cell_at_point(float(x),float(y),gt[0],gt[3]+self._rows*gt[5],
                             gt[0]+self._cols*gt[1],gt[3],self._cols,self._rows)

    @staticmethod
    def _value(band, row, col, label):
        value = float(_read(band,col,row,1,1)[0,0])
        nodata = band.GetNoDataValue()
        if (not math.isfinite(value) or (nodata is not None and value == nodata)
                or (not band.GetMaskFlags() & gdal.GMF_ALL_VALID
                    and not _read(band.GetMaskBand(),col,row,1,1)[0,0])):
            raise ValueError(f'La celda no tiene datos válidos de {label} (NoData).')
        return value

    def _validate_cell(self, row, col):
        if not (0 <= row < self._rows and 0 <= col < self._cols):
            raise ValueError('El punto está fuera de la extensión del MDT; elige una celda con datos.')
        x,y = gdal.ApplyGeoTransform(self._transform,col+.5,row+.5)
        distance = math.hypot(x-self.original_x,y-self.original_y)
        if (self.manual_selection_scope != 'raster_extent'
                and distance > self.radius + max(1e-9,self.radius*1e-12)):
            raise ValueError('La celda supera el radio permitido respecto al punto original.')
        band = self._dem.GetRasterBand(1)
        height = self._value(band,row,col,'MDT')
        scale, offset = band.GetScale(), band.GetOffset()
        if not math.isfinite(height*(1 if scale is None else scale)+(0 if offset is None else offset)):
            raise ValueError('La celda no tiene una cota válida en el MDT.')
        direction = self._value(self._directions.GetRasterBand(1),row,col,'dirección D8')
        if direction not in _VALID_CODES:
            raise ValueError('La celda tiene un código de dirección D8 desconocido.')
        return float(x),float(y),distance

    def _invalid(self, error):
        self._candidate = None
        self.apply_button.setEnabled(False)
        self.status_label.setText(str(error))
        self._remove_marker()

    def _propose_cell(self, row, col):
        if self._closed or self._busy:
            return False
        # Keep an in-grid NoData cell as the arrow origin, so the user can
        # move out of it. Out-of-grid clicks are never clamped to another cell.
        if 0 <= row < self._rows and 0 <= col < self._cols:
            self._row,self._col = row,col
            x,y = gdal.ApplyGeoTransform(self._transform,col+.5,row+.5)
            self.coordinate_label.setText(f'Centro propuesto: X={x:.3f}; Y={y:.3f}')
            limit = ('· selección manual en la extensión válida del MDT.'
                     if self.manual_selection_scope == 'raster_extent' else f'· límite {self.radius:g} m.')
            self.distance_label.setText(
                f'Distancia al punto original: {math.hypot(x-self.original_x,y-self.original_y):.2f} m {limit}')
        try:
            x,y,distance = self._validate_cell(row,col)
            self._candidate = (x,y)
            if not self._refresh_marker():
                return False
            self.apply_button.setEnabled(True)
            self.status_label.setText(f'Celda válida · fila {row+1}, columna {col+1}.')
            return True
        except (ValueError, RuntimeError) as error:
            self._invalid(error)
            return False

    def _propose_point(self, x, y):
        if self._closed or self._busy:
            return False
        try:
            cell = self._cell_at(x,y)
            return self._propose_cell(cell.row,cell.col)
        except (ValueError, TypeError, OverflowError) as error:
            self._invalid(error)
            return False

    def move_cell(self, row_direction, col_direction):
        if self._closed or self._busy:
            return
        if self._row is None:
            self._invalid('Elige primero una celda dentro del MDT con «Elegir en el mapa».')
            return
        self._propose_cell(self._row+row_direction*self.step.value(),
                           self._col+col_direction*self.step.value())

    def _refresh_marker(self):
        if self._closed or self._candidate is None:
            return False
        try:
            transform = QgsCoordinateTransform(self.crs,self.canvas.mapSettings().destinationCrs(),QgsProject.instance())
            point = transform.transform(QgsPointXY(*self._candidate))
            if self._marker is None:
                self._marker = QgsVertexMarker(self.canvas)
                self._marker.setColor(QColor(0,70,180))
                self._marker.setIconType(QgsVertexMarker.ICON_CROSS)
                self._marker.setIconSize(14)
                self._marker.setPenWidth(3)
            self._marker.setCenter(point)
            return True
        except Exception:
            self._invalid('No se puede representar el cierre en el CRS actual del mapa.')
            return False

    def start_picking(self, *args):
        if self._closed or self._busy or (self._map_tool is not None and self.canvas.mapTool() is self._map_tool):
            return
        self._previous_tool = self.canvas.mapTool()
        if self._map_tool is None:
            self._map_tool = QgsMapToolEmitPoint(self.canvas)
            self._map_tool.canvasClicked.connect(self._map_clicked)
        self._picking = True
        self.canvas.setMapTool(self._map_tool)
        self.status_label.setText('Haz clic en el mapa; después revisa la celda y pulsa «Aplicar cierre».')

    def _map_clicked(self, point, button):
        if self._closed or self._busy or not self._picking or button != Qt.LeftButton:
            return
        try:
            transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(),self.crs,QgsProject.instance())
            native = transform.transform(point)
            self._propose_point(native.x(),native.y())
        except Exception as error:
            self._invalid(error)

    def _tool_changed(self, new_tool, old_tool):
        if not self._closed and self._picking and old_tool is self._map_tool and new_tool is not self._map_tool:
            # A user-selected replacement tool takes precedence over the tool
            # that was active before the selector. Do not switch it back.
            if self.persistent:
                self._stop_picking(restore=False)
                self.status_label.setText('Puedes seguir usando el mapa. Pulsa «Elegir en el mapa» para marcar otro cierre.')
            else:
                self.reject()

    def _remove_marker(self):
        marker,self._marker = self._marker,None
        if marker is not None:
            try:
                self.canvas.scene().removeItem(marker)
                sip.delete(marker)
            except RuntimeError:
                pass

    def _stop_picking(self, restore=True):
        self._picking = False
        tool,self._map_tool = self._map_tool,None
        if tool is not None:
            try:
                tool.canvasClicked.disconnect(self._map_clicked)
            except (TypeError, RuntimeError):
                pass
            try:
                if restore and self.canvas.mapTool() is tool:
                    if self._previous_tool is not None and not sip.isdeleted(self._previous_tool):
                        self.canvas.setMapTool(self._previous_tool)
                    else:
                        self.canvas.unsetMapTool(tool)
                tool.deleteLater()
            except RuntimeError:
                pass
        self._previous_tool = None

    def _cleanup(self):
        if self._closed:
            return
        self._closed = True
        for signal, slot in ((self.canvas.mapToolSet,self._tool_changed),
                             (self.canvas.destinationCrsChanged,self._refresh_marker)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._stop_picking()
        self._remove_marker()
        self._dem = self._directions = None

    def accept(self):
        if self._closed or self._busy or self._candidate is None:
            return
        try:
            x,y,_ = self._validate_cell(self._row,self._col)
        except (ValueError, RuntimeError) as error:
            self._invalid(error)
            return
        self._selection = (x,y)
        if self.persistent:
            self.set_busy(True)
            self.applied.emit()
            return
        self._cleanup()
        super().accept()

    def reject(self):
        if self._closed:
            return
        self._selection = None
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
