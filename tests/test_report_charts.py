"""Capture all calculated longitudinal charts, even when the dialog is hidden."""
import unittest
from types import SimpleNamespace

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QWidget

from caumax_consulta.course_flow_profile import CourseFlowProfile
from caumax_consulta.report_charts import capture_available_charts


class ReportChartCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def test_computed_specific_graph_survives_hidden_panel_and_is_png(self):
        course = CourseFlowProfile()
        self.addCleanup(course.close)
        course.resize(700, 850)
        course._rows = [
            (0., 500000., 4700000., (20.,)*6),
            (.025, 500025., 4700000., (30.,)*6),
        ]
        course._flows = [20., 30.]
        course._specific_values = [10., 12.]
        course.graph.resize(680, 300)
        course.graph.show_profile(course._rows, course._flows, None)
        course.specific_graph.resize(680, 300)
        course.specific_graph.show_profile(
            course._rows, course._specific_values, None)
        course.specific_graph.hide()
        quantile_plot = QWidget()
        quantile_plot.resize(680, 300)
        self.addCleanup(quantile_plot.close)
        dialog = SimpleNamespace(
            hypsometry_widget=None,
            quantiles_widget=SimpleNamespace(
                snapshot=lambda: {'computed':True},
                course_profile=course,
                plots=quantile_plot),
            basin_hydrology_widget=None,
        )
        captured = capture_available_charts(dialog)
        self.assertEqual([item['key'] for item in captured], [
            'qci_return', 'main_course_qci', 'specific_contribution'])
        self.assertTrue(all(
            item['png'].startswith(b'\\x89PNG\\r\\n\\x1a\\n')
            for item in captured))

    def test_no_specific_graph_without_real_area_denominators(self):
        course = CourseFlowProfile()
        self.addCleanup(course.close)
        course._rows = [(0., 1., 1., (None,)*6)]
        course._specific_values = []
        course.graph.resize(650, 290)
        quantile_plot = QWidget()
        quantile_plot.resize(650, 290)
        self.addCleanup(quantile_plot.close)
        dialog = SimpleNamespace(
            hypsometry_widget=None,
            quantiles_widget=SimpleNamespace(
                snapshot=lambda: {'computed':True},
                course_profile=course,
                plots=quantile_plot),
            basin_hydrology_widget=None)
        keys = [x['key'] for x in capture_available_charts(dialog)]
        self.assertNotIn('specific_contribution', keys)


if __name__ == '__main__':
    unittest.main()
