"""Numerical readings shared by the GUI and PDF, including mixed units."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta.parameter_schema import (
    METRICS, format_value, interpret_value, morphology_table_label, morphology_table_value,
)


class ParameterSchemaTests(unittest.TestCase):
    def test_rectangular_resolution_and_real_cell_area(self):
        self.assertEqual(format_value('cell_resolution',(500,250)),'500mx250m')
        self.assertIn('12,5 ha',interpret_value('cell_resolution',(500,250)))
        self.assertEqual(format_value('cell_resolution',(.25,.5)),'0,25mx0,5m')
        for invalid in (None,(0,500),(-500,500),(500,float('nan')),(500,)):
            self.assertEqual(format_value('cell_resolution',invalid),'n.d.')

    def test_concentration_time_minutes_keep_integer_trailing_zeroes(self):
        self.assertIn('180 min',interpret_value('tc_h',3))
        self.assertIn('120 min',interpret_value('tc_10_85_h',2))

    def test_shape_readings_respect_circle_normalisation(self):
        self.assertIn('0 % más largo',interpret_value('kc',1))
        self.assertIn('50 % más largo',interpret_value('kc',1.5))
        self.assertIn('100 %',interpret_value('ic',1))
        self.assertIn('40 %',interpret_value('ic',.4))

    def test_unavailable_values_are_not_interpreted_as_zero(self):
        for metric in METRICS:
            self.assertEqual(format_value(metric.key,None),'n.d.')
            self.assertIn('No evaluable',interpret_value(metric.key,None))
        self.assertIn('sin desnivel',interpret_value('hypsometric',None,{'relief_m':0}))

    def test_small_positive_values_are_bounded_without_extra_decimal_places_or_false_zero(self):
        self.assertEqual(format_value('area_km2',.00125),'<0,01')
        self.assertEqual(format_value('area_km2',.00000045),'<0,01')
        self.assertEqual(format_value('perimeter_km',.005332),'<0,01')
        self.assertEqual(format_value('area_km2',1e-20),'<0,01')
        self.assertEqual(format_value('area_km2',0),'0,00')
        self.assertEqual(format_value('perimeter_km',.15),'0,15')
        self.assertEqual(format_value('area_km2',389),'389,00')

    def test_qgis_morphology_label_units_are_bracketed_and_pdf_data_unmodified(self):
        titles = {metric.key: morphology_table_label(metric) for metric in METRICS}
        self.assertEqual(len(titles), 19)
        for key, expected in (
                ('area_km2', 'Área de la cuenca [km²]'),
                ('perimeter_km', 'Perímetro [km]'),
                ('slope_d8_pct', 'Pendiente media de la cuenca (D8) [%]'),
                ('tri_m', 'Índice de Irregularidad del Terreno TRI (Riley) [m]'),
                ('main_length_km', 'Longitud del curso principal total (L) [km]'),
                ('tc_h', 'Tiempo de concentración (L) [h]'),
                ('cell_resolution', 'Resolución celda [m]'),
                ('cell_count', 'Número de celdas'),
                ('elongation_ratio', 'Relación de elongación'),
                ('melton', 'Índice de rugosidad de Melton')):
            with self.subTest(key=key):
                self.assertEqual(titles[key], expected)
        for metric in METRICS:
            if metric.unit:
                self.assertTrue(titles[metric.key].endswith(f'[{metric.unit}]'))
        self.assertEqual(morphology_table_value('cell_resolution', (25, 5)), '25 × 5')
        self.assertEqual(morphology_table_value('cell_resolution', None), 'n.d.')
        self.assertEqual(morphology_table_value('perimeter_km', 10.123), '10,12')
        # The PDF/report schema intentionally retains separate unit fields.
        self.assertEqual(format_value('cell_resolution', (25, 5)), '25mx5m')
        self.assertEqual(next(m.unit for m in METRICS if m.key == 'perimeter_km'), 'km')

    def test_morphology_table_uses_at_most_two_decimals_and_keeps_integer_counts(self):
        for key, value, expected in (
            ('area_km2',54.271,'54,27'), ('perimeter_km',61.050,'61,05'),
            ('elongation_ratio',.4400,'0,44'), ('kc',2.3378,'2,34'),
            ('ic',.1830,'0,18'), ('slope_d8_pct',3.257,'3,26'),
            ('cell_count',86833,'86833'),
        ):
            with self.subTest(key=key):
                self.assertEqual(format_value(key,value),expected)


if __name__=='__main__':
    unittest.main()
