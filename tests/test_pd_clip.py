"""PyQGIS tests for CEDEX Pd-T clipping, original values and visible layers."""
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
from osgeo import gdal, ogr, osr

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtTest import QSignalSpy
from caumax_consulta.po_clip import (
    PD_EXPORT_FILENAME, basin_clip_fingerprint, clip_po_to_basin, export_pd_clip,
    inspect_clipped_raster, publish_clipped_raster)
from caumax_consulta.basin_hydrology_widget import BasinHydrologyWidget


class PdCedexClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        self.wkt = srs.ExportToWkt()
        self.original = self.directory / 'PdT100_CEDEX.tif'
        raster = gdal.GetDriverByName('GTiff').Create(
            str(self.original), 5, 5, 1, gdal.GDT_Float32)
        raster.SetProjection(self.wkt)
        raster.SetGeoTransform((500000., 100., 0., 4700000., 0., -100.))
        band = raster.GetRasterBand(1)
        band.SetNoDataValue(-9999.)
        band.WriteArray(np.arange(25, dtype=np.float32).reshape(5, 5) + 60)
        raster = None
        shape = ogr.CreateGeometryFromWkt(
            'POLYGON ((500100 4699900,500400 4699900,500400 4699600,'
            '500100 4699600,500100 4699900))')
        self.polygon = bytes(shape.ExportToWkb())
        self.project = QgsProject.instance()
        self.original_layer_ids = set(self.project.mapLayers())

    def tearDown(self):
        new = list(set(self.project.mapLayers()) - self.original_layer_ids)
        if new:
            self.project.removeMapLayers(new)

    def test_ceDEX_selected_raster_clips_on_native_grid_and_preserves_input(self):
        source_before = self.original.read_bytes()
        destination = self.directory / 'pd_t100_clip.tif'
        clipped = clip_po_to_basin(
            self.original, self.polygon, self.wkt, destination, kind='pd')
        self.assertEqual(clipped, destination)
        self.assertEqual(self.original.read_bytes(), source_before)
        source = gdal.Open(str(self.original))
        result = gdal.Open(str(destination))
        self.assertEqual((result.RasterXSize, result.RasterYSize), (3, 3))
        self.assertEqual(result.GetGeoTransform(),
                         (500100., 100., 0., 4699900., 0., -100.))
        self.assertEqual(result.GetMetadataItem('HIDROCAUMAX_PRODUCT'),
                         'PD_BASIN_CLIP')
        np.testing.assert_array_equal(result.ReadAsArray(),
                                      source.ReadAsArray(1, 1, 3, 3))
        result = source = None
        exported = self.directory / PD_EXPORT_FILENAME
        self.assertEqual(PD_EXPORT_FILENAME, 'CONSULTA_Pd-T CEDEX.tif')
        self.assertEqual(export_pd_clip(destination, exported), exported)
        self.assertEqual(destination.read_bytes(), exported.read_bytes())
        with self.assertRaisesRegex(ValueError, 'existe'):
            export_pd_clip(destination, exported)

    def test_persisted_clip_metadata_matches_source_and_active_geometry(self):
        path = self.directory / 'pd_t100_verified.tif'
        clip_po_to_basin(self.original, self.polygon, self.wkt, path, kind='pd')
        fingerprint = basin_clip_fingerprint(self.polygon, self.wkt)
        self.assertEqual(inspect_clipped_raster(
            path, 'pd', self.original, fingerprint=fingerprint), path)
        with self.assertRaisesRegex(ValueError, 'divisoria'):
            inspect_clipped_raster(path, 'pd', self.original, fingerprint='x'*64)
        with self.assertRaisesRegex(ValueError, 'original'):
            inspect_clipped_raster(path, 'pd', 'wrong.tif', fingerprint=fingerprint)
        with self.assertRaisesRegex(ValueError, 'recorte'):
            inspect_clipped_raster(path, 'po', self.original, fingerprint=fingerprint)
        with self.assertRaises(FileNotFoundError):
            inspect_clipped_raster(self.directory / 'missing.tif', 'pd',
                                   self.original, fingerprint=fingerprint)

    def test_p0_and_selected_pd_are_loaded_and_explicitly_visible(self):
        p0_path = self.directory / 'po_cut.tif'
        pd_path = self.directory / 'pd_cut.tif'
        clip_po_to_basin(self.original, self.polygon, self.wkt, p0_path)
        clip_po_to_basin(self.original, self.polygon, self.wkt, pd_path, kind='pd')
        p0_layer = publish_clipped_raster(p0_path, kind='po', project=self.project)
        pd_layer = publish_clipped_raster(
            pd_path, kind='pd', period=100, project=self.project)
        for layer in (p0_layer, pd_layer):
            self.assertTrue(layer.isValid())
            self.assertIs(self.project.mapLayer(layer.id()), layer)
            node = self.project.layerTreeRoot().findLayer(layer.id())
            self.assertIsNotNone(node)
            self.assertTrue(node.itemVisibilityChecked())
        self.assertEqual(pd_layer.customProperty('HidroCAUMAX/return_period'), 100)
        self.assertEqual(pd_layer.customProperty('HidroCAUMAX/hydrology_clipped'), 'pd')
        self.assertIn('T=100', pd_layer.name())
        # Reopening a consultation must reuse the already published raster.
        self.assertIs(publish_clipped_raster(
            pd_path, kind='pd', period=100, project=self.project), pd_layer)
        self.assertEqual(len(set(self.project.mapLayers())-self.original_layer_ids), 2)

    def test_reopened_user_export_is_visible_without_reenrolling_in_cleanup(self):
        from unittest.mock import patch
        output = self.directory / 'saved_pd_t100.tif'
        clip_po_to_basin(self.original, self.polygon, self.wkt,
                          output, kind='pd')
        with patch('caumax_consulta.generated_cleanup.register_dataset') as register:
            layer = publish_clipped_raster(
                output, kind='pd', period=100, project=self.project,
                register_generated=False)
            self.assertTrue(layer.isValid())
            node = self.project.layerTreeRoot().findLayer(layer.id())
            self.assertTrue(node.itemVisibilityChecked())
            register.assert_not_called()
            self.assertIs(publish_clipped_raster(
                output, kind='pd', period=100, project=self.project,
                register_generated=False), layer)
            register.assert_not_called()

    def test_period_selector_is_in_requested_row_and_disables_old_export(self):
        widget = BasinHydrologyWidget()
        self.addCleanup(widget.close)
        widget.show()
        self.app.processEvents()
        row = widget.rainfall_group.layout().itemAt(2).layout()
        self.assertIs(row.itemAt(0).widget(), widget.pd_clip_button)
        self.assertIs(row.itemAt(1).widget(), widget.pd_period_combo)
        self.assertIs(row.itemAt(2).widget(), widget.pd_export_button)
        self.assertEqual(widget.selected_pd_period(), 100)
        self.assertEqual([
            widget.pd_period_combo.itemData(index)
            for index in range(widget.pd_period_combo.count())
        ], [2, 5, 10, 25, 100, 500])
        widget._state = {'rainfall_results': {
            '100': {'source_path': str(self.original), 'status': 'ok'},
            '500': {'source_path': str(self.original), 'status': 'partial'},
        }}
        widget._refresh_pd_actions()
        self.assertTrue(widget.pd_clip_button.isEnabled())
        spy = QSignalSpy(widget.pd_clip_requested)
        widget.pd_clip_button.click()
        self.assertEqual(len(spy), 1)
        self.assertEqual(spy[0][0], 100)
        widget.set_pd_clip_export_available(True, 100)
        self.assertTrue(widget.pd_export_button.isEnabled())
        widget.pd_period_combo.setCurrentIndex(
            widget.pd_period_combo.findData(500))
        self.assertFalse(widget.pd_export_button.isEnabled())
        self.assertTrue(widget.pd_clip_button.isEnabled())
        widget.pd_period_combo.setCurrentIndex(
            widget.pd_period_combo.findData(25))
        self.assertFalse(widget.pd_clip_button.isEnabled())

if __name__ == '__main__':
    unittest.main()
