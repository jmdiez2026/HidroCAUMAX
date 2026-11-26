import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.parameter_schema import interpret_value, interpretation_reference, METRICS


class ClassroomInterpretations(unittest.TestCase):
    def test_fine_grid_never_rounds_cell_area_to_zero(self):
        self.assertIn('25 m²',interpret_value('cell_resolution',(5,5)))
        self.assertIn('625 m²',interpret_value('cell_resolution',(25,25)))
        self.assertIn('<0,01 m²',interpret_value('cell_resolution',(.001,.001)))

    def test_kc_boundaries_are_explicit_and_unrounded(self):
        cases = [(1,'Compacta'),(1.25,'Compacta'),(1.250001,'Transición'),
                 (1.5,'Transición'),(1.500001,'Alargada'),(1.75,'Alargada'),(1.750001,'Delgada')]
        for value, category in cases:
            with self.subTest(value=value):
                self.assertIn(category, interpret_value('kc',value))
        self.assertIn('No evaluable',interpret_value('kc',.99))

    def test_course_elongation_is_distinct_from_existing_straight_ratio(self):
        values={'area_km2':3.154,'main_length_km':3.953}
        text=interpret_value('elongation_ratio',.88,values)
        self.assertIn('Alargada',text)
        self.assertIn('Re(L)=0,51',text)
        self.assertIn('88 % de Lb recta',text)
        self.assertEqual(values,{'area_km2':3.154,'main_length_km':3.953})
        for ratio, expected in [(.499,'Muy alargada'),(.5,'Alargada'),(.7,'Moderadamente'),
                                (.8,'Subcircular'),(.9,'Subcircular'),(.91,'Circular')]:
            context={'area_km2':math.pi/4,'main_length_km':1/ratio}
            self.assertIn(expected,interpret_value('elongation_ratio',.8,context))
        self.assertIn('necesita',interpret_value('elongation_ratio',.8,{'main_length_km':0}))
        self.assertIn('fuera',interpret_value('elongation_ratio',.8,{'area_km2':10,'main_length_km':1}))

    def test_melton_never_fills_missing_classes(self):
        for value in (.20001,.25,.29999,.60001,1,3,4):
            self.assertIn('sin clase',interpret_value('melton',value))
        for value, category in [(0,'muy baja'),(.1,'moderada'),(.2,'moderada'),
                                (.3,'alta'),(.409,'alta'),(.6,'alta'),(4.01,'extrema')]:
            self.assertIn(category,interpret_value('melton',value))

    def test_hypsometry_uses_didactic_classes_and_marks_exact_boundaries(self):
        for value, category in [(0,'vieja'),(.349,'vieja'),(.35,'Límite'),(.4,'madura'),
                                (.6,'Límite'),(.61,'joven'),(1,'joven')]:
            self.assertIn(category,interpret_value('hypsometric',value))
        self.assertIn('40 %',interpret_value('hypsometric',.4,{'zmedian_m':999}))
        self.assertIn('No evaluable',interpret_value('hypsometric',1.01))

    def test_tri_is_resolution_aware_without_applying_local_example_classes(self):
        small=interpret_value('tri_m',5.72,{'cell_resolution':(5,5)})
        coarse=interpret_value('tri_m',5.72,{'cell_resolution':(500,500)})
        self.assertIn('15×15 m',small)
        self.assertIn('1500×1500 m',coarse)
        self.assertNotIn('clase',small)
        self.assertNotIn('clase',coarse)
        self.assertIn('solo a la cuenca ejemplo',interpretation_reference('tri_m'))

    def test_missing_nonfinite_and_domain_values_are_not_classified(self):
        for metric in METRICS:
            for value in (None,float('nan'),float('inf')):
                self.assertTrue(interpret_value(metric.key,value).startswith('No evaluable'))
        for key in ('kc','ic','melton','tri_m','slope_d8_pct','hypsometric'):
            self.assertIn('No evaluable',interpret_value(key,-1))
        self.assertIn('0,25',interpret_value('tc_h',.25))


if __name__=='__main__': unittest.main()
