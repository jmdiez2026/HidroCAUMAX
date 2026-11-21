"""Reference shapes and unit invariance for the displayed shape indices."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.metrics import shape_metrics


class ShapeMetricsTests(unittest.TestCase):
    def test_circle_is_normalized_and_square_has_known_indices(self):
        circle = shape_metrics(math.pi * 80 ** 2, 2 * math.pi * 80)
        self.assertAlmostEqual(circle.compactness, 1.0)
        self.assertAlmostEqual(circle.circularity, 1.0)
        square = shape_metrics(1000 ** 2, 4 * 1000)
        self.assertAlmostEqual(square.compactness, 2 / math.sqrt(math.pi))
        self.assertAlmostEqual(square.circularity, math.pi / 4)

    def test_indices_do_not_depend_on_scale_and_are_reciprocal(self):
        reference = shape_metrics(180e6, 89000)
        scaled = shape_metrics(180e6 * 17 ** 2, 89000 * 17)
        self.assertAlmostEqual(reference.compactness, scaled.compactness)
        self.assertAlmostEqual(reference.circularity, scaled.circularity)
        self.assertAlmostEqual(reference.circularity * reference.compactness ** 2, 1.0)

    def test_invalid_inputs_do_not_produce_plausible_indices(self):
        for value in (0, -1, math.nan, math.inf, None, True, 'invalid'):
            for area, perimeter in ((value, 10), (10, value)):
                with self.subTest(area=area, perimeter=perimeter):
                    with self.assertRaises(ValueError):
                        shape_metrics(area, perimeter)


if __name__ == '__main__':
    unittest.main()
