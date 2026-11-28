"""Real Qt/GDAL rational-basin controls, surface variants and portable state."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.core import QgsGeometry, QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvas

from test_flow_quantiles import snapshot as raster_snapshot
from test_rational_basin import rational_fixture, _write
from caumax_consulta import flow_quantiles, rational_basin
from caumax_consulta.basin_dialog import AnalysisTask, BasinDialog
from caumax_consulta.resolution_controller import ResolutionController, base_variant_key
from caumax_consulta.session_store import load_session, save_session
from caumax_consulta.terrain import analyze_basin


class RationalBasinDialogTests(unittest.TestCase):
    def setUp(self):
        context = rational_fixture()
        self.fixture = f = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        # A genuine 25 m native MDT catches the old implicit 500 m baseline.
        f.transform = (0, 25, 0, 225, 0, -25)
        f.calc.geoTransform = f.transform
        f.calc.pt = QgsPointXY(112.5, 112.5)
        f.calc.area = 49 * 25 ** 2
        f.dialog.txtX.setText(112.5); f.dialog.txtY.setText(112.5)
        _write(f.paths['MDT'], f.calc.mdt, f.transform, f.reference)
        _write(f.paths['FLOWDIRS'], f.calc.dirs, f.transform, f.reference)
        f.analysis = analyze_basin(f.paths['FLOWDIRS'], f.paths['MDT'], 112.5, 112.5)
        geometry = QgsGeometry(); geometry.fromWkb(f.analysis.geometry_wkb)
        f.calc.basinGeometry = geometry.asGeometryCollection()
        f.query = rational_basin.capture_rational_query(f.dialog)
        self.project = QgsProject.instance()
        self.initial_ids = set(self.project.mapLayers())
        self.initial_groups = list(self.project.layerTreeRoot().children())
        self.canvas = QgsMapCanvas(); self.canvas.setDestinationCrs(f.query.crs)
        self.iface = SimpleNamespace(mapCanvas=lambda: self.canvas)
        self.dialogs = []
        self.manager = SimpleNamespace(tasks=[])
        self.manager.addTask = lambda task: self.manager.tasks.append(task)
        self.patchers = [
            patch('caumax_consulta.boundary_layers.storage_directory',
                  return_value=f.folder / 'boundaries'),
            patch('caumax_consulta.basin_dialog.QgsApplication.taskManager', return_value=self.manager),
            patch('caumax_consulta.flow_quantiles.sample_quantiles',
                  side_effect=AssertionError('Rational work must never sample Q rasters')),
            patch('caumax_consulta.flow_quantiles._source_paths',
                  side_effect=AssertionError('Rational work must never discover Q rasters')),
        ]
        values = 10 + np.arange(81, dtype=np.float32).reshape(9, 9) ** 2
        p0 = f.folder / 'p0.tif'; _write(p0, values, f.transform, f.reference)
        rainfall = {}
        for index, period in enumerate((2, 5, 10, 25, 100, 500)):
            path = f.folder / f'pd{period}.tif'
            _write(path, values + index * 20, f.transform, f.reference)
            rainfall[str(period)] = str(path)
        self.sources = dict(p0=str(p0), rainfall=rainfall)
        self.patchers.append(patch('caumax_consulta.basin_hydrology.source_paths', return_value=self.sources))
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dialog = self.make_dialog()

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.dispose(); dialog.close(); dialog.deleteLater()
        self.fixture.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.canvas.setLayers([]); sip.delete(self.canvas)
        for layer_id in set(self.project.mapLayers()) - self.initial_ids:
            self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_groups:
                self.project.layerTreeRoot().removeChildNode(node)

    def make_dialog(self, saved=None, query=None):
        with patch.object(BasinDialog, '_start'), patch.object(BasinDialog, '_setup_resolutions'):
            dialog = BasinDialog(query or self.fixture.query, self.iface, saved_session=saved)
        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.dialogs.append(dialog)
        if saved is None:
            dialog.analysis = self.fixture.analysis
            dialog._active_resolution_key = '25'
            dialog._display_analysis(); dialog._update_smoothing()
        return dialog

    def publish(self, task):
        success = task.run()
        self.assertTrue(success, task.error + '\n' + task.trace)
        task.finished(success)

    def fine_variant(self):
        f = self.fixture
        transform = (90, 5, 0, 135, 0, -5)
        dem, directions = f.folder / 'dem5.tif', f.folder / 'directions5.tif'
        _write(dem, f.calc.mdt, transform, f.reference)
        _write(directions, f.calc.dirs, transform, f.reference)
        query = replace(self.dialog.query, dem_path=str(dem), directions_path=str(directions))
        analysis = analyze_basin(directions, dem, query.x, query.y)
        return dict(analysis=analysis, query=query, smoothing_state=None)

    def controller_with_fine_variant(self):
        dialog = self.dialog
        controller = SimpleNamespace(variants={
            '25': dict(analysis=dialog.analysis, query=dialog.query,
                       smoothing_state=dialog.get_smoothing_state()),
            '5': self.fine_variant()},
            options=[dict(key=k, resolution=float(k), available=True) for k in ('25', '5')],
            base_analysis=dialog.analysis, progress=0, busy=False, requested_key=None,
            cancel=lambda: None, dispose=lambda: None)
        dialog._resolution_controller = controller
        return controller

    def test_report_buttons_order_and_consulta_names(self):
        """Keep save/open consultation before PDF; Cerrar stays on the right."""
        from qgis.PyQt.QtWidgets import QDialogButtonBox

        dialog = self.dialog
        controls = dialog.save_project_button.parentWidget().layout()
        self.assertEqual(dialog.save_project_button.text(), 'Guardar Consulta')
        self.assertEqual(dialog.open_project_button.text(), 'Abrir Consulta')
        self.assertEqual(dialog.report_button.text(), 'Informe (PDF)')
        self.assertIs(controls.itemAt(0).widget(), dialog.save_project_button)
        self.assertIs(controls.itemAt(1).widget(), dialog.open_project_button)
        self.assertIs(controls.itemAt(2).widget(), dialog.report_button)
        closing_group = controls.itemAt(controls.count() - 1).widget()
        self.assertIsInstance(closing_group, QDialogButtonBox)
        self.assertIs(dialog.close_button.parentWidget(), closing_group)
        # Compare with native, unmodified QGIS button hints to verify that all
        # four actions are scaled independently (including the red Cerrar).
        from qgis.PyQt.QtWidgets import QPushButton
        for button in (dialog.save_project_button, dialog.open_project_button,
                       dialog.report_button, dialog.close_button):
            native = QPushButton(button.text(), button.parentWidget())
            native.setFont(button.font())
            native.ensurePolished()
            expected = max(1, round(native.sizeHint().height() * 0.90))
            self.assertEqual(button.minimumHeight(), expected)
            self.assertEqual(button.maximumHeight(), expected)
            native.deleteLater()

    def test_initial_task_checks_exact_native_basin_before_publishing(self):
        task = AnalysisTask(self.fixture.query, lambda *args: None)
        with patch('caumax_consulta.terrain.analyze_basin', wraps=analyze_basin) as terrain:
            self.assertTrue(task.run(), task.error)
        self.assertFalse(terrain.call_args.kwargs['upstream_only'])
        self.assertEqual(task.result.geometry_wkb, self.fixture.analysis.geometry_wkb)
        self.assertEqual(task.result.cell_count, 49)
        changed = replace(self.fixture.analysis, basin=replace(self.fixture.analysis.basin,
                           area_m2=self.fixture.analysis.area_m2 + 625))
        rejected = AnalysisTask(self.fixture.query, lambda *args: None)
        with patch('caumax_consulta.terrain.analyze_basin', return_value=changed):
            self.assertFalse(rejected.run())
        self.assertIn('área', rejected.error)

    def test_pending_native_flow_keeps_terrain_and_rainfall_functional(self):
        dialog = self.dialog
        self.assertIn('Método racional', dialog.context.text())
        self.assertIn('pendiente de cálculo', dialog.context.text())
        self.assertIn('25mx25m', dialog.resolution_help.text())
        self.assertNotIn('500mx500m', dialog.resolution_help.text())
        self.assertTrue(dialog.quantiles_widget.table.isHidden())
        self.assertTrue(dialog.quantiles_widget.plots.isHidden())
        self.assertIsNone(dialog.quantiles_widget.snapshot())
        dialog.smooth_check.setChecked(True); dialog.smoothing_degree.setValue(21)
        dialog._smoothing_timer.stop(); dialog._update_smoothing()
        self.assertTrue(dialog._surface_pending)
        self.publish(self.manager.tasks[-1])
        self.assertLess(dialog.analysis.area_m2, self.fixture.analysis.area_m2)
        self.assertIsNone(dialog.query.flow)
        self.assertIsNone(dialog.quantiles_widget.snapshot())
        hydrology = dialog.basin_hydrology_widget.snapshot()
        self.assertEqual(hydrology['p0_result']['status'], 'ok')
        self.assertEqual(len(hydrology['rainfall_results']), 6)
        self.assertAlmostEqual(hydrology['basin_area_m2'], dialog.analysis.area_m2)
        self.assertNotIn('flow_quantiles', dialog.get_smoothing_state())
        self.assertTrue(dialog.report_button.isEnabled())

    def test_custom_extents_open_smooth_and_return_to_original_native_surface(self):
        f = self.fixture
        f.calc.mdt = np.pad(f.calc.mdt, ((0, 2), (0, 1)), constant_values=180)
        f.calc.basinCells = np.pad(f.calc.basinCells, ((0, 2), (0, 1)))
        f.paths['MDT'] = f.folder / 'custom_mdt.tif'
        _write(f.paths['MDT'], f.calc.mdt, f.transform, f.reference)
        query = rational_basin.capture_rational_query(f.dialog)
        task = AnalysisTask(query, lambda *_: None)
        self.assertTrue(task.run(), task.error)
        dialog = self.dialog
        dialog.query, dialog.analysis = query, task.result
        dialog._display_analysis()
        original = task.result.geometry_wkb
        dialog.smooth_check.setChecked(True); dialog.smoothing_degree.setValue(21)
        dialog._smoothing_timer.stop(); dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        self.assertLess(dialog.analysis.area_m2, query.rational_context['area_m2'])
        dialog.smooth_check.setChecked(False)
        dialog._smoothing_timer.stop(); dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        self.assertEqual(dialog.analysis.geometry_wkb, original)
        self.assertIs(rational_basin.validate_rational_analysis(dialog.analysis, query), dialog.analysis)

    def test_smoothed_pending_project_round_trips_without_flow_or_raster_reads(self):
        dialog = self.dialog
        dialog.smooth_check.setChecked(True); dialog.smoothing_degree.setValue(17)
        dialog._smoothing_timer.stop(); dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        path = self.fixture.folder / 'rational.caumax.json'
        save_session(path, dialog.analysis, dialog.query, dialog.get_smoothing_state())
        with patch('osgeo.gdal.Open', side_effect=AssertionError('Offline load read a raster')):
            saved = load_session(path)
        self.assertEqual(saved.query.rational_context, dialog.query.rational_context)
        self.assertIsNone(saved.query.flow)
        self.assertNotIn('flow_quantiles', saved.smoothing_state)
        restored = self.make_dialog(saved)
        self.assertEqual(restored.analysis, dialog.analysis)
        self.assertIsNone(restored.quantiles_widget.snapshot())
        self.assertEqual(restored.basin_hydrology_widget.snapshot(), dialog.basin_hydrology_widget.snapshot())

    def test_resolution_selection_retains_accepted_native_flow(self):
        accepted = rational_basin.capture_rational_query(self.fixture.dialog,
                    flow_result=dict(flow=17.25, period=100))
        self.dialog.query = accepted
        self.dialog._display_analysis()
        controller = self.controller_with_fine_variant()
        variant = controller.variants['5']
        self.dialog._resolution_result('5', variant['analysis'], variant['query'])
        self.dialog._smoothing_timer.stop(); self.dialog._update_smoothing()
        self.publish(self.manager.tasks[-1])
        self.assertEqual(self.dialog.analysis.cell_width, 5)
        self.assertEqual(self.dialog.query.flow, 17.25)
        self.assertEqual(self.dialog.query.rational_context, accepted.rational_context)
        self.assertIn('17,25 m³/s', self.dialog.quantiles_widget.rational_label.text())
        self.assertIn('25mx25m', self.dialog.resolution_help.text())
        self.assertIn('5mx5m', self.dialog.context_mdt.text())
        self.assertIsNone(self.dialog.quantiles_widget.snapshot())
        self.assertNotIn('flow_quantiles', self.dialog.get_smoothing_state())

    def test_save_prepares_unvisited_variant_without_discovering_flow_sources(self):
        controller = self.controller_with_fine_variant()
        errors = []
        self.dialog._prepare_resolution_states(errors.append)
        self.assertIsNotNone(self.dialog._save_surface_task)
        self.publish(self.manager.tasks[-1])
        self.assertEqual(errors, [None])
        state = controller.variants['5']['smoothing_state']
        self.assertNotIn('flow_quantiles', state)
        self.assertEqual(len(state['basin_hydrology']['rainfall_results']), 6)
        self.assertIsNone(controller.variants['5']['query'].flow)
        self.assertEqual(controller.variants['5']['query'].rational_context,
                         self.fixture.query.rational_context)

    def test_restore_controller_uses_native_custom_resolution_and_area_after_manual_change(self):
        f = self.fixture
        manual = replace(f.analysis, basin=replace(f.analysis.basin, area_m2=10000),
            diagnostics={'outlet_snap': dict(method='manual_cell_d8',
                selection_mode='user_selected_cell', explicit_user_choice=True,
                reference_area_m2=f.analysis.area_m2)})
        variants = {'25': dict(analysis=manual, query=f.query), '5': self.fine_variant()}
        self.assertEqual(base_variant_key(variants, '5'), '25')
        fine = variants['5']
        controller = ResolutionController(fine['analysis'], fine['query'],
                     lambda *args: None, lambda *args: None, lambda *args: None, lambda *args: None)
        self.addCleanup(controller.dispose)
        controller.restore(dict(active_key='5', variants=variants,
            options=[dict(key=k, resolution=float(k), available=True) for k in variants]))
        self.assertEqual(controller._base_key, '25')
        self.assertEqual(controller.reference_area_m2, f.query.rational_context['area_m2'])
        self.assertIn('método racional', controller._base_option()['label'])

    def test_switching_origin_clears_stale_flow_table_and_allows_raster_restore(self):
        widget = flow_quantiles.FlowQuantileWidget()
        self.addCleanup(widget.deleteLater)
        query = replace(self.fixture.query, flow=10, period=5, source_kind='caumax_raster', rational_context=None)
        widget.set_query(query, snapshot=raster_snapshot())
        self.assertIsNotNone(widget.snapshot())
        widget.set_query(self.fixture.query, snapshot=raster_snapshot())
        widget.restore(raster_snapshot())
        self.assertIsNone(widget.snapshot())
        self.assertTrue(widget.flow_spin.isHidden())
        self.assertTrue(widget.table.isHidden())
        widget.set_query(query, snapshot=raster_snapshot())
        self.assertEqual(widget.snapshot()['input_flow'], 10)
        self.assertTrue(widget.rational_label.isHidden())
        self.assertFalse(widget.table.isHidden())
        fractional = replace(self.fixture.query, flow=.0012345, period=3.2)
        widget.set_query(fractional)
        self.assertIn('0,0012345 m³/s', widget.rational_label.text())
        self.assertIn('T = 3,2 años', widget.rational_label.text())


class RationalSamplingGuardTests(unittest.TestCase):
    def test_sampling_and_discovery_short_circuit_even_with_explicit_paths(self):
        query = SimpleNamespace(source_kind='rational', flow=None, period=None)
        with patch('caumax_consulta.flow_quantiles._sample', side_effect=AssertionError('Q read')), \
                patch('caumax_consulta.flow_quantiles.importlib.import_module', side_effect=AssertionError('Q lookup')):
            self.assertIsNone(flow_quantiles.sample_quantiles(query, ['unsafe.tif'] * 6))
            self.assertEqual(flow_quantiles._source_paths(query, ['unsafe.tif'] * 6), [''] * 6)


if __name__ == '__main__':
    unittest.main()
