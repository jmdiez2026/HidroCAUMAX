# HidroCAUMAX 1.0.22 — Método racional con capas configuradas

## Condiciones

El error original «El punto seleccionado no se encuentra dentro de las capas de cálculo» aparece antes de construir el calculador: CAUMAX consulta la cota sobre el MDT de `getLayerPath(MDT)` y obtiene un resultado nulo. Esto puede significar punto fuera de extensión, celda NoData o proveedor QGIS que retiene una versión antigua del TIFF. El control de D8 posterior no sirve por sí solo para resolverlo.

- El cálculo racional utiliza exactamente las rutas actuales de «Configurar capas de cálculo» y nunca toma el MDT/D8 que usa Mapa de caudales máximos.
- Con rasters de 25×25 y 5×5 m se comprueban ambos GeoTIFF mediante GDAL: proyección métrica EPSG:25830, geotransformación y origen iguales, cobertura del cierre real, NoData, valores ESRI-D8 válidos, y que no haya direcciones sobre NoData del MDT.
- El cierre D8 puede ser terminal NoData solo si existe una entrada aguas arriba local. No se mueve automáticamente el punto.
- Un ráster D8 nativo de TauDEM sin recodificar no sirve para CAUMAX. Solo son válidos los ocho códigos ESRI-D8.
- En caso de malla coherente y punto válido se permite que CAUMAX use la cota GDAL verificada cuando su proveedor QGIS está obsoleto; la adaptación es local, reversible y no cambia el código original.
- Si cambia cualquier capa de cálculo racional (MDT, direcciones, I1Id, P0, Td), se reconstruye BasinCalculator. Una consulta máxima posterior nunca lee esas capas racionales.
- No se crean datos extrapolados. Si la pareja no cubre el punto, es necesario exportar una pareja adecuada para esa zona o seleccionar un punto real cubierto.

## Pruebas reproducibles

- `tests/test_rational_grid_validation.py`: D8 de 25/5 m válido, NoData, códigos TauDEM, geotransformación/CRS, máscara DEM/D8, extensión, dirección terminal y ventanas de clima.
- `tests/test_rational_inputs.py`: reinicialización de BasinCalculator con nuevas fuentes, detención de errores antes del cálculo original, sustitución privada de lecturas MDT cuando el proveedor QGIS devuelve nulo.
- `tests/test_rational_custom_grid.py`: extensiones distintas con origen/resolución/CRS iguales y sin recorte silencioso.
- Suites adicionales de regresión QGIS/Qt5/GDAL y verificación de empaquetado.

## Comprobación pendiente en la instalación Windows concreta

El perfil QGIS de usuario conserva sus propios TIFF de `ResolucionMejorada`, que no forman parte del repositorio. Hay que seleccionar un punto de esa cobertura y comprobar que se completan las variables intermedias y Q(T). Si sigue apareciendo una advertencia, facilitar el mensaje del propio diálogo o los dos TIFF para verificar físicamente sus celdas y extensión; no es posible confirmar la cobertura de ficheros privados únicamente desde GitHub.
