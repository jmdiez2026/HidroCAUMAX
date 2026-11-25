"""Real shapefile persistence and cautious station/inventory classifications."""
from collections import Counter
from functools import partial
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QEventLoop, QThread, QTimer
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsProject,
                       QgsRectangle, QgsVectorLayer)
from qgis.gui import QgsLayerTreeMapCanvasBridge, QgsMapCanvas, QgsMessageBar

from caumax_consulta import aemet_stations as stations
from caumax_consulta import map_services as maps
from caumax_consulta.delineation_cleanup import clear_delineation_layers


class AemetStationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.arguments = {}

    def create(self, **kwargs):
        layer = stations.create_station_layer(directory=self.folder / 'cache', **self.arguments, **kwargs)
        self.addCleanup(lambda: None if sip.isdeleted(layer) else sip.delete(layer))
        return layer

    def fixture(self, complete=True):
        base = {'schema_version': 1, 'crs': 'EPSG:4326', 'stations': []}
        for index in range(3):
            base['stations'].append({'indicative': f'000{index}A', 'name': f'Estación Á {index}',
                'province': 'Cáceres', 'altitude_m': 150.5 + index, 'longitude': -5.0 - index / 10,
                'latitude': 40.0, 'types': ['AUTOMATICAS', 'PLUVIOMETRICAS'], 'variables': 'Mp'})
        inventory = {'schema_version': 1, 'retrieved_at': '2026-11-25T12:00:00Z', 'complete': complete,
                     'stations': [{'indicative': '0000A', 'climatology': True, 'observation': False}]}
        for key, value in (('stations', base), ('inventory', inventory), ('provenance', {'source': 'Fixture'})):
            path = self.folder / (key + '.json')
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            self.arguments[key + '_path'] = path

    def test_bundled_snapshot_has_real_precipitation_points_and_dated_statuses(self):
        layer = self.create()
        self.assertEqual(layer.providerType(), 'ogr')
        self.assertEqual(layer.dataProvider().storageType(), 'ESRI Shapefile')
        self.assertEqual(layer.crs().authid(), 'EPSG:4326')
        self.assertEqual(layer.featureCount(), 3249)
        features = {str(feature['INDICATIVO']): feature for feature in layer.getFeatures()}
        self.assertEqual(len(features), 3249)
        self.assertIn('0016B', features)
        point = features['0016B'].geometry().asPoint()
        self.assertAlmostEqual(point.x(), 1.10889782032184, places=9)
        self.assertAlmostEqual(point.y(), 41.1541941622003, places=9)
        self.assertTrue(all(any(variable in feature['VARIABLES'] for variable in 'pPM')
                            for feature in features.values()))
        self.assertEqual(Counter(feature['OPENDATA'] for feature in features.values()),
                         {'SI': 859, 'NO_CONSTA': 2390})
        self.assertTrue(all(feature['BASE_ANO'] == 2017 for feature in features.values()))
        self.assertIn('16/04/2016', layer.metadata().abstract())
        self.assertIn('no garantiza una variable', layer.metadata().abstract())
        self.assertIn(layer.source(), layer.metadata().abstract())
        self.assertIn('copie también esta carpeta', layer.metadata().abstract())
        manifest = json.loads(Path(layer.customProperty('HidroCAUMAX/aemet_provenance')).read_text())
        self.assertTrue(manifest['opendata_complete'])
        self.assertEqual(manifest['station_count'], 3249)

    def test_status_requires_positive_match_or_complete_inventory_and_preserves_unicode(self):
        self.fixture(complete=False)
        layer = self.create()
        features = list(layer.getFeatures())
        self.assertEqual([feature['INDICATIVO'] for feature in features], ['0000A', '0001A', '0002A'])
        self.assertEqual(features[0]['NOMBRE'], 'Estación Á 0')
        self.assertEqual(features[0]['PROVINCIA'], 'Cáceres')
        self.assertEqual(features[0]['TIPO'], 'AUTOMATICAS; PLUVIOMETRICAS')
        self.assertEqual(features[0]['OD_LISTAS'], 'Climatología')
        self.assertEqual(Counter(feature['OPENDATA'] for feature in features), {'SI': 1, 'SIN_COMPR': 2})
        self.arguments['inventory_path'].write_text('{invalid', encoding='utf-8')
        unknown = self.create()
        self.assertEqual({feature['OPENDATA'] for feature in unknown.getFeatures()}, {'SIN_COMPR'})
        self.assertNotEqual(unknown.source(), layer.source())
        self.assertIn('3 sin comprobar', unknown.metadata().abstract())

    def test_renderer_filled_circles_and_cache_are_reusable(self):
        self.fixture()
        first = self.create()
        source = Path(first.source())
        stamp = {path.name: path.stat().st_mtime_ns for path in source.parent.iterdir()}
        second = self.create()
        self.assertEqual(second.source(), first.source())
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in source.parent.iterdir()}, stamp)
        self.assertEqual(first.renderer().classAttribute(), 'OPENDATA')
        colors = set()
        categories = first.renderer().categories()
        self.assertEqual([category.label() for category in categories], list(stations.STATUS_LABELS.values()))
        for category in categories:
            symbol = category.symbol()
            self.assertEqual(symbol.symbolLayer(0).properties()['name'], 'circle')
            self.assertEqual(symbol.color().alpha(), 255)
            colors.add(symbol.color().name())
        self.assertEqual(len(colors), 3)
        for suffix in ('.shp', '.shx', '.dbf', '.prj', '.cpg'):
            self.assertTrue(source.with_suffix(suffix).is_file())
        self.assertEqual(source.with_suffix('.cpg').read_text().strip(), 'UTF-8')

    def test_cancellation_publishes_no_partial_shapefile_and_duplicate_ids_are_rejected(self):
        self.fixture()
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 3
        with self.assertRaises(InterruptedError):
            stations.materialize_station_shapefile(self.folder / 'cache', **self.arguments, cancelled=cancelled)
        self.assertEqual(list((self.folder / 'cache').iterdir()), [])
        data = json.loads(self.arguments['stations_path'].read_text())
        data['stations'].append(data['stations'][0])
        self.arguments['stations_path'].write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'duplicado'):
            stations.materialize_station_shapefile(self.folder / 'cache', **self.arguments)

    def test_shp_catalogue_persists_style_and_identity_across_qgs_reload(self):
        self.fixture()
        project = QgsProject(); project.setCrs(QgsCoordinateReferenceSystem('EPSG:25830'))
        self.addCleanup(project.clear)
        factory = partial(stations.create_station_layer, directory=self.folder / 'cache', **self.arguments)
        first = maps.load_reference_layers(project, services=(maps.AEMET_SERVICE,), station_factory=factory)
        self.assertEqual((first.added, first.failed), (1, 0))
        layer = next(iter(project.mapLayers().values()))
        self.assertEqual(layer.customProperty(maps.SERVICE_PROPERTY), 'aemet')
        self.assertEqual(layer.featureCount(), 3)
        self.assertIn('copia publicada por Esri España en 2017', layer.metadata().abstract())
        path = self.folder / 'stations.qgs'
        self.assertTrue(project.write(str(path)))
        restored = QgsProject(); self.addCleanup(restored.clear)
        self.assertTrue(restored.read(str(path)))
        factory = Mock(side_effect=AssertionError('A repeated load must reuse the saved shapefile layer'))
        second = maps.load_reference_layers(restored, services=(maps.AEMET_SERVICE,), station_factory=factory)
        self.assertEqual((second.added, second.existing, second.failed), (0, 1, 0))
        factory.assert_not_called()
        saved = next(iter(restored.mapLayers().values()))
        self.assertEqual(saved.featureCount(), 3)
        self.assertEqual(saved.renderer().classAttribute(), 'OPENDATA')
        self.assertEqual([category.label() for category in saved.renderer().categories()], list(stations.STATUS_LABELS.values()))
        self.assertEqual(saved.fields()[0].alias(), 'Indicativo AEMET')
        self.assertEqual(clear_delineation_layers(restored), 0)

    def test_vector_task_creates_worker_layer_and_preserves_gui_crs_and_extent(self):
        self.fixture()
        project = QgsProject(); project.setCrs(QgsCoordinateReferenceSystem('EPSG:25830'))
        self.addCleanup(project.clear)
        window = QMainWindow(); self.addCleanup(sip.delete, window)
        canvas = QgsMapCanvas(window); canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        canvas.setExtent(QgsRectangle(100, 200, 500, 600)); extent = QgsRectangle(canvas.extent())
        bridge = QgsLayerTreeMapCanvasBridge(project.layerTreeRoot(), canvas)
        self.addCleanup(sip.delete, bridge); self.addCleanup(canvas.stopRendering)
        bar = QgsMessageBar(window)
        iface = SimpleNamespace(messageBar=lambda: bar, mapCanvas=lambda: canvas, layerTreeCanvasBridge=lambda: bridge)
        creation_threads = []
        def factory(title, cancelled):
            creation_threads.append(QThread.currentThread())
            return stations.create_station_layer(title, self.folder / 'cache', **self.arguments, cancelled=cancelled)
        loader = maps.ReferenceLayerLoader(iface, project=project, services=(maps.AEMET_SERVICE,), station_factory=factory)
        self.addCleanup(loader.dispose)
        loop = QEventLoop(); loader.busy_changed.connect(lambda busy: None if busy else loop.quit())
        timer = QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit); timer.start(5000)
        self.assertTrue(loader.load()); loop.exec_(); timer.stop()
        self.assertFalse(loader.busy, 'The station task did not finish')
        self.assertEqual(len(project.mapLayers()), 1)
        layer = next(iter(project.mapLayers().values()))
        self.assertIsNot(creation_threads[0], self.app.thread())
        self.assertIs(layer.thread(), self.app.thread())
        self.assertIs(layer.dataProvider().thread(), self.app.thread())
        self.assertEqual(canvas.extent(), extent)
        self.assertEqual(canvas.mapSettings().destinationCrs().authid(), 'EPSG:3857')
        self.assertEqual(project.crs().authid(), 'EPSG:25830')


if __name__ == '__main__':
    unittest.main()
