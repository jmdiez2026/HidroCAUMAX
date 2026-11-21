"""Reclip the archived native drainage network to the active analysis surface."""
from copy import deepcopy

from osgeo import ogr, osr


def clip_network(state, analysis, cancelled=lambda: False):
    if state is None:
        return None
    result = deepcopy(state)
    polygon = ogr.CreateGeometryFromWkb(bytes(analysis.geometry_wkb))
    source, target = osr.SpatialReference(), osr.SpatialReference()
    if (source.ImportFromWkt(analysis.crs_wkt) != 0
            or target.ImportFromWkt(state['network_crs_wkt']) != 0):
        raise ValueError('La red de drenaje no tiene un CRS válido para actualizar su recorte.')
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if not source.IsSame(target):
        if polygon.Transform(osr.CoordinateTransformation(source, target)) != 0:
            raise ValueError('No se puede transformar la divisoria para recortar su red.')
    features = []
    def lines(geometry):
        kind = ogr.GT_Flatten(geometry.GetGeometryType())
        if kind == ogr.wkbLineString:
            return [geometry] if not geometry.IsEmpty() and geometry.Length() > 0 else []
        if kind in (ogr.wkbMultiLineString, ogr.wkbGeometryCollection):
            return [line for part in geometry for line in lines(part)]
        return []
    for feature in state.get('features', []):
        if cancelled():
            raise InterruptedError('Actualización de la red cancelada.')
        geometry = ogr.CreateGeometryFromWkb(bytes(feature['wkb']))
        if geometry is None:
            raise ValueError('Un tramo archivado de drenaje no es válido.')
        for line in lines(geometry.Intersection(polygon)):
            features.append(dict(wkb=bytes(line.ExportToWkb()), attributes=deepcopy(feature['attributes'])))
    result['features'] = features
    recommended = max(100*analysis.cell_width*analysis.cell_height, .01*analysis.area_m2)
    result['recommended_threshold_area_m2'] = recommended
    result['metadata']['active_surface_area_m2'] = analysis.area_m2
    result['metadata']['surface_clip'] = 'exact_active_polygon'
    # D8 directions and channel threshold describe the native grid. Smoothing
    # updates their spatial clip; it does not modify the DEM or reroute rivers.
    return result
