"""Privacy, actual Qt lifecycle and anonymous-network contracts for AI analysis."""
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from qgis.PyQt.QtCore import QByteArray, QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import QgsApplication
from caumax_consulta import ai_analysis as ai
from caumax_consulta.parameter_schema import METRICS
from caumax_consulta.raster_profiles import encode_profile


def response(text='Interpretación sintética de la cuenca.', reason='stop'):
    return json.dumps({'choices': [{'message': {'content': text},
                                    'finish_reason': reason}]}).encode()


def structured():
    return json.dumps(dict(overview='Interpretación sintética de la cuenca.',
        parameter_interpretations={metric.key:'Interpretación técnica de este parámetro.'
                                   for metric in METRICS},
        p0='Umbral inicial pendiente de aplicar beta.',
        daily_precipitation='Las precipitaciones describen cuantiles diarios puntuales promediados.',
        caumax_flows='Los caudales pertenecen al punto original.',
        decisions_52ic='Comparar recorridos conforme al apartado 2.2.2.5.',
        priority_checks='Comprobar cierre, cobertura y representatividad del MDT.'))


def sample_state():
    values = {metric.key: float(index + 1) for index, metric in enumerate(METRICS)}
    values['hypsometric'] = .44
    values['private_path'] = '/private/SECRET_DATA.tif'
    analysis = SimpleNamespace(values=values, cell_width=25., cell_height=25.,
        touches_boundary=True, geometry_wkb=b'SECRET_GEOMETRY',
        diagnostics={'analysis_surface': {'kind': 'smoothed'}, 'outlet_snap': {
            'selection_mode': 'upstream_only', 'distance_m': 12., 'section_offset_m': -4.,
            'original_cell_center': [1234567, 7654321], 'original_direction': 64,
            'upstream_path': [[1234567, 7654321]], 'query_x': 1234567}})
    query = SimpleNamespace(flow=48.5, period=100., x=1234567, y=7654321,
                            dem_path='/private/SECRET_DATA.tif')

    def result(mean):
        return dict(mean_mm=mean, basin_area_m2=1000., covered_area_m2=1000.,
            valid_area_m2=1000., coverage_fraction=1., valid_fraction=1.,
            pixel_count=4, status='ok', source_path='/private/SECRET_DATA.tif',
            notes=['SECRET_NOTE'], statistics=dict(median_mm=mean, min_mm=mean-2,
                                                   max_mm=mean+2, stddev_mm=1.))

    periods = (2, 5, 10, 25, 100, 500)
    hydrology = dict(p0_result=result(12.),
        rainfall_results={str(t): result(50.+index*10) for index,t in enumerate(periods)},
        input_period=100., input_precipitation=90., extrapolate=True)
    quantiles = dict(flows=[10., 20., 30., 40., 48.5, 70.], source_paths=['SECRET_PATH']*6,
                     point={'x':1234567, 'y':7654321}, input_period=100.,
                     input_flow=48.5, extrapolate=True)
    return analysis, query, dict(degree=15, geometry_wkb={'original':b'SECRET_GEOMETRY'},
                                basin_hydrology=hydrology, flow_quantiles=quantiles)


class ContextTests(unittest.TestCase):
    def test_all_parameters_and_requested_hydrology_are_in_the_numeric_allowlist(self):
        context = ai.build_context(*sample_state())
        self.assertEqual(len(context['parameters']), 19)
        self.assertEqual([p['key'] for p in context['parameters']], [p.key for p in METRICS])
        self.assertEqual(context['parameters'][3]['value'], [25.,25.])
        self.assertEqual(context['p0_initial_without_beta']['mean_mm'], 12.)
        self.assertEqual(len(context['daily_precipitation_mm']), 6)
        self.assertEqual(context['precipitation_query']['direct']['flow'], 90.)
        self.assertEqual(context['precipitation_query']['spatial_statistics_mm']['stddev_mm'], 1.)
        self.assertEqual(context['flow_query']['direct']['flow'], 48.5)
        self.assertEqual(context['original_caumax_query']['return_period_years'], 100.)
        self.assertEqual(context['outlet_adjustment']['selection_mode'], 'upstream_only')
        self.assertEqual(context['outlet_adjustment']['section_offset_m'], -4.)
        payload = ai.build_payload(context)
        encoded = json.dumps(payload, allow_nan=False)
        for forbidden in ('SECRET', '/private/', '1234567', '7654321', 'upstream_path',
                          'original_cell_center', 'original_direction', 'source_path', 'geometry_wkb'):
            self.assertNotIn(forbidden, encoded)
        self.assertNotIn('api_key', payload)
        self.assertTrue(payload['private'])

    def test_nonfinite_missing_parameters_remain_unavailable_not_zero(self):
        analysis,query,state=sample_state()
        analysis.values['tc_h']=float('nan')
        analysis.values['tri_m']=float('inf')
        analysis.values['melton']=None
        context=ai.build_context(analysis,query,state)
        metrics={p['key']:p['value'] for p in context['parameters']}
        for key in ('tc_h','tri_m','melton'):
            self.assertIsNone(metrics[key])
        json.dumps(context,allow_nan=False)

    def test_precipitation_query_uses_shared_cell_statistics_not_interpolated_map_means(self):
        analysis,query,state=sample_state()
        rain=state['basin_hydrology']['rainfall_results']
        rain['2']['mean_mm']=55.
        rain['2']['distribution']=encode_profile([10.,100.],[1.,1.],[0,1],'a'*64)
        rain['5']['mean_mm']=20.
        rain['5']['distribution']=encode_profile([20.],[1.],[0],'a'*64)
        state['basin_hydrology']['input_period']=math.sqrt(10)
        context=ai.build_context(analysis,query,state)
        selected=context['precipitation_query']
        self.assertAlmostEqual(selected['direct']['flow'],15.)
        self.assertAlmostEqual(selected['spatial_statistics_mm']['median_mm'],15.)
        self.assertNotIn('distribution',json.dumps(context))
        self.assertNotIn(rain['2']['distribution']['data'],json.dumps(context))

    def test_payload_keeps_normative_limits_and_longest_path_qualification(self):
        system=ai.build_payload(ai.build_context(*sample_state()))['messages'][0]['content']
        self.assertIn('MAYOR LONGITUD',system)
        self.assertIn('mayor tiempo',system)
        self.assertIn('tc<=0,25',system)
        self.assertIn('sin beta',system)
        self.assertIn(ai.NORM_URL,system)


class DecodeTests(unittest.TestCase):
    def test_complete_and_length_limited_responses_are_distinguished(self):
        self.assertEqual(ai.decode_response(response(' texto ')),('texto',False))
        self.assertEqual(ai.decode_response(response(' parcial ', 'length')),('parcial',True))

    def test_empty_malformed_missing_and_non_text_content_are_rejected(self):
        for raw in (b'',b'{',b'\xff',b'{}',b'[]',b'{"choices":[]}',response(''),
                    response('  '),response(None),response(['not text'])):
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                ai.decode_response(raw)

    def test_oversized_response_is_rejected_before_json_parsing(self):
        with self.assertRaisesRegex(ValueError,'tamaño'):
            ai.decode_response(b'x'*(ai.MAX_RESPONSE_BYTES+1))

    def test_structured_output_requires_all_nineteen_keys_and_sections(self):
        original=json.loads(structured())
        for change in ('missing_metric','extra_metric','missing_section','empty_comment'):
            broken=json.loads(structured())
            if change=='missing_metric':broken['parameter_interpretations'].pop('zmedian_m')
            elif change=='extra_metric':broken['parameter_interpretations']['invented']= 'Invented'
            elif change=='missing_section':broken.pop('caumax_flows')
            else:broken['parameter_interpretations']['tc_h']=' '
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'19 interpretaciones'):
                ai.render_structured_analysis(json.dumps(broken),ai.build_context(*sample_state()))

    def test_structured_render_owns_names_values_units_and_preserves_table_rows(self):
        context=ai.build_context(*sample_state())
        text=ai.render_structured_analysis(structured(),context)
        self.assertIn('| Parámetro | Valor | Unidad | Interpretación IA |\n|---',text)
        self.assertIn('| T (años) | Qci (m³/s) |\n|---',text)
        self.assertIn('| 100 | 48,5 |',text)
        self.assertIn('Consulta original CAUMAX: T = 100 años; Q = 48,5 m³/s.',text)
        self.assertEqual(text.count('Interpretación técnica de este parámetro.'),19)
        for metric in METRICS:self.assertIn(metric.label,text)

    def test_specific_observed_contradictions_are_marked_without_silent_rewriting(self):
        result=json.loads(structured())
        observed=dict(cell_resolution='La resolución garantiza la representación detallada.',
                      zmin_m='La altitud mínima indica el punto de cierre.',
                      zmax_m='La altitud máxima define el origen del escurrimiento.',
                      tc_h='El Tc permite usar el método racional sin subdivisión.',
                      melton='El índice muestra menor potencial de erosión superficial.')
        result['parameter_interpretations'].update(observed)
        findings=ai.review_findings(result)
        self.assertEqual(set(findings),set(observed))
        rendered=ai.render_structured_analysis(json.dumps(result),ai.build_context(*sample_state()))
        self.assertIn(ai.REVIEW_PREFIX,rendered)
        self.assertIn('Comprobaciones del programa (no generadas por IA)',rendered)
        for text in observed.values():self.assertIn(text,rendered)

    def test_explicit_negations_of_unsupported_claims_are_not_flagged(self):
        result=json.loads(structured())
        result['parameter_interpretations'].update(
            cell_resolution='La resolución no garantiza exactitud.',
            zmin_m='La altitud mínima no indica necesariamente el cierre.',
            zmax_m='La cota máxima no define necesariamente la cabecera.',
            tc_h='Este Tc no permite validar por sí solo el método racional.',
            hypsometric='El HI no utiliza la mediana.',
            melton='Este índice no demuestra menor erosión.')
        self.assertEqual(ai.review_findings(result),{})

    def test_live_response_fixture_does_not_confuse_altitudes_with_high_risk(self):
        result=json.loads(structured())
        result['parameter_interpretations']['hypsometric']=(
            'HI = 0,44 indica distribución de altitudes, pero no diagnostica edad ni peligro.')
        result['parameter_interpretations']['zmedian_m']=(
            'Altitud mediana de 740 m se usa para HI; no implica riesgo ni conformidad.')
        self.assertEqual(set(ai.review_findings(result)),{'zmedian_m'})

    def test_request_is_https_anonymous_cookie_free_and_not_disk_cached(self):
        request=ai.network_request()
        self.assertEqual(request.url().scheme(),'https')
        self.assertFalse(request.hasRawHeader(b'Authorization'))
        self.assertFalse(request.hasRawHeader(b'Cookie'))
        self.assertFalse(request.hasRawHeader(b'X-API-Key'))
        for key in (QNetworkRequest.CookieLoadControlAttribute,
                    QNetworkRequest.CookieSaveControlAttribute,
                    QNetworkRequest.AuthenticationReuseAttribute):
            self.assertEqual(request.attribute(key),QNetworkRequest.Manual)
        self.assertFalse(request.attribute(QNetworkRequest.CacheSaveControlAttribute))
        self.assertEqual(request.attribute(QNetworkRequest.RedirectPolicyAttribute),
                         QNetworkRequest.SameOriginRedirectPolicy)
        with patch.object(ai,'ENDPOINT','http://text.pollinations.ai/openai'):
            with self.assertRaises(ValueError):ai.network_request()


class FakeReply(QObject):
    readyRead=pyqtSignal()
    finished=pyqtSignal()

    def __init__(self):
        super().__init__()
        self.buffer=bytearray()
        self.status=200
        self.retry_after=b''
        self.aborted=False
        self.deleted=0
        self.empty_read_is_none=False

    def read(self,limit):
        if not self.buffer and self.empty_read_is_none:return None
        chunk=bytes(self.buffer[:limit]);del self.buffer[:limit]
        return chunk

    def attribute(self,key):return self.status
    def rawHeader(self,key):return QByteArray(self.retry_after)
    def abort(self):
        self.aborted=True
        self.finished.emit()
    def deleteLater(self):self.deleted+=1


class FakeManager:
    def __init__(self,reply):self.reply=reply;self.requests=[]
    def post(self,request,data):
        self.requests.append((request,bytes(data)))
        return self.reply


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QgsApplication.instance() or QgsApplication([],False)

    def setUp(self):
        ai._CACHE.clear();ai._NEXT_REQUEST_AT=0.;ai._ACTIVE_DIALOG=None
        self.reply=FakeReply();self.manager=FakeManager(self.reply)
        self.autostart=patch.object(ai.QTimer,'singleShot');self.autostart.start()
        self.network=patch.object(ai,'QgsNetworkAccessManager',
                                 SimpleNamespace(instance=lambda:self.manager));self.network.start()
        self.dialog=ai.AnalysisDialog(None,ai.build_context(*sample_state()))

    def tearDown(self):
        self.dialog.close();self.autostart.stop();self.network.stop()
        ai._CACHE.clear();ai._NEXT_REQUEST_AT=0.;ai._ACTIVE_DIALOG=None

    def complete(self,raw=None,status=200):
        self.reply.status=status
        self.reply.buffer.extend(response(structured()) if raw is None else raw)
        self.reply.finished.emit()

    def test_request_is_nonblocking_and_duplicate_start_does_not_post_twice(self):
        self.dialog.start();self.dialog.start()
        self.assertEqual(len(self.manager.requests),1)
        self.assertTrue(self.dialog.timer.isActive())
        self.assertTrue(self.dialog.close_button.isEnabled())
        self.assertFalse(self.dialog.copy_button.isEnabled())
        self.assertIs(self.reply.parent(),self.dialog)

    def test_complete_response_is_displayed_cached_and_reopened_without_network(self):
        self.dialog.start();self.complete()
        self.assertIn('Interpretación sintética',self.dialog.browser.toPlainText())
        self.assertTrue(self.dialog.copy_button.isEnabled())
        self.assertIn(self.dialog.key,ai._CACHE)
        self.dialog.start()
        self.assertEqual(len(self.manager.requests),1)
        self.assertIn('recuperado',self.dialog.status.text())
        self.assertFalse(self.dialog.cooldown.isActive())

    def test_flagged_interpretation_is_visible_for_review_but_not_cached_as_complete(self):
        result=json.loads(structured())
        result['parameter_interpretations']['zmin_m']='La altitud mínima indica el cierre.'
        self.dialog.start();self.complete(response(json.dumps(result)))
        self.assertIn('requieren revisión',self.dialog.status.text())
        self.assertIn(ai.REVIEW_PREFIX,self.dialog.result_text)
        self.assertTrue(self.dialog.copy_button.isEnabled())
        self.assertFalse(ai._CACHE)

    def test_reject_cancels_like_close_and_ignores_late_results(self):
        self.dialog.start();ai._ACTIVE_DIALOG=self.dialog
        self.dialog.reject()
        self.assertTrue(self.reply.aborted)
        self.assertIsNone(self.dialog.reply)
        self.assertIsNone(ai._ACTIVE_DIALOG)
        self.complete()
        self.dialog.start()
        self.assertEqual(len(self.manager.requests),1)
        self.assertFalse(ai._CACHE)
        self.assertFalse(self.dialog.timer.isActive())

    def test_timeout_aborts_and_never_invents_or_caches_analysis(self):
        self.dialog.start();self.dialog._timeout()
        self.assertTrue(self.reply.aborted)
        self.assertIn('tiempo de espera',self.dialog.status.text())
        self.assertFalse(self.dialog.result_text)
        self.assertFalse(ai._CACHE)

    def test_oversize_final_chunk_is_bounded_and_finished_is_not_reentrant(self):
        self.dialog.start()
        self.complete(b'x'*(ai.MAX_RESPONSE_BYTES*2))
        self.assertEqual(len(self.dialog.raw),ai.MAX_RESPONSE_BYTES+1)
        self.assertIn('tamaño',self.dialog.status.text())
        self.assertEqual(self.reply.deleted,1)
        self.assertFalse(self.reply.aborted)

    def test_oversize_ready_read_aborts_safely_even_when_finished_is_synchronous(self):
        self.dialog.start();self.reply.buffer.extend(b'x'*(ai.MAX_RESPONSE_BYTES*2))
        self.reply.readyRead.emit()
        self.assertTrue(self.reply.aborted)
        self.assertEqual(self.reply.deleted,1)
        self.assertIsNone(self.dialog.reply)
        self.assertFalse(ai._CACHE)

    def test_http_429_honors_bounded_retry_after_without_requesting_credentials(self):
        with patch.object(ai.time,'monotonic',return_value=100.):
            self.dialog.start();self.reply.retry_after=b'120';self.complete(b'',429)
            self.assertEqual(ai._NEXT_REQUEST_AT,220.)
            self.assertIn('No se necesita una clave',self.dialog.status.text())
            self.assertIn('120 s',self.dialog.retry_button.text())
            self.assertFalse(self.dialog.retry_button.isEnabled())
            self.assertFalse(self.dialog.result_text)

    def test_connection_failure_with_none_read_is_shown_without_a_crash(self):
        self.dialog.start();self.reply.empty_read_is_none=True;self.complete(b'',None)
        self.assertIn('conexión',self.dialog.status.text())
        self.assertFalse(self.dialog.copy_button.isEnabled())

    def test_truncated_response_is_marked_and_not_cached(self):
        self.dialog.start();self.complete(response('Respuesta parcial','length'))
        self.assertIn('incompleta',self.dialog.status.text())
        self.assertFalse(self.dialog.copy_button.isEnabled())
        self.assertFalse(ai._CACHE)

    def test_failed_retry_does_not_copy_stale_partial_text(self):
        self.dialog.start();self.complete(response('Texto previo','length'))
        self.dialog._display('Texto previo en pantalla')
        ai._NEXT_REQUEST_AT=0.
        self.reply=FakeReply();self.manager.reply=self.reply
        self.dialog.start();self.complete(b'',503)
        self.assertFalse(self.dialog.copy_button.isEnabled())
        self.assertEqual(self.dialog.result_text,'')
        self.assertIn('No se ha generado',self.dialog.browser.toPlainText())

    def test_start_network_exception_is_reported_in_the_dialog(self):
        with patch.object(self.manager,'post',side_effect=RuntimeError('network unavailable')):
            self.dialog.start()
        self.assertIn('iniciar la conexión',self.dialog.status.text())
        self.assertFalse(self.dialog.copy_button.isEnabled())
        self.assertFalse(ai._CACHE)

    def test_remote_markdown_resources_are_disabled(self):
        self.assertIsNone(self.dialog.browser.loadResource(2,ai.QUrl('https://example.org/a.png')))
        self.assertFalse(self.dialog.browser.openLinks())
        self.assertFalse(self.dialog.browser.openExternalLinks())


if __name__=='__main__':unittest.main()
