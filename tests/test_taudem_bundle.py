"""Offline runtime packaging contract, with synthetic files and no QGIS/MPI."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('taudem_bundle_builder',
                                            ROOT/'scripts'/'prepare_taudem_bundle.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def pe_fixture(machine=0x8664):
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<H', data, 132, machine)
    return bytes(data)


class TauDEMBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hidrocaumax-runtime-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root/'stage'
        self.output = self.root/'taudem-5.5.0-win64.zip'
        for name in builder.REQUIRED_BINARIES:
            self.write(name, pe_fixture())
        self.write('share/proj/proj.db', b'SQLite format 3\0synthetic test database')
        self.write('licenses/TauDEM.txt', b'Test license fixture, not a distribution license.\n')
        self.write('licenses/dependencies.txt', b'Synthetic dependency notices.\n')

    def write(self, name, content):
        path = self.stage/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def rewrite_zip(self, change):
        with zipfile.ZipFile(self.output) as archive:
            payload = {info.filename: (info, archive.read(info)) for info in archive.infolist()}
        change(payload)
        with zipfile.ZipFile(self.output, 'w') as archive:
            for info, data in payload.values():
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    def test_manifest_accounts_for_all_unmodified_files_and_binary_metadata(self):
        originals = {path.relative_to(self.stage).as_posix(): path.read_bytes()
                     for path in self.stage.rglob('*') if path.is_file()}
        manifest = builder.prepare(self.stage, self.output)
        self.assertEqual({key: manifest[key] for key in builder.IDENTITY}, builder.IDENTITY)
        self.assertEqual(set(manifest['files']), set(originals))
        self.assertEqual(builder.validate_bundle(self.output), manifest)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), sorted(archive.namelist()))
            self.assertEqual(json.loads(archive.read('manifest.json')), manifest)
            for name, data in originals.items():
                self.assertEqual(archive.read(name), data)
                self.assertEqual(manifest['files'][name], hashlib.sha256(data).hexdigest())
                self.assertEqual((self.stage/name).read_bytes(), data)
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                self.assertEqual(info.external_attr >> 16, stat.S_IFREG | 0o644)
        self.assertNotIn(str(self.stage), json.dumps(manifest))

    def test_repeated_build_ignores_source_timestamps(self):
        first = builder.prepare(self.stage, self.output)
        original_zip = self.output.read_bytes()
        for path in self.stage.rglob('*'):
            if path.is_file():
                os.utime(path, (1_000_000_000, 1_000_000_000))
        another = self.root/'another.zip'
        self.assertEqual(builder.prepare(self.stage, another), first)
        self.assertEqual(another.read_bytes(), original_zip)

    def test_missing_required_components_are_rejected_before_output(self):
        for name in builder.REQUIRED_FILES:
            path = self.stage/name
            data = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Missing required'):
                builder.prepare(self.stage, self.output)
            self.assertFalse(self.output.exists())
            self.write(name, data)

    def test_invalid_binary_architecture_and_database_are_rejected(self):
        binary = self.stage/builder.REQUIRED_BINARIES[0]
        for data in (b'not an executable', pe_fixture(0x14c)):
            binary.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'Windows'):
                builder.prepare(self.stage, self.output)
        binary.write_bytes(pe_fixture())
        self.write('share/proj/proj.db', b'incorrect database')
        with self.assertRaisesRegex(ValueError, 'SQLite'):
            builder.prepare(self.stage, self.output)

    def test_licenses_are_required_and_cannot_be_empty(self):
        for path in (self.stage/'licenses').iterdir():
            path.unlink()
        with self.assertRaisesRegex(ValueError, 'licenses'):
            builder.prepare(self.stage, self.output)
        self.write('licenses/notice.txt', b' \n\t')
        with self.assertRaisesRegex(ValueError, 'must not be empty'):
            builder.prepare(self.stage, self.output)

    def test_personal_paths_and_obvious_credentials_cannot_enter_text_payload(self):
        values = (b'C:\\Users\\private-person\\Downloads\\runtime',
                  b'/home/private-person/runtime', b'-----BEGIN RSA PRIVATE KEY-----',
                  b'ghp_'+b'A'*36)
        for data in values:
            self.write('licenses/provenance.txt', data)
            with self.subTest(data=data[:18]), self.assertRaisesRegex(ValueError, 'Personal path or credential'):
                builder.prepare(self.stage, self.output)

    def test_hidden_configuration_and_unexpected_stage_files_are_rejected(self):
        for name in ('bin/.env', 'licenses/credentials.json', 'bin/private.pem', 'extra.txt'):
            path = self.write(name, b'local configuration')
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.prepare(self.stage, self.output)
            path.unlink()

    def test_source_symlink_is_rejected(self):
        target = self.root/'outside.txt'
        target.write_bytes(b'outside stage')
        try:
            (self.stage/'licenses'/'linked.txt').symlink_to(target)
        except OSError:
            self.skipTest('Host does not permit symlink creation; archive symlink test still runs.')
        with self.assertRaisesRegex(ValueError, 'Symbolic links'):
            builder.prepare(self.stage, self.output)

    def test_archive_symlink_and_traversal_are_rejected_on_every_host(self):
        for unsafe_name, mode in (('licenses/link.txt', stat.S_IFLNK | 0o777),
                                  ('../escape.txt', stat.S_IFREG | 0o644)):
            builder.prepare(self.stage, self.output)
            def mutate(payload):
                info = zipfile.ZipInfo(unsafe_name, builder.TIMESTAMP)
                info.external_attr = mode << 16
                payload[unsafe_name] = info, b'outside'
            self.rewrite_zip(mutate)
            with self.subTest(name=unsafe_name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                builder.validate_bundle(self.output)

    def test_payload_tampering_is_detected(self):
        builder.prepare(self.stage, self.output)
        def mutate(payload):
            name = 'licenses/TauDEM.txt'
            info, _data = payload[name]
            payload[name] = info, b'changed notice'
        self.rewrite_zip(mutate)
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            builder.validate_bundle(self.output)

    def test_unlisted_file_and_wrong_runtime_identity_are_rejected(self):
        for extra in (True, False):
            builder.prepare(self.stage, self.output)
            def mutate(payload):
                if extra:
                    info = zipfile.ZipInfo('bin/extra.dll', builder.TIMESTAMP)
                    payload[info.filename] = info, b'unlisted'
                else:
                    info, data = payload['manifest.json']
                    manifest = json.loads(data)
                    manifest['runtime_id'] = 'other-runtime'
                    payload['manifest.json'] = info, json.dumps(manifest).encode()
            self.rewrite_zip(mutate)
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                builder.validate_bundle(self.output)

    def test_windows_case_collision_is_rejected_and_lowercase_official_names_work(self):
        for name in builder.REQUIRED_BINARIES:
            path = self.stage/name
            temporary = path.with_suffix('.moving')
            path.rename(temporary)
            temporary.rename(path.with_name(path.name.lower()))
        self.assertEqual(len(builder.prepare(self.stage, self.output)['files']), 11)
        with self.assertRaisesRegex(ValueError, 'case-colliding'):
            builder.validate_inventory(['bin/PitRemove.exe', 'bin/pitremove.exe'])

    def test_output_inside_stage_is_rejected_without_replacing_existing_file(self):
        self.output.write_bytes(b'previous validated bundle')
        self.write('licenses/unwanted.txt', b'/home/private-person/path')
        with self.assertRaises(ValueError):
            builder.prepare(self.stage, self.output)
        self.assertEqual(self.output.read_bytes(), b'previous validated bundle')
        with self.assertRaisesRegex(ValueError, 'outside'):
            builder.prepare(self.stage, self.stage/'bundle.zip')


if __name__ == '__main__':
    unittest.main()
