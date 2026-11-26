import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from qgis.core import QgsApplication
from caumax_consulta.manual_dialog import ManualDialog, MANUAL_DIR, PDF_NAME


class ManualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QgsApplication.instance() or QgsApplication([],False)

    def test_offline_pages_match_pdf_and_open_correct_local_document(self):
        document=json.loads((MANUAL_DIR/'pages.json').read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256((MANUAL_DIR/PDF_NAME).read_bytes()).hexdigest(),document['pdf_sha256'])
        dialog=ManualDialog(); dialog.show(); self.app.processEvents()
        self.addCleanup(dialog.close)
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.pages.isEnabled())
        self.assertEqual(dialog.pages.count(),10)
        for index in range(10):
            dialog.pages.setCurrentIndex(index)
            self.assertFalse(dialog.page.pixmap().isNull())
        with patch('caumax_consulta.manual_dialog.QDesktopServices.openUrl',return_value=True) as opened:
            dialog.open_pdf.click()
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()),MANUAL_DIR/PDF_NAME)
