"""Recompute an explicitly chosen native D8 outlet without rerunning its engine."""

from dataclasses import replace
from copy import deepcopy
import math
from pathlib import Path

from osgeo import ogr, osr

from . import basin as _b
from .dem_hydrology import _check, _positive_finite, _preflight_analysis
from .grid import cell_at_point
from .terrain import analyze_basin


def _reference_overlap(analysis, context, cancelled):
    """Compare actual watershed footprints, not cell counts or target areas.

    This is only enforced when a finer-resolution consultation has an actual
    coarse watershed to compare against. It must not move the manual outlet.
    """
    if not context.get('verify_reference_overlap'):
        return None
    try:
        raw = bytes.fromhex(context['reference_geometry_wkb_hex'])
        reference = ogr.CreateGeometryFromWkb(raw)
        candidate = ogr.CreateGeometryFromWkb(bytes(analysis.geometry_wkb))
        source, target = osr.SpatialReference(), osr.SpatialReference()
        if (reference is None or candidate is None
                or source.ImportFromWkt(context['reference_crs_wkt']) != 0
                or target.ImportFromWkt(analysis.crs_wkt) != 0):
            raise ValueError('La geometría de referencia no se puede interpretar.')
        source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not source.IsSame(target):
            if reference.Transform(osr.CoordinateTransformation(source, target)) != ogr.OGRERR_NONE:
                raise ValueError('La referencia no se puede transformar al CRS del MDT.')
        _check(cancelled)
        intersection = reference.Intersection(candidate)
        if intersection is None:
            raise ValueError('No se puede calcular la intersección de las divisorias.')
        reference_area, candidate_area = float(reference.GetArea()), float(candidate.GetArea())
        common_area = float(intersection.GetArea())
        if (not all(math.isfinite(v) for v in (reference_area, candidate_area, common_area))
                or reference_area <= 0 or candidate_area <= 0 or common_area < 0):
            raise ValueError('Las superficies de comparación no son válidas.')
    except (ValueError, KeyError, TypeError, RuntimeError) as error:
        raise ValueError('No se pudo verificar la cuenca manual frente a la '
                         'divisoria original: ' + str(error)) from error
    coverage, overlap = common_area / reference_area, common_area / candidate_area
    return dict(reference_coverage_fraction=coverage,
                candidate_overlap_fraction=overlap,
                reference_overlap_area_m2=common_area,
                reference_basin_area_m2=reference_area,
                candidate_basin_area_m2=candidate_area,
                reference_correspondence_valid=coverage > .5 and overlap > .5)


def build_manual_outlet(context, x, y, cancelled=lambda: False, publish=lambda percent: None):
    """Build one result for a user-selected cell anywhere in the native raster.

    The user may move in any direction, including downstream. The original
    query coordinates remain unchanged; the selected cell controls every basin
    metric and the displayed main course. The automatic search radius remains
    diagnostic context, not a restriction on an explicit manual choice.
    ``publish`` receives progress percentages. This function never changes map
    layers, settings, source rasters or the D8 cache.
    """
    _check(cancelled)
    publish(0)
    if not isinstance(context, dict):
        raise ValueError('Falta el contexto de la selección manual del cierre.')
    try:
        original_x, original_y = float(context['original_x']), float(context['original_y'])
        requested_x, requested_y = float(x), float(y)
        directions_path = str(Path(context['directions_path']).expanduser().resolve())
        dem_path = str(Path(context['dem_path']).expanduser().resolve())
        radius = _positive_finite(context['search_radius_m'], 'El radio automático de referencia')
        reference_area = _positive_finite(context['reference_area_m2'], 'El área original de referencia')
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError('El contexto de selección manual está incompleto.') from error
    if not all(math.isfinite(value) for value in (original_x, original_y, requested_x, requested_y)):
        raise ValueError('Las coordenadas de selección manual deben ser finitas.')
    directions = dem = None
    try:
        directions = _b._open_raster(directions_path, 'direcciones de la selección manual')
        dem = _b._open_raster(dem_path, 'MDT de la selección manual')
        transform, _ = _b._aligned(directions, dem)
        rows, cols = directions.RasterYSize, directions.RasterXSize
        bounds = (transform[0], transform[3] + rows * transform[5],
                  transform[0] + cols * transform[1], transform[3])
        selected = cell_at_point(requested_x, requested_y, *bounds, cols, rows)
        original = cell_at_point(original_x, original_y, *bounds, cols, rows)
        distance = math.hypot(selected.x - original_x, selected.y - original_y)
        band = directions.GetRasterBand(1)
        value = float(_b._read(band, selected.col, selected.row, 1, 1)[0, 0])
        if (not math.isfinite(value) or value == band.GetNoDataValue()
                or (not band.GetMaskFlags() & _b.gdal.GMF_ALL_VALID
                    and not _b._read(band.GetMaskBand(), selected.col, selected.row, 1, 1)[0, 0])):
            raise ValueError('La celda elegida no tiene dirección D8 válida (NoData).')
        if value not in _b._VALID_CODES:
            raise ValueError('La celda elegida contiene un código de dirección D8 desconocido.')
        dem_band = dem.GetRasterBand(1)
        height = float(_b._read(dem_band, selected.col, selected.row, 1, 1)[0, 0])
        if (not math.isfinite(height) or height == dem_band.GetNoDataValue()
                or (not dem_band.GetMaskFlags() & _b.gdal.GMF_ALL_VALID
                    and not _b._read(dem_band.GetMaskBand(), selected.col, selected.row, 1, 1)[0, 0])):
            raise ValueError('La celda elegida no tiene cota MDT válida (NoData).')
        _check(cancelled)
        publish(10)
        count, window_cells, boundary, feasibility = _preflight_analysis(directions_path, selected, cancelled)
        if boundary and context.get('require_complete_coverage'):
            raise ValueError(
                'La cuenca del cierre elegido alcanza el borde del MDT refinado. '
                'No se publica una divisoria posiblemente cortada por la extensión del ráster. '
                'Amplía la cobertura del MDT o revisa la red D8.')
        if (selected.row, selected.col) == (original.row, original.col):
            original_count = count
        else:
            visit = _b._visit_upstream(band, original.row, original.col, cancelled, lambda n: None)
            original_count = visit[2]
            del visit
    finally:
        directions = dem = None
    _check(cancelled)
    publish(25)
    analysis = analyze_basin(
        directions_path, dem_path, selected.x, selected.y, cancelled=cancelled,
        progress=lambda n: publish(25 + min(70, int(70 * n / max(count, 1)))), upstream_only=False)
    cell_area = selected.width * selected.height
    candidate_area, original_area = count * cell_area, original_count * cell_area
    if analysis.area_m2 != candidate_area or analysis.cell_count != count:
        raise ValueError('La cuenca recalculada no coincide con la celda manual seleccionada.')
    correspondence = _reference_overlap(analysis, context, cancelled)
    mismatch = bool(correspondence and not correspondence['reference_correspondence_valid'])
    override = bool(context.get('allow_reference_mismatch', False))
    if mismatch and not override:
        raise ValueError(
            'El cierre pertenece a una cuenca diferente de la consulta de referencia: '
            'solape con la referencia ' +
            f"{100 * correspondence['reference_coverage_fraction']:.1f} %, " +
            'solape de la cuenca elegida ' +
            f"{100 * correspondence['candidate_overlap_fraction']:.1f} %. " +
            'Puede ser una ladera o un ramal lateral sin conexión D8 con el cauce '
            'principal. Elige otra celda del cauce o, si quieres estudiar '
            'deliberadamente una cuenca distinta, marca la excepción expresa.')
    snap = dict(
        method='manual_cell_d8', selection_mode='user_selected_cell', explicit_user_choice=True,
        manual_selection_scope='raster_extent', manual_raster_bounds=list(bounds),
        manual_raster_size=[cols, rows],
        manual_requested_x=requested_x, manual_requested_y=requested_y,
        original_x=original_x, original_y=original_y,
        snapped_x=selected.x, snapped_y=selected.y,
        original_row=original.row, original_col=original.col,
        snapped_row=selected.row, snapped_col=selected.col,
        status='unchanged' if (selected.row, selected.col) == (original.row, original.col) else 'snapped',
        distance_m=distance, search_radius_m=radius,
        minimum_upstream_area_m2=cell_area, reference_area_m2=reference_area,
        candidate_upstream_area_m2=candidate_area, original_cell_upstream_area_m2=original_area,
        candidate_touches_boundary=bool(boundary),
        reference_mismatch_override=bool(mismatch and override),
        area_change_vs_original_cell_m2=candidate_area - original_area,
        area_change_vs_reference_pct=100 * (candidate_area / reference_area - 1),
        policy='explicit user choice of any valid native D8 cell inside the raster extent; '
               'the automatic search radius is retained only as a reference; '
               'basin, main course and metrics are recomputed together')
    provenance = context.get('metadata')
    metadata = dict(provenance) if isinstance(provenance, dict) else {}
    metadata.update(native_resolution=(selected.width, selected.height),
                    raster_cells=rows * cols, analysis_window_cells=window_cells,
                    basin_cells=count, raster_boundary_touched=bool(boundary),
                    manual_outlet_selected=True, memory_feasibility=feasibility,
                    outlet_snap=deepcopy(snap))
    if correspondence is not None:
        metadata['manual_reference_overlap'] = correspondence
        snap['reference_correspondence'] = correspondence
        metadata['outlet_snap'] = deepcopy(snap)
    metadata.pop('coverage_warning', None)
    if boundary:
        metadata['coverage_warning'] = (
            'La cuenca del cierre manual alcanza el borde del MDT. Su cobertura puede ser '
            'incompleta; comprueba que el MDT incluya toda la superficie aportante.')
    diagnostic = dict(analysis.diagnostics)
    if metadata.get('engine'):
        diagnostic['dem_hydrology'] = metadata
    diagnostic['outlet_snap'] = snap
    notes = (*analysis.notes,
             f'Cierre elegido explícitamente por el usuario en la celda nativa '
             f'({selected.x:.3f}, {selected.y:.3f}), a {distance:.2f} m del punto original. '
             'La selección manual puede situarse en cualquier dirección, también aguas abajo. '
             'Su límite es la extensión válida del MDT; el radio automático se conserva solo como referencia. '
             'La divisoria, el curso principal y todos los parámetros se han recalculado con esa celda; '
             'el punto original de consulta permanece registrado.')
    if mismatch and override:
        notes += ('Se ha aceptado expresamente un cierre de cuenca distinta a '
                  'la referencia original. La correspondencia espacial no supera '
                  'la mayoría estricta de ambas divisorias; no se debe presentar '
                  'como simple refinamiento de la misma cuenca.',)
    analysis = replace(analysis, selected_x=original_x, selected_y=original_y,
                       diagnostics=diagnostic, notes=notes)
    _check(cancelled)
    publish(100)
    return dict(analysis=analysis, directions_path=directions_path, dem_path=dem_path, metadata=metadata)
