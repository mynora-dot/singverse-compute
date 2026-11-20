import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from singverse_compute.core.errors import ComputeError
from singverse_compute.infrastructure.model_download import download_bundle, download_source, public_socket


class ModelDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'model.zip'
        self.data = b'controlled archive bytes'
        self.source = {'url': 'https://models.example.test/model.zip', 'sha256': hashlib.sha256(self.data).hexdigest()}
        self.cancel = threading.Event()

    def response(self, status=200, **headers):
        response = io.BytesIO(self.data)
        response.status = status
        response.getheader = lambda key: headers.get(key)
        return response

    def download(self, responses, addresses=None, maximum=1024):
        addresses = addresses or [(2, 1, 6, '', ('93.184.215.14', 443))]
        connection = MagicMock()
        connection.getresponse.side_effect = responses
        with patch('singverse_compute.infrastructure.model_download.socket.getaddrinfo', return_value=addresses), \
                patch('singverse_compute.infrastructure.model_download.socket.create_connection') as connect, \
                patch('singverse_compute.infrastructure.model_download.ssl.create_default_context') as tls, \
                patch('singverse_compute.infrastructure.model_download.HTTPSConnection', return_value=connection):
            download_bundle(self.source, self.path, maximum, self.cancel, lambda size: None)
        return connection, connect, tls

    def test_complete_download_pins_public_address_checks_tls_and_omits_credentials(self):
        connection, connect, tls = self.download([self.response(**{'Content-Length': str(len(self.data))})])
        self.assertEqual(self.path.read_bytes(), self.data)
        connect.assert_called_once_with(('93.184.215.14', 443), timeout=20)
        self.assertEqual(tls.return_value.wrap_socket.call_args.kwargs['server_hostname'], 'models.example.test')
        self.assertNotIn('Authorization', connection.request.call_args.kwargs['headers'])

    def test_hash_mismatch_truncation_and_size_limit_fail(self):
        for digest, length, maximum in [('0'*64, str(len(self.data)), 1024),
                                      (self.source['sha256'], str(len(self.data)+1), 1024),
                                      (self.source['sha256'], None, 1)]:
            self.source['sha256'] = digest
            with self.subTest(length=length, maximum=maximum), self.assertRaises(ComputeError):
                self.download([self.response(**({'Content-Length': length} if length else {}))], maximum=maximum)

    def test_private_and_mixed_dns_addresses_are_rejected(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '224.0.0.1'):
            addresses = [(2, 1, 6, '', ('93.184.215.14', 443)), (2, 1, 6, '', (address, 443))]
            with self.subTest(address=address), self.assertRaises(ComputeError):
                self.download([], addresses)
        self.assertFalse(self.path.exists())

    def test_redirect_cannot_downgrade_https_or_read_local_files(self):
        for location in ('http://models.example.test/unsafe', 'file:///private/file', 'https://user:secret@example.test/model'):
            with self.subTest(location=location), self.assertRaises(ComputeError):
                self.download([self.response(302, Location=location)])
        self.assertFalse(self.path.exists())

    def test_redirect_is_resolved_and_downloaded_with_same_digest(self):
        connection, connect, _ = self.download([self.response(302, Location='/asset.zip'), self.response()])
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(connection.request.call_args.args[:2], ('GET', '/asset.zip'))
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_cancelled_download_never_opens_connection(self):
        self.cancel.set()
        with self.assertRaises(ComputeError):
            self.download([])
        self.assertFalse(self.path.exists())

    def test_public_ipv6_connection_failure_falls_back_to_checked_ipv4(self):
        import time
        addresses = [(10, 1, 6, '', ('2606:4700:4700::1111', 443)), (2, 1, 6, '', ('1.1.1.1', 443))]
        connected = MagicMock()
        with patch('singverse_compute.infrastructure.model_download.socket.getaddrinfo', return_value=addresses), \
                patch('singverse_compute.infrastructure.model_download.socket.create_connection', side_effect=[OSError('IPv6 unavailable'), connected]) as connect:
            self.assertIs(public_socket('models.example.test', 443, time.monotonic()+60), connected)
        self.assertEqual(connect.call_args.args[0], ('1.1.1.1', 443))

    def test_source_rejects_credentials_controls_protocols_and_extra_fields(self):
        for url in ('http://example.test/a', 'file:///a', 'https://u:p@example.test/a', 'https://example.test/a\nX: bad'):
            with self.subTest(url=url), self.assertRaises(ComputeError):
                download_source({**self.source, 'url': url})
        with self.assertRaises(ComputeError):
            download_source({**self.source, 'token': 'never-forward'})
