"""Instance-local, reversible support for HidroCAUMAX's fine D8 exports.

The installed CAUMAX files, class, globals and recursion limit stay untouched.
Only calculators created by a watched dialog from an explicitly tagged ESRI
D8 product receive an iterative equivalent of its recursive upstream walk.
"""
from dataclasses import dataclass, field
import functools
import math
import sys
import types
import weakref

import numpy as np
from osgeo import gdal, osr
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QDialog
from qgis.core import Qgis, QgsMessageLog


_MISSING = object()
_PRODUCTS = {'FLOW_DIRECTION', 'CAUMAX_FLOW_DIRECTIONS'}
_FINE_RESOLUTIONS = (25., 5.)


class FineGridError(ValueError):
    """An actionable fine-grid mismatch, never a fabricated rational result."""


def _fine_resolution(calculator):
    try:
        cell = abs(float(calculator.cellsize))
        return next((value for value in _FINE_RESOLUTIONS
                     if math.isclose(cell, value, abs_tol=1e-7, rel_tol=0)), None)
    except (AttributeError, TypeError, ValueError):
        return None


def _fine_grid_issue(calculator, direction_path):
    """CAUMAX indexes D8 by DEM row/col: their georeferencing must agree.

    Differing *dimensions* are allowed when the raster origin, cell size and
    CRS coincide. Files and selections remain unchanged.
    """
    size = _fine_resolution(calculator)
    if size is None or not direction_path:
        return None
    raster = None
    try:
        raster = gdal.Open(str(direction_path), gdal.GA_ReadOnly)
        if raster is None or raster.RasterCount != 1:
            return f'No se puede leer el ráster D8 de {size:g} m seleccionado.'
        transform = tuple(raster.GetGeoTransform())
        terrain_transform = tuple(calculator.geoTransform)
        if (len(transform) != 6 or len(terrain_transform) != 6
                or any(not math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-7)
                       for a, b in zip(transform, terrain_transform))):
            return (f'El MDT y el D8 de {size:g} m no comparten origen y malla. '
                    'CAUMAX relaciona ambos por fila y columna; selecciona dos TIFF '
                    'georreferenciados sobre la misma malla, sin reproyectar el D8.')
        directions = np.asarray(calculator.dirs)
        if directions.shape != (raster.RasterYSize, raster.RasterXSize):
            return 'Las dimensiones del D8 cargado no corresponden al archivo seleccionado.'
        source_crs = osr.SpatialReference()
        dem_crs = osr.SpatialReference()
        if (source_crs.ImportFromWkt(raster.GetProjectionRef() or '') != 0
                or dem_crs.ImportFromWkt(str(calculator.crs)) != 0
                or not source_crs.IsSame(dem_crs)):
            return f'El MDT y el D8 de {size:g} m utilizan CRS diferentes o inválidos.'
        if raster.GetMetadataItem('FLOW_DIRECTION_ENCODING') == 'TAUDEM_D8':
            return ('Se ha configurado un D8 TauDEM sin recodificar. '
                    'CAUMAX necesita el TIFF de direcciones CAUMAX/ESRI '
                    'exportado desde HidroCAUMAX.')
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return f'No se pudo comprobar la compatibilidad del MDT y el D8 de {size:g} m.'
    finally:
        raster = None
    return None


def _fine_failure_message(calculator, error):
    """Report an invalid D8/relief with no made-up length or concentration time."""
    size = _fine_resolution(calculator)
    if size is None:
        return None
    try:
        distance = float(calculator.maxDistance)
        relief = float(calculator.maxH) - float(calculator.minH)
        if not math.isfinite(distance) or distance <= 0:
            return (f'No existe recorrido aguas arriba con longitud positiva '
                    f'en el cierre de {size:g} m (L=0). Comprueba el ráster D8 '
                    'CAUMAX/ESRI, la superposición de su malla con el MDT y '
                    'selecciona un punto de cierre que reciba aportaciones. '
                    'No se calcula Tc ni caudal con una longitud ficticia.')
        if not math.isfinite(relief) or relief <= 0:
            return (f'El recorrido de {size:g} m no tiene un desnivel positivo '
                    '(Hmáx − Hmín ≤ 0). Comprueba el MDT, el D8 y el cierre. '
                    'No se calcula Tc ni caudal con una pendiente ficticia.')
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return None


def _report_fine_failure(iface, message):
    QgsMessageLog.logMessage(str(message), 'HidroCAUMAX', Qgis.Warning)
    bar_method = getattr(iface, 'messageBar', None)
    if callable(bar_method):
        try:
            bar = bar_method()
            if bar is not None:
                bar.pushMessage('HidroCAUMAX', str(message),
                                level=Qgis.Warning, duration=12)
        except (AttributeError, RuntimeError, TypeError):
            pass


def _is_owned_directions(path):
    """An encoding alone never establishes ownership of another user's data."""
    try:
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
        if dataset is None or dataset.RasterCount != 1:
            return False
        return (dataset.GetDriver().ShortName == 'GTiff'
                and dataset.GetMetadataItem('HIDROCAUMAX_PRODUCT') in _PRODUCTS
                and dataset.GetMetadataItem('FLOW_DIRECTION_ENCODING') == 'ESRI_D8')
    except (RuntimeError, TypeError, ValueError):
        return False


def _process_cell_iterative(self, x, y, distance=0):
    """Preserve CAUMAX's preorder and arithmetic, adding bounds and cycle guards."""
    rows, cols = self.mdt.shape
    drows, dcols = self.dirs.shape
    if not (0 <= x < cols and 0 <= y < rows):
        raise ValueError('La sección de cierre está fuera del MDT de cálculo.')
    if not (0 <= x < dcols and 0 <= y < drows):
        raise FineGridError('El cierre no está cubierto por el ráster D8 seleccionado.')
    # Native calculate() creates this zero-filled array for every new run.
    # Reusing it as the visited mask avoids a large additional allocation.
    pending = [(x, y, distance)]
    while pending:
        cx, cy, travelled = pending.pop()
        if self.basinCells[cy, cx] != 0:
            continue
        coordx, coordy = gdal.ApplyGeoTransform(self.geoTransform, cx + .5, cy + .5)
        self.basinCells[cy, cx] = 1
        p0 = self.getValueAtCoordinate(coordx, coordy, 'P0')
        if p0 is not None and p0 > 0:
            self.p0Values.append(p0)
        i1id = self.getValueAtCoordinate(coordx, coordy, 'i1id')
        if i1id is not None and i1id > 0:
            self.i1idValues.append(i1id)
        for return_period in self.rainFiles:
            rain = self.getValueAtCoordinate(coordx, coordy, return_period)
            if rain is not None and rain > 0:
                self.rainValues[return_period].append(rain)
        height = self.mdt[cy, cx]
        if (travelled > self.maxDistance
                or (travelled == self.maxDistance and height < self.maxH)):
            self.maxDistance = travelled
            self.maxH = height
            self.xMaxDistance, self.yMaxDistance = coordx, coordy
        self.area += self.cellarea
        # Reversed push order exactly reproduces the recursive native DFS.
        for dx, dy, direction in reversed(self.directions):
            nx, ny = cx + dx, cy + dy
            if (0 <= nx < min(cols, dcols) and 0 <= ny < min(rows, drows)
                    and self.basinCells[ny, nx] == 0
                    and self.dirs[ny, nx] == direction):
                step = 1 if 0 in (dx, dy) else 1.414
                pending.append((nx, ny, travelled + step * self.cellsize))


def _cell(value):
    return (lambda: value).__closure__[0]


def _clone_calculation(original, make_factory):
    """Clone the native waitcursor closure with private calculator-factory globals."""
    owner = getattr(original, '__self__', None)
    function = getattr(original, '__func__', None)
    if owner is None or not isinstance(function, types.FunctionType):
        raise ValueError('El cálculo racional de esta versión no es compatible.')
    replacements = [0]
    memo = {}

    def clone(current):
        if current in memo:
            return memo[current]
        namespace = current.__globals__
        if ('BasinCalculator' in current.__code__.co_names
                and callable(namespace.get('BasinCalculator'))):
            namespace = namespace.copy()
            namespace['BasinCalculator'] = make_factory(namespace['BasinCalculator'])
            replacements[0] += 1
        closure = []
        for captured in current.__closure__ or ():
            try:
                value = captured.cell_contents
            except ValueError:
                closure.append(captured)
                continue
            closure.append(_cell(clone(value)) if isinstance(value, types.FunctionType) else captured)
        copied = types.FunctionType(current.__code__, namespace, current.__name__,
                                    current.__defaults__, tuple(closure) if closure else None)
        copied.__kwdefaults__ = (None if current.__kwdefaults__ is None
                                 else current.__kwdefaults__.copy())
        copied.__annotations__ = current.__annotations__.copy()
        copied.__dict__.update(current.__dict__)
        copied.__module__, copied.__qualname__ = current.__module__, current.__qualname__
        copied.__doc__ = current.__doc__
        memo[current] = copied
        return copied

    copied = clone(function)
    if not replacements[0]:
        raise ValueError('CAUMAX no expone la creación esperada de BasinCalculator.')
    return types.MethodType(copied, owner)


def _accepted_flow_with_two_decimals(dialog, event, direct, interpolated):
    """Return CAUMAX's accepted Q with real fractional precision when observed.

    Never infer a fine value from the rounded screen text alone. Only use a
    sampled native result if its integer rounding agrees with the value CAUMAX
    actually committed. A foreign implementation may use a different formula;
    in that case only format its own final accepted text to two decimals.
    """
    flow_writes = event.get('flow_writes') or ()
    period_writes = event.get('period_writes') or ()
    if (not event.get('completed') or not flow_writes or not period_writes
            or str(flow_writes[-1]) != dialog.txtResultFlow.text()
            or str(period_writes[-1]) != dialog.txtResultReturnPeriod.text()):
        return None
    try:
        reported = float(flow_writes[-1])
        period = float(period_writes[-1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(reported) or reported < 0 or not math.isfinite(period):
        return None
    # For a custom T the native function uses its interpolation result; its
    # preceding calls to getFlow() belong to the six supporting periods.
    candidate = (interpolated[-1] if interpolated else
                 direct[0] if len(direct) == 1 else reported)
    if (not math.isfinite(candidate) or candidate < 0
            or (not math.isclose(candidate, reported, abs_tol=.005, rel_tol=0)
                and round(candidate) != reported)):
        candidate = reported
    return f'{candidate:.2f}'


@dataclass
class _CalculatorBinding:
    reference: object
    previous: object
    replacement: object


@dataclass
class _DialogBinding:
    dialog: object
    previous: object
    replacement: object
    connections: list = field(default_factory=list)
    calculators: list = field(default_factory=list)
    flow_previous: object = _MISSING
    flow_replacement: object = None


class RationalInputsBridge(QObject):
    """Watch native rational dialogs without changing any module-level binding."""
    calculation_started = pyqtSignal(object, str)
    calculation_finished = pyqtSignal(object, str, object)

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
        self.scan()
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self._running = False
        self.timer.stop()
        for key in tuple(self._bindings):
            self._detach(key)

    def _factory(self, original, binding):
        @functools.wraps(original)
        def create(*args, **kwargs):
            module = sys.modules.get(original.__module__)
            path = None
            if module is not None:
                get_path = getattr(module, 'getLayerPath', None)
                if callable(get_path):
                    path = get_path(getattr(module, 'FLOWDIRS', 'FLOWDIRS'))
            owned = _is_owned_directions(path) if path else False
            calculator = original(*args, **kwargs)
            fine = _fine_resolution(calculator)
            if fine is not None and self._running:
                issue = _fine_grid_issue(calculator, path)
                if issue:
                    raise FineGridError(issue)
            if (owned or fine is not None) and self._running:
                previous = vars(calculator).get('_processCell', _MISSING)
                replacement = types.MethodType(_process_cell_iterative, calculator)
                calculator._processCell = replacement
                binding.calculators.append(_CalculatorBinding(
                    weakref.ref(calculator), previous, replacement))
            return calculator
        return create

    def _observe(self, binding, stage, original):
        """Observe completed native writes, including an unchanged accepted Q.

        Qt's textChanged signal omits identical writes. Recording the native
        setText call distinguishes a newly accepted result from an old result
        left visible after validation or confirmation was cancelled. Every
        call still reaches the original widget method. After an accepted flow
        calculation we display the captured unrounded Q to two decimals;
        native CAUMAX formulas and its installed files remain untouched.
        """
        @functools.wraps(original)
        def observed(owner, *args, **kwargs):
            if not self._running or self._bindings.get(id(owner)) is not binding:
                return original(*args, **kwargs)
            event = dict(completed=False, ready_writes=[], flow_writes=[], period_writes=[])
            restorations = []
            direct_flows, interpolated_flows, requested_periods = [], [], []

            def capture_method(name, values):
                # Capture the unrounded native calculation. This is a local,
                # reversible instance hook, not a replacement of CAUMAX's code.
                method = getattr(owner, name, None)
                if not callable(method):
                    return
                previous = vars(owner).get(name, _MISSING)

                def observing(*call_args, **call_kwargs):
                    value = method(*call_args, **call_kwargs)
                    try:
                        numeric = float(value)
                        if math.isfinite(numeric):
                            values.append(numeric)
                    except (TypeError, ValueError, OverflowError):
                        pass
                    return value

                try:
                    setattr(owner, name, observing)
                    restorations.append((owner, name, previous, observing))
                except (AttributeError, RuntimeError, TypeError):
                    pass

            def record(attribute, method_name, key):
                widget = getattr(owner, attribute, None)
                method = getattr(widget, method_name, None)
                if not callable(method):
                    return
                previous = vars(widget).get(method_name, _MISSING)

                def recording(*call_args, **call_kwargs):
                    result = method(*call_args, **call_kwargs)
                    if call_args:
                        event[key].append(call_args[0])
                    return result

                try:
                    setattr(widget, method_name, recording)
                    restorations.append((widget, method_name, previous, recording))
                except (AttributeError, RuntimeError, TypeError):
                    return

            self.calculation_started.emit(owner, stage)
            if stage == 'parameters':
                record('btnCalculate', 'setEnabled', 'ready_writes')
            else:
                record('txtResultFlow', 'setText', 'flow_writes')
                record('txtResultReturnPeriod', 'setText', 'period_writes')
                capture_method('getFlow', direct_flows)
                capture_method('_getInterpolatedFlow', interpolated_flows)
                capture_method('returnPeriod', requested_periods)
            try:
                try:
                    result = original(*args, **kwargs)
                except (FineGridError, ZeroDivisionError, ValueError) as error:
                    calculator = getattr(owner, 'basinCalc', None)
                    message = (str(error) if isinstance(error, FineGridError)
                               else _fine_failure_message(calculator, error)
                               if stage == 'parameters' and calculator is not None
                               else None)
                    if not message:
                        raise
                    event['failure_reason'] = message
                    _report_fine_failure(self.iface, message)
                    # CAUMAX has disabled Calcular while the basin is invalid.
                    # Do not publish a stale basin or an invented Tc.
                    return None
                event['completed'] = True
                return result
            finally:
                for widget, name, previous, replacement in reversed(restorations):
                    try:
                        if vars(widget).get(name) is replacement:
                            if previous is _MISSING:
                                delattr(widget, name)
                            else:
                                setattr(widget, name, previous)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                if self._running and self._bindings.get(id(owner)) is binding:
                    if stage == 'flow':
                        precise = _accepted_flow_with_two_decimals(
                            owner, event, direct_flows, interpolated_flows)
                        if precise is not None:
                            # The native result is always dot-decimal for its
                            # own readers. The Spanish overlay displays a comma.
                            # Record the new accepted write for the basin bridge.
                            try:
                                owner.txtResultFlow.setText(precise)
                                event['flow_writes'].append(precise)
                                candidate = (interpolated_flows[-1] if interpolated_flows else
                                             direct_flows[0] if len(direct_flows) == 1 else float(precise))
                                event['accepted_flow'] = (candidate if math.isclose(
                                    candidate, float(precise), abs_tol=.00501, rel_tol=0) else float(precise))
                                event['accepted_period'] = (requested_periods[-1] if requested_periods
                                                            else float(event['period_writes'][-1]))
                                event['supporting_flows'] = tuple(direct_flows) if interpolated_flows else ()
                            except RuntimeError:
                                pass
                    self.calculation_finished.emit(owner, stage, event)

        return types.MethodType(observed, binding.dialog)

    def scan(self):
        if not self._running:
            return
        for dialog in QApplication.topLevelWidgets():
            try:
                native = any(base.__name__ == 'RationalMethodDialog'
                             and base.__module__.endswith('.rationalmethoddialog')
                             for base in type(dialog).__mro__)
                if (not native or not isinstance(dialog, QDialog) or not dialog.isVisible()
                        or id(dialog) in self._bindings or not hasattr(dialog, 'basinCalc')):
                    continue
                # Preserve a foreign instance-level adaptation rather than adopting it.
                if 'calculateParameters' in vars(dialog):
                    continue
                previous = dialog.calculateParameters
                binding = _DialogBinding(dialog, _MISSING, None)
                try:
                    adapted = _clone_calculation(previous, lambda cls: self._factory(cls, binding))
                except ValueError:
                    continue
                replacement = self._observe(binding, 'parameters', adapted)
                binding.replacement = replacement
                dialog.calculateParameters = replacement
                original_flow = getattr(dialog, 'calculate', None)
                if callable(original_flow) and 'calculate' not in vars(dialog):
                    binding.flow_replacement = self._observe(binding, 'flow', original_flow)
                    dialog.calculate = binding.flow_replacement
                key = id(dialog)
                closed = lambda *args, current=key: self._detach(current)
                for signal in (dialog.finished, dialog.destroyed):
                    signal.connect(closed)
                    binding.connections.append((signal, closed))
                self._bindings[key] = binding
            except (AttributeError, RuntimeError, TypeError):
                continue

    def _detach(self, key):
        binding = self._bindings.pop(key, None)
        if binding is None:
            return
        for signal, callback in binding.connections:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        try:
            if vars(binding.dialog).get('calculateParameters') is binding.replacement:
                if binding.previous is _MISSING:
                    del binding.dialog.calculateParameters
                else:
                    binding.dialog.calculateParameters = binding.previous
            if (binding.flow_replacement is not None
                    and vars(binding.dialog).get('calculate') is binding.flow_replacement):
                if binding.flow_previous is _MISSING:
                    del binding.dialog.calculate
                else:
                    binding.dialog.calculate = binding.flow_previous
        except RuntimeError:
            pass
        for owned in binding.calculators:
            calculator = owned.reference()
            if calculator is not None and vars(calculator).get('_processCell') is owned.replacement:
                if owned.previous is _MISSING:
                    del calculator._processCell
                else:
                    calculator._processCell = owned.previous
