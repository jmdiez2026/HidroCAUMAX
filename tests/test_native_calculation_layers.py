"""The native layer chooser and its Defaults button remain under CAUMAX control."""
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from qgis.PyQt import sip
from qgis.PyQt.QtWidgets import QComboBox, QDialog, QLineEdit, QPushButton, QMessageBox, QMainWindow
from qgis.core import QgsApplication

from caumax_consulta import native_plugin as module
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
            patch('caumax_consulta.improved_storage.prepare_export_directory'),
            patch('caumax_consulta.improved_storage.preserve_exports')]
        self.patches += [patch.object(module, name) for name in (
            'CaumaxBridge','RationalExportBridge','RationalInputsBridge','RationalLayersBridge',
            'VisibleDemController','UpdateNotifier')]
        for p in self.patches: p.start()
        self.dialog = LayersDialog(self.original)
        self.window = QMainWindow()
        self.plugin = module.NativeCaumaxPlugin(SimpleNamespace(mainWindow=lambda:self.window,
            addPluginToMenu=Mock(), removePluginMenu=Mock(), messageBar=lambda:Mock()))
        self.dialog.show(); self.app.processEvents(); self.plugin.initGui()

    def tearDown(self):
        self.plugin.unload(); sip.delete(self.dialog); sip.delete(self.window)
        for p in reversed(self.patches): p.stop()

    def paths(self): return {k: v[1].currentText() for k, v in self.dialog.layerbuttons.items()}

    def test_opening_with_plugin_preserves_all_user_settings(self):
        self.assertEqual(self.paths(), {key:'custom/'+key for key in self.original})
        self.assertEqual(self.dialog.txtBasinSize.text(), '10')
        self.assertEqual(self.native._defaultLayerPaths, self.original)
        self.assertFalse(hasattr(self.plugin, '_calculation_layers'))

    def test_reactivation_preserves_edits_and_default_button_restores_native_values(self):
        self.dialog.layerbuttons['MDT'][1].setCurrentText('manual.tif')
        self.dialog.txtBasinSize.setText('3')
        self.plugin.set_enabled(False); self.plugin.set_enabled(True)
        self.app.processEvents()
        self.assertEqual(self.paths()['MDT'], 'manual.tif')
        self.assertEqual(self.dialog.txtBasinSize.text(), '3')
        self.dialog.btnDefault.click()
        self.assertEqual(self.paths(), self.original)
        self.assertEqual(self.dialog.txtBasinSize.text(), '10')

    def test_default_button_never_reads_export_pairs_or_substitutes_po(self):
        with patch('caumax_consulta.improved_storage.latest_pair') as pair, \
             patch('caumax_consulta.improved_storage.po_path') as po:
            self.dialog.btnDefault.click()
        pair.assert_not_called(); po.assert_not_called()
        self.assertEqual(self.paths(), self.original)
        self.assertEqual(self.dialog.txtBasinSize.text(), '10')
        self.dialog.bar.pushMessage.assert_not_called()

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
