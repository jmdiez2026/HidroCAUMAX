"""One active course/outline across resolution changes and fresh dialogs."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal
from qgis.core import QgsGeometry

import test_drainage_dialog as fixture
from caumax_consulta.terrain import analyze_basin


class ActiveBasinLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(prefix='hidrocaumax_active_', ignore_cleanup_errors=True)
        self.addCleanup(self.storage.cleanup)
        storage_patch = patch('caumax_consulta.boundary_layers.storage_directory',
                              return_value=Path(self.storage.name)/'boundaries')
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog
        self.tree = self.fixture.project.layerTreeRoot()

    def coarse_variant(self):
        original = self.fixture.query
        paths = []
        for name, data in (
            ('coarse_dem', 100+np.arange(25,dtype=np.float64).reshape(5,5)),
            ('coarse_d8', np.pad(np.array([[2,4,8],[1,0,16],[128,64,32]]),1)),
        ):
            path = self.fixture.folder/(name+'.tif')
            dataset = gdal.GetDriverByName('GTiff').Create(str(path),5,5,1,gdal.GDT_Float64)
            dataset.SetProjection(self.fixture.crs.toWkt())
            # The original click and the coarse cell centre differ by 250 m.
            dataset.SetGeoTransform((original.x-1000,500,0,original.y+1250,0,-500))
            dataset.GetRasterBand(1).WriteArray(data)
            dataset = None
            paths.append(str(path))
        query = replace(original,dem_path=paths[0],directions_path=paths[1])
        analysis = analyze_basin(query.directions_path,query.dem_path,query.x,query.y)
        return analysis, query

    def assert_active_course_matches(self, dialog):
        active = dialog._course_layers.layers['main']
        visible = [node.layerId() for node in self.tree.findLayers()
                   if node.isVisible() and node.layer().customProperty('HidroCAUMAX/course_owner',None)
                   and next(node.layer().getFeatures(),None)['TIPO']=='total']
        self.assertEqual(visible,[active.id()])
        feature = next(active.getFeatures())
        start = feature.geometry().asPolyline()[0]
        self.assertEqual((start.x(),start.y()),(dialog.analysis.outlet_x,dialog.analysis.outlet_y))
        marker = dialog._marker.center()
        self.assertAlmostEqual(start.x(),marker.x())
        self.assertAlmostEqual(start.y(),marker.y())
        self.assertEqual(feature['RES_X_M'],dialog.analysis.cell_width)
        self.assertAlmostEqual(feature.geometry().length()/1000,dialog.analysis.values['main_length_km'])

    def test_500_to_25_then_new_query_hides_previous_course_and_outline_without_touching_terrain(self):
        dialog = self.dialog
        coarse, coarse_query = self.coarse_variant()
        fine, fine_query = self.fixture.analysis, self.fixture.query
        options = [dict(key=key,resolution=float(key),available=True,provider='Fixture',
                        label=key+' m',status='Disponible') for key in ('500','25')]
        controller = SimpleNamespace(options=options,dispose=lambda:None,variants={
            '500':dict(analysis=coarse,query=coarse_query,smoothing_state=None),
            '25':dict(analysis=fine,query=fine_query,smoothing_state=None)})
        dialog._resolution_controller = controller
        # Resolution worker results, geometries, layers and Qt previews are
        # real; hold only the subsequent hydrology/smoothing worker scheduling.
        with patch.object(dialog,'_queue_smoothing'):
            dialog._resolution_result('500',coarse,coarse_query)
            course_id = dialog._course_layers.layers['main'].id()
            old_outline = dialog._boundary_layers.layers['original']
            old_outline_node = self.tree.findLayer(old_outline.id())
            self.assertTrue(old_outline_node.isVisible())
            self.assert_active_course_matches(dialog)
            dialog._resolution_result('25',fine,fine_query)
            self.assertEqual(dialog._course_layers.layers['main'].id(),course_id)
            self.assert_active_course_matches(dialog)
            self.assertFalse(old_outline_node.isVisible())
            self.assertTrue(old_outline_node.itemVisibilityChecked())
            self.assertTrue(self.tree.findLayer(dialog._boundary_layers.layers['original'].id()).isVisible())
            # Keep a genuine 500 m result in the project when closing this
            # dialog, then reproduce a fresh 25 m query in another dialog.
            dialog._resolution_result('500',coarse,coarse_query)
        old_course = dialog._course_layers.layers['main']
        old_course_bytes = bytes(next(old_course.getFeatures()).geometry().asWkb())
        terrain_nodes = [node for node in self.tree.findLayers()
                         if node.parent().customProperty('HidroCAUMAX/terrain_owner',None)]
        terrain_before = {node.layerId():(node.itemVisibilityChecked(),node.parent().itemVisibilityChecked(),
                                        node.layer().source()) for node in terrain_nodes}
        self.assertTrue(terrain_before)
        dialog.dispose()
        dialog.close()
        fresh = self.fixture.make_dialog()
        self.assert_active_course_matches(fresh)
        self.assertFalse(self.tree.findLayer(old_course.id()).isVisible())
        self.assertFalse(old_outline_node.isVisible())
        self.assertTrue(old_outline_node.itemVisibilityChecked())
        self.assertEqual(bytes(next(old_course.getFeatures()).geometry().asWkb()),old_course_bytes)
        self.assertEqual(next(old_course.getFeatures())['RES_X_M'],500)
        for layer_id, before in terrain_before.items():
            node = self.tree.findLayer(layer_id)
            self.assertIsNotNone(node)
            self.assertEqual((node.itemVisibilityChecked(),node.parent().itemVisibilityChecked(),
                              node.layer().source()),before)


if __name__ == '__main__':
    unittest.main()
