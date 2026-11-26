# Verificación de HidroCAUMAX 1.0.6

Fecha: 26 de noviembre de 2026. Entorno observado: Windows, QGIS 3.44.15 LTR, Qt 5 y Python 3.12. Base de comparación: `08d9b85ba62a0a6b343cdcbb05371f28ed6df9f3` (1.0.5). Las pruebas interactivas se realizan en una sesión y un perfil de QGIS independientes del proyecto del usuario.

## Generación D8 a 5 m

El fallo se reprodujo con el punto X=346031,25; Y=4717696,95, EPSG:25830. El mensaje indicaba que las direcciones TauDEM no conservaban la cuenca aceptada en una sección compatible y rechazaba publicar otra cuenca automáticamente.

El MDT existente cubría la ventana: 1400×2900 celdas, 5×5 m, 4.060.000 celdas válidas. Sus metadatos identificaban la colección IGN/CNIG `EL.ElevationGridCoverage_25830_5_PB`, 21 teselas y fracción válida 1. La memoria estimada era 926.155.456 bytes, por debajo de la disponible. **No se repitió una descarga íntegra nueva de 5 m**; estas comprobaciones corresponden al MDT ya descargado. Las regresiones de proveedor comprueban cobertura, teselas, errores y resoluciones nativas.

La causa reproducida era la pérdida de `selection_policy` al construir la referencia de refinamiento. TauDEM aplicaba el criterio antiguo de sección en lugar de la celda validada de la cadena D8. Ahora ambos traspasos conservan el criterio, también en la identidad de la caché. Se mantienen los controles de solape, correspondencia de cauce y cierre manual; no se relajan los umbrales para obtener un resultado.

La repetición real completa produjo MDT, direcciones ESRI D8 y una red con **55 tramos**. El cierre aceptado y el de TauDEM coinciden en X=346027,5; Y=4717697,5. Áreas: referencia de 500 m, 14,750 km²; delineado fino previo, 14,491700 km²; resultado TauDEM, 16,271875 km². Son resultados de motores distintos y no se presentan como áreas idénticas. Se verificaron los ocho códigos ESRI, alineación y conservación de las elevaciones originales.

En QGIS se observó la generación en curso, se pulsó Cancelar, volvió a habilitarse Generar y el siguiente intento completó los productos. La cancelación de procesos padre/hijo en Windows también tiene una prueba real. Cancelar solo se muestra durante la generación D8.

Para repetir el caso con los rásteres originales disponibles, desde la consola Python de OSGeo4W/QGIS:

```text
python scripts/reproduce_d8_case.py --help
```

El script documenta los argumentos de MDT/direcciones de referencia, MDT fino, direcciones finas aceptadas, coordenadas y carpeta de salida. Usa los motores reales y escribe su diagnóstico y los tres productos. Los rásteres locales del caso no se publican en el repositorio.

## Tabla y gráfico racional

Al pulsar Calcular en la ventana real de CAUMAX, se abrió automáticamente el nuevo diálogo. Para el punto anterior, área 14,750 km², P0=42,15, corrector 0,70 y T=100, el caudal aceptado fue 9,629077983899101 m³/s, presentado como **9,63** en el resultado principal.

| T (años) | Q (m³/s), máximo dos decimales |
|---:|---:|
| 2 | 2,84 |
| 5 | 4,75 |
| 10 | 5,92 |
| 25 | 7,5 |
| 100 | 9,63 |
| 500 | 12,11 |

La tabla final tiene solo T y Q. Tabla y gráfico comparten altura y posición superior. El gráfico mide 422×268 píxeles, frente a los 844×447 medidos en la primera vista. Las pruebas Qt hacen clic en filas y puntos, incluidos los extremos, añaden consultas automáticas en ambos sentidos sin botones de estimación, verifican el zoom y los estilos, y comprueban que no se modifican los datos ni el resultado nativo. La entrada y el resultado aparecen en una línea por recuadro y los selectores contiguos a sus etiquetas admiten dos decimales. También se cubren redondeo solo visual, periodos fraccionarios, resultados cancelados, datos ausentes, restricciones regionales, múltiples soluciones y tramos constantes. Las consultas se identifican como interpolaciones de Q respecto a log(T), sin extrapolación; no se confunden con un nuevo ajuste GEV nativo. Se ha renderizado la vista final desde el mismo diálogo Qt con los valores observados en QGIS.

## Referencias, instalación y limpieza

Las cuatro capas SNCZI se comprobaron contra los servicios oficiales: válidas, inicialmente apagadas y con imagen GetMap no vacía. El grupo Extensión zonas inundables se coloca inmediatamente debajo de Red estaciones AEMET. Una prueba con el proveedor WMS real de QGIS y un servidor local registra dos BBOX distintas al cambiar encuadre/zoom y confirma que una capa apagada no solicita GetMap. Se comprobó que el aviso de carga desaparece; se conservaba antes una referencia nula porque `pushMessage` no devolvía el elemento esperado.

El GeoTIFF CLC2000 de AdrLBallesteros se incluye sin modificar: EPSG:25830, 10350×8708 celdas, 100×100 m, NoData 255; SHA-256 `611b41dbedfd64a0d0c84c710e85c80251b4bc8db55959ee9830cd2fa36bb871`. Se instala dentro de la carpeta del complemento y se añade como última referencia, apagada. No sustituye el P0 empleado por el cálculo.

Eliminar capas presenta las rutas antes de borrar y retira de QGIS los resultados identificados. Protege fuentes y referencias; verifica que los archivos no han cambiado y comunica fallos por bloqueo. Se ensaya con archivos temporales. No se promete identificar exportaciones antiguas descargadas a carpetas arbitrarias que no tengan registro o metadatos de procedencia. Se mantiene la notificación de cualquier archivo que Windows no permita eliminar.

## Pruebas y límites

- Conjunto específico: **230 pruebas, OK, 2 omitidas** (casos específicos de otra plataforma o creación de enlaces). Se ejecutó con los permisos normales de Windows; la primera ejecución restringida impidió renombrar archivos temporales y no se contó como válida.
- Tras ajustar decimales y presentación, se repiten los módulos de frecuencia y conexión racional; sus 25 pruebas verifican el código final del diálogo.
- La ejecución amplia anterior de 936 pruebas no fue completamente verde: 28 fallos y 4 errores. Los grupos afectados se compararon con la revisión base, que reproduce fallos previos de geometría/tipografía, expectativas antiguas y bloqueos de archivos en Windows. No se afirma que toda la batería histórica pase.
- La instalación del complemento en el perfil habitual del usuario y la ejecución de un instalador descargado son comprobaciones diferentes de las pruebas locales y del empaquetado. No se sustituyó el complemento de una sesión de trabajo abierta.

Ejemplo de regresión de la curva desde un entorno Python de QGIS, con `src` en PYTHONPATH:

```text
python -m unittest discover -s tests -p test_rational_frequency.py -v
python -m unittest discover -s tests -p test_rational_inputs.py -v
python -m unittest discover -s tests -p test_reference_viewport.py -v
python -m unittest discover -s tests -p test_taudem_outlet_consistency.py -v
python -m unittest discover -s tests -p test_generated_cleanup.py -v
python -m unittest discover -s tests -p test_release_packaging.py -v
```

Para las pruebas que comparan las funciones originales, `HIDROCAUMAX_CAUMAX_SOURCE` debe señalar la carpeta de fuentes de CAUMAX 3.0; si no está disponible se informa de la omisión. La publicación automática comprueba el paquete, la integridad del runtime TauDEM y el instalador Windows antes de publicar sus archivos.
