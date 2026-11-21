"""Lifecycle and ownership of the active native terrain background."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta.terrain_layers import TerrainLayers


class NoCanvas:
    def mapCanvas(self):
        raise AssertionError('Terrain lifecycle must not modify the map extent or tool')


class TerrainLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject.instance()
        self.initial_ids = set(self.project.mapLayers())
        self.initial_groups = list(self.project.layerTreeRoot().children())
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.dem('mdt25.tif', 25)
        self.analysis = SimpleNamespace(z_min=100.,z_max=200.,cell_width=25.,cell_height=25.)
        self.query = SimpleNamespace(dem_path=str(self.path))
        self.controllers = []

    def dem(self, name, resolution):
        path = Path(self.temp.name)/name
        ds = gdal.GetDriverByName('GTiff').Create(str(path),20,20,1,gdal.GDT_Float32)
        ref = osr.SpatialReference(); ref.ImportFromEPSG(25830)
        ds.SetProjection(ref.ExportToWkt())
        ds.SetGeoTransform((330000,resolution,0,4730000,0,-resolution))
        ds.GetRasterBand(1).WriteArray(np.linspace(100,200,400,dtype=np.float32).reshape(20,20))
        ds = None
        return path

    def controller(self):
        obj = TerrainLayers(NoCanvas())
        self.controllers.append(obj)
        return obj

    def tearDown(self):
        for obj in self.controllers: obj.remove()
        for layer_id in set(self.project.mapLayers())-self.initial_ids:
            self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_groups:
                self.project.layerTreeRoot().removeChildNode(node)
        self.temp.cleanup()

    def test_real_background_is_owned_and_repeated_update_keeps_user_visibility(self):
        obj = self.controller()
        layers = obj.update(self.analysis,self.query)
        self.assertEqual(len(layers),2)
        ids = [x.id() for x in layers]
        node = self.project.layerTreeRoot().findLayer(ids[0])
        root = self.project.layerTreeRoot()
        official = root.insertGroup(0, 'CAUMAX_CAPAS')
        node.setItemVisibilityChecked(False)
        self.assertEqual([x.id() for x in obj.update(self.analysis,self.query)],ids)
        self.assertFalse(node.itemVisibilityChecked())
        self.assertLess(root.children().index(obj._group), root.children().index(official))
        self.assertEqual(set(self.project.mapLayers())-self.initial_ids,set(ids))
        self.assertTrue(all(Path(x.source()).resolve()==self.path.resolve() for x in layers))

    def test_switching_resolution_replaces_only_this_owned_background(self):
        obj = self.controller()
        ids = {x.id() for x in obj.update(self.analysis,self.query)}
        foreign = QgsVectorLayer('Point?crs=EPSG:25830','unrelated','memory')
        self.project.addMapLayer(foreign)
        new_path = self.dem('mdt5.tif',5)
        changed = obj.update(SimpleNamespace(z_min=100.,z_max=200.,cell_width=5.,cell_height=5.),
                             SimpleNamespace(dem_path=str(new_path)))
        self.assertTrue(all(self.project.mapLayer(i) is None for i in ids))
        self.assertIs(self.project.mapLayer(foreign.id()),foreign)
        self.assertTrue(all(Path(x.source()).resolve()==new_path.resolve() for x in changed))
        self.assertIn('5 × 5 m', obj._group.name())

    def test_new_terrain_precedes_official_with_custom_drawing_order_enabled(self):
        root = self.project.layerTreeRoot()
        official = root.addGroup('CAUMAX_CAPAS')
        background = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Official background', 'memory')
        self.project.addMapLayer(background, False)
        official.addLayer(background)
        root.setCustomLayerOrder([background])
        root.setHasCustomLayerOrder(True)
        self.addCleanup(root.setHasCustomLayerOrder, False)
        obj = self.controller()
        layers = obj.update(self.analysis, self.query)
        drawing = root.customLayerOrder()
        self.assertTrue(all(drawing.index(layer) < drawing.index(background) for layer in layers))

    def test_missing_dem_keeps_offline_results_without_stale_terrain(self):
        obj = self.controller()
        ids = [x.id() for x in obj.update(self.analysis,self.query)]
        result = obj.update(self.analysis,SimpleNamespace(dem_path=str(Path(self.temp.name)/'missing.tif')))
        self.assertEqual(result,[])
        self.assertTrue(all(self.project.mapLayer(i) is None for i in ids))
        self.assertIn('captura',obj.note)

    def test_background_is_inserted_below_course_groups_and_explicit_removal_is_scoped(self):
        root = self.project.layerTreeRoot()
        courses = root.insertGroup(0,'courses')
        courses.setCustomProperty('HidroCAUMAX/course_owner','another-consultation')
        obj = self.controller()
        obj.update(self.analysis,self.query)
        self.assertLess(root.children().index(courses),root.children().index(obj._group))
        obj.remove()
        self.assertIn(courses,root.children())
        self.assertEqual(set(self.project.mapLayers()),self.initial_ids)
