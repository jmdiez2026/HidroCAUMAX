"""Own improved-MDT/TauDEM preparation independently of any basin window.

Only immutable query coordinates and source paths enter this workflow. Native
CAUMAX calculators, settings and results are never replaced by its products.
"""
from copy import deepcopy
from dataclasses import is_dataclass, replace
import math
from pathlib import Path

from qgis.PyQt.QtCore import QObject, pyqtSignal, pyqtSlot
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsTask

from .drainage_controller import DrainageController
from .resolution_controller import ResolutionController, _dem_point


def _source_signature(path):
    source = Path(path).expanduser().resolve()
    try:
        stat = source.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
    except OSError:
        stamp = None
    return str(source), stamp


def _query_snapshot(query):
    if not is_dataclass(query):
        raise ValueError('El contexto de capas debe contener una consulta de cálculo válida.')
    x, y = float(query.x), float(query.y)
    crs = QgsCoordinateReferenceSystem(query.crs)
    if not all(math.isfinite(value) for value in (x, y)) or not crs.isValid():
        raise ValueError('El punto y el sistema de referencia de cálculo no son válidos.')
    return replace(query, x=x, y=y, crs=crs,
                   dem_path=str(Path(query.dem_path).expanduser().resolve()),
                   directions_path=str(Path(query.directions_path).expanduser().resolve()))


def _query_signature(query):
    return (float(query.x), float(query.y), query.crs.toWkt(),
            _source_signature(query.dem_path), _source_signature(query.directions_path))


def _analyze_base(arguments, cancelled, publish):
    """Use the same containing-cell analysis as the original basin workflow."""
    directions, dem, source_wkt, x, y = arguments
    if cancelled():
        raise InterruptedError('Preparación de la cuenca de referencia cancelada.')
    publish('Preparando la cuenca de referencia del punto seleccionado…', 0.)
    # Transform to the actual direction raster, not an assumed national CRS.
    target_x, target_y = _dem_point(directions, source_wkt, x, y)
    from .terrain import analyze_basin
    analysis = analyze_basin(directions, dem, target_x, target_y,
                             cancelled=cancelled, upstream_only=False)
    if cancelled():
        raise InterruptedError('Preparación de la cuenca de referencia cancelada.')
    publish('Cuenca de referencia preparada.', 100.)
    return analysis


class _BaseTask(QgsTask):
    update = pyqtSignal(int, str, float)

    def __init__(self, generation, arguments, callback):
        super().__init__('HidroCAUMAX: referencia para capas de resolución mejorada', QgsTask.CanCancel)
        self.generation, self.arguments, self.callback = generation, arguments, callback
        self.result = None
        self.error = ''

    def publish(self, text, progress):
        if self.isCanceled():
            raise InterruptedError('Preparación de la cuenca de referencia cancelada.')
        self.update.emit(self.generation, str(text), float(progress))

    def run(self):
        try:
            self.result = _analyze_base(self.arguments, self.isCanceled, self.publish)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)


class RationalLayersController(QObject):
    """Coordinate existing resolution and TauDEM engines for an embedded panel.

    ``products`` and ``state`` describe the latest successful TauDEM result;
    per-resolution product caches remain in ``drainage.products``. Callbacks are
    on_options(list), on_result(key, products, state), on_status(text, progress)
    and on_error(text). Construction never starts work or calls a consumer.
    """
    def __init__(self, on_options=None, on_result=None, on_status=None, on_error=None, parent=None, on_basin=None):
        super().__init__(parent)
        self._on_options = on_options or (lambda options: None)
        self._on_result = on_result or (lambda key, products, state: None)
        self._on_status = on_status or (lambda text, progress=0.: None)
        self._on_error = on_error or (lambda text: None)
        self._on_basin = on_basin or (lambda key, analysis, query: None)
        self.query = None
        self.signature = None
        self.base_analysis = None
        self.resolution = None
        self.drainage = None
        self.options = []
        self.active_key = None
        self.products = None
        self.state = None
        self._states = {}
        self._task = None
        self._generation = 0
        self._prepare_generation = 0
        self._pending = None
        self._basin_pending = None
        self._disposed = False
        if parent is not None:
            parent.destroyed.connect(self.dispose)

    @property
    def busy(self):
        return bool(self._task is not None
                    or (self.resolution is not None and self.resolution.busy)
                    or (self.drainage is not None and self.drainage.busy))

    def _current(self, generation):
        return not self._disposed and generation == self._generation

    def _publish_options(self, options):
        if self._disposed:
            return
        self.options = deepcopy(options)
        self._on_options(deepcopy(self.options))

    def set_context(self, query):
        """Invalidate changed source data; repeated scans of one query do no work."""
        if self._disposed:
            return False
        try:
            snapshot = _query_snapshot(query) if query is not None else None
            signature = _query_signature(snapshot) if snapshot is not None else None
        except (AttributeError, TypeError, ValueError, OSError) as error:
            self.set_context(None)
            self._on_error(str(error))
            return False
        if signature == self.signature:
            return False
        self._generation += 1
        self._dispose_children()
        self.query, self.signature = snapshot, signature
        self.base_analysis = None
        self.active_key = None
        self.products = self.state = None
        self._states.clear()
        if snapshot is None:
            self._publish_options([])
        else:
            # Show the supported MDT sizes immediately while the reference
            # watershed is prepared asynchronously. Availability is confirmed
            # only by the later coverage-discovery callback.
            self._publish_options([
                {'key': key, 'resolution': float(key), 'available': False,
                 'status': 'Preparando cuenca de referencia'}
                for key in ('500', '25', '5', '2', '0.5')])
        if snapshot is None:
            self._on_status('Selecciona un punto de cálculo para preparar sus capas.', 0.)
            return True
        arguments = (snapshot.directions_path, snapshot.dem_path, snapshot.crs.toWkt(),
                     snapshot.x, snapshot.y)
        task = _BaseTask(self._generation, arguments, self._base_finished)
        task.update.connect(self._base_status)
        self._task = task
        self._on_status('Preparando la cuenca de referencia del punto seleccionado…', 0.)
        QgsApplication.taskManager().addTask(task)
        return True

    @pyqtSlot(int, str, float)
    def _base_status(self, generation, text, progress):
        if self._current(generation) and self._task is not None:
            self._on_status(text, progress)

    def _base_finished(self, task, success):
        if not self._current(task.generation) or task is not self._task:
            return
        self._task = None
        if not success:
            self._on_error(task.error or 'Preparación de la cuenca de referencia cancelada.')
            return
        if _query_signature(self.query) != self.signature:
            self.set_context(self.query)
            return
        try:
            generation = self._generation
            resolution = ResolutionController(task.result, self.query,
                lambda options: self._resolution_options(generation, options),
                lambda key, analysis, query: self._resolution_ready(generation, key, analysis, query),
                lambda text: self._resolution_status(generation, text),
                lambda text: self._resolution_failed(generation, text), parent=self)
            self.base_analysis = task.result
            self.resolution = resolution
            self.active_key = resolution.active_key
            self._publish_options(resolution.options)
            if not self._current(generation):
                return
            self._on_status('Cuenca de referencia preparada. Consultando resoluciones…', 100.)
            resolution.discover()
        except Exception as error:
            if self._current(task.generation):
                self._on_error(str(error))

    def _resolution_options(self, generation, options):
        if self._current(generation):
            self._publish_options(options)

    def _resolution_status(self, generation, text):
        if self._current(generation):
            progress = self.resolution.progress if self.resolution is not None else 0.
            self._on_status(str(text), float(progress))

    def _resolution_failed(self, generation, text):
        if not self._current(generation):
            return
        if self.resolution is None or self.resolution.outlet_selection_context is None:
            self._pending = None
        self._on_error(str(text))

    def select_resolution(self, key):
        """Delineate immediately with the verified reference-D8 algorithm.

        TauDEM network generation is intentionally NOT required here. A valid
        watershed must appear even if StreamNet later yields zero channels.
        """
        if self._disposed or self._task is not None or self.resolution is None:
            return False
        try:
            numeric = float(key)
            if not math.isfinite(numeric) or not 0 < numeric <= 500:
                raise ValueError('Selecciona la resolución original o un MDT mejorado.')
            key = format(numeric, '.12g')
            if _query_signature(self.query) != self.signature:
                self.set_context(self.query)
                return False
            base_key = format(min(self.base_analysis.cell_width, self.base_analysis.cell_height), '.12g')
            if key == base_key:
                # Reuse the CAUMAX native-resolution reference: do not download
                # or generate another terrain. During coverage discovery do not
                # cancel that task, so finer options can still become available.
                discovery = (self.resolution.busy and
                             getattr(self.resolution._task, 'kind', None) == 'discover')
                if not discovery:
                    self.cancel()
                else:
                    self._pending = self._basin_pending = None
                    if self.drainage is not None:
                        self.drainage.cancel()
                self.active_key = key
                self._on_basin(key, self.resolution.base_analysis,
                               self.resolution.base_query)
                return True
            self.cancel()
            self._basin_pending = key
            accepted = self.resolution.select(key)
            if not accepted:
                self._basin_pending = None
            return bool(accepted)
        except (AttributeError, OSError, TypeError, ValueError) as error:
            self._basin_pending = None
            self._on_error(str(error))
            return False

    def prepare(self, key, threshold_area_m2=None, automatic=True):
        """Prepare the chosen fine variant, then invoke the existing TauDEM route."""
        if self._disposed or self._task is not None or self.resolution is None:
            return False
        try:
            numeric = float(key)
            if not math.isfinite(numeric) or not 0. < numeric < 500.:
                raise ValueError('Selecciona una resolución mejorada, inferior a 500 m.')
            key = format(numeric, '.12g')
            if threshold_area_m2 is not None:
                threshold_area_m2 = float(threshold_area_m2)
                if not math.isfinite(threshold_area_m2) or threshold_area_m2 <= 0:
                    raise ValueError('El umbral de inicio de corriente debe ser positivo y finito.')
            if _query_signature(self.query) != self.signature:
                self.set_context(self.query)
                return False
            self.cancel()
            self._pending = (key, threshold_area_m2, bool(automatic), self._prepare_generation)
            self.products = self.state = None
            accepted = self.resolution.select(key)
            if not accepted and self.selection_context() is None:
                self._pending = None
            return bool(accepted)
        except Exception as error:
            self._pending = None
            self._on_error(str(error))
            return False

    def _resolution_ready(self, generation, key, analysis, query):
        if not self._current(generation):
            return
        if self._pending is None:
            if self._basin_pending != key:
                return
            self._basin_pending = None
            self.active_key = key
            self._on_basin(key, analysis, query)
            return
        pending_key, threshold, automatic, _ = self._pending
        if key != pending_key:
            return
        self.active_key = key
        # Publish the actual watershed before the optional TauDEM network.
        self._on_basin(key, analysis, query)
        try:
            if self.drainage is None:
                self.drainage = DrainageController(
                    lambda result_key, products, state: self._drainage_ready(generation, result_key, products, state),
                    lambda text, progress: self._drainage_status(generation, text, progress),
                    lambda text: self._drainage_failed(generation, text), parent=self)
            state = self._states.get(key)
            if not Path(query.dem_path).is_file() and state:
                exported = state.get('metadata', {}).get('exports', {}).get('dem', '')
                if exported and Path(exported).is_file():
                    query = replace(query, dem_path=exported)
            self.drainage.prepare(key, analysis, query, self.resolution.base_analysis,
                self.resolution.cache_dir/'taudem'/key, threshold_area_m2=threshold,
                automatic=automatic, encoding='caumax', bin_dir=None)
        except Exception as error:
            self._drainage_failed(generation, str(error))

    def _drainage_status(self, generation, text, progress):
        if self._current(generation) and self._pending is not None:
            self._on_status(str(text), float(progress))

    def _drainage_failed(self, generation, text):
        if self._current(generation):
            self._pending = None
            self._on_error(str(text))

    def _drainage_ready(self, generation, key, products, state):
        if (not self._current(generation) or self._pending is None
                or key != self._pending[0]):
            return
        if _query_signature(self.query) != self.signature:
            self.set_context(self.query)
            return
        self._pending = None
        self.active_key = key
        self.products, self.state = products, state
        self._states[key] = state
        # Keep the same active variant replacement as the existing basin panel,
        # confined to this controller; the native query remains immutable.
        variant = self.resolution.variants[key]
        variant.update(analysis=products.analysis, query=replace(variant['query'],
            dem_path=str(products.dem_path), directions_path=str(products.caumax_d8_path)),
            smoothing_state=None)
        self._on_basin(key, variant['analysis'], variant['query'])
        self._on_result(key, products, state)

    def selection_context(self):
        """Manual review is optional, including for a valid active delineation."""
        if self._disposed or self.resolution is None:
            return None
        if self._pending is None and self._basin_pending is None and self.active_key is None:
            return None
        try:
            context = self.resolution.selection_context()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        context['_rational_context_generation'] = self._generation
        context['_rational_prepare_generation'] = self._prepare_generation
        return context

    def select_outlet(self, context, x, y):
        if self._disposed or self.resolution is None:
            return False
        selected_key = str(context.get('resolution_key'))
        failed_key = ((self.resolution.outlet_selection_context or {}).get('resolution_key')
                      if self.resolution is not None else None)
        accepted_key = (self._pending[0] if self._pending else
                        self._basin_pending or failed_key or self.active_key)
        if (context.get('_rational_context_generation') != self._generation
                or context.get('_rational_prepare_generation') != self._prepare_generation
                or selected_key != accepted_key):
            self._on_error('La selección del cierre pertenece a un cálculo anterior.')
            return False
        prepared = deepcopy(context)
        prepared.pop('_rational_context_generation', None)
        prepared.pop('_rational_prepare_generation', None)
        # When network generation was not requested, manual adjustment updates
        # only the watershed; later D8 generation uses this verified closure.
        if self._pending is None:
            self._basin_pending = selected_key
        # A manual choice must not reuse products associated with another cell.
        if self.drainage is not None:
            self.drainage.dispose()
            self.drainage.deleteLater()
            self.drainage = None
        try:
            return bool(self.resolution.select_outlet(prepared, x, y))
        except Exception as error:
            self._on_error(str(error))
            return False

    def cancel(self):
        self._prepare_generation += 1
        self._pending = None
        self._basin_pending = None
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        if self.resolution is not None:
            self.resolution.cancel()
        if self.drainage is not None:
            self.drainage.cancel()

    def _dispose_children(self):
        self.cancel()
        for name in ('resolution', 'drainage'):
            controller = getattr(self, name)
            setattr(self, name, None)
            if controller is not None:
                controller.dispose()
                controller.deleteLater()

    def dispose(self, *args):
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        self._dispose_children()
        self._on_options = lambda options: None
        self._on_result = lambda key, products, state: None
        self._on_status = lambda text, progress=0.: None
        self._on_error = lambda text: None
        self._on_basin = lambda key, analysis, query: None
