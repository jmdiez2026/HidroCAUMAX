"""Real GDAL/Qt checks for changing the watershed analysis surface atomically."""
from dataclasses import replace
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtCore import Qt, QThread
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QSpinBox
from qgis.core import QgsApplication, QgsGeometry, QgsProject

import test_drainage_dialog as fixture
from caumax_consulta.basin_hydrology import basin_raster_mean
from caumax_consulta.terrain import analyze_basin
from caumax_consulta.session_store import load_session, save_session


class SurfaceRecalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog
        self.manager = SimpleNamespace(tasks=[])
        self.native_manager = QgsApplication.taskManager()
        self.manager.addTask = lambda task: self.manager.tasks.append(task)
        self.addCleanup(patch.stopall)
        patch('caumax_consulta.basin_dialog.QgsApplication.taskManager', return_value=self.manager).start()
        values = 10+np.arange(25, dtype=np.float32).reshape(5,5)**2
        p0 = self.fixture.raster('p0.tif', values, gdal.GDT_Float32, -9999)
        rainfall = {str(t):str(self.fixture.raster(f't{t}.tif', values+i*20,
                    gdal.GDT_Float32, -9999)) for i,t in enumerate((2,5,10,25,100,500))}
        self.sources = dict(p0=str(p0), rainfall=rainfall)
        patch('caumax_consulta.basin_hydrology.source_paths', return_value=self.sources).start()
        patch('caumax_consulta.flow_quantiles._source_paths', return_value=list(rainfall.values())).start()

    def start(self, degree):
        self.dialog.smooth_check.setChecked(True)
        self.dialog.smoothing_degree.setValue(degree)
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()
        return self.manager.tasks[-1]

    def publish(self, task):
        success = task.run()
        self.assertTrue(success, task.error+'\n'+task.trace)
        task.finished(success)

    def test_integer_control_and_compact_export_button(self):
        dialog = self.dialog
        self.assertIsInstance(dialog.smoothing_degree, QSpinBox)
        self.assertEqual(dialog.smoothing_degree.singleStep(), 1)
        dialog.ensurePolished()
        dialog.show()
        self.fixture.app.processEvents()
        button = dialog.export_button
        self.assertLess(button.height(), 30)
        self.assertLess(button.width(), 160)
        layout = button.parentWidget().layout()
        self.assertTrue(layout.itemAt(layout.indexOf(button)).alignment() & Qt.AlignLeft)

    def test_fractional_dem_area_and_weights_are_exact(self):
        f = self.fixture
        elevations = 100+np.arange(25, dtype=np.float32).reshape(5,5)**2
        dem = f.raster('nonlinear_dem.tif', elevations, gdal.GDT_Float32, -9999)
        polygon = QgsGeometry.fromWkt('MULTIPOLYGON (((500037.5 4699912.5,500087.5 4699912.5,500087.5 4699962.5,500037.5 4699962.5,500037.5 4699912.5)))')
        active = analyze_basin(f.directions, dem, f.analysis.outlet_x, f.analysis.outlet_y,
                               surface_wkb=bytes(polygon.asWkb()))
        weights = np.outer([.5,1,.5],[.5,1,.5])
        expected = float(np.average(elevations[1:4,1:4], weights=weights))
        self.assertAlmostEqual(active.area_m2, 2500)
        self.assertAlmostEqual(active.z_mean, expected)
        self.assertEqual(active.cell_count, 9)
        self.assertEqual(active.values['zmedian_m'], 244)
        self.assertAlmostEqual(active.diagnostics['terrain_valid_area_m2'], 2500)
        self.assertAlmostEqual(active.values['hypsometric'], (expected-136)/(424-136))

    def test_degree_change_refreshes_all_results_and_round_trips_offline(self):
        dialog = self.dialog
        original = dialog.analysis
        original_mean = basin_raster_mean(original, self.sources['p0'])['mean_mm']
        dialog.basin_hydrology_widget.period_spin.setValue(93)
        task = self.start(21)
        self.assertTrue(dialog._surface_pending)
        self.assertFalse(dialog.report_button.isEnabled())
        self.assertFalse(dialog.export_button.isEnabled())
        self.assertIsNone(dialog.basin_hydrology_widget.snapshot())
        self.publish(task)
        self.assertFalse(dialog._surface_pending)
        self.assertTrue(dialog._surface_valid)
        active = dialog.analysis
        self.assertLess(active.area_m2, original.area_m2)
        self.assertNotAlmostEqual(active.values['kc'], original.values['kc'])
        state = dialog.basin_hydrology_widget.snapshot()
        self.assertNotAlmostEqual(state['p0_result']['mean_mm'], original_mean, places=4)
        self.assertEqual(state['input_period'], 93)
        self.assertEqual(len(state['rainfall_results']), 6)
        for result in [state['p0_result'], *state['rainfall_results'].values()]:
            self.assertAlmostEqual(result['basin_area_m2'], active.area_m2)
            self.assertIsNotNone(result['statistics'])
        self.assertEqual(dialog.quantiles_widget.snapshot()['flows'],
                         task.result['quantiles']['flows'])
        path = self.fixture.folder/'active.caumax.json'
        save_session(path, active, dialog.query, dialog.get_smoothing_state())
        with patch('osgeo.gdal.Open', side_effect=AssertionError('Offline load opened a raster')):
            saved = load_session(path)
        self.assertEqual(saved.analysis, active)
        self.assertEqual(saved.smoothing_state['base_geometry_wkb'], original.geometry_wkb)
        self.assertEqual(saved.smoothing_state['basin_hydrology'], state)
        from caumax_consulta.reports import build_report_html, generate_basin_pdf
        html = build_report_html(active, dialog.query, smoothing_state=saved.smoothing_state)
        self.assertIn('divisoria SUAVIZADA activa', html)
        self.assertIn('Divisoria suavizada activa', html)
        self.assertNotIn('cuenca ORIGINAL de celdas', html)
        pdf = self.fixture.folder/'active.pdf'
        with patch('osgeo.gdal.Open', side_effect=AssertionError('Offline PDF opened a raster')):
            generate_basin_pdf(pdf, active, dialog.query, open_after=False,
                               smoothing_state=saved.smoothing_state)
        self.assertTrue(pdf.read_bytes().startswith(b'%PDF-'))
        restored = self.fixture.make_dialog(saved)
        self.assertEqual(restored.analysis, active)
        self.assertEqual(restored.smoothing_degree.value(), 21)
        self.assertEqual(restored.basin_hydrology_widget.snapshot(), state)

    def test_superseded_task_cannot_publish_and_original_surface_can_be_restored(self):
        original = self.dialog.analysis
        first = self.start(12)
        # Finish the old numerical work before changing degree, then deliver
        # its callback late: this reproduces a real queued-completion race.
        self.assertTrue(first.run(), first.error)
        second = self.start(24)
        self.assertTrue(first.isCanceled())
        first.finished(True)
        self.assertIs(self.dialog.analysis, original)
        self.assertIs(self.dialog._surface_task, second)
        self.publish(second)
        self.assertEqual(self.dialog.analysis.diagnostics['analysis_surface']['degree'], 24)
        self.dialog.smooth_check.setChecked(False)
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        self.assertEqual(self.dialog.analysis.geometry_wkb, original.geometry_wkb)
        self.assertEqual(self.dialog.analysis.values, original.values)
        self.assertNotIn('analysis_surface', self.dialog.analysis.diagnostics)
        self.assertEqual(len(self.dialog.basin_hydrology_widget.snapshot()['rainfall_results']), 6)

    def test_failed_recalculation_never_exports_old_surface_results(self):
        task = self.start(18)
        task.error = 'MDT no disponible'
        task.finished(False)
        self.assertFalse(self.dialog._surface_valid)
        self.assertFalse(self.dialog.report_button.isEnabled())
        self.assertFalse(self.dialog.export_button.isEnabled())
        with self.assertRaisesRegex(ValueError, 'recálculo completo'):
            self.dialog._state_for_output()

    def test_grid_switch_is_independent_and_defaults_depend_on_resolution(self):
        dialog = self.dialog
        self.assertTrue(dialog.cell_grid_check.isChecked())  # fixture: 25 m
        self.assertIsNotNone(dialog._grid)
        dialog.cell_grid_check.setChecked(False)
        self.assertIsNone(dialog._grid)
        self.assertEqual(len(self.manager.tasks), 0)
        state = dialog.get_smoothing_state()
        path = self.fixture.folder/'grid.caumax.json'
        save_session(path, dialog.analysis, dialog.query, state)
        restored = self.fixture.make_dialog(load_session(path))
        self.assertFalse(restored.cell_grid_check.isChecked())
        self.assertIsNone(restored._grid)
        self.assertIs(restored.resolution_group.parentWidget().layout().itemAt(0).widget(), restored.resolution_group)
        self.assertTrue(restored.resolution_refresh_button.isHidden())

    def test_resolution_selection_loads_hidden_dem_and_automatically_recalculates(self):
        dialog, f = self.dialog, self.fixture
        previous_analysis, previous_query = dialog.analysis, dialog.query
        previous_layers = list(dialog._terrain_layers.layers)
        project = QgsProject.instance()
        tree = project.layerTreeRoot()
        visible_ids = {layer.id() for layer in previous_layers
                       if tree.findLayer(layer.id()).isVisible()}
        new_paths = []
        for name, data, kind in (
            ('directions5.tif', np.array([[0,0,0,0,0],[0,2,4,8,0],[0,1,0,16,0],[0,128,64,32,0],[0,0,0,0,0]], dtype=np.uint8), gdal.GDT_Byte),
            ('dem5.tif', 200+np.arange(25, dtype=np.float32).reshape(5,5)**2, gdal.GDT_Float32)):
            path = f.folder/name
            dataset = gdal.GetDriverByName('GTiff').Create(str(path),5,5,1,kind)
            dataset.SetProjection(f.crs.toWkt())
            dataset.SetGeoTransform((500050,5,0,4699950,0,-5))
            dataset.GetRasterBand(1).WriteArray(data)
            dataset = None
            new_paths.append(path)
        directions, dem = new_paths
        changed = analyze_basin(directions, dem, previous_analysis.outlet_x, previous_analysis.outlet_y)
        query = replace(previous_query, directions_path=str(directions), dem_path=str(dem))
        options = [dict(key=k, resolution=float(k), provider='CAUMAX' if k=='25' else 'CNIG', available=True)
                   for k in ('25','5')]
        controller = SimpleNamespace(variants={
            '25':dict(analysis=previous_analysis, query=previous_query, smoothing_state=dialog.get_smoothing_state()),
            '5':dict(analysis=changed, query=query, smoothing_state=None)},
            options=options, base_analysis=previous_analysis, progress=0, busy=False, requested_key=None,
            cancel=lambda:None, dispose=lambda:None)
        calls = []
        def select(key):
            calls.append(key)
            item = controller.variants[key]
            dialog._resolution_result(key, item['analysis'], item['query'])
        controller.select = select
        dialog._resolution_controller = controller
        dialog._resolution_options(options)
        # The UI click must schedule the complete update; no extra action.
        dialog.resolution_checkboxes['5'].click()
        self.assertEqual(calls, ['5'])
        self.assertTrue(dialog._surface_pending)
        self.assertFalse(dialog.cell_grid_check.isChecked())
        self.assertTrue(all(project.mapLayer(layer.id()) is layer for layer in previous_layers))
        self.assertTrue(all(tree.findLayer(layer.id()).isVisible() for layer in previous_layers if layer.id() in visible_ids))
        self.assertTrue(all(not tree.findLayer(layer.id()).isVisible() for layer in dialog._terrain_layers.layers))
        dialog._smoothing_timer.stop()
        dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        state = dialog.basin_hydrology_widget.snapshot()
        self.assertEqual(dialog.analysis.cell_width, 5)
        self.assertEqual(len(state['rainfall_results']), 6)
        self.assertAlmostEqual(state['p0_result']['mean_mm'], 154)
        self.assertAlmostEqual(state['basin_area_m2'], changed.area_m2)
        self.assertTrue(all(tree.findLayer(layer.id()).isVisible() for layer in previous_layers if layer.id() in visible_ids))
        # Returning to the 25 m option recomputes as well and restores its grid.
        dialog.resolution_checkboxes['25'].click()
        self.assertTrue(dialog._surface_pending)
        self.assertTrue(dialog.cell_grid_check.isChecked())
        dialog._smoothing_timer.stop()
        dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        self.assertEqual(dialog.analysis.cell_width, 25)

    def test_real_background_task_publishes_on_gui_thread(self):
        dialog = self.dialog
        callback_threads = []
        finished = dialog._surface_finished
        def receive(task, success):
            callback_threads.append(QThread.currentThread())
            finished(task, success)
        with patch('caumax_consulta.basin_dialog.QgsApplication.taskManager', return_value=self.native_manager), \
                patch.object(dialog, '_surface_finished', side_effect=receive):
            dialog.smooth_check.setChecked(True)
            dialog.smoothing_degree.setValue(16)
            deadline = time.monotonic()+15
            while dialog._surface_pending and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertFalse(dialog._surface_pending, 'Background task did not finish')
            self.assertTrue(dialog._surface_valid, dialog._smoothing_error)
        self.assertEqual(callback_threads, [self.fixture.app.thread()])
        self.assertEqual(len(dialog.basin_hydrology_widget.snapshot()['rainfall_results']), 6)


if __name__ == '__main__':
    unittest.main()
