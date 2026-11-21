"""Release contract checks; standard library only, no QGIS installation required."""
from __future__ import annotations
import configparser
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts'/'build_release.py')
builder=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)

METADATA='''[general]
name=HidroCAUMAX v1.2.2
description=P₀ & Pd <cuenca>
about=Descripción de prueba.
version=1.2.2
qgisMinimumVersion=3.34
qgisMaximumVersion=3.99
author=JM
experimental=False
category=Raster
'''


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='hidrocaumax-build-')
        self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name)
        self.source=self.folder/'src'/'caumax_consulta'
        self.source.mkdir(parents=True)
        (self.source/'metadata.txt').write_text(METADATA,encoding='utf-8')
        (self.source/'__init__.py').write_text('def classFactory(iface):\n    return iface\n',encoding='utf-8')
        (self.source/'asset.txt').write_text('Bytes conservados: P₀\n',encoding='utf-8')
        self.output=self.folder/'dist'

    def build(self, **kwargs):
        return builder.build(self.source,self.output,**kwargs)

    def test_zip_structure_bytes_and_qgis_identity(self):
        manifest=self.build(tag='v1.2.2')
        self.assertEqual(manifest['version'],'1.2.2')
        self.assertEqual(manifest['package'],'HidroCAUMAX_v1_2_2.zip')
        with zipfile.ZipFile(self.output/manifest['package']) as archive:
            self.assertEqual({name.split('/')[0] for name in archive.namelist()},{'caumax_consulta'})
            self.assertEqual(archive.read('caumax_consulta/asset.txt'),(self.source/'asset.txt').read_bytes())
            self.assertTrue(all(item.compress_type==zipfile.ZIP_STORED for item in archive.infolist()))
        plugin=ET.parse(self.output/'plugins.xml').getroot().find('pyqgis_plugin')
        # This is the installed-id extraction used by QGIS pyplugin_installer.
        self.assertEqual(plugin.findtext('file_name').partition('.')[0],'caumax_consulta')
        self.assertEqual(plugin.findtext('download_url'),
            'https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.2.2/HidroCAUMAX_v1_2_2.zip')

    def test_xml_escapes_metadata_and_declares_compatibility(self):
        self.build()
        root=ET.parse(self.output/'plugins.xml').getroot()
        self.assertEqual(root.tag,'plugins')
        plugin=root.find('pyqgis_plugin')
        self.assertEqual(plugin.attrib,{'name':'HidroCAUMAX v1.2.2','version':'1.2.2'})
        self.assertEqual(plugin.findtext('description'),'P₀ & Pd <cuenca>')
        self.assertEqual(plugin.findtext('qgis_minimum_version'),'3.34')
        self.assertEqual(plugin.findtext('qgis_maximum_version'),'3.99')
        self.assertEqual(plugin.findtext('experimental'),'False')
        self.assertEqual((self.output/'plugins.xml').read_bytes(),(self.output/'site'/'plugins.xml').read_bytes())

    def test_repeated_build_is_byte_identical(self):
        first=self.build()
        first_bytes=(self.output/first['package']).read_bytes()
        second=builder.build(self.source,self.folder/'second')
        self.assertEqual(first,second)
        self.assertEqual(first_bytes,(self.folder/'second'/second['package']).read_bytes())

    def test_hashes_and_manifest_are_consistent(self):
        manifest=self.build()
        self.assertEqual(manifest['sha256'],builder.sha256(self.output/manifest['package']))
        for line in (self.output/'SHA256SUMS.txt').read_text().splitlines():
            digest,name=line.split('  ')
            self.assertEqual(digest,builder.sha256(self.output/name))
        self.assertEqual(json.loads((self.output/'release_manifest.json').read_text(encoding='utf-8')),manifest)

    def test_reuse_verified_zip_preserves_original_bytes(self):
        files=builder.source_files(self.source)
        supplied=self.folder/'supplied.zip'
        with zipfile.ZipFile(supplied,'w',zipfile.ZIP_STORED) as archive:
            for name,path in files.items():
                archive.writestr(name,path.read_bytes())
        manifest=self.build(reuse_zip=supplied)
        self.assertEqual(supplied.read_bytes(),(self.output/manifest['package']).read_bytes())

    def test_version_tag_mismatch_fails_before_output(self):
        with self.assertRaisesRegex(ValueError,'does not match'):
            self.build(tag='v1.2.1')
        self.assertFalse(self.output.exists())

    def test_experimental_channel_is_derived_from_metadata(self):
        (self.source/'metadata.txt').write_text(METADATA.replace('experimental=False','experimental=True'),encoding='utf-8')
        manifest=self.build()
        self.assertTrue(manifest['experimental'])
        self.assertEqual(ET.parse(self.output/'plugins.xml').find('.//experimental').text,'True')

    def test_unsafe_repository_names_are_rejected(self):
        for repository in ('https://github.com/x/y','x/../y','x/y;echo-secret','x/y\nother','-x/y'):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                self.build(repository=repository)

    def test_output_within_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'outside'):
            builder.build(self.source,self.source/'dist')

    def test_caches_are_excluded_and_unknown_hidden_files_rejected(self):
        cache=self.source/'__pycache__'
        cache.mkdir()
        (cache/'module.pyc').write_bytes(b'junk')
        self.assertEqual(len(builder.source_files(self.source)),3)
        (self.source/'.env').write_text('secret-placeholder',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'hidden file'):
            self.build()

    def test_symlinks_are_not_packaged(self):
        target=self.folder/'outside.txt'
        target.write_text('outside',encoding='utf-8')
        try:
            (self.source/'linked.txt').symlink_to(target)
        except OSError:
            self.skipTest('Creating symlinks is not permitted in this environment.')
        with self.assertRaisesRegex(ValueError,'Symbolic links'):
            self.build()

    def test_changed_source_prevents_reusing_stale_zip(self):
        manifest=self.build()
        (self.source/'asset.txt').write_text('changed',encoding='utf-8')
        with self.assertRaises(ValueError):
            builder.build(self.source,self.folder/'second',reuse_zip=self.output/manifest['package'])

    def test_duplicate_and_traversal_archive_members_are_rejected(self):
        files=builder.source_files(self.source)
        for bad in ('../outside','caumax_consulta/../../outside','other/metadata.txt','caumax_consulta\\metadata.txt'):
            path=self.folder/'bad.zip'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr(bad,b'x')
            with self.subTest(member=bad), self.assertRaises(ValueError):
                builder.validate_zip(path,files)
        path=self.folder/'duplicate.zip'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('caumax_consulta/asset.txt',b'a')
            archive.writestr('caumax_consulta/asset.txt',b'b')
        with self.assertRaisesRegex(ValueError,'duplicate'):
            builder.validate_zip(path,files)

    def test_compatibility_and_python_syntax_are_validated(self):
        (self.source/'metadata.txt').write_text(METADATA.replace('3.34','2.0'),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'3.34'):
            self.build()
        (self.source/'metadata.txt').write_text(METADATA,encoding='utf-8')
        (self.source/'broken.py').write_text('def missing_syntax(',encoding='utf-8')
        with self.assertRaises(SyntaxError):
            self.build()


if __name__=='__main__':
    unittest.main()
