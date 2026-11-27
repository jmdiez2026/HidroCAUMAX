"""Portable, clearly visible yellow outlet markers for HidroCAUMAX.

The clicked coordinate and the accepted D8 outlet are different when a cell
is snapped. Preserve both; never silently shift a user-picked section.
"""
import math
from pathlib import Path

from qgis.PyQt.QtCore import QVariant
from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
                       QgsFeature, QgsField, QgsGeometry, QgsMarkerSymbol,
                       QgsPointXY, QgsProject, QgsVectorFileWriter, QgsVectorLayer)

CLOSURE_ROLE = 'HidroCAUMAX/closure_point'
CLOSURE_LABEL = 'Punto de cierre · selección'
SHAPE_LABEL = 'Sección de cierre · guardada'


def closure_symbol():
    """Bright yellow 5 mm circle and contrasting near-black outline."""
    return QgsMarkerSymbol.createSimple({
        'name': 'circle', 'color': '255,232,0,255',
        'outline_style': 'solid', 'outline_color': '23,35,49,255',
        'outline_width': '0.65', 'outline_width_unit': 'MM',
        'size': '5', 'size_unit': 'MM'
    })


def _finite(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('El cierre debe tener coordenadas finitas.')
    return number


def make_closure_layer(x, y, crs, *, accepted_x=None, accepted_y=None, label=CLOSURE_LABEL):
    """Make an unregistered QGIS point from the exact selected UTM coordinate."""
    x, y = _finite(x), _finite(y)
    crs = QgsCoordinateReferenceSystem(crs)
    if not crs.isValid() or crs.isGeographic():
        raise ValueError('La sección de cierre necesita un CRS proyectado válido.')
    accepted_x = x if accepted_x is None else _finite(accepted_x)
    accepted_y = y if accepted_y is None else _finite(accepted_y)
    layer = QgsVectorLayer('Point', label, 'memory')
    layer.setCrs(crs)
    provider = layer.dataProvider()
    fields = [QgsField(name, QVariant.Double, '', 24, 9)
              for name in ('X_PICADO', 'Y_PICADO', 'X_CIERRE', 'Y_CIERRE')]
    if not provider.addAttributes(fields):
        raise ValueError('No se pudieron definir los atributos del punto de cierre.')
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
    feature.setAttributes([x, y, accepted_x, accepted_y])
    ok, _ = provider.addFeatures([feature])
    if not ok:
        raise ValueError('No se pudo crear el punto de cierre.')
    layer.updateExtents()
    layer.renderer().setSymbol(closure_symbol())
    layer.setCustomProperty(CLOSURE_ROLE, True)
    layer.setCustomProperty('HidroCAUMAX/selected_point_x', x)
    layer.setCustomProperty('HidroCAUMAX/selected_point_y', y)
    return layer


def analysis_closure_layer(analysis):
    """Coordinates of mouse click if retained, accepted D8 centre as attributes."""
    crs = QgsCoordinateReferenceSystem.fromWkt(analysis.crs_wkt)
    x = getattr(analysis, 'selected_x', None)
    y = getattr(analysis, 'selected_y', None)
    return make_closure_layer(
        analysis.outlet_x if x is None else x,
        analysis.outlet_y if y is None else y,
        crs, accepted_x=analysis.outlet_x, accepted_y=analysis.outlet_y)


def export_closure(x, y, crs, destination, *, label=SHAPE_LABEL):
    """Write an exact one-point SHP and register it in QGIS; no overwrite."""
    from .watershed_export import _check_new_destination
    from .layer_order import add_result_layer
    from .generated_cleanup import register_dataset

    target, suffix = _check_new_destination(destination)
    if suffix != '.shp':
        raise ValueError('Guardar Cierre requiere un shapefile .shp.')
    memory = make_closure_layer(x, y, crs, label=label)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'ESRI Shapefile'
    options.fileEncoding = 'UTF-8'
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        memory, str(target), QgsCoordinateTransformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise ValueError('No se pudo guardar el punto de cierre: ' + str(result[1]))
    saved = QgsVectorLayer(str(target), label, 'ogr')
    if not saved.isValid() or saved.featureCount() != 1:
        raise ValueError('El shapefile no contiene el punto de cierre esperado.')
    saved.renderer().setSymbol(closure_symbol())
    saved.setCustomProperty(CLOSURE_ROLE, True)
    saved.setCustomProperty('HidroCAUMAX/selected_point_x', float(x))
    saved.setCustomProperty('HidroCAUMAX/selected_point_y', float(y))
    if add_result_layer(saved) is None:
        raise ValueError('El punto se guardó, pero QGIS no pudo cargarlo.')
    register_dataset(target)
    return target, saved
