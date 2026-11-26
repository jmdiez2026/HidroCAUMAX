"""Snapshot CAUMAX rational quantiles without changing its inputs or fitting.

The accepted native T/Q is an anchor, including a native GEV result. Other
standard anchors use the native factorsFromRainfall/getFlow functions and the
same basin rainfall and zonal corrections. Interactive estimates interpolate Q
linearly in log(T), through these anchors; they are not a new native GEV fit.
"""
from dataclasses import dataclass
import math

PERIODS = (2, 5, 10, 25, 100, 500)
LIMITED_ZONES = (72, 73, 84, 821, 822)


def number(value, minimum=0):
    result = float(str(value).replace(',', '.'))
    if not math.isfinite(result) or result < minimum:
        raise ValueError('Valor numérico no válido.')
    return result


@dataclass(frozen=True)
class FrequencyPoint:
    period: float
    flow: object
    origin: str
    note: str = ''


@dataclass(frozen=True)
class RationalFrequency:
    points: tuple
    accepted_period: float
    accepted_flow: float

    @property
    def known(self):
        return tuple((p.period, p.flow) for p in self.points if p.flow is not None)

    def flow_at(self, period, allow_extrapolation=False):
        period = number(period, 2)
        known = self.known
        for p, q in known:
            if math.isclose(p, period, rel_tol=1e-12):
                return q
        for (p1, q1), (p2, q2) in zip(known, known[1:]):
            if p1 < period < p2:
                return q1 + (q2 - q1) * math.log(period / p1) / math.log(p2 / p1)
        if allow_extrapolation:
            if len(known) < 2:
                raise ValueError('Se necesitan al menos dos caudales disponibles para extrapolar.')
            (p1, q1), (p2, q2) = known[:2] if period < known[0][0] else known[-2:]
            flow = q1 + (q2 - q1) * math.log(period / p1) / math.log(p2 / p1)
            if not math.isfinite(flow) or flow < 0:
                raise ValueError('La extrapolación produce un caudal negativo o no válido.')
            return flow
        raise ValueError('El periodo debe estar dentro del intervalo con caudales disponibles.')

    def periods_at(self, flow):
        """Return all isolated solutions and plateau intervals, never guess one."""
        flow = number(flow)
        points, intervals = [], []
        known = self.known
        for p, q in known:
            if math.isclose(q, flow, rel_tol=1e-12, abs_tol=1e-12):
                points.append(p)
        for (p1, q1), (p2, q2) in zip(known, known[1:]):
            if q1 == q2:
                if math.isclose(flow, q1, rel_tol=1e-12, abs_tol=1e-12):
                    if intervals and intervals[-1][1] == p1:
                        intervals[-1] = (intervals[-1][0], p2)
                    else:
                        intervals.append((p1, p2))
            elif min(q1, q2) < flow < max(q1, q2):
                fraction = (flow - q1) / (q2 - q1)
                points.append(math.exp(math.log(p1) + fraction * math.log(p2 / p1)))
        unique = []
        for p in sorted(points):
            if any(a <= p <= b for a, b in intervals):
                continue
            if not unique or not math.isclose(p, unique[-1], rel_tol=1e-10):
                unique.append(p)
        if not unique and not intervals:
            raise ValueError('El caudal está fuera de la curva disponible; no se extrapola.')
        return tuple(unique), tuple(intervals)


def capture_frequency(dialog, event):
    """Read a newly accepted native result; cancelled/failed writes are ignored."""
    if not event.get('completed') or 'accepted_flow' not in event:
        return None
    writes, periods = event.get('flow_writes', ()), event.get('period_writes', ())
    if (not writes or not periods or str(writes[-1]) != dialog.txtResultFlow.text()
            or str(periods[-1]) != dialog.txtResultReturnPeriod.text()):
        return None
    accepted_t = number(event['accepted_period'], 2)
    accepted_q = number(event['accepted_flow'])
    if accepted_t > 500:
        return None
    missing = 'No se dispone de los datos necesarios para este periodo.'
    values = {float(p): FrequencyPoint(float(p), None, 'No disponible', missing) for p in PERIODS}
    supporting = event.get('supporting_flows', ())
    # A native custom-period calculation has already calculated these anchors.
    # Retain its exact results rather than querying its layers a second time.
    if len(supporting) in (4, 6):
        for p, q in zip(PERIODS, supporting):
            try:
                values[float(p)] = FrequencyPoint(float(p), number(q), 'Método racional')
            except (ValueError, TypeError):
                pass
        if len(supporting) == 4:
            for p in (100, 500):
                values[float(p)] = FrequencyPoint(float(p), None, 'No aplicable',
                    'CAUMAX limita el método racional a T ≤ 25 años en esta región.')
    else:
        try:
            namespace = dialog.factorsFromRainfall.__func__.__globals__
            sample, zones = namespace['getValueAtPoint'], namespace['ZONES']
            point = dialog.outletPoint()
            zone = sample(zones, point, namespace['ZONE_NAME_FIELD'])
            factor = number(dialog.txtIntensityFactor.text())
            p0 = number(dialog.txtP0.text()) * number(dialog.txtP0Corrector.text())
            area_correction = number(dialog.txtRainfallCorrection.text())
            rain = dialog.basinCalc.rain
            for i, period in enumerate(PERIODS):
                if zone in LIMITED_ZONES and period > 25:
                    values[float(period)] = FrequencyPoint(float(period), None, 'No aplicable',
                        'CAUMAX limita el método racional a T ≤ 25 años en esta región.')
                    continue
                try:
                    correction = number(sample(zones, point, dialog.p0CorrectorsReturnPeriod[i]))
                    intensity, runoff = dialog.factorsFromRainfall(
                        number(rain[period]) * area_correction, p0 * correction, factor)
                    flow = number(dialog.getFlow(runoff, intensity))
                    values[float(period)] = FrequencyPoint(float(period), flow, 'Método racional')
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    # Native formulas can produce a negative runoff for small
                    # rainfall. A missing/invalid quantile must not become zero.
                    values[float(period)] = FrequencyPoint(float(period), None, 'No disponible',
                        'Datos ausentes o caudal no válido para este periodo.')
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
            pass
    values[accepted_t] = FrequencyPoint(accepted_t, accepted_q, 'Cálculo CAUMAX',
        'Periodo elegido en CAUMAX y caudal aceptado, conservados sin recalcular.')
    return RationalFrequency(tuple(values[p] for p in sorted(values)), accepted_t, accepted_q)
