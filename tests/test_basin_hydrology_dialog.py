"""Real Qt dialog lifecycle for basin means; hold only raster task scheduling.

The fixture module is imported as a module, so unittest does not rediscover its
TestCase here. Dialogs, signals, QgsTask bodies and portable sessions are real.
"""
from copy import deepcopy
import json
from dataclasses import replace
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtTest import QTest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_drainage_dialog as fixture

from caumax_consulta.basin_hydrology import basin_fingerprint
from caumax_consulta.consultation_paths import (
    CONSULTATION_FILTER, CONSULTATION_FOLDER, DEFAULT_CONSULTATION_FILENAME,
    consultation_directory, default_consultation_path,
)
from caumax_consulta.session_store import load_session, save_session
from caumax_consulta.terrain import analyze_basin


PERIODS = (2, 5, 10, 25, 100, 500)


class BasinHydrologyDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass.__func__(cls)

    def raster(self, *args, **kwargs):
        return fixture.DrainageDialogTests.raster(self, *args, **kwargs)

    def make_dialog(self, saved=None):
        return fixture.DrainageDialogTests.make_dialog(self, saved)

    def setUp(self):
        fixture.DrainageDialogTests.setUp(self)
        self.manager = SimpleNamespace(tasks=[])
        self.manager.addTask = self.manager.tasks.append
        self.sources = {'p0': 'p0-original.tif',
                        'rainfall': {str(period): f'rain-{period}.tif' for period in PERIODS}}
        self.task_patch = patch('caumax_consulta.basin_hydrology_controller.QgsApplication.taskManager',
                                return_value=self.manager)
        self.source_patch = patch('caumax_consulta.basin_hydrology.source_paths',
                                  return_value=deepcopy(self.sources))
        self.task_patch.start()
        self.resolve_sources = self.source_patch.start()
        self.addCleanup(self.task_patch.stop)
        self.addCleanup(self.source_patch.stop)

    def tearDown(self):
        fixture.DrainageDialogTests.tearDown(self)

    def stats(self, mean=25.0, *, analysis=None, valid=1.0, covered=1.0, path='mean-source.tif'):
        analysis = self.analysis if analysis is None else analysis
        if mean is None:
            valid = 0.0
        return dict(source_path=path, mean_mm=mean, basin_area_m2=analysis.area_m2,
                    covered_area_m2=analysis.area_m2 * covered,
                    valid_area_m2=analysis.area_m2 * valid,
                    coverage_fraction=covered, valid_fraction=valid, pixel_count=4,
                    status='unavailable' if mean is None else 'partial' if valid < 1 else 'ok',
                    notes=['Promedio sobre el área válida, sin β.'])

    def publish(self, task, side_effect):
        with patch('caumax_consulta.basin_hydrology.basin_raster_mean', side_effect=side_effect) as mean:
            task.finished(task.run())
        return mean

    def seed_results(self, dialog=None, mean=20.0):
        dialog = self.dialog if dialog is None else dialog
        dialog.basin_hydrology_widget.set_result('p0', self.stats(mean, analysis=dialog.analysis))
        dialog.basin_hydrology_widget.set_result('rainfall', {
            str(period): self.stats(mean + index, analysis=dialog.analysis)
            for index, period in enumerate(PERIODS)})
        return dialog.basin_hydrology_widget.snapshot()

    def test_consultation_paths_create_the_qgis_profile_folder_and_exact_qci_filename(self):
        profile_root = self.folder / 'QGIS3' / 'profiles' / 'default'
        destination = default_consultation_path(profile_root)
        expected = profile_root / 'HidroCAUMAX' / 'ConsultaMapasMaximos'
        self.assertEqual(consultation_directory(profile_root), expected)
        self.assertTrue(expected.is_dir())
        self.assertEqual(expected.name, CONSULTATION_FOLDER)
        self.assertEqual(destination.parent, expected)
        self.assertEqual(destination.name,
                         'Consulta_Mapas_Caudales_Máximos_naturales (Qci).json')
        self.assertEqual(destination.name, DEFAULT_CONSULTATION_FILENAME)

    def test_save_and_open_consultation_choosers_use_same_default_json_folder(self):
        dialog = self.dialog
        folder = self.folder / 'HidroCAUMAX' / CONSULTATION_FOLDER
        folder.mkdir(parents=True)
        proposed = folder / DEFAULT_CONSULTATION_FILENAME
        with patch('caumax_consulta.basin_dialog.default_consultation_path',
                   return_value=proposed) as default_path, \
             patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',
                   return_value=('', CONSULTATION_FILTER)) as chooser:
            dialog._choose_save_project()
        default_path.assert_called_once_with()
        chooser.assert_called_once_with(
            dialog, 'Guardar Consulta', str(proposed), CONSULTATION_FILTER)
        with patch('caumax_consulta.basin_dialog.consultation_directory',
                   return_value=folder) as directory, \
             patch('caumax_consulta.basin_dialog.QFileDialog.getOpenFileName',
                   return_value=('', CONSULTATION_FILTER)) as chooser:
            dialog._choose_open_project()
        directory.assert_called_once_with()
        chooser.assert_called_once_with(
            dialog, 'Abrir Consulta', str(folder), CONSULTATION_FILTER)
        # Custom names without suffix remain plain .json, never .caumax.json.
        with patch('caumax_consulta.basin_dialog.default_consultation_path',
                   return_value=proposed), \
             patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(folder / 'personalizada'), CONSULTATION_FILTER)), \
             patch.object(dialog, 'save_project') as save:
            dialog._choose_save_project()
        save.assert_called_once_with(str(folder / 'personalizada.json'))

    def test_plain_named_json_retains_full_query_parameters_and_hydrology(self):
        dialog = self.dialog
        seeded = self.seed_results()
        destination = default_consultation_path(self.folder / 'QGIS3' / 'profiles' / 'default')
        state = dialog.get_smoothing_state()
        saved_path = save_session(destination, dialog.analysis, dialog.query, state)
        self.assertEqual(saved_path, destination)
        raw = json.loads(destination.read_text(encoding='utf-8'))
        self.assertEqual(raw['schema'], 'caumax-consulta')
        self.assertIn('values', raw['analysis'])
        self.assertIn('diagnostics', raw['analysis'])
        self.assertIn('main_course_wkb', raw['analysis'])
        self.assertIn('grid_wkb', raw['analysis'])
        self.assertIn('flow_quantiles', raw['smoothing_state'])
        self.assertIn('geometry_wkb', raw['smoothing_state'])
        self.assertIn('basin_hydrology', raw['smoothing_state'])
        restored = load_session(destination)
        self.assertEqual(restored.analysis.values, dialog.analysis.values)
        self.assertEqual(restored.query, dialog.query)
        self.assertEqual(restored.smoothing_state['basin_hydrology'], seeded)

    def test_automatic_joint_means_run_in_one_task_and_publish_only_current_basin(self):
        dialog = self.dialog
        before_query = dialog.query
        self.assertFalse(hasattr(dialog.basin_hydrology_widget, 'p0_button'))
        self.assertFalse(hasattr(dialog.basin_hydrology_widget, 'rainfall_button'))
        # Simulate the automatic callback scheduled after the native basin is ready.
        dialog._auto_hydrology_timer.start(0)
        QTest.qWait(30)
        self.assertEqual(len(self.manager.tasks), 1)
        task = self.manager.tasks[-1]
        self.assertEqual(task.kind, 'both')
        self.assertIs(task.analysis, dialog.analysis)
        self.assertEqual(task.key, basin_fingerprint(dialog.analysis))
        self.assertFalse(dialog.report_button.isEnabled())
        self.assertFalse(dialog.save_project_button.isEnabled())
        self.assertFalse(dialog.drainage_prepare_button.isEnabled())
        self.assertTrue(dialog.basin_hydrology_widget.cancel_button.isVisibleTo(dialog.basin_hydrology_widget))
        paths = []
        def values(analysis, path, **kwargs):
            self.assertIs(analysis, dialog.analysis)
            paths.append(path)
            return self.stats(0. if path == self.sources['p0'] else 10. + len(paths),
                              path=path)
        self.publish(task, values)
        self.assertEqual(paths, [self.sources['p0']] +
                         [self.sources['rainfall'][str(period)] for period in PERIODS])
        state = dialog.get_smoothing_state()['basin_hydrology']
        self.assertEqual(state['p0_result']['mean_mm'], 0)
        self.assertEqual(list(state['rainfall_results']), [str(period) for period in PERIODS])
        self.assertTrue(dialog.report_button.isEnabled())
        self.assertTrue(dialog.save_project_button.isEnabled())
        dialog._calculate_auto_basin_hydrology()
        self.assertEqual(len(self.manager.tasks), 1, 'No duplicate automatic work')
        self.assertEqual(dialog.query, before_query)
        self.assertEqual((dialog.query.flow, dialog.query.period), (12.0, 100))

    def test_busy_blocks_save_report_and_direct_output_without_opening_a_chooser(self):
        dialog = self.dialog
        dialog._calculate_basin_hydrology('both')
        QTest.qWait(30)
        self.assertFalse(dialog.report_button.isEnabled())
        self.assertFalse(dialog.save_project_button.isEnabled())
        with patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName') as chooser:
            dialog._choose_save_project()
        chooser.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'P₀|precipitación'):
            dialog._state_for_output()
        self.assertFalse(dialog._saving_project)
        self.assertTrue(dialog._basin_hydrology_busy())

    def test_rainfall_failure_does_not_publish_partial_series_or_erase_previous_results(self):
        dialog = self.dialog
        previous = self.seed_results()
        dialog._calculate_basin_hydrology('both')
        task = self.manager.tasks[-1]
        calls = []
        def failing(analysis, path, **kwargs):
            calls.append(path)
            if len(calls) == 2:
                raise RuntimeError('Error de lectura controlado')
            return self.stats(999.0, path=path)
        self.publish(task, failing)
        self.assertEqual(len(calls), 2)
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), previous)
        self.assertIn('Error de lectura', dialog.basin_hydrology_widget.status_label.text())
        self.assertTrue(dialog.save_project_button.isEnabled())
        self.assertTrue(dialog.report_button.isEnabled())
        self.assertFalse(dialog._basin_hydrology_busy())

    def test_cancel_and_late_callback_preserve_previous_values_and_allow_a_new_request(self):
        dialog = self.dialog
        previous = self.seed_results()
        dialog._calculate_basin_hydrology('both')
        old = self.manager.tasks[-1]
        dialog.basin_hydrology_widget.cancel_button.click()
        self.assertTrue(old.isCanceled())
        self.assertFalse(dialog._basin_hydrology_busy())
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), previous)
        dialog._calculate_basin_hydrology('both')
        current = self.manager.tasks[-1]
        old.result = self.stats(999.0)
        old.finished(True)
        self.assertTrue(dialog._basin_hydrology_busy())
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), previous)
        self.publish(current, lambda *args, **kwargs: self.stats(42.0))
        self.assertEqual(dialog.basin_hydrology_widget.snapshot()['p0_result']['mean_mm'], 42.0)

    def test_result_with_stale_basin_sha_is_ignored_even_with_the_same_resolution_key(self):
        dialog = self.dialog
        old_fingerprint = basin_fingerprint(dialog.analysis)
        different_crs = fixture.QgsCoordinateReferenceSystem('EPSG:32630').toWkt()
        new = replace(self.analysis, basin=replace(self.analysis.basin, crs_wkt=different_crs))
        dialog.analysis = new
        dialog.basin_hydrology_widget.set_analysis(new)
        previous = dialog.basin_hydrology_widget.snapshot()
        dialog._basin_hydrology_ready(old_fingerprint, 'p0', self.stats(999.0))
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), previous)
        self.assertIsNone(previous['p0_result'])

    def test_closing_dialog_disposes_worker_and_ignores_late_results(self):
        dialog = self.dialog
        previous = self.seed_results()
        dialog._calculate_basin_hydrology('both')
        task = self.manager.tasks[-1]
        dialog.dispose()
        self.assertTrue(task.isCanceled())
        task.result = self.stats(999.0)
        task.finished(True)
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), previous)

    def test_same_basin_taudem_threshold_update_preserves_means_and_interpolation_inputs(self):
        dialog = self.dialog
        self.seed_results()
        widget = dialog.basin_hydrology_widget
        widget.period_spin.setValue(7.25)
        widget.precipitation_spin.setValue(25.5)
        widget.extrapolate_check.setChecked(True)
        previous = widget.snapshot()
        updated = deepcopy(self.state)
        updated['threshold_area_m2'] /= 2
        products = SimpleNamespace(analysis=self.analysis, dem_path=str(self.dem),
                                   caumax_d8_path=str(self.directions))
        dialog._drainage_ready('25', products, updated)
        self.assertEqual(widget.snapshot(), previous)
        self.assertEqual(dialog.get_smoothing_state()['basin_hydrology'], previous)

    def test_saved_variants_restore_distinct_means_offline_and_legacy_stays_uncalculated(self):
        dialog = self.dialog
        coarse_state = self.seed_results(mean=10.0)
        directions = np.zeros((5, 5), dtype=np.uint8)
        directions[1:4, 1:4] = [[2, 4, 8], [1, 0, 16], [128, 64, 32]]
        fine_directions = self.raster('fine-d8.tif', directions, gdal.GDT_Byte, 255)
        fine_dem = self.raster('fine-dem.tif', 100 + np.arange(25).reshape(5, 5), gdal.GDT_Float32, -9999)
        for path in (fine_directions, fine_dem):
            dataset = gdal.Open(str(path), gdal.GA_Update)
            dataset.SetGeoTransform((self.query.x - 12.5, 5, 0, self.query.y + 12.5, 0, -5))
            dataset = None
        fine = analyze_basin(fine_directions, fine_dem, self.query.x, self.query.y)
        fine_query = replace(self.query, directions_path=str(fine_directions), dem_path=str(fine_dem))
        options = [dict(key=key, resolution=float(key), available=True, provider='Test', label=key+' m')
                   for key in ('25', '5')]
        controller = SimpleNamespace(options=options, requested_key=None, last_errors={},
            variants={'25': dict(analysis=self.analysis, query=self.query, smoothing_state=dialog.get_smoothing_state()),
                      '5': dict(analysis=fine, query=fine_query, smoothing_state=None)}, dispose=lambda:None)
        dialog._resolution_controller = controller

        def select_and_finish(key, analysis, query, mean):
            # Interactive resolution changes deliberately clear the snapshot
            # until the debounced, complete surface transaction has finished.
            # This fixture holds task scheduling, so release the queued worker
            # after Qt has delivered the debounce timer instead of treating the
            # selection callback as synchronous publication.
            dialog._resolution_result(key, analysis, query)
            self.assertTrue(dialog._surface_pending)
            self.assertIsNone(dialog.basin_hydrology_widget.snapshot())
            deadline = time.monotonic()+2
            while dialog._surface_task is None and time.monotonic() < deadline:
                QTest.qWait(10)
            task = dialog._surface_task
            self.assertIsNotNone(task, 'The resolution change did not queue its surface task')
            def result(current, path, **kwargs):
                offset = (0 if path == self.sources['p0']
                          else list(self.sources['rainfall'].values()).index(path))
                return self.stats(mean+offset, analysis=current)
            with patch('caumax_consulta.basin_hydrology.basin_raster_mean', side_effect=result):
                success = task.run()
            self.assertTrue(success, task.error+'\n'+task.trace)
            task.finished(success)
            self.assertFalse(dialog._surface_pending)
            self.assertIsNotNone(dialog.basin_hydrology_widget.snapshot())

        select_and_finish('5', fine, fine_query, 55.0)
        select_and_finish('25', self.analysis, self.query, 10.0)
        self.assertEqual(dialog.basin_hydrology_widget.snapshot(), coarse_state)
        state = dialog.get_smoothing_state()
        dialog._cache_variant_state()
        resolution = dict(active_key='25', options=options, variants=controller.variants)
        path = self.folder/'hydrology-variants.caumax.json'
        save_session(path, self.analysis, self.query, state, resolution_state=resolution)
        for source in (self.dem, self.directions, fine_dem, fine_directions):
            source.unlink()
        scheduled_tasks = len(self.manager.tasks)
        saved = load_session(path)
        restored = self.make_dialog(saved)
        self.assertEqual(restored.basin_hydrology_widget.snapshot(), coarse_state)
        self.assertEqual(saved.resolution_state['variants']['5']['smoothing_state']['basin_hydrology']['p0_result']['mean_mm'], 55)
        self.assertEqual(len(self.manager.tasks), scheduled_tasks)
        legacy_state = {key:value for key,value in state.items() if key != 'basin_hydrology'}
        legacy = self.make_dialog(replace(saved, smoothing_state=legacy_state, resolution_state=None))
        empty = legacy.basin_hydrology_widget.snapshot()
        self.assertIsNone(empty['p0_result'])
        self.assertEqual(empty['rainfall_results'], {})
        self.assertIn('pendiente de cálculo', legacy.basin_hydrology_widget.p0_label.text().lower())
        self.assertEqual(len(self.manager.tasks), scheduled_tasks)

    def test_discovery_failure_does_not_unlock_save_or_pdf_while_mean_task_is_busy(self):
        dialog = self.dialog
        dialog._calculate_basin_hydrology('both')
        self.assertTrue(dialog._basin_hydrology_busy())
        dialog._resolution_discovering = True
        dialog._resolution_error('No se pudo consultar el catálogo de MDT')
        self.assertFalse(dialog.report_button.isEnabled())
        self.assertFalse(dialog.save_project_button.isEnabled())
        self.assertTrue(dialog._basin_hydrology_busy())


if __name__ == '__main__':
    unittest.main()
