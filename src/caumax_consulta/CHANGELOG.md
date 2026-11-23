# HidroCAUMAX 1.2.5

## Sección de cierre

La selección nueva respeta la sección del punto original: admite un centro de celda en ella o aguas arriba, con conexión D8 comprobada. Si el centro de la celda del clic queda aguas abajo, se busca un antecesor válido y se vuelve a delimitar la cuenca desde él. Esta corrección afecta también a la malla original de 500 m y al curso principal utilizado en longitudes y tiempos.

En 25 y 5 m se retira el requisito rígido de alcanzar el 90 % del área de la malla original. Esa condición podía rechazar una cuenca detallada válida o desplazar el cierre para añadir una superficie que el usuario no había solicitado. El área de referencia se conserva en el diagnóstico. Sin candidato conectado y fiable, se mantiene el resultado anterior y se explica el problema.

La orientación de la sección se obtiene de la dirección D8 local; no se dispone de una transecta dibujada. La selección trabaja con centros y la divisoria con celdas completas, por lo que la exactitud geométrica sigue limitada por la malla. Las sesiones antiguas mantienen su cierre guardado; una consulta nueva aplica el nuevo criterio.

## Interfaz y gráficos

- Resoluciones compactas sin espacios, con disposición adaptable: `500mx500m`, `25mx25m`, `5mx5m`, `2mx2m` y `0,5mx0,5m`.
- MDT activo a la izquierda del punto original en la misma línea; caudal en la siguiente.
- Botón azul oscuro **Análisis IA** alineado a la derecha de **Morfología de la Cuenca**.
- Hipsometría con título único **Índice hipsométrico (HI) = …**, casilla adimensional dentro del blanco, altitud entera y ejes adimensionales cada 0,10. Se eliminan la etiqueta exterior y la nota inferior.
- Títulos, ejes y marcas de los gráficos de cuantiles un 10 % mayores; se retiran las leyendas inferiores. Consulta Q con casilla de extrapolación junto a las entradas.
- Secciones independientes **Umbral de Escorrentía (P₀)** y **Precipitación máxima diaria de la cuenca**.
- En las consultas de precipitación, negrita únicamente para la media obtenida y el periodo inverso. Línea **Desviación típica espacial**, sin el paréntesis anterior.

## Análisis IA

Conexión gratuita y anónima a Pollinations Classic. Interpreta los 19 parámetros y resultados hidrológicos disponibles en el marco de la Norma 5.2-IC. Explica implicaciones, datos ausentes y comprobaciones de proyecto; no sustituye la revisión técnica ni recalcula caudales.

La solicitud se ejecuta en segundo plano, admite cancelación y gestiona tiempos de espera y límites del servicio. Solo se envían medidas y definiciones: no se transmiten coordenadas, archivos, geometrías, rutas ni credenciales. La disponibilidad depende del proveedor. No hay un proveedor de pago ni un informe prefijado oculto como alternativa.

## Correcciones integradas

- Rásteres de caudal: máscara y NoData se verifican antes de aplicar escala y desplazamiento GDAL; se valida el valor físico resultante.
- Divisoria suavizada: un cierre anclado exactamente en su borde se acepta como parte de la superficie activa. Se conservan los controles más estrictos sobre la divisoria original de celdas.
- Proyecto multirresolución: las variantes no visitadas completan su cálculo de superficie, morfología, P₀, Pd y cuantiles antes de guardar. Cancelar o fallar no publica un archivo parcialmente preparado.
- La prueba de restauración espera al recálculo asíncrono antes de evaluar sus resultados, en vez de leer un estado transitorio.

## Verificación y límites

La ejecución consolidada de esta revisión comprende **536 pruebas: 520 correctas, 16 omitidas, sin fallos ni errores**, con cierre limpio de QGIS y código sin cambios durante la ejecución. Las omisiones corresponden a 7 pruebas que necesitan CAUMAX externo, 6 GRASS, 2 TauDEM/MPI reales y 1 reproducción con datos climáticos externos.

Pruebas reproducibles en `tests/`, con datos sintéticos para selección del cierre, geometría, escala GDAL, sesiones, interfaz y red IA. Verificación en Linux, QGIS 3.40.6, GDAL 3.10.3; comprobación separada de la conexión anónima. El paquete mantiene TauDEM/MPI de Windows de la versión anterior y verifica su integridad, pero esta revisión no se ha ejecutado en Windows.

Las capas originales del proyecto mostrado en las capturas no están incluidas. Las regresiones comprueban los casos geométricos y el cálculo; no equivalen a reproducir esa cuenca con sus rásteres reales.
