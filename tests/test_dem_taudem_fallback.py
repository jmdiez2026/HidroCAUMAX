"""No extra QGIS plugin: real D8 orchestration, masks, reference and provenance."""
import builtins
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr

from caumax_consulta import dem_hydrology as hydro
from caumax_consulta import taudem_engine as engine
from caumax_consulta.manual_outlet import build_manual_outlet
from caumax_consulta.outlet_errors import OutletSelectionRequired
import test_taudem_outlet_consistency as fixture_module


NO_GRASS = dict(available=False, name='GRASS', path='', reason='GRASS deliberately absent')


class DemTauDEMFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.TauDEMOutletConsistencyTests.setUpClass()

    def setUp(self):
        self.fixture = fixture_module.TauDEMOutletConsistencyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.folder = self.fixture.folder
        self.cache = self.folder/'fallback'
        self.dem = self.fixture.dem
        binary = self.folder/'taudem-fixture'
        binary.write_bytes(b'Controlled TauDEM output fixture')
        self.runtime = dict(available=True, reason='', engine='TauDEM', mpiexec='mock-mpi',
            executables={name:str(binary) for name in ('pitremove', 'd8flowdir')},
            runtime={'kind':'external'})
        self.patches = [patch.object(hydro, '_grass_availability', return_value=NO_GRASS),
            patch.object(hydro, '_run_grass', side_effect=AssertionError('GRASS may not execute')),
            patch.object(engine, 'probe_taudem', return_value=self.runtime),
            patch.object(engine, '_run', side_effect=self.fixture.cli)]
        self.mocks = [item.start() for item in self.patches]
        for item in self.patches:
            self.addCleanup(item.stop)

    def analyze(self, **kwargs):
        options = dict(reference_area_m2=54e6, reference_cell_width=500, reference_cell_height=500,
                       refinement_reference=self.fixture.reference)
        options.update(kwargs)
        return hydro.analyze_dem(self.dem, *self.fixture.click, self.cache, **options)

    def test_grass_is_preferred_and_bundled_tau_is_only_used_when_absent(self):
        grass = dict(available=True, name='GRASS r.watershed', path='/grass', reason='')
        with patch.object(hydro, '_grass_availability', return_value=grass):
            choice = hydro.availability_engine()
        self.assertEqual(choice['kind'], 'grass')
        self.mocks[2].assert_not_called()
        self.runtime['runtime'] = {'kind':'bundled'}
        choice = hydro.availability_engine()
        self.assertEqual(choice['kind'], 'taudem')
        self.assertIn('incluido', choice['name'])
        self.mocks[2].assert_called_once_with(required_tools=('pitremove', 'd8flowdir'))

    def test_fallback_checks_same_reference_without_importing_processing(self):
        before = self.dem.read_bytes()
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == 'processing' or name.startswith(('processing.', 'grass.', 'grassprovider')):
                raise AssertionError('No Processing or GRASS plugin may be imported')
            return original_import(name, *args, **kwargs)
        with patch.object(builtins, '__import__', side_effect=guarded_import):
            result = self.analyze()
        self.assertEqual(self.fixture.calls, ['pitremove', 'd8flowdir'])
        self.assertEqual(result.metadata['engine'], 'TauDEM')
        self.assertTrue(result.metadata['d8_only'])
        self.assertEqual(result.diagnostics['outlet_snap']['method'], 'reference_basin_d8')
        self.assertEqual((result.selected_x, result.selected_y), self.fixture.click)
        self.assertEqual(result.area_m2, result.cell_count*625)
        self.assertEqual(ogr.CreateGeometryFromWkb(result.main_course_wkb).GetPoint_2D(0),
                         (result.outlet_x, result.outlet_y))
        self.assertEqual(self.dem.read_bytes(), before)
        self.assertFalse(result.diagnostics['taudem_pipeline_executed'])

    def test_valid_grass_cache_preserves_its_provenance_without_grass_installed(self):
        self.cache.mkdir()
        source = gdal.Open(str(self.dem))
        key = hydro._source_key(source, self.dem)
        source = None
        cached = self.cache/('d8_'+key[:32]+'.tif')
        shutil.copyfile(self.fixture.directions, cached)
        result = self.analyze()
        self.assertEqual(result.metadata['engine'], 'GRASS r.watershed')
        self.assertEqual(result.metadata['engine_path'], '')
        self.assertTrue(result.metadata['cache_reused'])
        self.assertEqual(result.directions_path, str(cached))
        self.assertFalse(self.fixture.calls)

    def test_tau_cache_is_separate_and_source_change_regenerates_directions(self):
        first = self.analyze()
        self.assertFalse(first.metadata['cache_reused'])
        second = self.analyze()
        self.assertTrue(second.metadata['cache_reused'])
        self.assertEqual(second.directions_path, first.directions_path)
        self.assertEqual(self.fixture.calls, ['pitremove', 'd8flowdir'])
        rechecked = hydro.analyze_dem(self.dem, *self.fixture.click, Path(first.directions_path).parent,
            reference_area_m2=54e6, reference_cell_width=500, reference_cell_height=500,
            refinement_reference=self.fixture.reference)
        self.assertTrue(rechecked.metadata['cache_reused'])
        self.assertEqual(rechecked.directions_path, first.directions_path)
        self.assertEqual(self.fixture.calls, ['pitremove', 'd8flowdir'])
        source = gdal.Open(str(self.dem))
        self.assertFalse(hydro._valid_cache(Path(first.directions_path), source,
                                          hydro._source_key(source, self.dem)))
        source = None
        old = Path(first.directions_path).read_bytes()
        dem = gdal.Open(str(self.dem), gdal.GA_Update)
        band = dem.GetRasterBand(1)
        value = band.ReadAsArray(3, 3, 1, 1)
        band.WriteArray(value+1, 3, 3)
        dem.FlushCache()
        band = dem = None
        third = self.analyze()
        self.assertFalse(third.metadata['cache_reused'])
        self.assertNotEqual(third.directions_path, first.directions_path)
        self.assertEqual(Path(first.directions_path).read_bytes(), old)
        self.assertEqual(self.fixture.calls, ['pitremove', 'd8flowdir']*2)

    def test_ambiguous_fallback_retains_real_manual_context_and_can_apply_it(self):
        with patch('caumax_consulta.refinement_snap.select_reference_outlet', side_effect=ValueError('Dos ramales posibles')):
            with self.assertRaises(OutletSelectionRequired) as caught:
                self.analyze()
        context = caught.exception.selection_context
        self.assertEqual(context['metadata']['engine'], 'TauDEM')
        self.assertTrue(Path(context['directions_path']).is_file())
        manual = build_manual_outlet(context, self.fixture.validated.outlet_x, self.fixture.validated.outlet_y)
        self.assertEqual(manual['metadata']['engine'], 'TauDEM')
        analysis = manual['analysis']
        self.assertTrue(analysis.diagnostics['outlet_snap']['explicit_user_choice'])
        self.assertEqual((analysis.selected_x, analysis.selected_y), self.fixture.click)
        self.assertEqual(self.fixture.calls, ['pitremove', 'd8flowdir'])

    def test_cancelled_generation_never_publishes_complete_cache(self):
        before = self.dem.read_bytes()
        cancelled = [False]
        def runner(runtime, tool, arguments, work, check):
            hydro._check(check)
            result = self.fixture.cli(runtime, tool, arguments, work, check)
            cancelled[0] = True
            return result
        with patch.object(engine, '_run', side_effect=runner):
            with self.assertRaises(InterruptedError):
                engine.prepare_d8(self.dem, self.cache, cancelled=lambda:cancelled[0])
        self.assertEqual(self.fixture.calls, ['pitremove'])
        self.assertFalse(list(self.cache.rglob('source.json')))
        self.assertEqual(self.dem.read_bytes(), before)

    def test_recoding_never_gives_direction_to_original_nodata_or_masked_cells(self):
        dem = gdal.Open(str(self.dem), gdal.GA_Update)
        shape = (dem.RasterYSize, dem.RasterXSize)
        band = dem.GetRasterBand(1)
        band.WriteArray(np.array([[-9999]], dtype=np.float32), 3, 3)
        band.CreateMaskBand(gdal.GMF_PER_DATASET)
        mask = np.full(shape, 255, dtype=np.uint8)
        mask[4, 4] = 0
        band.GetMaskBand().WriteArray(mask)
        dem.FlushCache()
        band = dem = None
        native = self.folder/'native_all_valid.tif'
        self.fixture.raster(native, np.ones(shape, dtype=np.int16), -32768, gdal.GDT_Int16)
        coded = self.folder/'coded.tif'
        engine._recode_tau_d8(native, coded, self.dem, lambda:False)
        ds = gdal.Open(str(coded))
        values = ds.ReadAsArray()
        self.assertEqual((values[3, 3], values[4, 4], values[5, 5]), (255, 255, 1))
        ds = None


class D8OnlyAvailabilityTests(unittest.TestCase):
    def test_only_two_binaries_and_mpi_are_required_for_delineation(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            suffix = '.exe' if os.name == 'nt' else ''
            for name in ('pitremove', 'd8flowdir', 'mpiexec'):
                path = folder/(name+suffix)
                path.write_bytes(b'tool')
                path.chmod(0o700)
            with patch.object(engine.shutil, 'which', return_value=None):
                self.assertTrue(engine.probe_taudem(folder, required_tools=('pitremove', 'd8flowdir'))['available'])
                self.assertFalse(engine.probe_taudem(folder)['available'])

    @unittest.skipUnless(engine.probe_taudem(required_tools=('pitremove','d8flowdir'))['available'],
                         'Runtime TauDEM real no disponible')
    def test_real_d8_only_fallback_preserves_original_depression_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            row, col = np.indices((31,31))
            values = (150-row*2+abs(col-15)*3).astype(np.float32)
            values[10,15] -= 25
            dem = folder/'dem.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(dem),31,31,1,gdal.GDT_Float32)
            ds.SetGeoTransform((300000,25,0,4700000,0,-25))
            crs=osr.SpatialReference();crs.ImportFromEPSG(25830)
            ds.SetProjection(crs.ExportToWkt())
            ds.GetRasterBand(1).SetNoDataValue(-9999)
            ds.GetRasterBand(1).WriteArray(values)
            ds = None
            before = hashlib.sha256(dem.read_bytes()).hexdigest()
            calls = []
            original_run = engine._run
            def observed(runtime, tool, *args, **kwargs):
                self.assertIn(tool, ('pitremove', 'd8flowdir'))
                calls.append(tool)
                return original_run(runtime, tool, *args, **kwargs)
            with (patch.object(hydro, '_grass_availability', return_value=NO_GRASS),
                  patch.object(hydro, '_run_grass', side_effect=AssertionError('GRASS is absent')),
                  patch.object(engine, '_run', side_effect=observed)):
                result = hydro.analyze_dem(dem,300387.5,4699387.5,folder/'cache')
                cached = hydro.analyze_dem(dem,300387.5,4699387.5,folder/'cache')
            self.assertEqual(calls, ['pitremove','d8flowdir'])
            self.assertEqual(result.metadata['engine'], 'TauDEM')
            self.assertGreater(result.cell_count, 100)
            self.assertTrue(cached.metadata['cache_reused'])
            self.assertEqual(result.geometry_wkb, cached.geometry_wkb)
            self.assertEqual(before, hashlib.sha256(dem.read_bytes()).hexdigest())
            self.assertFalse(list((folder/'cache').rglob('network.shp')))


if __name__ == '__main__':
    unittest.main()
