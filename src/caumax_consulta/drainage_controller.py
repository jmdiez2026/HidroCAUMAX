"""Cancelable TauDEM products, isolated from the GUI and native CAUMAX settings."""
import hashlib
import time
from pathlib import Path

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import QgsApplication, QgsTask


def _refinement_reference(analysis, reference):
    """Use the actual footprint that certified this fine outlet, when present."""
    snap = (analysis.diagnostics or {}).get('outlet_snap') or {}
    if snap.get('method') == 'reference_basin_d8' and snap.get('reference_geometry_wkb_hex'):
        return dict(geometry_wkb=bytes.fromhex(snap['reference_geometry_wkb_hex']),
            main_course_wkb=bytes.fromhex(snap.get('reference_course_wkb_hex') or ''),
            crs_wkt=snap['reference_crs_wkt'], cell_width=float(reference.cell_width),
            cell_height=float(reference.cell_height))
    return dict(geometry_wkb=bytes(reference.geometry_wkb),
        main_course_wkb=bytes(reference.main_course_wkb or b''), crs_wkt=str(reference.crs_wkt),
        cell_width=float(reference.cell_width), cell_height=float(reference.cell_height))


def _signature(source, analysis, reference, footprint, bin_dir):
    """A red query point does not identify a later manual/validated closure."""
    stat = source.stat()
    snap = (analysis.diagnostics or {}).get('outlet_snap') or {}
    digest = hashlib.sha256()
    for value in (analysis.geometry_wkb, analysis.main_course_wkb,
                  footprint['geometry_wkb'], footprint['main_course_wkb']):
        raw = bytes(value or b'')
        digest.update(len(raw).to_bytes(8, 'big'))
        digest.update(raw)
    digest.update(str(footprint['crs_wkt']).encode('utf-8'))
    return (str(source.resolve()), stat.st_size, stat.st_mtime_ns,
        float(analysis.selected_x), float(analysis.selected_y),
        float(analysis.outlet_x), float(analysis.outlet_y), str(analysis.crs_wkt),
        float(analysis.cell_width), float(analysis.cell_height),
        snap.get('method') == 'manual_cell_d8' and snap.get('explicit_user_choice') is True,
        float(reference.area_m2), float(reference.cell_width), float(reference.cell_height),
        digest.hexdigest(), str(bin_dir) if bin_dir is not None else None)


def _state(products, automatic, encoding):
    from .drainage_layers import read_network_snapshot
    snapshot = read_network_snapshot(products)
    analysis = products.analysis
    recommended = max(100 * analysis.cell_width * analysis.cell_height, .01 * analysis.area_m2)
    state = dict(metadata=dict(products.metadata),
        threshold_area_m2=float(products.threshold_area_m2),
        recommended_threshold_area_m2=float(recommended), automatic=bool(automatic),
        flow_encoding=encoding, dem_path=str(products.dem_path),
        tau_d8_path=str(products.tau_d8_path), caumax_d8_path=str(products.caumax_d8_path),
        network_path=str(products.network_path), network_crs_wkt=snapshot['network_crs_wkt'],
        features=snapshot['features'])
    for key in ('fields','network_visible','directions_visible'):
        if key in snapshot:state[key]=snapshot[key]
    return state


class _ProductsTask(QgsTask):
    update = pyqtSignal(int, str, float)

    def __init__(self, generation, operation, callback):
        super().__init__('HidroCAUMAX: capas TauDEM para el Método Racional', QgsTask.CanCancel)
        self.generation, self.operation, self.callback = generation, operation, callback
        self.result = None
        self.error = ''
        self._last_update = 0.

    def publish(self, text, progress):
        if self.isCanceled():
            raise InterruptedError('Preparación de capas cancelada.')
        percent = max(0., min(100., float(progress)))
        self.setProgress(percent)
        now = time.monotonic()
        if percent in (0.,100.) or now-self._last_update >= .08:
            self._last_update = now
            self.update.emit(self.generation, str(text), percent)

    def run(self):
        try:
            self.result = self.operation(self.isCanceled,self.publish)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self,success)


class DrainageController(QObject):
    def __init__(self, on_result, on_status, on_error, parent=None):
        super().__init__(parent)
        self.on_result, self.on_status, self.on_error = on_result,on_status,on_error
        self._task = None
        self._generation = 0
        self._disposed = False
        self.products = {}
        self._signatures = {}
        self.busy = False

    def cancel(self):
        self._generation += 1
        task,self._task = self._task,None
        if task is not None: task.cancel()
        self.busy = False

    def dispose(self):
        self.cancel()
        self._disposed = True

    def prepare(self, key, analysis, query, reference, cache_dir, *,
                threshold_area_m2=None, automatic=True, encoding='caumax', bin_dir=None):
        if self._disposed: return
        self.cancel()
        generation = self._generation
        path = str(query.dem_path)
        original_x,original_y = float(analysis.selected_x),float(analysis.selected_y)
        source = Path(path)
        footprint = _refinement_reference(analysis,reference)
        signature = _signature(source,analysis,reference,footprint,bin_dir)
        previous = self.products.get(key) if self._signatures.get(key) == signature else None

        def operation(cancelled,publish):
            from .taudem_engine import prepare_taudem,rebuild_network
            if (previous is not None and Path(previous.dem_path).resolve() == Path(path).resolve()
                    and threshold_area_m2 is not None):
                products = rebuild_network(previous,float(threshold_area_m2),
                    cancelled=cancelled,progress=publish)
            else:
                products = prepare_taudem(path,original_x,original_y,Path(cache_dir),
                    reference_area_m2=float(reference.area_m2),
                    reference_cell_width=float(reference.cell_width),
                    reference_cell_height=float(reference.cell_height),
                    outlet_analysis=analysis,refinement_reference=footprint,
                    threshold_area_m2=None if automatic else threshold_area_m2,
                    bin_dir=bin_dir,cancelled=cancelled,progress=publish)
            if cancelled(): raise InterruptedError('Preparación de capas cancelada.')
            return products,_state(products,automatic,encoding)

        task = _ProductsTask(generation,operation,
            lambda current,success:self._finished(key,current,success))
        task.update.connect(self._status)
        task.signature = signature
        self._task = task
        self.busy = True
        self.on_status('Preparando las capas con TauDEM…',0.)
        QgsApplication.taskManager().addTask(task)

    def _status(self, generation, text, progress):
        if not self._disposed and generation == self._generation:
            self.on_status(text,progress)

    def _finished(self,key,task,success):
        if self._disposed or task is not self._task or task.generation != self._generation:
            return
        self._task = None
        self.busy = False
        if not success:
            self.on_error(task.error or 'Preparación de capas cancelada.')
            return
        products,state = task.result
        self.products[key] = products
        self._signatures[key] = task.signature
        self.on_result(key,products,state)
