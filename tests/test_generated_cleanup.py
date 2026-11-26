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
