"""Portable real OGR networks and persistent QGIS TauDEM result layers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gc
import os
from pathlib import Path
import sys
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsProject,
                       QgsGeometry, QgsVectorLayer, QgsWkbTypes,
                       QgsSingleBandPseudoColorRenderer, QgsHillshadeRenderer)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta import drainage_layers as drainage


class NoCanvasIface:
    def mapCanvas(self):
        raise AssertionError('Drainage layers must not move the map or change the tool')


class DrainageLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hidrocaumax_drainage_')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.project = QgsProject.instance()
        self.initial_ids = set(self.project.mapLayers())
        self.initial_groups = list(self.project.layerTreeRoot().children())
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.analysis = SimpleNamespace(cell_width=25., cell_height=25.)
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.remove()
        for layer_id in set(self.project.mapLayers())-self.initial_ids:
            self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_groups:
                self.project.layerTreeRoot().removeChildNode(node)

    def controller(self, owner=None):
        controller = drainage.DrainageLayers(NoCanvasIface(), owner)
        self.controllers.append(controller)
        return controller

    def network(self, name='streams', wkt=None, epsg=25830):
        path = self.folder/(name+'.shp')
        dataset = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(path))
        reference = osr.SpatialReference();reference.ImportFromEPSG(epsg)
        geometry = ogr.CreateGeometryFromWkt(wkt or 'LINESTRING (500000 4700000,500025 4700025,500050 4700050)')
        layer = dataset.CreateLayer(name, reference, geometry.GetGeometryType())
        for field_name, kind, width, precision in (
                ('LINKNO', ogr.OFTInteger, 9, 0), ('BIG_ID', ogr.OFTInteger64, 18, 0),
                ('LENGTH', ogr.OFTReal, 16, 5), ('LABEL', ogr.OFTString, 40, 0)):
            field = ogr.FieldDefn(field_name, kind);field.SetWidth(width);field.SetPrecision(precision)
            layer.CreateField(field)
        for index in range(2):
            feature = ogr.Feature(layer.GetLayerDefn());feature.SetGeometry(geometry)
            feature.SetField('LINKNO', index+1);feature.SetField('BIG_ID', 4000000000+index)
            feature.SetField('LENGTH', 70.71068+index)
            if index == 0:feature.SetField('LABEL', 'curso principal')
            self.assertEqual(layer.CreateFeature(feature), 0)
        layer = dataset = None
        return path

    def snapshot(self, path=None):
        return drainage.read_network_snapshot(SimpleNamespace(network_path=str(path or self.network())))

    def directions(self, native=False):
        path = self.folder/('taudem_d8.tif' if native else 'caumax_d8.tif')
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 4, 2, 1, gdal.GDT_Byte)
        ds.SetGeoTransform((500000,25,0,4700050,0,-25));ds.SetProjection(self.crs.toWkt())
        ds.GetRasterBand(1).SetNoDataValue(0)
        values = np.arange(1,9,dtype=np.uint8) if native else np.array([1,2,4,8,16,32,64,128],dtype=np.uint8)
        ds.GetRasterBand(1).WriteArray(values.reshape(2,4));ds = None
        return path

    def elevation(self):
        path = self.folder / 'real_mdt_25m.tif'
        dataset = gdal.GetDriverByName('GTiff').Create(
            str(path), 4, 2, 1, gdal.GDT_Float32)
        dataset.SetGeoTransform((500000,25,0,4700050,0,-25))
        dataset.SetProjection(self.crs.toWkt())
        dataset.GetRasterBand(1).SetNoDataValue(-9999)
        dataset.GetRasterBand(1).WriteArray(
            np.array([[900, 930, 960, 1050],
                      [850, 910, 975, 1100]], dtype=np.float32))
        dataset = None
        return path

    def test_generated_mdt_is_third_layer_of_drainage_group_with_natural_relief(self):
        state = self.snapshot()
        state['caumax_d8_path'] = str(self.directions())
        state['dem_path'] = str(self.elevation())
        controller = self.controller()
        early = controller.display_dem(self.analysis, state['dem_path'])
        group = controller._group
        self.assertIs(early, controller.layers['dem'])
        self.assertEqual([item.layer().name() for item in group.children()],
                         ['MDT 25x25m', 'Relieve sombreado MDT 25x25m'])
        layers = controller.update(self.analysis, state)
        self.assertEqual(set(layers), {'network', 'directions', 'dem', 'hillshade'})
        names = [node.layer().name() for node in group.children()]
        self.assertEqual(names[2], 'MDT 25x25m')
        self.assertEqual(names[3], 'Relieve sombreado MDT 25x25m')
        self.assertIn('Dirección de Flujo D8', names[1])
        self.assertEqual(Path(layers['dem'].source()), Path(state['dem_path']))
        self.assertEqual(Path(layers['hillshade'].source()), Path(state['dem_path']))
        self.assertIsInstance(layers['dem'].renderer(), QgsSingleBandPseudoColorRenderer)
        self.assertIsInstance(layers['hillshade'].renderer(), QgsHillshadeRenderer)
        self.assertTrue(all(node.itemVisibilityChecked() for node in
                            group.children()[2:4]))
        self.assertAlmostEqual(layers['dem'].opacity(), 0.86)
        # Subsequent network updates keep manual layer visibility choices.
        group.children()[2].setItemVisibilityChecked(False)
        next_layers = controller.update(self.analysis, state)
        self.assertFalse(self.project.layerTreeRoot().findLayer(
            next_layers['dem'].id()).itemVisibilityChecked())
        self.assertEqual([node.layer().name() for node in group.children()][2],
                         'MDT 25x25m')

    def test_reader_is_pure_ogr_in_worker_and_preserves_fields_geometry_attributes(self):
        path = self.network()
        with patch.object(drainage, 'QgsVectorLayer', side_effect=AssertionError('QGIS in worker')):
            with ThreadPoolExecutor(max_workers=1) as executor:
                state = executor.submit(drainage.read_network_snapshot, SimpleNamespace(network_path=str(path))).result()
        self.assertEqual(set(state), {'network_crs_wkt','fields','features'})
        self.assertEqual(len(state['features']), 2)
        self.assertEqual([f['name'] for f in state['fields']], ['LINKNO','BIG_ID','LENGTH','LABEL'])
        self.assertEqual([f['type'] for f in state['fields']], ['int','int64','real','str'])
        self.assertEqual(state['features'][0]['attributes'], dict(
            LINKNO=1,BIG_ID=4000000000,LENGTH=70.71068,LABEL='curso principal'))
        self.assertIsNone(state['features'][1]['attributes']['LABEL'])
        self.assertEqual(QgsCoordinateReferenceSystem.fromWkt(state['network_crs_wkt']), self.crs)
        ds = ogr.Open(str(path));source = ds.GetLayer(0).GetNextFeature()
        self.assertEqual(state['features'][0]['wkb'], bytes(source.GetGeometryRef().ExportToWkb()))
        self.assertEqual(set(self.project.mapLayers()), self.initial_ids)

    def test_3d_streamnet_snapshot_and_factory_preserve_all_z_coordinates(self):
        state = self.snapshot(self.network(wkt='LINESTRING Z (500000 4700000 120,500025 4700025 135,500050 4700050 150)'))
        layer = drainage.build_network_layer(state)
        self.assertTrue(QgsWkbTypes.hasZ(layer.wkbType()))
        self.assertEqual(QgsWkbTypes.flatType(layer.wkbType()), QgsWkbTypes.LineString)
        feature = next(layer.getFeatures())
        self.assertEqual(bytes(feature.geometry().asWkb()), state['features'][0]['wkb'])
        self.assertEqual([p.z() for p in feature.geometry().vertices()], [120.,135.,150.])
        self.assertEqual(set(self.project.mapLayers()), self.initial_ids)

    def test_multiline_network_is_supported_and_styles_are_independent(self):
        geometry = QgsGeometry.fromWkt('MULTILINESTRING ((500000 4700000,500025 4700025),(500025 4700025,500050 4700050))')
        state = dict(network_crs_wkt=self.crs.toWkt(),features=[dict(wkb=bytes(geometry.asWkb()),attributes={})])
        layer = drainage.build_network_layer(state)
        self.assertEqual(layer.wkbType(), QgsWkbTypes.MultiLineString)
        self.assertEqual(next(layer.getFeatures()).geometry().length(), geometry.length())
        first, second = drainage.network_symbol(), drainage.network_symbol()
        self.assertEqual(first.symbolLayerCount(), 2)
        self.assertEqual(first.symbolLayer(0).color().name(), '#16446b')
        self.assertEqual(first.symbolLayer(1).color().name(), '#65c7ff')
        self.assertAlmostEqual(first.symbolLayer(0).width(), .8)
        self.assertAlmostEqual(first.symbolLayer(1).width(), .6)
        first.symbolLayer(1).setWidth(3)
        self.assertAlmostEqual(second.symbolLayer(1).width(), .6)

    def test_layer_fields_and_attributes_round_trip_without_project_registration(self):
        state = self.snapshot();layer = drainage.build_network_layer(state)
        self.assertTrue(layer.isValid());self.assertEqual(layer.featureCount(), 2)
        self.assertEqual(layer.fields().names(), [field['name'] for field in state['fields']])
        first = next(layer.getFeatures())
        for name, value in state['features'][0]['attributes'].items():self.assertEqual(first[name], value)
        self.assertEqual(layer.fields().field('LENGTH').precision(), 5)
        self.assertEqual(layer.fields().field('LABEL').length(), 40)
        self.assertEqual(set(self.project.mapLayers()), self.initial_ids)

    def test_zero_length_streamnet_links_are_kept_with_topology_attributes(self):
        state = self.snapshot(self.network(wkt='LINESTRING Z (500000 4700000 120,500000 4700000 120)'))
        layer = drainage.build_network_layer(state)
        self.assertEqual(layer.featureCount(),2)
        records = list(layer.getFeatures())
        self.assertEqual([record['LINKNO'] for record in records],[1,2])
        self.assertTrue(all(record.geometry().length()==0 for record in records))
        self.assertEqual(bytes(records[0].geometry().asWkb()),state['features'][0]['wkb'])
        self.assertTrue(QgsWkbTypes.hasZ(layer.wkbType()))

    def test_generated_mdt_is_third_inside_d8_group_with_natural_relief(self):
        path = self.folder / 'mdt_25.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 4, 4, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((500000,25,0,4700100,0,-25))
        ds.SetProjection(self.crs.toWkt())
        ds.GetRasterBand(1).WriteArray(np.array([
            [130., 132., 146., 160.],
            [125., 140., 150., 170.],
            [118., 128., 145., 185.],
            [110., 120., 150., 190.],
        ], dtype=np.float32))
        ds = None
        state = self.snapshot()
        state['caumax_d8_path'] = str(self.directions())
        state['dem_path'] = str(path)
        controller = self.controller()
        layers = controller.update(self.analysis, state)
        self.assertEqual(set(layers), {'network', 'directions', 'dem', 'hillshade'})
        group = self.project.layerTreeRoot().findLayer(layers['dem'].id()).parent()
        ordered = [node.layer().id() for node in group.children()]
        self.assertEqual(ordered, [layers[k].id()
            for k in ('network', 'directions', 'dem', 'hillshade')])
        self.assertEqual(layers['dem'].name(), 'MDT 25x25m')
        self.assertEqual(Path(layers['dem'].source()), path)
        self.assertEqual(Path(layers['hillshade'].source()), path)
        self.assertTrue(group.children()[2].itemVisibilityChecked())
        self.assertTrue(group.children()[3].itemVisibilityChecked())
        self.assertEqual(layers['dem'].renderer().type(), 'singlebandpseudocolor')
        self.assertEqual(layers['hillshade'].renderer().type(), 'hillshade')
        # The user's legend visibility choices survive a second D8 refresh.
        group.children()[2].setItemVisibilityChecked(False)
        after = controller.update(self.analysis, state)
        self.assertFalse(self.project.layerTreeRoot()
            .findLayer(after['dem'].id()).itemVisibilityChecked())
        self.assertEqual(after['dem'].name(), 'MDT 25x25m')

    def test_real_layers_network_visible_and_categorical_caumax_d8_hidden(self):
        state = self.snapshot()
        state['tau_d8_path'] = str(self.directions(native=True))
        state['caumax_d8_path'] = str(self.directions())
        state['flow_encoding'] = 'taudem'  # A legacy choice must not change the fixed display format.
        controller = self.controller();layers = controller.update(self.analysis, state)
        self.assertEqual(set(layers), {'network','directions'})
        self.assertEqual(controller.visibility(), {'network':True,'directions':False})
        self.assertEqual(layers['directions'].renderer().type(), 'paletted')
        classes = layers['directions'].renderer().classes()
        self.assertEqual([c.value for c in classes], [1,2,4,8,16,32,64,128])
        self.assertEqual([c.label for c in classes], ['E','SE','S','SW','W','NW','N','NE'])
        self.assertEqual(Path(layers['directions'].source()), Path(state['caumax_d8_path']))
        self.assertIn('CAUMAX/ESRI', layers['directions'].name())
        self.assertEqual({layer.id() for layer in layers.values()}, set(self.project.mapLayers())-self.initial_ids)
        self.assertIn('25×25 m', self.project.layerTreeRoot().findLayer(layers['network'].id()).parent().name())

    def test_offline_network_survives_missing_caumax_raster_without_native_fallback(self):
        state = self.snapshot()
        state['caumax_d8_path'] = '/missing/offline.tif'
        state['tau_d8_path'] = str(self.directions(native=True))
        controller = self.controller();layers = controller.update(self.analysis, state, visible=False)
        self.assertIsNone(layers['directions']);self.assertEqual(layers['network'].featureCount(), 2)
        self.assertEqual(controller.visibility(), {'network':False,'directions':False})
        self.assertIn('red archivada', controller.note)
        tree = self.project.layerTreeRoot()
        official = tree.insertGroup(0, 'CAUMAX_CAPAS')
        group = controller._group
        group.setItemVisibilityChecked(False)
        state['network_visible'] = True;state['directions_visible'] = True
        controller.update(self.analysis, state, visible=False)
        self.assertEqual(controller.visibility(), {'network':True,'directions':False})
        self.assertIs(controller._group, group)
        self.assertFalse(group.itemVisibilityChecked())
        self.assertLess(tree.children().index(group), tree.children().index(official))

    def test_updates_clear_only_owned_layers_and_preserve_other_consultations(self):
        state = self.snapshot();first = self.controller('one');second = self.controller('two')
        initial = first.update(self.analysis, state);first_id = initial['network'].id()
        other = second.update(self.analysis, state)['network']
        user = QgsVectorLayer('LineString?crs=EPSG:25830','User layer','memory')
        self.project.addMapLayer(user)
        changed = SimpleNamespace(cell_width=5.,cell_height=5.)
        updated = first.update(changed, state)
        self.assertIsNone(self.project.mapLayer(first_id))
        self.assertEqual(len(set(self.project.mapLayers())-self.initial_ids), 3)
        self.assertIn('5×5 m', self.project.layerTreeRoot().findLayer(updated['network'].id()).parent().name())
        self.assertEqual(first.update(changed,None), {})
        self.assertIs(self.project.mapLayer(other.id()), other)
        self.assertIs(self.project.mapLayer(user.id()), user)
        self.assertEqual(first.layers, {})

    def test_drainage_group_is_below_courses_above_terrain_and_persists_on_close(self):
        tree = self.project.layerTreeRoot()
        terrain = tree.insertGroup(0,'Terrain');course = tree.insertGroup(0,'Course')
        course.setCustomProperty('HidroCAUMAX/course_owner','course')
        controller = drainage.DrainageLayers(NoCanvasIface())
        layer = controller.update(self.analysis,self.snapshot())['network'];layer_id = layer.id()
        group = tree.findLayer(layer_id).parent();nodes = tree.children()
        self.assertLess(nodes.index(course), nodes.index(group));self.assertLess(nodes.index(group), nodes.index(terrain))
        del controller;gc.collect()
        self.assertIs(self.project.mapLayer(layer_id), layer)

    def test_failed_update_keeps_previous_owned_network(self):
        controller = self.controller();old = controller.update(self.analysis,self.snapshot())['network']
        with self.assertRaises(ValueError):controller.update(self.analysis,dict(network_crs_wkt='',features=[]))
        self.assertIs(controller.layers['network'],old)
        self.assertIs(self.project.mapLayer(old.id()),old)

    def test_reader_rejects_nonmetric_crs_and_density_before_returning_snapshot(self):
        with self.assertRaisesRegex(ValueError,'métricas'):
            self.snapshot(self.network('degrees',epsg=4326))
        path = self.network('dense')
        for limit in ('MAX_NETWORK_WKB_BYTES','MAX_NETWORK_VERTICES','MAX_ATTRIBUTE_ITEMS'):
            with patch.object(drainage,limit,1):
                with self.assertRaisesRegex(ValueError,'demasiado densa'):self.snapshot(path)
        with self.assertRaises(ValueError):drainage.read_network_snapshot(self.folder/'absent.shp')

    def test_factory_rejects_invalid_geometry_attributes_and_schema(self):
        state = self.snapshot()
        for mutate in (
                lambda s:s['features'][0].update(wkb=b'invalid'),
                lambda s:s['features'][0]['attributes'].update(LENGTH=float('nan')),
                lambda s:s['features'][0]['attributes'].update(LABEL=['not scalar']),
                lambda s:s['fields'].append(dict(s['fields'][0])),
                lambda s:s['fields'][0].update(type='unknown'),
                lambda s:s['fields'][0].update(width=-1)):
            invalid = deepcopy(state);mutate(invalid)
            with self.assertRaises(ValueError):drainage.build_network_layer(invalid)
        z = QgsGeometry.fromWkt('LINESTRING Z (1 2 3,4 5 6)')
        nonfinite = bytes(z.asWkb())[:-8]+struct.pack('<d',float('nan'))
        with self.assertRaisesRegex(ValueError,'coordenadas no finitas'):
            drainage.build_network_layer(dict(network_crs_wkt=self.crs.toWkt(),
                features=[dict(wkb=nonfinite,attributes={})]))
        with patch.object(drainage,'MAX_NETWORK_WKB_BYTES',1):
            with self.assertRaises(ValueError):drainage.build_network_layer(state)


if __name__ == '__main__':
    unittest.main()
