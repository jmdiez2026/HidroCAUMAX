# Presentación D8 y divisorias sin duplicados

26 de noviembre de 2026. Nombre visible HidroCAUMAX v.1.0; revisión técnica 1.0.14.

## Cambios comprobados

- Una generación D8 abre el grupo y pliega las cuatro leyendas. Orden: red, direcciones ESRI, MDT y relieve sombreado. Se activan red y relieve; MDT y direcciones quedan desactivados. Mostrar red mantiene su control independiente.
- Abrir el diálogo de resolución mejorada ya no carga la carpeta de exportación. El antiguo grupo marcado HidroCAUMAX mejoradas se retira al activar el plugin, abrir un proyecto o abrir ese diálogo. Se conservan los archivos y las capas ajenas añadidas al grupo.
- La identidad de la divisoria depende del punto, cierre, CRS y resolución, sin incluir la ruta temporal del MDT. Se reutilizan las capas al repetir la consulta. Las copias antiguas se retiran solo si están identificadas como propias, tienen la misma geometría normalizada y atributos, y sus puntos de consulta son compatibles. Se conservan los GeoPackages, las capas ajenas y las que están en edición. Consultas distintas permanecen separadas aunque su polígono coincida.
- No se cambia el motor TauDEM, la codificación ESRI D8 ni la configuración manual nativa de CAUMAX.

## Pruebas reproducibles

Entorno: Python de QGIS 3.44.15, Qt 5, Windows. Ejecutar con `src` y `tests` en PYTHONPATH, un perfil QGIS temporal y `QT_QPA_PLATFORM=offscreen`. Para el ensayo del diálogo original, `HIDROCAUMAX_CAUMAX_SOURCE` debe apuntar a la carpeta del complemento CAUMAX instalado.

```text
python -m unittest discover -s tests -p test_drainage_layers.py -v
python -m unittest discover -s tests -p test_rational_layers_panel.py -v
python -m unittest discover -s tests -p test_rational_layers_bridge.py -v
python -m unittest discover -s tests -p test_boundary_layers.py -v
python -m unittest discover -s tests -p test_improved_storage.py -v
python -m unittest discover -s tests -p test_native_toolbar.py -v
python -m unittest discover -s tests -p test_native_calculation_layers.py -v
python -m unittest discover -s tests -p test_future_qgis_updates.py -v
python -m unittest discover -s tests -p test_release_packaging.py -v
python -m unittest discover -s tests -p test_manual_dialog.py -v
```

Resultado: 157 pruebas correctas y una omitida por falta de permiso para crear enlaces simbólicos. El primer pase contenía 157 pruebas y dos omisiones. Tras añadir la protección de consultas distintas y proporcionar las fuentes originales de CAUMAX, las 37 pruebas de divisorias y enlace del diálogo se repitieron y pasaron sin omisiones. No se contabilizan dos veces las pruebas repetidas.

Comprobaciones principales: nueva generación después de cambiar manualmente la visibilidad; reapertura sin importar la carpeta; reutilización de identificadores con otra ruta temporal; copias antiguas; capas ajenas y en edición; consultas distintas; conservación byte a byte de archivos existentes; actualización del plugin, empaquetado y manual.

La batería histórica `test_drainage_dialog.py` conserva seis fallos en este entorno: tres subcasos de geometría de controles, uno de publicación de exportaciones con WinError 5 y dos expectativas antiguas que solo contemplan dos de las cuatro capas. Se reprodujeron los mismos seis fallos al sustituir `drainage_layers.py` por el archivo publicado en 1.0.13. No se presenta esta batería histórica como superada ni se modifican sus expectativas para ocultar los resultados.

## Comprobación de escritorio

Perfil aislado `layers114`, con datos locales sintéticos de ensayo: MDT y D8 de 128 × 128 celdas a 25 m, red vectorial, tres copias idénticas de una divisoria, antiguo grupo de importación y una capa ajena dentro de él. Al iniciar el complemento queda una sola divisoria, desaparece el grupo de importación y se conservan la capa ajena y las huellas SHA-256 de los dos TIFF.

Se pulsa el botón real Generar capas D8 de un panel de ensayo que publica productos ya preparados mediante DrainageLayers. Se comprueban visualmente y mediante el árbol QGIS los cuatro nombres, orden, visibilidad y leyendas plegadas. Una nueva instancia de BoundaryLayers con otra ruta temporal reutiliza los mismos identificadores. Esta prueba verifica la presentación y la reutilización; no ejecuta TauDEM ni acredita una nueva descarga de MDT de 5 m.

En el perfil personal, con autorización expresa, se actualizó la instalación de 1.0.12 a 1.0.13 y se pulsó Valores por defecto en el diálogo original. Se observaron las rutas nativas mdt.tif, dir.tif, p0.tif e i1id.tif y el área mínima original de 10 km². Se pulsó Aceptar y se verificaron esos valores guardados. La validación de escritorio de 1.0.14 descrita arriba se realiza en el perfil separado.

Manual de diez páginas actualizado; páginas modificadas revisadas visualmente. El ZIP, el instalador, la etiqueta GitHub y el catálogo QGIS se verifican separadamente durante la publicación.
