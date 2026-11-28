"""Native Qt/QGIS map selection checks with disposable GDAL raster fixtures."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
from osgeo import gdal

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QPoint, QSize, Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDialog, QWidget
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsPointXY, QgsProject, QgsRectangle)
from qgis.gui import QgsMapCanvas, QgsMapToolPan

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta.outlet_selection import OutletSelectionDialog


class OutletSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], True)
        cls.app.initQgis()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='outlet_selection_')
        self.folder = Path(self.temporary.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.transform = (500000, 5, 0, 4700000, 0, -5)
        self.parent = QWidget()
        self.parent.setAttribute(Qt.WA_DontShowOnScreen)
        self.canvas = QgsMapCanvas()
        self.canvas.setDestinationCrs(self.crs)
        self.canvas.setAttribute(Qt.WA_DontShowOnScreen)
        self.canvas.resize(400, 300)
        self.iface = SimpleNamespace(mapCanvas=lambda: self.canvas, mainWindow=lambda: self.parent)
        self.previous_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self.previous_tool)
        self.project_ids = set(QgsProject.instance().mapLayers())
        codes = np.full((9, 9), 4, dtype=np.uint8)
        codes[-1, :] = 0
        self.directions = self.raster('directions.tif', codes, gdal.GDT_Byte, 255)
        self.dem = self.raster('dem.tif', 200 + np.indices((9, 9))[0], gdal.GDT_Float32, -9999)
        self.original = self.point(4, 4)
        self.context = dict(dem_path=str(self.dem), directions_path=str(self.directions),
                            crs_wkt=self.crs.toWkt(), original_x=self.original[0],
                            original_y=self.original[1], search_radius_m=17.0,
                            resolution_key='5', reason='Revisa la sección de la cuenca.')
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.reject()
                dialog.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.canvas.stopRendering()
        self.canvas.setLayers([])
        self.canvas.close()
        sip.delete(self.canvas)
        self.parent.close()
        sip.delete(self.parent)
        self.assertEqual(set(QgsProject.instance().mapLayers()), self.project_ids)
        self.temporary.cleanup()

    def raster(self, name, values, dtype, nodata):
        path = self.folder / name
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 9, 9, 1, dtype)
        dataset.SetGeoTransform(self.transform)
        dataset.SetProjection(self.crs.toWkt())
        dataset.GetRasterBand(1).SetNoDataValue(nodata)
        dataset.GetRasterBand(1).WriteArray(np.asarray(values))
        dataset = None
        return path

    def point(self, row, col):
        return tuple(gdal.ApplyGeoTransform(self.transform, col + .5, row + .5))

    def make_dialog(self, persistent=False, **changes):
        context = dict(self.context, **changes)
        dialog = OutletSelectionDialog(self.iface, context, self.parent, persistent=persistent)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def click_cell(self, dialog, row, col, button=Qt.LeftButton, offset=(0, 0)):
        dialog.start_picking()
        point = self.point(row, col)
        native = QgsPointXY(point[0] + offset[0], point[1] + offset[1])
        transform = QgsCoordinateTransform(self.crs, self.canvas.mapSettings().destinationCrs(),
                                             QgsProject.instance())
        dialog._map_tool.canvasClicked.emit(transform.transform(native), button)

    def assert_preview(self, dialog, row, col):
        self.assertTrue(dialog.apply_button.isEnabled(), dialog.status_label.text())
        self.assertIsNone(dialog.selection())
        self.assertIsNotNone(dialog._marker)
        transform = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.crs,
                                             QgsProject.instance())
        point = transform.transform(dialog._marker.center())
        expected = self.point(row, col)
        self.assertAlmostEqual(point.x(), expected[0], places=5)
        self.assertAlmostEqual(point.y(), expected[1], places=5)

    def test_nonmodal_native_preview_and_all_arrows_use_cell_steps(self):
        dialog = self.make_dialog()
        self.assertEqual(dialog.windowTitle(),'HidroCAUMAX · Ajuste Punto de Cierre')
        self.assertFalse(dialog.isModal())
        self.assertEqual(dialog.windowModality(), Qt.NonModal)
        self.assert_preview(dialog, 4, 4)
        self.assertGreater(dialog._marker.color().blue(), dialog._marker.color().red())
        dialog.step.setValue(2)
        dialog.north_button.click()
        self.assert_preview(dialog, 2, 4)
        dialog.east_button.click()
        self.assert_preview(dialog, 2, 6)
        dialog.south_button.click()
        self.assert_preview(dialog, 4, 6)
        dialog.west_button.click()
        self.assert_preview(dialog, 4, 4)
        self.assertIn(f'{self.original[0]:.3f}'.replace('.', ','), dialog.coordinate_label.text())
        self.assertIn('0,00 m', dialog.distance_label.text())
        dialog.apply_button.click()
        self.assertEqual(dialog.selection(), self.original)

    def test_initial_outlet_does_not_redefine_original_distance_reference(self):
        initial = self.point(4, 6)
        dialog = self.make_dialog(initial_x=initial[0] + .4, initial_y=initial[1] + .2)
        self.assert_preview(dialog, 4, 6)
        self.assertIn('10,00 m', dialog.distance_label.text())
        self.assertEqual((dialog.original_x, dialog.original_y), self.original)
        dialog.accept()
        self.assertEqual(dialog.selection(), initial)

    def test_map_clicks_transform_to_native_crs_and_marker_tracks_canvas_crs(self):
        self.canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:4326'))
        dialog = self.make_dialog()
        self.click_cell(dialog, 5, 6, offset=(.7, -.4))
        self.assert_preview(dialog, 5, 6)
        self.canvas.setDestinationCrs(self.crs)
        self.assert_preview(dialog, 5, 6)
        self.click_cell(dialog, 3, 4, button=Qt.RightButton)
        self.assert_preview(dialog, 5, 6)
        dialog.accept()
        self.assertEqual(dialog.selection(), self.point(5, 6))

    def test_accept_restores_tool_releases_gdal_and_ignores_late_map_click(self):
        before = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        dialog = self.make_dialog()
        self.click_cell(dialog, 5, 4)
        tool = dialog._map_tool
        self.assertIs(self.canvas.mapTool(), tool)
        dialog.apply_button.click()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.selection(), self.point(5, 4))
        self.assertIs(self.canvas.mapTool(), self.previous_tool)
        self.assertIsNone(dialog._marker)
        self.assertIsNone(dialog._map_tool)
        self.assertIsNone(dialog._dem)
        self.assertIsNone(dialog._directions)
        if not sip.isdeleted(tool):
            tool.canvasClicked.emit(QgsPointXY(*self.point(2, 2)), Qt.LeftButton)
        self.assertEqual(dialog.selection(), self.point(5, 4))
        self.assertEqual({path.name: path.read_bytes() for path in self.folder.iterdir()}, before)

    def test_escape_and_close_cancel_without_selection_and_restore_original_tool(self):
        for close in (lambda dialog: QTest.keyClick(dialog, Qt.Key_Escape), lambda dialog: dialog.close()):
            with self.subTest(close=close):
                dialog = self.make_dialog()
                self.click_cell(dialog, 5, 4)
                tool = dialog._map_tool
                close(dialog)
                self.assertIsNone(dialog.selection())
                self.assertIsNone(dialog._marker)
                self.assertIsNone(dialog._dem)
                self.assertIs(self.canvas.mapTool(), self.previous_tool)
                if not sip.isdeleted(tool):
                    tool.canvasClicked.emit(QgsPointXY(*self.point(2, 2)), Qt.LeftButton)
                self.assertIsNone(dialog.selection())
                self.assertIsNone(dialog._marker)

    def test_external_tool_change_cancels_and_preserves_the_users_new_tool(self):
        dialog = self.make_dialog()
        dialog.start_picking()
        replacement = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(replacement)
        self.assertIs(self.canvas.mapTool(), replacement)
        self.assertFalse(dialog.isVisible())
        self.assertIsNone(dialog.selection())
        self.assertIsNone(dialog._marker)
        self.assertIsNone(dialog._directions)
        dialog.reject()
        self.assertIs(self.canvas.mapTool(), replacement)

    def test_radius_and_extent_failures_do_not_accept_or_clamp_an_invalid_point(self):
        dialog = self.make_dialog()
        self.click_cell(dialog, 4, 8)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn('radio', dialog.status_label.text())
        self.assertIsNone(dialog._marker)
        dialog.accept()
        self.assertIsNone(dialog.selection())
        dialog.step.setValue(1)
        dialog.west_button.click()
        self.assert_preview(dialog, 4, 7)
        old_indices = (dialog._row, dialog._col)
        dialog._map_tool.canvasClicked.emit(QgsPointXY(499999, 4700001), Qt.LeftButton)
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertEqual((dialog._row, dialog._col), old_indices)
        self.assertIn('fuera', dialog.status_label.text())
        dialog.west_button.click()
        self.assert_preview(dialog, 4, 6)

    def test_direction_codes_elevation_nodata_and_masks_disable_acceptance(self):
        for source, value, masked in ((self.directions, 3, False), (self.directions, 255, False),
                                      (self.dem, -9999, False), (self.dem, 100, True)):
            with self.subTest(source=source.name, value=value, masked=masked):
                dataset = gdal.Open(str(source), gdal.GA_Update)
                band = dataset.GetRasterBand(1)
                original_value = band.ReadAsArray(4, 3, 1, 1).copy()
                band.WriteArray(np.array([[value]]), 4, 3)
                if masked:
                    band.CreateMaskBand(gdal.GMF_PER_DATASET)
                    flags = np.full((9, 9), 255, dtype=np.uint8)
                    flags[3, 4] = 0
                    band.GetMaskBand().WriteArray(flags)
                dataset = None
                dialog = self.make_dialog()
                dialog.north_button.click()
                self.assertFalse(dialog.apply_button.isEnabled())
                self.assertIsNone(dialog._marker)
                self.assertRegex(dialog.status_label.text(), 'NoData|desconocido')
                dialog.accept()
                self.assertIsNone(dialog.selection())
                dialog.south_button.click()
                self.assert_preview(dialog, 4, 4)
                dialog.reject()
                dataset = gdal.Open(str(source), gdal.GA_Update)
                dataset.GetRasterBand(1).WriteArray(original_value, 4, 3)
                if masked:
                    dataset.GetRasterBand(1).GetMaskBand().WriteArray(np.full((9, 9), 255, dtype=np.uint8))
                dataset = None

    def test_invalid_initial_cell_has_no_implicit_valid_selection(self):
        initial = self.point(4, 8)
        dialog = self.make_dialog(initial_x=initial[0], initial_y=initial[1])
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIsNone(dialog._marker)
        self.assertIsNone(dialog.selection())
        dialog.west_button.click()
        self.assert_preview(dialog, 4, 7)

    def test_context_crs_must_match_the_real_native_grid(self):
        wrong = dict(self.context, crs_wkt=QgsCoordinateReferenceSystem('EPSG:4326').toWkt())
        with self.assertRaisesRegex(ValueError, 'CRS.*no coincide'):
            OutletSelectionDialog(self.iface, wrong, self.parent)

    def test_manual_extent_allows_valid_cell_beyond_automatic_radius(self):
        dialog = self.make_dialog(manual_selection_scope='raster_extent')
        self.click_cell(dialog,4,8)
        self.assert_preview(dialog,4,8)
        self.assertIn('20,00 m',dialog.distance_label.text())
        self.assertIn('extensión válida',dialog.distance_label.text())
        self.assertNotIn('límite',dialog.distance_label.text())
        dialog.east_button.click()
        self.assertFalse(dialog.apply_button.isEnabled())
        self.assertIn('extensión',dialog.status_label.text())

    def test_fine_reference_mismatch_requires_explicit_manual_override(self):
        dialog = self.make_dialog(persistent=True, verify_reference_overlap=True)
        self.assertTrue(dialog.allow_different_check.isVisible())
        self.assertFalse(dialog.allow_different_check.isChecked())
        self.assertNotIn('allow_reference_mismatch', dialog.context)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.accept()
        self.assertFalse(dialog.context['allow_reference_mismatch'])
        dialog.rebind(dict(self.context, verify_reference_overlap=True))
        self.assertFalse(dialog.allow_different_check.isChecked())
        dialog.allow_different_check.setChecked(True)
        dialog.accept()
        self.assertTrue(dialog.context['allow_reference_mismatch'])
        dialog.rebind(dict(self.context))
        self.assertFalse(dialog.allow_different_check.isVisible())
        self.assertFalse(dialog.allow_different_check.isChecked())

    def test_persistent_apply_rebinds_new_outlet_and_original_reference_for_next_apply(self):
        dialog = self.make_dialog(persistent=True,manual_selection_scope='raster_extent')
        applied = []
        dialog.applied.connect(lambda:applied.append(dialog.selection()))
        self.click_cell(dialog,4,8)
        old_tool = dialog._map_tool
        dialog.apply_button.click()
        self.assertEqual(applied,[self.point(4,8)])
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isModal())
        self.assertTrue(dialog._busy)
        self.assertIs(self.canvas.mapTool(),self.previous_tool)
        self.assertFalse(dialog.pick_button.isEnabled())
        self.assertFalse(dialog.north_button.isEnabled())
        if not sip.isdeleted(old_tool):
            old_tool.canvasClicked.emit(QgsPointXY(*self.point(1,1)),Qt.LeftButton)
        self.assertEqual(dialog.selection(),self.point(4,8))
        dialog.rebind(dict(dialog.context,initial_x=self.point(4,8)[0],initial_y=self.point(4,8)[1]))
        self.assert_preview(dialog,4,8)
        self.assertFalse(dialog._busy)
        self.assertEqual((dialog.original_x,dialog.original_y),self.original)
        dialog.west_button.click()
        dialog.apply_button.click()
        self.assertEqual(applied,[self.point(4,8),self.point(4,7)])
        self.assertTrue(dialog.isVisible())
        dialog.reject()
        self.assertIsNone(dialog._directions)

    def test_persistent_selector_survives_user_pan_and_can_pick_again(self):
        dialog = self.make_dialog(persistent=True)
        dialog.start_picking()
        old_tool=dialog._map_tool
        replacement=QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(replacement)
        self.assertTrue(dialog.isVisible())
        self.assertIs(self.canvas.mapTool(),replacement)
        self.assertIsNone(dialog._map_tool)
        if not sip.isdeleted(old_tool):
            old_tool.canvasClicked.emit(QgsPointXY(*self.point(1,1)),Qt.LeftButton)
        self.assert_preview(dialog,4,4)
        self.click_cell(dialog,3,4)
        self.assert_preview(dialog,3,4)
        dialog.reject()
        self.assertIs(self.canvas.mapTool(),replacement)

    def test_hover_magnifier_is_4x_passive_and_click_preserves_exact_native_point(self):
        """The visual loupe must never turn screen pixels into outlet coordinates."""
        self.canvas.setExtent(QgsRectangle(499995, 4699950, 500050, 4700005))
        self.canvas.show()
        self.app.processEvents()
        dialog = self.make_dialog(persistent=True, manual_selection_scope='raster_extent')
        initial_extent = self.canvas.extent().toString(8)
        dialog.start_picking()
        lens = dialog._magnifier
        self.assertIsNotNone(lens)
        self.assertEqual(lens.size(), QSize(120, 120))
        self.assertEqual(lens.ZOOM, 4)
        self.assertIs(lens.parentWidget(), self.canvas)
        self.assertTrue(lens.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertFalse(lens.isVisible())

        target = QgsPointXY(*self.point(2, 6))
        pixel = self.canvas.mapSettings().mapToPixel().transform(target)
        cursor = QPoint(round(pixel.x()), round(pixel.y()))
        QTest.mouseMove(self.canvas.viewport(), cursor)
        QTest.qWait(85)
        self.app.processEvents()
        self.assertEqual(lens._cursor_pos, cursor)
        self.assertTrue(lens.isVisible())
        self.assertFalse(lens._sample.isNull())
        self.assertEqual(self.canvas.extent().toString(8), initial_extent)

        QTest.mouseClick(self.canvas.viewport(), Qt.LeftButton, Qt.NoModifier, cursor)
        self.app.processEvents()
        self.assert_preview(dialog, 2, 6)
        self.assertFalse(lens.isVisible())
        self.assertFalse(lens._refresh_timer.isActive())
        self.assertFalse(dialog._magnifier_active)
        self.assertEqual(self.canvas.extent().toString(8), initial_extent)
        dialog.apply_button.click()
        self.assertEqual(dialog.selection(), self.point(2, 6))
        self.assertIsNone(dialog._magnifier)

    def test_hover_magnifier_hides_on_leave_rearms_and_cleans_up_on_tool_switch(self):
        self.canvas.setExtent(QgsRectangle(499995, 4699950, 500050, 4700005))
        self.canvas.show()
        self.app.processEvents()
        dialog = self.make_dialog(persistent=True)
        dialog.start_picking()
        lens = dialog._magnifier
        cursor = QPoint(170, 140)
        QTest.mouseMove(self.canvas.viewport(), cursor)
        QTest.qWait(85)
        self.assertTrue(lens.isVisible())

        QCoreApplication.sendEvent(self.canvas.viewport(), QEvent(QEvent.Leave))
        self.assertFalse(lens.isVisible())
        self.assertFalse(lens._refresh_timer.isActive())
        dialog.start_picking()  # Already active: re-enable the passive preview.
        QTest.mouseMove(self.canvas.viewport(), QPoint(175, 140))
        QTest.qWait(85)
        self.assertTrue(lens.isVisible())

        replacement = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(replacement)
        self.assertIs(self.canvas.mapTool(), replacement)
        self.assertIsNone(dialog._magnifier)
        self.assertFalse(lens.isVisible())
        self.assertFalse(dialog._picking)
        self.assertTrue(dialog.isVisible())
        dialog.reject()
        self.assertIs(self.canvas.mapTool(), replacement)

    def test_real_mouse_click_reaches_map_tool_while_selector_is_open(self):
        self.canvas.setExtent(QgsRectangle(499995,4699950,500050,4700005))
        self.canvas.show()
        self.app.processEvents()
        dialog=self.make_dialog(persistent=True,manual_selection_scope='raster_extent')
        dialog.start_picking()
        target=QgsPointXY(*self.point(2,6))
        pixel=self.canvas.mapSettings().mapToPixel().transform(target)
        QTest.mouseClick(self.canvas.viewport(),Qt.LeftButton,Qt.NoModifier,
                         QPoint(round(pixel.x()),round(pixel.y())))
        self.app.processEvents()
        self.assertTrue(dialog.isVisible())
        self.assert_preview(dialog,2,6)


if __name__ == '__main__':
    unittest.main()
