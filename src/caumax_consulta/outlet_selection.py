"""Choose a native D8 cell explicitly, without changing or recalculating data."""
import math
from pathlib import Path

from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEvent, QPoint, QRect, QSize, QTimer, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QStyle, QVBoxLayout, QWidget,
)
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker

from .basin import _aligned, _open_raster, _read, _VALID_CODES
from .grid import cell_at_point
from .native_decimal import format_decimal


class _HoverPointTool(QgsMapToolEmitPoint):
    """Observe mouse hover without changing QgsMapToolEmitPoint click handling."""

    def __init__(self, canvas, on_hover):
        super().__init__(canvas)
        self._on_hover = on_hover

    def canvasMoveEvent(self, event):
        self._on_hover(event.pos())


class _CursorMagnifier(QWidget):
    """Passive, circular 4× view of the existing QGIS map canvas.

    The source is captured from the *viewport*, while this lens is a sibling
    of the viewport. It therefore never captures itself or alters the canvas
    extent, map tool, cursor coordinates, layers or raster values.
    """

    DIAMETER = 120
    ZOOM = 4
    HOVER_DELAY_MS = 25

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setObjectName('HidroCAUMAXOutletMagnifier')
        self.canvas = canvas
        self.setFixedSize(self.DIAMETER, self.DIAMETER)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._cursor_pos = None
        self._sample = QPixmap()
        self._sample_target = QRect()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(self.HOVER_DELAY_MS)
        self._refresh_timer.timeout.connect(self._refresh)
        self.hide()

    def follow(self, viewport_position):
        viewport = self.canvas.viewport()
        point = QPoint(viewport_position)
        if not viewport.rect().contains(point):
            self.stop()
            return
        self._cursor_pos = point
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self):
        self._refresh_timer.stop()
        self._cursor_pos = None
        self.hide()

    def _refresh(self):
        cursor = self._cursor_pos
        if cursor is None:
            return
        viewport = self.canvas.viewport()
        if not viewport.rect().contains(cursor):
            self.stop()
            return
        source_diameter = self.DIAMETER // self.ZOOM
        source = QRect(cursor.x() - source_diameter // 2,
                       cursor.y() - source_diameter // 2,
                       source_diameter, source_diameter)
        visible_source = source.intersected(viewport.rect())
        if visible_source.isEmpty():
            self.stop()
            return
        # The lens is a child of canvas, not of canvas.viewport(): grabbing
        # the viewport captures only the rendered map, never the magnifier.
        snapshot = viewport.grab(visible_source)
        if snapshot.isNull():
            self.stop()
            return
        self._sample = snapshot
        self._sample_target = QRect(
            (visible_source.x() - source.x()) * self.ZOOM,
            (visible_source.y() - source.y()) * self.ZOOM,
            visible_source.width() * self.ZOOM,
            visible_source.height() * self.ZOOM)
        position = viewport.mapTo(self.canvas, cursor)
        separation = 18
        x = position.x() + separation
        if x + self.DIAMETER > self.canvas.width():
            x = position.x() - separation - self.DIAMETER
        x = max(0, min(x, max(0, self.canvas.width() - self.DIAMETER)))
        y = max(0, min(position.y() - self.DIAMETER // 2,
                       max(0, self.canvas.height() - self.DIAMETER)))
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        circle = QPainterPath()
        circle.addEllipse(1, 1, self.DIAMETER - 2, self.DIAMETER - 2)
        painter.setClipPath(circle)
        painter.fillRect(self.rect(), QColor('#EFF7FB'))
        if not self._sample.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(self._sample_target, self._sample)
        painter.setClipping(False)
        mid = self.DIAMETER // 2
        # Contrasting crosshair remains stationary over the exact hovered
        # map pixel; it is a visual aid and never changes the selected point.
        painter.setPen(QPen(QColor('#FFFFFF'), 3))
        painter.drawLine(mid - 13, mid, mid - 5, mid)
        painter.drawLine(mid + 5, mid, mid + 13, mid)
        painter.drawLine(mid, mid - 13, mid, mid - 5)
        painter.drawLine(mid, mid + 5, mid, mid + 13)
        painter.setPen(QPen(QColor('#0068A8'), 2))
        painter.drawLine(mid - 13, mid, mid - 5, mid)
        painter.drawLine(mid + 5, mid, mid + 13, mid)
        painter.drawLine(mid, mid - 13, mid, mid - 5)
        painter.drawLine(mid, mid + 5, mid, mid + 13)
        painter.setPen(QPen(QColor('#0D678F'), 2))
        painter.drawEllipse(2, 2, self.DIAMETER - 4, self.DIAMETER - 4)
        painter.setPen(QColor('#0D678F'))
        painter.drawText(QRect(mid + 30, mid + 32, 24, 16),
                         Qt.AlignCenter, '4×')
        painter.end()


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
        self._magnifier = None
        self._magnifier_active = False
        self._map_viewport = self.canvas.viewport()
        self._previous_mouse_tracking = None
        self._map_viewport.installEventFilter(self)
        self._picking = self._closed = False
        self._busy = False
        self.persistent = bool(persistent)
        self._candidate = self._selection = None
        self._row = self._col = None
        self._load_context(context)

        self.setWindowTitle('HidroCAUMAX · Ajuste Punto de Cierre')
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        introduction = QLabel(
            'El cierre se sitúa en un centro de celda del MDT activo. '
            'Para ajustarlo manualmente, usa las direcciones o «Elegir en el mapa». '
            'El cálculo se actualizará al pulsar «Aplicar cierre».', self)
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
            f"Punto original: X={format_decimal(self.original_x, '.3f')}; Y={format_decimal(self.original_y, '.3f')}", self)
        reference.setTextFormat(Qt.PlainText)
        reference.setWordWrap(True)
        self.reference_label = reference
        layout.addWidget(reference)
        self.coordinate_label = QLabel('Centro propuesto: sin selección válida.', self)
        self.distance_label = QLabel(f'Radio máximo: {format_decimal(self.radius)} m.', self)
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
        for button,row,col,dr,dc,standard_icon,caption in (
                (self.north_button,0,1,-1,0,QStyle.SP_ArrowUp,'Norte'),
                (self.south_button,2,1,1,0,QStyle.SP_ArrowDown,'Sur'),
                (self.east_button,1,2,0,1,QStyle.SP_ArrowRight,'Este'),
                (self.west_button,1,0,0,-1,QStyle.SP_ArrowLeft,'Oeste')):
            arrows.addWidget(button,row,col)
            button.setText('')
            button.setIcon(self.style().standardIcon(standard_icon))
            button.setIconSize(QSize(22, 22))
            button.setToolTip(caption + ' · desplazar el cierre ' +
                              str(self.step.value()) + ' celda(s)')
            button.setAccessibleName(caption)
            button.setMinimumHeight(36)
            button.clicked.connect(lambda checked=False,dr=dr,dc=dc:self.move_cell(dr,dc))
        self.pick_button = QPushButton('', self)
        self.pick_button.setIcon(QIcon(str(
            Path(__file__).resolve().parent / 'assets' / 'ajuste_cierre_modelo4.svg')))
        self.pick_button.setIconSize(QSize(24, 24))
        self.pick_button.setToolTip('Elegir en el mapa')
        self.pick_button.setAccessibleName('Elegir en el mapa')
        self.pick_button.setMinimumHeight(36)
        self.pick_button.clicked.connect(self.start_picking)
        arrows.addWidget(self.pick_button,1,1)
        layout.addLayout(arrows)
        self.allow_different_check = QCheckBox(
            'Aceptar expresamente una cuenca diferente de la consulta original', self)
        self.allow_different_check.setToolTip(
            'Solo para estudiar deliberadamente otra cuenca. El cierre elegido puede '
            'pertenecer a una ladera o un afluente distinto: no se considerará '
            'el refinamiento espacial de la cuenca original.')
        self.allow_different_check.setChecked(False)
        self.allow_different_check.setVisible(bool(context.get('verify_reference_overlap')))
        layout.addWidget(self.allow_different_check)
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
        # Qt's automatic dialog height can clip wrapped explanations and the
        # south button on smaller screens. Size from the complete layout.
        width = max(520, self.sizeHint().width())
        self.resize(width, layout.heightForWidth(width))

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
            if hasattr(self, 'allow_different_check'):
                self.allow_different_check.setChecked(False)
                self.allow_different_check.setVisible(bool(
                    context.get('verify_reference_overlap')))
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
            f"Punto original: X={format_decimal(self.original_x, '.3f')}; Y={format_decimal(self.original_y, '.3f')}")
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
            self.coordinate_label.setText(f"Centro propuesto: X={format_decimal(x, '.3f')}; Y={format_decimal(y, '.3f')}")
            limit = ('· selección manual en la extensión válida del MDT.'
                     if self.manual_selection_scope == 'raster_extent' else f'· límite {format_decimal(self.radius)} m.')
            self.distance_label.setText(
                f"Distancia al punto original: {format_decimal(math.hypot(x-self.original_x,y-self.original_y), '.2f')} m {limit}")
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
                from .delineation_cleanup import register_overlay
                self._marker = register_overlay(self.canvas, QgsVertexMarker(self.canvas))
                self._marker.setColor(QColor(0,70,180))
                self._marker.setIconType(QgsVertexMarker.ICON_CROSS)
                self._marker.setIconSize(14)
                self._marker.setPenWidth(3)
            self._marker.setCenter(point)
            return True
        except Exception:
            self._invalid('No se puede representar el cierre en el CRS actual del mapa.')
            return False

    def _map_hover(self, position):
        if (self._picking and self._magnifier_active and not self._closed
                and not self._busy and self._magnifier is not None):
            self._magnifier.follow(position)

    def eventFilter(self, watched, event):
        if watched is self._map_viewport:
            if event.type() == QEvent.MouseMove:
                # The QGIS map tool does not always receive unpressed moves
                # (notably on Qt5/offscreen). Listen to the viewport too;
                # no mouse event is consumed and native click handling stays.
                self._map_hover(event.pos())
            elif event.type() in (QEvent.Leave, QEvent.Hide):
                if self._magnifier is not None:
                    self._magnifier.stop()
        return super().eventFilter(watched, event)

    def start_picking(self, *args):
        if self._closed or self._busy:
            return
        if self._map_tool is not None and self.canvas.mapTool() is self._map_tool:
            # A second click on «Elegir en el mapa» re-enables the hover lens
            # after it was hidden by a previous point selection.
            self._magnifier_active = True
            return
        self._previous_tool = self.canvas.mapTool()
        if self._map_tool is None:
            self._map_tool = _HoverPointTool(self.canvas, self._map_hover)
            self._map_tool.canvasClicked.connect(self._map_clicked)
        if self._magnifier is None:
            self._magnifier = _CursorMagnifier(self.canvas)
        self._magnifier_active = True
        self._picking = True
        # Hover must be delivered with no mouse button pressed on Qt5/QGIS.
        self._previous_mouse_tracking = self._map_viewport.hasMouseTracking()
        self._map_viewport.setMouseTracking(True)
        self.canvas.setMapTool(self._map_tool)
        self.status_label.setText('Haz clic en el mapa; después revisa la celda y pulsa «Aplicar cierre».')

    def _map_clicked(self, point, button):
        if self._closed or self._busy or not self._picking or button != Qt.LeftButton:
            return
        # Retain QGIS's original clicked point, not the enlarged preview's
        # position. Stop the lens until another explicit map-selection action.
        self._magnifier_active = False
        if self._magnifier is not None:
            self._magnifier.stop()
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
        from .delineation_cleanup import remove_overlay
        marker,self._marker = self._marker,None
        remove_overlay(marker)

    def _stop_picking(self, restore=True):
        self._picking = False
        self._magnifier_active = False
        lens,self._magnifier = self._magnifier,None
        if lens is not None:
            lens.stop()
            lens.deleteLater()
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
        if self._previous_mouse_tracking is not None:
            try:
                self._map_viewport.setMouseTracking(self._previous_mouse_tracking)
            except RuntimeError:
                pass
            self._previous_mouse_tracking = None

    def _cleanup(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._map_viewport.removeEventFilter(self)
        except RuntimeError:
            pass
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
        # This opt-in never silently changes the selected native cell; it only
        # records permission to analyse a deliberately different watershed.
        self.context['allow_reference_mismatch'] = bool(
            self.allow_different_check.isChecked()
            and self.context.get('verify_reference_overlap'))
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
