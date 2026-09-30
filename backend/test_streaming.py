import unittest
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from server import create_app

class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=create_app(Path(self.temp.name),'token');self.client=TestClient(self.app)
        with self.app.state.catalog.db() as db:
            db.execute('INSERT INTO stream_tickets VALUES (?,?,?,?,?,?,?)',('opaque',123,3,'anwap:file','https://z37.anwap.be/on/video.mp4',time.time(),time.time()+30))
    def tearDown(self):self.temp.cleanup()
    def test_unknown_expired_and_invalid_range(self):
        self.assertEqual(self.client.get('/play/nope').status_code,404)
        self.assertEqual(self.client.get('/play/opaque',headers={'Range':'bytes=1-2,4-5'}).status_code,416)
        with self.app.state.catalog.db() as db:db.execute('UPDATE stream_tickets SET expires_at=0')
        self.assertEqual(self.client.get('/play/opaque').status_code,404)
    def test_range_forwarded_without_device_credentials(self):
        def handle(req):
            self.assertEqual(req.headers['range'],'bytes=2-5')
            self.assertNotIn('authorization',req.headers)
            return httpx.Response(206,content=b'2345',headers={'Content-Type':'video/mp4','Content-Range':'bytes 2-5/10','Content-Length':'4'})
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch('streaming.httpx.AsyncClient',return_value=upstream):
            response=self.client.get('/play/opaque',headers={'Range':'bytes=2-5'})
        self.assertEqual(response.status_code,206);self.assertEqual(response.content,b'2345')
        self.assertEqual(response.headers['content-range'],'bytes 2-5/10')
    def test_html_error_not_forwarded(self):
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(403,content=b'secret-error')))
        with patch('streaming.httpx.AsyncClient',return_value=upstream):response=self.client.get('/play/opaque')
        self.assertEqual(response.status_code,502);self.assertNotIn('secret-error',response.text)
    def test_catalog_image_unknown(self):self.assertEqual(self.client.get('/images/no-such').status_code,404)

    def test_changed_file_revision_is_not_played(self):
        with self.app.state.catalog.db() as db:db.execute('UPDATE stream_tickets SET refreshed_at=0')
        with patch('streaming.resolve',return_value={'file_key':'different','stream_url':'https://z37.anwap.be/new.mp4'}):
            self.assertEqual(self.client.get('/play/opaque').status_code,409)

    def test_refresh_retains_range_and_does_not_redirect_client(self):
        with self.app.state.catalog.db() as db:db.execute('UPDATE stream_tickets SET refreshed_at=0')
        upstream=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(206,content=b'X',headers={'Content-Type':'video/mp4','Content-Range':'bytes 0-0/100'})))
        with patch('streaming.resolve',return_value={'file_key':'anwap:file','stream_url':'https://z37.anwap.be/new.mp4'}),patch('streaming.httpx.AsyncClient',return_value=upstream):
            response=self.client.get('/play/opaque',headers={'Range':'bytes=0-0'})
        self.assertEqual(response.status_code,206);self.assertNotIn('location',response.headers)
