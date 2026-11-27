# Verificación HidroCAUMAX 1.0.19 — recuperación de las consultas de cuenca

**Base:** versión funcional 1.0.15 (26-11-2026), preservando las mejoras aprobadas de 1.0.17/1.0.18.

## Incidencia

Con un MDT o D8 personalizado de 25×25 m o 5×5 m configurado en CAUMAX, el punto de Mapa de caudales máximos puede estar fuera del recorte. El diálogo de cuenca no alcanzaba la fase de descubrimiento de resoluciones, dejando «Resolución MDT» vacío y el estado «calculando».

## Contrato recuperado

1. Se mantienen coordenadas, periodo y caudal originales del resultado nativo.
2. Se comprueba que la pareja configurada MDT/D8 cubre exactamente el punto, tiene dimensiones, origen, CRS y celda compatibles, y no contiene NoData en ese punto.
3. Si falla y CAUMAX expone una pareja original verificable, se utiliza la pareja original **solo para la consulta de Mapa de caudales máximos**, sin escribir ni cambiar QSettings, CAUMAX ni sus archivos de cálculo personal.
4. Si no hay ninguna pareja válida, se muestra un error. Nunca se cambia el punto ni se calcula sobre una malla incorrecta.
5. El selector 500, 25 y 5 m se dibuja desde la apertura (deshabilitado hasta que se conozca la cuenca) y se habilita conforme a las resoluciones verificadas. El estado de error no permanece en «calculando».
6. Los proyectos anteriores, el resultado D8 ESRI/CAUMAX, los grupos Esri-D8, y el estilo rosa de Red > 50 km² no se alteran.

## Pruebas automáticas (PyQGIS + GDAL)

- `tests/test_caumax_bridge.py`: 25/5 fuera de recorte, 25/5 dentro, rutas actualizadas, preservación del caudal, prueba end-to-end de análisis con referencia oficial simulada de 500 m.
- `tests/test_drainage_dialog.py`: disponibilidad del selector incluso cuando falla el análisis inicial.
- Suites complementarias: `test_active_basin_layers`, `test_basin_hydrology_dialog`, `test_rational_baseline_select`, `test_rational_basin_dialog`, `test_rational_persistence`, `test_regression_outlet_boundary` y `test_regression_resolution_save`.

## Validación final de usuario en Windows

Reproducir en QGIS con el punto sobre la red de caudales máximos de la incidencia, confirmar que aparece el selector de resoluciones, el resultado de morfología, el cierre correcto y la posibilidad de cambiar a 25/5 m. Verificar después que Configurar capas de cálculo sigue mostrando la misma configuración personalizada. Las pruebas automatizadas no sustituyen esta comprobación de escritorio real.
