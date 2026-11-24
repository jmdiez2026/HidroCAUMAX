"""Real Qt checks for the interpretation title and its in-header AI action."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qgis.PyQt.QtCore import QEvent, QPoint, Qt
from qgis.PyQt.QtGui import QFontMetrics, QHelpEvent
from qgis.PyQt.QtTest import QSignalSpy, QTest
from qgis.PyQt.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem
from qgis.core import QgsApplication

from caumax_consulta.morphology_table import InterpretationHeader


class MorphologyHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.table = QTableWidget(2, 4)
        self.addCleanup(self.table.close)
        self.addCleanup(self.table.deleteLater)
        self.table.setHorizontalHeaderLabels(['Parámetro', 'Valor', 'Unidad', 'Interpretación'])
        self.table.verticalHeader().hide()
        self.table.setStyleSheet('QHeaderView::section { background-color:white; color:#17202A; padding:4px; }')
        for row in range(2):
            for col in range(4):
                self.table.setItem(row, col, QTableWidgetItem(f'Original {row}/{col}'))
        self.original_font = self.table.font()
        self.header = InterpretationHeader(self.table)
        self.table.setHorizontalHeader(self.header)
        self.header.setSectionResizeMode(QHeaderView.Fixed)
        self.header.setMinimumSectionSize(20)
        self.table.show()
        self.resize(650)

    def resize(self, dialog_width):
        # Reserve normal dialog/group margins so these widths exercise the
        # actual, smaller header space rather than an edge-to-edge table.
        self.table.resize(dialog_width - 64, 220)
        self.app.processEvents()
        available = self.table.viewport().width()
        widths = [round(available * ratio) for ratio in (.4, .13, .09)]
        widths[0] -= max(0, 65 - widths[2])
        widths[2] = max(65, widths[2])
        widths.append(available - sum(widths))
        blocked = self.header.blockSignals(True)
        for col, width in enumerate(widths):
            self.table.setColumnWidth(col, width)
        self.header.blockSignals(blocked)
        self.header.update_layout()
        self.app.processEvents()

    def test_title_and_button_fit_same_header_height_at_requested_widths(self):
        for width in (520, 650, 900):
            with self.subTest(width=width):
                self.resize(width)
                button = self.header.analysis_button
                geometry = button.geometry()
                left = self.header.sectionViewportPosition(3)
                right = left + self.header.sectionSize(3) - 1
                self.assertTrue(button.isVisible())
                self.assertEqual(button.text(), 'Análisis IA')
                self.assertIn('#174D78', button.styleSheet())
                self.assertIs(button.parentWidget(), self.header.viewport())
                self.assertEqual(geometry.top(), 0)
                self.assertEqual(geometry.height(), self.header.height())
                self.assertEqual(geometry.right(), right)
                self.assertGreaterEqual(geometry.left(), left)
                self.assertLess(self.header._title_rect.right(), geometry.left())
                self.assertEqual(self.header._title_text, 'Interpretación')
                title_metrics = QFontMetrics(self.header._title_font)
                self.assertLessEqual(title_metrics.horizontalAdvance('Interpretación'), self.header._title_rect.width())
                metrics = QFontMetrics(button.font())
                self.assertGreaterEqual(geometry.width(), metrics.horizontalAdvance(button.text()) + 10)
                self.assertGreaterEqual(geometry.height(), metrics.height() + 2)
                self.assertEqual(self.header.font(), self.original_font)
                self.assertEqual(self.table.font(), self.original_font)

    def test_button_emits_only_when_enabled_and_never_changes_table_values(self):
        spy = QSignalSpy(self.header.analysis_requested)
        before = [[self.table.item(row, col).text() for col in range(4)] for row in range(2)]
        button = self.header.analysis_button
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(len(spy), 1)
        button.setEnabled(False)
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(len(spy), 1)
        button.setEnabled(True)
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(len(spy), 2)
        self.assertEqual(before, [[self.table.item(row, col).text() for col in range(4)] for row in range(2)])

    def test_scroll_and_section_move_keep_button_in_its_logical_column(self):
        self.table.resize(440, 220)
        for col, width in enumerate((280, 160, 140, 250)):
            self.table.setColumnWidth(col, width)
        self.app.processEvents()
        scrollbar = self.table.horizontalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0)
        for offset in (scrollbar.maximum(), 0):
            scrollbar.setValue(offset)
            self.app.processEvents()
            button = self.header.analysis_button
            self.assertEqual(button.geometry().right(), self.header.sectionViewportPosition(3) + self.header.sectionSize(3) - 1)
        self.header.setSectionsMovable(True)
        self.header.moveSection(self.header.visualIndex(3), 0)
        self.app.processEvents()
        self.assertEqual(self.header.analysis_button.geometry().right(), self.header.sectionViewportPosition(3) + self.header.sectionSize(3) - 1)
        self.assertEqual([self.table.horizontalHeaderItem(col).text() for col in range(4)],
                         ['Parámetro', 'Valor', 'Unidad', 'Interpretación'])

    def test_hidden_or_too_narrow_column_does_not_cover_adjacent_headers(self):
        self.table.setColumnHidden(3, True)
        self.app.processEvents()
        self.assertTrue(self.header.analysis_button.isHidden())
        self.table.setColumnHidden(3, False)
        self.table.setColumnWidth(3, 20)
        self.app.processEvents()
        self.assertTrue(self.header.analysis_button.isHidden())
        self.assertNotEqual(self.header._title_text, 'Interpretación')
        x = self.header.sectionViewportPosition(3) + 1
        event = QHelpEvent(QEvent.ToolTip, QPoint(x, 4), self.header.mapToGlobal(QPoint(x, 4)))
        with patch('caumax_consulta.morphology_table.QToolTip.showText') as tooltip:
            self.header.viewportEvent(event)
            self.assertIn('botón superior', tooltip.call_args.args[1])
        self.resize(650)
        self.assertTrue(self.header.analysis_button.isVisible())
        self.assertEqual(self.header._title_text, 'Interpretación')

    def test_header_font_change_is_local_and_relayout_handles_blocked_signals(self):
        font = self.header.font()
        font.setPointSize(13)
        self.header.setFont(font)
        self.resize(520)
        self.assertEqual(self.header.font(), font)
        self.assertEqual(self.table.font(), self.original_font)
        self.assertLess(self.header.analysis_button.font().pointSizeF(), font.pointSizeF())
        self.assertEqual(self.header._title_text, 'Interpretación')
        self.assertGreaterEqual(self.header._title_rect.width(), QFontMetrics(self.header._title_font).horizontalAdvance('Interpretación'))
        self.assertEqual(self.header.analysis_button.height(), self.header.height())


if __name__ == '__main__':
    unittest.main()
