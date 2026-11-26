# HidroCAUMAX

Complemento para **QGIS 3** que amplía **CAUMAX 3.0** con delimitación y análisis de cuencas, selección de MDT, morfología, curva hipsométrica, umbral de escorrentía P₀, precipitación diaria máxima Pd y consultas de cuantiles. Versión pública actual: **v1.0.10** (versión técnica del paquete: **1.0.10**). Repositorio: **jmdiez2026/HidroCAUMAX**.

En **Método racional → Generación de capas resolución mejorada**, el selector incluye **500 × 500 m · original CAUMAX** como referencia informativa, junto a los MDT mejorados de 25 y 5 m. Al seleccionar 500 m se muestra en QGIS la cuenca básica y al elegir una resolución fina se vuelve a delinear automáticamente con su MDT y D8. El ajuste del cierre es opcional; el suavizado original/suavizado y el grado se encuentran en la misma sección. El recuadro GEA inferior contiene Calcular, Guardar cuenca…, Cuenca hidrográfica, Generar capas resolución mejorada y Limpiar capas. Si falla «Análisis IA», su estado aparece visiblemente debajo de la tabla morfológica.

El **Calcular original** conserva su formato y obtiene únicamente el caudal individual. El nuevo **Calcular con GEA** abre la tabla de periodos de retorno y caudales, con valores centrados, caudales con dos decimales y gráfico alineado. Las consultas T→Q y Q→T son automáticas; **Permitir extrapolación** está desmarcado por defecto y solo habilita la prolongación de T→Q, identificada en pantalla. La altura de Método racional se ajusta al contenido y su anchura mínima permite los cinco botones; las nuevas leyendas de capas de resolución mejorada aparecen plegadas. Véase la [verificación de 1.0.8](docs/verification-1.0.8.md).

El diálogo integra los estadísticos de cuenca y recalcula los resultados al cambiar la resolución del MDT o el grado de suavizado. Conserva el punto original consultado y los caudales oficiales de CAUMAX: cambiar el MDT modifica la delimitación y los parámetros de la cuenca, sin inventar nuevos caudales de avenida.

## ResolucionMejorada y configuración automática (1.0.10)

El complemento incluye `ResolucionMejorada/Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif` sin modificar sus celdas. Esta carpeta, dentro de la instalación del perfil de QGIS, es también el destino inicial de las exportaciones de MDT, direcciones ESRI D8 y red de drenaje. Se puede elegir otro destino en cada exportación.

Al abrir **Configurar capas de cálculo**, HidroCAUMAX propone la última pareja completa y compatible exportada y registrada en esa carpeta: misma malla, CRS EPSG:25830 y direcciones ESRI D8. Una exportación incompleta no sustituye una pareja válida; si la última ha cambiado o desaparecido se busca la anterior. Sin pareja registrada se proponen el MDT y las direcciones originales y se informa de ello. P0 apunta al archivo incluido, el factor de torrencialidad apunta al `i1id.tif` original de CAUMAX y la cuenca mínima propuesta es **2 km²**. Las precipitaciones no cambian. El usuario puede editar las propuestas; solo **Aceptar** las guarda en la configuración nativa. **Valores por defecto** vuelve a aplicar estas propuestas mientras HidroCAUMAX está activo.

Al pulsar **Generar capas resolución mejorada** se carga el contenido cartográfico de `ResolucionMejorada` en **HidroCAUMAX mejoradas**, por encima de **CAUMAX_CAPAS** y del grupo de referencias si ya existe. Se omiten los archivos auxiliares de cada capa. Repetir la pulsación incorpora archivos nuevos sin duplicar los ya cargados en ese grupo. Las leyendas aparecen plegadas y solo `Red_drenaje_25m.shp` queda activada; si falta, todas quedan desactivadas. El archivo P0 es un origen protegido.

**Limpiar capas**, con icono E3 y fondo rojo claro, está en el extremo derecho del recuadro GEA y también a la derecha de **Ajustar punto de cierre** en la ventana D8. Ambos accesos muestran la misma lista revisable de capas y archivos antes de borrarlos, con Cancelar como opción inicial. La limpieza de la barra sigue retirando solo capas de QGIS y conserva sus archivos.

La barra muestra icono y texto: **HidroCAUMAX · Capas · Abrir cuenca · Limpiar capas · Actualizar**. El icono C1 de capas queda inmediatamente a la derecha del logo.

El **Calcular** del recuadro GEA introduce primero el **valor medio regional del coeficiente corrector de P0** obtenido de las mismas fuentes y del mismo punto que la ventana nativa de valores recomendados. Después ejecuta el cálculo nativo y abre la tabla y el gráfico. Si no se encuentra un valor válido se informa y no se calcula con un valor inventado. El Calcular original conserva su comportamiento. Los números de la ventana **Valores recomendados** se presentan con coma decimal.

Desde esta versión, cada exportación se conserva también en `HidroCAUMAX/ResolucionMejorada` del perfil de QGIS, fuera de la carpeta que QGIS reemplaza al actualizar el complemento. Se restaura al cargar la versión siguiente sin sobrescribir archivos existentes; una limpieza explícita actualiza la lista para que los archivos borrados no reaparezcan. La primera actualización desde una versión anterior requiere conservar aparte las exportaciones que ya se hayan guardado dentro de la carpeta del plugin, porque esas versiones no creaban esta copia. El antiguo acceso `data/Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif` se conserva al activar el plugin instalado para proyectos existentes.

## Barra de herramientas HidroCAUMAX

La barra y las herramientas se activan automáticamente al cargar el complemento y al iniciar QGIS con HidroCAUMAX habilitado. De izquierda a derecha: **HidroCAUMAX v.1.0** (logo elegido), **Capas** (icono C1 de capas GIS), **Abrir cuenca**, **Limpiar capas** (icono E3 de capas y escoba) , **Actualizar** y **Ayuda**. Pulsar el logo restaura las herramientas y no las desactiva. Comparte las acciones del menú Complementos; puede moverse u ocultarse durante la sesión y se retira al deshabilitar el complemento en el gestor.

**Limpiar capas** retira del proyecto todos los resultados identificados como generados por HidroCAUMAX, incluidos los de consultas anteriores, y cancela el trabajo pendiente para evitar que reaparezcan. Conserva los archivos en disco, las fuentes originales, las referencias y las capas ajenas. El botón de borrado de archivos dentro de la ventana D8 mantiene su función independiente.

El comando nativo **Cargar capas CAUMAX** encuadra la península ibérica y Baleares con un margen, adaptándose al tamaño del mapa y a su sistema de referencia. El encuadre no se impone al abrir un proyecto existente ni al navegar después.

En **Método racional**, el recuadro inferior contiene un único logo GEA, cuatro botones azules y Limpiar capas en rojo claro al extremo derecho. El Calcular original permanece en la fila superior y sigue calculando únicamente el caudal individual. Véase la [verificación de 1.0.9](docs/verification-1.0.9.md).


AEMET · precipitación (inventario histórico), Tramos de ríos · Pfafstetter y Corine Land Cover son capas directas del grupo general, sin un subgrupo intermedio. Se eliminan también sus antiguos contenedores vacíos al repetir la carga en un proyecto anterior, conservando sus capas, estilos y visibilidad efectiva. Los cuatro subgrupos restantes son Extensión zonas inundables, Peligrosidad de inundación · SNCZI, Caracterización agroclimática e IGME.

## Requisitos

- **QGIS 3.34 a 3.x**, con Python, Qt 5, GDAL y NumPy de su distribución. El código incluye pruebas automatizadas para QGIS; los ensayos con QGIS 3.40.6/GDAL 3.10.3 y los ensayos históricos con Windows corresponden a entregas previas. La nueva entrega requiere comprobarse en sus propios entornos y con los MDT del proyecto.
- **CAUMAX 3.0 instalado y activo**, con sus capas configuradas. Los datos oficiales de CAUMAX no se distribuyen en este repositorio.
- **No se requiere instalar ni activar otro complemento de QGIS:** el delineado y el tratamiento espacial utilizan las bibliotecas incluidas con QGIS. No se usan Processing, SAGA, Whitebox ni proveedores de terceros. Si GRASS ya está incluido en la instalación de QGIS se puede utilizar directamente; en Windows de 64 bits, cuando falta, se usa automáticamente el TauDEM integrado.
- Conexión a los servicios oficiales IGN/CNIG para descargar MDT mejorados. Las fuentes conservan sus condiciones de disponibilidad y uso.
- Conexión a **Pollinations Classic** para el botón **Análisis IA**, gratuito y sin credenciales. No se requiere una cuenta, una clave ni una biblioteca adicional. La disponibilidad y los límites temporales dependen de ese servicio externo.
- En **Windows de 64 bits**, TauDEM 5.5.0, MPI y sus bibliotecas y datos de GDAL/PROJ están incluidos en el ZIP y se preparan automáticamente. **CAUMAX es el único complemento previo**; el usuario no instala otro complemento ni un motor hidrológico adicional. Se reutilizan las bibliotecas de ejecución de C++ de QGIS/Windows. El paquete de ejecutables integrado es para Windows: en Linux/macOS se requieren los motores GRASS o TauDEM/MPI ya disponibles en esa instalación; no se presenta el ZIP de Windows como un runtime multiplataforma.

La versión v1.0.10 se distribuye en el canal **estable** (`experimental=False`). El código contiene controles de cobertura, NoData y errores; sus resultados deben revisarse con las fuentes y la resolución de cada proyecto.

## Instalación

**Cambio de numeración anterior:** v1.0 reinició la serie pública desde 1.2.13. Los usuarios que aún tengan 1.2.13 deben instalar manualmente el ZIP de la serie 1.0.x; quienes ya tengan 1.0.0 podrán actualizar a 1.0.5 y posteriores desde el gestor de QGIS, con este repositorio habilitado.


Descargas de la versión **v1.0.10**:

- [ZIP instalable desde QGIS](https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.0.10/HidroCAUMAX_v1_0_10.zip).
- [Instalador de Windows](https://github.com/jmdiez2026/HidroCAUMAX/releases/download/v1.0.10/HidroCAUMAX_v1_0_10_Setup.exe), con icono propio de río en crecida.

**En Windows**, cierra QGIS, ejecuta el instalador y selecciona tu perfil de QGIS 3. El asistente muestra el destino dentro de `%APPDATA%\QGIS\QGIS3\profiles`, trabaja con el usuario actual y no necesita permisos de administrador. Después, abre QGIS con ese perfil y activa HidroCAUMAX. QGIS y CAUMAX original deben estar instalados previamente. Los perfiles ubicados fuera de la carpeta habitual pueden usar el ZIP desde QGIS.

La instalación nueva se prepara antes de sustituir la anterior. Si ya había HidroCAUMAX, se conserva una copia fuera de la carpeta de complementos y el asistente muestra su ubicación. El instalador mantiene los demás perfiles, complementos, ajustes y datos. Su desinstalador retira únicamente los archivos propios de la entrega reconocida. El EXE no tiene firma digital de editor.

**Instalación mediante ZIP**, válida también en Linux y macOS:

1. Utiliza el archivo instalable **HidroCAUMAX_v1_0_10.zip** de esta entrega. El ZIP de código fuente que ofrece GitHub no es el instalable de QGIS.
2. En QGIS abre **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP** y selecciona el instalable, sin descomprimirlo.
3. Si ya utilizas HidroCAUMAX, acepta sustituir el complemento anterior y reinicia QGIS para cargar juntos todos los módulos de la nueva versión. La carpeta interna permanece como **caumax_consulta** para conservar la identidad de instalación.
4. Activa CAUMAX 3.0, carga sus capas y realiza una consulta válida. Pulsa **Cuenca hidrográfica** para abrir las herramientas ampliadas.

La [guía detallada del complemento](src/caumax_consulta/README.md) describe las funciones, los cálculos y las limitaciones de cada sección.

## Capas de referencia

En **Complementos → HidroCAUMAX → Cargar capas HidroCAUMAX**, el complemento añade **HidroCAUMAX · Capas de referencia** como grupo independiente **inmediatamente encima de CAUMAX_CAPAS**. El grupo principal aparece abierto; las capas y subgrupos nuevos están desactivados y plegados. Si CAUMAX_CAPAS aún no existe, se coloca en la primera posición del árbol. Al repetir el comando se recoloca también un grupo ya cargado, sin duplicarlo. Se respeta esta prioridad de dibujo aunque el proyecto utilice un orden de capas personalizado. Contiene estas entradas, en este orden:

| Capa o subgrupo | Contenido |
|---|---|
| Red estaciones AEMET | SHP de 3.249 estaciones con precipitación documentada, diferenciadas por su presencia en los catálogos OpenData consultados. |
| Peligrosidad de inundación · SNCZI | Calados fluviales en tonos azules para T = 10, 100 y 500 años. |
| Google Satellite | Imagen de Google mediante conexión XYZ, desactivada inicialmente. |
| Clasificación Pfafstetter | Tramos de ríos clasificados según Pfafstetter modificado, MITECO. |
| Caracterización AgroClimática | Las 17 variables del servicio MAPA, incluida la clasificación de Papadakis. |
| IGME · Mapa Hidrogeológico | Mapa a escala 1:1.000.000 con hidrogeología, cuencas y unidades hidrogeológicas. |
| Corine Land Cover · Ocupación del suelo | Servicio multiescala oficial del IGN/SCNE: CLC2018, SIOSE2014 y SIOSE AR2017, según la escala de visualización. |

Las 25 capas WMS utilizan imagen PNG transparente: se solicitan al servidor al visualizar el mapa y requieren conexión. El comando conserva el CRS y la extensión actual del proyecto. Repetirlo incorpora únicamente las capas que falten. Las capas de referencia permanecen al borrar los delineados de HidroCAUMAX.

**Google Satellite** se coloca fuera del grupo de referencias, inmediatamente debajo de **Google Terrain**. La capa comienza desactivada; al repetir el comando se conserva la selección de visibilidad que haya hecho el usuario. Utiliza teselas XYZ del servidor de Google, con atribución a Google y sus proveedores.

Los escenarios SNCZI, las variables agroclimáticas y las capas IGME comienzan desactivados. Al abrir Peligrosidad de inundación · SNCZI o Caracterización agroclimática aparece la lista completa con las leyendas plegadas, sin alterar sus casillas de visibilidad. Si un servicio no responde, se comunican las capas fallidas y las demás pueden seguir cargándose.

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

## Cambios incluidos en 1.2.13

- **Cierre automático en Método racional:** al seleccionar MDT de 25 × 25 m o 5 × 5 m se identifica el drenaje correspondiente a la cuenca nativa y se elige el centro de celda admisible más próximo al punto original. Se elimina el veto de orientación del cauce grueso de 500 m que podía abrir innecesariamente el selector manual. **Ajuste Punto de Cierre** sigue disponible.
- **Comprobación del caso comunicado:** en EPSG:25830, X=346039,5 e Y=4717691,8, el ajuste automático desplaza el cierre 34,02 m con MDT25 y 12,02 m con MDT5. Las cuencas resultantes tienen 14,515625 y 14,491800 km², respectivamente, sin alcanzar el borde del MDT. Se comprobaron la mínima distancia entre todos los candidatos, los cambios de resolución en el diálogo y el guardado/reapertura.
- **IA en Método racional:** se retira el botón de la cabecera Resolución MDT. El acceso del encabezado Interpretación permanece disponible.
- **Instalador Windows:** asistente por usuario y perfil de QGIS, icono de crecida, copia de la instalación anterior y desinstalación limitada a sus archivos. El contenido se comprueba contra el mismo ZIP distribuido.
- **Google Satellite:** nueva capa XYZ, inicialmente desactivada, situada después de SNCZI y antes de Pfafstetter. El catálogo reúne 27 capas en siete subgrupos.
- **Posición de las capas de referencia:** el grupo se coloca fuera de CAUMAX_CAPAS, inmediatamente encima, para dibujar sus capas sobre las de CAUMAX. La recolocación se aplica también al repetir el comando con todas las capas ya presentes y contempla el orden de dibujo personalizado.

## Cambios incluidos en 1.2.12

- **Cargar capas HidroCAUMAX:** nuevo comando en **Complementos → HidroCAUMAX** para incorporar un grupo de referencia con CORINE/ocupación del suelo, clasificación Pfafstetter, las 17 variables agroclimáticas, los calados fluviales SNCZI de T10, T100 y T500 el Mapa Hidrogeológico del IGME y la red de estaciones AEMET. Son 25 WMS oficiales y un SHP local de estaciones; no se duplican al repetir el comando.
- **Cuenca hidrográfica en Método racional:** el botón con el logotipo GEA-UVa aparece a la izquierda de **Generar capas resolución mejorada**, en la misma fila inferior del diálogo nativo.
- **La misma cuenca nativa:** se abre el diálogo completo de morfología, hipsometría, MDT, suavizado, P₀ y precipitación a partir de la delineación válida del método racional, hasta 50 km². Se comprueban máscara, cierre, CRS y fuentes de terreno antes de aceptar el análisis.
- **Acceso desde las variables intermedias:** no es necesario haber calculado todavía el caudal final. El resultado Q se presenta como pendiente y solo se incorpora después de un cálculo nativo aceptado y vigente. Cambiar las entradas invalida el Q anterior.
- **Delineación racional con MDT de 25 × 25 m y 5 × 5 m:** el selector de resolución utiliza las mismas herramientas de descarga, direcciones D8, ajuste del cierre, delineación y suavizado que el diálogo de consulta de capas de caudales máximos, según la cobertura oficial disponible.
- **Origen conservado:** los proyectos, informes, exportaciones e interpretaciones distinguen el método racional de la consulta de capas de caudales máximos. Las capas ráster Q no se utilizan como sustituto del cálculo racional. Cambiar MDT, cierre o suavizado recalcula las propiedades de la cuenca, sin modificar el Q nativo.

## Cambios incluidos en 1.2.11

- **Gráficos más compactos:** los dos bloques Qci/T y Pd/T reducen su altura al 90 % de la versión 1.2.10; la altura habitual pasa de 324 a 292 píxeles, con redondeo. El ajuste se aplica también a la disposición vertical de ventanas estrechas.
- **Informe en una sola fila:** Informe (PDF), Guardar proyecto y Abrir proyecto se alinean a la izquierda. Cerrar ocupa el extremo derecho de esa misma fila, con fondo rojo claro y márgenes más compactos.
- **Consulta de precipitación:** Consultar T(Pd) comienza en 100 mm para una cuenca nueva. Los proyectos guardados conservan el valor elegido. La casilla Permitir extrapolación se coloca entre las entradas y los resultados, centrada entre las dos filas, igual que en la consulta de caudales.
- **Tabla morfológica:** filas más compactas con un margen cómodo para el texto; el encabezado conserva su altura y las interpretaciones pueden ocupar varias líneas.

- **Acceso a la cuenca:** el botón Cuenca hidrográfica aumenta su altura un 10 % respecto a 1.2.10.

## Cambios incluidos en 1.2.10

- **CAUMAX como único complemento previo:** no se depende de otros plugins de QGIS. En Windows de 64 bits el TauDEM incluido permite delinear MDT finos aunque QGIS no tenga GRASS; la selección del motor, la caché y los metadatos distinguen ambos procedimientos.
- **Limpieza completa del mapa:** Borrar Delineado retira también los contornos temporales, cursos y cruces roja y azul del lienzo, aunque sus capas ya no aparezcan en el listado. Cancela las tareas pendientes y actualiza la representación, manteniendo las capas ajenas.
- **Verificación de las cuencas finas guardadas:** abrir un proyecto conserva su resultado. Volver a seleccionar 25 m o 5 m comprueba su correspondencia con la cuenca de referencia antes de reutilizar un resultado automático antiguo. Una elección manual explícita conserva su cierre.
- **Cierre coherente en TauDEM:** la generación parte del cierre ya aceptado. Si sus nuevas direcciones producen otra cuenca, se comprueba su correspondencia espacial; un cambio manual de cierre invalida la caché anterior.
- **Capas mejoradas en Método racional:** el nuevo botón inferior Generar capas resolución mejorada abre una ventana independiente no modal, sin modificar el tamaño del diálogo nativo de CAUMAX. Tiene selector de MDT propio, ocupa la anchura disponible y funciona sin abrir Cuenca hidrográfica. Los cuatro botones internos aumentan al 110 % en anchura y altura.
- **Presentación compacta:** los gráficos Qci/T y Pd/T reducen su altura al 90 %, las filas de caudales se ajustan al texto y los resúmenes de P₀ y precipitación ocupan dos líneas. El botón de precipitación aumenta su anchura al 110 %. Se retira el recuadro de observaciones bajo la tabla morfológica.

## Cambios incluidos en 1.2.9

- **Análisis IA directo en la tabla:** ambos botones solicitan únicamente 19 comentarios técnicos breves, sin generar seis apartados adicionales que agotan el límite de texto del servicio. Solo se muestran respuestas completas y validadas; un indicador de límite no invalida por sí mismo un JSON terminado. No se abre otra ventana.
- **Botón del encabezado:** icono blanco de IA y diseño azul oscuro, con la misma altura que la fila del encabezado; el título y el botón comparten la columna sin superponerse.
- **Resolución MDT:** el botón superior se coloca a la derecha de ese título y se elimina la fila adicional que ocupaba.
- **Estado y errores:** durante la petición se desactivan ambos botones. El estado, los errores y los segundos de espera del servicio aparecen en su texto de ayuda, sin ventanas emergentes. Cambiar los datos o cerrar la cuenca cancela la petición pendiente y descarta respuestas antiguas.

## Cambios incluidos en 1.2.8

- **Cierre de la malla de 500 m:** se utiliza el centro de la celda que contiene el punto original, transformado al CRS del MDT. La cuenca se delimita con las direcciones D8 desde esa celda. En los MDT mejorados el punto original sigue siendo la referencia para determinar el cierre y para cualquier ajuste manual posterior.
- **Refinamiento de 25 m y 5 m:** se contrasta la cuenca de cada cierre candidato con la superficie de la cuenca original, mediante la acumulación D8 de la malla fina. Se conserva la celda del clic cuando corresponde a esa cuenca; de lo contrario, se busca un cierre próximo sobre una única cadena de drenaje compatible. Una pequeña escorrentía lateral no se acepta como sustituto del río original. Las situaciones sin correspondencia inequívoca requieren selección manual.
- **Validación de las fuentes:** se comprueban cobertura, NoData y dirección D8. Una incidencia debe resolverse con los datos adecuados o mediante una elección manual expresa; las fuentes y el punto original permanecen registrados.
- **Ajuste manual repetido:** **Ajuste Punto de Cierre** mantiene el selector abierto tras aplicar y recalcular. Permite mover el cierre Norte, Sur, Este u Oeste, con pasos en celdas, y utilizar **Elegir en el mapa**. Puede elegirse cualquier celda válida de la extensión disponible; cada aplicación recalcula divisoria, curso y parámetros.
- **Borrar Delineado para toda la sesión:** la fila queda **Malla de celdas → Ajuste Punto de Cierre → Borrar Delineado**. El borrado retira los trazados, marcadores y capas generadas de divisoria, curso, MDT y drenaje de todas las consultas de la sesión, incluidas las ya cerradas. Conserva las capas del usuario, las fuentes y los cálculos de las ventanas abiertas. Las tareas pendientes no vuelven a dibujar lo borrado; un ajuste o cambio de resolución explícito permite representarlo de nuevo.
- **Nuevo punto rojo, mapa limpio:** cada nuevo clic de consulta retira los delineados anteriores y cierra sus ventanas antes de calcular el nuevo punto. Así no se mezclan un curso o una divisoria de una consulta anterior con el resultado nuevo.
- **MDT y relieve desactivados al crearse:** todas las nuevas capas de terreno, incluida la primera de 500 m, se añaden desmarcadas. Puedes activarlas en el panel de capas; sus elecciones se conservan al reutilizarlas y al abrir un proyecto QGIS que ya las guarda.
- **Tabla y suavizado compactos:** el tramo central se etiqueta Longitud curso principal 10%-85% (L10-85), la columna Parámetro ocupa menos ancho y los valores ausentes se muestran como n.d. Exportar shape se sitúa a la derecha de Grado; el resumen de suavizado utiliza coma decimal y altura ajustada al contenido.
- **Cabecera compacta:** Análisis IA, con icono propio a la izquierda del texto, ocupa la esquina superior derecha fija. El MDT activo, punto original, caudal y datos del cierre se agrupan en un recuadro blanco de Resolución MDT, debajo de su mensaje de estado.
- **Interpretaciones de IA en la tabla:** la columna Interpretación empieza vacía y su encabezado incluye el botón Análisis IA a la derecha. Al completar la consulta, aparecen las 19 interpretaciones del informe. Los cambios de los resultados vacían la columna; las respuestas de un análisis anterior no sustituyen las de la cuenca activa.
- **Resumen de P₀:** las etiquetas se abrevian a Mín, Máx y Desv. típ, y se retira la línea de cobertura del panel. Los estadísticos y sus datos de cobertura se conservan para el cálculo, los proyectos y el informe.
- **Panel de precipitación:** botón de cálculo en una sola línea, filas más compactas y etiquetas Mín, Máx y Desv. típ. espacial en la consulta, con los mismos valores calculados.
- **Generación de capas:** Generar capas D8, Umbral inicio corriente, Automático y Mostrar red comparten una fila compacta. El MDT se carga en QGIS tan pronto como se dispone de él, incluso antes de terminar la red; al completarse, se añade como tercera capa al grupo de drenaje con nombre «MDT 25x25m» (o el correspondiente a la resolución seleccionada), sobre una capa auxiliar de sombreado y con rampa de elevaciones natural. Se retira el botón Cancelar cálculo y los productos se titulan Modelo Digital Terreno, Direcciones de Drenaje y Red de drenaje, centrados sobre sus botones.
- **Proyectos anteriores:** se restauran los resultados y cierres guardados, incluidos los ajustes automáticos históricos y las elecciones manuales. Una nueva consulta aplica el procedimiento vigente; abrir un archivo anterior no recalcula ni desplaza su cierre.

Se conservan la comprobación de actualizaciones desde QGIS, la tabla con un máximo de dos decimales y los controles compactos de **Generación capas resolución mejorada**, con TauDEM predeterminado y direcciones CAUMAX/ESRI. El punto original y su caudal CAUMAX permanecen registrados: modificar manualmente el cierre no recalcula el caudal oficial del punto consultado.

La delimitación utiliza centros de celdas y conectividad D8. Su geometría y detalle dependen de la resolución y calidad del MDT. El curso principal se calcula dentro de la cuenca de la celda seleccionada.

### Interfaz y funciones conservadas de 1.2.5

- **Interfaz compacta:** resoluciones `500mx500m`, `25mx25m`, `5mx5m`, `2mx2m` y `0,5mx0,5m`, en una fila cuando caben; MDT activo y punto original en la misma línea. Los paneles P₀ y precipitación diaria quedan separados.
- **Análisis IA:** botones azul oscuro con icono blanco en el título Resolución MDT y en el encabezado Interpretación. Genera las interpretaciones de los 19 parámetros usando también P₀, Pd y los cuantiles disponibles, con un enfoque aplicado a la Norma 5.2-IC. Envía únicamente los valores necesarios, sin coordenadas, archivos ni rutas. Se ejecuta sin bloquear QGIS y rellena directamente la tabla.
- **Gráficos:** HI con título único, casilla adimensional dentro del recuadro blanco, altitudes enteras y ejes adimensionales de 0 a 1 cada 0,10. Títulos, ejes y marcas de los cuantiles aumentan un 10 %. Se retiran textos redundantes y se compacta la consulta Q(T)/T(Q).
- **Precipitación:** media Pd(T) y periodo obtenido T(Pd) destacados en negrita; aclaración «Desviación típica espacial», sin confundirla con variación entre años o error de la media.
- **Correcciones de cálculo y guardado:** lectura de escala y desplazamiento GDAL en caudales, aceptación del cierre sobre el borde de una divisoria suavizada y preparación completa de las resoluciones antes de guardar el proyecto.

El [registro de la revisión](src/caumax_consulta/CHANGELOG.md) explica los cambios y sus límites. Se conservan las funciones de la 1.2.4 descritas a continuación.

### Funciones conservadas de la 1.2.4

- **Generar capas D8** utiliza el TauDEM real incluido en Windows de 64 bits. Los componentes se preparan automáticamente en una carpeta privada del usuario, sin descargas adicionales ni cambios en las bibliotecas de QGIS. La extracción y el cálculo se ejecutan en segundo plano; al cerrar la consulta se cancelan sus tareas pendientes. El motor predeterminado se utiliza directamente, sin selector de carpeta externa.
- Curva hipsométrica: eje horizontal dimensional con marcas enteras y aproximadamente diez divisiones. Para cuencas menores de 1 km², el eje muestra m². Los valores internos mantienen su precisión.

La representación del MDT conserva una paleta natural de verdes salvia luminosos, ocres suaves y tonos de roca, y un sombreado más marcado para apreciar el relieve. El cambio es visual; conserva la ventana de referencia cercana al tamaño de la cuenca.

- Paleta natural de **verdes salvia luminosos, ocres suaves y tonos de roca**, combinada con **sombreado con luz del noroeste más marcado**. Los ajustes afectan a la representación, sin modificar las cotas del MDT ni los cálculos hidrológicos.
- Las divisorias original y suavizada permanecen como capas de QGIS al cerrar el diálogo, hasta pulsar **Borrar Delineado** o señalar un nuevo punto de consulta. Sus archivos GeoPackage se conservan dentro del perfil; la limpieza del mapa no borra los datos de disco.
- La ventana de referencia es el rectángulo que encierra la cuenca, ampliado un **10 % por cada lado**. El rango de cotas se obtiene de su intersección con el encuadre visible. Al acercar el mapa, utiliza el área visible dentro de ese entorno.
- Si el mapa queda completamente fuera del entorno de la cuenca, utiliza una ventana centrada en el lienzo y limitada al tamaño de ese entorno: **1,2 veces la anchura y la altura del rectángulo de la cuenca**. Una vista lejana no incorpora automáticamente las cotas de todo el MDT.
- Los proyectos antiguos sin este contexto mantienen el ajuste al área visible hasta abrir la consulta o recalcular, que incorpora el entorno de referencia.

Se conservan las cotas y la malla del archivo, el suavizado, las tablas, las consultas y las exportaciones. Dentro de una consulta, la visibilidad de los grupos de curso y divisoria sigue la resolución activa. Los nuevos grupos de terreno nacen desmarcados y conservan después la elección del usuario; las capas oficiales mantienen su visibilidad. Un nuevo punto de consulta retira los resultados generados anteriormente. La interpolación cúbica spline suaviza exclusivamente la representación en pantalla antes de calcular las sombras. La luz del noroeste a 35° y el realce vertical visual ×3 destacan las laderas; no cambian las elevaciones ni las pendientes calculadas.

## Estructura

| Ruta | Contenido |
|---|---|
| `src/caumax_consulta/` | Código y recursos del complemento instalable. |
| `tests/` | Pruebas de cálculo, interfaz, persistencia, exportación y empaquetado. |
| `scripts/` | Preparación y publicación de versiones. |
| `packaging/windows/` | Asistente NSIS e icono del instalador Windows. |
| `.github/workflows/` | Automatización de distribución y del índice de complementos. |

No se incluyen capas de proyectos, sesiones guardadas, cachés, credenciales ni registros locales de validación. Los ensayos generan sus datos de prueba; algunos requieren una instalación externa de CAUMAX, GRASS o TauDEM y se omiten cuando falta ese requisito.

## Preparar una versión

El empaquetado utiliza **Python 3.10 o posterior** y su biblioteca estándar; no necesita iniciar QGIS:

```text
python scripts/build_release.py --output dist --tag v1.0.10
```

Genera el ZIP instalable, sus sumas SHA-256 y el índice XML de distribución. Los archivos resultantes quedan fuera del control de versiones mediante `.gitignore`.

Para añadir el instalador Windows se necesitan **NSIS (`makensis`) y 7-Zip (`7z` o `7zz`)** en la máquina de compilación. No se requiere Python en el equipo donde se ejecuta el instalador:

```text
python scripts/build_windows_installer.py --zip dist/HidroCAUMAX_v1_0_10.zip --manifest dist/release_manifest.json --output-dir dist
python scripts/build_release.py --output dist --tag v1.0.10 --reuse-zip dist/HidroCAUMAX_v1_0_10.zip --installer dist/HidroCAUMAX_v1_0_10_Setup.exe --installer-manifest dist/installer_manifest.json
```

El empaquetador verifica los archivos realmente contenidos en el EXE frente al ZIP, registra las fuentes y la versión del compilador y añade el instalador a las sumas SHA-256 y la página de descarga. El ZIP es reproducible byte a byte; no se presupone que dos versiones distintas de NSIS produzcan un EXE idéntico.

## Pruebas

La validación de esta revisión incluye ajustes manuales repetidos, limpieza global con callbacks tardíos, preservación de capas ajenas, visibilidad del MDT y recuperación de proyectos anteriores. El resultado consolidado se registra en el [historial de la versión](src/caumax_consulta/CHANGELOG.md).

Para verificar el empaquetado, desde la raíz del repositorio:

```text
python -m unittest discover -s tests -p test_release_packaging.py -v
```

Las pruebas funcionales deben ejecutarse con **el Python de QGIS**, que proporciona `qgis`, PyQt, GDAL y NumPy. Las pruebas de informes necesitan además `pypdf`. Un ejemplo para las secciones de caudales, precipitación e informes, desde `tests/`, es:

```text
python -m unittest test_flow_quantiles test_basin_hydrology_widget test_reports test_rational_export -v
```

En Windows puede usarse el lanzador `python-qgis-ltr.bat` o el equivalente de la instalación en lugar de `python`. No se fija una ruta de instalación personal. `tests/check_qgis_env.py` permite comprobar las versiones de QGIS y GDAL del intérprete seleccionado.

Se conservan las regresiones de geometría suavizada, lectura GDAL, guardado completo por resolución, distribución de controles y conexión IA. Las pruebas de red utilizan respuestas simuladas controladas; esta revisión no atribuye una nueva validación del servicio externo de IA. Las capas originales del caso de la captura no se distribuyen con el repositorio.

## Publicación y actualizaciones desde QGIS

La distribución se prepara para **GitHub Releases** y para un índice de complementos de QGIS publicado mediante **GitHub Pages**. El flujo de publicación es:

1. Actualizar la versión en los metadatos y verificar el código.
2. Generar y comprobar el paquete.
3. Publicar la etiqueta y el instalable correspondientes.
4. Actualizar `plugins.xml` para que el administrador de complementos de QGIS pueda ofrecer esa versión.

**Para publicar las siguientes versiones:** guarda los cambios de código, ejecuta `python scripts/prepare_next_version.py --version 1.0.6` (o la versión superior correspondiente), añade la nota al `CHANGELOG.md` y sube los archivos conjuntamente a `main`. GitHub Actions detecta el cambio de `metadata.txt`, valida y compila el ZIP y el instalador, realiza la prueba real de Windows, crea la etiqueta y la Release y publica el índice QGIS. Si falla una fase, se detiene sin marcar el paquete como actualizado. Las publicaciones anteriores se conservan para poder recuperar una versión.

El índice es `https://jmdiez2026.github.io/HidroCAUMAX/plugins.xml`. Añádelo una sola vez en **Complementos → Administrar e instalar complementos → Configuración → Repositorios de complementos**. Desde 1.0.10, el botón **Actualizar** descarga y verifica el paquete de la publicación, conserva las exportaciones y recarga el complemento sin abrir el gestor. Las comprobaciones automáticas solo avisan; instalar requiere pulsar Actualizar. Se rechaza la instalación mientras haya tareas pendientes y se intenta restaurar la versión anterior si falla. El gestor de QGIS sigue siendo una alternativa. No hace falta descargar otro ZIP ni utilizar el servidor oficial de QGIS. La publicación requiere autenticación y acceso al repositorio. La carpeta interna `caumax_consulta` se conserva en las siguientes versiones.

## Licencia y recursos externos

El material de partida no incluye un archivo `LICENSE` ni una declaración explícita de licencia del código de HidroCAUMAX. No se ha añadido ni supuesto una licencia. Queda pendiente que su titular la defina antes de ofrecer permisos de reutilización.

CAUMAX, QGIS, GRASS GIS, TauDEM y las cartografías oficiales son proyectos o recursos externos y conservan sus propias condiciones. El logotipo incluido se mantiene como recurso de la aplicación; su presencia no atribuye una licencia de marca ni de reutilización independiente.


## Nombre, ayuda y mapa inicial

El nombre visible es **HidroCAUMAX v.1.0** y la versión técnica de esta entrega es **1.0.10**. La barra muestra texto junto a todos los iconos y termina en **Ayuda**. Este botón abre un diálogo con las diez páginas del manual incluido, generado como PDF y disponible sin conexión. **Abrir PDF** abre el mismo documento en el lector del equipo. El PDF y sus vistas se incluyen en `docs/manual/`; `scripts/build_manual.py` permite regenerarlos a partir del contenido y las capturas de `docs/manual_sources/`.

Al cargar las capas originales de CAUMAX se apaga **Mapas raster del IGN** y se activa **Google Terrain**, como fondo fuera de CAUMAX_CAPAS, manteniendo el encuadre inicial. Se reutiliza si ya existe. La capa vectorial denominada **Red > 50 km²** recibe rosa intenso `#ff1493`, sin cambiar sus geometrías, grosor ni las otras redes. El cambio se aplica también a la capa ya cargada al activar el plugin.


El P0 incluido recibe automáticamente una rampa **RdYlGn rojo → amarillo → verde**, de 0 a 152, con **16 clases por cuantiles**, interpolación lineal y etiquetas enteras. La capa sigue apagada inicialmente. Google Satellite se sitúa debajo de Google Terrain, fuera del grupo general. Al abrir los grupos **Caracterización agroclimática** y **Peligrosidad de inundación · SNCZI** se muestran todas sus capas con las leyendas plegadas; cada leyenda puede abrirse individualmente después.
