import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.gui import QgsMessageBar
from caumax_consulta import automatic_update as module
from caumax_consulta.update_checker import parse_update_feed


class AutomaticUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root/'package.zip'
        self.info = dict(version='1.0.11', installed_version='1.0.10', download_url=module.RELEASES_URL+'/download/v1.0.11/HidroCAUMAX_v1_0_11.zip')
        self.payload = {'caumax_consulta/metadata.txt':b'[general]\nversion=1.0.11\nexperimental=False\nqgisMinimumVersion=3.34\nqgisMaximumVersion=3.99\n',
                        'caumax_consulta/__init__.py':b'NEW', 'caumax_consulta/data/file.txt':b'unchanged'}

    def package_and_manifest(self, payload=None):
        payload=self.payload if payload is None else payload
        with zipfile.ZipFile(self.package,'w') as archive:
            for name, data in payload.items(): archive.writestr(name,data)
        return dict(version='1.0.11',plugin_id='caumax_consulta',repository='jmdiez2026/HidroCAUMAX',experimental=False,
            download_url=self.info['download_url'],bytes=self.package.stat().st_size,
            sha256=module._hash(self.package),source_files={n:hashlib.sha256(v).hexdigest() for n,v in payload.items()})

    def validate(self, manifest):
        return module.validate_package(self.package,manifest,self.info,self.root/'stage')

    def test_verified_package_and_member_hashes(self):
        files=self.validate(self.package_and_manifest())
        self.assertIn(Path('metadata.txt'),files)
        self.assertEqual((self.root/'stage/__init__.py').read_bytes(),b'NEW')

    def test_reject_corrupt_hash_without_extracting(self):
        manifest=self.package_and_manifest(); manifest['sha256']='0'*64
        with self.assertRaises(ValueError): self.validate(manifest)
        self.assertFalse((self.root/'stage').exists())

    def test_reject_mismatched_member_even_with_matching_archive_hash(self):
        manifest=self.package_and_manifest(); manifest['source_files']['caumax_consulta/__init__.py']='0'*64
        with self.assertRaises(ValueError): self.validate(manifest)
        self.assertFalse((self.root/'stage').exists())

    def test_reject_traversal_and_windows_ambiguous_names(self):
        for name in ('caumax_consulta/../outside','caumax_consulta/drive:C','caumax_consulta/a./b','caumax_consulta\\x'):
            with self.subTest(name=name):
                manifest=self.package_and_manifest(dict(self.payload,**{name:b'bad'}))
                with self.assertRaises(ValueError): self.validate(manifest)
        self.assertFalse((self.root/'outside').exists())

    def test_reject_wrong_version_and_incompatible_qgis(self):
        for old,new in ((b'1.0.11',b'1.0.12'),(b'3.34',b'4.00')):
            payload=dict(self.payload); payload['caumax_consulta/metadata.txt']=payload['caumax_consulta/metadata.txt'].replace(old,new)
            with self.subTest(new=new), self.assertRaises(ValueError): self.validate(self.package_and_manifest(payload))

    def test_install_replaces_only_package_and_rollback_restores_old_files(self):
        files=self.validate(self.package_and_manifest())
        installed=self.root/'installed'; installed.mkdir()
        (installed/'__init__.py').write_bytes(b'OLD')
        (installed/'user_export.tif').write_bytes(b'USER DATA')
        transaction=module.FileTransaction(installed,self.root/'stage',self.root/'backup',files)
        transaction.apply()
        self.assertEqual((installed/'__init__.py').read_bytes(),b'NEW')
        self.assertEqual((installed/'user_export.tif').read_bytes(),b'USER DATA')
        transaction.rollback()
        self.assertEqual((installed/'__init__.py').read_bytes(),b'OLD')
        self.assertFalse((installed/'metadata.txt').exists())
        self.assertEqual((installed/'user_export.tif').read_bytes(),b'USER DATA')

    def test_failed_replace_rolls_back_already_written_files(self):
        files=self.validate(self.package_and_manifest())
        installed=self.root/'installed'; installed.mkdir()
        (installed/'metadata.txt').write_bytes(b'OLD')
        transaction=module.FileTransaction(installed,self.root/'stage',self.root/'backup',files)
        real=module.os.replace
        def replace(source,target):
            if Path(target).name=='__init__.py': raise PermissionError('file locked')
            return real(source,target)
        with patch.object(module.os,'replace',side_effect=replace), self.assertRaises(PermissionError): transaction.apply()
        transaction.rollback()
        self.assertEqual((installed/'metadata.txt').read_bytes(),b'OLD')
        self.assertFalse((installed/'__init__.py').exists())

    def test_cancel_download_never_stages_or_installs(self):
        task=SimpleNamespace(info=self.info,folder=self.root/'download',error='',files=(),isCanceled=lambda:False)
        with patch.object(module,'_download',side_effect=InterruptedError('cancelado')):
            self.assertFalse(module.DownloadTask.run(task))
        self.assertEqual(task.files,())

    def test_pending_work_blocks_download_and_displays_reason(self):
        window=QMainWindow(); bar=QgsMessageBar(window)
        updater=module.AutomaticUpdater(SimpleNamespace(mainWindow=lambda:window,messageBar=lambda:bar))
        with patch.object(module,'work_pending',return_value=True):
            self.assertFalse(updater.start(self.info))
        self.assertIsNone(updater.task)
        self.assertIn('Termina o cancela',bar.currentItem().text())
        window.deleteLater()

    def test_family_display_name_is_accepted_by_feed(self):
        xml=('''<plugins><pyqgis_plugin name="HidroCAUMAX v.1.0" version="1.0.11">
        <file_name>caumax_consulta.zip</file_name><experimental>False</experimental>
        <download_url>'''+self.info['download_url']+'''</download_url>
        <qgis_minimum_version>3.34</qgis_minimum_version><qgis_maximum_version>3.99</qgis_maximum_version>
        </pyqgis_plugin></plugins>''').encode()
        self.assertEqual(parse_update_feed(xml,'1.0.10',(3,44,15))[0],'available')

    def test_own_startup_errors_are_logged_without_blocking_rollback(self):
        original=Mock()
        runtime=SimpleNamespace(showException=original,loadPlugin=lambda name:True)
        def fail(name):
            runtime.showException(RuntimeError,RuntimeError('bad update'),None,'Failed '+name)
            return False
        runtime.startPlugin=fail
        with patch.object(module.QgsMessageLog,'logMessage') as logged:
            self.assertFalse(module._load_without_modal_error(runtime))
        self.assertIs(runtime.showException,original)
        original.assert_not_called()
        self.assertIn('bad update',logged.call_args.args[0])


if __name__=='__main__': unittest.main()
