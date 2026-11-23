"""Basin, terrain and D8-path analysis using the original CAUMAX grid.

Lengths are measured between cell centres. Basin length Lb is the maximum
straight distance from the effective outlet centre to an exterior boundary
vertex. The 10--85 profile interval is measured upstream from the outlet.
No QGIS object, project, setting or installed raster is modified here.
"""

from dataclasses import dataclass
import math

import numpy as np
from osgeo import ogr

from . import basin as _b
from .grid import cell_at_point
from .hypsometry_data import build_hypsometric_curve
from .metrics import shape_metrics


@dataclass(frozen=True)
class Analysis:
    basin: _b.BasinResult
    values: dict
    notes: tuple
    diagnostics: dict
    main_course_wkb: bytes
    grid_wkb: bytes
    selected_x: float
    selected_y: float

    def __getattr__(self, name):
        basin = object.__getattribute__(self, "basin")
        return getattr(basin, name)


def _dem_halo(band, window, raster_cols, raster_rows, cancelled):
    """Read the basin bounding window plus one actual DEM neighbour cell."""
    x, y, cols, rows = window
    left, top = max(0, x - 1), max(0, y - 1)
    right, bottom = min(raster_cols, x + cols + 1), min(raster_rows, y + rows + 1)
    _b._check_cancel(cancelled)
    data = _b._read(band, left, top, right - left, bottom - top).astype(np.float64)
    valid = np.isfinite(data)
    if not band.GetMaskFlags() & _b.gdal.GMF_ALL_VALID:
        valid &= _b._read(band.GetMaskBand(), left, top, right - left, bottom - top) != 0
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= data != nodata
    data[~valid] = np.nan
    halo = np.full((rows + 2, cols + 2), np.nan, dtype=np.float64)
    offset_x, offset_y = left - x + 1, top - y + 1
    halo[offset_y:offset_y + data.shape[0], offset_x:offset_x + data.shape[1]] = data
    _b._check_cancel(cancelled)
    return halo


def _terrain_means(halo, mask, dx, dy, cancelled, weights=None):
    """Riley TRI and local D8 slope, independently excluding invalid cells.

    TauDEM defines D8 slope as the steepest neighbour drop/distance. On an
    unfilled DEM a strict local pit has no D8 descent and is NoData, not zero.
    This is the local definition on the configured DEM, not a TauDEM run:
    no pit filling, flat routing or replacement of CAUMAX directions occurs.
    https://hydrology.usu.edu/taudem/taudem5/help53/D8FlowDirections.html
    """
    centre = halo[1:-1, 1:-1]
    complete = np.isfinite(centre) & (mask != 0)
    sum_squares = np.zeros(mask.shape, dtype=np.float64)
    maximum_descent = np.full(mask.shape, -np.inf, dtype=np.float64)
    with np.errstate(invalid="ignore", over="ignore"):
        for col_offset, row_offset, _ in _b.DIRECTIONS:
            _b._check_cancel(cancelled)
            neighbour = halo[
                1 + row_offset:1 + row_offset + mask.shape[0],
                1 + col_offset:1 + col_offset + mask.shape[1],
            ]
            complete &= np.isfinite(neighbour)
            difference = centre - neighbour
            sum_squares += difference * difference
            distance = math.hypot(col_offset * dx, row_offset * dy)
            np.maximum(maximum_descent, difference / distance, out=maximum_descent)
    tri_valid = complete & np.isfinite(sum_squares)
    slope_valid = complete & np.isfinite(maximum_descent) & (maximum_descent >= 0)
    tri_count, slope_count = int(np.count_nonzero(tri_valid)), int(np.count_nonzero(slope_valid))
    pit_count = int(np.count_nonzero(complete & (maximum_descent < 0)))
    average = lambda values, valid: (np.mean(values, dtype=np.float64) if weights is None
                                     else np.average(values, weights=weights[valid]))
    tri = (float(average(np.sqrt(sum_squares[tri_valid]), tri_valid))
           if tri_count else None)
    slope_pct = (float(average(maximum_descent[slope_valid], slope_valid) * 100)
                 if slope_count else None)
    return tri, slope_pct, tri_count, slope_count, pit_count


def _boundary_length(geometry_wkb, outlet_x, outlet_y, cancelled):
    geometry = ogr.CreateGeometryFromWkb(geometry_wkb)
    maximum = 0.0
    parts = [geometry] if ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbPolygon else geometry
    for polygon in parts:
        _b._check_cancel(cancelled)
        ring = polygon.GetGeometryRef(0)
        for index in range(ring.GetPointCount()):
            if index % 1000 == 0:
                _b._check_cancel(cancelled)
            x, y, *_ = ring.GetPoint(index)
            maximum = max(maximum, math.hypot(x - outlet_x, y - outlet_y))
    return maximum


def _profile_value(distances, heights, target):
    index = int(np.searchsorted(distances, target, side="left"))
    if index < len(distances) and math.isclose(
        float(distances[index]), target, rel_tol=1e-12, abs_tol=1e-9
    ):
        return float(heights[index]) if math.isfinite(float(heights[index])) else None
    if index == 0 or index == len(distances):
        return None
    previous, following = float(heights[index - 1]), float(heights[index])
    if not math.isfinite(previous) or not math.isfinite(following):
        return None
    ratio = (target - distances[index - 1]) / (distances[index] - distances[index - 1])
    return float(previous + ratio * (following - previous))


def _paths(directions, mask, elevations, outlet_row, outlet_col,
           transform, cancelled):
    """Longest D8 course and its Tc; retain old maximum Tc only as diagnostic."""
    rows, cols = mask.shape
    distances = np.full(mask.shape, -1.0, dtype=np.float64)
    parents = np.full(mask.shape, -1, dtype=np.int64)
    valid_path = np.zeros(mask.shape, dtype=bool)
    distances[outlet_row, outlet_col] = 0.0
    outlet_height = float(elevations[outlet_row, outlet_col])
    valid_path[outlet_row, outlet_col] = math.isfinite(outlet_height)
    pending = [outlet_row * cols + outlet_col]
    longest = pending[0]
    best_tc = None
    best_length = best_slope = best_start = None
    valid_candidates = missing_candidates = invalid_slope_candidates = processed = 0
    while pending:
        index = pending.pop()
        row, col = divmod(index, cols)
        processed += 1
        if processed % 1000 == 0:
            _b._check_cancel(cancelled)
        length = distances[row, col]
        if length > 0:
            if not valid_path[row, col]:
                missing_candidates += 1
            else:
                difference = float(elevations[row, col]) - outlet_height
                if difference <= 0:
                    invalid_slope_candidates += 1
                else:
                    slope = difference / length
                    tc = 0.3 * (length / 1000) ** 0.76 * slope ** -0.19
                    if math.isfinite(tc):
                        valid_candidates += 1
                        if best_tc is None or tc > best_tc:
                            best_tc, best_length, best_slope, best_start = (
                                float(tc), float(length), float(slope), index
                            )
        longest_row, longest_col = divmod(longest, cols)
        if length > distances[longest_row, longest_col]:
            longest = index
        for dc, dr, code in _b.DIRECTIONS:
            nr, nc = row + dr, col + dc
            if not (0 <= nr < rows and 0 <= nc < cols) or not mask[nr, nc]:
                continue
            if directions[nr, nc] != code:
                continue
            if distances[nr, nc] >= 0:
                raise ValueError("La red D8 contiene un ciclo durante el análisis de trayectorias.")
            distances[nr, nc] = length + math.hypot(dc * transform[1], dr * transform[5])
            parents[nr, nc] = index
            valid_path[nr, nc] = valid_path[row, col] and np.isfinite(elevations[nr, nc])
            pending.append(nr * cols + nc)
    _b._check_cancel(cancelled)
    path_indices = []
    index = longest
    while index >= 0:
        path_indices.append(index)
        row, col = divmod(index, cols)
        index = int(parents[row, col])
    path_indices.reverse()
    profile_distances = np.empty(len(path_indices), dtype=np.float64)
    profile_heights = np.empty(len(path_indices), dtype=np.float64)
    line = ogr.Geometry(ogr.wkbLineString)
    for number, index in enumerate(path_indices):
        if number % 1000 == 0:
            _b._check_cancel(cancelled)
        row, col = divmod(index, cols)
        profile_distances[number] = distances[row, col]
        profile_heights[number] = elevations[row, col]
        if len(path_indices) > 1:
            line.AddPoint_2D(transform[0] + (col + 0.5) * transform[1],
                             transform[3] + (row + 0.5) * transform[5])
    length = float(profile_distances[-1])
    main_slope = None
    if (length > 0 and np.isfinite(profile_heights).all()
            and profile_heights[-1] > profile_heights[0]):
        main_slope = float((profile_heights[-1] - profile_heights[0]) / length)
    main_tc = (float(0.3 * (length / 1000) ** 0.76 * main_slope ** -0.19)
               if main_slope is not None else None)
    z10 = z85 = segment_slope = None
    if length > 0:
        start, stop = 0.1 * length, 0.85 * length
        z10 = _profile_value(profile_distances, profile_heights, start)
        z85 = _profile_value(profile_distances, profile_heights, stop)
        lower = max(0, int(np.searchsorted(profile_distances, start, side="right")) - 1)
        upper = min(len(profile_heights), int(np.searchsorted(profile_distances, stop, side="left")) + 1)
        complete_interval = bool(np.isfinite(profile_heights[lower:upper]).all())
        if z10 is not None and z85 is not None and complete_interval and z85 > z10:
            segment_slope = (z85 - z10) / (0.75 * length)
    diagnostics = {
        "main_profile_cells": len(path_indices),
        "main_profile_missing_cells": int(np.count_nonzero(~np.isfinite(profile_heights))),
        "z10_m": z10, "z85_m": z85,
        "tc_valid_paths": valid_candidates,
        "tc_nodata_paths": missing_candidates,
        "tc_invalid_slope_paths": invalid_slope_candidates,
        "max_tc_all_paths_candidate_h": best_tc,
        "max_tc_all_paths_length_km": best_length / 1000 if best_length is not None else None,
        "max_tc_all_paths_slope": best_slope,
        "max_tc_all_paths_start_x": None, "max_tc_all_paths_start_y": None,
        "tc_path_length_km": length / 1000,
        "tc_path_slope": main_slope,
        "tc_path_start_x": line.GetPoint(line.GetPointCount() - 1)[0] if length > 0 else None,
        "tc_path_start_y": line.GetPoint(line.GetPointCount() - 1)[1] if length > 0 else None,
        "main_start_z_m": float(profile_heights[-1]) if np.isfinite(profile_heights[-1]) else None,
        "main_outlet_z_m": float(profile_heights[0]) if np.isfinite(profile_heights[0]) else None,
    }
    if best_start is not None:
        row, col = divmod(best_start, cols)
        diagnostics["max_tc_all_paths_start_x"] = transform[0] + (col + 0.5) * transform[1]
        diagnostics["max_tc_all_paths_start_y"] = transform[3] + (row + 0.5) * transform[5]
    return (bytes(line.ExportToWkb()), length, segment_slope,
            main_tc, length, main_slope, diagnostics)


def _runs(flags):
    padded = np.pad(np.asarray(flags, dtype=np.int8), (1, 1))
    changes = np.flatnonzero(np.diff(padded))
    return zip(changes[::2], changes[1::2])


def _grid_lines(mask, transform, cancelled):
    """Merge consecutive collinear grid edges, including internal cell edges."""
    rows, cols = mask.shape
    geometry = ogr.Geometry(ogr.wkbMultiLineString)
    count = 0

    def add(x1, y1, x2, y2):
        nonlocal count
        line = ogr.Geometry(ogr.wkbLineString)
        line.AddPoint_2D(x1, y1)
        line.AddPoint_2D(x2, y2)
        geometry.AddGeometry(line)
        count += 1
        if count % 1000 == 0:
            _b._check_cancel(cancelled)

    for row in range(rows + 1):
        _b._check_cancel(cancelled)
        flags = np.zeros(cols, dtype=bool)
        if row:
            flags |= mask[row - 1] != 0
        if row < rows:
            flags |= mask[row] != 0
        y = transform[3] + row * transform[5]
        for first, stop in _runs(flags):
            add(transform[0] + first * transform[1], y,
                transform[0] + stop * transform[1], y)
    for col in range(cols + 1):
        _b._check_cancel(cancelled)
        flags = np.zeros(rows, dtype=bool)
        if col:
            flags |= mask[:, col - 1] != 0
        if col < cols:
            flags |= mask[:, col] != 0
        x = transform[0] + col * transform[1]
        for first, stop in _runs(flags):
            add(x, transform[3] + first * transform[5],
                x, transform[3] + stop * transform[5])
    return bytes(geometry.ExportToWkb()), count


def analyze_basin(directions_path, dem_path, outlet_x, outlet_y,
                  cancelled=lambda: False, progress=lambda n: None, surface_wkb=None,
                  *, upstream_only=False):
    """Return one delineation with its original-grid terrain characteristics.

    Riley TRI and local maximum-descent D8 slope use complete valid 3x3 DEM
    neighbourhoods, including actual cells immediately outside the basin.
    Invalid neighbourhoods are excluded and counted, never interpolated.
    Tc uses the longest D8 course and its endpoint slope. Tc_10_85 applies
    the same formula to 0.75 L and the slope of that exact profile interval.
    Missing elevations are never bridged. Candidates <= 0.25 h are retained
    only in diagnostics pending calculation by flow segments. These are
    selected-course calculations, not a claim of a full normative route study.
    """
    directions_ds = dem_ds = None
    try:
        _b._check_cancel(cancelled)
        progress(0)
        directions_ds = _b._open_raster(directions_path, "direcciones")
        dem_ds = _b._open_raster(dem_path, "MDT")
        transform, reference = _b._aligned(directions_ds, dem_ds)
        cols, rows = directions_ds.RasterXSize, directions_ds.RasterYSize
        selected = cell_at_point(
            outlet_x, outlet_y, transform[0], transform[3] + rows * transform[5],
            transform[0] + cols * transform[1], transform[3], cols, rows,
        )
        original_selected = selected
        outlet_snap = None
        if upstream_only:
            if surface_wkb is not None:
                raise ValueError('Una superficie ya fijada conserva su cierre; no se vuelve a ajustar.')
            selected, outlet_snap = _b._upstream_section_cell(
                directions_ds.GetRasterBand(1), transform, outlet_x, outlet_y,
                cancelled=cancelled)
        weights = None
        if surface_wkb is None:
            mask, window, count, touches_boundary = _b._visit_upstream(
                directions_ds.GetRasterBand(1), selected.row, selected.col, cancelled, progress)
        else:
            from .terrain_surface import surface_window
            mask, window, count, touches_boundary, weights, area, perimeter = surface_window(
                dem_ds, surface_wkb, (selected.x, selected.y), cancelled, progress)
        local_transform = (transform[0] + window[0] * transform[1], transform[1], 0.0,
                           transform[3] + window[1] * transform[5], 0.0, transform[5])
        if surface_wkb is None:
            geometry_wkb, perimeter = _b._polygonize(mask, local_transform, reference, cancelled)
            z_min, z_max, z_mean = _b._elevations(dem_ds.GetRasterBand(1), window, mask, cancelled)
            area = count * abs(transform[1] * transform[5] - transform[2] * transform[4])
        else:
            geometry_wkb = bytes(surface_wkb)
        halo = _dem_halo(dem_ds.GetRasterBand(1), window, cols, rows, cancelled)
        elevations = halo[1:-1, 1:-1]
        valid_mask = (mask != 0) & np.isfinite(elevations)
        valid_elevations = elevations[valid_mask]
        valid_weights = weights[valid_mask] if weights is not None else None
        if weights is not None:
            from .raster_statistics import weighted_summary
            summary = weighted_summary(valid_elevations, valid_weights) if valid_elevations.size else None
            z_min, z_max, z_mean = ((summary['min_mm'], summary['max_mm'], summary['mean_mm'])
                                   if summary else (None, None, None))
            z_median = summary['median_mm'] if summary else None
        else:
            z_median = float(np.median(valid_elevations)) if valid_elevations.size else None
        result = _b.BasinResult(
            geometry_wkb, reference.ExportToWkt(), area, perimeter, z_min, z_max, z_mean,
            count, selected.width, selected.height, selected.x, selected.y, touches_boundary)
        tri, slope_pct, tri_cells, slope_cells, pit_cells = _terrain_means(
            halo, mask, selected.width, selected.height, cancelled, weights=weights
        )
        local_directions = _b._read(directions_ds.GetRasterBand(1), *window)
        main_wkb, main_length, main_slope, tc, tc_length, tc_slope, diagnostics = _paths(
            local_directions, mask, elevations,
            selected.row - window[1], selected.col - window[0], local_transform, cancelled
        )
        grid_wkb, grid_segments = _grid_lines(mask, local_transform, cancelled)
        tc_candidate = tc
        diagnostics["tc_candidate_h"] = tc_candidate
        if tc_candidate is not None and tc_candidate <= 0.25:
            tc = None
        tc_10_85_candidate = (
            float(0.3 * (0.75 * main_length / 1000) ** 0.76 * main_slope ** -0.19)
            if main_length > 0 and main_slope is not None else None
        )
        diagnostics["tc_10_85_candidate_h"] = tc_10_85_candidate
        diagnostics["tc_10_85_path_length_km"] = 0.75 * main_length / 1000 if main_length > 0 else None
        diagnostics["tc_10_85_path_slope"] = main_slope
        tc_10_85 = (tc_10_85_candidate
                    if tc_10_85_candidate is not None and tc_10_85_candidate > 0.25 else None)
        length = _boundary_length(geometry_wkb, selected.x, selected.y, cancelled)
        shape = shape_metrics(area, perimeter)
        relief = None if z_min is None or z_max is None else z_max - z_min
        hypsometric = ((z_mean - z_min) / relief
                       if relief is not None and relief > 0 and z_mean is not None else None)
        values = {
            "area_km2": area / 1e6, "perimeter_km": perimeter / 1000,
            "cell_resolution": (selected.width, selected.height),
            "cell_count": count, "elongation_ratio": 2 * math.sqrt(area / math.pi) / length,
            "kc": shape.compactness, "ic": shape.circularity,
            "melton": relief / math.sqrt(area) if relief is not None else None,
            "slope_d8_pct": slope_pct, "hypsometric": hypsometric, "tri_m": tri,
            "relief_m": relief, "main_length_km": main_length / 1000,
            "main_length_10_85_km": 0.75 * main_length / 1000 if main_length > 0 else None,
            "tc_h": tc, "tc_10_85_h": tc_10_85,
            "zmin_m": z_min, "zmax_m": z_max, "zmean_m": z_mean, "zmedian_m": z_median,
            "basin_length_km": length / 1000, "main_slope_10_85": main_slope,
            "tc_path_length_km": tc_length / 1000 if tc_length is not None else None,
            "tc_path_slope": tc_slope,
        }
        dem_valid = int(np.count_nonzero((mask != 0) & np.isfinite(elevations)))
        diagnostics.update({
            "hypsometric_curve": build_hypsometric_curve(
                valid_elevations, area / 1e6, exact_hi=hypsometric, weights=valid_weights
            ),
            "dem_valid_cells": dem_valid, "dem_missing_cells": count - dem_valid,
            "tri_valid_cells": tri_cells, "tri_excluded_cells": count - tri_cells,
            "slope_valid_cells": slope_cells, "slope_excluded_cells": count - slope_cells,
            "slope_pit_excluded_cells": pit_cells,
            "slope_d8_definition": "100 × media del máximo descenso/desplazamiento entre 8 vecinos válidos",
            "slope_d8_reference": "https://hydrology.usu.edu/taudem/taudem5/help53/D8FlowDirections.html",
            "taudem_pipeline_executed": False,
            "grid_segments": grid_segments,
            "profile_origin": "cierre", "profile_fractions": (0.10, 0.85),
            "basin_length_definition": "máxima distancia recta cierre-vértice exterior",
        })
        if outlet_snap is not None:
            original_count = count
            if (selected.row, selected.col) != (original_selected.row, original_selected.col):
                _, _, original_count, _ = _b._visit_upstream(
                    directions_ds.GetRasterBand(1), original_selected.row, original_selected.col,
                    cancelled, lambda value: None)
            original_area = original_count * selected.width * selected.height
            outlet_snap.update(reference_area_m2=area, candidate_upstream_area_m2=area,
                               original_cell_upstream_area_m2=original_area,
                               candidate_touches_boundary=bool(touches_boundary),
                               area_change_vs_original_cell_m2=area - original_area,
                               area_change_vs_reference_pct=0.0)
            diagnostics['outlet_snap'] = outlet_snap
        notes = [
            "Lb es la máxima distancia recta del centro de cierre a un vértice exterior de la divisoria.",
            "El tramo 10–85 % se mide desde el cierre sobre el curso D8 de máxima longitud.",
            "TRI Riley y pendiente D8 usan vecindarios completos de 8 vecinos válidos del MDT, con halo exterior.",
            "La pendiente D8 es la media del máximo desnivel/distancia entre ocho vecinos, expresado en %, sobre las cotas originales del MDT. La procedencia del análisis identifica por separado el motor que generó las direcciones de drenaje.",
            "Hmed es la mediana P50 de cotas válidas. El índice hipsométrico sigue usando la media aritmética, no la mediana.",
            "Tc(L) usa el curso D8 de máxima longitud y su pendiente de extremos; Tc(10–85) usa la longitud 0,75·L y la pendiente propia de ese tramo, sin suavizado cartográfico.",
            "Son cálculos sobre el curso elegido. La 5.2-IC requiere tantear recorridos para determinar el tiempo de concentración global; el máximo de todos los recorridos se conserva solo como diagnóstico.",
        ]
        if outlet_snap is not None:
            notes.append(
                'Cierre en la misma sección o aguas arriba del punto original; nunca aguas abajo. '
                f"Desplazamiento a centro de celda: {outlet_snap['distance_m']:.2f} m. "
                'La sección local es perpendicular a la dirección D8; no sustituye una sección topográfica medida. '
                'La cuenca se ha delimitado de nuevo desde ese cierre efectivo.')
        if weights is not None:
            diagnostics['terrain_valid_area_m2'] = float(valid_weights.sum())
            diagnostics['terrain_weighting'] = 'exact_polygon_cell_overlap'
            notes[4] = ('Hmed es la mediana P50 ponderada por el área de intersección. '
                        'El índice hipsométrico usa la media de cotas ponderada por esa misma área.')
            notes.append('Morfología, hipsometría, P₀ y Pd corresponden a la divisoria activa. '
                         'Las estadísticas del MDT ponderan las fracciones de celda por su superficie; '
                         'los recorridos D8 se recalculan con las direcciones nativas, restringidos a las celdas intersectadas y conectadas al cierre.')
        if touches_boundary:
            notes.append("La cuenca toca el borde del ráster; su delimitación puede estar truncada.")
        if dem_valid < count:
            notes.append(f"Se excluyen {count - dem_valid} celdas sin cota válida de las estadísticas del MDT.")
        if tri_cells < count:
            notes.append(f"Se excluyen {count - tri_cells} celdas sin vecindario válido del TRI de Riley.")
        if slope_cells < count:
            notes.append(f"Se excluyen {count - slope_cells} celdas de pendiente D8 por vecindarios incompletos o depresiones locales sin descenso; de ellas, {pit_cells} son depresiones estrictas del MDT sin rellenar.")
        if hypsometric is None:
            notes.append("La integral hipsométrica no está definida por falta de cotas o relieve nulo.")
        if main_slope is None:
            notes.append("La pendiente 10–85 % no está definida: longitud, cotas o desnivel positivo insuficientes; no se atraviesa NoData.")
        for label, candidate in (("Tc(L)", tc_candidate), ("Tc(10–85)", tc_10_85_candidate)):
            if candidate is None:
                notes.append(f"{label} no disponible: longitud, cotas continuas o pendiente positiva insuficientes en su propio tramo.")
            elif candidate <= 0.25:
                notes.append(f"{label} candidato ≤ 0,25 h: no se presenta como Tc normativo válido; requiere cálculo por tramos de flujo según 5.2-IC, apartado 2.2.2.5.")
        _b._check_cancel(cancelled)
        return Analysis(result, values, tuple(notes), diagnostics, main_wkb, grid_wkb,
                        float(outlet_x), float(outlet_y))
    except RuntimeError as error:
        raise ValueError(f"No se ha podido analizar la cuenca: {error}") from error
    finally:
        directions_ds = dem_ds = None
