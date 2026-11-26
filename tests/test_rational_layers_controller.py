"""Qt orchestration for rational-dialog products, with existing engines isolated."""
from dataclasses import dataclass, replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QCoreApplication, QEvent
from qgis.PyQt.QtWidgets import QWidget
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsPointXY, QgsProject)
from caumax_consulta import rational_layers_controller as module


@dataclass(frozen=True)
class Query:
    x: float
    y: float
    crs: object
    dem_path: str
    directions_path: str


def analysis(resolution=500):
    return SimpleNamespace(cell_width=resolution, cell_height=resolution,
        area_m2=54000000., selected_x=342324.8, selected_y=4709533.9)


class Manager:
    def __init__(self):
        self.tasks=[]

    def addTask(self, task):
        self.tasks.append(task)
        return len(self.tasks)


class Resolution(QObject):
    def __init__(self, base, query, options, result, status, error, parent=None):
        super().__init__(parent)
        self.base_analysis,self.base_query=base,query
        self.on_options,self.on_result,self.on_status,self.on_error=options,result,status,error
        self.active_key=format(base.cell_width,'.12g')
        self.variants={self.active_key:dict(analysis=base,query=query,smoothing_state=None)}
        self.options=[dict(key=key,resolution=float(key),available=True)
                      for key in ('500','25','5')]
        self.cache_dir=Path(query.dem_path).parent/'variants'
        self.progress=0.
        self.busy=False
        self.disposed=False
        self.discoveries=0
        self.selections=[]
        self.manual=[]
        self.outlet_selection_context=None
        self.pending=None

    def discover(self):
        self.discoveries+=1
        self.busy=True

    def discovered(self):
        self.busy=False
        self.on_options(self.options)
        self.on_status('Consulta de resoluciones terminada.')

    def select(self,key):
        self.selections.append(key)
        if key not in {option['key'] for option in self.options}:
            self.on_error('Resolución no disponible.')
            return False
        self.cancel()
        if key in self.variants:
            self.active_key=key
            variant=self.variants[key]
            self.on_result(key,variant['analysis'],variant['query'])
        else:
            self.pending=key
            self.busy=True
        return True

    def selected(self,key):
        self.busy=False
        self.pending=None
        self.active_key=key
        query=replace(self.base_query,dem_path=str(Path(self.base_query.dem_path).parent/f'mdt_{key}.tif'),
                      directions_path=str(Path(self.base_query.dem_path).parent/f'd8_{key}.tif'))
        # A true GeoTIFF permits end-to-end QGIS registration of the MDT.
        for path, kind in ((query.dem_path, 'dem'), (query.directions_path, 'd8')):
            dataset = gdal.GetDriverByName('GTiff').Create(
                path, 5, 5, 1, gdal.GDT_Float32 if kind == 'dem' else gdal.GDT_Byte)
            dataset.SetGeoTransform((342275., float(key), 0., 4710325., 0., -float(key)))
            dataset.SetProjection(query.crs.toWkt())
            dataset.GetRasterBand(1).Fill(125 if kind == 'dem' else 1)
            dataset = None
        item=dict(analysis=analysis(float(key)),query=query,smoothing_state=None)
        self.variants[key]=item
        self.on_result(key,item['analysis'],query)

    def ambiguous(self,key):
        self.busy=False
        self.outlet_selection_context=dict(resolution_key=key,dem_path='fine.tif',
            directions_path='fine-d8.tif',original_x=self.base_query.x,original_y=self.base_query.y,
            reason='Elige el cierre.')
        self.on_error('Dos ramales posibles.')

    def selection_context(self):
        return dict(self.outlet_selection_context)

    def select_outlet(self,context,x,y):
        self.manual.append((context,x,y))
        self.pending=context['resolution_key']
        self.busy=True
        self.outlet_selection_context=None
        return True

    def cancel(self):
        self.busy=False
        self.pending=None
        self.outlet_selection_context=None

    def dispose(self):
        self.cancel()
        self.disposed=True


class Drainage(QObject):
    def __init__(self,result,status,error,parent=None):
        super().__init__(parent)
        self.on_result,self.on_status,self.on_error=result,status,error
        self.busy=False
        self.disposed=False
        self.products={}
        self.calls=[]

    def prepare(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        self.busy=True
        self.on_status('Preparando TauDEM',10.)

    def complete(self):
        args,kwargs=self.calls[-1]
        key,source_analysis,query,reference,cache=args
        product=SimpleNamespace(analysis=source_analysis,dem_path=query.dem_path,
            caumax_d8_path=query.directions_path,tau_d8_path='native.tif',network_path='red.shp')
        state=dict(metadata={},features=[],automatic=kwargs['automatic'],
                   threshold_area_m2=kwargs['threshold_area_m2'] or 1000.)
        self.products[key]=product
        self.busy=False
        self.on_result(key,product,state)
        return product,state

    def cancel(self):
        self.busy=False

    def dispose(self):
        self.cancel()
        self.disposed=True


class RationalLayersControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rational_layers_')
        self.folder=Path(self.temp.name)
        self.dem,self.directions=self.folder/'mdt.tif',self.folder/'d8.tif'
        self.dem.write_bytes(b'dem')
        self.directions.write_bytes(b'd8')
        self.query=Query(342324.8,4709533.9,QgsCoordinateReferenceSystem('EPSG:25830'),
                         str(self.dem),str(self.directions))
        self.base=analysis()
        self.options,self.results,self.status,self.errors=[],[],[],[]
        self.manager=Manager()
        self.patches=[patch.object(module,'QgsApplication',SimpleNamespace(taskManager=lambda:self.manager)),
            patch.object(module,'ResolutionController',Resolution),
            patch.object(module,'DrainageController',Drainage),
            patch.object(module,'_analyze_base',return_value=self.base)]
        self.mocks=[patcher.start() for patcher in self.patches]
        self.controller=module.RationalLayersController(self.options.append,
            lambda *args:self.results.append(args),lambda *args:self.status.append(args),self.errors.append)

    def tearDown(self):
        self.controller.dispose()
        if not sip.isdeleted(self.controller):
            self.controller.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def base_ready(self):
        self.controller.set_context(self.query)
        task=self.manager.tasks[-1]
        task.finished(task.run())
        return self.controller.resolution

    def discovered(self):
        resolution=self.base_ready()
        resolution.discovered()
        return resolution

    def test_context_is_idempotent_and_no_generation_starts_before_user_prepare(self):
        c=self.controller
        self.assertTrue(c.set_context(self.query))
        self.assertTrue(c.busy)
        self.assertFalse(c.set_context(self.query))
        self.assertEqual(len(self.manager.tasks),1)
        task=self.manager.tasks[0]
        self.assertEqual(task.arguments,(str(self.directions),str(self.dem),self.query.crs.toWkt(),
                                         self.query.x,self.query.y))
        task.finished(task.run())
        task.finished(True)
        self.assertIs(c.base_analysis,self.base)
        self.assertEqual(c.resolution.discoveries,1)
        self.assertIsNone(c.drainage)
        self.assertEqual(c.active_key,'500')
        self.assertTrue(c.busy)
        c.resolution.discovered()
        self.assertFalse(c.busy)
        self.assertEqual([option['key'] for option in self.options[-1]],['500','25','5'])
        self.assertFalse(self.results)
        self.assertFalse(self.errors)
        self.assertFalse(hasattr(c.query,'flow'))

    def test_fine_selection_routes_to_existing_taudem_and_exposes_products(self):
        r=self.discovered();c=self.controller
        self.assertTrue(c.prepare('25',threshold_area_m2=4000.,automatic=False))
        self.assertTrue(c.busy)
        self.assertIsNone(c.drainage)
        r.selected('25')
        args,kwargs=c.drainage.calls[-1]
        self.assertEqual(args[0],'25')
        self.assertIs(args[1],r.variants['25']['analysis'])
        self.assertIs(args[3],self.base)
        self.assertEqual(args[4],r.cache_dir/'taudem'/'25')
        self.assertEqual(kwargs,dict(threshold_area_m2=4000.,automatic=False,encoding='caumax',bin_dir=None))
        product,state=c.drainage.complete()
        self.assertFalse(c.busy)
        self.assertIs(c.products,product)
        self.assertIs(c.state,state)
        self.assertEqual(self.results,[('25',product,state)])
        self.assertEqual(c.query,self.query)
        self.assertEqual(c.active_key,'25')
        self.assertIs(r.variants['25']['analysis'],product.analysis)

    def test_cached_variant_and_network_controller_are_reused_for_threshold_change(self):
        r=self.discovered();c=self.controller
        c.prepare('25');r.selected('25');first,state=c.drainage.complete()
        drainage=c.drainage
        c.prepare('25',threshold_area_m2=2500.,automatic=False)
        self.assertIs(c.drainage,drainage)
        self.assertEqual(r.selections,['25','25'])
        self.assertEqual(len(drainage.calls),2)
        self.assertEqual(drainage.calls[-1][1]['threshold_area_m2'],2500.)
        self.assertIn('25',drainage.products)
        self.assertIsNone(c.products)
        self.assertIsNone(c.state)
        drainage.complete()
        self.assertEqual(len(self.results),2)

    def test_context_change_disposes_both_children_and_rejects_old_callbacks(self):
        r=self.discovered();c=self.controller
        c.prepare('25');r.selected('25');d=c.drainage
        old_generation=c._generation
        self.assertTrue(c.set_context(replace(self.query,x=self.query.x+500)))
        self.assertTrue(r.disposed)
        self.assertTrue(d.disposed)
        self.assertIsNone(c.products)
        self.assertIsNone(c.state)
        self.assertIsNone(c.base_analysis)
        count=len(self.status)
        r.on_result('25',analysis(25),self.query)
        d.on_result('25',SimpleNamespace(),{})
        c._drainage_status(old_generation,'Obsoleto',90.)
        self.assertEqual(len(self.status),count)
        self.assertFalse(self.results)
        self.assertEqual(len(self.manager.tasks),2)

    def test_file_replacement_and_crs_changes_invalidate_the_context(self):
        c=self.controller
        c.set_context(self.query)
        self.dem.write_bytes(b'updated DEM data')
        self.assertTrue(c.set_context(self.query))
        self.assertTrue(self.manager.tasks[0].isCanceled())
        newer=replace(self.query,crs=QgsCoordinateReferenceSystem('EPSG:32630'))
        self.assertTrue(c.set_context(newer))
        self.assertEqual(len(self.manager.tasks),3)
        self.assertFalse(c.set_context(newer))

    def test_modified_source_during_base_task_discards_result_and_restarts(self):
        c=self.controller
        c.set_context(self.query)
        task=self.manager.tasks[-1]
        self.assertTrue(task.run())
        self.directions.write_bytes(b'replaced direction raster')
        task.finished(True)
        self.assertIsNone(c.resolution)
        self.assertIsNone(c.base_analysis)
        self.assertEqual(len(self.manager.tasks),2)

    def test_cancel_base_and_dispose_ignore_late_results(self):
        c=self.controller
        c.set_context(self.query)
        task=self.manager.tasks[0]
        c.cancel()
        task.result=self.base
        task.finished(True)
        self.assertIsNone(c.resolution)
        self.assertFalse(c.busy)
        c.set_context(replace(self.query,x=self.query.x+1))
        late=self.manager.tasks[-1]
        c.dispose()
        late.result=self.base
        late.finished(True)
        self.assertIsNone(c.resolution)
        self.assertFalse(c.busy)
        self.assertFalse(c.set_context(self.query))

    def test_ambiguous_resolution_preserves_prepare_until_manual_selection(self):
        r=self.discovered();c=self.controller
        c.prepare('25',threshold_area_m2=2500.,automatic=False)
        r.ambiguous('25')
        context=c.selection_context()
        self.assertEqual(context['resolution_key'],'25')
        self.assertFalse(c.busy)
        self.assertEqual(self.errors,['Dos ramales posibles.'])
        self.assertTrue(c.select_outlet(context,342300.,4709550.))
        self.assertEqual(r.manual[0][1:],(342300.,4709550.))
        self.assertNotIn('_rational_context_generation',r.manual[0][0])
        r.selected('25')
        self.assertEqual(c.drainage.calls[0][1]['threshold_area_m2'],2500.)
        c.drainage.complete()
        self.assertEqual(len(self.results),1)

    def test_stale_manual_context_cannot_target_another_point_or_prepare(self):
        r=self.discovered();c=self.controller
        c.prepare('25');r.ambiguous('25');old=c.selection_context()
        c.prepare('25');r.ambiguous('25')
        self.assertFalse(c.select_outlet(old,1.,2.))
        self.assertFalse(r.manual)
        self.assertIn('anterior',self.errors[-1])
        c.cancel()
        self.assertIsNone(c.selection_context())
        self.assertFalse(c.select_outlet(old,1.,2.))

    def test_nonselection_failure_does_not_continue_prepare(self):
        r=self.discovered();c=self.controller
        c.prepare('25')
        r.busy=False
        r.on_error('Descarga fallida.')
        self.assertIsNone(c._pending)
        self.assertIsNone(c.selection_context())
        r.on_result('25',analysis(25),self.query)
        self.assertIsNone(c.drainage)
        self.assertEqual(self.errors,['Descarga fallida.'])

    def test_only_valid_fine_resolutions_and_positive_thresholds_are_accepted(self):
        r=self.discovered();c=self.controller
        for key in ('500','1000','0','nan'):
            self.assertFalse(c.prepare(key))
        for threshold in (0,-1,float('nan')):
            self.assertFalse(c.prepare('25',threshold_area_m2=threshold))
        self.assertEqual(r.selections,[])
        self.assertFalse(self.results)

    def test_parent_destruction_cancels_all_work(self):
        parent=QWidget()
        c=module.RationalLayersController(parent=parent)
        c.set_context(self.query)
        task=self.manager.tasks[-1]
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(parent,QEvent.DeferredDelete)
        self.assertTrue(c._disposed)
        self.assertTrue(task.isCanceled())
        task.result=self.base
        task.finished(True)
        self.assertIsNone(c.resolution)

    def test_base_worker_uses_direction_crs_and_exact_selected_point(self):
        # Exercise the real coordinate conversion while only replacing analysis.
        self.patches[-1].stop()
        source=QgsCoordinateReferenceSystem('EPSG:4326')
        target=QgsCoordinateReferenceSystem('EPSG:25830')
        for path in (self.dem,self.directions):
            dataset=gdal.GetDriverByName('GTiff').Create(str(path),2,2,1,gdal.GDT_Float32)
            dataset.SetProjection(target.toWkt())
            dataset.SetGeoTransform((400000,500,0,4500000,0,-500))
            dataset.GetRasterBand(1).Fill(1)
            dataset=None
        expected=QgsCoordinateTransform(source,target,QgsProject.instance()).transform(QgsPointXY(-3.7,40.4))
        with patch('caumax_consulta.terrain.analyze_basin',return_value=self.base) as analyze:
            result=module._analyze_base((str(self.directions),str(self.dem),source.toWkt(),-3.7,40.4),
                                       lambda:False,lambda *args:None)
        self.assertIs(result,self.base)
        arguments=analyze.call_args.args
        self.assertEqual(arguments[:2],(str(self.directions),str(self.dem)))
        self.assertAlmostEqual(arguments[2],expected.x(),places=5)
        self.assertAlmostEqual(arguments[3],expected.y(),places=5)
        self.assertEqual(analyze.call_args.kwargs['upstream_only'],False)


if __name__=='__main__':
    unittest.main()
