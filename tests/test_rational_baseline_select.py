"""Baseline (500 m) selector regressions, without QGIS runtime dependencies."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'src' / 'caumax_consulta'


def method(file, cls, name, namespace):
    module = ast.parse((ROOT / file).read_text(encoding='utf-8'))
    klass = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == cls)
    function = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    function.decorator_list.clear()
    scope = dict(namespace)
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[function], type_ignores=[])), str(ROOT / file), 'exec'), scope)
    return scope[name]


class BaselineSelectorTests(unittest.TestCase):
    def controller(self, discovering=False):
        calls = []
        class Resolution:
            busy = discovering
            _task = SimpleNamespace(kind='discover') if discovering else None
            base_analysis = 'native-analysis'
            base_query = 'native-query'
            def select(self, key):
                calls.append(('select', key))
                return True
        controller = SimpleNamespace(
            _disposed=False, _task=None, resolution=Resolution(),
            base_analysis=SimpleNamespace(cell_width=500, cell_height=500),
            query=object(), signature='unchanged',
            drainage=None, _pending=None, _basin_pending=None, active_key='500',
            cancel=lambda: calls.append(('cancel',)),
            _on_basin=lambda k, a, q: calls.append(('basin', k, a, q)),
            _on_error=lambda m: calls.append(('error', m)),
            set_context=lambda q: calls.append(('new-query',)))
        select = method('rational_layers_controller.py',
            'RationalLayersController', 'select_resolution',
            {'math': math, '_query_signature': lambda _: 'unchanged'})
        return controller, select, calls

    def test_original_500_display_does_not_abort_discovery(self):
        c, select, calls = self.controller(discovering=True)
        self.assertTrue(select(c, '500'))
        self.assertEqual(calls, [('basin', '500', 'native-analysis', 'native-query')])

    def test_original_500_restores_cached_base(self):
        c, select, calls = self.controller()
        self.assertTrue(select(c, '500'))
        self.assertEqual(calls, [('cancel',), ('basin', '500',
                                             'native-analysis', 'native-query')])

    def test_finer_mdt_still_uses_verified_delineation(self):
        c, select, calls = self.controller()
        self.assertTrue(select(c, '25'))
        self.assertEqual(calls, [('cancel',), ('select', '25')])
        self.assertEqual(c._basin_pending, '25')

    def test_original_is_not_a_taudem_network(self):
        panel = (ROOT / 'rational_layers_panel.py').read_text(encoding='utf-8')
        self.assertIn("if key is None or reference is None or key == '500':", panel)
        self.assertIn("if self._selected_key() == '500':", panel)
        self.assertIn("original CAUMAX", panel)
        self.assertIn('self._resolution_explicit', panel)

    def test_native_basin_not_replaced_by_parallel_500_polygon(self):
        basin = (ROOT / 'basin_dialog.py').read_text(encoding='utf-8')
        self.assertIn('if key == controller._base_key:', basin)
        self.assertIn('return bool(controller.select(key))', basin)


if __name__ == '__main__':
    unittest.main()
