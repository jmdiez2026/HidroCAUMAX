# Verificación de HidroCAUMAX 1.0.9

Fecha: 26 de noviembre de 2026. Entorno: QGIS 3.44.15, Python 3.12 y Qt 5 en Windows. Alcance: activación automática, iconos C1/E3, retirada selectiva de resultados, encuadre al cargar CAUMAX y recuadro GEA compartido.

## Pruebas reproducibles

167 pruebas ejecutadas: 166 correctas y una omitida porque el entorno no permite crear enlaces simbólicos. Sin fallos en la ejecución final.

Módulos: `test_native_toolbar.py`, `test_native_layers_view.py`, `test_delineation_cleanup.py`, `test_native_query_cleanup.py`, `test_rational_layers_panel.py`, `test_rational_layers_bridge.py`, `test_rational_export.py`, `test_rational_inputs.py`, `test_rational_frequency.py`, `test_release_packaging.py` y `test_future_qgis_updates.py`.

En una consola configurada con Python de QGIS, añadir `src` a `PYTHONPATH` y definir `HIDROCAUMAX_CAUMAX_SOURCE` con la carpeta del CAUMAX original. Ejecutar para cada módulo:

```text
python -m unittest discover -s tests -p test_native_toolbar.py -v
python -m unittest discover -s tests -p test_native_layers_view.py -v
python -m unittest discover -s tests -p test_rational_layers_bridge.py -v
```

Las pruebas comprueban activación inicial y tras la señal de arranque, restauración sin desactivación al pulsar el logo, descarga y recarga sin duplicados, los cinco botones y sus iconos, estados de carga, retirada por identificación de propietario, resultados antiguos y proyectos reabiertos, conservación de referencias y capas ajenas, cancelación de callbacks pendientes, encuadre transformado al CRS actual y ausencia de cambios durante la navegación posterior. También comprueban el orden y la alineación del recuadro GEA, un único logo, botones sin iconos repetidos, conexión de Guardar cuenca y los dos órdenes de carga y descarga de sus componentes.

## Comprobación en QGIS de escritorio

Se usó un perfil independiente, con el CAUMAX original instalado y sus fuentes reales. El proyecto personal abierto no se modificó ni se reemplazó su plugin instalado.

- Barra visible y herramientas activas desde la carga: Cargar capas, Herramientas HidroCAUMAX, Abrir cuenca, Eliminar capas y Actualizar. Iconos propios C1 y E3. Tres pulsaciones del logo conservan la integración activa.
- Cargar capas CAUMAX: 21 capas nativas válidas. Encuadre de la península y Baleares en EPSG:25830, ajustado al tamaño del lienzo. La escala varía con las dimensiones de QGIS; no se ha deducido una escala numérica exacta de la captura del usuario.
- Se crearon cinco capas de prueba identificadas como resultados, una de ellas respaldada por un GeoJSON en disco, y dos capas ajenas, una marcada como referencia. El botón de la escoba retiró las cinco primeras y conservó las otras dos. SHA-256 del GeoJSON idéntico antes y después: `ed778c73ea51338d6576fb5992b189f2b94d9f3d5e199f46c1af520d6b0b3e6c`.
- Método racional: ABANADES, EPSG:25830, X=346031,3, Y=4717696,9, T=100 y corrector P0=0,70. Los cuatro botones inferiores aparecen automáticamente, en el orden solicitado, con un único logo GEA visible a la izquierda.
- Diálogo de 608×732 píxeles de contenido. Los cuatro botones miden 21 píxeles de altura y comparten la coordenada vertical. No se recortan los textos ni se ensancha el diálogo.
- Pulsación de Calcular original: Q=9,63 m³/s, T=100, sin abrir la curva. Pulsación de Calcular del recuadro GEA: abre la tabla y el gráfico con los seis cuantiles originales. La selección, los dos decimales y la extrapolación opcional conservan el comportamiento de 1.0.8.

## Identificación y límites

El borrado de la barra solo retira capas del proyecto. El borrado de archivos del diálogo D8 conserva su acción independiente. Los resultados se reconocen por propiedades explícitas guardadas en el proyecto, nunca por su nombre o apariencia. Las exportaciones incorporan identificación desde 1.0.9 y se retiran con el botón de la barra, pero se conservan al iniciar otra consulta. Las exportaciones antiguas sin identificación se conservan para evitar confundirlas con archivos del usuario.

No se ha repetido el procesamiento hidrológico completo de 5 m: esta revisión no cambia las fórmulas, el cierre, TauDEM ni la codificación ESRI D8. Su verificación anterior se documenta en `src/caumax_consulta/docs/verification-1.0.6.md`.

La publicación se comprueba por separado mediante ZIP reproducible, prueba del instalador Windows en GitHub Actions, hashes de las descargas y actualización del índice QGIS. Instalar o descargar un paquete no implica que una sesión QGIS ya abierta haya recargado su código.
