"""Reproduce a 54 km² reference becoming only two 25 m cells at a bank click.

The reference is a 6000 × 9000 m rectangle: 240 × 360 cells of 625 m².
Its D8 river is column 250. Two small eastern strips drain away to a larger
river outside the reference; the clicked cell has exactly one upstream cell.
The legal near-section closure on column 250 loses a small southern strip,
so its independently delineated area is close to, but not forced to, 54 km².
No GIS data files are shipped and no external GRASS process is required.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry, QgsPointXY, QgsRectangle

from caumax_consulta import basin, dem_hydrology as hydro
from caumax_consulta.caumax_bridge import NativeQuery


class Refinement54Km2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],False)
        cls.app.initQgis()
        cls.temporary=tempfile.TemporaryDirectory(prefix='refinement_54km2_')
        cls.folder=Path(cls.temporary.name)
        cls.cache=cls.folder/'cache'
        cls.cache.mkdir()
        cls.crs=QgsCoordinateReferenceSystem('EPSG:25830')
        cls.transform=(336000,25,0,4719000,0,-25)
        cls.click=(342324.8,4709533.9)
        rows,cols=np.indices((900,500))
        codes=np.full(rows.shape,255,dtype=np.uint8)
        codes[20:380,20:250]=1
        codes[20:380,250]=4
        codes[20:380,251:260]=16
        codes[379,250]=0
        # A larger, separate river to the east receives no reference basin
        # except the explicitly diverted two rows near the clicked bank.
        codes[:,260:400]=1
        codes[:,400]=4
        codes[:,401:]=16
        codes[-1,400]=0
        codes[377,252:260]=4
        codes[378,252:260]=1
        elevations=np.full(rows.shape,-9999,dtype=np.float32)
        elevations[:,260:]=((899-rows[:,260:])*.1+abs(cols[:,260:]-400)*.05)
        elevations[20:380,20:260]=(100+(379-rows[20:380,20:260])*.5
                                   +abs(cols[20:380,20:260]-250)*.1)
        elevations[378,252:260]=95-5*np.arange(8)
        elevations[377,252:260]=elevations[378,252:260]+1
        cls.dem=cls._raster('dem25.tif',elevations,gdal.GDT_Float32,-9999)
        source=gdal.Open(str(cls.dem))
        key=hydro._source_key(source,cls.dem)
        source=None
        cls.directions=cls._raster('cache/d8_'+key[:32]+'.tif',codes,gdal.GDT_Byte,255,
                                  metadata={'SOURCE_KEY':key,'HYDRO_VERSION':hydro._VERSION})
        cls.reference_geometry=QgsGeometry.fromRect(QgsRectangle(336500,4709500,342500,4718500))
        course=QgsGeometry.fromPolylineXY([
            QgsPointXY(342250,4709750),QgsPointXY(342250,4718250),QgsPointXY(336750,4718250)])
        cls.reference=dict(geometry_wkb=bytes(cls.reference_geometry.asWkb()),
                           main_course_wkb=bytes(course.asWkb()),crs_wkt=cls.crs.toWkt(),
                           cell_width=500,cell_height=500)
        cls.query=NativeQuery(12.,100,*cls.click,cls.crs,str(cls.directions),str(cls.dem),'q100')

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def _raster(cls,name,values,dtype,nodata,metadata=None):
        path=cls.folder/name
        raster=gdal.GetDriverByName('GTiff').Create(str(path),values.shape[1],values.shape[0],1,dtype)
        raster.SetGeoTransform(cls.transform)
        raster.SetProjection(cls.crs.toWkt())
        raster.GetRasterBand(1).SetNoDataValue(nodata)
        raster.GetRasterBand(1).WriteArray(values)
        if metadata:
            raster.SetMetadata(metadata)
        raster=None
        return path

    @classmethod
    def _point(cls,row,col):
        return tuple(gdal.ApplyGeoTransform(cls.transform,col+.5,row+.5))

    @staticmethod
    def _geometry(wkb):
        geometry=QgsGeometry()
        geometry.fromWkb(bytes(wkb))
        return geometry

    def test_fixture_has_two_clicked_cells_and_a_larger_unrelated_river(self):
        local=basin.delineate(self.directions,self.dem,*self.click)
        nearby=basin.delineate(self.directions,self.dem,*self._point(378,250))
        unrelated=basin.delineate(self.directions,self.dem,*self._point(899,400))
        self.assertEqual(self.reference_geometry.area(),54e6)
        self.assertEqual(local.cell_count,2)
        self.assertEqual(local.area_m2,1250)
        self.assertGreater(nearby.area_m2,53e6)
        self.assertLess(nearby.area_m2,54e6)
        self.assertGreater(unrelated.area_m2,2*self.reference_geometry.area())
        intersection=self._geometry(unrelated.geometry_wkb).intersection(self.reference_geometry)
        self.assertLess(intersection.area()/unrelated.area_m2,.001)

    def test_cached_refinement_selects_the_reference_river_and_keeps_original_query(self):
        original=(self.query.x,self.query.y,self.query.flow,self.query.period,
                  self.query.directions_path,self.query.dem_path,self.query.crs.toWkt())
        hashes={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.dem,self.directions)}
        with patch.object(hydro,'_run_grass',side_effect=AssertionError('Use the prepared native D8 cache')), \
                patch.object(hydro,'availability_engine',return_value={'available':False,'path':'','reason':'Cached fixture'}):
            local=hydro.analyze_dem(self.dem,self.query.x,self.query.y,self.cache)
            result=hydro.analyze_dem(self.dem,self.query.x,self.query.y,self.cache,
                refinement_reference=self.reference,reference_area_m2=54e6,
                reference_cell_width=500,reference_cell_height=500,snap_radius_m=math.hypot(500,500))
        self.assertEqual(local.cell_count,2)
        self.assertEqual(local.area_m2,1250)
        self.assertTrue(result.metadata['cache_reused'])
        self.assertEqual(result.directions_path,str(self.directions))
        self.assertEqual((result.cell_width,result.cell_height),(25,25))
        self.assertEqual(result.outlet_x,self._point(378,250)[0])
        self.assertGreaterEqual(result.outlet_y,self.query.y)
        self.assertLess(math.hypot(result.outlet_x-self.query.x,result.outlet_y-self.query.y),100)
        self.assertGreater(result.area_m2,53e6)
        self.assertLess(result.area_m2,54.1e6)
        trusted=basin.delineate(self.directions,self.dem,result.outlet_x,result.outlet_y)
        self.assertEqual(result.cell_count,trusted.cell_count)
        self.assertEqual(result.area_m2,trusted.area_m2)
        geometry=self._geometry(result.geometry_wkb)
        self.assertTrue(geometry.isGeosEqual(self._geometry(trusted.geometry_wkb)))
        self.assertAlmostEqual(geometry.area(),result.area_m2,places=6)
        point=QgsPointXY(result.outlet_x,result.outlet_y)
        self.assertTrue(geometry.contains(QgsGeometry.fromPointXY(point)))
        course=self._geometry(result.main_course_wkb).asPolyline()
        self.assertGreater(len(course),1)
        self.assertEqual(course[0],point)
        self.assertEqual((result.basin.outlet_x,result.basin.outlet_y),(point.x(),point.y()))
        self.assertEqual((result.selected_x,result.selected_y),self.click)
        self.assertEqual((self.query.x,self.query.y,self.query.flow,self.query.period,
                          self.query.directions_path,self.query.dem_path,self.query.crs.toWkt()),original)
        self.assertEqual({path:hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes},hashes)
        print('REFINEMENT_54KM2 '+json.dumps(dict(reference_km2=54,
            local_km2=local.area_m2/1e6,result_km2=result.area_m2/1e6,
            cells=result.cell_count,original=list(self.click),outlet=[point.x(),point.y()],
            displacement_m=round(math.hypot(point.x()-self.query.x,point.y()-self.query.y),3),
            d8_cache_reused=result.metadata['cache_reused']),sort_keys=True))


if __name__=='__main__':
    unittest.main()
