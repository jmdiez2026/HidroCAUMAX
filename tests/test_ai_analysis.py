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

from qgis.PyQt.QtCore import QByteArray, QCoreApplication, QEvent, QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QApplication, QDialog, QWidget
from qgis.PyQt import sip
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


def compact():
    return json.dumps({'parameter_interpretations': {
        metric.key: 'Lectura técnica concreta sin inferir peligrosidad ni conformidad.'
        for metric in METRICS}}, ensure_ascii=False)


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

    def test_new_snap_modes_share_numeric_diagnostics_without_the_location_proof(self):
        for mode in ('upstream_main_course','upstream_lateral_main_course','user_selected_cell',
                     'upstream_nearest_branch','upstream_lateral_nearest_branch','reference_basin_overlap'):
            analysis,query,state=sample_state()
            snap=analysis.diagnostics['outlet_snap']
            snap.update(selection_mode=mode,reference_geometry_wkb_hex='SECRET_GEOMETRY',
                reference_crs_wkt='SECRET_CRS',reference_grid_bounds=[1234567,7654321,1234568,7654322],
                association_lateral_distance_m=18.,
                channel_anchor_upstream_area_m2=25000.,section_projection_x=1234567,
                section_projection_y=7654321,manual_requested_x=1234567,
                manual_requested_y=7654321,downstream_connection_path=[[1234567,7654321]],
                branch_distance_m=7.6, branch_runner_up_distance_m=95.25,
                branch_ambiguity_tolerance_m=25., manual_selection_scope='raster_extent',
                manual_raster_bounds=[1234567,7654321,1234568,7654322],
                manual_raster_size=[100,100],
                explicit_user_choice=(mode=='user_selected_cell'))
            context=ai.build_context(analysis,query,state)
            self.assertEqual(context['outlet_adjustment']['selection_mode'],mode)
            self.assertEqual(context['outlet_adjustment']['association_lateral_distance_m'],18.)
            self.assertEqual(context['outlet_adjustment']['branch_distance_m'],7.6)
            encoded=json.dumps(context,allow_nan=False)
            for forbidden in ('1234567','7654321','section_projection','manual_requested',
                              'downstream_connection_path','upstream_path','manual_raster','reference_grid',
                              'reference_geometry','reference_crs','SECRET'):
                self.assertNotIn(forbidden,encoded)
            if mode=='user_selected_cell':
                self.assertIs(context['outlet_adjustment']['explicit_user_choice'],True)
                self.assertEqual(context['outlet_adjustment']['manual_selection_scope'],'raster_extent')
            else:
                self.assertNotIn('manual_selection_scope',context['outlet_adjustment'])

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

    def test_abridged_ai_payload_only_requests_table_comments(self):
        context = ai.build_context(*sample_state())
        payload = ai.build_payload(context)
        user = json.loads(payload['messages'][1]['content'].split('\\n', 1)[1])
        self.assertEqual(len(user['parameters']), 19)
        self.assertEqual([row['key'] for row in user['parameters']],
                         [metric.key for metric in METRICS])
        self.assertTrue(all(set(row) == {'key', 'parameter', 'value', 'unit', 'limitations'}
                            for row in user['parameters']))
        self.assertNotIn('overview', payload['messages'][0]['content'])
        self.assertIn('19', payload['messages'][0]['content'])
        self.assertNotIn('original_cell_center', json.dumps(user))
        self.assertNotIn('statistics', json.dumps(user))
        self.assertNotIn('rainfall_results', json.dumps(user))
        self.assertEqual(payload['response_format'], {'type':'json_object'})
        self.assertLess(len(json.dumps(payload, ensure_ascii=False)), 16000)

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

    def test_compact_ai_json_populates_all_nineteen_rows(self):
        context = ai.build_context(*sample_state())
        rendered = ai.render_structured_analysis(compact(), context)
        self.assertEqual(rendered.count('Lectura técnica concreta'), len(METRICS))
        self.assertIn('| Parámetro | Valor | Unidad | Interpretación IA |', rendered)
        self.assertNotIn('## Precipitación máxima diaria', rendered)
        self.assertIn('Norma 5.2-IC', rendered)
        for metric in METRICS:
            self.assertIn(metric.label, rendered)
        broken = json.loads(compact())
        broken['parameter_interpretations'].pop('area_km2')
        with self.assertRaisesRegex(ValueError, '19 interpretaciones'):
            ai.render_structured_analysis(json.dumps(broken), context)
        broken = json.loads(compact())
        broken['parameter_interpretations']['fake'] = 'Texto ajeno.'
        with self.assertRaisesRegex(ValueError, '19 interpretaciones'):
            ai.render_structured_analysis(json.dumps(broken), context)

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


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)

    def setUp(self):
        ai._CACHE.clear();ai._NEXT_REQUEST_AT=0.
        self.reply=FakeReply();self.manager=FakeManager(self.reply)
        self.autostart=patch.object(ai.QTimer,'singleShot');self.autostart_mock=self.autostart.start()
        self.network=patch.object(ai,'QgsNetworkAccessManager',
                                 SimpleNamespace(instance=lambda:self.manager));self.network.start()
        self.requests=[]
        self.received=[]
        self.statuses=[]
        self.finished=[]
        self.new_request()

    def new_request(self, parent=None, consumer=None):
        self.request=ai.request_interpretations(parent,*sample_state(),
            on_interpretations=consumer or (lambda values:self.received.append(dict(values))),
            on_status=self.statuses.append,on_finished=lambda:self.finished.append(True))
        self.requests.append(self.request)
        return self.request

    def tearDown(self):
        for request in self.requests:
            if not sip.isdeleted(request):
                request.cancel()
                request.deleteLater()
        self.autostart.stop();self.network.stop()
        ai._CACHE.clear();ai._NEXT_REQUEST_AT=0.
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def complete(self,raw=None,status=200):
        self.reply.status=status
        self.reply.buffer.extend(response(structured()) if raw is None else raw)
        self.reply.finished.emit()

    def test_entry_point_never_creates_a_widget_and_starts_after_return(self):
        before=set(QApplication.allWidgets())
        request=self.new_request()
        self.assertFalse(isinstance(request,QWidget))
        self.assertFalse(isinstance(request,QDialog))
        self.assertEqual(set(QApplication.allWidgets()),before)
        self.assertFalse(self.manager.requests)
        self.assertEqual(self.autostart_mock.call_args.args,(0,request.start))
        request.start();self.complete()
        self.assertEqual(set(QApplication.allWidgets()),before)
        self.assertEqual(len(self.received),1)
        self.assertEqual(len(self.finished),1)

    def test_request_is_nonblocking_and_duplicate_start_does_not_post_twice(self):
        self.request.start();self.request.start()
        self.assertEqual(len(self.manager.requests),1)
        self.assertTrue(self.request.timer.isActive())
        self.assertFalse(self.request.is_finished)
        self.assertEqual(self.statuses,['Interpretando los parámetros con IA…'])
        self.assertIs(self.reply.parent(),self.request)

    def test_complete_response_is_cached_and_finished_exactly_once(self):
        self.request.start();self.complete()
        self.assertIn('Interpretación sintética',self.request.result_text)
        self.assertIn(self.request.key,ai._CACHE)
        self.assertTrue(self.request.is_finished)
        self.assertFalse(self.request.timer.isActive())
        self.request.start();self.request.cancel();self.request._finished()
        self.assertEqual(len(self.manager.requests),1)
        self.assertEqual(len(self.received),1)
        self.assertEqual(len(self.finished),1)
        self.assertEqual(set(self.received[0]),{metric.key for metric in METRICS})

    def test_flagged_interpretation_is_delivered_for_review_but_not_cached(self):
        result=json.loads(structured())
        result['parameter_interpretations']['zmin_m']='La altitud mínima indica el cierre.'
        self.request.start();self.complete(response(json.dumps(result)))
        self.assertIn('requieren revisión',self.request.status_text)
        self.assertIn(ai.REVIEW_PREFIX,self.request.result_text)
        self.assertFalse(ai._CACHE)
        self.assertEqual(len(self.received),1)
        text=self.received[0]['zmin_m']
        self.assertTrue(text.startswith(ai.REVIEW_PREFIX))
        self.assertIn('Hmín no identifica necesariamente el cierre de la cuenca.',text)
        self.assertIn('**Texto recibido de IA:**',text)
        self.assertNotEqual(text,result['parameter_interpretations']['zmin_m'])
        self.assertIn(text.replace('|','\\|'),self.request.result_text)
        self.assertEqual(len(self.finished),1)

    def test_cancel_aborts_and_ignores_late_results(self):
        self.request.start();self.request.cancel()
        self.assertTrue(self.reply.aborted)
        self.assertIsNone(self.request.reply)
        self.complete();self.request.start();self.request.cancel()
        self.assertEqual(len(self.manager.requests),1)
        self.assertFalse(ai._CACHE)
        self.assertFalse(self.request.timer.isActive())
        self.assertFalse(self.received)
        self.assertEqual(len(self.finished),1)

    def test_timeout_aborts_and_never_invents_or_caches_analysis(self):
        self.request.start();self.request._timeout()
        self.assertTrue(self.reply.aborted)
        self.assertIn('tiempo de espera',self.request.status_text)
        self.assertFalse(self.request.result_text)
        self.assertFalse(ai._CACHE)
        self.assertFalse(self.received)
        self.assertEqual(len(self.finished),1)

    def test_oversize_final_chunk_is_bounded_and_finished_is_not_reentrant(self):
        self.request.start()
        self.complete(b'x'*(ai.MAX_RESPONSE_BYTES*2))
        self.assertEqual(len(self.request.raw),ai.MAX_RESPONSE_BYTES+1)
        self.assertIn('tamaño',self.request.status_text)
        self.assertEqual(self.reply.deleted,1)
        self.assertFalse(self.reply.aborted)
        self.assertEqual(len(self.finished),1)

    def test_oversize_ready_read_aborts_safely_when_finished_is_synchronous(self):
        self.request.start();self.reply.buffer.extend(b'x'*(ai.MAX_RESPONSE_BYTES*2))
        self.reply.readyRead.emit()
        self.assertTrue(self.reply.aborted)
        self.assertEqual(self.reply.deleted,1)
        self.assertIsNone(self.request.reply)
        self.assertFalse(ai._CACHE)
        self.assertEqual(len(self.finished),1)

    def test_http_429_honors_bounded_retry_after_without_credentials(self):
        with patch.object(ai.time,'monotonic',return_value=100.):
            self.request.start();self.reply.retry_after=b'120';self.complete(b'',429)
            self.assertEqual(ai._NEXT_REQUEST_AT,220.)
            self.assertIn('No se necesita una clave',self.request.status_text)
            self.assertFalse(self.request.result_text)
            self.new_request().start()
            self.assertIn('120 s',self.request.status_text)
            self.assertEqual(len(self.manager.requests),1)
            self.assertEqual(len(self.finished),2)

    def test_rate_limit_does_not_post_automatically_and_new_attempt_can_retry(self):
        with patch.object(ai.time,'monotonic',return_value=100.):
            ai._NEXT_REQUEST_AT=145.
            self.request.start()
            self.assertIn('45 s',self.request.status_text)
            self.assertTrue(self.request.is_finished)
            self.assertFalse(self.manager.requests)
        with patch.object(ai.time,'monotonic',return_value=146.):
            self.new_request().start();self.complete()
            self.assertEqual(len(self.manager.requests),1)
            self.assertEqual(len(self.received),1)
            self.assertEqual(len(self.finished),2)

    def test_connection_failure_with_none_read_is_reported_without_crash(self):
        self.request.start();self.reply.empty_read_is_none=True;self.complete(b'',None)
        self.assertIn('conexión',self.request.status_text)
        self.assertFalse(self.request.interpretations)
        self.assertEqual(len(self.finished),1)

    def test_truncated_response_is_marked_and_not_cached(self):
        self.request.start();self.complete(response('Respuesta parcial','length'))
        self.assertIn('incompleta',self.request.status_text)
        self.assertFalse(self.request.interpretations)
        self.assertFalse(ai._CACHE)
        self.assertFalse(self.received)
        self.assertEqual(len(self.finished),1)

    def test_compact_response_success_even_when_provider_marks_length(self):
        self.request.start()
        self.complete(response(compact(), 'length'))
        self.assertTrue(self.request.is_finished)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(set(self.received[0]), {metric.key for metric in METRICS})
        self.assertIn('Interpretación completada', self.request.status_text)
        self.assertIn(self.request.key, ai._CACHE)
        self.assertEqual(len(self.finished), 1)

    def test_callback_receives_all_reviewed_rows_without_markdown_parsing(self):
        result=json.loads(structured())
        result['parameter_interpretations']={metric.key:'Lectura específica de '+metric.key+'.'
                                              for metric in METRICS}
        result['parameter_interpretations']['area_km2']=' Primera línea | alternativa.\nSegunda línea. '
        self.request.start();self.complete(response(json.dumps(result)))
        self.assertEqual(len(self.received),1)
        delivered=self.received[0]
        self.assertEqual(list(delivered),[metric.key for metric in METRICS])
        for metric in METRICS:
            expected=result['parameter_interpretations'][metric.key].replace('\n',' ').strip()
            self.assertEqual(delivered[metric.key],expected)
            self.assertIn(' | '+expected.replace('|','\\|')+' |',self.request.result_text)
        self.assertEqual(delivered['area_km2'],'Primera línea | alternativa. Segunda línea.')

    def test_cached_request_delivers_fresh_dictionary_without_network(self):
        self.request.start();self.complete()
        expected=dict(self.received[0])
        cached_text=self.request.result_text
        def consumer(values):
            self.received.append(dict(values))
            values['area_km2']='Consumer-local edit'
        self.new_request(consumer=consumer).start();self.request.start()
        self.assertEqual(len(self.manager.requests),1)
        self.assertEqual(self.received,[expected,expected])
        self.assertEqual(self.request.interpretations,expected)
        self.assertEqual(self.request.result_text,cached_text)
        self.assertNotIn('Consumer-local edit',self.request.result_text)
        self.assertIn('recuperado',self.request.status_text)
        self.assertEqual(len(self.finished),2)

    def test_invalid_or_incomplete_response_never_publishes_parameter_rows(self):
        missing=json.loads(structured())
        missing['parameter_interpretations'].pop('area_km2')
        cases=((response('{"parameter_interpretations":'),200),
               (response(json.dumps(missing)),200),
               (response(compact()[:-15],'length'),200),
               (response(structured()),503))
        for raw,status in cases:
            with self.subTest(status=status,raw=raw[:60]):
                self.request.cancel()
                ai._NEXT_REQUEST_AT=0.
                self.reply=FakeReply();self.manager.reply=self.reply
                self.new_request().start();self.complete(raw,status)
                self.assertFalse(self.received)
                self.assertFalse(self.request.interpretations)
                self.assertTrue(self.request.is_finished)
                self.assertFalse(ai._CACHE)

    def test_cancel_before_start_does_not_publish_a_ready_cached_result(self):
        self.request.start();self.complete()
        self.assertEqual(len(self.received),1)
        self.new_request().cancel();self.request.start()
        self.assertEqual(len(self.received),1)
        self.assertEqual(len(self.manager.requests),1)
        self.assertEqual(len(self.finished),2)

    def test_consumer_can_cancel_after_receiving_result(self):
        self.request.interpretations_ready.connect(lambda values:self.request.cancel())
        self.request.start();self.complete()
        self.assertEqual(len(self.received),1)
        self.assertTrue(self.request._closed)
        self.assertIsNone(self.request.reply)
        self.assertEqual(self.reply.deleted,1)
        self.assertEqual(len(self.finished),1)

    def test_status_consumer_can_cancel_before_network_post(self):
        self.request.status_changed.connect(lambda text:self.request.cancel())
        self.request.start()
        self.assertFalse(self.manager.requests)
        self.assertFalse(self.received)
        self.assertEqual(len(self.finished),1)

    def test_destroyed_parent_invalidates_request_and_its_qt_children(self):
        self.request.cancel()
        parent=QWidget()
        previous=self.new_request(parent)
        previous.start()
        reply,timer=self.reply,previous.timer
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(parent,QEvent.DeferredDelete)
        previous.start();previous._finished();previous.cancel()
        self.assertTrue(previous._closed)
        self.assertTrue(previous.is_finished)
        self.assertIsNone(previous.reply)
        self.assertTrue(sip.isdeleted(reply))
        self.assertTrue(sip.isdeleted(timer))
        self.assertFalse(self.received)
        self.assertEqual(len(self.manager.requests),1)
        self.reply=FakeReply();self.manager.reply=self.reply
        ai._NEXT_REQUEST_AT=0.
        self.new_request().start();self.complete()
        self.assertEqual(len(self.manager.requests),2)
        self.assertEqual(len(self.received),1)

    def test_failed_new_request_does_not_reuse_previous_text(self):
        self.request.start();self.complete(response('Texto previo','length'))
        ai._NEXT_REQUEST_AT=0.
        self.reply=FakeReply();self.manager.reply=self.reply
        self.new_request().start();self.complete(b'',503)
        self.assertFalse(self.request.interpretations)
        self.assertEqual(self.request.result_text,'')
        self.assertIn('conectar',self.request.status_text)

    def test_start_network_exception_reports_status_and_finishes(self):
        with patch.object(self.manager,'post',side_effect=RuntimeError('network unavailable')):
            self.request.start()
        self.assertIn('iniciar la conexión',self.request.status_text)
        self.assertFalse(self.request.interpretations)
        self.assertFalse(ai._CACHE)
        self.assertEqual(len(self.finished),1)


if __name__=='__main__':unittest.main()
