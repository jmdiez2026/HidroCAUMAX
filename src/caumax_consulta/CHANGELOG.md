# HidroCAUMAX 1.2.7

## Elegir el ramal por el punto señalado

La 1.2.6 escogía el curso D8 de mayor longitud antes de decidir el cierre. En una confluencia esto podía desplazar el punto en diagonal hacia un río lateral, aunque otro ramal pasara mucho más cerca del clic. La corrección compara primero la distancia del clic a los segmentos D8 conectados y elige el ramal próximo. Solo después calcula el curso de máxima longitud de la cuenca resultante.

En la malla de 500 m se revisan los tramos entrantes reales y se conserva el centro original cuando es admisible; en otro caso se toma el primer centro sobre la sección proyectada o aguas arriba del ramal elegido. En los MDT mejorados se mantiene la asociación al receptor conectado y se comparan sus ramales, sin sustituir el cercano por otro más largo. No se inventa una conexión geométrica que contradiga el D8.

Cuando dos ramales con cierres distintos difieren en proximidad como máximo el 5 % del menor lado de la celda, se solicita selección manual. Esa tolerancia es una política del complemento, no una precisión topográfica garantizada. La selección inicial de 500 m también puede abrir el selector conservando un cálculo provisional en la celda original.

## Ajustar repetidamente con la ventana abierta

**Ajustar cierre…** mantiene abierto el selector al aplicar el cambio. Las flechas Norte, Sur, Este y Oeste desplazan el candidato una o varias celdas; **Elegir en el mapa** permite señalarlo con el ratón. Los controles se bloquean mientras se calcula y vuelven a habilitarse con el nuevo cierre, para repetir el ajuste sin cerrar la cuenca. Cerrar el selector conserva el último resultado aplicado.

Se separa el alcance manual del radio automático. Los aproximadamente 707 m de la malla de 500 m limitan la búsqueda automática, pero no una elección expresa del usuario: esta puede utilizar cualquier celda válida del MDT cargado. Se mantienen las comprobaciones de extensión, máscara, NoData, cota y dirección. La divisoria, el curso y los parámetros se recalculan juntos. El punto de consulta y su caudal CAUMAX permanecen registrados.

## Limpiar la representación

La fila superior queda **Malla de celdas → Ajustar cierre… → Borrar divisoria**. El nuevo botón retira los trazados y marcadores y elimina del proyecto las capas generadas para ese resultado, incluidas las variantes anteriores de divisoria y MDT. Conserva las capas ajenas y los archivos de origen. Los procesos de fondo no vuelven a dibujar el resultado borrado; aplicar un nuevo cierre o seleccionar otra resolución permite representarlo de nuevo.

## Presentación numérica

La tabla morfológica y el informe muestran un máximo de dos decimales, manteniendo entero el número de celdas. Los cálculos, las exportaciones de datos y los proyectos conservan la precisión interna. Para no representar como cero una magnitud positiva inferior a 0,01, se muestra **<0,01**.

## Generación de las capas de mayor resolución

La sección se titula **Generación capas resolución mejorada**. Se elimina el botón Motor TauDEM y la elección de carpeta externa; se utiliza directamente el motor predeterminado. Tanto la capa de direcciones de QGIS como el TIFF exportado se fijan en **CAUMAX / ESRI · Método Racional**, también al abrir una sesión que tenía seleccionada la codificación nativa TauDEM.

Los títulos **Modelo de Elevaciones**, **Modelo de Dirección de Flujo D8** y **Red de drenaje** se sitúan centrados en tres columnas, con sus botones compactos debajo. Los botones se llaman **Exportar MDT (TIFF)…**, **Exportar dirección de drenaje (TIFF)…** y **Exportar Red de Drenaje (SHP)…**. El estado y el texto de disponibilidad/documentación se integran en un mismo párrafo, con ajuste natural al ancho.

## Proyectos, informes y verificación

Los nuevos registros distinguen selección por ramal próximo, asociación lateral al ramal próximo y selección manual dentro de la extensión del ráster. Las sesiones 1.2.5 y 1.2.6 se abren con sus criterios originales y se validan sin reinterpretarlos. Los informes describen la política realmente aplicada. Los datos de ubicación y las pruebas de conectividad no se envían a Análisis IA.

La verificación cubre la confluencia con un ramal vertical próximo y otro diagonal más largo, ambigüedades, selección manual más allá de 707 m, ajustes repetidos con flechas y ratón, borrado selectivo de capas y recuperación posterior. Incluye la exportación real del TIFF CAUMAX/ESRI, la reapertura de sesiones anteriores y el recorte del MDT mejorado cuando el cierre manual está lejos del clic original.

**665 pruebas: 649 correctas, 16 omitidas, sin fallos ni errores**, con el código sin cambios durante la ejecución y cierre limpio de QGIS. Las omisiones corresponden a 7 pruebas con CAUMAX externo, 6 con GRASS, 2 con TauDEM/MPI reales y 1 con datos climáticos externos. Se utilizan QGIS 3.40.6, Qt y GDAL 3.10.3 reales en Linux con rásteres reproducibles; los servicios de red y procesos externos se simulan cuando corresponde. Se ha verificado la integridad de los 240 archivos del runtime TauDEM/MPI de Windows, que no se modifica; esta revisión no atribuye una nueva ejecución en Windows.

Las capas originales de las capturas no están disponibles en el repositorio; los ensayos reproducen la geometría del problema, sin atribuirles una reproducción exacta del proyecto del usuario. El cierre sigue representándose en centros de celda y no como una sección topográfica de precisión arbitraria.

---

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
