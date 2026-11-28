"""Recover native D8 contributing areas for consultations saved before v1.0.31.

An older consultation can contain exact Qci samples along its course without
per-cell drainage areas. Recompute the area only from available original D8
and DEM data. Never invent upstream areas from total catchment size.
"""
from pathlib import Path

from osgeo import osr
from qgis.core import QgsTask


def recover_course_areas(analysis, directions_path, dem_path,
                         cancelled=lambda: False):
    """Return original-course contributing-area bytes after consistency checks.

    The saved study, its morphology, hydrograph and actual original D8 course
    remain authoritative. If the recreated course differs, no areas are
    published rather than attaching a wrong denominator to Qci.
    """
    from .course_flow_profile import course_centres, main_course_areas
    from .terrain import analyze_basin

    existing = main_course_areas(analysis)
    if existing:
        return (analysis.diagnostics or {})['main_course_areas_b64']
    directions_path = str(directions_path or '').split('|', 1)[0]
    dem_path = str(dem_path or '').split('|', 1)[0]
    if not Path(directions_path).is_file() or not Path(dem_path).is_file():
        raise FileNotFoundError(
            'No están disponibles el MDT y las direcciones D8 originales.')
    if cancelled():
        raise InterruptedError('Recálculo de áreas aportantes cancelado.')
    saved_course = course_centres(analysis.main_course_wkb, analysis.crs_wkt)
    if not saved_course:
        raise ValueError('La consulta antigua no tiene un curso D8 recuperable.')

    surface = ((analysis.diagnostics or {}).get('analysis_surface') or {})
    surface_wkb = bytes(analysis.geometry_wkb) if surface else None
    rebuilt = analyze_basin(
        directions_path, dem_path, analysis.outlet_x, analysis.outlet_y,
        surface_wkb=surface_wkb, cancelled=cancelled,
        allow_different_extents=False)
    if cancelled():
        raise InterruptedError('Recálculo de áreas aportantes cancelado.')
    current_srs, new_srs = osr.SpatialReference(), osr.SpatialReference()
    if (current_srs.ImportFromWkt(analysis.crs_wkt) != 0
            or new_srs.ImportFromWkt(rebuilt.crs_wkt) != 0
            or not current_srs.IsSame(new_srs)):
        raise ValueError('El CRS del D8 original no coincide con la consulta guardada.')
    recovered_course = course_centres(rebuilt.main_course_wkb, rebuilt.crs_wkt)
    if len(recovered_course) != len(saved_course):
        raise ValueError('El curso D8 recalculado no coincide con el guardado.')
    for before, after in zip(saved_course, recovered_course):
        if any(abs(a - b) > 1e-6 for a, b in zip(before, after)):
            raise ValueError('El trazado D8 original ha cambiado; no se asignan áreas ajenas.')
    if abs(rebuilt.area_m2 - analysis.area_m2) > max(1., analysis.area_m2 * 1e-6):
        raise ValueError('La divisoria original no coincide con la cuenca guardada.')
    recovered = main_course_areas(rebuilt)
    if len(recovered) != len(saved_course):
        raise ValueError('Faltan áreas drenantes D8 de celdas del curso.')
    return rebuilt.diagnostics['main_course_areas_b64']


class LegacyD8AreaTask(QgsTask):
    """Cancellable legacy backfill on a worker; only callback touches QGIS."""

    def __init__(self, generation, analysis, directions_path, dem_path, callback):
        super().__init__('HidroCAUMAX: áreas drenantes del curso D8 antiguo',
                         QgsTask.CanCancel)
        self.generation = generation
        self.analysis = analysis
        self.directions_path = str(directions_path)
        self.dem_path = str(dem_path)
        self.callback = callback
        self.result = None
        self.error = ''

    def run(self):
        try:
            self.result = recover_course_areas(
                self.analysis, self.directions_path, self.dem_path,
                cancelled=self.isCanceled)
            return not self.isCanceled()
        except InterruptedError:
            self.error = 'Recálculo de áreas D8 cancelado.'
            return False
        except Exception as error:
            self.error = str(error)
            return False

    def finished(self, success):
        self.callback(self, success)
