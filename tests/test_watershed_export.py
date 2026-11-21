"""QGIS round-trip export checks; no user project or installed data changes."""

from dataclasses import replace
import importlib
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsProject
from qgis.PyQt import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from caumax_consulta.basin import BasinResult
from caumax_consulta.metrics import shape_metrics
from caumax_consulta.watershed_export import make_layer, save_result, write_layer


class WatershedExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.saved_layers = []
        # One polygon with a hole and a separate polygon: area 2100 m².
        geometry = QgsGeometry.fromWkt(
            "MULTIPOLYGON (((100 200, 140 200, 140 240, 100 240, 100 200),"
            "(110 210, 110 220, 120 220, 120 210, 110 210)),"
            "((160 200, 190 200, 190 220, 160 220, 160 200)))"
        )
        self.result = BasinResult(
            geometry_wkb=bytes(geometry.asWkb()),
            crs_wkt=QgsCoordinateReferenceSystem("EPSG:25830").toWkt(),
            area_m2=2100.0, perimeter_m=300.0,
            z_min=None, z_max=None, z_mean=None, cell_count=21,
            cell_width=10.0, cell_height=10.0,
            outlet_x=105.0, outlet_y=205.0, touches_boundary=False,
        )
        self.query = SimpleNamespace(flow=123.123456789123, period=5,
                                     cell=SimpleNamespace(row=427, col=756))
        self.initial_layer_ids = set(QgsProject.instance().mapLayers())

    def tearDown(self):
        for layer in self.saved_layers:
            if not sip.isdeleted(layer):
                # Invalidate this fixture's pooled OGR connection before closing
                # the layer so Windows can remove the temporary dataset.
                layer.dataProvider().forceReload()
        for layer_id in set(QgsProject.instance().mapLayers()) - self.initial_layer_ids:
            QgsProject.instance().removeMapLayer(layer_id)
        for layer in self.saved_layers:
            if not sip.isdeleted(layer):
                sip.delete(layer)
        self.temporary.cleanup()

    def assert_values(self, layer, places=8):
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 1)
        self.assertEqual(layer.crs(), QgsCoordinateReferenceSystem("EPSG:25830"))
        feature = next(layer.getFeatures())
        geometry = feature.geometry()
        self.assertTrue(geometry.isGeosValid())
        self.assertAlmostEqual(geometry.area(), 2100)
        parts = geometry.asMultiPolygon()
        self.assertEqual(len(parts), 2)
        self.assertEqual(sorted(map(len, parts)), [1, 2])
        self.assertAlmostEqual(feature["Q_M3S"], self.query.flow, places=12)
        self.assertEqual(feature["T_ANOS"], 5)
        self.assertEqual((feature["X_CIERRE"], feature["Y_CIERRE"]), (105, 205))
        self.assertAlmostEqual(feature["AREA_KM2"], 0.0021, places=places)
        self.assertAlmostEqual(feature["PERIM_KM"], 0.3, places=places)
        expected = shape_metrics(2100, 300)
        self.assertAlmostEqual(feature["KC_GRAV"], expected.compactness, places=places)
        self.assertAlmostEqual(feature["IC_CIRC"], expected.circularity, places=places)
        self.assertEqual((feature["FILA_Q"], feature["COL_Q"], feature["N_CELDAS"]),
                         (428, 757, 21))
        self.assertEqual((feature["RES_X_M"], feature["RES_Y_M"]), (10, 10))
        self.assertTrue(all(len(field.name()) <= 10 for field in layer.fields()))
        for name in ("KC_GRAV", "IC_CIRC"):
            if layer.providerType() == "memory" or layer.source().lower().endswith(".shp"):
                self.assertGreaterEqual(layer.fields().field(name).precision(), 8)

    def test_memory_layer_preserves_all_result_values_and_geometry(self):
        self.assert_values(make_layer(self.result, self.query), places=12)
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.initial_layer_ids)

    def test_shapefile_round_trip_preserves_hole_multipart_crs_and_metrics(self):
        saved = write_layer(make_layer(self.result, self.query), self.folder / "cuenca.shp")
        self.saved_layers.append(saved)
        self.assert_values(saved)
        self.assertTrue((self.folder / "cuenca.prj").is_file())
        self.assertTrue((self.folder / "cuenca.cpg").is_file())
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.initial_layer_ids)
        saved = None

    def test_geopackage_round_trip_preserves_hole_multipart_crs_and_metrics(self):
        saved = write_layer(make_layer(self.result, self.query), self.folder / "cuenca.gpkg")
        self.saved_layers.append(saved)
        self.assert_values(saved)
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.initial_layer_ids)
        saved = None

    def test_existing_destination_is_never_overwritten(self):
        for suffix in (".shp", ".gpkg"):
            target = self.folder / ("existing" + suffix)
            target.write_bytes(b"keep original bytes")
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "existe"):
                write_layer(make_layer(self.result, self.query), target)
            self.assertEqual(target.read_bytes(), b"keep original bytes")

    def test_existing_shapefile_sidecars_are_never_overwritten(self):
        layer = make_layer(self.result, self.query)
        for number, suffix in enumerate((".dbf", ".shx", ".prj", ".CPG", ".qpj", ".shp.xml", ".qix")):
            stem = "sidecar_%s" % number
            sidecar = self.folder / (stem + suffix)
            sidecar.write_bytes(b"keep sidecar bytes")
            target = self.folder / (stem + ".shp")
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "auxiliar"):
                write_layer(layer, target)
            self.assertEqual(sidecar.read_bytes(), b"keep sidecar bytes")
            self.assertFalse(target.exists())

    def test_reported_area_inconsistent_with_geometry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "superficie"):
            make_layer(replace(self.result, area_m2=2200), self.query)

    def test_invalid_extension_is_rejected_before_writing(self):
        target = self.folder / "cuenca.geojson"
        with self.assertRaisesRegex(ValueError, "extensión"):
            write_layer(make_layer(self.result, self.query), target)
        self.assertFalse(target.exists())

    def test_save_dialog_cancellation_changes_nothing(self):
        with patch("caumax_consulta.watershed_export.QFileDialog.getSaveFileName",
                   return_value=("", "")):
            self.assertIsNone(save_result(None, self.result, self.query))
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.initial_layer_ids)

    def test_save_result_adds_only_successful_saved_layer(self):
        target = self.folder / "saved_from_dialog"
        with patch("caumax_consulta.watershed_export.QFileDialog.getSaveFileName",
                   return_value=(str(target), "GeoPackage (*.gpkg)")):
            saved = save_result(None, self.result, self.query)
        self.assertIsNotNone(saved)
        self.saved_layers.append(saved)
        self.assertTrue(target.with_suffix(".gpkg").is_file())
        self.assertEqual(set(QgsProject.instance().mapLayers()) - self.initial_layer_ids,
                         {saved.id()})
        self.assert_values(saved)
        saved = None

    def test_save_failure_reports_error_and_does_not_add_layer(self):
        target = self.folder / "existing.shp"
        target.write_bytes(b"original")
        with patch("caumax_consulta.watershed_export.QFileDialog.getSaveFileName",
                   return_value=(str(target), "Shapefile (*.shp)")), \
                patch("caumax_consulta.watershed_export.QMessageBox.critical") as error:
            self.assertIsNone(save_result(None, self.result, self.query))
            error.assert_called_once()
        self.assertEqual(target.read_bytes(), b"original")
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.initial_layer_ids)


if __name__ == "__main__":
    unittest.main()
