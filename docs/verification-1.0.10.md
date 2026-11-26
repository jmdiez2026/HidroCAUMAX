# Verificación de HidroCAUMAX 1.0.10

Fecha: 26 de noviembre de 2026. QGIS 3.44.15 LTR, Qt 5, Python 3.12, Windows. Nombre visible: HidroCAUMAX v.1.0. Las pruebas de escritorio se realizan en perfiles independientes, sin sustituir el plugin del perfil personal ni modificar su proyecto.

## Regresiones reproducibles

La batería seleccionada ejecutó **267 pruebas: 266 correctas y una omitida** por la disponibilidad de creación de enlaces simbólicos. Se ejecutó además, correctamente, `test_drainage_dialog.DrainageDialogTests.test_legacy_taudem_snapshot_restores_and_exports_only_caumax_directions`, que cubre la exportación ESRI D8 desde un proyecto antiguo. Tras las últimas modificaciones de la rampa y del icono, las **45 pruebas de los módulos afectados pasan**, incluida la comprobación de 16 colores distintos en la rampa continua.

Módulos de la batería: `test_improved_storage`, `test_native_calculation_layers`, `test_rational_layers_panel`, `test_rational_layers_bridge`, `test_rational_frequency`, `test_native_toolbar`, `test_native_layers_view`, `test_map_services`, `test_native_decimal`, `test_generated_cleanup`, `test_hydro_export`, `test_rational_export`, `test_rational_inputs`, `test_future_qgis_updates`, `test_release_packaging`, `test_automatic_update`, `test_manual_dialog`, `test_update_checker` y `test_update_ui`.

Desde Python de QGIS, con `src` y `tests` en `sys.path` y `HIDROCAUMAX_CAUMAX_SOURCE` apuntando a CAUMAX original:

```text
python -m unittest discover -s tests -p test_improved_storage.py -v
python -m unittest discover -s tests -p test_native_calculation_layers.py -v
python -m unittest discover -s tests -p test_map_services.py -v
python -m unittest discover -s tests -p test_automatic_update.py -v
python -m unittest discover -s tests -p test_manual_dialog.py -v
```

Se prueban parejas completas/incompletas y modificadas, compatibilidad de malla y CRS, conservación de exportaciones y archivos auxiliares, restauración, limpieza sin resurrección de archivos, cancelación nativa de configuración, selección del P0 recomendado, dos botones Calcular independientes, tabla/gráfico, barra, grupos y simbología. La red >50 km² solo cambia de color. Las capas de referencia mantienen su visibilidad al repetir; los subgrupos pedidos pliegan sus leyendas al abrir.

La verificación de actualizaciones rechaza hashes, versiones, compatibilidad, rutas y miembros ZIP incorrectos; ensaya cancelación, bloqueo durante trabajo pendiente, fallo de sustitución y restauración de archivos. La descarga de la prueba de escritorio se sustituye por paquetes de prueba locales; la recarga y recuperación se ejecutan con las funciones reales de QGIS. La comprobación de la descarga pública se realiza por separado al publicar.

## Comprobaciones en QGIS

- Configuración real de CAUMAX: propone la pareja de 5 m exportada del caso anterior, el P0 incluido, torrencialidad original y cuenca mínima 2 km². Cancelar conserva los valores previos; Aceptar guarda los nuevos.
- Caso ABANADES, EPSG:25830, X=346031,3, Y=4717696,9, T=100: el Calcular original conserva el corrector introducido 0,35 y no abre la curva. El Calcular GEA lo sustituye por el promedio regional 0,70; Q=9,629077983899101 m³/s, presentado como 9,63. Cuantiles: 2→2,84; 5→4,75; 10→5,92; 25→7,50; 100→9,63; 500→12,11. Valores recomendados muestra coma decimal.
- La fila GEA contiene el logo único, los cuatro botones azules y Limpiar capas rojo al extremo derecho. El diálogo independiente D8 conserva su introducción exacta, separación y controles.
- Cinco conjuntos de datos de prueba en ResolucionMejorada se cargan sin duplicación. Solo Red_drenaje_25m.shp queda marcada. **Ese SHP de 25 m es una geometría de dos vértices para comprobar la interfaz; no representa un nuevo cálculo hidrológico.** Los MDT y D8 de 5 m proceden de la ejecución real previa.
- Referencias preparadas con proveedores reales: 31 capas dentro del grupo general y Google Satellite fuera, bajo Google Terrain. No se modifican el CRS ni el encuadre al cargar referencias. Los cuatro subgrupos restantes son extensión SNCZI, peligrosidad SNCZI, agroclima e IGME.
- Se verifican las listas desplegadas de agroclima y peligrosidad SNCZI con sus capas plegadas, y las 16 clases de P0. Terrain queda activo y Mapas raster del IGN apagado al cargar CAUMAX.
- Ayuda abre las diez páginas del manual incluido. Las vistas se generan del PDF y están vinculadas a su SHA-256; Abrir PDF apunta al archivo local. Se revisan visualmente las páginas renderizadas del PDF.
- Actualización real en perfil independiente: paquete de prueba 1.0.10→1.0.11 activado, barra restituida y archivo de exportación intacto. Se introduce un fallo deliberado en `classFactory` del paquete de prueba 1.0.12; se restaura 1.0.11 y el archivo permanece intacto. La segunda ejecución confirma la recuperación automática sin esperar un diálogo modal. Esas versiones de prueba no se publican.

## Límites y conservación

El GeoTIFF original P0 mantiene SHA-256 `611b41dbedfd64a0d0c84c710e85c80251b4bc8db55959ee9830cd2fa36bb871`. La simbología no modifica sus píxeles. La primera actualización desde una versión antigua que no preservaba exportaciones requiere conservar aparte los archivos guardados dentro del plugin si se utiliza el instalador ZIP nativo que sustituye toda la carpeta.

No se repite una descarga íntegra nueva ni el procesamiento completo de 5 m en esta revisión; se mantiene la corrección y ejecución real documentada en `src/caumax_consulta/docs/verification-1.0.6.md`. La compatibilidad de exportación ESRI D8 sí se vuelve a comprobar.

No se declara verde toda la batería histórica. El módulo completo antiguo `test_drainage_dialog` contiene cinco expectativas de presentación/visibilidad ya fallidas en la revisión base. Se conserva la comparación y se ejecuta individualmente el caso de exportación afectado, que pasa. Los estados de servicios externos y de IA dependen de sus proveedores; la prueba de un cálculo o de una imagen no garantiza disponibilidad permanente.

La publicación se verifica independientemente: mismo ZIP reproducible, instalador Windows probado por GitHub Actions, etiqueta asociada al commit y hashes de las descargas públicas. Ver [requirements-1.0.10.md](requirements-1.0.10.md) para la revisión de todas las peticiones vigentes.
