# HidroCAUMAX v1.2.2

Complemento auxiliar para **CAUMAX 3.0 y QGIS 3 con Qt 5**. La versión del paquete y del diálogo es **1.2.2**. Conserva procesamiento con TauDEM, exportación de MDT, direcciones y red, curva hipsométrica, los seis cuantiles originales y **P₀ y precipitación máxima diaria de la cuenca**. Incluye los ajustes completos del diálogo, estadísticos y recálculo automático por suavizado o resolución. La entrega se ha comprobado en Windows con QGIS 3.44.15. Las pruebas reproducibles se encuentran en la carpeta `tests` del repositorio; los registros locales de sesiones no se publican.

## Instalación y uso

1. Instala **HidroCAUMAX_v1_2_2.zip**, sin descomprimir, desde **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP**. Acepta sustituir la instalación auxiliar anterior. El nombre visible es **HidroCAUMAX v1.2.2**; la carpeta interna sigue siendo **caumax_consulta** para conservar la continuidad de la actualización.
2. Activa CAUMAX, carga sus capas y abre **Mapa de caudales máximos → Consulta de capas de caudales máximos**. Selecciona periodo y punto; pulsa **Consultar**.
3. Una consulta válida habilita **Cuenca hidrográfica**, a la izquierda del informe nativo. Su texto y su altura son el **110 %** de los botones de la derecha, con redondeo al píxel más cercano para la altura. La anchura reserva espacio para el texto completo y el logotipo **GEA-UVa**.
4. Pulsa el botón para calcular la cuenca. El diálogo se alinea con el borde derecho de QGIS, usa toda su altura y aproximadamente el **36 %** de su anchura. Sigue los cambios de posición y tamaño de QGIS. **Morfología de la Cuenca** presenta la tabla blanca de 19 parámetros: **Parámetro, Valor, Unidad e Interpretación**, con anchuras relativas **46/13/9/32 %**. Valor y Unidad se centran; la interpretación ocupa como máximo tres líneas y conserva el texto completo en su ayuda. Las observaciones del cálculo quedan inmediatamente debajo de esta tabla.
5. En **Resolución MDT**, situado arriba del diálogo, marca una opción disponible. Se descarga y añade el MDT a Capas y se recalculan automáticamente cuenca, morfología, hipsometría, suavizado, P₀ y los seis mapas Pd, incluidas sus consultas y gráficos. Las capas de relieve anteriores conservan su visibilidad; el nuevo MDT queda desmarcado para que puedas mostrarlo cuando quieras. No hace falta pulsar otro botón. **Malla de celdas** controla la trama, activada inicialmente para 500 × 500 y 25 × 25 m.
6. En **Suavizado divisoria**, elige **Original**, **Suavizada** o ambas y ajusta un grado entero. Cada cambio vuelve a calcular todos los resultados sobre la superficie activa, con cancelación de trabajos anteriores y publicación del conjunto completo. **Exportar shape**, compacto y alineado a la izquierda, guarda las divisorias ya mostradas.
7. La franja **Informe** reúne **Informe (PDF)**, **Guardar proyecto** y **Abrir proyecto**. El cuerpo de la ventana tiene desplazamiento y esa franja permanece visible.

En la sección de drenaje puedes pulsar **Generar capas D8** para procesar el MDT activo con TauDEM y preparar sus tres productos exportables. Esta función requiere los ejecutables y dependencias de TauDEM; **Carpeta de TauDEM…** permite seleccionarlos.

El menú **Complementos → HidroCAUMAX** permite activar las herramientas y **Abrir proyecto de cuenca…** sin realizar otra consulta nativa. Los botones nuevos tienen fondo azul claro. El botón general **Identificar objetos espaciales** conserva su funcionamiento habitual.

## Punto de cierre y elección de MDT

Se conserva el **punto exacto de la última consulta válida de CAUMAX**, junto con su caudal Q y periodo T. En la opción original de 500 m, el cierre D8 sigue siendo el centro de la celda que contiene ese punto.

Al usar un **MDT mejorado de 25 o 5 m**, se transforma siempre el **punto ORIGINAL de consulta** al CRS del MDT y se ajusta automáticamente al centro de la celda de cauce principal más cercana. Se identifican como candidatos las celdas con área acumulada D8 de al menos el **90 % del área de la cuenca original de 500 m**, dentro de un radio igual al mayor entre **500 m y la diagonal de la celda original**. Se elige la menor distancia al punto original; un empate se resuelve por fila y columna. Estos umbrales son criterios propios de HidroCAUMAX, no parámetros prescritos por HEC-HMS. Si no hay un candidato válido, se informa del problema y se conserva el resultado anterior.

El punto original y el cierre efectivo ajustado se identifican por separado en pantalla, informe y proyecto guardado, con la distancia de ajuste. Al cambiar otra vez de resolución, la búsqueda parte del punto ORIGINAL; no encadena desplazamientos desde el cierre de otra malla. Conviene revisar visualmente la sección elegida y la cuenca resultante. El ajuste no obliga a que el área del MDT detallado sea idéntica a la original.

| Opción | Datos y cálculo |
|---|---|
| **500 m · CAUMAX original** | MDT y direcciones de drenaje originales configurados en CAUMAX. |
| **25 m** | Descarga oficial IGN/CNIG mediante API-Coverages IDEE, previa comprobación de disponibilidad. |
| **5 m** | El mismo proveedor oficial, con descarga y cálculo de mayor detalle. |
| **2 m / 0,5 m** | Controles deshabilitados en esta entrega: no se ha comprobado una colección de descarga automática utilizable mediante la API integrada. La limitación se refiere a ese acceso automático, no a la existencia de esos productos en la zona. |

Las opciones descargadas necesitan conexión al servicio oficial. Se comprueban cobertura, resolución, CRS y cotas de la extensión solicitada. Para el MDT nuevo se obtienen direcciones **D8 con GRASS GIS**; las cotas originales se conservan para las estadísticas del terreno. El caudal **Q sigue siendo el consultado en el ráster original de CAUMAX**: cambiar el MDT recalcula cuenca y parámetros, no produce un nuevo caudal de avenida.

Antes del cálculo se estima la memoria necesaria: se aplica un límite del **55 % de la RAM disponible** y un máximo de **60 millones de celdas**. Si la cuenca alcanza el borde del recorte, se amplía la extensión hasta **dos veces**, con factor **1,5**. Si persiste el contacto con el borde, se advierte de una posible delimitación truncada.

La resolución activa condiciona área, perímetro, relieve, pendientes y recorridos; figura en la tabla y en el PDF. La malla de 500 × 500 m representa 25 ha por celda. El recorrido controla NoData, límites, ciclos y cancelación, y conserva partes y huecos.

## Morfología de la Cuenca: 19 parámetros

El diálogo y el PDF comparten **19 filas** y una tabla de **fondo blanco** con las columnas **Parámetro, Valor, Unidad e Interpretación**. Cada interpretación ofrece una lectura breve del valor; las definiciones completas figuran en los textos de ayuda y en el informe.

Los parámetros de la tabla corresponden a la **superficie activa de la resolución seleccionada**: la divisoria original o la suavizada, según los controles del diálogo. Al cambiar el grado de suavizado se recalculan los resultados sobre esa superficie. En las fórmulas, A está en m², P y las cotas H en m; Lb es la máxima distancia recta del centro de cierre a un vértice exterior de la divisoria.

| Parámetro | Unidad | Definición |
|---|---|---|
| Área de la cuenca | km² | Celdas vertientes por superficie de celda, contrastada con la geometría. |
| Perímetro | km | Longitud de todos los contornos originales, incluidos los huecos. |
| Número de celdas | — | Celdas conectadas aguas arriba con el cierre. |
| Resolución celda | m × m | Anchura y altura originales de la celda de drenaje. |
| Relación de elongación | — | Re = 2·√(A/π)/Lb. |
| Compacidad de Gravelius · Kc | — | Kc = P/(2·√(π·A)); normalización exacta del círculo. |
| Índice de circularidad · Ic | — | Ic = 4·π·A/P² = 1/Kc². |
| Índice de rugosidad de Melton | — | (Hmáx − Hmín)/√A. |
| Pendiente media de la cuenca (D8) | % | Media del máximo descenso local de cota dividido por distancia horizontal a uno de los ocho vecinos. |
| Índice hipsométrico | — | HI = (Hmedia aritmética − Hmín)/(Hmáx − Hmín). |
| Índice de Irregularidad del Terreno TRI (Riley) | m | Media de √Σ(Hcentro − Hvecino)² sobre los ocho vecinos. |
| Desnivel máximo (Hmáx − Hmín) | m | Diferencia entre cotas extremas válidas de la cuenca. |
| Altitud máxima (Hmáx) | m | Máxima cota válida del MDT en la cuenca. |
| Altitud mínima (Hmín) | m | Mínima cota válida del MDT en la cuenca; puede diferir de la cota del cierre. |
| Altitud mediana (Hmed · P50) | m | Percentil 50 de las cotas válidas de las celdas de la cuenca. |
| Longitud del curso principal total (L) | km | Ruta D8 horizontal de mayor longitud entre cierre y cabecera, incluidos los pasos diagonales. |
| Longitud del curso principal entre el 10 % y el 85 % (L10–85) | km | Tramo central del mismo curso: desde 0,10·L hasta 0,85·L, medido desde el cierre; longitud 0,75·L. |
| Tiempo de concentración (L) | h | Tc(L) = 0,3·L^0,76·J^−0,19 sobre el curso de máxima longitud. |
| Tiempo de concentración (L10–85) | h | Misma fórmula usando L10–85 y la pendiente propia J10–85 de ese tramo. |

**Hmed es la mediana P50, no la media aritmética.** La media aritmética se conserva para calcular el índice hipsométrico. Las estadísticas altimétricas excluyen NoData y valores no finitos; HI queda sin definir cuando el relieve es cero.

### Pendiente D8 y TRI

La pendiente de la tabla sigue la **definición local de TauDEM**: máximo descenso de cota/distancia horizontal entre ocho vecinos, con la distancia diagonal correspondiente. Este estadístico se calcula sobre las **cotas originales del MDT**, sin rellenarlas ni resolver sus planicies. Se excluyen vecindarios incompletos, NoData y mínimos estrictos de depresiones sin descenso; las celdas planas con descenso máximo cero pueden aportar cero a la media. Es independiente del procesamiento de direcciones y red con TauDEM descrito más abajo.

TRI y pendiente utilizan vecindarios completos y válidos de 3 × 3, incluyendo los vecinos reales inmediatamente exteriores a la cuenca. El informe recoge cuántas celdas se han excluido. La interpretación del TRI puede expresar además el contraste RMS local, equivalente a TRI/√8; el valor de la fila sigue siendo el TRI de Riley.

### Dos tiempos sobre el mismo curso principal

Se aplica la fórmula del apartado **2.2.2.5 de 5.2-IC**, con longitud en km y pendiente J en m/m:

- **Tc(L):** J = (Hcabecera − Hcierre)/(1000·L).
- **Tc(L10–85):** J10–85 = (H85 − H10)/(1000·L10–85), con L10–85 = 0,75·L. Las cotas H10 y H85 se obtienen sobre el perfil de ese mismo curso.

El segundo tiempo es una comparación para su tramo central; no sustituye al del curso completo. Ambos cálculos utilizan las cotas de su propio tramo, sin atravesar NoData ni aplicar el suavizado de la divisoria. Estos valores corresponden al curso D8 elegido por máxima longitud; el máximo Tc entre otras ramas queda como diagnóstico y no ocupa la fila Tc(L). La determinación normativa global exige estudiar los recorridos pertinentes.

Cuando faltan cotas continuas, longitud o pendiente positiva suficientes, aparece **No disponible**. Los candidatos Tc ≤ 0,25 h también se presentan como no disponibles, con una observación sobre el cálculo por tramos de flujo. No se sustituyen valores indefinidos por cero.

## Curva hipsométrica y cuantiles de caudal

Bajo los parámetros, la curva hipsométrica muestra **área situada por encima de cada altitud (km²)** frente a **altitud (m)**. La casilla adimensional cambia los ejes a **a/A** y **(h − Hmín)/(Hmáx − Hmín)**. Se conservan 201 muestras de la distribución de cotas válidas; la escala horizontal dimensional utiliza el área total de la cuenca. El **HI mostrado es el exacto**, calculado con la media de todas las cotas válidas; la integral aproximada de la curva muestreada puede diferir. Con relieve nulo, la normalización altimétrica queda indefinida.

El panel de cuantiles consulta **T = 2, 5, 10, 25, 100 y 500 años** en la **celda original de CAUMAX**, usando el punto de consulta anterior al ajuste al cauce. Ofrece **Q(T)** y **T(Q)** por interpolación lineal de Q respecto a log(T). La extrapolación está **activada por defecto** en las consultas nuevas y se respeta la opción de los proyectos guardados. La tabla muestra **Periodo de retorno T (años)** y **Caudal máximo instantáneo (Qci) [m3/s]**, centrados. Los años se presentan como enteros y los demás valores del panel con un máximo de dos decimales, conservando la precisión de los datos originales. Los gráficos **Qci vs. T** y **T vs. Qci** usan marcas enteras, diez marcas verticales de caudal y doce de periodo cuando el intervalo permite esos enteros, con márgenes medidos y disposición vertical si falta anchura. Conserva los cuantiles originales y los NoData sin forzar su monotonía; si la inversa ofrece varias soluciones o un intervalo, los muestra. Datos, entradas y casillas se guardan por resolución y se recuperan sin los rásteres fuente.

## P₀ y precipitación máxima diaria de la cuenca

Los botones permiten calcular estos datos para la **superficie activa de la cuenca**. Además, cambiar la resolución o el grado de suavizado desencadena el recálculo automático. Se usa toda la superficie seleccionada, incluida la divisoria suavizada cuando está activa; no solo la celda de cierre. Consulta las capas P₀ y de precipitación para **T = 2, 5, 10, 25, 100 y 500 años** configuradas en CAUMAX y conserva sus rutas y resultados. Cambiar de MDT no modifica los valores de esas cartografías: cambia el polígono sobre el que se obtiene la media.

La media en mm pondera cada valor por el **área de intersección entre su píxel y la cuenca**: `Σ(valor × área válida intersectada) / Σ(área válida intersectada)`. Las áreas se miden en el CRS métrico de la cuenca; se conservan partes y huecos, y las transformaciones de CRS se indican en las notas. Las capas P₀ y de precipitación conservan su **resolución nativa**; el MDT activo determina la divisoria sobre la que se promedian. Mejorar el MDT no aumenta la resolución de esas capas.

El panel de **P₀** muestra su media con **dos decimales, en rojo y negrita**, además de mediana, máximo, mínimo y desviación típica. La mediana y la desviación típica **poblacional** se ponderan por el área válida intersectada; los extremos corresponden a los píxeles válidos dentro de la cuenca.

Los dos botones de cálculo tienen la misma anchura compacta. **Calcular Precipitación diaria máxima anual MEDIA (mm)** obtiene los seis mapas y presenta una tabla centrada con **Periodo T (años), Pd (media), Pd mínimo, Pd máximo, Desv. Típica y Estado**. Los valores tienen como máximo dos decimales. El Estado y su ayuda conservan la información de cobertura e incidencias:

- **Cobertura:** área cubierta por el ráster dividida por el área total de la cuenca.
- **Área válida:** área con dato válido dividida por el área total de la cuenca.
- **Píxeles intersectados:** todos los que tienen intersección de área positiva, incluidos los NoData.
- **Estado:** disponible, parcial, no disponible o sin calcular. Una media parcial utiliza únicamente el área válida; los huecos no se rellenan con cero. **Cero es un valor válido**, distinto de NoData.

**P₀ se presenta bruto, sin aplicar β ni otros coeficientes correctores.** Tampoco se aplica reducción areal a la precipitación. Cada **Pd,T** es la **media espacial de los cuantiles puntuales del mapa T**; no es el cuantil de una serie observada de tormentas medias sobre toda la cuenca.

La consulta **Pd(T)** muestra media, mediana, máximo, mínimo y desviación típica a la derecha. Para obtenerlos interpola **los valores por celda en ln(T)** entre mapas de malla compatible y calcula después los estadísticos sobre la superficie válida común; no interpola directamente las medianas ni las desviaciones. **T(Pd)** invierte la curva de medias de los mapas disponibles. Los años se muestran sin decimales, las demás cifras con un máximo de dos, y la extrapolación está **activada por defecto** para nuevas consultas. La inversa conserva soluciones múltiples e intervalos de meseta. Los gráficos **Pd vs. T** y **T vs. Pd** comparten las reglas de tamaño, márgenes y marcas enteras de los gráficos de Qci. Pantalla y PDF utilizan los mismos datos; no estiman nuevas frecuencias de tormentas areales ni recalculan Q de CAUMAX.

Se guardan perfiles comprimidos de hasta **200000 píxeles válidos por mapa** para recuperar los estadísticos de Pd(T) sin las fuentes. Por encima de ese límite se conservan los estadísticos de cada mapa, sin un perfil por celda. Una consulta antigua sin estadísticos o perfiles debe recalcularse con los botones correspondientes; no se inventan los datos que faltan. Los grupos y capas de resultados de HidroCAUMAX se insertan **antes y encima del grupo CAUMAX_CAPAS**, conservando su orden interno y su visibilidad.

Los resultados quedan vinculados a la geometría original y su CRS mediante una huella **SHA-256**, además del área total. Se guardan por resolución con las entradas y casillas. Al abrir una consulta o generar su PDF **no se vuelven a leer los rásteres fuente ni se recalculan las medias**. Los resultados de otra divisoria no se muestran como si correspondieran a la activa. Las consultas anteriores sin este estado siguen abriéndose; no reciben medias inventadas. Si no se ha solicitado ningún cálculo, el informe lo indica brevemente; con solo P₀ calculado no añade gráficos vacíos de precipitación.

## TauDEM real y tres productos exportables

**Generar capas D8** ejecuta **PitRemove → D8FlowDir → AreaD8 → Threshold → StreamNet** con TauDEM real y actualiza la cuenca y su red con esas direcciones. El programa registra herramientas, versión, procedencia y umbral. Si faltan ejecutables o falla una etapa, informa del problema; no presenta otro motor como si fuera TauDEM. La consulta Q/T original permanece intacta. Las estadísticas de altitud, pendiente local y TRI siguen usando el **MDT original**; el MDT rellenado se usa para encaminar el flujo.

El **umbral de inicio de corriente**, expresado en km², controla la densidad de red: reducirlo incorpora más cauces. El modo automático propone el mayor entre **100 celdas nativas y el 1 % del área de la cuenca TauDEM**, convertido a un número entero de celdas. Es un criterio inicial del complemento, editable; al cambiarlo se regenera la red. **Mostrar red** controla su visibilidad.

| Botón | Producto |
|---|---|
| **Exportar MDT (TIFF)…** | MDT original, con sus cotas, resolución, extensión, CRS y NoData. |
| **Exportar dirección (TIFF)…** | Direcciones D8 en la misma malla, con la codificación seleccionada. |
| **Exportar red (SHP)…** | Red de cauces generada por TauDEM, con geometrías y atributos conservados. |

Para utilizar las direcciones en el **Método Racional de CAUMAX**, selecciona **CAUMAX / ESRI**. La opción **TauDEM** conserva sus códigos nativos. Los números representan direcciones diferentes:

| Dirección | TauDEM | CAUMAX / ESRI |
|---|---:|---:|
| E | 1 | 1 |
| NE | 2 | 128 |
| N | 3 | 64 |
| NO | 4 | 32 |
| O | 5 | 16 |
| SO | 6 | 8 |
| S | 7 | 4 |
| SE | 8 | 2 |

No basta con renombrar el TIFF: el exportador realiza la recodificación y conserva la malla. En **Windows**, instala **TauDEM y Microsoft MPI Runtime (MS-MPI)** siguiendo la [distribución oficial y sus dependencias](https://hydrology.usu.edu/taudem/taudem5/downloads.html). Son programas externos: **el ZIP del complemento no incluye ni instala TauDEM o MS-MPI**. El instalador completo oficial de TauDEM reúne sus ejecutables y dependencias; no se necesita ArcGIS para usarlos por línea de comandos. Después selecciona la carpeta de ejecutables mediante **Carpeta de TauDEM…** y comprueba la disponibilidad indicada en el diálogo.

## Cursos principal y central en QGIS

Al abrir la cuenca o cambiar el MDT se crean o actualizan dos capas reales del proyecto:

- **Curso principal total (L)**, visible inicialmente.
- **Tramo del curso principal 10–85 % (0,75 L)**, oculto inicialmente.

El curso principal se dibuja en **azul claro (#65C7FF)**, con trazo de **1,1 mm** y borde oscuro de **1,5 mm** para que destaque sobre el relieve. El tramo central usa **azul más pálido (#A6E7FF)** y trazo discontinuo; su casilla sigue desactivada inicialmente. El PDF comparte esta simbología.

Puedes activar sus casillas en el panel de capas. Las líneas siguen los centros de las celdas D8: L llega hasta el nodo de cabecera del recorrido, sin prolongarlo hasta el borde literal de la divisoria. El tramo central se interpola sobre ese mismo recorrido entre 0,10·L y 0,85·L, contados desde el cierre.

Las capas permanecen en el proyecto al cerrar el diálogo. Sus estados de visibilidad se conservan al guardar la consulta. Los contornos temporales de cuenca y suavizado se retiran al cerrar; las capas exportadas también permanecen.

## Fondo MDT y legibilidad de la cuenca

El MDT de la resolución activa se representa con una **rampa natural verde, oliva, ocre y piedra**, combinada con **relieve multidireccional** al **45 % de opacidad** en modo Multiplicar. QGIS dibuja ambos directamente desde el MDT real a su resolución nativa, según la escala solicitada. Esta representación no modifica cotas, direcciones D8, punto de cierre ni parámetros.

Al desplazar o ampliar el mapa, la rampa de colores se ajusta automáticamente a las cotas válidas del MDT que quedan visibles. La leyenda se actualiza con ese intervalo. El cálculo cartográfico se realiza en segundo plano tras una pausa breve de navegación y descarta solicitudes anteriores; respeta el CRS del lienzo, los NoData y la visibilidad de las capas. Se conservan los colores naturales, el sombreado multidireccional al 45 % y el factor vertical 1, sin exagerar alturas. Al ampliar se suavizan los píxeles mediante interpolación bilineal exclusivamente de pantalla; la malla original, los valores del archivo y todos los cálculos hidrológicos permanecen intactos.

El relleno de la cuenca original es **rojo al 20 % de opacidad**, equivalente al **80 % de transparencia** (alfa 51/255), para ver el terreno a través de él. La casilla **Malla de celdas** muestra u oculta la trama independientemente del contorno original y conserva su elección por resolución, también en el proyecto y el PDF. Para 500 y 25 m aparece activada por defecto; en resoluciones más finas, desactivada.

Cada MDT descargado conserva su grupo de **MDT y relieve** como capas normales del proyecto. Cambiar la resolución de cálculo añade el nuevo grupo desmarcado y conserva las capas y casillas que ya estaban visibles; volver a una resolución reutiliza su grupo y su visibilidad. El mapa no cambia de extensión. El PDF representa la resolución de cálculo activa con sus cursos y su divisoria.

## Suavizado y exportación

El grado **1–45**, con incrementos de **1**, controla un **filtro gaussiano periódico** sobre los anillos remuestreados. Se implementa con NumPy del entorno QGIS. Su escala es:

**σ = min(resolución X, resolución Y) × grado / 15**

El grado **15** equivale a σ de una celda y **45** a tres celdas de la dimensión menor. En una malla de 500 m son 500 y 1500 m. Es una intensidad gradual, no un número de iteraciones.

El **cierre permanece fijo**; cuando es necesario se ajusta localmente el contorno para conservar esa sección. Se exige geometría válida, conservación de partes y huecos e inclusión del cierre. Si un grado no cumple, se informa para reducirlo antes de exportar.

El mapa muestra la original en rojo transparente y la suavizada en azul. El grado es un **entero de 1 a 45**. Si está seleccionada **Suavizada**, esa superficie pasa a ser la superficie de análisis: se recalculan área, perímetro e índices, altitudes, hipsometría, pendiente y TRI, recorridos y tiempos D8, P₀, Pd y sus estadísticos. Las fracciones de celda se ponderan por su área exacta de intersección. La red archivada se recorta de nuevo desde su copia original. El MDT y las direcciones nativas se mantienen como fuentes; el suavizado no rellena el terreno ni desvía los cauces. Desmarcar **Suavizada** recalcula sobre la divisoria original. Los trabajos anteriores se cancelan y se descartan las respuestas tardías; guardar, exportar y generar PDF requiere que termine el conjunto actual.

**Exportar shape** permite elegir Shapefile (.shp) o GeoPackage (.gpkg). Guarda **las geometrías calculadas y mostradas**, sin volver a suavizar. Al seleccionar ambas opciones se generan archivos **_original** y **_suavizada**; con una sola se respeta el nombre elegido. El PDF recoge siempre las casillas, el grado y las medidas actuales.

| Campos de salida | Contenido |
|---|---|
| Q_M3S, T_ANOS | Caudal consultado y periodo de retorno. |
| X_CIERRE, Y_CIERRE | Centro de cierre efectivo, en el CRS de delimitación. |
| FILA_Q, COL_Q | Fila y columna del ráster de caudales, contadas desde 1. |
| N_CELDAS, RES_X_M, RES_Y_M | Celdas vertientes y resolución original en metros. |
| AREA_KM2, PERIM_KM, KC_GRAV, IC_CIRC | Medidas de la geometría guardada, original o suavizada. |
| A_ORIG_KM, P_ORIG_KM, KC_ORIG, IC_ORIG | Medidas de la cuenca de celdas sin suavizar, en la resolución activa. |
| ELONG_R, MELTON, SLOPE_D8, HYP_IDX, TRI_M, RELIEF_M | Parámetros de forma y terreno de la resolución activa. |
| ZMAX_M, ZMIN_M, ZMED_M | Altitudes máxima, mínima y **mediana P50**, en m. |
| LMAIN_KM, L1085_KM | Longitud total del curso principal y de su tramo 10–85 %, en km. |
| TC_H, TC1085_H | Tc del curso completo y Tc de su tramo central, cada uno con su pendiente; h. |
| SMOOTH_N, GEOM_TIPO | Grado **decimal** de Gauss aplicado y tipo de geometría; SMOOTH_N = 0 para la original. |

La capa conserva su CRS real. Se rechazan destinos existentes y sus archivos auxiliares. Conserva juntos los componentes del Shapefile: .shp, .shx, .dbf, .prj y los auxiliares generados.

## Informes y captura GIS

**Informe (PDF)** genera el documento y solicita su apertura en el visor del sistema tras comprobar la exportación. El informe nativo de CAUMAX conserva también la apertura automática. El PDF corresponde a la **resolución activa**, identifica su proveedor y recoge los 19 parámetros, las interpretaciones, fórmulas, observaciones y el estado actual del suavizado. Incluye la curva hipsométrica y los cuantiles de caudal, y añade **P₀ y precipitación máxima diaria de la cuenca** cuando existen resultados o un estado de cálculo de ese panel: medias, coberturas, fuentes, entradas, estimaciones y gráficos aplicables. Distingue datos parciales, no disponibles y no calculados.

Incluye una **imagen GIS de la cuenca completa**, con margen y las capas de contexto visibles. El encuadre se calcula para abarcar la cuenca, independientemente del zoom de trabajo. Generar la captura no modifica el zoom, la selección de capas ni los datos fuente. La imagen, su leyenda y su extensión se guardan con la consulta.

Si el MDT está disponible, la captura se vuelve a generar con el estilo actual. Si una consulta se abre sin sus rásteres, se conserva la **captura PNG guardada** como respaldo: los resultados y geometrías siguen disponibles, aunque esa imagen pueda mantener el estilo de una versión anterior. El relieve interactivo necesita el MDT de origen.

## Guardar y abrir todas las resoluciones

**Guardar proyecto** prepara los resultados de **todas las opciones disponibles**, no solo de la marcada. La preparación puede necesitar descargas y cálculos adicionales; espera a que termine el progreso. Cada opción guardada contiene:

- Análisis completo, 19 valores y observaciones, malla y curso principal.
- Consulta Q/T y punto exacto, cierre efectivo y CRS en WKT.
- Divisorias generadas, casillas, grado decimal y estadísticas.
- Imagen GIS en PNG, leyenda y extensión.
- Proveedor, resolución y visibilidad de las capas L y L10–85.
- Red TauDEM con sus campos, atributos y geometrías XY o XYZ, umbrales, modo automático y codificación D8 elegida.
- Curva hipsométrica, casilla adimensional y seis cuantiles CAUMAX con sus entradas y opción de extrapolación.
- P₀ y medias espaciales de precipitación, áreas cubiertas y válidas, estados, notas y fuentes; huella de la cuenca, entradas Pd(T)/T(Pd) y extrapolación.

El archivo **.caumax.json** es un proyecto portable de consulta. El formato multirresolución, versión 2, admite hasta seis variantes y un máximo de 128 MiB; **no incorpora los grandes rásteres MDT**. Se siguen leyendo los proyectos de formato anterior.

**Abrir proyecto**, o **Complementos → HidroCAUMAX → Abrir proyecto de cuenca…**, recupera todas las variantes y la selección activa **sin necesitar los MDT originales ni recalcular el terreno**. Puedes generar el PDF de la activa y exportar sus geometrías guardadas. Cambiar después de resolución o de grado solicita el recálculo completo y requiere las fuentes correspondientes; si faltan se indica la incidencia y no se publican datos antiguos como nuevos. Las interpretaciones se reconstruyen con el catálogo común a partir de los valores conservados. Se conservan por separado la divisoria original, la superficie activa y la red de origen para evitar suavizados y recortes acumulativos.

Los proyectos nuevos conservan también el punto original, el cierre ajustado y el registro del ajuste en cada resolución. Los proyectos anteriores sin ese registro recuperan sus resultados exactos guardados y se identifican como consultas sin ajuste; no se desplaza su cierre ni se recalculan al abrirlos. Para aplicar la política nueva a una consulta antigua, realiza otra consulta de **Cuenca hidrográfica** con los datos necesarios disponibles.

La red guardada puede mostrarse y exportarse a SHP **sin el archivo de red original**. Los TIFF de MDT y direcciones se conservan como **referencias externas**, no se incrustan en el JSON: para volver a exportarlos deben estar disponibles sus archivos fuente o las copias exportadas. El proyecto conserva los resultados y el PNG aunque esos rásteres falten.

## Exportador del Método Racional incluido

En **CAUMAX → Método Racional → Método racional**, el botón **Guardar cuenca…** permite guardar exactamente los polígonos rojos calculados por CAUMAX como Shapefile o GeoPackage. La integración se activa junto con HidroCAUMAX.

El ZIP conserva además el script original íntegro del usuario en **extras/CAUMAX_Exportador_Divisoria_Original.py** (9478 bytes), como copia de referencia. La integración utiliza su adaptación incluida; no es necesario ejecutar manualmente ese archivo.

## Referencias y validación

- [IGN/CNIG · API-Coverages IDEE: catálogo oficial](https://api-coverages.idee.es/collections?f=json).
- [GRASS GIS 8.5: r.watershed](https://grass.osgeo.org/grass85/manuals/r.watershed.html), cálculo de direcciones de drenaje.
- [HEC-HMS: identificación de cauces mediante un umbral de área acumulada](https://www.hec.usace.army.mil/confluence/hmsdocs/hmsguides/gis-tools-and-terrain-data/creating-a-georeferenced-model-using-hec-hms-gis-tools/preprocessing-terrain-data-to-delineate-subbasin-and-reach-elements) y [ajuste de puntos al cauce identificado más cercano](https://www.hec.usace.army.mil/confluence/hmsdocs/hmsguides/hms-modeling-applications/hec-hms-example-applications/generating-a-flow-forecast-for-international-locations). Son referencias del procedimiento general; el 90 % y el radio de búsqueda indicados arriba son decisiones del complemento.
- [TauDEM: D8 Flow Directions](https://hydrology.usu.edu/taudem/taudem5/help53/D8FlowDirections.html), definición local de pendiente, bordes, NoData y depresiones.
- [TauDEM: código y herramientas oficiales](https://github.com/dtarb/TauDEM) y [descargas y dependencias](https://hydrology.usu.edu/taudem/taudem5/downloads.html), para el procesamiento real y sus productos.
- [Norma 5.2-IC, texto consolidado del Ministerio](https://cdn.mitma.gob.es/portal-web-drupal/carreteras/52ic_fom2982016_err_fom1852017_res180326_consolidado.pdf), apartado 2.2.2.5; [Orden FOM/298/2016 en BOE](https://www.boe.es/buscar/doc.php?id=BOE-A-2016-2405).
- [GDAL: TRI Riley y Wilson](https://gdal.org/en/stable/programs/gdal_raster_tri.html) y [Riley, DeGloria y Elliot (1999)](https://arc.lib.montana.edu/ojs/index.php/IJS/article/view/2914).

El repositorio incluye pruebas reproducibles de cálculo, interfaz, recuperación de resultados y exportación. Los registros históricos locales se mantienen fuera de esta publicación porque contienen rutas y referencias a sesiones de trabajo. HidroCAUMAX v1.2.2 se distribuye en el canal **estable**; la comprobación de un proyecto concreto debe realizarse en su instalación de QGIS y con sus fuentes de datos.

