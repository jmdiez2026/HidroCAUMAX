"""Continuous cartographic Gaussian smoothing of closed basin boundaries.

The hydrological basin and its metrics are never changed. Each ring is
uniformly resampled once. A periodic Gaussian is applied spectrally, with
sigma = min(cell_width, cell_height) * degree / 15. Its transfer function is
exp(-2*pi**2*sigma**2*frequency**2), continuous even for fractional degrees.
The fixed output budget bounds FFT O(N log N) work and geometry complexity.

If smoothing excludes the supplied closure, a smooth periodic local bump
anchors the nearest boundary of its original polygon exactly to that same
point. This is a cartographic boundary constraint, not movement of the closure.
The result must still pass exact GEOS validity and retain every part and hole;
no makeValid, topology repair or silent strength reduction is performed.
"""

from dataclasses import dataclass
import math
from numbers import Real

import numpy as np
from qgis.core import QgsGeometry, QgsPointXY, QgsWkbTypes


MIN_DEGREE = 0.5
MAX_DEGREE = 45.0
MAX_OUTPUT_VERTICES = 65536
MAX_INPUT_VERTICES = 2000000
_MIN_RING_SAMPLES = 12
_SAMPLES_PER_CELL = 8


@dataclass(frozen=True)
class _Ring:
    spectrum: np.ndarray
    frequencies: np.ndarray
    centre: np.ndarray
    length: float
    count: int


@dataclass(frozen=True)
class _Prepared:
    parts: tuple
    multipart: bool
    topology: tuple
    cell_size: float
    original_area: float
    closure: object
    closure_part: object


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} debe ser un número finito.')
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f'{name} debe ser un número finito.') from error
    if not math.isfinite(value):
        raise ValueError(f'{name} debe ser un número finito.')
    return value


def _degree(value):
    value = _number(value, 'El grado')
    if not MIN_DEGREE <= value <= MAX_DEGREE:
        raise ValueError('El grado de suavizado debe estar entre 0,5 y 45.')
    return value


def _ring_points(ring):
    points = np.array([(point.x(), point.y()) for point in ring], dtype=np.float64)
    if len(points) > 1 and np.array_equal(points[0], points[-1]):
        points = points[:-1]
    if not np.isfinite(points).all():
        raise ValueError('La divisoria contiene coordenadas no finitas.')
    if len(points) > 1:
        keep = np.r_[True, np.any(np.diff(points, axis=0) != 0, axis=1)]
        points = points[keep]
    if len(points) < 3:
        raise ValueError('Un anillo de la divisoria no tiene suficientes vértices distintos.')
    lengths = np.hypot(*(np.roll(points, -1, axis=0) - points).T)
    perimeter = float(np.sum(lengths))
    if not math.isfinite(perimeter) or perimeter <= 0 or np.any(lengths <= 0):
        raise ValueError('Un anillo de la divisoria tiene longitud no válida.')
    return points, lengths, perimeter


def _counts(lengths, spacing):
    ring_count = len(lengths)
    # Reserve one optional closure anchor, in addition to ring closures.
    capacity = MAX_OUTPUT_VERTICES - ring_count - 1
    if ring_count * _MIN_RING_SAMPLES > capacity:
        raise ValueError('Hay demasiados anillos para el límite seguro de vértices.')
    desired = np.array([
        max(_MIN_RING_SAMPLES, min(capacity, math.ceil(length / spacing)))
        if math.isfinite(length / spacing) else capacity
        for length in lengths
    ], dtype=np.int64)
    if int(desired.sum()) <= capacity:
        return desired
    available = capacity - ring_count * _MIN_RING_SAMPLES
    extra = desired - _MIN_RING_SAMPLES
    allocation = available * extra.astype(np.float64) / float(extra.sum())
    counts = np.floor(allocation).astype(np.int64) + _MIN_RING_SAMPLES
    remainder = capacity - int(counts.sum())
    if remainder:
        order = np.argsort(-(allocation - np.floor(allocation)), kind='stable')
        counts[order[:remainder]] += 1
    return counts


def _nearest_projection(points, coordinates):
    following = np.roll(points, -1, axis=0)
    delta = following - points
    length_squared = np.sum(delta * delta, axis=1)
    fraction = np.zeros(len(points), dtype=np.float64)
    np.divide(np.sum((coordinates - points) * delta, axis=1), length_squared,
              out=fraction, where=length_squared > 0)
    np.clip(fraction, 0, 1, out=fraction)
    projections = points + fraction[:, None] * delta
    distances = np.sum((projections - coordinates) ** 2, axis=1)
    index = int(np.argmin(distances))
    return float(distances[index]), index, float(fraction[index]), projections[index]


def _sample(points, lengths, perimeter, count):
    distance = np.arange(count, dtype=np.float64) * (perimeter / count)
    cumulative = np.r_[0.0, np.cumsum(lengths)]
    indices = np.minimum(np.searchsorted(cumulative, distance, side='right') - 1, len(points) - 1)
    fraction = (distance - cumulative[indices]) / lengths[indices]
    following = (indices + 1) % len(points)
    sampled = points[indices] + fraction[:, None] * (points[following] - points[indices])
    centre = np.mean(sampled, axis=0)
    return _Ring(np.fft.rfft(sampled - centre, axis=0),
                 np.fft.rfftfreq(count, d=perimeter / count), centre, perimeter, int(count))


def _prepare(original, cell_width, cell_height, closure):
    if not isinstance(original, QgsGeometry) or original.isNull() or original.isEmpty():
        raise ValueError('La divisoria original está vacía o no es una geometría válida.')
    if QgsWkbTypes.geometryType(original.wkbType()) != QgsWkbTypes.PolygonGeometry:
        raise ValueError('La divisoria original debe ser poligonal.')
    if original.constGet().nCoordinates() > MAX_INPUT_VERTICES:
        raise ValueError('La divisoria supera el límite seguro de vértices de entrada.')
    if not original.isGeosValid() or not math.isfinite(original.area()) or original.area() <= 0:
        raise ValueError('La divisoria original no es válida o no tiene superficie positiva.')
    width, height = _number(cell_width, 'La resolución X'), _number(cell_height, 'La resolución Y')
    if width <= 0 or height <= 0:
        raise ValueError('La resolución de las celdas debe ser positiva.')
    cell_size = min(width, height)
    point = None
    if closure is not None:
        try:
            x, y = float(closure.x()), float(closure.y())
        except (TypeError, ValueError, OverflowError, AttributeError) as error:
            raise ValueError('El punto de cierre no tiene coordenadas válidas.') from error
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('El punto de cierre debe tener coordenadas finitas.')
        point = QgsGeometry.fromPointXY(QgsPointXY(x, y))
        if not original.intersects(point):
            raise ValueError('El punto de cierre no pertenece a la cuenca original.')
    multipart = original.isMultipart()
    parts = original.asMultiPolygon() if multipart else [original.asPolygon()]
    topology = tuple(len(part) - 1 for part in parts)
    closure_part = None
    ring_data = []
    for part_index, part in enumerate(parts):
        if point is not None and closure_part is None and QgsGeometry.fromPolygonXY(part).intersects(point):
            closure_part = part_index
        for ring in part:
            ring_data.append(_ring_points(ring))
    counts = _counts([data[2] for data in ring_data], cell_size / _SAMPLES_PER_CELL)
    prepared_parts, current = [], 0
    for part in parts:
        prepared_rings = []
        for _ in part:
            points, lengths, perimeter = ring_data[current]
            prepared_rings.append(_sample(points, lengths, perimeter, int(counts[current])))
            current += 1
        prepared_parts.append(tuple(prepared_rings))
    return _Prepared(tuple(prepared_parts), multipart, topology, cell_size,
                     float(original.area()), point, closure_part)


def _geometry(parts, multipart):
    converted = []
    for part in parts:
        rings = []
        for points in part:
            ring = [QgsPointXY(float(x), float(y)) for x, y in points]
            ring.append(QgsPointXY(ring[0]))
            rings.append(ring)
        converted.append(rings)
    return (QgsGeometry.fromMultiPolygonXY(converted) if multipart
            else QgsGeometry.fromPolygonXY(converted[0]))


def _validate(geometry, prepared):
    if geometry.isNull() or geometry.isEmpty() or not geometry.isGeosValid():
        raise ValueError('Este grado produce una divisoria no válida. Reduce el grado.')
    if not math.isfinite(geometry.area()) or geometry.area() <= 0:
        raise ValueError('Este grado elimina la superficie de la cuenca. Reduce el grado.')
    parts = geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]
    if tuple(len(part) - 1 for part in parts) != prepared.topology:
        raise ValueError('El suavizado cambia las partes o huecos de la cuenca. Reduce el grado.')
    if geometry.constGet().nCoordinates() > MAX_OUTPUT_VERTICES:
        raise ValueError('El suavizado supera el límite seguro de vértices.')


def _candidate(prepared, degree):
    sigma = prepared.cell_size * (degree / 15.0)
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('El grado y la resolución no definen una distancia de suavizado válida.')
    parts = []
    for part in prepared.parts:
        rings = []
        for ring in part:
            with np.errstate(over='ignore', under='ignore'):
                attenuation = np.exp(-2 * math.pi ** 2 * (sigma * ring.frequencies) ** 2)
            filtered = np.fft.irfft(ring.spectrum * attenuation[:, None], n=ring.count, axis=0)
            rings.append(filtered + ring.centre)
        parts.append(rings)
    geometry = _geometry(parts, prepared.multipart)
    _validate(geometry, prepared)
    if prepared.closure is not None and not geometry.intersects(prepared.closure):
        closure = prepared.closure.asPoint()
        coordinates = np.array([closure.x(), closure.y()])
        part = parts[prepared.closure_part]
        locations = [_nearest_projection(points, coordinates) for points in part]
        nearest = min(range(len(part)), key=lambda index: locations[index][0])
        points = part[nearest]
        _, segment, fraction, projection = locations[nearest]
        ring = prepared.parts[prepared.closure_part][nearest]
        positions = np.arange(ring.count, dtype=np.float64)
        parameter = segment + fraction
        if fraction == 0:
            anchor = segment
        elif fraction == 1:
            anchor = (segment + 1) % ring.count
        else:
            anchor = segment + 1
            points = np.insert(points, anchor, projection, axis=0)
            positions = np.insert(positions, anchor, parameter)
        # Anchoring the segment projection, rather than a nearby vertex,
        # removes tangential jumps when the closure constraint first acts.
        # Its displacement tends to zero as the point approaches the contour.
        scale = max(sigma, 2 * ring.length / ring.count)
        # A periodic von-Mises/Gaussian bump has continuous tangents at the
        # anchor and at the ring seam; it repositions no point of the query.
        kappa = (ring.length / (2 * math.pi * scale)) ** 2
        angle = math.pi * (positions - parameter) / ring.count
        weight = np.exp(-2 * kappa * np.sin(angle) ** 2)
        points += weight[:, None] * (coordinates - projection)
        points[anchor] = coordinates  # Exact coverage, without a geometric buffer.
        part[nearest] = points
        geometry = _geometry(parts, prepared.multipart)
        _validate(geometry, prepared)
        if not geometry.intersects(prepared.closure):
            raise ValueError('No se puede conservar el cierre con este grado. Reduce el grado.')
    return geometry


def smooth_boundary(original, degree, cell_width, cell_height, closure=None):
    """Return a new, strictly valid cartographic polygon at degree 0.5..45.

    Output parts and holes are preserved. The same vertex budget is used at
    every degree, so 10 -> 10.5 changes physical sigma rather than pass count.
    Invalid inputs or a strength incompatible with topology raise ValueError.
    """
    degree = _degree(degree)
    return _candidate(_prepare(original, cell_width, cell_height, closure), degree)


def suggested_degree(original, cell_width, cell_height, closure=None):
    """Highest valid starting degree among 0.5/1/2/3 with <=1% area change.

    If all valid candidates exceed 1%, return the least invasive valid one.
    If none is valid, raise ValueError rather than inventing a repaired shape.
    """
    prepared = _prepare(original, cell_width, cell_height, closure)
    valid, conservative = [], []
    for degree in (0.5, 1.0, 2.0, 3.0):
        try:
            geometry = _candidate(prepared, degree)
        except ValueError:
            continue
        valid.append(degree)
        if abs(geometry.area() / prepared.original_area - 1.0) <= 0.01:
            conservative.append(degree)
    if not valid:
        raise ValueError('No se encuentra un grado inicial compatible con la divisoria y el cierre.')
    return max(conservative) if conservative else min(valid)
