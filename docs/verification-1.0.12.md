# Verificación de la alternancia de Capas

26 de noviembre de 2026. Nombre visible HidroCAUMAX v.1.0; revisión técnica 1.0.12.

El mismo botón y comando de menú carga las referencias cuando no existen y las retira cuando están presentes. Durante una carga permite cancelarla y retirar los resultados parciales. El estado marcado depende de las capas y tareas reales, incluido un proyecto restaurado. Las marcas de propiedad permiten retirar referencias movidas, renombradas y duplicadas, también Google Satellite fuera del grupo. No se borran archivos. Se conservan capas ajenas y grupos con contenido ajeno.

## Pruebas reproducibles

En un entorno Python de QGIS con `src` y `tests` en sys.path:

```text
python -m unittest discover -s tests -p test_map_services.py -v
python -m unittest discover -s tests -p test_native_toolbar.py -v
python -m unittest discover -s tests -p test_reference_viewport.py -v
python -m unittest discover -s tests -p test_native_layers_view.py -v
```

- 29 pruebas de servicios y barra: correctas. Cubren carga/retirada/recarga, cancelación tras publicación parcial, llegada tardía de resultados ya preparados, reintento mientras termina un proveedor cancelado, propiedad frente a nombres/archivos iguales, proyecto restaurado y estado de la acción.
- 81 pruebas adicionales de vista, capas nativas, AEMET, versiones, empaquetado y manual: 80 correctas y una omitida por la disponibilidad de creación de enlaces simbólicos.
- Manual regenerado de diez páginas; páginas modificadas y conjunto revisados visualmente.

## QGIS de escritorio

QGIS 3.44.15, perfil independiente `toggle112`, con servicios reales del catálogo y P0 local. Se pulsa el botón de la barra:

1. Primera pulsación: 32 referencias cargadas; botón marcado.
2. Segunda pulsación: cero referencias y cero grupos propios vacíos; botón desmarcado.
3. Tercera pulsación: se vuelven a cargar las 32 referencias; botón marcado.

En las tres etapas permanecen las capas de control ajenas al catálogo, incluida una capa de resultado D8. La huella SHA-256 del TIFF P0 no cambia. Las pruebas se realizan en el perfil de pruebas, sin editar el proyecto personal.

La publicación verifica por separado el ZIP reproducible, el instalador ejecutado por GitHub Actions en Windows, la etiqueta y el índice de actualizaciones de QGIS.
