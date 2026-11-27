"""Regression checks for future QGIS updates via the custom HTTPS feed."""
import configparser
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_release
import prepare_next_version


class FutureQgisUpdates(unittest.TestCase):
    def test_version_identity_and_published_feed(self):
        metadata=build_release.read_metadata(ROOT/'src/caumax_consulta')
        version = metadata['version']
        self.assertEqual(metadata['name'], 'HidroCAUMAX v.1.0')
        tag = prepare_next_version.public_tag(version)
        name = 'HidroCAUMAX_v' + version.replace('.', '_') + '.zip'
        xml=ET.fromstring(build_release.repository_xml(metadata,build_release.DEFAULT_REPOSITORY,
            tag,name))
        entry=xml.find('pyqgis_plugin')
        self.assertEqual(entry.get('version'),version)
        self.assertEqual(entry.findtext('file_name'),'caumax_consulta.zip')
        self.assertEqual(entry.findtext('download_url'),
            f'https://github.com/jmdiez2026/HidroCAUMAX/releases/download/{tag}/{name}')
    def test_next_version_can_be_prepared_without_touching_current_source(self):
        metadata=build_release.read_metadata(ROOT/'src/caumax_consulta')
        major,minor,patch=map(int, metadata['version'].split('.'))
        future=f'{major}.{minor}.{patch+1}'
        updates=prepare_next_version.plan(ROOT,future)
        paths={p.relative_to(ROOT).as_posix() for p in updates}
        self.assertIn('src/caumax_consulta/metadata.txt',paths)
        self.assertEqual(prepare_next_version.public_tag(future),'v'+future)
        self.assertIn(('version='+future).encode(),
                      updates[ROOT/'src/caumax_consulta/metadata.txt'])
        self.assertIn('README.md', paths)
    def test_git_workflow_detects_metadata_change_and_verifies_tag(self):
        workflow=(ROOT/'.github/workflows/release.yml').read_text()
        self.assertIn("'src/caumax_consulta/metadata.txt'",workflow)
        self.assertIn('refs/heads/main',workflow)
        self.assertIn('Verificar o crear etiqueta',workflow)
        self.assertIn('git/ref/tags/',workflow)
        self.assertIn('compare/{sha}...{branch["sha"]}',workflow)
        self.assertIn("comparison.get('status') != 'ahead'",workflow)
        self.assertIn('actions/deploy-pages@v4',workflow)
        self.assertNotIn('retire-older-releases',workflow)

if __name__=='__main__':unittest.main()
