"""Portable TauDEM network snapshots and owned QGIS drainage layers."""
import math
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from osgeo import ogr,osr
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QVariant, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsCoordinateReferenceSystem,QgsFeature,QgsField,QgsGeometry,
    QgsLineSymbol,QgsPalettedRasterRenderer,QgsProject,QgsRasterLayer,
    QgsSimpleLineSymbolLayer,QgsVectorLayer,QgsWkbTypes,QgsMessageLog,Qgis)


OWNER_PROPERTY='HidroCAUMAX/drainage_owner'
ROLE_PROPERTY='HidroCAUMAX/drainage_role'

def _private_taudem_directory(source):
    """Recognize *only* a query-owned TauDEM temp directory, never exports."""
    try:
        root = (Path(tempfile.gettempdir()).resolve() / 'HidroCAUMAX' / 'dem_variants')
        source = Path(str(source).split('|', 1)[0]).resolve()
        parts = source.relative_to(root).parts
        if (len(parts) < 5 or not re.fullmatch(r'[a-f0-9]{20}', parts[0])
                or parts[1] != 'taudem'
                or not re.fullmatch(r'[0-9]+(?:[.][0-9]+)?', parts[2])):
            return None
        candidate = root / parts[0] / 'taudem' / parts[2]
        if candidate.is_symlink():
            return None
        return candidate
    except (OSError, ValueError, TypeError):
        return None


def _remove_unused_taudem_caches(candidates, project, protected_paths):
    if not candidates:
        return
    protected = {_private_taudem_directory(path) for path in protected_paths}
    protected.update(_private_taudem_directory(layer.source())
                     for layer in project.mapLayers().values()
                     if layer.providerType() in ('gdal', 'ogr'))
    for cache in candidates - protected:
        if cache is None or not cache.is_dir() or cache.is_symlink():
            continue
        # Do not follow symlink trees during disk cleanup.
        if any(path.is_symlink() for path in cache.rglob('*')):
            continue
        try:
            shutil.rmtree(cache)
        except OSError as error:
            QgsMessageLog.logMessage(
                'No se pudo retirar la caché TauDEM anterior ' + str(cache) +
                ': ' + str(error), 'HidroCAUMAX', Qgis.Warning)


def prune_managed_drainage(project=None, *, keep_owner=None, protected_paths=(), retire_paths=()):
    """Only the currently selected TauDEM generation may remain in QGIS.

    Remove older managed QGIS layers and unreferenced temporary D8 outputs.
    Explicit TIFF/SHP exports and user-created layers are never removed.
    """
    project = project or QgsProject.instance()
    root = project.layerTreeRoot()
    obsolete = [layer for layer in project.mapLayers().values()
                if layer.customProperty(OWNER_PROPERTY, '') and
                layer.customProperty(OWNER_PROPERTY) != keep_owner]
    editing = {layer.customProperty(OWNER_PROPERTY)
               for layer in obsolete if isinstance(layer, QgsVectorLayer) and layer.isEditable()}
    obsolete = [layer for layer in obsolete
                if layer.customProperty(OWNER_PROPERTY) not in editing]
    candidates = {_private_taudem_directory(layer.source()) for layer in obsolete
                  if layer.providerType() == 'gdal'}
    candidates.update(_private_taudem_directory(path) for path in retire_paths)
    candidates.discard(None)
    if obsolete:
        project.removeMapLayers([layer.id() for layer in obsolete])
    for group in reversed(root.findGroups(recursive=True)):
        if (not sip.isdeleted(group) and group.customProperty(OWNER_PROPERTY, '')
                and not group.children()):
            parent = group.parent()
            if parent is not None:
                parent.removeChildNode(group)
    if keep_owner is not None:
        for layer in project.mapLayers().values():
            if layer.customProperty(OWNER_PROPERTY) != keep_owner:
                continue
            nodes = [node for node in root.findLayers() if node.layerId() == layer.id()]
            nodes.sort(key=lambda node: (
                node.parent().customProperty(OWNER_PROPERTY, '') != keep_owner,
                not node.itemVisibilityChecked()))
            for node in nodes[1:]:
                node.parent().removeChildNode(node)
    if candidates:
        caches = frozenset(candidates)
        protected = tuple(protected_paths)
        QTimer.singleShot(0, lambda: _remove_unused_taudem_caches(caches, project, protected))
    return len(obsolete)


MAX_NETWORK_WKB_BYTES=8*1024*1024
MAX_NETWORK_VERTICES=500000
MAX_ATTRIBUTE_ITEMS=50000
FIELD_TYPES={ogr.OFTInteger:'int',ogr.OFTInteger64:'int64',ogr.OFTReal:'real',ogr.OFTString:'str'}


def _network_path(products):
    if isinstance(products,(str,Path)):
        return Path(products)
    for key in ('network_path','network_shp','network'):
        value=products.get(key) if isinstance(products,dict) else getattr(products,key,None)
        if value:
            return Path(value)
    paths=products.get('paths',{}) if isinstance(products,dict) else getattr(products,'paths',{})
    for key in ('network_path','network_shp','network'):
        if isinstance(paths,dict) and paths.get(key):
            return Path(paths[key])
    raise ValueError('TauDEM no ha proporcionado la ruta de la red de drenaje.')


def _point_count(geometry):
    return geometry.GetPointCount()+sum(_point_count(geometry.GetGeometryRef(i))
                                        for i in range(geometry.GetGeometryCount()))


def _finite_points(geometry):
    dimensions=3 if geometry.Is3D() else 2
    return (all(math.isfinite(value) for point in (geometry.GetPoints() or ()) for value in point[:dimensions])
            and all(_finite_points(geometry.GetGeometryRef(i))
                    for i in range(geometry.GetGeometryCount())))


def read_network_snapshot(products):
    """Read only OGR data; safe to call in the hydrological worker thread."""
    path=_network_path(products)
    try:
        dataset=ogr.Open(str(path),0)
    except RuntimeError as error:
        raise ValueError('No se puede abrir la red de drenaje generada por TauDEM.') from error
    if dataset is None:
        raise ValueError('No se puede abrir la red de drenaje generada por TauDEM.')
    layer=None
    try:
        layer=dataset.GetLayer(0)
        if layer is None or layer.GetSpatialRef() is None:
            raise ValueError('La red TauDEM no contiene un sistema de referencia.')
        reference=layer.GetSpatialRef().Clone()
        if not reference.IsProjected() or not math.isclose(reference.GetLinearUnits(),1.0,abs_tol=1e-10):
            raise ValueError('La red TauDEM necesita coordenadas proyectadas métricas.')
        reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        definition=layer.GetLayerDefn();fields=[]
        for i in range(definition.GetFieldCount()):
            field=definition.GetFieldDefn(i)
            if field.GetType() not in FIELD_TYPES:
                raise ValueError('La red TauDEM contiene un tipo de atributo no admitido.')
            fields.append({'name':field.GetName(),'type':FIELD_TYPES[field.GetType()],
                           'width':field.GetWidth(),'precision':field.GetPrecision()})
        features=[];total_bytes=vertices=attribute_items=0
        for feature in layer:
            geometry=feature.GetGeometryRef()
            if (geometry is None or geometry.IsEmpty()
                    or geometry.IsMeasured()
                    or ogr.GT_Flatten(geometry.GetGeometryType()) not in (ogr.wkbLineString,ogr.wkbMultiLineString)):
                raise ValueError('La red TauDEM debe contener líneas de drenaje no vacías.')
            if not _finite_points(geometry):
                raise ValueError('La red TauDEM contiene coordenadas no finitas.')
            # Preserve StreamNet Z coordinates for lossless offline export;
            # QGIS automatically renders their XY projection on the map.
            wkb=bytes(geometry.ExportToWkb())
            total_bytes+=len(wkb);vertices+=_point_count(geometry)
            attribute_items+=len(fields)
            if (total_bytes>MAX_NETWORK_WKB_BYTES or vertices>MAX_NETWORK_VERTICES
                    or attribute_items>MAX_ATTRIBUTE_ITEMS):
                raise ValueError('La red de drenaje es demasiado densa para guardarla; aumenta el umbral de cauce.')
            attributes={}
            for i,field in enumerate(fields):
                value=feature.GetField(i)
                if isinstance(value,float) and not math.isfinite(value):
                    raise ValueError('La red TauDEM contiene atributos numéricos no finitos.')
                attributes[field['name']]=value
            features.append({'wkb':wkb,'attributes':attributes})
        return {'network_crs_wkt':reference.ExportToWkt(),'fields':fields,'features':features}
    finally:
        layer=dataset=None


def network_symbol():
    symbol=QgsLineSymbol.createSimple({'line_color':'22,68,107,255','line_width':'.8',
        'line_width_unit':'MM','capstyle':'round','joinstyle':'round'})
    symbol.appendSymbolLayer(QgsSimpleLineSymbolLayer.create({'line_color':'101,199,255,255',
        'line_width':'.6','line_width_unit':'MM','capstyle':'round','joinstyle':'round'}))
    return symbol


def _snapshot_fields(snapshot):
    fields=snapshot.get('fields')
    if fields is not None:
        return fields
    inferred={}
    for feature in snapshot.get('features',[]):
        for name,value in feature.get('attributes',{}).items():
            kind='str' if isinstance(value,str) else 'real' if isinstance(value,float) else 'int64'
            if name not in inferred or kind=='str' or (kind=='real' and inferred[name]!='str'):
                inferred[name]=kind
    return [{'name':name,'type':kind,'width':0,'precision':10 if kind=='real' else 0}
            for name,kind in inferred.items()]


def build_network_layer(snapshot):
    """Build an unregistered, styled layer from the archived WKB/attributes."""
    if not isinstance(snapshot,dict):
        raise ValueError('La red archivada no es válida.')
    crs=QgsCoordinateReferenceSystem.fromWkt(snapshot.get('network_crs_wkt',''))
    if not crs.isValid():
        raise ValueError('La red archivada no tiene un CRS válido.')
    records=snapshot.get('features',[])
    if not isinstance(records,list):
        raise ValueError('Las entidades de la red archivada no son válidas.')
    geometries=[];size=vertices=attribute_items=0;wkb_type=None
    for record in records:
        if (not isinstance(record,dict) or not isinstance(record.get('wkb'),(bytes,bytearray))
                or not isinstance(record.get('attributes'),dict)):
            raise ValueError('Un tramo de la red archivada no es válido.')
        geometry=QgsGeometry();geometry.fromWkb(bytes(record['wkb']))
        if geometry.isNull() or geometry.isEmpty() or QgsWkbTypes.geometryType(geometry.wkbType())!=QgsWkbTypes.LineGeometry:
            raise ValueError('La red archivada contiene una geometría que no es una línea.')
        if QgsWkbTypes.hasM(geometry.wkbType()):
            raise ValueError('La red archivada usa coordenadas M no admitidas.')
        if wkb_type is None:wkb_type=geometry.wkbType()
        if geometry.wkbType()!=wkb_type:
            raise ValueError('La red archivada mezcla tipos de líneas incompatibles.')
        if any(not math.isfinite(v) for p in geometry.vertices()
               for v in ((p.x(),p.y(),p.z()) if QgsWkbTypes.hasZ(geometry.wkbType()) else (p.x(),p.y()))):
            raise ValueError('La red archivada contiene coordenadas no finitas.')
        size+=len(record['wkb']);vertices+=geometry.constGet().nCoordinates()
        attribute_items+=len(record['attributes'])
        if (size>MAX_NETWORK_WKB_BYTES or vertices>MAX_NETWORK_VERTICES
                or attribute_items>MAX_ATTRIBUTE_ITEMS):
            raise ValueError('La red archivada excede el tamaño admitido.')
        geometries.append(geometry)
    layer=QgsVectorLayer(QgsWkbTypes.displayString(wkb_type or QgsWkbTypes.LineString),'Red de drenaje TauDEM','memory')
    layer.setCrs(crs)
    types={'int':QVariant.Int,'int64':QVariant.LongLong,'real':QVariant.Double,'str':QVariant.String}
    fields=_snapshot_fields(snapshot)
    if (not isinstance(fields,list) or len(fields)>256
            or any(not isinstance(field,dict) or field.get('type') not in types
                   or not isinstance(field.get('name'),str) or not field['name']
                   or not isinstance(field.get('width',0),int) or not 0<=field.get('width',0)<=4096
                   or not isinstance(field.get('precision',0),int) or not 0<=field.get('precision',0)<=30
                   for field in fields)
            or len({field['name'] for field in fields})!=len(fields)):
        raise ValueError('El esquema de atributos de la red no es válido.')
    qfields=[QgsField(field['name'],types[field['type']],'',int(field.get('width',0)),int(field.get('precision',0))) for field in fields]
    if qfields and not layer.dataProvider().addAttributes(qfields):
        raise ValueError('No se pueden recuperar los atributos de la red.')
    layer.updateFields();features=[]
    for record,geometry in zip(records,geometries):
        feature=QgsFeature(layer.fields());feature.setGeometry(geometry)
        attrs=record.get('attributes',{})
        if set(attrs)-{field['name'] for field in fields}:
            raise ValueError('Los atributos no corresponden al esquema de la red.')
        if any(value is not None and (type(value) not in (bool,int,float,str)
                   or isinstance(value,float) and not math.isfinite(value)) for value in attrs.values()):
            raise ValueError('La red archivada contiene atributos no escalares o no finitos.')
        feature.setAttributes([attrs.get(field['name']) for field in fields]);features.append(feature)
    if features:
        ok,_=layer.dataProvider().addFeatures(features)
        if not ok:raise ValueError('No se puede recuperar la red de drenaje archivada.')
    layer.updateExtents();layer.renderer().setSymbol(network_symbol())
    return layer


def _directions_layer(path):
    if not path or not Path(path).is_file():return None
    layer=QgsRasterLayer(str(path),'Modelo de Dirección de Flujo D8 · CAUMAX/ESRI','gdal')
    if not layer.isValid():return None
    directions=((1,'E','#377eb8'),(2,'SE','#999999'),(4,'S','#e41a1c'),(8,'SW','#f781bf'),
                (16,'W','#a65628'),(32,'NW','#ff7f00'),(64,'N','#984ea3'),(128,'NE','#4daf4a'))
    classes=[QgsPalettedRasterRenderer.Class(value,QColor(color),label) for value,label,color in directions]
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(),1,classes))
    return layer



def _styled_mdt(analysis, dem_path):
    """Create natural-colour and hillshade views of one unchanged elevation TIFF."""
    from .dem_display import build_terrain_layers
    low, high = getattr(analysis, 'z_min', None), getattr(analysis, 'z_max', None)
    supplied = None
    if low is not None and high is not None:
        a, b = float(low), float(high)
        if math.isfinite(a) and math.isfinite(b) and a <= b:
            supplied = (a, b)
    shade, dem = build_terrain_layers(dem_path, elevation_range=supplied)
    resolution = f'{analysis.cell_width:g}x{analysis.cell_height:g}m'
    dem.setName(f'MDT {resolution}')
    shade.setName(f'Relieve sombreado MDT {resolution}')
    # The colour layer sits directly above the shadow. Its limited transparency
    # makes relief visible while the source DEM stays numerically unchanged.
    dem.setOpacity(0.86)
    return dem, shade

class DrainageLayers:
    """Own persistent result layers; clear only this instance explicitly."""
    def __init__(self,iface,owner_key=None):
        self.iface=iface;self.owner_key=str(owner_key) if owner_key is not None else uuid4().hex
        self.layers={};self._group=None;self.note=''

    def _ensure_group(self):
        from .layer_order import create_result_group, ensure_results_above_official
        ensure_results_above_official(QgsProject.instance())
        if self._group is None or sip.isdeleted(self._group):
            self._group=create_result_group(QgsProject.instance(),'HidroCAUMAX · Drenaje TauDEM',10)
            self._group.setCustomProperty(OWNER_PROPERTY,self.owner_key)
            self._group.setExpanded(True)
        return self._group

    def display_dem(self, analysis, dem_path):
        """Show a selected MDT immediately, before TauDEM has built a network.

        The temporary two-layer group becomes a four-layer result in update();
        the completed result starts with only the network and hillshade visible.
        The original TIFF is unchanged.
        """
        previous = dict(self.layers)
        old = previous.get('dem')
        if (old is not None and not sip.isdeleted(old)
                and len(previous) == 2
                and Path(old.source()).resolve() == Path(dem_path).resolve()
                and QgsProject.instance().mapLayer(old.id()) is old):
            return old
        dem, shade = _styled_mdt(analysis, dem_path)
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = self._ensure_group()
        group.setName(
            f'HidroCAUMAX · Drenaje TauDEM [{analysis.cell_width:g}×{analysis.cell_height:g} m]')
        for index, (kind, layer) in enumerate((('dem', dem), ('hillshade', shade))):
            layer.setCustomProperty(OWNER_PROPERTY, self.owner_key)
            layer.setCustomProperty(ROLE_PROPERTY, kind)
            project.addMapLayer(layer, False)
            node = group.insertLayer(index, layer)
            node.setItemVisibilityChecked(True)
            node.setExpanded(False)
        self.layers = {'dem': dem, 'hillshade': shade}
        for layer in previous.values():
            if (layer is not None and not sip.isdeleted(layer)
                    and layer.customProperty(OWNER_PROPERTY) == self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        from .layer_order import ensure_results_above_official
        ensure_results_above_official(project)
        return dem

    def update(self,analysis,state,visible=True,reset_presentation=False):
        """Register verified TauDEM products and the native-resolution MDT.

        Display order in the result group: drainage network, ESRI D8 raster,
        the actual elevation GeoTIFF (third), and its optional relief shading.
        The display uses the original raster without modifying elevation cells.
        """
        if state is None:
            self.remove()
            return {}
        network = build_network_layer(state)
        directions = _directions_layer(state.get('caumax_d8_path', ''))
        new = {'network': network, 'directions': directions}
        dem_path = state.get('dem_path')
        if dem_path:
            # Build first so an invalid MDT never removes a valid earlier result.
            dem, shade = _styled_mdt(analysis, dem_path)
            new.update(dem=dem, hillshade=shade)
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        previous = dict(self.layers)
        def last_visibility(kind, fallback):
            old = previous.get(kind)
            if old is not None and not sip.isdeleted(old):
                node = root.findLayer(old.id())
                if node is not None:
                    return bool(node.itemVisibilityChecked())
            return bool(fallback)

        flags = {
            'network': bool(state.get('network_visible', visible)),
            'directions': False if reset_presentation else bool(state.get('directions_visible', False)),
            # The preview is visible while TauDEM works, but its checkbox is
            # not an initial visibility preference for the completed result.
            'dem': (last_visibility('dem', False)
                    if not reset_presentation and 'network' in previous else False),
            'hillshade': True if reset_presentation else last_visibility('hillshade', True),
        }
        group = self._ensure_group()
        if reset_presentation:
            group.setItemVisibilityChecked(True)
            group.setExpanded(True)
        group.setName(
            f'HidroCAUMAX · Drenaje TauDEM [{analysis.cell_width:g}×{analysis.cell_height:g} m]')
        # Insert by final visual order, not by reversing a sequence of index 0
        # inserts. As long as both D8 products exist, MDT is precisely third.
        for index, kind in enumerate(('network', 'directions', 'dem', 'hillshade')):
            layer = new.get(kind)
            if layer is None:
                continue
            layer.setCustomProperty(OWNER_PROPERTY, self.owner_key)
            layer.setCustomProperty(ROLE_PROPERTY, kind)
            project.addMapLayer(layer, False)
            node = group.insertLayer(index, layer)
            node.setItemVisibilityChecked(flags[kind])
            node.setExpanded(False)
        self.layers = new
        for layer in previous.values():
            if (layer is not None and not sip.isdeleted(layer)
                    and layer.customProperty(OWNER_PROPERTY) == self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        self.note = ('' if directions is not None else
            'El TIFF D8 CAUMAX/ESRI no está disponible; la red archivada sigue recuperada.')
        from .layer_order import ensure_results_above_official
        ensure_results_above_official(project)
        return dict(self.layers)

    def visibility(self):
        result={'network':False,'directions':False};tree=QgsProject.instance().layerTreeRoot()
        for kind,layer in self.layers.items():
            if layer is not None and not sip.isdeleted(layer):
                node=tree.findLayer(layer.id())
                if node is not None:result[kind]=node.itemVisibilityChecked()
        return result

    def remove(self):
        project=QgsProject.instance()
        for layer in self.layers.values():
            if (layer is not None and not sip.isdeleted(layer)
                    and layer.customProperty(OWNER_PROPERTY)==self.owner_key
                    and project.mapLayer(layer.id()) is layer):
                project.removeMapLayer(layer.id())
        self.layers={}
        if self._group is not None and not sip.isdeleted(self._group) and not self._group.children():
            parent=self._group.parent()
            if parent is not None:parent.removeChildNode(self._group)
        self._group=None
