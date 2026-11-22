"""Qt curve axes, exact HI, mode selection and self-contained rendering."""
import os
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from qgis.core import QgsApplication
from qgis.PyQt.QtGui import QImage
from caumax_consulta.hypsometry import HypsometricWidget, curve_plot_data, render_hypsometric_png, _tick
from caumax_consulta.hypsometry_data import build_hypsometric_curve


def analysis(heights=(0, 0, 0, 0, 0, 0, 0, 8), hi=.125, area=8.0):
    return SimpleNamespace(values={'hypsometric': hi}, diagnostics={
        'hypsometric_curve': build_hypsometric_curve(heights, area, hi),
    })


class HypsometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def test_dimensional_axes_run_from_highest_cota_to_full_basin_area(self):
        data = curve_plot_data(analysis())
        self.assertTrue(data['available'])
        self.assertEqual((data['x'][0], data['x'][-1]), (0, 8))
        self.assertEqual((data['y'][0], data['y'][-1]), (8, 0))
        self.assertIn('km²', data['x_label'])
        self.assertEqual(data['y_label'], 'Altitud (m)')
        self.assertTrue(all(a <= b for a, b in zip(data['x'], data['x'][1:])))
        self.assertTrue(all(a >= b for a, b in zip(data['y'], data['y'][1:])))

    def test_dimensionless_mode_and_checkbox_preserve_the_exact_mean_based_hi(self):
        source = analysis()
        self.assertNotAlmostEqual(source.diagnostics['hypsometric_curve']['auc_approx'], .125)
        widget = HypsometricWidget()
        self.addCleanup(widget.close)
        widget.set_analysis(source)
        self.assertFalse(widget.is_dimensionless())
        self.assertIn('0,12', widget.hi_label.text())
        widget.dimensionless_check.click()
        self.assertTrue(widget.is_dimensionless())
        data = widget.plot_data()
        self.assertEqual(data['x_limits'], (0, 1))
        self.assertEqual(data['y_limits'], (0, 1))
        self.assertEqual(data['hi'], .125)
        widget.set_analysis(source)
        self.assertTrue(widget.is_dimensionless())
        widget.set_dimensionless(False)
        self.assertFalse(widget.dimensionless_check.isChecked())

    def test_flat_basin_has_a_dimensional_curve_and_undefined_relative_height(self):
        source = analysis((100, 100, 100, 100), None, 2)
        dimensional = curve_plot_data(source)
        self.assertTrue(dimensional['available'])
        self.assertTrue(all(value == 100 for value in dimensional['y']))
        relative = curve_plot_data(source, True)
        self.assertFalse(relative['available'])
        self.assertIn('no tiene desnivel', relative['reason'])
        self.assertIsNone(relative['hi'])

    def test_missing_legacy_distribution_is_explicit_and_does_not_read_a_dem(self):
        widget = HypsometricWidget()
        self.addCleanup(widget.close)
        widget.set_analysis(SimpleNamespace(values={'hypsometric': .4}, diagnostics={}))
        self.assertFalse(widget.plot_data()['available'])
        self.assertIn('no contiene la distribución', widget.status_label.text())
        self.assertIn('0,4', widget.hi_label.text())
        self.assertFalse(curve_plot_data(analysis((float('nan'),), None))['available'])

    def test_dimensional_integer_grid_preserves_the_exact_area_and_hi(self):
        source = analysis((970, 990, 1015, 1050, 1125), .4565, 54.25)
        before = deepcopy(source.diagnostics)
        data = curve_plot_data(source)
        self.assertEqual(data['x_ticks'], tuple(range(0, 56, 5)))
        self.assertEqual(data['x_display_limits'], (0, 55))
        self.assertEqual(data['x_limits'], (0, 54.25))
        self.assertEqual(data['x'][-1], 54.25)
        self.assertEqual(data['y_limits'], (970, 1125))
        self.assertEqual((data['y'][0], data['y'][-1]), (1125, 970))
        self.assertEqual(data['hi'], .4565)
        self.assertEqual(_tick(data['hi']), '0,46')
        self.assertEqual(source.diagnostics, before)

    def test_small_basin_uses_explicit_square_metres_without_duplicate_integer_ticks(self):
        for area in (.54, .0025, .00000025):
            with self.subTest(area=area):
                data = curve_plot_data(analysis(area=area))
                self.assertIn('(m²)', data['x_label'])
                self.assertEqual(data['x'][-1], area)
                self.assertEqual(data['x_scale'], 1e6)
                self.assertGreater(data['x'][-1]*data['x_scale'], 0)
                self.assertGreaterEqual(data['x_display_limits'][1], area*1e6)
                self.assertEqual(len(data['x_ticks']), len(set(data['x_ticks'])))
                self.assertTrue(all(float(value).is_integer() for value in data['x_ticks']))

    def test_dimensionless_grid_keeps_distinct_fractional_labels_with_at_most_two_decimals(self):
        data = curve_plot_data(analysis(area=.54), True)
        self.assertEqual(data['x_ticks'], tuple(i/10 for i in range(11)))
        self.assertEqual(data['x_display_limits'], (0, 1))
        self.assertEqual(data['x_scale'], 1)
        labels = [_tick(value) for value in data['x_ticks']]
        self.assertEqual(len(set(labels)), 11)
        self.assertIn('0,1', labels)
        for value in (1008.753, .4565, -.0001, 1125, None):
            text = _tick(value)
            self.assertLessEqual(len(text.split(',')[1]) if ',' in text else 0, 2)
        self.assertEqual(_tick(-.0001), '0')

    def test_png_contains_the_scientific_curve_in_both_modes(self):
        for relative in (False, True):
            with self.subTest(relative=relative):
                blob = render_hypsometric_png(analysis(), relative)
                self.assertTrue(blob.startswith(b'\x89PNG\r\n\x1a\n'))
                image = QImage.fromData(blob, 'PNG')
                self.assertEqual((image.width(), image.height()), (1280, 600))
                blue = 0
                for y in range(0, image.height(), 2):
                    for x in range(0, image.width(), 2):
                        color = image.pixelColor(x, y)
                        blue += color.red() < 50 and color.green() > 80 and color.blue() > 130
                self.assertGreater(blue, 20)


if __name__ == '__main__':
    unittest.main()
