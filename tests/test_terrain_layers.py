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
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsRectangle, QgsVectorLayer)
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

    def basin_analysis(self, bounds, resolution=25):
        geometry = QgsGeometry.fromRect(QgsRectangle(*bounds))
        return SimpleNamespace(z_min=100., z_max=200., cell_width=float(resolution),
                               cell_height=float(resolution), geometry_wkb=bytes(geometry.asWkb()),
                               crs_wkt=QgsCoordinateReferenceSystem('EPSG:25830').toWkt())

    def assert_context(self, layers, bounds, analysis):
        for layer in layers:
            self.assertEqual(layer.customProperty('hidrocaumax/dem_basin_bounds'), list(bounds))
            self.assertEqual(layer.customProperty('hidrocaumax/dem_basin_crs'), analysis.crs_wkt)

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
        self.assertFalse(obj._group.itemVisibilityChecked())
        self.assertTrue(all(not root.findLayer(layer.id()).itemVisibilityChecked()
                            for layer in layers))
        obj._group.setItemVisibilityChecked(True)
        node.setItemVisibilityChecked(True)
        self.assertEqual([x.id() for x in obj.update(self.analysis,self.query)],ids)
        self.assertTrue(node.isVisible(), 'Reusing a layer must preserve an explicit user choice.')
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

    def test_reused_layers_receive_changed_basin_even_with_same_elevation_range(self):
        obj = self.controller()
        first_bounds = (330000., 4729500., 330250., 4729750.)
        first = self.basin_analysis(first_bounds)
        layers = obj.update(first, self.query)
        self.assert_context(layers, first_bounds, first)
        ids = [layer.id() for layer in layers]
        changes = {layer.id(): [] for layer in layers}
        for layer in layers:
            layer.customPropertyChanged.connect(
                lambda key, events=changes[layer.id()]: events.append(key))
        second_bounds = (330050., 4729600., 330400., 4729900.)
        second = self.basin_analysis(second_bounds)
        self.assertEqual([layer.id() for layer in obj.update(second, self.query)], ids)
        self.assert_context(layers, second_bounds, second)
        for events in changes.values():
            self.assertEqual(events, ['hidrocaumax/dem_basin_bounds'])
        obj.update(second, self.query)
        for events in changes.values():
            self.assertEqual(events, ['hidrocaumax/dem_basin_bounds'])

    def test_retained_visible_background_tracks_active_basin_at_other_resolution(self):
        obj = self.controller()
        obj.retain_variants = True
        first_bounds = (330000., 4729500., 330250., 4729750.)
        layers25 = obj.update(self.basin_analysis(first_bounds), self.query)
        root = self.project.layerTreeRoot()
        self.assertFalse(obj._group.itemVisibilityChecked())
        self.assertTrue(all(not root.findLayer(layer.id()).itemVisibilityChecked()
                            for layer in layers25))
        obj._group.setItemVisibilityChecked(True)
        for layer in layers25:
            root.findLayer(layer.id()).setItemVisibilityChecked(True)
        path500 = self.dem('mdt500.tif', 500)
        query500 = SimpleNamespace(dem_path=str(path500))
        second_bounds = (330000., 4729000., 331000., 4730000.)
        second = self.basin_analysis(second_bounds, 500)
        layers500 = obj.update(second, query500)
        self.assert_context(layers25+layers500, second_bounds, second)
        self.assertTrue(all(root.findLayer(layer.id()).isVisible() for layer in layers25))
        self.assertTrue(all(not root.findLayer(layer.id()).isVisible() for layer in layers500))
        final_bounds = (330025., 4729525., 330450., 4729975.)
        final = self.basin_analysis(final_bounds)
        self.assertEqual([layer.id() for layer in obj.update(final, self.query)],
                         [layer.id() for layer in layers25])
        self.assert_context(layers25+layers500, final_bounds, final)
        self.assertTrue(all(root.findLayer(layer.id()).isVisible() for layer in layers25))
        self.assertTrue(all(not root.findLayer(layer.id()).isVisible() for layer in layers500))

    def test_legacy_layers_gain_context_on_reuse_and_missing_geometry_is_not_invented(self):
        obj = self.controller()
        layers = obj.update(self.analysis, self.query)
        for layer in layers:
            self.assertNotIn('hidrocaumax/dem_basin_bounds', layer.customPropertyKeys())
        bounds = (330000., 4729500., 330500., 4730000.)
        analysis = self.basin_analysis(bounds)
        self.assertEqual(obj.update(analysis, self.query), layers)
        self.assert_context(layers, bounds, analysis)

    def test_invalid_basin_geometry_or_crs_does_not_set_context(self):
        for geometry, crs in (
                (QgsGeometry.fromWkt('POINT (330000 4730000)'), 'EPSG:25830'),
                (QgsGeometry.fromWkt('POLYGON ((0 0, 1 1, 1 0, 0 1, 0 0))'), 'EPSG:25830'),
                (QgsGeometry.fromRect(QgsRectangle(330000, 4729500, 330500, 4730000)), '')):
            with self.subTest(crs=crs, geometry=geometry.asWkt()):
                obj = self.controller()
                analysis = SimpleNamespace(**vars(self.analysis),
                    geometry_wkb=bytes(geometry.asWkb()),
                    crs_wkt=QgsCoordinateReferenceSystem(crs).toWkt())
                layers = obj.update(analysis, self.query)
                for layer in layers:
                    self.assertNotIn('hidrocaumax/dem_basin_bounds', layer.customPropertyKeys())

    def test_basin_context_survives_clone_and_qgis_project_reopening(self):
        obj = self.controller()
        bounds = (330000., 4729500., 330500., 4730000.)
        analysis = self.basin_analysis(bounds)
        layers = obj.update(analysis, self.query)
        project, restored = QgsProject(), QgsProject()
        try:
            clones = [layer.clone() for layer in layers]
            for layer in clones:
                project.addMapLayer(layer)
            self.assert_context(clones, bounds, analysis)
            destination = str(Path(self.temp.name)/'terrain_context.qgz')
            self.assertTrue(project.write(destination))
            self.assertTrue(restored.read(destination))
            self.assertEqual(len(restored.mapLayers()), 2)
            self.assert_context(list(restored.mapLayers().values()), bounds, analysis)
        finally:
            restored.clear()
            project.clear()
