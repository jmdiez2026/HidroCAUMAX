"""The interpretation header remains a normal, readable Qt section."""
import unittest
from unittest.mock import patch
from qgis.PyQt.QtCore import QEvent, QPoint
from qgis.PyQt.QtGui import QHelpEvent
from qgis.PyQt.QtWidgets import QTableWidget, QPushButton
from qgis.core import QgsApplication
from caumax_consulta.morphology_table import InterpretationHeader


class MorphologyHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.table = QTableWidget(2, 3)
        self.addCleanup(self.table.close)
        self.addCleanup(self.table.deleteLater)
        self.table.setHorizontalHeaderLabels(['Parámetro', 'Valor', 'Interpretación'])
        self.header = InterpretationHeader(self.table)
        self.table.setHorizontalHeader(self.header)

    def test_header_uses_full_native_section_without_action_or_font_shrink(self):
        font = self.header.font()
        for width in (520, 650, 900):
            self.table.resize(width, 220)
            self.table.show(); self.app.processEvents()
            self.assertEqual(self.table.horizontalHeaderItem(2).text(), 'Interpretación')
            self.assertEqual(self.header.font(), font)
            self.assertFalse(self.header.findChildren(QPushButton))
        self.header.setSectionsMovable(True)
        self.header.moveSection(2, 0)
        self.assertEqual(self.table.horizontalHeaderItem(2).text(), 'Interpretación')

    def test_tooltip_explains_automatic_interpretation_and_full_cell_comment(self):
        self.table.show(); self.app.processEvents()
        x = self.header.sectionViewportPosition(2) + 2
        point = QPoint(x, 4)
        event = QHelpEvent(QEvent.ToolTip, point, self.header.mapToGlobal(point))
        with patch('caumax_consulta.morphology_table.QToolTip.showText') as tooltip:
            self.header.viewportEvent(event)
            text = tooltip.call_args.args[1]
            self.assertIn('Interpretación automática', text)
            self.assertIn('Cuenca Hidrográfica', text)
            self.assertNotIn('Análisis IA', text)


if __name__ == '__main__':
    unittest.main()
