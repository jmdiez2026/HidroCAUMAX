from qgis.core import QgsApplication, Qgis
from osgeo import gdal
app = QgsApplication([], False)
app.initQgis()
print('QGIS', Qgis.QGIS_VERSION, 'GDAL', gdal.VersionInfo())
app.exitQgis()
