# HidroCAUMAX

Complemento para **QGIS 3** que amplía **CAUMAX 3.0** con delimitación y análisis de cuencas, selección de MDT, morfología, curva hipsométrica, umbral de escorrentía P₀, precipitación diaria máxima Pd y consultas de cuantiles. Versión actual: **1.2.11**. Repositorio: **jmdiez2026/HidroCAUMAX**.

El diálogo integra los estadísticos de cuenca y recalcula los resultados al cambiar la resolución del MDT o el grado de suavizado. Conserva el punto original consultado y los caudales oficiales de CAUMAX: cambiar el MDT modifica la delimitación y los parámetros de la cuenca, sin inventar nuevos caudales de avenida.

## Requisitos

- **QGIS 3.34 a 3.x**, con Python, Qt 5, GDAL y NumPy de su distribución. Esta revisión se verifica con **QGIS 3.40.6 y GDAL 3.10.3 en Linux**. Las comprobaciones históricas de Windows/QGIS 3.44.15 corresponden a la entrega anterior; no equivalen a una nueva prueba de esta revisión en Windows.
- **CAUMAX 3.0 instalado y activo**, con sus capas configuradas. Los datos oficiales de CAUMAX no se distribuyen en este repositorio.
- **No se requiere instalar ni activar otro complemento de QGIS:** el delineado y el tratamiento espacial utilizan las bibliotecas incluidas con QGIS. No se usan Processing, SAGA, Whitebox ni proveedores de terceros. Si GRASS ya está incluido en la instalación de QGIS se puede utilizar directamente; en Windows de 64 bits, cuando falta, se usa automáticamente el TauDEM integrado.
- Conexión a los servicios oficiales IGN/CNIG para descargar MDT mejorados. Las fuentes conservan sus condiciones de disponibilidad y uso.
- Conexión a **Pollinations Classic** para el botón **Análisis IA**, gratuito y sin credenciales. No se requiere una cuenta, una clave ni una biblioteca adicional. La disponibilidad y los límites temporales dependen de ese servicio externo.
- En **Windows de 64 bits**, TauDEM 5.5.0, MPI y sus bibliotecas y datos de GDAL/PROJ están incluidos en el ZIP y se preparan automáticamente. **CAUMAX es el único complemento previo**; el usuario no instala otro complemento ni un motor hidrológico adicional. Se reutilizan las bibliotecas de ejecución de C++ de QGIS/Windows. El paquete de ejecutables integrado es para Windows: en Linux/macOS se requieren los motores GRASS o TauDEM/MPI ya disponibles en esa instalación; no se presenta el ZIP de Windows como un runtime multiplataforma.

La versión 1.2.11 se distribuye en el canal **estable** (`experimental=False`). El código contiene controles de cobertura, NoData y errores; sus resultados deben revisarse con las fuentes y la resolución de cada proyecto.

## Instalación

1. Utiliza el archivo instalable **HidroCAUMAX_v1_2_11.zip** de esta entrega. El ZIP de código fuente que ofrece GitHub no es el instalable de QGIS.
2. En QGIS abre **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP** y selecciona el instalable, sin descomprimirlo.
3. Si ya utilizas HidroCAUMAX, acepta sustituir el complemento anterior y reinicia QGIS para cargar juntos todos los módulos de la nueva versión. La carpeta interna permanece como **caumax_consulta** para conservar la identidad de instalación.
4. Activa CAUMAX 3.0, carga sus capas y realiza una consulta válida. Pulsa **Cuenca hidrográfica** para abrir las herramientas ampliadas.

La [guía detallada del complemento](src/caumax_consulta/README.md) describe las funciones, los cálculos y las limitaciones de cada sección.

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
- **Capas mejoradas en Método racional:** el nuevo botón inferior Generar capas resolución mejorada despliega la sección en el diálogo nativo de CAUMAX. Tiene selector de MDT propio, ocupa la anchura disponible y funciona sin abrir Cuenca hidrográfica. Los cuatro botones internos aumentan al 110 % en anchura y altura.
- **Presentación compacta:** los gráficos Qci/T y Pd/T reducen su altura al 90 %, las filas de caudales se ajustan al texto y los resúmenes de P₀ y precipitación ocupan dos líneas. El botón de precipitación aumenta su anchura al 110 %. Se retira el recuadro de observaciones bajo la tabla morfológica.

## Cambios incluidos en 1.2.9

- **Análisis IA directo en la tabla:** ambos botones generan las 19 interpretaciones en segundo plano y rellenan la columna Interpretación. No se abre una ventana de informe.
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
- **Generación de capas:** Generar capas D8, Umbral inicio corriente, Automático y Mostrar red comparten una fila compacta. Se retira el botón Cancelar cálculo y los productos se titulan Modelo Digital Terreno, Direcciones de Drenaje y Red de drenaje, centrados sobre sus botones.
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
| `.github/workflows/` | Automatización de distribución y del índice de complementos. |

No se incluyen capas de proyectos, sesiones guardadas, cachés, credenciales ni registros locales de validación. Los ensayos generan sus datos de prueba; algunos requieren una instalación externa de CAUMAX, GRASS o TauDEM y se omiten cuando falta ese requisito.

## Preparar una versión

El empaquetado utiliza **Python 3.10 o posterior** y su biblioteca estándar; no necesita iniciar QGIS:

```text
python scripts/build_release.py --output dist --tag v1.2.11
```

Genera el ZIP instalable, sus sumas SHA-256 y el índice XML de distribución. Los archivos resultantes quedan fuera del control de versiones mediante `.gitignore`.

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

El índice es `https://jmdiez2026.github.io/HidroCAUMAX/plugins.xml`. Añádelo una sola vez en **Complementos → Administrar e instalar complementos → Configuración → Repositorios de complementos**. Desde 1.2.6 el complemento comprueba nuevas versiones sin bloquear QGIS y ofrece abrir el gestor mediante **Actualizar**. Si falta el repositorio, ese botón lo añade; no cambia los repositorios existentes ni instala automáticamente. Con el repositorio habilitado, las versiones publicadas aparecen en **Actualizable**, desde donde se instalan con **Actualizar complemento**. No hace falta descargar otro ZIP ni utilizar el servidor oficial de QGIS. La publicación requiere autenticación y acceso al repositorio. La carpeta interna `caumax_consulta` se conserva en las siguientes versiones.

## Licencia y recursos externos

El material de partida no incluye un archivo `LICENSE` ni una declaración explícita de licencia del código de HidroCAUMAX. No se ha añadido ni supuesto una licencia. Queda pendiente que su titular la defina antes de ofrecer permisos de reutilización.

CAUMAX, QGIS, GRASS GIS, TauDEM y las cartografías oficiales son proyectos o recursos externos y conservan sus propias condiciones. El logotipo incluido se mantiene como recurso de la aplicación; su presencia no atribuye una licencia de marca ni de reutilización independiente.
