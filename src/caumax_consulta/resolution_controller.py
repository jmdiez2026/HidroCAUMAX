"""Asynchronous discovery and analysis of real, native-resolution DEMs.

Only a QgsTask's run method performs downloads/GDAL/hydrology work. Public
callbacks are delivered by QObject slots or QgsTask.finished on the Qt thread.
The native caudal query remains unchanged apart from its DEM/direction paths.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import math
from pathlib import Path
import tempfile
import time

from osgeo import gdal, ogr, osr
from qgis.PyQt.QtCore import QObject, pyqtSignal, pyqtSlot
from qgis.core import QgsApplication, QgsTask


_STANDARD_RESOLUTIONS=(25.0,5.0,2.0,.5)


def _key(resolution):
    number=float(resolution)
    if not math.isfinite(number) or number<=0:
        raise ValueError('La resolución del MDT debe ser positiva y finita.')
    return format(number,'.12g')


def _reference_area_m2(analysis):
    """Keep the original 500 m reference when its outlet was chosen manually."""
    diagnostic=analysis.diagnostics or {}
    snap=diagnostic.get('outlet_snap') or {}
    if (min(analysis.cell_width,analysis.cell_height)==500.0
            and snap.get('method')=='manual_cell_d8'
            and snap.get('selection_mode')=='user_selected_cell'
            and snap.get('explicit_user_choice') is True):
        area=float(snap['reference_area_m2'])
    else:
        area=float((diagnostic.get('analysis_surface') or {}).get(
            'original_area_m2',analysis.area_m2))
    if not math.isfinite(area) or area<=0:
        raise ValueError('El área original de referencia debe ser positiva y finita.')
    return area


def _discover_resolutions(bounds,crs_wkt,cancelled,progress):
    from .dem_provider import discover_resolutions
    return discover_resolutions(bounds,crs_wkt,cancelled=cancelled,progress=progress)


def _download_dem(option,bounds,crs_wkt,destination,cancelled,progress):
    from .dem_provider import download_dem
    return download_dem(option,bounds,crs_wkt,destination,
                        cancelled=cancelled,progress=progress)


def _analyze_dem(path,x,y,cache_dir,cancelled,progress,**snap_options):
    from .dem_hydrology import analyze_dem
    return analyze_dem(path,x,y,cache_dir,cancelled=cancelled,progress=progress,**snap_options)


def _manual_analysis(context,x,y,cancelled,publish):
    from .manual_outlet import build_manual_outlet
    return build_manual_outlet(context,x,y,cancelled=cancelled,
        publish=lambda percent:publish('Recalculando desde el cierre elegido',percent))


def _engine_available():
    from .dem_hydrology import availability_engine
    return availability_engine()


def _check(cancelled):
    if cancelled():
        raise InterruptedError('La operación de resolución del MDT se ha cancelado.')


def _buffered_bounds(analysis):
    reference=osr.SpatialReference()
    if (reference.ImportFromWkt(str(analysis.crs_wkt))!=0 or not reference.IsProjected()
            or not math.isclose(reference.GetLinearUnits(),1.0,rel_tol=1e-8)):
        raise ValueError('La cuenca original necesita un CRS proyectado con unidades en metros.')
    geometry=ogr.CreateGeometryFromWkb(bytes(analysis.geometry_wkb))
    if geometry is None or geometry.IsEmpty():
        raise ValueError('La cuenca original no tiene una geometría válida.')
    xmin,xmax,ymin,ymax=geometry.GetEnvelope()
    original_x,original_y=float(analysis.selected_x),float(analysis.selected_y)
    if not all(math.isfinite(value) for value in (original_x,original_y)):
        raise ValueError('El punto original de consulta debe ser finito.')
    # A manual outlet can be far from the original query. Finer DEMs still
    # start from that exact query, so both must be covered by the download.
    xmin,xmax=min(xmin,original_x),max(xmax,original_x)
    ymin,ymax=min(ymin,original_y),max(ymax,original_y)
    padding=max(2000.0,.10*max(xmax-xmin,ymax-ymin))
    return (float(xmin-padding),float(ymin-padding),
            float(xmax+padding),float(ymax+padding))


def _enlarge(bounds):
    xmin,ymin,xmax,ymax=bounds
    cx,cy=(xmin+xmax)/2,(ymin+ymax)/2
    hx,hy=(xmax-xmin)*.75,(ymax-ymin)*.75
    return cx-hx,cy-hy,cx+hx,cy+hy


def _dem_point(path,source_wkt,x,y):
    """Transform the exact selected native point; never snap it to a channel."""
    dataset=gdal.Open(str(path),gdal.GA_ReadOnly)
    if dataset is None:
        raise ValueError('El MDT descargado no se puede abrir.')
    target_wkt=dataset.GetProjectionRef()
    dataset=None
    source,target=osr.SpatialReference(),osr.SpatialReference()
    if source.ImportFromWkt(source_wkt)!=0 or target.ImportFromWkt(target_wkt)!=0:
        raise ValueError('No se puede transformar el cierre al CRS del MDT.')
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    point=osr.CoordinateTransformation(source,target).TransformPoint(float(x),float(y))
    if not all(math.isfinite(v) for v in point[:2]):
        raise ValueError('La transformación del punto de cierre no es finita.')
    return float(point[0]),float(point[1])


def _build(option,bounds,basin_crs,source_crs,x,y,cache_root,snap_options,cancelled,publish):
    current=tuple(bounds)
    result=None
    for attempt in range(3):
        _check(cancelled)
        directory=Path(cache_root)/('extent_'+str(attempt))
        directory.mkdir(parents=True,exist_ok=True)
        label='MDT de '+_key(getattr(option,'resolution_m',getattr(option,'resolution',None)))+' m'
        publish(label+': descargando cobertura'+(' ampliada' if attempt else ''),0)
        path=_download_dem(option,current,basin_crs,directory/'mdt.tif',cancelled,
                           lambda p:publish(label+': descargando',float(p)*.45))
        _check(cancelled)
        native_x,native_y=_dem_point(path,source_crs,x,y)
        publish(label+': calculando drenaje y cuenca',45)
        result=_analyze_dem(path,native_x,native_y,directory/'hydrology',cancelled,
                            lambda p:publish(label+': calculando drenaje y cuenca',45+float(p)*.55),
                            **snap_options)
        _check(cancelled)
        if not result.analysis.touches_boundary:
            break
        if attempt<2:
            publish(label+': la cuenca alcanza el borde; ampliando la cobertura',100)
            current=_enlarge(current)
    analysis=result.analysis
    metadata=dict(getattr(result,'metadata',{}) or {})
    metadata.update(download_bounds=current,coverage_attempts=attempt+1,
                    native_resolution=float(getattr(option,'resolution_m',getattr(option,'resolution',None))))
    snap=(getattr(analysis,'diagnostics',{}) or {}).get('outlet_snap')
    if snap:
        metadata['outlet_snap']=deepcopy(snap)
    if analysis.touches_boundary:
        note=('La cuenca sigue alcanzando el borde del MDT tras dos ampliaciones de cobertura. '
              'La divisoria puede estar truncada; compruebe la extensión antes de utilizarla.')
        analysis=replace(analysis,notes=tuple(analysis.notes)+(note,))
        metadata['coverage_warning']=note
    publish('Análisis de '+label+' terminado',100)
    return {'analysis':analysis,'directions_path':str(result.directions_path),
            'dem_path':str(result.dem_path),'metadata':metadata}


class _WorkTask(QgsTask):
    update=pyqtSignal(int,str,float)

    def __init__(self,generation,title,operation,callback,kind,key=None):
        super().__init__(title,QgsTask.CanCancel)
        self.generation,self.operation,self.callback=generation,operation,callback
        self.kind,self.key=kind,key
        self.payload=None
        self.error=''
        self.selection_context=None
        self._last_publish=0.0

    def publish(self,text,progress):
        _check(self.isCanceled)
        percent=max(0.0,min(100.0,float(progress)))
        self.setProgress(percent)
        now=time.monotonic()
        if percent in (0,100) or now-self._last_publish>=.05:
            self._last_publish=now
            self.update.emit(self.generation,str(text),percent)

    def run(self):
        try:
            self.payload=self.operation(self.isCanceled,self.publish)
            _check(self.isCanceled)
            return True
        except InterruptedError:
            return False
        except Exception as error:
            self.error=str(error)
            self.selection_context=deepcopy(getattr(error,'selection_context',None))
            return False

    def finished(self,success):
        self.callback(self,success)


class ResolutionController(QObject):
    """Coordinate native DEM variants without changing the visible one in bulk work.

    Callbacks: on_options(list[dict]), on_result(key,analysis,query),
    on_status(text), on_error(text). No callback is called during construction.
    ``active_key`` identifies the applied variant; ``requested_key`` identifies
    a pending selection. Store GUI smoothing state in variants[key] before
    switching; cached variants are returned without terrain recomputation.
    """
    def __init__(self,base_analysis,base_query,on_options=None,on_result=None,
                 on_status=None,on_error=None,parent=None):
        super().__init__(parent)
        self._on_options=on_options or (lambda options:None)
        self._on_result=on_result or (lambda key,analysis,query:None)
        self._on_status=on_status or (lambda text:None)
        self._on_error=on_error or (lambda text:None)
        self._disposed=False
        self._generation=0
        self._task=None
        self._ensure_queue=[]
        self._ensure_ready=None
        self.last_errors={}
        self.progress=0.0
        self.engine={}
        self.requested_key=None
        self.outlet_selection_context=None
        self._set_base(base_analysis,base_query)
        self.active_key=self._base_key
        self.variants={self._base_key:{'analysis':base_analysis,'query':base_query,
                                      'smoothing_state':None,'metadata':{'provider':'CAUMAX'}}}
        self.options=[self._base_option()]+[
            {'key':_key(r),'resolution':r,'label':f'MDT de {_key(r)} m',
             'provider':'','available':False,'status':'Pendiente de consultar cobertura',
             'metadata_url':'','url':''}
            for r in _STANDARD_RESOLUTIONS if _key(r)!=self._base_key]
        self._option_objects={}
        if parent is not None:
            parent.destroyed.connect(self.dispose)

    def _set_base(self,analysis,query):
        self.base_analysis,self.base_query=analysis,query
        self.reference_area_m2=_reference_area_m2(analysis)
        self._base_key=_key(min(analysis.cell_width,analysis.cell_height))
        self.bounds=_buffered_bounds(analysis)
        self.crs_wkt=str(analysis.crs_wkt)
        self._query_crs=query.crs.toWkt()
        self._query_x,self._query_y=float(query.x),float(query.y)
        self.snap_radius_m=max(500.0,math.hypot(analysis.cell_width,analysis.cell_height))
        self.snap_minimum_fraction=.9
        raw=(bytes(analysis.geometry_wkb)+self.crs_wkt.encode('utf-8')
             +repr((self._query_x,self._query_y,self._query_crs)).encode('utf-8'))
        # Temporary storage is writable both in ordinary QGIS and restricted
        # test hosts; portable consultations keep every computed variant.
        directory=tempfile.gettempdir()
        self.cache_dir=Path(directory)/'HidroCAUMAX'/'dem_variants'/hashlib.sha256(raw).hexdigest()[:20]

    def _base_option(self):
        return {'key':self._base_key,'resolution':float(self._base_key),
                'label':f'Original CAUMAX · {self._base_key} m','provider':'CAUMAX',
                'available':True,'status':'Cálculo original disponible','metadata_url':'','url':''}

    @property
    def busy(self):return self._task is not None

    def cancel(self):
        self._generation+=1
        task,self._task=self._task,None
        if task is not None:
            task.cancel()
        self._ensure_queue=[]
        self._ensure_ready=None
        self.requested_key=None
        self.outlet_selection_context=None

    def dispose(self,*args):
        if self._disposed:return
        self.cancel()
        self._disposed=True
        self._on_options=lambda options:None
        self._on_result=lambda key,analysis,query:None
        self._on_status=lambda text:None
        self._on_error=lambda text:None

    @pyqtSlot(int,str,float)
    def _status_update(self,generation,text,percent):
        if self._disposed or generation!=self._generation:return
        self.progress=percent
        self._on_status(f'{text} ({percent:.0f} %)')

    def _start(self,title,operation,kind,key=None):
        task=_WorkTask(self._generation,title,operation,self._finished,kind,key)
        task.update.connect(self._status_update)
        self._task=task
        QgsApplication.taskManager().addTask(task)

    def discover(self):
        if self._disposed:return False
        selection_context=deepcopy(self.outlet_selection_context)
        self.cancel()
        self.outlet_selection_context=selection_context
        bounds,crs=tuple(self.bounds),self.crs_wkt
        def operation(cancelled,publish):
            options=_discover_resolutions(bounds,crs,cancelled,
                      lambda p:publish('Consultando MDT disponibles',p))
            _check(cancelled)
            return {'options':list(options),'engine':dict(_engine_available())}
        self._on_status('Consultando cobertura de modelos del terreno…')
        self._start('CAUMAX: consultar resoluciones del MDT',operation,'discover')
        return True

    def _merge_options(self,payload):
        self.engine=dict(payload['engine'])
        original=self._base_option()
        by_key={item['key']:dict(item) for item in self.options}
        by_key[self._base_key]=original
        for source in payload['options']:
            resolution=float(getattr(source,'resolution_m',getattr(source,'resolution',float('nan'))))
            key=_key(resolution)
            if key==self._base_key:continue
            self._option_objects[key]=source
            available=bool(getattr(source,'available',False))
            status=str(getattr(source,'status',''))
            if available and not self.engine.get('available',False):
                available=False
                status='Motor hidrológico no disponible: '+str(self.engine.get('reason','GRASS no instalado'))
            by_key[key]={'key':key,'resolution':resolution,
                         'label':str(getattr(source,'title',f'MDT de {key} m')),
                         'provider':str(getattr(source,'provider','')),'available':available,
                         'status':status,'metadata_url':str(getattr(source,'metadata_url','') or ''),
                         'url':str(getattr(source,'url','') or ''),
                         'collection_id':str(getattr(source,'collection_id','') or ''),
                         'source_crs_wkt':str(getattr(source,'source_crs_wkt','') or '')}
        for key,item in by_key.items():
            if key in self.variants:
                item['available']=True
                if key!=self._base_key:item['status']='Análisis disponible en caché'
            elif key not in self._option_objects:
                item['available']=False
                item['status']='No se ha publicado una cobertura disponible para esta resolución'
        self.options=[by_key[self._base_key]]+sorted(
            (item for key,item in by_key.items() if key!=self._base_key),
            key=lambda item:item['resolution'],reverse=True)

    def select(self,key):
        if self._disposed:return False
        key=str(key)
        option=next((item for item in self.options if item['key']==key),None)
        if key in self.variants:
            self.cancel()
            self.active_key=key
            variant=self.variants[key]
            self._on_result(key,variant['analysis'],variant['query'])
            return True
        if option is None or not option['available'] or key not in self._option_objects:
            self._on_error('MDT '+key+' m: '+(option['status'] if option else 'resolución desconocida'))
            return False
        self.cancel()
        self.requested_key=key
        self._start_build(key,'select')
        return True

    def selection_context(self):
        """Return the unresolved DEM, or the active calculation, for user review."""
        if self.outlet_selection_context:
            return dict(deepcopy(self.outlet_selection_context),manual_selection_scope='raster_extent')
        variant=self.variants[self.active_key]
        analysis,query=variant['analysis'],variant['query']
        x,y=_dem_point(query.dem_path,self._query_crs,self._query_x,self._query_y)
        return dict(resolution_key=self.active_key,dem_path=str(query.dem_path),
            directions_path=str(query.directions_path),crs_wkt=analysis.crs_wkt,
            original_x=x,original_y=y,search_radius_m=self.snap_radius_m,
            initial_x=float(analysis.outlet_x),initial_y=float(analysis.outlet_y),
            manual_selection_scope='raster_extent',
            reference_area_m2=self.reference_area_m2,
            metadata=deepcopy(variant.get('metadata') or {}),
            reason='Elige el centro de cierre para esta resolución. Se recalculará toda la cuenca.')

    def select_outlet(self,context,x,y):
        """Apply an explicitly reviewed cell without changing the CAUMAX query."""
        if self._disposed:return False
        context=deepcopy(context)
        key=_key(context.get('resolution_key'))
        if not any(item['key']==key for item in self.options):
            raise ValueError('La resolución del cierre elegido ya no está disponible.')
        x,y=float(x),float(y)
        if not all(math.isfinite(value) for value in (x,y)):
            raise ValueError('Las coordenadas del cierre elegido no son válidas.')
        self.cancel()
        self.requested_key=key
        def operation(cancelled,publish):
            try:
                return _manual_analysis(context,x,y,cancelled,publish)
            except ValueError as error:
                error.selection_context=deepcopy(context)
                raise
        self._on_status('Preparando la cuenca desde el cierre elegido…')
        self._start('CAUMAX: cierre elegido por el usuario',operation,'select',key)
        return True

    def _start_build(self,key,kind):
        option=self._option_objects[key]
        snap_options={
            'reference_area_m2':self.reference_area_m2,
            'reference_cell_width':float(self.base_analysis.cell_width),
            'reference_cell_height':float(self.base_analysis.cell_height),
            'snap_enabled':float(key)<float(self._base_key),
            'snap_radius_m':float(self.snap_radius_m),
            'snap_minimum_fraction':float(self.snap_minimum_fraction),
        }
        args=(option,tuple(self.bounds),self.crs_wkt,self._query_crs,self._query_x,self._query_y,
              self.cache_dir/('resolution_'+key.replace('.','_')),snap_options)
        operation=lambda cancelled,publish:_build(*args,cancelled,publish)
        self._on_status('Preparando el MDT de '+key+' m…')
        self._start('CAUMAX: MDT de '+key+' m',operation,kind,key)

    def ensure_all(self,on_ready):
        if self._disposed:return False
        if not callable(on_ready):raise TypeError('on_ready debe ser una función.')
        self.cancel()
        self._ensure_ready=on_ready
        self._ensure_queue=[item['key'] for item in self.options
                            if item['available'] and item['key'] not in self.variants]
        self._next_bulk()
        return True

    def _next_bulk(self):
        if self._disposed:return
        if not self._ensure_queue:
            callback,self._ensure_ready=self._ensure_ready,None
            if callback is not None:callback(dict(self.variants))
            return
        key=self._ensure_queue.pop(0)
        if key not in self._option_objects:
            self._failed_key(key,'No se conservan metadatos de descarga para esta resolución.')
            self._next_bulk()
            return
        self._start_build(key,'ensure')

    def _failed_key(self,key,message):
        text='MDT '+key+' m: '+message
        self.last_errors[key]=text
        for item in self.options:
            if item['key']==key and key not in self.variants:
                item['available']=bool(self.outlet_selection_context)
                item['status']=text
        self._on_options(deepcopy(self.options))
        self._on_error(text)

    def _finished(self,task,success):
        try:
            self._apply_finished(task,success)
        except Exception as error:
            if not self._disposed and task.generation==self._generation:
                self._task=None
                self._on_error('No se pudo aplicar el resultado del MDT: '+str(error))

    def _apply_finished(self,task,success):
        if self._disposed or task.generation!=self._generation or task is not self._task:return
        self._task=None
        if task.kind=='discover':
            if success:
                self._merge_options(task.payload)
                self._on_options(deepcopy(self.options))
                self._on_status('Consulta de resoluciones terminada.')
            elif task.error:
                self._on_options(deepcopy(self.options))
                self._on_error('No se pudo consultar la cobertura de MDT: '+task.error)
            return
        key=task.key
        if success:
            data=task.payload
            self.outlet_selection_context=None
            query=replace(self.base_query,directions_path=data['directions_path'],dem_path=data['dem_path'])
            self.variants[key]={'analysis':data['analysis'],'query':query,
                                'smoothing_state':None,'metadata':data['metadata']}
            self.last_errors.pop(key,None)
            for item in self.options:
                if item['key']==key:
                    item['status']='Análisis disponible en caché'
                    item['available']=True
            self._on_options(deepcopy(self.options))
            if task.kind=='select':
                self.active_key=key
                self.requested_key=None
                self._on_result(key,data['analysis'],query)
            warning=data['metadata'].get('coverage_warning')
            if warning:self._on_status(warning)
        elif task.error:
            self.requested_key=None
            context=getattr(task,'selection_context',None)
            self.outlet_selection_context=(dict(deepcopy(context),resolution_key=key,
                                                reason=task.error) if context else None)
            self._failed_key(key,task.error)
        if task.kind=='ensure':self._next_bulk()

    def restore(self,resolution_state):
        """Apply an already validated portable snapshot without network or rasters."""
        if self._disposed:return False
        if not isinstance(resolution_state,dict):raise ValueError('Estado de resoluciones no válido.')
        options=deepcopy(resolution_state.get('options'))
        variants=resolution_state.get('variants')
        active=str(resolution_state.get('active_key',''))
        if not isinstance(options,list) or not isinstance(variants,dict) or active not in variants:
            raise ValueError('El proyecto no contiene la variante activa completa.')
        for item in options:
            if not isinstance(item,dict) or _key(item.get('resolution'))!=str(item.get('key')):
                raise ValueError('Catálogo de resoluciones no válido.')
            if item.get('available') and item['key'] not in variants:
                raise ValueError('Falta una variante anunciada como disponible.')
        for variant in variants.values():
            if not isinstance(variant,dict) or 'analysis' not in variant or 'query' not in variant:
                raise ValueError('Variante incompleta en el proyecto.')
        self.cancel()
        self.variants={str(key):dict(value) for key,value in variants.items()}
        self.options=options
        self.active_key=active
        base=self.variants.get('500') or self.variants.get(self._base_key) or self.variants[active]
        self._set_base(base['analysis'],base['query'])
        self._option_objects={}
        self._on_options(deepcopy(self.options))
        selected=self.variants[active]
        self._on_result(active,selected['analysis'],selected['query'])
        return True
