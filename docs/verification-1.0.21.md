# HidroCAUMAX 1.0.21 — divisorias gestionadas y estilo Red > 50 km²

## Problemas reproducidos

- QGIS mostraba dos grupos «HidroCAUMAX · Divisoria 500x500 m», uno procedente de una consulta anterior. La política de última consulta solo se aplicaba a 25/5 m; 500 m usaba equivalencia exacta de geometrías, por lo que dos consultas 500 m distintas permanecían.
- El color «Red > 50 km²» seguía azul en algunos proyectos. Aunque el código registraba el estilo rosa, una marca de estilo antigua impedía volver a aplicarlo si QGIS restauraba posteriormente el renderizador original.

## Correcciones

1. Los resultados gestionados de 500, 25 y 5 m aplican la misma regla: conservar la consulta más reciente por resolución, con el contorno original y el suavizado opcional de esa misma consulta.
2. Se retiran del árbol las capas obsoletas, sus entradas duplicadas y grupos gestionados vacíos. GeoPackages antiguos: se eliminan solo bajo `<perfil QGIS>/HidroCAUMAX/boundaries` o un directorio explícitamente registrado por el controlador; se verifica patrón de nombre generado, ausencia de referencias activas y ausencia de enlaces simbólicos. Windows puede impedir la supresión de un fichero abierto; se deja un aviso en el registro de QGIS.
3. «Red > 50 km²»: rosa-violeta `#FF1493`, ancho de línea 1,30 **milímetros**. Se comprueban los atributos efectivos del símbolo, no solamente la propiedad de estilo. El complemento vuelve a aplicar el estilo al cargar nuevas capas y durante la sincronización, sin cambiar otras redes.
4. Se mantienen la independencia de **Mapa de caudales máximos** frente a las capas de Método racional, el formato ESRI-D8 y la configuración de los archivos originales CAUMAX.

## Validación reproducible

- `tests/test_native_layers_view.py`: capa añadida tras iniciar el complemento, restablecimiento desde renderizador azul, color y ancho real.
- `tests/test_boundary_layers.py`: retirada del resultado 500 m antiguo y su GeoPackage no referenciado, coexistencia de últimas divisorias 500/25/5 m, conservación del archivo si existe una capa ajena apuntando al mismo.
- Resto de la batería PyQGIS/QGIS/Qt/GDAL mediante `.github/workflows/test-improved-resolution.yml`.

**Validación en la instalación Windows:** cargar CAUMAX, abrir el proyecto con las dos divisorias 500 m y comprobar que queda una; verificar si Windows permite eliminar el GeoPackage antiguo. Confirmar que «Red > 50 km²» se ve `#FF1493` y ancho 1,30 mm después de volver a cargar las capas originales.
