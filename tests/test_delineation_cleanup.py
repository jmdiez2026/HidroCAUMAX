"""Session-wide cleanup with real QGIS layers, dialogs and stored rasters."""
import gc
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
from osgeo import gdal
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QDialog, QWidget
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsGeometry,
                       QgsPointXY, QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer, QgsWkbTypes)
from qgis.gui import QgsRubberBand, QgsVertexMarker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from caumax_consulta import delineation_cleanup as cleanup
from caumax_consulta.boundary_layers import BoundaryLayers
from caumax_consulta.course_layers import CourseLayers
from caumax_consulta.drainage_layers import DrainageLayers
from caumax_consulta.terrain_layers import TerrainLayers


OWNERS = ('HidroCAUMAX/course_owner', 'HidroCAUMAX/boundary_owner',
          'HidroCAUMAX/terrain_owner', 'HidroCAUMAX/drainage_owner')


def owned_ids(project):
    return {layer.id() for layer in project.mapLayers().values()
            if any(layer.customProperty(key, None) not in (None, '') for key in OWNERS)}


class NoCanvas:
    def mapCanvas(self):
        raise AssertionError('Removing project layers must not change the map tool or extent.')


class RegisteredDialog(QDialog):
    """Small Qt window for registry lifetime and dispatch-order assertions."""
    def __init__(self):
        super().__init__()
        self._disposed = False
        self.local_calls = self.dispose_calls = self.close_calls = 0

    def _clear_local_delineation(self):
        self.local_calls += 1

    def dispose(self):
        self.dispose_calls += 1
        self._disposed = True
        cleanup.unregister_dialog(self)

    def close(self):
        self.close_calls += 1
        return super().close()


class DelineationCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance()
        if cls.app is None:
            cls.app = QgsApplication([], True)
            cls.app.initQgis()

    def setUp(self):
        self.project = QgsProject.instance()
        self.before_ids = set(self.project.mapLayers())
        self.before_groups = list(self.project.layerTreeRoot().children())
        self.before_filename = self.project.fileName()
        self.temp = tempfile.TemporaryDirectory(prefix='hidrocaumax_cleanup_', ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.crs = QgsCoordinateReferenceSystem('EPSG:25830')
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            cleanup.unregister_dialog(dialog)
            if not sip.isdeleted(dialog):
                dialog.close()
                sip.delete(dialog)
        for layer_id in set(self.project.mapLayers()) - self.before_ids:
            self.project.removeMapLayer(layer_id)
        root = self.project.layerTreeRoot()
        for group in list(root.children()):
            if group not in self.before_groups:
                root.removeChildNode(group)
        self.project.setFileName(self.before_filename)

    def raster(self, name, step, x=0, directions=False):
        path = self.folder / name
        kind = gdal.GDT_Byte if directions else gdal.GDT_Float32
        dataset = gdal.GetDriverByName('GTiff').Create(str(path), 6, 6, 1, kind)
        dataset.SetProjection(self.crs.toWkt())
        dataset.SetGeoTransform((x, step, 0, 6 * step, 0, -step))
        band = dataset.GetRasterBand(1)
        band.SetNoDataValue(255 if directions else -9999)
        values = np.full((6, 6), 4, dtype=np.uint8) if directions else np.arange(36, dtype=np.float32).reshape(6, 6) + 100
        band.WriteArray(values)
        dataset = None
        return path

    def generated_results(self, label, steps=(25, 5), x=0):
        """Return IDs, file hashes and weak refs, without retaining controllers."""
        course = CourseLayers(NoCanvas())
        boundaries = BoundaryLayers(self.folder / (label + '_boundaries'))
        terrain = TerrainLayers(NoCanvas(), retain_variants=True)
        drainage = DrainageLayers(NoCanvas())
        references = [weakref.ref(item) for item in (course, boundaries, terrain, drainage)]
        paths = set()
        for step in steps:
            dem = self.raster(f'{label}_dem_{step}.tif', step, x)
            d8 = self.raster(f'{label}_d8_{step}.tif', step, x, directions=True)
            paths.update((dem, d8))
            original = QgsGeometry.fromRect(QgsRectangle(x, 0, x + 6 * step, 6 * step))
            ox, oy = x + 2.5 * step, 2.5 * step
            line = QgsGeometry.fromWkt(
                f'LINESTRING ({ox} {oy},{ox} {oy + step},{ox + step} {oy + 2 * step})')
            analysis = SimpleNamespace(
                crs_wkt=self.crs.toWkt(), cell_width=float(step), cell_height=float(step),
                outlet_x=ox, outlet_y=oy, cell_count=36, z_min=100., z_max=135.,
                geometry_wkb=bytes(original.asWkb()), main_course_wkb=bytes(line.asWkb()),
                values={'main_length_km': line.length() / 1000,
                        'main_length_10_85_km': .75 * line.length() / 1000})
            query = SimpleNamespace(dem_path=str(dem), x=ox, y=oy, crs=self.crs,
                                    period=100, flow=12.5, cell=None)
            course.update(analysis, query)
            for layer in boundaries.update(analysis, query, original, original, degree=3).values():
                paths.add(Path(layer.source().split('|')[0]))
            terrain.update(analysis, query)
            state = dict(network_crs_wkt=self.crs.toWkt(), fields=[],
                         features=[{'wkb': bytes(line.asWkb()), 'attributes': {}}],
                         caumax_d8_path=str(d8), tau_d8_path='', metadata={})
            drainage.update(analysis, state)
        ids = owned_ids(self.project)
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        return ids, hashes, references

    def test_all_queries_and_retained_resolutions_are_removed_after_controller_gc(self):
        first, first_files, first_refs = self.generated_results('first')
        all_ids, second_files, second_refs = self.generated_results('second', x=1000)
        self.assertGreater(len(all_ids), len(first))
        self.assertTrue(all_ids.issubset(self.project.mapLayers()))
        gc.collect()
        self.assertTrue(all(reference() is None for reference in first_refs + second_refs))
        tags = {key for layer in self.project.mapLayers().values()
                for key in OWNERS if layer.customProperty(key, None)}
        self.assertEqual(tags, set(OWNERS))
        cleanup.clear_delineation_layers()
        self.assertFalse(owned_ids(self.project))
        self.assertTrue(all(self.project.mapLayer(layer_id) is None for layer_id in all_ids))
        for path, digest in {**first_files, **second_files}.items():
            self.assertTrue(path.is_file(), str(path))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(cleanup.clear_delineation_layers(), 0)

    def test_names_style_properties_and_foreign_children_do_not_imply_ownership(self):
        project = QgsProject()
        self.addCleanup(project.clear)
        root = project.layerTreeRoot()
        mixed = root.addGroup('HidroCAUMAX · Divisoria')
        mixed.setCustomProperty(OWNERS[1], 'old-query')
        outer = root.addGroup('Former terrain')
        outer.setCustomProperty(OWNERS[2], 'old-query')
        inner = outer.addGroup('Former resolution')
        inner.setCustomProperty(OWNERS[2], 'old-query')
        foreign_empty = root.addGroup('HidroCAUMAX · MDT y relieve')
        foreign = QgsVectorLayer('Polygon?crs=EPSG:25830', 'Divisoria original', 'memory')
        foreign.setCustomProperty('HidroCAUMAX/boundary_kind', 'original')
        foreign.setCustomProperty('HidroCAUMAX/order_rank', 20)
        project.addMapLayer(foreign, False)
        mixed.addLayer(foreign)
        dem = self.raster('user_dem.tif', 25)
        user_dem = QgsRasterLayer(str(dem), 'HidroCAUMAX · MDT 25 m', 'gdal')
        self.assertTrue(user_dem.isValid())
        user_dem.setCustomProperty('hidrocaumax/dem_display_role', 'hypsometry')
        user_dem.setCustomProperty('hidrocaumax/dem_role', 'hillshade')
        project.addMapLayer(user_dem)
        expected = {foreign.id(), user_dem.id()}
        for index, key in enumerate(OWNERS):
            layer = QgsVectorLayer('Point?crs=EPSG:25830', f'Generated {index}', 'memory')
            layer.setCustomProperty(key, f'query-{index}')
            project.addMapLayer(layer, False)
            (mixed if index % 2 else inner).addLayer(layer)
        cleanup.clear_delineation_layers(project)
        self.assertEqual(set(project.mapLayers()), expected)
        self.assertFalse(sip.isdeleted(mixed))
        self.assertEqual([node.layerId() for node in mixed.findLayers()], [foreign.id()])
        self.assertTrue(sip.isdeleted(inner))
        self.assertTrue(sip.isdeleted(outer))
        self.assertFalse(sip.isdeleted(foreign_empty))
        self.assertTrue(dem.is_file())

    def test_reopened_qgis_project_is_cleaned_without_the_original_controllers(self):
        ids, hashes, references = self.generated_results('reopened', steps=(25,))
        path = self.folder / 'previous.qgs'
        self.assertTrue(self.project.write(str(path)))
        reopened = QgsProject()
        self.addCleanup(reopened.clear)
        self.assertTrue(reopened.read(str(path)))
        self.assertEqual(owned_ids(reopened), ids)
        cleanup.clear_delineation_layers(reopened)
        self.assertFalse(owned_ids(reopened))
        self.assertTrue(ids.issubset(self.project.mapLayers()), 'Explicit project must not clear another project.')
        self.assertTrue(all(reference() is None for reference in references))
        self.assertTrue(all(path.is_file() for path in hashes))

    def registered(self):
        dialog = RegisteredDialog()
        self.dialogs.append(dialog)
        cleanup.register_dialog(dialog)
        return dialog

    def test_registration_is_weak_idempotent_and_unregister_skips_the_window(self):
        active, unregistered, disposed = self.registered(), self.registered(), self.registered()
        cleanup.register_dialog(active)
        cleanup.unregister_dialog(unregistered)
        cleanup.unregister_dialog(unregistered)
        disposed._disposed = True
        temporary = RegisteredDialog()
        cleanup.register_dialog(temporary)
        reference = weakref.ref(temporary)
        del temporary
        gc.collect()
        self.assertIsNone(reference(), 'Registry must not keep closed windows alive.')
        cleanup.clear_delineations()
        self.assertEqual(active.local_calls, 1)
        self.assertEqual((unregistered.local_calls, disposed.local_calls), (0, 0))
        self.assertFalse(active._disposed)
        self.assertEqual(active.close_calls, 0)

    def test_automatic_query_cleanup_preserves_published_d8_but_manual_clean_removes_it(self):
        from caumax_consulta.drainage_layers import OWNER_PROPERTY, PIN_PROPERTY
        from caumax_consulta.layer_order import create_result_group
        layer = QgsVectorLayer('LineString?crs=EPSG:25830', 'D8 network fixture', 'memory')
        layer.setCustomProperty(OWNER_PROPERTY, 'persist-through-rational')
        layer.setCustomProperty(PIN_PROPERTY, True)
        self.project.addMapLayer(layer, False)
        group = create_result_group(self.project, 'HidroCAUMAX · Drenaje Esri-D8 [25×25 m]', 10)
        group.setCustomProperty(OWNER_PROPERTY, 'persist-through-rational')
        group.setCustomProperty(PIN_PROPERTY, True)
        group.addLayer(layer)
        key = layer.id()
        cleanup.clear_delineations(close_dialogs=True, preserve_published_drainage=True)
        self.assertIs(self.project.mapLayer(key), layer)
        self.assertIsNotNone(self.project.layerTreeRoot().findLayer(key))
        cleanup.clear_delineations()
        self.assertIsNone(self.project.mapLayer(key))
        self.assertFalse(any(g.customProperty(OWNER_PROPERTY, '')
                             for g in self.project.layerTreeRoot().findGroups(recursive=True)))

    def test_registered_windows_are_disposed_before_global_layer_removal(self):
        first, second = self.registered(), self.registered()
        layer = QgsVectorLayer('Point?crs=EPSG:25830', 'Old result', 'memory')
        layer.setCustomProperty(OWNERS[0], 'previous')
        self.project.addMapLayer(layer)
        observations = []
        def before_removal(*args):
            observations.append((first._disposed, second._disposed,
                                 first.close_calls, second.close_calls))
        self.project.layersWillBeRemoved.connect(before_removal)
        try:
            cleanup.clear_delineations(close_dialogs=True)
        finally:
            self.project.layersWillBeRemoved.disconnect(before_removal)
        self.assertTrue(observations)
        self.assertTrue(all(item == (True, True, 1, 1) for item in observations))
        self.assertEqual((first.dispose_calls, second.dispose_calls), (1, 1))
        self.assertFalse(owned_ids(self.project))


class DelineationCleanupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_drainage_dialog as fixture
        fixture.DrainageDialogTests.setUpClass()

    def setUp(self):
        import test_drainage_dialog as fixture
        self.fixture = fixture.DrainageDialogTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.dialog = self.fixture.dialog

    def saved_dialog(self):
        from caumax_consulta.session_store import load_session, save_session
        path = self.fixture.folder / 'reopened.caumax.json'
        save_session(path, self.dialog.analysis, self.dialog.query, self.dialog.get_smoothing_state())
        return self.fixture.make_dialog(load_session(path))

    def test_button_clears_live_and_saved_windows_without_closing_or_losing_analysis(self):
        saved = self.saved_dialog()
        before = (self.dialog.analysis, saved.analysis)
        self.assertTrue(owned_ids(self.fixture.project))
        self.assertEqual(self.dialog.clear_delineation_button.text(), 'Borrar Delineado')
        self.dialog.clear_delineation_button.click()
        for dialog, analysis in zip((self.dialog, saved), before):
            self.assertFalse(dialog._disposed)
            self.assertTrue(dialog._delineation_cleared)
            self.assertIs(dialog.analysis, analysis)
            self.assertIsNone(dialog._marker)
            dialog._display_analysis()
            dialog._update_smoothing()
            self.assertIsNone(dialog._marker)
        self.assertFalse(owned_ids(self.fixture.project))

    def test_new_query_disposes_live_and_saved_windows_before_late_initial_callback(self):
        from caumax_consulta.basin_dialog import AnalysisTask
        saved = self.saved_dialog()
        task = AnalysisTask(self.dialog.query, self.dialog._completed)
        task.result = self.fixture.analysis
        self.dialog._task = task
        QTimer.singleShot(0, lambda: self.dialog._completed(task, True))
        cleanup.clear_delineations(close_dialogs=True)
        self.assertTrue(self.dialog._disposed)
        self.assertTrue(saved._disposed)
        self.assertTrue(task.isCanceled())
        self.fixture.app.processEvents()
        self.assertFalse(owned_ids(self.fixture.project))

    def test_clear_while_initial_result_is_pending_cannot_redraw_on_success(self):
        from caumax_consulta.basin_dialog import AnalysisTask
        task = AnalysisTask(self.dialog.query, self.dialog._completed)
        task.result = self.fixture.analysis
        self.dialog._task = task
        cleanup.clear_delineations()
        self.assertFalse(self.dialog._disposed)
        self.assertTrue(task.isCanceled())
        self.assertIsNone(self.dialog._task)
        with patch.object(self.dialog, '_setup_resolutions'):
            self.dialog._completed(task, True)
        self.assertIs(self.dialog.analysis, task.result)
        self.assertTrue(self.dialog._delineation_cleared)
        self.assertIsNone(self.dialog._marker)
        self.assertFalse(owned_ids(self.fixture.project))

    def test_real_canvas_sweeps_all_owned_generations_and_keeps_foreign_objects(self):
        canvas = self.fixture.canvas
        canvas.setExtent(QgsRectangle(499950, 4699800, 500250, 4700100))
        canvas.resize(400, 400)
        canvas.show()
        self.dialog._preview()
        current = [getattr(self.dialog, name, None) for name in cleanup.PREVIEW_ATTRIBUTES]
        current = [item for item in current if item is not None]
        self.assertTrue(current)
        previous = []
        for generation in range(3):
            polygon = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
            polygon.setColor(QColor('red'))
            polygon.setToGeometry(QgsGeometry.fromRect(QgsRectangle(500000, 4699900, 500100, 4700000)), None)
            course = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
            course.setColor(QColor('blue'))
            course.setToGeometry(QgsGeometry.fromWkt('LINESTRING(500025 4699925,500075 4699975)'), None)
            marker = QgsVertexMarker(canvas)
            marker.setCenter(QgsPointXY(500025, 4699925))
            marker.setColor(QColor('red'))
            for item in (polygon, course, marker):
                cleanup.register_overlay(canvas, item)
                previous.append(item)
        foreign_rubber = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        foreign_rubber.setColor(QColor('blue'))
        foreign_rubber.setToGeometry(QgsGeometry.fromWkt('LINESTRING(500005 4699905,500095 4699995)'), None)
        foreign_marker = QgsVertexMarker(canvas)
        foreign_marker.setColor(QColor('red'))
        foreign_marker.setCenter(QgsPointXY(500050, 4699950))
        user_layer = QgsVectorLayer('LineString?crs=EPSG:25830', 'SeccionCierreCaumaxGIS', 'memory')
        self.fixture.project.addMapLayer(user_layer)
        canvas.setLayers([user_layer] + list(self.fixture.project.mapLayers().values()))
        canvas.refresh()
        QTest.qWait(50)
        # Reproduce the reported screen: the layer tree is already clean,
        # while red/blue canvas objects remain independently of those layers.
        cleanup.clear_delineation_layers()
        self.assertFalse(owned_ids(self.fixture.project))
        self.assertTrue(any(item.isVisible() for item in current + previous))
        with patch.object(canvas, 'stopRendering', wraps=canvas.stopRendering) as stop, \
                patch.object(canvas, 'clearCache', wraps=canvas.clearCache) as clear_cache, \
                patch.object(canvas, 'refresh', wraps=canvas.refresh) as refresh:
            self.dialog.clear_delineation_button.click()
        stop.assert_called()
        clear_cache.assert_called()
        refresh.assert_called()
        self.fixture.app.processEvents()
        self.assertFalse(self.dialog._disposed)
        self.assertFalse(owned_ids(self.fixture.project))
        for item in current + previous:
            self.assertTrue(sip.isdeleted(item))
        self.assertIs(self.fixture.project.mapLayer(user_layer.id()), user_layer)
        for item in (foreign_rubber, foreign_marker):
            self.assertFalse(sip.isdeleted(item))
            self.assertIs(item.scene(), canvas.scene())
        self.dialog._preview()
        self.dialog._display_analysis()
        self.fixture.app.processEvents()
        self.assertFalse(any(item.data(cleanup.OVERLAY_OWNER_ROLE) == cleanup.OVERLAY_OWNER
                             for item in canvas.scene().items()))
        cleanup.remove_overlay(foreign_rubber)
        cleanup.remove_overlay(foreign_marker)

    def test_current_native_query_marker_is_removed_without_touching_another_query(self):
        class MaxFlowDialog(QDialog):
            pass
        MaxFlowDialog.__module__ = '_cleanup_native.maxflowdialog'
        parent = MaxFlowDialog()
        other = MaxFlowDialog()
        self.addCleanup(parent.deleteLater)
        self.addCleanup(other.deleteLater)
        parent.marker = QgsVertexMarker(self.fixture.canvas)
        parent.marker.setCenter(QgsPointXY(500062.5, 4699937.5))
        other.marker = QgsVertexMarker(self.fixture.canvas)
        other.marker.setCenter(QgsPointXY(500080, 4699980))
        original, foreign = parent.marker, other.marker
        self.dialog.setParent(parent)
        cleanup.register_dialog(self.dialog)
        self.dialog.clear_delineation_button.click()
        self.assertIsNone(parent.marker)
        self.assertTrue(sip.isdeleted(original))
        self.assertIs(other.marker, foreign)
        self.assertFalse(sip.isdeleted(foreign))
        # Native updatePoint can now create another marker without a deleted
        # wrapper remaining in its attribute.
        parent.marker = QgsVertexMarker(self.fixture.canvas)
        self.assertFalse(sip.isdeleted(parent.marker))
        cleanup.remove_overlay(parent.marker)
        cleanup.remove_overlay(other.marker)
        parent.marker = other.marker = None
        self.dialog.setParent(None)

    def test_reusable_participant_is_cleared_without_close_or_dispose_on_new_point(self):
        class Participant(QWidget):
            def __init__(self, iface):
                super().__init__()
                self.iface = iface
                self._disposed = False
                self.cleared = 0
            def _clear_local_delineation(self):
                self.cleared += 1
        participant = Participant(self.fixture.iface)
        participant.show()
        self.addCleanup(participant.deleteLater)
        self.addCleanup(cleanup.unregister_participant, participant)
        cleanup.register_participant(participant)
        cleanup.register_participant(participant)
        cleanup.clear_delineations(close_dialogs=True)
        self.assertEqual(participant.cleared, 1)
        self.assertFalse(participant._disposed)
        self.assertTrue(participant.isVisible())


if __name__ == '__main__':
    unittest.main()
