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
import numpy as np
from osgeo import gdal,ogr,osr
from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication,QgsCoordinateReferenceSystem,QgsGeometry

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from caumax_consulta.basin import BasinResult
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.terrain import Analysis
from caumax_consulta import resolution_controller as rc
from caumax_consulta import dem_hydrology as hydro


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

    def test_live_rational_query_without_snapshot_prepares_fine_resolutions(self):
        # The native rational dialog supplies paths and a point, not the
        # portable rational_context used by saved basin projects.
        live=SimpleNamespace(x=self.query.x,y=self.query.y,crs=self.query.crs,
            source_kind='rational',dem_path=self.query.dem_path,
            directions_path=self.query.directions_path)
        self.assertEqual(rc._reference_area_m2(self.base,live),self.base.area_m2)
        frozen=SimpleNamespace(source_kind='rational',
            rational_context={'area_m2':self.base.area_m2+500.})
        self.assertEqual(rc._reference_area_m2(self.base,frozen),
            self.base.area_m2+500.)
        controller=self.controller(query=live)
        self.assertEqual(controller.reference_area_m2,self.base.area_m2)
        self.discover(controller)
        self.assertTrue(next(item for item in controller.options
            if item['key']=='25')['available'])

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
        self.assertEqual(offered['manual_selection_scope'],'raster_extent')
        offered['original_x']=-123
        self.assertEqual(c.selection_context()['original_x'],self.query.x)

    def test_discovery_preserves_deferred_initial_outlet_review_but_cancel_discards_it(self):
        c=self.controller()
        context=dict(resolution_key='500',dem_path='/original/mdt.tif',
            directions_path='/original/dir.tif',original_x=self.query.x,original_y=self.query.y,
            search_radius_m=707.106781,reference_area_m2=self.base.area_m2,
            reason='Dos ramales de entrada; elige el cierre.')
        c.outlet_selection_context=context
        self.discover(c)
        self.assertEqual(c.outlet_selection_context,context)
        self.assertIsNot(c.outlet_selection_context,context)
        self.assertEqual(c.selection_context()['manual_selection_scope'],'raster_extent')
        self.assertEqual(c.active_key,'500')
        self.assertEqual(self.results,[])
        c.cancel()
        self.assertIsNone(c.outlet_selection_context)

    def test_restored_manual_500_keeps_original_reference_in_context_and_finer_builds(self):
        reference=2250000.0
        self.base=replace(self.base,diagnostics={
            'outlet_snap':dict(method='manual_cell_d8',selection_mode='user_selected_cell',
                               explicit_user_choice=True,reference_area_m2=reference),
            'analysis_surface':dict(original_area_m2=200000.0)})
        c=self.controller()
        self.assertEqual(c.reference_area_m2,reference)
        self.discover(c)
        snapshot=dict(active_key='500',options=[c.options[0]],variants=dict(c.variants))
        self.assertTrue(c.restore(snapshot))
        with patch.object(rc,'_dem_point',return_value=(self.query.x,self.query.y)):
            self.assertEqual(c.selection_context()['reference_area_m2'],reference)
        self.discover(c)
        self.assertTrue(c.select('25'))
        self.wait(lambda:not c.busy)
        self.assertEqual(self.snap_options[-1]['reference_area_m2'],reference)
        automatic=replace(self.base,diagnostics={
            'outlet_snap':dict(selection_mode='upstream_nearest_branch',reference_area_m2=reference),
            'analysis_surface':dict(original_area_m2=200000.0)})
        self.assertEqual(rc._reference_area_m2(automatic),200000.0)

    def test_restored_distant_manual_500_download_bounds_also_cover_original_query(self):
        c=self.controller()
        polygon=QgsGeometry.fromWkt('MULTIPOLYGON (((10000 10000,10500 10000,10500 10500,10000 10500,10000 10000)))')
        manual=replace(self.base,basin=replace(self.base.basin,
            geometry_wkb=bytes(polygon.asWkb()),outlet_x=10250,outlet_y=10250),
            diagnostics={'outlet_snap':dict(method='manual_cell_d8',
                selection_mode='user_selected_cell',explicit_user_choice=True,
                reference_area_m2=self.base.area_m2)})
        snapshot=dict(active_key='500',options=[c.options[0]],variants={
            '500':dict(analysis=manual,query=self.query,smoothing_state=None)})
        self.assertTrue(c.restore(snapshot))
        self.assertEqual(c.bounds,(self.base.selected_x-2000,self.base.selected_y-2000,12500.,12500.))
        self.assertLess(c.bounds[0],self.base.selected_x)
        self.assertLess(c.bounds[1],self.base.selected_y)
        self.assertGreater(c.bounds[2],manual.outlet_x)
        self.assertGreater(c.bounds[3],manual.outlet_y)

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
        c.select('500')
        c.select('25')
        self.assertIs(c.variants['25']['analysis'],result)
        ready=[]
        c.ensure_all(lambda variants:ready.append(variants))
        self.wait(lambda:bool(ready))
        self.assertIs(ready[0]['25']['analysis'],result)
        self.assertEqual([entry[0] for entry in self.downloads],[5])
        self.assertEqual(manual.call_count,1)

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

    def test_missing_previously_verified_d8_cache_forces_rebuild(self):
        # Only a grid that physically existed when generated may later be
        # invalidated. Restored/manual fixtures without such files survive.
        c = self.controller()
        self.discover(c)
        def generated_with_d8(path, x, y, cache_dir, cancelled, progress, **kwargs):
            payload = self.fake_analyze(path, x, y, cache_dir,
                                        cancelled, progress, **kwargs)
            directions = Path(payload.directions_path)
            directions.parent.mkdir(parents=True, exist_ok=True)
            directions.write_bytes(b'synthetic D8 fixture')
            return payload
        with patch.object(rc, '_analyze_dem', side_effect=generated_with_d8):
            self.assertTrue(c.select('25'))
            self.wait(lambda: not c.busy)
            self.assertIn('25', c._verified_cache_sources)
            old_analysis = c.variants['25']['analysis']
            source = Path(c.variants['25']['query'].directions_path)
            source.unlink()
            self.assertTrue(c.select('25'))
            self.wait(lambda: not c.busy)
            self.assertIsNot(c.variants['25']['analysis'], old_analysis)
            self.assertTrue(source.is_file())
            self.assertIn('25', c._verified_cache_sources)

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

    def test_improved_variants_keep_reference_data_without_enabling_channel_snap(self):
        c=self.controller()
        self.discover(c)
        c.select('25')
        self.wait(lambda:not c.busy)
        settings=self.snap_options[-1]
        self.assertFalse(settings['snap_enabled'])
        self.assertEqual(settings['reference_area_m2'],self.base.area_m2)
        self.assertEqual((settings['reference_cell_width'],settings['reference_cell_height']),(500,500))
        self.assertAlmostEqual(settings['snap_radius_m'],math.hypot(500,500))
        self.assertEqual(settings['snap_minimum_fraction'],.9)
        self.assertNotIn('selection_policy',settings['refinement_reference'])
        count=len(self.analyses)
        c.select('500')
        self.assertEqual(len(self.analyses),count)
        self.assertIs(self.results[-1][1],self.base)

    def test_nearest_reference_policy_is_rational_only_for_new_and_restored_variants(self):
        for source_kind in ('caumax_raster','rational'):
            with self.subTest(source_kind=source_kind):
                query=replace(self.query,source_kind=source_kind,
                    rational_context=({'area_m2':self.base.area_m2,
                        'geotransform':[0,500,0,500,0,-500]} if source_kind=='rational' else None))
                controller=self.controller(query)
                self.discover(controller)
                self.assertTrue(controller.select('25'))
                self.wait(lambda:not controller.busy)
                expected='nearest_reference_chain_cell' if source_kind=='rational' else None
                self.assertEqual(self.snap_options[-1]['refinement_reference'].get('selection_policy'),expected)
                variant=controller.variants['25']
                # The fake analysis engine only needs this availability marker;
                # real D8 correspondence is covered by the refinement tests.
                Path(variant['query'].directions_path).parent.mkdir(parents=True,exist_ok=True)
                Path(variant['query'].directions_path).touch()
                state=dict(active_key='25',
                    options=[item for item in controller.options if item['key'] in ('500','25')],
                    variants={key:controller.variants[key] for key in ('500','25')})
                restored=self.controller(query)
                self.assertTrue(restored.restore(state))
                self.assertTrue(restored.needs_reference_check('25'))
                downloads=len(self.downloads)
                self.assertTrue(restored.select('25'))
                self.wait(lambda:not restored.busy)
                self.assertEqual(len(self.downloads),downloads)
                self.assertEqual(self.snap_options[-1]['refinement_reference'].get('selection_policy'),expected)
                self.assertFalse(self.errors)

    def test_real_native_variants_and_bulk_save_cache_verify_the_matching_clicked_cell(self):
        c=self.controller()
        self.discover(c)
        expected={}
        expected_counts={}
        def prepared_download(source,bounds,crs,destination,cancelled,progress):
            resolution=source.resolution_m
            path=Path(destination)
            path.parent.mkdir(parents=True,exist_ok=True)
            self.downloads.append((resolution,tuple(bounds),str(path)))
            size=round(500/resolution)
            transform=(-resolution,resolution,0,500+resolution,0,-resolution)
            outlet_col=math.floor(self.query.x/resolution)+1
            outlet_row=math.floor((500-self.query.y)/resolution)+1
            codes=np.full((size+2,size+2),255,dtype=np.uint8)
            codes[1:size+1,1:outlet_col]=1
            codes[1:size+1,outlet_col]=4
            codes[1:size+1,outlet_col+1:size+1]=16
            codes[size,outlet_col]=0
            row,col=np.indices(codes.shape)
            dem=gdal.GetDriverByName('GTiff').Create(str(path),size+2,size+2,1,gdal.GDT_Float64)
            dem.SetGeoTransform(transform)
            dem.SetProjection(source.source_crs_wkt)
            dem.GetRasterBand(1).SetNoDataValue(-9999)
            dem.GetRasterBand(1).WriteArray(np.where(codes==255,-9999,1000-row+abs(col-12)))
            dem=None
            dem=gdal.Open(str(path))
            source_key=hydro._source_key(dem,path)
            dem=None
            cache=path.parent/'hydrology'
            cache.mkdir()
            directions=gdal.GetDriverByName('GTiff').Create(
                str(cache/('d8_'+source_key[:32]+'.tif')),size+2,size+2,1,gdal.GDT_Byte)
            directions.SetGeoTransform(transform)
            directions.SetProjection(source.source_crs_wkt)
            directions.GetRasterBand(1).SetNoDataValue(255)
            directions.GetRasterBand(1).WriteArray(codes)
            directions.SetMetadata({'SOURCE_KEY':source_key,'HYDRO_VERSION':hydro._VERSION})
            directions=None
            expected[rc._key(resolution)]=tuple(gdal.ApplyGeoTransform(transform,outlet_col+.5,outlet_row+.5))
            expected_counts[rc._key(resolution)]=outlet_row*size
            progress(100)
            return path
        ready=[]
        with (patch.object(rc,'_download_dem',side_effect=prepared_download),
              patch.object(rc,'_analyze_dem',side_effect=hydro.analyze_dem) as calculate,
              patch.object(hydro,'_run_grass',side_effect=AssertionError('Prepared native D8')),
              patch.object(hydro,'_nearest_main_channel',side_effect=AssertionError('No automatic snap'))):
            c.select('5')
            self.wait(lambda:not c.busy)
            self.assertFalse(self.errors)
            first=c.variants['5']['analysis']
            c.select('500')
            c.select('5')
            self.assertIs(c.variants['5']['analysis'],first)
            self.assertEqual(calculate.call_count,1)
            c.ensure_all(lambda variants:ready.append(variants))
            self.wait(lambda:bool(ready))
            self.assertEqual(calculate.call_count,2)
            self.assertTrue(all(call.kwargs['snap_enabled'] is False for call in calculate.call_args_list))
        self.assertFalse(self.errors)
        self.assertEqual(c.active_key,'5')
        self.assertEqual([entry[0] for entry in self.downloads],[5,25])
        self.assertEqual(set(ready[0]),{'500','25','5'})
        for key in ('5','25'):
            analysis=ready[0][key]['analysis']
            query=ready[0][key]['query']
            self.assertEqual((query.x,query.y,query.flow,query.period),(230.2,245.3,177,100))
            self.assertEqual((analysis.selected_x,analysis.selected_y),(230.2,245.3))
            self.assertEqual((analysis.outlet_x,analysis.outlet_y),expected[key])
            self.assertNotEqual(expected[key],(230.2,245.3))
            self.assertEqual(analysis.cell_count,expected_counts[key])
            self.assertEqual(analysis.area_m2,expected_counts[key]*float(key)**2)
            course=ogr.CreateGeometryFromWkb(analysis.main_course_wkb)
            self.assertEqual(course.GetPoint_2D(0),expected[key])
            self.assertEqual(analysis.diagnostics['outlet_snap']['method'],'reference_basin_d8')
            self.assertEqual(analysis.diagnostics['outlet_snap']['status'],'unchanged')
            self.assertGreater(analysis.diagnostics['outlet_snap']['reference_coverage_fraction'],.5)
            self.assertEqual(ready[0][key]['metadata']['outlet_snap'],analysis.diagnostics['outlet_snap'])
            self.assertTrue(ready[0][key]['metadata']['cache_reused'])
            self.assertFalse(ready[0][key]['metadata']['outlet_snapped_to_channel'])

    def test_manual_coarse_change_rebuilds_current_run_automatic_variant_with_actual_bounds(self):
        c=self.controller()
        self.discover(c)
        c.select('25')
        self.wait(lambda:not c.busy)
        first=c.variants['25']['analysis']
        polygon=QgsGeometry.fromWkt('MULTIPOLYGON (((10000 10000,10500 10000,10500 10500,10000 10500,10000 10000)))')
        manual=replace(self.base,basin=replace(self.base.basin,
            geometry_wkb=bytes(polygon.asWkb()),outlet_x=10250,outlet_y=10250))
        context=dict(resolution_key='500',dem_path=self.query.dem_path,directions_path=self.query.directions_path)
        payload=dict(analysis=manual,dem_path=self.query.dem_path,
                     directions_path=self.query.directions_path,metadata={'manual_outlet_selected':True})
        with patch.object(rc,'_manual_analysis',return_value=payload):
            c.select_outlet(context,10250,10250)
            self.wait(lambda:not c.busy)
        self.assertIs(c.base_analysis,self.base)
        self.assertTrue(c._needs_reference_rebuild('25'))
        c.select('25')
        self.wait(lambda:not c.busy)
        self.assertIsNot(c.variants['25']['analysis'],first)
        reference=self.snap_options[-1]['refinement_reference']
        self.assertEqual(reference['geometry_wkb'],manual.geometry_wkb)
        self.assertEqual(self.downloads[-1][1],rc._buffered_bounds(manual))
        self.assertGreater(self.downloads[-1][1][2],manual.outlet_x)
        self.assertFalse(c._needs_reference_rebuild('25'))
        # A later explicit fine choice must survive another coarse change.
        context['resolution_key']='25'
        payload['analysis']=sample(25)
        with patch.object(rc,'_manual_analysis',return_value=payload):
            c.select_outlet(context,237.5,237.5)
            self.wait(lambda:not c.busy)
        explicit=c.variants['25']['analysis']
        c.variants['500']['analysis']=self.base
        before=len(self.downloads)
        c.select('25')
        self.assertIs(c.variants['25']['analysis'],explicit)
        self.assertEqual(len(self.downloads),before)

    def test_smoothed_reference_uses_native_base_or_requires_explicit_review(self):
        c=self.controller()
        self.discover(c)
        smoothed=replace(self.base,diagnostics={'analysis_surface':{'kind':'smoothed'}})
        c.variants['500'].update(analysis=smoothed,smoothing_state={'base_geometry_wkb':self.base.geometry_wkb})
        c.select('25')
        self.wait(lambda:not c.busy)
        self.assertEqual(self.snap_options[-1]['refinement_reference']['geometry_wkb'],self.base.geometry_wkb)
        c.variants['500']['smoothing_state']=None
        c.select('5')
        self.wait(lambda:not c.busy)
        self.assertIsNone(self.snap_options[-1]['refinement_reference']['geometry_wkb'])

    def test_invalid_clicked_direction_is_a_visible_error_without_a_neighbouring_fallback(self):
        c=self.controller()
        self.discover(c)
        with patch.object(rc,'_analyze_dem',side_effect=ValueError('La celda seleccionada no tiene dirección D8 válida (NoData)')) as engine:
            c.select('5')
            self.wait(lambda:not c.busy)
        self.assertEqual(engine.call_count,1)
        self.assertEqual(len(self.downloads),1)
        self.assertEqual(c.active_key,'500')
        self.assertNotIn('5',c.variants)
        self.assertIn('NoData',self.errors[-1])

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

    def test_reference_outside_download_retries_coverage_before_requiring_manual_choice(self):
        c=self.controller()
        self.discover(c)
        normal=self.fake_analyze
        attempts=[]
        def wider_reference(*args,**kwargs):
            attempts.append(args[0])
            if len(attempts)<3:
                error=ValueError('El MDT no cubre la referencia')
                error.selection_context={'reason_code':'reference_outside_dem'}
                raise error
            return normal(*args,**kwargs)
        with patch.object(rc,'_analyze_dem',side_effect=wider_reference):
            c.select('25')
            self.wait(lambda:not c.busy)
        self.assertFalse(self.errors)
        self.assertEqual(len(self.downloads),3)
        self.assertEqual(c.variants['25']['metadata']['coverage_attempts'],3)
        widths=[bounds[2]-bounds[0] for _,bounds,_ in self.downloads]
        self.assertAlmostEqual(widths[1]/widths[0],1.5)
        self.assertAlmostEqual(widths[2]/widths[1],1.5)

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
            self.assertTrue(restored.needs_reference_check('25'))
            self.assertFalse(restored.select('25'))
            self.assertEqual(restored.active_key,'5')
        self.assertEqual(self.results[-1][0],'5')
        self.assertIn('rásteres no están disponibles',self.errors[-1])

    def test_restore_preserves_an_older_explicitly_saved_automatic_snap(self):
        c=self.controller()
        legacy=replace(sample(5),basin=replace(sample(5).basin,outlet_x=262.5,outlet_y=252.5),
            diagnostics={'outlet_snap':dict(method='upstream_section_d8',
                selection_mode='upstream_lateral_nearest_branch',snapped_x=262.5,snapped_y=252.5)})
        state=dict(active_key='5',options=[c.options[0],dict(key='5',resolution=5,available=True)],
            variants={'500':c.variants['500'],'5':dict(analysis=legacy,query=self.query,
                smoothing_state=None,metadata={})})
        with (patch.object(rc,'_download_dem',side_effect=AssertionError('No download on restore')),
              patch.object(rc,'_analyze_dem',side_effect=AssertionError('No reinterpretation on restore'))):
            self.assertTrue(c.restore(state))
            self.assertTrue(c.select('500'))
            self.assertTrue(c.needs_reference_check('5'))
            self.assertFalse(c.select('5'))
            self.assertEqual(c.active_key,'500')
        restored=c.variants['5']['analysis']
        self.assertIs(restored,legacy)
        self.assertEqual((restored.outlet_x,restored.outlet_y),(262.5,252.5))
        self.assertEqual(restored.diagnostics,legacy.diagnostics)

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
