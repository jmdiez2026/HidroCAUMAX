"""Explicit cleaning includes reference maps; automatic basin cleanup does not."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from osgeo import gdal
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer
from caumax_consulta import generated_cleanup as cleanup, map_services as maps
from caumax_consulta.clean_layers_ui import confirm_cleanup
from caumax_consulta.delineation_cleanup import clear_delineations


class ReferenceCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'reference.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(self.path), 3, 3, 1, gdal.GDT_Byte)
        ds.SetGeoTransform((0, 5, 0, 15, 0, -5))
        ds.GetRasterBand(1).Fill(10)
        ds = None
        self.project = QgsProject()
        self.addCleanup(self.project.clear)
        self.generated = patch('caumax_consulta.improved_storage.generated_files', return_value=[])
        self.generated.start(); self.addCleanup(self.generated.stop)

    def references(self):
        group = self.project.layerTreeRoot().addGroup(maps.ROOT_TITLE)
        group.setCustomProperty(maps.ROOT_PROPERTY, 'v1')
        po = QgsRasterLayer(str(self.path), 'Po España', 'gdal')
        po.setCustomProperty(maps.SERVICE_PROPERTY, 'po_clc2000')
        po.setCustomProperty(maps.LAYER_PROPERTY, 'runoff_threshold')
        self.project.addMapLayer(po, False); group.addLayer(po)
        satellite = QgsVectorLayer('Point?crs=EPSG:25830', 'Google Satellite', 'memory')
        satellite.setCustomProperty(maps.SERVICE_PROPERTY, 'google_satellite')
        satellite.setCustomProperty(maps.LAYER_PROPERTY, 'satellite')
        self.project.addMapLayer(satellite)
        return group, po, satellite

    def loader(self, project=None):
        loader = maps.ReferenceLayerLoader(SimpleNamespace(messageBar=lambda:None),
            project=self.project if project is None else project, services=(maps.WMS_SERVICES[-1],),
            layer_factory=lambda uri,title,provider:QgsRasterLayer(str(self.path),title,'gdal'))
        self.addCleanup(loader.dispose)
        return loader

    def test_reference_only_plan_removes_group_and_satellite_without_deleting_data(self):
        self.references()
        original = self.path.read_bytes()
        cleanup.register_dataset(self.path, self.project)  # An incorrect old export record.
        plan = cleanup.deletion_plan(self.project, roots=[])
        self.assertEqual(len(plan['layers']), 2)
        self.assertEqual(plan['files'], {})
        self.assertIn(str(self.path.resolve()), plan['protected'])
        result = cleanup.delete_generated(plan, self.project)
        self.assertEqual(result['layers'], 2)
        self.assertFalse(result['removed'])
        self.assertFalse(self.project.mapLayers())
        self.assertFalse(self.project.layerTreeRoot().children())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(cleanup.deletion_plan(self.project, roots=[])['files'])

    def test_foreign_layer_in_reference_group_and_shared_source_survive(self):
        group, po, satellite = self.references()
        foreign = QgsRasterLayer(str(self.path), 'Po España', 'gdal')
        self.project.addMapLayer(foreign, False); group.addLayer(foreign)
        cleanup.register_dataset(self.path, self.project)
        plan = cleanup.deletion_plan(self.project, roots=[])
        self.assertNotIn(foreign.id(), plan['layers'])
        cleanup.delete_generated(plan, self.project)
        self.assertEqual(list(self.project.mapLayers()), [foreign.id()])
        self.assertEqual(group.findLayers()[0].layerId(), foreign.id())
        self.assertTrue(self.path.exists())

    def test_automatic_new_basin_cleanup_preserves_references(self):
        group, po, satellite = self.references()
        identifiers = set(self.project.mapLayers())
        clear_delineations(close_dialogs=True, project=self.project)
        self.assertEqual(set(self.project.mapLayers()), identifiers)
        clear_delineations(project=self.project, include_references=True)
        self.assertFalse(self.project.mapLayers())

    def test_pending_load_is_cancelled_only_in_target_project_and_late_results_stay_out(self):
        loader = self.loader()
        other_project = QgsProject(); self.addCleanup(other_project.clear)
        other = self.loader(other_project)
        with patch.object(maps.QgsApplication, 'taskManager', return_value=SimpleNamespace(addTask=Mock())):
            loader.load(); other.load()
            task = next(iter(loader._tasks))
            self.assertTrue(task.run())  # Prepared, not published yet.
            plan = cleanup.deletion_plan(self.project, roots=[])
            self.assertTrue(plan['references_loading'])
            cleanup.delete_generated(plan, self.project)
            self.assertTrue(task.isCanceled())
            self.assertFalse(loader.busy)
            self.assertTrue(other.busy)
            task.finished(True)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.assertFalse(self.project.mapLayers())
            loader.load()
            retry = next(iter(loader._tasks))
            self.assertTrue(retry.run()); retry.finished(True)
            self.assertTrue(loader.has_references)
        other.cancel()
        for task in tuple(other._retiring):
            task.finished(False)

    def test_reference_loaded_after_plan_is_protected_when_confirming(self):
        layer = QgsRasterLayer(str(self.path), 'Exportación registrada', 'gdal')
        self.project.addMapLayer(layer)
        cleanup.register_dataset(self.path, self.project)
        plan = cleanup.deletion_plan(self.project, roots=[])
        self.assertIn(str(self.path.resolve()), plan['files'])
        layer.setCustomProperty(maps.SERVICE_PROPERTY, 'po_clc2000')
        layer.setCustomProperty(maps.LAYER_PROPERTY, 'runoff_threshold')
        result = cleanup.delete_generated(plan, self.project)
        self.assertTrue(self.path.exists())
        self.assertFalse(result['removed'])

    def confirm(self, role):
        real_delete = cleanup.delete_generated
        seen = []
        def answer(prompt):
            seen.append((prompt.text(), prompt.informativeText()))
            next(button for button in prompt.buttons() if prompt.buttonRole(button)==role).click()
        with patch.object(cleanup, 'deletion_plan', return_value=cleanup.deletion_plan(self.project, roots=[])), \
             patch.object(cleanup, 'delete_generated', side_effect=lambda plan:real_delete(plan,self.project)), \
             patch('caumax_consulta.improved_storage.preserve_exports'), \
             patch.object(QMessageBox, 'exec', answer):
            message = confirm_cleanup(None)
        return message, seen

    def test_red_button_confirmation_cancels_cleanly_then_removes_reference_only_group(self):
        self.references()
        before = set(self.project.mapLayers())
        message, seen = self.confirm(QMessageBox.RejectRole)
        self.assertIsNone(message)
        self.assertEqual(set(self.project.mapLayers()), before)
        self.assertIn('0 archivos', seen[0][0])
        self.assertIn('P0, se conservan', seen[0][1])
        message, _ = self.confirm(QMessageBox.DestructiveRole)
        self.assertIn('retirado 2 capas', message)
        self.assertFalse(self.project.layerTreeRoot().children())
        self.assertTrue(self.path.exists())

    def test_red_button_also_prunes_empty_nested_reference_groups(self):
        root = self.project.layerTreeRoot().addGroup(maps.ROOT_TITLE)
        root.setCustomProperty(maps.ROOT_PROPERTY, 'v1')
        child = root.addGroup('SNCZI')
        child.setCustomProperty(maps.SERVICE_PROPERTY, 'snczi')
        message, seen = self.confirm(QMessageBox.DestructiveRole)
        self.assertTrue(seen)
        self.assertFalse(self.project.layerTreeRoot().children())

    def test_red_button_cancel_preserves_pending_load_accept_stops_it(self):
        loader = self.loader()
        with patch.object(maps.QgsApplication, 'taskManager', return_value=SimpleNamespace(addTask=Mock())):
            loader.load()
        task = next(iter(loader._tasks))
        message, seen = self.confirm(QMessageBox.RejectRole)
        self.assertIsNone(message)
        self.assertTrue(seen)
        self.assertTrue(loader.busy)
        self.assertFalse(task.isCanceled())
        message, seen = self.confirm(QMessageBox.DestructiveRole)
        self.assertTrue(task.isCanceled())
        self.assertFalse(loader.busy)
        task.finished(False)


if __name__ == '__main__':
    unittest.main()
