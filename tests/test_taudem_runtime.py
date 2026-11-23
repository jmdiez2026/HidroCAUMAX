"""Verified offline extraction and private process environment; no real profile writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from caumax_consulta import taudem_runtime as runtime


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='hc_taudem_runtime_')
        self.folder = Path(self.temporary.name)
        self.bundle = self.folder/'taudem.zip'
        self.cache = self.folder/'cache with spaces'
        self.vc = self.folder/'existing QGIS runtime'
        self.vc.mkdir()
        for name in runtime._VC_DLLS:
            (self.vc/name).write_bytes(b'existing VC runtime')
        self.payload = {'bin/'+name: ('binary '+name).encode() for name in
                        ('PitRemove.exe', 'D8FlowDir.exe', 'AreaD8.exe', 'Threshold.exe',
                         'StreamNet.exe', 'mpiexec.exe', 'smpd.exe', 'msmpi.dll', 'gdal.dll')}
        self.payload.update({'share/proj/proj.db': b'projection database',
                             'share/gdal/gdalvrt.xsd': b'gdal schema',
                             'licenses/TauDEM.txt': b'TauDEM license'})
        self.windows = patch.object(runtime, '_windows_x64', return_value=True)
        self.windows.start()
        self.addCleanup(self.windows.stop)
        self.vc_patch = patch.object(runtime, '_vc_runtime_directory', return_value=self.vc)
        self.vc_patch.start()
        self.addCleanup(self.vc_patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def write_bundle(self, payload=None, manifest_transform=None, extras=()):
        payload = self.payload if payload is None else payload
        manifest = {'schema_version': 1, 'runtime_id': 'taudem-5.5.0-win64',
                    'platform': 'windows', 'architecture': 'x86_64', 'taudem_version': '5.5.0',
                    'files': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
        if manifest_transform:
            manifest = manifest_transform(manifest)
        with zipfile.ZipFile(self.bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for name, data in payload.items():
                archive.writestr(name, data)
            for name, data in extras:
                archive.writestr(name, data)

    def discover(self):
        return runtime.discover_bundled_runtime(self.bundle, self.cache)


class PrivateRuntimeTests(RuntimeFixture):
    def test_missing_and_unsupported_platform_return_none_without_writes(self):
        self.assertIsNone(self.discover())
        self.write_bundle()
        with patch.object(runtime, '_windows_x64', return_value=False):
            self.assertIsNone(self.discover())
        self.assertFalse(self.cache.exists())

    def test_inspection_does_not_extract_and_case_insensitive_tools_resolve(self):
        self.write_bundle()
        state = self.discover()
        self.assertTrue(state['available'])
        self.assertFalse(state['runtime_prepared'])
        self.assertFalse(self.cache.exists())
        self.assertEqual(Path(state['executables']['pitremove']).name, 'PitRemove.exe')
        self.assertEqual(state['runtime']['sha256'], hashlib.sha256(self.bundle.read_bytes()).hexdigest())

    def test_complete_extraction_reuses_verified_cache_without_rewriting(self):
        self.write_bundle()
        state = runtime.ensure_runtime(self.discover())
        root = Path(state['private_runtime']['root'])
        self.assertTrue(state['runtime_prepared'])
        stamps = {}
        for name, data in self.payload.items():
            self.assertEqual((root/name).read_bytes(), data)
            stamps[name] = (root/name).stat().st_mtime_ns
        with patch.object(runtime.tempfile, 'mkdtemp', side_effect=AssertionError('cache must be reused')):
            again = runtime.ensure_runtime(self.discover())
        self.assertTrue(again['runtime_prepared'])
        self.assertEqual(stamps, {name: (root/name).stat().st_mtime_ns for name in self.payload})

    def test_changed_dependency_has_distinct_cache_identity(self):
        self.write_bundle()
        first = self.discover()
        self.payload['bin/msmpi.dll'] += b'new MPI dependency'
        self.write_bundle()
        second = self.discover()
        self.assertNotEqual(first['private_runtime']['root'], second['private_runtime']['root'])
        self.assertNotEqual(first['runtime']['sha256'], second['runtime']['sha256'])

    def test_malformed_zip_and_manifest_are_rejected_before_extraction(self):
        self.bundle.write_bytes(b'not a zip')
        with self.assertRaisesRegex(ValueError, 'dañado'):
            self.discover()
        for transform in (lambda manifest: [], lambda manifest: dict(manifest, architecture='x86'),
                          lambda manifest: dict(manifest, files={})):
            self.write_bundle(manifest_transform=transform)
            with self.assertRaises(ValueError):
                self.discover()
        self.assertFalse(self.cache.exists())

    def test_incomplete_runtime_missing_mpi_proj_or_license_is_not_available(self):
        for missing in ('bin/smpd.exe', 'bin/msmpi.dll', 'share/proj/proj.db', 'licenses/TauDEM.txt'):
            with self.subTest(missing=missing):
                self.write_bundle({name: data for name, data in self.payload.items() if name != missing})
                with self.assertRaisesRegex(ValueError, 'incompleto'):
                    self.discover()
        self.assertFalse(self.cache.exists())

    def test_traversal_absolute_ads_and_reserved_names_cannot_escape(self):
        for name in ('../escape.exe', '/absolute.exe', 'C:/escape.exe', 'bin/../../escape.exe',
                     'bin\\escape.exe', 'bin/a.dll:payload', 'bin/CON.txt', 'bin/trailing. '):
            with self.subTest(path=name):
                self.write_bundle(extras=[(name, b'unsafe')])
                with self.assertRaises(ValueError):
                    self.discover()
                self.assertFalse(self.cache.exists())
                self.assertFalse((self.folder/'escape.exe').exists())

    def test_symlinks_case_collisions_and_unlisted_dll_are_rejected(self):
        link = zipfile.ZipInfo('bin/link.dll')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for entry in ((link, b'../../escape'), ('bin/PITREMOVE.EXE', b'duplicate'), ('bin/extra.dll', b'unlisted')):
            self.write_bundle(extras=[entry])
            with self.assertRaises(ValueError):
                self.discover()
        self.assertFalse(self.cache.exists())

    def test_bad_payload_hash_never_publishes_partial_cache(self):
        def corrupt(manifest):
            manifest['files']['bin/msmpi.dll'] = '0'*64
            return manifest
        self.write_bundle(manifest_transform=corrupt)
        state = self.discover()
        with self.assertRaisesRegex(ValueError, 'Hash incorrecto'):
            runtime.ensure_runtime(state)
        self.assertFalse(Path(state['private_runtime']['root']).exists())
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_bundle_replaced_after_probe_is_not_executed(self):
        self.write_bundle()
        state = self.discover()
        self.payload['bin/msmpi.dll'] += b'replaced'
        self.write_bundle()
        with self.assertRaisesRegex(ValueError, 'cambió'):
            runtime.ensure_runtime(state)
        self.assertFalse(self.cache.exists())

    def test_corrupt_or_extra_cached_dll_is_rejected_and_not_deleted(self):
        self.write_bundle()
        state = runtime.ensure_runtime(self.discover())
        root = Path(state['private_runtime']['root'])
        (root/'bin/msmpi.dll').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'dañado'):
            runtime.ensure_runtime(self.discover())
        self.assertEqual((root/'bin/msmpi.dll').read_bytes(), b'corrupt')
        (root/'bin/msmpi.dll').write_bytes(self.payload['bin/msmpi.dll'])
        (root/'bin/unlisted.dll').write_bytes(b'extra DLL')
        with self.assertRaisesRegex(ValueError, 'ajenos'):
            runtime.ensure_runtime(self.discover())
        self.assertTrue((root/'bin/unlisted.dll').exists())

    def test_cancelled_extraction_removes_only_its_unpublished_stage(self):
        self.write_bundle()
        state = self.discover()
        unrelated = self.cache/'unrelated'
        unrelated.mkdir(parents=True)
        (unrelated/'keep.txt').write_text('keep')
        def cancelled():
            return any(self.cache.glob('*/bin/PitRemove.exe'))
        with self.assertRaises(InterruptedError):
            runtime.ensure_runtime(state, cancelled)
        self.assertFalse(Path(state['private_runtime']['root']).exists())
        self.assertEqual(list(self.cache.iterdir()), [unrelated])
        self.assertEqual((unrelated/'keep.txt').read_text(), 'keep')

    def test_initial_cancellation_performs_no_extraction(self):
        self.write_bundle()
        state = self.discover()
        with self.assertRaises(InterruptedError):
            runtime.ensure_runtime(state, lambda: True)
        self.assertFalse(self.cache.exists())

    def test_competing_successful_extractor_is_verified_and_own_stage_cleaned(self):
        self.write_bundle()
        state = self.discover()
        def competing_publish(source, destination):
            shutil.copytree(source, destination)
            raise FileExistsError('another worker published first')
        with patch.object(Path, 'rename', competing_publish):
            prepared = runtime.ensure_runtime(state)
        self.assertTrue(prepared['runtime_prepared'])
        self.assertEqual(list(self.cache.iterdir()), [Path(state['private_runtime']['root'])])

    def test_private_environment_removes_qgis_dll_and_gdal_settings_without_global_changes(self):
        self.write_bundle()
        state = runtime.ensure_runtime(self.discover())
        windows = self.folder/'Windows'
        inherited = {'PATH': 'QGIS/bin;untrusted', 'GDAL_DATA': 'QGIS/share/gdal', 'PROJ_LIB': 'QGIS/proj',
                     'GDAL_SKIP': 'GTiff', 'CPL_PLUGIN_PATH': 'foreign', 'QGIS_PREFIX_PATH': 'QGIS',
                     'QT_PLUGIN_PATH': 'QGIS/plugins', 'PYTHONPATH': 'QGIS/python', 'SystemRoot': str(windows),
                     'USERNAME': 'sample', 'TEMP': 'old'}
        before = dict(os.environ)
        original = dict(inherited)
        result = runtime.subprocess_environment(state, self.folder, inherited)
        root = Path(state['private_runtime']['root'])
        self.assertEqual(result['PATH'].split(os.pathsep), [str(root/'bin'), str(windows/'System32'), str(windows)])
        self.assertEqual(result['PROJ_DATA'], str(root/'share/proj'))
        self.assertEqual(result['GDAL_DATA'], str(root/'share/gdal'))
        self.assertEqual(result['PROJ_NETWORK'], 'OFF')
        self.assertEqual(result['MSMPI_LOCAL_ONLY'], '1')
        self.assertEqual(result['TEMP'], str(self.folder))
        for key in ('GDAL_SKIP', 'CPL_PLUGIN_PATH', 'QGIS_PREFIX_PATH', 'QT_PLUGIN_PATH', 'PYTHONPATH'):
            self.assertNotIn(key, result)
        self.assertEqual(inherited, original)
        self.assertEqual(dict(os.environ), before)

    def test_external_environment_remains_compatible(self):
        inherited = {'PATH': 'user-binaries', 'GDAL_DATA': 'external-gdal'}
        result = runtime.subprocess_environment({}, self.folder, inherited)
        self.assertEqual(result['PATH'], inherited['PATH'])
        self.assertEqual(result['GDAL_DATA'], inherited['GDAL_DATA'])
        self.assertNotIn('PROJ_NETWORK', result)


    def test_missing_vc_runtime_has_explicit_diagnostic_without_installation(self):
        self.write_bundle()
        with patch.object(runtime, '_vc_runtime_directory', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Visual C\\+\\+'):
                self.discover()
        self.assertFalse(self.cache.exists())

    def test_vc_runtime_resolves_existing_qgis_folder_not_inherited_path(self):
        self.vc_patch.stop()
        install = self.folder/'QGIS'
        binary = install/'bin'
        binary.mkdir(parents=True)
        for name in runtime._VC_DLLS:
            (binary/name).write_bytes(b'existing runtime')
        env = {'SystemRoot': str(self.folder/'Windows'), 'OSGEO4W_ROOT': str(install), 'PATH': str(self.vc)}
        self.assertEqual(runtime._vc_runtime_directory(env, self.folder/'python/python.exe'), binary.resolve())
        del env['OSGEO4W_ROOT']
        self.assertIsNone(runtime._vc_runtime_directory(env, self.folder/'python/python.exe'))
        env['QGIS_PREFIX_PATH'] = str(install/'apps/qgis-ltr')
        self.assertEqual(runtime._vc_runtime_directory(env, self.folder/'python/python.exe'), binary.resolve())
        del env['QGIS_PREFIX_PATH']
        self.assertEqual(runtime._vc_runtime_directory(env, install/'apps/Python312/python.exe'), binary.resolve())

    def test_qgis_vc_runtime_takes_priority_over_complete_older_system_runtime(self):
        self.vc_patch.stop()
        install = self.folder/'QGIS'
        binary = install/'bin'
        system = self.folder/'Windows/System32'
        binary.mkdir(parents=True)
        system.mkdir(parents=True)
        for name in runtime._VC_DLLS:
            (binary/name).write_bytes(b'new QGIS VC')
            (system/name).write_bytes(b'older System32 VC')
        env = {'SystemRoot': str(system.parent), 'OSGEO4W_ROOT': str(install)}
        self.assertEqual(runtime._vc_runtime_directory(env, self.folder/'python/python.exe'), binary.resolve())
        (binary/runtime._VC_DLLS[0]).unlink()
        self.assertEqual(runtime._vc_runtime_directory(env, self.folder/'python/python.exe'), system.resolve())

    def test_local_vc_copies_are_hashed_reused_and_changed_runtime_has_separate_cache(self):
        self.write_bundle()
        first = runtime.ensure_runtime(self.discover())
        root = Path(first['private_runtime']['root'])
        receipt = json.loads((root/runtime._RECEIPT).read_text())
        for name in runtime._VC_DLLS:
            self.assertEqual((root/'bin'/name).read_bytes(), (self.vc/name).read_bytes())
            self.assertEqual(receipt['vc_sha256'][name], hashlib.sha256((self.vc/name).read_bytes()).hexdigest())
        with zipfile.ZipFile(self.bundle) as archive:
            self.assertFalse(any('bin/'+name in archive.namelist() for name in runtime._VC_DLLS))
        with patch.object(runtime.tempfile, 'mkdtemp', side_effect=AssertionError('valid cache must be reused')):
            runtime.ensure_runtime(self.discover())
        cached_dll = root/'bin/msvcp140.dll'
        cached_dll.write_bytes(b'corrupt local VC copy')
        with self.assertRaisesRegex(ValueError, 'dañado'):
            runtime.ensure_runtime(self.discover())
        (self.vc/'msvcp140.dll').write_bytes(b'upgraded QGIS VC')
        second = runtime.ensure_runtime(self.discover())
        second_root = Path(second['private_runtime']['root'])
        self.assertNotEqual(root, second_root)
        self.assertNotEqual(first['runtime']['vc_sha256'], second['runtime']['vc_sha256'])
        self.assertEqual(cached_dll.read_bytes(), b'corrupt local VC copy')
        self.assertEqual((second_root/'bin/msvcp140.dll').read_bytes(), b'upgraded QGIS VC')


class EngineRuntimeIntegrationTests(RuntimeFixture):
    def setUp(self):
        super().setUp()
        from caumax_consulta import taudem_engine
        self.engine = taudem_engine

    def test_probe_prefers_bundled_runtime_without_extracting(self):
        self.write_bundle()
        state = self.discover()
        with patch.object(runtime, 'discover_bundled_runtime', return_value=state), \
                patch.object(self.engine.shutil, 'which', side_effect=AssertionError('no PATH discovery')):
            result = self.engine.probe_taudem()
        self.assertTrue(result['available'])
        self.assertEqual(result['runtime'], state['runtime'])
        self.assertFalse(self.cache.exists())

    def test_explicit_directory_wins_and_prefers_its_colocated_mpi(self):
        folder = self.folder/'chosen bin'
        folder.mkdir()
        suffix = '.exe' if os.name == 'nt' else ''
        for tool in ('pitremove', 'd8flowdir', 'aread8', 'threshold', 'streamnet', 'mpiexec'):
            path = folder/(tool+suffix)
            path.write_bytes(b'external')
            path.chmod(0o700)
        with patch.object(runtime, 'discover_bundled_runtime', side_effect=AssertionError('explicit must win')), \
                patch.object(self.engine.shutil, 'which', return_value='outside/mpiexec'):
            result = self.engine.probe_taudem(folder)
        self.assertTrue(result['available'])
        self.assertEqual(result['mpiexec'], str((folder/('mpiexec'+suffix)).resolve()))
        self.assertNotIn('private_runtime', result)

    def test_corrupt_bundle_produces_diagnostic_without_silent_external_fallback(self):
        with patch.object(runtime, 'discover_bundled_runtime', side_effect=ValueError('hash incorrecto')), \
                patch.object(self.engine.shutil, 'which', side_effect=AssertionError('no silent fallback')):
            result = self.engine.probe_taudem()
        self.assertFalse(result['available'])
        self.assertIn('hash incorrecto', result['reason'])

    def test_subprocess_uses_private_executable_argv_environment_and_preserves_global_path(self):
        self.write_bundle()
        state = self.discover()
        process = Mock()
        process.wait.return_value = 0
        captured = {}
        def start(command, **kwargs):
            captured.update(command=command, **kwargs)
            kwargs['stdout'].write(b'TauDEM version 5.5.0\n')
            return process
        before = dict(os.environ)
        with patch.object(self.engine.subprocess, 'Popen', side_effect=start), \
                patch.object(self.engine._h, '_windows_job', return_value=None):
            result = self.engine._run(state, 'pitremove', ['-z', self.folder/'a b.tif'], self.folder, lambda: False)
        self.assertEqual(result['version'], '5.5.0')
        self.assertEqual(captured['command'], [state['mpiexec'], '-n', '1', state['executables']['pitremove'], '-z', str(self.folder/'a b.tif')])
        self.assertNotIn('shell', captured)
        self.assertEqual(captured['env']['PATH'].split(os.pathsep)[0], state['bin_dir'])
        self.assertTrue(Path(state['executables']['pitremove']).is_file())
        self.assertEqual(dict(os.environ), before)

    def test_cancellation_after_launch_terminates_mpi_process_tree(self):
        state = {'mpiexec': 'mpiexec', 'executables': {'pitremove': 'PitRemove'}}
        process = Mock()
        launched = []
        def start(*args, **kwargs):
            launched.append(True)
            return process
        with patch.object(self.engine.subprocess, 'Popen', side_effect=start), \
                patch.object(self.engine._h, '_windows_job', return_value=None), \
                patch.object(self.engine._h, '_stop_process') as stop:
            with self.assertRaises(InterruptedError):
                self.engine._run(state, 'pitremove', [], self.folder, lambda: bool(launched))
        stop.assert_called_once_with(process, None)


if __name__ == '__main__':
    unittest.main()
