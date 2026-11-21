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
from qgis.PyQt.QtWidgets import QDialog, QMainWindow
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from caumax_consulta.rational_inputs import RationalInputsBridge, _process_cell_iterative


def native_class():
    """Compile the actual native class with isolated dependencies, not its settings."""
    source=ROOT.parent/'inputs/caumax/caumax/basincalculator.py'
    tree=ast.parse(source.read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='BasinCalculator')
    namespace=dict(gdal=gdal,ogr=ogr,np=np,statistics=statistics,math=math,
                   defaultdict=defaultdict,QgsGeometry=QgsGeometry,P0='P0',I1ID='i1id')
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    return namespace['BasinCalculator']


NATIVE=native_class()


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
             '    if self.basinCalc is None:\n'
             '        self.basinCalc=BasinCalculator()\n'
             '    return self.basinCalc\n',namespace)
        def waitcursor(method):
            def wrapped(*args,**kwargs):return method(*args,**kwargs)
            return wrapped
        class RationalMethodDialog(QDialog):
            def __init__(self,parent):super().__init__(parent);self.basinCalc=None
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


if __name__=='__main__':unittest.main()
