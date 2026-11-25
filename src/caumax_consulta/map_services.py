"""Load reference maps and station points without changing the map or results.

The catalogue is deliberately explicit: WMS layer names and styles are server
identifiers, not translated labels. WMS images and the dated AEMET station map
are references, not inputs to calculations. Neither carries delineation ownership.
"""
from dataclasses import dataclass

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem,
                       QgsDataSourceUri, QgsLayerTreeGroup, QgsProject,
                       QgsRasterLayer, QgsRectangle, QgsTask)


ROOT_TITLE = 'HidroCAUMAX · Capas de referencia'
ROOT_PROPERTY = 'HidroCAUMAX/reference_group'
SERVICE_PROPERTY = 'HidroCAUMAX/service_id'
LAYER_PROPERTY = 'HidroCAUMAX/layer_id'


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
REFERENCE_SERVICES = (AEMET_SERVICE,) + WMS_SERVICES


def build_wms_uri(service, layer):
    """Encode Unicode, spaces, empty styles and nested URL query parameters."""
    uri = QgsDataSourceUri()
    for key, value in (('url', service.url), ('layers', layer.name),
                       ('styles', layer.style), ('crs', layer.crs),
                       ('format', layer.image_format), ('version', service.version),
                       ('transparent', 'true')):
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
        unavailable = ''
        for spec in specifications:
            if cancelled():
                break
            progress(spec.title, completed, total)
            layer = None
            error = unavailable
            if not unavailable:
                try:
                    if isinstance(service, StationServiceSpec):
                        if station_factory is None:
                            from .aemet_stations import create_station_layer
                            layer = create_station_layer(spec.title, cancelled=cancelled)
                        else:
                            layer = station_factory(spec.title, cancelled=cancelled)
                    else:
                        layer = layer_factory(build_wms_uri(service, spec), spec.title, 'wms')
                    if not layer.isValid():
                        error = layer.error().summary() or 'La fuente de referencia no está disponible.'
                    else:
                        layer.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                        layer.setCustomProperty(LAYER_PROPERTY, spec.layer_id)
                        metadata = layer.metadata()
                        content = metadata.abstract() if isinstance(service, StationServiceSpec) else (
                            'Visualización mediante WMS en línea; requiere conexión y no descarga '
                            'un ráster local ni aporta datos al cálculo hidrológico.')
                        description = ' '.join(filter(None, (service.description, spec.description, content)))
                        metadata.setTitle(spec.title)
                        metadata.setAbstract(description)
                        layer.setMetadata(metadata)
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


def _root_group(project):
    root = project.layerTreeRoot()
    for group in root.findGroups(recursive=True):
        if group.customProperty(ROOT_PROPERTY, '') == 'v1':
            return group
    # Appending keeps all existing result and user layers above the references.
    group = root.addGroup(ROOT_TITLE)
    group.setCustomProperty(ROOT_PROPERTY, 'v1')
    group.setExpanded(True)
    return group


def _publish_layers(project, prepared, services, failures=()):
    """Publish on the GUI thread; preserve existing groups and visibility."""
    original_crs = QgsCoordinateReferenceSystem(project.crs())
    existing = _owned_layers(project)
    root, groups, created, added = None, {}, set(), 0
    try:
        for service, spec, layer in prepared:
            key = service.service_id, spec.layer_id
            if key in existing:
                layer.deleteLater()
                continue
            if root is None:
                root = _root_group(project)
            group = groups.get(service.service_id)
            if group is None:
                group = next((node for node in root.children()
                              if isinstance(node, QgsLayerTreeGroup)
                              and node.customProperty(SERVICE_PROPERTY, '') == service.service_id), None)
                if group is None:
                    order = {item.service_id: index for index, item in enumerate(services)}
                    position = next((index for index, node in enumerate(root.children())
                        if order.get(str(node.customProperty(SERVICE_PROPERTY, '')), len(order))
                        > order[service.service_id]), len(root.children()))
                    group = root.insertGroup(position, service.title)
                    group.setCustomProperty(SERVICE_PROPERTY, service.service_id)
                    group.setExpanded(False)
                    group.setItemVisibilityChecked(service.visible)
                    created.add(service.service_id)
                groups[service.service_id] = group
            project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            # Repeating the command does not change the user's selected scene.
            node.setItemVisibilityChecked(spec.visible if service.service_id in created else False)
            existing[key] = layer
            added += 1
        for service in services:
            if service.exclusive and service.service_id in created:
                group = groups[service.service_id]
                selected = next((index for index, node in enumerate(group.children())
                                 if node.itemVisibilityChecked()), -1)
                group.setIsMutuallyExclusive(True, selected)
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
        self._task, self._message = None, None
        self._failures = []
        self._disposed = False
        self.project.cleared.connect(self.cancel)

    @property
    def busy(self):
        return self._task is not None

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
        pending, present, self._failures = _pending_layers(self.project, self.services)
        if not pending:
            self._report(LoadReport(0, present, tuple(self._failures)))
            return True
        task = _ReferenceTask(pending, self.layer_factory, self._finished, self.station_factory)
        task.layer_progress.connect(self._progress)
        self._task = task
        self.busy_changed.emit(True)
        bar = self._bar()
        if bar is not None:
            self._message = bar.pushMessage('HidroCAUMAX', 'Cargando capas de referencia…',
                                           level=Qgis.Info, duration=0)
        QgsApplication.taskManager().addTask(task)
        return True

    def _progress(self, title, done, total):
        if not self._disposed and self._task is not None and self.sender() is self._task:
            if self._message is not None and not sip.isdeleted(self._message):
                self._message.setText(f'Cargando referencias ({done}/{total}): {title}')

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
        if self._disposed or task is not self._task or not success:
            for _, _, layer in task.prepared:
                layer.deleteLater()
            if task is self._task:
                self._task = None
                self._remove_message()
                self.busy_changed.emit(False)
            return
        self._task = None
        self._remove_message()
        try:
            report = self._publish_preserving_view(task.prepared, self._failures + task.failures)
            self._report(report)
        finally:
            self.busy_changed.emit(False)

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
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            self._remove_message()
            self.busy_changed.emit(False)

    def dispose(self):
        if self._disposed:
            return
        self.cancel()
        self._disposed = True
        try:
            self.project.cleared.disconnect(self.cancel)
        except (TypeError, RuntimeError):
            pass
