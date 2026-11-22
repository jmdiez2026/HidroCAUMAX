# Runtime oficial de TauDEM para Windows x64

TauDEM (*Terrain Analysis Using Digital Elevation Models*) es el proyecto de
David G. Tarboton y colaboradores, Utah State University. Sus fuentes oficiales
son [el repositorio TauDEM](https://github.com/dtarb/TauDEM) y
[la web del proyecto](https://hydrology.usu.edu/taudem/).
El runtime incluido corresponde a TauDEM **5.5.0**. HidroCAUMAX conserva los
binarios originales; el empaquetador no los recompila ni los ejecuta.

La carpeta de preparación debe contener exclusivamente:

- `bin/`: `PitRemove.exe`, `D8FlowDir.exe`, `AreaD8.exe`, `Threshold.exe`,
  `StreamNet.exe`, `mpiexec.exe`, `smpd.exe`, `msmpi.dll` y sus DLL dependientes.
  Se conservan los nombres oficiales, que Windows interpreta sin distinguir
  mayúsculas y minúsculas.
- `share/proj/proj.db`, junto con los datos auxiliares necesarios de PROJ/GDAL.
- `licenses/`: las licencias, avisos de copyright y atribuciones originales de
  TauDEM y de **cada dependencia distribuida**, incluidos GDAL, PROJ y Microsoft
  MPI. Las DLL de Visual C++ se obtienen de QGIS/Windows al preparar la copia
  local del usuario y no forman parte del ZIP distribuido.
  Añadir aquí la procedencia y las versiones de los paquetes oficiales, mediante
  URLs públicas y hashes, sin rutas personales de la máquina de preparación.

Las licencias originales determinan los permisos de cada componente. El
empaquetador comprueba que hay archivos de licencia no vacíos; no deduce ni
sustituye sus condiciones, ni certifica por sí mismo que el conjunto de avisos
esté completo. La revisión de procedencia y permisos precede al empaquetado.

Desde la raíz del repositorio, con Python 3.10 o posterior y sin QGIS:

```powershell
python scripts/prepare_taudem_bundle.py --stage RUTA_STAGING --output src/caumax_consulta/vendor/taudem-5.5.0-win64.zip
python scripts/prepare_taudem_bundle.py --verify src/caumax_consulta/vendor/taudem-5.5.0-win64.zip
python -m unittest discover -s tests -p test_taudem_bundle.py -v
```

El ZIP contiene `manifest.json` en su raíz. Su esquema es:

```json
{
  "schema_version": 1,
  "runtime_id": "taudem-5.5.0-win64",
  "platform": "windows",
  "architecture": "x86_64",
  "taudem_version": "5.5.0",
  "files": {"bin/pitremove.exe": "SHA-256 de los bytes del archivo"}
}
```

`files` enumera todos los archivos, excepto el propio manifiesto, mediante rutas
relativas POSIX. La validación rechaza archivos faltantes, hashes incorrectos,
entradas no declaradas, enlaces simbólicos, junctions, rutas inseguras,
colisiones de mayúsculas, ejecutables requeridos que no sean PE x64 y una base
de PROJ que no tenga la cabecera SQLite. También impide configuraciones ocultas,
archivos de credenciales y patrones reconocibles de claves o rutas personales
en los documentos y archivos de configuración. Esto no es un análisis de
secretos o de seguridad de los binarios oficiales: su revisión de procedencia
y sus comprobaciones funcionales se realizan por separado.

La preparación captura los bytes de entrada y escribe el destino de forma
atómica. El orden de entradas, fecha ZIP (`1980-01-01`), permisos, manifiesto y
nivel de compresión DEFLATE (9) son fijos. Repetirla con los mismos archivos y
la misma implementación de zlib produce el mismo ZIP, con independencia de
las fechas locales. Diferentes versiones de zlib pueden producir flujos
comprimidos distintos: **se versiona el ZIP terminado como un único blob** y
CI verifica ese archivo sin recomprimirlo. El ZIP exterior del complemento
utiliza `ZIP_STORED`, por lo que conserva los bytes del runtime entre Windows
y Linux. El control de publicación exige que este blob esté incluido en Git.

El script funciona sin red y no instala servicios MPI, modifica el registro,
escribe en QGIS ni descarga ningún componente.
