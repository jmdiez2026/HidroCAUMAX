"""Real QGIS WMS requests against a local server; no external network required."""
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QBuffer, QIODevice, QSize
from qgis.PyQt.QtGui import QColor, QImage
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsMapSettings,
    QgsMapRendererSequentialJob, QgsProject, QgsRectangle)
from caumax_consulta import map_services as maps


class ViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def test_only_enabled_view_is_requested_and_zoom_changes_bbox(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                query = {k.upper(): v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                requests.append(query)
                if query.get('REQUEST', '').lower() == 'getmap':
                    pic = QImage(int(query['WIDTH']), int(query['HEIGHT']), QImage.Format_ARGB32)
                    pic.fill(QColor('#6b9c66'))
                    buf = QBuffer(); buf.open(QIODevice.WriteOnly); pic.save(buf, 'PNG')
                    body, kind = bytes(buf.data()), 'image/png'
                else:
                    address = f'http://127.0.0.1:{self.server.server_port}/'
                    body = f'''<?xml version="1.0"?>
<WMT_MS_Capabilities version="1.1.1"><Service><Name>OGC:WMS</Name><Title>Local test</Title></Service>
<Capability><Request><GetCapabilities><Format>application/vnd.ogc.wms_xml</Format><DCPType><HTTP><Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{address}"/></Get></HTTP></DCPType></GetCapabilities>
<GetMap><Format>image/png</Format><DCPType><HTTP><Get><OnlineResource xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="{address}"/></Get></HTTP></DCPType></GetMap></Request>
<Layer><Title>Root</Title><SRS>EPSG:25830</SRS><BoundingBox SRS="EPSG:25830" minx="300000" miny="4600000" maxx="500000" maxy="4800000"/>
<Layer><Name>terrain</Name><Title>Terrain</Title><SRS>EPSG:25830</SRS></Layer></Layer></Capability></WMT_MS_Capabilities>'''.encode()
                    kind = 'text/xml'
                self.send_response(200); self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        project = QgsProject(); self.addCleanup(project.clear)
        service = maps.WmsServiceSpec('viewport_test', 'Viewport',
            f'http://127.0.0.1:{server.server_port}/',
            (maps.WmsLayerSpec('terrain', 'Terrain', 'terrain'),), version='1.1.1')
        result = maps.load_reference_layers(project, services=(service,))
        self.assertEqual(result.failed, 0, result.message)
        self.assertFalse(any(r.get('REQUEST', '').lower() == 'getmap' for r in requests))
        node = project.layerTreeRoot().findLayers()[0]
        self.assertFalse(node.isVisible())
        node.setItemVisibilityCheckedParentRecursive(True)
        settings = QgsMapSettings()
        settings.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:25830'))
        settings.setOutputSize(QSize(400, 300))
        settings.setLayers([node.layer()])
        wanted = []
        for extent in (QgsRectangle(345000, 4716000, 349000, 4719000),
                       QgsRectangle(346000, 4717000, 346400, 4717300)):
            settings.setExtent(extent)
            job = QgsMapRendererSequentialJob(settings); job.start(); job.waitForFinished()
            self.assertFalse(job.errors())
            self.assertFalse(job.renderedImage().isNull())
            wanted.append(tuple(float(x) for x in
                next(r for r in reversed(requests) if r.get('REQUEST', '').lower() == 'getmap')['BBOX'].split(',')))
            sip.delete(job)
        self.assertEqual(wanted, [(345000., 4716000., 349000., 4719000.),
                                  (346000., 4717000., 346400., 4717300.)])
        maps_requested = [r for r in requests if r.get('REQUEST', '').lower() == 'getmap']
        self.assertEqual(len(maps_requested), 2)
        self.assertTrue(all((r['WIDTH'], r['HEIGHT']) == ('400', '300') for r in maps_requested))


if __name__ == '__main__':
    unittest.main()
