"""Real exported grids and update/cleanup preservation in isolated folders."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer

from caumax_consulta import improved_storage as storage
from caumax_consulta.improved_layers import (load_improved_layers, GROUP_NAME, VISIBLE_FILE,
                                           remove_improved_folder_group)


class StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='improved_storage_')
        self.root = Path(self.temp.name)
        self.patches = [patch.object(storage, 'PLUGIN_ROOT', self.root/'plugin'),
                        patch.object(storage, 'backup_directory', return_value=self.root/'backup')]
        for p in self.patches: p.start()
        self.folder = storage.export_directory()
        self.project = QgsProject()

    def tearDown(self):
        self.project.clear()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def raster(self, name, flow=False, offset=0, encoding='ESRI_D8'):
        path = self.folder/name
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 3, 3, 1, gdal.GDT_Byte if flow else gdal.GDT_Float32)
        crs = osr.SpatialReference(); crs.ImportFromEPSG(25830)
        dataset.SetProjection(crs.ExportToWkt())
        dataset.SetGeoTransform((346000 + offset, 25, 0, 4717000, 0, -25))
        dataset.GetRasterBand(1).WriteArray(np.full((3, 3), 1 if flow else 100))
        dataset.SetMetadataItem('HIDROCAUMAX_PRODUCT', 'FLOW_DIRECTION' if flow else 'ELEVATION')
        if flow: dataset.SetMetadataItem('FLOW_DIRECTION_ENCODING', encoding)
        dataset = None
        return path

    def pair(self, suffix='25'):
        dem, d8 = self.raster('MDT_'+suffix+'.tif'), self.raster('D8_'+suffix+'.tif', True)
        storage.record_exports({'dem': str(dem), 'directions_caumax': str(d8)})
        return dem, d8

    def shape(self, name=VISIBLE_FILE):
        path = self.folder/name
        ds = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(path))
        crs = osr.SpatialReference(); crs.ImportFromEPSG(25830)
        layer = ds.CreateLayer(path.stem, crs, ogr.wkbLineString)
        f = ogr.Feature(layer.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt('LINESTRING(346000 4717000,346025 4716975)'))
        layer.CreateFeature(f); ds = None
        return path

    def test_only_complete_compatible_pair_is_registered(self):
        dem = self.raster('MDT.tif')
        storage.record_exports({'dem': str(dem)})
        self.assertIsNone(storage.latest_pair())
        for name, offset, encoding in [('offset.tif', 1, 'ESRI_D8'), ('tau.tif', 0, 'TAUDEM_D8')]:
            d8 = self.raster(name, True, offset, encoding)
            storage.record_exports({'dem': str(dem), 'directions_caumax': str(d8)})
            self.assertIsNone(storage.latest_pair())
        pair = self.pair()
        self.assertEqual(storage.latest_pair(), pair)

    def test_latest_complete_pair_survives_incomplete_and_falls_back_if_changed(self):
        first, second = self.pair('25'), self.pair('5')
        self.assertEqual(storage.latest_pair(), second)
        storage.record_exports({'dem': str(self.raster('only_dem.tif'))})
        self.assertEqual(storage.latest_pair(), second)
        second[1].write_bytes(b'changed file')
        self.assertEqual(storage.latest_pair(), first)
        first[0].unlink()
        self.assertIsNone(storage.latest_pair())

    def test_outside_folder_and_corrupt_indexes_are_ignored(self):
        pair = self.pair()
        outside = self.root/'outside.tif'; outside.write_bytes(pair[0].read_bytes())
        storage.record_exports({'dem': str(outside), 'directions_caumax': str(pair[1])})
        self.assertEqual(storage.latest_pair(), pair)
        index = self.folder/storage.PAIR_INDEX
        for bad in ({}, 'invalid json', [None, {'dem': '../outside.tif', 'directions': 'C:evil.tif'}]):
            index.write_text(json.dumps(bad))
            self.assertIsNone(storage.latest_pair())

    def test_update_restores_exact_exports_sidecars_and_latest_pair(self):
        pair = self.pair()
        self.shape()
        storage.po_path().write_bytes(b'bundled source')
        storage.preserve_exports()
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.name != storage.PO_FILENAME}
        # Simulate QGIS replacing only this isolated plugin's folder.
        for p in self.folder.iterdir(): p.unlink()
        storage.po_path().write_bytes(b'new bundled source')
        storage.restore_exports(legacy_alias=True)
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir() if p.name != storage.PO_FILENAME}, before)
        self.assertEqual(storage.latest_pair(), pair)
        self.assertEqual(storage.po_path().read_bytes(), b'new bundled source')
        self.assertEqual((storage.PLUGIN_ROOT/'data'/storage.PO_FILENAME).read_bytes(), b'new bundled source')

    def test_explicit_cleaning_is_not_resurrected_and_existing_files_are_not_overwritten(self):
        self.pair()
        deleted = self.folder/'MDT_25.tif'
        deleted.unlink(); storage.preserve_exports(); storage.restore_exports()
        self.assertFalse(deleted.exists())
        self.assertFalse((self.root/'backup'/deleted.name).exists())
        present = self.folder/'D8_25.tif'
        present.write_bytes(b'user modification')
        storage.restore_exports()
        self.assertEqual(present.read_bytes(), b'user modification')

    def test_malicious_restore_paths_and_staging_files_are_skipped(self):
        staging = self.folder/'.hidrocaumax_export_partial'; staging.mkdir()
        (staging/'partial.tif').write_bytes(b'partial')
        storage.preserve_exports()
        self.assertFalse((self.root/'backup'/staging.name).exists())
        backup = self.root/'backup'; backup.mkdir(exist_ok=True)
        (backup/storage.BACKUP_INDEX).write_text(json.dumps(['../outside', 'C:/outside', None]))
        storage.restore_exports()
        self.assertEqual(list(self.folder.iterdir()), [staging])

    def test_copy_failure_does_not_leave_a_partial_restore(self):
        source, target = self.root/'source', self.root/'target'
        source.write_bytes(b'complete')
        def broken(src, dst, length):
            dst.write(b'part'); raise OSError('disk full')
        with patch.object(storage.os, 'link', side_effect=OSError('no hardlink')), patch.object(storage.shutil, 'copyfileobj', side_effect=broken):
            with self.assertRaisesRegex(OSError, 'disk full'): storage._copy_missing(source, target)
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), b'complete')

    def test_group_order_visibility_collapsed_legends_and_no_duplicates(self):
        root = self.project.layerTreeRoot()
        root.addGroup('User group'); root.addGroup('CAUMAX_CAPAS')
        self.pair(); self.shape(); self.shape('Red_drenaje_5m.shp')
        self.raster(storage.PO_FILENAME)
        (self.folder/'broken.tif').write_bytes(b'not a raster')
        first = load_improved_layers(self.project)
        self.assertEqual([g.name() for g in root.children()], ['User group', GROUP_NAME, 'CAUMAX_CAPAS'])
        self.assertEqual(first['failed'], ['broken.tif'])
        self.assertEqual(len(first['loaded']), 5)
        nodes = first['group'].findLayers()
        self.assertEqual([n.name() for n in nodes if n.itemVisibilityChecked()], ['Red_drenaje_25m'])
        self.assertTrue(all(not n.isExpanded() for n in nodes))
        po = next(n.layer() for n in nodes if n.name().startswith('Po_Spain'))
        self.assertFalse(po.customProperty('HidroCAUMAX/export_owner', False))
        second = load_improved_layers(self.project)
        self.assertEqual(second['loaded'], first['loaded'])
        self.assertEqual(len(self.project.mapLayers()), 5)
        self.assertEqual(len(root.findGroups()), 3)

    def test_missing_25m_network_leaves_every_layer_off_and_new_exports_are_added(self):
        self.pair(); self.shape('Red_drenaje_5m.shp')
        first = load_improved_layers(self.project)
        self.assertFalse(any(n.itemVisibilityChecked() for n in first['group'].findLayers()))
        self.shape()
        second = load_improved_layers(self.project)
        self.assertEqual(len(second['loaded']), 4)
        self.assertEqual([n.name() for n in second['group'].findLayers() if n.itemVisibilityChecked()], ['Red_drenaje_25m'])

    def test_obsolete_folder_group_is_removed_without_deleting_files_or_user_layers(self):
        self.pair(); self.shape(); self.raster(storage.PO_FILENAME)
        imported = load_improved_layers(self.project)
        before = {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        foreign = QgsVectorLayer('Point?crs=EPSG:25830', 'Capa propia', 'memory')
        self.project.addMapLayer(foreign, False)
        imported['group'].addLayer(foreign).setItemVisibilityChecked(False)
        count = len(imported['loaded'])
        self.assertEqual(remove_improved_folder_group(self.project), count)
        self.assertIsNone(self.project.layerTreeRoot().findGroup(GROUP_NAME))
        self.assertIs(self.project.mapLayer(foreign.id()), foreign)
        self.assertFalse(self.project.layerTreeRoot().findLayer(foreign.id()).itemVisibilityChecked())
        # Closing a GDAL layer may create a statistics .aux.xml sidecar; all
        # pre-existing data and registry files must remain byte-for-byte.
        self.assertTrue(set(before) <= {p.name for p in self.folder.iterdir()})
        for name, payload in before.items():
            self.assertEqual((self.folder/name).read_bytes(), payload, name)
        self.assertEqual(remove_improved_folder_group(self.project), 0)

    def test_network_reexport_does_not_change_latest_pair_and_cleanup_survives_project_reopen(self):
        first = self.pair('25'); second = self.pair('5')
        network = self.shape()
        storage.po_path().write_bytes(b'protected bundled source')
        storage.record_exports({'dem':str(first[0]),'directions_caumax':str(first[1]),'network':str(network)})
        self.assertEqual(storage.latest_pair(), second)
        from caumax_consulta.generated_cleanup import deletion_plan
        plan = deletion_plan(project=self.project, roots=[])
        self.assertIn(str(network.resolve()), plan['files'])
        self.assertIn(str(network.with_suffix('.dbf').resolve()), plan['files'])
        self.assertNotIn(str(storage.po_path().resolve()), plan['files'])


if __name__ == '__main__': unittest.main()
