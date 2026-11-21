"""Background area-weighted P0 and daily precipitation, bound to one basin."""
import time

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import QgsApplication, QgsTask


class _MeanTask(QgsTask):
    update = pyqtSignal(int, str, float)

    def __init__(self, generation, kind, key, analysis, sources, callback):
        super().__init__('HidroCAUMAX: P₀ y precipitación media de la cuenca', QgsTask.CanCancel)
        self.generation, self.kind, self.key = generation, kind, key
        self.analysis, self.sources, self.callback = analysis, sources, callback
        self.result = None
        self.error = ''
        self._last_update = 0.

    def publish(self, text, progress):
        if self.isCanceled():
            raise InterruptedError('Cálculo de medias cancelado.')
        progress = max(0., min(100., float(progress)))
        self.setProgress(progress)
        now = time.monotonic()
        if progress in (0., 100.) or now - self._last_update >= .1:
            self._last_update = now
            self.update.emit(self.generation, str(text), progress)

    def run(self):
        try:
            from .basin_hydrology import basin_raster_mean
            if self.kind == 'p0':
                self.result = basin_raster_mean(self.analysis, self.sources.get('p0', ''),
                    statistics=True,
                    cancelled=self.isCanceled,
                    progress=lambda percent: self.publish('Calculando P₀ medio…', percent))
            else:
                result = {}
                periods = (2, 5, 10, 25, 100, 500)
                for index, period in enumerate(periods):
                    if self.isCanceled():
                        raise InterruptedError('Cálculo de medias cancelado.')
                    text = f'Precipitación máxima diaria media · T = {period} años…'
                    self.publish(text, index * 100. / len(periods))
                    result[str(period)] = basin_raster_mean(self.analysis,
                        self.sources.get('rainfall', {}).get(str(period), ''),
                        statistics=True,profile=True,
                        cancelled=self.isCanceled,
                        progress=lambda percent, i=index, text=text:
                            self.publish(text, (i + percent / 100.) * 100. / len(periods)))
                self.result = result
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)


class BasinHydrologyController(QObject):
    def __init__(self, on_result, on_status, on_error, parent=None):
        super().__init__(parent)
        self.on_result, self.on_status, self.on_error = on_result, on_status, on_error
        self._task = None
        self._generation = 0
        self._disposed = False
        self.busy = False

    def calculate(self, kind, analysis, sources):
        if self._disposed:
            return
        if kind not in ('p0', 'rainfall'):
            raise ValueError('Tipo de media no reconocido.')
        from .basin_hydrology import basin_fingerprint
        self.cancel()
        key = basin_fingerprint(analysis)
        task = _MeanTask(self._generation, kind, key, analysis, sources, self._finished)
        task.update.connect(self._status)
        self._task = task
        self.busy = True
        self.on_status('Calculando medias sobre la cuenca delimitada…', 0.)
        QgsApplication.taskManager().addTask(task)

    def cancel(self):
        self._generation += 1
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self.busy = False

    def dispose(self):
        self.cancel()
        self._disposed = True

    def _status(self, generation, text, progress):
        if not self._disposed and generation == self._generation:
            self.on_status(text, progress)

    def _finished(self, task, success):
        if self._disposed or task is not self._task or task.generation != self._generation:
            return
        self._task = None
        self.busy = False
        if success:
            self.on_result(task.key, task.kind, task.result)
        else:
            self.on_error(task.error or 'Cálculo de medias cancelado.')
