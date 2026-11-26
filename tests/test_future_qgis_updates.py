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
        self.assertEqual(metadata['version'],'1.0.7')
        self.assertEqual(metadata['name'],'HidroCAUMAX v1.0.7')
        self.assertEqual(prepare_next_version.public_tag(metadata['version']),'v1.0.7')
        xml=ET.fromstring(build_release.repository_xml(metadata,build_release.DEFAULT_REPOSITORY,
            'v1.0.7','HidroCAUMAX_v1_0_7.zip'))
        entry=xml.find('pyqgis_plugin')
        self.assertEqual(entry.get('version'),'1.0.7')
        self.assertEqual(entry.findtext('file_name'),'caumax_consulta.zip')
        self.assertEqual(entry.findtext('download_url'),
            'https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.0.7/HidroCAUMAX_v1_0_7.zip')
    def test_next_version_can_be_prepared_without_touching_current_source(self):
        updates=prepare_next_version.plan(ROOT,'1.0.8')
        paths={p.relative_to(ROOT).as_posix() for p in updates}
        self.assertIn('src/caumax_consulta/metadata.txt',paths)
        self.assertEqual(prepare_next_version.public_tag('1.0.8'),'v1.0.8')
        self.assertIn(b'version=1.0.8',updates[ROOT/'src/caumax_consulta/metadata.txt'])
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
