"""Instance-local, reversible support for HidroCAUMAX's fine D8 exports.

The installed CAUMAX files, class, globals and recursion limit stay untouched.
Only calculators created by a watched dialog from an explicitly tagged ESRI
D8 product receive an iterative equivalent of its recursive upstream walk.
"""
from dataclasses import dataclass, field
import functools
import sys
import types
import weakref

from osgeo import gdal
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QDialog


_MISSING = object()
_PRODUCTS = {'FLOW_DIRECTION', 'CAUMAX_FLOW_DIRECTIONS'}


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
    if not (0 <= x < cols and 0 <= y < rows):
        raise ValueError('La sección de cierre está fuera del MDT de cálculo.')
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
            if (0 <= nx < cols and 0 <= ny < rows
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
            if owned and self._running:
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
        call still reaches the original widget method without altering its
        arguments, return value, signals or the native calculation.
        """
        @functools.wraps(original)
        def observed(owner, *args, **kwargs):
            if not self._running or self._bindings.get(id(owner)) is not binding:
                return original(*args, **kwargs)
            event = dict(completed=False, ready_writes=[], flow_writes=[], period_writes=[])
            restorations = []

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
            try:
                result = original(*args, **kwargs)
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
