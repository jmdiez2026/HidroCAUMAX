"""Qt regression: the Spanish UI never leaks comma decimals into CAUMAX."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDialog, QLineEdit
from qgis.core import QgsApplication

from caumax_consulta.native_decimal import (
    RationalDecimalPresentation, canonical_numeric, decimal_display,
)


class NativeDecimalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.dialog = QDialog()
        self.dialog.txtX = QLineEdit('346039.6', self.dialog)
        self.dialog.txtX.setObjectName('txtX')
        self.dialog.txtArea = QLineEdit('14.750', self.dialog)
        self.dialog.txtArea.setObjectName('txtArea')
        self.dialog.txtResultFlow = QLineEdit('10', self.dialog)
        self.dialog.txtResultFlow.setObjectName('txtResultFlow')
        self.dialog.txtRainfall = QLineEdit('92.10', self.dialog)
        self.dialog.txtRainfall.setObjectName('txtRainfall')
        self.dialog.txtRainfall.setReadOnly(True)
        for name in ('txtI1Id', 'txtIntensity'):
            widget = QLineEdit('', self.dialog)
            widget.setObjectName(name)
            setattr(self.dialog, name, widget)
        self.native_writes = []
        self.dialog.txtArea.textChanged.connect(self.native_writes.append)
        self.presentation = RationalDecimalPresentation(self.dialog)

    def tearDown(self):
        self.presentation.dispose()
        sip.delete(self.dialog)

    def editor(self, name):
        return self.dialog.findChild(QLineEdit, 'hidrocaumax_decimal_' + name)

    def test_native_fields_retain_dot_but_render_comma(self):
        self.assertEqual(self.dialog.txtX.text(), '346039.6')
        self.assertEqual(self.editor('txtX').text(), '346039,6')
        self.assertEqual(self.dialog.txtArea.text(), '14.750')
        self.assertEqual(self.editor('txtArea').text(), '14,750')
        self.assertTrue(self.editor('txtRainfall').isReadOnly())
        self.assertEqual(self.editor('txtRainfall').text(), '92,10')
        self.assertEqual(self.native_writes, [])

        self.dialog.txtArea.setText('16.125')
        self.assertEqual(self.editor('txtArea').text(), '16,125')
        self.assertEqual(self.native_writes, ['16.125'])

    def test_result_flow_always_shows_two_decimals_with_comma(self):
        self.assertEqual(self.editor('txtResultFlow').text(), '10,00')
        self.dialog.txtResultFlow.setText('10.346')
        self.assertEqual(self.editor('txtResultFlow').text(), '10,35')
        self.dialog.txtResultFlow.setText('0')
        self.assertEqual(self.editor('txtResultFlow').text(), '0,00')
        self.dialog.txtResultFlow.setText('')
        self.assertEqual(self.editor('txtResultFlow').text(), '')
        self.assertEqual(self.dialog.txtResultFlow.text(), '')

    def test_ca_umax_blocked_signals_do_not_leave_numeric_cells_blank(self):
        # CAUMAX updates I1/Id and I with blockSignals(True), so the
        # presentation must synchronize from the actual native field.
        for name, numeric in (('txtI1Id', '9.00'), ('txtIntensity', '11.61')):
            native = getattr(self.dialog, name)
            overlay = self.editor(name)
            self.assertEqual(overlay.text(), '')
            native.blockSignals(True)
            native.setText(numeric)
            native.blockSignals(False)
            self.assertEqual(overlay.text(), '')
        self.presentation.sync()
        self.assertEqual(self.editor('txtI1Id').text(), '9,00')
        self.assertEqual(self.editor('txtIntensity').text(), '11,61')
        self.assertEqual(self.dialog.txtIntensity.text(), '11.61')

    def test_user_can_type_spanish_comma_without_breaking_native_float_parsing(self):
        editor = self.editor('txtArea')
        editor.setFocus()
        editor.selectAll()
        QTest.keyClicks(editor, '14,75')
        self.assertEqual(editor.text(), '14,75')
        self.assertEqual(self.dialog.txtArea.text(), '14.75')
        self.assertEqual(float(self.dialog.txtArea.text()), 14.75)
        self.assertEqual(self.native_writes[-1], '14.75')
        self.assertFalse(any(',' in text for text in self.native_writes))

    def test_decimal_view_does_not_touch_other_text(self):
        self.assertEqual(decimal_display('14.750'), '14,750')
        self.assertEqual(decimal_display('-.25'), '-,25')
        self.assertEqual(decimal_display('N/A 2.5'), 'N/A 2.5')
        self.assertEqual(canonical_numeric('-0,001'), '-0.001')

    def test_dispose_restores_native_fields(self):
        self.presentation.dispose()
        self.assertIsNone(self.editor('txtArea'))
        self.assertEqual(self.dialog.txtArea.text(), '14.750')
        self.dialog.txtArea.setText('24.5')
        self.assertEqual(self.dialog.txtArea.text(), '24.5')


if __name__ == '__main__':
    unittest.main()
