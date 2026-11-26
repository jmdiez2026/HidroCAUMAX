# Limpieza de referencias y configuración original

26 de noviembre de 2026. Nombre visible HidroCAUMAX v.1.0; revisión interna 1.0.13.

## Cambio y alcance

Limpiar capas incluye las referencias identificadas por sus marcas de propiedad y elimina sus grupos vacíos. Los archivos de referencia (incluido P0) quedan protegidos. Se conservan las capas ajenas, incluso dentro del grupo o con la misma fuente. La barra no borra archivos; el botón rojo conserva la confirmación del borrado de productos generados. Cancelar esa confirmación no cancela cargas ni retira capas. Confirmar cancela los cargadores del mismo proyecto para impedir que vuelvan a publicar referencias.

La limpieza automática de una nueva consulta conserva las referencias. Se elimina el componente que intervenía en Configurar capas de cálculo: el diálogo nativo mantiene sus valores guardados y su edición manual. Valores por defecto restaura los valores originales, incluida la cuenca mínima de 10 km²; Aceptar los guarda. No se altera automáticamente la configuración ya guardada del usuario.

## Pruebas reproducibles

Python de QGIS 3.44.15, Qt 5, Windows; `src` y `tests` en sys.path:

```text
python -m unittest discover -s tests -p test_reference_cleanup.py -v
python -m unittest discover -s tests -p test_generated_cleanup.py -v
python -m unittest discover -s tests -p test_map_services.py -v
python -m unittest discover -s tests -p test_native_toolbar.py -v
python -m unittest discover -s tests -p test_native_calculation_layers.py -v
```

- 46 pruebas de limpieza, referencias, barra y configuración: correctas.
- 89 pruebas adicionales de delineados, vista, conservación de exportaciones, versiones, empaquetado y manual: 88 correctas y una omitida por creación de enlaces simbólicos no disponible.
- La conservación de exportaciones se aísla en las pruebas del menú/barra; sus operaciones de disco se comprueban por separado en test_improved_storage. Los archivos de pruebas de borrado son temporales.
- Se verifica cancelación parcial y tardía, aislamiento entre proyectos, grupos vacíos, protección frente a registros antiguos incorrectos y referencias cargadas después de preparar la confirmación.
- Se comprueban apertura sin sustitución, reactivación sin sobrescribir valores manuales y botón nativo de valores por defecto sin consultar parejas mejoradas ni sustituir P0.

## Comprobación de escritorio

Perfil independiente `cleanup113`: 32 referencias reales del catálogo, una capa ajena y un archivo MDT temporal creado para el ensayo. Cancelar conserva referencias, grupo y archivo temporal. Tras la limpieza quedan cero referencias y cero grupos de referencia, el botón Capas queda desmarcado, la capa ajena permanece, el archivo temporal se elimina y la huella SHA-256 del P0 permanece igual. Se restringe la búsqueda de archivos al ensayo; no se borran cachés del usuario.

Perfil independiente `config113`: se abre el LayersDialog original del CAUMAX instalado, con selecciones manuales de ensayo para MDT y direcciones y área 3. Con HidroCAUMAX activo, permanecen sin sustituir. Valores por defecto devuelve las diez rutas a `_defaultLayerPaths` y el área a 10. Se comprueba de nuevo después del intervalo que utilizaba la antigua intervención automática. Los datos nativos se leen; no se modifica el perfil personal.

Manual de diez páginas regenerado y páginas modificadas revisadas visualmente. La publicación verifica por separado el ZIP, el instalador en Windows, el commit de la etiqueta y el catálogo QGIS.
