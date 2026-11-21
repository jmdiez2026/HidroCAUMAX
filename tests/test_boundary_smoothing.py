"""Physical-strength, geometry and complexity checks for Gaussian smoothing."""

import math
import os
from pathlib import Path
import sys
import time
import unittest

import numpy as np
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.boundary_smoothing import (
    MAX_OUTPUT_VERTICES, smooth_boundary, suggested_degree,
)


class BoundarySmoothingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def geometry(self):
        return QgsGeometry.fromWkt(
            'MULTIPOLYGON (((0 0,100 0,100 100,0 100,0 0),'
            '(30 30,30 70,70 70,70 30,30 30)),'
            '((140 0,220 0,220 100,140 100,140 0)))'
        )

    def test_parts_holes_and_closed_rings_survive_all_key_degrees(self):
        original = self.geometry()
        original_bytes = bytes(original.asWkb())
        for degree in (0.5, 3, 15, 30, 45):
            with self.subTest(degree=degree):
                result = smooth_boundary(original, degree, 1, 1, QgsPointXY(10, 10))
                self.assertTrue(result.isGeosValid())
                parts = result.asMultiPolygon()
                self.assertEqual([len(part) for part in parts], [2, 1])
                for part in parts:
                    for ring in part:
                        self.assertEqual(ring[0], ring[-1])
                self.assertTrue(result.intersects(QgsGeometry.fromPointXY(QgsPointXY(10, 10))))
        self.assertEqual(bytes(original.asWkb()), original_bytes)

    def test_fractional_degrees_change_continuously_without_vertex_growth(self):
        original = QgsGeometry.fromWkt('POLYGON ((0 0,100 0,100 100,0 100,0 0))')
        values = [smooth_boundary(original, degree, 10, 10) for degree in (10, 10.5, 11, 15, 45)]
        self.assertEqual(len({value.constGet().nCoordinates() for value in values}), 1)
        self.assertFalse(values[0].isGeosEqual(values[1]))
        small_change = values[0].symDifference(values[1]).area()
        larger_change = values[0].symDifference(values[2]).area()
        self.assertGreater(small_change, 0)
        self.assertLess(small_change, larger_change)
        self.assertLess(values[-1].area(), values[-2].area())
        self.assertGreater(values[-2].symDifference(values[-1]).area(), small_change)

    def test_boundary_closure_is_anchored_without_moving_its_coordinates(self):
        original = QgsGeometry.fromWkt('POLYGON ((0 0,100 0,100 100,0 100,0 0))')
        closure = QgsPointXY(0, 0)
        before = (closure.x(), closure.y())
        result = smooth_boundary(original, 45, 5, 5, closure)
        self.assertTrue(result.isGeosValid())
        self.assertTrue(result.intersects(QgsGeometry.fromPointXY(closure)))
        self.assertEqual((closure.x(), closure.y()), before)

    def test_closure_constraint_has_no_jump_when_it_first_becomes_active(self):
        original = QgsGeometry.fromWkt('POLYGON ((0 0,100 0,100 100,0 100,0 0))')
        closure = QgsPointXY(1, 1)
        point = QgsGeometry.fromPointXY(closure)
        low, high = 0.5, 45.0
        self.assertTrue(smooth_boundary(original, low, 10, 10).intersects(point))
        self.assertFalse(smooth_boundary(original, high, 10, 10).intersects(point))
        for _ in range(20):
            middle = (low + high) / 2
            if smooth_boundary(original, middle, 10, 10).intersects(point):
                low = middle
            else:
                high = middle
        before = smooth_boundary(original, low, 10, 10, closure)
        after = smooth_boundary(original, high, 10, 10, closure)
        self.assertTrue(before.intersects(point) and after.intersects(point))
        self.assertLess(before.symDifference(after).area() / original.area(), 1e-5)

    def test_sigma_is_physical_and_scales_with_geometry_and_cells(self):
        small = QgsGeometry.fromWkt('POLYGON ((0 0,100 0,100 100,0 100,0 0))')
        large = QgsGeometry.fromWkt('POLYGON ((0 0,200 0,200 200,0 200,0 0))')
        a = smooth_boundary(small, 10.5, 10, 20)
        b = smooth_boundary(large, 10.5, 20, 40)
        self.assertAlmostEqual(b.area() / a.area(), 4, places=10)

    def test_dense_large_ring_has_fixed_vertex_budget_and_no_exponential_passes(self):
        count = 100000
        angles = np.linspace(0, 2 * math.pi, count, endpoint=False)
        radius = 10000 + 150 * np.sin(100 * angles)
        points = [QgsPointXY(float(r * math.cos(t)), float(r * math.sin(t)))
                  for r, t in zip(radius, angles)]
        points.append(QgsPointXY(points[0]))
        original = QgsGeometry.fromPolygonXY([points])
        started = time.monotonic()
        a = smooth_boundary(original, 15, 1, 1)
        b = smooth_boundary(original, 45, 1, 1)
        self.assertTrue(a.isGeosValid() and b.isGeosValid())
        self.assertLessEqual(a.constGet().nCoordinates(), MAX_OUTPUT_VERTICES)
        self.assertEqual(a.constGet().nCoordinates(), b.constGet().nCoordinates())
        self.assertFalse(a.isGeosEqual(b))
        self.assertLess(time.monotonic() - started, 12)

    def test_suggestion_is_a_conservative_valid_float(self):
        original = self.geometry()
        value = suggested_degree(original, 1, 1, QgsPointXY(10, 10))
        self.assertIsInstance(value, float)
        self.assertIn(value, (0.5, 1.0, 2.0, 3.0))
        result = smooth_boundary(original, value, 1, 1, QgsPointXY(10, 10))
        self.assertLessEqual(abs(result.area() / original.area() - 1), 0.01)

    def test_invalid_inputs_are_controlled_and_geometry_is_never_repaired(self):
        original = self.geometry()
        for degree in (None, True, 0, 0.49, 45.01, math.nan, math.inf, '10'):
            with self.subTest(degree=degree), self.assertRaises(ValueError):
                smooth_boundary(original, degree, 1, 1)
        for width, height in ((0, 1), (-1, 1), (True, 1), (1, math.nan), (1, None)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                smooth_boundary(original, 1, width, height)
        for geometry in (None, QgsGeometry(), QgsGeometry.fromWkt('LINESTRING (0 0,1 1)'),
                         QgsGeometry.fromWkt('POLYGON ((0 0,10 10,0 10,10 0,0 0))')):
            with self.subTest(geometry=geometry), self.assertRaises(ValueError):
                smooth_boundary(geometry, 1, 1, 1)
        with self.assertRaisesRegex(ValueError, 'cierre'):
            smooth_boundary(original, 1, 1, 1, QgsPointXY(300, 300))

    def test_diagonally_touching_parts_remain_separate(self):
        original = QgsGeometry.fromWkt(
            'MULTIPOLYGON (((0 0,10 0,10 10,0 10,0 0)),'
            '((10 10,20 10,20 20,10 20,10 10)))'
        )
        result = smooth_boundary(original, 45, 1, 1, QgsPointXY(5, 5))
        self.assertTrue(result.isGeosValid())
        self.assertEqual(len(result.asMultiPolygon()), 2)

    def test_actual_basin_cases_including_degree_45(self):
        data = (Path(os.environ.get('APPDATA', '')) / 'QGIS' / 'QGIS3' /
                'profiles' / 'default' / 'python' / 'plugins' / 'caumax' / 'data')
        if not (data / 'dir.tif').is_file():
            self.skipTest('CAUMAX no está instalado')
        from caumax_consulta.basin import delineate
        for x, y, expected in ((341825, 4646700, 720), (334288.7, 4723787.6, 1556)):
            basin = delineate(data / 'dir.tif', data / 'mdt.tif', x, y)
            self.assertEqual(basin.cell_count, expected)
            original = QgsGeometry()
            original.fromWkb(basin.geometry_wkb)
            closure = QgsPointXY(basin.outlet_x, basin.outlet_y)
            for degree in (0.5, 3, 15, 30, 45):
                with self.subTest(cells=expected, degree=degree):
                    result = smooth_boundary(original, degree, basin.cell_width, basin.cell_height, closure)
                    self.assertTrue(result.isGeosValid())
                    self.assertTrue(result.intersects(QgsGeometry.fromPointXY(closure)))
                    self.assertLessEqual(result.constGet().nCoordinates(), MAX_OUTPUT_VERTICES)
            low = smooth_boundary(original, 15, basin.cell_width, basin.cell_height, closure)
            high = smooth_boundary(original, 45, basin.cell_width, basin.cell_height, closure)
            self.assertFalse(low.isGeosEqual(high))


if __name__ == '__main__':
    unittest.main()
