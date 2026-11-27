"""Regression checks for actual 25 m / 5 m rational MDT + Esri-D8 exports."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, osr

from caumax_consulta.rational_grid_validation import (verify_fine_rational_pair,
                                                       verify_rational_climate_crs)
from caumax_consulta.rational_inputs import (_fine_preflight, _safe_secondary_sample,
                                             _configured_pair_hint, _qgis_drainage_pair_hint)


class RationalGridValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='hc_rational_grid_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.srs = osr.SpatialReference()
        self.srs.ImportFromEPSG(25830)
        self.default_x = 355900.
        self.default_y = 4616710.

    def grid(self, name, size, array, *, origin=None, crs=None,
             nodata=None, product=None, encoding=None):
        path = self.root / name
        if origin is None:
            origin = (self.default_x - 2.5 * size, self.default_y + 2.5 * size)
        ds = gdal.GetDriverByName('GTiff').Create(
            str(path), array.shape[1], array.shape[0], 1,
            gdal.GDT_Byte if array.dtype == np.uint8 else gdal.GDT_Float32)
        ds.SetGeoTransform((origin[0], size, 0, origin[1], 0, -size))
        ds.SetProjection((crs or self.srs).ExportToWkt())
        band = ds.GetRasterBand(1)
        band.WriteArray(array)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        if product:
            ds.SetMetadataItem('HIDROCAUMAX_PRODUCT', product)
        if encoding:
            ds.SetMetadataItem('FLOW_DIRECTION_ENCODING', encoding)
        ds = None
        return path

    def pair(self, size, *, prefix=''):
        dem = self.grid(prefix + f'MDT_{size}m.tif', size,
                        np.full((7, 7), 100, dtype=np.float32),
                        nodata=-9999, product='ELEVATION')
        d8 = self.grid(prefix + f'Direccion_D8_caumax_{size}m.tif', size,
                       np.full((7, 7), 1, dtype=np.uint8),
                       nodata=255, product='FLOW_DIRECTION', encoding='ESRI_D8')
        return dem, d8

    def test_good_real_exported_pair_covers_rational_outlet_at_25_and_5m(self):
        for size in (25, 5):
            with self.subTest(size=size):
                dem, d8 = self.pair(size)
                result = verify_fine_rational_pair(
                    dem, d8, self.default_x, self.default_y)
                self.assertEqual(result['resolution_m'], size)
                self.assertEqual((result['col'], result['row']), (2, 2))
                self.assertEqual(result['elevation_m'], 100)
                self.assertEqual(result['direction_codes'], 49)
                self.assertEqual(result['dem_path'], str(dem))

    def test_point_outside_real_mdt_reports_extent_and_original_coordinates(self):
        for size in (25, 5):
            with self.subTest(size=size):
                dem, d8 = self.pair(size)
                with self.assertRaisesRegex(ValueError, 'no está cubierto por el MDT') as result:
                    verify_fine_rational_pair(dem, d8, 355909, 4616712 + 5000)
                self.assertIn('X=355909.00', str(result.exception))
                self.assertIn('Cobertura del TIFF', str(result.exception))

    def test_d8_from_different_basin_rejected_even_same_resolution(self):
        for size in (25, 5):
            with self.subTest(size=size):
                dem, d8 = self.pair(size)
                ds = gdal.Open(str(d8), gdal.GA_Update)
                ds.SetGeoTransform((self.default_x + 4000, size, 0,
                                    self.default_y + 4000, 0, -size))
                ds = None
                with self.assertRaisesRegex(ValueError, 'misma malla'):
                    verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_custom_d8_aligned_but_crs_differs_is_rejected(self):
        dem, d8 = self.pair(25)
        alternative = osr.SpatialReference()
        alternative.ImportFromEPSG(32630)
        ds = gdal.Open(str(d8), gdal.GA_Update)
        ds.SetProjection(alternative.ExportToWkt())
        ds = None
        with self.assertRaisesRegex(ValueError, 'misma malla y CRS'):
            verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_esri_d8_invalid_code_and_native_taudem_label_are_rejected(self):
        for size in (25, 5):
            with self.subTest(size=size):
                dem, d8 = self.pair(size)
                ds = gdal.Open(str(d8), gdal.GA_Update)
                ds.GetRasterBand(1).WriteArray(
                    np.array([[3]], dtype=np.uint8), 1, 1)
                ds = None
                with self.assertRaisesRegex(ValueError, 'código 3'):
                    verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)
                ds = gdal.Open(str(d8), gdal.GA_Update)
                ds.GetRasterBand(1).WriteArray(
                    np.array([[1]], dtype=np.uint8), 1, 1)
                ds.SetMetadataItem('FLOW_DIRECTION_ENCODING', 'TAUDEM_D8')
                ds = None
                with self.assertRaisesRegex(ValueError, 'ESRI_D8'):
                    verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_dem_nodata_invalidates_selected_cell(self):
        dem, d8 = self.pair(25)
        ds = gdal.Open(str(dem), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(
            np.array([[-9999]], dtype=np.float32), 2, 2)
        ds = None
        with self.assertRaisesRegex(ValueError, 'NoData'):
            verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_d8_flow_in_dem_nodata_rejected_even_away_from_outlet(self):
        dem, d8 = self.pair(5)
        ds = gdal.Open(str(dem), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(
            np.array([[-9999]], dtype=np.float32), 0, 0)
        ds = None
        with self.assertRaisesRegex(ValueError, 'celdas NoData del MDT'):
            verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_no_data_at_closure_can_be_a_real_terminal_if_inflow_exists(self):
        dem, d8 = self.pair(25)
        ds = gdal.Open(str(d8), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(np.array([[255]], dtype=np.uint8), 2, 2)
        ds.GetRasterBand(1).WriteArray(np.array([[4]], dtype=np.uint8), 2, 1)
        ds = None
        inspected = verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)
        self.assertEqual(inspected['resolution_m'], 25)
        ds = gdal.Open(str(d8), gdal.GA_Update)
        ds.GetRasterBand(1).WriteArray(np.array([[255]], dtype=np.uint8), 2, 1)
        ds.GetRasterBand(1).WriteArray(np.array([[255]], dtype=np.uint8), 1, 2)
        ds = None
        with self.assertRaisesRegex(ValueError, 'no recibe flujo'):
            verify_fine_rational_pair(dem, d8, self.default_x, self.default_y)

    def test_differing_file_dimensions_are_allowed_when_outlet_is_in_overlap(self):
        for size in (25, 5):
            with self.subTest(size=size):
                dem, d8 = self.pair(size)
                ds = gdal.Open(str(d8), gdal.GA_Update)
                ds = None
                # An independently supplied file can end two cells earlier.
                narrower = self.grid(f'cropped_d8_{size}m.tif', size,
                    np.full((5, 5), 1, dtype=np.uint8),
                    nodata=255, product='FLOW_DIRECTION', encoding='ESRI_D8')
                result = verify_fine_rational_pair(
                    dem, narrower, self.default_x, self.default_y)
                self.assertEqual(result['resolution_m'], size)

    def test_climate_layers_use_same_utm_crs_as_native_rational_mdt(self):
        dem, d8 = self.pair(25)
        good = self.grid('p0_100m.tif', 100, np.full((4, 4), 35, dtype=np.float32))
        self.assertIsNone(verify_rational_climate_crs(dem, [good]))
        # Different resolution is valid, different CRS is not: CAUMAX
        # samples climate values from UTM coordinates without reprojection.
        other = osr.SpatialReference()
        other.ImportFromEPSG(32630)
        wrong = self.grid('rain_wrong_crs.tif', 100,
                          np.full((4, 4), 35, dtype=np.float32), crs=other)
        with self.assertRaisesRegex(ValueError, 'CRS diferente'):
            verify_rational_climate_crs(dem, [good, wrong])

    def test_native_500m_stays_under_caumax_original_behaviour(self):
        dem = self.grid('original-mdt.tif', 500,
                        np.full((5, 5), 120, dtype=np.float32))
        d8 = self.grid('original-d8.tif', 500,
                       np.full((5, 5), 1, dtype=np.uint8))
        self.assertIsNone(verify_fine_rational_pair(
            dem, d8, self.default_x, self.default_y))

    def test_native_dialog_uses_the_real_configured_paths_for_preflight(self):
        for size in (25, 5):
            dem, d8 = self.pair(size, prefix=f'case{size}_')
            label = SimpleNamespace(text=lambda: str(self.default_x))
            ordinate = SimpleNamespace(text=lambda: str(self.default_y))
            dialog = SimpleNamespace(txtX=label, txtY=ordinate)
            result = _fine_preflight(dialog, (str(dem), str(d8)))
            self.assertEqual(result['resolution_m'], size)

    def test_secondary_raster_window_does_not_wrap_negative_indices(self):
        calc = SimpleNamespace(
            secondaryLayers={'P0': np.arange(9, dtype=float).reshape(3, 3)},
            secondaryTransforms={'P0': (100, 100, 0, 500, 0, -100)},
            secondaryNodata={'P0': -9999},
            getValueAtCoordinate=lambda *unused: 9999)
        self.assertIsNone(_safe_secondary_sample(calc, 50, 450, 'P0'))
        self.assertIsNone(_safe_secondary_sample(calc, 200, 550, 'P0'))
        self.assertEqual(_safe_secondary_sample(calc, 150, 450, 'P0'), 0.)
        self.assertEqual(_safe_secondary_sample(calc, 250, 350, 'P0'), 4.)

    def test_spanish_comma_coordinates_are_preflighted_not_silently_skipped(self):
        dem, d8 = self.pair(25)
        dialog = SimpleNamespace(
            txtX=SimpleNamespace(text=lambda: '355.900,00'),
            txtY=SimpleNamespace(text=lambda: '4.616.710,00'))
        verified = _fine_preflight(dialog, (str(dem), str(d8)))
        self.assertEqual(verified['resolution_m'], 25)
        self.assertEqual((verified['col'], verified['row']), (2, 2))
        dialog.txtY = SimpleNamespace(text=lambda: '4.717.759,20')
        with self.assertRaisesRegex(ValueError, 'no está cubierto por el MDT'):
            _fine_preflight(dialog, (str(dem), str(d8)))

    def test_configured_raster_differs_from_displayed_valid_qgis_drainage_pair(self):
        from qgis.core import QgsApplication, QgsProject, QgsRasterLayer
        from caumax_consulta.drainage_layers import OWNER_PROPERTY, ROLE_PROPERTY
        app = QgsApplication.instance() or QgsApplication([], False)
        app.initQgis()
        project = QgsProject.instance()
        dem, d8 = self.pair(25)
        layers = []
        for role, path in (('dem', dem), ('directions', d8)):
            layer = QgsRasterLayer(str(path), role, 'gdal')
            self.assertTrue(layer.isValid())
            layer.setCustomProperty(OWNER_PROPERTY, 'same-rational-result')
            layer.setCustomProperty(ROLE_PROPERTY, role)
            project.addMapLayer(layer)
            layers.append(layer)
        self.addCleanup(lambda: project.removeMapLayers(
            [layer.id() for layer in layers if project.mapLayer(layer.id())]))
        foreign = (str(self.root / 'wrong_mdt.tif'), str(self.root / 'wrong_d8.tif'))
        hint = _qgis_drainage_pair_hint(foreign, self.default_x, self.default_y)
        self.assertIn('Método racional tiene configurados otros archivos', hint)
        self.assertIn(str(dem), hint)
        self.assertIn(str(d8), hint)
        self.assertFalse(_qgis_drainage_pair_hint((str(dem), str(d8)),
                                                  self.default_x, self.default_y))
        self.assertFalse(_qgis_drainage_pair_hint(foreign,
                                                  self.default_x, self.default_y + 100000))

    def test_one_click_recovery_persists_real_25m_dem_d8_and_updates_only_native_sources(self):
        from caumax_consulta.rational_source_recovery import activate_verified_qgis_pair
        from caumax_consulta.rational_grid_validation import verify_fine_rational_pair
        from caumax_consulta.improved_storage import _compatible
        dem, d8 = self.pair(25)
        selected = {'MDT': 'previous-mdt.tif', 'FLOWDIRS': 'previous-d8.tif',
                    'P0': 'original-p0.tif'}
        cached = {'MDT': object(), 'FLOWDIRS': object(), 'P0': object()}
        native = SimpleNamespace(MDT='MDT', FLOWDIRS='FLOWDIRS',
                                 _layerPaths=selected, _layers=cached)
        writes = {}
        class FakeSettings:
            NoError = 0
            def setValue(self, key, value):
                writes[key] = value
            def remove(self, key):
                writes.pop(key, None)
            def sync(self):
                pass
            def status(self):
                return self.NoError
        with patch('caumax_consulta.improved_storage.PLUGIN_ROOT',
                   self.root / 'installed'), patch(
                   'caumax_consulta.rational_source_recovery.QSettings',
                   FakeSettings), patch(
                   'caumax_consulta.improved_storage.record_exports') as recorder:
            new_dem, new_d8 = activate_verified_qgis_pair(
                native, str(dem), str(d8), self.default_x, self.default_y)
            self.assertTrue(Path(new_dem).is_file())
            self.assertTrue(Path(new_d8).is_file())
            self.assertTrue(_compatible(Path(new_dem), Path(new_d8)))
            self.assertEqual(verify_fine_rational_pair(
                new_dem, new_d8, self.default_x, self.default_y)['resolution_m'], 25)
            recorder.assert_called_once()
            self.assertEqual(selected['MDT'], new_dem)
            self.assertEqual(selected['FLOWDIRS'], new_d8)
            self.assertEqual(writes['/caumax/MDT'], new_dem)
            self.assertEqual(writes['/caumax/FLOWDIRS'], new_d8)
            self.assertEqual(selected['P0'], 'original-p0.tif')
            self.assertIn('P0', cached)
            self.assertNotIn('MDT', cached)
            self.assertNotIn('FLOWDIRS', cached)
            before = len(list((self.root / 'installed' / 'ResolucionMejorada').glob('*.tif')))
            with self.assertRaisesRegex(ValueError, 'no está cubierto por el MDT'):
                activate_verified_qgis_pair(native, str(dem), str(d8),
                                            self.default_x, self.default_y + 100000)
            self.assertEqual(before, len(list(
                (self.root / 'installed' / 'ResolucionMejorada').glob('*.tif'))))
            self.assertEqual(selected['MDT'], new_dem)

    def test_existing_matching_export_is_suggested_without_reconfiguring_native_layers(self):
        dem, d8 = self.pair(25)
        foreign = (str(self.root / 'wrong_dem.tif'), str(self.root / 'wrong_d8.tif'))
        with patch('caumax_consulta.improved_storage.exported_pairs', return_value=((dem, d8),)):
            hint = _configured_pair_hint(foreign, self.default_x, self.default_y)
            self.assertIn('Existe una pareja', hint)
            self.assertIn(str(dem), hint)
            self.assertIn(str(d8), hint)
            self.assertEqual(_configured_pair_hint((str(dem), str(d8)),
                                                   self.default_x, self.default_y), '')
            self.assertEqual(_configured_pair_hint(foreign, self.default_x,
                                                   self.default_y + 100000), '')
        self.assertEqual(foreign, (str(self.root / 'wrong_dem.tif'),
                                   str(self.root / 'wrong_d8.tif')))


if __name__ == '__main__':
    unittest.main()
