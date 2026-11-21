"""Portable consultation snapshots, hostile inputs and atomic write failures."""
import base64
from dataclasses import replace
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.PyQt.QtCore import QByteArray, QBuffer, QIODevice
from qgis.PyQt.QtGui import QImage
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta import session_store
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.grid import Cell
from caumax_consulta.metrics import shape_metrics
from caumax_consulta.parameter_schema import METRICS, interpret_value
from caumax_consulta.terrain import analyze_basin


class SessionStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='caumax_session_')
        self.folder = Path(self.temp.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.directions_path = self.folder / 'original-directions.tif'
        self.dem_path = self.folder / 'original-dem.tif'
        directions = np.zeros((5, 5), dtype=np.uint8)
        directions[1:4, 1:4] = [[0, 16, 16], [4, 0, 64], [1, 1, 64]]
        dem = np.full((5, 5), 80.0)
        profile = [(1, 1), (1, 2), (1, 3), (2, 3), (3, 3), (3, 2), (3, 1), (2, 1)]
        for index, (row, col) in enumerate(profile):
            dem[row, col] = 100 + index * 10
        self.raster(self.directions_path, directions, gdal.GDT_Byte, 255)
        self.raster(self.dem_path, dem, gdal.GDT_Float32, -9999)
        self.analysis = analyze_basin(self.directions_path, self.dem_path, 340770.25, 4647244.75)
        self.query = NativeQuery(
            177.125, 100, 340770.25, 4647244.75, self.crs,
            str(self.directions_path), str(self.dem_path), 'Caudal de ensayo',
            'saved-layer-id', str(self.folder / 'original-q100.tif'),
            Cell(1, 1, 340750, 4647250, 500, 500),
        )
        original = QgsGeometry()
        original.fromWkb(self.analysis.geometry_wkb)
        smoothed = original.smooth(1, 0.25)
        self.assertTrue(smoothed.isGeosValid())
        self.assertEqual(len(original.asMultiPolygon()[0]), 2, 'Fixture must contain a hole.')
        image = QImage(16, 12, QImage.Format_ARGB32)
        image.fill(0xffc7e8ff)
        array = QByteArray()
        stream = QBuffer(array)
        stream.open(QIODevice.WriteOnly)
        self.assertTrue(image.save(stream, 'PNG'))
        stream.close()
        bounds = original.boundingBox()
        self.state = {
            'original_checked': True, 'smooth_checked': True, 'degree': 1.5,
            'hypsometric_dimensionless': False,
            'course_visibility': {'main': True, 'central': False},
            'resolution_metadata': {},
            'geometry_wkb': {'original': bytes(original.asWkb()), 'suavizada': bytes(smoothed.asWkb())},
            'stats': {'original': self.stats(original), 'suavizada': self.stats(smoothed)},
            'map_png': bytes(array), 'map_caption': 'Cuenca de ensayo · Q = 177,125 m³/s',
            'map_extent': dict(xmin=bounds.xMinimum() - 100, xmax=bounds.xMaximum() + 100,
                               ymin=bounds.yMinimum() - 100, ymax=bounds.yMaximum() + 100,
                               crs_wkt=self.crs.toWkt()),
        }
        self.path = self.folder / 'consulta.caumax.json'

    def tearDown(self):
        self.temp.cleanup()

    def raster(self, path, array, datatype, nodata):
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, datatype)
        ds.SetGeoTransform((340000, 500, 0, 4648000, 0, -500))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.WriteArray(array)
        band = ds = None

    def stats(self, geometry):
        area, perimeter = geometry.area(), geometry.length()
        indices = shape_metrics(area, perimeter)
        return dict(area_km2=area / 1e6, perimeter_km=perimeter / 1000,
                    kc=indices.compactness, ic=indices.circularity,
                    delta_area_pct=100 * (area / self.analysis.area_m2 - 1),
                    delta_perimeter_pct=100 * (perimeter / self.analysis.perimeter_m - 1))

    def save(self, **changes):
        return session_store.save_session(self.path, changes.get('analysis', self.analysis),
                                          changes.get('query', self.query), changes.get('state', self.state))

    def changed_document(self, change):
        self.save()
        document = json.loads(self.path.read_text(encoding='utf-8'))
        change(document)
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        return self.path

    def drainage_fixture(self):
        line = QgsGeometry.fromWkt('LINESTRING (340750 4647250, 341250 4647250)')
        return dict(
            metadata={'taudem_version': '5.3.9', 'source': 'TauDEM D8FlowDir / StreamNet',
                      'visible': True, 'extra': {'resolution_m': 25.0}},
            threshold_area_m2=500000.0, recommended_threshold_area_m2=500000.0,
            automatic=True, flow_encoding='taudem', dem_path=str(self.dem_path),
            tau_d8_path=str(self.folder / 'tau_d8.tif'),
            caumax_d8_path=str(self.directions_path), network_path=str(self.folder / 'streams.shp'),
            network_crs_wkt=self.crs.toWkt(), network_visible=True, directions_visible=False,
            fields=[dict(name='LINKNO', type='int64', width=18, precision=0),
                    dict(name='LENGTH', type='real', width=18, precision=6),
                    dict(name='NAME', type='str', width=80, precision=0)],
            features=[dict(wkb=bytes(line.asWkb()),
                           attributes={'LINKNO': 9223372036854775806, 'LENGTH': 500.0, 'NAME': None})],
        )

    def quantiles_fixture(self):
        return dict(version=1, periods=[2, 5, 10, 25, 100, 500],
                    flows=[10.0, None, 20.0, 19.0, self.query.flow, 230.0],
                    source_paths=[str(self.folder / f'q{period}.tif') for period in (2, 5, 10, 25, 100, 500)],
                    point=dict(x=self.query.x, y=self.query.y, crs_wkt=self.crs.toWkt()),
                    input_period=70.5, input_flow=123.25, extrapolate=True,
                    method='piecewise_linear_q_log_t')

    def basin_hydrology_fixture(self, analysis=None):
        from caumax_consulta.basin_hydrology import basin_fingerprint
        analysis = self.analysis if analysis is None else analysis
        area = analysis.area_m2

        def stats(mean, covered=1.0, valid=1.0, status='ok'):
            return dict(source_path=str(self.folder / 'original-hydrology.tif'), mean_mm=mean,
                        basin_area_m2=area, covered_area_m2=area * covered,
                        valid_area_m2=area * valid, coverage_fraction=covered,
                        valid_fraction=valid, pixel_count=4 if covered else 0,
                        status=status, notes=['Media ponderada sobre área válida.'])
        return dict(version=1, basin_fingerprint=basin_fingerprint(analysis), basin_area_m2=area,
                    p0_result=stats(0.0),
                    rainfall_results={'2': stats(100.0), '5': stats(90.0, .75, .5, 'partial'),
                                      '10': stats(None, 1.0, 0.0, 'unavailable')},
                    input_period=12.5, input_precipitation=75.25, extrapolate=False,
                    method='piecewise_linear_p_log_t')

    def test_basin_hydrology_roundtrips_zero_partial_nodata_and_original_means_offline(self):
        state = {**self.state, 'basin_hydrology': self.basin_hydrology_fixture()}
        self.save(state=state)
        self.dem_path.unlink()
        self.directions_path.unlink()
        with patch('caumax_consulta.basin_hydrology.gdal.Open', side_effect=AssertionError('Offline restore')):
            restored = session_store.load_session(self.path)
        self.assertEqual(restored.smoothing_state, state)
        saved = restored.smoothing_state['basin_hydrology']
        self.assertEqual(saved['p0_result']['mean_mm'], 0)
        self.assertEqual(saved['rainfall_results']['5']['mean_mm'], 90)
        self.assertIsNone(saved['rainfall_results']['10']['mean_mm'])
        self.assertEqual(saved['rainfall_results']['10']['pixel_count'], 4)
        self.assertGreater(saved['rainfall_results']['2']['mean_mm'], saved['rainfall_results']['5']['mean_mm'])
        self.assertEqual(restored.analysis, self.analysis)

    def test_basin_hydrology_is_distinct_per_variant_and_legacy_has_no_fake_state(self):
        selected, state = self.multiresolution('25')
        for key, variant in state['variants'].items():
            data = self.basin_hydrology_fixture(variant['analysis'])
            data['p0_result']['mean_mm'] = float(key)
            data['extrapolate'] = key == '25'
            variant['smoothing_state']['basin_hydrology'] = data
        session_store.save_session(self.path, selected['analysis'], selected['query'],
                                   selected['smoothing_state'], resolution_state=state)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.resolution_state, state)
        self.assertNotEqual(restored.resolution_state['variants']['500']['smoothing_state']['basin_hydrology']['basin_fingerprint'],
                            restored.resolution_state['variants']['25']['smoothing_state']['basin_hydrology']['basin_fingerprint'])
        legacy_state = {key: value for key, value in self.state.items() if key != 'basin_hydrology'}
        self.save(state=legacy_state)
        self.assertNotIn('basin_hydrology', session_store.load_session(self.path).smoothing_state)

    def test_basin_hydrology_rejects_tampered_schema_fingerprint_and_input_before_write(self):
        changes = (
            lambda value: value.update(version=True),
            lambda value: value.update(version=2),
            lambda value: value.update(basin_fingerprint='f' * 64),
            lambda value: value.update(basin_fingerprint='not a SHA256'),
            lambda value: value.update(basin_area_m2=self.analysis.area_m2 + 100),
            lambda value: value.update(input_period=1),
            lambda value: value.update(input_precipitation=-1),
            lambda value: value.update(extrapolate=1),
            lambda value: value.update(method='other'),
            lambda value: value.update(extra=True),
            lambda value: value['rainfall_results'].update({'3': value['p0_result']}),
        )
        self.path.write_bytes(b'previous consultation')
        for change in changes:
            with self.subTest(change=change):
                data = self.basin_hydrology_fixture()
                change(data)
                with self.assertRaises(ValueError):
                    self.save(state={**self.state, 'basin_hydrology': data})
                self.assertEqual(self.path.read_bytes(), b'previous consultation')
        state = {**self.state, 'basin_hydrology': self.basin_hydrology_fixture()}
        changed_crs = replace(self.analysis, basin=replace(self.analysis.basin,
                             crs_wkt=QgsCoordinateReferenceSystem('EPSG:32630').toWkt()))
        with self.assertRaises(ValueError):
            self.save(analysis=changed_crs, state=state)

    def test_basin_hydrology_rejects_inconsistent_areas_coverage_status_and_types_on_load(self):
        changes = (
            lambda value: value.update(basin_area_m2=self.analysis.area_m2 + 10),
            lambda value: value.update(covered_area_m2=self.analysis.area_m2 + 10),
            lambda value: value.update(valid_area_m2=self.analysis.area_m2 + 10),
            lambda value: value.update(coverage_fraction=.5),
            lambda value: value.update(valid_fraction=.5),
            lambda value: value.update(mean_mm=None),
            lambda value: value.update(mean_mm=-1),
            lambda value: value.update(mean_mm=False),
            lambda value: value.update(mean_mm=float('inf')),
            lambda value: value.update(pixel_count=0),
            lambda value: value.update(pixel_count=True),
            lambda value: value.update(status='partial'),
            lambda value: value.update(status='unavailable'),
            lambda value: value.update(notes='not a list'),
            lambda value: value.update(notes=[{'not': 'text'}]),
            lambda value: value.update(source_path=123),
            lambda value: value.update(extra=True),
        )
        for change in changes:
            with self.subTest(change=change):
                self.save(state={**self.state, 'basin_hydrology': self.basin_hydrology_fixture()})
                document = json.loads(self.path.read_text(encoding='utf-8'))
                change(document['smoothing_state']['basin_hydrology']['p0_result'])
                self.path.write_text(json.dumps(document), encoding='utf-8')
                with self.assertRaises(ValueError):
                    session_store.load_session(self.path)

    def test_basin_hydrology_accepts_uncomputed_and_full_precision_boundary_roundoff(self):
        data = self.basin_hydrology_fixture()
        data.update(p0_result=None, rainfall_results={}, input_precipitation=0.0)
        self.save(state={**self.state, 'basin_hydrology': data})
        self.assertIsNone(session_store.load_session(self.path).smoothing_state['basin_hydrology']['p0_result'])
        data = self.basin_hydrology_fixture()
        stats = data['p0_result']
        stats['covered_area_m2'] -= self.analysis.area_m2 * 1e-9
        stats['valid_area_m2'] = stats['covered_area_m2']
        stats['coverage_fraction'] = stats['covered_area_m2'] / self.analysis.area_m2
        stats['valid_fraction'] = stats['coverage_fraction']
        self.save(state={**self.state, 'basin_hydrology': data})
        self.assertEqual(session_store.load_session(self.path).smoothing_state['basin_hydrology'], data)

    def test_drainage_network_and_quantiles_restore_offline_with_exact_original_state(self):
        state = {**self.state, 'drainage_state': self.drainage_fixture(),
                 'flow_quantiles': self.quantiles_fixture(), 'hypsometric_dimensionless': True}
        self.save(state=state)
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIsInstance(document['smoothing_state']['drainage_state']['features'][0]['wkb'], str)
        self.directions_path.unlink()
        self.dem_path.unlink()
        with patch('caumax_consulta.terrain.analyze_basin', side_effect=AssertionError('No terrain work')):
            restored = session_store.load_session(self.path)
        self.assertEqual(restored.smoothing_state, state)
        self.assertEqual(restored.query, self.query)
        self.assertEqual(restored.smoothing_state['drainage_state']['features'][0]['attributes']['LINKNO'],
                         9223372036854775806)
        self.assertEqual(restored.smoothing_state['flow_quantiles']['flows'][2:4], [20.0, 19.0])
        self.assertFalse(Path(restored.smoothing_state['drainage_state']['tau_d8_path']).exists())

    def test_drainage_unknown_fields_bad_geometry_attributes_and_thresholds_are_rejected(self):
        changes = (
            lambda value: value.update(unknown=True),
            lambda value: value.update(automatic='yes'),
            lambda value: value.update(threshold_area_m2=0),
            lambda value: value.update(recommended_threshold_area_m2=float('nan')),
            lambda value: value.update(flow_encoding='esri'),
            lambda value: value.update(network_crs_wkt='not a CRS'),
            lambda value: value.update(network_visible=1),
            lambda value: value['features'][0].update(wkb=self.analysis.geometry_wkb),
            lambda value: value['features'][0]['attributes'].update(LENGTH=float('inf')),
            lambda value: value['features'][0]['attributes'].update(LINKNO=True),
            lambda value: value['features'][0]['attributes'].update(NAME={'nested': 'not scalar'}),
            lambda value: value['features'][0]['attributes'].update(extra=1),
            lambda value: value['fields'][0].update(type='binary'),
        )
        self.path.write_bytes(b'previous consultation')
        for change in changes:
            with self.subTest(change=change):
                drainage = self.drainage_fixture()
                change(drainage)
                with self.assertRaises(ValueError):
                    self.save(state={**self.state, 'drainage_state': drainage})
                self.assertEqual(self.path.read_bytes(), b'previous consultation')

    def test_drainage_aggregate_binary_vertex_and_metadata_limits_are_enforced(self):
        drainage = self.drainage_fixture()
        feature = drainage['features'][0]
        drainage['features'] = [feature, feature]
        with patch.object(session_store, 'MAX_WKB_BYTES', len(feature['wkb']) * 2 - 1):
            with self.assertRaises(ValueError):
                session_store._drainage_state(drainage, encode=True)
        with patch.object(session_store, 'MAX_GEOMETRY_POINTS', 3):
            with self.assertRaises(ValueError):
                session_store._drainage_state(drainage, encode=True)
        with patch.object(session_store, 'MAX_METADATA_ITEMS', 5):
            with self.assertRaises(ValueError):
                session_store._drainage_state(drainage, encode=True)

    def test_drainage_preserves_xyz_and_rejects_m_srid_and_nonfinite_z(self):
        for wkt in ('LINESTRING Z (340750 4647250 100, 341250 4647250 120)',
                    'LINESTRING Z (340750 4647250 100, 340750 4647250 100)',
                    'MULTILINESTRING Z ((340750 4647250 100, 341250 4647250 120), '
                    '(341250 4647250 120, 341750 4647250 140))'):
            geometry = ogr.CreateGeometryFromWkt(wkt)
            for raw in (bytes(geometry.ExportToWkb()), bytes(geometry.ExportToIsoWkb())):
                with self.subTest(wkt=wkt, kind=struct.unpack_from('<I', raw, 1)[0]):
                    state = self.drainage_fixture()
                    state['features'][0]['wkb'] = raw
                    self.save(state={**self.state, 'drainage_state': state})
                    restored = session_store.load_session(self.path)
                    self.assertEqual(restored.smoothing_state['drainage_state']['features'][0]['wkb'], raw)
        for kind, coordinates in ((2002, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)),
                                  (0x20000002, (1.0, 2.0, 4.0, 5.0)),
                                  (1002, (1.0, 2.0, float('nan'), 4.0, 5.0, 6.0))):
            raw = b'\x01' + struct.pack('<II', kind, 2) + struct.pack('<' + 'd' * len(coordinates), *coordinates)
            state = self.drainage_fixture()
            state['features'][0]['wkb'] = raw
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.save(state={**self.state, 'drainage_state': state})
        # The existing analysis geometries retain their strict 2D contract.
        raw = bytes(ogr.CreateGeometryFromWkt('LINESTRING Z (0 0 1, 1 1 2)').ExportToWkb())
        with self.assertRaises(ValueError):
            session_store._wkb(raw, 'curso de análisis', {2}, [0])

    def test_hypsometric_curve_is_portable_and_rejects_incoherent_samples(self):
        curve = self.analysis.diagnostics['hypsometric_curve']
        self.assertEqual(curve['sample_count'], 201)
        self.save()
        self.assertEqual(session_store.load_session(self.path).analysis.diagnostics['hypsometric_curve'], curve)
        changes = (
            lambda item: item.update(area_km2=item['area_km2'] + 1),
            lambda item: item.update(valid_count=self.analysis.cell_count + 1),
            lambda item: item.update(sample_count=200),
            lambda item: item['fraction'].__setitem__(1, -1),
            lambda item: item['elevation_m'].__setitem__(1, item['zmax_m'] + 1),
            lambda item: item.update(hi_exact=0.99),
            lambda item: item.update(auc_approx=float('nan')),
        )
        for change in changes:
            with self.subTest(change=change):
                item = {**curve, 'fraction': list(curve['fraction']), 'elevation_m': list(curve['elevation_m'])}
                change(item)
                analysis = replace(self.analysis, diagnostics={**self.analysis.diagnostics, 'hypsometric_curve': item})
                with self.assertRaises(ValueError):
                    self.save(analysis=analysis)

    def test_quantiles_bad_catalogue_point_types_and_methods_are_rejected(self):
        changes = (
            lambda value: value.update(version=True),
            lambda value: value.update(periods=[2, 5, 10, 25, 100]),
            lambda value: value['flows'].__setitem__(0, -1),
            lambda value: value['flows'].__setitem__(0, float('nan')),
            lambda value: value.update(source_paths=[]),
            lambda value: value['point'].update(x=self.query.x + 100),
            lambda value: value['point'].update(crs_wkt='invalid'),
            lambda value: value.update(input_period=1),
            lambda value: value.update(input_flow=-1),
            lambda value: value.update(extrapolate=1),
            lambda value: value.update(method='invented'),
            lambda value: value.update(extra=True),
        )
        for change in changes:
            with self.subTest(change=change):
                value = self.quantiles_fixture()
                change(value)
                with self.assertRaises(ValueError):
                    self.save(state={**self.state, 'flow_quantiles': value})
        with self.assertRaises(ValueError):
            self.save(state={**self.state, 'hypsometric_dimensionless': 'yes'})

    def test_new_optional_states_restore_per_resolution_and_legacy_uses_dimensional_curve(self):
        selected, state = self.multiresolution('25')
        for key, variant in state['variants'].items():
            variant['smoothing_state'].update(
                drainage_state=self.drainage_fixture(), flow_quantiles=self.quantiles_fixture(),
                hypsometric_dimensionless=key == '25')
            variant['smoothing_state']['drainage_state']['threshold_area_m2'] = float(key) * 1000
        session_store.save_session(self.path, selected['analysis'], selected['query'],
                                   selected['smoothing_state'], resolution_state=state)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.resolution_state, state)
        self.save()
        document = json.loads(self.path.read_text(encoding='utf-8'))
        document['smoothing_state'].pop('hypsometric_dimensionless')
        document['smoothing_state'].pop('drainage_state', None)
        document['smoothing_state'].pop('flow_quantiles', None)
        self.path.write_text(json.dumps(document), encoding='utf-8')
        legacy = session_store.load_session(self.path)
        self.assertFalse(legacy.smoothing_state['hypsometric_dimensionless'])
        self.assertNotIn('drainage_state', legacy.smoothing_state)
        self.assertNotIn('flow_quantiles', legacy.smoothing_state)

    def snapped_fixture(self, moved=True):
        x = self.query.x + (self.analysis.cell_width if moved else 0)
        y = self.query.y
        query = replace(self.query, x=x, y=y,
                        cell=replace(self.query.cell, col=2 if moved else 1,
                                     x=self.query.cell.x + (self.analysis.cell_width if moved else 0)))
        basin = self.analysis.basin
        snap = dict(
            original_x=x, original_y=y, snapped_x=basin.outlet_x, snapped_y=basin.outlet_y,
            distance_m=math.hypot(x - basin.outlet_x, y - basin.outlet_y),
            search_radius_m=math.hypot(500, 500), minimum_upstream_area_m2=basin.area_m2 * 0.9,
            reference_area_m2=basin.area_m2, candidate_upstream_area_m2=basin.area_m2,
            method='nearest_main_channel_d8', status='snapped' if moved else 'unchanged',
            minimum_fraction=0.9, original_row=1, original_col=2 if moved else 1,
            snapped_row=1, snapped_col=1, candidate_count=3,
            original_cell_upstream_area_m2=basin.area_m2 - (basin.cell_width * basin.cell_height if moved else 0),
            candidate_touches_boundary=basin.touches_boundary, tie_break='row_col',
        )
        analysis = replace(self.analysis, selected_x=x, selected_y=y,
                           diagnostics={**self.analysis.diagnostics, 'outlet_snap': snap})
        return analysis, query

    def test_adjusted_outlet_restores_original_query_and_snap_offline(self):
        analysis, query = self.snapped_fixture()
        self.save(analysis=analysis, query=query)
        self.directions_path.unlink()
        self.dem_path.unlink()
        with patch('caumax_consulta.terrain.analyze_basin', side_effect=AssertionError('No recalculation')):
            restored = session_store.load_session(self.path)
        self.assertEqual(restored.analysis, analysis)
        self.assertEqual(restored.query, query)
        self.assertEqual((restored.query.flow, restored.query.period), (177.125, 100))
        self.assertEqual(restored.analysis.selected_x, query.x)
        self.assertNotEqual(restored.analysis.selected_x, restored.analysis.outlet_x)
        self.assertEqual(restored.smoothing_state, self.state)

    def test_full_engine_adjustment_diagnostic_roundtrips_and_bounds_its_text(self):
        from caumax_consulta.dem_hydrology import _flow_accumulation, _nearest_main_channel
        analysis, query = self.snapped_fixture()
        dataset = gdal.Open(str(self.directions_path))
        counts = _flow_accumulation(dataset.GetRasterBand(1))
        _, diagnostic = _nearest_main_channel(
            counts, dataset.GetGeoTransform(), query.x, query.y,
            analysis.area_m2, 0.9, math.hypot(500, 500))
        dataset = None
        diagnostic['candidate_touches_boundary'] = analysis.touches_boundary
        self.assertIn('reference', diagnostic)
        self.assertIn('policy', diagnostic)
        analysis = replace(analysis, diagnostics={**analysis.diagnostics, 'outlet_snap': diagnostic})
        self.save(analysis=analysis, query=query)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.analysis.diagnostics['outlet_snap'], diagnostic)
        previous = self.path.read_bytes()
        for key in ('reference', 'policy'):
            for invalid_text in (123, None, ['text'], 'x' * 4097, 'bad\x00text'):
                with self.subTest(key=key, value=invalid_text):
                    invalid_diagnostic = {**diagnostic, key: invalid_text}
                    invalid = replace(analysis, diagnostics={**analysis.diagnostics,
                                                            'outlet_snap': invalid_diagnostic})
                    with self.assertRaises(ValueError):
                        self.save(analysis=invalid, query=query)
                    self.assertEqual(self.path.read_bytes(), previous)

    def test_unchanged_snap_can_record_nonzero_distance_to_cell_center(self):
        analysis, query = self.snapped_fixture(moved=False)
        self.assertGreater(analysis.diagnostics['outlet_snap']['distance_m'], 0)
        self.save(analysis=analysis, query=query)
        self.assertEqual(session_store.load_session(self.path).analysis, analysis)
        # Minimal audited records remain readable when optional provenance is absent.
        core = {key: value for key, value in analysis.diagnostics['outlet_snap'].items()
                if key in {'original_x', 'original_y', 'snapped_x', 'snapped_y', 'distance_m',
                           'search_radius_m', 'minimum_upstream_area_m2', 'reference_area_m2',
                           'candidate_upstream_area_m2', 'method', 'status'}}
        analysis = replace(analysis, diagnostics={**analysis.diagnostics, 'outlet_snap': core})
        self.save(analysis=analysis, query=query)
        self.assertEqual(session_store.load_session(self.path).analysis, analysis)

    def test_corrupt_outlet_adjustment_is_rejected_without_replacing_destination(self):
        analysis, query = self.snapped_fixture()
        changes = (
            lambda snap: snap.update(original_x=query.x + 1),
            lambda snap: snap.update(snapped_x=analysis.outlet_x + 1),
            lambda snap: snap.update(distance_m=0),
            lambda snap: snap.update(search_radius_m=1),
            lambda snap: snap.update(candidate_upstream_area_m2=analysis.area_m2 + 500),
            lambda snap: snap.update(minimum_upstream_area_m2=analysis.area_m2 * 2),
            lambda snap: snap.update(minimum_fraction=0.8),
            lambda snap: snap.update(method='untrusted'),
            lambda snap: snap.update(status='unchanged'),
            lambda snap: snap.update(original_col=1),
            lambda snap: snap.update(candidate_count=0),
            lambda snap: snap.update(candidate_touches_boundary=not analysis.touches_boundary),
            lambda snap: snap.update(tie_break='unknown'),
            lambda snap: snap.update(distance_m=float('nan')),
            lambda snap: snap.update(extra='unknown'),
            lambda snap: snap.pop('reference_area_m2'),
        )
        self.path.write_bytes(b'previous valid project')
        for change in changes:
            with self.subTest(change=change):
                snap = dict(analysis.diagnostics['outlet_snap'])
                change(snap)
                invalid = replace(analysis, diagnostics={**analysis.diagnostics, 'outlet_snap': snap})
                with self.assertRaises(ValueError):
                    self.save(analysis=invalid, query=query)
                self.assertEqual(self.path.read_bytes(), b'previous valid project')
        for diagnostics in ({}, {'outlet_snap': None}):
            with self.subTest(diagnostics=diagnostics), self.assertRaises(ValueError):
                self.save(analysis=replace(analysis, diagnostics=diagnostics), query=query)

    def test_adjustment_checks_original_query_after_crs_transformation(self):
        analysis, query = self.snapped_fixture()
        source, target = osr.SpatialReference(), osr.SpatialReference()
        source.ImportFromEPSG(25830)
        target.ImportFromEPSG(4326)
        for reference in (source, target):
            reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        x, y, _ = osr.CoordinateTransformation(source, target).TransformPoint(query.x, query.y)
        query = replace(query, x=x, y=y, crs=QgsCoordinateReferenceSystem('EPSG:4326'), cell=None)
        self.save(analysis=analysis, query=query)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.query, query)
        self.assertEqual(restored.analysis, analysis)
        with self.assertRaises(ValueError):
            self.save(analysis=analysis, query=replace(query, x=x + 0.001))

    def test_roundtrip_preserves_all_metrics_geometry_holes_and_snapshot(self):
        self.assertEqual(self.save(), self.path)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.analysis, self.analysis)
        self.assertEqual(restored.query, self.query)
        self.assertEqual(restored.smoothing_state, self.state)
        self.assertEqual(len(METRICS), 19)
        for metric in METRICS:
            self.assertEqual(interpret_value(metric.key, restored.analysis.values[metric.key], restored.analysis.values),
                             interpret_value(metric.key, self.analysis.values[metric.key], self.analysis.values))
        self.assertEqual(restored.analysis.geometry_wkb, self.analysis.geometry_wkb)
        self.assertEqual(restored.analysis.main_course_wkb, self.analysis.main_course_wkb)
        self.assertEqual(restored.analysis.grid_wkb, self.analysis.grid_wkb)
        self.assertEqual(restored.smoothing_state['geometry_wkb'], self.state['geometry_wkb'])
        self.assertEqual(restored.smoothing_state['map_png'], self.state['map_png'])

    def test_restoration_needs_no_source_rasters_or_calculation(self):
        self.save()
        self.directions_path.unlink()
        self.dem_path.unlink()
        with patch('caumax_consulta.terrain.analyze_basin', side_effect=AssertionError('Must not recompute')):
            restored = session_store.load_session(self.path)
        self.assertEqual(restored.analysis.values, self.analysis.values)
        self.assertFalse(Path(restored.query.directions_path).exists())
        self.assertEqual(restored.smoothing_state['map_caption'], self.state['map_caption'])

    def test_fractional_degree_and_non_epsg_wkt_are_preserved(self):
        custom = QgsCoordinateReferenceSystem.fromProj(
            '+proj=tmerc +lat_0=0 +lon_0=3.12345 +k=0.9996 +x_0=500123.456 '
            '+y_0=1234.567 +datum=WGS84 +units=m +no_defs'
        )
        self.assertTrue(custom.isValid())
        self.assertFalse(custom.authid().startswith('EPSG:'))
        analysis = replace(self.analysis, basin=replace(self.analysis.basin, crs_wkt=custom.toWkt()))
        self.save(analysis=analysis, query=replace(self.query, crs=custom))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['query']['crs_wkt'], custom.toWkt())
        restored = session_store.load_session(self.path)
        self.assertTrue(restored.query.crs.isValid())
        source_srs, restored_srs = osr.SpatialReference(), osr.SpatialReference()
        source_srs.ImportFromWkt(custom.toWkt())
        restored_srs.ImportFromWkt(restored.query.crs.toWkt())
        self.assertTrue(source_srs.IsSame(restored_srs))
        self.assertEqual(restored.analysis.crs_wkt, custom.toWkt())
        self.assertEqual(restored.smoothing_state['degree'], 1.5)
        self.assertIs(type(restored.smoothing_state['degree']), float)

    def test_unknown_schema_duplicate_keys_and_unknown_fields_are_rejected(self):
        for change in (
            lambda doc: doc.update(version=2),
            lambda doc: doc.update(schema='qgis-qgz'),
            lambda doc: doc.update(run='untrusted code'),
            lambda doc: doc['query'].update(extra=True),
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    session_store.load_session(self.changed_document(change))
        self.path.write_text('{"schema":"caumax-consulta","schema":"duplicate"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            session_store.load_session(self.path)

    def test_nonfinite_values_invalid_crs_and_mismatched_cell_area_are_rejected(self):
        changes = (
            lambda doc: doc['analysis']['values'].update(tc_h=float('nan')),
            lambda doc: doc['analysis']['values'].update(zmedian_m='not numeric'),
            lambda doc: doc['query'].update(crs_wkt='invalid WKT'),
            lambda doc: doc['analysis']['basin'].update(cell_count=99),
            lambda doc: doc['analysis']['basin'].update(outlet_x=341250, outlet_y=4646750),
            lambda doc: doc['smoothing_state'].update(degree=45.1),
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    session_store.load_session(self.changed_document(change))

    def test_wkb_bad_base64_and_excessive_counts_fail_before_gdal(self):
        for encoded in ('not base64!', base64.b64encode(b'\x01' + struct.pack('<II', 6, 0xffffffff)).decode('ascii')):
            path = self.changed_document(lambda doc: doc['analysis']['basin'].update(geometry_wkb=encoded))
            with patch.object(session_store.ogr, 'CreateGeometryFromWkb', side_effect=AssertionError('Unsafe parse')):
                with self.assertRaises(ValueError):
                    session_store.load_session(path)

    def test_corrupt_png_and_inconsistent_preview_statistics_are_rejected(self):
        bad_png = self.state['map_png'][:-1] + bytes([self.state['map_png'][-1] ^ 1])
        with self.assertRaises(ValueError):
            session_store.load_session(self.changed_document(
                lambda doc: doc['smoothing_state'].update(map_png=base64.b64encode(bad_png).decode('ascii'))))
        with self.assertRaises(ValueError):
            session_store.load_session(self.changed_document(
                lambda doc: doc['smoothing_state']['stats']['suavizada'].update(area_km2=12345)))

    def test_failed_validation_does_not_touch_an_existing_destination(self):
        previous = b'previous user-owned consultation contents'
        self.path.write_bytes(previous)
        invalid = replace(self.analysis, values={**self.analysis.values, 'tc_h': float('inf')})
        with self.assertRaises(ValueError):
            self.save(analysis=invalid)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertFalse(list(self.folder.glob('.*.tmp')))

    def test_atomic_replace_failure_keeps_old_file_and_cleans_temporary(self):
        previous = b'previous consultation file'
        self.path.write_bytes(previous)
        before = set(self.folder.iterdir())
        with patch.object(session_store.os, 'replace', side_effect=OSError('replace denied')):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(set(self.folder.iterdir()), before)

    def test_size_bound_is_checked_before_json_parsing(self):
        self.save()
        with patch.object(session_store, 'MAX_FILE_BYTES', 128):
            with patch.object(session_store.json, 'loads', side_effect=AssertionError('Should not parse')):
                with self.assertRaises(ValueError):
                    session_store.load_session(self.path)

    def test_missing_cached_stats_are_derived_from_stored_geometry_only(self):
        state = dict(self.state)
        state.pop('stats')
        self.save(state=state)
        restored = session_store.load_session(self.path)
        for name, metrics in self.state['stats'].items():
            for key, value in metrics.items():
                self.assertAlmostEqual(restored.smoothing_state['stats'][name][key], value)

    def multiresolution(self, active='500'):
        """Two fully generated analyses of one native query at 500 m and 25 m."""
        fine_directions = self.folder / 'directions-25.tif'
        fine_dem = self.folder / 'dem-25.tif'
        directions = np.zeros((5, 5), dtype=np.uint8)
        directions[1:4, 1:4] = [[0, 16, 16], [4, 0, 64], [1, 1, 64]]
        for path, array, kind, nodata in (
            (fine_directions, directions, gdal.GDT_Byte, 255),
            (fine_dem, np.arange(25).reshape(5, 5) + 500, gdal.GDT_Float32, -9999),
        ):
            dataset = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, kind)
            dataset.SetGeoTransform((self.query.x - 37.5, 25, 0, self.query.y + 37.5, 0, -25))
            dataset.SetProjection(self.crs.toWkt())
            band = dataset.GetRasterBand(1)
            band.SetNoDataValue(nodata)
            band.WriteArray(array)
            band = dataset = None
        fine = analyze_basin(fine_directions, fine_dem, self.query.x, self.query.y)
        fine_query = replace(self.query, directions_path=str(fine_directions), dem_path=str(fine_dem))
        polygon = QgsGeometry()
        polygon.fromWkb(fine.geometry_wkb)
        bounds = polygon.boundingBox()
        fine_state = dict(
            original_checked=True, smooth_checked=False, degree=12.5,
            hypsometric_dimensionless=False,
            course_visibility={'main': True, 'central': False}, resolution_metadata={},
            geometry_wkb={'original': bytes(polygon.asWkb())},
            stats={'original': dict(area_km2=fine.area_m2 / 1e6, perimeter_km=fine.perimeter_m / 1000,
                                    kc=fine.values['kc'], ic=fine.values['ic'],
                                    delta_area_pct=0.0, delta_perimeter_pct=0.0)},
            map_png=self.state['map_png'], map_caption='Consulta con MDT de 25 m',
            map_extent=dict(xmin=bounds.xMinimum(), xmax=bounds.xMaximum(),
                            ymin=bounds.yMinimum(), ymax=bounds.yMaximum(), crs_wkt=self.crs.toWkt()),
        )
        variants = {
            '500': dict(analysis=self.analysis, query=self.query, smoothing_state=self.state),
            '25': dict(analysis=fine, query=fine_query, smoothing_state=fine_state),
        }
        state = dict(
            active_key=active,
            options=[
                dict(key='500', resolution=500.0, label='500 m (CAUMAX original)',
                     provider='CAUMAX', available=True),
                dict(key='25', resolution=25.0, label='25 m', provider='IGN', available=True,
                     download={'url': 'https://example.invalid/metadata-only', 'size_mib': 12.5,
                               'files': ['directions-25.tif', 'dem-25.tif']}),
            ],
            variants=variants,
        )
        return variants[active], state

    def save_multiresolution(self, active='500'):
        selected, state = self.multiresolution(active)
        session_store.save_session(self.path, selected['analysis'], selected['query'],
                                   selected['smoothing_state'], resolution_state=state)
        return selected, state

    def test_multiresolution_roundtrip_restores_both_results_and_either_active_key(self):
        for active in ('500', '25'):
            with self.subTest(active=active):
                selected, original = self.save_multiresolution(active)
                document = json.loads(self.path.read_text(encoding='utf-8'))
                self.assertEqual(document['version'], 2)
                restored = session_store.load_session(self.path)
                self.assertEqual(restored.resolution_state['active_key'], active)
                self.assertEqual(restored.resolution_state, original)
                self.assertEqual(restored.analysis, selected['analysis'])
                self.assertEqual(restored.query, selected['query'])
                variants = restored.resolution_state['variants']
                self.assertNotEqual(variants['25']['analysis'].values['area_km2'],
                                    variants['500']['analysis'].values['area_km2'])
                self.assertEqual(variants['25']['analysis'].values['cell_resolution'], (25, 25))
                self.assertEqual(variants['500']['analysis'].values['cell_resolution'], (500, 500))
                self.assertEqual(variants['25']['smoothing_state']['degree'], 12.5)
                self.assertEqual(variants['25']['smoothing_state']['map_png'], self.state['map_png'])

    def test_multiresolution_adjustment_reference_must_match_original_500_area(self):
        selected, state = self.multiresolution()
        analysis, query = self.snapped_fixture(moved=False)
        snap = dict(analysis.diagnostics['outlet_snap'])
        snap['reference_area_m2'] *= 0.5
        snap['minimum_upstream_area_m2'] *= 0.5
        invalid = replace(analysis, diagnostics={**analysis.diagnostics, 'outlet_snap': snap})
        state['variants']['500']['analysis'] = invalid
        with self.assertRaises(ValueError):
            session_store.save_session(self.path, invalid, query, self.state, resolution_state=state)
        state['variants']['500']['analysis'] = analysis
        session_store.save_session(self.path, analysis, query, self.state, resolution_state=state)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.resolution_state['variants']['500']['analysis'], analysis)

    def test_all_resolution_results_restore_offline_without_mdt_or_downloads(self):
        _, original = self.save_multiresolution('25')
        for variant in original['variants'].values():
            Path(variant['query'].directions_path).unlink()
            Path(variant['query'].dem_path).unlink()
        with patch('caumax_consulta.terrain.analyze_basin', side_effect=AssertionError('No recalculation')):
            restored = session_store.load_session(self.path)
        self.assertEqual(restored.resolution_state, original)
        for variant in restored.resolution_state['variants'].values():
            self.assertFalse(Path(variant['query'].dem_path).exists())
            self.assertTrue(variant['analysis'].geometry_wkb)
            self.assertTrue(variant['smoothing_state']['map_png'])

    def test_available_options_require_a_complete_variant_before_atomic_save(self):
        selected, state = self.multiresolution()
        state['variants'].pop('25')
        previous = b'preserve the previous multiresolution project'
        self.path.write_bytes(previous)
        with self.assertRaises(ValueError):
            session_store.save_session(self.path, selected['analysis'], selected['query'],
                                       selected['smoothing_state'], resolution_state=state)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertFalse(list(self.folder.glob('.*.tmp')))

    def test_unavailable_metadata_option_can_be_retained_without_invented_results(self):
        selected, state = self.multiresolution()
        state['options'].append(dict(key='5', resolution=5.0, label='5 m', provider='IGN',
                                     available=False, reason='Fuera de cobertura'))
        session_store.save_session(self.path, selected['analysis'], selected['query'],
                                   selected['smoothing_state'], resolution_state=state)
        restored = session_store.load_session(self.path)
        self.assertFalse(restored.resolution_state['options'][2]['available'])
        self.assertNotIn('5', restored.resolution_state['variants'])

    def test_corrupt_variant_active_mismatch_resolution_and_nested_sets_are_rejected(self):
        changes = (
            lambda doc: doc['resolution_state'].update(active_key='25'),
            lambda doc: doc['resolution_state']['variants']['25'].update(resolution_state={}),
            lambda doc: doc['resolution_state']['variants']['25']['analysis']['values'].update(zmedian_m='bad'),
            lambda doc: doc['resolution_state']['variants']['25']['query'].update(flow=999),
            lambda doc: doc['resolution_state']['options'][1].update(resolution=26),
            lambda doc: doc['resolution_state']['options'][1].update(available='true'),
        )
        for change in changes:
            with self.subTest(change=change):
                self.save_multiresolution()
                document = json.loads(self.path.read_text(encoding='utf-8'))
                change(document)
                self.path.write_text(json.dumps(document), encoding='utf-8')
                with self.assertRaises(ValueError):
                    session_store.load_session(self.path)

    def test_resolution_count_and_large_document_limits_preserve_destination(self):
        selected, state = self.multiresolution()
        for key in ('10', '5', '2', '1', '0.5'):
            state['options'].append(dict(key=key, resolution=float(key), label=key + ' m',
                                         provider='test', available=False))
        self.path.write_bytes(b'previous project')
        with self.assertRaises(ValueError):
            session_store.save_session(self.path, selected['analysis'], selected['query'],
                                       selected['smoothing_state'], resolution_state=state)
        self.assertEqual(self.path.read_bytes(), b'previous project')
        selected, state = self.multiresolution()
        with patch.object(session_store, 'MAX_FILE_BYTES', 512):
            with self.assertRaises(ValueError):
                session_store.save_session(self.path, selected['analysis'], selected['query'],
                                           selected['smoothing_state'], resolution_state=state)
        self.assertEqual(self.path.read_bytes(), b'previous project')

    def test_single_resolution_version_one_stays_backward_compatible(self):
        self.save()
        document = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(document['version'], 1)
        self.assertNotIn('resolution_state', document)
        restored = session_store.load_session(self.path)
        self.assertIsNone(restored.resolution_state)
        self.assertEqual(restored.analysis, self.analysis)

    def test_course_visibility_and_resolution_metadata_roundtrip_per_variant(self):
        selected, state = self.multiresolution('25')
        for key, variant in state['variants'].items():
            variant['smoothing_state']['course_visibility'] = {
                'main': key == '500', 'central': key == '25',
            }
            variant['smoothing_state']['resolution_metadata'] = dict(
                active_key=key, label=f'{key} m', resolution=float(key),
                provider='CAUMAX' if key == '500' else 'IGN/CNIG',
                source='' if key == '500' else 'https://example.invalid/provider-metadata',
            )
        session_store.save_session(self.path, selected['analysis'], selected['query'],
                                   selected['smoothing_state'], resolution_state=state)
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.resolution_state, state)
        self.assertEqual(restored.smoothing_state['course_visibility'], {'main': False, 'central': True})
        self.assertEqual(restored.smoothing_state['resolution_metadata']['resolution'], 25.0)

    def test_legacy_state_without_visibility_or_resolution_metadata_uses_defaults(self):
        self.save()
        document = json.loads(self.path.read_text(encoding='utf-8'))
        document['smoothing_state'].pop('course_visibility')
        document['smoothing_state'].pop('resolution_metadata')
        self.path.write_text(json.dumps(document), encoding='utf-8')
        restored = session_store.load_session(self.path)
        self.assertEqual(restored.smoothing_state['course_visibility'], {'main': True, 'central': False})
        self.assertEqual(restored.smoothing_state['resolution_metadata'], {})

    def test_invalid_course_visibility_and_resolution_metadata_are_rejected(self):
        changes = (
            lambda doc: doc['smoothing_state'].update(course_visibility={'main': 'true', 'central': False}),
            lambda doc: doc['smoothing_state'].update(course_visibility={'main': True}),
            lambda doc: doc['smoothing_state'].update(course_visibility={'main': True, 'central': False, 'other': True}),
            lambda doc: doc['smoothing_state'].update(resolution_metadata={'source': 'missing fields'}),
            lambda doc: doc['smoothing_state'].update(resolution_metadata={
                'active_key': '25', 'label': '25 m', 'resolution': 25.0,
                'provider': 'IGN', 'source': '',
            }),
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    session_store.load_session(self.changed_document(change))


if __name__ == '__main__':
    unittest.main()
