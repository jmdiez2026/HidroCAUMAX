"""Tests for the area-scaled channel-initiation heuristic (no QGIS needed)."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drainage_threshold',
    ROOT / 'src' / 'caumax_consulta' / 'drainage_threshold.py')
threshold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(threshold)


class DrainageThresholdTests(unittest.TestCase):
    def test_rational_example_25_metres(self):
        area, cell_area = 14_750_000, 625
        self.assertEqual(threshold.recommended_threshold(area, cell_area), 73_750)
        self.assertEqual(threshold.manual_step(area, cell_area), 1_475)
        self.assertEqual(threshold.automatic_threshold_cells(area, cell_area),
                         (118, 59, 29, 14, 7))

    def test_same_basin_finer_resolution_keeps_initial_area(self):
        self.assertEqual(threshold.recommended_threshold(14_750_000, 25), 73_750)
        self.assertEqual(threshold.manual_step(14_750_000, 25), 1_475)
        self.assertEqual(threshold.automatic_threshold_cells(14_750_000, 25)[0], 2_950)

    def test_manual_step_proportional_except_native_cell_floor(self):
        self.assertEqual(threshold.manual_step(50_000_000, 625), 5_000)
        self.assertEqual(threshold.manual_step(1_000_000, 625), 625)
        self.assertEqual(threshold.manual_step(1_000_000, 25), 100)

    def test_very_small_basin_does_not_round_to_zero(self):
        self.assertEqual(threshold.recommended_threshold(100, 625), 625)
        self.assertEqual(threshold.automatic_threshold_cells(100, 625), (1,))
        self.assertEqual(threshold.manual_step(100, 625), 625)

    def test_diminishing_candidates_are_unique_and_bounded(self):
        for basin, cell in [(25_000, 625), (10_000_000, 25), (50_000_000, 625)]:
            values = threshold.automatic_threshold_cells(basin, cell)
            self.assertTrue(1 <= len(values) <= 5)
            self.assertTrue(all(a > b for a, b in zip(values, values[1:])))
            self.assertTrue(all(isinstance(a, int) and a >= 1 for a in values))

    def test_bad_areas_rejected(self):
        for area in (0, -10, float('nan'), float('inf'), 'no es un número', None):
            with self.subTest(area=area), self.assertRaises(ValueError):
                threshold.recommended_threshold(area, 625)
            with self.subTest(area=area), self.assertRaises(ValueError):
                threshold.manual_step(10_000, area)


if __name__ == '__main__':
    unittest.main()
