"""Validation shared by live widgets and portable, offline basin sessions."""
import math


STATISTIC_KEYS = {'median_mm', 'max_mm', 'min_mm', 'stddev_mm'}


def weighted_summary(values, weights, mean=None):
    import numpy as np
    values=np.asarray(values,dtype=np.float64)
    weights=np.asarray(weights,dtype=np.float64)
    total=float(weights.sum())
    if not len(values) or total<=0:
        raise ValueError('No hay superficie válida para los estadísticos.')
    mean=float(np.dot(values,weights)/total) if mean is None else float(mean)
    order=np.argsort(values,kind='stable')
    cumulative=np.cumsum(weights[order],dtype=np.float64)
    index=min(len(order)-1,int(np.searchsorted(cumulative,total/2,side='left')))
    median=float(values[order[index]])
    if (index+1<len(order) and math.isclose(float(cumulative[index]),total/2,rel_tol=1e-12,abs_tol=1e-9)):
        median=(median+float(values[order[index+1]]))/2
    return dict(mean_mm=mean,median_mm=median,min_mm=float(values.min()),max_mm=float(values.max()),
                stddev_mm=math.sqrt(max(0.,float(np.dot(weights,(values-mean)**2)/total))))


def normalise_statistics(value, mean):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != STATISTIC_KEYS or mean is None:
        raise ValueError('Los estadísticos de P₀ no contienen los campos esperados.')
    result = {}
    for key in STATISTIC_KEYS:
        raw = value[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError('Los estadísticos de P₀ deben ser números finitos.')
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            raise ValueError('Los estadísticos de P₀ deben ser finitos y no negativos.')
        result[key] = number
    low, high = result['min_mm'], result['max_mm']
    tolerance = max(1e-9, abs(high) * 1e-12)
    if (low > high or not low <= result['median_mm'] <= high
            or not low - tolerance <= mean <= high + tolerance):
        raise ValueError('La media, mediana y extremos de P₀ no son coherentes.')
    return result
