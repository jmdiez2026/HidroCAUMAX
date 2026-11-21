"""Official MDT requests, native resolution, coverage and atomic installation."""
from dataclasses import replace
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from urllib.error import HTTPError

import numpy as np
from osgeo import gdal,osr

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta import dem_provider as provider


class Response(io.BytesIO):
    def __init__(self,payload,kind='image/tiff'):
        super().__init__(payload)
        self.headers={'Content-Type':kind,'Content-Length':str(len(payload))}


class DemProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='caumax_dem_test_')
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.bounds=(334250,4723750,334350,4723850)
        self.srs=provider._epsg(25830)
        self.wkt=self.srs.ExportToWkt()
        self.requests=[]
        self.sequence=0
        self.cache_patch=patch.object(provider,'_CATALOGUE_CACHE',None)
        self.cache_patch.start();self.addCleanup(self.cache_patch.stop)

    def option(self,resolution=5):
        ident=f'EL.ElevationGridCoverage_25830_{resolution:g}_PB'
        return provider.ResolutionOption(float(resolution),f'MDT {resolution:g} m','IGN/CNIG',
            provider.API_BASE+'/collections/'+ident+'/coverage?f=COG',ident,self.wkt,True,'Verificado',
            provider.API_BASE+'/collections/'+ident+'?f=json')

    def catalogue(self):
        collections=[]
        for resolution in (25,5):
            option=self.option(resolution)
            collections.append({'id':option.collection_id,'title':option.title,
                'extent':{'spatial':{'bbox':[[-11,34,5,44]],'crs':'http://www.opengis.net/def/crs/OGC/1.3/CRS84'}},
                'links':[{'rel':'http://www.opengis.net/def/rel/ogc/1.0/coverage','type':'image/tiff','href':option.url}]})
        return {'collections':collections}

    def tiff(self,url,nodata=False,actual_resolution=None):
        parsed=urlsplit(url);params=parse_qs(parsed.query)
        bounds=tuple(map(float,params['bbox'][0].split(',')))
        resolution=float(parsed.path.split('/')[-2].split('_')[-2])
        step=actual_resolution or resolution
        cols=max(1,int(math.ceil((bounds[2]-bounds[0])/step)))
        rows=max(1,int(math.ceil((bounds[3]-bounds[1])/step)))
        self.sequence+=1;path=self.folder/f'fixture{self.sequence}.tif'
        ds=gdal.GetDriverByName('GTiff').Create(str(path),cols,rows,1,gdal.GDT_Float32)
        ds.SetProjection(self.wkt);ds.SetGeoTransform((bounds[0],step,0,bounds[3],0,-step))
        ds.GetRasterBand(1).SetNoDataValue(provider.NODATA)
        ds.GetRasterBand(1).Fill(provider.NODATA if nodata else 100)
        ds=None
        return path.read_bytes()

    def serve(self,url):
        self.requests.append(url)
        if url==provider.CATALOGUE_URL:
            return Response(json.dumps(self.catalogue()).encode(),'application/json')
        return Response(self.tiff(url))

    def test_discovery_checks_real_native_crops_and_does_not_invent_finer_products(self):
        with patch.object(provider,'_open_url',side_effect=self.serve):
            options=provider.discover_resolutions(self.bounds,self.wkt)
        self.assertEqual([o.resolution_m for o in options],[25,5,2,.5])
        self.assertEqual([o.available for o in options],[True,True,False,False])
        self.assertEqual(options[1].resolution,5)
        self.assertIn('no verificada',options[2].status)
        queries=[parse_qs(urlsplit(url).query) for url in self.requests if '/coverage?' in url]
        self.assertEqual(len(queries),2)
        self.assertTrue(all(query['bbox-crs']==['25830'] for query in queries))

    def test_nodata_probe_is_unavailable_without_claiming_a_download(self):
        def serve(url):
            if url==provider.CATALOGUE_URL:return Response(json.dumps(self.catalogue()).encode(),'application/json')
            return Response(self.tiff(url,nodata='_5_PB/' in url))
        with patch.object(provider,'_open_url',side_effect=serve):
            options=provider.discover_resolutions(self.bounds,self.wkt)
        self.assertTrue(options[0].available)
        self.assertFalse(options[1].available)
        self.assertIn('NoData',options[1].status)

    def test_tiled_download_is_aligned_metric_complete_and_cached_per_extent(self):
        destination=self.folder/'first.tif'
        progress=[]
        bounds=(334251,4723751,334349,4723849)
        with patch.object(provider,'_open_url',side_effect=self.serve),patch.object(provider,'TILE_PIXELS',8):
            result=provider.download_dem(self.option(),bounds,self.wkt,destination,progress=progress.append)
            request_count=len(self.requests)
            second=provider.download_dem(self.option(),bounds,self.wkt,self.folder/'second.tif')
        self.assertEqual(result,destination)
        self.assertEqual(len(self.requests),request_count)
        self.assertEqual(request_count,9)
        self.assertEqual(destination.read_bytes(),second.read_bytes())
        ds=gdal.Open(str(destination))
        self.assertEqual((ds.RasterXSize,ds.RasterYSize),(20,20))
        self.assertEqual(ds.GetGeoTransform(),(334250,5,0,4723850,0,-5))
        self.assertTrue(provider._reference(ds.GetProjection(),True).IsSame(self.srs))
        np.testing.assert_array_equal(ds.ReadAsArray(),np.full((20,20),100,dtype=np.float32))
        self.assertEqual(ds.GetMetadataItem('MDT_NATIVE_RESOLUTION_M'),'5.0')
        self.assertEqual(ds.GetMetadataItem('MDT_VALID_PIXEL_FRACTION'),'1')
        ds=None
        self.assertEqual(progress[-1],100)

    def test_wrong_native_resolution_is_rejected_instead_of_upscaling(self):
        destination=self.folder/'kept.tif';destination.write_bytes(b'keep old file')
        with patch.object(provider,'_open_url',side_effect=lambda url:Response(self.tiff(url,actual_resolution=25))):
            with self.assertRaisesRegex(ValueError,'más grueso'):
                provider.download_dem(self.option(),self.bounds,self.wkt,destination)
        self.assertEqual(destination.read_bytes(),b'keep old file')

    def test_official_north_east_axis_variant_3042_is_equivalent_not_another_grid(self):
        option=self.option()
        payload=self.tiff(provider._coverage_url(option,self.bounds))
        path=self.folder/'axis3042.tif';path.write_bytes(payload)
        ds=gdal.Open(str(path),gdal.GA_Update)
        reference=provider._epsg(3042)
        ds.SetProjection(reference.ExportToWkt());ds=None
        provider._native_metadata(path,option)
        wrong=provider._epsg(25829)
        ds=gdal.Open(str(path),gdal.GA_Update);ds.SetProjection(wrong.ExportToWkt());ds=None
        with self.assertRaises(ValueError):provider._native_metadata(path,option)

    def test_error_xml_disguised_as_download_preserves_existing_destination(self):
        destination=self.folder/'kept.tif';destination.write_bytes(b'previous contents')
        with patch.object(provider,'_open_url',return_value=Response(b'<ExceptionReport/>','application/xml')):
            with self.assertRaisesRegex(ValueError,'error'):
                provider.download_dem(self.option(),self.bounds,self.wkt,destination)
        self.assertEqual(destination.read_bytes(),b'previous contents')

    def test_partial_or_absent_coverage_does_not_produce_a_final_raster(self):
        destination=self.folder/'partial.tif'
        with patch.object(provider,'_open_url',side_effect=lambda url:Response(self.tiff(url,nodata=True))):
            with self.assertRaisesRegex(ValueError,'NoData'):
                provider.download_dem(self.option(),self.bounds,self.wkt,destination)
        self.assertFalse(destination.exists())
        self.assertFalse(any(p.name.startswith('.caumax_mdt_') for p in self.folder.iterdir()))

    def test_cancellation_during_transfer_preserves_existing_file(self):
        destination=self.folder/'kept.tif';destination.write_bytes(b'keep')
        cancelled=[False]
        owner=self
        class CancelResponse(Response):
            def read(self,size=-1):
                payload=super().read(size)
                cancelled[0]=True
                return payload
        with patch.object(provider,'_open_url',side_effect=lambda url:CancelResponse(owner.tiff(url))):
            with self.assertRaises(InterruptedError):
                provider.download_dem(self.option(),self.bounds,self.wkt,destination,cancelled=lambda:cancelled[0])
        self.assertEqual(destination.read_bytes(),b'keep')

    def test_unsupported_resolution_geographic_crs_or_oversized_zone_never_download(self):
        geographic=provider._epsg(4326).ExportToWkt()
        with patch.object(provider,'_open_url') as network:
            with self.assertRaises(ValueError):
                provider.download_dem(replace(self.option(),available=False),self.bounds,self.wkt,self.folder/'x.tif')
            with self.assertRaisesRegex(ValueError,'métrico'):
                provider.download_dem(self.option(),self.bounds,geographic,self.folder/'x.tif')
            with patch.object(provider,'MAX_CELLS',10):
                with self.assertRaisesRegex(ValueError,'excede'):
                    provider.download_dem(self.option(),self.bounds,self.wkt,self.folder/'x.tif')
        network.assert_not_called()

    def test_only_official_download_hosts_and_dem_collections_are_accepted(self):
        with patch.object(provider,'_open_url') as network:
            with self.assertRaises(ValueError):
                provider.download_dem(replace(self.option(),url='https://example.com/terrain'),self.bounds,self.wkt,self.folder/'x.tif')
            with self.assertRaises(ValueError):
                provider.download_dem(replace(self.option(),collection_id='MDS_surface_5'),self.bounds,self.wkt,self.folder/'x.tif')
        network.assert_not_called()

    def test_http_size_error_keeps_exact_url_and_server_diagnostics(self):
        url=provider._coverage_url(self.option(25),(330000,4720000,355600,4745600))
        body={'description':'Requested bbox width 25600.0 m is larger than allowed: 12500.0 m.'}
        error=HTTPError(url,500,'INTERNAL SERVER ERROR',{},io.BytesIO(json.dumps(body).encode()))
        with patch.object(provider,'urlopen',side_effect=error):
            with self.assertRaises(provider.DemServiceError) as caught:
                provider._open_url(url)
        self.assertEqual(caught.exception.request_url,url)
        self.assertEqual(caught.exception.status_code,500)
        self.assertEqual(caught.exception.limit_axis,'width')
        self.assertEqual(caught.exception.max_span_m,12500)
        self.assertTrue(caught.exception.is_size_limit)

    def test_service_size_limit_splits_without_changing_resolution_or_leaving_seams(self):
        attempts=[]
        def serve(url):
            bounds=tuple(map(float,parse_qs(urlsplit(url).query)['bbox'][0].split(',')))
            attempts.append(bounds)
            if bounds[2]-bounds[0]>60:
                raise provider.DemServiceError(url,500,'Requested bbox width 110 m is larger than allowed: 60 m.')
            if bounds[3]-bounds[1]>60:
                raise provider.DemServiceError(url,500,'Requested bbox height 110 m is larger than allowed: 60 m.')
            return Response(self.tiff(url))
        destination=self.folder/'adaptive.tif'
        with patch.object(provider,'TILE_PIXELS',32),patch.object(provider,'_open_url',side_effect=serve):
            provider.download_dem(self.option(),self.bounds,self.wkt,destination)
        dataset=gdal.Open(str(destination))
        self.assertEqual(dataset.GetGeoTransform(),(334250,5,0,4723850,0,-5))
        np.testing.assert_array_equal(dataset.ReadAsArray(),np.full((20,20),100,dtype=np.float32))
        self.assertEqual(dataset.GetMetadataItem('MDT_DOWNLOAD_TILES'),'4')
        self.assertEqual(dataset.GetMetadataItem('MDT_TILE_SIZE_RETRIES'),'3')
        dataset=None
        self.assertEqual(len(attempts),7)

    def test_generic_server_failure_is_not_recursively_retried_as_a_size_limit(self):
        destination=self.folder/'kept.tif';destination.write_bytes(b'keep')
        with patch.object(provider,'_open_url',side_effect=provider.DemServiceError('url',500,'Temporary internal server failure')) as network:
            with self.assertRaises(provider.DemServiceError):
                provider.download_dem(self.option(),self.bounds,self.wkt,destination)
        self.assertEqual(network.call_count,1)
        self.assertEqual(destination.read_bytes(),b'keep')


if __name__=='__main__':
    unittest.main()
