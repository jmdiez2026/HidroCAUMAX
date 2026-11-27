"""Native rational provenance, unchanged geometry, and the screenshot golden case."""
import ast
from collections import defaultdict
from contextlib import contextmanager
import copy
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import types
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import QgsApplication, QgsGeometry, QgsPointXY, QgsRectangle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from caumax_consulta import rational_basin as rational
from caumax_consulta.terrain import analyze_basin


class Text:
    def __init__(self, value): self.value = str(value)
    def text(self): return self.value
    def currentText(self): return self.value
    def setText(self, value): self.value = str(value)


class Flag:
    def __init__(self, value): self.value = value
    def isEnabled(self): return self.value
    def isChecked(self): return self.value
    def setEnabled(self, value): self.value = bool(value)


def _app():
    app = QgsApplication.instance()
    if app is None:
        app = QgsApplication([], False)
        app.initQgis()
    return app


def _write(path, data, transform, reference, nodata=-9999):
    data = np.asarray(data)
    datatype = gdal.GDT_Float32 if data.dtype.kind == 'f' else gdal.GDT_Int32
    dataset = gdal.GetDriverByName('GTiff').Create(str(path), data.shape[1], data.shape[0], 1, datatype)
    dataset.SetGeoTransform(transform)
    dataset.SetProjection(reference.ExportToWkt())
    dataset.GetRasterBand(1).SetNoDataValue(nodata)
    dataset.GetRasterBand(1).WriteArray(data)
    dataset = None


@contextmanager
def rational_fixture():
    """Reusable real-raster fixture; yields query/analysis/dialog/calc/paths/folder.

    A 7x7 upstream square inside a 9x9 MDT gives 49 cells, 12.25 km², and a
    central closure at (2250,2250). All artifacts and module aliases are owned
    by this context and removed on exit. No native settings are modified.
    """
    app = _app()
    # QGIS's OGR connection pool can retain a Windows GeoPackage handle until
    # process exit, even after the test has removed/deleted its map layer.
    with tempfile.TemporaryDirectory(prefix='rational_basin_', ignore_cleanup_errors=True) as temporary:
        folder = Path(temporary)
        paths = {'MDT': folder / 'mdt.tif', 'FLOWDIRS': folder / 'dir.tif'}
        transform = (0, 500, 0, 4500, 0, -500)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25830)
        reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        rows, cols = np.indices((9, 9))
        dem = (100 + (abs(rows - 4) + abs(cols - 4)) * 10).astype(np.float32)
        directions = np.zeros((9, 9), dtype=np.int32)
        codes = {(1, 0): 1, (1, 1): 2, (0, 1): 4, (-1, 1): 8,
                 (-1, 0): 16, (-1, -1): 32, (0, -1): 64, (1, -1): 128}
        for row in range(1, 8):
            for col in range(1, 8):
                if (row, col) != (4, 4):
                    directions[row, col] = codes[(int(np.sign(4 - col)), int(np.sign(4 - row)))]
        _write(paths['MDT'], dem, transform, reference)
        _write(paths['FLOWDIRS'], directions, transform, reference)
        analysis = analyze_basin(paths['FLOWDIRS'], paths['MDT'], 2250, 2250, upstream_only=False)
        geometry = QgsGeometry()
        geometry.fromWkb(analysis.geometry_wkb)
        native_geometries = geometry.asGeometryCollection()
        # Reproduce the extra background polygon native GDAL Polygonize emits.
        native_geometries.append(QgsGeometry.fromRect(QgsRectangle(0, 0, 4500, 4500)).difference(geometry))
        name = '_test_rational_basin_' + folder.name
        module = types.ModuleType(name)
        module.MDT = 'MDT'; module.FLOWDIRS = 'FLOWDIRS'
        module.getLayerPath = lambda key: str(paths[key])
        Calculator = type('BasinCalculator', (), {'__module__': name})
        calculator = Calculator()
        calculator.pt = QgsPointXY(2250, 2250)
        calculator.mdt = dem; calculator.dirs = directions
        calculator.basinCells = np.zeros((9, 9), dtype=np.float64)
        calculator.basinCells[1:8, 1:8] = 1
        calculator.basinGeometry = native_geometries
        calculator.geoTransform = transform; calculator.crs = reference.ExportToWkt()
        calculator.nodataMdt = -9999; calculator.nodataDirs = -9999
        calculator.area = 12_250_000.0
        calculator.concentrationTime = 2.5; calculator.maxDistance = 3000
        calculator.minH = 100; calculator.maxH = 160
        calculator.p0 = 12; calculator.i1id = 9
        calculator.rain = {period: float(40 + index * 10) for index, period in enumerate((2, 5, 10, 25, 100, 500))}
        dialog = types.SimpleNamespace(
            basinCalc=calculator, btnCalculate=Flag(True),
            txtX=Text(2250), txtY=Text(2250), comboReturnPeriod=Text(100),
            chkOrdinaryMaxFlood=Flag(False), txtResultFlow=Text(999),
            txtResultReturnPeriod=Text(100),
        )
        sys.modules[name] = module
        try:
            query = rational.capture_rational_query(dialog)
            yield types.SimpleNamespace(app=app, query=query, analysis=analysis, dialog=dialog,
                                        calc=calculator, paths=paths, folder=folder, module=module,
                                        transform=transform, reference=reference)
        finally:
            sys.modules.pop(name, None)


class RationalBasinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = _app()

    def setUp(self):
        self.context = rational_fixture()
        self.fixture = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_completed_native_basin_without_flow_is_captured_and_validated(self):
        fixture = self.fixture; query = fixture.query
        self.assertEqual(query.source_kind, 'rational')
        self.assertIsNone(query.flow)  # A stale 999 in the native field is ignored.
        self.assertEqual(query.period, 100)
        self.assertEqual((query.cell.row, query.cell.col), (4, 4))
        context = query.rational_context
        self.assertEqual(context['cell_count'], 49)
        self.assertEqual(context['window'], [1, 1, 7, 7])
        self.assertEqual(context['area_m2'], 12_250_000)
        self.assertEqual(context['flow_status'], 'pending')
        self.assertEqual(rational.validate_rational_context(json.loads(json.dumps(context))), context)
        self.assertIs(rational.validate_rational_analysis(fixture.analysis, query), fixture.analysis)
        self.assertLess(len(json.dumps(context)), 6000)
        self.assertNotIn('basinCells', json.dumps(context))

    def test_explicit_flow_and_decimal_mco_period(self):
        fixture = self.fixture
        query = rational.capture_rational_query(fixture.dialog, flow_result={'flow': 17, 'period': 100})
        self.assertEqual(query.rational_context['flow_result'], {'flow': 17, 'period': 100})
        self.assertEqual(query.rational_context['fingerprint'], fixture.query.rational_context['fingerprint'])
        fixture.dialog.chkOrdinaryMaxFlood.value = True
        query = rational.capture_rational_query(fixture.dialog, flow_result={'flow': 3.5, 'period': 3.2})
        self.assertEqual(query.period, 3.2)
        self.assertEqual(query.flow, 3.5)
        self.assertIsNone(rational.capture_rational_query(fixture.dialog).period)
        with self.assertRaises(ValueError):
            rational.capture_rational_query(fixture.dialog, flow_result={'flow': -1, 'period': 100})

    def test_unfinished_or_edited_point_is_not_a_current_basin(self):
        fixture = self.fixture
        fixture.dialog.btnCalculate.value = False
        with self.assertRaisesRegex(ValueError, 'variables intermedias'):
            rational.capture_rational_query(fixture.dialog)
        # The cooperative bridge may retain a completed basin across T edits.
        fixture.dialog.comboReturnPeriod.value = '25'
        self.assertEqual(rational.capture_rational_query(fixture.dialog, parameters_validated=True).period, 25)
        fixture.dialog.txtX.value = '2251'
        with self.assertRaisesRegex(ValueError, 'coordenadas'):
            rational.capture_rational_query(fixture.dialog, parameters_validated=True)

    def test_source_content_change_outside_basin_cannot_reuse_cached_arrays(self):
        fixture = self.fixture
        for key, array in (('MDT', fixture.calc.mdt), ('FLOWDIRS', fixture.calc.dirs)):
            with self.subTest(layer=key):
                changed = array.copy(); changed[0, 0] += 1
                path = fixture.folder / (key + '_changed.tif')
                _write(path, changed, fixture.transform, fixture.reference)
                old_path = fixture.paths[key]; fixture.paths[key] = path
                try:
                    with self.assertRaisesRegex(ValueError, 'no corresponden'):
                        rational.capture_rational_query(fixture.dialog)
                finally:
                    fixture.paths[key] = old_path

    def test_same_array_with_changed_georeferencing_is_rejected(self):
        fixture = self.fixture
        path = fixture.folder / 'shifted.tif'
        _write(path, fixture.calc.mdt, (500, 500, 0, 4500, 0, -500), fixture.reference)
        fixture.paths['MDT'] = path
        with self.assertRaisesRegex(ValueError, 'malla'):
            rational.capture_rational_query(fixture.dialog)

    def test_background_and_nonbinary_masks_cannot_become_the_basin(self):
        fixture = self.fixture
        fixture.calc.basinCells[0, 0] = 2
        with self.assertRaisesRegex(ValueError, '0 y 1'):
            rational.capture_rational_query(fixture.dialog)
        fixture.calc.basinCells[0, 0] = 0
        fixture.calc.basinCells = 1 - fixture.calc.basinCells
        with self.assertRaisesRegex(ValueError, 'área nativa'):
            rational.capture_rational_query(fixture.dialog)

    def test_only_native_background_geometry_is_rejected(self):
        fixture = self.fixture
        fixture.calc.basinGeometry = fixture.calc.basinGeometry[-1:]
        with self.assertRaisesRegex(ValueError, 'divisoria'):
            rational.capture_rational_query(fixture.dialog)

    def test_exactly_fifty_square_kilometres_is_allowed_and_above_is_rejected(self):
        fixture = self.fixture; calc = fixture.calc
        calc.mdt = np.full((16, 24), 150, dtype=np.float32)
        calc.dirs = np.zeros((16, 24), dtype=np.int32)
        calc.basinCells = np.zeros((16, 24), dtype=np.float64)
        calc.basinCells[1:11, 1:21] = 1
        codes = {(1, 0): 1, (1, 1): 2, (0, 1): 4, (-1, 1): 8,
                 (-1, 0): 16, (-1, -1): 32, (0, -1): 64, (1, -1): 128}
        for row in range(1, 11):
            for col in range(1, 21):
                calc.mdt[row, col] = 100 + abs(row - 4) + abs(col - 4)
                if (row, col) != (4, 4):
                    calc.dirs[row, col] = codes[(int(np.sign(4 - col)), int(np.sign(4 - row)))]
        calc.area = 50_000_000
        calc.basinGeometry = [QgsGeometry.fromRect(QgsRectangle(500, -1000, 10500, 4000))]
        _write(fixture.paths['MDT'], calc.mdt, fixture.transform, fixture.reference)
        _write(fixture.paths['FLOWDIRS'], calc.dirs, fixture.transform, fixture.reference)
        query = rational.capture_rational_query(fixture.dialog)
        self.assertEqual(query.rational_context['area_m2'], 50_000_000)
        self.assertEqual(query.rational_context['cell_count'], 200)
        analysis = analyze_basin(query.directions_path, query.dem_path, query.x, query.y, upstream_only=False)
        self.assertIs(rational.validate_rational_analysis(analysis, query), analysis)
        calc.area += 250000
        with self.assertRaisesRegex(ValueError, 'límites'):
            rational.capture_rational_query(fixture.dialog)

    def test_changed_area_closure_and_geometry_are_rejected_before_display(self):
        fixture = self.fixture
        changes = (
            {'area_m2': fixture.analysis.area_m2 + 250000},
            {'cell_count': 48}, {'outlet_x': 2750},
        )
        for change in changes:
            with self.subTest(change=change):
                altered = replace(fixture.analysis, basin=replace(fixture.analysis.basin, **change))
                with self.assertRaises(ValueError):
                    rational.validate_rational_analysis(altered, fixture.query)
        geometry = ogr.CreateGeometryFromWkb(fixture.analysis.geometry_wkb)
        # Same area and cell count, different footprint must still fail.
        polygon = geometry.GetGeometryRef(0); ring = polygon.GetGeometryRef(0)
        for index in range(ring.GetPointCount()):
            x, y, *_ = ring.GetPoint(index); ring.SetPoint_2D(index, x + 1, y)
        altered = replace(fixture.analysis, basin=replace(fixture.analysis.basin, geometry_wkb=bytes(geometry.ExportToWkb())))
        with self.assertRaisesRegex(ValueError, 'geometría'):
            rational.validate_rational_analysis(altered, fixture.query)

    def test_files_changed_during_analysis_are_rejected(self):
        fixture = self.fixture
        changed = fixture.calc.mdt.copy(); changed[0, 0] += 2
        _write(fixture.paths['MDT'], changed, fixture.transform, fixture.reference)
        with self.assertRaisesRegex(ValueError, 'capas han cambiado'):
            rational.validate_rational_analysis(fixture.analysis, fixture.query)

    def test_nodata_metadata_change_during_analysis_is_rejected(self):
        fixture = self.fixture
        dataset = gdal.Open(str(fixture.paths['MDT']), gdal.GA_Update)
        dataset.GetRasterBand(1).SetNoDataValue(100)
        dataset = None
        with self.assertRaisesRegex(ValueError, 'capas han cambiado'):
            rational.validate_rational_analysis(fixture.analysis, fixture.query)

    def test_declared_context_requires_bounded_known_json_and_coherent_hash(self):
        context = self.fixture.query.rational_context
        bad_contexts = []
        for key, value in (('version', 2), ('fingerprint', 'abc'), ('mask_sha256', 'A' * 64),
                           ('area_m2', 50_000_001), ('unknown', []), ('native', {'array': np.zeros(3)})):
            candidate = copy.deepcopy(context); candidate[key] = value; bad_contexts.append(candidate)
        candidate = copy.deepcopy(context); candidate['sources']['dem']['path'] = 'x' * 4100; bad_contexts.append(candidate)
        candidate = copy.deepcopy(context); candidate['native']['p0_mm'] = float('nan'); bad_contexts.append(candidate)
        candidate = copy.deepcopy(context); candidate['outlet']['row'] = True; bad_contexts.append(candidate)
        candidate = copy.deepcopy(context); candidate['flow_result'] = {'flow': 3, 'period': 100}; bad_contexts.append(candidate)
        for index, candidate in enumerate(bad_contexts):
            with self.subTest(case=index), self.assertRaises(ValueError):
                rational.validate_rational_context(candidate)

    def test_context_validation_is_offline_and_flow_must_match_query(self):
        fixture = self.fixture
        context = copy.deepcopy(fixture.query.rational_context)
        offline = str(fixture.folder / 'not-installed' / 'mdt.tif')
        context['sources']['dem']['path'] = offline
        self.assertEqual(rational.validate_rational_context(context)['sources']['dem']['path'], offline)
        with self.assertRaisesRegex(ValueError, 'caudal'):
            rational.validate_rational_analysis(fixture.analysis, replace(fixture.query, flow=10))


class NativeScreenshotGoldenTest(unittest.TestCase):
    def test_native_original_data_matches_user_screenshot_and_analysis(self):
        candidates = [
            Path(os.environ.get('HIDROCAUMAX_CAUMAX_SOURCE', '/not-configured')),
            ROOT.parent / 'inputs/caumax/caumax',
        ]
        source = next((path for path in candidates if (path / 'basincalculator.py').is_file()
                       and (path / 'data/mdt.tif').is_file()), None)
        if source is None:
            self.skipTest('Fuentes y datos originales de CAUMAX no disponibles')
        app = _app()
        name = '_native_rational_golden'
        module = types.ModuleType(name)
        files = {'MDT': 'mdt.tif', 'FLOWDIRS': 'dir.tif', 'P0': 'p0.tif', 'I1ID': 'i1id.tif'}
        files.update({f'RAIN_{period}': f't{period}.tif' for period in (2, 5, 10, 25, 100, 500)})
        module.__dict__.update({key: key for key in files})
        module.__dict__.update(gdal=gdal, ogr=ogr, np=np, math=math, statistics=statistics,
                               defaultdict=defaultdict, QgsGeometry=QgsGeometry,
                               getLayerPath=lambda key: str(source / 'data' / files[key]))
        tree = ast.parse((source / 'basincalculator.py').read_text(encoding='utf-8-sig'))
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'BasinCalculator')
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source / 'basincalculator.py'), 'exec'), module.__dict__)
        sys.modules[name] = module
        try:
            calc = module.BasinCalculator(); calc.calculate(QgsPointXY(346034.4, 4717685.7))
            dialog = types.SimpleNamespace(basinCalc=calc, btnCalculate=Flag(True), txtX=Text(346034.4),
                                           txtY=Text(4717685.7), comboReturnPeriod=Text(100), chkOrdinaryMaxFlood=Flag(False))
            query = rational.capture_rational_query(dialog)
            analysis = analyze_basin(query.directions_path, query.dem_path, query.x, query.y, upstream_only=False)
            self.assertIs(rational.validate_rational_analysis(analysis, query), analysis)
            self.assertEqual(analysis.cell_count, 59)
            self.assertEqual(analysis.area_m2, 14_750_000)
            self.assertEqual(analysis.perimeter_m, 33000)
            self.assertEqual((query.cell.row, query.cell.col), (286, 747))
            self.assertEqual((analysis.outlet_x, analysis.outlet_y), (345825, 4717700))
            self.assertAlmostEqual(query.rational_context['native']['tc_h'], 5.305793137101772)
            self.assertAlmostEqual(query.rational_context['native']['rainfall_mm']['100'], 92.10169491525424)
            self.assertEqual(query.rational_context['native']['outlet_elevation_m'], 1032)
            self.assertEqual(analysis.z_min, 1023)
        finally:
            sys.modules.pop(name, None)


if __name__ == '__main__':
    unittest.main()
