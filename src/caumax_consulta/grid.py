"""Raster cell indexing with no QGIS dependency.

The raster is axis aligned, with row zero at its northern edge. West and
north edges belong to the grid; east and south edges do not. Coordinates
are never snapped to a neighbouring cell. ``Cell.width`` and ``height``
are cell sizes in map units, not the raster's column and row counts.
"""

from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Integral, Real


@dataclass(frozen=True)
class Cell:
    """Zero-based row/column, cell centre, and cell sizes in map units."""

    row: int
    col: int
    x: float
    y: float
    width: float
    height: float


def _finite_real(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} debe ser un número real finito.")
    try:
        value = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} debe ser un número real finito.") from error
    if not math.isfinite(value):
        raise ValueError(f"{name} debe ser un número real finito.")
    return value


def _positive_dimension(value, name):
    if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
        raise ValueError(f"{name} debe ser un entero positivo.")
    return int(value)


def cell_at_point(x, y, xmin, ymin, xmax, ymax, width, height):
    """Return the cell containing a point in a north-up raster.

    ``width`` and ``height`` are positive integer raster dimensions.
    The accepted domain is ``xmin <= x < xmax`` and ``ymin < y <= ymax``.
    Invalid numbers, dimensions, extent, or outside points raise ValueError.
    Rational arithmetic for the indices prevents floating-point cancellation
    from moving a point just inside an edge into an adjacent/outside cell.
    """
    x = _finite_real(x, "x")
    y = _finite_real(y, "y")
    xmin = _finite_real(xmin, "xmin")
    ymin = _finite_real(ymin, "ymin")
    xmax = _finite_real(xmax, "xmax")
    ymax = _finite_real(ymax, "ymax")
    width = _positive_dimension(width, "width")
    height = _positive_dimension(height, "height")
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("La extensión debe tener anchura y altura positivas.")
    if not (xmin <= x < xmax and ymin < y <= ymax):
        raise ValueError("El punto está fuera de la extensión de la malla.")

    west, south = Fraction(xmin), Fraction(ymin)
    east, north = Fraction(xmax), Fraction(ymax)
    cell_width = (east - west) / width
    cell_height = (north - south) / height
    col = int((Fraction(x) - west) // cell_width)
    row = int((north - Fraction(y)) // cell_height)
    centre_x = west + (Fraction(col) + Fraction(1, 2)) * cell_width
    centre_y = north - (Fraction(row) + Fraction(1, 2)) * cell_height
    try:
        sizes = float(cell_width), float(cell_height)
        centre = float(centre_x), float(centre_y)
    except OverflowError as error:
        raise ValueError("La celda no es representable con números finitos.") from error
    if not all(math.isfinite(value) and value > 0 for value in sizes):
        raise ValueError("El tamaño de celda debe ser positivo y finito.")
    return Cell(row, col, centre[0], centre[1], sizes[0], sizes[1])


