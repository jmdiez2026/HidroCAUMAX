# Arranque sin recuperación automática de resultados

27 de noviembre de 2026. HidroCAUMAX v.1.0, revisión técnica 1.0.15.

## Causa y corrección

NativeCaumaxPlugin.initGui llamaba a restore_exports al activar el complemento. Si una copia del perfil enumeraba MDT_25m.tif y .hidrocaumax-generated-files.json, esos archivos reaparecían en la instalación aunque el usuario no hubiera exportado nada en esa sesión. Se confirmó que el MDT recuperado coincidía byte a byte con la copia anterior.

El arranque llama ahora a prepare_export_directory, que prepara el directorio y el alias compatible de P0 fuera de él. No lee ni restaura la copia de resultados. El registro de archivos generados se sigue escribiendo después de exportaciones correctas. El arranque tampoco borra exportaciones existentes.

El empaquetado exige exactamente el TIFF de P0 y su .tif.aux.xml en ResolucionMejorada, y rechaza resultados, registros, archivos de pruebas o cualquier otro archivo en esa carpeta. El instalador Windows comprueba expresamente la misma lista después de una instalación nueva.

Se deja de crear el fondo de relieve de 500 × 500 m en TerrainLayers, antes de construir o registrar sus rásteres. La activación y la apertura de proyectos retiran las capas de relieve de 500 m marcadas como propias y su grupo, conservando archivos, el MDT nativo de CAUMAX y cualquier capa ajena dentro del grupo. Las capas mejoradas de 25 y 5 m mantienen su comportamiento.

## Verificación reproducible

Con Python de QGIS 3.44.15, Qt 5, perfil temporal, src y tests en PYTHONPATH y QT_QPA_PLATFORM=offscreen:

```text
python -m unittest discover -s tests -p test_release_packaging.py -v
python -m unittest discover -s tests -p test_improved_storage.py -v
python -m unittest discover -s tests -p test_native_toolbar.py -v
python -m unittest discover -s tests -p test_native_calculation_layers.py -v
python -m unittest discover -s tests -p test_automatic_update.py -v
python -m unittest discover -s tests -p test_map_services.py -v
python -m unittest discover -s tests -p test_future_qgis_updates.py -v
python -m unittest discover -s tests -p test_manual_dialog.py -v
python -m unittest discover -s tests -p test_terrain_layers.py -v
python -m unittest discover -s tests -p test_rational_basin_dialog.py -v
```

148 pruebas distintas: 147 correctas y una omitida por falta de permiso para crear enlaces simbólicos. Se cubren copia antigua con los dos archivos denunciados, activaciones repetidas, ausencia de índices antes de exportar, exportación posterior, conservación de resultados nuevos, rechazo de archivos adicionales y ausencia de uno de los dos archivos P0 en el paquete. Las pruebas de relieve comprueban que 500 m no llega al constructor de rásteres y que la retirada de copias antiguas conserva las capas ajenas, el MDT original y los bytes del archivo. El diálogo racional se comprueba con su configuración manual.

En un QGIS de escritorio con perfil aislado startup115 se prepara una instalación de ensayo que contiene solo los dos archivos originales de P0, con una copia anterior que enumera un MDT y un registro. Se activa NativeCaumaxPlugin, se descarga, se vuelve a preparar la copia antigua y se reactiva. En el arranque, la reactivación y la comprobación diferida quedan exclusivamente los dos archivos P0, con las mismas huellas SHA-256. No se invoca restore_exports. Los archivos del respaldo permanecen intactos durante cada activación.

En otro QGIS de escritorio, perfil relief115, se prepara un ráster sintético de 500 m con su capa original, dos copias gestionadas en el antiguo grupo de relieve y una capa ajena dentro de ese grupo. Al activar el plugin desaparecen las dos copias y el grupo; permanecen la capa original y la ajena. Dos actualizaciones de TerrainLayers con 500 m no añaden ninguna capa. La huella del TIFF permanece igual. La lista de capas se inspecciona en la ventana real de QGIS.

Manual de diez páginas regenerado. Se inspeccionan visualmente las páginas 2 y 7, que explican la instalación inicial y la ausencia de recuperación automática. La publicación verifica por separado los bytes del ZIP, el instalador Windows y el catálogo QGIS.
