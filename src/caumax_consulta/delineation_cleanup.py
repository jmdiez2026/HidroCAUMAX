"""Remove generated results across queries without touching source data.

Layer ownership is stored in the QGIS project, so cleanup also finds results
whose dialog has already closed. Live dialogs are notified first so pending
calculations cannot put their cleared results back on the map.
"""
from weakref import WeakSet

from qgis.PyQt import sip
from qgis.core import QgsProject


OWNER_PROPERTIES = (
    'HidroCAUMAX/course_owner',
    'HidroCAUMAX/boundary_owner',
    'HidroCAUMAX/terrain_owner',
    'HidroCAUMAX/drainage_owner',
)
_dialogs = WeakSet()


def register_dialog(dialog):
    _dialogs.add(dialog)


def unregister_dialog(dialog):
    _dialogs.discard(dialog)


def _owned(item):
    return any(item.customProperty(key, None) not in (None, '')
               for key in OWNER_PROPERTIES)


def clear_delineation_layers(project=None):
    """Remove all tool-owned result layers and only empty owned groups.

    Do not match display names, delete files, or remove foreign layers placed
    inside a generated group. Auxiliary D8/network layers share the lifecycle
    of the basin that generated them.
    """
    project = QgsProject.instance() if project is None else project
    layer_ids = [layer.id() for layer in project.mapLayers().values()
                 if _owned(layer)]
    if layer_ids:
        project.removeMapLayers(layer_ids)
    for group in reversed(project.layerTreeRoot().findGroups(recursive=True)):
        if not sip.isdeleted(group) and _owned(group) and not group.children():
            parent = group.parent()
            if parent is not None:
                parent.removeChildNode(group)
    return len(layer_ids)


def clear_delineations(*, close_dialogs=False, project=None):
    """Clear every query, keeping dialogs open unless a new red point begins."""
    project = QgsProject.instance() if project is None else project
    before = set(project.mapLayers())
    for dialog in tuple(_dialogs):
        try:
            if getattr(dialog, '_disposed', False):
                continue
            if close_dialogs:
                dialog.dispose()
                dialog.close()
            else:
                dialog._clear_local_delineation()
        except RuntimeError:
            # Qt may already have destroyed a dialog whose Python wrapper is
            # still referenced by an asynchronous task.
            _dialogs.discard(dialog)
    clear_delineation_layers(project)
    return len(before - set(project.mapLayers()))
