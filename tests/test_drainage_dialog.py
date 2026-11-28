"""Qt regressions for pending thresholds and portable drainage dialog state.

Only the TauDEM worker is held: widgets, project layers, raster fixtures,
state serialization and restoration use real Qt/QGIS/GDAL implementations.
"""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from osgeo import gdal

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QComboBox, QGridLayout, QLabel, QPushButton
from qgis.PyQt.QtGui import QFontMetrics
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsProject, QgsVectorLayer)
from qgis.gui import QgsMapCanvas

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from caumax_consulta.basin_dialog import BasinDialog
from caumax_consulta.caumax_bridge import NativeQuery
from caumax_consulta.terrain import analyze_basin
from caumax_consulta.session_store import load_session,save_session


class HeldWorker:
    def __init__(self):
        self.busy=False;self.products={};self.calls=[]

    def prepare(self,*args,**kwargs):
        self.calls.append((args,kwargs));self.busy=True

    def cancel(self):self.busy=False
    def dispose(self):self.cancel()


class DrainageDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QgsApplication.instance() or QgsApplication([],True)
        cls.app.initQgis()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='hidrocaumax_drainage_dialog_')
        self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
        private = patch('caumax_consulta.improved_storage.PLUGIN_ROOT', self.folder / 'plugin')
        private.start(); self.addCleanup(private.stop)
        self.project=QgsProject.instance();self.initial_ids=set(self.project.mapLayers())
        self.initial_groups=list(self.project.layerTreeRoot().children())
        self.crs=QgsCoordinateReferenceSystem('EPSG:25830')
        self.canvas=QgsMapCanvas();self.canvas.setDestinationCrs(self.crs)
        self.iface=SimpleNamespace(mapCanvas=lambda:self.canvas)
        directions=np.zeros((5,5),dtype=np.uint8)
        directions[1:4,1:4]=[[2,4,8],[1,0,16],[128,64,32]]
        self.directions=self.raster('caumax_d8.tif',directions,gdal.GDT_Byte,255)
        self.dem=self.raster('dem.tif',100+np.arange(25,dtype=np.float32).reshape(5,5),gdal.GDT_Float32,-9999)
        self.tau=self.raster('tau_d8.tif',np.ones((5,5),dtype=np.uint8),gdal.GDT_Byte,0)
        self.analysis=analyze_basin(self.directions,self.dem,500062.5,4699937.5)
        self.query=NativeQuery(12.,100,500062.5,4699937.5,self.crs,str(self.directions),str(self.dem),'q100')
        x,y=self.analysis.outlet_x,self.analysis.outlet_y
        line=QgsGeometry.fromWkt(f'LINESTRING Z ({x} {y} 110,{x+25} {y+25} 120)')
        recommended=max(100*625.,.01*self.analysis.area_m2)
        self.state=dict(metadata={'engine':'TauDEM','automatic':True,'visible':True},
            threshold_area_m2=recommended,recommended_threshold_area_m2=recommended,
            automatic=True,flow_encoding='caumax',dem_path=str(self.dem),tau_d8_path=str(self.tau),
            caumax_d8_path=str(self.directions),network_path=str(self.folder/'offline-network.shp'),
            network_crs_wkt=self.crs.toWkt(),fields=[{'name':'LINKNO','type':'int','width':9,'precision':0}],
            features=[{'wkb':bytes(line.asWkb()),'attributes':{'LINKNO':1}}])
        self.dialogs=[]
        self.dialog=self.make_dialog()

    def raster(self,name,data,kind,nodata):
        path=self.folder/name;ds=gdal.GetDriverByName('GTiff').Create(str(path),5,5,1,kind)
        ds.SetProjection(self.crs.toWkt());ds.SetGeoTransform((500000,25,0,4700000,0,-25))
        ds.GetRasterBand(1).SetNoDataValue(nodata);ds.GetRasterBand(1).WriteArray(data);ds=None
        return path

    def make_dialog(self,saved=None):
        with patch.object(BasinDialog,'_start'),patch.object(BasinDialog,'_setup_resolutions'):
            dialog=BasinDialog(self.query,self.iface,saved_session=saved)
        dialog.setAttribute(Qt.WA_DontShowOnScreen,True)
        self.dialogs.append(dialog)
        if saved is None:
            dialog.analysis=self.analysis;dialog._active_resolution_key='25'
            dialog._drainage_state=deepcopy(self.state)
            dialog._display_analysis();dialog._update_smoothing()
        dialog._drainage_controller=HeldWorker()
        dialog._refresh_drainage_controls()
        return dialog

    def tearDown(self):
        for dialog in self.dialogs:
            if not sip.isdeleted(dialog):
                dialog.dispose();dialog.close();dialog.deleteLater()
        self.app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.canvas.setLayers([]);sip.delete(self.canvas)
        for layer_id in set(self.project.mapLayers())-self.initial_ids:self.project.removeMapLayer(layer_id)
        for node in list(self.project.layerTreeRoot().children()):
            if node not in self.initial_groups:self.project.layerTreeRoot().removeChildNode(node)

    def choose_manual_threshold(self):
        self.dialog.drainage_threshold.setValue(self.state['threshold_area_m2']/2e6)
        self.assertTrue(self.dialog._drainage_timer.isActive())

    def test_initial_basin_task_shows_all_resolution_choices_before_it_finishes(self):
        # The 1.0.18 window was empty when AnalysisTask failed before
        # _setup_resolutions(). Do not make the selector disappear again.
        dialog = self.dialog
        self.assertEqual(dialog.resolution_checkboxes, {})
        manager = SimpleNamespace(tasks=[], addTask=lambda task: manager.tasks.append(task))
        with patch('caumax_consulta.basin_dialog.QgsApplication.taskManager',
                   return_value=manager):
            dialog._start()
        self.assertEqual(set(dialog.resolution_checkboxes), {'500', '25', '5'})
        self.assertEqual(len(manager.tasks), 1)
        self.assertTrue(all(not control.isEnabled()
                            for control in dialog.resolution_checkboxes.values()))
        dialog._failed('El punto está fuera de la extensión de la malla.')
        self.assertIn('cálculo no disponible', dialog.context_mdt.text())
        self.assertEqual(set(dialog.resolution_checkboxes), {'500', '25', '5'})

    def test_exportar_cierre_green_shapefile_from_visible_blue_cross_button(self):
        dialog = self.dialog
        self.assertEqual(dialog.export_closure_button.text(), 'Guardar Cierre')
        self.assertEqual(dialog.export_button.text(), 'Guardar Divisoria')
        self.assertTrue(dialog._smoothing_ready)
        self.assertTrue(dialog._surface_valid)
        self.assertFalse(dialog._basin_hydrology_busy())
        target = self.folder / 'Cierre_visible.shp'
        dialog._set_export_enabled(True)
        with patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(target), 'Shapefile (*.shp)')):
            dialog.export_closure_button.click()
        self.assertTrue(target.is_file())
        self.assertTrue(target.with_suffix('.qml').is_file())
        layer = next(layer for layer in self.project.mapLayers().values()
                     if layer.customProperty('HidroCAUMAX/main_course_outlet', False))
        self.assertEqual(layer.renderer().symbol().color().name(), '#23e733')
        self.assertTrue(layer.isValid())
        self.assertEqual(layer.featureCount(), 1)
        point = next(layer.getFeatures()).geometry().asPoint()
        self.assertAlmostEqual(point.x(), self.analysis.outlet_x, places=6)
        self.assertAlmostEqual(point.y(), self.analysis.outlet_y, places=6)
        dialog._mark_drainage_pending()
        self.assertFalse(dialog.export_closure_button.isEnabled())
        self.assertFalse(dialog.export_button.isEnabled())

    def test_pending_threshold_blocks_save_and_export_until_effective_network_arrives(self):
        dialog=self.dialog;self.choose_manual_threshold()
        for name in ('save_project_button','report_button','export_button','network_export_button'):
            self.assertFalse(getattr(dialog,name).isEnabled(),name)
        original=deepcopy(dialog._drainage_state)
        with patch('caumax_consulta.taudem_engine.probe_taudem',return_value={'available':True}), \
                patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName') as chooser:
            dialog._choose_save_project()
        chooser.assert_not_called()
        self.assertFalse(dialog._saving_project);self.assertFalse(dialog._drainage_timer.isActive())
        self.assertTrue(dialog._drainage_controller.busy)
        self.assertEqual(dialog._drainage_state,original)
        call=dialog._drainage_controller.calls[0]
        self.assertFalse(call[1]['automatic'])
        self.assertEqual(call[1]['threshold_area_m2'],self.state['threshold_area_m2']/2)
        with self.assertRaisesRegex(ValueError,'preparación'):dialog._state_for_output()
        updated=deepcopy(self.state);updated['automatic']=False
        updated['threshold_area_m2']/=2;updated['metadata']['automatic']=False
        dialog._drainage_controller.busy=False
        dialog._drainage_ready('25',SimpleNamespace(analysis=self.analysis,dem_path=str(self.dem),
            caumax_d8_path=str(self.directions)),updated)
        self.assertEqual(dialog.get_smoothing_state()['drainage_state']['threshold_area_m2'],updated['threshold_area_m2'])
        self.assertTrue(dialog.save_project_button.isEnabled())

    def test_basin_d8_generation_erases_all_course_groups_and_does_not_restore_them(self):
        from caumax_consulta.course_layers import _OWNER_PROPERTY
        from caumax_consulta.drainage_layers import DRAINAGE_GROUP_LABEL
        dialog = self.dialog
        root = self.project.layerTreeRoot()
        old_ids = []
        for i in range(2):
            group = root.addGroup('HidroCAUMAX · Curso principal [obsolete]')
            group.setCustomProperty(_OWNER_PROPERTY, 'old-' + str(i))
            layer = QgsVectorLayer('LineString?crs=EPSG:25830',
                                   'Curso principal total (L)', 'memory')
            layer.setCustomProperty(_OWNER_PROPERTY, 'old-' + str(i))
            self.project.addMapLayer(layer, False)
            group.addLayer(layer)
            old_ids.append(layer.id())
        products = SimpleNamespace(analysis=self.analysis, dem_path=str(self.dem),
                                   caumax_d8_path=str(self.directions))
        dialog._drainage_ready('25', products, deepcopy(self.state))
        self.assertTrue(all(self.project.mapLayer(item) is None for item in old_ids))
        self.assertFalse(any(g.customProperty(_OWNER_PROPERTY, '')
                             for g in root.findGroups(recursive=True)))
        self.assertTrue(any(g.name().startswith(DRAINAGE_GROUP_LABEL)
                            for g in root.findGroups(recursive=True)))
        self.assertIsNotNone(self.project.mapLayer(
            dialog._drainage_layers.layers['directions'].id()))
        self.assertTrue(self.dem.is_file())
        self.assertTrue(self.directions.is_file())
        # A subsequent report/redraw must not recreate courses while the D8
        # result remains the published view.
        dialog._display_analysis()
        self.assertFalse(any(g.name().startswith('HidroCAUMAX · Curso principal')
                             for g in root.findGroups(recursive=True)))

    def test_hydrology_export_flushes_pending_threshold_without_exporting_stale_network(self):
        self.choose_manual_threshold();dialog=self.dialog
        with patch('caumax_consulta.taudem_engine.probe_taudem',return_value={'available':True}), \
                patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName') as chooser, \
                patch('caumax_consulta.hydro_export.export_network') as export:
            dialog._export_hydrology('network')
        chooser.assert_not_called();export.assert_not_called()
        self.assertTrue(dialog._drainage_controller.busy)
        self.assertEqual(len(dialog._drainage_controller.calls),1)

    def test_manual_mode_without_threshold_edit_survives_real_save_load_and_restore(self):
        dialog=self.dialog;dialog.drainage_auto.setChecked(False)
        self.assertFalse(dialog._drainage_timer.isActive())
        state=dialog.get_smoothing_state()
        self.assertFalse(state['drainage_state']['automatic'])
        self.assertFalse(state['drainage_state']['metadata']['automatic'])
        path=self.folder/'manual.caumax.json';save_session(path,self.analysis,self.query,state)
        restored=self.make_dialog(load_session(path))
        self.assertFalse(restored.drainage_auto.isChecked())
        self.assertFalse(restored._drainage_state['automatic'])
        self.assertEqual(restored.drainage_threshold.value(),dialog.drainage_threshold.value())

    def test_live_layer_tree_visibility_survives_snapshot_serialization_and_restore(self):
        dialog=self.dialog;layers=dialog._drainage_layers.layers;tree=self.project.layerTreeRoot()
        tree.findLayer(layers['network'].id()).setItemVisibilityChecked(False)
        tree.findLayer(layers['directions'].id()).setItemVisibilityChecked(True)
        state=dialog.get_smoothing_state();drainage=state['drainage_state']
        self.assertEqual((drainage['network_visible'],drainage['directions_visible']),(False,True))
        self.assertFalse(drainage['metadata']['visible'])
        path=self.folder/'visibility.caumax.json';save_session(path,self.analysis,self.query,state)
        restored=self.make_dialog(load_session(path))
        self.assertEqual({key: restored._drainage_layers.visibility()[key]
                          for key in ('network','directions')},
                         {'network': False, 'directions': True})

    def test_rebuilt_network_preserves_live_visibility_and_ignores_stale_resolution(self):
        dialog=self.dialog;tree=self.project.layerTreeRoot();layers=dialog._drainage_layers.layers
        tree.findLayer(layers['network'].id()).setItemVisibilityChecked(False)
        tree.findLayer(layers['directions'].id()).setItemVisibilityChecked(True)
        products=SimpleNamespace(analysis=self.analysis,dem_path=str(self.dem),caumax_d8_path=str(self.directions))
        before=deepcopy(dialog._drainage_state)
        dialog._drainage_ready('5',products,deepcopy(self.state))
        self.assertEqual(dialog._drainage_state,before)
        updated=deepcopy(self.state);updated['threshold_area_m2']/=2
        dialog._drainage_ready('25',products,updated)
        self.assertEqual({key: dialog._drainage_layers.visibility()[key]
                          for key in ('network','directions')},
                         {'network': False, 'directions': True})
        self.assertEqual(dialog._drainage_state['threshold_area_m2'],updated['threshold_area_m2'])

    def test_bulk_save_disables_threshold_edits_and_restores_them_after_completion(self):
        dialog=self.dialog
        callbacks=[]
        option=dict(key='25',resolution=25.,available=True,provider='Test',label='25 m',status='Available')
        controller=SimpleNamespace(options=[option],variants={'25':{'analysis':self.analysis,'query':self.query,
            'smoothing_state':None}},last_errors={},ensure_all=lambda ready:callbacks.append(ready),dispose=lambda:None)
        dialog._resolution_controller=controller
        path=self.folder/'bulk.caumax.json'
        with patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',return_value=(str(path),'')), \
                patch.object(dialog,'save_project') as save, \
                patch('caumax_consulta.basin_dialog.QMessageBox.critical') as error:
            dialog._choose_save_project()
            self.assertTrue(dialog._saving_project)
            self.assertFalse(dialog.drainage_threshold.isEnabled())
            self.assertFalse(dialog.drainage_auto.isEnabled())
            self.assertFalse(dialog.drainage_prepare_button.isEnabled())
            self.assertFalse(dialog.save_project_button.isEnabled())
            callbacks[0](controller.variants)
            save.assert_called_once_with(str(path));error.assert_not_called()
        self.assertFalse(dialog._saving_project)
        self.assertTrue(dialog.drainage_threshold.isEnabled())
        self.assertTrue(dialog.drainage_auto.isEnabled())
        self.assertTrue(dialog.save_project_button.isEnabled())

    def test_direct_prepare_stops_debounce_timer_instead_of_restarting_worker(self):
        self.choose_manual_threshold();dialog=self.dialog
        self.assertFalse(hasattr(dialog,'drainage_cancel_button'))
        self.assertNotIn('Cancelar cálculo',[b.text() for b in dialog.drainage_group.findChildren(QPushButton)])
        with patch('caumax_consulta.taudem_engine.probe_taudem',return_value={'available':True}):
            dialog.drainage_prepare_button.click()
            self.assertFalse(dialog._drainage_timer.isActive())
            QTest.qWait(550)
        self.assertEqual(len(dialog._drainage_controller.calls),1)
        self.assertTrue(dialog._drainage_controller.busy)
        self.assertFalse(dialog.drainage_prepare_button.isEnabled())
        dialog.dispose()
        self.assertFalse(dialog._drainage_controller.busy)

    def test_prepare_uses_default_engine_and_caumax_even_with_old_external_setting(self):
        settings_path=str(self.folder/'isolated-settings.ini')
        settings=QSettings(settings_path,QSettings.IniFormat)
        external=str(self.folder/'external-engine')
        settings.setValue('HidroCAUMAX/taudem_bin',external)
        settings.setValue('HidroCAUMAX/unrelated-setting','preserved')
        settings.sync()
        dialog=self.dialog
        self.assertFalse(hasattr(dialog,'taudem_folder_button'))
        self.assertFalse(hasattr(dialog,'direction_encoding'))
        self.assertFalse(dialog.drainage_group.findChildren(QComboBox))
        with patch('caumax_consulta.basin_dialog.QSettings',return_value=settings), \
                patch('caumax_consulta.taudem_engine.probe_taudem',return_value={'available':True}) as probe:
            dialog._prepare_drainage()
        probe.assert_called_once_with(bin_dir=None)
        kwargs=dialog._drainage_controller.calls[-1][1]
        self.assertIsNone(kwargs['bin_dir'])
        self.assertEqual(kwargs['encoding'],'caumax')
        self.assertEqual(settings.value('HidroCAUMAX/taudem_bin'),external)
        self.assertEqual(settings.value('HidroCAUMAX/unrelated-setting'),'preserved')
        self.assertFalse(dialog.drainage_prepare_button.isEnabled())

    def test_unavailable_default_engine_displays_reason_and_documentation_in_one_label(self):
        reason='El runtime incluido no está disponible en esta plataforma de prueba.'
        with patch('caumax_consulta.taudem_engine.probe_taudem',
                   return_value={'available':False,'reason':reason}) as probe:
            self.dialog._prepare_drainage()
        probe.assert_called_once_with(bin_dir=None)
        self.assertEqual(self.dialog._drainage_controller.calls,[])
        text=self.dialog.drainage_status.text()
        self.assertIn(reason,text)
        self.assertIn('Windows de 64 bits',text)
        self.assertIn('Documentación',text)
        labels=[label for label in self.dialog.drainage_group.findChildren(QLabel)
                if 'Documentación' in label.text()]
        self.assertEqual(labels,[self.dialog.drainage_status])

    def test_legacy_taudem_snapshot_restores_and_exports_only_caumax_directions(self):
        state=self.dialog.get_smoothing_state()
        # The dialog fixture's held engine uses an arbitrary TauDEM raster.
        # Real export also validates that both direction encodings correspond.
        source=gdal.Open(str(self.directions))
        esri=source.ReadAsArray();source=None
        esri[esri==0]=1
        native=np.zeros_like(esri)
        for tau,code in enumerate((1,128,64,32,16,8,4,2),1):
            native[esri==code]=tau
        caumax_export=self.raster('export_esri.tif',esri,gdal.GDT_Byte,255)
        tau_export=self.raster('export_tau.tif',native,gdal.GDT_Byte,0)
        for name in ('drainage_state','base_drainage_state'):
            if state.get(name):
                state[name].update(caumax_d8_path=str(caumax_export),tau_d8_path=str(tau_export))
        state['drainage_state']['flow_encoding']='taudem'
        state['drainage_state']['directions_visible']=True
        if state.get('base_drainage_state'):
            state['base_drainage_state']['flow_encoding']='taudem'
        source=self.folder/'legacy-taudem.caumax.json'
        save_session(source,self.analysis,self.query,state)
        restored=self.make_dialog(load_session(source))
        snapshot=restored.get_smoothing_state()
        self.assertEqual(snapshot['drainage_state']['flow_encoding'],'caumax')
        self.assertEqual(snapshot['base_drainage_state']['flow_encoding'],'caumax')
        self.assertEqual(Path(restored._drainage_layers.layers['directions'].source()),caumax_export)
        destination=self.folder/'only-caumax.tif'
        with patch('caumax_consulta.basin_dialog.QFileDialog.getSaveFileName',
                   return_value=(str(destination),'')), \
                patch('caumax_consulta.basin_dialog.QMessageBox.critical') as errors:
            restored._export_hydrology('directions')
        errors.assert_not_called()
        self.assertTrue(destination.is_file())
        actual=gdal.Open(str(destination));expected=gdal.Open(str(caumax_export))
        self.assertTrue(np.array_equal(actual.ReadAsArray(),expected.ReadAsArray()))
        actual=expected=None
        self.assertEqual(restored._drainage_state['metadata']['exports']['directions_caumax'],str(destination))

    def test_exports_share_three_centered_columns_with_complete_compact_buttons(self):
        from qgis.PyQt.QtWidgets import QLayout
        from caumax_consulta.drainage_panel import DrainageControls
        dialog=self.dialog
        dialog.show()
        self.assertTrue(dialog.drainage_group.isHidden())
        self.assertTrue(all(layout.indexOf(dialog.drainage_group)<0
                            for layout in dialog.findChildren(QLayout)))
        # Generation now belongs to the rational-method dialog. Measure its
        # visible reusable controls, not the hidden legacy persistence adapter.
        group=DrainageControls()
        self.addCleanup(lambda:sip.delete(group) if not sip.isdeleted(group) else None)
        group.setAttribute(Qt.WA_DontShowOnScreen,True)
        group.show()
        self.assertEqual(group.title(),'Generación capas resolución mejorada')
        self.assertEqual(group.layout().itemAt(0).widget().text(),
            'Genera el MDT elegido, el ráster de Dirección de Flujo y el shape de la Red de Drenaje con el esquema Esri-D8')
        row=group.layout().itemAt(1).layout()
        controls=[row.itemAt(i).widget() for i in range(5)]
        self.assertEqual(controls,[group.drainage_prepare_button,controls[1],group.drainage_threshold,
                                   group.drainage_auto,group.drainage_visible])
        self.assertEqual(controls[1].text(),'Umbral inicio corriente')
        self.assertFalse(controls[1].wordWrap())
        self.assertEqual(group.drainage_threshold.decimals(),6)
        grid=group._drainage_exports_widget.layout()
        self.assertIsInstance(grid,QGridLayout)
        labels=['Modelo Digital Terreno','Direcciones de Drenaje','Red de drenaje']
        texts=['Exportar MDT (TIFF)…','Exportar dirección de drenaje (TIFF)…','Exportar Red de Drenaje (SHP)…']
        for width in (520,650,900):
            with self.subTest(width=width):
                group.resize(width,400)
                QTest.qWait(100)
                group.fit_to_width()
                self.app.processEvents()
                centers=[control.geometry().center().y() for control in controls]
                self.assertLessEqual(max(centers)-min(centers),1)
                for previous,current in zip(controls,controls[1:]):
                    self.assertLess(previous.geometry().right(),current.x())
                    self.assertLessEqual(current.x()-previous.geometry().right()-1,3)
                for button in (group.drainage_prepare_button,)+group.product_buttons:
                    baseline=group.button_base_sizes[button]
                    self.assertEqual(button.height(),round(baseline.height()*1.10))
                    self.assertEqual(button.width(),round(baseline.width()*1.10))
                self.assertGreaterEqual(controls[1].width(),QFontMetrics(controls[1].font()).horizontalAdvance(controls[1].text()))
                line=group.drainage_threshold.lineEdit()
                self.assertGreaterEqual(line.width(),QFontMetrics(line.font()).horizontalAdvance(line.text())+2)
                self.assertLessEqual(controls[-1].mapTo(group,QPoint()).x()+controls[-1].width(),group.width())
                for column,(label,text) in enumerate(zip(labels,texts)):
                    heading=grid.itemAtPosition(0,column).widget()
                    button=grid.itemAtPosition(1,column).widget()
                    self.assertEqual(heading.text(),label)
                    self.assertEqual(button.text(),text)
                    self.assertTrue(heading.alignment() & Qt.AlignHCenter)
                    self.assertTrue(grid.itemAtPosition(1,column).alignment() & Qt.AlignHCenter)
                    self.assertLessEqual(abs(heading.geometry().center().x()-button.geometry().center().x()),
                                         22 if width == 520 else 1)
                    self.assertGreaterEqual(button.width(),QFontMetrics(button.font()).horizontalAdvance(text)+4)
                    self.assertGreaterEqual(heading.height(),heading.heightForWidth(heading.width()))
                    self.assertGreater(group.drainage_status.y(),button.mapTo(group,QPoint(0,button.height())).y())
                    self.assertLessEqual(button.mapTo(group,QPoint()).x()+button.width(),group.width())
                self.assertEqual(len({grid.itemAtPosition(1,c).widget().y() for c in range(3)}),1)
                self.assertLessEqual(group.width(),width)


if __name__=='__main__':unittest.main()
