"""Script independiente para QGIS: exportar cuencas de CAUMAX 3.0.

No modifica el complemento CAUMAX original. Detecta la ventana modal
"Método racional", añade un botón y extrae sus polígonos calculados.

INSTRUCCIONES:
1. Abre la consola Python de QGIS antes de entrar en "Método racional".
2. Abre el editor de la consola, carga este archivo y pulsa Ejecutar.
3. Abre "Método racional" en CAUMAX y calcula la cuenca.
4. Pulsa "Guardar cuenca..." en esa ventana.

Hay que activar este script una vez cada vez que inicies QGIS. Si prefieres
que esté siempre disponible, instala el complemento auxiliar en ZIP.
"""

import os

from qgis.PyQt.QtCore import QStandardPaths, QTimer, QVariant
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QPushButton,
)
from qgis.core import (
    QgsFeature,
    QgsField,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)


class CaumaxExportController:
    BUTTON_NAME = "btnExportarCuencaAuxiliar"

    def __init__(self, qgis_iface):
        self.iface = qgis_iface
        self.timer = QTimer(qgis_iface.mainWindow())
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._watch_dialogs)

    def start(self):
        if not self.timer.isActive():
            self.timer.start()
        self._watch_dialogs()

    def stop(self):
        self.timer.stop()
        for dialog in self._rational_dialogs():
            button = dialog.findChild(QPushButton, self.BUTTON_NAME)
            if button is not None:
                button.deleteLater()

    @staticmethod
    def _rational_dialogs():
        """Only the CAUMAX 3.x dialog has this combination of attributes."""
        for widget in QApplication.topLevelWidgets():
            try:
                if (
                    hasattr(widget, "basinCalc")
                    and hasattr(widget, "horizontalLayout_2")
                    and hasattr(widget, "txtX")
                    and hasattr(widget, "txtY")
                    and hasattr(widget, "rubberBand")
                ):
                    yield widget
            except RuntimeError:
                continue

    def _watch_dialogs(self):
        # QDialog.exec_() is modal: the timer still runs in its nested event loop.
        for dialog in self._rational_dialogs():
            try:
                button = dialog.findChild(QPushButton, self.BUTTON_NAME)
                if button is None:
                    button = QPushButton("Guardar cuenca...", dialog)
                    button.setObjectName(self.BUTTON_NAME)
                    button.setToolTip(
                        "Guardar la delimitación roja de CAUMAX "
                        "como Shapefile o GeoPackage"
                    )
                    # Place the button beside Calcular and Cerrar.
                    dialog.horizontalLayout_2.insertWidget(2, button)
                    button.clicked.connect(
                        lambda checked=False, current=dialog: self.export_dialog(current)
                    )

                calculator = getattr(dialog, "basinCalc", None)
                visible = getattr(dialog, "rubberBand", None) is not None
                available = (
                    calculator is not None
                    and bool(getattr(calculator, "basinGeometry", None))
                    and visible
                )
                button.setEnabled(bool(available))
            except RuntimeError:
                # The CAUMAX window may have closed between timer ticks.
                continue

    @staticmethod
    def _show_error(dialog, message):
        QMessageBox.critical(dialog, "CAUMAX · Exportador de cuencas", message)

    def export_dialog(self, dialog):
        """Use the exact geometries CAUMAX draws in red, without recalculating."""
        calculator = getattr(dialog, "basinCalc", None)
        if calculator is None or not getattr(calculator, "basinGeometry", None):
            QMessageBox.warning(
                dialog,
                "CAUMAX · Exportador de cuencas",
                "Primero pulsa 'Calcular variables intermedias' en CAUMAX.",
            )
            return

        # The same filter is used by CAUMAX when drawing the red basin and
        # when making its PDF report (rationalmethoddialog.py, CAUMAX 3.0).
        geometries = [
            QgsGeometry(g)
            for g in calculator.basinGeometry
            if not g.isEmpty() and len(g.asPolygon()) == 1
        ]
        if not geometries:
            self._show_error(dialog, "No se encontraron polígonos exportables.")
            return

        # Store the outlet used in the actual calculation, not a later edit
        # of the X/Y fields in the dialog.
        outlet = getattr(calculator, "pt", None)
        if outlet is None:
            self._show_error(dialog, "No se encontró el punto de cálculo.")
            return
        x, y = float(outlet.x()), float(outlet.y())
        area_total = float(calculator.area) / 1_000_000.0
        area_poligonos = sum(g.area() for g in geometries) / 1_000_000.0

        default_dir = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation
        ) or os.path.expanduser("~")
        suggested = os.path.join(
            default_dir, f"Cuenca_CAUMAX_{round(x)}_{round(y)}.shp"
        )
        destination, selected_filter = QFileDialog.getSaveFileName(
            dialog,
            "Guardar la cuenca delimitada por CAUMAX",
            suggested,
            "ESRI Shapefile (*.shp);;GeoPackage (*.gpkg)",
        )
        if not destination:
            return

        extension = os.path.splitext(destination)[1].lower()
        if extension not in (".shp", ".gpkg"):
            extension = ".gpkg" if "GeoPackage" in selected_filter else ".shp"
            destination += extension

        # Never overwrite previous hydrological results implicitly.
        if os.path.exists(destination):
            QMessageBox.warning(
                dialog,
                "CAUMAX · Exportador de cuencas",
                "Ya existe un archivo con ese nombre. Elige otro nombre "
                "para conservar ambas delimitaciones.",
            )
            return

        driver = "GPKG" if extension == ".gpkg" else "ESRI Shapefile"
        layer = QgsVectorLayer("Polygon?crs=EPSG:25830", "Cuenca_CAUMAX", "memory")
        if not layer.isValid():
            self._show_error(dialog, "No se pudo crear la capa vectorial temporal.")
            return
        provider = layer.dataProvider()
        provider.addAttributes(
            [
                QgsField("PARTE", QVariant.Int),
                QgsField("X_SALIDA", QVariant.Double),
                QgsField("Y_SALIDA", QVariant.Double),
                QgsField("AREA_TOT", QVariant.Double),
                QgsField("AREA_POL", QVariant.Double),
            ]
        )
        layer.updateFields()

        features = []
        for i, geometry in enumerate(geometries, start=1):
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(
                [i, x, y, area_total, geometry.area() / 1_000_000.0]
            )
            features.append(feature)

        added_ok, added_features = provider.addFeatures(features)
        if not added_ok or len(added_features) != len(features):
            self._show_error(dialog, "No se pudieron incorporar todos los polígonos.")
            return
        layer.updateExtents()

        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            destination,
            QgsProject.instance().transformContext(),
            options,
        )
        if result[0] != QgsVectorFileWriter.NoError:
            self._show_error(dialog, f"Error al guardar: {result}")
            return

        saved_layer = QgsVectorLayer(
            destination, os.path.splitext(os.path.basename(destination))[0], "ogr"
        )
        if saved_layer.isValid():
            saved_layer.renderer().setSymbol(
                QgsFillSymbol.createSimple(
                    {
                        "color": "255,0,0,90",
                        "outline_color": "220,0,0,255",
                        "outline_width": "0.3",
                    }
                )
            )
            QgsProject.instance().addMapLayer(saved_layer)

        QMessageBox.information(
            dialog,
            "CAUMAX · Cuenca guardada",
            f"Cuenca guardada en:\n{destination}\n\n"
            f"Superficie calculada por CAUMAX: {area_total:.3f} km²\n"
            f"Superficie de los polígonos: {area_poligonos:.3f} km²\n"
            f"Punto de salida: X={x:.1f}; Y={y:.1f}\n"
            "Sistema de referencia: ETRS89 / UTM 30N (EPSG:25830)",
        )


# Mantener el controlador vivo mientras QGIS esté abierto, incluso si el
# editor ejecuta este archivo en un espacio de nombres temporal.
import qgis.utils

previous = getattr(qgis.utils, "_caumax_export_controller", None)
if previous is not None:
    previous.stop()

qgis.utils._caumax_export_controller = CaumaxExportController(qgis.utils.iface)
qgis.utils._caumax_export_controller.start()
print("CAUMAX Exportador activado: abre Método racional y calcula una cuenca.")
