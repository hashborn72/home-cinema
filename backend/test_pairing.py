import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from pairing import Pairing
from server import create_app


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.client = TestClient(create_app(self.path, 'legacy-token'))
        self.pairing = Pairing(self.path / 'library.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_single_use_and_device_auth_survives_restart(self):
        code = self.pairing.issue('TV')
        result = self.client.post('/api/v1/pair', json={'code': code})
        self.assertEqual(result.status_code, 200)
        token = result.json()['token']
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertNotIn(token.encode(), (self.path / 'library.sqlite3').read_bytes())
        self.assertEqual(self.client.post('/api/v1/pair', json={'code': code}).status_code, 400)
        other = TestClient(create_app(self.path, 'legacy-token'))
        self.assertEqual(other.get('/api/v1/library', headers={'Authorization': 'Bearer '+token}).status_code, 200)
        self.assertEqual(other.get('/api/v1/library', headers={'Authorization': 'Bearer legacy-token'}).status_code, 200)
        self.assertEqual(other.get('/api/v1/library').status_code, 401)

    def test_expired_code(self):
        code = self.pairing.issue('TV', ttl=-1)
        self.assertEqual(self.client.post('/api/v1/pair', json={'code': code}).status_code, 400)

    def test_rate_limit_counts_failed_codes(self):
        with patch('pairing.time.time', return_value=120):
            for _ in range(10):
                self.assertEqual(self.client.post('/api/v1/pair', json={'code': '000000000000'}).status_code, 400)
            self.assertEqual(self.client.post('/api/v1/pair', json={'code': '000000000000'}).status_code, 429)
        with patch('pairing.time.time', return_value=181):
            self.assertEqual(self.client.post('/api/v1/pair', json={'code': '000000000000'}).status_code, 400)

    def test_concurrent_code_only_one_winner(self):
        code = self.pairing.issue('TV')
        def redeem(_):
            try: return self.pairing.redeem(code)
            except ValueError: return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(x is not None for x in pool.map(redeem, range(2))), 1)

    def test_validation(self):
        for code in ('123', 'x'*12, '1'*100):
            self.assertEqual(self.client.post('/api/v1/pair', json={'code': code}).status_code, 422)

    def test_download_is_fixed_file_supports_head_and_range(self):
        apk = self.path / 'test.apk'
        apk.write_bytes(b'0123456789')
        with patch('server.Path', return_value=apk):
            response = self.client.head('/downloads/home-cinema.apk')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-length'], '10')
            self.assertEqual(response.content, b'')
            response = self.client.get('/downloads/home-cinema.apk', headers={'Range':'bytes=2-5'})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b'2345')
        self.assertEqual(self.client.get('/downloads/store-password').status_code, 404)
