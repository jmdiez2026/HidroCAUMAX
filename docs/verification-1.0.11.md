# Verificación de la interpretación automática

26 de noviembre de 2026. Nombre visible HidroCAUMAX v.1.0; revisión técnica 1.0.11.

## Alcance

Se retiran los dos accesos Análisis IA y la integración de solicitudes externas del diálogo de cuenca. Las 19 filas y el PDF comparten `parameter_schema.interpret_value`; las ayudas y definiciones añaden la procedencia y las decisiones de aplicación documentadas en [interpretation-criteria.md](interpretation-criteria.md). Los valores medidos, fórmulas hidrológicas, mallas y códigos ESRI D8 no se modifican. Re(L) del temario se deriva e identifica por separado de Re(Lb), que conserva su valor en la tabla.

El PDF docente se lee localmente y no se incluye en el repositorio, en el complemento ni en el manual distribuido. Se documentan sus intervalos numéricos y las discrepancias encontradas. Los huecos de Melton no se completan; las clases locales de TRI no se generalizan; HI mantiene la media de cotas.

## Pruebas reproducibles

En Python de QGIS 3.44.15, Windows, con `src` y `tests` en sys.path:

```text
python -m unittest discover -s tests -p test_classroom_interpretations.py -v
python -m unittest discover -s tests -p test_ai_table_integration.py -v
python -m unittest discover -s tests -p test_reports.py -v
python -m unittest discover -s tests -p test_morphology_header.py -v
```

`test_ai_table_integration.py` conserva su nombre por compatibilidad con el workflow, pero ahora prueba la interpretación automática sin solicitudes IA.

- Batería seleccionada: 129 pruebas, 128 correctas y una omitida por la disponibilidad de creación de enlaces simbólicos. Incluye esquema, criterios docentes, PDF, integración, encabezado, manual, empaquetado, identidad de versiones y guardado multirresolución.
- Se ejecutan además 16 pruebas Qt/GDAL de ciclo de vida, encabezado y guardado con acceso completo a las carpetas temporales: todas correctas.
- Tras corregir la presentación de la superficie de una celda fina, 13 pruebas de esquema/criterios correctas: 5×5 m se presenta como 25 m², sin redondearlo a cero hectáreas.
- Se comprueban fronteras de clase sin redondear, valores ausentes/no finitos, dominios inválidos, falta de longitud del cauce, diferencias de resolución y preservación de los valores originales.
- Se verifica que un fallo seguido de cambios de periodo o repintado no resucita comentarios antiguos. El recálculo de cierre, resolución y superficie invalida los textos hasta publicar resultados válidos.

No se declara correcta toda la batería histórica: cuatro subcasos de dos pruebas de ancho/ajuste de coordenadas de `test_compact_layout` fallan también en la revisión limpia db76948, con la misma geometría. Los nuevos controles de ausencia de botones e interpretación automática pasan.

## QGIS y PDF

En el perfil independiente `auto111`, se vuelve a analizar la cuenca usando los rásteres reales exportados anteriormente `MDT_5m.tif` y `D8_ESRI_5m.tif`, cierre EPSG:25830 X=346027,5 Y=4717697,5. Se abre un BasinDialog real y se comprueban 19 comentarios automáticos y ausencia de ambos botones IA. No se descarga otro MDT ni se ejecuta de nuevo TauDEM: esta prueba es un análisis morfológico de los rásteres conservados. El caudal está pendiente en este escenario y no se inventa un resultado Q.

Se revisan visualmente la tabla en QGIS, el informe generado con datos de ensayo y el manual actualizado de diez páginas. Las pruebas del PDF cotejan valores, unidades, comentarios y conservación de archivos ante errores. Las pruebas de escritorio no sustituyen la instalación del perfil personal.

La publicación comprueba por separado la etiqueta, el ZIP reproducible, el instalador ejecutado en Windows por GitHub Actions y el índice QGIS.
