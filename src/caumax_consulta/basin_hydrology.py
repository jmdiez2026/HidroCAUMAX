"""Area-weighted P0 and daily rainfall on the original basin polygon.

Only GDAL/OGR/NumPy are used by the raster worker. Pixel intersections are
measured in the basin's projected metre CRS, preserving parts and holes. No
P0 correction, beta factor or areal rainfall reduction is applied.
"""
import hashlib
import math
import json
from pathlib import Path
import sys

import numpy as np
from osgeo import gdal, ogr, osr

PERIODS = (2, 5, 10, 25, 100, 500)
MAX_CANDIDATE_CELLS = 5_000_000
_REL_TOL, _ABS_TOL = 1e-7, 1e-5


def basin_fingerprint(analysis):
    """SHA256 of exact WKB+CRS, length-framed; no raster or filesystem reads."""
    try:
        geometry = bytes(analysis.geometry_wkb)
        crs = str(analysis.crs_wkt).encode("utf-8")
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("La cuenca no tiene geometría y CRS para identificarla.") from error
    if not geometry or not crs:
        raise ValueError("La cuenca no tiene geometría y CRS para identificarla.")
    digest = hashlib.sha256()
    for value in (geometry, crs):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def source_paths(query, paths=None):
    """Freeze configured paths before launching a raster worker.

    Explicit canonical {'p0','rainfall':{'2',...}} or native P0/RAIN_T mappings
    take priority. Otherwise read getLayerPath from the already-loaded native
    CAUMAX layers module; never import its UI, call getLayer, or access a project.
    A configured missing/empty path is preserved, producing unavailable later.
    Only unconfigured keys fall back beside query.dem_path.
    """
    supplied = dict(paths or {})
    rainfall = supplied.get("rainfall", {})
    rainfall = rainfall if isinstance(rainfall, dict) else {}
    modules = []
    direct = sys.modules.get("caumax.layers")
    if direct is not None:
        modules.append(direct)
    modules.extend(module for name, module in list(sys.modules.items())
                   if name != "caumax.layers" and "caumax" in name.lower() and
                   name.endswith(".layers") and module is not None)
    native = next((module for module in modules
                   if callable(getattr(module, "getLayerPath", None)) and hasattr(module, "P0")), None)
    dem = query.get("dem_path", "") if isinstance(query, dict) else getattr(query, "dem_path", "")
    folder = Path(str(dem)).expanduser().parent
    flow = query.get("flow_layer_path", "") if isinstance(query, dict) else getattr(query, "flow_layer_path", "")
    if flow and "|" not in str(flow) and Path(str(flow)).expanduser().is_file():
        folder = Path(str(flow)).expanduser().parent

    def value(canonical, identifier, fallback):
        if canonical == "p0" and "p0" in supplied:
            return str(supplied["p0"] or "")
        if canonical != "p0" and canonical in rainfall:
            return str(rainfall[canonical] or "")
        if identifier in supplied:
            return str(supplied[identifier] or "")
        if native is not None and hasattr(native, identifier):
            try:
                configured = native.getLayerPath(getattr(native, identifier))
            except (KeyError, AttributeError):
                pass
            else:
                return str(configured or "")
        return str(folder / fallback)

    return {"p0": value("p0", "P0", "p0.tif"),
            "rainfall": {str(period): value(str(period), "RAIN_" + str(period),
                                             "t" + str(period) + ".tif") for period in PERIODS}}


def _check(cancelled):
    if cancelled():
        raise InterruptedError("Cálculo de P0/precipitación media cancelado.")


def _result(path, basin_area=0.0):
    return {"source_path": str(path or ""), "mean_mm": None,
            "basin_area_m2": float(basin_area), "covered_area_m2": 0.0,
            "valid_area_m2": 0.0, "coverage_fraction": 0.0, "valid_fraction": 0.0,
            "pixel_count": 0, "status": "unavailable", "notes": []}


def _reference(wkt, label, metric=False):
    reference = osr.SpatialReference()
    if not wkt or reference.ImportFromWkt(wkt) != 0:
        raise ValueError(f"{label} no tiene un CRS válido.")
    if metric and (not reference.IsProjected() or not math.isclose(
            reference.GetLinearUnits(), 1.0, rel_tol=0, abs_tol=1e-12)):
        raise ValueError("La cuenca debe estar en un CRS proyectado en metros.")
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return reference


def _basin(analysis):
    geometry = ogr.CreateGeometryFromWkb(bytes(analysis.geometry_wkb))
    if (geometry is None or geometry.IsEmpty() or ogr.GT_Flatten(geometry.GetGeometryType())
            not in (ogr.wkbPolygon, ogr.wkbMultiPolygon)):
        raise ValueError("La cuenca original no es una geometría poligonal válida.")
    geometry.FlattenTo2D()
    if not geometry.IsValid():
        raise ValueError("La cuenca original es inválida; no se repara ni suaviza para calcular la media.")
    reference = _reference(analysis.crs_wkt, "La cuenca", metric=True)
    area = float(geometry.GetArea())
    if not math.isfinite(area) or area <= 0:
        raise ValueError("La cuenca no tiene área positiva finita.")
    declared = float(analysis.area_m2)
    if not math.isfinite(declared) or not math.isclose(area, declared, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
        raise ValueError("El área de la cuenca original no coincide con su geometría.")
    return geometry, reference, area


def _point(transform, col, row):
    return (transform[0] + col * transform[1] + row * transform[2],
            transform[3] + col * transform[4] + row * transform[5])


def _transformed(point, operation):
    if operation is None:
        return point
    result = operation.TransformPoint(point[0], point[1])[:2]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("No se puede transformar una celda del ráster al CRS de la cuenca.")
    return result


def _pixel(transform, col, row, operation=None, tolerance=.001):
    corners = [_point(transform, col, row), _point(transform, col + 1, row),
               _point(transform, col + 1, row + 1), _point(transform, col, row + 1)]
    target = [_transformed(point, operation) for point in corners]
    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint_2D(*target[0])

    def edge(a, b, pa, pb, depth=0):
        if operation is not None:
            midpoint = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            projected_mid = _transformed(midpoint, operation)
            error = math.hypot(projected_mid[0] - (pa[0] + pb[0]) / 2,
                               projected_mid[1] - (pa[1] + pb[1]) / 2)
            if error > tolerance:
                if depth >= 12:
                    raise ValueError("La transformación requiere una densificación excesiva de píxeles.")
                edge(a, midpoint, pa, projected_mid, depth + 1)
                edge(midpoint, b, projected_mid, pb, depth + 1)
                return
        ring.AddPoint_2D(*pb)

    for index in range(4):
        following = (index + 1) % 4
        edge(corners[index], corners[following], target[index], target[following])
    polygon = ogr.Geometry(ogr.wkbPolygon)
    polygon.AddGeometry(ring)
    return polygon


def _surface_area(geometry):
    if geometry is None or geometry.IsEmpty():
        return 0.0
    kind = ogr.GT_Flatten(geometry.GetGeometryType())
    if kind in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
        return float(geometry.GetArea())
    if kind == ogr.wkbGeometryCollection:
        return math.fsum(_surface_area(child) for child in geometry)
    return 0.0


def basin_raster_mean(analysis, path, *, statistics=False, profile=False, cancelled=lambda: False, progress=lambda pct: None):
    """Mean physical millimetres weighted by exact positive-area intersections.

    covered_area is basin overlap with the raster grid, including NoData cells;
    valid_area additionally excludes the band mask, NoData, nonfinite values and
    negative physical values AFTER applying GDAL scale/offset. Zero is valid.
    Both fractions divide by the complete original basin area. pixel_count counts
    every intersected cell, including invalid values. Means divide by valid area
    only, with no missing-data imputation and no rainfall/P0 correction.

    Affine rotated grids are supported. Different source CRSs use densified
    transformed pixel boundaries, with exact GEOS intersections on those
    polylines, measured in the basin metre CRS. Unsupported inputs/failures
    return unavailable with no mean. Cancellation always raises InterruptedError.
    """
    statistics = statistics or profile
    _check(cancelled)
    progress(0)
    source_path = str(path or "")
    try:
        fallback_area = float(analysis.area_m2)
        fallback_area = fallback_area if math.isfinite(fallback_area) and fallback_area > 0 else 0.0
    except (AttributeError, TypeError, ValueError):
        fallback_area = 0.0
    output = _result(source_path, fallback_area)
    if statistics:
        output['statistics'] = None
    dataset = None
    try:
        basin, basin_reference, area = _basin(analysis)
        output["basin_area_m2"] = area
        if not source_path:
            raise ValueError("No se ha configurado el ráster de P0/precipitación.")
        dataset = gdal.Open(source_path, gdal.GA_ReadOnly)
        if dataset is None or dataset.RasterCount < 1:
            raise ValueError("El ráster de P0/precipitación no está disponible: " + source_path)
        source_reference = _reference(dataset.GetProjectionRef(), "El ráster")
        transform = dataset.GetGeoTransform(can_return_null=True)
        if (transform is None or not all(math.isfinite(value) for value in transform) or
                transform[1] * transform[5] - transform[2] * transform[4] == 0):
            raise ValueError("El ráster no tiene una malla afín invertible válida.")
        inverse = gdal.InvGeoTransform(transform)
        if inverse is None:
            raise ValueError("No se puede invertir la malla del ráster.")
        same_crs = bool(source_reference.IsSame(basin_reference))
        to_basin = None if same_crs else osr.CoordinateTransformation(source_reference, basin_reference)
        source_basin = basin.Clone()
        tolerance = .001
        if not same_crs:
            to_source = osr.CoordinateTransformation(basin_reference, source_reference)
            centre = basin.Centroid()
            source_centre = _transformed((centre.GetX(), centre.GetY()), to_source)
            at = _transformed(source_centre, to_basin)
            p1 = _transformed((source_centre[0] + transform[1], source_centre[1] + transform[4]), to_basin)
            p2 = _transformed((source_centre[0] + transform[2], source_centre[1] + transform[5]), to_basin)
            side = min(math.hypot(p1[0] - at[0], p1[1] - at[1]),
                       math.hypot(p2[0] - at[0], p2[1] - at[1]))
            if not math.isfinite(side) or side <= 0:
                raise ValueError("El tamaño transformado del píxel no es válido.")
            tolerance = max(.0001, side * 1e-5)
            source_basin.Segmentize(max(.05, side / 16))
            if source_basin.Transform(to_source) != 0:
                raise ValueError("No se puede transformar el contorno de la cuenca al ráster.")
            output["notes"].append(f"CRS distinto: bordes de píxel reproyectados y densificados, tolerancia {tolerance:g} m.")
        xmin, xmax, ymin, ymax = source_basin.GetEnvelope()
        corners = [gdal.ApplyGeoTransform(inverse, x, y)
                   for x in (xmin, xmax) for y in (ymin, ymax)]
        if not all(math.isfinite(value) for corner in corners for value in corner):
            raise ValueError("La cuenca queda fuera del dominio de transformación del ráster.")
        left = max(0, int(math.floor(min(point[0] for point in corners))) - 1)
        right = min(dataset.RasterXSize, int(math.ceil(max(point[0] for point in corners))) + 1)
        top = max(0, int(math.floor(min(point[1] for point in corners))) - 1)
        bottom = min(dataset.RasterYSize, int(math.ceil(max(point[1] for point in corners))) + 1)
        if left >= right or top >= bottom:
            output["notes"].append("El ráster no cubre la cuenca original.")
            progress(100)
            return output
        candidate_cells = (right - left) * (bottom - top)
        if candidate_cells > MAX_CANDIDATE_CELLS:
            raise ValueError(f"La ventana supera el límite de {MAX_CANDIDATE_CELLS:,} píxeles candidatos; no se calcula una media incompleta.")
        band = dataset.GetRasterBand(1)
        unit = (band.GetUnitType() or "").strip().lower()
        if unit and unit not in ("mm", "millimeter", "millimeters", "millimetre", "millimetres"):
            raise ValueError("La banda no está declarada en milímetros: " + unit)
        scale, offset = band.GetScale(), band.GetOffset()
        scale, offset = (1.0 if scale is None else float(scale)), (0.0 if offset is None else float(offset))
        if not math.isfinite(scale) or not math.isfinite(offset):
            raise ValueError("El scale/offset del ráster no es finito.")
        nodata = band.GetNoDataValue()
        prepared = basin.CreatePreparedGeometry()
        covered, valid_area, weighted = [], [], []
        physical_values = [] if statistics else None
        profile_cells = [] if profile else None
        pixel_count = processed = excluded = 0
        for first in range(top, bottom, 32):
            _check(cancelled)
            height = min(32, bottom - first)
            values = band.ReadAsArray(left, first, right - left, height)
            if values is None or values.dtype.kind not in "biuf":
                raise ValueError("No se pueden leer valores numéricos del ráster.")
            mask = (None if band.GetMaskFlags() & gdal.GMF_ALL_VALID else
                    band.GetMaskBand().ReadAsArray(left, first, right - left, height))
            if mask is not None and mask.shape != values.shape:
                raise ValueError("No se puede leer la máscara del ráster.")
            for local_row in range(height):
                row = first + local_row
                for local_col in range(right - left):
                    processed += 1
                    if processed % 256 == 0:
                        _check(cancelled)
                        progress(100 * processed / candidate_cells)
                    col = left + local_col
                    pixel = _pixel(transform, col, row, to_basin, tolerance)
                    if not prepared.Intersects(pixel):
                        continue
                    overlap = _surface_area(pixel if prepared.Contains(pixel) else basin.Intersection(pixel))
                    if overlap <= 0:
                        continue
                    if not math.isfinite(overlap):
                        raise ValueError("La intersección de una celda no tiene área finita.")
                    covered.append(overlap)
                    pixel_count += 1
                    raw = float(values[local_row, local_col])
                    value = raw * scale + offset
                    good = (math.isfinite(raw) and math.isfinite(value) and value >= 0 and
                            (nodata is None or raw != nodata) and
                            (mask is None or mask[local_row, local_col] != 0))
                    if good:
                        valid_area.append(overlap)
                        weighted.append(value * overlap)
                        if statistics:
                            physical_values.append(value)
                        if profile and profile_cells is not None:
                            from .raster_profiles import MAX_PROFILE_CELLS
                            if len(profile_cells)<MAX_PROFILE_CELLS:
                                profile_cells.append(row*dataset.RasterXSize+col)
                            else:
                                profile_cells=None
                    else:
                        excluded += 1
        _check(cancelled)
        coverage = math.fsum(covered)
        valid = math.fsum(valid_area)
        if coverage > area and not math.isclose(coverage, area, rel_tol=_REL_TOL, abs_tol=_ABS_TOL):
            raise ValueError("Las intersecciones suman más área que la cuenca; no se publica una media inconsistente.")
        mean = math.fsum(weighted) / valid if valid > 0 else None
        if mean is not None and not math.isfinite(mean):
            raise ValueError("La media ponderada no es finita.")
        if statistics and mean is not None:
            samples = np.asarray(physical_values, dtype=np.float64)
            weights = np.asarray(valid_area, dtype=np.float64)
            order = np.argsort(samples, kind='stable')
            cumulative = np.cumsum(weights[order], dtype=np.float64)
            middle = valid / 2
            index = min(len(order) - 1, int(np.searchsorted(cumulative, middle, side='left')))
            median = float(samples[order[index]])
            if (index + 1 < len(order)
                    and math.isclose(float(cumulative[index]), middle, rel_tol=1e-12, abs_tol=1e-9)):
                median = (median + float(samples[order[index + 1]])) / 2
            variance = float(np.dot(weights, (samples - mean) ** 2) / valid)
            output['statistics'] = dict(median_mm=median, min_mm=float(samples.min()),
                max_mm=float(samples.max()), stddev_mm=math.sqrt(max(0., variance)))
            output['notes'].append('Mediana y desviación típica poblacional ponderadas por el área válida; extremos de los píxeles válidos intersectados.')
            if profile and profile_cells is not None:
                from .raster_profiles import encode_profile
                grid=hashlib.sha256(json.dumps([dataset.GetProjectionRef(),list(transform),
                    dataset.RasterXSize,dataset.RasterYSize],sort_keys=True).encode('utf-8')).hexdigest()
                output['distribution']=encode_profile(samples,weights,profile_cells,grid)
            elif profile:
                output['notes'].append('Perfil por celda no guardado por superar su límite; los estadísticos de cada mapa sí están calculados.')
        # Normalize only roundoff at a complete boundary; never fill a gap.
        reported_covered = area if math.isclose(coverage, area, rel_tol=_REL_TOL, abs_tol=_ABS_TOL) else min(area, coverage)
        reported_valid = area if math.isclose(valid, area, rel_tol=_REL_TOL, abs_tol=_ABS_TOL) else min(reported_covered, valid)
        status = "ok" if reported_valid == area else "partial" if valid > 0 else "unavailable"
        output.update(mean_mm=mean, covered_area_m2=reported_covered, valid_area_m2=reported_valid,
                      coverage_fraction=max(0.0, min(1.0, reported_covered / area)),
                      valid_fraction=max(0.0, min(1.0, reported_valid / area)),
                      pixel_count=pixel_count, status=status)
        if reported_covered < area:
            output["notes"].append("Cobertura espacial parcial; la zona no cubierta no se imputa.")
        if excluded:
            output["notes"].append(f"Se excluyen {excluded} píxeles intersectados por NoData, máscara o valores no finitos/negativos.")
        if status == "partial":
            output["notes"].append("La media se calcula solo sobre el área con dato válido, sin rellenar faltantes.")
        elif status == "unavailable":
            output["notes"].append("No hay área con valores válidos para calcular la media.")
        if scale != 1 or offset != 0:
            output["notes"].append(f"Se aplica el scale/offset de la banda: valor × {scale:g} + {offset:g}.")
        progress(100)
        return output
    except InterruptedError:
        raise
    except Exception as error:
        failed = _result(source_path, output["basin_area_m2"])
        failed["notes"] = ["No se ha podido completar la media: " + str(error)]
        return failed
    finally:
        dataset = None
