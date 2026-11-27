"""Real PyQGIS regressions for yellow closure markers and SHP output."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject
from caumax_consulta.closure_layers import (
    CLOSURE_ROLE, GREEN_CLOSURE_ROLE, analysis_closure_layer, export_closure,
    export_green_course_closure, make_closure_layer)


class ClosureLayersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.project = QgsProject.instance()
        self.original = set(self.project.mapLayers())
        self.variables = dict(self.project.customVariables())
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')

    def tearDown(self):
        self.project.removeMapLayers(list(set(self.project.mapLayers())-self.original))
        self.project.setCustomVariables(self.variables)

    def test_exact_picked_point_has_yellow_circle_and_accepted_d8_attributes(self):
        analysis = SimpleNamespace(
            crs_wkt=self.crs.toWkt(), selected_x=353387.1465,
            selected_y=4725249.11019, outlet_x=353387.5,
            outlet_y=4725237.5)
        layer = analysis_closure_layer(analysis)
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 1)
        self.assertTrue(layer.customProperty(CLOSURE_ROLE, False))
        feature = next(layer.getFeatures())
        self.assertAlmostEqual(feature.geometry().asPoint().x(), analysis.selected_x, 6)
        self.assertAlmostEqual(feature.geometry().asPoint().y(), analysis.selected_y, 6)
        self.assertAlmostEqual(feature['X_CIERRE'], analysis.outlet_x, 6)
        self.assertAlmostEqual(feature['Y_CIERRE'], analysis.outlet_y, 6)
        symbol = layer.renderer().symbol()
        self.assertEqual(symbol.color().name(), '#ffe800')
        self.assertAlmostEqual(symbol.size(), 5.)

    def test_guardar_cierre_writes_one_point_shapefile_and_loads_it_in_qgis(self):
        target = self.folder / 'Cierre_353387_4725249.shp'
        written, saved = export_closure(353387.1465, 4725249.11019,
                                       self.crs, target)
        self.assertEqual(written, target)
        self.assertTrue(all(target.with_suffix(s).is_file()
                            for s in ('.shp', '.shx', '.dbf', '.prj')))
        self.assertIs(self.project.mapLayer(saved.id()), saved)
        self.assertEqual(saved.featureCount(), 1)
        self.assertEqual(saved.renderer().symbol().color().name(), '#ffe800')
        point = next(saved.getFeatures()).geometry().asPoint()
        self.assertAlmostEqual(point.x(), 353387.1465, 5)
        self.assertAlmostEqual(point.y(), 4725249.11019, 5)
        self.assertTrue(saved.customProperty(CLOSURE_ROLE, False))
        with self.assertRaisesRegex(ValueError, 'existe'):
            export_closure(353387.1465, 4725249.11019, self.crs, target)

    def test_exportar_cierre_green_circle_uses_exact_blue_cross_and_saved_qml(self):
        analysis = SimpleNamespace(
            crs_wkt=self.crs.toWkt(), selected_x=353387.1465,
            selected_y=4725249.11019, outlet_x=353362.5,
            outlet_y=4725237.5)
        destination = self.folder / 'Cierre_CursoPrincipal.shp'
        target, layer = export_green_course_closure(analysis, destination)
        self.assertEqual(target, destination)
        self.assertEqual(layer.renderer().symbol().color().name(), '#23e733')
        self.assertEqual(layer.customProperty(GREEN_CLOSURE_ROLE), True)
        self.assertFalse(layer.customProperty(CLOSURE_ROLE, False))
        point = next(layer.getFeatures()).geometry().asPoint()
        # The blue cross is at the accepted outlet, not the original
        # mouse click stored in analysis.selected_x/selected_y.
        self.assertAlmostEqual(point.x(), analysis.outlet_x, places=6)
        self.assertAlmostEqual(point.y(), analysis.outlet_y, places=6)
        self.assertNotEqual(point.x(), analysis.selected_x)
        self.assertTrue(destination.with_suffix('.qml').is_file())
        self.assertTrue(all(destination.with_suffix(s).is_file()
                            for s in ('.shp', '.shx', '.dbf', '.prj')))
        from qgis.core import QgsVectorLayer
        reloaded = QgsVectorLayer(str(target), 'Cierre reabierto', 'ogr')
        message, success = reloaded.loadNamedStyle(str(target.with_suffix('.qml')))
        self.assertTrue(success, message)
        self.assertEqual(reloaded.renderer().symbol().color().name(), '#23e733')
        self.assertAlmostEqual(reloaded.renderer().symbol().size(), 5.)
        with self.assertRaisesRegex(ValueError, 'existe'):
            export_green_course_closure(analysis, destination)

    def test_rejects_nonprojected_or_nonnumeric_coordinates(self):
        with self.assertRaisesRegex(ValueError, 'CRS proyectado'):
            make_closure_layer(0, 40, QgsCoordinateReferenceSystem('EPSG:4326'))
        with self.assertRaisesRegex(ValueError, 'finitas'):
            make_closure_layer(float('nan'), 40, self.crs)


if __name__ == '__main__':
    unittest.main()
