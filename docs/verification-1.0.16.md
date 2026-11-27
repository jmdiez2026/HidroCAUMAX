# Cuencas racionales con capas propias y una divisoria activa de 25 m

27 de noviembre de 2026. Nombre visible HidroCAUMAX v.1.0; revisión técnica 1.0.16.

## Error reproducido y corrección

El botón Cuenca hidrográfica rechazaba el cálculo racional con el mensaje «La máscara racional no corresponde a las capas cargadas». Los archivos del caso real tienen celdas de 25 m y la misma transformación `(342575, 25, 0, 4729950, 0, -25)`, pero el MDT tiene 580 filas por 280 columnas y las direcciones 560 por 260. El código exigía igualdad de dimensiones aun cuando toda la cuenca quedaba cubierta por ambos archivos.

La captura valida ahora cada ráster con sus propias dimensiones y exige que todas las celdas de la máscara nativa queden dentro de las direcciones. Se sigue verificando cada valor de ambos archivos, incluso fuera de la cuenca, además del origen, resolución, CRS, NoData y geometría nativa. La huella completa permite detectar modificaciones posteriores, incluidas dimensiones nuevas.

El análisis de la cuenca racional, su suavizado y la preparación independiente de capas admiten extensiones diferentes con una malla común. Antes de leer cotas o direcciones se exige cobertura de toda la ventana de cuenca. Se conserva el halo real del MDT para pendiente y TRI. No se remuestrea, no se recorta la divisoria a la intersección, no se ajusta el cierre y no se modifican los archivos ni la configuración manual. El flujo de consulta de rásteres oficiales conserva la exigencia previa de dimensiones idénticas.

## Divisoria única

La última consulta de 25 × 25 m sustituye en la lista de QGIS a las anteriores, aunque cambien el punto, cierre o polígono. Repetir la consulta reutiliza el identificador de capa. La variante suavizada opcional pertenece a la misma consulta. La activación del plugin y la lectura de proyectos retiran resultados antiguos gestionados y entradas repetidas de una misma capa; conservan archivos, capas ajenas y otras resoluciones. No se sustituyen divisorias con ediciones pendientes. La publicación de una consulta nueva se retrasa hasta que se han preparado sus geometrías.

## Pruebas reproducibles

Entorno: QGIS 3.44.15, Qt 5 y GDAL de esa distribución, Windows, perfil de pruebas. Añadir src y tests a PYTHONPATH. Para la prueba nativa de 500 m, HIDROCAUMAX_CAUMAX_SOURCE apunta a la carpeta del CAUMAX original, leída sin modificarla.

```text
python -m unittest discover -s tests -p test_rational_custom_grid.py -v
python -m unittest discover -s tests -p test_rational_basin.py -v
python -m unittest discover -s tests -p test_rational_basin_dialog.py -v
python -m unittest discover -s tests -p test_rational_persistence.py -v
python -m unittest discover -s tests -p test_boundary_layers.py -v
python -m unittest discover -s tests -p test_active_basin_layers.py -v
```

Las cuatro primeras suites suman 40 pruebas correctas, incluida la reproducción con datos originales de 500 m. Los casos nuevos cubren 5, 25, 100 y 500 m, MDT mayor o menor, distintas extensiones por eje, halo real, cobertura insuficiente, CRS u origen distintos, cambios del archivo fuera de la cuenca, fecha de modificación sin cambios de datos, suavizado y retorno al contorno original. Las pruebas Qt aíslan su almacenamiento y toleran únicamente que el pool OGR retenga archivos temporales de ensayo al finalizar un contexto en Windows.

También se ejecutaron 117 pruebas de terreno, enlace con el diálogo nativo, controlador de capas, empaquetado y manual: 116 correctas y una omitida porque el entorno no permite crear enlaces simbólicos. Las pruebas de divisorias y resultados activos pasaron después de aislar el directorio temporal de esta última. Otras 103 comprobaciones de cierre, panel, controlador, actualización y paquete finalizaron con una omisión de enlaces simbólicos; estas suites se solapan con las anteriores.

En una ejecución ampliada, la comprobación histórica del tamaño en píxeles de Exportar shape falla con la fuente/estilo de este entorno. Se reprodujo también cargando basin_dialog.py anterior a esta revisión. No se modificó el diseño del botón para resolver esa prueba ajena a estos cambios; no se presenta la batería completa del repositorio como aprobada.

## QGIS de escritorio

En el perfil aislado boundary116, tres divisorias gestionadas antiguas de 25 m se reducen a una al activar el plugin. Un botón de ensayo cambia cierre y polígono: queda una divisoria, desaparece de QGIS el identificador anterior y repetir la consulta desde otra instancia reutiliza el nuevo. Se comprueban una capa ajena conservada y los GeoPackages anteriores con las mismas huellas SHA-256.

En el perfil aislado rational116ui se abre el diálogo original Método racional con los archivos reales MDT_25m.tif y Direccion_D8_caumax_25m.tif y las fuentes auxiliares elegidas por el usuario. En X=346037,3; Y=4717743,2; EPSG:25830, el cálculo original da área 14.402.500 m², Tc=5,440789472112805 h y Q=10,29 m³/s para T=100. Se confirma el aviso original de capas personalizadas y se pulsa Cuenca hidrográfica en la interfaz real. El diálogo abre y muestra 23.044 celdas de 25 × 25 m, área 14,402 km² en su presentación de tres decimales, morfología e hipsometría. La validación posterior confirma máscara y geometría idénticas, área exacta, punto y caudal conservados. Las huellas de los dos TIFF permanecen intactas. El proyecto personal no se utiliza para esta prueba.

Manual de diez páginas regenerado y página 4 revisada visualmente; la página 7 documenta la divisoria única. Publicación y actualización instalada se verifican por separado del ensayo local.
