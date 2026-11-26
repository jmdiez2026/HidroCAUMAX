# Verificación de HidroCAUMAX 1.0.7

Fecha: 26 de noviembre de 2026. Alcance: barra de herramientas, logo elegido y visibilidad automática; el cálculo hidrológico no se modifica en esta entrega.

## Resultado local

QGIS 3.44.15-Solothurn, Python 3.12.15 y Qt 5 en Windows. Se ejecutaron 100 pruebas de `test_native_toolbar.py`, `test_map_services.py`, `test_update_ui.py`, `test_release_packaging.py` y `test_future_qgis_updates.py`: 99 correctas, una omitida por no permitir este entorno crear enlaces simbólicos; ningún fallo.

Las cinco pruebas de la barra usan widgets Qt reales y verifican:

- Los cuatro botones comparten las acciones del menú y tienen iconos válidos.
- Clic y estado marcado sincronizados; la barra permanece disponible al desactivar las herramientas con su primer botón.
- El botón de referencias comparte el estado ocupado.
- La barra está visible al cargar y después de restaurar una disposición que la ocultaba.
- Descarga y recarga sin barras duplicadas ni conexiones de arranque residuales.

Para repetir las pruebas en una consola configurada con Python de QGIS, añadir `src` a `PYTHONPATH` y ejecutar `python -m unittest discover -s tests -p test_native_toolbar.py -v`. Los otros cuatro módulos se ejecutan sustituyendo el patrón.

## Comprobación en QGIS de escritorio

Se extrajo el ZIP 1.0.7 construido por `scripts/build_release.py` en un perfil de prueba independiente y se habilitó `caumax_consulta` en ese perfil. El script observador no creó la barra ni llamó a `show()` sobre ella.

1. Al abrir QGIS se encontró una sola barra HidroCAUMAX visible, con los cuatro botones y el logo adjunto por el usuario.
2. Se comprobó mediante clic el interruptor de herramientas y la apertura del selector de proyectos de cuenca; se canceló ese selector sin abrir archivos.
3. Se ocultó la barra desde el menú de barras de QGIS, se verificó `isVisible() == False` y se cerró esa sesión.
4. Al abrir de nuevo el mismo perfil, la barra apareció automáticamente y siguió habiendo una sola instancia.
5. Se cerró la sesión de prueba; el proyecto de QGIS del usuario no se modificó.

La imagen del paquete conserva exactamente los bytes del adjunto elegido. SHA-256: `e84687a382ba9988e699a5219668f2233b380449f48648ab006abd0be321b6f4`.

El arranque usa la señal oficial [QgisInterface.initializationCompleted](https://api.qgis.org/api/classQgisInterface.html) para mostrar la barra después de que QGIS termine su inicialización, además de mostrarla al cargar el complemento.

## Límites y publicación

Estas pruebas se refieren a la barra y a las regresiones seleccionadas; no se ha vuelto a ejecutar toda la batería histórica ni el cálculo D8 de 5 m, que no cambia. La prueba de escritorio usa el ZIP extraído en un perfil aislado; no ejecuta el instalador EXE sobre el perfil personal del usuario.

La publicación automatizada debe completar construcción, prueba real del instalador en Windows y actualización del repositorio QGIS. Ese resultado se comprueba por separado antes de anunciar la entrega como publicada.
