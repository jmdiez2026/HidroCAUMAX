"""Real Qt chooser lifecycle; only the four general rasters are proposed."""
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QMessageBox
from qgis.core import QgsApplication

from caumax_consulta import native_calculation_layers as module
from caumax_consulta.recommended_p0 import RecommendedP0Presentation, TITLE


class LayersDialog(QDialog):
    def __init__(self, defaults):
        super().__init__()
        self.layerbuttons = {}
        self.defaults = defaults
        for key in defaults:
            combo = QComboBox(self); combo.setEditable(True); combo.setCurrentText('custom/'+key)
            self.layerbuttons[key] = (QPushButton(self), combo)
        self.txtBasinSize = QLineEdit('10', self)
        self.btnDefault = QPushButton(self)
        self.btnDefault.clicked.connect(self.native_defaults)
        self.bar = Mock()

    def native_defaults(self):
        for key, path in self.defaults.items(): self.layerbuttons[key][1].setCurrentText(path)
        self.txtBasinSize.setText('10')

    def _setCurrentLayerFromPath(self, combo, path): combo.setCurrentText(path)


LayersDialog.__module__ = '_config_test.layers'


class NativeCalculationLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.native = ModuleType(LayersDialog.__module__)
        for key in ('MDT', 'FLOWDIRS', 'I1ID', 'P0'): setattr(self.native, key, key)
        self.original = {key: 'native/'+key for key in ('MDT', 'FLOWDIRS', 'I1ID', 'P0', 'rain2', 'rain500')}
        self.native._defaultLayerPaths = self.original.copy()
        self.patches = [patch.dict(sys.modules, {self.native.__name__: self.native}),
            patch.object(module, 'latest_pair', return_value=('exports/MDT.tif', 'exports/D8.tif')),
            patch.object(module, 'po_path', return_value='exports/P0.tif')]
        for p in self.patches: p.start()
        self.dialog = LayersDialog(self.original)
        self.bridge = module.NativeCalculationLayers(SimpleNamespace())
        self.dialog.show(); self.app.processEvents(); self.bridge.start()

    def tearDown(self):
        self.bridge.stop(); sip.delete(self.dialog)
        for p in reversed(self.patches): p.stop()

    def paths(self): return {k: v[1].currentText() for k, v in self.dialog.layerbuttons.items()}

    def test_general_defaults_preserve_rain_and_native_definitions(self):
        values = self.paths()
        self.assertEqual([values[k] for k in ('MDT','FLOWDIRS','P0','I1ID')],
                         ['exports/MDT.tif','exports/D8.tif','exports/P0.tif','native/I1ID'])
        self.assertEqual(values['rain2'], 'custom/rain2')
        self.assertEqual(self.dialog.txtBasinSize.text(), '2')
        self.assertEqual(self.native._defaultLayerPaths, self.original)

    def test_scan_does_not_overwrite_user_edits_and_default_button_reapplies(self):
        self.dialog.layerbuttons['MDT'][1].setCurrentText('manual.tif')
        self.dialog.txtBasinSize.setText('3')
        self.bridge.scan()
        self.assertEqual(self.paths()['MDT'], 'manual.tif')
        self.assertEqual(self.dialog.txtBasinSize.text(), '3')
        self.dialog.btnDefault.click()
        self.assertEqual(self.paths()['MDT'], 'exports/MDT.tif')
        self.assertEqual(self.paths()['rain2'], 'native/rain2')
        self.assertEqual(self.dialog.txtBasinSize.text(), '2')

    def test_no_pair_uses_originals_and_unload_restores_native_default_button(self):
        with patch.object(module, 'latest_pair', return_value=None): self.dialog.btnDefault.click()
        self.assertEqual(self.paths()['MDT'], 'native/MDT')
        self.assertEqual(self.paths()['FLOWDIRS'], 'native/FLOWDIRS')
        self.assertTrue(self.dialog.bar.pushMessage.called)
        self.bridge.stop(); self.dialog.btnDefault.click()
        self.assertEqual(self.paths(), self.original)
        self.assertEqual(self.dialog.txtBasinSize.text(), '10')

    def test_recommendation_popup_only_localizes_its_decimal_numbers_and_is_reversible(self):
        presentation = RecommendedP0Presentation()
        presentation.start()
        box = QMessageBox(); box.setWindowTitle(TITLE)
        original = 'Valor medio: 0.70; 50%: ± 0.20; 67%: ± 0.35; 90%: ± 0.55'
        try:
            box.setText(original); box.show(); self.app.processEvents()
            self.assertEqual(box.text(), original.replace('.', ','))
            box.hide(); box.setWindowTitle('Unrelated'); box.setText(original)
            box.show(); self.app.processEvents(); self.assertEqual(box.text(), original)
            presentation.stop(); box.hide(); box.setWindowTitle(TITLE); box.setText(original)
            box.show(); self.app.processEvents(); self.assertEqual(box.text(), original)
        finally:
            presentation.stop(); sip.delete(box)


if __name__ == '__main__': unittest.main()
