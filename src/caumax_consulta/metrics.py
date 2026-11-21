"""Dimensionless basin-shape indices from the displayed/exported geometry."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ShapeMetrics:
    compactness: float
    circularity: float


def shape_metrics(area_m2, perimeter_m):
    """Gravelius Kc=P/(2*sqrt(pi*A)); circularity Ic=4*pi*A/P**2.

    Both use the same area and perimeter in metres. The exact circle
    normalization is used, rather than a rounded factor such as 0.28.
    No smoothing or remeasurement is applied to the delineation.
    """
    if isinstance(area_m2, bool) or isinstance(perimeter_m, bool):
        raise ValueError('El área y el perímetro deben ser números positivos.')
    try:
        area, perimeter = float(area_m2), float(perimeter_m)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError('El área y el perímetro deben ser números positivos.') from error
    if not all(math.isfinite(v) and v > 0 for v in (area, perimeter)):
        raise ValueError('El área y el perímetro deben ser positivos y finitos.')
    kc = perimeter / (2 * math.sqrt(math.pi) * math.sqrt(area))
    try:
        ic = (2 * math.sqrt(math.pi) * math.sqrt(area) / perimeter) ** 2
    except OverflowError as error:
        raise ValueError('Los índices de forma no son representables.') from error
    if not all(math.isfinite(v) and v > 0 for v in (kc, ic)):
        raise ValueError('Los índices de forma no son representables.')
    return ShapeMetrics(kc, ic)
