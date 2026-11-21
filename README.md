# HidroCAUMAX

Complemento para **QGIS 3** que amplía **CAUMAX 3.0** con delimitación y análisis de cuencas, selección de MDT, morfología, curva hipsométrica, umbral de escorrentía P₀, precipitación diaria máxima Pd y consultas de cuantiles. Versión actual: **1.2.2**. Repositorio previsto: **jmdiez2026/HidroCAUMAX**.

El diálogo integra los estadísticos de cuenca y recalcula los resultados al cambiar la resolución del MDT o el grado de suavizado. Conserva el punto original consultado y los caudales oficiales de CAUMAX: cambiar el MDT modifica la delimitación y los parámetros de la cuenca, sin inventar nuevos caudales de avenida.

## Requisitos

- **QGIS 3.34 a 3.x**, con Python, Qt 5, GDAL y NumPy de su distribución. La entrega se ha probado en **Windows con QGIS 3.44.15**; esta declaración no certifica todas las versiones ni todos los sistemas.
- **CAUMAX 3.0 instalado y activo**, con sus capas configuradas. Los datos oficiales de CAUMAX no se distribuyen en este repositorio.
- **GRASS GIS** disponible en QGIS para procesar las direcciones de drenaje de MDT descargados.
- Conexión a los servicios oficiales IGN/CNIG para descargar MDT mejorados. Las fuentes conservan sus condiciones de disponibilidad y uso.
- **TauDEM y MPI**, instalados por separado, para la función opcional de generación de productos D8 mediante TauDEM.

La versión 1.2.2 se distribuye en el canal **estable** (`experimental=False`). El código contiene controles de cobertura, NoData y errores; sus resultados deben revisarse con las fuentes y la resolución de cada proyecto.

## Instalación

1. Descarga el archivo **HidroCAUMAX_v1_2_2.zip** de la versión publicada en GitHub Releases. El ZIP de código fuente que ofrece GitHub no es el instalable de QGIS.
2. En QGIS abre **Complementos → Administrar e instalar complementos → Instalar a partir de ZIP** y selecciona el instalable, sin descomprimirlo.
3. Si ya utilizas HidroCAUMAX, acepta sustituir el complemento anterior y vuelve a abrir su diálogo. La carpeta interna permanece como **caumax_consulta** para conservar la identidad de instalación.
4. Activa CAUMAX 3.0, carga sus capas y realiza una consulta válida. Pulsa **Cuenca hidrográfica** para abrir las herramientas ampliadas.

La [guía detallada del complemento](src/caumax_consulta/README.md) describe las funciones, los cálculos y las limitaciones de cada sección.

## Cambios incluidos en 1.2.2

Esta versión prepara la distribución estable y la actualización desde 1.2.1. Conserva sus cálculos y sitúa la sección completa **Suavizado divisoria** inmediatamente debajo de **Resolución MDT**, con todos sus controles, texto y botón **Exportar shape**.

- Diálogo alineado a la derecha de QGIS, tablas compactas, resultados centrados y textos legibles al cambiar la anchura.
- Estadísticos de P₀ y Pd: media, mediana, mínimo, máximo y desviación típica según cada consulta; P₀ medio resaltado en rojo y negrita.
- Consultas Qci/T y Pd/T con años enteros, presentación de resultados con un máximo de dos decimales y gráficas adaptables.
- Recálculo automático al cambiar el suavizado o la resolución; selector de MDT al principio del diálogo y control de **Malla de celdas**.
- Conservación de la visibilidad del relieve y colocación de las nuevas capas antes y encima del grupo oficial de CAUMAX.
- Rampa de altitudes y leyenda ajustadas automáticamente al MDT visible al desplazar o ampliar el mapa, con colores naturales y sombreado multidireccional. La interpolación bilineal afecta solo a la representación; las cotas y cálculos conservan la malla original.

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
python scripts/build_release.py --output dist --tag v1.2.2
```

Genera el ZIP instalable, sus sumas SHA-256 y el índice XML de distribución. Los archivos resultantes quedan fuera del control de versiones mediante `.gitignore`.

## Pruebas

Para verificar el empaquetado, desde la raíz del repositorio:

```text
python -m unittest discover -s tests -p test_release_packaging.py -v
```

Las pruebas funcionales deben ejecutarse con **el Python de QGIS**, que proporciona `qgis`, PyQt, GDAL y NumPy. Las pruebas de informes necesitan además `pypdf`. Un ejemplo para las secciones de caudales, precipitación e informes, desde `tests/`, es:

```text
python -m unittest test_flow_quantiles test_basin_hydrology_widget test_reports test_rational_export -v
```

En Windows puede usarse el lanzador `python-qgis-ltr.bat` o el equivalente de la instalación en lugar de `python`. No se fija una ruta de instalación personal. `tests/check_qgis_env.py` permite comprobar las versiones de QGIS y GDAL del intérprete seleccionado.

La validación de la entrega original incluyó **281 pruebas globales**, y una comprobación final de **72 pruebas dirigidas** tras los últimos ajustes de interfaz y versión, sin fallos en esa comprobación final. Son ensayos parcialmente solapados; no representan 353 pruebas independientes. Este repositorio mantiene las pruebas reproducibles y excluye los registros históricos que contenían rutas locales.

## Publicación y actualizaciones previstas

La distribución se prepara para **GitHub Releases** y para un índice de complementos de QGIS publicado mediante **GitHub Pages**. El flujo previsto es:

1. Actualizar la versión en los metadatos y verificar el código.
2. Generar y comprobar el paquete.
3. Publicar la etiqueta y el instalable correspondientes.
4. Actualizar `plugins.xml` para que el administrador de complementos de QGIS pueda ofrecer esa versión.

La dirección prevista del índice es `https://jmdiez2026.github.io/HidroCAUMAX/plugins.xml`. Debe configurarse en QGIS después de publicar y comprobar el sitio; la presencia de estos archivos en el repositorio no significa que el servicio esté ya activo. La publicación requiere autenticación y acceso al repositorio. La carpeta interna `caumax_consulta` debe conservarse en las siguientes versiones.

## Licencia y recursos externos

El material de partida no incluye un archivo `LICENSE` ni una declaración explícita de licencia del código de HidroCAUMAX. No se ha añadido ni supuesto una licencia. Queda pendiente que su titular la defina antes de ofrecer permisos de reutilización.

CAUMAX, QGIS, GRASS GIS, TauDEM y las cartografías oficiales son proyectos o recursos externos y conservan sus propias condiciones. El logotipo incluido se mantiene como recurso de la aplicación; su presencia no atribuye una licencia de marca ni de reutilización independiente.
