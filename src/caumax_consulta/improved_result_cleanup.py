"""Retire obsolete 500 m result layers after improved D8 generation.

Only plugin-owned results are affected. Source MDTs, direction rasters,
reference layers and user-created layers are never deleted.
"""
from pathlib import Path

from qgis.PyQt.QtCore import QTimer
from qgis.core import QgsProject, QgsVectorLayer, QgsMessageLog, Qgis

from .boundary_layers import (OWNER_PROPERTY as BOUNDARY_OWNER,
                              KIND_PROPERTY as BOUNDARY_KIND,
                              _NAMES as BOUNDARY_KINDS,
                              storage_directory, _prune_obsolete_files)
from .course_layers import _OWNER_PROPERTY as COURSE_OWNER


def _is_500m(layer):
    """Check resolution attributes, not layer display names."""
    if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
        return False
    fields = layer.fields()
    x, y = fields.indexFromName('RES_X_M'), fields.indexFromName('RES_Y_M')
    if x < 0 or y < 0:
        return False
    try:
        return any(abs(float(f[x])-500.) < 1e-6 and
                   abs(float(f[y])-500.) < 1e-6 for f in layer.getFeatures())
    except (TypeError, ValueError):
        return False


def retire_500m_results(iface=None, project=None):
    """Remove obsolete 500×500 m courses/outlines and their owned GeoPackages.

    Call only after a finer-resolution result has been successfully published.
    An edit session blocks deletion rather than discarding unsaved changes.
    """
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    selected, paths = [], []
    for layer in tuple(project.mapLayers().values()):
        course = bool(layer.customProperty(COURSE_OWNER, ''))
        boundary = (bool(layer.customProperty(BOUNDARY_OWNER, ''))
                    and layer.customProperty(BOUNDARY_KIND, '') in BOUNDARY_KINDS)
        if not (course or boundary) or not _is_500m(layer):
            continue
        if layer.isEditable():
            QgsMessageLog.logMessage(
                'No se elimina la capa de 500 m en edición: ' + layer.name(),
                'HidroCAUMAX', Qgis.Warning)
            continue
        selected.append(layer.id())
        if boundary and layer.providerType() == 'ogr':
            path = Path(layer.source().split('|', 1)[0])
            # Only private, plugin-owned GeoPackages, never user output files.
            if path.parent.resolve() == storage_directory().resolve():
                paths.append(str(path))
    if selected:
        project.removeMapLayers(selected)
    for group in reversed(root.findGroups(recursive=True)):
        if (not group.children() and
                (group.customProperty(COURSE_OWNER, '') or
                 group.customProperty(BOUNDARY_OWNER, ''))):
            parent = group.parent()
            if parent is not None:
                parent.removeChildNode(group)
    if paths:
        # Windows/OGR may keep database handles until QGIS releases the layers.
        QTimer.singleShot(0, lambda: _prune_obsolete_files(
            paths, (storage_directory(),), project))
    if iface is not None and iface.mapCanvas() is not None:
        canvas = iface.mapCanvas()
        canvas.clearCache()
        canvas.refresh()
        canvas.repaint()
    return len(selected)
