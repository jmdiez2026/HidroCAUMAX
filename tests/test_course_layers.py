"""Real project layers for the untouched course and fractional chainages."""

import gc
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject,
    QgsVectorLayer, QgsWkbTypes,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.course_layers import (
    CourseLayers, course_symbol, deduplicate_course_groups, _OWNER_PROPERTY)


class NoCanvasIface:
    def mapCanvas(self):
        raise AssertionError('CourseLayers must not change the map view or tool')


class CourseLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject.instance()
        self.initial_ids = set(self.project.mapLayers())
        self.initial_groups = list(self.project.layerTreeRoot().children())
        self.initial_crs = self.project.crs()
        self.initial_ellipsoid = self.project.ellipsoid()
        geometry = QgsGeometry.fromWkt(
            'LINESTRING (500000 4700000,500030 4700040,500150 4700040,500150 4700120)')
        self.analysis = SimpleNamespace(
            main_course_wkb=bytes(geometry.asWkb()),
            crs_wkt=QgsCoordinateReferenceSystem('EPSG:25830').toWkt(),
            cell_width=10., cell_height=20., outlet_x=500000., outlet_y=4700000.,
            cell_count=20,
            values={'main_length_km': .25, 'main_length_10_85_km': .1875},
        )
        self.query = SimpleNamespace(period=100,flow=12.5,cell=SimpleNamespace(row=4,col=8))
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.remove()
        for layer_id in set(self.project.mapLayers()) - self.initial_ids:
            self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_groups:
                self.project.layerTreeRoot().removeChildNode(node)
        self.project.setCrs(self.initial_crs)
        self.project.setEllipsoid(self.initial_ellipsoid)

    def controller(self, owner_key=None):
        controller = CourseLayers(NoCanvasIface(),owner_key)
        self.controllers.append(controller)
        return controller

    def feature(self,layer):
        self.assertEqual(layer.featureCount(),1)
        return next(layer.getFeatures())

    def test_real_layers_have_exact_whole_and_interpolated_central_lengths(self):
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        self.assertEqual(set(layers),{'main','central'})
        self.assertEqual(set(self.project.mapLayers()) - self.initial_ids,
                         {layer.id() for layer in layers.values()})
        main = self.feature(layers['main']).geometry()
        central = self.feature(layers['central']).geometry()
        self.assertEqual(bytes(main.asWkb()),self.analysis.main_course_wkb)
        self.assertEqual(main.length(),250.)
        self.assertEqual(central.length(),187.5)
        points = central.asPolyline()
        self.assertEqual([(p.x(),p.y()) for p in points],[
            (500015.,4700020.),(500030.,4700040.),
            (500150.,4700040.),(500150.,4700082.5),
        ])
        self.assertEqual((main.asPolyline()[0].x(),main.asPolyline()[0].y()),
                         (self.analysis.outlet_x,self.analysis.outlet_y))
        self.assertEqual(controller.note,'')

    def test_initial_visibility_and_distinct_styles_are_real_tree_properties(self):
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        self.assertEqual(controller.visibility(),{'main':True,'central':False})
        for kind,expected in (('main',True),('central',False)):
            node = self.project.layerTreeRoot().findLayer(layers[kind].id())
            self.assertEqual(node.itemVisibilityChecked(),expected)
            self.assertEqual(node.isVisible(),expected)
        main_style = layers['main'].renderer().symbol().symbolLayer(0).properties()
        central_style = layers['central'].renderer().symbol().symbolLayer(0).properties()
        self.assertEqual(layers['main'].renderer().symbol().symbolLayerCount(),2)
        self.assertEqual(layers['central'].renderer().symbol().symbolLayerCount(),2)
        self.assertEqual(main_style['line_style'],'solid')
        self.assertEqual(central_style['line_style'],'dash')
        for kind in ('main','central'):
            symbol=layers[kind].renderer().symbol()
            self.assertEqual(symbol.symbolLayer(0).color().name(),'#16446b')
            self.assertEqual(symbol.symbolLayer(1).color().name(),'#65c7ff' if kind=='main' else '#a6e7ff')
            self.assertAlmostEqual(symbol.symbolLayer(0).width(),1.5)
            self.assertAlmostEqual(symbol.symbolLayer(1).width(),1.1)
        name = self.project.layerTreeRoot().findLayer(layers['main'].id()).parent().name()
        self.assertIn('HidroCAUMAX · Curso principal',name)
        self.assertIn('500000',name)
        self.assertIn('celda Q 5,9',name)
        self.assertIn('10×20 m',name)

    def test_course_group_is_created_above_existing_terrain_and_symbols_are_independent(self):
        terrain=self.project.layerTreeRoot().insertGroup(0,'HidroCAUMAX · Terreno')
        layers=self.controller().update(self.analysis,self.query)
        group=self.project.layerTreeRoot().findLayer(layers['main'].id()).parent()
        children=self.project.layerTreeRoot().children()
        self.assertLess(children.index(group),children.index(terrain))
        first,second=course_symbol('main'),course_symbol('main')
        self.assertIsNot(first,second)
        first.symbolLayer(1).setWidth(3)
        self.assertAlmostEqual(second.symbolLayer(1).width(),1.1)
        with self.assertRaises(ValueError):course_symbol('unknown')

    def test_attributes_and_crs_describe_original_analysis_without_mutation(self):
        before = bytes(self.analysis.main_course_wkb)
        values = dict(self.analysis.values)
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        for kind,length in (('main',.25),('central',.1875)):
            feature = self.feature(layers[kind])
            self.assertEqual(feature['L_KM'],length)
            self.assertEqual((feature['RES_X_M'],feature['RES_Y_M']),(10.,20.))
            self.assertEqual((feature['X_CIERRE'],feature['Y_CIERRE']),(500000.,4700000.))
            self.assertEqual((feature['T_ANOS'],feature['Q_M3S']),(100.,12.5))
            self.assertEqual(feature['TIPO'],'total' if kind=='main' else 'tramo_10_85')
            self.assertEqual(layers[kind].crs(),QgsCoordinateReferenceSystem('EPSG:25830'))
            self.assertEqual(layers[kind].wkbType(),QgsWkbTypes.LineString)
        self.assertEqual(self.analysis.main_course_wkb,before)
        self.assertEqual(self.analysis.values,values)
        feature = self.feature(layers['main'])
        changed = feature.geometry()
        changed.translate(100,200)
        self.assertEqual(self.analysis.main_course_wkb,before)
        self.assertEqual(bytes(self.feature(layers['main']).geometry().asWkb()),before)

    def test_updates_retain_ids_and_user_checks_while_changing_resolution_and_course(self):
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        ids = {kind:layer.id() for kind,layer in layers.items()}
        tree = self.project.layerTreeRoot()
        official = tree.insertGroup(0, 'CAUMAX_CAPAS')
        tree.findLayer(ids['main']).setItemVisibilityChecked(False)
        tree.findLayer(ids['central']).setItemVisibilityChecked(True)
        geometry = QgsGeometry.fromWkt('LINESTRING (500010 4700010,500010 4700110)')
        updated = SimpleNamespace(**vars(self.analysis))
        updated.main_course_wkb = bytes(geometry.asWkb())
        updated.outlet_x,updated.outlet_y = 500010.,4700010.
        updated.cell_width,updated.cell_height = 25.,50.
        updated.values = {'main_length_km':.1,'main_length_10_85_km':.075}
        for degree in (.5,10.5,45.):
            updated.smoothing_degree = degree
            current = controller.update(updated,self.query)
            self.assertEqual({kind:layer.id() for kind,layer in current.items()},ids)
            self.assertEqual(controller.visibility(),{'main':False,'central':True})
            self.assertLess(tree.children().index(controller._group), tree.children().index(official))
            self.assertEqual(self.feature(current['main'])['L_KM'],.1)
            self.assertEqual(self.feature(current['central'])['L_KM'],.075)
            self.assertEqual(self.feature(current['main'])['RES_Y_M'],50.)
        self.assertEqual(set(self.project.mapLayers())-self.initial_ids,set(ids.values()))

    def test_explicit_visibility_overrides_layer_flags_without_reenabling_hidden_group(self):
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        group = self.project.layerTreeRoot().findLayer(layers['main'].id()).parent()
        group.setItemVisibilityChecked(False)
        flags = {'main':False,'central':True}
        controller.update(self.analysis,self.query,flags)
        self.assertEqual(controller.visibility(),flags)
        self.assertFalse(group.itemVisibilityChecked())
        self.assertEqual(flags,{'main':False,'central':True})

    def test_second_course_generation_retires_first_completely_and_keeps_user_layers(self):
        unrelated = QgsVectorLayer('LineString?crs=EPSG:25830', 'User layer', 'memory')
        self.project.addMapLayer(unrelated)
        first, second = self.controller(), self.controller()
        previous = first.update(self.analysis, self.query)
        tree = self.project.layerTreeRoot()
        old_group = tree.findLayer(previous['main'].id()).parent()
        old_ids = {layer.id() for layer in previous.values()}
        new = second.update(self.analysis, self.query)
        newest_group = tree.findLayer(new['main'].id()).parent()
        self.assertNotEqual(first.owner_key, second.owner_key)
        self.assertTrue(sip.isdeleted(old_group))
        self.assertEqual(first.layers, {})
        self.assertIsNone(first._group)
        self.assertTrue(all(self.project.mapLayer(item) is None for item in old_ids))
        self.assertEqual(
            [g for g in tree.findGroups(recursive=True) if g.customProperty(_OWNER_PROPERTY, '')],
            [newest_group])
        self.assertTrue(all(self.project.mapLayer(l.id()) is l for l in new.values()))
        self.assertIs(self.project.mapLayer(unrelated.id()), unrelated)
        # Disposing the old dialog after eviction must not remove newest work.
        first.remove()
        self.assertTrue(all(self.project.mapLayer(l.id()) is l for l in new.values()))
        self.assertEqual(len([g for g in tree.findGroups(recursive=True)
                              if g.customProperty(_OWNER_PROPERTY, '')]), 1)

    def test_retired_group_keeps_foreign_child_but_not_its_container(self):
        first, second = self.controller(), self.controller()
        old = first.update(self.analysis, self.query)
        root = self.project.layerTreeRoot()
        old_group = root.findLayer(old['main'].id()).parent()
        user = QgsVectorLayer('Point?crs=EPSG:25830', 'Independent user points', 'memory')
        self.project.addMapLayer(user, False)
        old_group.addLayer(user)
        second.update(self.analysis, self.query)
        self.assertTrue(sip.isdeleted(old_group))
        self.assertIs(self.project.mapLayer(user.id()), user)
        self.assertIsNotNone(root.findLayer(user.id()))
        self.assertIs(root.findLayer(user.id()).parent(), root)

    def test_reopening_legacy_project_retains_lowest_newest_course_group(self):
        from caumax_consulta.course_layers import _NAMES
        project = self.project
        root = project.layerTreeRoot()
        old, recent = [], []
        for container in (old, recent):
            group = root.addGroup('HidroCAUMAX · Curso principal [legacy]')
            group.setCustomProperty(_OWNER_PROPERTY, 'old' if container is old else 'new')
            layer = QgsVectorLayer('LineString?crs=EPSG:25830', _NAMES['main'], 'memory')
            layer.setCustomProperty(_OWNER_PROPERTY, group.customProperty(_OWNER_PROPERTY))
            project.addMapLayer(layer, False)
            group.addLayer(layer)
            container.extend((group, layer))
        self.assertEqual(deduplicate_course_groups(project), 1)
        self.assertTrue(sip.isdeleted(old[0]))
        self.assertIsNone(project.mapLayer(old[1].id()) if not sip.isdeleted(old[1]) else None)
        self.assertIsNotNone(project.mapLayer(recent[1].id()))
        self.assertFalse(sip.isdeleted(recent[0]))

    def test_deletes_only_private_course_disk_files_not_shared_dem_or_user_files(self):
        from osgeo import ogr, osr
        import caumax_consulta.course_layers as module
        first, second = self.controller(), self.controller()
        with TemporaryDirectory() as folder:
            private = Path(folder) / 'HidroCAUMAX' / 'courses'
            private.mkdir(parents=True)
            file = private / 'outdated.gpkg'
            ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(file))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(25830)
            ds.CreateLayer('course', srs, ogr.wkbLineString)
            ds = None
            old = QgsVectorLayer(str(file), 'Old course', 'ogr')
            self.assertTrue(old.isValid())
            old.setCustomProperty(_OWNER_PROPERTY, first.owner_key)
            self.project.addMapLayer(old, False)
            old_group = self.project.layerTreeRoot().addGroup('HidroCAUMAX · Curso principal antiguo')
            old_group.setCustomProperty(_OWNER_PROPERTY, first.owner_key)
            old_group.addLayer(old)
            external = Path(folder) / 'user-owned.tif'
            external.write_bytes(b'Protected user DEM')
            with patch.object(module, 'QgsApplication',
                              SimpleNamespace(qgisSettingsDirPath=lambda: folder)):
                second.update(self.analysis, self.query)
            self.assertFalse(file.exists())
            self.assertTrue(external.exists())
            self.assertTrue(sip.isdeleted(old_group))
            self.assertEqual(len([g for g in self.project.layerTreeRoot().findGroups(recursive=True)
                                  if g.customProperty(_OWNER_PROPERTY, '')]), 1)

    def test_result_layers_survive_controller_or_dialog_lifetime(self):
        controller = CourseLayers(NoCanvasIface())
        layers = controller.update(self.analysis,self.query)
        ids = {layer.id() for layer in layers.values()}
        del controller
        gc.collect()
        self.assertTrue(ids.issubset(self.project.mapLayers()))
        for layer in layers.values():
            self.assertEqual(layer.featureCount(),1)

    def test_newer_rational_course_replaces_maxflow_course_in_both_directions(self):
        from caumax_consulta.course_layers import _OWNER_PROPERTY
        first, second = self.controller(), self.controller()
        previous = first.update(self.analysis, self.query,
                                {'main': False, 'central': True})
        previous_ids = {layer.id() for layer in previous.values()}
        tree = self.project.layerTreeRoot()
        unrelated = tree.insertGroup(0, 'Relieve del usuario')
        unrelated.setCustomProperty('HidroCAUMAX/terrain_owner', 'terrain')
        recent = second.update(self.analysis, self.query)
        recent_ids = {layer.id() for layer in recent.values()}
        self.assertFalse(any(self.project.mapLayer(i) for i in previous_ids))
        self.assertTrue(all(self.project.mapLayer(i) is not None for i in recent_ids))
        self.assertTrue(unrelated.itemVisibilityChecked())
        self.assertEqual(len([g for g in tree.findGroups(recursive=True)
                              if g.customProperty(_OWNER_PROPERTY, '')]), 1)
        first.update(self.analysis, self.query)
        self.assertFalse(any(self.project.mapLayer(i) for i in recent_ids))
        self.assertEqual(len([g for g in tree.findGroups(recursive=True)
                              if g.customProperty(_OWNER_PROPERTY, '')]), 1)

    def test_single_cell_has_empty_layers_and_can_update_in_place_both_directions(self):
        controller = self.controller()
        layers = controller.update(self.analysis,self.query)
        ids = {kind:layer.id() for kind,layer in layers.items()}
        empty = SimpleNamespace(**vars(self.analysis))
        empty.main_course_wkb = bytes(QgsGeometry.fromWkt('LINESTRING EMPTY').asWkb())
        empty.cell_count = 1
        empty.values = {'main_length_km':0.,'main_length_10_85_km':None}
        for wkb in (empty.main_course_wkb,b''):
            empty.main_course_wkb = wkb
            result = controller.update(empty,self.query)
            self.assertTrue(all(layer.featureCount()==0 for layer in result.values()))
            self.assertEqual({kind:layer.id() for kind,layer in result.items()},ids)
            self.assertIn('vacías',controller.note)
        result = controller.update(self.analysis,self.query)
        self.assertTrue(all(layer.featureCount()==1 for layer in result.values()))
        self.assertEqual({kind:layer.id() for kind,layer in result.items()},ids)

    def test_duplicate_vertices_and_3d_input_use_planar_lengths_without_geodesic_changes(self):
        self.project.setCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        self.project.setEllipsoid('WGS84')
        updated = SimpleNamespace(**vars(self.analysis))
        geometry = QgsGeometry.fromWkt(
            'LINESTRING Z (500000 4700000 0,500000 4700000 1000,'
            '500030 4700040 5000,500150 4700040 9000,500150 4700120 12000)')
        updated.main_course_wkb = bytes(geometry.asWkb())
        original = bytes(updated.main_course_wkb)
        layers = self.controller().update(updated,self.query)
        self.assertEqual(self.feature(layers['main']).geometry().length(),250.)
        self.assertEqual(self.feature(layers['central']).geometry().length(),187.5)
        self.assertFalse(QgsWkbTypes.hasZ(layers['main'].wkbType()))
        self.assertEqual(updated.main_course_wkb,original)
        self.assertEqual(self.project.crs(),QgsCoordinateReferenceSystem('EPSG:4326'))
        self.assertEqual(self.project.ellipsoid(),'WGS84')

    def test_invalid_crs_geometry_length_or_origin_does_not_add_project_results(self):
        cases = [dict(crs_wkt='invalid'),dict(main_course_wkb=b'not wkb'),
                 dict(main_course_wkb=bytes(QgsGeometry.fromWkt('POINT (0 0)').asWkb())),
                 dict(values={'main_length_km':.5}),dict(outlet_x=500001.)]
        for changes in cases:
            with self.subTest(changes=changes):
                bad = SimpleNamespace(**dict(vars(self.analysis),**changes))
                with self.assertRaises(ValueError):
                    self.controller().update(bad,self.query)
                self.assertEqual(set(self.project.mapLayers()),self.initial_ids)
                self.assertEqual(list(self.project.layerTreeRoot().children()),self.initial_groups)


if __name__ == '__main__':
    unittest.main()
