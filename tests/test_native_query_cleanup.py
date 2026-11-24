"""Native query identity and cleanup with real basin dialogs and QGIS layers."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from osgeo import gdal
from qgis.PyQt.QtCore import QObject, Qt
from qgis.PyQt.QtWidgets import QDialog
from qgis.core import QgsProject, QgsVectorLayer

import test_drainage_dialog as fixture
from caumax_consulta.basin_dialog import AnalysisTask
from caumax_consulta.native_plugin import NativeCaumaxPlugin
from caumax_consulta.session_store import load_session, save_session
from caumax_consulta.terrain import analyze_basin


class NativeQueryCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        self.helper=fixture.DrainageDialogTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.addCleanup(self.helper.tearDown)
        self.dialog=self.helper.dialog
        self.native=QDialog()
        self.native.setAttribute(Qt.WA_DontShowOnScreen,True)
        self.addCleanup(self.native.deleteLater)
        self.addCleanup(self.native.close)
        self.plugin=NativeCaumaxPlugin.__new__(NativeCaumaxPlugin)
        QObject.__init__(self.plugin)
        self.plugin.iface=self.helper.iface
        self.plugin._enabled=True
        self.plugin._dialogs={self.native:self.dialog}
        self.plugin._saved_dialogs=[]
        self.plugin._updates=SimpleNamespace(check=Mock())
        self.dialog.finished.connect(lambda *_:self.plugin._forget(self.native,self.dialog))

    def coarse_query(self):
        paths={}
        x,y=self.helper.query.x,self.helper.query.y
        for key in ('dem_path','directions_path'):
            source=gdal.Open(str(getattr(self.helper.query,key)))
            path=self.helper.folder/f'original500_{key}.tif'
            raster=gdal.GetDriverByName('GTiff').CreateCopy(str(path),source)
            raster.SetGeoTransform((x-1250,500,0,y+1250,0,-500))
            raster=source=None
            paths[key]=str(path)
        return replace(self.helper.query,**paths)

    def test_reopening_native_500_query_after_switch_to_25_reuses_window_and_layers(self):
        # The displayed query has fine raster paths, while the native request
        # retains the immutable snapshot that created the window.
        native_query=self.coarse_query()
        self.dialog._native_query=native_query
        self.assertEqual(self.dialog.analysis.cell_width,25)
        self.assertNotEqual(self.plugin._key(self.dialog.query),self.plugin._key(native_query))
        layers=set(QgsProject.instance().mapLayers())
        with patch('caumax_consulta.native_plugin.BasinDialog') as factory:
            self.plugin._open_basin(self.native,native_query)
        factory.assert_not_called()
        self.plugin._updates.check.assert_called_once_with()
        self.assertIs(self.plugin._dialogs[self.native],self.dialog)
        self.assertFalse(self.dialog._disposed)
        self.assertEqual(set(QgsProject.instance().mapLayers()),layers)
        self.assertIsNotNone(self.dialog._marker)

    def test_new_native_point_clears_live_and_saved_queries_before_creation_and_late_result(self):
        path=self.helper.folder/'saved.caumax.json'
        save_session(path,self.dialog.analysis,self.dialog.query,self.dialog.get_smoothing_state())
        saved=self.helper.make_dialog(load_session(path))
        self.plugin._saved_dialogs.append(saved)
        saved.finished.connect(lambda *_:self.plugin._forget_saved(saved))
        saved.show()
        self.dialog.show()
        self.dialog._native_query=self.helper.query
        late=AnalysisTask(self.helper.query,self.dialog._completed)
        self.dialog._task=late
        self.assertTrue(late.run(),late.error)
        old_ids=set(QgsProject.instance().mapLayers())-self.helper.initial_ids
        foreign=QgsVectorLayer('Point?crs=EPSG:25830','HidroCAUMAX · usuario','memory')
        QgsProject.instance().addMapLayer(foreign)
        next_query=replace(self.helper.query,x=self.helper.query.x+25)

        def create(query,iface,parent):
            self.assertTrue(self.dialog._disposed)
            self.assertTrue(saved._disposed)
            self.assertTrue(late.isCanceled())
            self.assertFalse(old_ids.intersection(QgsProject.instance().mapLayers()))
            self.assertIs(QgsProject.instance().mapLayer(foreign.id()),foreign)
            self.helper.query=query
            self.helper.analysis=analyze_basin(query.directions_path,query.dem_path,query.x,query.y)
            return self.helper.make_dialog()

        with patch('caumax_consulta.native_plugin.BasinDialog',side_effect=create) as factory:
            self.plugin._open_basin(self.native,next_query)
        factory.assert_called_once_with(next_query,self.helper.iface,self.native)
        current=self.plugin._dialogs[self.native]
        self.assertIs(current._native_query,next_query)
        self.assertFalse(current._disposed)
        self.assertEqual(self.plugin._saved_dialogs,[])
        published=set(QgsProject.instance().mapLayers())
        late.finished(True)
        self.assertEqual(set(QgsProject.instance().mapLayers()),published)
        self.assertIs(self.plugin._dialogs[self.native],current)


if __name__=='__main__':
    unittest.main()
