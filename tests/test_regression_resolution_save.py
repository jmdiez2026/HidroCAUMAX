"""Bulk save completes every unvisited surface before publishing the batch.

Qt/QGIS, SurfaceTask calculations, rasters and session round trips are real;
only task scheduling, the file chooser and map capture are controlled.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
from qgis.core import QgsApplication, QgsGeometry

import test_drainage_dialog as fixture
from caumax_consulta.basin_hydrology import basin_fingerprint
from caumax_consulta.session_store import load_session
from caumax_consulta.terrain import analyze_basin


class ResolutionSaveRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog
        # These regressions exercise the atomic JSON/variant transaction.
        # Optional SHP exports have their own tests and default destinations.
        exports = patch.object(self.dialog, '_export_consultation_layers',
                               return_value=([], []))
        exports.start()
        self.addCleanup(exports.stop)
        self.manager = SimpleNamespace(tasks=[])
        self.manager.addTask = self.manager.tasks.append
        task_patch = patch('caumax_consulta.basin_dialog.QgsApplication.taskManager',return_value=self.manager)
        task_patch.start()
        self.addCleanup(task_patch.stop)
        values = 10+np.arange(25,dtype=np.float32).reshape(5,5)**2
        p0 = self.fixture.raster('p0.tif',values,gdal.GDT_Float32,-9999)
        rainfall = {str(t):str(self.fixture.raster(f't{t}.tif',values+i*20,
                    gdal.GDT_Float32,-9999)) for i,t in enumerate((2,5,10,25,100,500))}
        for patcher in (
            patch('caumax_consulta.basin_hydrology.source_paths',return_value=dict(p0=str(p0),rainfall=rainfall)),
            patch('caumax_consulta.flow_quantiles._source_paths',return_value=list(rainfall.values())),
            patch('caumax_consulta.map_capture.capture_basin_map',return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.fixture.folder/'complete.caumax.json'
        chooser = patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',return_value=(str(self.path),''))
        chooser.start()
        self.addCleanup(chooser.stop)
        errors = patch('caumax_consulta.basin_dialog.QMessageBox.critical')
        self.errors = errors.start()
        self.addCleanup(errors.stop)

    def variant(self, step):
        x,y = self.fixture.query.x,self.fixture.query.y
        paths = []
        for name,values in (
            ('dem',100+np.arange(25,dtype=np.float64).reshape(5,5)),
            ('directions',np.pad(np.array([[2,4,8],[1,0,16],[128,64,32]]),1)),
        ):
            path = self.fixture.folder/f'{name}_{step}.tif'
            dataset = gdal.GetDriverByName('GTiff').Create(str(path),5,5,1,gdal.GDT_Float64)
            dataset.SetProjection(self.fixture.crs.toWkt())
            dataset.SetGeoTransform((x-2.5*step,step,0,y+2.5*step,0,-step))
            dataset.GetRasterBand(1).WriteArray(values)
            dataset = None
            paths.append(str(path))
        query = replace(self.fixture.query,dem_path=paths[0],directions_path=paths[1])
        analysis = analyze_basin(query.directions_path,query.dem_path,x,y)
        return dict(analysis=analysis,query=query,smoothing_state=None)

    def configure(self, steps=(5,), smoothed=False):
        if smoothed:
            self.dialog.smooth_check.setChecked(True)
            self.dialog.smoothing_degree.setValue(18)
            self.dialog._smoothing_timer.stop()
            self.dialog._update_smoothing()
            self.publish(self.manager.tasks[-1])
        variants = {'25':dict(analysis=self.dialog.analysis,query=self.dialog.query,smoothing_state=None)}
        variants.update({str(step):self.variant(step) for step in steps})
        options = [dict(key=key,resolution=float(key),available=True,provider='Fixture',
                        label=f'{key} m',status='Disponible') for key in variants]
        controller = SimpleNamespace(options=options,variants=variants,last_errors={},dispose=lambda:None)
        controller.ensure_all = lambda ready:ready(controller.variants)
        self.dialog._resolution_controller = controller
        self.controller = controller

    def publish(self, task):
        success = task.run()
        self.assertTrue(success,task.error+'\n'+task.trace)
        task.finished(success)

    def test_unvisited_original_variant_has_complete_hydrology_after_save_load(self):
        self.configure()
        active = self.dialog.analysis
        self.dialog._choose_save_project()
        self.assertTrue(self.dialog._saving_project)
        self.assertFalse(self.dialog.original_check.isEnabled())
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])
        self.publish(self.manager.tasks[-1])
        self.errors.assert_not_called()
        restored = load_session(self.path)
        variant = restored.resolution_state['variants']['5']
        analysis,state = variant['analysis'],variant['smoothing_state']
        self.assertFalse(state['smooth_checked'])
        self.assertNotIn('analysis_surface',analysis.diagnostics)
        self.assertEqual(state['base_geometry_wkb'],analysis.geometry_wkb)
        self.assertEqual(state['basin_hydrology']['basin_fingerprint'],basin_fingerprint(analysis))
        self.assertEqual(len(state['basin_hydrology']['rainfall_results']),6)
        self.assertEqual(state['basin_hydrology']['p0_result']['basin_area_m2'],analysis.area_m2)
        self.assertIs(self.dialog.analysis,active)
        self.assertFalse(self.dialog._saving_project)
        self.assertTrue(self.dialog.original_check.isEnabled())

    def test_unvisited_smoothed_variant_saves_matching_metrics_geometry_and_means(self):
        self.configure(smoothed=True)
        base = self.controller.variants['5']['analysis']
        active = self.dialog.analysis
        self.dialog._choose_save_project()
        self.publish(self.manager.tasks[-1])
        self.errors.assert_not_called()
        restored = load_session(self.path)
        variant = restored.resolution_state['variants']['5']
        analysis,state = variant['analysis'],variant['smoothing_state']
        geometry = QgsGeometry()
        geometry.fromWkb(state['geometry_wkb']['suavizada'])
        self.assertTrue(state['smooth_checked'])
        self.assertEqual(analysis.diagnostics['analysis_surface']['kind'],'smoothed')
        self.assertEqual(analysis.geometry_wkb,state['geometry_wkb']['suavizada'])
        self.assertAlmostEqual(analysis.area_m2,geometry.area(),places=7)
        self.assertNotAlmostEqual(analysis.area_m2,base.area_m2,places=4)
        self.assertEqual(state['base_geometry_wkb'],base.geometry_wkb)
        for result in [state['basin_hydrology']['p0_result'],*state['basin_hydrology']['rainfall_results'].values()]:
            self.assertAlmostEqual(result['basin_area_m2'],analysis.area_m2,places=7)
        self.assertIs(self.dialog.analysis,active)

    def test_second_variant_failure_discards_batch_and_does_not_save(self):
        self.configure((5,2))
        variant = self.controller.variants['2']
        variant['query'] = replace(variant['query'],directions_path=str(self.fixture.folder/'missing.tif'))
        self.dialog._choose_save_project()
        self.publish(self.manager.tasks[-1])
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])
        second = self.manager.tasks[-1]
        success = second.run()
        self.assertFalse(success)
        second.finished(success)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])
        self.assertIsNone(self.controller.variants['2']['smoothing_state'])
        self.assertFalse(self.dialog._saving_project)
        self.errors.assert_called_once()

    def test_second_variant_cancel_discards_batch_and_ignores_late_result(self):
        self.configure((5,2))
        self.dialog._choose_save_project()
        self.publish(self.manager.tasks[-1])
        first,second = self.manager.tasks[-2:]
        second.cancel()
        self.assertFalse(second.run())
        second.finished(False)
        first.finished(True)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])
        self.assertIsNone(self.controller.variants['2']['smoothing_state'])
        self.assertFalse(self.dialog._saving_project)
        self.assertTrue(self.dialog.smooth_check.isEnabled())
        self.errors.assert_called_once()

    def test_direct_bundle_refuses_unprepared_variant_instead_of_mixing_surfaces(self):
        self.configure(smoothed=True)
        self.dialog._cache_variant_state()
        with self.assertRaisesRegex(ValueError,'resultados completos del MDT de 5'):
            self.dialog._resolution_bundle()
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])

    def test_selecting_another_legacy_variant_recalculates_its_own_d8_areas(self):
        from caumax_consulta.course_flow_profile import main_course_areas
        from caumax_consulta.resolution_controller import ResolutionController
        self.configure()
        coarse_areas = main_course_areas(self.dialog.analysis)
        fine = self.controller.variants['5']
        expected = main_course_areas(fine['analysis'])
        fine['analysis'] = replace(fine['analysis'], diagnostics={
            key: value for key, value in fine['analysis'].diagnostics.items()
            if key not in ('main_course_areas_b64', 'main_course_area_at_closure_km2')})
        fine['smoothing_state'] = self.dialog._new_resolution_state('5', fine['analysis'])
        controller = ResolutionController(self.dialog.analysis, self.dialog.query,
                                         parent=self.dialog)
        controller.variants, controller.options = self.controller.variants, self.controller.options
        self.dialog._resolution_controller = controller
        self.dialog._resolution_result('5', fine['analysis'], fine['query'])
        self.assertTrue(self.dialog._surface_pending)
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()
        task = self.manager.tasks[-1]
        self.assertEqual(task.query.directions_path, fine['query'].directions_path)
        self.assertEqual(task.query.dem_path, fine['query'].dem_path)
        self.publish(task)
        self.assertEqual(main_course_areas(self.dialog.analysis), expected)
        self.assertNotEqual(main_course_areas(self.dialog.analysis), coarse_areas)

    def test_dispose_cancels_save_worker_without_publishing(self):
        self.configure()
        self.dialog._choose_save_project()
        task = self.manager.tasks[-1]
        self.assertTrue(task.run(),task.error)
        self.dialog.dispose()
        self.assertTrue(task.isCanceled())
        task.finished(True)
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.controller.variants['5']['smoothing_state'])
        self.errors.assert_not_called()


if __name__ == '__main__':
    unittest.main()
