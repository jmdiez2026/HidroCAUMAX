"""Remove generated results across queries without touching source data.

Layer ownership is stored in the QGIS project, so cleanup also finds results
whose dialog has already closed. Live dialogs are notified first so pending
calculations cannot put their cleared results back on the map.
"""
from weakref import WeakKeyDictionary, WeakSet, ref

from qgis.PyQt import sip
from qgis.core import QgsProject


OWNER_PROPERTIES = (
    'HidroCAUMAX/course_owner',
    'HidroCAUMAX/boundary_owner',
    'HidroCAUMAX/terrain_owner',
    'HidroCAUMAX/drainage_owner',
)
_dialogs = WeakSet()
_participants = WeakSet()
_canvases = WeakSet()
_native_overlays = WeakKeyDictionary()
OVERLAY_OWNER_ROLE = 0x48434D58
OVERLAY_OWNER = 'HidroCAUMAX/delineation-overlay/v1'
PREVIEW_ATTRIBUTES = ('_rubber', '_grid', '_course', '_marker', '_smooth',
                      '_original_marker', '_snap_link')


def register_participant(participant):
    """Register a reusable panel; cleanup never disposes or closes this object."""
    _participants.add(participant)
    _remember_dialog_overlays(participant)


def unregister_participant(participant):
    _participants.discard(participant)


def register_overlay(canvas, item):
    """Tag an explicitly owned canvas item, surviving loss of its controller."""
    item.setData(OVERLAY_OWNER_ROLE, OVERLAY_OWNER)
    _canvases.add(canvas)
    return item


def remove_overlay(item):
    """Remove from the actual scene and destroy the graphics item immediately."""
    if item is None:
        return
    try:
        if sip.isdeleted(item):
            return
        item.hide()
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
        sip.delete(item)
    except RuntimeError:
        pass


def _remember_dialog_overlays(dialog):
    try:
        canvas = dialog.iface.mapCanvas()
    except (AttributeError, RuntimeError):
        return
    _canvases.add(canvas)
    # Existing attributes are proof of ownership even if the item predates
    # registration; never infer ownership from colour, geometry or layer name.
    for name in PREVIEW_ATTRIBUTES:
        item = getattr(dialog, name, None)
        if item is not None:
            try:
                register_overlay(canvas, item)
            except (AttributeError, RuntimeError):
                pass


def _remember_native_query_marker(dialog):
    """The red native marker belongs to the query which opened this basin."""
    try:
        parent = dialog.parent()
        if parent is None or not any(cls.__name__ == 'MaxFlowDialog' and
                cls.__module__.endswith('.maxflowdialog') for cls in type(parent).__mro__):
            return
        marker = getattr(parent, 'marker', None)
        if marker is None:
            return
        register_overlay(dialog.iface.mapCanvas(), marker)
        _native_overlays.setdefault(parent, {})['marker'] = ref(marker)
    except (AttributeError, RuntimeError, TypeError):
        return


def clear_canvas_overlays():
    """Sweep only ownership-tagged items, including earlier query generations."""
    for owner, attributes in tuple(_native_overlays.items()):
        for name, reference in attributes.items():
            item = reference()
            try:
                if getattr(owner, name, None) is item:
                    # Native updatePoint/closeEvent must not keep a deleted SIP
                    # wrapper; the next click can create a fresh marker normally.
                    setattr(owner, name, None)
            except RuntimeError:
                pass
            remove_overlay(item)
    _native_overlays.clear()
    for canvas in tuple(_canvases):
        try:
            for item in tuple(canvas.scene().items()):
                if item.data(OVERLAY_OWNER_ROLE) == OVERLAY_OWNER:
                    remove_overlay(item)
        except RuntimeError:
            _canvases.discard(canvas)


def _refresh_canvases():
    for canvas in tuple(_canvases):
        try:
            # A render started before layer removal must not publish its stale
            # image after the graphics items have already been cleared.
            canvas.stopRendering()
            canvas.clearCache()
            canvas.refresh()
            canvas.scene().update()
            canvas.viewport().update()
        except RuntimeError:
            _canvases.discard(canvas)


def register_dialog(dialog):
    _dialogs.add(dialog)
    _remember_dialog_overlays(dialog)
    _remember_native_query_marker(dialog)


def unregister_dialog(dialog):
    _dialogs.discard(dialog)


def _owned(item):
    return any(item.customProperty(key, None) not in (None, '')
               for key in OWNER_PROPERTIES)


def clear_delineation_layers(project=None, *, preserve_published_drainage=False):
    """Remove all tool-owned result layers and only empty owned groups.

    Do not match display names, delete files, or remove foreign layers placed
    inside a generated group. Auxiliary D8/network layers share the lifecycle
    of the basin that generated them.
    """
    project = QgsProject.instance() if project is None else project
    from .drainage_layers import PIN_PROPERTY
    layer_ids = [layer.id() for layer in project.mapLayers().values()
                 if _owned(layer) and not
                 (preserve_published_drainage and
                  layer.customProperty(PIN_PROPERTY, False))]
    if layer_ids:
        project.removeMapLayers(layer_ids)
    for group in reversed(project.layerTreeRoot().findGroups(recursive=True)):
        if not sip.isdeleted(group) and _owned(group) and not group.children():
            parent = group.parent()
            if parent is not None:
                parent.removeChildNode(group)
    return len(layer_ids)


def clear_delineations(*, close_dialogs=False, project=None, include_exports=False,
                       include_references=False, preserve_published_drainage=False):
    """Clear every query, keeping dialogs open unless a new red point begins."""
    project = QgsProject.instance() if project is None else project
    before = set(project.mapLayers())
    # Only explicit Limpiar capas opts in. Starting another basin query must
    # preserve the user's reference maps and their in-progress requests.
    if include_references:
        from .map_services import clear_reference_layers
        clear_reference_layers(project)
    for participant in tuple(_participants):
        try:
            if not getattr(participant, '_disposed', False):
                participant._clear_local_delineation()
        except RuntimeError:
            _participants.discard(participant)
    for dialog in tuple(_dialogs):
        try:
            _remember_dialog_overlays(dialog)
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
    clear_canvas_overlays()
    clear_delineation_layers(
        project, preserve_published_drainage=preserve_published_drainage)
    if include_exports:
        from .layer_order import EXPORT_OWNER_PROPERTY
        project.removeMapLayers([layer.id() for layer in project.mapLayers().values()
            if layer.customProperty(EXPORT_OWNER_PROPERTY, None) == 'HidroCAUMAX'])
    _refresh_canvases()
    return len(before - set(project.mapLayers()))
