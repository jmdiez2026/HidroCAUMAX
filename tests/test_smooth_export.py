"""Preview-first and round-trip checks for cartographic smoothing."""
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject, QgsVariantUtils

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.basin import BasinResult
from caumax_consulta.metrics import shape_metrics
from caumax_consulta.smooth_export import (
    SmoothExportDialog, smoothed_geometry, smoothing_settings, suggested_iterations, export_layer,
    export_previewed, choose_export_destination, _suggested_basin_destination,
)
from caumax_consulta.watershed_export import write_layer


class SmoothExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        geometry = QgsGeometry.fromWkt(
            'MULTIPOLYGON (((100 200, 140 200, 140 240, 100 240, 100 200),'
            '(110 210, 110 220, 120 220, 120 210, 110 210)),'
            '((160 200, 190 200, 190 220, 160 220, 160 200)))')
        self.basin = BasinResult(
            geometry_wkb=bytes(geometry.asWkb()),
            crs_wkt=QgsCoordinateReferenceSystem('EPSG:25830').toWkt(),
            area_m2=geometry.area(), perimeter_m=geometry.length(),
            z_min=100, z_max=200, z_mean=150, cell_count=21,
            cell_width=10, cell_height=10, outlet_x=105, outlet_y=205,
            touches_boundary=False,
        )
        self.analysis = SimpleNamespace(**vars(self.basin), values={
            'elongation_ratio': .7, 'melton': .4, 'slope_d8_pct': 8.0,
            'hypsometric': .5, 'tri_m': 12.0, 'relief_m': 100.0,
            'zmax_m': 200.0, 'zmin_m': 100.0, 'zmedian_m': 137.5,
            'main_length_km': 1.5, 'main_length_10_85_km': 1.125,
            'tc_h': .75, 'tc_10_85_h': .5,
        }, grid_wkb=b'')
        self.query = SimpleNamespace(flow=20.5, period=100, cell=SimpleNamespace(row=5,col=7))
        self.dialogs = []
        self.saved = []
        self.initial_ids = set(QgsProject.instance().mapLayers())

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            sip.delete(dialog)
        for layer_id in set(QgsProject.instance().mapLayers()) - self.initial_ids:
            layer=QgsProject.instance().takeMapLayer(QgsProject.instance().mapLayer(layer_id))
            if layer is not None and not sip.isdeleted(layer):
                layer.dataProvider().forceReload()
                sip.delete(layer)
        for layer in self.saved:
            if not sip.isdeleted(layer):
                layer.dataProvider().forceReload()
                sip.delete(layer)
        self.tmp.cleanup()

    def dialog(self):
        dialog = SmoothExportDialog(self.analysis,self.query,None)
        self.dialogs.append(dialog)
        return dialog

    def rendered(self, dialog):
        dialog.show()
        deadline=time.monotonic()+5
        while not dialog._preview_ready and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(dialog._preview_ready, dialog.status.text())

    def test_export_requires_rendered_preview(self):
        dialog=self.dialog()
        with self.assertRaisesRegex(ValueError,'vista previa'):
            dialog.export_to(self.folder/'unseen.shp',False)
        self.assertFalse((self.folder/'unseen.shp').exists())
        self.rendered(dialog)
        self.assertTrue(dialog.save_button.isEnabled())

    def test_smoothing_preserves_components_holes_and_closure(self):
        original=QgsGeometry.fromWkt('MULTIPOLYGON (((0 0,10 0,10 10,0 10,0 0),(3 3,3 5,5 5,5 3,3 3)),((20 0,30 0,30 10,20 10,20 0)))')
        for count in range(1,16):
            result=smoothed_geometry(original,count)
            self.assertTrue(result.isGeosValid())
            self.assertEqual(sorted(map(len,result.asMultiPolygon())),[1,2])
        self.assertIn(suggested_iterations(original),(1,2,3))
        with self.assertRaises(ValueError):
            smoothed_geometry(original,0)

    def test_extended_degrees_preserve_old_results_and_bound_vertex_growth(self):
        original=QgsGeometry()
        original.fromWkb(self.basin.geometry_wkb)
        for degree in range(1,6):
            self.assertEqual(bytes(smoothed_geometry(original,degree).asWkb()),
                             bytes(original.smooth(degree,.25,-1,180).asWkb()))
        old_maximum=smoothed_geometry(original,5)
        previous=bytes(old_maximum.asWkb())
        for degree in range(6,16):
            actual=smoothed_geometry(original,degree)
            self.assertNotEqual(previous,bytes(actual.asWkb()))
            self.assertLessEqual(len(list(actual.vertices())),4*len(list(old_maximum.vertices())))
            previous=bytes(actual.asWkb())
        self.assertEqual(smoothing_settings(15),(5,.45))
        for invalid in (0,16,True,2.5):
            with self.assertRaises(ValueError):
                smoothed_geometry(original,invalid)
        dialog=self.dialog()
        self.assertEqual((dialog.iterations.minimum(),dialog.iterations.maximum()),(1,15))

    def test_spinner_changes_preview_and_export_is_exact_geometry(self):
        self.analysis.outlet_x,self.analysis.outlet_y=130,230
        dialog=self.dialog()
        dialog.original_check.setChecked(False)
        dialog.smooth_check.setChecked(True)
        dialog.iterations.setValue(1)
        self.rendered(dialog)
        first=bytes(dialog.preview_geometries['suavizada'].asWkb())
        dialog.iterations.setValue(15)
        self.assertFalse(dialog.save_button.isEnabled())
        self.rendered(dialog)
        preview=QgsGeometry(dialog.preview_geometries['suavizada'])
        self.assertNotEqual(first,bytes(preview.asWkb()))
        cached=dialog._smooth_cache[15]
        with patch('caumax_consulta.smooth_export.smoothed_geometry',side_effect=AssertionError('must not recompute')):
            saved=dialog.export_to(self.folder/'smooth.gpkg',False)[0]
        self.saved.append(saved)
        feature=next(saved.getFeatures())
        self.assertTrue(feature.geometry().isGeosEqual(preview))
        self.assertIs(dialog._smooth_cache[15],cached)
        self.assertAlmostEqual(feature['AREA_KM2'],preview.area()/1e6,places=10)
        self.assertAlmostEqual(feature['A_ORIG_KM'],self.basin.area_m2/1e6,places=10)
        self.assertEqual(feature['SMOOTH_N'],15)
        self.assertEqual(feature['GEOM_TIPO'],'suavizada')
        self.assertAlmostEqual(feature['TC_H'],.75)
        self.assertAlmostEqual(feature['TC1085_H'],.5)
        self.assertEqual((feature['ZMAX_M'],feature['ZMIN_M'],feature['ZMED_M']),
                         (200.0,100.0,137.5))
        self.assertNotEqual(feature['ZMED_M'],self.basin.z_mean)
        self.assertEqual((feature['RES_X_M'],feature['RES_Y_M']),(10.0,10.0))
        self.assertAlmostEqual(feature['KC_GRAV'],shape_metrics(preview.area(),preview.length()).compactness,places=9)

    def test_both_variants_get_suffixes_and_preflight_every_destination(self):
        dialog=self.dialog()
        dialog.smooth_check.setChecked(True)
        self.rendered(dialog)
        targets=dialog.destinations(self.folder/'basin.shp')
        self.assertEqual(targets['original'].name,'basin_original.shp')
        self.assertEqual(targets['suavizada'].name,'basin_suavizada.shp')
        targets['suavizada'].with_suffix('.dbf').write_bytes(b'keep existing')
        with self.assertRaisesRegex(ValueError,'auxiliar'):
            dialog.export_to(self.folder/'basin.shp',False)
        self.assertFalse(targets['original'].exists())
        self.assertEqual(targets['suavizada'].with_suffix('.dbf').read_bytes(),b'keep existing')
        layers=dialog.export_to(self.folder/'both.gpkg',False)
        self.saved.extend(layers)
        self.assertEqual(len(layers),2)
        for kind, layer in zip(dialog.selected_kinds(),layers):
            feature=next(layer.getFeatures())
            self.assertTrue(feature.geometry().isGeosEqual(dialog.preview_geometries[kind]))
            self.assertTrue(all(len(field.name())<=10 for field in layer.fields()))

    def test_smoothed_shapefile_matches_visible_geometry_and_original_metrics(self):
        dialog=self.dialog()
        dialog.original_check.setChecked(False)
        dialog.smooth_check.setChecked(True)
        self.rendered(dialog)
        preview=QgsGeometry(dialog.preview_geometries['suavizada'])
        saved=dialog.export_to(self.folder/'visible_smooth.shp',False)[0]
        self.saved.append(saved)
        feature=next(saved.getFeatures())
        self.assertTrue(feature.geometry().isGeosEqual(preview))
        self.assertEqual(sorted(map(len,feature.geometry().asMultiPolygon())),[1,2])
        self.assertEqual(saved.crs(),QgsCoordinateReferenceSystem('EPSG:25830'))
        self.assertAlmostEqual(feature['AREA_KM2'],preview.area()/1e6,places=9)
        self.assertAlmostEqual(feature['P_ORIG_KM'],self.basin.perimeter_m/1000,places=9)
        self.assertAlmostEqual(feature['ELONG_R'],.7,places=9)
        self.assertEqual(feature['N_CELDAS'],21)
        self.assertEqual((feature['ZMAX_M'],feature['ZMIN_M'],feature['ZMED_M']),
                         (200.0,100.0,137.5))
        self.assertAlmostEqual(feature['TC_H'],.75)
        self.assertAlmostEqual(feature['TC1085_H'],.5)
        self.assertEqual((feature['RES_X_M'],feature['RES_Y_M']),(10.0,10.0))

    def test_unavailable_elevations_and_both_times_remain_null_in_shp_and_gpkg(self):
        for key in ('zmax_m','zmin_m','zmedian_m','tc_h','tc_10_85_h'):
            self.analysis.values[key]=None
        geometry=QgsGeometry()
        geometry.fromWkb(self.basin.geometry_wkb)
        for suffix in ('.shp','.gpkg'):
            with self.subTest(suffix=suffix):
                layer=export_layer(self.analysis,self.query,geometry,'original')
                saved=write_layer(layer,self.folder/('null_parameters'+suffix))
                self.saved.append(saved)
                feature=next(saved.getFeatures())
                for name in ('ZMAX_M','ZMIN_M','ZMED_M','TC_H','TC1085_H'):
                    self.assertTrue(QgsVariantUtils.isNull(feature[name]),name)
                self.assertTrue(feature.geometry().isGeosEqual(geometry))
                self.assertEqual((feature['RES_X_M'],feature['RES_Y_M']),(10.0,10.0))

    def test_map_preview_export_keeps_cached_geometry_and_fractional_degree(self):
        original=QgsGeometry()
        original.fromWkb(self.basin.geometry_wkb)
        preview=original.smooth(2,.3,-1,180)
        snapshot=bytes(preview.asWkb())
        for suffix,degree in (('.shp',10.5),('.gpkg',45.0),('.shp',.5)):
            with self.subTest(suffix=suffix,degree=degree):
                seen=[]
                def record_write(layer,path):
                    seen.append(bytes(next(layer.getFeatures()).geometry().asWkb()))
                    return write_layer(layer,path)
                with patch('caumax_consulta.smooth_export.smoothed_geometry',
                           side_effect=AssertionError('must not recompute')), \
                     patch('caumax_consulta.smooth_export.write_layer',side_effect=record_write):
                    saved=export_previewed(self.analysis,self.query,{'suavizada':preview},
                                           self.folder/(str(degree)+suffix),degree,False)[0]
                self.saved.append(saved)
                self.assertEqual(seen,[snapshot])
                self.assertEqual(bytes(preview.asWkb()),snapshot)
                feature=next(saved.getFeatures())
                self.assertTrue(feature.geometry().isGeosEqual(preview))
                self.assertEqual(feature['SMOOTH_N'],degree)
                self.assertEqual(saved.fields().field('SMOOTH_N').type(),QVariant.Double)
                self.assertEqual((feature['TC_H'],feature['TC1085_H']),(.75,.5))
                self.assertEqual((feature['ZMAX_M'],feature['ZMIN_M'],feature['ZMED_M']),
                                 (200.0,100.0,137.5))
                self.assertEqual((feature['RES_X_M'],feature['RES_Y_M']),(10.0,10.0))
                self.assertAlmostEqual(feature['AREA_KM2'],preview.area()/1e6,places=9)
                self.assertAlmostEqual(feature['A_ORIG_KM'],original.area()/1e6,places=9)
                self.assertEqual(saved.crs(),QgsCoordinateReferenceSystem('EPSG:25830'))
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.initial_ids)

    def test_map_export_preflights_paths_geometries_and_degree_before_writing(self):
        original=QgsGeometry()
        original.fromWkb(self.basin.geometry_wkb)
        preview=original.smooth(1,.25,-1,180)
        geometries={'suavizada':preview,'original':original}
        invalid_cases=[({},1,'empty.shp'),({'other':original},1,'kind.shp'),
                       (geometries,1,'wrong.tif'),
                       ({'original':original,'suavizada':None},1,'null.shp'),
                       ({'original':original,'suavizada':QgsGeometry.fromWkt(
                           'POLYGON ((0 0,1 0,1 1,0 1,0 0))')},1,'parts.shp')]
        invalid_cases.extend((geometries,degree,'degree.shp')
                             for degree in (0,.49,45.5,float('nan'),float('inf'),True,None))
        for selected,degree,name in invalid_cases:
            with self.subTest(name=name,degree=degree):
                with patch('caumax_consulta.smooth_export.write_layer') as writer:
                    with self.assertRaises(ValueError):
                        export_previewed(self.analysis,self.query,selected,self.folder/name,degree)
                    writer.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()),[])
        existing=self.folder/'both_suavizada.dbf'
        existing.write_bytes(b'keep existing')
        with patch('caumax_consulta.smooth_export.write_layer') as writer:
            with self.assertRaisesRegex(ValueError,'auxiliar'):
                export_previewed(self.analysis,self.query,geometries,self.folder/'both.shp',2.5)
            writer.assert_not_called()
        self.assertEqual(list(self.folder.iterdir()),[existing])
        self.assertEqual(existing.read_bytes(),b'keep existing')
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.initial_ids)

    def test_default_caumax_divisoria_name_uses_query_coords_period_and_profile(self):
        analysis = SimpleNamespace(outlet_x=334825.0, outlet_y=4729200.0)
        query = SimpleNamespace(source_kind='caumax_raster', period=100)
        with patch('caumax_consulta.improved_storage.generated_layers_directory',
                   return_value=self.folder):
            suggested = _suggested_basin_destination(analysis, query)
            self.assertEqual(suggested, self.folder /
                             'CONSULTA_Divisoria_X334825_Y4729200_T100.shp')
            query.period = 500
            self.assertEqual(_suggested_basin_destination(analysis, query).name,
                             'CONSULTA_Divisoria_X334825_Y4729200_T500.shp')
            query.period = None
            self.assertEqual(_suggested_basin_destination(analysis, query).name,
                             'CONSULTA_Divisoria_X334825_Y4729200.shp')

    def test_map_destination_cancel_leaves_project_and_files_unchanged(self):
        geometry=QgsGeometry()
        geometry.fromWkb(self.basin.geometry_wkb)
        with patch('caumax_consulta.smooth_export.QFileDialog.getSaveFileName',return_value=('','')), \
             patch('caumax_consulta.smooth_export.SmoothExportDialog',
                   side_effect=AssertionError('no extra preview dialog')):
            self.assertEqual(choose_export_destination(None,self.analysis,self.query,
                                                       {'original':geometry},10.5),[])
        self.assertEqual(list(self.folder.iterdir()),[])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.initial_ids)

    def test_map_destination_defaults_to_shp_and_adds_exact_variants_to_project(self):
        original=QgsGeometry()
        original.fromWkb(self.basin.geometry_wkb)
        geometries={'suavizada':original.smooth(1,.3,-1,180),'original':original}
        with patch('caumax_consulta.smooth_export.QFileDialog.getSaveFileName',
                   return_value=(str(self.folder/'chosen'),'Shapefile (*.shp)')) as chooser, \
             patch('caumax_consulta.smooth_export.SmoothExportDialog',
                   side_effect=AssertionError('no extra preview dialog')):
            saved=choose_export_destination(None,self.analysis,self.query,geometries,20.5)
        self.saved.extend(saved)
        self.assertTrue(chooser.call_args.args[2].endswith('.shp'))
        self.assertTrue(chooser.call_args.args[3].startswith('Shapefile'))
        self.assertEqual(len(saved),2)
        self.assertEqual(set(QgsProject.instance().mapLayers())-self.initial_ids,
                         {layer.id() for layer in saved})
        for kind,layer in zip(('original','suavizada'),saved):
            self.assertTrue((self.folder/f'chosen_{kind}.shp').exists())
            feature=next(layer.getFeatures())
            self.assertTrue(feature.geometry().isGeosEqual(geometries[kind]))
            self.assertEqual(feature['SMOOTH_N'],20.5 if kind=='suavizada' else 0.0)

    def test_no_selection_disables_save_and_cancel_does_not_change_project(self):
        dialog=self.dialog()
        self.rendered(dialog)
        dialog.original_check.setChecked(False)
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertEqual(dialog.preview_geometries,{})
        dialog.reject()
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.initial_ids)
        self.assertEqual(list(self.folder.iterdir()),[])

    def test_selected_preview_layers_are_not_added_to_project(self):
        dialog=self.dialog()
        dialog.smooth_check.setChecked(True)
        self.rendered(dialog)
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.initial_ids)
        self.assertEqual({layer.name() for layer in dialog.preview_layers},{'original','suavizada'})


if __name__=='__main__':
    unittest.main()
