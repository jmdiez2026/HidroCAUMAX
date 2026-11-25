"""Official-ZIP validation and a real NSIS build/extract check when available."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_installer', ROOT / 'scripts/build_windows_installer.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
MAKENSIS = os.environ.get('HIDROCAUMAX_MAKENSIS') or shutil.which('makensis')
EXTRACTOR = os.environ.get('HIDROCAUMAX_7Z') or shutil.which('7z') or shutil.which('7zz')


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'source'
        for name in installer.BUILD_SOURCES:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.members = {
            'caumax_consulta/__init__.py': b'def classFactory(iface): return None\n',
            'caumax_consulta/metadata.txt': b'[general]\nname=HidroCAUMAX v1.2.13\nversion=1.2.13\nqgisMinimumVersion=3.34\nqgisMaximumVersion=3.99\n',
            'caumax_consulta/data/datos con espacio$.txt': 'Datos del río\n'.encode('utf-8'),
        }
        self.zip = self.folder / 'HidroCAUMAX_v1_2_13.zip'
        self.manifest = self.write_zip(self.members)

    def write_zip(self, members):
        with zipfile.ZipFile(self.zip, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return dict(version='1.2.13', plugin_id='caumax_consulta', package=self.zip.name,
            sha256=installer.sha256(self.zip), bytes=self.zip.stat().st_size,
            source_files={name: hashlib.sha256(data).hexdigest() for name, data in members.items()})

    def test_source_archive_is_pinned_and_invalid_windows_names_are_rejected(self):
        self.assertEqual(installer.validate_payload(self.zip, self.manifest), self.manifest)
        corrupted = dict(self.manifest, sha256='0' * 64)
        with self.assertRaisesRegex(ValueError, 'ZIP does not match'):
            installer.validate_payload(self.zip, corrupted)
        for name in ('caumax_consulta/../foreign.txt', 'caumax_consulta/NUL.txt',
                     'caumax_consulta/file:stream', 'caumax_consulta/space .',
                     'caumax_consulta/a\\b.txt', 'caumax_consulta/data/../../foreign.txt'):
            with self.subTest(name=name):
                members = dict(self.members, **{name: b'bad'})
                manifest = self.write_zip(members)
                with self.assertRaises(ValueError):
                    installer.validate_payload(self.zip, manifest)

    def test_case_collisions_extra_members_and_wrong_member_hashes_cannot_build(self):
        members = dict(self.members)
        members['caumax_consulta/METADATA.TXT'] = b'collision'
        manifest = self.write_zip(members)
        with self.assertRaisesRegex(ValueError, 'collide'):
            installer.validate_payload(self.zip, manifest)
        manifest = self.write_zip(self.members)
        manifest['source_files'] = dict(manifest['source_files'])
        manifest['source_files']['caumax_consulta/__init__.py'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            installer.validate_payload(self.zip, manifest)

    def test_generated_uninstall_is_an_exact_file_allowlist_with_only_empty_directory_cleanup(self):
        code = installer._generate_payload_include(self.folder, self.members)
        self.assertNotIn('RMDir /r', code)
        self.assertNotIn('Delete "${TARGET}\\*', code)
        self.assertIn('Delete "${TARGET}\\__init__.py"', code)
        self.assertIn('IfFileExists "${TARGET}\\data\\datos con espacio$$.txt" 0 +2', code)
        self.assertIn('!macro HCAU_REMOVE_EMPTY_DIRS TARGET', code)
        deletion = code.split('!macro HCAU_DELETE_PAYLOAD TARGET\n', 1)[1].split('!macroend', 1)[0]
        self.assertNotIn('metadata.txt', deletion)
        self.assertIn('!macro HCAU_DELETE_METADATA TARGET', code)
        template = (ROOT / 'packaging/windows/installer.nsi').read_text(encoding='utf-8')
        self.assertNotIn('HKLM', template)
        self.assertNotIn('RMDir /r', template)
        self.assertNotIn('taskkill', template.lower())
        self.assertLess(template.index('!insertmacro HCAU_INSTALL_PAYLOAD "$StageDir"'),
                        template.index('Rename "$INSTDIR" "$BackupDir"'))
        self.assertIn('Rename "$BackupDir" "$INSTDIR"', template)
        self.assertIn('RequestExecutionLevel user', template)
        self.assertIn('ReadINIStr $0 "$INSTDIR\\metadata.txt" "general" "version"', template)
        uninstall = template.split('Section "Uninstall"', 1)[1]
        self.assertLess(uninstall.index('IfErrors uninstall_locked'),
                        uninstall.index('!insertmacro HCAU_DELETE_METADATA'))

    @unittest.skipUnless(MAKENSIS and EXTRACTOR, 'NSIS and 7-Zip are required for the real compile check')
    def test_real_unicode_exe_payload_icon_asinvoker_and_repeated_build(self):
        first = installer.build(self.zip, self.manifest, self.folder / 'first',
            source_root=self.root, makensis=MAKENSIS, extractor=EXTRACTOR)
        second = installer.build(self.zip, self.manifest, self.folder / 'second',
            source_root=self.root, makensis=MAKENSIS, extractor=EXTRACTOR)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['bytes'], second['bytes'])
        exe = self.folder / 'first' / first['installer']
        verified = installer.verify_installer(exe, first, self.zip, self.manifest,
                                              source_root=self.root, extractor=EXTRACTOR)
        self.assertEqual(verified['payload_files'], len(self.members))
        self.assertEqual(verified['execution_level'], 'asInvoker')
        self.assertFalse(verified['windows_executed'])
        changed = dict(first, requires_admin=True)
        with self.assertRaisesRegex(ValueError, 'requires_admin'):
            installer.verify_installer(exe, changed, self.zip, self.manifest, source_root=self.root, extractor=EXTRACTOR)
        (self.root / 'packaging/windows/installer.nsi').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'source mismatch'):
            installer.verify_installer(exe, first, self.zip, self.manifest, source_root=self.root, extractor=EXTRACTOR)


if __name__ == '__main__':
    unittest.main()
