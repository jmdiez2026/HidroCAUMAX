"""Real Qt/QGIS checks for the requested dialog layout and result placement."""
from types import SimpleNamespace
from html import unescape
import unittest
from unittest.mock import patch

from qgis.PyQt.QtCore import QPoint, QRect, Qt
from qgis.PyQt.QtGui import QFontMetrics
from qgis.PyQt.QtWidgets import QLabel, QMainWindow, QPushButton
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer

import test_drainage_dialog as fixture
from caumax_consulta.layer_order import add_result_layer, create_result_group, ensure_results_above_official
from caumax_consulta.morphology_table import three_line_text


class DialogLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog
        self.dialog.show()
        self.fixture.app.processEvents()

    def test_notes_are_below_morphology_and_compact_export_is_immediately_after_degree(self):
        dialog = self.dialog
        body = dialog.body_scroll.widget().layout()
        self.assertEqual(body.indexOf(dialog.status), body.indexOf(dialog.morphology_group) + 1)
        self.assertLess(body.indexOf(dialog.status), body.indexOf(dialog.hypsometry_widget))
        self.assertTrue(dialog.status.isReadOnly())
        button = dialog.export_button
        row = button.parentWidget().layout().itemAt(0).layout()
        self.assertEqual(row.indexOf(button),row.indexOf(dialog.smoothing_degree)+1)
        for width in (520,650,900):
            with self.subTest(width=width):
                dialog.resize(width,1000)
                QTest.qWait(100)
                dialog._fit_rows()
                self.fixture.app.processEvents()
                spin = dialog.smoothing_degree
                self.assertLessEqual(abs(button.geometry().center().y()-spin.geometry().center().y()),1)
                self.assertEqual(button.x()-spin.geometry().right()-1,row.spacing())
                self.assertGreaterEqual(button.width(),button.minimumSizeHint().width())
                self.assertLessEqual(button.mapTo(dialog.body_scroll.viewport(),QPoint()).x()+button.width(),
                                     dialog.body_scroll.viewport().width())
                self.assertLess(button.height(),30)
                self.assertLess(button.width(),160)

    def test_report_buttons_use_ninety_percent_natural_height_and_compact_centered_widths(self):
        dialog=self.dialog
        buttons=(dialog.report_button,dialog.save_project_button,dialog.open_project_button)
        for button in buttons:
            baseline=QPushButton(button.text(),button.parentWidget())
            baseline.ensurePolished()
            self.assertEqual(button.height(),round(baseline.sizeHint().height()*.90))
            self.assertGreaterEqual(button.height(),button.fontMetrics().height()+4)
            baseline.deleteLater()
        for width in (520,650,900):
            dialog.resize(width,900)
            self.fixture.app.processEvents()
            group=buttons[0].parentWidget()
            self.assertEqual(len({button.y() for button in buttons}),1)
            self.assertLessEqual(abs((buttons[0].x()+buttons[-1].geometry().right())/2-group.width()/2),1)
            for button in buttons:
                self.assertLessEqual(button.width(),button.sizeHint().width()+1)
                self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text())+20)
                self.assertLessEqual(button.geometry().right(),group.width())

    def test_resolution_and_complete_smoothing_section_are_first(self):
        dialog = self.dialog
        body = dialog.body_scroll.widget().layout()
        smoothing = dialog.export_button.parentWidget()
        self.assertIs(body.itemAt(0).widget(), dialog.resolution_group)
        self.assertIs(body.itemAt(1).widget(), smoothing)
        self.assertEqual(smoothing.title(), 'Suavizado divisoria')
        self.assertLess(dialog.resolution_group.geometry().bottom(), smoothing.geometry().top())
        for control in (dialog.original_check, dialog.smooth_check, dialog.smoothing_degree,
                        dialog.smoothing_status, dialog.export_button):
            self.assertTrue(smoothing.isAncestorOf(control))
            self.assertFalse(control.isHidden())
            self.assertLessEqual(control.geometry().bottom(), smoothing.height())
        summary = dialog.smoothing_status
        self.assertIsInstance(summary,QLabel)
        self.assertEqual(summary.textFormat(),Qt.PlainText)
        self.assertTrue(summary.textInteractionFlags() & Qt.TextSelectableByMouse)
        self.assertTrue(summary.wordWrap())
        heights = {}
        for smoothed in (False,True):
            dialog.smooth_check.blockSignals(True)
            dialog.smooth_check.setChecked(smoothed)
            dialog.smooth_check.blockSignals(False)
            dialog.smoothing_degree.blockSignals(True)
            dialog.smoothing_degree.setValue(2)
            dialog.smoothing_degree.blockSignals(False)
            dialog._update_smoothing()
            stats = dialog._geometry_stats()
            dialog._show_smoothing_summary()
            self.assertEqual(stats,dialog._geometry_stats())
            for title,digits in (('área',3),('perímetro',3),('Kc',4),('Ic',4)):
                self.assertRegex(summary.text(),rf'{title} [-+]?\d+,\d{{{digits}}}')
            self.assertNotRegex(summary.text(),r'\d\.\d')
            if smoothed:
                self.assertIn('Gauss gradual · escala 3,33333 m',summary.text())
                self.assertRegex(summary.text(),r'Variación: área [-+]\d+,\d{3} %; perímetro [-+]\d+,\d{3}')
            for width in (650,900):
                dialog.resize(width,1000)
                QTest.qWait(100)
                dialog._fit_rows()
                self.fixture.app.processEvents()
                natural = summary.heightForWidth(summary.width())
                self.assertLessEqual(abs(summary.height()-natural),2)
                heights[smoothed,width] = summary.height()
        self.assertGreater(heights[True,650],heights[False,650])
        self.assertGreater(heights[True,650],heights[True,900])

    def test_complete_manual_outlet_label_fits_the_action_row(self):
        dialog = self.dialog
        self.assertEqual(dialog.outlet_adjust_button.text(),'Ajuste Punto de Cierre')
        self.assertEqual(dialog.clear_delineation_button.text(),'Borrar Delineado')
        for width in (520,650,900):
            with self.subTest(width=width):
                dialog.resize(width,900)
                self.fixture.app.processEvents()
                grid,adjust,clear=(dialog.cell_grid_check,dialog.outlet_adjust_button,
                                   dialog.clear_delineation_button)
                self.assertLess(grid.geometry().right(),adjust.geometry().left())
                self.assertLess(adjust.geometry().right(),clear.geometry().left())
                self.assertEqual(adjust.geometry().center().y(),clear.geometry().center().y())
                self.assertLessEqual(clear.geometry().right(),dialog.resolution_group.width())
                self.assertGreaterEqual(adjust.width(),adjust.minimumSizeHint().width())
                self.assertLess(adjust.height(),30)

    def test_morphology_widths_alignments_and_complete_interpretation_are_retained(self):
        dialog = self.dialog
        for width in (520, 650, 900):
            with self.subTest(width=width):
                dialog.resize(width, 900)
                self.fixture.app.processEvents()
                dialog._fit_rows()
                table = dialog.table
                actual = [table.columnWidth(column) for column in range(4)]
                self.assertLessEqual(abs(sum(actual) - table.viewport().width()), 1)
                for column,ratio in ((1,.13),(3,.38)):
                    self.assertAlmostEqual(actual[column]/sum(actual),ratio,delta=.01)
                self.assertAlmostEqual((actual[0]+actual[2])/sum(actual),.49,delta=.01)
                self.assertLessEqual(actual[0]/sum(actual),.41)
                header = table.horizontalHeader()
                text_width = QFontMetrics(header.font()).horizontalAdvance(table.horizontalHeaderItem(2).text())
                self.assertGreaterEqual(actual[2],text_width+10)
                self.assertEqual(header.font(),dialog.font())
                for row in range(table.rowCount()):
                    for column in (1, 2):
                        self.assertEqual(table.item(row, column).textAlignment(), int(Qt.AlignCenter))
                    item = table.item(row, 3)
                    self.assertIn(item.text(), unescape(item.toolTip()))
                    shown = three_line_text(item.text(), table.font(), actual[3] - 6)
                    self.assertLessEqual(len(shown.splitlines()), 3)

    def test_dialog_tracks_qgis_frame_at_right_with_its_full_height(self):
        window = QMainWindow()
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        dialog = self.dialog
        dialog._qgis_window = window
        window.installEventFilter(dialog)
        available = QRect(0, 0, 2600, 1800)
        desktop = SimpleNamespace(availableGeometry=lambda *_: available)
        with patch('caumax_consulta.basin_dialog.QApplication.desktop', return_value=desktop):
            for width, height in ((1600, 900), (1900, 1050)):
                with self.subTest(width=width, height=height):
                    window.setGeometry(100, 100, width, height)
                    self.fixture.app.processEvents()
                    dialog._place_beside_map()
                    self.fixture.app.processEvents()
                    target = window.frameGeometry().intersected(available)
                    actual = dialog.frameGeometry()
                    self.assertEqual(actual.right(), target.right())
                    self.assertEqual(actual.top(), target.top())
                    self.assertEqual(actual.height(), target.height())
                    self.assertLessEqual(abs(actual.width() - round(target.width() * .36)), 1)

    def test_three_line_interpretation_does_not_overflow_its_text_width(self):
        font = self.dialog.table.font()
        metrics = QFontMetrics(font)
        long_text = ('Cuenca alargada con una respuesta progresiva ante la precipitación. ' * 8).strip()
        for width in (70, 150, 250):
            with self.subTest(width=width):
                lines = three_line_text(long_text, font, width).splitlines()
                self.assertEqual(len(lines), 3)
                self.assertTrue(lines[-1].endswith('…'))
                for line in lines:
                    self.assertLessEqual(metrics.horizontalAdvance(line), width + 1)
        self.assertEqual(three_line_text('Sin incidencia', font, 500), 'Sin incidencia')


class ResultLayerOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject()
        self.addCleanup(self.project.clear)
        self.root = self.project.layerTreeRoot()

    def test_new_groups_and_exported_layers_precede_official_block(self):
        before = self.root.addGroup('Cartografía anterior')
        official = self.root.addGroup('CAUMAX_CAPAS')
        after = self.root.addGroup('Cartografía posterior')
        terrain = create_result_group(self.project, 'MDT', 30)
        course = create_result_group(self.project, 'Curso', 0)
        drainage = create_result_group(self.project, 'Drenaje', 10)
        layer = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Divisoria exportada', 'memory')
        self.assertIs(add_result_layer(layer, self.project), layer)
        self.assertEqual(self.root.children(),
                         [before, course, drainage, self.root.findLayer(layer.id()), terrain, official, after])
        add_result_layer(layer, self.project)
        self.assertEqual(len(self.root.findLayers()), 1)

    def test_nested_official_block_is_not_split_by_new_results(self):
        wrapper = self.root.addGroup('Datos CAUMAX')
        official = wrapper.addGroup('CAUMAX_CAPAS')
        other = wrapper.addGroup('Otro dato oficial')
        result = create_result_group(self.project, 'Resultado', 0)
        self.assertEqual(self.root.children(), [result, wrapper])
        self.assertEqual(wrapper.children(), [official, other])

    def test_existing_results_move_intact_and_keep_visibility_and_internal_order(self):
        before = self.root.addGroup('Cartografía anterior')
        official = self.root.addGroup('CAUMAX_CAPAS')
        course = self.root.addGroup('Curso 1.2.1')
        course.setCustomProperty('HidroCAUMAX/course_owner', 'old-course')
        after = self.root.addGroup('Cartografía posterior')
        terrain = self.root.addGroup('MDT 1.2.1')
        terrain.setCustomProperty('HidroCAUMAX/terrain_owner', 'old-terrain')
        nodes = []
        for group, name, visible in ((course, 'central', False), (course, 'main', True),
                                     (terrain, 'shade', False), (terrain, 'dem', True)):
            layer = QgsVectorLayer('LineString?crs=EPSG:25830', name, 'memory')
            self.project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            node.setItemVisibilityChecked(visible)
            nodes.append((node, layer, visible))
        terrain.setItemVisibilityChecked(False)
        course.setExpanded(False)
        originals = {group: list(group.children()) for group in (course, terrain)}
        for _ in range(3):
            ensure_results_above_official(self.project)
            self.assertEqual(self.root.children(), [before, course, terrain, official, after])
            self.assertFalse(terrain.itemVisibilityChecked())
            self.assertFalse(course.isExpanded())
            for group, children in originals.items():
                self.assertEqual(group.children(), children)
            for node, layer, visible in nodes:
                self.assertIs(self.project.mapLayer(layer.id()), layer)
                self.assertIs(self.root.findLayer(layer.id()), node)
                self.assertEqual(node.itemVisibilityChecked(), visible)
        self.assertTrue(self.project.layerTreeRegistryBridge().isEnabled())

    def test_custom_drawing_order_also_keeps_results_above_official_without_reordering_others(self):
        official = self.root.addGroup('CAUMAX_CAPAS')
        ours = create_result_group(self.project, 'HidroCAUMAX', 0)
        layers = [QgsVectorLayer('LineString?crs=EPSG:25830', name, 'memory')
                  for name in ('foreign before', 'official', 'course', 'terrain', 'foreign after')]
        for index, layer in enumerate(layers):
            self.project.addMapLayer(layer, False)
            parent = official if index == 1 else ours if index in (2, 3) else self.root
            parent.addLayer(layer)
        self.root.setCustomLayerOrder(layers)
        self.root.setHasCustomLayerOrder(True)
        ensure_results_above_official(self.project)
        self.assertTrue(self.root.hasCustomLayerOrder())
        self.assertEqual(self.root.customLayerOrder(), [layers[i] for i in (0, 2, 3, 1, 4)])
        self.assertEqual(ours.children()[0].layer(), layers[2])
        self.assertEqual(ours.children()[1].layer(), layers[3])

    def test_new_export_is_above_official_when_custom_drawing_order_is_enabled(self):
        official = self.root.addGroup('CAUMAX_CAPAS')
        background = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Official background', 'memory')
        self.project.addMapLayer(background, False)
        official.addLayer(background)
        self.root.setCustomLayerOrder([background])
        self.root.setHasCustomLayerOrder(True)
        exported = QgsVectorLayer('LineString?crs=EPSG:25830', 'Exported basin', 'memory')
        add_result_layer(exported, self.project)
        self.assertEqual(self.root.customLayerOrder(), [exported, background])


if __name__ == '__main__':
    unittest.main()
