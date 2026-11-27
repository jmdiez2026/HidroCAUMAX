# Verificación HidroCAUMAX 1.0.20 — independencia del cálculo de máximos y racional

## Regla obligatoria

- **Consulta de capas de caudales máximos:** mantiene los seis rásteres originales de caudal Q(T) y usa exclusivamente el MDT y D8 originales de CAUMAX para `Cuenca hidrográfica`; también usa fuentes originales de P₀ y lluvia. Obtiene los archivos originales de `caumax.layers._defaultLayerPaths`.
- **Método racional:** conserva los rásteres elegidos en su configuración (25 m, 5 m u otros). Sus configuraciones, máscaras, áreas, resultados y rutas no se cambian por consultas Q(T).
- El aislamiento se aplica incluso cuando un ráster personalizado cubre el punto seleccionado en el mapa de máximos. No existe fallback hacia capas racionales.
- Si falta el MDT/D8 original, se registra una advertencia clara. No se fuerza un valor de caudal ni se cambia automáticamente la configuración.
- **No se modifica CAUMAX original.** La corrección reside solo en el complemento auxiliar HidroCAUMAX.

## Comprobaciones

- `tests/test_caumax_bridge.py`: punto Q(T), rutas originales, personalizadas 25/5 m fuera y dentro de cobertura, alternancia de configuraciones, consulta estable cuando se editan rutas racionales, falta de originales.
- `tests/test_basin_hydrology.py`: fuentes P₀/Pd oficiales para consulta máxima frente a fuentes personalizadas para Método racional; independencia de configuraciones.
- Se mantiene la batería previa de QGIS, Qt5, GDAL, resultados de cuenca, variantes, proyectos guardados, exportaciones y empaquetado.

## Verificación posterior en QGIS Windows

Consultar Q(T) sobre «Red > 50 km²» con las capas racionales personalizadas de 25 m y 5 m configuradas y confirmar que se devuelve exactamente el mismo caudal y cuenca que con la configuración racional original. Después abrir **Método racional** y confirmar que las rutas personalizadas siguen seleccionadas. Una prueba automatizada en Linux con PyQGIS no sustituye esta comprobación en el perfil real del usuario.
