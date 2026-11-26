# HidroCAUMAX v1.0.5

Complemento auxiliar para **CAUMAX 3.0 y QGIS 3 con Qt 5**. La versión técnica del paquete es **1.0.5** y la identificación pública y del diálogo es **v1.0.5**. Conserva procesamiento con TauDEM, exportación de MDT, direcciones y red, curva hipsométrica, los seis cuantiles originales y **P₀ y precipitación máxima diaria de la cuenca**. Conserva el punto original de consulta y permite ajustar el cierre manualmente. Incluye comprobación de actualizaciones, interfaz compacta y **Análisis IA gratuito sin credenciales**. Los ensayos reproducibles de esta revisión deben ejecutarse en un entorno QGIS 3 con Qt 5; la última validación integral registrada en el repositorio corresponde a versiones anteriores. Las pruebas reproducibles se encuentran en la carpeta `tests` del repositorio; los registros locales de sesiones no se publican.

## Selección de resolución en Método racional

La fila «Resolución MDT» permite comparar **500 × 500 m · original CAUMAX** (referencia, sin nueva descarga ni red TauDEM) con los MDT finos disponibles de 25 × 25 m y 5 × 5 m. Seleccionar una resolución disponible actualiza la divisoria visible de QGIS sin requerir «Generar capas D8». La referencia racional en vivo obtiene el área del análisis nativo de drenaje, incluso cuando no tiene un contexto de proyecto serializado, evitando el fallo `area_m2`. El cierre calculado por CAUMAX se conserva como referencia; el botón «Ajustar punto de cierre» ofrece un ajuste manual opcional para las resoluciones finas. Los controles Original, Suavizada y Grado utilizan el procedimiento de suavizado ya disponible. La selección de 500 m restaura la geometría de referencia del Método racional y no modifica el caudal nativo.

Los botones «Guardar cuenca», «Cuenca hidrográfica» y «Generar capas resolución mejorada» se encuentran en una misma fila, con logotipo GEA. El mensaje de estado de «Análisis IA» se muestra bajo la tabla, además de la ayuda emergente, para informar de peticiones en curso y errores del servicio.

## Separador decimal en CAUMAX original

Con HidroCAUMAX activo, los campos numéricos de la ventana nativa **Método racional** se presentan y editan con **coma decimal** (por ejemplo, `14,750`, `5,306` y `346039,6`). La adaptación es reversible y se aplica solo a esa ventana mientras nuestro complemento está activo. Los controles y archivos originales de CAUMAX permanecen intactos.

Las entradas que CAUMAX recibe y las señales de sus controles nativos conservan el separador interno **punto** que exige su cálculo Python. Se evita así romper funciones como `float(txtArea.text())`, recálculos automáticos y comprobación de datos. No se introducen separadores de millares.

## Dos decimales en el caudal punta de Método racional

El resultado **Caudal (m³/s)** se muestra siempre con dos decimales y coma (por ejemplo, `10,35`). Cuando se pulsa **Calcular**, HidroCAUMAX observa el valor devuelto por la fórmula nativa `getFlow`, o el resultado nativo de interpolación para un período T introducido manualmente. Comprueba que corresponde al resultado aceptado y evita que CAUMAX pierda las fracciones al escribir su entero redondeado en el campo. La fórmula, el punto de cálculo, la lluvia y los coeficientes de CAUMAX original **no cambian**.

Las cifras que no pueden recuperarse de un cálculo anterior o de una variante de CAUMAX sin el cálculo observable no se inventan: se presenta el entero original con dos ceros decimales. La captura del valor preciso se realiza de manera reversible sobre el diálogo activo y permite que la cuenca y los informes asociados utilicen el Q aceptado con dos decimales.

## Capas de referencia

En **Complementos → HidroCAUMAX → Cargar capas HidroCAUMAX**, el complemento añade **HidroCAUMAX · Capas de referencia** como **primer subgrupo dentro de CAUMAX_CAPAS**, por encima de **Red 10 km²**, **Ríos** y las demás capas de CAUMAX. Si CAUMAX_CAPAS aún no existe, se coloca en la primera posición del árbol. Al repetir el comando se recoloca también un grupo ya cargado, sin duplicarlo. Se respeta esta prioridad de dibujo aunque el proyecto utilice un orden de capas personalizado. Contiene siete subgrupos, en este orden:

| Subgrupo | Contenido |
|---|---|
| Red estaciones AEMET | SHP de 3.249 estaciones con precipitación documentada, diferenciadas por su presencia en los catálogos OpenData consultados. |
| Peligrosidad de inundación · SNCZI | Calados fluviales en tonos azules para T = 10, 100 y 500 años. |
| Google Satellite | Imagen de Google mediante conexión XYZ, desactivada inicialmente. |
| Clasificación Pfafstetter | Tramos de ríos clasificados según Pfafstetter modificado, MITECO. |
| Caracterización AgroClimática | Las 17 variables del servicio MAPA, incluida la clasificación de Papadakis. |
| IGME · Mapa Hidrogeológico | Mapa a escala 1:1.000.000 con hidrogeología, cuencas y unidades hidrogeológicas. |
| Corine Land Cover · Ocupación del suelo | Servicio multiescala oficial del IGN/SCNE: CLC2018, SIOSE2014 y SIOSE AR2017, según la escala de visualización. |

Las 25 capas WMS utilizan imagen PNG transparente: se solicitan al servidor al visualizar el mapa y requieren conexión. El comando conserva el CRS y la extensión actual del proyecto. Repetirlo incorpora únicamente las capas que falten. Las capas de referencia permanecen al borrar los delineados de HidroCAUMAX.

**Google Satellite** se coloca inmediatamente después de SNCZI y antes de Pfafstetter. Su grupo y su capa comienzan desactivados; al repetir el comando se conserva la selección de visibilidad que haya hecho el usuario. Utiliza teselas XYZ del servidor de Google, con atribución a Google y sus proveedores.

Los tres escenarios SNCZI se cargan por separado, con **T100 como escenario inicial**. Las variables agroclimáticas se agrupan para elegirlas; su grupo empieza desactivado y conserva Papadakis como selección inicial. El grupo IGME empieza desactivado y conserva sus tres componentes preparados para mostrarse juntos. Si un servicio no responde, se comunican las capas fallidas y las demás pueden seguir cargándose.

El endpoint de ocupación del suelo indicado no muestra CORINE a todas las escalas: utiliza CLC2018 a escala menos detallada que 1:50.000, SIOSE2014 entre 1:50.000 y 1:10.000 y SIOSE AR2017 al acercar más. Esa procedencia se registra en la descripción de la capa.

### Red estaciones AEMET

La capa contiene **3.249 estaciones con precipitación diaria, horaria o mensual documentada**, deduplicadas por indicativo. Se conserva el indicativo como texto, incluidos sus ceros iniciales. Se genera un **Shapefile real de puntos**, con sus archivos auxiliares, en una carpeta persistente del perfil QGIS; puede consultarse sin conexión una vez instalado el complemento. La ruta figura en las propiedades de la capa. Para trasladar un proyecto a otro equipo, copia también todos los archivos del SHP o exporta la capa a un GeoPackage del proyecto.

Los círculos distinguen **859 estaciones identificadas en OpenData** y **2.390 que no figuran en los inventarios consultados**. El cruce usa el indicativo y la unión de los selectores oficiales de climatología y observación convencional de AEMET, comprobados el **25/11/2026**. La ausencia en estos selectores no acredita que una estación carezca de cualquier dato abierto; tampoco se deduce disponibilidad a partir de su condición automática o manual. La clasificación y su alcance quedan registrados en los atributos y la descripción.

La geometría procede de una **copia pública publicada por Esri España en 2017 y atribuida a AEMET**, coherente con los tipos y campos del catálogo MITECO actualizado el **16/04/2016**. No se presenta como inventario completo de estaciones activas en 2026: hay 133 indicativos de los selectores OpenData consultados que no tienen geometría en esta base histórica. Se conservan la atribución a MITECO, las fuentes y las fechas.

Fuentes y procedencia verificadas:

- [IDEE: servicio de Ocupación del suelo](https://www.idee.es/csw-codsi-idee/srv/api/records/spaignwms_OcupacionSuelo).
- [MITECO: red hidrográfica y Pfafstetter](https://www.miteco.gob.es/es/cartografia-y-sig/ide/descargas/agua/red-hidrografica.html).
- [MAPA: servicios de Agricultura](https://www.mapa.gob.es/es/cartografia-y-sig/ide/directorio_datos_servicios/agricultura/wms-inspire-agricultura).
- [MITECO: catálogo WMS de Agua, mapas de peligrosidad](https://www.miteco.gob.es/es/cartografia-y-sig/ide/directorio_datos_servicios/agua/wms-inspire-agua.html).
- [IGME: Mapa Hidrogeológico de España a escala 1:1.000.000](https://info.igme.es/cartografiadigital/geologica/mapa.aspx?Id=19).
- [MITECO: estaciones climatológicas AEMET y condiciones de uso](https://www.miteco.gob.es/es/cartografia-y-sig/ide/descargas/otros.html).
- [Esri España: copia cartográfica AEMET publicada en 2017](https://www.arcgis.com/home/item.html?id=20e0ab17fcf249d1a03a4b5c7b083cb1).
- [AEMET OpenData: centro de descargas](https://opendata.aemet.es/centrodedescargas/inicio).

## Cuenca del Método racional

En **Método Racional → Método Racional**, selecciona el punto y calcula las **variables intermedias**. El botón **Cuenca hidrográfica**, situado a la izquierda de **Generar capas resolución mejorada**, abre el mismo diálogo utilizado desde la consulta de capas de caudales máximos.

La cuenca inicial es la delineada por el calculador racional. Se conservan el punto y su CRS, y se contrastan la máscara, la geometría, el área y las fuentes MDT/D8. El área nativa debe ser como máximo 50 km²; la red negra sirve como referencia cartográfica, sin mover el cierre automáticamente hacia otro tramo. Los controles nativos de CAUMAX permanecen: CAUMAX 3.0 rechaza exactamente 50 km² durante las variables intermedias.

Puedes utilizar las herramientas de cuenca antes de calcular Q. **Pendiente de cálculo** significa que aún no hay un caudal racional vigente. Después de pulsar **Calcular** en CAUMAX, vuelve a abrir la cuenca para incorporar el resultado aceptado. Una edición de las entradas invalida el Q anterior aunque CAUMAX siga mostrando su texto. Las capas de caudales máximos no se muestrean en este modo y no se fabrica una curva Q(T) a partir de ellas.

En **Resolución MDT** puedes seleccionar **25 × 25 m** y **5 × 5 m**, cuando exista cobertura oficial. Se utilizan las herramientas comunes de descarga, generación D8 y delineación. En modo racional, el cierre se ajusta automáticamente al centro admisible más próximo al punto original, una vez comprobados el solape bidireccional con la cuenca nativa y la pertenencia de los candidatos a una única cadena de drenaje. El eje del curso de 500 m no impone la orientación del drenaje fino. También están disponibles **Ajuste Punto de Cierre**, **Malla de celdas**, suavizado de la divisoria y exportación. La resolución se aplica al terreno y al nuevo delineado.

El botón **Análisis IA** de la cabecera **Resolución MDT** se oculta en este modo; se conserva el situado en el encabezado Interpretación de la tabla.

Morfología, hipsometría, resolución, ajuste del cierre, suavizado, P₀, Pd y sus gráficos, exportación, informes, proyectos e IA funcionan sobre la cuenca activa. El caudal conserva su relación con el cálculo racional nativo: cambiar la resolución o la divisoria en este diálogo no recalcula sus coeficientes ni el resultado Q. Los informes y proyectos conservan esta procedencia y distinguen los datos pendientes.

## Dependencias de instalación

**En Windows de 64 bits, CAUMAX es el único complemento de QGIS que debe estar instalado previamente.** HidroCAUMAX no utiliza Processing, sus proveedores, SAGA, Whitebox ni otros plugins para delinear o tratar los datos espaciales. QGIS aporta PyQt, GDAL/OGR y NumPy.

El ZIP incluye TauDEM, MPI y sus dependencias y datos. Los componentes se preparan automáticamente sin un instalador adicional, sin activar otro complemento y sin sustituir las bibliotecas de QGIS. El cálculo puede utilizar los ejecutables GRASS que ya incluya QGIS; si no están, el delineado fino utiliza el TauDEM integrado. Sus cachés y metadatos identifican el motor real, y ambos conservan las comprobaciones de cobertura, correspondencia de la cuenca y selección manual.

El runtime empaquetado es para Windows de 64 bits. Linux/macOS pueden utilizar motores GRASS o TauDEM/MPI ya disponibles; este paquete no incluye ejecutables nativos para esas plataformas. La descarga de los MDT y la consulta de IA requieren conexión a sus respectivos servicios, sin instalar plugins adicionales.

## Instalación y uso

Como alternativa al ZIP, Windows dispone de **HidroCAUMAX_v1_0_5_Setup.exe** en la misma [publicación de GitHub](https://github.com/jmdiez2026/HidroCAUMAX/releases/tag/v1.0.5). Cierra QGIS, ejecuta el asistente y elige el perfil. La instalación es por usuario, sin permisos de administrador, y conserva una copia de la versión anterior. Después abre QGIS con ese perfil y activa el complemento. El instalador no incluye QGIS ni CAUMAX original y no está firmado digitalmente.

1. Instala **HidroCAUMAX_v1_0_5.zip**, sin descomprimir, desde **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP**. Acepta sustituir la instalación auxiliar anterior y reinicia QGIS después de actualizar para cargar todos los módulos de esta revisión. El nombre visible es **HidroCAUMAX v1.0.5**; la carpeta interna sigue siendo **caumax_consulta** para conservar la continuidad de la actualización.
2. Activa CAUMAX, carga sus capas y abre **Mapa de caudales máximos → Consulta de capas de caudales máximos**. Selecciona periodo y punto; pulsa **Consultar**.
3. Una consulta válida habilita **Cuenca hidrográfica**, a la izquierda del informe nativo. Su texto conserva el **110 %** del tamaño de los botones de la derecha; en esta revisión la altura del botón aumenta otro **10 %** respecto a 1.2.10, con redondeo al píxel más cercano. La anchura reserva espacio para el texto completo y el logotipo **GEA-UVa**.
4. Pulsa el botón para calcular la cuenca. El diálogo se alinea con el borde derecho de QGIS, usa toda su altura y aproximadamente el **36 %** de su anchura. Sigue los cambios de posición y tamaño de QGIS. **Morfología de la Cuenca** presenta la tabla blanca de 19 parámetros: **Parámetro, Valor, Unidad e Interpretación**, con anchuras orientativas **40/13/9/38 %** y espacio mínimo para leer la cabecera Unidad. Valor y Unidad se centran. Interpretación empieza vacía y se rellena al pulsar **Análisis IA** en su encabezado; cada texto ocupa como máximo tres líneas y se conserva completo en su ayuda. Las observaciones técnicas se conservan en los resultados y el informe; se retira su recuadro bajo la tabla.
5. En **Resolución MDT**, situado arriba del diálogo, marca una opción disponible. Se descarga y añade el MDT a Capas y se recalculan automáticamente cuenca, morfología, hipsometría, suavizado, P₀ y los seis mapas Pd, incluidas sus consultas y gráficos. Todas las capas nuevas de MDT y relieve se añaden desmarcadas, incluida la primera de 500 m. Puedes mostrarlas cuando quieras; al reutilizar capas existentes se conserva la visibilidad que hayas elegido. No hace falta pulsar otro botón. **Malla de celdas** controla la trama, activada inicialmente para 500 × 500 y 25 × 25 m.
6. En **Suavizado divisoria**, elige **Original**, **Suavizada** o ambas y ajusta un grado entero. Cada cambio vuelve a calcular todos los resultados sobre la superficie activa, con cancelación de trabajos anteriores y publicación del conjunto completo. **Exportar shape**, compacto y situado inmediatamente a la derecha de **Grado**, guarda las divisorias ya mostradas.
7. La franja **Informe** reúne **Informe (PDF)**, **Guardar proyecto** y **Abrir proyecto** a la izquierda, con **Cerrar** a la derecha en la misma fila y con fondo rojo claro. El cuerpo de la ventana tiene desplazamiento y esa franja permanece visible.

En **CAUMAX → Método Racional → Método racional**, pulsa el nuevo botón inferior **Generar capas resolución mejorada**. Se abre un **cuadro de diálogo independiente, no modal**, para elegir la resolución y pulsar **Generar capas D8** sin abrir Cuenca hidrográfica. El MDT se carga en QGIS nada más estar preparado, sin esperar a StreamNet. Al finalizar, en el mismo grupo «HidroCAUMAX · Drenaje TauDEM» se añaden la red, el ráster de direcciones CAUMAX/ESRI y **«MDT 25x25m» como tercera capa** cuando se trabaja a 25 m (o «MDT 5x5m» a 5 m). Por debajo queda el sombreado de relieve, que permite mostrar colores de elevación con apariencia tridimensional, sin alterar las cotas del GeoTIFF original. Los tres productos continúan siendo exportables por separado. En Windows de 64 bits, los ejecutables y todas sus dependencias están incluidos en el ZIP y se preparan automáticamente al primer cálculo. Se utiliza directamente el motor predeterminado, sin selector de carpeta externa.

El menú **Complementos → HidroCAUMAX** permite activar las herramientas, **Abrir proyecto de cuenca…** y **Buscar actualizaciones…** sin realizar otra consulta nativa. Los botones de cálculo tienen fondo azul claro; Análisis IA utiliza azul oscuro. El botón general **Identificar objetos espaciales** conserva su funcionamiento habitual.

En una cuenca nueva, **Consultar T(Pd)** comienza en **100 mm**. La casilla **Permitir extrapolación** se sitúa entre los campos de consulta y sus resultados, centrada entre las dos filas. Al abrir un proyecto se conserva el valor guardado.

## Actualizaciones desde QGIS

El complemento consulta por HTTPS `https://jmdiez2026.github.io/HidroCAUMAX/plugins.xml` al cargar o activar las herramientas, al abrir o reutilizar una cuenca y al invocar la apertura de un proyecto guardado. Cada invocación vuelve a comprobarlo; solo se agrupan peticiones simultáneas. No envía datos de la cuenca y no bloquea QGIS. Los errores de conexión automáticos no interrumpen el trabajo.

Si existe una versión estable compatible con tu QGIS, aparece un aviso con **Actualizar**. Ese botón añade el repositorio si falta y abre el administrador de complementos filtrado por HidroCAUMAX. Conserva la configuración de los repositorios existentes; si el nuestro estaba desactivado, lo indica. La descarga e instalación se realizan desde el gestor con la intervención del usuario. **Buscar actualizaciones…** muestra también si la versión está al día o si hubo un error de consulta.

Para actualizar desde 1.2.5, añade el índice una vez en **Complementos → Administrar e instalar complementos → Configuración → Repositorios de complementos** y utiliza **Actualizable → Actualizar complemento**. El ZIP instalable es una alternativa. Después de actualizar, realiza una consulta nueva para aplicar el procedimiento vigente de selección del cierre. Abrir un proyecto antiguo restaura sus resultados y cierres guardados.

La tabla morfológica y su informe muestran como máximo dos decimales; los valores no disponibles se indican como **n.d.**. La etiqueta del tramo central es **Longitud curso principal 10%-85% (L10-85)** y la columna Parámetro se reduce para dar más espacio a Interpretación. Los cálculos y proyectos conservan los valores completos. Las magnitudes positivas menores de 0,01 se presentan como **<0,01**; el número de celdas se muestra entero.

## Punto de cierre y elección de MDT

Se conserva el **punto exacto de la última consulta válida de CAUMAX**, junto con su caudal Q y periodo T. En la malla original de **500 m**, el cierre se fija en el **centro de la celda que contiene ese clic**, después de transformarlo al CRS del MDT. La divisoria y el curso se calculan desde esa celda, aunque produzca una cuenca pequeña.

En los **MDT mejorados de 25, 5, 2 y 0,5 m**, se recalcula la red D8 a la resolución nativa y se comprueba la correspondencia espacial con la cuenca original. Se rasteriza la divisoria original sobre la malla fina y se acumulan por separado todas las celdas aportantes y las que pertenecen a esa referencia. Esto permite distinguir un cauce que recoge la cuenca buscada de una pequeña rama lateral o de un río vecino, aunque este tenga mayor aporte.

La política del complemento exige que la intersección supere el **50 % de cada una de las dos superficies rasterizadas**. Es un control geométrico conservador del programa, no un umbral de la Norma 5.2-IC ni una obligación de reproducir el área de 500 m. Todos los candidatos deben pertenecer a una misma cadena D8.

En **Método racional**, se elige el centro de celda admisible de **menor distancia euclídea al punto original**. Un empate puramente numérico se resuelve hacia aguas arriba en la misma cadena. Se comprueba la cadena completa incluso cuando la celda original resulta válida. La orientación inicial del curso de 500 m no limita esta elección. La distancia, el centro elegido, el solape y la cadena comprobada se conservan en el proyecto.

En la **consulta de capas de caudales**, permanece la política de sección: se mantiene la celda original si cumple la correspondencia; en caso contrario, se compara la distancia a los segmentos de la cadena y se elige el primer centro sobre la sección proyectada o aguas arriba, comprobando la orientación del curso original. Cuando la correspondencia exigida por cada modo no puede acreditarse, se solicita **Ajuste Punto de Cierre**.

Después de instalar v1.0, reinicia QGIS y calcula una cuenca nueva desde el punto racional original para aplicar el nuevo ajuste. Los proyectos anteriores conservan sus resultados y elecciones manuales al abrirse.

El contorno final procede de las direcciones del **MDT fino**, no de ampliar artificialmente un polígono ni de suavizar la divisoria de 500 m. El control **Suavizado divisoria** sigue siendo independiente. El punto original y el caudal oficial se conservan; las coordenadas del cierre y la distancia del ajuste quedan registradas. Los informes y proyectos guardan la referencia espacial utilizada en ese cálculo, incluso si más adelante se ajusta manualmente la cuenca de 500 m.

La búsqueda está acotada por el radio de referencia de la malla original. Se comprueban cobertura, NoData, coherencia de la acumulación, conectividad y memoria disponible, sin remuestrear silenciosamente el MDT. La documentación de [GRASS r.water.outlet](https://grass.osgeo.org/grass-stable/manuals/r.water.outlet.html) explica por qué un punto sobre una ladera puede producir una cuenca estrecha y pequeña; la posición del cierre sobre la red de acumulación es determinante.

El resumen del MDT activo, punto original, caudal y cierre se muestra en un recuadro blanco dentro de **Resolución MDT**, inmediatamente debajo del estado del cálculo. El resumen de **Suavizado divisoria** utiliza coma decimal y adapta su altura al texto y al ancho disponibles, sin reservar un hueco fijo sobrante.

El botón **Ajuste Punto de Cierre**, junto a **Malla de celdas**, permite modificar un cierre calculado sin salir de la cuenca. El selector permite:

- Pulsar **Elegir en el mapa** y señalar el cierre deseado.
- Mover el candidato **Norte, Sur, Este u Oeste**, en pasos de una o varias celdas del MDT activo.
- Revisar coordenadas y distancia al punto original antes de **Aplicar cierre**.
- Repetir ajustes después de recalcular, manteniendo abiertas la ventana de la cuenca y la del selector. Cerrar el selector conserva el último resultado aplicado.

El clic manual se transforma al CRS del MDT y se lleva al centro de su celda. Se comprueban la extensión, la máscara, NoData, la cota y la dirección D8. Puede elegirse cualquier celda válida dentro de la extensión disponible, en cualquier orientación, también aguas abajo. La selección manual no tiene el antiguo límite de unos 707 m. Si falta cobertura, hace falta un MDT que incluya el nuevo cierre y su cuenca. Las flechas expresan direcciones de la malla proyectada; no deducen el sentido del flujo.

**Aplicar cierre** recalcula el polígono, el curso, la morfología y los resultados asociados. El informe y el proyecto registran expresamente la elección manual. El punto rojo original y el caudal consultado se conservan. Se trabaja con centros y contornos de celdas: la resolución del MDT limita el detalle de la delimitación.

### Borrar delineados y comenzar otra consulta

La fila superior presenta **Malla de celdas → Ajuste Punto de Cierre → Borrar Delineado**. **Borrar Delineado** retira del mapa los trazados y marcadores, las divisorias, los cursos principales y centrales, los MDT y el relieve, y las capas auxiliares de direcciones y red generadas para **cualquier punto de la sesión**, también de consultas cuyas ventanas ya se cerraron. Las capas se identifican por su pertenencia a HidroCAUMAX; se conservan las capas del usuario y las fuentes de CAUMAX, aunque tengan nombres parecidos o estén dentro de un grupo de resultados.

El borrado mantiene abiertas las ventanas y conserva sus cálculos para consultar, guardar o generar un informe. No borra los archivos de origen ni los datos guardados en disco. Las tareas que terminen después no vuelven a dibujar lo retirado. Un nuevo ajuste del cierre o una elección explícita de resolución permite representar otra vez esa cuenca.

**Cada nuevo clic rojo de consulta limpia los delineados anteriores y cierra sus ventanas** antes de trabajar con el punto nuevo. Se cancelan sus cálculos pendientes y se descartan las respuestas tardías. Dentro de una misma consulta, cambiar de resolución conserva las variantes y activa el curso y las divisorias correspondientes al MDT elegido; las capas de terreno existentes mantienen la visibilidad que haya seleccionado el usuario.

### Resoluciones disponibles

| Opción | Datos y cálculo |
|---|---|
| **500 m · CAUMAX original** | MDT y direcciones de drenaje originales configurados en CAUMAX. |
| **25 m** | Descarga oficial IGN/CNIG mediante API-Coverages IDEE, previa comprobación de disponibilidad. |
| **5 m** | El mismo proveedor oficial, con descarga y cálculo de mayor detalle. |
| **2 m / 0,5 m** | Controles deshabilitados en esta entrega: no se ha comprobado una colección de descarga automática utilizable mediante la API integrada. La limitación se refiere a ese acceso automático, no a la existencia de esos productos en la zona. |

Las opciones descargadas necesitan conexión al servicio oficial. Se comprueban cobertura, resolución, CRS y cotas de la extensión solicitada. Para el MDT nuevo se obtienen direcciones **D8 con GRASS GIS o, si no está disponible, con el TauDEM integrado en Windows de 64 bits**; las cotas originales se conservan para las estadísticas del terreno. El caudal **Q sigue siendo el consultado en el ráster original de CAUMAX**: cambiar el MDT recalcula cuenca y parámetros, no produce un nuevo caudal de avenida.

Antes del cálculo se estima la memoria necesaria: se aplica un límite del **55 % de la RAM disponible** y un máximo de **60 millones de celdas**. Si la cuenca alcanza el borde del recorte, se amplía la extensión hasta **dos veces**, con factor **1,5**. Si persiste el contacto con el borde, se advierte de una posible delimitación truncada.

La resolución activa condiciona área, perímetro, relieve, pendientes y recorridos; figura en la tabla y en el PDF. La malla de 500 × 500 m representa 25 ha por celda. El recorrido controla NoData, límites, ciclos y cancelación, y conserva partes y huecos.

## Morfología de la Cuenca: 19 parámetros

El diálogo y el PDF comparten **19 parámetros** y sus valores y unidades. La tabla del diálogo tiene **fondo blanco** y las columnas **Parámetro, Valor, Unidad e Interpretación**. Su última columna empieza vacía y se completa con la respuesta de **Análisis IA**. El PDF general conserva sus interpretaciones calculadas a partir del catálogo de parámetros; las definiciones completas figuran en los textos de ayuda y en el informe.

El botón azul oscuro **Análisis IA** interpreta todos esos parámetros y los datos hidrológicos disponibles. Está en el encabezado Interpretación y, para consultas de capas de caudales, también a la derecha del título Resolución MDT. Su uso se describe en la sección de IA de esta guía.

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
| Longitud curso principal 10%-85% (L10-85) | km | Tramo central del mismo curso: desde 0,10·L hasta 0,85·L, medido desde el cierre; longitud 0,75·L. |
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

Cuando faltan cotas continuas, longitud o pendiente positiva suficientes, aparece **n.d.** (no disponible). Los candidatos Tc ≤ 0,25 h también se presentan como no disponibles, con una observación sobre el cálculo por tramos de flujo. No se sustituyen valores indefinidos por cero.

## Curva hipsométrica y cuantiles de caudal

Bajo los parámetros, la curva hipsométrica muestra **área situada por encima de cada altitud (km²)** frente a **altitud (m)**. La casilla adimensional cambia los ejes a **a/A** y **(h − Hmín)/(Hmáx − Hmín)**. Se conservan 201 muestras de la distribución de cotas válidas; la escala horizontal dimensional utiliza el área total de la cuenca. El **HI mostrado es el exacto**, calculado con la media de todas las cotas válidas; la integral aproximada de la curva muestreada puede diferir. Con relieve nulo, la normalización altimétrica queda indefinida.

El panel de cuantiles consulta **T = 2, 5, 10, 25, 100 y 500 años** en la **celda original de CAUMAX**, usando el punto original de consulta, aunque después se ajuste el cierre manualmente. Ofrece **Q(T)** y **T(Q)** por interpolación lineal de Q respecto a log(T). La extrapolación está **activada por defecto** en las consultas nuevas y se respeta la opción de los proyectos guardados. La tabla muestra **Periodo de retorno T (años)** y **Caudal máximo instantáneo (Qci) [m3/s]**, centrados. Los años se presentan como enteros y los demás valores del panel con un máximo de dos decimales, conservando la precisión de los datos originales. Los gráficos **Qci vs. T** y **T vs. Qci** usan marcas enteras, diez marcas verticales de caudal y doce de periodo cuando el intervalo permite esos enteros, con márgenes medidos y disposición vertical si falta anchura. Conserva los cuantiles originales y los NoData sin forzar su monotonía; si la inversa ofrece varias soluciones o un intervalo, los muestra. Datos, entradas y casillas se guardan por resolución y se recuperan sin los rásteres fuente.

## P₀ y precipitación máxima diaria de la cuenca

La interfaz presenta dos recuadros separados: **Umbral de Escorrentía (P₀)** y **Precipitación máxima diaria de la cuenca**.

Los botones permiten calcular estos datos para la **superficie activa de la cuenca**. Además, cambiar la resolución o el grado de suavizado desencadena el recálculo automático. Se usa toda la superficie seleccionada, incluida la divisoria suavizada cuando está activa; no solo la celda de cierre. Consulta las capas P₀ y de precipitación para **T = 2, 5, 10, 25, 100 y 500 años** configuradas en CAUMAX y conserva sus rutas y resultados. Cambiar de MDT no modifica los valores de esas cartografías: cambia el polígono sobre el que se obtiene la media.

La media en mm pondera cada valor por el **área de intersección entre su píxel y la cuenca**: `Σ(valor × área válida intersectada) / Σ(área válida intersectada)`. Las áreas se miden en el CRS métrico de la cuenca; se conservan partes y huecos, y las transformaciones de CRS se indican en las notas. Las capas P₀ y de precipitación conservan su **resolución nativa**; el MDT activo determina la divisoria sobre la que se promedian. Mejorar el MDT no aumenta la resolución de esas capas.

El panel de **P₀** muestra su media con **dos decimales, en rojo y negrita**, además de mediana, máximo, mínimo y desviación típica. La mediana y la desviación típica **poblacional** se ponderan por el área válida intersectada; los extremos corresponden a los píxeles válidos dentro de la cuenca.

Las etiquetas del resumen se abrevian a **Mín**, **Máx** y **Desv. típ**. La línea de cobertura ráster y área válida se omite en este panel; esos datos se conservan en el proyecto y el informe.

Los dos botones de cálculo tienen la misma anchura compacta. **Calcular Precipitación diaria máxima anual MEDIA (mm)** obtiene los seis mapas y presenta una tabla centrada con **Periodo T (años), Pd (media), Pd mínimo, Pd máximo, Desv. Típica y Estado**. Los valores tienen como máximo dos decimales. El Estado y su ayuda conservan la información de cobertura e incidencias:

- **Cobertura:** área cubierta por el ráster dividida por el área total de la cuenca.
- **Área válida:** área con dato válido dividida por el área total de la cuenca.
- **Píxeles intersectados:** todos los que tienen intersección de área positiva, incluidos los NoData.
- **Estado:** disponible, parcial, no disponible o sin calcular. Una media parcial utiliza únicamente el área válida; los huecos no se rellenan con cero. **Cero es un valor válido**, distinto de NoData.

**P₀ se presenta bruto, sin aplicar β ni otros coeficientes correctores.** Tampoco se aplica reducción areal a la precipitación. Cada **Pd,T** es la **media espacial de los cuantiles puntuales del mapa T**; no es el cuantil de una serie observada de tormentas medias sobre toda la cuenca.

La consulta **Pd(T)** muestra media, mediana, máximo, mínimo y desviación típica a la derecha. Para obtenerlos interpola **los valores por celda en ln(T)** entre mapas de malla compatible y calcula después los estadísticos sobre la superficie válida común; no interpola directamente las medianas ni las desviaciones. **T(Pd)** invierte la curva de medias de los mapas disponibles. Los años se muestran sin decimales, las demás cifras con un máximo de dos, y la extrapolación está **activada por defecto** para nuevas consultas. La inversa conserva soluciones múltiples e intervalos de meseta. Los gráficos **Pd vs. T** y **T vs. Pd** comparten las reglas de tamaño, márgenes y marcas enteras de los gráficos de Qci. Pantalla y PDF utilizan los mismos datos; no estiman nuevas frecuencias de tormentas areales ni recalculan Q de CAUMAX.

Se guardan perfiles comprimidos de hasta **200000 píxeles válidos por mapa** para recuperar los estadísticos de Pd(T) sin las fuentes. Por encima de ese límite se conservan los estadísticos de cada mapa, sin un perfil por celda. Una consulta antigua sin estadísticos o perfiles debe recalcularse con los botones correspondientes; no se inventan los datos que faltan. Los grupos y capas de resultados de HidroCAUMAX se insertan **antes y encima del grupo CAUMAX_CAPAS**, conservando su orden interno y su visibilidad.

Solo se resaltan en negrita la **media obtenida en Pd(T)** y el **periodo obtenido en T(Pd)**. La línea **Desviación típica espacial** expresa la dispersión entre zonas de la cuenca. No representa variabilidad entre años ni el error de la media; se elimina el paréntesis que podía confundir estas magnitudes.

El botón de cálculo de precipitación ocupa una sola línea y las filas de la tabla son compactas. Los resultados de la consulta muestran las etiquetas **Mín**, **Máx** y **Desv. típ. espacial**; la abreviatura conserva el significado espacial del estadístico.

Los resultados quedan vinculados a la geometría original y su CRS mediante una huella **SHA-256**, además del área total. Se guardan por resolución con las entradas y casillas. Al abrir una consulta o generar su PDF **no se vuelven a leer los rásteres fuente ni se recalculan las medias**. Los resultados de otra divisoria no se muestran como si correspondieran a la activa. Las consultas anteriores sin este estado siguen abriéndose; no reciben medias inventadas. Si no se ha solicitado ningún cálculo, el informe lo indica brevemente; con solo P₀ calculado no añade gráficos vacíos de precipitación.

## Formato de la curva hipsométrica

El título único es **Índice hipsométrico (HI) = …**. La casilla **Adimensional** queda dentro del recuadro blanco, arriba a la derecha; se suprimen la etiqueta gris exterior y la nota de puntos/cotas. El eje horizontal dimensional muestra enteros con pasos regulares y aproximadamente diez divisiones. Para áreas menores de 1 km² se indican m². Las marcas de altitud son enteras. En modo adimensional, ambos ejes van de 0 a 1 cada 0,10 y el horizontal se titula **Área relativa (a/A)**. La representación dimensional conserva sus unidades de superficie. Las cifras guardadas y los cálculos mantienen toda su precisión; el PDF utiliza el mismo formato y unidades.

Los gráficos Q y Pd aumentan un 10 % el tamaño de títulos, nombres de ejes y marcas. Se elimina la leyenda inferior de colores. En Q(T)/T(Q), la casilla de extrapolación está junto a las entradas y la explicación de interpolación pasa a la ayuda del control.

## Análisis IA gratuito y sin credenciales

**Análisis IA** conecta directamente por HTTPS con **Pollinations Classic**, mediante `https://text.pollinations.ai/openai`, modelo anónimo `openai-fast`. No utiliza la API de pago de Pollinations ni requiere una clave de OpenAI. Se conserva el acceso anónimo de la API Classic, que es una interfaz antigua y cuya continuidad y límites son externos al complemento. La API nueva de Pollinations utiliza autenticación: esta revisión no solicita claves ni afirma que el acceso anónimo esté garantizado.

La consulta se limita a las 19 interpretaciones de la tabla, en frases técnicas breves. Se envían los nombres y valores numéricos de los parámetros, sus limitaciones, la resolución, el contorno y un resumen reducido de P₀, Pd, Q y ajuste del cierre. Las cifras y unidades de la tabla proceden siempre del programa. Se excluyen coordenadas, geometrías, rutas, archivos y credenciales. Esta reducción evita pedir al proveedor otros seis textos extensos que antes provocaban respuestas truncadas. Se valida que estén presentes las 19 claves antes de rellenar la columna; si una respuesta completa viene marcada erróneamente como limitada, no se descarta por ese indicador. La consulta es asíncrona y no abre otra ventana. Ambos botones se desactivan durante la petición. Cerrar la cuenca o cambiar sus datos cancela la solicitud; las respuestas idénticas se reutilizan en memoria. Si falla el servicio, no se inventan comentarios y se indica el error.

La columna **Interpretación** de la tabla de morfología empieza vacía. Puedes pulsar **Análisis IA** a la derecha de su encabezado. En las consultas de capas de caudales también aparece el botón situado a la derecha del título **Resolución MDT**; ese acceso superior se oculta en Método racional. Utilizan el icono blanco de IA y el fondo azul oscuro; el del encabezado tiene la misma altura que esa fila. Cuando se obtiene una respuesta completa, la tabla muestra las 19 interpretaciones, incluidas las marcas de revisión del contenido. También se actualiza cuando se recupera una respuesta de la caché. El texto completo permanece disponible en la ayuda de cada celda. Si cambian los resultados analizados, la columna queda vacía hasta solicitar un análisis actualizado; una respuesta antigua no puede reemplazarla. Las cifras y unidades calculadas mantienen su valor.

El contexto del modelo remite al [texto consolidado oficial de 5.2-IC](https://cdn.transportes.gob.es/portal-web-transportes/carreteras/normativa_tecnica/07_drenaje/52ic_fom2982016_err_fom1852017_res180326_consolidado.pdf). Pide distinguir el uso del método según el área, Tc del cauce completo frente a Tc10–85, selección del recorrido más desfavorable, P₀ sin β, factores de precipitación pendientes, dispersión espacial y cuantiles originales de CAUMAX. La respuesta debe ligar cada parámetro a comprobaciones aplicadas, señalar los datos ausentes y evitar atribuir categorías de riesgo o una conformidad normativa a los índices morfométricos. El técnico debe contrastar la interpretación antes de incorporarla a un proyecto.

## Generación capas resolución mejorada

El botón **Generar capas resolución mejorada**, situado abajo en **CAUMAX → Método Racional → Método racional**, abre una **ventana independiente no modal** con selector de resolución MDT y desplazamiento para pantallas pequeñas. Los controles originales de CAUMAX no cambian de posición. **Generar capas D8** ejecuta **PitRemove → D8FlowDir → AreaD8 → Threshold → StreamNet** con TauDEM real y prepara la cuenca y su red con esas direcciones, sin sustituir los rásteres configurados ni los resultados del calculador nativo. El programa registra herramientas, versión, procedencia y umbral. Si faltan ejecutables o falla una etapa, informa del problema; no presenta otro motor como si fuera TauDEM. La consulta Q/T original permanece intacta. Las estadísticas de altitud, pendiente local y TRI siguen usando el **MDT original**; el MDT rellenado se usa para encaminar el flujo.

El **umbral de inicio de corriente**, expresado en km², controla la densidad de red: reducirlo incorpora más cauces. El modo automático propone el mayor entre **100 celdas nativas y el 1 % del área de la cuenca TauDEM**, convertido a un número entero de celdas. Es un criterio inicial del complemento, editable; al cambiarlo se regenera la red. **Mostrar red** controla su visibilidad.

Los cuatro botones azules de generación y exportación tienen anchura y altura del **110 %** de su tamaño compacto anterior. Los tres productos se muestran en una fila con títulos centrados —**Modelo Digital Terreno**, **Direcciones de Drenaje** y **Red de drenaje**— y sus botones compactos debajo. **Generar capas D8**, **Umbral inicio corriente**, la entrada numérica, **Automático** y **Mostrar red** se sitúan juntos en una fila compacta. Se retira el botón Cancelar cálculo. El estado del cálculo, la disponibilidad de TauDEM para Windows y el enlace a documentación forman un único texto continuo.


| Botón | Producto |
|---|---|
| **Exportar MDT (TIFF)…** | MDT original, con sus cotas, resolución, extensión, CRS y NoData. |
| **Exportar dirección de drenaje (TIFF)…** | Direcciones D8 en la misma malla, con codificación CAUMAX/ESRI para Método Racional. |
| **Exportar Red de Drenaje (SHP)…** | Red de cauces generada por TauDEM, con geometrías y atributos conservados. |

Las direcciones se exportan siempre como **CAUMAX / ESRI · Método Racional**. No hay un desplegable de codificación; el TIFF nativo de TauDEM sigue siendo un producto interno. La recodificación utiliza estas correspondencias:

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

No basta con renombrar el TIFF: el exportador realiza la recodificación y conserva la malla. En **Windows de 64 bits**, el ZIP incluye **TauDEM 5.5.0, Microsoft MPI y GDAL/PROJ**, con sus licencias y procedencia. Al primer cálculo se verifica y extrae el conjunto en una carpeta privada del usuario, sin permisos de administrador, descargas adicionales, ArcGIS ni modificaciones del PATH global. Las bibliotecas se utilizan únicamente en los procesos de TauDEM. La interfaz utiliza siempre el motor predeterminado e ignora las carpetas externas elegidas en versiones anteriores. En Windows se emplea el conjunto incluido; en Linux/macOS sigue siendo necesaria una instalación de TauDEM y MPI disponible para el sistema, sin selector de carpeta en este diálogo. El archivo `vendor/README.md` documenta el conjunto incluido.

## Cursos principal y central en QGIS

Al abrir la cuenca o cambiar el MDT se crean o actualizan dos capas reales del proyecto:

- **Curso principal total (L)**, visible inicialmente.
- **Tramo del curso principal 10–85 % (0,75 L)**, oculto inicialmente.

El curso principal se dibuja en **azul claro (#65C7FF)**, con trazo de **1,1 mm** y borde oscuro de **1,5 mm** para que destaque sobre el relieve. El tramo central usa **azul más pálido (#A6E7FF)** y trazo discontinuo; su casilla sigue desactivada inicialmente. El PDF comparte esta simbología.

Puedes activar sus casillas en el panel de capas. Las líneas siguen los centros de las celdas D8: L llega hasta el nodo de cabecera del recorrido, sin prolongarlo hasta el borde literal de la divisoria. El tramo central se interpola sobre ese mismo recorrido entre 0,10·L y 0,85·L, contados desde el cierre.

Las capas generadas permanecen en el proyecto al cerrar el diálogo, hasta pulsar **Borrar Delineado** o señalar un nuevo punto de consulta. Las capas generadas se pueden conservar con el proyecto QGIS; las consultas antiguas conservan sus instantáneas de drenaje archivadas. Los contornos temporales de cuenca y suavizado se retiran al cerrar; los archivos exportados se conservan.

## Fondo MDT y legibilidad de la cuenca

Las capas nuevas de **MDT y relieve se crean desactivadas**, también al abrir la primera cuenca de 500 m. Activa sus casillas en el panel de capas si deseas verlas. Recalcular o volver a una resolución con capas ya existentes conserva tu elección. Abrir un proyecto QGIS que contiene esas capas respeta su visibilidad guardada; el valor predeterminado desactivado se aplica a las capas nuevas.

Cuando se activa, el MDT de la resolución seleccionada se representa con una **paleta natural de verdes salvia luminosos, ocres suaves y tonos de roca**, combinada con **sombreado con luz del noroeste más marcado** para apreciar mejor el relieve. QGIS dibuja ambos directamente desde el MDT real a su resolución nativa, según la escala solicitada. Los ajustes de color y sombreado son exclusivamente visuales: no modifican cotas, direcciones D8, punto de cierre ni parámetros hidrológicos.

La rampa cd-a utilizada en la versión 1.2.3 es obra de Jim Mossman (2005), distribuida en cpt-city. Portions of this work include intellectual property of Jim Mossman and are used herein with permission. Copyright (C) 2005 Jim Mossman. All rights reserved. Su licencia y atribución originales se conservan en `CPT_CITY_LICENSE.txt`.

Al desplazar o ampliar el mapa, la rampa y su leyenda se ajustan automáticamente a las cotas válidas de una ventana de referencia local. Esta ventana parte del rectángulo que encierra la cuenca y añade un **10 % de margen por cada lado**; el rango se calcula sobre su intersección con el encuadre visible. En un acercamiento, se utilizan las cotas del área visible dentro de ese entorno. Así, al alejar el mapa, no se incorporan las cotas de todo el MDT para representar una cuenca pequeña.

Si el encuadre queda completamente fuera del entorno de la cuenca, la ventana se centra en el lienzo y sus dimensiones se limitan a las del entorno: **1,2 veces la anchura y la altura del rectángulo de la cuenca**. Los proyectos antiguos que no contienen este contexto siguen usando el área visible hasta abrir la consulta o recalcular; esa operación incorpora el entorno de referencia.

El cálculo cartográfico se realiza en segundo plano tras una pausa breve de navegación y descarta solicitudes anteriores; respeta el CRS del lienzo, los NoData y la visibilidad de las capas. La iluminación del noroeste a 35° utiliza un realce vertical visual ×3, opacidad del 80 % y mezcla Multiplicar. Al ampliar se suaviza la representación mediante interpolación cúbica spline antes del sombreado; la malla original, los valores del archivo y todos los cálculos hidrológicos permanecen intactos.

El relleno de la cuenca original es **rojo al 20 % de opacidad**, equivalente al **80 % de transparencia** (alfa 51/255), para ver el terreno a través de él. La casilla **Malla de celdas** muestra u oculta la trama independientemente del contorno original y conserva su elección por resolución, también en el proyecto y el PDF. Para 500 y 25 m aparece activada por defecto; en resoluciones más finas, desactivada.

Cada MDT dispone de su grupo de **MDT y relieve** como capas normales del proyecto. Cambiar la resolución de cálculo añade el nuevo grupo y sus capas desmarcados; los grupos existentes conservan sus casillas. Volver a una resolución reutiliza su grupo y su visibilidad. **Borrar Delineado** o un nuevo punto de consulta retira esos grupos generados del mapa, sin borrar los rásteres de disco. El mapa no cambia de extensión. El PDF representa la resolución de cálculo activa con sus cursos y su divisoria.

## Suavizado y exportación

El grado **1–45**, con incrementos de **1**, controla un **filtro gaussiano periódico** sobre los anillos remuestreados. Se implementa con NumPy del entorno QGIS. Su escala es:

**σ = min(resolución X, resolución Y) × grado / 15**

El grado **15** equivale a σ de una celda y **45** a tres celdas de la dimensión menor. En una malla de 500 m son 500 y 1500 m. Es una intensidad gradual, no un número de iteraciones.

El **cierre permanece fijo**; cuando es necesario se ajusta localmente el contorno para conservar esa sección. Se exige geometría válida, conservación de partes y huecos e inclusión del cierre. Si un grado no cumple, se informa para reducirlo antes de exportar.

El mapa muestra la original en rojo transparente y la suavizada en azul. El grado es un **entero de 1 a 45**. Si está seleccionada **Suavizada**, esa superficie pasa a ser la superficie de análisis: se recalculan área, perímetro e índices, altitudes, hipsometría, pendiente y TRI, recorridos y tiempos D8, P₀, Pd y sus estadísticos. Las fracciones de celda se ponderan por su área exacta de intersección. La red archivada se recorta de nuevo desde su copia original. El MDT y las direcciones nativas se mantienen como fuentes; el suavizado no rellena el terreno ni desvía los cauces. Desmarcar **Suavizada** recalcula sobre la divisoria original. Los trabajos anteriores se cancelan y se descartan las respuestas tardías; guardar, exportar y generar PDF requiere que termine el conjunto actual.

Las divisorias también se incorporan como **capas vectoriales sin relleno** al administrador de QGIS, por encima del MDT y de CAUMAX. **Cerrar el diálogo conserva estas capas y su visibilidad**, hasta pulsar **Borrar Delineado** o señalar un nuevo punto de consulta. Los archivos GeoPackage se guardan en `HidroCAUMAX/boundaries` dentro del perfil de QGIS, por lo que el proyecto QGIS puede guardar y reabrir sus referencias. La misma consulta y resolución reutilizan las capas; los cambios de grado actualizan su geometría. El botón **Exportar shape** sigue disponible para guardar una copia en otra ubicación.

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

**Abrir proyecto**, o **Complementos → HidroCAUMAX → Abrir proyecto de cuenca…**, recupera todas las variantes y la selección activa **sin necesitar los MDT originales ni recalcular el terreno**. Puedes generar el PDF de la activa y exportar sus geometrías guardadas. Cambiar después de resolución o de grado solicita el recálculo completo y requiere las fuentes correspondientes; si faltan se indica la incidencia y no se publican datos antiguos como nuevos. Las interpretaciones del PDF se reconstruyen con el catálogo común a partir de los valores conservados; la columna del diálogo queda vacía hasta solicitar Análisis IA. Se conservan por separado la divisoria original, la superficie activa y la red de origen para evitar suavizados y recortes acumulativos.

Los proyectos nuevos conservan el punto original y el cierre de cada resolución, junto con el registro de ajuste cuando corresponde. Los proyectos anteriores conservan sus resultados y sus registros históricos de ajuste automático o manual: no se desplaza el cierre ni se recalcula el terreno al abrirlos. Para aplicar el procedimiento vigente a una consulta antigua, realiza una consulta nueva con las fuentes necesarias disponibles.

La red guardada puede mostrarse y exportarse a SHP **sin el archivo de red original**. Los TIFF de MDT y direcciones se conservan como **referencias externas**, no se incrustan en el JSON: para volver a exportarlos deben estar disponibles sus archivos fuente o las copias exportadas. El proyecto conserva los resultados y el PNG aunque esos rásteres falten.

## Exportador del Método Racional incluido

En **CAUMAX → Método Racional → Método racional**, el botón **Guardar cuenca…** permite guardar exactamente los polígonos rojos calculados por CAUMAX como Shapefile o GeoPackage. La integración se activa junto con HidroCAUMAX.

El ZIP conserva además el script original íntegro del usuario en **extras/CAUMAX_Exportador_Divisoria_Original.py** (9478 bytes), como copia de referencia. La integración utiliza su adaptación incluida; no es necesario ejecutar manualmente ese archivo.

## Referencias y validación

- [IGN/CNIG · API-Coverages IDEE: catálogo oficial](https://api-coverages.idee.es/collections?f=json).
- [GRASS GIS 8.5: r.watershed](https://grass.osgeo.org/grass85/manuals/r.watershed.html), cálculo de direcciones de drenaje.
- [HEC-HMS: identificación de cauces mediante un umbral de área acumulada](https://www.hec.usace.army.mil/confluence/hmsdocs/hmsguides/gis-tools-and-terrain-data/creating-a-georeferenced-model-using-hec-hms-gis-tools/preprocessing-terrain-data-to-delineate-subbasin-and-reach-elements). Referencia general para la generación de redes por acumulación; no define por sí sola la posición del cierre de una consulta.
- [TauDEM: D8 Flow Directions](https://hydrology.usu.edu/taudem/taudem5/help53/D8FlowDirections.html), definición local de pendiente, bordes, NoData y depresiones.
- [TauDEM: código y herramientas oficiales](https://github.com/dtarb/TauDEM) y [descargas y dependencias](https://hydrology.usu.edu/taudem/taudem5/downloads.html), para el procesamiento real y sus productos.
- [Norma 5.2-IC, texto consolidado del Ministerio](https://cdn.mitma.gob.es/portal-web-drupal/carreteras/52ic_fom2982016_err_fom1852017_res180326_consolidado.pdf), apartado 2.2.2.5; [Orden FOM/298/2016 en BOE](https://www.boe.es/buscar/doc.php?id=BOE-A-2016-2405).
- [GDAL: TRI Riley y Wilson](https://gdal.org/en/stable/programs/gdal_raster_tri.html) y [Riley, DeGloria y Elliot (1999)](https://arc.lib.montana.edu/ojs/index.php/IJS/article/view/2914).

- [Pollinations Classic: documentación](https://github.com/pollinations/hive/blob/main/POLLINATIONS_APIDOCS.md) y [catálogo público de modelos anónimos](https://text.pollinations.ai/models). La integración usa el servicio Classic comprobado, no la API de pago gen.pollinations.ai.
El repositorio incluye pruebas reproducibles de cálculo, interfaz, recuperación de resultados y exportación. Los registros históricos locales se mantienen fuera de esta publicación porque contienen rutas y referencias a sesiones de trabajo. HidroCAUMAX v1.0 se distribuye en el canal **estable**; la comprobación de un proyecto concreto debe realizarse en su instalación de QGIS y con sus fuentes de datos.

