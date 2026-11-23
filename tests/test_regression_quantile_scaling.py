"""Regression checks for physical flow values in real GDAL raster bands.

Run with a Python environment providing the QGIS and GDAL bindings:
    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -p 'test_regression_quantile_scaling.py'
"""
from pathlib import Path
import tempfile
import unittest

from osgeo import gdal, osr

from caumax_consulta.flow_quantiles import _sample


class QuantileScalingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25830)
        self.wkt = reference.ExportToWkt()
        self.path = str(Path(self.directory.name) / 'flow.tif')

    def sample(self, raw, *, scale=None, offset=None, nodata=None, masked=False):
        dataset = gdal.GetDriverByName('GTiff').Create(
            self.path, 1, 1, 1, gdal.GDT_Float64)
        dataset.SetProjection(self.wkt)
        dataset.SetGeoTransform((500000, 500, 0, 4600000, 0, -500))
        band = dataset.GetRasterBand(1)
        band.Fill(raw)
        if scale is not None:
            band.SetScale(scale)
        if offset is not None:
            band.SetOffset(offset)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        if masked:
            band.CreateMaskBand(gdal.GMF_PER_DATASET)
            band.GetMaskBand().Fill(0)
        dataset.FlushCache()
        band = None
        dataset = None
        return _sample(self.path, 500250, 4599750, self.wkt)

    def test_unscaled_flow_is_preserved(self):
        self.assertEqual(self.sample(120), 120)

    def test_scale_and_offset_are_applied(self):
        self.assertAlmostEqual(self.sample(120, scale=0.1, offset=5), 17)

    def test_explicit_identity_conversion_is_preserved(self):
        self.assertEqual(self.sample(120, scale=1, offset=0), 120)

    def test_negative_stored_value_can_be_a_valid_physical_flow(self):
        self.assertEqual(self.sample(-100, offset=200), 100)

    def test_negative_physical_flow_is_unavailable(self):
        self.assertIsNone(self.sample(10, offset=-20))

    def test_physical_zero_is_valid(self):
        self.assertEqual(self.sample(-10, offset=10), 0)

    def test_nodata_is_checked_before_conversion(self):
        self.assertIsNone(self.sample(-9999, scale=0.1, offset=2000, nodata=-9999))

    def test_nodata_does_not_reject_an_equal_physical_value(self):
        self.assertEqual(self.sample(50, scale=2, nodata=100), 100)

    def test_masked_flow_is_unavailable(self):
        self.assertIsNone(self.sample(120, scale=0.1, offset=5, masked=True))

    def test_nonfinite_scale_is_unavailable(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(scale=value):
                self.assertIsNone(self.sample(120, scale=value))

    def test_nonfinite_offset_is_unavailable(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(offset=value):
                self.assertIsNone(self.sample(120, offset=value))

    def test_nonfinite_stored_flow_is_unavailable(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(raw=value):
                self.assertIsNone(self.sample(value))

    def test_overflowing_physical_flow_is_unavailable(self):
        self.assertIsNone(self.sample(1e308, scale=10))


if __name__ == '__main__':
    unittest.main()
