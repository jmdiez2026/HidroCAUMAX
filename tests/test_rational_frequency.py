"""Real Qt interactions and native CAUMAX formula equivalence for T/Q curves."""
import ast
import math
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock

from qgis.PyQt.QtCore import QPoint, QPointF, Qt
from qgis.PyQt.QtGui import QWheelEvent
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QCheckBox, QDialog, QLabel, QLineEdit, QMainWindow, QPushButton
from qgis.core import QgsApplication

from caumax_consulta.rational_frequency import (
    PERIODS, FrequencyPoint, RationalFrequency, capture_frequency,
)
from caumax_consulta.rational_frequency_dialog import (
    RationalFrequencyBridge, RationalFrequencyDialog, integer_ticks,
)
from caumax_consulta.rational_inputs import RationalInputsBridge

NATIVE_SOURCE = Path(os.environ.get('HIDROCAUMAX_CAUMAX_SOURCE',
    str(Path(__file__).resolve().parents[2] / 'inputs/caumax/caumax')))


def model(flows=(1.5, 3.1, 4.7, 7.2, 12.5, 20.4), custom=None):
    points = [FrequencyPoint(float(p), q, 'Método racional') for p, q in zip(PERIODS, flows)]
    if custom:
        points.append(FrequencyPoint(*custom, 'Cálculo CAUMAX'))
    points.sort(key=lambda p: p.period)
    return RationalFrequency(tuple(points), *(custom or (100., 12.5)))


class ModelTests(unittest.TestCase):
    def test_exact_anchors_and_log_interpolation_round_trip(self):
        curve = model(custom=(60., 10.25))
        for p, q in curve.known:
            self.assertEqual(curve.flow_at(p), q)
        self.assertAlmostEqual(curve.flow_at(math.sqrt(10 * 25)), (4.7 + 7.2) / 2)
        for period in (2, 5, 8.35, 10, 25, 37.1, 60, 72.34, 100, 375, 500):
            solutions, intervals = curve.periods_at(curve.flow_at(period))
            self.assertEqual(intervals, ())
            self.assertEqual(len(solutions), 1)
            self.assertAlmostEqual(solutions[0], period)

    def test_invalid_or_outside_values_are_not_extrapolated(self):
        curve = model()
        for period in (1, 501, float('nan'), float('inf')):
            with self.assertRaises(ValueError): curve.flow_at(period)
        for flow in (-1, .5, 21, float('nan')):
            with self.assertRaises(ValueError): curve.periods_at(flow)

    def test_flat_and_non_monotonic_curves_return_all_solutions(self):
        curve = model((1., 3., 3., 3., 5., 2.))
        points, intervals = curve.periods_at(3.)
        self.assertEqual(intervals, ((5., 25.),))
        self.assertEqual(len(points), 1)
        self.assertGreater(points[0], 100)
        points, intervals = curve.periods_at(2.5)
        self.assertEqual(len(points), 2)
        self.assertFalse(intervals)

    def test_extrapolation_requires_opt_in_and_keeps_original_anchors(self):
        curve = model()
        original = curve.points
        expected = 20.4 + (20.4 - 12.5) * math.log(1000 / 500) / math.log(500 / 100)
        self.assertAlmostEqual(curve.flow_at(1000, allow_extrapolation=True), expected)
        with self.assertRaises(ValueError): curve.flow_at(1000)
        with self.assertRaises(ValueError): curve.periods_at(expected)
        self.assertEqual(curve.points, original)
        for period, flow in curve.known:
            self.assertEqual(curve.flow_at(period, allow_extrapolation=True), flow)

    def test_extrapolation_rejects_single_anchor_negative_and_nonfinite_results(self):
        single = RationalFrequency((FrequencyPoint(100., 2., 'Cálculo CAUMAX'),), 100., 2.)
        with self.assertRaisesRegex(ValueError, 'dos caudales'): single.flow_at(1000, True)
        descending = model((1., 2., 3., 4., 100., 1.))
        with self.assertRaisesRegex(ValueError, 'negativo'): descending.flow_at(1000, True)
        for value in (1, float('nan'), float('inf')):
            with self.assertRaises(ValueError): descending.flow_at(value, True)
        partial = RationalFrequency((FrequencyPoint(5., 10., 'Método racional'),
                                     FrequencyPoint(10., 12., 'Método racional')), 5., 10.)
        self.assertAlmostEqual(partial.flow_at(2, True), 10 + 2 * math.log(2 / 5) / math.log(2))

    def test_integer_ticks_remain_integer_and_sufficient_at_normal_extent(self):
        values = integer_ticks(0, 28)
        self.assertGreaterEqual(len(values), 6)
        self.assertTrue(all(isinstance(value, int) for value in values))


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.dialog = RationalFrequencyDialog(model(custom=(60., 10.25)))
        self.dialog.setAttribute(Qt.WA_DontShowOnScreen)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.app.processEvents()

    def row(self, period):
        return next(row for row in range(self.dialog.table.rowCount())
                    if math.isclose(self.dialog.table.item(row, 0).data(Qt.UserRole), period))

    def test_required_rows_comma_decimals_and_aligned_table_plot(self):
        d = self.dialog
        self.assertEqual(d.table.rowCount(), 7)
        self.assertEqual(d.table.columnCount(), 2)
        self.assertFalse(any('Estimar' in button.text() for button in d.findChildren(QPushButton)))
        self.assertEqual(d.period_spin.decimals(), 2)
        self.assertEqual([d.table.horizontalHeaderItem(i).text() for i in range(2)],
                         ['Periodo de\nretorno T (años)', 'Caudal punta\n(m³/s)'])
        self.assertEqual(d.table.item(self.row(60), 1).text(), '10,25')
        self.assertEqual(d.table.item(self.row(2), 1).text(), '1,50')
        self.assertEqual(d.table.item(self.row(100), 1).text(), '12,50')
        for row in range(d.table.rowCount()):
            for column in range(2):
                self.assertEqual(d.table.item(row, column).textAlignment(), int(Qt.AlignCenter))
        self.assertIn(',', d.flow_spin.text())
        self.assertEqual(d.table.mapTo(d, QPoint()).y(),
                         d.plot.mapTo(d, QPoint()).y() + d.plot.plot_rect().top())
        self.assertEqual(d.table.height(), d.plot.plot_rect().height())
        self.assertEqual((d.plot.width(), d.plot.height()), (422, 268))
        xticks, yticks = d.plot.ticks()
        self.assertGreaterEqual(len(xticks), 6)
        self.assertEqual((xticks[0], xticks[-1]), (2, 500))
        self.assertGreaterEqual(len(yticks), 6)
        self.assertTrue(all(isinstance(value, int) for value in xticks + yticks))
        self.assertFalse(d.grab().isNull())

    def test_actual_mouse_clicks_link_table_and_plot_selection(self):
        d = self.dialog
        index = d.table.model().index(self.row(10), 1)
        QTest.mouseClick(d.table.viewport(), Qt.LeftButton, pos=d.table.visualRect(index).center())
        self.assertEqual(d.plot.selected_period, 10)
        position = d.plot.coordinate(100, 12.5).toPoint()
        QTest.mouseClick(d.plot, Qt.LeftButton, pos=position)
        self.assertEqual(d.table.currentRow(), self.row(100))
        self.assertEqual(d.plot.selected_period, 100)
        QTest.mouseClick(d.plot, Qt.LeftButton, pos=d.plot.coordinate(500, 20.4).toPoint())
        self.assertEqual(d.table.currentRow(), self.row(500))

    def test_forward_inverse_add_points_without_changing_anchors_or_duplicates(self):
        d = self.dialog
        initial = d.model.known
        d.period_spin.setValue(37.5)
        QTest.qWait(300)
        self.assertEqual(d.table.rowCount(), 8)
        expected = d.model.flow_at(37.5)
        self.assertAlmostEqual(d.rows[37.5].flow, expected)
        self.assertEqual(d.table.item(self.row(37.5), 1).text(), f'{expected:.2f}'.replace('.', ','))
        self.assertEqual(d.flow_spin.decimals(), 2)
        self.assertEqual(d.plot.selected_period, 37.5)
        d.period_spin.editingFinished.emit()
        self.assertEqual(d.table.rowCount(), 8)
        d.flow_spin.setValue(6.2)
        QTest.qWait(300)
        self.assertEqual(d.table.rowCount(), 9)
        self.assertIn('Periodo de retorno (T)', d.inverse_result.text())
        self.assertAlmostEqual(d.rows[d.plot.selected_period].flow, 6.2)
        self.assertEqual(d.model.known, initial)
        d.flow_spin.setValue(999)
        QTest.qWait(300)
        self.assertIn('fuera', d.inverse_result.text())
        self.assertEqual(d.table.rowCount(), 9)

    def test_graph_customization_wheel_zoom_and_reset(self):
        d = self.dialog
        d.labels_check.setChecked(True)
        d.grid_check.setChecked(False)
        self.assertTrue(d.plot.labels)
        self.assertFalse(d.plot.grid)
        self.assertEqual(d.plot.line_width, 2)
        self.assertEqual(d.plot.line_style, Qt.SolidLine)
        self.assertFalse(d.grab().isNull())
        before = list(d.plot.view)
        pos = d.plot.plot_rect().center()
        event = QWheelEvent(pos, QPointF(d.plot.mapToGlobal(pos.toPoint())), QPoint(),
                            QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication = type(self.app)
        QApplication.sendEvent(d.plot, event)
        self.assertLess(d.plot.view[1] - d.plot.view[0], before[1] - before[0])
        d.plot.reset_view()
        self.assertEqual(d.plot.view, before)
        d.log_check.setChecked(False)
        self.assertFalse(d.plot.logarithmic)
        self.assertEqual(d.plot.view[:2], [2., 500.])

    def test_only_requested_compact_controls_are_shown(self):
        d = self.dialog
        self.assertEqual([c.text() for c in (d.labels_check, d.grid_check, d.log_check)],
                         ['Etiqueta', 'Cuadrícula', 'Escala logarítmica T'])
        for left, right in ((d.labels_check, d.grid_check), (d.grid_check, d.log_check)):
            self.assertLessEqual(right.x() - (left.x() + left.width()), 14)
        self.assertEqual([b.text() for b in d.findChildren(QPushButton)], ['Cerrar'])
        labels = ' '.join(label.text() for label in d.findChildren(QLabel))
        self.assertNotIn('Rueda:', labels)
        self.assertNotIn('Estimaciones:', labels)
        self.assertNotIn('Grosor:', labels)
        self.assertFalse(d.extrapolation_check.isChecked())

    def test_extrapolation_toggle_recomputes_and_removes_estimates_when_disabled(self):
        d = self.dialog
        initial = d.model.points
        d.period_spin.setValue(1000)
        QTest.qWait(300)
        self.assertNotIn(1000, d.rows)
        self.assertIn('intervalo', d.forward_result.text())
        QTest.mouseClick(d.extrapolation_check, Qt.LeftButton)
        self.assertIn(1000, d.rows)
        self.assertIn('extrapolado', d.forward_result.text())
        self.assertIn('extrapolada', d.table.item(self.row(1000), 1).toolTip())
        self.assertTrue(d.plot.plot_rect().adjusted(-1, -1, 1, 1).contains(
            d.plot.coordinate(1000, d.rows[1000].flow)))
        self.assertEqual(d.plot.selected_period, 1000)
        self.assertFalse(d.grab().isNull())
        d.log_check.setChecked(False)
        self.assertEqual(d.plot.view[1], 1000)
        QTest.mouseClick(d.extrapolation_check, Qt.LeftButton)
        self.assertNotIn(1000, d.rows)
        self.assertEqual(d.plot.view[1], 500)
        self.assertEqual(d.model.points, initial)

    def test_disabling_extrapolation_restores_unavailable_original_period(self):
        self.dialog.close()
        restricted = model((1., 2., 3., 4., None, None), custom=(20., 3.5))
        self.dialog = RationalFrequencyDialog(restricted)
        d = self.dialog
        d.setAttribute(Qt.WA_DontShowOnScreen)
        d.show()
        d.period_spin.setValue(100)
        d.extrapolation_check.setChecked(True)
        self.assertIsNotNone(d.rows[100].flow)
        self.assertIsNone(next(p for p in d.model.points if p.period == 100).flow)
        d.extrapolation_check.setChecked(False)
        self.assertIsNone(d.rows[100].flow)
        self.assertEqual(d.table.item(self.row(100), 1).text(), '—')


@unittest.skipUnless((NATIVE_SOURCE / 'rationalmethoddialog.py').is_file(), 'Fuente nativa CAUMAX no disponible')
class NativeFormulaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()
        source = NATIVE_SOURCE / 'rationalmethoddialog.py'
        node = next(n for n in ast.parse(source.read_text(encoding='utf-8-sig')).body
                    if isinstance(n, ast.ClassDef) and n.name == 'RationalMethodDialog')
        methods = [n for n in node.body if isinstance(n, ast.FunctionDef)
                   and n.name in ('calculate', 'getFlow', 'factorsFromRainfall')]
        cls.namespace = dict(math=math, ZONES='zones', ZONE_NAME_FIELD='zone', MDT='dem',
                             usesOnlyDefaultLayers=lambda: True, QMessageBox=Mock(),
                             waitcursor=lambda method: method)
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), cls.namespace)

    def setUp(self):
        self.window = QMainWindow()
        self.bar = Mock()
        self.iface = types.SimpleNamespace(mainWindow=lambda: self.window, messageBar=lambda: self.bar)
        class RationalMethodDialog(QDialog):
            def calculateParameters(self): pass
            def returnPeriod(self): return self.period
            def outletPoint(self): return (346031.3, 4717696.9)
        RationalMethodDialog.__module__ = '_frequency_test.rationalmethoddialog'
        parameters = dict(BasinCalculator=lambda: None)
        exec('def calculateParameters(self):\n    self.basinCalc = BasinCalculator()\n', parameters)
        RationalMethodDialog.calculateParameters = parameters['calculateParameters']
        for name in ('calculate', 'getFlow', 'factorsFromRainfall'):
            setattr(RationalMethodDialog, name, self.namespace[name])
        self.owner = RationalMethodDialog(self.window)
        d = self.owner
        for name, text in dict(txtArea='14.750', txtUniformity='1.37', txtIntensityFactor='3.28',
                txtP0='42.15', txtP0Corrector='0.70', txtRainfallCorrection='0.92',
                txtCorrectedRainfall='84.93', txtP0CorrectorReturnPeriod='1.44',
                txtResultFlow='', txtResultReturnPeriod='', txtCFactor='', txtIntensity='',
                txtCorrectedP0='', txtI1Id='9.00').items():
            setattr(d, name, QLineEdit(text, d))
        for name in ('btnFitInfo', 'btnFrequencyLaw', 'btnReport', 'btnCalculate'):
            setattr(d, name, QPushButton(d))
        d.btnCalculate.clicked.connect(lambda: d.calculate())
        d.chkOrdinaryMaxFlood = QCheckBox(d)
        d.period = 100
        d.basinCalc = types.SimpleNamespace(rain=dict(zip(PERIODS, (46., 54., 63., 75., 92.10, 115.))))
        d.p0CorrectorsReturnPeriod = list(PERIODS)
        d.outletMarker = types.SimpleNamespace(setCenter=lambda p: None)
        self.zone = 21
        self.corrections = dict(zip(PERIODS, (.72, .90, 1., 1.17, 1.44, 1.75)))
        self.namespace['getValueAtPoint'] = lambda layer, pt, field=None: (
            self.zone if field == 'zone' else self.corrections[field] if layer == 'zones' else 100)
        self.inputs = RationalInputsBridge(self.iface)
        self.presentation = RationalFrequencyBridge(self.iface)
        self.events = []
        self.inputs.calculation_started.connect(self.presentation.calculation_started)
        self.inputs.calculation_finished.connect(lambda owner, stage, event: self.events.append(event))
        self.inputs.calculation_finished.connect(self.presentation.calculation_finished)
        d.setAttribute(Qt.WA_DontShowOnScreen)
        d.show()
        self.inputs.start()

    def tearDown(self):
        self.inputs.stop()
        self.presentation.stop()
        self.owner.close()
        self.window.close()
        self.app.processEvents()

    def test_only_explicit_gea_request_opens_curve_with_exact_q_and_no_input_mutation(self):
        d = self.owner
        before = {name: getattr(d, name).text() for name in ('txtArea', 'txtUniformity', 'txtP0',
                    'txtP0Corrector', 'txtIntensityFactor', 'txtRainfallCorrection')}
        d.calculate()
        self.assertNotIn(id(d), self.presentation.dialogs)
        self.presentation.request_calculation(d)
        self.assertIn(id(d), self.presentation.dialogs)
        dialog = self.presentation.dialogs[id(d)]
        curve = dialog.model
        self.assertEqual(curve.accepted_period, 100)
        expected_intensity, expected_runoff = d.factorsFromRainfall(84.93, 42.15 * .70 * 1.44, 3.28)
        self.assertAlmostEqual(curve.accepted_flow, d.getFlow(expected_runoff, expected_intensity))
        self.assertEqual(d.txtResultFlow.text(), f'{curve.accepted_flow:.2f}')
        for period in (2, 5, 10, 25, 500):
            intensity, runoff = d.factorsFromRainfall(d.basinCalc.rain[period] * .92,
                                                     42.15 * .70 * self.corrections[period], 3.28)
            self.assertAlmostEqual(curve.flow_at(period), d.getFlow(runoff, intensity))
        dialog.period_spin.setValue(62.5)
        dialog.estimate_forward()
        dialog.flow_spin.setValue(5.)
        dialog.estimate_inverse()
        self.assertEqual(before, {name: getattr(d, name).text() for name in before})
        self.assertEqual(d.txtResultFlow.text(), f'{curve.accepted_flow:.2f}')
        self.assertFalse(self.bar.pushWarning.called)

    def test_restricted_region_and_missing_rain_do_not_invent_high_periods(self):
        d = self.owner
        self.zone, d.period = 72, 25
        del d.basinCalc.rain[5]
        self.presentation.request_calculation(d)
        curve = self.presentation.dialogs[id(d)].model
        missing = {p.period: p for p in curve.points if p.flow is None}
        self.assertEqual(set(missing), {5., 100., 500.})
        self.assertIn('25', missing[100.].note)
        with self.assertRaises(ValueError): curve.flow_at(50)

    def test_early_native_validation_does_not_reopen_old_result(self):
        d = self.owner
        self.presentation.request_calculation(d)
        self.assertIn(id(d), self.presentation.dialogs)
        d.txtArea.setText('51')
        self.presentation.request_calculation(d)
        self.assertNotIn(id(d), self.presentation.dialogs)
        self.assertFalse(self.events[-1].get('accepted_flow'))

    def test_custom_native_period_retains_supporting_flows_and_exact_fractional_T(self):
        d = self.owner
        d.calculate()
        event = dict(self.events[-1], accepted_period=12.5, accepted_flow=4.123,
                     supporting_flows=(1., 2., 3., 5., 9., 18.))
        curve = capture_frequency(d, event)
        self.assertEqual(curve.accepted_period, 12.5)
        self.assertEqual(curve.flow_at(12.5), 4.123)
        self.assertEqual(curve.flow_at(500), 18.)
        self.assertEqual(len(curve.points), 7)

    def test_cancelled_and_mismatched_writes_are_ignored_and_owner_close_cleans_up(self):
        d = self.owner
        self.presentation.request_calculation(d)
        event = dict(self.events[-1], completed=False)
        self.assertIsNone(capture_frequency(d, event))
        event = dict(self.events[-1], flow_writes=['999'])
        self.assertIsNone(capture_frequency(d, event))
        d.close()
        self.assertFalse(self.presentation.dialogs)

    def test_native_button_never_opens_table_and_disabled_request_is_ignored(self):
        d = self.owner
        d.btnCalculate.click()
        self.assertFalse(self.presentation.dialogs)
        self.assertTrue(d.txtResultFlow.text())
        self.presentation.request_calculation(d)
        self.assertIn(id(d), self.presentation.dialogs)
        d.btnCalculate.click()
        self.assertFalse(self.presentation.dialogs)
        d.btnCalculate.setEnabled(False)
        self.presentation.request_calculation(d)
        self.assertFalse(self.presentation.dialogs)
        self.assertFalse(self.presentation._requested)
