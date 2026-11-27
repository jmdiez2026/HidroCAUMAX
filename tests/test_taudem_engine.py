"""True TauDEM CLI products and explicit native-to-CAUMAX compatibility."""
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import subprocess
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from caumax_consulta import taudem_engine as engine


class TauDEMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="taudem_tests_")
        self.folder = Path(self.temp.name)
        work = self.folder / "process_work"
        work.mkdir()
        for target, name, value in ((engine._runtime, "_default_cache", self.folder / "runtime"),
                                    (engine._h, "_runtime_folder", work)):
            mocked = patch.object(target, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def tearDown(self):
        self.temp.cleanup()

    def raster(self, values, name="dem.tif", kind=gdal.GDT_Float32, nodata=-9999):
        values = np.asarray(values)
        path = self.folder / name
        ds = gdal.GetDriverByName("GTiff").Create(str(path), values.shape[1], values.shape[0], 1, kind)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25830)
        ds.SetProjection(reference.ExportToWkt())
        ds.SetGeoTransform((300000, 25, 0, 4700000, 0, -25))
        ds.GetRasterBand(1).SetNoDataValue(nodata)
        ds.GetRasterBand(1).WriteArray(values)
        ds.FlushCache()
        ds = None
        return path

    def test_native_encoding_recodes_all_eight_directions_and_preserves_mask(self):
        native = self.raster([[1, 2, 3, 4, 5, 6, 7, 8, -32768]], "native.tif", gdal.GDT_Int16, -32768)
        dem = self.raster(np.arange(9).reshape(1, 9))
        original = native.read_bytes()
        target = self.folder / "caumax.tif"
        engine._recode_tau_d8(native, target, dem, lambda: False)
        ds = gdal.Open(str(target))
        np.testing.assert_array_equal(ds.ReadAsArray(), [[1, 128, 64, 32, 16, 8, 4, 2, 255]])
        self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(), 255)
        self.assertEqual(ds.GetMetadataItem("HIDROCAUMAX_PRODUCT"), "FLOW_DIRECTION")
        self.assertEqual(ds.GetMetadataItem("FLOW_DIRECTION_ENCODING"), "ESRI_D8")
        self.assertEqual(ds.GetMetadataItem("HYDROLOGY_ENGINE"), "TauDEM")
        ds = None
        self.assertEqual(native.read_bytes(), original)

    def test_probe_missing_binary_directory_explains_real_dependency_no_fallback(self):
        with patch.object(engine.shutil, "which", return_value=None), patch.dict(os.environ, {}, clear=True):
            result = engine.probe_taudem(self.folder / "missing")
        self.assertFalse(result["available"])
        self.assertIn("Esri-D8", result["reason"])
        self.assertIn("MPI", result["reason"])
        self.assertIn("github.com/dtarb/TauDEM", result["reason"])

    def test_threshold_uses_area_and_native_cells_without_silent_zero(self):
        self.assertEqual(engine._threshold(626, 625), (2, 1250))
        for value in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                engine._threshold(value, 625)

    def test_explicit_invalid_folder_does_not_silently_fall_back_to_path(self):
        result = engine.probe_taudem(self.folder / "explicit_missing")
        self.assertFalse(result["available"])
        self.assertFalse(result["executables"])

    def test_initial_cancellation_never_runs_tau_tools(self):
        dem = self.raster(np.ones((5, 5)))
        with patch.object(engine, "probe_taudem", return_value={"available": True}), \
                patch.object(engine, "_run", side_effect=AssertionError("Must not execute")):
            with self.assertRaises(InterruptedError):
                engine.prepare_taudem(dem, 300050, 4699950, self.folder / "cache",
                    reference_area_m2=1000, reference_cell_width=500, reference_cell_height=500,
                    cancelled=lambda: True)
        self.assertFalse((self.folder / "cache").exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree cancellation')
    def test_windows_cancellation_stops_worker_and_its_child(self):
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        marker = self.folder / 'child.pid'
        code = ('import subprocess,sys,time,pathlib; '
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
                f'pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(60)')
        real_popen = subprocess.Popen
        processes, handles = [], []
        started = time.monotonic()
        def launch(*args, **kwargs):
            process = real_popen([sys.executable, '-c', code], **kwargs)
            processes.append(process)
            return process
        def cancelled():
            if marker.exists() and not handles:
                handles.append(kernel.OpenProcess(0x100000, False, int(marker.read_text())))
            return bool(handles) or time.monotonic() - started > 8
        engine_info = dict(mpiexec='test', executables={'pitremove': 'test'})
        try:
            with patch.object(engine._runtime, 'ensure_runtime', side_effect=lambda e,c:e), \
                    patch.object(engine._runtime, 'subprocess_environment', return_value=os.environ.copy()), \
                    patch.object(engine.subprocess, 'Popen', side_effect=launch):
                with self.assertRaises(InterruptedError):
                    engine._run(engine_info, 'pitremove', [], self.folder, cancelled)
            self.assertTrue(handles, 'Child must have started before cancellation')
            self.assertIsNotNone(processes[0].poll())
            self.assertEqual(kernel.WaitForSingleObject(handles[0], 5000), 0)
            self.assertLess(time.monotonic() - started, 10)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait()
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)

    @unittest.skipUnless(engine.probe_taudem()["available"] and os.name != "nt", "MPI Linux requerido")
    def test_running_mpi_process_can_be_cancelled(self):
        runtime = engine.probe_taudem()
        script = self.folder / "slow_process.sh"
        script.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
        script.chmod(0o700)
        runtime["executables"] = dict(runtime["executables"], pitremove=str(script))
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            engine._run(runtime, "pitremove", [], self.folder,
                        lambda: time.monotonic() - started > 1)
        self.assertLess(time.monotonic() - started, 8)

    @unittest.skipUnless(engine.probe_taudem()["available"], "Runtime TauDEM real no disponible")
    def test_real_pipeline_keeps_manual_outlet_original_heights_basin_network_and_rebuild(self):
        yy, xx = np.indices((101, 101))
        values = 1000 + (100 - .05 * (np.abs(xx - 50) - 40) ** 2) + 200 - .002 * (yy - 15) ** 2
        values[35, 35] = 0  # a pit raised for flow routing, retained in terrain statistics
        dem = self.raster(values)
        source_bytes = dem.read_bytes()
        x, y = 300000 + 55.5 * 25, 4700000 - 70.5 * 25
        dataset = gdal.Open(str(dem))
        accepted = SimpleNamespace(outlet_x=300000 + 50.5 * 25, outlet_y=y,
            crs_wkt=dataset.GetProjectionRef(), diagnostics={'outlet_snap': {
                'method': 'manual_cell_d8', 'selection_mode': 'user_selected_cell',
                'explicit_user_choice': True}})
        dataset = None
        updates = []
        result = engine.prepare_taudem(dem, x, y, self.folder / "cache",
            reference_area_m2=2e6, reference_cell_width=500, reference_cell_height=500,
            outlet_analysis=accepted,
            progress=lambda text, percent: updates.append(percent))
        self.assertEqual(result.metadata["engine"], "TauDEM")
        self.assertEqual(result.metadata["version"], "5.5.0")
        self.assertTrue(result.analysis.diagnostics["taudem_pipeline_executed"])
        self.assertEqual((result.analysis.selected_x, result.analysis.selected_y), (x, y))
        self.assertAlmostEqual(result.analysis.outlet_x, 300000 + 50.5 * 25)
        self.assertAlmostEqual(result.analysis.outlet_y, y)
        self.assertAlmostEqual(result.analysis.diagnostics["outlet_snap"]["distance_m"], 125)
        self.assertEqual(result.analysis.z_min, 0)
        self.assertEqual(result.analysis.area_m2,
                         result.analysis.diagnostics["outlet_snap"]["candidate_upstream_area_m2"])
        area_ds = gdal.Open(result.accumulation_path)
        area = area_ds.ReadAsArray()
        area_nodata = area_ds.GetRasterBand(1).GetNoDataValue()
        self.assertEqual(np.count_nonzero(area != area_nodata), result.analysis.cell_count)
        area_ds = None
        streams_ds = gdal.Open(result.stream_raster_path)
        streams = streams_ds.ReadAsArray()
        self.assertFalse(np.any((streams == 1) & (area == area_nodata)))
        streams_ds = None
        self.assertEqual(updates, sorted(updates))
        self.assertEqual(updates[-1], 100)
        for path in (result.filled_dem_path, result.tau_d8_path, result.caumax_d8_path,
                     result.slope_path, result.accumulation_path, result.stream_raster_path, result.network_path):
            self.assertTrue(Path(path).is_file())
        vector = ogr.Open(result.network_path)
        layer = vector.GetLayer(0)
        self.assertGreater(layer.GetFeatureCount(), 0)
        self.assertEqual(layer.GetSpatialRef().GetAuthorityCode(None), "25830")
        layer = vector = None
        original_run = engine._run
        calls = []

        def restricted_run(runtime, tool, arguments, work, cancelled):
            calls.append(tool)
            self.assertIn(tool, ("threshold", "streamnet"))
            return original_run(runtime, tool, arguments, work, cancelled)

        with patch.object(engine, "_run", side_effect=restricted_run):
            rebuilt = engine.rebuild_network(result, result.threshold_area_m2 * 2)
        self.assertEqual(calls, ["threshold", "streamnet"])
        self.assertIs(rebuilt.analysis, result.analysis)
        self.assertEqual(rebuilt.caumax_d8_path, result.caumax_d8_path)
        self.assertEqual(rebuilt.threshold_cells, result.threshold_cells * 2)
        with patch.object(engine, "_run", side_effect=AssertionError("Cached products must not rerun tools")):
            cached = engine.prepare_taudem(dem, x, y, self.folder / "cache",
                reference_area_m2=2e6, reference_cell_width=500, reference_cell_height=500,
                outlet_analysis=accepted)
        self.assertEqual(cached.network_path, result.network_path)
        self.assertEqual(cached.analysis.area_m2, result.analysis.area_m2)
        self.assertEqual(dem.read_bytes(), source_bytes)


if __name__ == "__main__":
    unittest.main()
