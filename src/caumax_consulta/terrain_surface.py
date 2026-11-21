"""Exact polygon/cell intersections for terrain on the active watershed surface."""
import math

import numpy as np
from osgeo import gdal, ogr

from . import basin as _b
from .basin_hydrology import _pixel, _surface_area, MAX_CANDIDATE_CELLS


def surface_window(dataset, geometry_wkb, outlet, cancelled, progress):
    geometry = ogr.CreateGeometryFromWkb(bytes(geometry_wkb))
    if (geometry is None or geometry.IsEmpty() or not geometry.IsValid()
            or ogr.GT_Flatten(geometry.GetGeometryType()) not in (3, 6)):
        raise ValueError('La divisoria activa no es un polígono válido.')
    point = ogr.Geometry(ogr.wkbPoint)
    point.AddPoint_2D(*outlet)
    if not geometry.Contains(point):
        raise ValueError('La divisoria activa debe conservar el centro de cierre.')
    transform = dataset.GetGeoTransform()
    inverse = gdal.InvGeoTransform(transform)
    xmin, xmax, ymin, ymax = geometry.GetEnvelope()
    corners = [gdal.ApplyGeoTransform(inverse, x, y)
               for x in (xmin, xmax) for y in (ymin, ymax)]
    left = max(0, math.floor(min(p[0] for p in corners)))
    right = min(dataset.RasterXSize, math.ceil(max(p[0] for p in corners)))
    top = max(0, math.floor(min(p[1] for p in corners)))
    bottom = min(dataset.RasterYSize, math.ceil(max(p[1] for p in corners)))
    size = (right-left)*(bottom-top)
    if left >= right or top >= bottom or size > MAX_CANDIDATE_CELLS:
        raise ValueError('La divisoria queda fuera del MDT o supera la ventana de cálculo permitida.')
    weights = np.zeros((bottom-top, right-left), dtype=np.float64)
    prepared = geometry.CreatePreparedGeometry()
    for row in range(top, bottom):
        _b._check_cancel(cancelled)
        for col in range(left, right):
            if (col-left) % 256 == 0:
                _b._check_cancel(cancelled)
            pixel = _pixel(transform, col, row)
            if prepared.Intersects(pixel):
                weights[row-top, col-left] = _surface_area(
                    pixel if prepared.Contains(pixel) else geometry.Intersection(pixel))
        progress(40*(row-top+1)/max(1, bottom-top))
    area = float(geometry.GetArea())
    if not math.isclose(float(weights.sum()), area, rel_tol=1e-8, abs_tol=.01):
        raise ValueError('El MDT no cubre íntegramente la divisoria activa; no se publica un análisis incompleto.')
    mask = (weights > 0).astype(np.uint8)
    count = int(np.count_nonzero(mask))
    touches = bool((left == 0 and mask[:, 0].any())
                   or (top == 0 and mask[0].any())
                   or (right == dataset.RasterXSize and mask[:, -1].any())
                   or (bottom == dataset.RasterYSize and mask[-1].any()))
    return mask, (left, top, right-left, bottom-top), count, touches, weights, area, float(geometry.Boundary().Length())
