"""Compact QGIS header, complete resolution labels and guarded AI entry point."""
from dataclasses import replace
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt.QtCore import QPoint, Qt
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
        self.assertIs(dialog.context_frame.parentWidget(),dialog.resolution_group)
        self.assertIs(dialog.context_header.parentWidget(),dialog.context_frame)
        self.assertIs(dialog.context.parentWidget(),dialog.context_frame)
        resolution_layout=dialog.resolution_group.layout()
        self.assertEqual(resolution_layout.indexOf(dialog.context_frame),
                         resolution_layout.indexOf(dialog.resolution_status)+1)
        self.assertEqual(resolution_layout.indexOf(dialog.resolution_progress),
                         resolution_layout.indexOf(dialog.context_frame)+1)
        body=dialog.body_scroll.widget().layout()
        self.assertEqual([body.itemAt(i).widget() for i in range(3)],
                         [dialog.resolution_group,dialog.export_button.parentWidget(),dialog.morphology_group])
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
                self.assertLess(dialog.resolution_status.geometry().bottom(),dialog.context_frame.geometry().top())
                self.assertLessEqual(dialog.context.geometry().bottom(),dialog.context_frame.height()-6)

    def test_ai_button_shares_resolution_title_row_and_is_disabled_during_work(self):
        dialog = self.dialog
        button=dialog.analysis_ai_button
        self.assertEqual(button.text(),'Análisis IA')
        self.assertIn('#174D78',button.styleSheet())
        self.assertIs(button.parentWidget(),dialog.resolution_group)
        self.assertIs(dialog.layout().itemAt(0).widget(),dialog.body_scroll)
        self.assertFalse(button.icon().isNull())
        self.assertFalse(button.icon().pixmap(button.iconSize()).isNull())
        self.assertEqual(button.iconSize().width(),18)
        self.assertEqual(button.layoutDirection(),Qt.LeftToRight)
        for width in (520,650,900):
            with self.subTest(width=width):
                self.resize(width)
                group=dialog.resolution_group
                title=group.title_rect()
                self.assertLessEqual(abs(button.geometry().center().y()-title.center().y()),1)
                self.assertGreater(button.geometry().left(),title.right())
                self.assertEqual(button.geometry().right(),group.width()-group.layout().contentsMargins().right()-1)
                self.assertLess(button.geometry().bottom(),dialog.resolution_help.geometry().top())
                text_width=QFontMetrics(button.font()).horizontalAdvance(button.text())
                self.assertGreaterEqual(button.width(),text_width+button.iconSize().width()+4)
                scrollbar=dialog.body_scroll.verticalScrollBar()
                scrollbar.setValue(0)
                self.fixture.app.processEvents()
                position=button.mapTo(dialog,QPoint(0,0))
                self.assertGreater(scrollbar.maximum(),0)
                scrollbar.setValue(scrollbar.maximum())
                self.fixture.app.processEvents()
                self.assertLess(button.mapTo(dialog,QPoint(0,0)).y(),position.y())
                self.assertTrue(button.isVisible())
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
        from caumax_consulta.ai_analysis import build_context
        module = ModuleType('caumax_consulta.ai_analysis')
        module.build_context = build_context
        job = Mock()
        module.request_interpretations = Mock(return_value=job)
        with patch.dict(sys.modules,{'caumax_consulta.ai_analysis':module}):
            self.assertIs(dialog._show_ai_analysis(),job)
            args = module.request_interpretations.call_args.args
            self.assertIs(args[0],dialog)
            self.assertIs(args[1],dialog.analysis)
            self.assertIs(args[2],dialog.query)
            self.assertEqual(args[3],dialog.get_smoothing_state())
            self.assertTrue(callable(module.request_interpretations.call_args.kwargs['on_interpretations']))
            module.request_interpretations.reset_mock()
            dialog._surface_pending = True
            self.assertIsNone(dialog._show_ai_analysis())
            module.request_interpretations.assert_not_called()
            dialog._surface_pending = False

    def test_hydrology_uses_its_own_groups_without_combined_outer_frame(self):
        dialog = self.dialog
        self.assertIs(dialog.basin_hydrology_widget.parentWidget(),dialog.body_scroll.widget())
        titles = [group.title() for group in dialog.findChildren(QGroupBox)]
        self.assertNotIn('P₀ y precipitación máxima diaria de la cuenca',titles)

    def test_grid_outlet_approximation_is_identified_below_the_query_header(self):
        dialog = self.dialog
        diagnostics = dict(dialog.analysis.diagnostics)
        diagnostics['outlet_snap'] = dict(selection_mode='upstream_only',distance_m=12.5)
        dialog.analysis = replace(dialog.analysis,diagnostics=diagnostics)
        dialog._update_context()
        self.assertIn('Cierre aproximado a centro de celda; no es una sección topográfica exacta.',dialog.context.text())
        self.assertIn('12.50 m',dialog.context.text())
        self.assertIn('La referencia sigue siendo el punto original.',dialog.context.text())


if __name__ == '__main__':
    unittest.main()
