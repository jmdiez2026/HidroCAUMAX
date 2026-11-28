"""Shared QGIS-profile location for complete maximum-flow consultation JSONs.

Use the active QGIS profile rather than hard-coding the Windows account name.
"""
from pathlib import Path

from qgis.core import QgsApplication


CONSULTATION_FOLDER = 'ConsultaMapasMaximos'
DEFAULT_CONSULTATION_FILENAME = 'Consulta_Mapas_Caudales_Máximos_naturales (Qci).json'
CONSULTATION_FILTER = 'Consulta HidroCAUMAX (*.json)'


def consultation_directory(profile_root=None):
    """Create and return HidroCAUMAX/ConsultaMapasMaximos in the active profile."""
    if profile_root is None:
        profile_root = QgsApplication.qgisSettingsDirPath()
    folder = Path(profile_root) / 'HidroCAUMAX' / CONSULTATION_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_consultation_path(profile_root=None):
    """Default location and filename for a complete consultation save."""
    return consultation_directory(profile_root) / DEFAULT_CONSULTATION_FILENAME
