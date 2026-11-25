"""Release contract checks; standard library only, no QGIS installation required."""
from __future__ import annotations
import configparser
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('release_builder',ROOT/'scripts'/'build_release.py')
builder=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
PUBLISH_SPEC=importlib.util.spec_from_file_location('release_publisher',ROOT/'scripts'/'publish_release.py')
publisher=importlib.util.module_from_spec(PUBLISH_SPEC)
with patch.dict('sys.modules',{'build_release':builder}):
    PUBLISH_SPEC.loader.exec_module(publisher)

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


def attach_windows_fixture(test):
    """Exercise bundling with a non-executable envelope and a fake extractor.

    The NSIS builder's own tests cover real PE/extraction. This fixture keeps
    the release and retry contracts testable using the standard library only.
    """
    base=test.manifest
    source_names=('scripts/build_windows_installer.py','packaging/windows/installer.nsi',
                  'packaging/windows/hidrocaumax.ico')
    for index,name in enumerate(source_names):
        path=test.folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(f'Versioned installer input {index}\n'.encode('ascii'))
    test.installer=test.output/('HidroCAUMAX_v'+base['version'].replace('.','_')+'_Setup.exe')
    test.installer_proof=test.output/'installer_manifest.json'
    prefix=b'MZ\0Non-executable packaging test envelope\0'
    test.installer.write_bytes(prefix+(test.output/base['package']).read_bytes())
    document=dict(schema_version=1,version=base['version'],plugin_id=builder.PLUGIN_ID,
        installer=test.installer.name,sha256=builder.sha256(test.installer),bytes=test.installer.stat().st_size,
        payload_zip=base['package'],payload_sha256=base['sha256'],payload_bytes=base['bytes'],
        payload_source_files=base['source_files'],nsis_version='fixture-only',
        icon_sha256=builder.sha256(test.folder/source_names[2]),
        script_sha256=builder.sha256(test.folder/source_names[1]),requires_admin=False,
        profile_root=r'%APPDATA%\QGIS\QGIS3\profiles',
        build_sources={name:builder.sha256(test.folder/name) for name in source_names})
    test.installer_proof.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    test.installer_verifications=[]
    def verify(exe,proof,package,release_manifest,*,source_root=None,extractor=None):
        proof=json.loads(Path(proof).read_text()) if not isinstance(proof,dict) else proof
        test.installer_verifications.append(str(exe))
        if Path(source_root)!=test.folder:
            raise ValueError('Installer build root differs.')
        if (proof['version']!=release_manifest['version'] or proof['plugin_id']!=builder.PLUGIN_ID
                or proof['payload_sha256']!=builder.sha256(package)
                or proof['payload_bytes']!=Path(package).stat().st_size
                or proof['payload_source_files']!=release_manifest['source_files']):
            raise ValueError('Installer payload manifest differs from release ZIP.')
        if (proof['sha256']!=builder.sha256(exe) or proof['bytes']!=Path(exe).stat().st_size):
            raise ValueError('Installer bytes differ from manifest.')
        if Path(exe).read_bytes()!=prefix+Path(package).read_bytes():
            raise ValueError('Extracted installer payload differs from release ZIP.')
        if proof['build_sources']!={name:builder.sha256(test.folder/name) for name in source_names}:
            raise ValueError('Installer build sources differ from manifest.')
        return proof
    for mocked in (patch.dict('sys.modules',{'build_windows_installer':SimpleNamespace(verify_installer=verify)}),
                   patch.object(builder,'DEFAULT_ROOT',test.folder)):
        mocked.start();test.addCleanup(mocked.stop)
    test.manifest=builder.build(test.source,test.output,reuse_zip=test.output/base['package'],
        installer=test.installer,installer_manifest=test.installer_proof)
    return document


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

    def test_public_v1_0_alias_preserves_qgis_three_part_version(self):
        (self.source/'metadata.txt').write_text(
            METADATA.replace('v1.2.2', 'v1.0').replace('version=1.2.2', 'version=1.0.0'),
            encoding='utf-8')
        manifest=self.build(tag='v1.0')
        self.assertEqual((manifest['tag'], manifest['version']), ('v1.0', '1.0.0'))
        self.assertEqual(manifest['package'], 'HidroCAUMAX_v1_0_0.zip')
        plugin=ET.parse(self.output/'plugins.xml').getroot().find('pyqgis_plugin')
        self.assertEqual(plugin.attrib, {'name':'HidroCAUMAX v1.0','version':'1.0.0'})
        self.assertEqual(plugin.findtext('download_url'),
            'https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.0/HidroCAUMAX_v1_0_0.zip')

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

    def test_new_publisher_accepts_a_pinned_zip_only_legacy_builder_signature(self):
        self.build()
        calls=[]
        def legacy(source,output,tag,repository,reuse_zip):
            calls.append(True)
            return builder.build(source,output,tag,repository,reuse_zip)
        with patch.object(publisher,'DEFAULT_ROOT',self.folder),patch.object(publisher,'build',side_effect=legacy):
            manifest,assets=publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY)
        self.assertEqual(calls,[True])
        self.assertNotIn('windows_installer',manifest)
        self.assertEqual(len(assets),4)


class WindowsReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='hidrocaumax-windows-bundle-')
        self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name)
        self.source=self.folder/'src'/'caumax_consulta';self.source.mkdir(parents=True)
        (self.source/'metadata.txt').write_text(METADATA,encoding='utf-8')
        (self.source/'__init__.py').write_text('def classFactory(iface):\n    return iface\n',encoding='utf-8')
        self.output=self.folder/'dist';self.manifest=builder.build(self.source,self.output)
        self.zip_bytes=(self.output/self.manifest['package']).read_bytes()
        self.document=attach_windows_fixture(self)
        mocked=patch.object(publisher,'DEFAULT_ROOT',self.folder);mocked.start();self.addCleanup(mocked.stop)

    def rebuild(self,**kwargs):
        arguments=dict(reuse_zip=self.output/self.manifest['package'],
                       installer=self.installer,installer_manifest=self.installer_proof)
        arguments.update(kwargs)
        return builder.build(self.source,self.output,**arguments)

    def test_same_zip_exe_manifest_checksums_and_pages_are_bound_together(self):
        self.assertEqual((self.output/self.manifest['package']).read_bytes(),self.zip_bytes)
        windows=self.manifest['windows_installer']
        self.assertEqual(windows['payload_sha256'],self.manifest['sha256'])
        self.assertEqual(windows['sha256'],builder.sha256(self.installer))
        self.assertEqual(windows['manifest_sha256'],builder.sha256(self.installer_proof))
        checksums=dict(line.split('  ')[::-1] for line in (self.output/'SHA256SUMS.txt').read_text().splitlines())
        self.assertEqual(set(checksums),{self.manifest['package'],'plugins.xml',self.installer.name,'installer_manifest.json'})
        for name,digest in checksums.items():self.assertEqual(digest,builder.sha256(self.output/name))
        self.assertEqual((self.output/'SHA256SUMS.txt').read_bytes(),(self.output/'site'/'SHA256SUMS.txt').read_bytes())
        self.assertIn(windows['download_url'],(self.output/'site'/'index.html').read_text())
        self.assertIn(windows['sha256'],(self.output/'release_notes.md').read_text())
        checked,assets=publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY)
        self.assertEqual(checked,self.manifest)
        self.assertEqual(len(assets),6)
        self.assertGreaterEqual(len(self.installer_verifications),2)

    def test_installer_inputs_must_be_supplied_together(self):
        for arguments in (dict(installer=self.installer),dict(installer_manifest=self.installer_proof)):
            target=self.folder/'invalid-output'
            with self.subTest(arguments=arguments),self.assertRaisesRegex(ValueError,'both'):
                builder.build(self.source,target,**arguments)
            self.assertFalse(target.exists())

    def test_wrong_version_and_unsafe_installer_filename_are_rejected(self):
        for name in ('../outside.exe','other.exe','HidroCAUMAX_v9_9_9_Setup.exe'):
            document=dict(self.document,installer=name)
            self.installer_proof.write_text(json.dumps(document))
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'filename'):
                self.rebuild()

    def test_changed_exe_is_rejected_even_if_its_own_hash_is_updated(self):
        self.installer.write_bytes(self.installer.read_bytes()+b'altered-payload')
        with self.assertRaisesRegex(ValueError,'Installer bytes differ'):
            self.rebuild()
        document=dict(self.document,sha256=builder.sha256(self.installer),bytes=self.installer.stat().st_size)
        self.installer_proof.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError,'payload differs'):
            self.rebuild()

    def test_stale_payload_or_build_source_proof_is_rejected(self):
        self.installer_proof.write_text(json.dumps(dict(self.document,payload_sha256='0'*64)))
        with self.assertRaisesRegex(ValueError,'payload manifest'):
            self.rebuild()
        self.installer_proof.write_text(json.dumps(self.document))
        (self.folder/'packaging/windows/installer.nsi').write_bytes(b'changed installer code')
        with self.assertRaisesRegex(ValueError,'build sources'):
            self.rebuild()

    def test_public_checksums_pages_and_installer_manifest_cannot_drift(self):
        for relative,message in (('SHA256SUMS.txt','bundle is inconsistent'),
                                 ('site/SHA256SUMS.txt','Pages bundle is inconsistent'),
                                 ('site/index.html','Pages bundle is inconsistent')):
            path=self.output/relative;original=path.read_bytes();path.write_bytes(original+b'changed')
            with self.subTest(path=relative),self.assertRaisesRegex(ValueError,message):
                publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY)
            path.write_bytes(original)
        self.installer_proof.write_text(self.installer_proof.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'manifest no longer matches'):
            publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY)

    def test_explicit_publisher_paths_must_match_finalized_installer(self):
        publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY,self.installer,self.installer_proof)
        with self.assertRaisesRegex(ValueError,'Explicit installer files'):
            publisher.validate_bundle(self.output,builder.DEFAULT_REPOSITORY,self.folder/self.installer.name,self.installer_proof)


class ReleasePublishingTests(unittest.TestCase):
    """Real bundles and hashes, with a stateful fake for remote GitHub/CLI I/O."""
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='hidrocaumax-publish-')
        self.addCleanup(self.temporary.cleanup)
        self.folder=Path(self.temporary.name)
        self.source=self.folder/'src'/'caumax_consulta'
        self.source.mkdir(parents=True)
        (self.source/'metadata.txt').write_text(METADATA,encoding='utf-8')
        (self.source/'__init__.py').write_text('def classFactory(iface):\n    return iface\n',encoding='utf-8')
        self.output=self.folder/'dist'
        self.manifest=builder.build(self.source,self.output)
        self.names=[self.manifest['package'],'SHA256SUMS.txt','plugins.xml','release_manifest.json']
        self.local={name:(self.output/name).read_bytes() for name in self.names}
        self.remote={}
        self.exists=True
        self.release=dict(id=101,html_url='https://github.com/example/project/releases/tag/v1.2.2',
                          draft=False,prerelease=False,body='Notes entered on the web.',name='Web title')
        self.head='a'*40
        self.reference=dict(type='commit',sha=self.head)
        self.tracked=['src/'+name for name in self.manifest['source_files']]
        self.dirty=False
        self.events=[]
        self.commands=[]
        self.mutations=[]
        self.fail_after=None
        self.before_upload=None
        self.after_upload=None
        self.after_download=None
        self.download_error=False
        self.console=io.StringIO()
        for mocked in (patch.object(publisher,'DEFAULT_ROOT',self.folder),
                       patch.object(publisher,'run',side_effect=self.run_cli),
                       patch.object(publisher,'api',side_effect=self.api),
                       patch('sys.stdout',self.console)):
            mocked.start()
            self.addCleanup(mocked.stop)

    def api(self, endpoint, *, missing_ok=False):
        if '/git/ref/tags/' in endpoint:
            return dict(object=deepcopy(self.reference))
        if '/git/tags/' in endpoint:
            return dict(object=dict(type='commit',sha=self.head))
        if '/releases/tags/' in endpoint:
            if not self.exists:
                self.assertTrue(missing_ok)
                return None
            return dict(deepcopy(self.release),assets=[dict(name=name) for name in self.remote])
        self.fail('Unexpected API request: '+endpoint)

    def run_cli(self, args, *, check=True):
        self.commands.append(list(args))
        stdout=''
        if args[:3]==['git','rev-parse','HEAD']:
            stdout=self.head
        elif args[:2]==['git','diff']:
            if self.dirty:
                raise RuntimeError('Uncommitted source changes')
        elif args[:2]==['git','ls-files']:
            stdout='\n'.join(self.tracked)
        elif args[:3]==['gh','release','download']:
            name=args[args.index('--pattern')+1]
            self.events.append(('download',name))
            if self.download_error:
                raise RuntimeError('Download failed')
            target=Path(args[args.index('--dir')+1])/name
            self.assertFalse(target.exists(),'Verification must use a fresh download directory')
            target.write_bytes(self.remote[name])
            if self.after_download:
                self.after_download(name)
        elif args[:3] in (['gh','release','upload'],['gh','release','create']):
            action=args[2]
            self.assertNotIn('--clobber',args)
            self.mutations.append(list(args))
            paths=list(map(Path,args[4:args.index('--repo')]))
            self.events.append((action,tuple(path.name for path in paths)))
            if action=='create':
                self.assertFalse(self.exists)
                self.exists=True
                self.release['body']=Path(args[args.index('--notes-file')+1]).read_text(encoding='utf-8')
                self.release['name']=args[args.index('--title')+1]
                self.release['prerelease']='--prerelease' in args
            if self.before_upload:
                self.before_upload()
            for count,path in enumerate(paths,1):
                if path.name in self.remote:
                    raise RuntimeError('An asset with this name already exists')
                self.remote[path.name]=path.read_bytes()
                if count==self.fail_after:
                    raise RuntimeError('Connection lost during upload')
            if self.after_upload:
                self.after_upload()
        else:
            self.fail('Unexpected command: '+repr(args))
        return subprocess.CompletedProcess(args,0,stdout,'')

    def publish(self, **kwargs):
        publisher.publish(self.output,builder.DEFAULT_REPOSITORY,**kwargs)

    def test_empty_web_release_receives_all_assets_and_preserves_notes(self):
        before=deepcopy(self.release)
        self.publish()
        self.assertEqual(self.remote,self.local)
        self.assertEqual(self.release,before)
        self.assertEqual(len(self.mutations),1)
        self.assertEqual(self.mutations[0][:4],['gh','release','upload','v1.2.2'])
        self.assertEqual(self.events,[('upload',tuple(self.names))]+[('download',name) for name in self.names])

    def test_partial_release_verifies_present_assets_before_uploading_only_missing(self):
        present=[self.names[0],self.names[2]]
        self.remote={name:self.local[name] for name in present}
        self.publish()
        missing=tuple(name for name in self.names if name not in present)
        self.assertEqual(self.events,[('download',name) for name in present]+
                         [('upload',missing)]+[('download',name) for name in self.names])
        self.assertEqual(self.remote,self.local)
        self.assertEqual(self.release['body'],'Notes entered on the web.')

    def test_complete_matching_release_is_read_only_even_with_unrelated_asset(self):
        self.remote=dict(self.local,**{'extra.txt':b'Untouched extra'})
        before=dict(self.remote)
        self.publish()
        self.assertEqual(self.remote,before)
        self.assertFalse(self.mutations)

    def test_any_existing_asset_mismatch_prevents_every_upload(self):
        for bad in self.names:
            with self.subTest(asset=bad):
                self.remote={bad:self.local[bad]+b'corrupt'}
                before=dict(self.remote)
                with self.assertRaisesRegex(ValueError,'Existing release asset differs'):
                    self.publish()
                self.assertFalse(self.mutations)
                self.assertEqual(self.remote,before)

    def test_later_mismatch_prevents_upload_after_other_assets_verify(self):
        self.remote={self.names[0]:self.local[self.names[0]],self.names[2]:b'wrong XML'}
        with self.assertRaisesRegex(ValueError,'Existing release asset differs'):
            self.publish()
        self.assertEqual(self.events,[('download',self.names[0]),('download',self.names[2])])
        self.assertFalse(self.mutations)

    def test_incomplete_release_with_unverifiable_extra_asset_is_not_mutated(self):
        self.remote={'extra.txt':b'No matching file in the bundle'}
        with self.assertRaisesRegex(ValueError,'outside the validated bundle'):
            self.publish()
        self.assertFalse(self.mutations)

    def test_draft_and_wrong_channel_are_rejected_without_upload(self):
        for draft,prerelease in ((True,False),(False,True)):
            with self.subTest(draft=draft,prerelease=prerelease):
                self.release.update(draft=draft,prerelease=prerelease)
                with self.assertRaisesRegex(ValueError,'channel/draft'):
                    self.publish()
                self.assertFalse(self.mutations)

    def test_bundle_tag_and_committed_source_checks_still_precede_mutations(self):
        (self.output/'plugins.xml').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'bundle is inconsistent'):
            self.publish()
        (self.output/'plugins.xml').write_bytes(self.local['plugins.xml'])
        self.reference['sha']='b'*40
        with self.assertRaisesRegex(ValueError,'tag does not point'):
            self.publish()
        self.reference['sha']=self.head
        self.dirty=True
        with self.assertRaisesRegex(RuntimeError,'Uncommitted source'):
            self.publish()
        self.dirty=False
        self.tracked=[]
        with self.assertRaisesRegex(ValueError,'must be committed'):
            self.publish()
        self.assertFalse(self.mutations)

    def test_annotated_tag_to_checkout_is_still_supported(self):
        self.reference=dict(type='tag',sha='b'*40)
        self.remote=dict(self.local)
        self.publish()
        self.assertFalse(self.mutations)

    def test_download_failure_prevents_upload(self):
        self.remote={self.names[0]:self.local[self.names[0]]}
        self.download_error=True
        with self.assertRaisesRegex(RuntimeError,'Download failed'):
            self.publish()
        self.assertFalse(self.mutations)

    def test_partial_upload_failure_can_be_retried_without_replacing_uploaded_file(self):
        self.fail_after=1
        with self.assertRaisesRegex(RuntimeError,'Connection lost'):
            self.publish()
        self.assertEqual(self.remote,{self.names[0]:self.local[self.names[0]]})
        self.fail_after=None
        self.events.clear()
        self.publish()
        self.assertEqual(self.events[0],('download',self.names[0]))
        self.assertEqual(self.events[1],('upload',tuple(self.names[1:])))
        self.assertEqual(self.remote,self.local)
        self.assertEqual(len(self.mutations),2)

    def test_concurrent_asset_name_collision_fails_without_overwriting_or_retrying(self):
        def competing_upload():
            self.remote[self.names[0]]=b'Another publisher uploaded this'
        self.before_upload=competing_upload
        with self.assertRaisesRegex(RuntimeError,'already exists'):
            self.publish()
        self.assertEqual(self.remote,{self.names[0]:b'Another publisher uploaded this'})
        self.assertEqual(len(self.mutations),1)

    def test_final_verification_rejects_changed_identity_channel_missing_file_and_wrong_bytes(self):
        changes=(lambda:self.release.update(id=202),
                 lambda:self.release.update(prerelease=True),
                 lambda:self.remote.pop(self.names[-1]),
                 lambda:self.remote.update({self.names[0]:b'Corrupted after upload'}))
        messages=('Release changed','channel/draft','still lacks','asset differs')
        for change,message in zip(changes,messages):
            with self.subTest(message=message):
                self.remote={}
                self.release.update(id=101,prerelease=False)
                self.after_upload=change
                previous=len(self.mutations)
                with self.assertRaisesRegex(ValueError,message):
                    self.publish()
                self.assertEqual(len(self.mutations),previous+1)

    def test_local_missing_asset_changed_during_remote_verification_is_not_uploaded(self):
        self.remote={self.names[0]:self.local[self.names[0]]}
        self.after_download=lambda name:(self.output/self.names[1]).write_bytes(b'changed during network I/O')
        with self.assertRaisesRegex(ValueError,'Local release asset changed'):
            self.publish()
        self.assertFalse(self.mutations)

    def test_new_release_creation_keeps_tag_verification_and_verifies_uploaded_bytes(self):
        self.exists=False
        self.publish()
        self.assertEqual(self.remote,self.local)
        self.assertEqual(len(self.mutations),1)
        self.assertIn('--verify-tag',self.mutations[0])
        self.assertEqual(self.mutations[0][2],'create')
        self.assertEqual(self.release['body'],(self.output/'release_notes.md').read_text(encoding='utf-8'))

    def test_dry_run_validates_locally_without_remote_commands(self):
        self.publish(dry_run=True)
        self.assertFalse(self.commands)
        self.assertFalse(self.mutations)
        self.assertEqual(json.loads(self.console.getvalue())['action'],'validate-only')

    def test_verify_only_never_creates_or_completes_a_release(self):
        self.exists=False
        with self.assertRaisesRegex(ValueError,'never creates'):
            self.publish(verify_only=True)
        self.exists=True
        self.remote={self.names[0]:self.local[self.names[0]]}
        with self.assertRaisesRegex(ValueError,'never uploads'):
            self.publish(verify_only=True)
        self.assertFalse(self.mutations)
        self.remote=dict(self.local)
        self.publish(verify_only=True)
        self.assertFalse(self.mutations)
        self.assertEqual(json.loads(self.console.getvalue())['action'],'verify-only')


class WindowsReleasePublishingTests(ReleasePublishingTests):
    """Apply all retry/identity/channel cases to the six-asset Windows bundle."""
    def setUp(self):
        super().setUp()
        self.legacy_local=dict(self.local)
        document=attach_windows_fixture(self)
        self.names.extend((self.installer.name,'installer_manifest.json'))
        self.local={name:(self.output/name).read_bytes() for name in self.names}
        self.tracked.extend(document['build_sources'])

    def test_retry_after_exe_upload_preserves_it_and_adds_only_missing_manifest(self):
        self.fail_after=5
        with self.assertRaisesRegex(RuntimeError,'Connection lost'):
            self.publish()
        self.assertIn(self.installer.name,self.remote)
        before=self.remote[self.installer.name]
        self.fail_after=None;self.events.clear()
        self.publish()
        self.assertEqual(self.remote[self.installer.name],before)
        self.assertEqual([event for event in self.events if event[0]=='upload'],
                         [('upload',('installer_manifest.json',))])
        self.assertEqual(self.remote,self.local)

    def test_untracked_installer_source_prevents_every_upload(self):
        self.tracked.remove('packaging/windows/installer.nsi')
        with self.assertRaisesRegex(ValueError,'installer build source must be committed'):
            self.publish()
        self.assertFalse(self.mutations)
        self.assertTrue(any(command[:2]==['git','diff'] and 'packaging' in command for command in self.commands))

    def test_installer_changed_during_remote_verification_is_not_uploaded(self):
        self.remote={self.names[0]:self.local[self.names[0]]}
        self.after_download=lambda name:self.installer.write_bytes(b'changed executable')
        with self.assertRaisesRegex(ValueError,'Local release asset changed'):
            self.publish()
        self.assertFalse(self.mutations)

    def test_changed_installer_before_release_creation_is_not_uploaded(self):
        self.exists=False
        original_api=self.api
        def changed(endpoint,*,missing_ok=False):
            value=original_api(endpoint,missing_ok=missing_ok)
            if '/releases/tags/' in endpoint:
                self.installer.write_bytes(b'changed before create')
            return value
        with patch.object(publisher,'api',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'Local release asset changed'):
                self.publish()
        self.assertFalse(self.mutations)
        self.assertFalse(self.exists)

    def test_previously_published_zip_only_checksums_are_never_replaced_to_add_exe(self):
        self.remote=dict(self.legacy_local)
        before=dict(self.remote)
        with self.assertRaisesRegex(ValueError,'Existing release asset differs'):
            self.publish()
        self.assertEqual(self.remote,before)
        self.assertFalse(self.mutations)


if __name__=='__main__':
    unittest.main()
