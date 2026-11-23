# HidroCAUMAX

Complemento para **QGIS 3** que amplía **CAUMAX 3.0** con delimitación y análisis de cuencas, selección de MDT, morfología, curva hipsométrica, umbral de escorrentía P₀, precipitación diaria máxima Pd y consultas de cuantiles. Versión actual: **1.2.6**. Repositorio: **jmdiez2026/HidroCAUMAX**.

El diálogo integra los estadísticos de cuenca y recalcula los resultados al cambiar la resolución del MDT o el grado de suavizado. Conserva el punto original consultado y los caudales oficiales de CAUMAX: cambiar el MDT modifica la delimitación y los parámetros de la cuenca, sin inventar nuevos caudales de avenida.

## Requisitos

- **QGIS 3.34 a 3.x**, con Python, Qt 5, GDAL y NumPy de su distribución. Esta revisión se verifica con **QGIS 3.40.6 y GDAL 3.10.3 en Linux**. Las comprobaciones históricas de Windows/QGIS 3.44.15 corresponden a la entrega anterior; no equivalen a una nueva prueba de esta revisión en Windows.
- **CAUMAX 3.0 instalado y activo**, con sus capas configuradas. Los datos oficiales de CAUMAX no se distribuyen en este repositorio.
- **GRASS GIS** disponible en QGIS para procesar las direcciones de drenaje de MDT descargados.
- Conexión a los servicios oficiales IGN/CNIG para descargar MDT mejorados. Las fuentes conservan sus condiciones de disponibilidad y uso.
- Conexión a **Pollinations Classic** para el botón **Análisis IA**, gratuito y sin credenciales. No se requiere una cuenta, una clave ni una biblioteca adicional. La disponibilidad y los límites temporales dependen de ese servicio externo.
- En **Windows de 64 bits**, TauDEM 5.5.0, MPI y las bibliotecas de GDAL/PROJ están incluidos en el ZIP. No requieren una instalación separada. En Linux/macOS se utiliza una instalación externa de TauDEM y MPI.

La versión 1.2.6 se distribuye en el canal **estable** (`experimental=False`). El código contiene controles de cobertura, NoData y errores; sus resultados deben revisarse con las fuentes y la resolución de cada proyecto.

## Instalación

1. Descarga el archivo **HidroCAUMAX_v1_2_6.zip** de la versión publicada en GitHub Releases. El ZIP de código fuente que ofrece GitHub no es el instalable de QGIS.
2. En QGIS abre **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP** y selecciona el instalable, sin descomprimirlo.
3. Si ya utilizas HidroCAUMAX, acepta sustituir el complemento anterior y vuelve a abrir su diálogo. La carpeta interna permanece como **caumax_consulta** para conservar la identidad de instalación.
4. Activa CAUMAX 3.0, carga sus capas y realiza una consulta válida. Pulsa **Cuenca hidrográfica** para abrir las herramientas ampliadas.

La [guía detallada del complemento](src/caumax_consulta/README.md) describe las funciones, los cálculos y las limitaciones de cada sección.

## Cambios incluidos en 1.2.6

- **Snap al cauce del MDT activo:** en la malla de 500 m, la sección se orienta con el tramo entrante del curso principal, evitando usar la salida de una confluencia como referencia equivocada. En 25 y 5 m se sigue la conexión D8 desde el punto original y se analizan los aportes de las confluencias para asociar el cauce receptor; el cierre se sitúa en el primer centro de su curso sobre la sección proyectada o aguas arriba.
- **Selección asistida del cierre:** si el ajuste automático es ambiguo, se ofrece elegir el cierre en el mapa. **Ajustar cierre…** permite revisar también un resultado ya calculado. El selector tiene movimientos Norte, Sur, Este y Oeste por celdas, muestra las coordenadas y la distancia, y solo aplica el cambio al aceptar. Una elección manual puede desplazarse aguas abajo de forma explícita. La divisoria, el curso y los parámetros se recalculan conjuntamente.
- **Mapa coherente con la cuenca activa:** al cambiar de resolución o consulta se ocultan los grupos anteriores de curso principal y divisorias. Se conservan sus geometrías y sus casillas internas, y se recuperan al reactivar el resultado. Las capas del terreno mantienen su visibilidad.
- **Actualizaciones desde QGIS:** el complemento consulta el índice al cargar/activar sus herramientas y cada vez que se abre o reutiliza una consulta o se invoca la apertura de un proyecto. Avisa con **Actualizar** cuando hay una versión compatible. El botón abre el administrador de complementos; la instalación la realiza el usuario. También existe **Buscar actualizaciones…** en el menú.
- **Trazabilidad:** pantalla, PDF y proyectos distinguen el ajuste automático de la elección manual y conservan el punto original. Los proyectos anteriores mantienen sus resultados guardados; una consulta nueva aplica el algoritmo actual.

El snap utiliza centros de celdas y conectividad D8. La geometría y la exactitud siguen limitadas por la resolución del MDT. No se exige alcanzar el 90 % del área de 500 m ni se utiliza el filtro fijo de 9 celdas/4 veces la mediana para decidir el cauce. Una asociación dudosa se remite al selector, en lugar de publicarse como una cuenca lateral fiable.

### Interfaz y funciones conservadas de 1.2.5

- **Interfaz compacta:** resoluciones `500mx500m`, `25mx25m`, `5mx5m`, `2mx2m` y `0,5mx0,5m`, en una fila cuando caben; MDT activo y punto original en la misma línea. Los paneles P₀ y precipitación diaria quedan separados.
- **Análisis IA:** botón azul oscuro junto a Morfología de la Cuenca. Interpreta los 19 parámetros, P₀, Pd y los cuantiles disponibles, con un enfoque aplicado a la Norma 5.2-IC. Envía únicamente los valores necesarios, sin coordenadas, archivos ni rutas. Se ejecuta sin bloquear QGIS y permite cancelar, reintentar y copiar la respuesta.
- **Gráficos:** HI con título único, casilla adimensional dentro del recuadro blanco, altitudes enteras y ejes adimensionales de 0 a 1 cada 0,10. Títulos, ejes y marcas de los cuantiles aumentan un 10 %. Se retiran textos redundantes y se compacta la consulta Q(T)/T(Q).
- **Precipitación:** media Pd(T) y periodo obtenido T(Pd) destacados en negrita; aclaración «Desviación típica espacial», sin confundirla con variación entre años o error de la media.
- **Correcciones de cálculo y guardado:** lectura de escala y desplazamiento GDAL en caudales, aceptación del cierre sobre el borde de una divisoria suavizada y preparación completa de las resoluciones antes de guardar el proyecto.

El [registro de la revisión](src/caumax_consulta/CHANGELOG.md) explica los cambios y sus límites. Se conservan las funciones de la 1.2.4 descritas a continuación.

### Funciones conservadas de la 1.2.4

- **Generar capas D8** utiliza el TauDEM real incluido en Windows de 64 bits. Los componentes se preparan automáticamente en una carpeta privada del usuario, sin descargas adicionales ni cambios en las bibliotecas de QGIS. La extracción y el cálculo se ejecutan en segundo plano y admiten cancelación. **Motor TauDEM…** permite volver al motor incluido o elegir una instalación externa.
- Curva hipsométrica: eje horizontal dimensional con marcas enteras y aproximadamente diez divisiones. Para cuencas menores de 1 km², el eje muestra m². Los valores internos mantienen su precisión.

Esta versión ajusta la representación del MDT con una paleta natural de verdes salvia luminosos, ocres suaves y tonos de roca, y un sombreado más marcado para apreciar el relieve. El cambio es visual; conserva la ventana de referencia cercana al tamaño de la cuenca.

- Paleta natural de **verdes salvia luminosos, ocres suaves y tonos de roca**, combinada con **sombreado con luz del noroeste más marcado**. Los ajustes afectan a la representación, sin modificar las cotas del MDT ni los cálculos hidrológicos.
- Las divisorias original y suavizada permanecen como capas de QGIS al cerrar el diálogo. Se guardan en GeoPackage dentro del perfil, se actualizan al recalcular y se reutilizan al abrir la misma consulta.
- La ventana de referencia es el rectángulo que encierra la cuenca, ampliado un **10 % por cada lado**. El rango de cotas se obtiene de su intersección con el encuadre visible. Al acercar el mapa, utiliza el área visible dentro de ese entorno.
- Si el mapa queda completamente fuera del entorno de la cuenca, utiliza una ventana centrada en el lienzo y limitada al tamaño de ese entorno: **1,2 veces la anchura y la altura del rectángulo de la cuenca**. Una vista lejana no incorpora automáticamente las cotas de todo el MDT.
- Los proyectos antiguos sin este contexto mantienen el ajuste al área visible hasta abrir la consulta o recalcular, que incorpora el entorno de referencia.

Se conservan las cotas y la malla del archivo, el suavizado, las tablas, las consultas y las exportaciones. La visibilidad de los grupos de curso y divisoria sigue ahora la consulta activa; la del terreno y la de las capas oficiales permanecen bajo control del usuario. La interpolación cúbica spline suaviza exclusivamente la representación en pantalla antes de calcular las sombras. La luz del noroeste a 35° y el realce vertical visual ×3 destacan las laderas; no cambian las elevaciones ni las pendientes calculadas.

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
python scripts/build_release.py --output dist --tag v1.2.6
```

Genera el ZIP instalable, sus sumas SHA-256 y el índice XML de distribución. Los archivos resultantes quedan fuera del control de versiones mediante `.gitignore`.

## Pruebas

La validación de esta revisión incluye los casos de confluencia, conexión lateral, elección manual en todas las orientaciones, coherencia de las capas, guardado y actualizaciones. El resultado consolidado se registra en el [historial de la versión](src/caumax_consulta/CHANGELOG.md).

Para verificar el empaquetado, desde la raíz del repositorio:

```text
python -m unittest discover -s tests -p test_release_packaging.py -v
```

Las pruebas funcionales deben ejecutarse con **el Python de QGIS**, que proporciona `qgis`, PyQt, GDAL y NumPy. Las pruebas de informes necesitan además `pypdf`. Un ejemplo para las secciones de caudales, precipitación e informes, desde `tests/`, es:

```text
python -m unittest test_flow_quantiles test_basin_hydrology_widget test_reports test_rational_export -v
```

En Windows puede usarse el lanzador `python-qgis-ltr.bat` o el equivalente de la instalación en lugar de `python`. No se fija una ruta de instalación personal. `tests/check_qgis_env.py` permite comprobar las versiones de QGIS y GDAL del intérprete seleccionado.

La revisión añade regresiones para el cierre respecto a la sección original, la geometría suavizada, la lectura GDAL, el guardado completo por resolución, la distribución de controles y la conexión IA. Las pruebas de la red utilizan respuestas simuladas controladas; la conexión anónima también se comprueba por separado con datos sintéticos. Las capas originales del caso de la captura no se distribuyen con el repositorio.

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
