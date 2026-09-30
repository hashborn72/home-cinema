import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app
from test_catalog import release


class TrustedLanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def app(self, mode):
        with patch.dict(os.environ, {'CINEMA_AUTH_MODE':mode}):
            return create_app(self.path, 'existing-private-token')

    def test_anonymous_reads_and_library_writes(self):
        app=self.app('trusted-lan')
        app.state.catalog.ingest('exkinoray',[release('trusted-test','Trusted Film (2020)')])
        card=app.state.catalog.search('Trusted Film')['results'][0]
        client=TestClient(app)
        for path in ['/api/v1/library','/api/v1/catalog/home','/api/v1/probe']:
            self.assertEqual(client.get(path).status_code,200)
        response=client.post('/api/v1/library/'+card['id'],json={'favorite':True})
        self.assertEqual(response.status_code,200)
        restarted=TestClient(self.app('trusted-lan'))
        rows=restarted.get('/api/v1/library').json()['shelves']
        self.assertEqual(len(next(s['results'] for s in rows if s['id']=='favorite')),1)

    def test_existing_token_clients_still_work(self):
        client=TestClient(self.app('trusted-lan'))
        self.assertEqual(client.get('/api/v1/library',headers={'Authorization':'Bearer existing-private-token'}).status_code,200)

    def test_discovery_is_explicit_and_does_not_expose_credentials(self):
        client=TestClient(self.app('trusted-lan'))
        response=client.get('/api/v1/connection')
        self.assertEqual(response.json(),{'service':'home-cinema','auth_mode':'trusted-lan'})
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn('existing-private-token',response.text)
        self.assertEqual(client.post('/api/v1/pair',json={'code':'123456789012'}).status_code,409)

    def test_token_mode_is_still_protected(self):
        client=TestClient(self.app('token'))
        self.assertEqual(client.get('/api/v1/connection').json()['auth_mode'],'token')
        self.assertEqual(client.get('/api/v1/library').status_code,401)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaises(ValueError): self.app('trust-all-typo')
