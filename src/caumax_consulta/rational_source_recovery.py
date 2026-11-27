"""Explicit, verified repair of rational MDT/D8 sources from QGIS products.

The visible QGIS layers and CAUMAX's QSettings-backed calculation paths can
differ. Reconcile them only after a user accepts the proposed correction.
Never resample D8, shift the outlet, or alter the user's other climate rasters.
"""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from osgeo import gdal
from qgis.PyQt.QtCore import QSettings

from .rational_grid_validation import verify_fine_rational_pair


def activate_verified_qgis_pair(layers_module, dem_source, d8_source, x, y):
    """Persist a proven plugin-owned pair and select it for native CAUMAX.

    Caller confirms the change first and verifies project-layer ownership.
    New, uniquely named files leave prior user exports untouched. Settings
    only change after both published rasters pass read-back validation.
    """
    if not (hasattr(layers_module, '_layerPaths')
            and all(hasattr(layers_module, key) for key in ('MDT', 'FLOWDIRS'))):
        raise ValueError('CAUMAX no permite actualizar las fuentes racionales de forma segura.')
    verified = verify_fine_rational_pair(dem_source, d8_source, x, y)
    if not verified:
        raise ValueError('El par de capas activo no es de 25 o 5 m.')
    from .improved_storage import export_directory, record_exports, _compatible
    from .hydro_export import _raster_export, _open_raster
    directory = export_directory().resolve()
    resolution = verified['resolution_m']
    token = uuid4().hex[:12]
    targets = (directory / f'MDT_{resolution}m_{token}.tif',
               directory / f'Direccion_D8_caumax_{resolution}m_{token}.tif')
    products = SimpleNamespace(metadata={'engine': 'TauDEM', 'source': 'QGIS owned drainage results',
                                         'resolution_m': resolution})
    created = []
    try:
        # The export helper verifies georeferencing, values and NoData against
        # the source, then tags the materialised copies for CAUMAX/ESRI use.
        for source_path, target, product, encoding in (
                (dem_source, targets[0], 'ELEVATION', None),
                (d8_source, targets[1], 'FLOW_DIRECTION', 'caumax')):
            raster = _open_raster(source_path)
            try:
                written = _raster_export(products, raster, target, product, encoding, False)
            finally:
                raster = None
            created.append(Path(written))
        verify_fine_rational_pair(*targets, x, y)
        if not _compatible(*targets):
            raise ValueError('Los TIFF publicados no conservan una pareja ESRI-D8 válida.')
        record_exports({'dem': str(targets[0]), 'directions_caumax': str(targets[1])})
    except Exception:
        # None of the native CAUMAX sources have been changed at this point.
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        raise

    keys = (layers_module.MDT, layers_module.FLOWDIRS)
    previous = tuple(layers_module._layerPaths.get(key) for key in keys)
    settings = QSettings()
    try:
        for key, path in zip(keys, targets):
            layers_module._layerPaths[key] = str(path)
            settings.setValue('/caumax/' + str(key), str(path))
        settings.sync()
        if settings.status() != QSettings.NoError:
            raise OSError('No se pudieron guardar las nuevas rutas CAUMAX.')
        # CAUMAX also caches QgsRasterLayer providers; they must no longer
        # expose previous file contents after configuration changes.
        cached = getattr(layers_module, '_layers', None)
        if isinstance(cached, dict):
            for key in keys:
                cached.pop(key, None)
    except Exception:
        for key, old in zip(keys, previous):
            if old is None:
                layers_module._layerPaths.pop(key, None)
                settings.remove('/caumax/' + str(key))
            else:
                layers_module._layerPaths[key] = old
                settings.setValue('/caumax/' + str(key), old)
        settings.sync()
        raise
    return tuple(map(str, targets))
