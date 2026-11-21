"""PDF tests using QGIS's Qt runtime and the installed PDF text reader."""
import importlib
import copy
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry
from qgis.PyQt.QtGui import QColor, QImage
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
reports = importlib.import_module('caumax_consulta.reports')
schema = importlib.import_module('caumax_consulta.parameter_schema')
APP = None


def normalized(value):
    # Linux fonts may expose presentation ligatures in otherwise identical PDF
    # text. Keep scientific units intact while comparing the underlying words.
    ligatures = str.maketrans({'ﬀ': 'ff', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬃ': 'ffi',
                              'ﬄ': 'ffl', 'ﬅ': 'st', 'ﬆ': 'st'})
    return re.sub(r'\s+', ' ', str(value).translate(ligatures)).strip()


def pdf_text(reader):
    """Join visible page continuations despite Qt's repeated clipped fragments."""
    words = []
    for page in reader.pages:
        raw = re.sub(r'\s+\d+\s*$', '', page.extract_text())
        following = normalized(raw).split()
        for count in range(min(200, len(words), len(following)), 2, -1):
            if words[-count:] == following[:count]:
                following = following[count:]
                break
        words.extend(following)
    return ' '.join(words)


def example(candidate=False):
    crs = QgsCoordinateReferenceSystem('EPSG:25830')
    values = {
        'area_km2': 180.0, 'perimeter_km': 89.0, 'cell_count': 720,
        'cell_resolution': (500.0, 500.0),
        'elongation_ratio': 0.6321, 'kc': 1.871323287164952,
        'ic': 0.2855632761753125, 'melton': 0.07345,
        'slope_d8_pct': 12.35, 'hypsometric': 0.4567,
        'tri_m': 87.6543, 'relief_m': 986.0,
        'main_length_km': 32.125, 'main_length_10_85_km': 24.09375,
        'tc_h': None if candidate else 3.5432,
        'tc_10_85_h': None if candidate else 2.7821,
        'zmin_m': 450.0, 'zmax_m': 1436.0, 'zmean_m': 900.0, 'zmedian_m': 890.0,
        'basin_length_km': 23.952, 'main_slope_10_85': 0.0243,
        'tc_path_length_km': 32.125, 'tc_path_slope': 0.0182,
    }
    analysis = SimpleNamespace(
        values=values, notes=('Caso de ensayo con cuenca original.',),
        diagnostics={'tc_candidate_h': 0.163456, 'tc_10_85_candidate_h': 0.123456} if candidate else {},
        selected_x=338834.5, selected_y=4715307.1,
        outlet_x=338825.0, outlet_y=4715200.0,
        crs_wkt=crs.toWkt(), cell_width=500, cell_height=500,
        touches_boundary=False,
    )
    query = SimpleNamespace(
        flow=12.125, period=100, x=338834.5, y=4715307.1,
        crs=crs, layername='q100 <nombre de prueba & original>',
        directions_path=str(ROOT / 'dir.tif'), dem_path=str(ROOT / 'mdt.tif'),
    )
    return analysis, query


def hydrology_snapshot(analysis):
    from caumax_consulta.basin_hydrology import basin_fingerprint
    analysis.area_m2 = 180e6
    analysis.geometry_wkb = bytes(QgsGeometry.fromWkt(
        'POLYGON((330000 4710000,348000 4710000,348000 4720000,330000 4720000,330000 4710000))'
    ).asWkb())

    def stats(name, mean, coverage=1., valid=1., status='ok', notes=()):
        return dict(source_path='/absent/offline/' + name, mean_mm=mean, basin_area_m2=180e6,
                    covered_area_m2=180e6 * coverage, valid_area_m2=180e6 * valid,
                    coverage_fraction=coverage, valid_fraction=valid,
                    pixel_count=round(720 * coverage), status=status, notes=list(notes))

    return dict(version=1, basin_fingerprint=basin_fingerprint(analysis), basin_area_m2=180e6,
                p0_result=stats('p0.tif', 0.),
                rainfall_results={
                    '2': stats('t2.tif', 40., .75, .5, 'partial', ['Media sobre el área válida.']),
                    '5': stats('t5.tif', None, 1., 0., 'unavailable', ['NoData en la parte cubierta.']),
                    '10': stats('t10.tif', 80.), '25': stats('t25.tif', 100.),
                    '100': stats('t100.tif', 150.),
                }, input_period=3.5, input_precipitation=100., extrapolate=False,
                method='piecewise_linear_p_log_t')


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        APP = QgsApplication.instance()
        if APP is None:
            APP = QgsApplication([], False)
            APP.initQgis()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='caumax_pdf_')
        self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name)
        self.analysis, self.query = example()

    def test_pdf_contains_all_values_units_coordinates_and_definitions(self):
        destination = self.folder / 'informe.pdf'
        with patch.object(reports.QDesktopServices, 'openUrl') as opener:
            result = reports.generate_basin_pdf(destination, self.analysis, self.query, open_after=False)
        self.assertEqual(result, destination)
        opener.assert_not_called()
        self.assertTrue(destination.read_bytes().startswith(b'%PDF-'))
        reader = PdfReader(str(destination))
        self.assertGreaterEqual(len(reader.pages), 2)
        self.assertLessEqual(len(reader.pages), 6)
        self.assertEqual(len(schema.METRICS), 19)
        self.assertTrue(all(float(page.mediabox.width) > float(page.mediabox.height) for page in reader.pages))
        all_text = pdf_text(reader)
        for metric in schema.METRICS:
            self.assertIn(normalized(metric.label), all_text)
            self.assertIn(normalized(schema.format_value(metric.key, self.analysis.values.get(metric.key))), all_text)
            self.assertIn(re.sub(r'\s+', '', metric.definition), re.sub(r'\s+', '', all_text))
            interpretation = schema.interpret_value(metric.key, self.analysis.values.get(metric.key), self.analysis.values)
            self.assertIn(re.sub(r'\s+', '', interpretation), re.sub(r'\s+', '', all_text))
        for value in ('338834.500', '4715307.100', '338825.000', '4715200.000', 'EPSG:25830', '12.125000', '100 años', 'dir.tif', 'mdt.tif'):
            self.assertIn(value, all_text)
        self.assertIn('cuenca ORIGINAL', all_text)
        self.assertIn('2.2.2.5', all_text)
        self.assertIn('0,25 h', all_text)
        self.assertIn('q100 <nombre de prueba & original>', all_text)
        self.assertIn('Interpretación', all_text)
        self.assertIn('Hmed es la altitud mediana (percentil 50)', all_text)
        self.assertIn('el índice hipsométrico utiliza la media aritmética', all_text)
        self.assertIn('Ninguno se sustituye por el máximo entre otras ramas', all_text)
        self.assertIn('Identificar el Tc global normativo exige tantear los recorridos candidatos', all_text)
        self.assertNotIn('se adopta el de mayor Tc', all_text)
        self.assertTrue(all(normalized(page.extract_text()) for page in reader.pages))
        review = ROOT / 'work' / 'pdf_reports_review'
        review.mkdir(parents=True, exist_ok=True)
        (review / 'report_example.pdf').write_bytes(destination.read_bytes())

    def test_pdf_candidate_below_threshold_remains_unavailable_not_plausible_tc(self):
        analysis, query = example(candidate=True)
        result = reports.generate_basin_pdf(self.folder / 'candidate.pdf', analysis, query, open_after=False)
        text = normalized(' '.join(page.extract_text() for page in PdfReader(str(result)).pages))
        self.assertIn('No disponible', text)
        self.assertIn('0.163456 h', text)
        self.assertIn('0.123456 h', text)
        self.assertIn('fórmula de Tc(L)', text)
        self.assertIn('fórmula de Tc(L10-85)', text)
        self.assertIn('No se presenta como un Tc normativo válido', text)

    def test_report_table_is_white_and_interpretation_follows_unit(self):
        content = reports.build_report_html(self.analysis, self.query)
        self.assertNotIn('#edf5fa', content)
        self.assertNotIn('#195577', content)
        header = content.split('<thead>', 1)[1].split('</thead>', 1)[0]
        self.assertIn('bgcolor="#ffffff"', header)
        self.assertLess(header.index('Parámetro'), header.index('Valor'))
        self.assertLess(header.index('Valor'), header.index('Unidad'))
        self.assertLess(header.index('Unidad'), header.index('Interpretación'))
        self.assertEqual(content.count('<tr bgcolor="#ffffff"><td width="29%">'), 19)

    def test_smoothing_pdf_contains_current_selection_decimal_degree_and_comparison(self):
        state = {
            'original_checked': False, 'smooth_checked': True, 'degree': 4.5,
            # The report must use the cached statistics, without evaluating geometry.
            'geometry_wkb': {'suavizada': b'not interpreted by the PDF renderer'},
            'stats': {'suavizada': {
                'area_km2': 172.123, 'perimeter_km': 66.456,
                'kc': 1.5522, 'ic': 0.4149,
                'delta_area_pct': -4.376, 'delta_perimeter_pct': -25.330,
            }},
        }
        original_values = copy.deepcopy(self.analysis.values)
        original_state = copy.deepcopy(state)
        result = reports.generate_basin_pdf(
            self.folder / 'smoothing.pdf', self.analysis, self.query,
            open_after=False, smoothing_state=state,
        )
        reader = PdfReader(str(result))
        text = normalized(' '.join(page.extract_text() for page in reader.pages))
        for expected in (
            'INFORME DE CONSULTA', 'Suavizado de la divisoria',
            'Original: sin seleccionar; Suavizada: seleccionada', 'Grado de suavizado: 4,5',
            '172,123', '66,456', '1,5522', '0,4149', '-4,376', '-25,330',
            'Fuente de la comparación: geometrías de la vista previa actual',
            'los 19 parámetros de la cuenca original',
        ):
            self.assertIn(expected, text)
        for metric in schema.METRICS:
            self.assertIn(schema.format_value(metric.key, original_values.get(metric.key)), text)
        self.assertEqual(self.analysis.values, original_values)
        self.assertEqual(state, original_state)

    def test_smoothing_state_changes_update_comparison_and_leave_original_results_unchanged(self):
        state = {
            'original_checked': True, 'smooth_checked': True, 'degree': 4.5,
            'stats': {
                'original': {key: self.analysis.values[key]
                             for key in ('area_km2', 'perimeter_km', 'kc', 'ic')},
                'suavizada': {'area_km2': 170.123, 'perimeter_km': 65.456,
                              'kc': 1.5555, 'ic': 0.4132,
                              'delta_area_pct': -5.487, 'delta_perimeter_pct': -26.454},
            },
        }
        before = reports.build_report_html(self.analysis, self.query, state)
        state['degree'] = 45.0
        state['stats']['suavizada'].update(area_km2=160.987, perimeter_km=60.321)
        after = reports.build_report_html(self.analysis, self.query, state)
        self.assertIn('Grado de suavizado: <b>45</b>', after)
        self.assertIn('160,987', after)
        self.assertIn('60,321', after)
        self.assertNotIn('170,123', after)
        self.assertNotIn('65,456', after)
        marker = '<h2>Morfología de la Cuenca</h2>'
        original_table_before = before.split(marker, 1)[1].split('</table>', 1)[0]
        original_table_after = after.split(marker, 1)[1].split('</table>', 1)[0]
        self.assertEqual(original_table_before, original_table_after)
        result = reports.generate_basin_pdf(
            self.folder / 'degree_45.pdf', self.analysis, self.query,
            open_after=False, smoothing_state=state,
        )
        text = normalized(' '.join(page.extract_text() for page in PdfReader(str(result)).pages))
        self.assertIn('Grado de suavizado: 45', text)
        self.assertIn('160,987', text)
        self.assertNotIn('170,123', text)

    def test_tiny_original_and_smoothed_areas_use_the_shared_adaptive_precision(self):
        self.analysis.values['area_km2'] = .00125
        state = {
            'original_checked': True, 'smooth_checked': True, 'degree': 45.0,
            'stats': {
                'original': {'area_km2': .00125, 'perimeter_km': .15},
                'suavizada': {'area_km2': .00000045, 'perimeter_km': .005332,
                              'delta_area_pct': -99.964},
            },
        }
        result = reports.generate_basin_pdf(
            self.folder / 'tiny_area.pdf', self.analysis, self.query,
            open_after=False, smoothing_state=state,
        )
        text = normalized(' '.join(page.extract_text() for page in PdfReader(str(result)).pages))
        for expected in ('0,00125', '0,000000450', '0,00533', '-99,964'):
            self.assertIn(expected, text)

    def test_save_dialog_passes_the_exact_smoothing_snapshot_to_generator(self):
        state = {'original_checked': True, 'smooth_checked': True, 'degree': 0.5, 'stats': {}}
        path = self.folder / 'current_state.pdf'
        with patch.object(reports.QFileDialog, 'getSaveFileName', return_value=(str(path), 'PDF')), patch.object(reports, 'generate_basin_pdf', return_value=path) as generate:
            self.assertEqual(reports.ask_basin_pdf(None, self.analysis, self.query, state), path)
        self.assertIs(generate.call_args.kwargs['smoothing_state'], state)
        self.assertTrue(generate.call_args.kwargs['open_after'])

    def test_legacy_report_has_no_smoothing_section_without_a_snapshot(self):
        content = reports.build_report_html(self.analysis, self.query)
        self.assertIn('<h1>CUENCA HIDROGRÁFICA</h1>', content)
        self.assertNotIn('<h2>Suavizado de la divisoria</h2>', content)
        self.assertNotIn('<h2>Resolución MDT seleccionada</h2>', content)
        self.assertNotIn('P₀ y precipitación máxima diaria de la cuenca', content)

    def test_active_mdt_resolution_source_and_original_flow_are_reported_without_alternatives(self):
        self.analysis.cell_width = self.analysis.cell_height = 25.0
        self.analysis.values.update(cell_resolution=(25.0, 25.0), area_km2=0.45)
        self.analysis.diagnostics['dem_hydrology'] = {'engine': 'GRASS r.watershed'}
        self.query.flow = 177.0
        state = {
            'original_checked': True, 'smooth_checked': False, 'degree': 4.5,
            'resolution_metadata': {
                'active_key': '25', 'label': 'MDT25 IGN', 'resolution': 25.0,
                'provider': 'IGN/CNIG', 'source': 'https://centrodedescargas.cnig.es/',
                'available_options': [
                    {'label': 'MDT2 NO ACTIVO', 'resolution': 2,
                     'values': {'area_km2': 98765.432}},
                    {'label': 'CAUMAX NO ACTIVO', 'resolution': 500},
                ],
            },
        }
        snapshot = copy.deepcopy(state)
        values_before = copy.deepcopy(self.analysis.values)
        result = reports.generate_basin_pdf(
            self.folder / 'active_mdt.pdf', self.analysis, self.query,
            open_after=False, smoothing_state=state,
        )
        text = normalized(' '.join(page.extract_text() for page in PdfReader(str(result)).pages))
        for expected in (
            'HidroCAUMAX v1.2.2', 'Resolución MDT seleccionada', 'MDT25 IGN',
            '25,00 × 25,00 m', 'IGN/CNIG', 'https://centrodedescargas.cnig.es/',
            'Mayor detalle espacial', 'Caudal CAUMAX original', '177.000000 m³/s',
            'no se recalcula al descargar o seleccionar otro MDT',
            'corresponden exclusivamente al MDT activo', '0,450', '25 m × 25 m',
            'MDT activo: IGN/CNIG', 'Direcciones D8: GRASS r.watershed sobre el MDT activo',
        ):
            self.assertIn(expected, text)
        self.assertNotIn('MDT2 NO ACTIVO', text)
        self.assertNotIn('CAUMAX NO ACTIVO', text)
        self.assertNotIn('98765', text)
        self.assertEqual(state, snapshot)
        self.assertEqual(self.analysis.values, values_before)

    def test_native_mdt_label_uses_effective_grid_resolution(self):
        state = {'resolution_metadata': {
            'active_key': '500', 'label': 'MDT original CAUMAX', 'resolution': 500.0,
            'provider': 'CEDEX / MITECO', 'source': 'CAUMAX',
        }}
        content = reports.build_report_html(self.analysis, self.query, state)
        self.assertIn('Malla original CAUMAX de 500 m', content)
        self.assertIn('Direcciones D8 originales: CAUMAX', content)
        self.assertIn('malla efectiva del análisis: 500,00 × 500,00 m', content)
        self.analysis.cell_width, self.analysis.cell_height = 25, 30
        content = reports.build_report_html(self.analysis, self.query, state)
        self.assertIn('malla efectiva del análisis: 25,00 × 30,00 m', content)
        self.assertIn('Resolución nominal: 500,00 m', content)

    def test_hypsometric_pdf_embeds_selected_curve_mode_and_uses_exact_hi(self):
        from caumax_consulta.hypsometry_data import build_hypsometric_curve
        self.analysis.values.update(hypsometric=.125, zmean_m=573.25, zmedian_m=450)
        self.analysis.diagnostics['hypsometric_curve'] = build_hypsometric_curve(
            [450] * 7 + [1436], self.analysis.values['area_km2'], .125,
        )
        for relative in (False, True):
            with self.subTest(relative=relative):
                result = reports.generate_basin_pdf(
                    self.folder / ('relative.pdf' if relative else 'dimensional.pdf'),
                    self.analysis, self.query, open_after=False,
                    smoothing_state={'hypsometric_dimensionless': relative},
                )
                reader = PdfReader(str(result))
                text = normalized(' '.join(page.extract_text() for page in reader.pages))
                self.assertIn('Curva hipsométrica', text)
                self.assertIn('Modo adimensional' if relative else 'Modo dimensional', text)
                self.assertIn('HI exacto: 0,1250', text)
                self.assertIn('201 cuantiles de 8 cotas válidas', text)
                self.assertIn('media aritmética de todas las cotas válidas', text)
                self.assertIn('integral aproximada no sustituye el HI exacto', text)
                self.assertIn('Área relativa a/A' if relative else 'Área acumulada por encima de la cota (km²)', text)
                images = []
                for page in reader.pages:
                    resources = page['/Resources'].get_object()
                    for reference in resources.get('/XObject', {}).values():
                        obj = reference.get_object()
                        if obj.get('/Subtype') == '/Image':
                            images.append(obj)
                self.assertTrue(any(obj.get('/Width') == 1280 and obj.get('/Height') == 600
                                    for obj in images))

    def test_pdf_direction_provenance_uses_recorded_engine_instead_of_resolution(self):
        self.analysis.diagnostics['dem_hydrology'] = {'engine': 'TauDEM D8FlowDir'}
        state = {'resolution_metadata': {'active_key': '25', 'provider': 'IGN/CNIG'}}
        content = reports.build_report_html(self.analysis, self.query, state)
        self.assertIn('Direcciones D8: TauDEM D8FlowDir sobre el MDT activo', content)
        self.assertNotIn('GRASS r.watershed', content)
        self.analysis.diagnostics.pop('dem_hydrology')
        content = reports.build_report_html(self.analysis, self.query, state)
        self.assertIn('procedencia no registrada', content)
        self.assertNotIn('GRASS r.watershed', content)

    def test_quantile_pdf_uses_portable_active_panel_snapshot_and_shared_plot_helpers(self):
        picture = QImage(1100, 500, QImage.Format_RGB32)
        picture.fill(QColor('#eef7fc'))
        path = self.folder / 'plots.png'
        self.assertTrue(picture.save(str(path), 'PNG'))
        blob = path.read_bytes()
        path.unlink()
        snapshot = {
            'version': 1, 'periods': [2, 5, 10, 25, 100, 500],
            'flows': [10., 20., None, 40., 40., 160.],
            'source_paths': [str(self.folder / f'q{period}.tif') for period in (2, 5, 10, 25, 100, 500)],
            'point': {'x': self.query.x, 'y': self.query.y, 'crs_wkt': self.query.crs.toWkt()},
            'input_period': 50., 'input_flow': 40., 'extrapolate': True,
            'method': 'piecewise_linear_q_log_t',
        }
        before = copy.deepcopy(snapshot)
        shared = ModuleType('caumax_consulta.flow_quantiles')
        shared.describe_estimates = lambda value: {
            'forward_text': 'Q estimado: 40 m³/s.',
            'inverse_text': 'Intervalo compatible de T: [25, 100] años.',
            'method_note': 'Interpolación lineal de Q respecto de log(T), por tramos.',
        }
        calls = []
        def render(value, width, height, font_family=None):
            self.assertIs(value, snapshot)
            self.assertEqual((width, height), (1100, 500))
            self.assertTrue(font_family)
            calls.append(value)
            return blob
        shared.render_quantile_plots = render
        with patch.dict(sys.modules, {'caumax_consulta.flow_quantiles': shared}):
            result = reports.generate_basin_pdf(
                self.folder / 'quantiles.pdf', self.analysis, self.query,
                open_after=False, smoothing_state={'flow_quantiles': snapshot},
            )
        reader = PdfReader(str(result))
        text = normalized(' '.join(page.extract_text() for page in reader.pages))
        for expected in (
            'Caudales originales y estimaciones de periodo de retorno',
            'punto original', 'no recalculan el caudal con el nuevo MDT',
            '10,00', '20,00', '40,00', '160,00', 'No disponible',
            'T introducido: 50 años', 'Q introducido: 40,00 m³/s',
            'Q estimado: 40 m³/s', 'Intervalo compatible de T: [25, 100] años',
            'Extrapolación: permitida', 'lineal de Q respecto de log(T)',
        ):
            self.assertIn(expected, text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(snapshot, before)
        images = []
        for page in reader.pages:
            for reference in page['/Resources'].get_object().get('/XObject', {}).values():
                obj = reference.get_object()
                if obj.get('/Subtype') == '/Image':
                    images.append(obj)
        self.assertTrue(any(obj.get('/Width') == 1100 and obj.get('/Height') == 500 for obj in images))

    def test_quantile_pdf_with_real_shared_renderer_preserves_current_estimates(self):
        from caumax_consulta.flow_quantiles import describe_estimates
        snapshot = {
            'version': 1, 'periods': [2, 5, 10, 25, 100, 500],
            'flows': [10., 20., 30., 40., 80., 160.], 'source_paths': [''] * 6,
            'point': {'x': self.query.x, 'y': self.query.y, 'crs_wkt': self.query.crs.toWkt()},
            'input_period': 50., 'input_flow': 60., 'extrapolate': False,
            'method': 'piecewise_linear_q_log_t',
        }
        description = describe_estimates(snapshot)
        result = reports.generate_basin_pdf(
            self.folder / 'real_quantile_renderer.pdf', self.analysis, self.query,
            open_after=False, smoothing_state={'flow_quantiles': snapshot},
        )
        text = pdf_text(PdfReader(str(result)))
        for expected in description.values():
            self.assertIn(normalized(expected), text)
        self.assertIn('Extrapolación: desactivada', text)

    def test_basin_p0_precipitation_pdf_keeps_zero_partial_coverage_and_offline_sources(self):
        from osgeo import gdal
        from caumax_consulta.basin_hydrology_widget import describe_precipitation_estimates
        snapshot = hydrology_snapshot(self.analysis)
        before = copy.deepcopy(snapshot)
        description = describe_precipitation_estimates(snapshot)
        with patch.object(gdal, 'Open', side_effect=AssertionError('PDF must not reopen raster sources')):
            result = reports.generate_basin_pdf(
                self.folder / 'basin_rainfall.pdf', self.analysis, self.query,
                open_after=False, smoothing_state={'basin_hydrology': snapshot},
            )
        reader = PdfReader(str(result)); text = pdf_text(reader)
        for expected in (
            'P₀ y precipitación máxima diaria de la cuenca', 'P₀ bruto', '0,000',
            'Cobertura (%)', 'Área válida (%)', '75,00', '50,00', 'Parcial',
            'No disponible', 'Sin calcular', 'p0.tif', 't5.tif', 'Píxeles',
            'píxeles intersectados incluyen los NoData',
            'No se aplica reducción areal', 'sin aplicar β',
            'intersección de cada píxel', 'utiliza solo los valores válidos',
            'no es el cuantil de una serie observada de tormentas medias',
            'Precipitación introducida: 100,00 mm',
        ):
            self.assertIn(expected, text)
        for value in description.values():
            self.assertIn(normalized(value), text)
        self.assertEqual(snapshot, before)
        images = [reference.get_object() for page in reader.pages
                  for reference in page['/Resources'].get_object().get('/XObject', {}).values()]
        self.assertTrue(any(image.get('/Subtype') == '/Image' and image.get('/Width') == 1100
                            and image.get('/Height') >= 500 for image in images))

    def test_basin_hydrology_without_requests_is_a_brief_not_computed_note(self):
        snapshot = hydrology_snapshot(self.analysis)
        snapshot.update(p0_result=None, rainfall_results={})
        with patch('caumax_consulta.basin_hydrology_widget.render_precipitation_plots',
                   side_effect=AssertionError('No empty plots')) as renderer:
            section = reports._basin_hydrology_report_html(self.analysis, {'basin_hydrology': snapshot})
        self.assertIn('No calculados para esta cuenca', section)
        self.assertNotIn('<table', section)
        self.assertNotIn('<img', section)
        self.assertNotIn('page-break-before', section)
        renderer.assert_not_called()

    def test_p0_only_report_has_no_uncomputed_rainfall_plots(self):
        snapshot = hydrology_snapshot(self.analysis); snapshot['rainfall_results'] = {}
        with patch('caumax_consulta.basin_hydrology_widget.render_precipitation_plots',
                   side_effect=AssertionError('No uncomputed rainfall plots')) as renderer:
            section = reports._basin_hydrology_report_html(self.analysis, {'basin_hydrology': snapshot})
        self.assertIn('P₀ bruto', section)
        self.assertIn('0,000', section)
        self.assertIn('Precipitación máxima diaria: no calculada', section)
        self.assertNotIn('T introducido', section)
        self.assertNotIn('<img', section)
        renderer.assert_not_called()

    def test_unavailable_rainfall_results_do_not_invent_plots_or_a_zero_mean(self):
        snapshot = hydrology_snapshot(self.analysis)
        snapshot['p0_result'] = None
        snapshot['rainfall_results'] = {'5': snapshot['rainfall_results']['5']}
        with patch('caumax_consulta.basin_hydrology_widget.render_precipitation_plots',
                   side_effect=AssertionError('No unavailable rainfall plots')) as renderer:
            section = reports._basin_hydrology_report_html(self.analysis, {'basin_hydrology': snapshot})
        self.assertIn('Gráficos no disponibles', section)
        self.assertIn('No disponible', section)
        self.assertIn('100,00', section)
        self.assertIn('0,00', section)
        self.assertIn('NoData', section)
        self.assertNotIn('<img', section)
        renderer.assert_not_called()

    def test_other_basin_fingerprint_or_area_cannot_leak_its_hydrologic_means(self):
        base = hydrology_snapshot(self.analysis)
        base['p0_result']['mean_mm'] = 9876.543
        for key, value in (('basin_fingerprint', '0' * 64), ('basin_area_m2', 999e6)):
            with self.subTest(key=key):
                snapshot = copy.deepcopy(base); snapshot[key] = value
                section = reports._basin_hydrology_report_html(self.analysis, {'basin_hydrology': snapshot})
                self.assertIn('No se muestran medias de otra divisoria', section)
                self.assertNotIn('9876,543', section)
                self.assertNotIn('<img', section)

    def test_adjusted_outlet_pdf_records_original_point_distance_criterion_and_original_flow(self):
        self.analysis.outlet_x, self.analysis.outlet_y = 338925.0, 4715400.0
        self.analysis.diagnostics['outlet_snap'] = {
            'original_x': self.analysis.selected_x, 'original_y': self.analysis.selected_y,
            'snapped_x': self.analysis.outlet_x, 'snapped_y': self.analysis.outlet_y,
            'distance_m': 129.700028, 'search_radius_m': 707.106781,
            'minimum_upstream_area_m2': 162e6, 'reference_area_m2': 180e6,
            'candidate_upstream_area_m2': 181.25e6,
            'method': 'nearest_main_channel_d8', 'status': 'snapped',
        }
        before = copy.deepcopy(self.analysis.diagnostics)
        self.query.flow = 177.0
        result = reports.generate_basin_pdf(self.folder / 'adjusted.pdf', self.analysis,
                                           self.query, open_after=False)
        text = normalized(' '.join(page.extract_text() for page in PdfReader(str(result)).pages))
        for expected in (
            'Ajuste de la sección de cierre', 'Punto original: X = 338834.500; Y = 4715307.100',
            'Cierre ajustado: X = 338925.000; Y = 4715400.000', 'Desplazamiento: 129,70 m',
            'radio de búsqueda: 707,11 m', 'Área mínima: 162,000 km²',
            'área de referencia: 180,000 km²', 'área aportante del cierre: 181,250 km²',
            '177.000000 m³/s', 'El caudal CAUMAX original se conserva en el punto consultado',
            'una celda de cauce distinta',
        ):
            self.assertIn(expected, text)
        self.assertNotIn('HEC', text)
        self.assertEqual(self.analysis.diagnostics, before)
        self.assertEqual(self.analysis.selected_x, 338834.5)
        self.assertEqual(self.analysis.selected_y, 4715307.1)
        self.assertEqual(self.query.flow, 177.0)

    def test_unchanged_outlet_explains_cell_centre_distance_without_claiming_a_cell_change(self):
        self.analysis.diagnostics['outlet_snap'] = {
            'original_x': 338834.5, 'original_y': 4715307.1,
            'snapped_x': 338825.0, 'snapped_y': 4715200.0,
            'distance_m': 107.520045, 'search_radius_m': 707.106781,
            'minimum_upstream_area_m2': 162e6, 'reference_area_m2': 180e6,
            'candidate_upstream_area_m2': 180e6,
            'method': 'nearest_main_channel_d8', 'status': 'unchanged',
        }
        content = reports.build_report_html(self.analysis, self.query)
        self.assertIn('Se mantiene la misma celda de cierre', content)
        self.assertIn('107,52', content)
        self.assertNotIn('una celda de cauce distinta', content)

    def test_map_snapshot_is_embedded_in_pdf_with_caption_and_results(self):
        image = QImage(1200, 600, QImage.Format_RGB32)
        image.fill(QColor('#b8dce9'))
        png_path = self.folder / 'snapshot.png'
        self.assertTrue(image.save(str(png_path), 'PNG'))
        state = {
            'original_checked': True, 'smooth_checked': False, 'degree': 0.5,
            'stats': {}, 'map_png': png_path.read_bytes(),
            'map_caption': 'Cuenca completa · EPSG:25830 · Cartografía local de prueba.',
        }
        result = reports.generate_basin_pdf(
            self.folder / 'map.pdf', self.analysis, self.query,
            open_after=False, smoothing_state=state,
        )
        png_path.unlink()
        reader = PdfReader(str(result))
        text = normalized(' '.join(page.extract_text() for page in reader.pages))
        for expected in ('Mapa de la cuenca completa', 'EPSG:25830',
                         'Cartografía local de prueba.', 'Morfología de la Cuenca',
                         '3,543', 'Tc(L10-85)'):
            self.assertIn(expected, text)
        for metric in schema.METRICS:
            self.assertIn(normalized(metric.label), text)
            self.assertIn(schema.format_value(metric.key, self.analysis.values.get(metric.key)), text)
            interpretation = schema.interpret_value(metric.key, self.analysis.values.get(metric.key), self.analysis.values)
            self.assertIn(re.sub(r'\s+', '', interpretation), re.sub(r'\s+', '', text))
        image_objects = []
        for page in reader.pages:
            resources = page.get('/Resources')
            if resources is None:
                continue
            objects = resources.get_object().get('/XObject', {})
            for reference in objects.values():
                obj = reference.get_object()
                if obj.get('/Subtype') == '/Image':
                    image_objects.append(obj)
        self.assertTrue(any(obj.get('/Width') == 1200 and obj.get('/Height') == 600
                            for obj in image_objects))

    def test_invalid_map_preserves_existing_report_and_never_launches_viewer(self):
        destination = self.folder / 'existing.pdf'
        destination.write_bytes(b'previous report')
        with patch.object(reports.QDesktopServices, 'openUrl') as opener:
            with self.assertRaisesRegex(ValueError, 'PNG válida'):
                reports.generate_basin_pdf(destination, self.analysis, self.query,
                                           smoothing_state={'map_png': b'invalid png'})
        opener.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'previous report')

    def test_none_metrics_keep_common_interpretation_and_are_not_replaced_by_zero(self):
        self.analysis.values.update(tc_h=None, tc_10_85_h=None, hypsometric=None, tri_m=None)
        content = reports.build_report_html(self.analysis, self.query)
        for key in ('tc_h', 'tc_10_85_h', 'hypsometric', 'tri_m'):
            expected = schema.interpret_value(key, None, self.analysis.values)
            self.assertIn(reports._text(expected), content)
        self.assertGreaterEqual(content.count('No disponible'), 4)

    def test_opener_runs_only_after_final_pdf_is_complete_and_replaces_old_file(self):
        destination = self.folder / 'complete.pdf'
        destination.write_bytes(b'anterior')
        observed = []
        def open_url(url):
            path = Path(url.toLocalFile())
            self.assertEqual(path, destination)
            self.assertTrue(path.read_bytes().startswith(b'%PDF-'))
            self.assertTrue(reports._valid_pdf(path))
            observed.append(path)
            return True
        with patch.object(reports.QDesktopServices, 'openUrl', side_effect=open_url):
            result = reports.generate_basin_pdf(destination, self.analysis, self.query)
        self.assertEqual(result, destination)
        self.assertEqual(observed, [destination])
        self.assertFalse(list(self.folder.glob('.caumax_informe_*')))

    def test_failed_generation_preserves_existing_pdf_and_never_opens_viewer(self):
        destination = self.folder / 'old.pdf'
        destination.write_bytes(b'contenido anterior que debe preservarse')
        def invalid_document(path, content):
            Path(path).write_bytes(b'no es PDF')
        with patch.object(reports, '_write_document', side_effect=invalid_document), patch.object(reports.QDesktopServices, 'openUrl') as opener:
            with self.assertRaises(OSError):
                reports.generate_basin_pdf(destination, self.analysis, self.query)
        opener.assert_not_called()
        self.assertEqual(destination.read_bytes(), b'contenido anterior que debe preservarse')
        self.assertFalse(list(self.folder.glob('.caumax_informe_*')))

    def test_extension_added_and_failed_viewer_does_not_hide_successful_pdf(self):
        with patch.object(reports.QDesktopServices, 'openUrl', return_value=False):
            with self.assertRaises(reports.PdfOpenError) as raised:
                reports.generate_basin_pdf(self.folder / 'sin_extension', self.analysis, self.query)
        self.assertEqual(raised.exception.path, self.folder / 'sin_extension.pdf')
        self.assertTrue(reports._valid_pdf(raised.exception.path))

    def test_cancel_save_dialog_does_not_generate_any_pdf(self):
        with patch.object(reports.QFileDialog, 'getSaveFileName', return_value=('', '')), patch.object(reports, 'generate_basin_pdf') as generate:
            self.assertIsNone(reports.ask_basin_pdf(None, self.analysis, self.query))
        generate.assert_not_called()

    def test_save_dialog_reports_open_failure_but_returns_saved_path(self):
        path = self.folder / 'saved.pdf'
        path.write_bytes(b'%PDF- saved')
        with patch.object(reports.QFileDialog, 'getSaveFileName', return_value=(str(path), 'PDF')), patch.object(reports, 'generate_basin_pdf', side_effect=reports.PdfOpenError(path)), patch.object(reports.QMessageBox, 'warning') as warning, patch.object(reports.QMessageBox, 'critical') as critical:
            self.assertEqual(reports.ask_basin_pdf(None, self.analysis, self.query), path)
        warning.assert_called_once()
        critical.assert_not_called()

    def test_adding_pdf_extension_cannot_silently_overwrite_an_existing_pdf(self):
        path = self.folder / 'existente.pdf'
        path.write_bytes(b'contenido anterior')
        with patch.object(reports.QFileDialog, 'getSaveFileName', return_value=(str(path.with_suffix('')), 'PDF')), patch.object(reports.QMessageBox, 'question', return_value=reports.QMessageBox.No) as confirm, patch.object(reports, 'generate_basin_pdf') as generate:
            self.assertIsNone(reports.ask_basin_pdf(None, self.analysis, self.query))
        confirm.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(path.read_bytes(), b'contenido anterior')

    def test_generation_failure_reports_error_and_does_not_claim_saved_pdf(self):
        path = self.folder / 'error.pdf'
        with patch.object(reports.QFileDialog, 'getSaveFileName', return_value=(str(path), 'PDF')), patch.object(reports, 'generate_basin_pdf', side_effect=OSError('escritura fallida')), patch.object(reports.QMessageBox, 'critical') as critical:
            self.assertIsNone(reports.ask_basin_pdf(None, self.analysis, self.query))
        critical.assert_called_once()
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
