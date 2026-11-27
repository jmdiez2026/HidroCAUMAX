# HidroCAUMAX 1.0.23 — cálculo automático P₀ y Pd(T), color de la red principal

## Conducta esperada

1. Una vez delimitada una cuenca nueva, la ventana «Cuenca hidrográfica» inicia **en segundo plano y sin intervención** el cálculo de P₀ medio y de las medias espaciales Pd(T) para T=2,5,10,25,100,500 años.
2. Una sola transacción de resultados publica P₀ y los seis mapas de Pd(T) completos; una cancelación o error nunca mezcla medias de cuencas o resoluciones distintas. Los botones continúan permitiendo ejecutar únicamente P₀ o Pd(T) cuando el usuario decide recalcular.
3. Los controles de Guardar/Informe no permiten exportar un cálculo incompleto mientras la tarea esté activa.
4. Si el usuario cambia la resolución MDT, el cierre o la divisoria suavizada, el recálculo de superficie ya existente recalcula también las medias sin lanzar una segunda tarea duplicada.
5. Se usa el mismo código `basin_raster_mean` ya validado, sin interpolaciones verticales ni corrección β. Los estadísticos corresponden a **medias espaciales de seis cuantiles de precipitación máxima diaria**, no a una nueva serie temporal de máximas anuales.

## Simbología CAUMAX original

La etiqueta «Red > 50 km²» no identifica una capa independiente. Es la categoría `sup=2` del renderizador categorizado de la capa «Red 10 km²» (archivo original `red10km.qml`).

- Se aplica rosa/violeta `#FF1493` y anchura **1,30 mm** únicamente a esta categoría `sup=2`.
- El símbolo y grosor de la categoría `sup=1` («Red 10–50 km²») permanecen idénticos, así como la geometría, los atributos y los GeoPackages originales.
- Se comprueba la simbología efectiva al cargarse la capa o cambiar el renderizador; los indicadores antiguos de estilo no impiden la corrección.
- Para evitar punteros obsoletos SIP/C++ de QGIS, se clona el símbolo mientras su categoría Python está viva y se actualiza **la categoría in situ**, sin sustituir el renderizador.

## Nombre de los grupos de Curso principal

El título de grupo se reduce a `HidroCAUMAX · Curso principal {resolución X}x{resolución Y}`, sin X/Y del cierre, índice de celda ni sufijo `m` (ejemplos: `500x500`, `25x25` y `5x5`). Al actualizar el resultado se conserva el identificador de ambas capas. Al abrir proyectos QGIS previos se renombran únicamente los grupos con marca de propiedad `HidroCAUMAX/course_owner` y atributos de resolución de las capas propias; los grupos de usuario quedan intactos.

Las pruebas en `tests/test_course_layers.py` verifican nombres exactos y migración segura.

## Pruebas

- `tests/test_basin_hydrology_controller.py`: siete fuentes en una tarea, publicación atómica, ninguna publicación en error.
- `tests/test_basin_hydrology_dialog.py`: cálculo sin clic tras análisis, bloqueo Guardar/Informe, cancelación, no duplicación durante recálculo de superficie.
- `tests/test_native_layers_view.py`: renderizador real `QgsCategorizedSymbolRenderer` con dos categorías `sup=1/2`, preservación exacta de la primera, asignación de color/ancho de la segunda y reaplicación tras cargar estilo azul.
- CI ampliada de QGIS/Qt/GDAL, sintaxis Python y empaquetado. Validación final de escritorio en Windows pendiente tras instalar la versión oficial.
