# HidroCAUMAX 1.0.31 — continuidad de desarrollo

Fecha: 28 de noviembre de 2026.

La versión estable publicada es **1.0.30**. Esta revisión se prepara en
`mejora-icono-cargar-capas-caumax-v31`; no supone publicar una nueva versión
estable ni sustituir la instalación del usuario.

## Decisiones de interfaz conservadas

El logo de la barra ejecuta exclusivamente la acción nativa
**Complementos → CAUMAX → Cargar Capas CAUMAX**.

La consulta de mapas de caudales utiliza **SNZI-Análisis Cuenca** y Método
Racional utiliza **IC-5.2 Análisis Cuenca**, según los rótulos solicitados.

Los botones de Método Racional están organizados así:

| Fila | Izquierda | Centro | Derecha |
| --- | --- | --- | --- |
| Superior | Calcular Q vs. T | IC-5.2 Análisis Cuenca | Mejorar resolución MDT |
| Inferior | Guardar Cierre | Guardar Divisoria | Guardar IC-5.2 |

Los iconos se eligen de uno en uno, entre tres propuestas, a la izquierda del
texto. Están aprobados e instalados:

- **Calcular Q vs. T: modelo 2**, gráfico con puntos.
- **IC-5.2 Análisis Cuenca: modelo 3**, cuenca con lupa.

El siguiente es **Mejorar resolución MDT**. Sus tres propuestas están en
`design/mdt_icon_options/`: malla más fina (1), relieve con lupa (2) y modelo
del terreno con flecha (3). Ninguna está seleccionada ni conectada al botón.
Después quedan Guardar Cierre, Guardar Divisoria y Guardar IC-5.2.

## P₀ en el perfil de QGIS

El TIFF entregado por el usuario es idéntico al ya incluido:
`Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif`.

Al activar HidroCAUMAX después de instalarlo, se copia al perfil activo:

```text
HidroCAUMAX/ResolucionMejorada/Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif
```

En la instalación indicada por el usuario, la carpeta es:

```text
C:\Users\JM_GIS\AppData\Roaming\QGIS\QGIS3\profiles\default\HidroCAUMAX\ResolucionMejorada
```

La copia es independiente, no sobrescribe archivos existentes y queda
protegida frente a Limpiar capas. Se incluye su auxiliar cuando corresponde
al mismo TIFF. No se cambia la configuración de fuentes de CAUMAX ni se
restauran MDT antiguos. El instalador EXE mantiene su transacción anterior;
esta preparación se ejecuta en la primera activación del complemento en QGIS.

SHA-256 del TIFF comprobado:
`611b41dbedfd64a0d0c84c710e85c80251b4bc8db55959ee9830cd2fa36bb871`.

## Recuperación de consultas

La consulta guardada conserva las referencias de los recortes P₀ y Pd-T.
Al reabrirlos, se comprueba su relación con la divisoria y la fuente; se
incorporan como capas visibles sin duplicarlas. Una ruta no disponible
permite abrir los demás resultados guardados. Los TIFF exportados por el
usuario no se registran para borrado por el mero hecho de abrirlos.

Los recortes de P₀ con un único valor válido se representan con ese valor;
no se intenta obtener dieciséis cuantiles de una distribución constante.

Las áreas aportantes de consultas antiguas se recuperan exclusivamente
desde sus D8 y MDT, comprobando el curso y la geometría de la cuenca. La
recuperación se inicia después de restaurar la resolución activa y conserva
las muestras Qci guardadas. Los resultados incompletos no dejan bloqueados
los botones Guardar e Informe.

## Guardado e informes

Los cierres elegidos manualmente conservan su comprobación de correspondencia
y, cuando procede, la excepción expresa del usuario. La validación del JSON
comprueba la coherencia de las superficies y sus fracciones de solape.

El guardado multirresolución mantiene las capturas de gráficos y el perfil Qci
tanto en el estado principal como en la variante activa. Se conserva la
comprobación de igualdad entre ambos, sin aceptar estados contradictorios.

El informe PDF reconoce la firma PNG real de las capturas guardadas y puede
incluir sus gráficos. Se corrigió el literal que antes los rechazaba.

## Criterio para la entrega

La comprobación disponible se realiza con PyQGIS/GDAL reales en Linux.
La validación de esta revisión en QGIS sobre Windows y con las capas del
proyecto del usuario sigue siendo una comprobación distinta. No se atribuye
a esta revisión una prueba realizada en versiones anteriores.

El detalle de comandos, resultados y omisiones se conserva en
`verification-1.0.31.md`.
