# Revisión de peticiones para HidroCAUMAX 1.0.10

Se aplica la última indicación del usuario cuando modifica una anterior. El nombre visible es **HidroCAUMAX v.1.0**; la versión técnica y la etiqueta de publicación son **1.0.10** y **v1.0.10**.

| Petición vigente | Implementación y comprobación |
|---|---|
| Generación D8 a 5 m; investigar cobertura, ventana, memoria y cierre | Se mantiene la corrección de `selection_policy` verificada con motores reales en 1.0.6. La cobertura y la memoria no eran la causa del caso reproducido. Ver el informe de 1.0.6; no se afirma una descarga nueva en esta entrega. |
| Compatibilidad CAUMAX/ESRI D8 | Exportación de ocho códigos ESRI, alineación y restauración de proyectos antiguos probadas. No cambian las fórmulas ni el motor. |
| Diálogo D8 independiente y adaptable | `RationalLayersDialog` conserva el diálogo no modal y el ajuste al contenido. |
| Texto introductorio exacto | «Genera las tres capas necesarias para ejecutar CAUMAX con resolución mayor de la original 500x500m (esquema D8-TauDEM/Esri): (1) ráster MDT, (2) ráster Direcciones de flujo, y (3) shape Red de Drenaje.» |
| Resolución MDT compacta y mayor separación | Selector compacto y espaciado de controles conservados y cubiertos por las pruebas Qt. |
| Cancelar durante D8 y permitir reintentar | Solo visible durante la generación; cancela tareas y procesos y rechaza resultados tardíos. Ejecución, cancelación y reintento reales documentados en 1.0.6. |
| Relieve más expresivo | Rampa de elevación con sombreado, sin alterar las cotas del MDT. |
| Coma decimal en los diálogos | Presentación con coma, incluido el área y los valores recomendados de P0; entradas internas compatibles con CAUMAX. |
| Limpiar capas del diálogo y del recuadro GEA | Botón rojo claro con icono E3, confirmación y detalle de archivos generados. En D8, a la derecha de Ajustar punto de cierre; en GEA, al extremo derecho. |
| Limpiar capas de la barra | Inmediatamente antes de Actualizar. Retira del mapa todos los resultados identificados; conserva los archivos. |
| P0 incluido e instalado automáticamente | GeoTIFF original en ResolucionMejorada, con procedencia y SHA-256 conservados. Última entrada dentro del grupo de referencias. |
| Carpeta de exportación predeterminada | ResolucionMejorada dentro del plugin. MDT, direcciones y red se proponen allí; se preservan copias fuera del directorio reemplazable del plugin. |
| Configurar capas de cálculo | Última pareja registrada compatible MDT/D8, P0 incluido, torrencialidad original y cuenca mínima 2 km². No cambia las precipitaciones; aplica al aceptar. |
| Grupo HidroCAUMAX mejoradas | Carga los conjuntos de datos GIS de la carpeta al abrir la generación. Se coloca sobre CAUMAX_CAPAS; solo Red_drenaje_25m.shp marcada; sin duplicados y leyendas plegadas. |
| Referencias apagadas y mensaje de carga temporal | Nuevas capas y subgrupos apagados; aviso retirado al terminar, cancelar o agotar la espera. WMS/XYZ usan extensión y escala actuales de QGIS. |
| Extensión zonas inundables | Escenarios SNCZI T10, T50, T100 y T500, bajo AEMET, inicialmente apagados. |
| Posición del grupo de referencias | Grupo independiente inmediatamente encima de CAUMAX_CAPAS; grupo abierto y entradas plegadas. |
| AEMET, Pfafstetter y Corine sin contenedores intermedios | Capas directas del grupo general; migración de los antiguos grupos vacíos sin duplicar sus capas. |
| Google Satellite debajo de Terrain | Última indicación: fuera del grupo de referencias, inmediatamente bajo Google Terrain. Inicialmente apagada; se conserva la visibilidad elegida al repetir. |
| Agroclima y peligrosidad SNCZI | Al abrir cada grupo se ve la lista completa con las leyendas plegadas. Las casillas no cambian; cada leyenda se puede desplegar después. |
| P0 con la rampa de la captura | Rojo–amarillo–verde RdYlGn continuo, 16 cuantiles, interpolación lineal, rango 0–152 y etiquetas enteras. |
| Mapa inicial | Cargar CAUMAX apaga Mapas raster del IGN y activa Google Terrain; conserva el encuadre inicial península/Baleares y el CRS. |
| Red > 50 km² rosa | Se cambia solo la capa vectorial con ese nombre a #ff1493, conservando geometría y grosor. |
| Dos botones Calcular independientes | El original obtiene un único caudal sin abrir la curva. El de GEA utiliza el valor medio regional recomendado de P0 y abre la tabla/gráfico. |
| Tabla T/Q y consultas | T2,5,10,25,100,500 y T del usuario; dos columnas, centrado y Q con dos decimales. Consultas automáticas T→Q y Q→T, selección vinculada y controles con dos decimales. |
| Presentación del gráfico | Tamaño reducido respecto a la versión inicial, alineado con la tabla, ejes con marcas enteras y título horizontal compacto. Etiqueta, Cuadrícula y Escala logarítmica T alineadas a la izquierda. |
| Extrapolación opcional | Casilla Permitir extrapolación en T→Q, desactivada inicialmente. Se mantienen los límites de Q→T. Sin fila de color/grosor ni textos inferiores retirados. |
| Recuadro GEA y altura del Método racional | Un solo logo GEA a la izquierda; Calcular, Guardar cuenca…, Cuenca hidrográfica y Generar capas resolución mejorada azules, más Limpiar capas rojo al extremo derecho. Altura ajustada al contenido. |
| Barra e iconos | Logo elegido, Capas C1 inmediatamente a la derecha, Abrir cuenca, Limpiar capas E3, Actualizar y Ayuda. Icono y texto en cada botón; activación automática y sin duplicados al recargar. |
| Actualización directa | Descarga e inventario verificados, respaldo y recarga con comprobación de versión. Bloquea instalación durante trabajo pendiente y restaura ante fallo. Probada con paquetes locales de prueba y QGIS real. |
| Ayuda y manual | Libro abierto azul/turquesa con interrogación, elegido por el usuario. Diálogo con diez páginas del PDF incluido, disponible sin conexión, y apertura en el lector PDF. |
| Publicación segura | ZIP reproducible; control de versión/etiqueta, prueba Windows del instalador y comprobación independiente de artefactos públicos. |

Los detalles y límites de la verificación están en [verification-1.0.10.md](verification-1.0.10.md). El caso hidrológico de 5 m está documentado en [verification-1.0.6.md](../src/caumax_consulta/docs/verification-1.0.6.md).
