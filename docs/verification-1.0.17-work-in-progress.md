# Validación pendiente de las correcciones D8 y divisorias

Rama de trabajo: `fix/1.0.17-retencion-d8-export`. Base `main` de 1.0.16. **No constituye una versión publicada** hasta ejecutar las pruebas en QGIS de escritorio y cambiar la versión técnica.

## Contrato esperado

- Se conserva en el proyecto la última divisoria **25 × 25 m** y la última **5 × 5 m**, opcionalmente con su pareja suavizada; no se acumulan otros resultados gestionados de esas mismas resoluciones.
- Cerrar el diálogo *Generación capas resolución mejorada* no elimina esas últimas divisorias de QGIS ni su GeoPackage.
- En QGIS queda **solo un grupo Drenaje TauDEM**: el correspondiente a la resolución seleccionada en el desplegable. Los grupos previos se retiran incluso si eran de la misma resolución.
- Las cachés temporales del MDT y D8 que se reconocen como propias del complemento y ya no tienen usuarios pueden retirarse del disco. No se borran los TIFF/SHP exportados ni fuentes de CAUMAX. Si hay bloqueos del sistema, se registra aviso en el registro de QGIS y no se borran archivos externos.
- La exportación D8 pasa de los códigos TauDEM 1–8 a ESRI 1,2,4,8,16,32,64,128, y preserva el NoData del MDT original. Las direcciones TauDEM en celdas NoData del MDT no deben bloquear la exportación.

## Comprobaciones manuales (QGIS 3, Qt5, Windows)

1. Abrir Método racional y **Generar capas resolución mejorada**. Seleccionar 25 × 25 m y generar el D8; comprobar la divisoria y el grupo TauDEM.
2. Exportar el MDT y **Exportar dirección de drenaje (TIFF)…**. El destino por defecto del TIFF en el perfil estándar es `C:\Users\JM_GIS\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\caumax_consulta\ResolucionMejorada\Direccion_D8_caumax_25m.tif`; la ruta elegida en el diálogo prevalece. Confirmar con Explorador que existe y con QGIS/GDAL que abre, es de una banda y tiene `FLOW_DIRECTION_ENCODING=ESRI_D8`.
3. Cambiar a 5 × 5 m y generar D8. Debe permanecer la última divisoria 25 m, aparecer la última 5 m y desaparecer todo grupo TauDEM de 25 m.
4. Repetir generación 5 m y cambiar el punto o cierre. Debe sustituir la divisoria 5 m anterior; las versiones antiguas se retiran del árbol y se intenta limpiar sus GPKG gestionados cuando no están referenciados.
5. Pulsar **Cerrar**, comprobar que las últimas divisorias 25 m y 5 m permanecen y que sus GeoPackages siguen en el disco. Reabrir y volver a seleccionar 25 m; las cachés temporales que se hubiesen depurado se deben regenerar.

## Pruebas automatizadas incorporadas, pero aún no ejecutadas en Windows

```text
python -m unittest discover -s tests -p test_hydro_export.py -v
python -m unittest discover -s tests -p test_boundary_layers.py -v
python -m unittest discover -s tests -p test_drainage_layers.py -v
python -m unittest discover -s tests -p test_rational_layers_panel.py -v
python -m unittest discover -s tests -p test_resolution_controller.py -v
```

Estas pruebas requieren el Python de QGIS con PyQGIS y GDAL disponibles. No atribuir resultados satisfactorios hasta ejecutarlas.
