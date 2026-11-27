"""Portable complete basin archive, native QGIS layers and recovery tests."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
from osgeo import gdal

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsGeometry, QgsPointXY, QgsProject, QgsRasterLayer,
                       QgsVectorLayer)
from caumax_consulta import basin_archive as archive
from caumax_consulta.closure_layers import analysis_closure_layer
from caumax_consulta.drainage_layers import (OWNER_PROPERTY, ROLE_PROPERTY,
                                              PIN_PROPERTY)


class BasinArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.improved = self.folder / 'plugin' / 'ResolucionMejorada'
        self.improved.mkdir(parents=True)
        self.project = QgsProject.instance()
        self.original_ids = set(self.project.mapLayers())
        self.original_groups = list(self.project.layerTreeRoot().children())
        self.patcher = patch('caumax_consulta.improved_storage.export_directory',
                             return_value=self.improved)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')

    def tearDown(self):
        self.project.removeMapLayers(list(set(self.project.mapLayers())-self.original_ids))
        for group in list(self.project.layerTreeRoot().children()):
            if group not in self.original_groups:
                self.project.layerTreeRoot().removeChildNode(group)

    def test_complete_archive_restores_mixed_memory_network_raster_yellow_point_and_improved_files(self):
        source_dem = self.improved / 'MDT_25m.tif'
        raster = gdal.GetDriverByName('GTiff').Create(str(source_dem), 3, 3,
                                                      1, gdal.GDT_Float32)
        raster.SetProjection(self.crs.toWkt())
        raster.SetGeoTransform((353350, 25, 0, 4725287.5, 0, -25))
        raster.GetRasterBand(1).WriteArray(np.arange(9, dtype='float32').reshape(3,3))
        raster = None
        extra = self.improved / 'Red_drenaje_25m.shp.prj'
        extra.write_text('archivo auxiliar conservado')
        raster_layer = QgsRasterLayer(str(source_dem), 'MDT 25x25m', 'gdal')
        raster_layer.setCustomProperty(OWNER_PROPERTY, 'archive-1')
        raster_layer.setCustomProperty(ROLE_PROPERTY, 'dem')
        network = QgsVectorLayer('LineString?crs=EPSG:25830', 'Red de drenaje Esri-D8', 'memory')
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY([
            QgsPointXY(353365,4725270), QgsPointXY(353390,4725245)]))
        ok, _ = network.dataProvider().addFeatures([feature])
        self.assertTrue(ok)
        network.updateExtents()
        network.setCustomProperty(OWNER_PROPERTY, 'archive-1')
        network.setCustomProperty(ROLE_PROPERTY, 'network')
        point = analysis_closure_layer(SimpleNamespace(
            crs_wkt=self.crs.toWkt(), selected_x=353387.14,
            selected_y=4725249.11, outlet_x=353387.5, outlet_y=4725237.5))
        point.setCustomProperty(OWNER_PROPERTY, 'archive-1')
        point.setCustomProperty(ROLE_PROPERTY, 'closure')
        self.project.addMapLayer(network, False)
        self.project.addMapLayer(point, False)
        self.project.addMapLayer(raster_layer, False)
        group = self.project.layerTreeRoot().insertGroup(
            0, 'HidroCAUMAX · Drenaje Esri-D8 [25×25 m]')
        group.setCustomProperty(OWNER_PROPERTY, 'archive-1')
        group.setCustomProperty(PIN_PROPERTY, True)
        group.addLayer(point)
        group.addLayer(network)
        group.addLayer(raster_layer)
        native = SimpleNamespace(
            txtX=SimpleNamespace(text=lambda: '353387,14'),
            txtY=SimpleNamespace(text=lambda: '4725249,11'),
            txtArea=SimpleNamespace(text=lambda: '9,416'))
        target = self.folder / 'cuenca_completa.hidrocuenca'
        path, manifest = archive.save_complete_basin(
            target, native_dialog=native, project=self.project,
            queries=[SimpleNamespace(x=353385, y=4725251, source_kind='consulta_capas',
                                     crs=self.crs)])
        self.assertEqual(path, target)
        self.assertEqual(len(manifest['layers']), 3)
        self.assertIn('txtArea', manifest['rational_values'])
        self.assertEqual(len(manifest['points']), 2)
        with ZipFile(target) as bundle:
            self.assertIn('manifest.json', bundle.namelist())
            self.assertTrue(any(x.startswith('improved/') for x in bundle.namelist()))
            self.assertEqual(len([x for x in bundle.namelist() if x.startswith('styles/')]), 3)
        self.project.removeMapLayers([point.id(), network.id(), raster_layer.id()])
        old_root = group.parent()
        if old_root is not None:
            old_root.removeChildNode(group)
        loaded, destination = archive.open_complete_basin(target, project=self.project)
        self.assertEqual(len(loaded['layers']), 3)
        self.assertTrue((destination / 'improved' / 'MDT_25m.tif').exists())
        self.assertEqual((destination / 'improved' / extra.name).read_text(),
                         'archivo auxiliar conservado')
        imported = [layer for layer in self.project.mapLayers().values()
                    if layer.customProperty(OWNER_PROPERTY, '') == 'archive-1']
        self.assertEqual(len(imported), 3)
        restored_point = next(layer for layer in imported
                              if layer.customProperty(ROLE_PROPERTY) == 'closure')
        restored_network = next(layer for layer in imported
                                if layer.customProperty(ROLE_PROPERTY) == 'network')
        self.assertEqual(restored_network.featureCount(), 1)
        self.assertEqual(restored_point.renderer().symbol().color().name(), '#ffe800')
        self.assertAlmostEqual(next(restored_point.getFeatures()).geometry().asPoint().x(),
                               353387.14, 5)
        restored_group = self.project.layerTreeRoot().findLayer(
            restored_point.id()).parent()
        self.assertEqual(restored_group.findLayers()[0].layerId(), restored_point.id())
        self.assertTrue(restored_group.customProperty(PIN_PROPERTY, False))
        self.assertEqual(archive._digest(target), archive._digest(path))

    def test_green_blue_cross_export_is_included_in_complete_basin(self):
        """The green exported outlet must survive Guardar CUENCA and Abrir cuenca."""
        from caumax_consulta.closure_layers import (
            GREEN_CLOSURE_ROLE, green_closure_symbol)
        green = QgsVectorLayer('Point?crs=EPSG:25830',
                               'Cierre del curso principal · cruz azul', 'memory')
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(
            QgsPointXY(353387.14, 4725249.11)))
        self.assertTrue(green.dataProvider().addFeatures([feature])[0])
        green.renderer().setSymbol(green_closure_symbol())
        green.setCustomProperty(GREEN_CLOSURE_ROLE, True)
        self.project.addMapLayer(green)
        green_name = green.name()
        destination = self.folder / 'con_cierre_verde.hidrocuenca'
        _, manifest = archive.save_complete_basin(
            destination, project=self.project)
        self.assertTrue(any(item['name'] == green_name
                            for item in manifest['layers']))
        self.project.removeMapLayer(green.id())
        restored, _ = archive.open_complete_basin(
            destination, project=self.project)
        self.assertTrue(any(item['name'] == green_name
                            for item in restored['layers']))
        actual = [layer for layer in self.project.mapLayers().values()
                  if layer.customProperty(GREEN_CLOSURE_ROLE, False)]
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0].featureCount(), 1)
        self.assertEqual(actual[0].renderer().symbol().color().name(), '#23e733')
        point = next(actual[0].getFeatures()).geometry().asPoint()
        self.assertAlmostEqual(point.x(), 353387.14, 5)
        self.assertAlmostEqual(point.y(), 4725249.11, 5)

    def test_native_maximum_flow_section_is_recorded_without_open_basin_dialog(self):
        section = QgsVectorLayer('Point?crs=EPSG:25830',
                                 'Seccion de cierre Ej II Arroyo del Corco', 'memory')
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPointXY(
            QgsPointXY(349325.0, 4717000.0)))
        self.assertTrue(section.dataProvider().addFeatures([feature])[0])
        self.project.addMapLayer(section)
        points = archive._query_points(project=self.project)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['kind'], 'consulta_capas')
        self.assertEqual((points[0]['x'], points[0]['y']), (349325.0, 4717000.0))
        self.assertEqual(points[0]['crs'], 'EPSG:25830')

    def test_invalid_archive_traversal_is_rejected_without_writing_outside_storage(self):
        malicious = self.folder / 'malicious.hidrocuenca'
        with ZipFile(malicious, 'w', ZIP_DEFLATED) as bundle:
            bundle.writestr('../outside.txt', 'bad')
            bundle.writestr('manifest.json', json.dumps(
                {'schema':archive.SCHEMA, 'version':1, 'checksums':{}, 'layers':[]}))
        with self.assertRaisesRegex(ValueError, 'ruta'):
            archive.open_complete_basin(malicious, project=self.project)
        self.assertFalse((self.folder / 'outside.txt').exists())

    def test_archive_cannot_live_in_directory_cleaned_by_limpiar_capas(self):
        with self.assertRaisesRegex(ValueError, 'fuera de ResolucionMejorada'):
            archive.save_complete_basin(
                self.improved / 'archive.hidrocuenca', project=self.project)


if __name__ == '__main__':
    unittest.main()
