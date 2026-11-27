"""Exact native DFS equivalence and reversible, owned-product-only hooks."""
import ast
from collections import defaultdict
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import types
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from osgeo import gdal, ogr, osr
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog, QLineEdit, QMainWindow, QPushButton
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY

ROOT=Path(__file__).resolve().parents[1]
NATIVE_SOURCE=Path(os.environ.get('HIDROCAUMAX_CAUMAX_SOURCE',str(ROOT.parent/'inputs/caumax/caumax')))
sys.path.insert(0,str(ROOT/'src'))
from caumax_consulta.rational_inputs import (RationalInputsBridge, _process_cell_iterative,
    _fine_grid_issue, _fine_failure_message, FineGridError)


def native_class():
    """Compile the actual native class with isolated dependencies, not its settings."""
    source=NATIVE_SOURCE/'basincalculator.py'
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BasinCalculator')
    namespace=dict(gdal=gdal,ogr=ogr,np=np,statistics=statistics,math=math,
                   defaultdict=defaultdict,QgsGeometry=QgsGeometry,P0='P0',I1ID='i1id')
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['BasinCalculator']


NATIVE = native_class() if (NATIVE_SOURCE/'basincalculator.py').is_file() else None


def calculator(dem,dirs,resolution=25):
    result=NATIVE.__new__(NATIVE)
    result.mdt=dem.copy();result.dirs=dirs.copy();result.nodataMdt=-9999;result.nodataDirs=255
    result.cellsize=resolution;result.cellarea=resolution**2
    result.geoTransform=(0,resolution,0,dem.shape[0]*resolution,0,-resolution)
    srs=osr.SpatialReference();srs.ImportFromEPSG(25830);result.crs=srs.ExportToWkt()
    result.secondaryLayers={key:np.full(dem.shape,value,np.float32)
                            for key,value in [('P0',12),('i1id',9),(5,50)]}
    result.secondaryTransforms={key:result.geoTransform for key in result.secondaryLayers}
    result.secondaryNodata={key:-9999 for key in result.secondaryLayers}
    result.rainFiles={5:'unused'};result.xMaxDistance=None;result.yMaxDistance=None
    result._resetValues()
    return result


def point(calc,row,col):
    return QgsPointXY(*gdal.ApplyGeoTransform(calc.geoTransform,col+.5,row+.5))


@unittest.skipUnless(NATIVE is not None, 'Fuente nativa CAUMAX basincalculator.py no disponible')
class IterativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis()

    def test_exact_native_statistics_preorder_ties_and_polygon_on_small_golden(self):
        rows,cols=np.indices((7,7));dem=(100+abs(rows-3)+abs(cols-3)*.5).astype(np.float32)
        dirs=np.zeros((7,7),np.uint8)
        for dx,dy,code in NATIVE.directions:dirs[3+dy,3+dx]=code
        original=calculator(dem,dirs);adapted=calculator(dem,dirs)
        # Different sample values establish equality of native accumulation order.
        for calc in (original,adapted):
            calc.secondaryLayers['P0']=np.arange(49,dtype=np.float32).reshape(7,7)
            calc.secondaryLayers['P0'][2,2]=-9999
        adapted._processCell=types.MethodType(_process_cell_iterative,adapted)
        for calc in (original,adapted):calc.calculate(point(calc,3,3))
        for name in ('area','maxDistance','maxH','minH','concentrationTime','p0','i1id',
                     'rain','xMaxDistance','yMaxDistance','p0Values','i1idValues','rainValues'):
            self.assertEqual(getattr(adapted,name),getattr(original,name),name)
        np.testing.assert_array_equal(adapted.basinCells,original.basinCells)
        self.assertEqual([bytes(g.asWkb()) for g in adapted.basinGeometry],
                         [bytes(g.asWkb()) for g in original.basinGeometry])

    def test_1501_cell_five_metre_course_does_not_change_recursion_limit(self):
        size=1503;dem=np.repeat((100+(1501-np.arange(size))*.1)[:,None],3,axis=1)
        dirs=np.zeros((size,3),np.uint8);dirs[1:1502,1]=4
        native=calculator(dem,dirs,5)
        limit=sys.getrecursionlimit()
        with self.assertRaises(RecursionError):native.calculate(point(native,1501,1))
        adapted=calculator(dem,dirs,5)
        adapted._processCell=types.MethodType(_process_cell_iterative,adapted)
        adapted.calculate(point(adapted,1501,1))
        self.assertEqual(int(adapted.basinCells.sum()),1501)
        self.assertEqual(adapted.area,1501*25)
        self.assertEqual(adapted.maxDistance,7500)
        self.assertGreater(adapted.concentrationTime,0)
        self.assertEqual(sys.getrecursionlimit(),limit)
        # Native calculate still resets the visited mask on repeated queries.
        adapted.calculate(point(adapted,1501,1))
        self.assertEqual(adapted.area,1501*25)

    def test_bounds_and_cycle_guards_visit_each_cell_once(self):
        dem=np.array([[100.,101.],[102.,103.]])
        dirs=np.array([[1,16],[0,0]],np.uint8)
        adapted=calculator(dem,dirs)
        adapted._processCell=types.MethodType(_process_cell_iterative,adapted)
        adapted.calculate(point(adapted,0,0))
        self.assertEqual(adapted.area,2*625)
        self.assertEqual(int(adapted.basinCells.sum()),2)


class FineGridProtectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rational_fine_grid_')
        self.folder = Path(self.temp.name)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        self.crs = srs.ExportToWkt()

    def tearDown(self):
        self.temp.cleanup()

    def d8(self, size, transform=None, shape=(5, 5), encoding='ESRI_D8'):
        filename = self.folder / f'd8_{size}_{len(list(self.folder.iterdir()))}.tif'
        ds = gdal.GetDriverByName('GTiff').Create(
            str(filename), shape[1], shape[0], 1, gdal.GDT_Byte)
        ds.SetGeoTransform(transform or (342000, size, 0, 4720000, 0, -size))
        ds.SetProjection(self.crs)
        ds.SetMetadataItem('FLOW_DIRECTION_ENCODING', encoding)
        ds.GetRasterBand(1).SetNoDataValue(255)
        ds.GetRasterBand(1).Fill(1)
        ds = None
        return filename

    def calc(self, size, directions_shape=(5, 5)):
        return types.SimpleNamespace(
            cellsize=size,
            geoTransform=(342000, size, 0, 4720000, 0, -size),
            crs=self.crs,
            mdt=np.full((7, 7), 300., dtype=np.float32),
            dirs=np.full(directions_shape, 1, dtype=np.uint8),
            maxDistance=0.,
            maxH=300.,
            minH=300.,
        )

    def test_both_fine_sizes_reject_zero_length_and_no_positive_relief(self):
        for size in (25, 5):
            with self.subTest(size=size):
                calc = self.calc(size)
                message = _fine_failure_message(calc, ZeroDivisionError())
                self.assertIn('L=0', message)
                self.assertIn(f'{size:g} m', message)
                calc.maxDistance = size * 4.
                self.assertIn('desnivel positivo',
                              _fine_failure_message(calc, ZeroDivisionError()))
        self.assertIsNone(_fine_failure_message(self.calc(500), ZeroDivisionError()))

    def test_grid_origin_and_encoding_are_checked_for_5_and_25m(self):
        for size in (25, 5):
            with self.subTest(size=size):
                calc = self.calc(size)
                valid = self.d8(size)
                self.assertIsNone(_fine_grid_issue(calc, valid))
                shifted = self.d8(size, transform=(342000+size, size, 0,
                                                   4720000, 0, -size))
                self.assertIn('no comparten origen',
                              _fine_grid_issue(calc, shifted))
                tau = self.d8(size, encoding='TAUDEM_D8')
                self.assertIn('sin recodificar', _fine_grid_issue(calc, tau))

    def test_directions_smaller_than_mdt_do_not_read_outside_array(self):
        # Different extents sharing a grid are supported by 1.0.16.
        for size in (25, 5):
            with self.subTest(size=size):
                calc = self.calc(size, directions_shape=(5, 5))
                calc.mdt = np.full((7, 7), 100., dtype=np.float32)
                calc.basinCells = np.zeros((7, 7), dtype=np.uint8)
                calc.p0Values = []
                calc.i1idValues = []
                calc.rainFiles = {}
                calc.rainValues = {}
                calc.area = 0
                calc.cellarea = size * size
                calc.xMaxDistance = calc.yMaxDistance = None
                # One downstream cell and a connected neighbour in the
                # 5x5 direction raster, with the 7x7 elevation data untouched.
                calc.directions = [(-1, 0, 1)]
                calc.dirs.fill(255)
                calc.dirs[1, 1] = 1
                calc.getValueAtCoordinate = lambda *args: None
                _process_cell_iterative(calc, 2, 1)
                self.assertEqual(int(calc.basinCells.sum()), 2)
                self.assertEqual(calc.maxDistance, size)
                self.assertEqual(calc.area, size * size * 2)


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis();cls.window=QMainWindow()
        cls.iface=types.SimpleNamespace(mainWindow=lambda:cls.window)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'directions.tif'
        self.write_tags('FLOW_DIRECTION','ESRI_D8')
        module=types.ModuleType('_rational_input_test_calculator')
        module.getLayerPath=lambda name:str(self.path);module.FLOWDIRS='FLOWDIRS'
        self.instances=[]
        owner=self
        class Calculator:
            def __init__(self):owner.instances.append(self)
            def _processCell(self,*args):return 'original'
        Calculator.__module__=module.__name__
        self.Calculator=Calculator;sys.modules[module.__name__]=module
        namespace={'BasinCalculator':Calculator}
        exec('def calculateParameters(self):\n'
             '    self.btnCalculate.setEnabled(False)\n'
             '    if self.basinCalc is None:\n'
             '        self.basinCalc=BasinCalculator()\n'
             '    self.btnCalculate.setEnabled(True)\n'
             '    return self.basinCalc\n',namespace)
        def waitcursor(method):
            def wrapped(*args,**kwargs):return method(*args,**kwargs)
            return wrapped
        class RationalMethodDialog(QDialog):
            def __init__(self,parent):
                super().__init__(parent);self.basinCalc=None
                self.btnCalculate=QPushButton(self)
                self.txtResultFlow=QLineEdit('27',self)
                self.txtResultReturnPeriod=QLineEdit('100',self)
                self.flow_behavior='accept'

            def getFlow(self, runoff, intensity):
                return 10.346

            def _getInterpolatedFlow(self, period):
                return 12.346

            def calculate(self):
                if self.flow_behavior=='abort':
                    return
                if self.flow_behavior=='raise':
                    raise ValueError('native failure')
                if self.flow_behavior=='direct':
                    flow = self.getFlow(.5, 10.)
                    self.txtResultReturnPeriod.setText('100')
                    self.txtResultFlow.setText(str(round(flow)))
                    return 'native accepted'
                if self.flow_behavior=='interpolated':
                    for ignored in range(6):
                        self.getFlow(.5, 10.)
                    flow = self._getInterpolatedFlow(60)
                    self.txtResultReturnPeriod.setText('60')
                    self.txtResultFlow.setText(str(round(flow)))
                    return 'native accepted'
                self.txtResultReturnPeriod.setText('100')
                self.txtResultFlow.setText('27')
                return 'native accepted'
        RationalMethodDialog.__module__='_test.rationalmethoddialog'
        RationalMethodDialog.calculateParameters=waitcursor(namespace['calculateParameters'])
        self.dialog=RationalMethodDialog(self.window)
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen);self.dialog.show()
        self.bridge=RationalInputsBridge(self.iface)

    def tearDown(self):
        self.bridge.stop();self.dialog.close();self.temp.cleanup()
        sys.modules.pop('_rational_input_test_calculator',None)

    def write_tags(self,product,encoding):
        ds=gdal.GetDriverByName('GTiff').Create(str(self.path),3,3,1,gdal.GDT_Byte)
        if product:ds.SetMetadataItem('HIDROCAUMAX_PRODUCT',product)
        if encoding:ds.SetMetadataItem('FLOW_DIRECTION_ENCODING',encoding)
        ds=None

    def test_tagged_factory_creates_once_and_unload_restores_both_methods(self):
        original=self.dialog.calculateParameters.__func__
        self.bridge.start();self.bridge.start()
        calc=self.dialog.calculateParameters()
        self.assertEqual(len(self.instances),1)
        self.assertIs(calc._processCell.__func__,_process_cell_iterative)
        self.assertIs(self.dialog.calculateParameters(),calc)
        self.assertEqual(len(self.instances),1)
        self.assertEqual(self.Calculator()._processCell(),'original')
        self.bridge.stop();self.bridge.stop()
        self.assertIs(self.dialog.calculateParameters.__func__,original)
        self.assertNotIn('calculateParameters',vars(self.dialog))
        self.assertNotIn('_processCell',vars(calc))
        self.assertEqual(calc._processCell(),'original')

    def test_native_zero_division_at_both_fine_resolutions_is_reported_not_published(self):
        namespace = {'BasinCalculator': self.Calculator, 'requested_size': [25]}
        exec(
            'def calculateParameters(self):\n'
            '    self.basinCalc = BasinCalculator()\n'
            '    self.basinCalc.cellsize = requested_size[0]\n'
            '    self.basinCalc.maxDistance = 0.\n'
            '    self.basinCalc.maxH = 300.\n'
            '    self.basinCalc.minH = 300.\n'
            '    raise ZeroDivisionError("float division by zero")\n',
            namespace,
        )
        self.dialog.__class__.calculateParameters = namespace['calculateParameters']
        finished = []
        self.bridge.calculation_finished.connect(
            lambda _owner, stage, event: finished.append((stage, event)))
        # The synthetic calculator has no DEM: this test isolates the native
        # zero-division error path while a separate suite validates D8 grids.
        from unittest.mock import patch
        with patch('caumax_consulta.rational_inputs._fine_grid_issue', return_value=None):
            self.bridge.start()
            for size in (25, 5):
                namespace['requested_size'][0] = size
                self.dialog.basinCalc = None
                self.assertIsNone(self.dialog.calculateParameters())
                stage, event = finished[-1]
                self.assertEqual(stage, 'parameters')
                self.assertFalse(event['completed'])
                self.assertIn('L=0', event['failure_reason'])
                self.assertIn(f'{size:g} m', event['failure_reason'])

    def test_unmarked_foreign_taudem_and_other_products_remain_unmodified(self):
        self.bridge.start()
        for product,encoding in ((None,'ESRI_D8'),('FLOW_DIRECTION','TAUDEM_D8'),
                                 ('ELEVATION','ESRI_D8'),('FOREIGN','ESRI_D8')):
            with self.subTest(product=product,encoding=encoding):
                self.write_tags(product,encoding);self.dialog.basinCalc=None
                calc=self.dialog.calculateParameters()
                self.assertNotIn('_processCell',vars(calc))
                self.assertEqual(calc._processCell(),'original')

    def test_preexisting_calculator_and_later_foreign_override_are_not_adopted(self):
        existing=self.Calculator();self.dialog.basinCalc=existing
        self.bridge.start();self.assertIs(self.dialog.calculateParameters(),existing)
        self.assertNotIn('_processCell',vars(existing))
        self.dialog.basinCalc=None;owned=self.dialog.calculateParameters()
        foreign=lambda *args:'foreign'
        self.dialog.calculateParameters=foreign;owned._processCell=foreign
        self.bridge.stop()
        self.assertIs(self.dialog.calculateParameters,foreign)
        self.assertIs(owned._processCell,foreign)

    def test_dialog_with_foreign_instance_hook_is_skipped(self):
        foreign=lambda:'foreign';self.dialog.calculateParameters=foreign
        self.bridge.start()
        self.assertFalse(self.bridge._bindings)
        self.bridge.stop();self.assertIs(self.dialog.calculateParameters,foreign)

    def test_observer_records_accepted_unchanged_output_without_changing_native_signals(self):
        events=[];starts=[];text_changes=[]
        self.bridge.calculation_started.connect(lambda dialog,stage:starts.append((dialog,stage)))
        self.bridge.calculation_finished.connect(lambda dialog,stage,event:events.append((dialog,stage,event)))
        self.dialog.txtResultFlow.textChanged.connect(text_changes.append)
        original_flow=self.dialog.calculate.__func__
        self.bridge.start()
        self.dialog.calculateParameters()
        self.assertEqual(events[-1][2]['ready_writes'],[False,True])
        self.assertEqual(self.dialog.calculate(),'native accepted')
        self.assertEqual(text_changes,['27.00'])
        self.assertEqual(events[-1][1],'flow')
        self.assertTrue(events[-1][2]['completed'])
        self.assertEqual(events[-1][2]['flow_writes'],['27','27.00'])
        self.assertEqual(self.dialog.txtResultFlow.text(),'27.00')
        self.assertEqual(events[-1][2]['period_writes'],['100'])
        self.assertEqual([stage for _,stage in starts],['parameters','flow'])
        self.assertNotIn('setText',vars(self.dialog.txtResultFlow))
        self.assertNotIn('setEnabled',vars(self.dialog.btnCalculate))
        self.bridge.stop()
        self.assertIs(self.dialog.calculate.__func__,original_flow)
        self.assertNotIn('calculate',vars(self.dialog))

    def test_two_decimals_preserve_true_native_precision_for_standard_return(self):
        events=[]
        self.bridge.calculation_finished.connect(
            lambda dialog,stage,event: events.append((stage,event)))
        self.bridge.start()
        self.dialog.flow_behavior='direct'
        self.assertEqual(self.dialog.calculate(),'native accepted')
        self.assertEqual(self.dialog.txtResultFlow.text(),'10.35')
        self.assertEqual(events[-1][1]['flow_writes'],['10','10.35'])
        self.assertEqual(events[-1][1]['period_writes'],['100'])
        self.assertNotIn('getFlow',vars(self.dialog))
        self.assertNotIn('_getInterpolatedFlow',vars(self.dialog))
        self.assertNotIn('setText',vars(self.dialog.txtResultFlow))

    def test_two_decimals_use_fitted_flow_at_manual_return_period(self):
        events=[]
        self.bridge.calculation_finished.connect(
            lambda dialog,stage,event: events.append((stage,event)))
        self.bridge.start()
        self.dialog.flow_behavior='interpolated'
        self.assertEqual(self.dialog.calculate(),'native accepted')
        self.assertEqual(self.dialog.txtResultReturnPeriod.text(),'60')
        # Six native getFlow calls are input quantiles, not Q(T=60).
        self.assertEqual(self.dialog.txtResultFlow.text(),'12.35')
        self.assertEqual(events[-1][1]['flow_writes'],['12','12.35'])

    def test_observer_rejects_cancelled_or_failed_calculation_with_old_results_visible(self):
        events=[]
        self.bridge.calculation_finished.connect(lambda dialog,stage,event:events.append(event))
        self.bridge.start()
        self.dialog.flow_behavior='abort'
        self.dialog.calculate()
        self.assertTrue(events[-1]['completed'])
        self.assertEqual(events[-1]['flow_writes'],[])
        self.assertEqual(self.dialog.txtResultFlow.text(),'27')
        self.dialog.flow_behavior='raise'
        with self.assertRaisesRegex(ValueError,'native failure'):
            self.dialog.calculate()
        self.assertFalse(events[-1]['completed'])
        self.assertEqual(events[-1]['flow_writes'],[])
        self.assertNotIn('setText',vars(self.dialog.txtResultFlow))
        self.assertNotIn('setText',vars(self.dialog.txtResultReturnPeriod))

    def test_foreign_flow_method_is_not_adopted_or_replaced(self):
        foreign=lambda:'foreign flow'
        self.dialog.calculate=foreign
        self.bridge.start()
        self.dialog.calculateParameters()
        self.assertIs(self.dialog.calculate,foreign)
        self.bridge.stop()
        self.assertIs(self.dialog.calculate,foreign)


if __name__=='__main__':unittest.main()
