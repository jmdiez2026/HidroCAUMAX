"""Reference-layer lifecycle with actual QGIS projects, trees and task threads."""
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from osgeo import gdal, osr
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsDataSourceUri,
                       QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsLayerTreeMapCanvasBridge, QgsMapCanvas, QgsMessageBar

from caumax_consulta.delineation_cleanup import clear_delineation_layers
from caumax_consulta import map_services as maps


class ReferenceLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'reference.tif')
        dataset = gdal.GetDriverByName('GTiff').Create(self.path, 2, 2, 1, gdal.GDT_Byte)
        dataset.SetGeoTransform((400000, 25, 0, 4500000, 0, -25))
        crs = osr.SpatialReference(); crs.ImportFromEPSG(25830)
        dataset.SetProjection(crs.ExportToWkt()); dataset.GetRasterBand(1).Fill(1)
        dataset = None
        self.project = QgsProject()
        self.project.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        self.addCleanup(self.project.clear)
        self.total = sum(len(service.layers) for service in maps.WMS_SERVICES)
        self.requests = []

    def layer_factory(self, uri, title, provider):
        self.requests.append((uri, title, provider, QThread.currentThread()))
        return QgsRasterLayer(self.path, title, 'gdal')

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_bundled_po_is_last_local_offline_layer_and_kept_on_repeat(self):
        self.assertTrue(maps.PO_SERVICE.path.is_file())
        self.assertEqual(maps.REFERENCE_SERVICES[-1], maps.PO_SERVICE)
        self.load()
        report = maps.load_reference_layers(self.project, services=(maps.PO_SERVICE,))
        self.assertEqual((report.added, report.failed), (1, 0))
        root, _ = self.groups()
        node = root.children()[-1]
        layer = node.layer()
        self.assertEqual(layer.name(), 'Po España (CLC2000) 100mx100m · AdrLBallesteros')
        self.assertEqual(layer.providerType(), 'gdal')
        self.assertEqual(layer.crs().authid(), 'EPSG:25830')
        self.assertEqual((layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY()), (100, 100))
        self.assertFalse(node.isVisible())
        self.assertEqual(Path(layer.source()), maps.PO_SERVICE.path)
        node.setItemVisibilityChecked(True)
        report = maps.load_reference_layers(self.project, services=(maps.PO_SERVICE,))
        self.assertEqual((report.added, report.existing), (0, 1))
        self.assertTrue(node.isVisible())
        self.assertEqual(clear_delineation_layers(self.project), 0)

    def test_flood_extents_follow_aemet_exact_titles_off_and_distinct_endpoints(self):
        service = maps.FLOOD_EXTENT_SERVICE
        services = (maps.AEMET_SERVICE, service, maps.WMS_SERVICES[0])
        # Simulate a different completion order, then insert AEMET above.
        maps.load_reference_layers(self.project, services=services[1:], layer_factory=self.layer_factory)
        maps.load_reference_layers(self.project, services=services, layer_factory=self.layer_factory,
            station_factory=lambda title, cancelled: QgsVectorLayer('Point?crs=EPSG:4326', title, 'memory'))
        root, groups = self.groups()
        self.assertEqual(list(groups), ['aemet', 'snczi_extent', 'snczi'])
        self.assertEqual(groups['snczi_extent'].name(), 'Extensión zonas inundables')
        self.assertEqual([node.name() for node in groups['snczi_extent'].children()], [
            'Z.I con alta probabilidad (T=10)', 'Z.I. de inundación frecuente (T=50)',
            'Z.I. probabilidad media u ocasional (T=100)',
            'Z.I. con probabilidad baja o excepcional (T=500)'])
        self.assertFalse(any(node.isVisible() for node in root.findLayers()))
        for period, spec in zip((10, 50, 100, 500), service.layers):
            uri = QgsDataSourceUri(); uri.setEncodedUri(maps.build_wms_uri(service, spec))
            self.assertEqual(uri.param('layers'), f'Zi_laminas_q{period}')
            self.assertEqual(uri.param('styles'), f'Zi_laminas_q{period}_estilo')
            self.assertTrue(uri.param('url').endswith(f'/Zi_laminas_q{period}/wms'))

    def test_unavailable_extent_does_not_skip_other_return_period_endpoints(self):
        def factory(uri, title, provider):
            decoded = QgsDataSourceUri(); decoded.setEncodedUri(uri)
            return (QgsRasterLayer('/absent.tif', title, 'gdal')
                if decoded.param('layers') == 'Zi_laminas_q10' else self.layer_factory(uri, title, provider))
        report = maps.load_reference_layers(self.project, services=(maps.FLOOD_EXTENT_SERVICE,), layer_factory=factory)
        self.assertEqual((report.added, report.failed), (3, 1))

    def load(self, **kwargs):
        return maps.load_reference_layers(self.project, services=maps.WMS_SERVICES, layer_factory=self.layer_factory, **kwargs)

    def groups(self):
        root = next(group for group in self.project.layerTreeRoot().findGroups()
                    if group.customProperty(maps.ROOT_PROPERTY, '') == 'v1')
        return root, {group.customProperty(maps.SERVICE_PROPERTY): group for group in root.children()}

    def test_encoded_server_names_styles_and_nested_url_survive_uri(self):
        agro = next(service for service in maps.WMS_SERVICES if service.service_id == 'agroclima')
        spec = next(layer for layer in agro.layers if layer.layer_id == 'papadakis')
        service = replace(agro, url=agro.url + '?token=a+b&language=es')
        encoded = maps.build_wms_uri(service, spec)
        uri = QgsDataSourceUri(); uri.setEncodedUri(encoded)
        self.assertEqual(uri.param('url'), service.url)
        self.assertEqual(uri.param('layers'), 'Clasificación climáticos')
        self.assertEqual(uri.param('styles'), 'Agricultura_ClimaticosClasificacion')
        self.assertEqual(uri.param('format'), 'image/png')
        self.assertEqual(uri.param('crs'), 'EPSG:25830')
        flood = next(service for service in maps.WMS_SERVICES if service.service_id == 'snczi')
        self.assertEqual([layer.name for layer in flood.layers],
                         ['NZ.Flood.FluvialT10', 'NZ.Flood.FluvialT100', 'NZ.Flood.FluvialT500'])
        for layer in flood.layers:
            uri.setEncodedUri(maps.build_wms_uri(flood, layer))
            self.assertEqual(uri.param('styles'), '')
            self.assertIn('styles=', maps.build_wms_uri(flood, layer))
        uri.setEncodedUri(maps.build_xyz_uri(maps.GOOGLE_SERVICE))
        self.assertEqual(uri.param('type'), 'xyz')
        self.assertEqual(uri.param('url'), 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}')
        self.assertEqual(uri.param('zmin'), '0')
        self.assertEqual(uri.param('zmax'), '19')
        self.assertEqual(uri.param('crs'), 'EPSG:3857')

    def test_groups_default_visibility_repeat_and_foreign_content_are_preserved(self):
        foreign_group = self.project.layerTreeRoot().addGroup(maps.ROOT_TITLE)
        foreign = QgsVectorLayer('Point?crs=EPSG:25830', 'Capa del usuario', 'memory')
        self.project.addMapLayer(foreign, False); foreign_group.addLayer(foreign)
        original_crs = self.project.crs()
        report = self.load()
        self.assertEqual((report.added, report.existing, report.failed), (self.total, 0, 0))
        self.assertEqual(self.project.crs(), original_crs)
        root, groups = self.groups()
        self.assertIs(self.project.layerTreeRoot().children()[0], root)
        self.assertIs(self.project.layerTreeRoot().children()[-1], foreign_group)
        self.assertEqual(list(groups), [service.service_id for service in maps.WMS_SERVICES])
        self.assertTrue(all(not group.isExpanded() for group in groups.values()))
        flood = groups['snczi']
        self.assertTrue(flood.isMutuallyExclusive())
        self.assertFalse(any(node.isVisible() for node in root.findLayers()))
        self.assertEqual([node.itemVisibilityChecked() for node in flood.children()], [False, False, False])
        flood.children()[0].setItemVisibilityChecked(True)
        self.assertEqual([node.itemVisibilityChecked() for node in flood.children()], [True, False, False])
        agro = groups['agroclima']
        self.assertFalse(agro.itemVisibilityChecked())
        self.assertEqual(sum(node.itemVisibilityChecked() for node in agro.children()), 0)
        self.assertEqual(len(agro.children()), 17)
        self.assertFalse(any(node.isVisible() for node in agro.children()))
        root.setName('Mis referencias')
        agro.setItemVisibilityChecked(True)
        self.requests.clear()
        again = self.load()
        self.assertEqual((again.added, again.existing, again.failed), (0, self.total, 0))
        self.assertEqual(self.requests, [])
        self.assertEqual([node.itemVisibilityChecked() for node in flood.children()], [True, False, False])
        self.assertTrue(agro.itemVisibilityChecked())
        self.assertEqual(len(self.project.layerTreeRoot().children()), 2)
        self.assertIs(self.project.mapLayer(foreign.id()), foreign)
        self.assertEqual(clear_delineation_layers(self.project), 0)
        self.assertEqual(len(self.project.mapLayers()), self.total + 1)
        land = groups['corine'].children()[0].layer()
        self.assertIn('SIOSE de Alta Resolución 2017', land.metadata().abstract())
        self.assertIn('no descarga un ráster local', land.metadata().abstract())

    def test_repeat_moves_existing_block_inside_caumax_first_without_recreating_layers(self):
        self.load()
        references, groups = self.groups()
        references.setName('Referencias renombradas')
        groups['snczi'].children()[2].setItemVisibilityChecked(True)
        groups['agroclima'].setItemVisibilityChecked(True)
        original_nodes = list(references.findLayers())
        original_groups = list(references.findGroups())
        checked = [node.itemVisibilityChecked() for node in original_nodes]
        layers = dict(self.project.mapLayers())
        root = self.project.layerTreeRoot()
        official = root.addGroup('CAUMAX_CAPAS')
        native = []
        for name in ('Red10km2', 'Ríos'):
            layer = QgsVectorLayer('LineString?crs=EPSG:25830', name, 'memory')
            self.project.addMapLayer(layer, False); official.addLayer(layer); native.append(layer)
        result = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Resultado hidrológico', 'memory')
        self.project.addMapLayer(result)
        result.setCustomProperty('HidroCAUMAX/order_rank', 20)
        outside = QgsVectorLayer('Point?crs=EPSG:25830', 'Ajena', 'memory')
        self.project.addMapLayer(outside)
        reference_order = list(reversed(list(layers.values())))
        root.setCustomLayerOrder([result, native[0], reference_order[0], outside,
                                  native[1]] + reference_order[1:])
        root.setHasCustomLayerOrder(True)
        root_nodes = [node for node in root.children() if node is not references]
        loader = self.make_loader()
        before_extent = QgsRectangle(self.canvas.extent())
        before_crs = self.canvas.mapSettings().destinationCrs()
        self.requests.clear()
        with patch.object(maps.QgsApplication, 'taskManager') as manager:
            self.assertTrue(loader.load())
            self.assertTrue(loader.load())
        manager.assert_not_called()
        self.assertEqual(self.requests, [])
        self.assertIs(official.children()[0], references)
        self.assertEqual([node.name() for node in official.children()[1:]], ['Red10km2', 'Ríos'])
        self.assertEqual(list(root.children()), root_nodes)
        self.assertEqual(references.name(), 'Referencias renombradas')
        self.assertEqual(references.findLayers(), original_nodes)
        self.assertEqual(references.findGroups(), original_groups)
        self.assertEqual([node.itemVisibilityChecked() for node in original_nodes], checked)
        self.assertTrue(groups['agroclima'].itemVisibilityChecked())
        self.assertTrue(groups['snczi'].isMutuallyExclusive())
        self.assertEqual([node.itemVisibilityChecked() for node in groups['snczi'].children()], [False, False, True])
        self.assertEqual(root.customLayerOrder(), [result] + reference_order + [native[0], outside, native[1]])
        self.assertEqual({key: self.project.mapLayer(key) for key in layers}, layers)
        self.assertEqual(self.canvas.extent(), before_extent)
        self.assertEqual(self.canvas.mapSettings().destinationCrs(), before_crs)
        self.assertEqual(self.project.crs().authid(), 'EPSG:4326')

    def test_new_block_is_first_inside_caumax_and_its_custom_drawing_order(self):
        root = self.project.layerTreeRoot()
        official = root.addGroup('CAUMAX_CAPAS')
        native = QgsVectorLayer('LineString?crs=EPSG:25830', 'Red10km2', 'memory')
        self.project.addMapLayer(native, False); official.addLayer(native)
        root.setCustomLayerOrder([native]); root.setHasCustomLayerOrder(True)
        self.load()
        references = official.children()[0]
        self.assertEqual(references.customProperty(maps.ROOT_PROPERTY), 'v1')
        self.assertEqual(official.children()[1].name(), 'Red10km2')
        self.assertEqual(root.customLayerOrder()[-1], native)
        self.assertEqual(len(root.customLayerOrder()), self.total + 1)

    def test_google_xyz_is_inserted_after_snczi_hidden_and_repeated_visibility_is_kept(self):
        services = (maps.WMS_SERVICES[0], maps.GOOGLE_SERVICE, maps.WMS_SERVICES[1])
        report = maps.load_reference_layers(self.project, services=services, layer_factory=self.layer_factory)
        self.assertEqual((report.added, report.failed), (5, 0))
        root, groups = self.groups()
        self.assertEqual(list(groups), ['snczi', 'google_satellite', 'pfafstetter'])
        google = groups['google_satellite']
        node = google.children()[0]
        self.assertFalse(google.itemVisibilityChecked())
        self.assertFalse(node.itemVisibilityChecked())
        self.assertEqual(node.layer().attribution(), '© Google y proveedores de imágenes')
        self.assertEqual(node.layer().attributionUrl(), 'https://maps.google.com')
        self.assertIn('teselas XYZ', node.layer().metadata().abstract())
        google.setItemVisibilityChecked(True); node.setItemVisibilityChecked(True)
        factory = Mock(side_effect=AssertionError('Repeated references must be reused'))
        report = maps.load_reference_layers(self.project, services=services, layer_factory=factory)
        factory.assert_not_called()
        self.assertEqual((report.added, report.existing), (0, 5))
        self.assertTrue(google.itemVisibilityChecked())
        self.assertTrue(node.itemVisibilityChecked())
        request = next(item for item in self.requests if item[1] == 'Google Satellite')
        self.assertEqual(request[2], 'wms')

    def test_one_unavailable_service_does_not_repeat_timeouts_or_block_other_sources(self):
        def factory(uri, title, provider):
            decoded = QgsDataSourceUri(); decoded.setEncodedUri(uri)
            if 'CaractAgroClimaticas' in decoded.param('url'):
                self.requests.append((uri, title, provider, QThread.currentThread()))
                return QgsRasterLayer('/does-not-exist.tif', title, 'gdal')
            return self.layer_factory(uri, title, provider)
        report = maps.load_reference_layers(self.project, services=maps.WMS_SERVICES, layer_factory=factory)
        self.assertEqual((report.added, report.failed), (self.total - 17, 17))
        self.assertEqual(len(self.requests), self.total - 17 + 1)
        self.assertEqual({failure.service_id for failure in report.failures}, {'agroclima'})
        self.assertIn('Sin cargar: 17', report.message)
        self.assertEqual(len(self.project.mapLayers()), self.total - 17)
        retried = self.load()
        self.assertEqual((retried.added, retried.existing, retried.failed), (17, self.total - 17, 0))
        self.assertEqual(list(self.groups()[1]), [service.service_id for service in maps.WMS_SERVICES])

    def test_saved_project_restores_ownership_and_prevents_second_load(self):
        self.load()
        path = str(Path(self.temp.name) / 'references.qgs')
        self.assertTrue(self.project.write(path))
        restored = QgsProject()
        self.addCleanup(restored.clear)
        self.assertTrue(restored.read(path))
        factory = Mock(side_effect=AssertionError('Existing layers must be reused'))
        report = maps.load_reference_layers(restored, services=maps.WMS_SERVICES, layer_factory=factory)
        self.assertEqual((report.added, report.existing, report.failed), (0, self.total, 0))
        factory.assert_not_called()

    def make_loader(self):
        self.window = QMainWindow()
        self.addCleanup(sip.delete, self.window)
        self.canvas = QgsMapCanvas(self.window)
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        self.canvas.setExtent(QgsRectangle(1, 2, 101, 102))
        self.canvas_bridge = QgsLayerTreeMapCanvasBridge(self.project.layerTreeRoot(), self.canvas)
        self.canvas_bridge.setAutoSetupOnFirstLayer(True)
        self.bar = QgsMessageBar(self.window)
        iface = SimpleNamespace(messageBar=lambda: self.bar, mapCanvas=lambda: self.canvas,
                                layerTreeCanvasBridge=lambda: self.canvas_bridge)
        service = next(service for service in maps.WMS_SERVICES if service.service_id == 'corine')
        loader = maps.ReferenceLayerLoader(iface, services=(service,), project=self.project,
                                          layer_factory=self.layer_factory)
        self.addCleanup(loader.dispose)
        self.addCleanup(sip.delete, self.canvas_bridge)
        self.addCleanup(self.canvas.stopRendering)
        return loader

    def test_background_creation_publishes_on_gui_without_changing_the_first_map_view(self):
        loader = self.make_loader()
        extent = QgsRectangle(self.canvas.extent())
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        project_crs = self.project.crs()
        loop = QEventLoop()
        loader.busy_changed.connect(lambda busy: None if busy else loop.quit())
        timer = QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(5000)
        self.assertTrue(loader.load())
        progress_item = loader._message
        self.assertIsNotNone(progress_item)
        self.assertFalse(loader.load())
        loop.exec_(); timer.stop()
        self.assertFalse(loader.busy, 'Background task did not finish')
        self.assertEqual(len(self.project.mapLayers()), 1)
        self.assertIsNot(self.requests[0][3], self.app.thread())
        self.assertIs(next(iter(self.project.mapLayers().values())).thread(), self.app.thread())
        self.assertEqual(self.canvas.extent(), extent)
        self.assertEqual(self.canvas.mapSettings().destinationCrs(), canvas_crs)
        self.assertEqual(self.project.crs(), project_crs)
        self.assertTrue(self.canvas_bridge.autoSetupOnFirstLayer())
        self.assertIn('añadidas: 1', self.bar.currentItem().text())
        self.assertNotIn(progress_item, self.bar.items())
        self.assertEqual(len(self.bar.items()), 1)

    def test_services_publish_independently_and_timeout_removes_only_our_message(self):
        loader = self.make_loader()
        loader.services = (maps.WMS_SERVICES[0], maps.WMS_SERVICES[-1])
        with patch.object(maps.QgsApplication, 'taskManager', return_value=SimpleNamespace(addTask=Mock())):
            self.assertTrue(loader.load())
        tasks = sorted(loader._tasks, key=lambda task: task.pending[0][0].service_id)
        fast, slow = tasks
        progress_item = loader._message
        self.assertTrue(fast.run())
        fast.finished(True)
        self.assertTrue(loader.busy)
        self.assertGreater(len(self.project.mapLayers()), 0)
        ids = set(self.project.mapLayers())
        other = self.bar.pushWidget(self.bar.createMessage('Otro complemento', 'Mensaje ajeno'))
        loader._timed_out()
        self.assertFalse(loader.busy)
        self.assertTrue(slow.isCanceled())
        self.assertNotIn(progress_item, self.bar.items())
        self.assertIn(other, self.bar.items())
        self.assertIn('Sin cargar:', self.bar.currentItem().text())
        self.assertEqual(set(self.project.mapLayers()), ids)
        self.assertFalse(slow.run())
        slow.finished(False)
        self.assertEqual(set(self.project.mapLayers()), ids)
        self.assertFalse(loader._retiring)

    def test_unload_or_project_clear_discards_late_task_results(self):
        for stop in ('dispose', 'clear'):
            loader = self.make_loader()
            manager = SimpleNamespace(addTask=Mock())
            with patch.object(maps.QgsApplication, 'taskManager', return_value=manager):
                self.assertTrue(loader.load())
            task = next(iter(loader._tasks))
            self.assertTrue(task.run())
            if stop == 'dispose':
                loader.dispose()
            else:
                self.project.clear()
            self.assertTrue(task.isCanceled())
            task.finished(True)
            self.assertFalse(loader.busy)
            self.assertEqual(len(self.project.mapLayers()), 0)
            loader.dispose()


class ReferenceMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def test_menu_command_busy_state_and_unload(self):
        from caumax_consulta import native_plugin as native
        window = QMainWindow()
        self.addCleanup(window.deleteLater)
        iface = SimpleNamespace(mainWindow=lambda: window, addPluginToMenu=Mock(), removePluginMenu=Mock())
        replacements = []
        for name in ('CaumaxBridge', 'RationalExportBridge', 'RationalInputsBridge',
                     'RationalLayersBridge', 'VisibleDemController', 'UpdateNotifier'):
            replacement = patch.object(native, name)
            replacement.start(); replacements.append(replacement)
            self.addCleanup(replacement.stop)
        plugin = native.NativeCaumaxPlugin(iface)
        connected_load = patch.object(plugin._map_services, 'load')
        load = connected_load.start()
        self.addCleanup(connected_load.stop)
        plugin.initGui()
        self.addCleanup(plugin.unload)
        action = plugin.reference_action
        self.assertEqual(action.text(), 'Cargar capas HidroCAUMAX')
        self.assertIn(unittest.mock.call('HidroCAUMAX', action), iface.addPluginToMenu.call_args_list)
        action.trigger()
        load.assert_called_once()
        plugin._map_services.busy_changed.emit(True)
        self.assertFalse(action.isEnabled())
        plugin._map_services.busy_changed.emit(False)
        self.assertTrue(action.isEnabled())
        plugin.unload()
        self.assertTrue(plugin._map_services._disposed)
        self.assertIn(unittest.mock.call('HidroCAUMAX', action), iface.removePluginMenu.call_args_list)


if __name__ == '__main__':
    unittest.main()
