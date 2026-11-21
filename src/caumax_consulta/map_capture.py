"""Render a complete basin overview without changing the live QGIS map."""
import math
from pathlib import Path
from qgis.PyQt.QtCore import QBuffer, QEventLoop, QIODevice, QSize, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMapRendererParallelJob,
    QgsMapSettings, QgsMarkerSymbol, QgsPointXY, QgsProject, QgsRasterLayer, QgsVectorLayer,
    QgsWkbTypes,
)

from .course_layers import _course_geometries, course_symbol
from .drainage_layers import OWNER_PROPERTY as DRAINAGE_OWNER_PROPERTY, build_network_layer


RENDER_TIMEOUT_MS = 20000
OUTPUT_SIZE = (1400, 900)
MIN_GRID_CELL_PIXELS = 3.0
# A timed-out render must keep its objects alive until cancellation finishes.
# Destroying a still-running render job would otherwise block or invalidate it.
_PENDING_RENDERS = {}


def _geometry(wkb, polygon=False):
    try:
        geometry = QgsGeometry()
        geometry.fromWkb(bytes(wkb))
    except (TypeError, ValueError, RuntimeError) as error:
        raise ValueError('No se puede leer una geometría del mapa de localización.') from error
    if geometry.isNull() or geometry.isEmpty():
        raise ValueError('La geometría del mapa de localización está vacía.')
    if polygon and (QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry
                    or not geometry.isGeosValid()):
        raise ValueError('La divisoria del mapa de localización no es un polígono válido.')
    return geometry


def _layer(geometry, crs, name, symbol):
    layer = QgsVectorLayer(QgsWkbTypes.displayString(geometry.wkbType()), name, 'memory')
    layer.setCrs(crs)
    feature = QgsFeature()
    feature.setGeometry(geometry)
    ok, _ = layer.dataProvider().addFeatures([feature])
    if not layer.isValid() or not ok:
        raise ValueError('No se ha podido preparar la capa de localización.')
    layer.updateExtents()
    layer.renderer().setSymbol(symbol)
    return layer


def _snap_details(analysis):
    diagnostic = (getattr(analysis, 'diagnostics', {}) or {}).get('outlet_snap')
    if diagnostic is None:
        return None
    if not isinstance(diagnostic, dict):
        raise ValueError('El diagnóstico del ajuste de cierre no es válido.')
    try:
        x, y = float(diagnostic['original_x']), float(diagnostic['original_y'])
        if not all(math.isfinite(v) for v in (x, y, analysis.outlet_x, analysis.outlet_y)):
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError('El ajuste de cierre no conserva coordenadas originales válidas.') from error
    # Both points are expressed in MDT CRS. The basin outlet is authoritative
    # even if an earlier candidate left another snapped_x/y in metadata.
    distance = math.hypot(analysis.outlet_x - x, analysis.outlet_y - y)
    return {'original': QgsPointXY(x, y), 'distance_m': distance,
            'status': str(diagnostic.get('status', ''))}


def _cell_pixels(analysis, original, transform, settings):
    """Actual displayed cell size, including metric-to-geographic map CRS."""
    try:
        width, height = float(analysis.cell_width), float(analysis.cell_height)
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            return 0.0
        centre = original.boundingBox().center()
        projected = transform.transform(centre)
        east = transform.transform(QgsPointXY(centre.x()+width, centre.y()))
        north = transform.transform(QgsPointXY(centre.x(), centre.y()+height))
        map_units = min(math.hypot(east.x()-projected.x(), east.y()-projected.y()),
                        math.hypot(north.x()-projected.x(), north.y()-projected.y()))
        return map_units/settings.mapUnitsPerPixel()
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _terrain_background(analysis, query):
    path = getattr(query, 'dem_path', '')
    if not path or not Path(path).is_file():
        return [], 'El MDT activo no está disponible; se conserva el contexto cartográfico accesible.'
    from .dem_display import build_terrain_layers
    try:
        layers = build_terrain_layers(path, elevation_range=(getattr(analysis, 'z_min', None),
                                                            getattr(analysis, 'z_max', None)))
    except (OSError, ValueError, RuntimeError):
        return [], 'No se ha podido preparar el relieve del MDT activo; se conserva el contexto accesible.'
    return list(layers), ''


def _managed_terrain_layer(layer):
    return (layer.customProperty('hidrocaumax/dem_display_role', None) in ('hillshade','hypsometry')
            and layer.customProperty('hidrocaumax/dem_source', None) is not None)


def _render(settings, keep_alive):
    """Bound waiting time; cancellation retains job/layers until finished."""
    job = QgsMapRendererParallelJob(settings)
    key = id(job)
    _PENDING_RENDERS[key] = (job, settings, tuple(keep_alive))
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    expired = [False]

    def release():
        QTimer.singleShot(0, lambda: _PENDING_RENDERS.pop(key, None))

    def timeout():
        expired[0] = True
        job.cancelWithoutBlocking()
        loop.quit()

    job.finished.connect(loop.quit)
    job.finished.connect(release)
    timer.timeout.connect(timeout)
    try:
        job.start()
        if job.isActive():
            timer.start(RENDER_TIMEOUT_MS)
            loop.exec_()
        if expired[0]:
            raise TimeoutError('La captura del mapa ha superado el tiempo de espera. Comprueba las capas visibles y vuelve a intentarlo.')
        if job.isActive():
            job.cancelWithoutBlocking()
            raise ValueError('La captura del mapa se ha cancelado antes de terminar.')
        image = job.renderedImage()
        if image.isNull():
            raise ValueError('QGIS no ha podido generar la imagen de localización.')
        errors = list(job.errors())
        return image, errors
    except Exception:
        if not job.isActive():
            _PENDING_RENDERS.pop(key, None)
        raise
    finally:
        timer.stop()


def capture_basin_map(iface, analysis, query, smoothing_state):
    """Return PNG bytes and georeferencing of a whole-basin overview.

    The visible layers provide context. Own memory layers reproduce the cached
    basin contours and outlet, which ordinary canvas screenshots omit when
    these are rubber bands. No layer is registered in QgsProject and neither
    the live extent nor the selected layers are changed.
    """
    canvas = iface.mapCanvas()
    settings = QgsMapSettings(canvas.mapSettings())
    source_crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
    if not source_crs.isValid():
        raise ValueError('La cuenca no tiene un sistema de referencia válido para su localización.')
    destination_crs = settings.destinationCrs()
    if not destination_crs.isValid():
        destination_crs = QgsCoordinateReferenceSystem(source_crs)
        settings.setDestinationCrs(destination_crs)
    state = smoothing_state or {}
    original = _geometry(state.get('base_geometry_wkb', analysis.geometry_wkb), polygon=True)
    cached = state.get('geometry_wkb') or {}
    original_checked = bool(state.get('original_checked', True))
    smooth_checked = bool(state.get('smooth_checked', False))
    contours = {}
    if original_checked:
        contours['original'] = _geometry(cached.get('original', analysis.geometry_wkb), polygon=True)
    if smooth_checked:
        if not cached.get('suavizada'):
            raise ValueError('Espera a que se actualice la divisoria suavizada antes de generar el mapa.')
        contours['suavizada'] = _geometry(cached['suavizada'], polygon=True)
    reference_only = not contours
    if reference_only:
        contours['original'] = original

    snapped = _snap_details(analysis)
    effective_point = QgsGeometry.fromPointXY(QgsPointXY(analysis.outlet_x, analysis.outlet_y))
    point_geometries = [effective_point]
    if snapped is not None:
        point_geometries.append(QgsGeometry.fromPointXY(snapped['original']))

    transform = QgsCoordinateTransform(source_crs, destination_crs, QgsProject.instance())
    extent = None
    for geometry in [original] + list(contours.values()) + point_geometries:
        transformed = QgsGeometry(geometry)
        try:
            transformed.transform(transform)
        except Exception as error:
            raise ValueError('No se puede transformar la cuenca al CRS del mapa.') from error
        box = transformed.boundingBox()
        if extent is None:
            extent = box
        else:
            extent.combineExtentWith(box)
    if extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        raise ValueError('La extensión de la cuenca no permite generar su localización.')
    extent.scale(1.15)

    settings.setRotation(0)
    settings.setOutputSize(QSize(*OUTPUT_SIZE))
    settings.setOutputDpi(120)
    settings.setBackgroundColor(QColor('white'))
    settings.setExtent(extent)
    mesh_available = bool(getattr(analysis, 'grid_wkb', b''))
    show_grid = (mesh_available and bool(state['cell_grid_visible']) if 'cell_grid_visible' in state
                 else bool(mesh_available and original_checked
                           and _cell_pixels(analysis, original, transform, settings) >= MIN_GRID_CELL_PIXELS))

    own_layers = []
    if 'original' in contours:
        own_layers.append(_layer(contours['original'], source_crs, 'Cuenca original',
            QgsFillSymbol.createSimple({'color':'220,30,30,51','outline_color':'195,20,20,255','outline_width':'0.5'})))
    if show_grid:
        mesh = _geometry(analysis.grid_wkb)
        own_layers.insert(0, _layer(mesh, source_crs, 'Celdas de la cuenca',
            QgsLineSymbol.createSimple({'line_color':'200,20,20,95','line_width':'0.12'})))
    if 'suavizada' in contours:
        own_layers.insert(0, _layer(contours['suavizada'], source_crs, 'Divisoria suavizada',
            QgsFillSymbol.createSimple({'color':'0,0,0,0','outline_color':'0,90,190,255','outline_width':'0.7'})))
    # Use the archived network for this analysis variant, including offline
    # projects; live canvas layers may belong to another resolution.
    drainage = state.get('drainage_state')
    network_visible = isinstance(drainage, dict) and bool(drainage.get(
        'network_visible', (drainage.get('metadata') or {}).get('visible', True)))
    if network_visible:
        own_layers.insert(0, build_network_layer(drainage))
    # Live QGIS course layers may belong to a different MDT variant while a
    # project saves all variants. Rebuild courses from this exact analysis,
    # never from whichever course happens to be active on the live canvas.
    visibility = {'main': True, 'central': False}
    requested_visibility = state.get('course_visibility')
    if isinstance(requested_visibility, dict):
        visibility.update({kind: bool(requested_visibility[kind]) for kind in visibility if kind in requested_visibility})
    courses = _course_geometries(analysis)
    visible_courses = []
    for kind, name in (
        ('main', 'Curso principal total (L)'),
        ('central', 'Tramo del curso principal 10–85 % (0,75 L)'),
    ):
        if visibility[kind] and courses[kind] is not None:
            own_layers.insert(0, _layer(courses[kind], source_crs, name, course_symbol(kind)))
            visible_courses.append(kind)
    if snapped is not None:
        if snapped['distance_m'] > 1e-9:
            displacement = QgsGeometry.fromPolylineXY([snapped['original'], QgsPointXY(analysis.outlet_x, analysis.outlet_y)])
            own_layers.insert(0, _layer(displacement, source_crs, 'Desplazamiento del cierre',
                QgsLineSymbol.createSimple({'line_color':'105,105,105,230','line_width':'0.4','line_style':'dash'})))
        own_layers.insert(0, _layer(point_geometries[-1], source_crs, 'Punto de consulta original',
            QgsMarkerSymbol.createSimple({'name':'circle','color':'255,255,255,0','outline_color':'215,90,0,255',
                                          'outline_width':'0.7','size':'5.5'})))
    own_layers.insert(0, _layer(effective_point, source_crs, 'Sección de cierre ajustada' if snapped is not None else 'Sección de cierre',
        QgsMarkerSymbol.createSimple({'name':'cross2','color':'0,70,180,255','outline_color':'0,70,180,255',
                                      'outline_width':'0.7','size':'4.8'})))
    base_layers = [layer for layer in settings.layers()
                   if layer.customProperty('HidroCAUMAX/course_owner', None) is None
                   and layer.customProperty(DRAINAGE_OWNER_PROPERTY, None) is None
                   and not _managed_terrain_layer(layer)]
    terrain_layers, terrain_note = _terrain_background(analysis, query)
    if terrain_layers:
        # Keep contextual vectors/labels visible above the active terrain;
        # other raster basemaps remain underneath and outside its coverage.
        context_vectors = [layer for layer in base_layers if not isinstance(layer,QgsRasterLayer)]
        context_rasters = [layer for layer in base_layers if isinstance(layer,QgsRasterLayer)]
        render_layers = own_layers + context_vectors + terrain_layers + context_rasters
    else:
        render_layers = own_layers + base_layers
    settings.setLayers(render_layers)
    image, errors = _render(settings, render_layers)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    try:
        if not image.save(buffer, 'PNG'):
            raise ValueError('No se ha podido codificar la imagen de localización.')
        png = bytes(buffer.data())
    finally:
        buffer.close()
    visible_extent = settings.visibleExtent()
    crs_label = destination_crs.authid() or destination_crs.description()
    legend = []
    if 'original' in contours:
        legend.append('original en rojo')
    if 'suavizada' in contours:
        legend.append('suavizada en azul')
    caption = ('Localización de la cuenca completa; ' + ', '.join(legend)
               + '; cruz en la sección de cierre. CRS: ' + crs_label + '.')
    if snapped is not None:
        distance = f"{snapped['distance_m']:.2f}".replace('.', ',')
        caption += (' Punto original: círculo naranja; cierre ajustado: cruz azul. '
                    'Desplazamiento: ' + distance + ' m'
                    + (' (misma celda)' if snapped['status'] == 'unchanged' else '') + '.')
        if snapped['distance_m'] > 1e-9:
            caption += ' La línea gris discontinua une ambas posiciones.'
    if 'main' in visible_courses:
        caption += ' Curso principal total en azul claro con contorno azul oscuro.'
    if 'central' in visible_courses:
        caption += ' Tramo 10–85 % en azul claro discontinuo.'
    if network_visible:
        caption += ' Red de drenaje TauDEM de esta consulta en azul claro.'
    if terrain_layers:
        caption += ' Fondo: MDT activo con rampa natural y sombreado multidireccional de sus elevaciones reales.'
    elif terrain_note and getattr(query,'dem_path',''):
        caption += ' ' + terrain_note
    if mesh_available and not show_grid and 'cell_grid_visible' not in state:
        caption += ' Malla omitida a esta escala (celda menor de 3 px) para conservar la legibilidad del relieve.'
    if reference_only:
        caption += ' Sin divisoria activa: se muestra la original como referencia de localización.'
    if not base_layers and not terrain_layers:
        caption += ' Sin capas de contexto visibles; se representa la geometría georreferenciada.'
    if errors:
        caption += ' Algunas capas de contexto no se han podido representar completamente.'
    return {'map_png': png, 'map_caption': caption,
            'map_extent': {'xmin': visible_extent.xMinimum(), 'ymin': visible_extent.yMinimum(),
                           'xmax': visible_extent.xMaximum(), 'ymax': visible_extent.yMaximum(),
                           'crs_wkt': destination_crs.toWkt()}}
