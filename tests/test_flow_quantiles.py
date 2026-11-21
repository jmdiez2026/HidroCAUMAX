"""Sampling, inverse ambiguity, portable state and actual Qt PNG plots."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import numpy as np
from osgeo import gdal,osr
from qgis.PyQt.QtGui import QImage
from qgis.PyQt.QtTest import QSignalSpy
from qgis.core import QgsApplication,QgsCoordinateReferenceSystem

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from caumax_consulta import flow_quantiles as f


def snapshot(flows=None):
    return dict(version=1,periods=list(f.PERIODS),flows=flows or [0,10,20,30,40,50],
        source_paths=['q'+str(p)+'.tif' for p in f.PERIODS],
        point=dict(x=342825.,y=4643700.,crs_wkt='originalWKT'),
        input_period=5.,input_flow=10.,extrapolate=False,method=f.METHOD)


class MathTests(unittest.TestCase):
    def test_exact_six_quantiles_and_zero_are_preserved(self):
        flows=[0,10,20,30,40,50]
        for period,flow in zip(f.PERIODS,flows):
            self.assertEqual(f.estimate_flow(period,flows),
                             dict(status='exact',flow=float(flow),extrapolated=False))
        self.assertEqual(f.estimate_period(0,flows)['solutions'][0]['period'],2)

    def test_geometric_midpoint_interpolation_and_inverse_use_same_curve(self):
        period=math.sqrt(10*25)
        forward=f.estimate_flow(period,[0,10,20,30,40,50])
        self.assertAlmostEqual(forward['flow'],25)
        inverse=f.estimate_period(25,[0,10,20,30,40,50])
        self.assertEqual(inverse['status'],'unique')
        self.assertAlmostEqual(inverse['solutions'][0]['period'],period)

    def test_missing_quantiles_are_not_zero_and_known_nodes_remain_exact(self):
        flows=[None,None,20,None,40,None]
        self.assertIsNone(f.estimate_flow(5,flows)['flow'])
        self.assertEqual(f.estimate_flow(10,flows)['flow'],20)
        self.assertAlmostEqual(f.estimate_flow(math.sqrt(10*100),flows)['flow'],30)
        self.assertIsNone(f.estimate_flow(20,[None]*6)['flow'])
        self.assertEqual(f.estimate_period(3,[None]*6)['status'],'unavailable')

    def test_default_no_extrapolation_and_opt_in_is_explicit(self):
        flows=[0,10,20,30,40,50]
        self.assertIsNone(f.estimate_flow(1000,flows)['flow'])
        self.assertIsNone(f.estimate_flow(1.5,flows)['flow'])
        extra=f.estimate_flow(1000,flows,True)
        self.assertTrue(extra['extrapolated']);self.assertGreater(extra['flow'],50)
        inverse=f.estimate_period(extra['flow'],flows,True)
        self.assertAlmostEqual(inverse['solutions'][0]['period'],1000)
        self.assertTrue(inverse['extrapolated'])
        # Physically negative Q extrapolations are unavailable, never clipped.
        self.assertIsNone(f.estimate_flow(1.5,flows,True)['flow'])

    def test_flat_segments_return_closed_interval_and_join_adjacent_plateaus(self):
        result=f.estimate_period(20,[0,10,20,20,20,50])
        self.assertEqual(result['status'],'interval')
        self.assertEqual(result['solutions'],[])
        self.assertEqual(result['intervals'],[dict(min_period=10.,max_period=100.,
                          min_inclusive=True,max_inclusive=True,extrapolated=False)])
        text=f.describe_estimates(snapshot([0,10,20,20,20,50])|dict(input_flow=20))
        self.assertIn('meseta',text['inverse_text']);self.assertIn('no única',text['inverse_text'])

    def test_nonmonotonic_inputs_return_every_inverse_solution(self):
        result=f.estimate_period(15,[0,20,10,30,40,50])
        self.assertEqual(result['status'],'multiple')
        self.assertEqual(len(result['solutions']),3)
        text=f.describe_estimates(snapshot([0,20,10,30,40,50])|dict(input_flow=15))
        self.assertIn('T posibles',text['inverse_text']);self.assertIn('no única',text['inverse_text'])

    def test_invalid_periods_flows_and_nonfinite_values_are_rejected(self):
        for period in (1,0,-2,float('inf'),float('nan'),True):
            with self.subTest(period=period),self.assertRaises(ValueError):
                f.estimate_flow(period,[0,10,20,30,40,50])
        for flow in (-1,float('nan'),float('inf'),False):
            with self.subTest(flow=flow),self.assertRaises(ValueError):
                f.estimate_period(flow,[0,10,20,30,40,50])


class QtAndSamplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False);cls.app.initQgis()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.query=SimpleNamespace(x=342825.,y=4643700.,period=5,flow=10.,
                                   crs=QgsCoordinateReferenceSystem('EPSG:25830'))

    def tearDown(self):self.temp.cleanup()

    def raster(self,period,values,nodata=-9999,epsg=25830):
        path=self.folder/('q'+str(period)+'.tif')
        ds=gdal.GetDriverByName('GTiff').Create(str(path),2,2,1,gdal.GDT_Float32)
        srs=osr.SpatialReference();srs.ImportFromEPSG(epsg)
        ds.SetProjection(srs.ExportToWkt())
        ds.SetGeoTransform((342800,25,0,4643725,0,-25))
        ds.GetRasterBand(1).SetNoDataValue(nodata)
        ds.GetRasterBand(1).WriteArray(np.array(values,np.float32));ds=None
        return path

    def test_sampling_original_point_ignores_snap_dem_and_preserves_nodata_zero(self):
        # Original point is the bottom-right cell; a fictional snapped point is top-left.
        self.query.outlet_x=342812.5;self.query.outlet_y=4643712.5
        paths={p:self.raster(p,[[999,999],[999,q]])
               for p,q in zip(f.PERIODS,[0,10,20,30,-9999,-1])}
        state=f.sample_quantiles(self.query,paths)
        self.assertEqual(state['flows'],[0,10,20,30,None,None])
        self.assertEqual(state['point']['x'],self.query.x)
        self.assertEqual(state['point']['y'],self.query.y)
        self.assertTrue(state['extrapolate'])

    def test_raster_sampling_transforms_query_crs(self):
        paths={p:self.raster(p,[[1,2],[3,p]]) for p in f.PERIODS}
        source=osr.SpatialReference();source.ImportFromEPSG(25830)
        target=osr.SpatialReference();target.ImportFromEPSG(4326)
        for s in (source,target):s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        # Use a point inside the cell, avoiding numerical grid-edge ambiguity.
        self.query.x,self.query.y,_=osr.CoordinateTransformation(source,target).TransformPoint(342837.5,4643687.5)
        self.query.crs=QgsCoordinateReferenceSystem('EPSG:4326')
        self.assertEqual(f.sample_quantiles(self.query,paths)['flows'],list(f.PERIODS))

    def test_offline_restore_has_no_source_read_and_emits_one_change(self):
        widget=f.FlowQuantileWidget();spy=QSignalSpy(widget.changed)
        state=snapshot()
        with patch.object(f.gdal,'Open',side_effect=AssertionError('offline source read')):
            widget.restore(state)
            self.assertEqual(widget.snapshot(),state)
        self.assertEqual(len(spy),1)
        clone=widget.snapshot();clone['flows'][0]=999
        self.assertEqual(widget.snapshot()['flows'][0],0)
        widget.period_spin.setValue(20)
        self.assertEqual(len(spy),2);self.assertEqual(widget.snapshot()['input_period'],20)
        widget.extrapolate_check.setChecked(True)
        self.assertTrue(widget.snapshot()['extrapolate'])
        json.dumps(widget.snapshot(),allow_nan=False);widget.close()

    def test_set_query_with_saved_snapshot_never_resamples(self):
        widget=f.FlowQuantileWidget()
        with patch.object(f,'sample_quantiles',side_effect=AssertionError('saved resample')):
            widget.set_query(self.query,snapshot=snapshot())
        self.assertEqual(widget.table.rowCount(),6)
        self.assertEqual(widget.table.columnCount(),2)
        self.assertEqual(widget.table.item(0,1).text(),'0');widget.close()

    def test_editing_one_control_does_not_round_other_restored_inputs(self):
        widget=f.FlowQuantileWidget()
        state=snapshot()|dict(input_period=12.123456789,input_flow=123.123456789)
        widget.restore(state)
        widget.extrapolate_check.setChecked(True)
        self.assertEqual(widget.snapshot()['input_period'],state['input_period'])
        self.assertEqual(widget.snapshot()['input_flow'],state['input_flow'])
        widget.period_spin.setValue(20)
        self.assertEqual(widget.snapshot()['input_flow'],state['input_flow'])
        widget.close()

    def test_actual_pngs_render_empty_single_ambiguous_and_regular_panels(self):
        for label,flows in [('empty',[None]*6),('single',[None,None,20,None,None,None]),
                            ('ambiguous',[0,20,10,30,40,50]),('regular',[0,10,20,30,40,50])]:
            with self.subTest(label=label):
                state=snapshot(flows)
                png=f.render_quantile_plots(state,1100,500)
                self.assertTrue(png.startswith(b'\x89PNG'))
                image=QImage.fromData(png);self.assertEqual((image.width(),image.height()),(1100,500))
                for left,right in ((0,image.width()//2),(image.width()//2,image.width())):
                    nonwhite=sum(image.pixelColor(x,y).name()!='#ffffff'
                        for x in range(left,right,8) for y in range(0,image.height(),8))
                    self.assertGreater(nonwhite,10)
                # A brush left by the first panel must never flood-fill the second.
                blue=0;pixels=0
                for x in range(640,1040,8):
                    for y in range(85,375,8):
                        color=image.pixelColor(x,y);pixels+=1
                        blue+=color.blue()>130 and color.red()<60 and color.green()<160
                self.assertLess(blue/pixels,.15)
                (self.folder/(label+'.png')).write_bytes(png)


if __name__=='__main__':unittest.main()
