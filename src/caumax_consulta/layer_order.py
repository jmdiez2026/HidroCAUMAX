"""Place HidroCAUMAX results above the official CAUMAX layer-tree block."""
from qgis.core import QgsProject


ORDER_PROPERTY = 'HidroCAUMAX/order_rank'
EXPORT_OWNER_PROPERTY = 'HidroCAUMAX/export_owner'
_INACTIVE_PROPERTY = 'HidroCAUMAX/automatically_hidden_result'
_OWNERS = (('HidroCAUMAX/course_owner', 0),
           ('HidroCAUMAX/drainage_owner', 10),
           ('HidroCAUMAX/terrain_owner', 30))


def _rank(node):
    value = node.customProperty(ORDER_PROPERTY, None)
    if value is not None:
        return int(value)
    for key, rank in _OWNERS:
        if node.customProperty(key, None) is not None:
            return rank
    layer = node.layer() if hasattr(node, 'layer') else None
    if layer is not None:
        value = layer.customProperty(ORDER_PROPERTY, None)
        if value is not None:
            return int(value)
    return None


def _official_block(root):
    official = root.findGroup('CAUMAX_CAPAS')
    if official is not None:
        while official.parent() is not root:
            official = official.parent()
    return official


def ensure_results_above_official(project):
    """Move existing owned nodes intact; retain their order and visibility."""
    root = project.layerTreeRoot()
    official = _official_block(root)
    if official is None:
        return
    children = root.children()
    owned = [node for node in children if node is not official and _rank(node) is not None]
    if root.hasCustomLayerOrder():
        # A user-defined drawing order can override the visible tree order.
        # Move only our layers ahead of the official block, retaining their
        # tree order and the existing custom order of every other layer.
        own_nodes = [leaf for node in owned
                     for leaf in (node.findLayers() if hasattr(node, 'findLayers') else [node])]
        own_ids = {leaf.layerId() for leaf in own_nodes}
        official_ids = {node.layerId() for node in official.findLayers()}
        current = root.customLayerOrder()
        current_ids = {layer.id() for layer in current}
        ours = [leaf.layer() for leaf in own_nodes if leaf.layerId() in current_ids]
        remaining = [layer for layer in current if layer.id() not in own_ids]
        index = next((i for i, layer in enumerate(remaining) if layer.id() in official_ids), None)
        if index is not None:
            updated = remaining[:index] + ours + remaining[index:]
            if current != updated:
                root.setCustomLayerOrder(updated)
    others = [node for node in children if node not in owned]
    position = others.index(official)
    if children == others[:position] + owned + others[position:]:
        return
    for node in owned:
        # QGIS takeChild also detaches descendants. Retain their exact objects,
        # and keep a temporary clone in the tree so the registry bridge never
        # interprets the move as deleting the associated map layers.
        descendants = []
        def remember(parent):
            nested = list(parent.children())
            if nested:
                descendants.append((parent, nested))
                for child in nested:
                    remember(child)
        remember(node)
        placeholder = node.clone()
        root.insertChildNode(root.children().index(official), placeholder)
        root.takeChild(node)
        for parent, nested in reversed(descendants):
            parent.insertChildNodes(0, nested)
        root.insertChildNode(root.children().index(placeholder), node)
        root.removeChildNode(placeholder)


def result_index(root, rank):
    official = _official_block(root)
    children = root.children()
    if official is not None:
        position = children.index(official)
        while position > 0 and _rank(children[position - 1]) is not None:
            position -= 1
    else:
        position = 0
    while position < len(children):
        current = _rank(children[position])
        if current is None or current > rank:
            break
        position += 1
    return position


def create_result_group(project, name, rank):
    ensure_results_above_official(project)
    root = project.layerTreeRoot()
    group = root.insertGroup(result_index(root, rank), name)
    group.setCustomProperty(ORDER_PROPERTY, rank)
    return group


def activate_result_group(project, active, owner_property):
    """Show one calculation's result group without deleting previous results.

    Child legend choices stay intact. Only groups explicitly owned by this
    result type are affected; terrain, official layers and user groups retain
    their visibility. Reactivation restores groups hidden by this helper, but
    never overrides a group the user had already hidden.
    """
    def groups(parent):
        for node in parent.children():
            if hasattr(node, 'children'):
                yield node
                yield from groups(node)

    for group in groups(project.layerTreeRoot()):
        if group.customProperty(owner_property, None) is None:
            continue
        if group is active:
            if group.customProperty(_INACTIVE_PROPERTY, False):
                group.setItemVisibilityChecked(True)
                group.removeCustomProperty(_INACTIVE_PROPERTY)
        elif group.itemVisibilityChecked():
            group.setCustomProperty(_INACTIVE_PROPERTY, True)
            group.setItemVisibilityChecked(False)


def add_result_layer(layer, project=None):
    project = QgsProject.instance() if project is None else project
    ensure_results_above_official(project)
    rank = 40 if layer.type() == layer.RasterLayer else 20
    layer.setCustomProperty(ORDER_PROPERTY, rank)
    layer.setCustomProperty(EXPORT_OWNER_PROPERTY, 'HidroCAUMAX')
    registered = project.addMapLayer(layer, False)
    if registered is not None:
        root = project.layerTreeRoot()
        if root.findLayer(layer.id()) is None:
            root.insertLayer(result_index(root, rank), layer)
        ensure_results_above_official(project)
    return registered
