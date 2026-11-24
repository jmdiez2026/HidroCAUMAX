"""Numerical readings shared by the GUI and PDF, including mixed units."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta.parameter_schema import METRICS,format_value,interpret_value


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
