"""Real Qt/QGIS lifecycle for the native panel, with worker engines isolated."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt, QRect
from qgis.PyQt.QtWidgets import QDialog, QMainWindow, QScrollArea, QVBoxLayout, QPushButton
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

from caumax_consulta import rational_layers_controller as controller_module
from caumax_consulta import rational_layers_panel as module
from caumax_consulta.delineation_cleanup import clear_delineations
from test_rational_layers_controller import Drainage, Manager, Query, Resolution, analysis


class _Drainage(Drainage):
    def complete(self):
        args, kwargs = self.calls[-1]
        key, terrain, query = args[:3]
        products = SimpleNamespace(analysis=terrain, dem_path=query.dem_path,
            caumax_d8_path=query.directions_path, network_path='network.shp')
        geometry = QgsGeometry.fromWkt('LINESTRING(342325 4710000,342350 4710025)')
        state = dict(automatic=kwargs['automatic'], metadata={},
            dem_path=str(products.dem_path),
            caumax_d8_path=str(products.caumax_d8_path),
            threshold_area_m2=kwargs['threshold_area_m2'] or 540000.,
            recommended_threshold_area_m2=540000., network_crs_wkt=query.crs.toWkt(),
            features=[dict(wkb=bytes(geometry.asWkb()), attributes={})])
        self.products[key] = products
        self.busy = False
        self.on_result(key, products, state)
        return products, state


class _Selector(QDialog):
    def __init__(self, iface, context, parent):
        super().__init__(parent)
        self.context = context

    def selection(self):
        return (342312.5, 4710262.5)


class RationalLayersPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rational_panel_')
        folder = Path(self.temp.name)
        dem, directions = folder/'mdt.tif', folder/'d8.tif'
        dem.write_bytes(b'dem path fixture')
        directions.write_bytes(b'd8 path fixture')
        self.query = Query(342317.6, 4710252.9, QgsCoordinateReferenceSystem('EPSG:25830'),
                           str(dem), str(directions))
        self.window = QMainWindow()
        self.canvas = QgsMapCanvas(self.window)
        self.iface = SimpleNamespace(mainWindow=lambda: self.window, mapCanvas=lambda: self.canvas)
        self.manager = Manager()
        self.patches = [
            patch('caumax_consulta.improved_storage.PLUGIN_ROOT', folder/'plugin'),
            patch('caumax_consulta.improved_storage.backup_directory', return_value=folder/'backup'),
            patch('caumax_consulta.boundary_layers.storage_directory', return_value=folder/'boundaries'),
            patch.object(controller_module, 'QgsApplication', SimpleNamespace(taskManager=lambda: self.manager)),
            patch.object(controller_module, 'ResolutionController', Resolution),
            patch.object(controller_module, 'DrainageController', _Drainage),
            patch.object(controller_module, '_analyze_base', return_value=analysis()),
        ]
        for item in self.patches:
            item.start()
        self.panel = module.RationalLayersPanel(self.iface, lambda: self.query, self.window)

    def tearDown(self):
        if not sip.isdeleted(self.panel):
            self.panel._clear_local_delineation()
            self.panel.dispose()
        if not sip.isdeleted(self.window):
            sip.delete(self.window)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QgsProject.instance().removeAllMapLayers()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def discovered(self):
        task = self.manager.tasks[-1]
        task.finished(task.run())
        resolution = self.panel._controller.resolution
        resolution.discovered()
        self.app.processEvents()
        return resolution

    def prepared(self):
        resolution = self.discovered()
        resolution.selected(self.panel._selected_key())
        self.panel.drainage_prepare_button.click()
        return self.panel._controller.drainage.complete()

    def test_resolution_sizes_are_visible_while_reference_is_loading(self):
        panel = self.panel
        self.assertEqual(panel.resolution_combo.count(), 5)
        self.assertEqual(panel._selected_key(), '25')
        self.assertFalse(panel._selected_available())
        self.assertFalse(panel.resolution_combo.model().item(
            panel.resolution_combo.findData('25')).isEnabled())
        self.assertIn('25', panel.resolution_combo.currentText())
        self.discovered()
        self.assertEqual(panel._selected_key(), '25')
        self.assertTrue(panel._selected_available())

    def test_available_fine_choices_and_changed_point_discard_old_work(self):
        resolution = self.discovered()
        panel = self.panel
        self.assertEqual(panel._selected_key(), '25')
        self.assertEqual([panel.resolution_combo.itemData(i) for i in range(3)], ['500', '25', '5'])
        options = resolution.options + [dict(key='2', resolution=2, available=False)]
        resolution.on_options(options)
        self.assertFalse(panel.resolution_combo.model().item(panel.resolution_combo.findData('2')).isEnabled())
        panel.resolution_combo.setCurrentIndex(panel.resolution_combo.findData('5'))
        panel.drainage_prepare_button.click()
        self.assertEqual(resolution.selections, ['25', '5'])
        self.query = replace(self.query, x=self.query.x + 500.)
        self.assertTrue(panel.refresh_context())
        self.assertTrue(resolution.disposed)
        resolution.selected('5')
        self.assertIsNone(panel._state)
        # The last refined outline remains, but stale TauDEM products do not.
        self.assertFalse(any(layer.customProperty('HidroCAUMAX/drainage_owner')
                             for layer in QgsProject.instance().mapLayers().values()))
        self.assertEqual(len(self.manager.tasks), 2)
        self.assertFalse(panel.refresh_context())
        self.assertEqual(len(self.manager.tasks), 2)

    def test_clear_initial_reference_is_sticky_until_explicit_generate(self):
        panel = self.panel
        old_task = self.manager.tasks[0]
        clear_delineations(close_dialogs=True)
        self.assertFalse(panel._disposed)
        self.assertTrue(old_task.isCanceled())
        self.assertTrue(panel.drainage_prepare_button.isEnabled())
        for unused in range(3):
            self.assertFalse(panel.refresh_context())
            self.app.processEvents()
        self.assertEqual(len(self.manager.tasks), 1)
        old_task.finished(True)
        self.assertIsNone(panel._controller.resolution)
        panel.drainage_prepare_button.click()
        self.assertEqual(len(self.manager.tasks), 2)
        resolution = self.discovered()
        self.app.processEvents()
        self.assertEqual(resolution.selections, ['25'])
        resolution.selected('25')
        panel._controller.drainage.complete()
        self.assertFalse(panel._cleared)
        self.assertIsNotNone(panel._state)
        self.assertTrue(panel.network_export_button.isEnabled())

    def test_clear_coverage_discovery_can_retry_without_rebuilding_reference(self):
        panel = self.panel
        task = self.manager.tasks[-1]
        task.finished(task.run())
        resolution = panel._controller.resolution
        unavailable = [dict(option, available=option['key'] == '500') for option in resolution.options]
        resolution.on_options(unavailable)
        self.assertEqual(panel._selected_key(), '500')
        self.assertTrue(panel._selected_available())  # native reference remains available
        clear_delineations()
        self.assertFalse(panel.refresh_context())
        self.assertEqual(resolution.discoveries, 1)
        panel.drainage_prepare_button.click()
        self.assertEqual(resolution.discoveries, 2)
        self.assertEqual(len(self.manager.tasks), 1)
        self.assertTrue(panel._controller.busy)
        resolution.discovered()
        self.app.processEvents()
        self.assertEqual(resolution.selections, ['25'])

    def test_failed_reference_can_retry_from_generate(self):
        task = self.manager.tasks[-1]
        task.error = 'Fallo de lectura temporal'
        task.finished(False)
        self.assertTrue(self.panel.drainage_prepare_button.isEnabled())
        self.panel.drainage_prepare_button.click()
        self.assertEqual(len(self.manager.tasks), 2)
        self.discovered()
        self.assertEqual(self.panel._controller.resolution.selections, ['25'])

    def test_generate_displays_downloaded_dem_before_taudem_network_finishes(self):
        resolution = self.discovered()
        key = self.panel._selected_key()
        resolution.selected(key)
        with patch.object(self.panel._layers, 'display_dem') as display_dem:
            self.panel.drainage_prepare_button.click()
            self.assertIsNone(self.panel._state)
            display_dem.assert_called_once()
            args = display_dem.call_args.args
            self.assertEqual(args[0].cell_width, float(key))
            self.assertEqual(Path(args[1]).name, f'mdt_{key}.tif')
            self.assertIsNone(self.panel._state)
            # QGIS sees the real MDT while the drainage task continues.
            self.assertTrue(self.panel._controller.drainage.busy)

    def test_generate_esri_d8_keeps_new_group_but_deletes_all_old_course_groups(self):
        from caumax_consulta.course_layers import _OWNER_PROPERTY
        from caumax_consulta.drainage_layers import DRAINAGE_GROUP_LABEL
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        old_ids = []
        old_groups = []
        for i in range(2):
            group = root.addGroup('HidroCAUMAX · Curso principal [old]')
            group.setCustomProperty(_OWNER_PROPERTY, 'old-' + str(i))
            layer = QgsVectorLayer('LineString?crs=EPSG:25830',
                                   'Curso principal total (L)', 'memory')
            layer.setCustomProperty(_OWNER_PROPERTY, 'old-' + str(i))
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            old_ids.append(layer.id())
            old_groups.append(group)
        resolution = self.discovered()
        resolution.selected('25')
        self.panel.drainage_prepare_button.click()
        # A queued or failed computation must not erase the previous result:
        # only a successfully published new D8 group may retire it.
        self.assertTrue(all(project.mapLayer(key) is not None for key in old_ids))
        self.panel._controller.drainage.complete()
        self.assertTrue(all(project.mapLayer(key) is None for key in old_ids))
        self.assertFalse(any(g.customProperty(_OWNER_PROPERTY, '')
                             for g in root.findGroups(recursive=True)))
        self.assertEqual(len([g for g in root.findGroups(recursive=True)
                              if g.name().startswith(DRAINAGE_GROUP_LABEL)]), 1)
        group = self.panel._layers._group
        self.assertIn(group, root.findGroups(recursive=True))
        self.assertTrue(all(project.mapLayer(layer.id()) is layer
                            for layer in self.panel._layers.layers.values()))
        self.assertTrue(Path(self.panel._controller.resolution.base_query.dem_path).exists())

    def test_ready_network_visibility_exports_and_clear_preserve_native_dialog(self):
        products, state = self.prepared()
        panel = self.panel
        self.assertIs(panel._products, products)
        network = panel._layers.layers['network']
        self.assertTrue(network.isValid())
        self.assertEqual(network.featureCount(), 1)
        self.assertIs(QgsProject.instance().mapLayer(network.id()), network)
        # Generar capas D8 automatically loads the real MDT into the same
        # TauDEM group, directly after network and flow-direction raster.
        dem = panel._layers.layers['dem']
        self.assertEqual(dem.name(), 'MDT 25x25m')
        group = QgsProject.instance().layerTreeRoot().findLayer(dem.id()).parent()
        self.assertEqual([node.layer().id() for node in group.children()][2], dem.id())
        self.assertFalse(QgsProject.instance().layerTreeRoot()
                        .findLayer(dem.id()).itemVisibilityChecked())
        self.assertEqual(panel._layers.visibility(),
                         dict(network=True, directions=False, dem=False, hillshade=True))
        self.assertTrue(group.isExpanded())
        self.assertTrue(all(not node.isExpanded() for node in group.children()))
        # A new Generate click restores the requested presentation even when
        # the preview or previous result was manually changed or collapsed.
        group.setItemVisibilityChecked(False)
        group.setExpanded(False)
        for node in group.children():
            node.setExpanded(True)
            node.setItemVisibilityChecked(node.layer() is dem)
        panel.drainage_prepare_button.click()
        products, state = panel._controller.drainage.complete()
        group = panel._layers._group
        self.assertEqual(panel._layers.visibility(),
                         dict(network=True, directions=False, dem=False, hillshade=True))
        self.assertTrue(group.itemVisibilityChecked())
        self.assertTrue(group.isExpanded())
        self.assertTrue(all(not node.isExpanded() for node in group.children()))
        panel.drainage_visible.setChecked(False)
        self.assertFalse(panel._layers.visibility()['network'])
        self.assertTrue(all(button.isEnabled() for button in panel.product_buttons))
        with patch.object(module.QFileDialog, 'getSaveFileName', return_value=('/tmp/product.tif', '')):
            with patch('caumax_consulta.hydro_export.export_dem', return_value='/tmp/dem.tif') as export:
                panel.dem_export_button.click()
                export.assert_called_once_with(products, '/tmp/product.tif')
            with patch('caumax_consulta.hydro_export.export_directions', return_value='/tmp/d8.tif') as export:
                panel.directions_export_button.click()
                export.assert_called_once_with(products, '/tmp/product.tif', encoding='caumax')
            with patch('caumax_consulta.hydro_export.export_network', return_value='/tmp/network.shp') as export:
                panel.network_export_button.click()
                export.assert_called_once_with(products, '/tmp/product.tif', snapshot=state)
        self.assertEqual(set(state['metadata']['exports']), {'dem', 'directions_caumax', 'network'})
        clear_delineations()
        self.assertFalse(panel._disposed)
        self.assertFalse(QgsProject.instance().mapLayers())
        self.assertIsNone(panel._state)
        self.assertFalse(any(button.isEnabled() for button in panel.product_buttons))
        panel.refresh_context()
        self.assertEqual(len(self.manager.tasks), 1)
        panel.drainage_prepare_button.click()
        self.assertTrue(panel._controller.drainage.busy)
        panel._controller.drainage.complete()
        self.assertIsNotNone(panel._state)

    def test_close_keeps_saved_25m_outline_and_no_new_layer_is_inserted(self):
        self.prepared()
        from caumax_consulta.boundary_layers import OWNER_PROPERTY
        project = QgsProject.instance()
        outlines = [layer for layer in project.mapLayers().values()
                    if layer.customProperty(OWNER_PROPERTY)]
        self.assertEqual(len(outlines), 1)
        layer = outlines[0]
        gpkg = Path(layer.source().split('|', 1)[0])
        self.assertTrue(gpkg.is_file())
        initial_id = layer.id()
        self.panel.dispose()
        self.app.processEvents()
        self.assertIs(project.mapLayer(initial_id), layer)
        self.assertTrue(gpkg.is_file())
        self.assertEqual(len([item for item in project.mapLayers().values()
                              if item.customProperty(OWNER_PROPERTY)]), 1)

    def test_threshold_debounce_blocks_export_and_changed_context_cancels_it(self):
        self.prepared()
        panel = self.panel
        panel.drainage_threshold.setValue(.25)
        self.assertFalse(panel.drainage_auto.isChecked())
        self.assertTrue(panel._threshold_timer.isActive())
        with patch.object(module.QFileDialog, 'getSaveFileName') as chooser:
            panel._export('network')
            chooser.assert_not_called()
        self.query = replace(self.query, y=self.query.y + 500.)
        panel.refresh_context()
        self.assertFalse(panel._threshold_timer.isActive())
        self.assertFalse(any(layer.customProperty('HidroCAUMAX/drainage_owner')
                             for layer in QgsProject.instance().mapLayers().values()))
        self.assertIsNotNone(next((layer for layer in QgsProject.instance().mapLayers().values()
                                   if layer.customProperty('HidroCAUMAX/boundary_owner')), None))
        self.assertIsNone(panel._products)

    def test_manual_selector_tuple_is_applied_and_context_change_closes_stale_selector(self):
        resolution = self.discovered()
        panel = self.panel
        with patch('caumax_consulta.outlet_selection.OutletSelectionDialog', _Selector):
            panel.drainage_prepare_button.click()
            resolution.ambiguous('25')
            self.app.processEvents()
            panel.outlet_button.click()
            selector = panel._selector
            self.assertIsNotNone(selector)
            selector.accept()
            self.assertEqual(resolution.manual[-1][1:], (342312.5, 4710262.5))
            self.assertIsNone(panel._selector)
            resolution.ambiguous('25')
            self.app.processEvents()
            panel.outlet_button.click()
            stale = panel._selector
            self.assertIsNotNone(stale)
            self.query = replace(self.query, x=self.query.x + 500.)
            panel.refresh_context()
            self.assertIsNone(panel._selector)
            panel._apply_selection(stale, stale.context)
            self.assertEqual(len(resolution.manual), 1)

    def test_native_destruction_cancels_worker_and_late_callback_is_harmless(self):
        panel = self.panel
        controller = panel._controller
        task = self.manager.tasks[-1]
        sip.delete(self.window)
        self.assertTrue(controller._disposed)
        self.assertTrue(task.isCanceled())
        task.finished(True)
        self.assertIsNone(controller.resolution)

    def test_cancel_button_download_and_taudem_restore_retry_and_hide_at_rest(self):
        resolution = self.discovered()
        panel = self.panel
        resolution.selected('25')
        self.assertTrue(panel.cancel_button.isHidden())
        panel.drainage_prepare_button.click()
        drainage = panel._controller.drainage
        self.assertFalse(panel.cancel_button.isHidden())
        self.assertFalse(panel.drainage_prepare_button.isEnabled())
        self.assertFalse(panel.delete_layers_button.isEnabled())
        panel.cancel_button.click()
        self.assertFalse(drainage.busy)
        self.assertIsNone(panel._controller._pending)
        self.assertTrue(panel.cancel_button.isHidden())
        self.assertTrue(panel.drainage_prepare_button.isEnabled())
        self.assertFalse(any(button.isEnabled() for button in panel.product_buttons))
        panel.drainage_prepare_button.click()
        drainage.complete()
        self.assertTrue(panel.cancel_button.isHidden())
        self.assertTrue(all(button.isEnabled() for button in panel.product_buttons))
        # An explicit generation that also needs a finer DEM can be stopped.
        panel._controller.resolution.variants.pop('25')
        panel.drainage_prepare_button.click()
        self.assertTrue(resolution.busy)
        self.assertFalse(panel.cancel_button.isHidden())
        panel.cancel_button.click()
        self.assertFalse(resolution.busy)
        self.assertTrue(panel.drainage_prepare_button.isEnabled())

    def test_requested_text_decimal_comma_and_compact_selector(self):
        resolution = self.discovered()
        resolution.selected('25')
        self.assertEqual(self.panel.help_label.text(), 'Genera las tres capas necesarias para ejecutar CAUMAX con resolución mayor de la original 500x500m (esquema Esri-D8): (1) ráster MDT, (2) ráster Direcciones de flujo, y (3) shape Red de Drenaje.')
        self.assertIn('54,000 km²', self.panel.drainage_status.text())
        self.assertIn(',', self.panel.drainage_threshold.text())
        self.assertLessEqual(self.panel.resolution_combo.width(),
                             self.panel.fontMetrics().horizontalAdvance('500×500 m') + 38)

    def test_clean_button_and_default_export_folder(self):
        self.prepared()
        panel = self.panel
        panel.resize(760, 480); self.window.show(); panel.show(); self.app.processEvents()
        clean, outlet = panel.delete_layers_button, panel.outlet_button
        self.assertEqual(clean.text(), 'Limpiar capas')
        self.assertFalse(clean.icon().isNull())
        self.assertIn('#FADBD8', clean.styleSheet())
        self.assertIs(clean.parent(), outlet.parent())
        self.assertLess(outlet.geometry().right(), clean.x())
        self.assertLessEqual(abs(clean.geometry().center().y()-outlet.geometry().center().y()), 1)
        folder = Path(self.temp.name)/'plugin'/'ResolucionMejorada'
        with patch.object(module.QFileDialog, 'getSaveFileName', return_value=('', '')) as chooser:
            for kind, name in (('dem','MDT_25m.tif'), ('directions','Direccion_D8_caumax_25m.tif'),
                               ('network','Red_drenaje_25m.shp')):
                panel._export(kind)
                self.assertEqual(Path(chooser.call_args.args[2]), folder/name)

    def test_actual_panel_autosizes_at_rest_during_work_and_after_cancel(self):
        from caumax_consulta.rational_layers_dialog import RationalLayersDialog
        resolution = self.discovered(); resolution.selected('25')
        window = RationalLayersDialog(self.window)
        layout = QVBoxLayout(window)
        scroll = QScrollArea(window); scroll.setWidgetResizable(True); scroll.setWidget(self.panel)
        layout.addWidget(scroll); layout.addWidget(QPushButton('Cerrar', window))
        screen = SimpleNamespace(availableGeometry=lambda: QRect(0, 0, 1600, 1200))
        with patch.object(window, 'screen', return_value=screen):
            window.fit_content(self.panel, scroll); window.show()
            for action in (lambda: None, self.panel.drainage_prepare_button.click,
                           self.panel.cancel_button.click):
                action()
                for _ in range(8):
                    self.app.processEvents()
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                self.assertLessEqual(window.height(), 1140)
        scroll.takeWidget(); self.panel.setParent(self.window); sip.delete(window)

    def test_cancel_invalidates_queued_automatic_delineation(self):
        resolution = self.discovered(); resolution.selected('25')
        self.panel._queued_resolution = '25'
        self.panel._cancel_generation()
        count = len(resolution.selections)
        self.panel._start_delineation('25')
        self.assertEqual(len(resolution.selections), count)

    def test_four_buttons_scale_once_and_fit_narrow_and_wide_panel(self):
        self.discovered()
        panel = self.panel
        scroll = QScrollArea()
        self.addCleanup(lambda: sip.delete(scroll) if not sip.isdeleted(scroll) else None)
        scroll.setAttribute(Qt.WA_DontShowOnScreen)
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.show()
        for width in (650, 800, 650):
            with self.subTest(width=width):
                scroll.resize(width, 400)
                for unused in range(4):
                    self.app.processEvents()
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(panel.width(), scroll.viewport().width())
                for button in (panel.drainage_prepare_button,) + panel.product_buttons:
                    base = panel.button_base_sizes[button]
                    self.assertEqual(button.width(), round(base.width() * 1.10))
                    self.assertEqual(button.height(), round(base.height() * 1.10))
                    bounds = button.mapTo(panel, button.rect().bottomRight())
                    self.assertLess(bounds.x(), panel.width())
                self.assertLess(panel.drainage_visible.geometry().right(), panel.width())
        scroll.takeWidget()
        panel.setParent(self.window)


if __name__ == '__main__':
    unittest.main()
