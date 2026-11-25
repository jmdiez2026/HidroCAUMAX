"""Provisional, area-scaled initiation thresholds for a D8 drainage network.

A contributing-area cutoff determines the *mapped* drainage density, not the
observed location of channel heads. These constants are plugin heuristics,
not a legal/design rule or a substitute for cartographic/field verification.
All values are in square metres until the QGIS spin box converts them to km².
"""
import math


INITIAL_BASIN_FRACTION = 0.005      # 0.5% of the delineated contributing area
MAX_INITIAL_FRACTION = 0.05        # avoid excessive minimum on tiny basins
INITIAL_CELL_FLOOR = 8            # avoid making every raster cell a channel
MANUAL_STEP_FRACTION = 0.0001     # 0.01% of the delineated contributing area
MAX_AUTOMATIC_REDUCTIONS = 4       # at most five bounded TauDEM attempts


def _positive(value, name):
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f'{name} debe ser positivo y finito.') from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'{name} debe ser positivo y finito.')
    return number


def recommended_threshold(area_m2, cell_area_m2):
    """Return a cell-aligned *initial* threshold, never a mapped river claim."""
    area = _positive(area_m2, 'La superficie de cuenca')
    cell = _positive(cell_area_m2, 'La superficie de celda')
    proposed = min(max(INITIAL_CELL_FLOOR * cell,
                       INITIAL_BASIN_FRACTION * area),
                   MAX_INITIAL_FRACTION * area)
    return max(1, math.ceil(proposed / cell)) * cell


def manual_step(area_m2, cell_area_m2):
    """Small proportional arrow adjustment, at least one original DEM cell."""
    area = _positive(area_m2, 'La superficie de cuenca')
    cell = _positive(cell_area_m2, 'La superficie de celda')
    return max(cell, MANUAL_STEP_FRACTION * area)


def automatic_threshold_cells(area_m2, cell_area_m2):
    """Successive narrower networks; duplicates from raster quantization removed."""
    cell = _positive(cell_area_m2, 'La superficie de celda')
    count = max(1, math.ceil(recommended_threshold(area_m2, cell) / cell - 1e-10))
    choices = []
    for _ in range(MAX_AUTOMATIC_REDUCTIONS + 1):
        if count not in choices:
            choices.append(count)
        if count == 1:
            break
        count = max(1, count // 2)
    return tuple(choices)
