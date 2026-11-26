# Verificación de HidroCAUMAX 1.0.8

Fecha: 26 de noviembre de 2026. Entorno: QGIS 3.44.15, Python 3.12 y Qt 5 en Windows. Alcance: separación de los botones Calcular, presentación de la curva racional, extrapolación opcional T→Q, altura nativa y leyendas plegadas.

## Pruebas reproducibles

Se ejecutaron 168 pruebas: 167 correctas y una omitida por no permitir el entorno crear enlaces simbólicos. Ningún fallo en la ejecución final.

Módulos: `test_rational_frequency.py`, `test_rational_inputs.py`, `test_rational_layers_bridge.py`, `test_rational_export.py`, `test_native_toolbar.py`, `test_drainage_layers.py`, `test_terrain_layers.py`, `test_boundary_layers.py`, `test_release_packaging.py` y `test_future_qgis_updates.py`.

Para repetirlas, usar una consola configurada con Python de QGIS, añadir `src` a `PYTHONPATH` y definir `HIDROCAUMAX_CAUMAX_SOURCE` con la carpeta del CAUMAX original. Ejecutar, por ejemplo:

```text
python -m unittest discover -s tests -p test_rational_frequency.py -v
python -m unittest discover -s tests -p test_rational_layers_bridge.py -v
```

Repetir sustituyendo el patrón por cada módulo de la lista. Las pruebas usan Qt y capas GDAL/QGIS reales. Cubren selección vinculada, decimales, alineación del área dibujada, extrapolación permitida y rechazada, resultados no válidos, restauración de datos ausentes, conservación de los cuantiles originales, cálculo cancelado, estado de botones, descarga de la integración, anchura conservada y leyendas plegadas.

La prueba de la interfaz original carga `rationalmethoddialog.ui` y comprueba la altura una vez procesadas las actualizaciones de los grupos internos de Qt. Esto reproduce el hueco vertical que quedaba al consultar demasiado pronto el tamaño mínimo almacenado por Qt.

## Comprobación de escritorio

Se utilizó un perfil QGIS de prueba independiente, con el código 1.0.8 y el CAUMAX original instalado. No se modificaron archivos de CAUMAX ni el proyecto personal abierto.

- Caso de Método racional: ABANADES, EPSG:25830, X=346031,3, Y=4717696,9, T=100 años, corrector P0=0,70.
- Calcular original: muestra Q=9,63 m³/s y T=100; mantiene su formato sin icono GEA y no abre la tabla ni el gráfico.
- Nuevo Calcular GEA: abre los valores T/Q de 2/2,84; 5/4,75; 10/5,92; 25/7,50; 100/9,63 y 500/12,11. Las cifras están centradas y los caudales tienen dos decimales fijos.
- La tabla y el área dibujada del gráfico empiezan en la misma coordenada vertical y miden 201 píxeles de altura. Se observaron las tres casillas agrupadas a la izquierda y la retirada de los controles y textos solicitados.
- T=1000 con Permitir extrapolación desmarcado: consulta rechazada por estar fuera del intervalo disponible, sin nueva fila. Al marcarla: Q=13,18 m³/s, identificado como extrapolado, con fila y prolongación discontinua. Al desmarcar: se retira el punto extrapolado. El resultado nativo permanece en Q=9,63 y T=100.
- Guardar cuenca… aparece una sola vez, en la fila principal y con icono GEA. La fila inferior empieza con Calcular GEA, seguida de Cuenca hidrográfica y Generar capas resolución mejorada.
- El contenido de Método racional pasó de 608×783 a 608×727 píxeles; la anchura se conservó y desapareció el hueco bajo las variables. Las dimensiones incluyen solo el contenido, sin el marco del sistema.
- Se cargaron los productos reales de 5 m exportados durante la verificación anterior. El grupo Drenaje TauDEM permanece abierto; sus cuatro leyendas aparecen plegadas. Red, MDT y relieve están visibles; direcciones D8 permanece desactivada.

## Límites y publicación

No se ha repetido toda la batería histórica ni el procesamiento hidrológico de 5 m en esta revisión: se reutilizaron sus productos para comprobar la presentación. Las fórmulas nativas, la selección del cierre, TauDEM y la codificación ESRI D8 no cambian. La corrección y verificación anteriores de 5 m están documentadas en `src/caumax_consulta/docs/verification-1.0.6.md`.

La publicación se valida por separado mediante construcción reproducible del ZIP, prueba del instalador en Windows en GitHub Actions, comparación SHA-256 de las descargas e índice QGIS actualizado. No se ejecuta el instalador sobre el perfil personal abierto durante estas pruebas.
