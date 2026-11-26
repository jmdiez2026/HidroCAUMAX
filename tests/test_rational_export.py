"""Rational-method export, ownership, stale-input and real-dialog checks."""

import ast
import importlib
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QMainWindow, QPushButton
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY, QgsProject
from qgis.gui import QgsMapCanvas, QgsRubberBand
import qgis.utils

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from caumax_consulta import rational_export as exporter


class RationalMethodDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.horizontalLayout_2 = QHBoxLayout(self)
        self.txtX, self.txtY = QLineEdit(self), QLineEdit(self)
        self.basinCalc = None
        self.rubberBand = None


RationalMethodDialog.__module__ = 'test_native.rationalmethoddialog'


class RationalExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()
        cls.window = QMainWindow()
        cls.canvas = QgsMapCanvas(cls.window)
        cls.iface = SimpleNamespace(mainWindow=lambda: cls.window, mapCanvas=lambda: cls.canvas)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.dialog = RationalMethodDialog(self.window)
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialog.show()
        self.bridge = exporter.RationalExportBridge(self.iface)
        self.saved = []
        self.project_ids = set(QgsProject.instance().mapLayers())
        self.legacy = patch.object(qgis.utils, '_caumax_export_controller', None, create=True)
        self.legacy.start()

    def tearDown(self):
        self.bridge.stop()
        for layer in self.saved:
            if not sip.isdeleted(layer):
                layer.dataProvider().forceReload()
        for layer_id in set(QgsProject.instance().mapLayers()) - self.project_ids:
            QgsProject.instance().removeMapLayer(layer_id)
        for layer in self.saved:
            if not sip.isdeleted(layer):
                sip.delete(layer)
        self.dialog.close()
        sip.delete(self.dialog)
        self.legacy.stop()
        self.temp.cleanup()

    def calculate(self, dialog=None, epsg='EPSG:25830'):
        dialog = dialog or self.dialog
        a = QgsGeometry.fromWkt('POLYGON ((100 200,110 200,110 210,100 210,100 200))')
        b = QgsGeometry.fromWkt('POLYGON ((120 200,140 200,140 210,120 210,120 200))')
        hole = QgsGeometry.fromWkt('POLYGON ((0 0,1000 0,1000 1000,0 1000,0 0),'
                                   '(100 100,100 200,200 200,200 100,100 100))')
        dialog.basinCalc = SimpleNamespace(basinGeometry=[a, b, hole, QgsGeometry()],
                                           pt=QgsPointXY(105.25, 205.5), area=450.0,
                                           crs=QgsCoordinateReferenceSystem(epsg).toWkt())
        dialog.rubberBand = object()
        return a, b

    def export(self, target):
        with patch.object(exporter.QFileDialog, 'getSaveFileName', return_value=(str(target), 'GeoPackage (*.gpkg)')), \
                patch.object(exporter.QMessageBox, 'information'), \
                patch.object(exporter.QMessageBox, 'critical') as error:
            saved = self.bridge.export_dialog(self.dialog)
            self.assertFalse(error.called)
        self.assertIsNotNone(saved)
        self.saved.append(saved)
        return saved

    def assert_result(self, layer, expected, epsg='EPSG:25830'):
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.crs(), QgsCoordinateReferenceSystem(epsg))
        features = sorted(layer.getFeatures(), key=lambda feature: feature['PARTE'])
        self.assertEqual(len(features), 2)
        for number, (feature, geometry) in enumerate(zip(features, expected), 1):
            self.assertTrue(feature.geometry().isGeosEqual(geometry))
            self.assertEqual(feature['PARTE'], number)
            self.assertAlmostEqual(feature['X_SALIDA'], 105.25)
            self.assertAlmostEqual(feature['Y_SALIDA'], 205.5)
            self.assertAlmostEqual(feature['AREA_TOT'], 0.00045, places=10)
            self.assertAlmostEqual(feature['AREA_POL'], geometry.area() / 1e6, places=10)

    def test_button_lifecycle_is_idempotent_and_disabled_before_calculation(self):
        self.bridge.start()
        self.bridge.start()
        buttons = self.dialog.findChildren(QPushButton, exporter.BUTTON_NAME)
        self.assertEqual(len(buttons), 1)
        self.assertFalse(buttons[0].isEnabled())
        self.assertFalse(buttons[0].icon().isNull())
        self.assertIn('#D9F0FF', buttons[0].styleSheet())
        self.assertEqual(self.bridge.timer.interval(), 400)
        self.calculate()
        self.bridge.scan()
        self.assertTrue(buttons[0].isEnabled())
        self.bridge.stop()
        self.bridge.stop()
        self.assertFalse(self.bridge.timer.isActive())
        self.assertIsNone(self.dialog.findChild(QPushButton, exporter.BUTTON_NAME))

    def test_exact_original_filter_and_geometries_round_trip_shapefile_and_gpkg(self):
        expected = self.calculate()
        for extension in ('.shp', '.gpkg'):
            with self.subTest(extension=extension):
                self.assert_result(self.export(self.folder / ('cuenca' + extension)), expected)

    def test_later_text_edits_never_replace_calculator_outlet(self):
        expected = self.calculate()
        self.dialog.txtX.setText('999999')
        self.dialog.txtY.setText('111111')
        self.assert_result(self.export(self.folder / 'stale.gpkg'), expected)

    def test_crs_is_actual_calculator_crs_not_hardcoded_25830(self):
        expected = self.calculate(epsg='EPSG:32631')
        self.assert_result(self.export(self.folder / 'other_crs.gpkg'), expected, 'EPSG:32631')

    def test_snapshot_does_not_mix_a_later_calculation_during_file_dialog(self):
        expected = self.calculate()

        def filename(*args, **kwargs):
            self.dialog.basinCalc.pt = QgsPointXY(9999, 8888)
            self.dialog.basinCalc.area = 999999
            self.dialog.basinCalc.basinGeometry = []
            return str(self.folder / 'snapshot.gpkg'), 'GeoPackage (*.gpkg)'

        with patch.object(exporter.QFileDialog, 'getSaveFileName', side_effect=filename), \
                patch.object(exporter.QMessageBox, 'information'):
            saved = self.bridge.export_dialog(self.dialog)
        self.saved.append(saved)
        self.assert_result(saved, expected)

    def test_existing_target_and_sidecar_are_not_overwritten(self):
        self.calculate()
        for suffix in ('.shp', '.gpkg', '.prj'):
            original = self.folder / ('existing' + suffix)
            original.write_bytes(b'keep original')
            target = original if suffix != '.prj' else self.folder / 'existing.shp'
            with patch.object(exporter.QFileDialog, 'getSaveFileName', return_value=(str(target), 'SHP')), \
                    patch.object(exporter.QMessageBox, 'critical') as error:
                self.assertIsNone(self.bridge.export_dialog(self.dialog))
                self.assertEqual(error.call_count, 1)
            self.assertEqual(original.read_bytes(), b'keep original')

    def test_foreign_button_is_not_adopted_or_removed(self):
        foreign = QPushButton('Botón de otro controlador', self.dialog)
        foreign.setObjectName(exporter.BUTTON_NAME)
        foreign.setEnabled(False)
        listener = Mock()
        foreign.clicked.connect(listener)
        self.bridge.start()
        self.assertFalse(self.bridge._bindings)
        self.bridge.stop()
        self.assertIs(self.dialog.findChild(QPushButton, exporter.BUTTON_NAME), foreign)
        self.assertFalse(foreign.isEnabled())
        foreign.setEnabled(True)
        foreign.click()
        self.assertEqual(listener.call_count, 1)

    def test_running_standalone_controller_is_respected(self):
        timer = QTimer(self.window)
        timer.start(400)
        previous = SimpleNamespace(timer=timer, start=Mock(), stop=Mock())
        with patch.object(qgis.utils, '_caumax_export_controller', previous):
            self.bridge.start()
            self.assertIsNone(self.dialog.findChild(QPushButton, exporter.BUTTON_NAME))
            self.bridge.stop()
            previous.start.assert_not_called()
            previous.stop.assert_not_called()
            self.assertTrue(timer.isActive())
        timer.stop()

    def test_no_geometry_or_missing_crs_is_an_explicit_error(self):
        with self.assertRaises(ValueError):
            exporter.make_rational_layer(self.dialog)
        self.calculate()
        self.dialog.basinCalc.crs = ''
        with self.assertRaisesRegex(ValueError, 'referencia'):
            exporter.make_rational_layer(self.dialog)

    def test_cancel_save_does_not_add_a_layer(self):
        self.calculate()
        with patch.object(exporter.QFileDialog, 'getSaveFileName', return_value=('', '')):
            self.assertIsNone(self.bridge.export_dialog(self.dialog))
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.project_ids)

    def test_original_script_footer_is_not_executed_by_import(self):
        self.assertIsNone(getattr(qgis.utils, '_caumax_export_controller', None))
        asset = ROOT / 'src' / 'caumax_consulta' / 'extras' / 'CAUMAX_Exportador_Divisoria_Original.py'
        if asset.is_file():
            tree = ast.parse(asset.read_text(encoding='utf-8-sig'))
            self.assertTrue(any(isinstance(node, ast.ClassDef) and node.name == 'CaumaxExportController'
                                for node in tree.body))

    def test_installed_native_rational_dialog_is_detected_without_activating_plugin(self):
        plugins = Path(os.environ.get('APPDATA', '')) / 'QGIS' / 'QGIS3' / 'profiles' / 'default' / 'python' / 'plugins'
        if not (plugins / 'caumax' / 'rationalmethoddialog.py').is_file():
            self.skipTest('CAUMAX no instalado')
        if os.environ.get('HIDROCAUMAX_RATIONAL_NATIVE_CHILD') != '1':
            # The bridge suite already loads the installed CAUMAX UI under an
            # isolated package name and removes that package afterwards. PyQt's
            # UI compiler retains proxy state across these otherwise isolated
            # imports (UICompiler.item_nr). A fresh process tests the real native
            # class without patching that vendor state or the installed plugin.
            env = os.environ.copy()
            env.update(HIDROCAUMAX_RATIONAL_NATIVE_CHILD='1', QT_QPA_PLATFORM='offscreen')
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 'RationalExportTests.test_installed_native_rational_dialog_is_detected_without_activating_plugin', '-v'],
                capture_output=True, text=True, errors='replace', timeout=45,
                env=env, cwd=str(ROOT), **kwargs)
            self.assertEqual(result.returncode, 0,
                             'Smoke nativo aislado falló:\n' + (result.stdout + result.stderr)[-14000:])
            return
        sys.path.insert(0, str(plugins))
        with patch.object(qgis.utils, 'iface', self.iface):
            module = importlib.import_module('caumax.rationalmethoddialog')
            with patch.object(module, 'iface', self.iface), \
                    patch.object(module.RationalMethodDialog, 'populateRivers'):
                native = module.RationalMethodDialog()
                native.setAttribute(Qt.WA_DontShowOnScreen)
                native.show()
                try:
                    self.bridge.start()
                    button = native.findChild(QPushButton, exporter.BUTTON_NAME)
                    self.assertIsNotNone(button)
                    self.assertFalse(button.isEnabled())
                    expected = self.calculate(native)
                    native.rubberBand = QgsRubberBand(self.canvas)
                    for geometry in expected:
                        native.rubberBand.addGeometry(geometry)
                    self.bridge.scan()
                    self.assertTrue(button.isEnabled())
                    layer = exporter.make_rational_layer(native)
                    try:
                        self.assert_result(layer, expected)
                    finally:
                        sip.delete(layer)
                finally:
                    self.bridge.stop()
                    native.close()
                    sip.delete(native)


if __name__ == '__main__':
    unittest.main()
