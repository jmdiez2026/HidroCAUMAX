"""Async resolution variants: exact outlet, cache, bulk work and late results."""
from dataclasses import replace
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from osgeo import gdal,osr
from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication,QgsCoordinateReferenceSystem,QgsGeometry

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from caumax_consulta.basin import BasinResult
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.terrain import Analysis
from caumax_consulta import resolution_controller as rc


def sample(resolution=500,boundary=False):
    crs=QgsCoordinateReferenceSystem('EPSG:25830')
    polygon=QgsGeometry.fromWkt('MULTIPOLYGON (((0 0,500 0,500 500,0 500,0 0)))')
    count=round(250000/(resolution*resolution))
    basin=BasinResult(bytes(polygon.asWkb()),crs.toWkt(),250000,2000,
                      100,200,150,count,resolution,resolution,250,250,boundary)
    values={'area_km2':.25,'cell_count':count,'cell_resolution':(resolution,resolution),
            'zmedian_m':155,'tc_h':1,'tc_10_85_h':.8}
    return Analysis(basin,values,(),{},b'',b'',230.2,245.3)


def option(resolution,available=True):
    return SimpleNamespace(resolution_m=float(resolution),title=f'MDT {resolution} m',
          provider='Official fixture',url='https://example.invalid/dem',collection_id='dem',
          source_crs_wkt=QgsCoordinateReferenceSystem('EPSG:25830').toWkt(),
          available=available,status='Disponible' if available else 'Sin cobertura',
          metadata_url='https://example.invalid/metadata')


class ResolutionControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='caumax_resolution_')
        self.folder=Path(self.tmp.name)
        self.base=sample()
        self.query=NativeQuery(177,100,230.2,245.3,QgsCoordinateReferenceSystem('EPSG:25830'),
                               '/original/dir.tif','/original/mdt.tif','q100',cell=None)
        self.options_events=[]
        self.results=[]
        self.status=[]
        self.errors=[]
        self.threads=[]
        self.downloads=[]
        self.analyses=[]
        self.snap_options=[]
        self.controllers=[]
        self.catalog=[option(25),option(5),option(2,False),option(.5,False)]
        self.patchers=[
            patch.object(rc,'_discover_resolutions',side_effect=self.fake_discover),
            patch.object(rc,'_download_dem',side_effect=self.fake_download),
            patch.object(rc,'_analyze_dem',side_effect=self.fake_analyze),
            patch.object(rc,'_engine_available',return_value={'available':True,'name':'GRASS fixture'}),
        ]
        for p in self.patchers:p.start()

    def tearDown(self):
        for controller in self.controllers:controller.dispose()
        self.wait(lambda:not QgsApplication.taskManager().activeTasks())
        for p in reversed(self.patchers):p.stop()
        self.tmp.cleanup()

    def event(self,target,value):
        self.threads.append(QThread.currentThread())
        target.append(value)

    def controller(self,query=None):
        c=rc.ResolutionController(self.base,query or self.query,
            lambda options:self.event(self.options_events,options),
            lambda key,analysis,query:self.event(self.results,(key,analysis,query)),
            lambda text:self.event(self.status,text),lambda text:self.event(self.errors,text))
        c.cache_dir=self.folder/str(len(self.controllers))
        self.controllers.append(c)
        return c

    def wait(self,condition,timeout=5):
        deadline=time.monotonic()+timeout
        while not condition() and time.monotonic()<deadline:QTest.qWait(5)
        self.assertTrue(condition(),'Timed out waiting for Qt task callbacks')

    def fake_discover(self,bounds,crs,cancelled,progress):
        progress(20)
        self.assertEqual(tuple(bounds),(-2000,-2000,2500,2500))
        self.assertIn('PROJCS',crs)
        progress(100)
        return self.catalog

    def fake_download(self,source,bounds,crs,destination,cancelled,progress):
        if cancelled():raise InterruptedError()
        self.downloads.append((source.resolution_m,tuple(bounds),str(destination)))
        path=Path(destination)
        path.parent.mkdir(parents=True,exist_ok=True)
        ds=gdal.GetDriverByName('GTiff').Create(str(path),5,5,1,gdal.GDT_Float32)
        ds.SetGeoTransform((0,source.resolution_m,0,500,0,-source.resolution_m))
        ds.SetProjection(source.source_crs_wkt)
        ds.GetRasterBand(1).Fill(100)
        ds=None
        progress(100)
        return path

    def fake_analyze(self,path,x,y,cache_dir,cancelled,progress,**snap_options):
        self.analyses.append((str(path),x,y,str(cache_dir)))
        self.snap_options.append(dict(snap_options))
        ds=gdal.Open(str(path))
        resolution=ds.GetGeoTransform()[1]
        ds=None
        progress(100)
        return SimpleNamespace(analysis=sample(resolution),
                               directions_path=Path(cache_dir)/'directions.tif',
                               dem_path=Path(path),metadata={'native_resolution':resolution})

    def discover(self,c):
        c.discover()
        self.wait(lambda:not c.busy)
        self.assertFalse(self.errors)

    def test_discovery_has_original_and_only_operational_native_resolutions(self):
        c=self.controller()
        self.assertEqual(self.options_events,[])
        self.discover(c)
        by_key={o['key']:o for o in c.options}
        self.assertEqual(list(by_key),['500','25','5','2','0.5'])
        self.assertTrue(all(by_key[k]['available'] for k in ('500','25','5')))
        self.assertFalse(by_key['2']['available'])
        self.assertFalse(by_key['0.5']['available'])
        self.assertTrue(all(t is self.app.thread() for t in self.threads))
        self.assertEqual(self.downloads,[])

    def test_ambiguous_snap_keeps_active_basin_and_offers_reviewable_native_context(self):
        c=self.controller()
        self.discover(c)
        context=dict(dem_path='/fine/dem.tif',directions_path='/fine/d8.tif',
            crs_wkt=self.query.crs.toWkt(),original_x=self.query.x,original_y=self.query.y,
            search_radius_m=707.106781,reference_area_m2=self.base.area_m2)
        error=ValueError('Dos ramales posibles: elige el cierre.')
        error.selection_context=context
        with patch.object(rc,'_analyze_dem',side_effect=error):
            self.assertTrue(c.select('25'))
            self.wait(lambda:not c.busy)
        self.assertEqual(c.active_key,'500')
        self.assertNotIn('25',c.variants)
        self.assertEqual(self.results,[])
        self.assertTrue(next(item for item in c.options if item['key']=='25')['available'])
        offered=c.selection_context()
        self.assertEqual(offered['resolution_key'],'25')
        self.assertEqual(offered['original_x'],self.query.x)
        self.assertEqual(offered['directions_path'],'/fine/d8.tif')
        offered['original_x']=-123
        self.assertEqual(c.selection_context()['original_x'],self.query.x)

    def test_manual_choice_applies_complete_variant_without_requerying_caumax_or_downloading(self):
        c=self.controller()
        self.discover(c)
        context=dict(resolution_key='25',dem_path='/fine/dem.tif',directions_path='/fine/d8.tif',
            original_x=self.query.x,original_y=self.query.y,search_radius_m=707.106781,
            reference_area_m2=self.base.area_m2)
        result=sample(25)
        payload=dict(analysis=result,directions_path=context['directions_path'],
                     dem_path=context['dem_path'],metadata={'manual_outlet_selected':True})
        with patch.object(rc,'_manual_analysis',return_value=payload) as manual:
            self.assertTrue(c.select_outlet(context,237.5,212.5))
            self.assertEqual(c.active_key,'500')
            self.wait(lambda:not c.busy)
        self.assertEqual(manual.call_args.args[1:3],(237.5,212.5))
        self.assertEqual(c.active_key,'25')
        self.assertIs(c.variants['25']['analysis'],result)
        self.assertEqual((self.results[-1][2].x,self.results[-1][2].y),
                         (self.query.x,self.query.y))
        self.assertEqual((self.results[-1][2].flow,self.results[-1][2].period),(177,100))
        self.assertEqual(self.downloads,[])
        self.assertEqual(self.analyses,[])
        self.assertIsNone(c.outlet_selection_context)

    def test_invalid_manual_cell_preserves_previous_result_and_allows_another_choice(self):
        c=self.controller()
        context=dict(resolution_key='500',dem_path='/original/mdt.tif',
            directions_path='/original/dir.tif',original_x=self.query.x,original_y=self.query.y,
            search_radius_m=707.106781,reference_area_m2=self.base.area_m2)
        with patch.object(rc,'_manual_analysis',side_effect=ValueError('Celda NoData')):
            self.assertTrue(c.select_outlet(context,250,250))
            self.wait(lambda:not c.busy)
        self.assertIs(c.variants['500']['analysis'],self.base)
        self.assertEqual(self.results,[])
        self.assertEqual(c.selection_context()['reason'],'Celda NoData')
        self.assertIn('NoData',self.errors[-1])

    def test_selection_keeps_native_point_flow_period_and_uses_cache(self):
        c=self.controller()
        self.discover(c)
        self.assertTrue(c.select('5'))
        self.assertEqual(c.active_key,'500')
        self.assertEqual(c.requested_key,'5')
        self.wait(lambda:not c.busy)
        self.assertEqual(c.active_key,'5')
        key,analysis,query=self.results[-1]
        self.assertEqual(key,'5')
        self.assertEqual((query.x,query.y,query.flow,query.period),
                         (self.query.x,self.query.y,177,100))
        self.assertEqual(query.crs,self.query.crs)
        self.assertEqual((self.analyses[-1][1],self.analyses[-1][2]),(230.2,245.3))
        self.assertTrue(query.dem_path.endswith('mdt.tif'))
        self.assertTrue(query.directions_path.endswith('directions.tif'))
        self.assertEqual(analysis.cell_width,5)
        c.variants['5']['smoothing_state']={'degree':15.5}
        count=len(self.downloads)
        c.select('500')
        c.select('5')
        self.assertEqual(len(self.downloads),count)
        self.assertEqual(c.variants['5']['smoothing_state'],{'degree':15.5})
        self.assertTrue(all(t is self.app.thread() for t in self.threads))

    def test_improved_variants_receive_explicit_original_area_and_bounded_nearest_channel_rules(self):
        c=self.controller()
        self.discover(c)
        c.select('25')
        self.wait(lambda:not c.busy)
        settings=self.snap_options[-1]
        self.assertTrue(settings['snap_enabled'])
        self.assertEqual(settings['reference_area_m2'],self.base.area_m2)
        self.assertEqual((settings['reference_cell_width'],settings['reference_cell_height']),(500,500))
        self.assertAlmostEqual(settings['snap_radius_m'],math.hypot(500,500))
        self.assertEqual(settings['snap_minimum_fraction'],.9)
        count=len(self.analyses)
        c.select('500')
        self.assertEqual(len(self.analyses),count)
        self.assertIs(self.results[-1][1],self.base)

    def test_adjusted_closure_does_not_overwrite_original_query_or_selected_point(self):
        c=self.controller()
        self.discover(c)
        normal=self.fake_analyze
        def snapped(*args,**kwargs):
            result=normal(*args,**kwargs)
            x,y=args[1:3]
            snap=dict(original_x=x,original_y=y,snapped_x=262.5,snapped_y=252.5,
                      distance_m=math.hypot(262.5-x,252.5-y),search_radius_m=math.hypot(500,500),
                      minimum_upstream_area_m2=.9*self.base.area_m2,
                      reference_area_m2=self.base.area_m2,candidate_upstream_area_m2=240000,
                      method='nearest_main_channel_d8',status='snapped')
            analysis=replace(result.analysis,
                             basin=replace(result.analysis.basin,outlet_x=262.5,outlet_y=252.5),
                             selected_x=x,selected_y=y,diagnostics={'outlet_snap':snap})
            return SimpleNamespace(analysis=analysis,directions_path=result.directions_path,
                                   dem_path=result.dem_path,metadata={})
        with patch.object(rc,'_analyze_dem',side_effect=snapped):
            c.select('5')
            self.wait(lambda:not c.busy)
        self.assertFalse(self.errors)
        _,analysis,query=self.results[-1]
        self.assertEqual((query.x,query.y,query.flow,query.period),(230.2,245.3,177,100))
        self.assertEqual((analysis.selected_x,analysis.selected_y),(230.2,245.3))
        self.assertEqual((analysis.outlet_x,analysis.outlet_y),(262.5,252.5))
        self.assertEqual(c.variants['5']['metadata']['outlet_snap'],analysis.diagnostics['outlet_snap'])

    def test_no_eligible_main_channel_is_a_visible_error_without_distant_or_unsnapped_fallback(self):
        c=self.controller()
        self.discover(c)
        with patch.object(rc,'_analyze_dem',side_effect=ValueError('No existe cauce principal dentro del radio autorizado')) as engine:
            c.select('5')
            self.wait(lambda:not c.busy)
        self.assertEqual(engine.call_count,1)
        self.assertEqual(len(self.downloads),1)
        self.assertEqual(c.active_key,'500')
        self.assertNotIn('5',c.variants)
        self.assertIn('radio autorizado',self.errors[-1])

    def test_exact_geographic_source_point_is_transformed_for_analysis_only(self):
        query=replace(self.query,x=-3.0,y=43.0,crs=QgsCoordinateReferenceSystem('EPSG:4326'))
        c=self.controller(query)
        self.discover(c)
        c.select('25')
        self.wait(lambda:not c.busy)
        self.assertFalse(self.errors)
        _,x,y,_=self.analyses[-1]
        self.assertAlmostEqual(x,500000,places=5)
        self.assertGreater(y,4700000)
        selected=self.results[-1][2]
        self.assertEqual((selected.x,selected.y),(-3,43))
        self.assertEqual(selected.crs.authid(),'EPSG:4326')

    def test_unavailable_resolution_does_not_download_or_replace_active_view(self):
        c=self.controller()
        self.discover(c)
        self.assertFalse(c.select('0.5'))
        self.assertEqual(c.active_key,'500')
        self.assertEqual(self.downloads,[])
        self.assertIn('Sin cobertura',self.errors[-1])

    def test_cancel_and_late_result_cannot_replace_the_new_selection(self):
        c=self.controller()
        self.discover(c)
        normal=self.fake_analyze
        def slow(*args,**kwargs):
            time.sleep(.12)
            return normal(*args,**kwargs)
        with patch.object(rc,'_analyze_dem',side_effect=slow):
            c.select('5')
            self.wait(lambda:bool(self.downloads))
            c.select('500')
            self.wait(lambda:not QgsApplication.taskManager().activeTasks())
        self.assertEqual(c.active_key,'500')
        self.assertEqual([r[0] for r in self.results],['500'])
        self.assertNotIn('5',c.variants)
        self.assertEqual(self.errors,[])

    def test_ensure_all_is_sequential_and_preserves_the_visible_selection(self):
        c=self.controller()
        self.discover(c)
        c.select('5')
        self.wait(lambda:not c.busy)
        callbacks=[]
        c.ensure_all(lambda variants:callbacks.append(variants))
        self.wait(lambda:bool(callbacks))
        self.assertEqual([item[0] for item in self.downloads],[5,25])
        self.assertEqual(c.active_key,'5')
        self.assertEqual([r[0] for r in self.results],['5'])
        self.assertEqual(set(callbacks[0]),{'500','25','5'})
        self.assertTrue(all(t is self.app.thread() for t in self.threads))

    def test_bulk_failure_reports_and_disables_only_the_failed_variant(self):
        c=self.controller()
        self.discover(c)
        normal=self.fake_download
        def fail_25(source,*args):
            if source.resolution_m==25:raise ValueError('Servidor no disponible')
            return normal(source,*args)
        done=[]
        with patch.object(rc,'_download_dem',side_effect=fail_25):
            c.ensure_all(lambda variants:done.append(variants))
            self.wait(lambda:bool(done))
        self.assertEqual(set(done[0]),{'500','5'})
        self.assertEqual(c.active_key,'500')
        self.assertFalse(next(o for o in c.options if o['key']=='25')['available'])
        self.assertIn('Servidor no disponible',self.errors[-1])

    def test_truncated_basin_retries_twice_and_retains_an_explicit_warning(self):
        c=self.controller()
        self.discover(c)
        normal=self.fake_analyze
        def bounded(*args,**kwargs):
            result=normal(*args,**kwargs)
            return SimpleNamespace(analysis=sample(5,True),directions_path=result.directions_path,
                                   dem_path=result.dem_path,metadata={})
        with patch.object(rc,'_analyze_dem',side_effect=bounded):
            c.select('5')
            self.wait(lambda:not c.busy)
        self.assertEqual(len(self.downloads),3)
        widths=[b[2]-b[0] for _,b,_ in self.downloads]
        self.assertAlmostEqual(widths[1]/widths[0],1.5)
        self.assertAlmostEqual(widths[2]/widths[1],1.5)
        self.assertTrue(any('dos ampliaciones' in note for note in c.variants['5']['analysis'].notes))
        self.assertEqual(c.variants['5']['metadata']['coverage_attempts'],3)

    def test_restore_requires_no_network_engine_or_source_rasters(self):
        c=self.controller()
        self.discover(c)
        done=[]
        c.ensure_all(lambda variants:done.append(variants))
        self.wait(lambda:bool(done))
        state={'active_key':'5','options':c.options,'variants':c.variants}
        restored=self.controller()
        with (patch.object(rc,'_discover_resolutions',side_effect=AssertionError('network')),
              patch.object(rc,'_download_dem',side_effect=AssertionError('download')),
              patch.object(rc,'_analyze_dem',side_effect=AssertionError('analysis'))):
            self.assertTrue(restored.restore(state))
            self.assertEqual(restored.active_key,'5')
            self.assertFalse(restored.busy)
            self.assertTrue(restored.select('25'))
            self.assertEqual(restored.active_key,'25')
        self.assertEqual(self.results[-1][0],'25')

    def test_engine_unavailable_keeps_original_enabled(self):
        c=self.controller()
        with patch.object(rc,'_engine_available',return_value={'available':False,'reason':'Falta GRASS'}):
            self.discover(c)
        self.assertTrue(c.options[0]['available'])
        self.assertTrue(all(not o['available'] for o in c.options[1:]))
        self.assertIn('Falta GRASS',next(o for o in c.options if o['key']=='5')['status'])

    def test_dispose_suppresses_all_late_callbacks(self):
        c=self.controller()
        def slow(*args):
            time.sleep(.08)
            return self.catalog
        with patch.object(rc,'_discover_resolutions',side_effect=slow):
            c.discover()
            c.dispose()
            count=(len(self.options_events),len(self.results),len(self.status),len(self.errors))
            self.wait(lambda:not QgsApplication.taskManager().activeTasks())
            self.assertEqual(count,(len(self.options_events),len(self.results),len(self.status),len(self.errors)))


if __name__=='__main__':unittest.main()
