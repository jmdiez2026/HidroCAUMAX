"""Bounded per-cell rainfall profiles for correct offline Pd(T) statistics."""
import base64
from functools import lru_cache
import math
import zlib
import numpy as np

MAX_PROFILE_CELLS=200_000
_KEYS={'count','grid_sha256','data'}


def encode_profile(values,weights,cells,grid_sha256):
    count=len(values)
    if not 0<count<=MAX_PROFILE_CELLS:
        return None
    payload=(np.asarray(cells,dtype='<u8').tobytes()+np.asarray(weights,dtype='<f8').tobytes()
             +np.asarray(values,dtype='<f8').tobytes())
    return dict(count=count,grid_sha256=grid_sha256,
                data=base64.b64encode(zlib.compress(payload,6)).decode('ascii'))


@lru_cache(maxsize=12)
def _decode(count,grid,data):
    if (isinstance(count,bool) or not isinstance(count,int) or not 0<count<=MAX_PROFILE_CELLS
            or not isinstance(grid,str) or len(grid)!=64 or any(c not in '0123456789abcdef' for c in grid)
            or not isinstance(data,str) or len(data)>MAX_PROFILE_CELLS*33):
        raise ValueError('El perfil de celdas de precipitación no es válido o excede su límite.')
    compressed=base64.b64decode(data,validate=True)
    decoder=zlib.decompressobj()
    raw=decoder.decompress(compressed,count*24+1)
    if len(raw)!=count*24 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('El perfil de precipitación está dañado o tiene un tamaño incoherente.')
    cells=np.frombuffer(raw,dtype='<u8',count=count)
    weights=np.frombuffer(raw,dtype='<f8',count=count,offset=count*8)
    values=np.frombuffer(raw,dtype='<f8',count=count,offset=count*16)
    if (not np.isfinite(weights).all() or not np.isfinite(values).all()
            or np.any(weights<=0) or np.any(values<0) or np.any(cells[1:]<=cells[:-1])):
        raise ValueError('El perfil de precipitación contiene celdas o valores inválidos.')
    return cells,weights,values


def decode_profile(profile):
    if not isinstance(profile,dict) or set(profile)!=_KEYS:
        raise ValueError('El perfil de precipitación no contiene los campos esperados.')
    return _decode(profile['count'],profile['grid_sha256'],profile['data'])


def normalise_profile(profile,mean,area):
    if profile is None:
        return None
    cells,weights,values=decode_profile(profile)
    total=float(weights.sum())
    if (mean is None or not math.isclose(total,area,rel_tol=1e-7,abs_tol=1e-5)
            or not math.isclose(float(np.dot(weights,values)/total),mean,rel_tol=1e-7,abs_tol=1e-7)):
        raise ValueError('El perfil de precipitación no corresponde a la media y superficie válidas.')
    return dict(profile)


def statistics_for_period(state):
    from .raster_statistics import weighted_summary
    period=float(state['input_period'])
    known=sorted((float(key),result) for key,result in state['rainfall_results'].items()
                 if result.get('mean_mm') is not None)
    for value,result in known:
        if math.isclose(period,value,rel_tol=1e-12,abs_tol=1e-12):
            stats=result.get('statistics')
            if stats is not None:
                return dict(mean_mm=result['mean_mm'],**stats)
            return None
    if len(known)<2:
        return None
    pairs=list(zip(known,known[1:]))
    pair=next((pair for pair in pairs if pair[0][0]<=period<=pair[1][0]),None)
    if pair is None:
        if not state['extrapolate']:
            return None
        pair=pairs[0] if period<known[0][0] else pairs[-1]
    (low,left),(high,right)=pair
    a,b=left.get('distribution'),right.get('distribution')
    if not a or not b or a['grid_sha256']!=b['grid_sha256']:
        return None
    ids_a,weights_a,values_a=decode_profile(a)
    ids_b,weights_b,values_b=decode_profile(b)
    common,ia,ib=np.intersect1d(ids_a,ids_b,assume_unique=True,return_indices=True)
    if not len(common):
        return None
    weights=np.minimum(weights_a[ia],weights_b[ib])
    fraction=math.log(period/low)/math.log(high/low)
    values=values_a[ia]+fraction*(values_b[ib]-values_a[ia])
    if not np.isfinite(values).all() or np.any(values<0):
        return None
    return weighted_summary(values,weights)
