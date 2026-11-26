"""Real GeoPackage/QGIS checks for outlines surviving their basin dialog."""
from copy import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject, QgsVectorLayer

from caumax_consulta.boundary_layers import BoundaryLayers, OWNER_PROPERTY, KIND_PROPERTY
import test_drainage_dialog as fixture


class BoundaryLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], True)
        cls.app.initQgis()

    def setUp(self):
        # QGIS's OGR connection pool may retain handles until process exit on Windows.
        self.tmp = tempfile.TemporaryDirectory(prefix='hidrocaumax_boundaries_', ignore_cleanup_errors=True)
        self.folder = Path(self.tmp.name)
        self.project = QgsProject.instance()
        self.initial_ids = set(self.project.mapLayers())
        self.initial_nodes = list(self.project.layerTreeRoot().children())
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.original = QgsGeometry.fromWkt('POLYGON ((0 0,100 0,100 100,0 100,0 0),(20 20,20 30,30 30,30 20,20 20))')
        self.smooth = QgsGeometry.fromWkt('POLYGON ((0 0,90 0,100 90,10 100,0 0))')
        self.analysis = SimpleNamespace(crs_wkt=self.crs.toWkt(), cell_width=25., cell_height=25., outlet_x=50., outlet_y=50.)
        self.query = SimpleNamespace(dem_path=str(self.folder/'dem.tif'), x=50., y=50., crs=self.crs)
        self.controller = BoundaryLayers(self.folder/'stored')

    def tearDown(self):
        for layer_id in set(self.project.mapLayers()) - self.initial_ids:
            self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_nodes:
                self.project.layerTreeRoot().removeChildNode(node)
        self.project.layerTreeRoot().setHasCustomLayerOrder(False)
        self.controller.layers = {}
        self.tmp.cleanup()

    def update(self, smooth=True, **kwargs):
        return self.controller.update(self.analysis, self.query, self.original,
            self.smooth if smooth else None, degree=kwargs.pop('degree', 3), **kwargs)

    def test_real_gpkg_polygons_preserve_holes_crs_and_unfilled_styles(self):
        layers = self.update()
        self.assertEqual(set(layers), {'original', 'suavizada'})
        for kind, layer in layers.items():
            self.assertEqual(layer.providerType(), 'ogr')
            self.assertTrue(Path(layer.source().split('|')[0]).is_file())
            self.assertEqual(layer.crs(), self.crs)
            self.assertEqual(layer.featureCount(), 1)
            properties = layer.renderer().symbol().symbolLayer(0).properties()
            self.assertEqual(properties['style'], 'no')
            self.assertEqual(','.join(properties['outline_color'].split(',')[:4]),
                             '190,0,0,220' if kind == 'original' else '0,80,210,255')
        self.assertTrue(next(layers['original'].getFeatures()).geometry().isGeosEqual(self.original))
        self.assertAlmostEqual(next(layers['original'].getFeatures()).geometry().area(), 9900.)

    def test_boundary_legend_names_show_only_mdt_resolution(self):
        layers = self.update(degree=3)
        root = self.project.layerTreeRoot()
        group = root.findLayer(layers['original'].id()).parent()
        self.assertEqual(group.name(), 'HidroCAUMAX · Divisoria 25x25 m')
        self.assertEqual(layers['original'].name(), 'HidroCAUMAX · Divisoria 25x25 m')
        self.assertEqual(layers['suavizada'].name(),
                         'HidroCAUMAX · Divisoria 25x25 m · suavizada (grado 3)')
        self.assertNotIn('X=', group.name())
        self.assertNotIn('Y=', group.name())
        self.analysis.cell_width = self.analysis.cell_height = 5.
        self.query.dem_path = str(self.folder / 'mdt5.tif')
        fine = self.update(degree=5)
        fine_group = root.findLayer(fine['original'].id()).parent()
        self.assertEqual(fine_group.name(), 'HidroCAUMAX · Divisoria 5x5 m')
        self.assertEqual(fine['original'].name(), 'HidroCAUMAX · Divisoria 5x5 m')
        self.assertNotEqual(fine_group, group)

    def test_degree_updates_geometry_and_attributes_without_replacing_layer_ids(self):
        before = self.update()
        ids = {kind: layer.id() for kind, layer in before.items()}
        self.smooth = QgsGeometry.fromWkt('POLYGON ((0 0,75 0,95 90,0 100,0 0))')
        after = self.update(degree=12)
        self.assertEqual(ids, {kind: layer.id() for kind, layer in after.items()})
        feature = next(after['suavizada'].getFeatures())
        self.assertTrue(feature.geometry().isGeosEqual(self.smooth))
        self.assertEqual(feature['GRADO'], 12)
        self.assertTrue(next(after['original'].getFeatures()).geometry().isGeosEqual(self.original))

    def test_new_controller_reuses_layers_and_reads_manual_legend_choices(self):
        layers = self.update(degree=7)
        root = self.project.layerTreeRoot()
        root.findLayer(layers['original'].id()).setItemVisibilityChecked(False)
        other = BoundaryLayers(self.folder/'stored')
        state = other.existing_state(self.analysis, self.query)
        self.assertEqual(state, {'visibility': {'original': False, 'suavizada': True}, 'degree': 7})
        result = other.update(self.analysis, self.query, self.original, self.smooth, degree=7,
                              visibility=state['visibility'])
        self.assertEqual({l.id() for l in result.values()}, {l.id() for l in layers.values()})
        self.assertFalse(root.findLayer(result['original'].id()).itemVisibilityChecked())

    def test_resolution_variants_are_distinct_and_keep_previous_visibility(self):
        old = self.update(visibility={'original': False, 'suavizada': True})
        self.query.dem_path = str(self.folder/'dem5.tif')
        self.analysis.cell_width = self.analysis.cell_height = 5.
        new = self.update(visibility={'original': True, 'suavizada': False})
        self.assertFalse({l.id() for l in old.values()} & {l.id() for l in new.values()})
        root = self.project.layerTreeRoot()
        self.assertFalse(root.findLayer(old['original'].id()).itemVisibilityChecked())
        self.assertTrue(root.findLayer(old['suavizada'].id()).itemVisibilityChecked())
        self.assertFalse(root.findLayer(old['suavizada'].id()).isVisible())
        self.assertTrue(root.findLayer(new['original'].id()).isVisible())
        self.assertEqual(new['original'].featureCount(), 1)
        self.query.dem_path = str(self.folder/'dem.tif')
        self.analysis.cell_width = self.analysis.cell_height = 25.
        self.update(visibility={'original': False, 'suavizada': True})
        self.assertTrue(root.findLayer(old['suavizada'].id()).isVisible())
        self.assertFalse(root.findLayer(new['original'].id()).isVisible())

    def test_qgis_project_round_trip_keeps_geometries_styles_and_visibility(self):
        layers = self.update(visibility={'original': False, 'suavizada': True})
        path = self.folder/'basin.qgs'
        self.assertTrue(self.project.write(str(path)))
        reopened = QgsProject()
        try:
            self.assertTrue(reopened.read(str(path)))
            for kind, old in layers.items():
                layer = reopened.mapLayer(old.id())
                self.assertTrue(layer.isValid())
                self.assertEqual(layer.featureCount(), 1)
                self.assertTrue(next(layer.getFeatures()).geometry().isGeosEqual(next(old.getFeatures()).geometry()))
                self.assertEqual(layer.customProperty(KIND_PROPERTY), kind)
                self.assertEqual(layer.renderer().symbol().symbolLayer(0).properties()['style'], 'no')
                self.assertEqual(reopened.layerTreeRoot().findLayer(layer.id()).itemVisibilityChecked(), kind == 'suavizada')
        finally:
            reopened.clear()

    def test_boundaries_are_above_terrain_and_official_with_custom_drawing_order(self):
        root = self.project.layerTreeRoot()
        official = root.addGroup('CAUMAX_CAPAS')
        raster = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Official', 'memory')
        self.project.addMapLayer(raster, False)
        official.addLayer(raster)
        terrain = root.addGroup('Relieve propio')
        terrain.setCustomProperty('HidroCAUMAX/order_rank', 30)
        root.setHasCustomLayerOrder(True)
        root.setCustomLayerOrder([raster])
        layers = self.update()
        group = root.findLayer(layers['original'].id()).parent()
        self.assertLess(root.children().index(group), root.children().index(official))
        self.assertLess(root.children().index(group), root.children().index(terrain))
        order = root.customLayerOrder()
        self.assertTrue(all(order.index(layer) < order.index(raster) for layer in layers.values()))

    def test_unchecking_smooth_hides_retained_layer_without_erasing_geometry(self):
        layers = self.update()
        old = bytes(next(layers['suavizada'].getFeatures()).geometry().asWkb())
        result = self.update(smooth=False, visibility={'original': True, 'suavizada': False})
        self.assertEqual(old, bytes(next(result['suavizada'].getFeatures()).geometry().asWkb()))
        self.assertFalse(self.project.layerTreeRoot().findLayer(result['suavizada'].id()).itemVisibilityChecked())

    def test_invalid_geometry_does_not_replace_existing_boundaries(self):
        layers = self.update()
        self.original = QgsGeometry.fromWkt('LINESTRING (0 0,1 1)')
        with self.assertRaisesRegex(ValueError, 'polígono válido'):
            self.update()
        self.assertAlmostEqual(next(layers['original'].getFeatures()).geometry().area(), 9900.)

    def test_explicit_remove_clears_all_visited_results_but_preserves_other_layers_and_files(self):
        first = self.update()
        ids = {layer.id() for layer in first.values()}
        paths = {Path(layer.source().split('|')[0]) for layer in first.values()}
        self.analysis.outlet_x += 25
        self.query.dem_path = str(self.folder/'dem5.tif')
        self.analysis.cell_width = self.analysis.cell_height = 5.
        second = self.update()
        ids.update(layer.id() for layer in second.values())
        paths.update(Path(layer.source().split('|')[0]) for layer in second.values())
        other_query = copy(self.query)
        other_query.x += 1000
        other = BoundaryLayers(self.folder/'other')
        other_ids = {layer.id() for layer in other.update(
            self.analysis, other_query, self.original).values()}
        root = self.project.layerTreeRoot()
        group = root.findLayer(second['original'].id()).parent()
        user_layer = QgsVectorLayer('LineString?crs=EPSG:25830', 'Divisoria original', 'memory')
        self.project.addMapLayer(user_layer, False)
        group.addLayer(user_layer)
        user_id = user_layer.id()
        self.assertEqual(self.controller.remove(), len(ids))
        self.assertTrue(all(self.project.mapLayer(layer_id) is None for layer_id in ids))
        self.assertTrue(all(self.project.mapLayer(layer_id) is not None for layer_id in other_ids))
        self.assertIs(self.project.mapLayer(user_id), user_layer)
        self.assertEqual([node.layerId() for node in group.findLayers()], [user_id])
        self.assertTrue(all(path.is_file() for path in paths))
        self.assertFalse(self.controller.layers)
        self.assertIsNone(self.controller.key)
        self.assertEqual(self.controller.remove(), 0)

    def test_update_after_explicit_remove_restores_real_layers_without_stale_references(self):
        old_ids = {layer.id() for layer in self.update().values()}
        self.controller.remove()
        new = self.update()
        self.assertFalse(old_ids & {layer.id() for layer in new.values()})
        self.assertTrue(next(new['original'].getFeatures()).geometry().isGeosEqual(self.original))
        self.assertTrue(self.project.layerTreeRoot().findLayer(new['original'].id()).isVisible())


class BoundaryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hidrocaumax_boundary_dialog_', ignore_cleanup_errors=True)
        self.folder = Path(self.tmp.name)
        self.store_patch = patch('caumax_consulta.boundary_layers.storage_directory', return_value=self.folder/'stored')
        self.store_patch.start()
        self.manager = SimpleNamespace(tasks=[])
        self.manager.addTask = self.manager.tasks.append
        self.task_patch = patch('caumax_consulta.basin_dialog.QgsApplication.taskManager', return_value=self.manager)
        self.task_patch.start()
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.dialog = self.fixture.dialog

    def tearDown(self):
        self.fixture.tearDown()
        self.fixture.doCleanups()
        self.task_patch.stop()
        self.store_patch.stop()
        self.tmp.cleanup()

    def refresh(self):
        self.dialog._smoothing_timer.stop()
        self.dialog._update_smoothing()

    def test_close_keeps_real_boundary_and_reopening_has_no_duplicate(self):
        layer = self.dialog._boundary_layers.layers['original']
        layer_id = layer.id()
        self.dialog.close()
        self.fixture.app.processEvents()
        self.assertIs(self.fixture.project.mapLayer(layer_id), layer)
        self.assertTrue(layer.isValid())
        original = QgsGeometry()
        original.fromWkb(self.fixture.analysis.geometry_wkb)
        self.assertTrue(next(layer.getFeatures()).geometry().isGeosEqual(original))
        root = self.fixture.project.layerTreeRoot()
        root.findLayer(layer_id).setItemVisibilityChecked(False)
        reopened = self.fixture.make_dialog()
        self.assertEqual(reopened._boundary_layers.layers['original'].id(), layer_id)
        self.assertFalse(reopened.original_check.isChecked())
        self.assertFalse(root.findLayer(layer_id).itemVisibilityChecked())
        self.assertEqual(len([l for l in self.fixture.project.mapLayers().values() if l.customProperty(OWNER_PROPERTY)]), 1)

    def test_smoothing_checkbox_and_degree_update_exact_visible_geometry(self):
        self.dialog.smooth_check.setChecked(True)
        self.dialog.smoothing_degree.setValue(6)
        self.refresh()
        layer = self.dialog._boundary_layers.layers['suavizada']
        old_id = layer.id()
        old_wkb = bytes(next(layer.getFeatures()).geometry().asWkb())
        self.dialog.smoothing_degree.setValue(15)
        self.refresh()
        self.assertEqual(self.dialog._boundary_layers.layers['suavizada'].id(), old_id)
        feature = next(layer.getFeatures())
        self.assertNotEqual(bytes(feature.geometry().asWkb()), old_wkb)
        self.assertTrue(feature.geometry().isGeosEqual(self.dialog.preview_geometries['suavizada']))
        self.assertEqual(feature['GRADO'], 15)
        self.dialog.smooth_check.setChecked(False)
        self.refresh()
        self.assertFalse(self.fixture.project.layerTreeRoot().findLayer(old_id).itemVisibilityChecked())

    def test_saved_session_restores_outline_geometry_and_flags(self):
        from caumax_consulta.session_store import save_session, load_session
        path = self.folder/'saved.caumax.json'
        save_session(path, self.dialog.analysis, self.dialog.query, self.dialog.get_smoothing_state())
        old = self.dialog._boundary_layers.layers['original']
        saved = load_session(path)
        restored = self.fixture.make_dialog(saved)
        self.assertEqual(restored._boundary_layers.layers['original'].id(), old.id())
        self.assertTrue(restored.original_check.isChecked())
        self.assertFalse(restored.smooth_check.isChecked())

    def test_storage_failure_keeps_preview_and_reports_clear_note(self):
        with patch.object(self.dialog._boundary_layers, 'update', side_effect=OSError('Disco de prueba no disponible')):
            self.refresh()
        self.assertIsNotNone(self.dialog._rubber)
        self.assertTrue(self.dialog._smoothing_ready)
        self.assertIn('No se ha podido conservar la divisoria como capa', self.dialog.status.text())
        self.assertIn('Disco de prueba no disponible', self.dialog.status.text())
