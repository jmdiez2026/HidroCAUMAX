"""Rational basins retain their origin across offline projects, reports and exports."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from qgis.core import QgsApplication, QgsVariantUtils

from caumax_consulta import ai_analysis, reports, session_store
from caumax_consulta.watershed_export import make_layer, suggested_filename, write_layer
from test_rational_basin import rational_fixture


class RationalPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], False)
            cls.app.initQgis()

    @staticmethod
    def state():
        return dict(original_checked=True, smooth_checked=False, degree=3)

    def test_pending_result_roundtrips_offline_without_raster_quantiles(self):
        with rational_fixture() as fixture:
            target = fixture.folder / 'racional.caumax.json'
            session_store.save_session(target, fixture.analysis, fixture.query, self.state())
            for name in ('dem_path', 'directions_path'):
                Path(getattr(fixture.query, name)).unlink()
            with patch('caumax_consulta.terrain.analyze_basin', side_effect=AssertionError('offline')):
                restored = session_store.load_session(target)
            self.assertEqual(restored.query, fixture.query)
            self.assertIsNone(restored.query.flow)
            self.assertEqual(restored.query.source_kind, 'rational')
            self.assertEqual(restored.analysis, fixture.analysis)
            self.assertNotIn('flow_quantiles', restored.smoothing_state)

    def test_calculated_result_preserves_decimal_return_period(self):
        with rational_fixture() as fixture:
            context = deepcopy(fixture.query.rational_context)
            context.update(flow_status='calculated', flow_result=dict(flow=17., period=100.5))
            query = replace(fixture.query, flow=17., period=100.5, rational_context=context)
            target = fixture.folder / 'resultado.caumax.json'
            session_store.save_session(target, fixture.analysis, query, self.state())
            restored = session_store.load_session(target)
            self.assertEqual(restored.query.flow, 17.)
            self.assertEqual(restored.query.period, 100.5)
            self.assertEqual(restored.query.rational_context, context)

    def test_pending_result_roundtrips_through_resolution_variants(self):
        with rational_fixture() as fixture:
            state = self.state()
            resolutions = dict(active_key='500', options=[
                dict(key='500', resolution=500., label='Original método racional',
                     provider='CAUMAX', available=True),
                dict(key='25', resolution=25., label='MDT25', provider='IGN', available=False),
            ], variants={'500': dict(analysis=fixture.analysis, query=fixture.query,
                                      smoothing_state=state)})
            target = fixture.folder / 'resoluciones.caumax.json'
            session_store.save_session(target, fixture.analysis, fixture.query, state,
                                       resolution_state=resolutions)
            restored = session_store.load_session(target)
            self.assertEqual(restored.resolution_state['active_key'], '500')
            self.assertIsNone(restored.resolution_state['variants']['500']['query'].flow)

    def test_project_rejects_flow_invented_after_pending_native_calculation(self):
        with rational_fixture() as fixture:
            target = fixture.folder / 'racional.caumax.json'
            session_store.save_session(target, fixture.analysis, fixture.query, self.state())
            document = json.loads(target.read_text(encoding='utf-8'))
            document['query']['flow'] = 18.
            target.write_text(json.dumps(document), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'pendiente'):
                session_store.load_session(target)

    def test_old_raster_projects_without_origin_fields_still_open(self):
        with rational_fixture() as fixture:
            query = replace(fixture.query, source_kind='caumax_raster',
                            rational_context=None, flow=17., period=100)
            target = fixture.folder / 'anterior.caumax.json'
            session_store.save_session(target, fixture.analysis, query, self.state())
            document = json.loads(target.read_text(encoding='utf-8'))
            document['query'].pop('source_kind')
            document['query'].pop('rational_context')
            target.write_text(json.dumps(document), encoding='utf-8')
            restored = session_store.load_session(target)
            self.assertEqual(restored.query.source_kind, 'caumax_raster')
            self.assertEqual(restored.query.flow, 17.)

    def test_raster_quantiles_cannot_be_attached_to_rational_project(self):
        with rational_fixture() as fixture:
            state = self.state()
            state['flow_quantiles'] = {}
            with self.assertRaisesRegex(ValueError, 'no son resultados del método racional'):
                session_store.save_session(fixture.folder / 'invalid.caumax.json',
                                           fixture.analysis, fixture.query, state)

    def test_report_explains_pending_origin_and_ignores_raster_snapshot(self):
        with rational_fixture() as fixture:
            state = self.state()
            state['flow_quantiles'] = {'flows': [999999.] * 6}
            with patch.object(reports, '_flow_quantiles_report_html',
                              side_effect=AssertionError('raster quantiles')):
                html = reports.build_report_html(fixture.analysis, fixture.query, state)
            self.assertIn('Método racional', html)
            self.assertIn('Pendiente de cálculo', html)
            self.assertNotIn('Capa de caudales', html)
            self.assertNotIn('Caudal CAUMAX original', html)
            self.assertNotIn('999999', html)

    def test_ai_preserves_numeric_origin_without_exporting_native_sources(self):
        with rational_fixture() as fixture:
            context = ai_analysis.build_context(fixture.analysis, fixture.query, self.state())
            self.assertEqual(context['source_kind'], 'rational')
            self.assertIsNone(context['rational_query']['flow_m3_s'])
            self.assertEqual(context['rational_query']['status'], 'pending')
            self.assertNotIn('original_caumax_query', context)
            self.assertNotIn('original_caumax_quantiles_m3_s', context)
            encoded = json.dumps(context)
            self.assertNotIn(str(fixture.folder), encoded)
            self.assertNotIn(fixture.query.rational_context['mask_sha256'], encoded)
            answer = dict(overview='Interpretación de la cuenca.',
                          parameter_interpretations={m.key: 'Interpretación de este parámetro.'
                                                     for m in ai_analysis.METRICS},
                          p0='Umbral inicial.', daily_precipitation='Precipitación diaria.',
                          caumax_flows='Caudal pendiente.', decisions_52ic='Comprobar recorridos.',
                          priority_checks='Comprobar fuentes y cierre.')
            rendered = ai_analysis.render_structured_analysis(json.dumps(answer), context)
            self.assertIn('Caudal del método racional', rendered)
            self.assertIn('pendiente de cálculo', rendered)
            self.assertNotIn('Caudales CAUMAX del punto original', rendered)

    def test_export_preserves_null_q_and_native_mdt_cell(self):
        with rational_fixture() as fixture:
            query = replace(fixture.query, period=None)
            layer = make_layer(fixture.analysis, query)
            target = fixture.folder / 'cuenca_racional.gpkg'
            saved = write_layer(layer, target)
            feature = next(saved.getFeatures())
            self.assertTrue(QgsVariantUtils.isNull(feature['Q_M3S']))
            self.assertTrue(QgsVariantUtils.isNull(feature['T_ANOS']))
            self.assertEqual(feature['ORIGEN'], 'Metodo racional')
            self.assertEqual(feature['ESTADO_Q'], 'pendiente')
            self.assertEqual(feature['FILA_MDT'], fixture.query.cell.row + 1)
            self.assertEqual(feature['COL_MDT'], fixture.query.cell.col + 1)
            self.assertEqual(suggested_filename(query), 'cuenca_Racional.shp')


if __name__ == '__main__':
    unittest.main()
