"""Numerical readings shared by the GUI and PDF, including mixed units."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta.parameter_schema import METRICS,format_value,interpret_value


class ParameterSchemaTests(unittest.TestCase):
    def test_rectangular_resolution_and_real_cell_area(self):
        self.assertEqual(format_value('cell_resolution',(500,250)),'500 m × 250 m')
        self.assertIn('12,5 ha',interpret_value('cell_resolution',(500,250)))
        self.assertEqual(format_value('cell_resolution',(.25,.5)),'0,25 m × 0,5 m')
        for invalid in (None,(0,500),(-500,500),(500,float('nan')),(500,)):
            self.assertEqual(format_value('cell_resolution',invalid),'No disponible')

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
            self.assertEqual(format_value(metric.key,None),'No disponible')
            self.assertIn('No evaluable',interpret_value(metric.key,None))
        self.assertIn('sin desnivel',interpret_value('hypsometric',None,{'relief_m':0}))

    def test_small_positive_geometry_measurements_keep_significant_digits(self):
        self.assertEqual(format_value('area_km2',.00125),'0,00125')
        self.assertEqual(format_value('area_km2',.00000045),'0,000000450')
        self.assertEqual(format_value('perimeter_km',.005332),'0,00533')
        self.assertGreater(float(format_value('area_km2',1e-20).replace(',','.')),0)
        self.assertEqual(format_value('area_km2',0),'0,000')
        self.assertEqual(format_value('perimeter_km',.15),'0,150')
        self.assertEqual(format_value('area_km2',389),'389,000')


if __name__=='__main__':
    unittest.main()
