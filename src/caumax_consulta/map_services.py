"""Load reference maps and station points without changing the map or results.

The catalogue is deliberately explicit: WMS layer names and styles are server
identifiers, not translated labels. WMS images and the dated AEMET station map
are references, not inputs to calculations. Neither carries delineation ownership.
"""
from dataclasses import dataclass
from pathlib import Path

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer, Qt, pyqtSignal
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsDataSourceUri, QgsLayerTreeGroup, QgsProject,
                       QgsRasterLayer, QgsRectangle, QgsTask)


ROOT_TITLE = 'HidroCAUMAX · Capas de referencia'
ROOT_PROPERTY = 'HidroCAUMAX/reference_group'
SERVICE_PROPERTY = 'HidroCAUMAX/service_id'
LAYER_PROPERTY = 'HidroCAUMAX/layer_id'
FLAT_SERVICE_IDS = frozenset(('aemet', 'google_satellite', 'pfafstetter', 'corine'))


@dataclass(frozen=True)
class WmsLayerSpec:
    layer_id: str
    title: str
    name: str
    style: str = ''
    crs: str = 'EPSG:25830'
    image_format: str = 'image/png'
    visible: bool = True
    description: str = ''
    url: str = ''


@dataclass(frozen=True)
class WmsServiceSpec:
    service_id: str
    title: str
    url: str
    layers: tuple
    visible: bool = True
    exclusive: bool = False
    description: str = ''
    version: str = '1.3.0'


@dataclass(frozen=True)
class XyzLayerSpec:
    layer_id: str
    title: str
    visible: bool = False
    description: str = ''


@dataclass(frozen=True)
class XyzServiceSpec:
    service_id: str
    title: str
    url: str
    layers: tuple
    visible: bool = False
    exclusive: bool = False
    description: str = ''
    zmin: int = 0
    zmax: int = 19
    attribution: str = ''
    attribution_url: str = ''


@dataclass(frozen=True)
class StationLayerSpec:
    layer_id: str
    title: str
    visible: bool = True
    description: str = ''


@dataclass(frozen=True)
class StationServiceSpec:
    service_id: str
    title: str
    layers: tuple
    visible: bool = True
    exclusive: bool = False
    description: str = ''


@dataclass(frozen=True)
class LocalRasterServiceSpec:
    service_id: str
    title: str
    path: Path
    layers: tuple
    visible: bool = False
    exclusive: bool = False
    description: str = ''


_AGRO_LAYERS = (
    ('papadakis', 'Clasificación climáticos', 'Agricultura_ClimaticosClasificacion'),
    ('summer', 'Tipos de verano de la clasificación climática de Papadakis', 'Agricultura_Verano'),
    ('temperature_min', 'Temperatura mínima', 'Agricultura_TemperaturaMin'),
    ('temperature_mean', 'Temperatura media anual', 'Agricultura_TemperaturaMedia'),
    ('temperature_max', 'Temperatura máxima', 'Agricultura_TemperaturaMax'),
    ('thermal_regime', 'Regímenes térmicos según Papadakis', 'Agricultura_RegimenesTermicos'),
    ('moisture_regime', 'Regímenes de humedad', 'Agricultura_RegimenesHumedad'),
    ('rainfall', 'Pluviometría media anual', 'Agricultura_Pluviometria'),
    ('dry_period', 'Duración media del período seco', 'Agricultura_PeriodoSeco'),
    ('cold_period', 'Duración media del período frío', 'Agricultura_PeriodoFrio'),
    ('warm_period', 'Duración media del período cálido', 'Agricultura_PeriodoCalido'),
    ('winter', 'Tipos de invierno de la clasificación climática de Papadakis', 'Agricultura_Invierno'),
    ('r_factor', 'Factor R', 'Agricultura_FactorR'),
    ('evapotranspiration', 'Evapotranspiración', 'Agricultura_Evapotranspiracion'),
    ('turc_rainfed', 'Índice de potencialidad agrícola de índice Turc en secano', 'Agricultura_covtusec'),
    ('turc_irrigated', 'Índice potencialidad agrícola de índice Turc en regadío', 'Agricultura_covtureg'),
    ('aridity', 'Aridez', 'Agricultura_Aridez'),
)


WMS_SERVICES = (
    WmsServiceSpec('snczi', 'Peligrosidad de inundación · SNCZI',
        'https://servicios.idee.es/wms-inspire/riesgos-naturales/inundaciones',
        tuple(WmsLayerSpec(f'fluvial_t{period}', f'Calados T={period} años',
                          f'NZ.Flood.FluvialT{period}', visible=period == 100)
              for period in (10, 100, 500)), exclusive=True,
        description='Fuente: MITECO, Sistema Nacional de Cartografía de Zonas Inundables. '
                    'Calados de inundación fluvial, con la leyenda oficial en tonos azules. '
                    'Se muestra un escenario de período de retorno cada vez.'),
    WmsServiceSpec('pfafstetter', 'Clasificación Pfafstetter · tramos de ríos',
        'https://gis.miteco.gob.es/geoserver/agua/rios_pfaf_cx/wms',
        (WmsLayerSpec('river_segments', 'Tramos de ríos · Pfafstetter',
                      'rios_pfaf_cx', 'generic'),),
        description='Fuente: MITECO. Clasificación Pfafstetter de los tramos de ríos.'),
    WmsServiceSpec('agroclima', 'Caracterización agroclimática',
        'https://wms.mapama.gob.es/sig/Agricultura/CaractAgroClimaticas',
        tuple(WmsLayerSpec(key, 'Clasificación climática de Papadakis' if key == 'papadakis' else name,
                          name, style, visible=key == 'papadakis')
              for key, name, style in _AGRO_LAYERS), visible=False,
        description='Fuente: Ministerio de Agricultura, Pesca y Alimentación (MAPA). '
                    'Diecisiete mapas de caracterización agroclimática. Active el grupo '
                    'y seleccione la variable que desee consultar.'),
    WmsServiceSpec('igme', 'IGME · Mapa Hidrogeológico (1:1.000.000)',
        'https://mapas.igme.es/gis/services/Cartografia_Tematica/IGME_Hidrogeologico_1M/MapServer/WMSServer',
        (WmsLayerSpec('hydrogeological_units', 'Unidades hidrogeológicas', '2', 'default'),
         WmsLayerSpec('river_basins', 'Cuencas hidrográficas', '1', 'default'),
         WmsLayerSpec('hydrogeology', 'Hidrogeología 1:1.000.000', '0', 'default')),
        visible=False,
        description='Fuente: Instituto Geológico y Minero de España (IGME-CSIC). '
                    'Mapa Hidrogeológico de España a escala 1:1.000.000. Incluye '
                    'hidrogeología, cuencas hidrográficas y unidades hidrogeológicas. '
                    'El relleno hidrogeológico se muestra al acercarse más allá de '
                    'aproximadamente 1:1.890.000, según los límites de escala del servicio.'),
    WmsServiceSpec('corine', 'Corine Land Cover · Ocupación del suelo',
        'https://servicios.idee.es/wms-inspire/ocupacion-suelo',
        (WmsLayerSpec('land_cover', 'Corine Land Cover · Ocupación del suelo',
                     'LC.LandCoverSurfaces', 'LC.LandCoverSurfaces.Default'),),
        description='Fuente: IGN / Sistema Cartográfico Nacional de España (SCNE), CC BY 4.0. '
                    'Servicio multiescala: CORINE Land Cover 2018, SIOSE 2014 y '
                    'SIOSE de Alta Resolución 2017; la información cambia con la escala.'),
)


AEMET_SERVICE = StationServiceSpec('aemet', 'Red estaciones AEMET',
    (StationLayerSpec('precipitation_stations', 'Estaciones AEMET · precipitación (inventario histórico)'),))
FLOOD_EXTENT_SERVICE = WmsServiceSpec('snczi_extent', 'Extensión zonas inundables', '',
    tuple(WmsLayerSpec(f'extent_t{period}', title, f'Zi_laminas_q{period}',
        style=f'Zi_laminas_q{period}_estilo', crs='EPSG:25830', visible=False,
        url=f'https://gis.miteco.gob.es/geoserver/agua/Zi_laminas_q{period}/wms')
        for period, title in (
            (10, 'Z.I con alta probabilidad (T=10)'),
            (50, 'Z.I. de inundación frecuente (T=50)'),
            (100, 'Z.I. probabilidad media u ocasional (T=100)'),
            (500, 'Z.I. con probabilidad baja o excepcional (T=500)'))),
    visible=False,
    description='Fuente: MITECO, SNCZI. Extensión de zonas inundables de origen fluvial. '
                'Visualización oficial a partir de 1:1.000.000; acerca el mapa para consultar la zona. '
                'https://www.miteco.gob.es/es/cartografia-y-sig/ide/directorio_datos_servicios/agua.html')
GOOGLE_SERVICE = XyzServiceSpec('google_satellite', 'Google Satellite',
    'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    (XyzLayerSpec('satellite', 'Google Satellite'),),
    description='Imágenes de satélite de Google. © Google y proveedores de imágenes. '
                'Fuente: https://maps.google.com',
    attribution='© Google y proveedores de imágenes', attribution_url='https://maps.google.com')
PO_TITLE = 'Po España (CLC2000) 100mx100m · AdrLBallesteros'
PO_SERVICE = LocalRasterServiceSpec('po_clc2000', PO_TITLE,
    Path(__file__).resolve().parent / 'ResolucionMejorada' / 'Po_Spain_CLC2000_100x100_30N_AdrLBallesteros.tif',
    (StationLayerSpec('runoff_threshold', PO_TITLE, visible=False),),
    description='Umbral de escorrentía Po · España · CLC2000 · AdrLBallesteros. '
                'Ráster local de 100 × 100 m, ETRS89 / UTM huso 30N (EPSG:25830), '
                'incluido en ResolucionMejorada. Se propone como P0 al configurar las capas de cálculo; '
                'cargar su referencia en el mapa no cambia la configuración.')
REFERENCE_SERVICES = ((AEMET_SERVICE, FLOOD_EXTENT_SERVICE, WMS_SERVICES[0], GOOGLE_SERVICE)
                      + WMS_SERVICES[1:] + (PO_SERVICE,))


def build_wms_uri(service, layer):
    """Encode Unicode, spaces, empty styles and nested URL query parameters."""
    uri = QgsDataSourceUri()
    for key, value in (('url', layer.url or service.url), ('layers', layer.name),
                       ('styles', layer.style), ('crs', layer.crs),
                       ('format', layer.image_format), ('version', service.version),
                       ('transparent', 'true')):
        uri.setParam(key, value)
    return bytes(uri.encodedUri()).decode('utf-8')


def build_xyz_uri(service):
    uri = QgsDataSourceUri()
    for key, value in (('type', 'xyz'), ('url', service.url), ('crs', 'EPSG:3857'),
                       ('zmin', str(service.zmin)), ('zmax', str(service.zmax))):
        uri.setParam(key, value)
    return bytes(uri.encodedUri()).decode('utf-8')


@dataclass(frozen=True)
class LayerFailure:
    service_id: str
    layer_id: str
    title: str
    detail: str


@dataclass(frozen=True)
class LoadReport:
    added: int
    existing: int
    failures: tuple = ()

    @property
    def failed(self):
        return len(self.failures)

    @property
    def message(self):
        text = f'Capas de referencia añadidas: {self.added}; ya presentes: {self.existing}.'
        if self.failures:
            titles = list(dict.fromkeys(failure.title for failure in self.failures))
            text += f' Sin cargar: {self.failed} ({"; ".join(titles)}).'
        return text


def _owned_layers(project):
    return {(str(layer.customProperty(SERVICE_PROPERTY, '')),
             str(layer.customProperty(LAYER_PROPERTY, ''))): layer
            for layer in project.mapLayers().values()
            if layer.customProperty(SERVICE_PROPERTY, '') and layer.customProperty(LAYER_PROPERTY, '')}


def _pending_layers(project, services):
    existing = _owned_layers(project)
    pending, failures, present = [], [], 0
    for service in services:
        missing = []
        for spec in service.layers:
            layer = existing.get((service.service_id, spec.layer_id))
            if layer is None:
                missing.append(spec)
            elif layer.isValid():
                present += 1
            else:
                failures.append(LayerFailure(service.service_id, spec.layer_id, service.title,
                                             'La capa ya está en el proyecto, pero no es válida.'))
        if missing:
            pending.append((service, tuple(missing)))
    return pending, present, failures


def _prepare_layers(pending, layer_factory, cancelled=lambda: False, progress=lambda *args: None,
                    station_factory=None):
    """Create unregistered layers. No project or canvas is accessed here."""
    prepared, failures = [], []
    total = sum(len(layers) for _, layers in pending)
    completed = 0
    for service, specifications in pending:
        unavailable, previous_endpoint = '', None
        for spec in specifications:
            if cancelled():
                break
            progress(spec.title, completed, total)
            endpoint = getattr(spec, 'url', '') or getattr(service, 'url', '')
            if endpoint != previous_endpoint:
                unavailable, previous_endpoint = '', endpoint
            layer = None
            error = unavailable
            if not unavailable:
                try:
                    if isinstance(service, LocalRasterServiceSpec):
                        layer = layer_factory(str(service.path), spec.title, 'gdal')
                    elif isinstance(service, StationServiceSpec):
                        if station_factory is None:
                            from .aemet_stations import create_station_layer
                            layer = create_station_layer(spec.title, cancelled=cancelled)
                        else:
                            layer = station_factory(spec.title, cancelled=cancelled)
                    else:
                        uri = build_xyz_uri(service) if isinstance(service, XyzServiceSpec) else build_wms_uri(service, spec)
                        layer = layer_factory(uri, spec.title, 'wms')
                    if not layer.isValid():
                        error = layer.error().summary() or 'La fuente de referencia no está disponible.'
                    else:
                        if service.service_id == 'po_clc2000':
                            from .po_style import apply_po_style
                            apply_po_style(layer)
                        layer.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                        layer.setCustomProperty(LAYER_PROPERTY, spec.layer_id)
                        metadata = layer.metadata()
                        content = ('Lectura local de la zona visible a la escala del mapa.'
                            if isinstance(service, LocalRasterServiceSpec) else
                            metadata.abstract() if isinstance(service, StationServiceSpec) else (
                            ('Visualización mediante teselas XYZ en línea; ' if isinstance(service, XyzServiceSpec)
                             else 'Visualización mediante WMS en línea; ') +
                            'requiere conexión y no descarga un ráster local ni aporta datos al cálculo hidrológico.'))
                        description = ' '.join(filter(None, (service.description, spec.description, content)))
                        metadata.setTitle(spec.title)
                        metadata.setAbstract(description)
                        layer.setMetadata(metadata)
                        if isinstance(service, XyzServiceSpec):
                            layer.setAttribution(service.attribution)
                            layer.setAttributionUrl(service.attribution_url)
                        prepared.append((service, spec, layer))
                except Exception as exception:
                    error = str(exception) or type(exception).__name__
                if error:
                    # Avoid seventeen repeated network timeouts on one failed
                    # source. Other sources are still attempted independently.
                    unavailable = error
                    if layer is not None:
                        sip.delete(layer)
            if error:
                failures.append(LayerFailure(service.service_id, spec.layer_id, service.title, error))
            completed += 1
            progress(spec.title, completed, total)
        if cancelled():
            break
    return prepared, failures


def _collapse_service_children(node, expanded):
    if (expanded and isinstance(node, QgsLayerTreeGroup)
            and node.customProperty(SERVICE_PROPERTY, '') in ('agroclima', 'snczi')):
        for child in node.children():
            child.setExpanded(False)


def _bind_collapsed_groups(group):
    for node in group.findGroups():
        if node.customProperty(SERVICE_PROPERTY, '') not in ('agroclima', 'snczi'):
            continue
        previous = getattr(node, '_hidrocaumax_collapse_slot', None)
        if previous is _collapse_service_children:
            continue
        if previous is not None:
            try:
                node.expandedChanged.disconnect(previous)
            except (TypeError, RuntimeError):
                pass
        try:
            node.expandedChanged.connect(_collapse_service_children, Qt.UniqueConnection)
            node._hidrocaumax_collapse_slot = _collapse_service_children
        except TypeError:
            pass


def _root_group(project, create=True):
    """Place the reference block immediately above CAUMAX_CAPAS at root level."""
    root = project.layerTreeRoot()
    group = next((item for item in root.findGroups(recursive=True)
                  if item.customProperty(ROOT_PROPERTY, '') == 'v1'), None)
    if group is None and not create:
        return None
    official = root.findGroup('CAUMAX_CAPAS')
    while official is not None and official.parent() is not root:
        official = official.parent()
    if official is group:
        official = None
    target = root
    position = target.children().index(official) if official is not None else 0
    placed = (group is not None and group.parent() is target and
              target.children().index(group) == (position - 1 if official is not None else 0))
    current = list(root.customLayerOrder()) if root.hasCustomLayerOrder() else None
    if group is None:
        group = target.insertGroup(position, ROOT_TITLE)
        group.setCustomProperty(ROOT_PROPERTY, 'v1')
        group.setExpanded(True)
    elif not placed:
        # Keep a clone in the tree while detaching: the registry bridge must
        # never interpret this move as deletion of the registered map layers.
        descendants, exclusive_groups = [], []
        def remember(parent):
            children = list(parent.children())
            if isinstance(parent, QgsLayerTreeGroup) and parent.isMutuallyExclusive():
                selected = next((index for index, child in enumerate(children)
                                 if child.itemVisibilityChecked()), -1)
                exclusive_groups.append((parent, selected, parent.itemVisibilityChecked()))
            if children:
                descendants.append((parent, children))
                for child in children:
                    remember(child)
        remember(group)
        placeholder = group.clone()
        target.insertChildNode(position, placeholder)
        for parent, _, _ in exclusive_groups:
            parent.setIsMutuallyExclusive(False)
        group.parent().takeChild(group)
        for parent, children in reversed(descendants):
            parent.insertChildNodes(0, children)
        target.insertChildNode(target.children().index(placeholder), group)
        for parent, selected, checked in exclusive_groups:
            parent.setIsMutuallyExclusive(True, selected)
            parent.setItemVisibilityChecked(checked)
        target.removeChildNode(placeholder)
    # Upgrade existing projects as well as new loads, retaining layer IDs,
    # styles and effective visibility when removing a disabled wrapper.
    for wrapper in list(group.children()):
        service_id = wrapper.customProperty(SERVICE_PROPERTY, '')
        if not isinstance(wrapper, QgsLayerTreeGroup) or service_id not in FLAT_SERVICE_IDS:
            continue
        for node in list(wrapper.children()):
            layer = node.layer() if hasattr(node, 'layer') else None
            if layer is None or layer.customProperty(SERVICE_PROPERTY, '') != service_id:
                continue
            visible = node.isVisible()
            placeholder = node.clone()
            group.insertChildNode(group.children().index(wrapper), placeholder)
            wrapper.takeChild(node)
            group.insertChildNode(group.children().index(placeholder), node)
            group.removeChildNode(placeholder)
            node.setCustomProperty(SERVICE_PROPERTY, service_id)
            node.setItemVisibilityChecked(visible)
            node.setExpanded(False)
        if not wrapper.children():
            group.removeChildNode(wrapper)
    if current is not None:
        reference_ids = {node.layerId() for node in group.findLayers()}
        references = [layer for layer in current if layer.id() in reference_ids]
        others = [layer for layer in current if layer.id() not in reference_ids]
        native_ids = ({node.layerId() for node in official.findLayers()} - reference_ids
                      if official is not None else set())
        index = (0 if official is None else
                 next((i for i, layer in enumerate(others) if layer.id() in native_ids), None))
        if index is not None:
            updated = others[:index] + references + others[index:]
            if root.customLayerOrder() != updated:
                root.setCustomLayerOrder(updated)
    _bind_collapsed_groups(group)
    for node in group.findLayers():
        layer = node.layer()
        if (layer is not None and layer.customProperty(SERVICE_PROPERTY, '') == 'po_clc2000'
                and not layer.customProperty('HidroCAUMAX/po_style', '')):
            from .po_style import apply_po_style
            apply_po_style(layer)
    from .native_layers_view import place_satellite_below_terrain
    place_satellite_below_terrain(project)
    return group


def _publish_layers(project, prepared, services, failures=()):
    """Publish on the GUI thread; preserve existing groups and visibility."""
    original_crs = QgsCoordinateReferenceSystem(project.crs())
    existing = _owned_layers(project)
    root, groups, created, added = _root_group(project, create=bool(prepared)), {}, set(), 0
    ordered = list(REFERENCE_SERVICES) + [item for item in services
        if item.service_id not in {known.service_id for known in REFERENCE_SERVICES}]
    order = {item.service_id: index for index, item in enumerate(ordered)}
    try:
        for service, spec, layer in prepared:
            key = service.service_id, spec.layer_id
            if key in existing:
                layer.deleteLater()
                continue
            if root is None:
                root = _root_group(project)
            if service.service_id == 'google_satellite':
                project.addMapLayer(layer, False)
                node = project.layerTreeRoot().addLayer(layer)
                node.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                node.setItemVisibilityChecked(False)
                node.setExpanded(False)
                existing[key] = layer
                added += 1
                continue
            if isinstance(service, LocalRasterServiceSpec) or service.service_id in FLAT_SERVICE_IDS:
                project.addMapLayer(layer, False)
                position = next((index for index, node in enumerate(root.children())
                    if order.get(str(node.customProperty(SERVICE_PROPERTY, '')), len(order))
                    > order[service.service_id]), len(root.children()))
                node = root.insertLayer(position, layer)
                node.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                node.setItemVisibilityChecked(False)
                node.setExpanded(False)
                existing[key] = layer
                added += 1
                continue
            group = groups.get(service.service_id)
            if group is None:
                group = next((node for node in root.children()
                              if isinstance(node, QgsLayerTreeGroup)
                              and node.customProperty(SERVICE_PROPERTY, '') == service.service_id), None)
                if group is None:
                    position = next((index for index, node in enumerate(root.children())
                        if order.get(str(node.customProperty(SERVICE_PROPERTY, '')), len(order))
                        > order[service.service_id]), len(root.children()))
                    group = root.insertGroup(position, service.title)
                    group.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                    group.setExpanded(False)
                    group.setItemVisibilityChecked(False)
                    created.add(service.service_id)
                groups[service.service_id] = group
            project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            # Repeating the command does not change the user's selected scene.
            node.setItemVisibilityChecked(False)
            node.setExpanded(False)
            existing[key] = layer
            added += 1
        for service in services:
            if service.exclusive and service.service_id in created:
                group = groups[service.service_id]
                selected = next((index for index, node in enumerate(group.children())
                                 if node.itemVisibilityChecked()), -1)
                group.setIsMutuallyExclusive(True, selected)
        # Include newly added references in custom drawing order as well.
        _root_group(project, create=False)
    finally:
        if project.crs() != original_crs:
            project.setCrs(original_crs)
    valid = sum(1 for service in services for spec in service.layers
                if (service.service_id, spec.layer_id) in existing
                and existing[(service.service_id, spec.layer_id)].isValid())
    return LoadReport(added, valid - added, tuple(failures))


def load_reference_layers(project, services=REFERENCE_SERVICES, layer_factory=QgsRasterLayer,
                          station_factory=None):
    """Synchronous core for headless callers. The plugin uses the task below."""
    pending, _, failures = _pending_layers(project, services)
    prepared, errors = _prepare_layers(pending, layer_factory, station_factory=station_factory)
    return _publish_layers(project, prepared, services, failures + errors)


class _ReferenceTask(QgsTask):
    layer_progress = pyqtSignal(str, int, int)

    def __init__(self, pending, layer_factory, callback, station_factory=None):
        super().__init__('Cargar capas de referencia HidroCAUMAX', QgsTask.CanCancel)
        self.pending, self.layer_factory, self.callback = pending, layer_factory, callback
        self.station_factory = station_factory
        self.prepared, self.failures = [], []
        self.target_thread = QgsApplication.instance().thread()

    def _progress(self, title, done, total):
        self.layer_progress.emit(title, done, total)
        self.setProgress(100. * done / max(total, 1))

    def run(self):
        self.prepared, self.failures = _prepare_layers(
            self.pending, self.layer_factory, self.isCanceled, self._progress, self.station_factory)
        # A provider is created and used only in its worker thread until this
        # transfer. Publication and later map rendering belong to the GUI.
        for _, _, layer in self.prepared:
            layer.moveToThread(self.target_thread)
        return not self.isCanceled()

    def finished(self, success):
        callback, self.callback = self.callback, None
        if callback is not None:
            callback(self, success)


class ReferenceLayerLoader(QObject):
    busy_changed = pyqtSignal(bool)

    def __init__(self, iface, parent=None, *, services=REFERENCE_SERVICES,
                 project=None, layer_factory=QgsRasterLayer, station_factory=None):
        super().__init__(parent)
        self.iface, self.services, self.layer_factory = iface, tuple(services), layer_factory
        self.station_factory = station_factory
        self.project = QgsProject.instance() if project is None else project
        self._tasks, self._retiring, self._message = set(), set(), None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(45000)
        self._timeout.timeout.connect(self._timed_out)
        self._added = self._present = 0
        self._failures = []
        self._disposed = False
        self.project.cleared.connect(self.cancel)

    @property
    def busy(self):
        return bool(self._tasks)

    def _bar(self):
        try:
            return self.iface.messageBar()
        except (AttributeError, RuntimeError):
            return None

    def _remove_message(self):
        bar = self._bar()
        if bar is not None and self._message is not None and not sip.isdeleted(self._message):
            bar.popWidget(self._message)
        self._message = None

    def load(self, *unused):
        if self._disposed or self.busy:
            return False
        pending, self._present, self._failures = _pending_layers(self.project, self.services)
        self._added = 0
        # A cancelled provider may still be leaving a blocking network call.
        # Do not pile up duplicate tasks on repeated clicks while it exits.
        retiring = {service.service_id for task in self._retiring for service, _ in task.pending}
        for service, specs in pending:
            if service.service_id in retiring:
                self._failures.extend(LayerFailure(service.service_id, spec.layer_id, service.title,
                    'La solicitud anterior todavía está terminando. Reintenta en unos segundos.') for spec in specs)
        pending = [(service, specs) for service, specs in pending if service.service_id not in retiring]
        if not pending:
            self._report(self._publish_preserving_view([], self._failures))
            return True
        # Independent services publish as soon as ready. No national image is
        # downloaded here: native WMS/XYZ providers render the current canvas
        # BBOX, image size and scale when the user enables a layer.
        for service in pending:
            task = _ReferenceTask([service], self.layer_factory, self._finished, self.station_factory)
            task.layer_progress.connect(self._progress)
            self._tasks.add(task)
        self.busy_changed.emit(True)
        bar = self._bar()
        if bar is not None:
            # pushMessage returns None in QGIS: keep the actual item so it can
            # be updated and removed, including when another message covers it.
            item = bar.createMessage('HidroCAUMAX', 'Preparando referencias; las capas se activan en el panel de QGIS…')
            self._message = bar.pushWidget(item, Qgis.Info, 0)
        self._timeout.start()
        for task in tuple(self._tasks):
            QgsApplication.taskManager().addTask(task)
        return True

    def _progress(self, title, done, total):
        if not self._disposed and self.sender() in self._tasks:
            if self._message is not None and not sip.isdeleted(self._message):
                self._message.setText(f'Preparando: {title} ({done}/{total}). Servicios pendientes: {len(self._tasks)}.')

    def _publish_preserving_view(self, prepared, failures):
        bridge = getattr(self.iface, 'layerTreeCanvasBridge', lambda: None)()
        canvas = getattr(self.iface, 'mapCanvas', lambda: None)()
        extent = QgsRectangle(canvas.extent()) if canvas is not None else None
        crs = QgsCoordinateReferenceSystem(canvas.mapSettings().destinationCrs()) if canvas is not None else None
        automatic = bridge.autoSetupOnFirstLayer() if bridge is not None else None
        if bridge is not None:
            bridge.setAutoSetupOnFirstLayer(False)
        try:
            result = _publish_layers(self.project, prepared, self.services, failures)
            if bridge is not None:
                bridge.setCanvasLayers()
            if canvas is not None:
                if canvas.mapSettings().destinationCrs() != crs:
                    canvas.setDestinationCrs(crs)
                if canvas.extent() != extent:
                    canvas.setExtent(extent)
            return result
        finally:
            if bridge is not None:
                bridge.setAutoSetupOnFirstLayer(automatic)

    def _finished(self, task, success):
        self._retiring.discard(task)
        if self._disposed or task not in self._tasks:
            for _, _, layer in task.prepared:
                layer.deleteLater()
            return
        self._tasks.remove(task)
        try:
            if success:
                report = self._publish_preserving_view(task.prepared, task.failures)
                self._added += report.added
                self._failures.extend(report.failures)
            else:
                for _, _, layer in task.prepared:
                    layer.deleteLater()
                self._failures.extend(LayerFailure(service.service_id, spec.layer_id, service.title,
                    'Preparación cancelada o interrumpida.') for service, specs in task.pending for spec in specs)
        except Exception as error:
            self._failures.extend(LayerFailure(service.service_id, spec.layer_id, service.title,
                str(error)) for service, specs in task.pending for spec in specs)
        finally:
            if not self._tasks:
                self._timeout.stop()
                self._remove_message()
                self._report(LoadReport(self._added, self._present, tuple(self._failures)))
                self.busy_changed.emit(False)

    def _timed_out(self):
        for task in self._tasks:
            self._failures.extend(LayerFailure(service.service_id, spec.layer_id, service.title,
                'Tiempo de espera agotado al preparar el servicio. Puedes reintentar su carga.')
                for service, specs in task.pending for spec in specs)
        self.cancel()
        self._report(LoadReport(self._added, self._present, tuple(self._failures)))

    def _report(self, report):
        bar = self._bar()
        if bar is not None:
            level = Qgis.Warning if report.failed else Qgis.Success if report.added else Qgis.Info
            bar.pushMessage('HidroCAUMAX', report.message, level=level,
                            duration=20 if report.failed else 8)
        logged = set()
        for failure in report.failures:
            if (failure.service_id, failure.detail) in logged:
                continue
            logged.add((failure.service_id, failure.detail))
            from qgis.core import QgsMessageLog
            QgsMessageLog.logMessage(f'{failure.title}: {failure.detail}', 'HidroCAUMAX', Qgis.Warning)

    def cancel(self):
        tasks, self._tasks = self._tasks, set()
        self._timeout.stop()
        self._retiring.update(tasks)
        for task in tasks:
            task.cancel()
        self._remove_message()
        if tasks:
            self.busy_changed.emit(False)

    def dispose(self):
        if self._disposed:
            return
        self.cancel()
        for node in self.project.layerTreeRoot().findGroups():
            callback = getattr(node, '_hidrocaumax_collapse_slot', None)
            if callback is not None:
                try:
                    node.expandedChanged.disconnect(callback)
                    del node._hidrocaumax_collapse_slot
                except (TypeError, RuntimeError):
                    pass
        self._disposed = True
        try:
            self.project.cleared.disconnect(self.cancel)
        except (TypeError, RuntimeError):
            pass
