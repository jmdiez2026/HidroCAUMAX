"""Cancelable P0/rainfall tasks publish complete results for one exact basin."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.core import QgsApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.basin_hydrology_controller import BasinHydrologyController
from caumax_consulta.basin_hydrology import basin_fingerprint


class BasinHydrologyControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.analysis = SimpleNamespace(geometry_wkb=b'controlled-geometry', crs_wkt='controlled-crs')
        self.sources = {'p0': 'p0.tif', 'rainfall': {str(p): f't{p}.tif' for p in (2, 5, 10, 25, 100, 500)}}
        self.results, self.statuses, self.errors = [], [], []
        self.controller = BasinHydrologyController(
            lambda *args: self.results.append(args),
            lambda *args: self.statuses.append(args),
            lambda text: self.errors.append(text))
        self.manager = SimpleNamespace(tasks=[], addTask=lambda task: self.manager.tasks.append(task))
        self.patcher = patch('caumax_consulta.basin_hydrology_controller.QgsApplication.taskManager', return_value=self.manager)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.controller.dispose)

    def test_p0_publishes_one_complete_result_with_exact_basin_identity(self):
        self.controller.calculate('p0', self.analysis, self.sources)
        self.assertTrue(self.controller.busy)
        task = self.manager.tasks[-1]
        value = {'mean_mm': 0., 'source_path': 'p0.tif'}
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean', return_value=value) as mean:
            success = task.run()
        task.finished(success)
        self.assertEqual(mean.call_args.args, (self.analysis, 'p0.tif'))
        self.assertEqual(self.results, [(basin_fingerprint(self.analysis), 'p0', value)])
        self.assertFalse(self.controller.busy)
        self.assertFalse(self.errors)

    def test_rainfall_runs_all_periods_and_never_reads_original_clicked_cell(self):
        self.controller.calculate('rainfall', self.analysis, self.sources)
        task = self.manager.tasks[-1]
        paths = []
        def mean(analysis, path, *, cancelled, progress, statistics, profile):
            self.assertIs(analysis, self.analysis)
            self.assertTrue(statistics)
            self.assertTrue(profile)
            self.assertFalse(cancelled())
            paths.append(path)
            progress(50.)
            return {'source_path': path, 'mean_mm': float(len(paths))}
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean', side_effect=mean):
            task.finished(task.run())
        self.assertEqual(paths, ['t2.tif', 't5.tif', 't10.tif', 't25.tif', 't100.tif', 't500.tif'])
        self.assertEqual(list(self.results[0][2]), ['2', '5', '10', '25', '100', '500'])
        self.assertTrue(all(0 <= value <= 100 for _, value in self.statuses))

    def test_automatic_combined_worker_publishes_p0_and_six_pd_in_one_result(self):
        self.controller.calculate('both', self.analysis, self.sources)
        self.assertTrue(self.controller.busy)
        task = self.manager.tasks[-1]
        self.assertEqual(task.kind, 'both')
        called = []
        def mean(analysis, path, **kwargs):
            self.assertIs(analysis, self.analysis)
            called.append(path)
            return {'source_path': path, 'mean_mm': float(len(called))}
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean',
                   side_effect=mean):
            success = task.run()
        self.assertTrue(success, task.error)
        task.finished(success)
        self.assertEqual(len(self.results), 1)
        key, kind, value = self.results[0]
        self.assertEqual(key, basin_fingerprint(self.analysis))
        self.assertEqual(kind, 'both')
        self.assertEqual(value['p0']['mean_mm'], 1.)
        self.assertEqual(list(value['rainfall']), ['2','5','10','25','100','500'])
        self.assertEqual(called, ['p0.tif'] + [f't{p}.tif' for p in (2,5,10,25,100,500)])
        self.assertFalse(self.controller.busy)

    def test_automatic_combined_worker_failure_never_publishes_half_result(self):
        self.controller.calculate('both', self.analysis, self.sources)
        task = self.manager.tasks[-1]
        count = [0]
        def fail(*args, **kwargs):
            count[0] += 1
            if count[0] == 3:
                raise RuntimeError('No se puede leer Pd(5)')
            return {'mean_mm': 20}
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean',
                   side_effect=fail):
            self.assertFalse(task.run())
        task.finished(False)
        self.assertEqual(self.results, [])
        self.assertTrue(any('No se puede leer Pd(5)' in err for err in self.errors))
        self.assertFalse(self.controller.busy)

    def test_failure_during_rainfall_does_not_publish_an_incomplete_series(self):
        self.controller.calculate('rainfall', self.analysis, self.sources)
        task = self.manager.tasks[-1]
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean',
                   side_effect=[{'mean_mm': 10}, RuntimeError('Raster read failed')]):
            task.finished(task.run())
        self.assertFalse(self.results)
        self.assertEqual(self.errors, ['Raster read failed'])
        self.assertFalse(self.controller.busy)

    def test_late_result_from_canceled_previous_basin_is_ignored(self):
        self.controller.calculate('p0', self.analysis, self.sources)
        old = self.manager.tasks[-1]
        self.controller.calculate('rainfall', self.analysis, self.sources)
        current = self.manager.tasks[-1]
        old.result = {'mean_mm': 123}
        old.finished(True)
        self.assertFalse(self.results)
        self.assertTrue(old.isCanceled())
        self.assertTrue(self.controller.busy)
        current.result = {'2': {'mean_mm': 42}}
        current.finished(True)
        self.assertEqual(self.results[0][1], 'rainfall')

    def test_dispose_prevents_late_callbacks_and_new_tasks(self):
        self.controller.calculate('p0', self.analysis, self.sources)
        old = self.manager.tasks[-1]
        self.controller.dispose()
        old.result = {'mean_mm': 12}
        old.finished(True)
        self.controller.calculate('p0', self.analysis, self.sources)
        self.assertFalse(self.results)
        self.assertEqual(len(self.manager.tasks), 1)
        self.assertFalse(self.controller.busy)

    def test_invalid_operation_does_not_create_a_task(self):
        with self.assertRaises(ValueError):
            self.controller.calculate('runoff', self.analysis, self.sources)
        self.assertFalse(self.manager.tasks)


if __name__ == '__main__':
    unittest.main()
