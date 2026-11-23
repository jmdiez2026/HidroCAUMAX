"""One cancellable transaction for all data attached to a watershed surface."""
import traceback
import time
from dataclasses import replace

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsTask


class SurfaceTask(QgsTask):
    update = pyqtSignal(int, str, float)

    def __init__(self, generation, base, query, surface_wkb, degree, sources,
                 flow_query, flow_sources, callback, drainage_source=None):
        super().__init__('HidroCAUMAX: recalcular toda la cuenca', QgsTask.CanCancel)
        self.generation = generation
        self.base, self.query, self.surface_wkb = base, query, surface_wkb
        self.degree, self.sources = degree, sources
        self.flow_query, self.flow_sources, self.callback = flow_query, flow_sources, callback
        self.result = None
        self.drainage_source = drainage_source
        self.error = self.trace = ''
        self._last_update = 0.
        self._last_text = ''

    def publish(self, text, percent):
        if self.isCanceled():
            raise InterruptedError('Recálculo cancelado.')
        now = time.monotonic()
        if text != self._last_text or percent in (0, 100) or now-self._last_update >= .1:
            self._last_update, self._last_text = now, text
            self.setProgress(percent)
            self.update.emit(self.generation, text, float(percent))

    def run(self):
        try:
            from .terrain import analyze_basin
            from .basin_hydrology import basin_raster_mean
            from .flow_quantiles import sample_quantiles
            self.publish('Recalculando morfología e hipsometría…', 0)
            analysis = analyze_basin(self.query.directions_path, self.query.dem_path,
                self.base.outlet_x, self.base.outlet_y, surface_wkb=self.surface_wkb,
                cancelled=self.isCanceled,
                progress=lambda p: self.publish('Recalculando morfología e hipsometría…', p*.3))
            diagnostics = dict(self.base.diagnostics)
            for key in ('analysis_surface', 'terrain_valid_area_m2', 'terrain_weighting'):
                diagnostics.pop(key, None)
            diagnostics.update(analysis.diagnostics)
            if self.surface_wkb is not None:
                previous = self.base.diagnostics.get('analysis_surface') or {}
                diagnostics['analysis_surface'] = dict(kind='smoothed', degree=int(self.degree),
                    original_area_m2=previous.get('original_area_m2', self.base.area_m2),
                    original_perimeter_m=previous.get('original_perimeter_m', self.base.perimeter_m),
                    original_cell_count=previous.get('original_cell_count', self.base.cell_count))
            review = diagnostics.get('outlet_review_pending')
            notes = tuple(dict.fromkeys((*analysis.notes,review))) if review else analysis.notes
            analysis = replace(analysis, selected_x=self.base.selected_x, selected_y=self.base.selected_y,
                               diagnostics=diagnostics, notes=notes)
            p0 = basin_raster_mean(analysis, self.sources.get('p0', ''), statistics=True,
                cancelled=self.isCanceled, progress=lambda p: self.publish('Recalculando P₀…', 30+p*.1))
            rainfall = {}
            periods = (2, 5, 10, 25, 100, 500)
            for index, period in enumerate(periods):
                rainfall[str(period)] = basin_raster_mean(analysis,
                    self.sources.get('rainfall', {}).get(str(period), ''), statistics=True, profile=True,
                    cancelled=self.isCanceled,
                    progress=lambda p, i=index, t=period: self.publish(
                        f'Recalculando Pd · T = {t} años…', 40+(i+p/100)*9.5))
            self.publish('Actualizando cuantiles CAUMAX…', 98)
            quantiles = sample_quantiles(self.flow_query, self.flow_sources)
            from .surface_drainage import clip_network
            drainage = clip_network(self.drainage_source, analysis, self.isCanceled)
            self.result = dict(analysis=analysis, p0=p0, rainfall=rainfall, quantiles=quantiles,
                               drainage=drainage)
            self.publish('Recálculo completo.', 100)
            return not self.isCanceled()
        except InterruptedError:
            return False
        except Exception as error:
            self.error, self.trace = str(error), traceback.format_exc()
            return False

    def finished(self, success):
        self.callback(self, success)
