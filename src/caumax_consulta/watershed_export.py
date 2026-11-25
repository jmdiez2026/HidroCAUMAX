"""Build and save the already computed basin and its query attributes."""

import math
import os
from pathlib import Path

from qgis.PyQt.QtCore import QStandardPaths, QVariant
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsFeature,
    QgsField, QgsGeometry, QgsProject, QgsVectorFileWriter, QgsVectorLayer,
    QgsWkbTypes,
)

from .metrics import shape_metrics


_SHAPEFILE_SUFFIXES = frozenset((
    ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx",
    ".fbn", ".fbx", ".ain", ".aih", ".ixs", ".mxs", ".xml", ".idm",
    ".ind", ".qpj", ".shp.xml", ".shp.aux.xml", ".dbf.aux.xml",
))


def _double_field(name, precision=10):
    return QgsField(name, QVariant.Double, "", 32, precision)


def make_layer(result, query):
    """Return one memory MultiPolygon with values from result and query.

    The basin geometry, area, perimeter, closure, and resolution are never
    recomputed here. Its geometric area is only checked against the reported
    cell area to catch accidental export of the polygonized background.
    """
    crs = QgsCoordinateReferenceSystem.fromWkt(result.crs_wkt)
    if not crs.isValid():
        raise ValueError("La cuenca no tiene un sistema de referencia válido.")
    geometry = QgsGeometry()
    geometry.fromWkb(bytes(result.geometry_wkb))
    if (geometry.isNull() or geometry.isEmpty() or
            QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry):
        raise ValueError("La geometría de la cuenca debe ser poligonal y no estar vacía.")
    if not geometry.isMultipart():
        if not geometry.convertToMultiType():
            raise ValueError("No se ha podido preparar la geometría multipolígono.")
    if not geometry.isGeosValid():
        raise ValueError("La geometría calculada de la cuenca no es válida.")
    try:
        area = float(result.area_m2)
        perimeter = float(result.perimeter_m)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("La superficie y el perímetro de la cuenca no son válidos.") from error
    if not math.isfinite(area) or not math.isfinite(perimeter) or area <= 0 or perimeter <= 0:
        raise ValueError("La superficie y el perímetro deben ser positivos y finitos.")
    if not math.isclose(geometry.area(), area, rel_tol=1e-8, abs_tol=1e-4):
        raise ValueError("La superficie de la geometría no coincide con la cuenca calculada.")
    if query.cell is None:
        raise ValueError("No se conocen la fila y la columna de la celda consultada.")
    metrics = shape_metrics(area, perimeter)
    rational = getattr(query, 'source_kind', 'caumax_raster') == 'rational'
    origin = 'Método racional' if rational else 'CAUMAX'
    period_text = 'T pendiente' if query.period is None else 'T = %s años' % query.period
    layer = QgsVectorLayer("MultiPolygon", "Cuenca %s · %s" % (origin, period_text), "memory")
    if not layer.isValid():
        raise ValueError("No se ha podido crear la capa de la cuenca.")
    layer.setCrs(crs)
    fields = [
        _double_field("Q_M3S", 15), _double_field("T_ANOS"),
        _double_field("X_CIERRE"), _double_field("Y_CIERRE"),
        _double_field("AREA_KM2"), _double_field("PERIM_KM"),
        _double_field("KC_GRAV"), _double_field("IC_CIRC"),
        QgsField("FILA_MDT" if rational else "FILA_Q", QVariant.LongLong, "", 20),
        QgsField("COL_MDT" if rational else "COL_Q", QVariant.LongLong, "", 20),
        QgsField("N_CELDAS", QVariant.LongLong, "", 20),
        _double_field("RES_X_M"), _double_field("RES_Y_M"),
    ]
    if rational:
        fields.extend([QgsField('ORIGEN', QVariant.String, '', 24),
                       QgsField('ESTADO_Q', QVariant.String, '', 16)])
    provider = layer.dataProvider()
    if not provider.addAttributes(fields):
        raise ValueError("No se han podido crear los atributos de la cuenca.")
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(geometry)
    attributes = [
        query.flow, query.period, result.outlet_x, result.outlet_y,
        area / 1e6, perimeter / 1000, metrics.compactness, metrics.circularity,
        int(query.cell.row) + 1, int(query.cell.col) + 1, int(result.cell_count),
        result.cell_width, result.cell_height,
    ]
    if rational:
        attributes.extend(['Metodo racional', 'pendiente' if query.flow is None else 'calculado'])
    feature.setAttributes(attributes)
    added, _ = provider.addFeatures([feature])
    if not added:
        raise ValueError("No se ha podido añadir la geometría a la capa de la cuenca.")
    layer.updateExtents()
    return layer


def _check_new_destination(destination):
    destination = Path(os.fspath(destination))
    extension = destination.suffix.lower()
    if extension not in (".shp", ".gpkg"):
        raise ValueError("El archivo debe tener extensión .shp o .gpkg.")
    if not destination.parent.is_dir():
        raise ValueError("La carpeta de destino no existe.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("El archivo ya existe. Elige otro nombre para guardar la cuenca.")
    if extension == ".shp":
        stem = destination.stem.lower()
        for sibling in destination.parent.iterdir():
            name = sibling.name.lower()
            if (name in {stem + suffix for suffix in _SHAPEFILE_SUFFIXES} or
                    (name.startswith(stem + ".") and name.endswith(".atx"))):
                raise ValueError("Ya existe un archivo auxiliar de este shapefile. Elige otro nombre.")
    elif any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Ya existen archivos auxiliares de este GeoPackage. Elige otro nombre.")
    return destination, extension


def write_layer(layer, destination):
    """Write SHP/GPKG in UTF-8 and return its valid saved layer.

    Existing target files and shapefile sidecars are refused. This function
    neither adds layers to the QGIS project nor transforms their coordinates.
    """
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        raise ValueError("La capa que se quiere guardar no es válida.")
    destination, extension = _check_new_destination(destination)
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile" if extension == ".shp" else "GPKG"
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    options.layerName = destination.stem
    options.forceMulti = True
    written = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(destination), QgsCoordinateTransformContext(), options
    )
    if written[0] != QgsVectorFileWriter.NoError:
        raise ValueError("No se ha podido guardar la cuenca: %s" % (written[1] or written[0]))
    actual_path = written[2] or str(destination)
    uri = actual_path
    if extension == ".gpkg":
        uri += "|layername=" + (written[3] or destination.stem)
    saved = QgsVectorLayer(uri, layer.name(), "ogr")
    if not saved.isValid():
        raise ValueError("El archivo se ha escrito, pero no se puede abrir como capa válida.")
    if saved.featureCount() != layer.featureCount():
        raise ValueError("La capa guardada no contiene todas las geometrías de la cuenca.")
    return saved


def suggested_filename(query):
    origin = 'Racional' if getattr(query, 'source_kind', 'caumax_raster') == 'rational' else 'CAUMAX'
    period = '' if query.period is None else '_T%s' % format(query.period, 'g')
    return 'cuenca_' + origin + period + '.shp'


def save_result(parent, result, query):
    """Ask for a destination, save the basin, and add it to the current project."""
    directory = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    suggested = os.path.join(directory, suggested_filename(query))
    destination, selected_filter = QFileDialog.getSaveFileName(
        parent, "Guardar cuenca CAUMAX", suggested,
        "Shapefile (*.shp);;GeoPackage (*.gpkg)",
        options=QFileDialog.DontConfirmOverwrite,
    )
    if not destination:
        return None
    if not Path(destination).suffix:
        destination += ".gpkg" if "*.gpkg" in selected_filter else ".shp"
    try:
        saved = write_layer(make_layer(result, query), destination)
        from .layer_order import add_result_layer
        if add_result_layer(saved) is None:
            raise ValueError("La cuenca se ha guardado, pero no se ha podido añadir al proyecto.")
        return saved
    except Exception as error:
        QMessageBox.critical(parent, "CAUMAX · Guardar cuenca", str(error))
        return None
