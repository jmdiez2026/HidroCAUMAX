"""Remove the obsolete automatic export-folder group without deleting files."""
from pathlib import Path

from qgis.core import QgsProject, QgsProviderRegistry, QgsProviderSublayerDetails, QgsVectorLayer, QgsWkbTypes

from .improved_storage import export_directory, _plain, PO_FILENAME


GROUP_NAME = 'HidroCAUMAX mejoradas'
GROUP_PROPERTY = 'HidroCAUMAX/improved_folder_group'
SOURCE_PROPERTY = 'HidroCAUMAX/improved_folder_source'
VISIBLE_FILE = 'Red_drenaje_25m.shp'
# Sidecars (.dbf/.shx/.prj/.aux.xml/.ovr) belong to their parent dataset.
DATA_SUFFIXES = {'.tif', '.tiff', '.shp', '.gpkg', '.geojson', '.json', '.kml',
                 '.gml', '.sqlite', '.asc', '.img', '.vrt', '.grd', '.dem'}


def remove_improved_folder_group(project=None):
    """Remove only marked folder imports; retain user layers and source files."""
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    groups = [g for g in root.findGroups() if g.customProperty(GROUP_PROPERTY, False)]
    ids = {n.layerId() for group in groups for n in group.findLayers()
           if n.layer() is not None and n.layer().customProperty(SOURCE_PROPERTY, '')}
    project.removeMapLayers(list(ids))
    for group in reversed(groups):
        parent = group.parent()
        if parent is None:
            continue
        # Any user-added contents keep their layers, subgroups and checkboxes.
        index = parent.children().index(group)
        for child in list(group.children()):
            parent.insertChildNode(index, child.clone())
            index += 1
        parent.removeChildNode(group)
    return len(ids)


def _group(project):
    root = project.layerTreeRoot()
    group = next((g for g in root.findGroups() if g.customProperty(GROUP_PROPERTY, False)), None)
    if group is None:
        group = root.findGroup(GROUP_NAME)
    anchor = root.findGroup('CAUMAX_CAPAS')
    references = next((g for g in root.findGroups()
        if g.customProperty('HidroCAUMAX/reference_group', '') == 'v1'), None)
    if references is not None and (anchor is None or references.parent() == anchor.parent()):
        anchor = references
    parent = anchor.parent() if anchor is not None else root
    index = parent.children().index(anchor) if anchor is not None else 0
    if group is None:
        group = parent.insertGroup(index, GROUP_NAME)
    elif group.parent() != parent or parent.children().index(group) != index - 1:
        # Insert the clone before removing the old node, retaining layer IDs.
        old = group
        group = old.clone()
        parent.insertChildNode(index, group)
        old.parent().removeChildNode(old)
    group.setName(GROUP_NAME)
    group.setCustomProperty(GROUP_PROPERTY, True)
    group.setItemVisibilityChecked(True)
    group.setExpanded(True)
    return group


def load_improved_layers(project=None):
    """Refresh on each button click; never infer ownership for disk deletion."""
    project = project or QgsProject.instance()
    folder = export_directory()
    group = _group(project)
    known = {n.layer().customProperty(SOURCE_PROPERTY, ''): n for n in group.findLayers()
             if n.layer() is not None and n.layer().customProperty(SOURCE_PROPERTY, '')}
    for node in group.findLayers():
        node.setItemVisibilityChecked(False)
    loaded, failed = [], []
    options = QgsProviderSublayerDetails.LayerOptions(project.transformContext())
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if (path.name.startswith('.') or path.suffix.lower() not in DATA_SUFFIXES or not _plain(path)):
            continue
        try:
            details = QgsProviderRegistry.instance().querySublayers(str(path))
            if not details:
                failed.append(path.name)
            for detail in details:
                key = str(path.resolve()) + '|' + detail.providerKey() + '|' + detail.uri()
                node = known.get(key)
                if node is None:
                    layer = detail.toLayer(options)
                    if layer is None or not layer.isValid():
                        failed.append(path.name)
                        continue
                    layer.setName(path.stem if len(details) == 1 else path.stem + ' · ' + detail.name())
                    layer.setCustomProperty(SOURCE_PROPERTY, key)
                    # The imported P0 is source data, not a generated product.
                    if path.name != PO_FILENAME:
                        layer.setCustomProperty('HidroCAUMAX/export_owner', 'HidroCAUMAX')
                    else:
                        from .po_style import apply_po_style
                        apply_po_style(layer)
                    if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.LineGeometry:
                        from .drainage_layers import network_symbol
                        layer.renderer().setSymbol(network_symbol())
                    project.addMapLayer(layer, False)
                    node = group.addLayer(layer)
                    known[key] = node
                node.setItemVisibilityChecked(path.name.casefold() == VISIBLE_FILE.casefold())
                node.setExpanded(False)
                loaded.append(node.layerId())
        except (RuntimeError, OSError) as error:
            failed.append(path.name + ': ' + str(error))
    return dict(group=group, loaded=loaded, failed=failed)
