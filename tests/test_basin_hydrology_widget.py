"""Actual offscreen Qt checks for portable basin precipitation results.

These tests exercise the widget and its PNG renderer without opening a raster.
The raster aggregation engine has its own fixtures; a saved result must remain
usable after all original source paths have disappeared.
"""

import copy
import json
import math
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from osgeo import gdal
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QImage, QPalette
from qgis.PyQt.QtTest import QSignalSpy, QTest
from qgis.PyQt.QtWidgets import QLabel
from qgis.core import QgsApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from caumax_consulta import basin_hydrology_widget as hydrology


PERIODS = (2, 5, 10, 25, 100, 500)
METHOD = "piecewise_linear_p_log_t"
MEANS = (0, 10, 20, 30, 40, 50)
FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


def result(mean=12.5, *, coverage=1.0, valid=1.0, area=1000.0,
           path="/sources/no-longer-present.tif", notes=None):
    if mean is None:
        valid = 0.0
    return {
        "source_path": path,
        "mean_mm": mean,
        "basin_area_m2": area,
        "covered_area_m2": coverage * area,
        "valid_area_m2": valid * area,
        "coverage_fraction": coverage,
        "valid_fraction": valid,
        "pixel_count": 4,
        "status": ("unavailable" if mean is None else
                   "partial" if coverage < 1 or valid < 1 else "ok"),
        "notes": list(notes or []),
    }


def saved_state(means=MEANS, **changes):
    state = {
        "version": 1,
        "basin_fingerprint": FINGERPRINT_A,
        "basin_area_m2": 1000.0,
        "p0_result": result(12.5),
        "rainfall_results": {
            str(period): result(mean, path=f"/sources/P{period}.tif")
            for period, mean in zip(PERIODS, means)
        },
        "input_period": 5.0,
        "input_precipitation": 20.0,
        "extrapolate": False,
        "method": METHOD,
    }
    state.update(changes)
    return state


def text_number(value):
    return f"{value:.2f}".rstrip('0').rstrip('.').replace(".", ",")


class BasinHydrologyWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.identity = patch.object(
            hydrology, "_fingerprint", side_effect=lambda analysis: analysis.fingerprint
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.analysis = SimpleNamespace(
            fingerprint=FINGERPRINT_A, basin=SimpleNamespace(area_m2=1000.0)
        )
        self.widget = hydrology.BasinHydrologyWidget()
        self.widget.set_analysis(self.analysis)

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_initial_analysis_is_uncalculated_and_never_reads_sources(self):
        widget = hydrology.BasinHydrologyWidget()
        try:
            self.assertIsNone(widget.snapshot())
            with patch.object(gdal, "Open", side_effect=AssertionError("unexpected raster IO")):
                widget.set_analysis(self.analysis)
                state = widget.snapshot()
            self.assertEqual(state["basin_fingerprint"], FINGERPRINT_A)
            self.assertEqual(state["basin_area_m2"], 1000)
            self.assertIsNone(state["p0_result"])
            self.assertEqual(state["rainfall_results"], {})
            self.assertEqual(state["method"], METHOD)
            self.assertTrue(state["extrapolate"])
            self.assertTrue(widget.extrapolate_check.isChecked())
            self.assertNotIn("= 0 mm", widget.p0_label.text())
        finally:
            widget.close()

    def test_no_calculation_buttons_and_busy_preserves_automatic_means(self):
        """The presentational widget has no manual trigger and no raster I/O."""
        self.widget.restore(saved_state())
        self.assertEqual(self.widget.status_label.text(), "")
        self.assertTrue(self.widget.status_label.isHidden())
        self.assertFalse(hasattr(self.widget, 'p0_button'))
        self.assertFalse(hasattr(self.widget, 'rainfall_button'))
        self.assertEqual(self.widget.rainfall_group.title(),
                         'Precipitación diaria máxima anual media en la cuenca')
        cancel_spy = QSignalSpy(self.widget.cancel_requested)
        before = self.widget.snapshot()
        self.widget.set_busy(True, "Calculando cobertura…")
        self.assertFalse(self.widget.cancel_button.isHidden())
        self.assertIn("Calculando cobertura", self.widget.status_label.text())
        self.assertFalse(self.widget.status_label.isHidden())
        self.widget.cancel_button.click()
        self.assertEqual(len(cancel_spy), 1)
        self.assertEqual(self.widget.snapshot(), before)
        self.widget.set_busy(False)
        self.assertTrue(self.widget.cancel_button.isHidden())
        self.assertTrue(self.widget.status_label.isHidden())
        self.widget.set_busy(False, "No se pudo abrir la capa de precipitación.")
        self.assertFalse(self.widget.status_label.isHidden())
        self.assertIn("No se pudo abrir", self.widget.status_label.text())
        self.assertEqual(self.widget.snapshot(), before)

    def test_atomic_joint_results_reject_partial_publication(self):
        before = self.widget.snapshot()
        payload = {'p0': result(5.5), 'rainfall':
                   {str(period): result(11. + i) for i, period in enumerate(PERIODS)}}
        with self.assertRaises(ValueError):
            self.widget.set_result('both', {'p0': payload['p0']})
        self.assertEqual(self.widget.snapshot(), before)
        self.widget.set_result('both', payload)
        self.assertEqual(self.widget.snapshot()['p0_result']['mean_mm'], 5.5)
        self.assertEqual(len(self.widget.snapshot()['rainfall_results']), 6)
        self.assertIn('P₀ medio en la cuenca', self.widget.p0_label.text())

    def test_same_basin_preserves_calculated_values_without_resampling(self):
        state = saved_state()
        self.widget.restore(state)
        equivalent = SimpleNamespace(fingerprint=FINGERPRINT_A, area_m2=1000.0)
        with patch.object(gdal, "Open", side_effect=AssertionError("unexpected resampling")):
            self.widget.set_analysis(equivalent)
        self.assertEqual(self.widget.snapshot(), state)
        self.assertTrue(self.widget.coverage_label.isHidden())

    def test_different_basin_clears_every_result_and_updates_identity(self):
        self.widget.restore(saved_state())
        other = SimpleNamespace(fingerprint=FINGERPRINT_B, area_m2=2500.0)
        self.widget.set_analysis(other)
        state = self.widget.snapshot()
        self.assertEqual(state["basin_fingerprint"], FINGERPRINT_B)
        self.assertEqual(state["basin_area_m2"], 2500)
        self.assertIsNone(state["p0_result"])
        self.assertEqual(state["rainfall_results"], {})
        self.assertNotIn("12,5", self.widget.p0_label.text())

    def test_explicit_empty_state_resets_same_basin_and_none_analysis_clears_panel(self):
        self.widget.restore(saved_state())
        self.widget.set_analysis(self.analysis, state=None)
        self.assertIsNone(self.widget.snapshot()["p0_result"])
        self.assertEqual(self.widget.snapshot()["rainfall_results"], {})
        self.widget.set_analysis(None)
        self.assertIsNone(self.widget.snapshot())
        self.assertFalse(hasattr(self.widget, 'p0_button'))
        self.assertFalse(hasattr(self.widget, 'rainfall_button'))

    def test_offline_restore_emits_one_change_and_returns_deep_copies(self):
        state = saved_state()
        state["p0_result"]["notes"] = ["Cobertura revisada"]
        spy = QSignalSpy(self.widget.changed)
        with patch.object(gdal, "Open", side_effect=AssertionError("offline source read")):
            self.widget.restore(state, analysis=self.analysis)
            self.assertEqual(self.widget.snapshot(), state)
        self.assertEqual(len(spy), 1)
        state["p0_result"]["notes"].append("Changed input")
        clone = self.widget.snapshot()
        clone["p0_result"]["notes"].append("Changed output")
        clone["rainfall_results"]["2"]["mean_mm"] = 999
        self.assertEqual(self.widget.snapshot()["p0_result"]["notes"], ["Cobertura revisada"])
        self.assertEqual(self.widget.snapshot()["rainfall_results"]["2"]["mean_mm"], 0)
        json.dumps(self.widget.snapshot(), allow_nan=False)

    def test_control_changes_preserve_other_restored_input_precision(self):
        state = saved_state(input_period=12.123456789, input_precipitation=123.123456789)
        self.widget.restore(state)
        spy = QSignalSpy(self.widget.changed)
        self.widget.extrapolate_check.setChecked(True)
        self.assertEqual(self.widget.snapshot()["input_period"], state["input_period"])
        self.assertEqual(self.widget.snapshot()["input_precipitation"], state["input_precipitation"])
        self.widget.period_spin.setValue(20)
        self.assertEqual(self.widget.snapshot()["input_period"], 20)
        self.assertEqual(self.widget.snapshot()["input_precipitation"], state["input_precipitation"])
        self.widget.precipitation_spin.setValue(0)
        self.assertEqual(self.widget.snapshot()["input_precipitation"], 0)
        self.assertTrue(self.widget.snapshot()["extrapolate"])
        self.assertEqual(len(spy), 3)

    def test_invalid_restore_is_rejected_atomically(self):
        valid = saved_state()
        self.widget.restore(valid)
        invalid_states = [
            valid | {"version": 2}, valid | {"input_period": 1},
            valid | {"input_precipitation": -1}, valid | {"input_period": float("nan")},
            valid | {"extrapolate": "yes"}, valid | {"method": "piecewise_linear_q_log_t"},
            valid | {"basin_fingerprint": "basin-a"},
            valid | {"basin_fingerprint": "A" * 64},
        ]
        for key, value in (("mean_mm", -1), ("coverage_fraction", 1.1),
                           ("valid_area_m2", 1001), ("status", "unknown"),
                           ("pixel_count", -1)):
            broken = copy.deepcopy(valid)
            broken["p0_result"][key] = value
            invalid_states.append(broken)
        broken = copy.deepcopy(valid)
        broken["p0_result"]["unexpected"] = "not canonical"
        invalid_states.append(broken)
        broken = copy.deepcopy(valid)
        broken["p0_result"]["status"] = "unavailable"
        invalid_states.append(broken)
        for invalid in invalid_states:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.widget.restore(invalid)
            self.assertEqual(self.widget.snapshot(), valid)

    def test_status_and_intersected_pixel_invariants_are_rejected_atomically(self):
        valid = saved_state()
        self.widget.restore(valid)
        inconsistent = [
            result(None) | {"valid_area_m2": 500.0, "valid_fraction": 0.5},
            result(12.5, valid=0.5) | {"status": "ok"},
            result(12.5) | {"status": "partial"},
            result(12.5, valid=0.0),
            result(12.5) | {"pixel_count": 0},
            result(12.5) | {"pixel_count": True},
        ]
        spy = QSignalSpy(self.widget.changed)
        for p0 in inconsistent:
            with self.subTest(p0=p0), self.assertRaises(ValueError):
                self.widget.restore(valid | {"p0_result": p0})
            self.assertEqual(self.widget.snapshot(), valid)
        self.assertEqual(len(spy), 0)

    def test_basin_and_statistical_area_rounding_within_tolerance_is_accepted(self):
        # Persisted areas may differ slightly after a JSON/geometry round trip.
        # Both identity and statistics use rel=1e-7 / abs=1e-5 for square metres.
        state = saved_state(basin_area_m2=1000.00005)
        state["p0_result"]["basin_area_m2"] = 1000.00008
        self.widget.restore(state, analysis=self.analysis)
        self.assertEqual(self.widget.snapshot(), state)
        partial = result(12.5, valid=0.5)
        self.widget.set_result("p0", partial)
        self.assertEqual(self.widget.snapshot()["p0_result"]["status"], "partial")
        self.widget.set_result("p0", result(None))
        self.assertIsNone(self.widget.snapshot()["p0_result"]["mean_mm"])
        self.assertEqual(self.widget.snapshot()["p0_result"]["valid_area_m2"], 0)

    def test_mismatched_basin_restore_is_rejected_without_source_access(self):
        valid = saved_state()
        self.widget.restore(valid)
        mismatch = valid | {"basin_fingerprint": "c" * 64}
        with patch.object(gdal, "Open", side_effect=AssertionError("mismatch source read")):
            with self.assertRaises(ValueError):
                self.widget.restore(mismatch, analysis=self.analysis)
        self.assertEqual(self.widget.snapshot(), valid)

    def test_result_display_distinguishes_zero_missing_and_partial_coverage(self):
        p0 = result(12.5, coverage=0.5, valid=0.25, notes=["Cobertura parcial"])
        p0['statistics'] = dict(median_mm=12.5, max_mm=20, min_mm=5, stddev_mm=3)
        rainfall = {str(p): result(v) for p, v in zip(PERIODS, (0, None, 20, 30, 40, 50))}
        self.widget.set_result("p0", p0)
        self.widget.set_result("rainfall", rainfall)
        self.assertEqual(self.widget.snapshot()["p0_result"], p0)
        self.assertEqual(self.widget.snapshot()["rainfall_results"], rainfall)
        self.assertRegex(self.widget.p0_label.text(), r"12[,.]5")
        coverage = self.widget.coverage_label.text()
        self.assertRegex(coverage, r"50(?:[,.]0+)?\s*%")
        self.assertRegex(coverage, r"25(?:[,.]0+)?\s*%")
        self.assertTrue(self.widget.coverage_label.isHidden())
        self.assertEqual(self.widget.p0_statistics_label.text(),
            'Mediana: 12,50 mm · Máx: 20,00 mm · Mín: 5,00 mm · Desv. típ: 3,00 mm')
        table = self.widget.rainfall_table
        self.assertEqual(table.rowCount(), 6)
        self.assertEqual([table.item(row, 0).text() for row in range(6)],
                         [str(period) for period in PERIODS])
        self.assertRegex(table.item(0, 1).text(), r"^0(?:[,.]0+)?$")
        self.assertNotEqual(table.item(1, 1).text(), table.item(0, 1).text())
        self.assertRegex(table.item(1, 1).text().lower(), r"disponible|sin dato|—|–")
        self.assertFalse(table.alternatingRowColors())
        self.assertIn("coberturas parciales", self.widget.status_label.text())
        self.assertFalse(self.widget.status_label.isHidden())
        self.assertTrue("white" in table.styleSheet().lower()
                        or "#fff" in table.styleSheet().lower()
                        or table.palette().color(QPalette.Base).name() == "#ffffff")

    def test_precipitation_labels_explain_spatial_quantiles_and_p0_without_beta(self):
        state = saved_state()
        state["rainfall_results"]["5"]["statistics"] = dict(
            median_mm=10, min_mm=8, max_mm=12, stddev_mm=1)
        self.widget.restore(state)
        texts = [label.text() for label in self.widget.findChildren(QLabel)]
        texts += [self.widget.rainfall_table.horizontalHeaderItem(i).text()
                  for i in range(self.widget.rainfall_table.columnCount())]
        combined = " ".join(texts)
        self.assertNotRegex(combined, r"\bQ\s*\(|\bQ\s*=|m³/s|[Cc]audal")
        self.assertRegex(combined, r"Pd\(")
        description = hydrology.describe_precipitation_estimates(state)
        note = description["method_note"].lower()
        self.assertIn("espacial", note)
        self.assertIn("cuantiles", note)
        self.assertRegex(note, r"puntual|punto")
        self.assertIn("areal", note)
        self.assertRegex(combined.lower(), r"β|beta")
        self.assertRegex(combined.lower(), r"no (?:se )?aplic|sin aplicar")
        for extrapolate in (False, True):
            self.widget.extrapolate_check.setChecked(extrapolate)
            note = self.widget.method_label.text()
            self.assertIn("Promedio espacial de los cuantiles puntuales", note)
            self.assertIn("Los estadísticos de Pd(T)", note)
            for removed in ("Interpolación lineal de Pd media", "No equivale",
                            "P₀ se presenta", "Extrapolación permitida",
                            "Sin extrapolación fuera"):
                self.assertNotIn(removed, note)
            report = hydrology.describe_precipitation_estimates(self.widget.snapshot())
            self.assertIn("Interpolación lineal de Pd media", report["method_note"])
            self.assertIn("No equivale", report["method_note"])
            self.assertIn("P₀ se presenta", report["method_note"])
            self.assertIn("Extrapolación permitida" if extrapolate else
                          "Sin extrapolación fuera", report["method_note"])
        self.assertIn("Máx: 12 mm · Mín: 8 mm · Desv. típ. espacial: 1 mm", self.widget.forward_label.text())
        self.assertEqual(self.widget.forward_label.text().count('<br/>'),1)
        self.assertIn("Máximo: 12 mm · Mínimo: 8 mm", description["forward_text"])
        self.assertIn("Desviación típica espacial: 1 mm", description["forward_text"])

    def test_automatic_precipitation_six_rows_fit_narrow_width_without_clipping(self):
        state = saved_state()
        for item in state["rainfall_results"].values():
            mean = item["mean_mm"]
            item["statistics"] = dict(median_mm=mean, min_mm=max(0, mean-2),
                                      max_mm=mean+2, stddev_mm=1)
        self.widget.restore(state)
        # A 520px dialog leaves 464px for this widget after its scroll/body margins.
        for width in (464, 594):
            with self.subTest(width=width):
                self.widget.resize(width, 1700)
                self.widget.show()
                QTest.qWait(50)
                self.assertGreaterEqual(self.widget.width(), width)
                self.assertFalse(hasattr(self.widget, 'rainfall_button'))
                self.assertFalse(hasattr(self.widget, 'p0_button'))
                table = self.widget.rainfall_table
                self.assertEqual(table.rowCount(), 6)
                self.assertEqual([table.horizontalHeaderItem(i).text() for i in range(6)],
                    ['Periodo T (años)', 'Pd (media) [mm]', 'Pd mín [mm]',
                     'Pd máx [mm]', 'Desv. típ. [mm]', 'Estado'])
                metrics = table.fontMetrics()
                minimum = max(metrics.height(), metrics.lineSpacing())+2
                for row in range(6):
                    self.assertGreaterEqual(table.rowHeight(row), minimum)
                    for column in range(6):
                        bounds = metrics.boundingRect(0, 0, table.columnWidth(column)-6, 1000,
                            Qt.AlignCenter | Qt.TextWordWrap, table.item(row, column).text())
                        self.assertLessEqual(bounds.height()+2, table.rowHeight(row))
                        self.assertLessEqual(bounds.width(), table.columnWidth(column)-6)
                self.assertEqual(table.height(), table.horizontalHeader().height()
                    + sum(table.rowHeight(i) for i in range(6)) + 2*table.frameWidth())
        # Under the usual smaller QGIS font, metric-sized rows are shorter than
        # the former fixed 24px while still retaining the complete text height.
        font = QFont(self.widget.rainfall_table.font())
        font.setPointSize(9)
        self.widget.rainfall_table.setFont(font)
        self.widget._fit_rainfall_rows()
        self.assertLess(self.widget.rainfall_table.rowHeight(0), 24)

    def test_log_period_interpolation_inverse_and_explicit_extrapolation(self):
        period = math.sqrt(10 * 25)
        state = saved_state(input_period=period, input_precipitation=25)
        text = hydrology.describe_precipitation_estimates(state)
        self.assertRegex(text["forward_text"], r"=\s*25(?:[,.]0+)?\s*mm")
        self.assertIn(f'{period:.0f}', text["inverse_text"])
        state["input_period"] = 1000
        unavailable = hydrology.describe_precipitation_estimates(state)
        self.assertIn("no disponible", unavailable["forward_text"].lower())
        state["extrapolate"] = True
        extrapolated = hydrology.describe_precipitation_estimates(state)
        self.assertIn("extrapola", extrapolated["forward_text"].lower())
        self.assertNotIn("no disponible", extrapolated["forward_text"].lower())

    def test_inverse_reports_plateau_multiple_solutions_and_valid_zero(self):
        plateau = saved_state((0, 10, 20, 20, 20, 50), input_precipitation=20)
        text = hydrology.describe_precipitation_estimates(plateau)["inverse_text"].lower()
        self.assertIn("meseta", text)
        self.assertIn("no única", text)
        self.assertRegex(text, r"10\b.*100\b")
        ambiguous = saved_state((0, 20, 10, 30, 40, 50), input_precipitation=15)
        text = hydrology.describe_precipitation_estimates(ambiguous)["inverse_text"]
        self.assertIn("T posibles", text)
        self.assertIn("no única", text)
        # The three intersected pieces provide three distinct return periods.
        self.assertGreaterEqual(len(re.findall(r"años", text)), 3)
        zero = saved_state(input_period=2, input_precipitation=0)
        text = hydrology.describe_precipitation_estimates(zero)
        self.assertRegex(text["forward_text"], r"=\s*0(?:[,.]0+)?\s*mm")
        self.assertNotIn("no disponible", text["inverse_text"].lower())

    def test_actual_pngs_render_empty_single_full_and_plateau_without_panel_fill(self):
        variants = {
            "empty": {},
            "single": {"10": result(20)},
            "full": saved_state()["rainfall_results"],
            "plateau": saved_state((0, 10, 20, 20, 20, 50))["rainfall_results"],
        }
        for label, rainfall in variants.items():
            with self.subTest(label=label):
                state = saved_state(rainfall_results=rainfall)
                with patch.object(gdal, "Open", side_effect=AssertionError("PNG source read")):
                    png = hydrology.render_precipitation_plots(state, width=1100, height=500)
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                image = QImage.fromData(png)
                self.assertFalse(image.isNull())
                self.assertEqual((image.width(), image.height()), (1100, 500))
                # Each panel must contain rendered axes, labels or data; avoid
                # tying this check to the renderer's exact margin in pixels.
                for left, right in ((60, 500), (610, 1060)):
                    painted = sum(image.pixelColor(x, y).name() != "#ffffff"
                                  for x in range(left, right, 5)
                                  for y in range(65, 400, 5))
                    self.assertGreater(painted, 50)
                blue = pixels = 0
                for x in range(640, 1040, 8):
                    for y in range(85, 375, 8):
                        color = image.pixelColor(x, y)
                        pixels += 1
                        blue += color.blue() > 130 and color.red() < 60 and color.green() < 160
                self.assertLess(blue / pixels, 0.15)


if __name__ == "__main__":
    unittest.main()
