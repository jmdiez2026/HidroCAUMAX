"""Disk removal is exercised only on temporary datasets, never user caches."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from osgeo import gdal, osr
from qgis.core import QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer
from caumax_consulta import generated_cleanup as cleanup


class GeneratedCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = QgsProject()
        self.addCleanup(self.project.clear)

    def raster(self, name, tagged=False):
        path = self.root / name
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 3, 3, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((300000, 5, 0, 4700000, 0, -5))
        crs = osr.SpatialReference(); crs.ImportFromEPSG(25830)
        ds.SetProjection(crs.ExportToWkt()); ds.GetRasterBand(1).Fill(100)
        if tagged:
            ds.SetMetadataItem('HIDROCAUMAX_PRODUCT', 'ELEVATION')
        ds = None
        layer = QgsRasterLayer(str(path), name)
        self.project.addMapLayer(layer)
        return path, layer

    def test_prior_tagged_export_removed_but_original_and_named_foreign_layer_survive(self):
        exported, layer = self.raster('exported.tif', True)
        native, original = self.raster('original.tif')
        original.setCustomProperty('HidroCAUMAX/terrain_owner', 'original-display')
        foreign, other = self.raster('MDT_5x5m.tif')
        other_id = other.id()
        memory = QgsVectorLayer('LineString?crs=EPSG:25830', 'network', 'memory')
        memory.setCustomProperty('HidroCAUMAX/drainage_owner', 'test')
        self.project.addMapLayer(memory)
        plan = cleanup.deletion_plan(self.project, roots=[])
        self.assertEqual(len(plan['layers']), 3)
        self.assertIn(str(native), plan['protected'])
        result = cleanup.delete_generated(plan, self.project)
        self.assertFalse(exported.exists())
        self.assertTrue(native.exists())
        self.assertTrue(foreign.exists())
        self.assertIsNotNone(self.project.mapLayer(other_id))
        self.assertFalse(result['failed'])

    def test_strict_cleanup_removes_exports_backups_all_cache_files_and_registry(self):
        from caumax_consulta import improved_storage as storage
        exported = self.root / 'plugin' / 'ResolucionMejorada'
        backups = self.root / 'profile' / 'HidroCAUMAX' / 'ResolucionMejorada'
        temp_cache = self.root / 'private-cache'
        for root in (exported, backups, temp_cache / 'nested'):
            root.mkdir(parents=True)
        po = exported / storage.PO_FILENAME
        po.write_bytes(b'Original included P0: never delete')
        auxiliary = Path(str(po) + '.aux.xml')
        auxiliary.write_bytes(b'Original P0 auxiliary')
        (exported / storage.PAIR_INDEX).write_text('[]')
        (exported / storage.GENERATED_INDEX).write_text('{}')
        (backups / storage.BACKUP_INDEX).write_text('[]')
        (backups / 'MDT_25m.tif').write_bytes(b'old preserved copy')
        (backups / 'extra.tmp').write_bytes(b'stale temporary')
        (temp_cache / 'nested' / 'directions.dat').write_bytes(b'worker data')
        (temp_cache / 'nested' / 'worker.part').write_bytes(b'unfinished')
        result_tif = exported / 'MDT_25m.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(result_tif), 4, 4, 1, gdal.GDT_Float32)
        ds.GetRasterBand(1).Fill(42)
        ds = None
        layer = QgsRasterLayer(str(result_tif), 'MDT mejorado')
        layer.setCustomProperty('HidroCAUMAX/drainage_owner', 'test')
        self.project.addMapLayer(layer)
        manually_loaded = exported / 'unknown-old-file.txt'
        manually_loaded.write_text('also from dedicated output store')
        foreign = self.root / 'user-owned-original.tif'
        foreign.write_bytes(b'unrelated file outside private stores')
        with patch.object(storage, 'PLUGIN_ROOT', self.root / 'plugin'), \
             patch.object(storage, 'po_path', return_value=po), \
             patch.object(storage, 'backup_directory', return_value=backups), \
             patch.object(cleanup, 'cache_roots', return_value=(temp_cache,)):
            plan = cleanup.deletion_plan(self.project)
            self.assertIn(str(result_tif.resolve()), plan['files'])
            self.assertIn(str((temp_cache / 'nested' / 'worker.part').resolve()), plan['files'])
            self.assertNotIn(str(po.resolve()), plan['files'])
            self.assertNotIn(str(auxiliary.resolve()), plan['files'])
            outcome = cleanup.delete_generated(plan, self.project)
            self.assertFalse(outcome['failed'], outcome['failed'])
            self.assertEqual(self.project.mapLayers(), {})
            self.assertFalse(result_tif.exists())
            self.assertFalse((exported / storage.PAIR_INDEX).exists())
            self.assertFalse((exported / storage.GENERATED_INDEX).exists())
            self.assertFalse(manually_loaded.exists())
            self.assertFalse(backups.exists())
            self.assertFalse(temp_cache.exists())
            self.assertTrue(po.exists())
            self.assertTrue(auxiliary.exists())
            self.assertTrue(foreign.exists())
            self.assertNotIn(cleanup.REGISTRY, self.project.customVariables())
            # Repeated cleanup cannot resurrect any preserved export.
            again = cleanup.deletion_plan(self.project)
            self.assertFalse(again['files'])
            self.assertFalse(again['layers'])

    def test_late_gdal_auxiliary_is_deleted_after_original_tif(self):
        cache = self.root / 'private-cache'
        cache.mkdir()
        target = cache / 'dem.tif'
        target.write_bytes(b'generated')
        auxiliary = Path(str(target) + '.aux.xml')
        plan = cleanup.deletion_plan(self.project, roots=[cache])
        real_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == target:
                auxiliary.write_bytes(b'GDAL sidecar written during release')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            result = cleanup.delete_generated(plan, self.project)
        self.assertFalse(target.exists())
        self.assertFalse(auxiliary.exists())
        self.assertFalse(cache.exists())
        self.assertFalse(result['failed'])
        self.assertEqual(len(result['removed']), 2)

    def test_profile_p0_and_its_late_auxiliary_survive_strict_cleanup(self):
        from caumax_consulta import improved_storage as storage
        installed = self.root / 'plugin' / storage.FOLDER_NAME
        backup = self.root / 'profile' / 'HidroCAUMAX' / storage.FOLDER_NAME
        installed.mkdir(parents=True)
        backup.mkdir(parents=True)
        source = installed / storage.PO_FILENAME
        reference = backup / storage.PO_FILENAME
        source.write_bytes(b'Bundled P0')
        reference.write_bytes(b'User profile P0')
        generated = backup / 'MDT_25m.tif'
        generated.write_bytes(b'generated terrain')
        auxiliary = backup / storage.PO_AUX_FILENAME
        real_unlink = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == generated:
                auxiliary.write_bytes(b'P0 metadata written on GDAL close')
            return real_unlink(path, *args, **kwargs)
        with patch.object(storage, 'PLUGIN_ROOT', self.root / 'plugin'), \
             patch.object(storage, 'backup_directory', return_value=backup), \
             patch.object(cleanup, 'cache_roots', return_value=()):
            # A wrong historical registry must not turn reference data into results.
            cleanup.register_dataset(reference, self.project)
            plan = cleanup.deletion_plan(self.project)
            self.assertNotIn(str(reference.resolve()), plan['files'])
            with patch.object(Path, 'unlink', unlink):
                result = cleanup.delete_generated(plan, self.project)
            self.assertFalse(result['failed'], result['failed'])
            self.assertFalse(generated.exists())
            self.assertEqual(reference.read_bytes(), b'User profile P0')
            self.assertEqual(source.read_bytes(), b'Bundled P0')
            self.assertEqual(auxiliary.read_bytes(), b'P0 metadata written on GDAL close')

    def test_registered_shapefile_family_and_unloaded_exports_are_included(self):
        target = self.root / 'network.shp'
        for suffix in ('.shp', '.shx', '.dbf', '.prj'):
            target.with_suffix(suffix).write_bytes(b'created by the test')
        unrelated = self.root / 'network.txt'; unrelated.write_text('keep')
        cleanup.register_dataset(target, self.project)
        plan = cleanup.deletion_plan(self.project, roots=[])
        self.assertEqual(len(plan['files']), 4)
        result = cleanup.delete_generated(plan, self.project)
        self.assertEqual(len(result['removed']), 4)
        self.assertTrue(unrelated.exists())

    def test_replaced_file_after_review_is_preserved_and_reported(self):
        path = self.root / 'saved.tif'; path.write_bytes(b'old')
        cleanup.register_dataset(path, self.project)
        plan = cleanup.deletion_plan(self.project, roots=[])
        path.write_bytes(b'a different dataset')
        result = cleanup.delete_generated(plan, self.project)
        self.assertTrue(path.exists())
        self.assertEqual(len(result['failed']), 1)

    def test_generated_cache_without_open_layers_and_locked_file(self):
        cache = self.root / 'cache'; cache.mkdir()
        first = cache / 'mdt.tif'; first.write_bytes(b'cache terrain')
        second = cache / 'd8.tif'; second.write_bytes(b'cache directions')
        plan = cleanup.deletion_plan(self.project, roots=[cache])
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == second:
                raise PermissionError('used by another process')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink):
            result = cleanup.delete_generated(plan, self.project)
        self.assertFalse(first.exists()); self.assertTrue(second.exists())
        self.assertEqual(len(result['failed']), 1)


if __name__ == '__main__':
    unittest.main()
