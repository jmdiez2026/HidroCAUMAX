# TauDEM integrado para Windows de 64 bits

`taudem-5.5.0-win64.zip` contiene los cinco ejecutables oficiales de TauDEM
5.5.0 utilizados por HidroCAUMAX: PitRemove, D8FlowDir, AreaD8, Threshold y
StreamNet, junto con Microsoft MPI 10.1.3, sus bibliotecas GDAL/PROJ y los
datos necesarios para los sistemas de referencia. No contiene ArcGIS ni
necesita sus herramientas.

El complemento verifica el manifiesto SHA-256 y prepara estos componentes
automáticamente al primer cálculo D8, en la carpeta privada
`%LOCALAPPDATA%/HidroCAUMAX/runtime`. No descarga componentes, solicita
privilegios de administrador ni modifica el PATH global. La extracción y los
cálculos se realizan en segundo plano y pueden cancelarse.

El runtime de Microsoft Visual C++ ya proporcionado por QGIS o Windows se
localiza y copia automáticamente a la carpeta privada del usuario; sus DLL
no se redistribuyen en este archivo. Solo el proceso de TauDEM utiliza esa
copia. GDAL y PROJ utilizan las
bibliotecas y datos del paquete, sin sustituir los de QGIS.

El paquete contiene `licenses/` con los avisos de los componentes, su
procedencia y el código fuente completo de TauDEM. Cada componente conserva
su propia licencia. TauDEM se ejecuta como programa independiente.

Fuentes originales:

- [TauDEM 5.5.0](https://github.com/dtarb/TauDEM/releases/tag/v5.5.0).
- [Código de TauDEM 5.5.0](https://github.com/dtarb/TauDEM/tree/v5.5.0).
- [Microsoft MPI](https://learn.microsoft.com/en-us/message-passing-interface/microsoft-mpi).

En Linux y macOS se conserva la selección de un TauDEM y MPI externos. En
Windows, **Motor TauDEM…** permite seleccionar una instalación externa de
forma opcional y volver al motor incluido.

Para los mantenedores: `scripts/prepare_taudem_bundle.py` genera y valida el
archivo a partir de un staging revisado. El ZIP se versiona en Git y CI
comprueba sus bytes sin recomprimirlo. El instalable exterior conserva esos
bytes para mantener un mismo SHA-256 en Windows y Linux.
