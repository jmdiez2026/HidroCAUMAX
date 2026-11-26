"""Compact QGIS header, complete resolution labels and automatic interpretation."""
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
                self.assertEqual(right.text(),'Punto original de consulta: X=500062,5; Y=4699937,5 · EPSG:25830')
                self.assertEqual(left.y(),right.y())
                self.assertLess(left.geometry().right(),right.geometry().left())
                self.assertFalse(right.wordWrap())
                for label in (left,right):
                    self.assertLessEqual(QFontMetrics(label.font()).horizontalAdvance(label.text()),label.width())
                self.assertLess(dialog.context_header.geometry().bottom(),dialog.context.geometry().top())
                self.assertTrue(dialog.context.text().startswith('Caudal CAUMAX original:'))
                self.assertLess(dialog.resolution_status.geometry().bottom(),dialog.context_frame.geometry().top())
                self.assertLessEqual(dialog.context.geometry().bottom(),dialog.context_frame.height()-6)

    def test_automatic_interpretation_removes_both_ai_buttons(self):
        from qgis.PyQt.QtWidgets import QPushButton
        dialog = self.dialog
        self.assertIs(dialog.layout().itemAt(0).widget(), dialog.body_scroll)
        self.assertFalse(hasattr(dialog, 'analysis_ai_button'))
        self.assertFalse(hasattr(dialog, 'analysis_header_button'))
        for width in (520, 650, 900):
            with self.subTest(width=width):
                self.resize(width)
                self.assertFalse(any('Análisis IA' in b.text() for b in dialog.findChildren(QPushButton)))
                self.assertEqual(dialog.table.horizontalHeaderItem(3).text(), 'Interpretación')
                self.assertTrue(all(dialog.table.item(row, 3).text() for row in range(dialog.table.rowCount())))

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
        self.assertIn('12,50 m',dialog.context.text())
        self.assertIn('La referencia sigue siendo el punto original.',dialog.context.text())


if __name__ == '__main__':
    unittest.main()
