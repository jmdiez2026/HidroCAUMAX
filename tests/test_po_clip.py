"""PyQGIS/GDAL regression checks for P₀ mask cutting and export placement."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.core import QgsApplication
from qgis.PyQt.QtTest import QSignalSpy
from caumax_consulta.po_clip import clip_po_to_basin, export_po_clip, EXPORT_FILENAME
from caumax_consulta.basin_hydrology_widget import BasinHydrologyWidget


class PoClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25830)
        self.wkt = srs.ExportToWkt()
        self.source = self.folder / 'source_Po.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(self.source), 6, 6, 1, gdal.GDT_Float32)
        ds.SetProjection(self.wkt)
        ds.SetGeoTransform((500000., 100., 0., 4700000., 0., -100.))
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999.)
        band.WriteArray(np.arange(36, dtype=np.float32).reshape(6, 6) + 1)
        ds = None
        # The L-shape covers a 3x3 pixel window but leaves one corner outside.
        ring = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in (
            (500100., 4699900.), (500400., 4699900.),
            (500400., 4699700.), (500300., 4699700.),
            (500300., 4699600.), (500100., 4699600.),
            (500100., 4699900.),
        ):
            ring.AddPoint_2D(x, y)
        polygon = ogr.Geometry(ogr.wkbPolygon)
        polygon.AddGeometry(ring)
        self.geometry = bytes(polygon.ExportToWkb())

    def test_native_grid_cut_has_no_background_values_and_does_not_change_source(self):
        before = self.source.read_bytes()
        destination = self.folder / 'po_clip.tif'
        output = clip_po_to_basin(self.source, self.geometry, self.wkt, destination)
        self.assertEqual(output, destination)
        self.assertEqual(self.source.read_bytes(), before)
        original = gdal.Open(str(self.source))
        clipped = gdal.Open(str(output))
        self.assertEqual((clipped.RasterXSize, clipped.RasterYSize), (3, 3))
        self.assertEqual(clipped.GetGeoTransform(),
                         (500100., 100., 0., 4699900., 0., -100.))
        self.assertEqual(clipped.GetRasterBand(1).GetNoDataValue(), -9999.)
        pixels = clipped.ReadAsArray()
        self.assertEqual(pixels.shape, (3, 3))
        self.assertEqual(pixels[0, 0], original.ReadAsArray()[1, 1])
        self.assertEqual(pixels[0, 2], original.ReadAsArray()[1, 3])
        self.assertEqual(pixels[2, 0], -9999.)
        self.assertNotEqual(pixels[2, 2], -9999.)
        clipped = original = None
        with self.assertRaisesRegex(ValueError, 'existe'):
            clip_po_to_basin(self.source, self.geometry, self.wkt, destination)

    def test_export_is_exact_tiff_copy_without_overwriting(self):
        clipped = self.folder / 'temporary.tif'
        clip_po_to_basin(self.source, self.geometry, self.wkt, clipped)
        saved = self.folder / EXPORT_FILENAME
        self.assertEqual(EXPORT_FILENAME, 'CONSULTA_Umbral Po Cuenca.tif')
        self.assertEqual(export_po_clip(clipped, saved), saved)
        self.assertEqual(saved.read_bytes(), clipped.read_bytes())
        with self.assertRaisesRegex(ValueError, 'existe'):
            export_po_clip(clipped, saved)

    def test_mask_cancel_does_not_publish_partial_tiff(self):
        destination = self.folder / 'cancelled.tif'
        with self.assertRaises(InterruptedError):
            clip_po_to_basin(self.source, self.geometry, self.wkt, destination,
                             cancelled=lambda: True)
        self.assertFalse(destination.exists())

    def test_selected_model_two_buttons_are_in_p0_group(self):
        widget = BasinHydrologyWidget()
        self.addCleanup(widget.close)
        widget.show()
        self.app.processEvents()
        self.assertIs(widget.po_clip_button.parentWidget(), widget.p0_group)
        self.assertIs(widget.po_export_button.parentWidget(), widget.p0_group)
        self.assertFalse(widget.po_clip_button.icon().isNull())
        self.assertFalse(widget.po_export_button.icon().isNull())
        self.assertFalse(widget.po_export_button.isEnabled())
        signals = QSignalSpy(widget.po_export_requested)
        widget.set_clip_export_available(True)
        widget.po_export_button.click()
        self.assertEqual(len(signals), 1)


if __name__ == '__main__':
    unittest.main()
