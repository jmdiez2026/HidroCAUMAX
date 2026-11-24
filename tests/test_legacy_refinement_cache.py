"""Explicitly selecting a restored tiny basin must run current reference checks."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from osgeo import gdal
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication
from caumax_consulta import dem_hydrology as hydro
from caumax_consulta import resolution_controller as rc
from caumax_consulta.terrain import analyze_basin
import test_regression_refinement_54km2 as fixture


class LegacyRefinementCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.Refinement54Km2Tests.setUpClass()
        cls.fixture=fixture.Refinement54Km2Tests

    @classmethod
    def tearDownClass(cls):
        fixture.Refinement54Km2Tests.tearDownClass()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='legacy_refinement_')
        self.folder=Path(self.temp.name)
        self.directions=self.folder/self.fixture.directions.name
        shutil.copy2(self.fixture.directions,self.directions)
        raster=gdal.Open(str(self.directions),gdal.GA_Update)
        codes=raster.GetRasterBand(1).ReadAsArray()
        # At the reported point, exactly seventeen bank cells drain east;
        # the river corresponding to the reference remains in column 250.
        codes[333:350,252]=4
        codes[333:350,253:260]=1
        codes[349,252]=1
        raster.GetRasterBand(1).WriteArray(codes)
        raster=None
        self.query=replace(self.fixture.query,x=342317.6,y=4710252.9,
                           directions_path=str(self.directions))
        self.legacy=analyze_basin(self.directions,self.fixture.dem,self.query.x,self.query.y,
                                  upstream_only=False)
        self.assertEqual(self.legacy.cell_count,17)
        self.base=replace(self.legacy,basin=replace(self.legacy.basin,
            geometry_wkb=self.fixture.reference['geometry_wkb'],area_m2=54e6,
            cell_width=500,cell_height=500,cell_count=216),
            main_course_wkb=self.fixture.reference['main_course_wkb'])
        self.results,self.errors=[],[]
        self.controller=rc.ResolutionController(self.base,self.query,
            on_result=lambda *args:self.results.append(args),on_error=self.errors.append)
        self.snapshot=dict(active_key='25',options=[dict(key='500',resolution=500,available=True),
            dict(key='25',resolution=25,available=True)],variants={
            '500':dict(analysis=self.base,query=self.query,smoothing_state=None,metadata={}),
            '25':dict(analysis=self.legacy,query=self.query,smoothing_state=None,metadata={})})

    def tearDown(self):
        self.controller.dispose()
        deadline=time.monotonic()+10
        while QgsApplication.taskManager().activeTasks() and time.monotonic()<deadline:
            QTest.qWait(5)
        self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+15
        while self.controller.busy and time.monotonic()<deadline:
            QTest.qWait(5)
        self.assertFalse(self.controller.busy)

    def test_open_preserves_snapshot_but_explicit_selection_rechecks_seventeen_cells(self):
        c=self.controller
        hashes={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.fixture.dem,self.directions)}
        with patch.object(rc,'_analyze_dem',side_effect=AssertionError('No analysis on open')):
            self.assertTrue(c.restore(self.snapshot))
        self.assertIs(self.results[-1][1],self.legacy)
        self.assertEqual(self.legacy.area_m2,10625.)
        self.assertAlmostEqual(self.legacy.outlet_x,342312.5)
        self.assertAlmostEqual(self.legacy.outlet_y,4710262.5)
        self.assertTrue(c.needs_reference_check('25'))
        with patch.object(rc,'_download_dem',side_effect=AssertionError('Use saved native DEM')), \
                patch.object(hydro,'_run_grass',side_effect=AssertionError('Reuse validated D8 grid')), \
                patch.object(hydro,'availability_engine',return_value={'available':False,'path':'','reason':'cached fixture'}):
            self.assertTrue(c.select('25'))
            self.assertIs(self.results[-1][1],self.legacy)  # No premature success.
            self.wait()
        self.assertFalse(self.errors)
        selected=c.variants['25']['analysis']
        self.assertGreater(selected.area_m2,45e6)
        self.assertLess(selected.area_m2,54e6)
        self.assertEqual(selected.diagnostics['outlet_snap']['method'],'reference_basin_d8')
        self.assertGreater(selected.diagnostics['outlet_snap']['reference_coverage_fraction'],.5)
        self.assertNotEqual((selected.outlet_x,selected.outlet_y),(self.legacy.outlet_x,self.legacy.outlet_y))
        self.assertFalse(c.needs_reference_check('25'))
        self.assertTrue(c.variants['25']['metadata']['saved_variant_reference_rechecked'])
        verified=analyze_basin(self.directions,self.fixture.dem,selected.outlet_x,selected.outlet_y,
                              upstream_only=False)
        self.assertEqual(selected.area_m2,verified.area_m2)
        self.assertEqual(selected.cell_count,verified.cell_count)
        self.assertEqual({p:hashlib.sha256(p.read_bytes()).hexdigest() for p in hashes},hashes)
        with patch.object(rc,'_analyze_dem',side_effect=AssertionError('Current verified result is cached')):
            self.assertTrue(c.select('500'))
            self.assertTrue(c.select('25'))
        self.assertIs(self.results[-1][1],selected)
        print('LEGACY_17_CELLS',self.legacy.area_m2/1e6,'->',selected.area_m2/1e6,
              'outlet',selected.outlet_x,selected.outlet_y)

    def test_missing_rasters_preserve_archive_but_never_claim_automatic_validation(self):
        query=replace(self.query,dem_path=str(self.folder/'missing.tif'))
        self.snapshot['variants']['25']['query']=query
        c=self.controller
        c.restore(self.snapshot)
        count=len(self.results)
        self.assertFalse(c.select('25'))
        self.assertEqual(len(self.results),count)
        self.assertIs(c.variants['25']['analysis'],self.legacy)
        self.assertIn('rásteres no están disponibles',self.errors[-1])
        self.assertTrue(c.needs_reference_check('25'))

    def test_explicit_manual_saved_cell_is_not_replaced_by_automatic_policy(self):
        manual=replace(self.legacy,diagnostics=dict(outlet_snap=dict(
            method='manual_cell_d8',explicit_user_choice=True,selection_mode='user_selected_cell')))
        self.snapshot['variants']['25']['analysis']=manual
        c=self.controller
        c.restore(self.snapshot)
        self.assertFalse(c.needs_reference_check('25'))
        with patch.object(rc,'_analyze_dem',side_effect=AssertionError('Preserve explicit user choice')):
            c.select('500')
            self.assertTrue(c.select('25'))
        self.assertIs(self.results[-1][1],manual)


if __name__=='__main__':
    unittest.main()
