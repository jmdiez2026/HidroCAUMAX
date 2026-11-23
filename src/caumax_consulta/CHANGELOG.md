# HidroCAUMAX 1.2.6

## Snap hidrológico y cierre de la cuenca

Se corrige la selección del cierre en la malla original de 500 m y en los MDT mejorados. El punto original de consulta permanece como referencia; el cierre efectivo, el extremo aguas abajo del curso principal y la cuenca se calculan conjuntamente desde la celda seleccionada.

En 500 m se utiliza el primer tramo entrante del curso D8 de máxima longitud, con el mismo recorrido y desempate que el trazado del curso principal. La dirección de salida de una celda de confluencia ya no orienta la sección del clic. Cuando corresponde mover el centro aguas arriba, se selecciona el primer centro del mismo curso y se vuelve a delimitar la cuenca.

En 25 y 5 m, el algoritmo puede asociar un clic lateral al cauce al que realmente drena. Examina la traza D8 y sus confluencias, compara los aportes relativos y proyecta el punto original sobre el curso asociado. La búsqueda no se detiene en la primera pequeña unión ni utiliza automáticamente el receptor aguas abajo como cierre. Se han retirado los umbrales rígidos de 9 celdas y 4 veces la mediana; el área original de 500 m sigue siendo informativa y no obliga a alcanzar su 90 %. Las laderas sin concentración de flujo y las asociaciones ambiguas requieren revisión.

## Elección manual cuando hace falta

Un ajuste automático ambiguo abre un selector de cierre. **Ajustar cierre…**, junto a Malla de celdas, permite revisar también una cuenca calculada. El usuario puede elegir una celda en el mapa o mover el candidato Norte, Sur, Este u Oeste, en pasos de una o varias celdas. Se muestran coordenadas y distancia antes de **Aplicar cierre**.

La elección se comprueba contra el MDT y sus direcciones D8: cobertura, cota, código de dirección y radio. Aceptar recalcula la divisoria, el curso y los parámetros; cancelar conserva el resultado anterior. El ajuste manual puede ir aguas abajo si el usuario así lo decide y queda identificado expresamente en el proyecto y en el informe. No vuelve a ejecutar GRASS cuando ya existen direcciones válidas.

El selector restaura la herramienta anterior del mapa al terminar, respeta un cambio de herramienta realizado por el usuario y retira sus marcas provisionales. No aplica elecciones pendientes durante el guardado ni incluye su marca provisional en los informes.

## Coherencia del mapa y de los proyectos

- Solo quedan visibles los grupos de curso y divisorias de la consulta activa. Los grupos anteriores se conservan ocultos y pueden reactivarse; mantienen sus geometrías y casillas internas. El relieve y las capas oficiales conservan su visibilidad.
- Al reemplazar el cierre dentro de la misma resolución, el estado anterior de suavizado no sobrescribe el nuevo cálculo.
- Pantalla y PDF describen el cierre como centro de celda aproximado. Se retira la afirmación general de «sección conservada» que podía sugerir una precisión topográfica inexistente.
- Las sesiones validan por separado el snap sobre el curso principal, la asociación lateral y la elección manual. El cierre, los recorridos, la proyección y las áreas deben ser coherentes. Los proyectos anteriores siguen abriéndose con sus resultados originales; una nueva consulta aplica el algoritmo actual.

## Actualizaciones desde QGIS

El complemento consulta el índice HTTPS de HidroCAUMAX al cargarse o activarse, al abrir o reutilizar una cuenca y al invocar la apertura de un proyecto. Las llamadas coincidentes comparten la petición en curso; cada nueva invocación posterior vuelve a comprobar las versiones.

La consulta no bloquea QGIS ni transmite datos de la cuenca. Si hay una versión estable compatible, aparece un aviso con **Actualizar**. El botón añade el repositorio del complemento si falta y abre el administrador de QGIS; el usuario realiza allí la instalación. Los repositorios existentes, incluidos los desactivados, conservan sus ajustes. El menú incorpora **Buscar actualizaciones…** con respuesta explícita sobre disponibilidad o problemas de conexión.

## Verificación y límites

**634 pruebas: 618 correctas, 16 omitidas, sin fallos ni errores**, con cierre limpio de QGIS y sin cambios del código durante la ejecución. Las omisiones corresponden a 7 pruebas con CAUMAX externo, 6 con GRASS, 2 con TauDEM/MPI reales y 1 con datos climáticos externos.

La batería reproducible cubre confluencias, conexión lateral, ladera sin cauce, cierre manual en todas las orientaciones, guardado sin fuentes, corrupción de registros, capas activas y comprobación de actualizaciones. Incluye Qt, QGIS y GDAL reales con datos de prueba; los servicios de red y los procesos externos se simulan cuando corresponde.

Esta revisión se verifica en Linux con QGIS 3.40.6 y GDAL 3.10.3. Los componentes TauDEM/MPI de Windows se conservan y se comprueba su integridad; no se atribuye a esta revisión una ejecución nueva en Windows.

Las capas originales de las capturas no están disponibles en el repositorio. Las regresiones reproducen los problemas geométricos y de conexión con datos sintéticos, pero no acreditan una reproducción exacta de esa cuenca. El snap opera sobre centros de celdas y una red D8; su resultado debe contrastarse con la cartografía y el MDT del proyecto, especialmente en confluencias o ante discrepancias con el cauce real.
