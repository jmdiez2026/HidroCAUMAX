"""Compact QGIS header, complete resolution labels and guarded AI entry point."""
from dataclasses import replace
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontMetrics
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QGroupBox

import test_drainage_dialog as fixture


OPTIONS = [dict(key=key,resolution=float(key),available=key in ('500','25','5'),
                provider='CAUMAX' if key=='500' else 'CNIG',label=key+' m',status='Disponible')
           for key in ('500','25','5','2','0.5')]
LABELS = ['500mx500m','25mx25m','5mx5m','2mx2m','0,5mx0,5m']


class CompactLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog
        self.dialog._resolution_options(OPTIONS)
        self.dialog.show()
        self.resize(650)

    def resize(self, width):
        self.dialog.resize(width,1100)
        QTest.qWait(100)
        self.dialog._fit_rows()
        self.fixture.app.processEvents()

    def test_exact_resolution_labels_share_one_row_at_normal_widths(self):
        dialog = self.dialog
        for width in (650,900):
            with self.subTest(width=width):
                self.resize(width)
                boxes = list(dialog.resolution_checkboxes.values())
                self.assertEqual([box.text() for box in boxes],LABELS)
                self.assertEqual(dialog._resolution_layout_columns,5)
                self.assertEqual(len({box.y() for box in boxes}),1)
                for box in boxes:
                    self.assertGreaterEqual(box.width(),box.minimumSizeHint().width())
                    self.assertLessEqual(box.geometry().right(),dialog._resolution_checks_widget.width())
                self.assertLessEqual(dialog.body_scroll.widget().width(),dialog.body_scroll.viewport().width())

    def test_resolution_labels_wrap_whole_choices_in_a_narrow_container(self):
        widget = self.dialog._resolution_checks_widget
        widget.setFixedWidth(320)
        self.dialog._layout_resolution_options()
        widget.layout().activate()
        boxes = list(self.dialog.resolution_checkboxes.values())
        self.assertGreater(len({box.y() for box in boxes}),1)
        self.assertEqual([box.text() for box in boxes],LABELS)
        for box in boxes:
            self.assertGreaterEqual(box.width(),box.minimumSizeHint().width())
            self.assertLessEqual(box.geometry().right(),widget.width())

    def test_first_header_line_keeps_mdt_and_exact_point_without_overlap(self):
        dialog = self.dialog
        for width in (650,900):
            with self.subTest(width=width):
                self.resize(width)
                left,right = dialog.context_mdt,dialog.context_point
                self.assertEqual(left.text(),'MDT activo: 25mx25m')
                self.assertEqual(right.text(),'Punto original de consulta: X=500062.5; Y=4699937.5 · EPSG:25830')
                self.assertEqual(left.y(),right.y())
                self.assertLess(left.geometry().right(),right.geometry().left())
                self.assertFalse(right.wordWrap())
                for label in (left,right):
                    self.assertLessEqual(QFontMetrics(label.font()).horizontalAdvance(label.text()),label.width())
                self.assertLess(dialog.context_header.geometry().bottom(),dialog.context.geometry().top())
                self.assertTrue(dialog.context.text().startswith('Caudal CAUMAX original:'))

    def test_ai_button_is_at_right_of_morphology_title_and_disabled_during_work(self):
        dialog = self.dialog
        self.assertEqual(dialog.analysis_ai_button.text(),'Análisis IA')
        self.assertIn('#174D78',dialog.analysis_ai_button.styleSheet())
        self.assertEqual(dialog.analysis_ai_button.parentWidget(),dialog.morphology_group)
        self.assertEqual(dialog.analysis_ai_button.y(),0)
        self.assertLessEqual(dialog.analysis_ai_button.geometry().right(),dialog.morphology_group.width())
        self.assertTrue(dialog.analysis_ai_button.isEnabled())
        for flag in ('_surface_pending','_resolution_pending','_saving_project'):
            with self.subTest(flag=flag):
                setattr(dialog,flag,True)
                dialog._refresh_ai_button()
                self.assertFalse(dialog.analysis_ai_button.isEnabled())
                setattr(dialog,flag,False)
        dialog._surface_valid = False
        dialog._refresh_ai_button()
        self.assertFalse(dialog.analysis_ai_button.isEnabled())
        dialog._surface_valid = True
        dialog._drainage_controller.busy = True
        dialog._refresh_ai_button()
        self.assertFalse(dialog.analysis_ai_button.isEnabled())
        dialog._drainage_controller.busy = False
        dialog._refresh_ai_button()
        self.assertTrue(dialog.analysis_ai_button.isEnabled())

    def test_ai_lazy_call_receives_current_analysis_query_and_snapshot(self):
        dialog = self.dialog
        module = ModuleType('caumax_consulta.ai_analysis')
        module.show_analysis = Mock(return_value='opened')
        with patch.dict(sys.modules,{'caumax_consulta.ai_analysis':module}):
            self.assertEqual(dialog._show_ai_analysis(),'opened')
            args = module.show_analysis.call_args.args
            self.assertIs(args[0],dialog)
            self.assertIs(args[1],dialog.analysis)
            self.assertIs(args[2],dialog.query)
            self.assertEqual(args[3],dialog.get_smoothing_state())
            module.show_analysis.reset_mock()
            dialog._surface_pending = True
            self.assertIsNone(dialog._show_ai_analysis())
            module.show_analysis.assert_not_called()
            dialog._surface_pending = False

    def test_hydrology_uses_its_own_groups_without_combined_outer_frame(self):
        dialog = self.dialog
        self.assertIs(dialog.basin_hydrology_widget.parentWidget(),dialog.body_scroll.widget())
        titles = [group.title() for group in dialog.findChildren(QGroupBox)]
        self.assertNotIn('P₀ y precipitación máxima diaria de la cuenca',titles)

    def test_upstream_only_selection_is_identified_below_the_query_header(self):
        dialog = self.dialog
        diagnostics = dict(dialog.analysis.diagnostics)
        diagnostics['outlet_snap'] = dict(selection_mode='upstream_only',distance_m=12.5)
        dialog.analysis = replace(dialog.analysis,diagnostics=diagnostics)
        dialog._update_context()
        self.assertIn('Sección conservada: cierre en la misma sección o aguas arriba',dialog.context.text())
        self.assertIn('12.50 m',dialog.context.text())
        self.assertIn('El área del MDT de 500 m se usa solo como referencia.',dialog.context.text())


if __name__ == '__main__':
    unittest.main()
