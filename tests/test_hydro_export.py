"""Exact native product copies, eight-direction encoding and exclusive writes."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal, ogr, osr

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta import hydro_export as export


class HydroExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hydro_export_')
        self.folder = Path(self.temp.name)
        self.source_folder = self.folder / 'source'
        self.source_folder.mkdir()
        self.output = self.folder / 'output'
        self.output.mkdir()
        self.transform = (500000., 25., 0., 4700000., 0., -25.)
        self.reference = osr.SpatialReference()
        self.reference.ImportFromEPSG(25830)
        self.native = np.array([[8, 7, 6], [1, -32768, 5], [2, 3, 4]], dtype=np.int16)
        self.esri = np.array([[2, 4, 8], [1, 255, 16], [128, 64, 32]], dtype=np.uint8)
        self.dem = np.array([[120, 130, 140], [110, -9999, 100], [90, 80, 70]], dtype=np.float32)
        self.products = SimpleNamespace(
            dem_path=self.raster('dem', self.dem, gdal.GDT_Float32, -9999),
            tau_d8_path=self.raster('tau', self.native, gdal.GDT_Int16, -32768),
            caumax_d8_path=self.raster('esri', self.esri, gdal.GDT_Byte, 255),
            metadata={'engine': 'TauDEM', 'version': '5.3.9', 'sha256': 'a'*64,
                      'provenance': 'real D8FlowDir; PitRemove; StreamNet'},
            threshold_cells=10, threshold_area_m2=6250.,
            network_path=self.network(),
        )
        self.before = self.hashes()

    def tearDown(self):
        self.temp.cleanup()

    def raster(self, name, data, kind, nodata):
        path = self.source_folder / (name + '.tif')
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 3, 3, 1, kind)
        dataset.SetGeoTransform(self.transform)
        dataset.SetProjection(self.reference.ExportToWkt())
        dataset.SetMetadataItem('ORIGINAL_SOURCE', 'active raster')
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.SetMetadataItem('SOURCE_NOTE', 'preserved band metadata')
        if name == 'dem':
            band.SetUnitType('m')
        band.WriteArray(data)
        dataset = None
        return str(path)

    def network(self):
        path = self.source_folder / 'network.shp'
        dataset = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(path))
        layer = dataset.CreateLayer('network', self.reference, ogr.wkbLineString,
                                    options=['ENCODING=UTF-8'])
        layer.CreateField(ogr.FieldDefn('LINKNO', ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn('DSLINKNO', ogr.OFTInteger))
        field = ogr.FieldDefn('Length', ogr.OFTReal)
        field.SetWidth(24)
        field.SetPrecision(10)
        layer.CreateField(field)
        for index in range(2):
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetField('LINKNO', index+1)
            feature.SetField('DSLINKNO', -1)
            feature.SetField('Length', 25.)
            geometry = ogr.CreateGeometryFromWkt(
                f'LINESTRING ({500000+index*25} 4700000,{500025+index*25} 4700000)')
            feature.SetGeometry(geometry)
            layer.CreateFeature(feature)
        dataset = None
        return str(path)

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.source_folder.iterdir() if p.is_file()}

    def read(self, path):
        dataset = gdal.Open(str(path))
        result = (dataset.ReadAsArray(), dataset.GetGeoTransform(), dataset.GetProjectionRef(),
                  dataset.GetRasterBand(1).GetNoDataValue(), dataset.GetMetadata(),
                  dataset.GetRasterBand(1).DataType, dataset.GetRasterBand(1).GetMetadata(),
                  dataset.GetDriver().ShortName)
        dataset = None
        return result

    def test_dem_is_original_geotiff_with_exact_grid_nodata_and_metadata(self):
        target = self.output/'chosen_dem.tif'
        self.assertEqual(export.export_dem(self.products,target),target)
        data, transform, projection, nodata, metadata, kind, band_metadata, driver = self.read(target)
        np.testing.assert_array_equal(data,self.dem)
        self.assertEqual(transform,self.transform)
        reference = osr.SpatialReference()
        reference.ImportFromWkt(projection)
        self.assertTrue(reference.IsSame(self.reference))
        self.assertEqual((nodata,kind,driver),(-9999,gdal.GDT_Float32,'GTiff'))
        self.assertEqual(metadata['HIDROCAUMAX_PRODUCT'],'ELEVATION')
        self.assertEqual(metadata['HIDROCAUMAX_ENGINE'],'TauDEM')
        self.assertEqual(metadata['ORIGINAL_SOURCE'],'active raster')
        self.assertIn('D8FlowDir',metadata['HIDROCAUMAX_PROVENANCE'])
        self.assertEqual(band_metadata['SOURCE_NOTE'],'preserved band metadata')
        self.assertEqual(self.hashes(),self.before)

    def test_all_eight_directions_have_distinct_exact_encodings_and_native_nodata(self):
        for encoding,expected,nodata,kind,tag in (
                ('caumax',self.esri,255,gdal.GDT_Byte,'ESRI_D8'),
                ('taudem',self.native,-32768,gdal.GDT_Int16,'TAUDEM_D8')):
            with self.subTest(encoding=encoding):
                target = export.export_directions(self.products,self.output/(encoding+'.tif'),encoding)
                data, transform, _, actual_nodata, metadata, actual_kind, band_metadata, driver = self.read(target)
                np.testing.assert_array_equal(data,expected)
                self.assertEqual(transform,self.transform)
                self.assertEqual((actual_nodata,actual_kind,driver),(nodata,kind,'GTiff'))
                self.assertEqual(metadata['HIDROCAUMAX_PRODUCT'],'FLOW_DIRECTION')
                self.assertEqual(metadata['FLOW_DIRECTION_ENCODING'],tag)
                self.assertEqual(metadata['HIDROCAUMAX_ENGINE'],'TauDEM')
                self.assertEqual(band_metadata['SOURCE_NOTE'],'preserved band metadata')
        self.assertEqual(self.hashes(),self.before)

    def test_default_plugin_directory_contains_a_reopenable_direction_tiff(self):
        from caumax_consulta import improved_storage
        with patch.object(improved_storage, 'PLUGIN_ROOT', self.folder / 'plugin'):
            folder = improved_storage.export_directory()
            target = folder / 'Direccion_D8_caumax_25m.tif'
            saved = export.export_directions(self.products, target)
            self.assertEqual(saved, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            raster = gdal.Open(str(target), gdal.GA_ReadOnly)
            self.assertIsNotNone(raster)
            self.assertEqual(raster.GetDriver().ShortName, 'GTiff')
            self.assertEqual(raster.GetMetadataItem('FLOW_DIRECTION_ENCODING'),
                             'ESRI_D8')
            raster = None

    def test_original_dem_nodata_outside_native_tau_d8_does_not_block_export(self):
        # PitRemove may assign a direction to an edge cell which was NoData
        # in the *original* elevation raster. The CAUMAX recode correctly
        # masks it out; both TIFFs must not be required to have identical
        # validity beyond the original MDT footprint.
        raster = gdal.Open(self.products.tau_d8_path, gdal.GA_Update)
        native = self.native.copy()
        native[1, 1] = 1
        raster.GetRasterBand(1).WriteArray(native)
        raster = None
        target = self.output / 'Direccion_D8_caumax_25m.tif'
        saved = export.export_directions(self.products, target, encoding='caumax')
        self.assertTrue(saved.is_file())
        self.assertGreater(saved.stat().st_size, 0)
        data, _, _, _, metadata, _, _, driver = self.read(saved)
        np.testing.assert_array_equal(data, self.esri)
        self.assertEqual(driver, 'GTiff')
        self.assertEqual(metadata['FLOW_DIRECTION_ENCODING'], 'ESRI_D8')

    def test_default_directions_are_caummx_esri_and_source_is_not_overwritten(self):
        target = export.export_directions(self.products,self.output/'default.tif')
        np.testing.assert_array_equal(self.read(target)[0],self.esri)
        with self.assertRaisesRegex(ValueError,'existe'):
            export.export_directions(self.products,self.products.caumax_d8_path)
        self.assertEqual(self.hashes(),self.before)

    def test_alignment_encoding_and_provenance_errors_publish_nothing(self):
        target = self.output/'invalid.tif'
        dataset = gdal.Open(self.products.caumax_d8_path,gdal.GA_Update)
        dataset.SetGeoTransform((500001.,25.,0.,4700000.,0.,-25.))
        dataset = None
        with self.assertRaisesRegex(ValueError,'malla'):
            export.export_directions(self.products,target)
        dataset = gdal.Open(self.products.caumax_d8_path,gdal.GA_Update)
        dataset.SetGeoTransform(self.transform)
        invalid = self.esri.copy()
        invalid[0,0] = 128
        dataset.GetRasterBand(1).WriteArray(invalid)
        dataset = None
        with self.assertRaisesRegex(ValueError,'ocho direcciones'):
            export.export_directions(self.products,target)
        self.products.metadata = {'engine':'GRASS'}
        with self.assertRaisesRegex(ValueError,'TauDEM'):
            export.export_dem(self.products,target)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_native_encoding_can_keep_other_crs_but_caummx_refuses_reprojection(self):
        reference = osr.SpatialReference()
        reference.ImportFromEPSG(25831)
        for path in (self.products.dem_path,self.products.tau_d8_path,self.products.caumax_d8_path):
            dataset = gdal.Open(path,gdal.GA_Update)
            dataset.SetProjection(reference.ExportToWkt())
            dataset = None
        with self.assertRaisesRegex(ValueError,'EPSG:25830'):
            export.export_directions(self.products,self.output/'caumax.tif')
        target = export.export_directions(self.products,self.output/'native.tif','taudem')
        projection = osr.SpatialReference()
        projection.ImportFromWkt(self.read(target)[2])
        self.assertTrue(projection.IsSame(reference))

    def test_sidecars_existing_targets_and_racing_writes_are_never_overwritten(self):
        sidecar = self.output/'existing.tif.aux.xml'
        sidecar.write_bytes(b'keep sidecar')
        with self.assertRaisesRegex(ValueError,'auxiliares'):
            export.export_dem(self.products,self.output/'existing.tif')
        target = self.output/'racing.tif'
        real_link = os.link
        def racing_link(source,destination):
            if Path(destination) == target:
                target.write_bytes(b'another process')
            return real_link(source,destination)
        with patch.object(export.os,'link',side_effect=racing_link):
            with self.assertRaisesRegex(ValueError,'sobrescribir'):
                export.export_dem(self.products,target)
        self.assertEqual(target.read_bytes(),b'another process')
        self.assertEqual(sidecar.read_bytes(),b'keep sidecar')
        self.assertFalse(any(p.is_dir() for p in self.output.iterdir()))

    def test_failed_integrity_check_leaves_no_partial_export(self):
        with patch.object(export,'_check_copy',side_effect=ValueError('integrity failure')):
            with self.assertRaisesRegex(ValueError,'integrity'):
                export.export_dem(self.products,self.output/'failed.tif')
        self.assertEqual(list(self.output.iterdir()),[])
        self.assertEqual(self.hashes(),self.before)

    def test_network_shapefile_preserves_taudem_fields_and_real_geometries(self):
        target = export.export_network(self.products,self.output/'chosen_network.shp')
        for suffix in ('.shp','.shx','.dbf','.prj','.cpg'):
            self.assertTrue(target.with_suffix(suffix).is_file())
        source, copied = ogr.Open(self.products.network_path), ogr.Open(str(target))
        export._check_network(source,copied)
        self.assertEqual(copied.GetDriver().GetName(),'ESRI Shapefile')
        self.assertEqual(copied.GetLayer(0).GetFeatureCount(),2)
        self.assertEqual([f.GetName() for f in copied.GetLayer(0).schema],
                         ['LINKNO','DSLINKNO','Length'])
        source = copied = None
        self.assertEqual(self.hashes(),self.before)

    def test_network_checks_all_sidecars_and_rolls_back_only_its_own_links(self):
        sidecar = self.output/'blocked.DBF'
        sidecar.write_bytes(b'keep existing')
        with self.assertRaisesRegex(ValueError,'existe'):
            export.export_network(self.products,self.output/'blocked.shp')
        target = self.output/'race.shp'
        real_link = os.link
        count = [0]
        def fail_second(source,destination):
            count[0] += 1
            if count[0] == 2:
                raise OSError('disk unavailable')
            return real_link(source,destination)
        with patch.object(export.os,'link',side_effect=fail_second):
            with self.assertRaisesRegex(ValueError,'publicar'):
                export.export_network(self.products,target)
        self.assertEqual(list(self.output.iterdir()),[sidecar])
        self.assertEqual(sidecar.read_bytes(),b'keep existing')

    def test_mask_is_preserved_and_mismatched_direction_masks_are_rejected(self):
        dataset = gdal.Open(self.products.dem_path,gdal.GA_Update)
        dataset.GetRasterBand(1).CreateMaskBand(gdal.GMF_PER_DATASET)
        mask = np.full((3,3),255,dtype=np.uint8)
        mask[0,0] = 0
        dataset.GetRasterBand(1).GetMaskBand().WriteArray(mask)
        dataset = None
        export.export_dem(self.products,self.output/'masked.tif')
        dataset = gdal.Open(self.products.tau_d8_path,gdal.GA_Update)
        data = self.native.copy()
        data[0,0] = -32768
        dataset.GetRasterBand(1).WriteArray(data)
        dataset = None
        with self.assertRaisesRegex(ValueError,'máscara'):
            export.export_directions(self.products,self.output/'bad_mask.tif')

    def test_missing_source_and_invalid_extension_do_not_invent_products(self):
        for destination in (self.output/'wrong.shp',self.output/'missing'/'a.tif'):
            with self.assertRaises(ValueError):
                export.export_dem(self.products,destination)
        self.products.network_path = str(self.folder/'gone.shp')
        with self.assertRaisesRegex(ValueError,'no está disponible'):
            export.export_network(self.products,self.output/'missing.shp')
        self.assertEqual(list(self.output.iterdir()),[])

    def test_offline_snapshot_exports_real_geometry_and_original_taudem_attributes(self):
        from qgis.core import QgsApplication
        from caumax_consulta.drainage_layers import read_network_snapshot
        app = QgsApplication.instance() or QgsApplication([],False)
        app.initQgis()
        snapshot = read_network_snapshot(self.products)
        offline = SimpleNamespace(network_path=str(self.folder/'not_available.shp'))
        target = export.export_network(offline,self.output/'offline.shp',snapshot=snapshot)
        source, copied = ogr.Open(self.products.network_path), ogr.Open(str(target))
        export._check_network(source,copied)
        source = copied = None
        self.assertEqual(self.hashes(),self.before)

    def test_optional_project_layers_are_valid_and_network_has_light_blue_style(self):
        from qgis.PyQt import sip
        from qgis.core import QgsApplication, QgsProject
        app = QgsApplication.instance() or QgsApplication([],False)
        app.initQgis()
        project = QgsProject.instance()
        before = set(project.mapLayers())
        try:
            export.export_dem(self.products,self.output/'added_dem.tif',True)
            export.export_network(self.products,self.output/'added_network.shp',True)
            added = [layer for key,layer in project.mapLayers().items() if key not in before]
            self.assertEqual(len(added),2)
            network = next(layer for layer in added if layer.providerType()=='ogr')
            self.assertEqual(network.renderer().symbol().color().name(),'#84c8ea')
            self.assertTrue(all(layer.isValid() for layer in added))
        finally:
            for layer_id in set(project.mapLayers())-before:
                layer = project.takeMapLayer(project.mapLayer(layer_id))
                sip.delete(layer)

    def test_live_and_offline_network_preserve_z_values_and_native_attributes(self):
        from qgis.core import QgsApplication
        from caumax_consulta.drainage_layers import read_network_snapshot
        app = QgsApplication.instance() or QgsApplication([],False)
        app.initQgis()
        path = self.source_folder/'network_z.shp'
        dataset = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(path))
        layer = dataset.CreateLayer('network_z',self.reference,ogr.wkbLineString25D)
        layer.CreateField(ogr.FieldDefn('LINKNO',ogr.OFTInteger))
        field = ogr.FieldDefn('Length',ogr.OFTReal)
        field.SetWidth(24)
        field.SetPrecision(10)
        layer.CreateField(field)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField('LINKNO',3)
        feature.SetField('Length',25.125)
        feature.SetGeometry(ogr.CreateGeometryFromWkt(
            'LINESTRING Z (500000 4700000 123.125,500025 4700000 121.75)'))
        layer.CreateFeature(feature)
        feature = layer = dataset = None
        self.products.network_path = str(path)
        snapshot = read_network_snapshot(self.products)
        offline = SimpleNamespace(network_path='')
        for name,products,state in (('live_z',self.products,None),('offline_z',offline,snapshot)):
            target = export.export_network(products,self.output/(name+'.shp'),snapshot=state)
            source, copied = ogr.Open(str(path)),ogr.Open(str(target))
            export._check_network(source,copied)
            copied.GetLayer(0).ResetReading()
            saved = copied.GetLayer(0).GetNextFeature()
            self.assertEqual(saved.GetGeometryRef().GetPoints(),
                             [(500000.,4700000.,123.125),(500025.,4700000.,121.75)])
            self.assertEqual(saved['Length'],25.125)
            saved = copied = source = None

    def test_zero_length_taudem_links_preserve_identical_xyz_and_null_fields(self):
        from qgis.core import QgsApplication
        from caumax_consulta.drainage_layers import read_network_snapshot
        app = QgsApplication.instance() or QgsApplication([],False)
        app.initQgis()
        path = self.source_folder/'zero_z.shp'
        dataset = ogr.GetDriverByName('ESRI Shapefile').CreateDataSource(str(path))
        layer = dataset.CreateLayer('zero_z',self.reference,ogr.wkbLineString25D)
        layer.CreateField(ogr.FieldDefn('LINKNO',ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn('DSLINKNO',ogr.OFTInteger))
        field = ogr.FieldDefn('Length',ogr.OFTReal)
        field.SetWidth(24)
        field.SetPrecision(10)
        layer.CreateField(field)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetField('LINKNO',75)
        # Both unset fields are real source NULL values, not zero sentinels.
        geometry = ogr.CreateGeometryFromWkt(
            'LINESTRING Z (500025 4700025 121.75,500025 4700025 121.75)')
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
        feature = layer = dataset = None
        self.products.network_path = str(path)
        snapshot = read_network_snapshot(self.products)
        offline = SimpleNamespace(network_path='')
        for name,products,state in (('live_zero',self.products,None),('offline_zero',offline,snapshot)):
            with self.subTest(name=name):
                target = export.export_network(products,self.output/(name+'.shp'),snapshot=state)
                source, copied = ogr.Open(str(path)),ogr.Open(str(target))
                export._check_network(source,copied)
                copied.GetLayer(0).ResetReading()
                saved = copied.GetLayer(0).GetNextFeature()
                self.assertEqual(saved.GetGeometryRef().Length(),0.)
                self.assertEqual(saved.GetGeometryRef().GetPoints(),
                                 [(500025.,4700025.,121.75),(500025.,4700025.,121.75)])
                self.assertEqual(saved['LINKNO'],75)
                self.assertIsNone(saved['DSLINKNO'])
                self.assertIsNone(saved['Length'])
                saved = copied = source = None


if __name__ == '__main__':
    unittest.main()
