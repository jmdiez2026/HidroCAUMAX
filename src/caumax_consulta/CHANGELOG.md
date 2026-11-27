# HidroCAUMAX v.1.0 — independencia de consulta máxima y Método racional (revisión 1.0.20, 2026-11-27)

- El botón «Cuenca hidrográfica» de **Consulta de capas de caudales máximos** toma siempre MDT y D8 de la configuración original que proporciona CAUMAX (`_defaultLayerPaths`). Se elimina la lógica de 1.0.19 que recurría a capas racionales personalizadas si cubrían el punto.
- Las fuentes de P₀ y precipitaciones empleadas por ese análisis se resuelven también de los valores originales. La configuración personal del **Método racional**, incluidos MDT/D8 de 25 y 5 m, permanece independiente e intacta.
- Se prueba la separación con cambios repetidos de resolución racional, incluso cuando las capas personalizadas cubren el punto, y el fallo seguro si no existe la referencia original.
- Se mantienen las correcciones previas de exportación D8 CAUMAX/ESRI y el estilo rosa de 1,30 mm para Red > 50 km².

---

# HidroCAUMAX v.1.0 — recuperación de Cuenca hidrográfica (revisión 1.0.19, 2026-11-27)

- Se recupera el cálculo de la cuenca desde **Mapa de caudales máximos** cuando el punto consultado no está cubierto por las capas personalizadas de 25/5 m configuradas en CAUMAX. Se verifican las rutas oficiales nativas de 500 m antes de utilizarlas exclusivamente para la cuenca de esa consulta, sin tocar la configuración del método racional ni el caudal original.
- Se muestran las opciones 500/25/5 m de «Resolución MDT» desde la apertura. Una excepción al cargar datos ya no deja el selector vacío ni el indicador en «calculando» de forma indefinida.
- Se incorporan pruebas reales PyQGIS de consulta, recuperación de malla, caso fuera de cobertura, conservación del punto y de la configuración personalizada, y compatibilidad con proyectos guardados.
- Se conservan los estilos de «Red > 50 km²» y los productos finales Esri-D8 de 1.0.18.

---

# HidroCAUMAX v.1.0 — denominación visible Esri-D8 (revisión 1.0.18, 2026-11-27)

- Los grupos y las capas de red calculadas se muestran como **HidroCAUMAX · Drenaje Esri-D8** y **Red de drenaje Esri-D8**. Las etiquetas de diálogos, estados, mensajes y manual adoptan la misma denominación.
- Los proyectos antiguos migran los nombres gestionados sin tocar las capas ajenas ni los datos o los identificadores de QGIS.
- Esta actualización es de presentación. El motor real sigue siendo TauDEM, la procedencia sigue registrada como `HIDROCAUMAX_ENGINE=TauDEM` y la codificación final permanece `FLOW_DIRECTION_ENCODING=ESRI_D8`.

---

# HidroCAUMAX v.1.0 — corrección exportación D8, divisorias y TauDEM (revisión 1.0.17, 2026-11-27)

- Se corrige la comprobación de máscara al exportar direcciones D8 CAUMAX/ESRI cuando TauDEM asigna direcciones fuera del MDT válido. Se verifica el GeoTIFF completo en el destino elegido, incluida la carpeta predeterminada ResolucionMejorada.
- Se conservan la última divisoria de 25 m y la última de 5 m; las anteriores gestionadas de cada resolución se retiran del proyecto y sus GeoPackages se eliminan sólo cuando se identifican como propios, están sin uso y no están protegidos. Cerrar el diálogo no borra ninguna divisoria vigente.
- El panel mantiene un solo grupo TauDEM, el correspondiente a Resolución MDT. Al cambiar de resolución, se retiran los grupos antiguos y sus cachés temporales si están libres. Las exportaciones manuales, P0, CAUMAX original y capas ajenas permanecen intactos.
- Tras Limpiar capas ya no aparece el diálogo modal Archivos conservados. Las incidencias por bloqueo Windows se anotan en el registro de QGIS.
- Pruebas de regresión y flujo de validación PyQGIS incluidos; la aceptación final para Windows queda condicionada a las pruebas y al despliegue.

---

# HidroCAUMAX v.1.0 - cuencas con capas propias y divisoria única (revisión 1.0.16, 2026-11-27)

- Cuenca hidrográfica abre la cuenca del método racional con MDT y direcciones configurados por el usuario, incluidos archivos de distinta extensión con idénticos origen, resolución y CRS. Se exige cobertura de toda la cuenca, sin remuestreo ni recorte del resultado nativo.
- Se conservan exactamente la máscara, geometría, área, punto y caudal aceptados por CAUMAX. Se verifican completos los dos archivos, cada uno con sus dimensiones; también se admite el suavizado y el retorno al contorno original.
- Regresión reproducible a 5, 25, 100 y 500 m, con distinta extensión en ambos sentidos, y rechazo de mallas desplazadas, CRS incompatibles, cobertura insuficiente y fuentes modificadas. La configuración de capas sigue siendo manual.

- La divisoria original de 25 × 25 m de la última consulta sustituye a la anterior en QGIS, incluso al cambiar el punto, el cierre o el polígono. Repetir la misma consulta reutiliza su capa.
- Se retiran las copias gestionadas antiguas al activar el plugin y abrir proyectos. También se corrigen entradas duplicadas del mismo identificador en la lista de capas.
- La divisoria suavizada opcional corresponde a la misma consulta actual. Se conservan los GeoPackages anteriores, las capas ajenas y las otras resoluciones.
- Una sustitución fallida conserva las capas anteriores. Una divisoria en edición impide sustituirla hasta terminar la edición.
- Pruebas reproducibles con cambios de cierre, proyectos antiguos, nuevas instancias, visibilidad, errores de escritura y conservación de archivos. Manual actualizado.

---

# HidroCAUMAX v.1.0 - carpeta inicial sin recuperación automática (revisión 1.0.15, 2026-11-26)

- Se elimina la recuperación automática de exportaciones al iniciar QGIS. Un MDT y su registro de una copia anterior ya no reaparecen en ResolucionMejorada al activar el plugin.
- La instalación incluye exclusivamente el TIFF de P0 y su archivo .tif.aux.xml en esa carpeta. El empaquetado rechaza cualquier archivo adicional y exige ambos archivos.
- Las nuevas exportaciones del método racional siguen usando esa carpeta. Se mantienen los resultados que ya existen; el arranque no borra archivos del usuario.
- Se deja de crear y cargar el grupo HidroCAUMAX · MDT 500 × 500 m · Relieve. Se retiran sus capas gestionadas antiguas, conservando el MDT original de CAUMAX y las capas ajenas.
- Pruebas con una copia antigua, activación repetida y exportación posterior; comprobación exacta de los dos archivos en la instalación Windows.

---

# HidroCAUMAX v.1.0 - presentación D8 y capas sin duplicados (revisión 1.0.14, 2026-11-26)

- Generar capas D8 termina con el grupo abierto y las cuatro leyendas plegadas: red y relieve visibles, MDT y direcciones apagados. La vista previa deja de activar el MDT del resultado final.
- Se elimina la carga automática del grupo HidroCAUMAX mejoradas. Se retira el grupo antiguo gestionado al activar el plugin, abrir un proyecto o abrir el diálogo D8, conservando los archivos y las capas ajenas.
- La identidad de una divisoria ya no depende de la ruta temporal del MDT. Repetir el cálculo para el mismo punto y resolución reutiliza sus capas; las copias idénticas de versiones anteriores se retiran sin borrar los GeoPackages ni capas ajenas o en edición.
- Se mantiene la configuración manual y Valores por defecto de CAUMAX original, con p0.tif. Comprobado en el perfil instalado tras actualizar de 1.0.12 a 1.0.13.
- Manual actualizado y pruebas de presentación, reapertura, copias antiguas y conservación de archivos.

---

# HidroCAUMAX v.1.0 - limpieza de referencias y configuración nativa (revisión 1.0.13, 2026-11-26)

- Corregido Limpiar capas: el botón rojo GEA/D8 y la barra retiran también el grupo de referencias, sus capas y Google Satellite. Se cancelan las cargas pendientes para impedir reapariciones tardías.
- La confirmación del botón rojo incluye las referencias aunque no existan resultados generados. Cancelar conserva capas y solicitudes. Se retiran también los grupos propios vacíos; se preserva cualquier contenido ajeno.
- Los archivos de referencia, incluido P0, se excluyen del borrado incluso ante un registro antiguo erróneo o una referencia incorporada mientras estaba abierta la confirmación. La barra sigue conservando todos los archivos; el botón rojo mantiene la revisión del borrado de productos generados.
- La limpieza automática al iniciar otra consulta mantiene las referencias. Capas conserva su alternancia y actualiza su estado después de limpiar.
- Retirada la autoconfiguración de Configurar capas de cálculo. El diálogo y Valores por defecto funcionan como en CAUMAX original: rutas nativas y cuenca mínima original de 10 km². Se conservan la selección guardada y la edición manual; no se vuelven a imponer MDT/D8 mejorados ni P0 al restaurar valores.
- Pruebas reproducibles de confirmación, cancelación, datos protegidos, grupos, solicitudes pendientes y estado de la barra. Manual actualizado; nombre visible HidroCAUMAX v.1.0.

---

# HidroCAUMAX v.1.0 - alternancia de referencias (revisión 1.0.12, 2026-11-26)

- El botón Capas alterna cargar y retirar de QGIS las referencias gestionadas por HidroCAUMAX, incluida Google Satellite fuera del grupo general.
- Pulsarlo durante la preparación cancela las tareas y retira las referencias parciales; la siguiente pulsación permite cargarlas de nuevo. El estado marcado refleja la presencia de referencias o una carga en curso, y la ayuda del botón describe la acción.
- La retirada conserva los archivos del disco, las capas ajenas y los resultados D8, de resolución mejorada y de CAUMAX. Limpiar capas mantiene su alcance propio sobre los resultados.
- Documentación y manual de diez páginas actualizados. Pruebas de actualización preparadas para la revisión técnica 1.0.12 y la siguiente 1.0.13.
- La identificación visible continúa siendo HidroCAUMAX v.1.0.

---

# HidroCAUMAX v.1.0 - interpretación automática (revisión 1.0.11, 2026-11-26)

- Retirados los dos botones Análisis IA y la consulta externa desde la ventana de cuenca. Las 19 interpretaciones se generan localmente al finalizar el cálculo, también al recuperar proyectos.
- Tabla y PDF comparten lecturas cuantitativas, clases y referencias del tema docente Cuenca Hidrográfica, revisión 21SEP. El material original no se redistribuye.
- Compacidad y elongación de cauce con intervalos documentados; distinción explícita entre Re(Lb) de la fila y Re(L) del tema. Índice hipsométrico con media de cotas y extremos 0,35/0,60 tratados como límites.
- Se conservan los huecos de la clasificación de Melton. TRI informa contraste y escala de la ventana sin universalizar la clasificación del ejemplo docente. No se cambian las fórmulas CAUMAX, los caudales ni las direcciones ESRI D8.
- Comentarios actualizados cuando cambia cuenca, cierre, resolución o superficie suavizada; las incidencias y valores ausentes no reutilizan resultados anteriores como nuevos.
- Pruebas de clases, extremos, datos inválidos y ausentes, actualización de la tabla, ausencia de peticiones IA y concordancia de informe e interfaz. Manual de diez páginas actualizado a esta revisión.
- Nombre visible conservado: HidroCAUMAX v.1.0; versión interna para las actualizaciones: 1.0.11. Véase docs/interpretation-criteria.md.

---

# HidroCAUMAX v1.0.10 — carpeta de exportaciones, capas mejoradas y P0 (2026-11-26)

- Nombre visible HidroCAUMAX v.1.0; paquete y actualizaciones 1.0.10.
- Actualización directa desde la barra: descarga verificada, protección de trabajo pendiente, copia de recuperación y recarga con comprobación de versión.
- Ayuda al extremo derecho, con manual PDF de diez páginas, visor sin conexión y apertura en el lector del equipo.
- Fondo inicial Google Terrain, IGN apagado y encuadre conservado; Red > 50 km² en rosa intenso.

- P0 incluido en ResolucionMejorada; carpeta inicial para exportar MDT, direcciones D8 y red. Conservación fuera del directorio del plugin para futuras actualizaciones, restauración sin sobrescritura y limpieza sin resurrección de archivos.
- Configurar capas de cálculo propone la última pareja compatible registrada, P0 incluido, torrencialidad original y cuenca mínima 2 km². Respeta precipitaciones y la aceptación/cancelación del diálogo nativo.
- Generar capas resolución mejorada carga el grupo HidroCAUMAX mejoradas encima de CAUMAX_CAPAS. Solo Red_drenaje_25m.shp activada, leyendas plegadas y sin duplicados al repetir.
- Limpiar capas rojo claro con icono E3 al extremo derecho del recuadro GEA y en la fila de Ajustar punto de cierre; limpieza compartida con revisión previa.
- Barra con textos breves y Capas inmediatamente después del logo HidroCAUMAX.
- Calcular de HidroCAUMAX aplica el valor medio regional recomendado de P0 antes de obtener los cuantiles. La ventana de recomendaciones utiliza comas. Calcular original sigue independiente.
- Referencias fuera de CAUMAX_CAPAS e inmediatamente encima; grupo abierto, entradas nuevas apagadas y plegadas. AEMET, Pfafstetter y Corine directamente en el grupo general; Google Satellite fuera, bajo Google Terrain, con migración de contenedores anteriores sin duplicar capas.
- Mantiene fórmulas CAUMAX, malla y codificación ESRI D8. Pruebas de almacenamiento, actualización, capas, Qt, cuantiles y paquete; ver docs/verification-1.0.10.md del repositorio.

- Rampa P0 RdYlGn: 16 cuantiles, interpolación lineal, 0–152 y etiquetas enteras. Agroclima y peligrosidad SNCZI abren con las leyendas plegadas.

---

# HidroCAUMAX v1.0.9 — activación automática, barra y recuadro GEA (2026-11-26)

- Las herramientas se activan al cargar el complemento y al terminar el arranque de QGIS. El logo restaura los botones y ya no permite desactivarlos accidentalmente.
- Barra ordenada: Cargar capas (C1, capas GIS), herramientas HidroCAUMAX, Abrir cuenca, Eliminar capas (E3, capas y escoba) y Actualizar.
- Eliminar capas cancela el trabajo pendiente y retira del proyecto todos los resultados con identificación de HidroCAUMAX, de cualquier consulta o resolución. Incluye las exportaciones identificadas desde esta versión; conserva los archivos, las fuentes originales y las referencias. Las exportaciones siguen conservándose al iniciar otra consulta.
- Cargar capas CAUMAX encuadra inicialmente la península ibérica y Baleares en el sistema de referencia del mapa. Respeta proyectos existentes y la navegación posterior.
- Recuadro inferior del Método racional con un único logo GEA visible a la izquierda y cuatro botones azules: Calcular, Guardar cuenca…, Cuenca hidrográfica y Generar capas resolución mejorada. Mantiene el Calcular original en la fila superior, sin abrir el gráfico.
- Pruebas de activación, recarga, orden, limpieza selectiva, preservación de archivos, carga tardía de CAUMAX, encuadre, exportación y cálculo. Ver `docs/verification-1.0.9.md` del repositorio.

---

# HidroCAUMAX v1.0.8 — cálculo GEA, tabla alineada y diálogo compacto (2026-11-26)

- Calcular original conserva su formato y calcula solo el caudal individual. El nuevo Calcular con GEA, abajo a la izquierda, abre la tabla y el gráfico de varios periodos de retorno tras aceptar el cálculo nativo.
- Guardar cuenca… vuelve a la fila principal y añade el logotipo GEA. Los botones añadidos se retiran al desactivar las herramientas.
- Altura de Método racional ajustada al contenido, eliminando el espacio vertical vacío y conservando la anchura. Restauración de la altura previa si no se ha redimensionado manualmente.
- Tabla con valores centrados y caudales con dos decimales fijos y coma. Su borde superior e inferior coincide con el área del gráfico; título horizontal más próximo al eje.
- Casillas Etiqueta, Cuadrícula y Escala logarítmica T compactas a la izquierda. Retira controles de color, estilo, grosor, botón de restablecimiento y textos inferiores solicitados; conserva zoom, arrastre y doble clic para restablecer.
- Permitir extrapolación en Periodo → caudal, desmarcado por defecto. Prolonga la tendencia de los dos valores extremos en log(T), identifica las estimaciones y usa línea discontinua. Al desmarcar se eliminan las consultas extrapoladas; los cuantiles originales y la inversa permanecen intactos.
- Nuevas leyendas de capas de resolución mejorada plegadas, manteniendo el grupo TauDEM abierto y sus estados de visibilidad.
- Pruebas de regresión con Qt/QGIS y comprobación de escritorio de ambos botones, extrapolación, altura y leyendas. Detalles en `docs/verification-1.0.8.md` del repositorio.

---

# HidroCAUMAX v1.0.7 — barra de herramientas visible al cargar (2026-11-26)

- Barra HidroCAUMAX con el logo elegido por el usuario (gota, hidrograma, río y emblema Q Max), conservado sin modificar la imagen.
- Accesos a Activar herramientas, Abrir cuenca, Cargar capas y Buscar actualizaciones. Los botones comparten las acciones del menú Complementos, incluido el estado activado y la desactivación temporal durante la carga de referencias.
- La barra aparece al cargar el complemento y al terminar el arranque de QGIS, incluso si había quedado oculta en la sesión anterior. Puede ocultarse o moverse durante la sesión desde los controles habituales de QGIS.
- Al descargar el complemento se retiran la barra y su conexión al arranque; una nueva carga crea una única barra.
- Pruebas con Qt real de botones, estado, visibilidad restaurada y descarga/recarga.

---

# HidroCAUMAX v1.0.6 — curva racional, D8 de 5 m y referencias (2026-11-26)

- Al aceptar Calcular en el Método racional, abre una tabla de dos columnas alineada con un gráfico interactivo: T=2, 5, 10, 25, 100 y 500 años, más el periodo elegido. Cuantiles con un máximo de dos decimales y coma; precisión interna conservada.
- Consultas T→Q y Q→T incorporadas a la tabla y al gráfico, selección vinculada, zoom, desplazamiento, color, tipo/grosor de línea, cuadrícula, escala lineal/logarítmica y etiquetas opcionales. Marcas enteras en ambos ejes.
- Consultas automáticas sin botones de estimación; entrada y resultado en una línea por recuadro, selectores junto a la etiqueta y con máximo dos decimales. Gráfico reducido a 422×268 píxeles (50 % de anchura y 60 % de altura), con tabla alineada.
- Mantiene el resultado nativo y obtiene los otros cuantiles con las mismas funciones y datos de CAUMAX. Las consultas adicionales interpolan Q respecto a log(T), sin extrapolar; se indican datos ausentes, restricciones regionales e inversas múltiples.

- Corrige el traslado del criterio de cierre validado al motor TauDEM y a la comprobación fina; mantiene la codificación ESRI/CAUMAX y las validaciones de correspondencia entre cuencas.
- Ventana D8 independiente ajustada al contenido, texto introductorio solicitado, selector compacto y separación de controles. Cancelar aparece durante la generación, detiene el trabajo y permite reintentar.
- Eliminar capas retira resultados de QGIS y archivos generados identificables, con revisión previa de rutas y notificación de archivos bloqueados. Conserva fuentes originales y referencias.
- Rampa de relieve con mayor contraste; decimales con coma en los diálogos, conservando los valores numéricos y la entrada nativa de CAUMAX.
- Referencias nuevas apagadas, preparación independiente por servicio, límite de espera y retirada correcta del aviso. WMS/XYZ solicitan el encuadre visible y el zoom del mapa.
- Grupo Extensión zonas inundables debajo de Red estaciones AEMET: T=10, T=50, T=100 y T=500 del SNCZI, con nombres y estilos oficiales verificados.
- Po España (CLC2000) 100mx100m · AdrLBallesteros como última capa. GeoTIFF original incluido en el complemento e instalado con él.
- Retira el botón Guardar cuenca… de la fila del Método racional.
- Regresiones reproducibles, prueba WMS local por BBOX/zoom y script para repetir el caso real de 5 m. Véase `docs/verification-1.0.6.md` para resultados y límites de las pruebas.

---

# HidroCAUMAX v1.0.4 — caudal punta con dos decimales (2026-11-26)

- Método racional: resultado `Caudal (m³/s)` con **dos cifras decimales y coma** en la interfaz (`10,35`).
- Captura reversible del caudal nativo no redondeado, tanto para períodos de retorno estándar como para interpolaciones cuando T se introduce manualmente.
- El cálculo, las fórmulas y los archivos originales de CAUMAX permanecen intactos; el resultado aceptado se presenta en el campo con dos decimales.
- Si el cálculo no aporta valor sin redondear, no se inventan cifras: se añaden dos decimales al valor aceptado.
- Pruebas de regresión de precisión, actualización de la interfaz, resultados cancelados y escritura nativa.

---

# HidroCAUMAX v1.0.1 — actualizaciones desde QGIS (2026-11-25)

- Mantiene todas las funciones de v1.0 y la carpeta de complemento `caumax_consulta`.
- Publicación futura automatizada al incrementar la versión en `metadata.txt` sobre `main`: comprobación de fuente, paquete QGIS, instalador Windows, etiqueta GitHub, Release e índice `plugins.xml` en GitHub Pages.
- Conservación de entregas anteriores para recuperación sin afectar al gestor de QGIS.
- Nombre, metadatos, título de diálogo, informes y referencias de instalación coherentes con la versión 1.0.1.
- La instalación desde QGIS sigue siendo una acción explícita del usuario; ninguna actualización se instala en segundo plano.

---

# HidroCAUMAX v1.0 — reinicio de numeración (2026-11-25)

- Nueva identificación pública **v1.0**; versión técnica QGIS **1.0.0** y etiqueta Git **v1.0**.
- Método racional: selector **500 × 500 m · original CAUMAX**, restauración de la divisoria básica y recálculo automático 25/5 m; suavizado, cierre manual opcional y capa visible en QGIS.
- Fila inferior: **Guardar cuenca**, **Cuenca hidrográfica** y **Generar capas resolución mejorada**, con iconos GEA. Controles nativos más compactos.
- **Análisis IA** informa visiblemente del estado y errores. Su contenido depende de un servicio externo.
- Umbral de inicio de drenaje dependiente de superficie, con ajustes finos y reducciones automáticas si TauDEM no produce tramos; no es un criterio normativo de nacimiento de cauces.
- Se conserva el historial funcional de la serie 1.2.x a continuación.

---

# HidroCAUMAX 1.2.13

## Cierre automático de mínima distancia en Método racional

Seleccionar MDT de **25 × 25 m o 5 × 5 m** ajusta automáticamente el cierre al centro de celda admisible más próximo al punto original. Antes de elegirlo, se exige que la cuenca refinada y la referencia compartan estrictamente más de la mitad de ambas huellas rasterizadas, y que todos los candidatos pertenezcan a una única cadena D8 conectada. Estos filtros son una política geométrica del complemento; no constituyen un umbral normativo ni un objetivo de área.

Se elimina, solo en modo racional, la condición que imponía la orientación inicial del curso de 500 m al drenaje fino. Esa condición descartaba cierres válidos ligeramente al oeste del clic y abría innecesariamente el ajuste manual. La selección nueva minimiza la distancia euclídea; únicamente los empates numéricos se resuelven hacia aguas arriba. **Ajuste Punto de Cierre** permanece disponible para una elección manual. Una correspondencia realmente ambigua sigue requiriendo revisión.

En el punto comunicado **X=346039,5; Y=4717691,8 (EPSG:25830)**, se han ejecutado los cálculos con MDT oficiales reales y D8 de GRASS:

| MDT | Cierre automático X; Y | Desplazamiento | Área | Celdas |
|---|---|---:|---:|---:|
| 25 m | 346012,5; 4717712,5 | 34,0219 m | 14,515625 km² | 23.225 |
| 5 m | 346027,5; 4717692,5 | 12,0204 m | 14,491800 km² | 579.672 |

La enumeración de los 53 candidatos admisibles de 25 m y los 285 de 5 m confirma el mínimo. Ambas cuencas quedan completas dentro del MDT. También se ha comprobado el punto anterior X=346034,4; Y=4717685,7. El diálogo permite pasar de 25 a 5 m sin abrir el selector, actualiza morfología, hipsometría, P₀ y los seis Pd, y conserva el cálculo racional nativo. Guardar y abrir recupera las variantes 500/25/5 y la resolución activa sin necesitar los rásteres originales.

Los proyectos registran la política v2 y la cadena comprobada. El cargador valida conectividad, ausencia de ciclos, recuentos, coincidencia geométrica y elección de mínima distancia. Se conservan los proyectos y elecciones manuales anteriores, así como la política v1 de las consultas de caudales ráster.

## Cabecera del diálogo racional

Se retira **Análisis IA** de la cabecera **Resolución MDT** cuando la cuenca procede del Método racional. Se conserva el botón del encabezado Interpretación y el acceso superior del modo de consulta ráster.

## Instalador Windows

Se incorpora **HidroCAUMAX_v1_2_13_Setup.exe**, con un icono propio de río en crecida. El asistente instala el complemento para el usuario actual, permite elegir un perfil estándar de QGIS 3 y muestra el destino antes de instalar. Requiere QGIS y CAUMAX original ya instalados y pide cerrar QGIS; no necesita permisos de administrador.

El EXE contiene los mismos archivos del ZIP oficial. La actualización prepara la carpeta nueva antes de sustituir la anterior y conserva una copia previa fuera de la carpeta de complementos. El desinstalador limita el borrado a los archivos de su entrega reconocida, sin eliminar los datos de otros complementos ni las configuraciones de QGIS. Se publica el manifiesto del instalador y su suma SHA-256. El icono y las fuentes NSIS forman parte del repositorio.

## Google Satellite

Nueva conexión XYZ **Google Satellite**, situada después de SNCZI y antes de Pfafstetter. Su grupo y su capa se crean desactivados; las cargas posteriores respetan la visibilidad elegida. El catálogo contiene 27 capas en siete subgrupos. Se ha comprobado la entrega de imagen del servidor de Google.

## Capas de referencia por encima de las capas de CAUMAX

**Cargar capas HidroCAUMAX** coloca **HidroCAUMAX · Capas de referencia** como primer subgrupo de **CAUMAX_CAPAS**, antes de Red 10 km², Ríos y las demás capas originales. Si el grupo CAUMAX_CAPAS todavía no existe, las referencias se colocan en la primera posición del árbol.

El comando recoloca un grupo ya existente, también cuando no es necesario descargar ni crear ninguna capa. Se conservan sus capas, nombres, subgrupos y elecciones de visibilidad. El orden de dibujo personalizado también sitúa las referencias por encima de las capas originales.

---

# HidroCAUMAX 1.2.12

## Cargar capas HidroCAUMAX

Nuevo comando en **Complementos → HidroCAUMAX → Cargar capas HidroCAUMAX**. Crea un grupo de referencia con los servicios oficiales de ocupación del suelo (CLC/SIOSE), tramos de ríos Pfafstetter, las 17 variables de Caracterización AgroClimática, los calados fluviales del SNCZI para T10, T100 y T500, y las tres capas del Mapa Hidrogeológico del IGME a escala 1:1.000.000.

Los nombres de capa, estilos y CRS se han contrastado con GetCapabilities y peticiones GetMap reales. Se mantiene el estilo azul oficial de calados y la representación multiescala del endpoint de ocupación del suelo facilitado. Las capas son WMS; se mantiene la extensión y CRS del proyecto. Las cargas repetidas evitan duplicados y los errores de una fuente no impiden cargar las restantes. Los grupos de referencia no se borran al limpiar un delineado.

## Red estaciones AEMET

El grupo de referencia incorpora una capa de puntos **Red estaciones AEMET**, almacenada como SHP real en el perfil de QGIS y disponible sin conexión. Incluye **3.249 indicativos únicos** con precipitación diaria, horaria o mensual documentada, fusionando tipos de estación y conservando los ceros iniciales.

Los círculos diferencian **859 estaciones identificadas en los inventarios OpenData consultados** y **2.390 que no figuran en ellos**. Se cruzan por indicativo los 104 selectores públicos oficiales de climatología y observación convencional consultados el 25/11/2026. La ausencia no se presenta como prueba de inexistencia de cualquier dato abierto, ni se utiliza la condición automática como sustituto de esa comprobación.

Se documenta que la base geométrica es una copia pública de Esri España publicada en 2017, atribuida a AEMET y coherente con el catálogo MITECO actualizado en 2016. No es una relación completa de la red activa de 2026: 133 indicativos OpenData consultados no tienen geometría en esa base. Se incluyen procedencia, fechas, atribución y alcance de la clasificación. Los datos persistentes quedan fuera de la carpeta del complemento y no se sobrescriben al actualizarlo.

## Cuenca hidrográfica en Método racional

El diálogo nativo **Método Racional → Método Racional** incorpora el botón **Cuenca hidrográfica**, con el mismo logotipo, estilo y proporciones del acceso desde las capas de caudales. Se sitúa inmediatamente a la izquierda de **Generar capas resolución mejorada**.

Abre el mismo componente de cuenca sobre la delineación nativa válida, de área igual o inferior a 50 km². El cálculo inicial debe reproducir su máscara, geometría, punto de cierre y CRS; se verifican las fuentes MDT/D8 utilizadas por el calculador. No se une el fondo de la polygonización nativa ni se desplaza el cierre hacia otra red.

La cuenca puede consultarse después de calcular las variables intermedias, aunque todavía falte Q. En ese caso se muestra **pendiente de cálculo**. Los resultados de caudal solo se incorporan tras un cálculo racional aceptado y se invalidan al cambiar sus entradas. No se muestrean las seis capas ráster de caudales para completar ese resultado ni para crear una curva racional inexistente.

Se conservan morfología, hipsometría, MDT mejorados, ajuste del cierre, suavizado, exportación, P₀, precipitación, consultas Pd(T)/T(Pd), PDF, proyectos e interpretación IA. El selector permite **volver a delinear con MDT de 25 × 25 m y 5 × 5 m**, con la misma descarga de terreno, generación D8 y correspondencia de cuenca que el diálogo de consulta de capas, según cobertura oficial. Los proyectos guardan la procedencia racional y admiten Q pendiente; el cargador mantiene la compatibilidad con los proyectos anteriores. El caudal nativo no se recalcula al cambiar el MDT o la divisoria.

Los controles originales de CAUMAX siguen vigentes. En CAUMAX 3.0, el cálculo de variables intermedias rechaza áreas exactamente iguales a 50 km² aunque el cálculo final solo rechaza áreas mayores; esta integración no modifica ese comportamiento del complemento original.

---

# HidroCAUMAX 1.2.11

## Gráficos y pie de la ventana más compactos

- Los bloques de gráficos Qci/T y Pd/T reducen su altura al 90 % de la que tenían en 1.2.10: 324 → 292 píxeles en la disposición horizontal habitual, y reducción proporcional en la disposición vertical. Se mantienen el ancho, las fuentes, los valores y la geometría de las figuras exportadas al informe.
- El recuadro Informe ocupa una sola fila compacta: Informe (PDF), Guardar proyecto y Abrir proyecto a la izquierda; Cerrar a la derecha, con fondo rojo claro y altura alineada. El cierre conserva su conexión con la limpieza del diálogo.
- **Consulta de precipitación:** Consultar T(Pd) comienza en 100 mm para una cuenca nueva. Los proyectos guardados conservan el valor elegido. La casilla Permitir extrapolación se coloca entre las entradas y los resultados, centrada entre las dos filas, igual que en la consulta de caudales.
- **Tabla morfológica:** filas más compactas con un margen cómodo para el texto; el encabezado conserva su altura y las interpretaciones pueden ocupar varias líneas.


- **Acceso a la cuenca:** el botón Cuenca hidrográfica aumenta su altura un 10 % respecto a 1.2.10.


---

# HidroCAUMAX 1.2.10

## CAUMAX como único complemento previo

El delineado y las operaciones espaciales no dependen de Processing, SAGA, Whitebox ni de otro complemento. Se utilizan las bibliotecas propias de QGIS. En Windows de 64 bits, si la instalación de QGIS no incluye ejecutables GRASS, se utiliza automáticamente el **TauDEM integrado en HidroCAUMAX** para calcular las direcciones D8. No se requiere instalar ni activar otro plugin o motor por separado.

La ruta de delineado utiliza únicamente **PitRemove y D8FlowDir**; no necesita preparar una red ni un umbral para delimitar la cuenca. Mantiene el análisis sobre el MDT original y la misma verificación del cierre y de la correspondencia espacial. GRASS sigue disponible cuando ya forma parte de QGIS, y una caché GRASS válida conserva su procedencia aunque su ejecutable original ya no esté presente. Las cachés y metadatos diferencian los motores. El bundle distribuido es para Windows de 64 bits; en Linux/macOS se usan los motores ya disponibles en esas instalaciones.

La ruta alternativa se ha comprobado con el MDT25 oficial de la ubicación comunicada, bloqueando GRASS y las importaciones de Processing: **54,98375 km² y 87.974 celdas**, ejecutando sólo PitRemove y D8FlowDir. Un segundo cálculo reutiliza las direcciones; el SHA-256 del MDT original permanece idéntico. La prueba del motor se realizó en Linux con TauDEM real; el runtime Windows incluido se verifica por su manifiesto y no ha cambiado.

## Borrar Delineado limpia el lienzo y las capas

La limpieza elimina las divisorias, cursos y puntos roja y azul creados por HidroCAUMAX, incluidos los objetos gráficos temporales que permanecían visibles cuando sus capas ya habían desaparecido del listado. Los objetos se identifican por pertenencia explícita al complemento; se conservan las capas y objetos ajenos, incluida SeccionCierreCaumaxGIS. Se detiene el render anterior, se vacía su caché y se actualiza el lienzo.

El borrado cancela cálculos y temporizadores pendientes antes de retirar la representación. Las respuestas tardías no vuelven a dibujar el resultado. El diálogo permanece abierto, conserva sus cálculos terminados y permite generar o ajustar de nuevo. El panel de Método racional participa en la misma limpieza sin cerrarse ni reanudar automáticamente el trabajo por un sondeo periódico.

Reinicia QGIS después de actualizar para cargar todos los módulos de esta revisión y retirar también los objetos huérfanos de versiones antiguas que ya no tengan ninguna referencia identificable.

## Correspondencia de las cuencas de 25 m y 5 m

Al abrir un proyecto se conservan sus resultados guardados. Al volver a seleccionar explícitamente una resolución fina, incluso la que ya aparece activa, un resultado automático antiguo se comprueba contra la cuenca de referencia antes de reutilizarse. Se aprovechan el MDT y las direcciones almacenadas cuando son válidos. Si no están disponibles las fuentes necesarias, se comunica la incidencia y no se presenta una minicuenca histórica como cálculo comprobado. Las elecciones manuales explícitas mantienen su cierre.

La verificación con el MDT25 oficial de IGN/CNIG en la ubicación comunicada reproduce exactamente las **17 celdas y 0,010625 km²** de la captura cuando se usa directamente la celda del clic. La selección de la cuenca correspondiente obtiene **55,206875 km² y 88.331 celdas**, frente a **53,50 km²** en la referencia CAUMAX de 500 m, sin alcanzar el borde del ráster. El resultado se ha comprobado desde la casilla de resolución del diálogo, tanto en un cálculo nuevo como después de guardar y reabrir el resultado antiguo de 17 celdas. La tabla muestra **55,21 km²** y el curso termina en el cierre utilizado.

La ejecución con el **MDT5 oficial** en el mismo punto obtiene **54,7848 km² y 2.191.392 celdas**, también sin alcanzar el borde del ráster. La celda literal del clic aportaba sólo cuatro celdas (**0,0001 km²**). Ambos ensayos usan datos oficiales descargados y direcciones generadas por GRASS; no son terrenos sintéticos.

El área de referencia no es un objetivo numérico: la superficie fina se calcula a partir de sus propias direcciones D8. La comprobación exige correspondencia espacial de las cuencas y una sección de drenaje compatible; no se copia ni se suaviza la geometría de 500 m para fabricar el resultado fino.

## TauDEM conserva la correspondencia al cambiar de motor

La generación ya no repite la asociación local antigua que podía elegir otra rama. En automático se verifica la sección desde el punto original usando las direcciones propias de TauDEM y se exige correspondencia tanto con la cuenca de referencia como con la cuenca fina aceptada. Esto permite resolver una diferencia de celda entre motores conservando la cuenca. Un cierre elegido manualmente permanece fijo. Si la correspondencia no se acredita, no se sustituye la cuenca ni se publica una red de otro punto.

La caché diferencia las geometrías, el cierre aceptado, el CRS, la resolución y el modo manual. Cambiar manualmente el cierre sin cambiar el punto rojo no recupera productos del cierre anterior. Los metadatos registran la procedencia y las comprobaciones del cambio de motor; la cuenca se recalcula con las D8 resultantes.

La ejecución completa con el MDT25 oficial y **TauDEM 5.5.0** produce **54,98375 km² y 87.974 celdas**, con 33 tramos de red. Se comprobaron las exportaciones de MDT, direcciones CAUMAX/ESRI y red SHP, y el guardado y reapertura de la sesión sin leer los rásteres. La cuenca TauDEM cubre el 98,63 % de la cuenca fina de GRASS y el 99,03 % de su propia superficie coincide con ella. Estos resultados documentan la diferencia entre motores manteniendo la correspondencia del drenaje.

## Generación de capas dentro de Método racional

El botón inferior **Generar capas resolución mejorada**, en **CAUMAX → Método Racional → Método racional**, despliega la sección completa debajo del diálogo. La sección deja de ocupar espacio en Cuenca hidrográfica y funciona sin abrir esa ventana. Incluye un selector propio de resolución MDT y utiliza el punto y las fuentes actuales del diálogo nativo, sin depender de un calculador anterior ni modificar los rásteres configurados de CAUMAX.

La sección ocupa la anchura disponible; los tres productos tienen títulos centrados sobre sus botones. Los cuatro botones azules de generación/exportación aumentan al **110 % en anchura y altura**, sin crecer acumulativamente al redimensionar. En pantallas pequeñas, el cuerpo nativo y el panel permiten desplazamiento, manteniendo los botones nativos accesibles. Al recoger la sección se restituye la disposición original.

Se mantienen el TauDEM real predeterminado, el umbral editable, Automático, Mostrar red y las exportaciones separadas de MDT TIFF, direcciones CAUMAX/ESRI TIFF y red SHP. Cambiar el punto invalida tareas y productos anteriores. Borrar Delineado retira las capas generadas y permite volver a generar explícitamente.

## Ajustes de presentación

- Gráficos Qci/T y Pd/T: altura al **90 %**, manteniendo etiquetas, graduaciones y fuentes. No se altera el tamaño de las figuras exportadas al informe.
- Botón de precipitación: anchura al **110 %**, con el texto en una línea y la misma altura.
- Resultado Pd(T): media y mediana en la primera línea; Máx, Mín y Desv. típ. espacial en la segunda.
- P₀: media y nota de β en la primera línea; mediana, máximo, mínimo y desviación típica en la segunda, a la derecha del botón.
- Tabla de caudales: filas ajustadas al alto del texto sin reducir su fuente.
- Se retira el recuadro de observaciones bajo la tabla morfológica. Las observaciones y los datos de cálculo se conservan para el informe.
- Se incluyen los cambios de IA de la 1.2.9: relleno directo de Interpretación sin abrir otro diálogo, icono blanco sobre azul y botón superior alineado con el título Resolución MDT.

## Verificación

**790 pruebas: 782 correctas, 8 omitidas, sin fallos ni errores.** Se ejecutaron con QGIS 3.40.6, GDAL 3.10.3, GRASS 8.4.1 y TauDEM 5.5.0 en Linux. Las fuentes permanecieron idénticas durante la ejecución y QGIS cerró sin tareas pendientes. Las omisiones son siete pruebas antiguas que requieren CAUMAX en un perfil de usuario y una reproducción con datos climáticos no disponibles. Las pruebas nuevas del diálogo nativo y las comprobaciones reales de los MDT sí se ejecutaron. Se verificó también la integridad de los **240 archivos** del runtime Windows incluido.

El progreso del recálculo de superficie convierte correctamente el número de celdas recorridas a porcentajes entre 0 y 100; no cambia las métricas.

Pruebas con Qt/QGIS real, GDAL, GRASS y TauDEM. Incluyen limpieza de tres generaciones de objetos del lienzo con el árbol de capas ya vacío, conservación de objetos ajenos, tareas tardías, recalculado de una cuenca histórica, selección manual, cambio de CRS, exportación, persistencia y redimensionamiento del diálogo nativo. La integración de IA utiliza respuestas simuladas; no se atribuye una nueva comprobación del servicio externo. Los datos geográficos descargados y los registros locales de las pruebas no se incorporan al complemento.


# HidroCAUMAX 1.2.9

## Análisis IA sin ventana adicional

- Los dos botones Análisis IA rellenan directamente la columna Interpretación, sin abrir el informe en otro diálogo.
- La petición se ejecuta de forma asíncrona mediante un objeto sin widgets. Se mantienen el servicio gratuito sin credenciales, el contexto numérico permitido, la revisión de las 19 interpretaciones, la caché y los límites de respuesta.
- Ambos botones se desactivan durante la petición y vuelven a activarse al terminar. El texto de ayuda comunica el estado o error y permite conocer la espera antes de reintentar; no aparecen ventanas emergentes.
- Cambiar el cierre, resolución o resultados cancela la petición y deja la columna vacía hasta solicitar una nueva interpretación. Cerrar el diálogo cancela la conexión y sus temporizadores.

## Botones integrados en sus encabezados

- El botón del encabezado Interpretación incorpora el mismo icono blanco de IA y diseño azul oscuro. Su altura coincide con la fila y su anchura incluye icono y texto.
- El botón superior se alinea a la derecha del título Resolución MDT. Se elimina la fila exterior adicional.

## Verificación

- Pruebas con Qt/QGIS real y respuestas de red simuladas: generación sin widgets, actualización de tabla desde ambos botones, caché, cancelación, destrucción del padre, respuestas tardías, tiempo de espera, límites del servicio y respuestas incompletas.
- Comprobación visual y de geometría a 520, 650 y 900 píxeles, con adaptación del icono a la altura del encabezado.
- Se conservan los cálculos hidrológicos, el delineado, las exportaciones y los formatos de proyecto de la 1.2.8.

# HidroCAUMAX 1.2.8

## Punto original y cierre de la malla de 500 m

En la malla original de **500 m**, las consultas nuevas utilizan el **centro de la celda que contiene el punto original de consulta**, transformado al CRS del MDT. La divisoria y el curso principal se calculan desde esa celda. Los proyectos anteriores conservan sus cierres y diagnósticos históricos al abrirse; una consulta nueva aplica el procedimiento vigente.

## Correspondencia de cuenca al refinar el MDT

El criterio local de las revisiones anteriores podía aceptar una rama de dos celdas frente a vecinas de una celda, sin acreditar su correspondencia con el río original. Los MDT mejorados reciben ahora la geometría nativa y el curso de la cuenca de referencia vigente, incluidas las elecciones manuales posteriores en 500 m.

La acumulación D8 ponderada por esa superficie permite calcular la parte común de las dos cuencas. Se exige mayoría espacial estricta en ambas superficies rasterizadas, una cadena ancestral única y una sección local inequívoca. Se mantiene la celda del clic cuando cumple esas condiciones; en otro caso se busca un centro próximo del cauce compatible. El área de 500 m no es un objetivo numérico y no se vuelve al umbral rígido del 90 %. Una asociación no demostrada solicita revisión manual en lugar de publicar una minicuenca.

La regresión reproducible de 25 m parte de una referencia de **54 km²** y una celda de clic que aporta **0,00125 km²**. El resultado corregido del ráster de ensayo es **53,84 km²**, coincide con una delimitación independiente y evita el río vecino de mayor aporte. No se presenta este ensayo como ejecución de los archivos del usuario. Se conserva el motor GRASS y sus direcciones; cambia el procedimiento de asociación del cierre.

La evidencia espacial se guarda con el proyecto y se valida al abrirlo, sin los rásteres de origen. El snapshot de referencia conserva su significado aunque después cambie manualmente el cierre de otra resolución.

## Ajuste manual y borrado de toda la sesión

El botón se llama **Ajuste Punto de Cierre**. Conserva el selector no modal, las flechas Norte, Sur, Este y Oeste, el paso en celdas y **Elegir en el mapa**. Permite repetir los ajustes y recalcular sin cerrar la cuenca. La selección expresa puede usar cualquier celda válida del MDT disponible, manteniendo el punto y caudal originales como referencia.

**Borrar Delineado** retira los trazados, marcadores y capas generadas de todas las consultas de la sesión: divisorias, cursos, MDT y relieve, direcciones y red, incluidas las variantes y los resultados de ventanas ya cerradas. La limpieza utiliza las propiedades de pertenencia al complemento y conserva las capas ajenas y los archivos de disco. Las ventanas permanecen abiertas y mantienen sus cálculos para informe y guardado. Las tareas pendientes no vuelven a dibujar lo retirado; un ajuste o cambio explícito de resolución permite representarlo de nuevo.

Cada nuevo clic rojo de consulta limpia los resultados anteriores y cierra sus ventanas antes de iniciar el nuevo punto. Sus tareas se cancelan y los callbacks tardíos no publican capas de la consulta anterior.

El resumen de coordenadas, MDT, caudal y cierre se sitúa en un recuadro blanco de **Resolución MDT**, debajo del mensaje de estado.

## MDT y relieve desactivados al crearse

Todas las capas nuevas de terreno, incluida la primera cuenca de 500 m, se añaden desmarcadas. El usuario puede activarlas en el panel de capas. Se conserva su elección al recalcular o reutilizar el grupo, y se respeta la visibilidad de las capas existentes en un proyecto QGIS guardado. No cambian las cotas, la representación disponible ni los cálculos del MDT.

## Análisis IA en la cabecera

El botón azul oscuro **Análisis IA** se sitúa en la esquina superior derecha, en una barra fija por encima del contenido desplazable. Incluye a la izquierda del texto un icono vectorial de conexiones neuronales. Se conservan su acción y las condiciones de disponibilidad durante los cálculos.

La columna **Interpretación** empieza vacía. Su cabecera incluye un segundo botón **Análisis IA**, situado a la derecha del título y con la misma altura del encabezado. Ambos botones acceden a la misma consulta gratuita. Al obtener una respuesta completa, las 19 interpretaciones del informe se incorporan a la tabla, vinculadas por la clave de cada parámetro. Se incluyen las observaciones de revisión que aparecen en el informe y se admite la recuperación desde la caché de la sesión. Un error, una cancelación o una respuesta incompleta no reemplazan el contenido de la tabla. Cuando cambian los resultados se vacía la columna y se descartan las respuestas tardías del análisis anterior.

## Tabla y suavizado más compactos

**Exportar shape** pasa a la misma fila que **Grado**, inmediatamente a su derecha. El resumen blanco de suavizado presenta sus cifras con coma decimal y adapta la altura al contenido. Las demás observaciones de la ventana conservan sus controles.

El parámetro del tramo central se muestra como **Longitud curso principal 10%-85% (L10-85)**. La columna Parámetro se reduce del 46 % a un máximo del 40 % del ancho de la tabla y el espacio liberado se asigna a Interpretación. En ventanas estrechas se reserva el ancho necesario para leer entera la cabecera Unidad. Los valores ausentes se presentan como **n.d.**, tanto en la tabla como en su informe, sin convertirlos en cero ni modificar los cálculos.

El resumen de **Umbral de Escorrentía (P₀)** utiliza **Mín**, **Máx** y **Desv. típ** y prescinde de la línea de cobertura ráster y área válida. Sus valores y datos de cobertura siguen guardándose y utilizándose en los cálculos y el informe.

El botón **Calcular Precipitación diaria máxima anual MEDIA (mm)** presenta el texto en una sola línea y adapta su ancho. Las seis filas de la tabla de precipitación reducen su altura sin recortar las cifras. Se abrevian sus encabezados de extremos y desviación, y la consulta utiliza **Mín**, **Máx** y **Desv. típ. espacial**.

Se retira del panel el párrafo sobre interpolación e inversa, frecuencia de tormenta, P₀ sin coeficientes y permiso de extrapolación, así como el resumen de cálculos completados situado bajo los gráficos. La casilla de extrapolación y los mensajes necesarios para calcular o revisar incidencias siguen disponibles.

## Generación de capas en una fila compacta

El texto introductorio pasa a ser **Genera el MDT elegido, el ráster de Dirección de Flujo y el shape de la Red de Drenaje con el esquema D8 (TauDEM/Esri)**. Se retira la frase adicional sobre CAUMAX/ESRI y Método Racional. El botón **Generar capas D8** reduce su tamaño y comparte fila con **Umbral inicio corriente**, su entrada numérica y las casillas **Automático** y **Mostrar red**. Se elimina el botón **Cancelar cálculo**; la limpieza al cerrar o cambiar de consulta conserva su cancelación interna.

Los productos se titulan **Modelo Digital Terreno**, **Direcciones de Drenaje** y **Red de drenaje**, centrados sobre sus botones de exportación. Esta reorganización no cambia el motor ni los formatos y códigos de las direcciones exportadas.

Los tres botones de **Informe** —Informe (PDF), Guardar proyecto y Abrir proyecto— ajustan su ancho al texto y reducen su altura al **90 %** de la anterior, manteniendo una fila alineada.

## Verificación y compatibilidad

Las regresiones incluyen selección manual repetida, guardado y restauración, limpieza global con cálculos pendientes, preservación de capas ajenas y visibilidad inicial del MDT. Se conservan las pruebas de lectura de sesiones con ajustes históricos. La verificación se realiza con QGIS 3.40.6 y GDAL 3.10.3 reales en Linux; no se atribuye una nueva ejecución en Windows ni una nueva comprobación del servicio externo de IA.

**730 pruebas: 714 correctas, 16 omitidas, sin fallos ni errores**, con el código sin cambios durante la ejecución y cierre limpio de QGIS. Las omisiones corresponden a 7 pruebas con CAUMAX externo, 6 con GRASS, 2 con TauDEM/MPI reales y 1 con datos climáticos externos. Las respuestas de IA se simulan para verificar la tabla, la caché, las cancelaciones y los cambios de consulta. Se comprueba la integridad de los 240 archivos del runtime TauDEM/MPI de Windows, sin modificarlo. La revisión visual a 520, 650 y 900 píxeles comprueba los controles compactos, la cabecera de IA y los botones de informe.

---

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
