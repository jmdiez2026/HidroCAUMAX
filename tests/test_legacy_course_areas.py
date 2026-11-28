"""Recover authentic D8 contributing areas for pre-v1.0.31 consultations."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsApplication

from caumax_consulta.course_flow_profile import course_centres, main_course_areas
from caumax_consulta.legacy_course_areas import LegacyD8AreaTask, recover_course_areas
from caumax_consulta.terrain import analyze_basin


class LegacyCourseAreasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hc-legacy-d8-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        self.wkt = srs.ExportToWkt()
        self.directions = self.directory / 'd8.tif'
        self.dem = self.directory / 'dem.tif'
        self.raster(self.directions, np.array([
            [0,0,0,0,0],
            [0,2,4,8,0],
            [0,1,0,16,0],
            [0,128,64,32,0],
            [0,0,0,0,0],
        ], dtype=np.uint8), gdal.GDT_Byte, 255)
        self.raster(self.dem, 100 + np.arange(25, dtype=np.float32).reshape(5,5),
                    gdal.GDT_Float32, -9999.)
        self.analysis = analyze_basin(
            str(self.directions), str(self.dem), 500062.5, 4699937.5)
        self.expected = main_course_areas(self.analysis)
        self.assertGreaterEqual(len(self.expected), 2)
        self.legacy = replace(self.analysis, diagnostics={
            k: v for k, v in self.analysis.diagnostics.items()
            if k not in ('main_course_areas_b64', 'main_course_area_at_closure_km2')
        })
        self.assertEqual(main_course_areas(self.legacy), [])

    def raster(self, path, values, dtype, nodata):
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, dtype)
        dataset.SetGeoTransform((500000., 25., 0., 4700000., 0., -25.))
        dataset.SetProjection(self.wkt)
        dataset.GetRasterBand(1).WriteArray(values)
        dataset.GetRasterBand(1).SetNoDataValue(nodata)
        dataset = None

    def test_exact_native_d8_areas_reconstructed_from_originals(self):
        packed = recover_course_areas(
            self.legacy, str(self.directions), str(self.dem))
        self.assertEqual(packed, self.analysis.diagnostics['main_course_areas_b64'])
        received = main_course_areas(SimpleNamespace(diagnostics={
            'main_course_areas_b64':packed}))
        self.assertEqual(received, self.expected)
        self.assertGreater(received[0], received[-1])
        self.assertEqual(len(received), len(course_centres(
            self.legacy.main_course_wkb, self.legacy.crs_wkt)))

    def test_worker_returns_only_exact_areas_without_editing_saved_analysis(self):
        callbacks = []
        task = LegacyD8AreaTask(
            7, self.legacy, self.directions, self.dem,
            lambda finished, ok: callbacks.append((finished, ok)))
        self.assertTrue(task.run(), task.error)
        task.finished(True)
        self.assertEqual(callbacks, [(task, True)])
        self.assertEqual(task.result, self.analysis.diagnostics['main_course_areas_b64'])
        self.assertNotIn('main_course_areas_b64', self.legacy.diagnostics)

    def test_missing_original_data_never_produces_fabricated_areas(self):
        with self.assertRaises(FileNotFoundError):
            recover_course_areas(self.legacy, self.directions,
                                 self.directory / 'missing-dem.tif')
        with self.assertRaises(InterruptedError):
            recover_course_areas(self.legacy, self.directions, self.dem,
                                 cancelled=lambda: True)
        self.assertEqual(main_course_areas(self.legacy), [])

    def test_changed_native_course_is_rejected(self):
        from qgis.core import QgsGeometry, QgsPointXY
        saved = course_centres(self.legacy.main_course_wkb, self.legacy.crs_wkt)
        altered = [QgsPointXY(x, y) for _, x, y in saved]
        altered[-1] = QgsPointXY(altered[-1].x() + 25., altered[-1].y())
        invalid = replace(self.legacy, main_course_wkb=bytes(
            QgsGeometry.fromPolylineXY(altered).asWkb()))
        with self.assertRaisesRegex(ValueError, 'curso D8|trazado D8'):
            recover_course_areas(invalid, self.directions, self.dem)


class LegacySavedConsultationRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_drainage_dialog as fixture
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        import test_drainage_dialog as fixture
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()
        self.fixture.doCleanups()

    def test_old_json_reopens_and_recovers_linked_specific_discharge(self):
        from unittest.mock import patch
        from caumax_consulta.session_store import save_session, load_session
        from caumax_consulta.basin_dialog import BasinDialog
        from caumax_consulta.course_flow_profile import course_centres

        original = self.fixture.dialog
        legacy = replace(original.analysis, diagnostics={
            key: value for key, value in original.analysis.diagnostics.items()
            if key not in ('main_course_areas_b64',
                           'main_course_area_at_closure_km2')})
        centres = course_centres(legacy.main_course_wkb, legacy.crs_wkt)
        self.assertGreaterEqual(len(centres), 2)
        state = original.get_smoothing_state()
        state['course_flow_profile'] = {
            'version': 1, 'selected': 0,
            'rows': [[distance, x, y, [10., 20., 30., 40., 50., 60.]]
                     for distance, x, y in centres]}
        destination = self.fixture.folder / 'old_consultation_without_areas.json'
        save_session(destination, legacy, original.query, state)
        saved = load_session(destination)
        self.assertNotIn('main_course_areas_b64', saved.analysis.diagnostics)

        tasks = []
        manager = SimpleNamespace(addTask=lambda task: tasks.append(task))
        with patch('caumax_consulta.basin_dialog.QgsApplication.taskManager',
                   return_value=manager):
            dialog = self.fixture.make_dialog(saved)
            recovery = [task for task in tasks if isinstance(task, LegacyD8AreaTask)]
            self.assertEqual(len(recovery), 1)
            task = recovery[0]
            self.assertTrue(task.run(), task.error)
            task.finished(True)
        self.assertIsNotNone(dialog.analysis.diagnostics.get('main_course_areas_b64'))
        graph = dialog.quantiles_widget.course_profile
        self.assertEqual(len(graph._specific_values), len(centres))
        self.assertTrue(all(value is not None for value in graph._specific_values))
        self.assertTrue(graph.specific_graph.isVisible() or
                        not dialog.isVisible())
        # Saving this reopened legacy consultation now retains its real D8
        # denominators. No old caudal samples have been recomputed or changed.
        new_state = dialog.get_smoothing_state()
        new_state['course_flow_profile'] = graph.snapshot()
        updated = self.fixture.folder / 'legacy_upgraded.json'
        save_session(updated, dialog.analysis, dialog.query, new_state)
        reopened = load_session(updated)
        self.assertIn('main_course_areas_b64', reopened.analysis.diagnostics)
        self.assertEqual(reopened.smoothing_state['course_flow_profile']['rows'],
                         state['course_flow_profile']['rows'])


if __name__ == '__main__':
    unittest.main()
