"""Native DEM hypsometry and live multidirectional terrain shading.

Both unregistered layers read the same actual GeoTIFF. QGIS derives hillshade
on demand at the requested map scale; no background image, resampled DEM or
hillshade file is generated. Styling never changes hydrological elevations.
"""

import math
from pathlib import Path

import numpy as np
from osgeo import gdal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.core import (
    QgsBilinearRasterResampler, QgsColorRampShader, QgsHillshadeRenderer, QgsRasterLayer,
    QgsRasterShader, QgsSingleBandPseudoColorRenderer,
)

from . import basin as _b


ROLE_PROPERTY = "hidrocaumax/dem_display_role"
SOURCE_PROPERTY = "hidrocaumax/dem_source"
RESOLUTION_PROPERTY = "hidrocaumax/dem_resolution"
HILLSHADE_OPACITY = 0.45
# Relative stops, from vegetated low ground to warm stone. No white snow cap.
NATURAL_RAMP = (
    (0.00, "#476B3F"), (0.18, "#65834A"), (0.38, "#8F9857"),
    (0.58, "#B4A36A"), (0.76, "#BA9A73"), (0.90, "#A99E89"),
    (1.00, "#CFCCBD"),
)


def smooth_terrain_display(layer):
    """Smooth enlarged display pixels while retaining the native raster grid."""
    if (not isinstance(layer, QgsRasterLayer)
            or layer.customProperty(ROLE_PROPERTY) not in ('hypsometry', 'hillshade')):
        return False
    resampling = layer.resampleFilter()
    current = resampling.zoomedInResampler() if resampling is not None else None
    if resampling is None or (current is not None and current.type() == 'bilinear'):
        return False
    resampling.setZoomedInResampler(QgsBilinearRasterResampler())
    layer.triggerRepaint()
    return True


def apply_visible_elevation_range(layer, low, high):
    """Restretch only our existing colour renderer; never change source data."""
    if (layer.customProperty(ROLE_PROPERTY) != 'hypsometry'
            or not isinstance(layer.renderer(), QgsSingleBandPseudoColorRenderer)):
        return False
    low, high = float(low), float(high)
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        return False
    if layer.customProperty('hidrocaumax/dem_visible_range_m') == [low, high]:
        return False
    renderer = layer.renderer()
    shader = renderer.shader()
    ramp = shader.rasterShaderFunction() if shader is not None else None
    if not isinstance(ramp, QgsColorRampShader):
        return False
    bottom, top = (low, high) if low < high else (low-.5, high+.5)
    shader.setMinimumValue(bottom)
    shader.setMaximumValue(top)
    renderer.setClassificationMin(bottom)
    renderer.setClassificationMax(top)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(bottom + fraction*(top-bottom), QColor(colour),
                                       f'{bottom + fraction*(top-bottom):g} m')
        for fraction, colour in NATURAL_RAMP
    ])
    layer.setCustomProperty('hidrocaumax/dem_visible_range_m', [low, high])
    layer.rendererChanged.emit()
    layer.triggerRepaint()
    return True


def _elevation_range(dataset, supplied):
    if supplied is not None:
        try:
            low, high = supplied
            if low is not None and high is not None:
                low, high = float(low), float(high)
                if math.isfinite(low) and math.isfinite(high) and low <= high:
                    return low, high
                raise ValueError("El rango de cotas para representar el MDT no es válido.")
        except (TypeError, OverflowError) as error:
            raise ValueError("El rango de cotas debe contener dos números finitos.") from error
    # Read-only nearest-neighbour sampling for styling limits, never a DEM
    # transformation. Avoid ComputeStatistics/PAM writes to the source file.
    band = dataset.GetRasterBand(1)
    width, height = min(dataset.RasterXSize, 256), min(dataset.RasterYSize, 256)
    values = band.ReadAsArray(buf_xsize=width, buf_ysize=height)
    if values is None:
        raise ValueError("No se pueden leer cotas del MDT para representarlo.")
    valid = np.isfinite(values)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= values != nodata
    if not band.GetMaskFlags() & gdal.GMF_ALL_VALID:
        mask = band.GetMaskBand().ReadAsArray(buf_xsize=width, buf_ysize=height)
        valid &= mask != 0
    if not np.any(valid):
        raise ValueError("El MDT no contiene cotas válidas para representarlo.")
    return float(np.min(values[valid])), float(np.max(values[valid]))


def build_terrain_layers(dem_path, *, elevation_range=None):
    """Return [hillshade, hypsometry] in map drawing order, without registration.

    The GeoTIFF must have a projected metric north-up grid and metre heights.
    elevation_range optionally reuses the active analysis' (z_min,z_max), avoiding
    extra pixel reads. Otherwise <=65536 actual DEM values set styling limits.
    Missing limits fall back to sampling; invalid limits raise ValueError.
    A flat DEM has one uniform colour, not an invented altitudinal variation.
    The caller owns layer registration, visibility, replacement and removal.
    """
    source = Path(dem_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError("El MDT activo no está disponible para representar el relieve.")
    dataset = _b._open_raster(source, "MDT de representación")
    try:
        if dataset.GetDriver().ShortName != "GTiff":
            raise ValueError("El fondo de relieve debe proceder del GeoTIFF MDT real.")
        transform, _ = _b._aligned(dataset, dataset)
        low, high = _elevation_range(dataset, elevation_range)
        resolution = (float(transform[1]), float(-transform[5]))
    finally:
        dataset = None
    size = f"{resolution[0]:g}×{resolution[1]:g} m"
    options = QgsRasterLayer.LayerOptions()
    options.loadDefaultStyle = False
    # QGIS can calculate default provider statistics while opening a raster.
    # Keep that read from producing a .aux.xml beside the user's DEM; this is
    # thread-local, restored immediately, and not a global QGIS/GDAL setting.
    previous_pam = gdal.GetThreadLocalConfigOption("GDAL_PAM_ENABLED")
    gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", "NO")
    try:
        colours = QgsRasterLayer(str(source), f"HidroCAUMAX · Altitud MDT {size}", "gdal", options)
        hillshade = QgsRasterLayer(str(source), f"HidroCAUMAX · Relieve MDT {size}", "gdal", options)
    finally:
        gdal.SetThreadLocalConfigOption("GDAL_PAM_ENABLED", previous_pam)
    if not colours.isValid() or not hillshade.isValid():
        raise ValueError("QGIS no puede representar el MDT activo.")
    # A nonzero shader interval makes the flat terrain render its middle colour.
    display_low, display_high = (low, high) if low < high else (low - .5, high + .5)
    ramp = QgsColorRampShader(display_low, display_high)
    ramp.setColorRampType(QgsColorRampShader.Interpolated)
    ramp.setClip(False)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(
            display_low + fraction * (display_high - display_low), QColor(colour),
            f"{display_low + fraction * (display_high - display_low):g} m")
        for fraction, colour in NATURAL_RAMP
    ])
    shader = QgsRasterShader(display_low, display_high)
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(colours.dataProvider(), 1, shader)
    renderer.setClassificationMin(display_low)
    renderer.setClassificationMax(display_high)
    colours.setRenderer(renderer)
    colours.setOpacity(1.0)
    shade = QgsHillshadeRenderer(hillshade.dataProvider(), 1, 315.0, 45.0)
    shade.setMultiDirectional(True)
    shade.setZFactor(1.0)
    hillshade.setRenderer(shade)
    hillshade.setBlendMode(QPainter.CompositionMode_Multiply)
    hillshade.setOpacity(HILLSHADE_OPACITY)
    for layer, role in ((hillshade, "hillshade"), (colours, "hypsometry")):
        layer.setCustomProperty(ROLE_PROPERTY, role)
        layer.setCustomProperty(SOURCE_PROPERTY, str(source))
        layer.setCustomProperty(RESOLUTION_PROPERTY, list(resolution))
        layer.setCustomProperty("hidrocaumax/dem_range_m", [low, high])
        layer.setCustomProperty("hidrocaumax/dem_native_grid", True)
        smooth_terrain_display(layer)
        metadata = layer.metadata()
        metadata.setAbstract("MDT original a resolución nativa; altitudes en metros. "
                             "Representación cartográfica sin modificar datos ni parámetros hidrológicos.")
        layer.setMetadata(metadata)
        layer.triggerRepaint()
    return [hillshade, colours]


def build_dem_display(dem_path, cache_dir=None, *, elevation_range=None):
    """Compatibility entry point; live QGIS shading needs no disk cache."""
    return build_terrain_layers(dem_path, elevation_range=elevation_range)
