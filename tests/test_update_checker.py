"""Update metadata, network policy and real Qt lifecycle regression checks."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qgis.PyQt.QtCore import QObject, QUrl, pyqtSignal
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtTest import QTest
from qgis.core import QgsApplication

updates = importlib.import_module('caumax_consulta.update_checker')


def entry(version='1.2.7', **changes):
    data = dict(name='HidroCAUMAX v' + version, version=version,
                file_name='caumax_consulta.zip', experimental='False', deprecated='False',
                qgis_minimum_version='3.34', qgis_maximum_version='3.99',
                download_url=updates.RELEASES_URL + '/download/v' + version
                + '/HidroCAUMAX_v' + version.replace('.', '_') + '.zip')
    data.update(changes)
    result = ET.Element('pyqgis_plugin', name=data.pop('name'), version=data['version'])
    for key, value in data.items():
        ET.SubElement(result, key).text = value
    return result


def feed(*entries):
    root = ET.Element('plugins')
    root.extend(entries or [entry()])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


class MetadataTests(unittest.TestCase):
    def parse(self, raw, current='1.2.6', qgis=(3, 40, 6)):
        return updates.parse_update_feed(raw, current, qgis)

    def test_numeric_order_equal_and_older_versions(self):
        self.assertGreater(updates.version_tuple('1.10.0'), updates.version_tuple('1.9.99'))
        for version in ('1.2.5', '1.2.6'):
            with self.subTest(version=version):
                self.assertEqual(self.parse(feed(entry(version))), ('current', None))
        status, info = self.parse(feed(entry('1.9.9'), entry('1.10.0')))
        self.assertEqual(status, 'available')
        self.assertEqual(info['version'], '1.10.0')
        self.assertEqual(info['installed_version'], '1.2.6')

    def test_version_contract_rejects_suffixes_ambiguous_components_and_huge_numbers(self):
        for version in ('v1.2.6', '1.2', '1.2.6rc1', '01.2.6', '1.2.-1', '1.2.6\n', '1.'+'9'*40+'.0'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                updates.version_tuple(version)

    def test_installed_version_is_read_from_packaged_metadata_each_time(self):
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / 'update_checker.py'
            metadata = module.with_name('metadata.txt')
            with patch.object(updates, '__file__', str(module)):
                for version in ('1.2.6', '1.2.7'):
                    metadata.write_text('[general]\nversion='+version+'\n', encoding='utf-8')
                    self.assertEqual(updates.installed_version(), version)

    def test_identity_requires_both_package_id_and_name(self):
        for candidate in (entry(file_name='other.zip'), entry(name='Other plugin'),
                          entry(file_name='../caumax_consulta.zip')):
            self.assertEqual(self.parse(feed(candidate)), ('not_found', None))
        status, info = self.parse(feed(entry(name='HidroCAUMAX')))
        self.assertEqual(status, 'available')
        self.assertEqual(info['plugin_id'], 'caumax_consulta')

    def test_experimental_deprecated_and_disabled_are_never_announced(self):
        for field in ('experimental', 'deprecated', 'disabled'):
            with self.subTest(field=field):
                self.assertEqual(self.parse(feed(entry(**{field:'True'}))), ('not_found', None))

    def test_compatibility_uses_patch_boundaries_and_selects_latest_compatible(self):
        narrow = entry(qgis_minimum_version='3.40', qgis_maximum_version='3.40')
        self.assertEqual(self.parse(feed(narrow), qgis=(3,40,99))[0], 'available')
        self.assertEqual(self.parse(feed(narrow), qgis=(3,41,0))[0], 'incompatible')
        candidates = feed(entry('1.2.7'), entry('1.3.0', qgis_minimum_version='4.0', qgis_maximum_version='4.99'))
        self.assertEqual(self.parse(candidates)[1]['version'], '1.2.7')
        self.assertEqual(self.parse(feed(entry()), qgis=(3,33,99))[0], 'incompatible')
        self.assertEqual(self.parse(feed(entry()), qgis=(4,0,0))[0], 'incompatible')

    def test_qgis_development_minor_99_targets_next_major(self):
        with patch.object(updates, 'Qgis', SimpleNamespace(QGIS_VERSION_INT=39900)):
            self.assertEqual(updates.running_qgis_version(), (4,0,0))

    def test_xml_rejects_dtd_entities_wrong_root_and_oversize(self):
        cases = (b'<!DOCTYPE plugins [<!ENTITY x "expanded">]><plugins>&x;</plugins>',
                 b'<!DOCTYPE plugins SYSTEM "file:///etc/passwd"><plugins/>',
                 '<?xml version="1.0"?><!DOCTYPE plugins><plugins/>'.encode('utf-16'),
                 b'<html>Not an index</html>', b'<plugins>', b'x'*(updates.MAX_RESPONSE_BYTES+1))
        for raw in cases:
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                self.parse(raw)

    def test_untrusted_download_urls_are_rejected(self):
        original = entry().findtext('download_url')
        urls = (original.replace('https:', 'http:'),
                original.replace('github.com/', 'github.com.attacker.example/'),
                original.replace('github.com/', 'someone@github.com/'),
                original.replace('github.com/', 'github.com:443/'),
                original.replace('/jmdiez2026/', '/another-owner/'),
                original.replace('/v1.2.7/', '/v1.2.8/'),
                original+'?token=anything', original+'#fragment',
                original.replace('/download/', '/download/../'),
                original.replace('/download/', '/%64ownload/'), 'file:///tmp/plugin.zip')
        for url in urls:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'no autorizada'):
                self.parse(feed(entry(download_url=url)))

    def test_duplicate_conflicting_metadata_and_invalid_compatibility_fail(self):
        duplicate = entry()
        ET.SubElement(duplicate, 'download_url').text = duplicate.findtext('download_url')
        contradictory = entry()
        contradictory.find('version').text = '2.0.0'
        cases = (duplicate, contradictory, entry(experimental='maybe'),
                 entry(name='HidroCAUMAX v2.0.0'), entry(qgis_minimum_version='unknown'),
                 entry(qgis_minimum_version='4.0', qgis_maximum_version='3.99'))
        for candidate in cases:
            with self.subTest(xml=ET.tostring(candidate)), self.assertRaises(ValueError):
                self.parse(feed(candidate))

    def test_information_contains_only_fixed_identity_numeric_versions_and_safe_urls(self):
        candidate = entry()
        ET.SubElement(candidate, 'description').text = '<img src="https://attacker.example/track">'
        status, info = self.parse(feed(candidate))
        self.assertEqual(status, 'available')
        self.assertEqual(set(info), {'plugin_id','installed_version','version','download_url','release_url','repository_url'})
        self.assertEqual(info['repository_url'], updates.REPOSITORY_URL)
        self.assertNotIn('attacker', repr(info))

    def test_request_is_fixed_anonymous_https_and_always_network(self):
        request = updates.network_request()
        self.assertEqual(request.url().toString(), updates.REPOSITORY_URL)
        self.assertFalse(request.url().hasQuery())
        self.assertFalse(request.hasRawHeader(b'Authorization'))
        self.assertFalse(request.hasRawHeader(b'Cookie'))
        self.assertEqual(request.attribute(QNetworkRequest.CacheLoadControlAttribute), QNetworkRequest.AlwaysNetwork)
        self.assertFalse(request.attribute(QNetworkRequest.CacheSaveControlAttribute))
        for attribute in (QNetworkRequest.CookieLoadControlAttribute, QNetworkRequest.CookieSaveControlAttribute,
                          QNetworkRequest.AuthenticationReuseAttribute):
            self.assertEqual(request.attribute(attribute), QNetworkRequest.Manual)
        self.assertEqual(request.attribute(QNetworkRequest.RedirectPolicyAttribute), QNetworkRequest.ManualRedirectPolicy)
        for url in ('http://jmdiez2026.github.io/HidroCAUMAX/plugins.xml',
                    updates.REPOSITORY_URL+'?project=anything'):
            with patch.object(updates, 'REPOSITORY_URL', url), self.assertRaises(ValueError):
                updates.network_request()


class FakeReply(QObject):
    readyRead = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.buffer = bytearray()
        self.http_status = 200
        self.network_error = QNetworkReply.NoError
        self.origin = updates.REPOSITORY_URL
        self.aborted = False
        self.deleted = 0
        self.total_read = 0
        self.buffer_limit = None
        self.none_read = False

    def setReadBufferSize(self, limit):
        self.buffer_limit = limit

    def read(self, limit):
        if not self.buffer and self.none_read:
            return None
        chunk = bytes(self.buffer[:limit])
        del self.buffer[:limit]
        self.total_read += len(chunk)
        return chunk

    def attribute(self, key):
        return self.http_status

    def error(self):
        return self.network_error

    def url(self):
        return QUrl(self.origin)

    def abort(self):
        self.aborted = True
        self.finished.emit()

    def deleteLater(self):
        self.deleted += 1


class FakeManager:
    def __init__(self):
        self.requests = []
        self.replies = []

    def get(self, request):
        self.requests.append(request)
        reply = FakeReply()
        self.replies.append(reply)
        return reply


class NetworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)

    def setUp(self):
        self.manager = FakeManager()
        for mocked in (patch.object(updates, 'QgsNetworkAccessManager', SimpleNamespace(instance=lambda:self.manager)),
                       patch.object(updates, 'installed_version', return_value='1.2.6'),
                       patch.object(updates, 'running_qgis_version', return_value=(3,40,6))):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.checker = updates.UpdateChecker()
        self.addCleanup(self.checker.dispose)
        self.events = []
        self.checker.available.connect(lambda info:self.events.append(('available', info)))
        self.checker.completed.connect(lambda status, detail:self.events.append(('completed', status, detail)))

    def finish(self, raw=None, reply=None):
        reply = reply or self.manager.replies[-1]
        reply.buffer.extend(feed() if raw is None else raw)
        reply.finished.emit()
        return reply

    def test_constructor_is_idle_and_concurrent_checks_share_one_request(self):
        self.assertFalse(self.manager.requests)
        self.assertTrue(self.checker.check())
        self.assertFalse(self.checker.check())
        self.assertTrue(self.checker.busy)
        self.assertTrue(self.checker._timer.isActive())
        self.assertEqual(len(self.manager.requests), 1)
        reply = self.manager.replies[-1]
        self.assertIs(reply.parent(), self.checker)
        self.assertEqual(reply.buffer_limit, updates.MAX_RESPONSE_BYTES+1)

    def test_result_is_available_before_completed_and_repeat_check_uses_network_again(self):
        for count in (1,2):
            self.assertTrue(self.checker.check())
            reply = self.finish()
            self.assertFalse(self.checker.busy)
            self.assertFalse(self.checker._timer.isActive())
            self.assertEqual(reply.deleted, 1)
            self.assertEqual(self.events[-2][0], 'available')
            self.assertEqual(self.events[-1][1], 'available')
            self.assertEqual(len(self.manager.requests), count)

    def test_current_and_errors_are_also_checked_again_without_a_ttl(self):
        for raw, status in ((feed(entry('1.2.6')), 'current'), (b'bad XML', 'error'), (feed(), 'available')):
            self.assertTrue(self.checker.check())
            self.finish(raw)
            self.assertEqual(self.events[-1][1], status)
        self.assertEqual(len(self.manager.requests), 3)

    def test_cancel_aborts_once_and_late_response_cannot_publish(self):
        self.checker.check()
        reply = self.manager.replies[-1]
        self.checker.cancel()
        self.assertTrue(reply.aborted)
        self.assertEqual(reply.deleted, 1)
        self.assertEqual(self.events[-1][1], 'cancelled')
        self.finish(reply=reply)
        reply.readyRead.emit()
        self.assertEqual(len(self.events), 1)
        self.assertFalse(self.checker.busy)

    def test_old_callbacks_do_not_finish_a_new_request(self):
        self.checker.check()
        old = self.manager.replies[-1]
        self.checker.cancel()
        self.checker.check()
        self.finish(reply=old)
        self.assertTrue(self.checker.busy)
        self.finish()
        self.assertEqual([event[1] for event in self.events if event[0]=='completed'], ['cancelled','available'])

    def test_dispose_cancels_and_permanently_disables_new_checks(self):
        self.checker.check()
        reply = self.manager.replies[-1]
        self.checker.dispose()
        self.assertTrue(reply.aborted)
        self.assertFalse(self.checker.check())
        self.finish(reply=reply)
        self.assertEqual(len(self.manager.requests), 1)
        self.assertEqual(len(self.events), 1)

    def test_real_qt_timeout_aborts_and_ignores_late_finish(self):
        with patch.object(updates, 'TIMEOUT_MS', 10):
            self.checker.check()
        reply = self.manager.replies[-1]
        for _ in range(50):
            if reply.aborted:
                break
            QTest.qWait(10)
        self.assertTrue(reply.aborted)
        self.assertEqual(self.events[-1][1], 'timeout')
        self.finish(reply=reply)
        self.assertEqual(len(self.events), 1)

    def test_oversize_ready_read_is_bounded_and_abort_is_not_reentrant(self):
        self.checker.check()
        reply = self.manager.replies[-1]
        reply.buffer.extend(b'x'*(updates.MAX_RESPONSE_BYTES*2))
        reply.readyRead.emit()
        self.assertTrue(reply.aborted)
        self.assertEqual(reply.total_read, updates.MAX_RESPONSE_BYTES+1)
        self.assertEqual(reply.deleted, 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[-1][1], 'error')

    def test_oversize_final_chunk_is_rejected_without_aborting_finished_reply(self):
        self.checker.check()
        reply = self.finish(b'x'*(updates.MAX_RESPONSE_BYTES*2))
        self.assertFalse(reply.aborted)
        self.assertEqual(reply.total_read, updates.MAX_RESPONSE_BYTES+1)
        self.assertEqual(reply.deleted, 1)
        self.assertEqual(self.events[-1][1], 'error')

    def test_http_tls_and_origin_failures_never_emit_available(self):
        for failure in ('http','tls','origin'):
            with self.subTest(failure=failure):
                self.events.clear()
                self.checker.check()
                reply = self.manager.replies[-1]
                if failure=='http':
                    reply.http_status = 302
                elif failure=='tls':
                    reply.network_error = QNetworkReply.SslHandshakeFailedError
                else:
                    reply.origin = 'https://another.example/plugins.xml'
                self.finish(reply=reply)
                self.assertEqual(len(self.events), 1)
                self.assertEqual(self.events[-1][1], 'error')

    def test_network_start_failure_and_empty_none_read_are_reported(self):
        with patch.object(self.manager, 'get', side_effect=RuntimeError('offline')):
            self.assertFalse(self.checker.check())
        self.assertEqual(self.events[-1][1], 'error')
        self.assertFalse(self.checker.busy)
        self.checker.check()
        reply = self.manager.replies[-1]
        reply.none_read = True
        reply.http_status = None
        self.finish(b'')
        self.assertEqual(self.events[-1][1], 'error')

    def test_reentrant_check_from_result_listener_keeps_new_request_alive(self):
        self.checker.available.connect(lambda info:self.checker.check())
        self.checker.check()
        self.finish()
        self.assertEqual(len(self.manager.requests), 2)
        self.assertTrue(self.checker.busy)
        self.assertTrue(self.checker._timer.isActive())


if __name__ == '__main__':
    unittest.main()
