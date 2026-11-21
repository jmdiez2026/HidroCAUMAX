"""Whole-basin overview rendering without live-project changes."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal,osr

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtGui import QImage
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature,
                       QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta import map_capture


class MapCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis()

    def setUp(self):
        # QGIS/GDAL's Windows provider pool may retain a read handle after the
        # transient render layers are gone. Cleanup must not hide a passed
        # render test behind an OS-only attempt to unlink that open raster.
        self.tmp=tempfile.TemporaryDirectory(prefix='hidrocaumax_map_', ignore_cleanup_errors=os.name=='nt')
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.canvas=QgsMapCanvas()
        self.canvas.resize(400,300)
        self.crs=QgsCoordinateReferenceSystem('EPSG:25830')
        self.canvas.setDestinationCrs(self.crs)
        self.canvas.setExtent(QgsRectangle(101,201,102,202))
        self.iface=SimpleNamespace(mapCanvas=lambda:self.canvas)
        self.geometry=QgsGeometry.fromWkt('MULTIPOLYGON (((100 200,180 200,180 260,100 260,100 200)))')
        self.analysis=SimpleNamespace(geometry_wkb=bytes(self.geometry.asWkb()),
            crs_wkt=self.crs.toWkt(),grid_wkb=b'',outlet_x=110,outlet_y=210)
        self.query=SimpleNamespace()
        self.state={'original_checked':True,'smooth_checked':True,'degree':10.5,
                    'geometry_wkb':{'original':bytes(self.geometry.asWkb()),
                                    'suavizada':bytes(self.geometry.smooth(2,.25,-1,180).asWkb())}}
        self.ids=set(QgsProject.instance().mapLayers())
        self.extra=[]

    def tearDown(self):
        self.app.processEvents()
        self.canvas.setLayers([])
        sip.delete(self.canvas)
        for layer in self.extra:
            if not sip.isdeleted(layer):sip.delete(layer)

    def test_full_basin_capture_preserves_live_extent_layers_and_project(self):
        old_extent=QgsRectangle(self.canvas.extent())
        result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        self.assertTrue(result['map_png'].startswith(b'\x89PNG\r\n\x1a\n'))
        image=QImage.fromData(result['map_png'],'PNG')
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(),image.height()),map_capture.OUTPUT_SIZE)
        extent=result['map_extent']
        self.assertLess(extent['xmin'],100)
        self.assertGreater(extent['xmax'],180)
        self.assertLess(extent['ymin'],200)
        self.assertGreater(extent['ymax'],260)
        self.assertEqual(self.canvas.extent(),old_extent)
        self.assertEqual(self.canvas.layers(),[])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)
        self.assertIn('original en rojo',result['map_caption'])
        self.assertIn('suavizada en azul',result['map_caption'])
        self.assertIn('Sin capas de contexto',result['map_caption'])
        self.assertGreater(image.sizeInBytes(),0)

    def test_visible_context_is_used_without_registering_or_changing_layers(self):
        layer=QgsVectorLayer('Polygon?crs=EPSG:25830','Contexto de prueba','memory')
        feature=QgsFeature();feature.setGeometry(QgsGeometry.fromWkt('POLYGON ((0 100,300 100,300 400,0 400,0 100))'))
        layer.dataProvider().addFeatures([feature]);layer.updateExtents()
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'90,180,90,255','outline_style':'no'}))
        self.extra.append(layer)
        self.canvas.setLayers([layer])
        result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        image=QImage.fromData(result['map_png'],'PNG')
        corner=image.pixelColor(20,20)
        self.assertGreater(corner.green(),corner.red())
        self.assertEqual(self.canvas.layers(),[layer])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)
        self.assertNotIn('Sin capas de contexto',result['map_caption'])

    def test_reference_outline_is_available_when_no_contour_is_selected(self):
        result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,
            {'original_checked':False,'smooth_checked':False,'geometry_wkb':{}})
        self.assertIn('referencia de localización',result['map_caption'])
        self.assertTrue(result['map_png'].startswith(b'\x89PNG'))

    def course(self):
        geometry=QgsGeometry.fromWkt('LINESTRING (110 210,125 225,140 230,165 250)')
        self.analysis.main_course_wkb=bytes(geometry.asWkb())
        self.analysis.values={'main_length_km':geometry.length()/1000}
        return geometry

    def test_foreign_active_variant_course_is_replaced_by_the_analysis_course(self):
        expected=self.course()
        foreign=QgsVectorLayer('LineString?crs=EPSG:25830','Curso principal total (L)','memory')
        foreign.setCustomProperty('HidroCAUMAX/course_owner','other-500m-variant')
        feature=QgsFeature();feature.setGeometry(QgsGeometry.fromWkt('LINESTRING (0 0,50 50)'))
        foreign.dataProvider().addFeatures([feature]);foreign.updateExtents()
        foreign.renderer().setSymbol(QgsLineSymbol.createSimple({'line_color':'255,0,255,255'}))
        context=QgsVectorLayer('Polygon?crs=EPSG:25830','HidroCAUMAX · MDT de contexto','memory')
        context.setCustomProperty('HidroCAUMAX/dem_owner','retained-context')
        feature=QgsFeature();feature.setGeometry(QgsGeometry.fromWkt('POLYGON ((0 100,300 100,300 400,0 400,0 100))'))
        context.dataProvider().addFeatures([feature]);context.updateExtents()
        self.extra.extend([foreign,context]);self.canvas.setLayers([foreign,context])
        layers=[];drawn_courses=[]
        original_render=map_capture._render
        def inspect(settings,keep_alive):
            layers.extend(layer.id() for layer in settings.layers())
            drawn_courses.extend(bytes(next(layer.getFeatures()).geometry().asWkb())
                                 for layer in settings.layers() if layer.name()=='Curso principal total (L)')
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        self.assertNotIn(foreign.id(),layers)
        self.assertIn(context.id(),layers)
        self.assertEqual(drawn_courses,[bytes(expected.asWkb())])
        self.assertEqual(self.canvas.layers(),[foreign,context])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)
        self.assertIn('Curso principal total en azul claro',result['map_caption'])

    def test_course_visibility_hides_total_and_draws_exact_central_interval(self):
        expected=self.course()
        original_render=map_capture._render
        drawn=[]
        def inspect(settings,keep_alive):
            drawn.append({layer.name():bytes(next(layer.getFeatures()).geometry().asWkb())
                          for layer in settings.layers() if 'curso principal' in layer.name().lower()})
            return original_render(settings,keep_alive)
        state={**self.state,'course_visibility':{'main':False,'central':False}}
        with patch.object(map_capture,'_render',side_effect=inspect):
            hidden=map_capture.capture_basin_map(self.iface,self.analysis,self.query,state)
            state['course_visibility']['central']=True
            visible=map_capture.capture_basin_map(self.iface,self.analysis,self.query,state)
        self.assertEqual(drawn[0],{})
        self.assertEqual(len(drawn[1]),1)
        name,wkb=next(iter(drawn[1].items()))
        self.assertIn('10–85',name)
        central=QgsGeometry();central.fromWkb(wkb)
        self.assertAlmostEqual(central.length(),.75*expected.length(),places=8)
        self.assertNotIn('Curso principal total en azul claro',hidden['map_caption'])
        self.assertIn('Tramo 10–85 % en azul claro discontinuo',visible['map_caption'])

    def test_snapshot_network_replaces_foreign_variant_below_main_course_even_offline(self):
        self.course()
        foreign=QgsVectorLayer('LineString?crs=EPSG:25830','Red de otra variante','memory')
        foreign.setCustomProperty('HidroCAUMAX/drainage_owner','other-500m')
        feature=QgsFeature();feature.setGeometry(QgsGeometry.fromWkt('LINESTRING (0 0,1 1)'))
        foreign.dataProvider().addFeatures([feature]);foreign.updateExtents()
        self.extra.append(foreign);self.canvas.setLayers([foreign])
        expected=QgsGeometry.fromWkt('LINESTRING Z (110 210 800,115 225 810,125 240 850)')
        state={**self.state,'drainage_state':{'network_crs_wkt':self.crs.toWkt(),
            'network_path':'/missing/network.shp','tau_d8_path':'/missing/taudem.tif',
            'features':[{'wkb':bytes(expected.asWkb()),'attributes':{'LINKNO':1}}]}}
        names=[];drawn=[];original_render=map_capture._render
        def inspect(settings,keep_alive):
            self.assertNotIn(foreign,settings.layers())
            names.extend(layer.name() for layer in settings.layers())
            drawn.extend(bytes(next(layer.getFeatures()).geometry().asWkb())
                         for layer in settings.layers() if layer.name()=='Red de drenaje TauDEM')
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,state)
        self.assertEqual(drawn,[bytes(expected.asWkb())])
        self.assertLess(names.index('Curso principal total (L)'),names.index('Red de drenaje TauDEM'))
        self.assertLess(names.index('Red de drenaje TauDEM'),names.index('Cuenca original'))
        self.assertIn('Red de drenaje TauDEM de esta consulta',result['map_caption'])
        self.assertEqual(self.canvas.layers(),[foreign])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)

    def test_network_visibility_uses_saved_flags_and_never_live_directions_of_other_variant(self):
        directions=QgsRasterLayer(str(self.terrain()),'Direcciones D8 otra variante','gdal')
        directions.setCustomProperty('HidroCAUMAX/drainage_owner','other-25m')
        self.extra.append(directions);self.canvas.setLayers([directions])
        geometry=QgsGeometry.fromWkt('LINESTRING (110 210,150 240)')
        drainage={'network_crs_wkt':self.crs.toWkt(),
            'features':[{'wkb':bytes(geometry.asWkb()),'attributes':{}}],
            'metadata':{'visible':False}}
        state={**self.state,'drainage_state':drainage}
        names=[];original_render=map_capture._render
        def inspect(settings,keep_alive):
            names.append([layer.name() for layer in settings.layers()])
            self.assertNotIn(directions,settings.layers())
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            hidden=map_capture.capture_basin_map(self.iface,self.analysis,self.query,state)
            drainage['network_visible']=True
            visible=map_capture.capture_basin_map(self.iface,self.analysis,self.query,state)
        self.assertNotIn('Red de drenaje TauDEM',names[0])
        self.assertIn('Red de drenaje TauDEM',names[1])
        self.assertNotIn('Red de drenaje TauDEM',hidden['map_caption'])
        self.assertIn('Red de drenaje TauDEM',visible['map_caption'])

    def test_red_fill_is_twenty_percent_opaque_and_fine_grid_is_omitted_at_overview_scale(self):
        self.analysis.grid_wkb=bytes(QgsGeometry.fromWkt('MULTILINESTRING ((100 220,180 220),(100 230,180 230))').asWkb())
        self.analysis.cell_width=self.analysis.cell_height=.05
        captured=[];original_render=map_capture._render
        def inspect(settings,keep_alive):
            captured.append({layer.name():layer.renderer().symbol().symbolLayer(0).color().alpha()
                             for layer in settings.layers() if isinstance(layer,QgsVectorLayer)})
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            fine=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
            self.analysis.cell_width=self.analysis.cell_height=10
            coarse=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        self.assertEqual(captured[0]['Cuenca original'],51)
        self.assertEqual(captured[1]['Cuenca original'],51)
        self.assertNotIn('Celdas de la cuenca',captured[0])
        self.assertIn('Celdas de la cuenca',captured[1])
        self.assertIn('celda menor de 3 px',fine['map_caption'])
        self.assertNotIn('Malla omitida',coarse['map_caption'])

    def terrain(self):
        path=self.folder/'terrain.tif'
        y,x=np.mgrid[:200,:200]
        data=(500+200*np.exp(-((x-100)**2+(y-100)**2)/2000)+2*x+.5*y).astype(np.float32)
        dataset=gdal.GetDriverByName('GTiff').Create(str(path),200,200,1,gdal.GDT_Float32)
        dataset.SetGeoTransform((90,.5,0,270,0,-.4))
        srs=osr.SpatialReference();srs.ImportFromEPSG(25830);dataset.SetProjection(srs.ExportToWkt())
        dataset.GetRasterBand(1).WriteArray(data);dataset=None
        return path

    def test_active_dem_uses_real_hillshade_and_natural_colors_without_foreign_variant_terrain(self):
        path=self.terrain();self.query.dem_path=str(path)
        foreign=QgsRasterLayer(str(path),'Otra variante MDT','gdal')
        foreign.setCustomProperty('hidrocaumax/dem_display_role','hypsometry')
        foreign.setCustomProperty('hidrocaumax/dem_source','/another/variant.tif')
        self.extra.append(foreign);self.canvas.setLayers([foreign])
        inspected=[];original_render=map_capture._render
        def inspect(settings,keep_alive):
            inspected.extend((layer.id(),layer.customProperty('hidrocaumax/dem_display_role',None),
                              layer.renderer().type() if isinstance(layer,QgsRasterLayer) else None,
                              layer.source()) for layer in settings.layers())
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        self.assertNotIn(foreign.id(),[item[0] for item in inspected])
        terrain=[item for item in inspected if item[1] is not None]
        self.assertEqual([(item[1],item[2]) for item in terrain],
                         [('hillshade','hillshade'),('hypsometry','singlebandpseudocolor')])
        self.assertTrue(all(Path(item[3]).resolve()==path.resolve() for item in terrain))
        self.assertIn('rampa natural y sombreado multidireccional',result['map_caption'])
        self.assertEqual(self.canvas.layers(),[foreign])
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)
        self.assertTrue(result['map_png'].startswith(b'\x89PNG'))

    def test_invalid_or_missing_selected_geometry_fails_before_render(self):
        with patch.object(map_capture,'_render') as render:
            with self.assertRaisesRegex(ValueError,'actualice'):
                map_capture.capture_basin_map(self.iface,self.analysis,self.query,
                    {'original_checked':True,'smooth_checked':True,'geometry_wkb':{}})
            invalid=SimpleNamespace(**vars(self.analysis));invalid.geometry_wkb=b'not wkb'
            with self.assertRaises(ValueError):
                map_capture.capture_basin_map(self.iface,invalid,self.query,self.state)
        render.assert_not_called()

    def test_snap_draws_both_points_and_includes_original_outside_the_basin(self):
        self.analysis.diagnostics={'outlet_snap':{'original_x':300,'original_y':330,
            'snapped_x':9999,'snapped_y':9999,'distance_m':9999,'status':'snapped',
            'method':'nearest_main_channel_d8'}}
        old_extent=QgsRectangle(self.canvas.extent())
        drawn={};original_render=map_capture._render
        def inspect(settings,keep_alive):
            drawn.update({layer.name():QgsGeometry(next(layer.getFeatures()).geometry())
                          for layer in settings.layers()})
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        extent=result['map_extent']
        self.assertGreater(extent['xmax'],300)
        self.assertGreater(extent['ymax'],330)
        source=drawn['Punto de consulta original'].asPoint()
        effective=drawn['Sección de cierre ajustada'].asPoint()
        self.assertEqual((source.x(),source.y()),(300,330))
        self.assertEqual((effective.x(),effective.y()),(110,210))
        self.assertAlmostEqual(drawn['Desplazamiento del cierre'].length(),(190**2+120**2)**.5)
        self.assertIn('círculo naranja',result['map_caption'])
        self.assertIn('cruz azul',result['map_caption'])
        self.assertIn('224,72 m',result['map_caption'])
        self.assertEqual(self.canvas.extent(),old_extent)
        self.assertEqual(set(QgsProject.instance().mapLayers()),self.ids)

    def test_unchanged_snap_has_two_symbols_without_a_zero_length_connector(self):
        self.analysis.diagnostics={'outlet_snap':{'original_x':110,'original_y':210,'status':'unchanged'}}
        names=[];original_render=map_capture._render
        def inspect(settings,keep_alive):
            names.extend(layer.name() for layer in settings.layers())
            return original_render(settings,keep_alive)
        with patch.object(map_capture,'_render',side_effect=inspect):
            result=map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        self.assertIn('Punto de consulta original',names)
        self.assertIn('Sección de cierre ajustada',names)
        self.assertNotIn('Desplazamiento del cierre',names)
        self.assertIn('0,00 m (misma celda)',result['map_caption'])
        self.analysis.diagnostics['outlet_snap']['original_x']=float('nan')
        with patch.object(map_capture,'_render') as render:
            with self.assertRaisesRegex(ValueError,'coordenadas originales'):
                map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
        render.assert_not_called()

    def test_timeout_is_bounded_and_keeps_cancelled_job_alive_until_finished(self):
        class SlowJob(QObject):
            finished=pyqtSignal()
            instances=[]
            def __init__(self,settings):
                super().__init__();self.active=True;self.cancelled=False;self.instances.append(self)
            def start(self):pass
            def isActive(self):return self.active
            def cancelWithoutBlocking(self):self.cancelled=True
            def finish(self):self.active=False;self.finished.emit()
        with patch.object(map_capture,'QgsMapRendererParallelJob',SlowJob),patch.object(map_capture,'RENDER_TIMEOUT_MS',10):
            with self.assertRaises(TimeoutError):
                map_capture.capture_basin_map(self.iface,self.analysis,self.query,self.state)
            job=SlowJob.instances[-1]
            self.assertTrue(job.cancelled)
            self.assertIn(id(job),map_capture._PENDING_RENDERS)
            job.finish()
            self.app.processEvents()
            self.assertNotIn(id(job),map_capture._PENDING_RENDERS)


if __name__=='__main__':
    unittest.main()
