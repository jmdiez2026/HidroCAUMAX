"""Compact hypsometric samples from the elevations already read for a basin.

The sampled quantile curve is a display approximation. The hypsometric integral
is retained separately and uses the arithmetic mean of every valid cell.
"""

import math

import numpy as np


CURVE_SAMPLE_COUNT = 201


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def build_hypsometric_curve(valid_elevations, area_km2, exact_hi=None, weights=None):
    """Return JSON-safe descending elevation quantiles and exact scalar HI.

    ``fraction`` runs from zero to one: at a sampled elevation it describes the
    cumulative fraction of area above that elevation. Multiplying it by the
    basin's ``area_km2`` gives the dimensional horizontal axis. Elevation
    statistics use valid cells only; the basin area itself is not redefined by
    missing elevations. No raster or hydrological calculation is performed.
    """
    elevations = np.asarray(valid_elevations, dtype=np.float64).ravel()
    finite = np.isfinite(elevations)
    if weights is not None:
        weights = np.asarray(weights, dtype=np.float64).ravel()
        if weights.shape != elevations.shape or not np.all(np.isfinite(weights)) or np.any(weights <= 0):
            raise ValueError('Pesos de superficie inválidos para la curva hipsométrica.')
        weights = weights[finite]
    elevations = elevations[finite]
    result = {
        "fraction": [],
        "elevation_m": [],
        "area_km2": _finite_number(area_km2),
        "valid_count": int(elevations.size),
        "zmin_m": None,
        "zmax_m": None,
        "hi_exact": None,
        "auc_approx": None,
        "sample_count": 0,
        "method": (
            "201 cuantiles lineales descendentes. AUC aproximada de la curva; "
            "el HI exacto usa la media aritmética de todas las cotas válidas."
        ),
        "population": "valid_cells",
    }
    if not elevations.size:
        return result

    fractions = np.linspace(0.0, 1.0, CURVE_SAMPLE_COUNT)
    if weights is None:
        sampled = np.percentile(elevations, (1.0 - fractions) * 100.0, method="linear")
    else:
        order = np.argsort(elevations, kind='stable')
        positions = (np.cumsum(weights[order]) - weights[order]/2)/float(weights.sum())
        sampled = np.interp(1.0-fractions, positions, elevations[order])
        result['method'] = 'Cuantiles de cotas ponderados por área de intersección de celda; HI exacto calculado con todos los pesos.'
        result['population'] = 'valid_cell_area'
    z_min, z_max = float(np.min(elevations)), float(np.max(elevations))
    result.update({
        "fraction": fractions.tolist(),
        "elevation_m": sampled.tolist(),
        "zmin_m": z_min,
        "zmax_m": z_max,
        "sample_count": CURVE_SAMPLE_COUNT,
    })
    relief = z_max - z_min
    if relief > 0:
        calculated_hi = float((np.average(elevations, weights=weights) - z_min) / relief)
        supplied_hi = _finite_number(exact_hi)
        result["hi_exact"] = supplied_hi if supplied_hi is not None else calculated_hi
        normalized = (sampled - z_min) / relief
        integrate = getattr(np, "trapezoid", None) or np.trapz
        result["auc_approx"] = float(integrate(normalized, fractions))
    return result
