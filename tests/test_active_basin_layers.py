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

    def test_latest_d8_group_retires_course_without_deleting_terrain_or_input_rasters(self):
        from caumax_consulta.course_layers import CourseLayers
        from caumax_consulta.drainage_layers import OWNER_PROPERTY, PIN_PROPERTY
        dialog = self.dialog
        # The old 500 m/main-course group may have been produced by a
        # previous maximum-flow calculation; the new rational D8 must replace
        # it while preserving both the displayed network and source rasters.
        dialog._course_layers = CourseLayers(self.fixture.iface)
        old = dialog._course_layers.update(dialog.analysis, dialog.query)
        old_ids = {layer.id() for layer in old.values()}
        terrain = [layer for layer in self.fixture.project.mapLayers().values()
                   if layer.customProperty('HidroCAUMAX/terrain_owner', '')]
        terrain_ids = {layer.id() for layer in terrain}
        self.assertTrue(old_ids)
        self.assertTrue(terrain_ids)
        new_dialog = self.fixture.make_dialog()
        self.assertTrue(all(self.fixture.project.mapLayer(key) is None for key in old_ids))
        self.assertFalse(any(g.customProperty('HidroCAUMAX/course_owner', '')
                             for g in self.tree.findGroups(recursive=True)))
        self.assertTrue(terrain_ids.issubset(self.fixture.project.mapLayers()))
        published = [g for g in self.tree.findGroups(recursive=True)
                     if g.customProperty(OWNER_PROPERTY, '')
                     and g.customProperty(PIN_PROPERTY, False)]
        self.assertTrue(published)
        self.assertTrue(all(self.fixture.project.mapLayer(layer.id()) is layer
                            for layer in new_dialog._drainage_layers.layers.values()
                            if layer is not None))
        self.assertTrue(self.fixture.dem.exists())
        self.assertTrue(self.fixture.directions.exists())


if __name__ == '__main__':
    unittest.main()
