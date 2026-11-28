# Verificación de desarrollo HidroCAUMAX 1.0.31

Fecha: 28/11/2026. Rama: `mejora-icono-cargar-capas-caumax-v31`.
Base recuperada: `166190b134cb02b171f363351841e36152184150`.

## Resultado

**308 pruebas distintas: 306 correctas, 2 omitidas, 0 fallos pendientes.**
La verificación utiliza QGIS 3.40.6, Qt 5 y GDAL 3.10.3 reales en Linux.
No es una validación de Windows ni de las fuentes del proyecto del usuario.

| Módulo | Ejecutadas | Correctas | Omitidas |
| --- | ---: | ---: | ---: |
| `test_po_clip` | 4 | 4 | 0 |
| `test_pd_clip` | 5 | 5 | 0 |
| `test_course_flow_profile` | 9 | 9 | 0 |
| `test_hypsometric_selection` | 5 | 5 | 0 |
| `test_native_toolbar` | 12 | 12 | 0 |
| `test_native_caumax_toolbar_shortcut` | 4 | 4 | 0 |
| `test_caumax_bridge` | 22 | 21 | 1 |
| `test_rational_layers_bridge` | 20 | 19 | 1 |
| `test_reports` | 35 | 35 | 0 |
| `test_report_charts` | 2 | 2 | 0 |
| `test_refinement_session` | 15 | 15 | 0 |
| `test_regression_resolution_save` | 7 | 7 | 0 |
| `test_session_store` | 41 | 41 | 0 |
| `test_basin_hydrology_dialog` | 15 | 15 | 0 |
| `test_legacy_course_areas` | 11 | 11 | 0 |
| `test_manual_outlet` | 9 | 9 | 0 |
| `test_improved_storage` | 18 | 18 | 0 |
| `test_generated_cleanup` | 7 | 7 | 0 |
| `test_release_packaging` | 67 | 67 | 0 |
| **Total** | **308** | **306** | **2** |

No se suman los reintentos ni las tandas iniciales de diagnóstico. En informes,
la primera ejecución obtuvo 34 correctas y un error de firma PNG; después de
corregirlo se repitió la única prueba afectada, que pasó. Las pruebas finales
de persistencia se ejecutaron con las correcciones completas.

### Omisiones

- `test_caumax_bridge.BridgeTests.test_an_actual_native_dialog_class_is_recognized_without_installing_anything`: CAUMAX original no está instalado en este host de pruebas.
- `test_rational_layers_bridge.RationalLayersBridgeTests.test_independent_d8_window_fits_screens_without_resizing_native_ui`: no están disponibles las fuentes originales de CAUMAX.

### Comandos reproducibles

Cada módulo de la tabla se ejecuta en un proceso independiente:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src:tests /usr/bin/python3 -m unittest NOMBRE_DEL_MODULO -v
```

`test_reports` requiere `pypdf`. En este entorno se añadió únicamente ese
paquete Python puro, ya disponible, a una ruta temporal de dependencias.
La ejecución automática de preview del repositorio instala `python3-pypdf`.

Las pruebas de las rutas de Guardar Consulta y la transacción JSON aíslan la
exportación opcional de SHP: las salidas cartográficas no forman parte de esos
fixtures y antes abrían un mensaje modal que bloqueaba el proceso sin interfaz.
El guardado JSON real y sus comprobaciones de atomicidad siguen ejecutándose.

## Cobertura de las correcciones

- Recuperación D8 tras restaurar la resolución activa, conservación de muestras Qci, comprobación de toda la divisoria y cancelación al recalcular.
- Selección de una variante histórica de 5 m utilizando sus propios MDT/D8, con áreas diferentes de la variante de 25 m.
- Restauración offline y rechazo de metadatos incoherentes en cierres manuales, incluidas excepciones explícitas.
- Coherencia de capturas y perfiles entre el estado superior y la variante activa al guardar.
- Reapertura real de P₀ y Pd-T, visibilidad, ausencia de duplicados y conservación de exportaciones del usuario.
- P₀ homogéneo y firma PNG válida al incluir gráficos en el PDF.
- Copia idempotente e independiente del P₀ al perfil, protección frente a limpieza e índices antiguos.
- Orden de botones e iconos aprobados, acción exclusiva Cargar Capas CAUMAX y empaquetado.

## TIFF facilitado por el usuario

El fichero facilitado coincide byte por byte con el ya incluido. La copia al
perfil temporal mantiene los 11.794.752 bytes y la malla de 10.350 × 8.708.
Se comprobó la misma huella del original y del destino, el auxiliar, la
independencia física y que una segunda activación no modifica ni duplica:

```text
611b41dbedfd64a0d0c84c710e85c80251b4bc8db55959ee9830cd2fa36bb871
```

La copia se hace en la primera activación del complemento después de
instalarlo; no se ha modificado ni generado un instalador EXE.

## ZIP de desarrollo

Construido con `python scripts/build_release.py --output DESTINO --tag v1.0.31`.
El constructor analiza la sintaxis de todas las fuentes y comprueba lista de
archivos, contenido exacto y CRC del ZIP. Se incluye un único complemento
`caumax_consulta`, con los dos iconos aprobados y ambos archivos P₀.

- Tamaño: **35,589,232 bytes**.
- SHA-256: `79c1ee98aba0028d95216c7aa50b7d3d45727ce1a7a71d3524c8e4d6529a3fc3`.

La rama y el ZIP siguen siendo de desarrollo. La publicación estable 1.0.30
se conserva. Quedan la revisión con QGIS sobre Windows y la elección de los
cuatro iconos restantes, empezando por Mejorar resolución MDT.
