import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app
from settings import endpoint, public_url, torrserver_public_url
from stack_init import initialize
from torrents import jackett_url


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data, self.jackett, self.torr, self.imports = [self.root/n for n in ('data','jackett','torr','imports')]
        self.imports.mkdir()

    def tearDown(self): self.temp.cleanup()

    def init(self): initialize(self.data,self.jackett,self.torr,self.imports)

    def test_fresh_stack_seed_and_repeat_preserves_user_config(self):
        self.init()
        config = self.jackett/'Jackett/ServerConfig.json'
        first = json.loads(config.read_text())
        self.assertEqual(len(first['APIKey']),32)
        self.assertEqual(first['Port'],9117)
        self.assertEqual((self.data/'jackett-key').read_text(),first['APIKey'])
        for name in ('rutor','lostfilm','exkinoray'):
            self.assertTrue((self.jackett/f'Jackett/Indexers/{name}.json').is_file())
        user = self.jackett/'Jackett/Indexers/lostfilm.json'
        user.write_text('[{"id":"cookieheader","value":"PRIVATE"}]')
        self.init()
        self.assertEqual(json.loads(config.read_text()),first)
        self.assertIn('PRIVATE',user.read_text())

    def test_optional_private_import_and_no_overwrite(self):
        folder = self.imports/'Jackett/Indexers'
        folder.mkdir(parents=True)
        (folder.parent/'ServerConfig.json').write_text(json.dumps({'APIKey':'private-api','Port':8091,'AdminPassword':'private-admin'}))
        (folder/'lostfilm.json').write_text('[{"id":"cookieheader","value":"private-cookie"}]')
        (self.imports/'tmdb-token').write_text('private-tmdb')
        (self.imports/'device-token').write_text('private-device')
        with sqlite3.connect(self.imports/'library.sqlite3') as db:
            db.execute('CREATE TABLE example (id INTEGER)')
            db.execute('INSERT INTO example VALUES (42)')
        db.close()
        self.init()
        self.assertEqual((self.data/'jackett-key').read_text(),'private-api')
        self.assertEqual((self.data/'tmdb-token').read_text(),'private-tmdb')
        self.assertIn('private-cookie',(self.jackett/'Jackett/Indexers/lostfilm.json').read_text())
        with sqlite3.connect(self.data/'library.sqlite3') as db:
            self.assertEqual(db.execute('SELECT id FROM example').fetchone()[0],42)
            db.execute('UPDATE example SET id=43')
        (self.imports/'tmdb-token').write_text('do-not-overwrite')
        self.init()
        self.assertEqual((self.data/'tmdb-token').read_text(),'private-tmdb')
        with sqlite3.connect(self.data/'library.sqlite3') as db:
            self.assertEqual(db.execute('SELECT id FROM example').fetchone()[0],43)

    def test_only_approved_legacy_jackett_rewritten_to_internal(self):
        with patch.dict(os.environ, {'JACKETT_URL':'http://jackett:9117','JACKETT_LEGACY_URLS':'http://192.168.1.144:8091'}):
            value=jackett_url('http://192.168.1.144:8091/dl/rutor/?path=opaque&jackett_apikey=old','rutor','new')
            self.assertTrue(value.startswith('http://jackett:9117/dl/rutor/'))
            self.assertIn('path=opaque',value)
            self.assertIn('jackett_apikey=new',value)
            for link in ('http://evil/dl/rutor/','http://jackett:9117/config','http://jackett:9117/dl/exkinoray/'):
                with self.assertRaises(ValueError): jackett_url(link,'rutor','new')

    def test_import_torrserver_settings_once_without_reset(self):
        folder=self.imports/'TorrServer'
        folder.mkdir()
        (folder/'settings.json').write_text('{"UseDisk":false,"CacheSize":134217728}')
        (folder/'config.db').write_bytes(b'fixture-boltdb')
        self.init()
        self.assertEqual((self.torr/'config.db').read_bytes(),b'fixture-boltdb')
        self.assertTrue((self.data/'torrserver-setup-v1').exists())
        (self.torr/'settings.json').write_text('{"CacheSize":67108864}')
        self.init()
        self.assertEqual(json.loads((self.torr/'settings.json').read_text())['CacheSize'],67108864)

    def test_offline_wal_snapshot_import(self):
        source=sqlite3.connect(self.imports/'library.sqlite3')
        source.execute('PRAGMA journal_mode=WAL')
        source.execute('CREATE TABLE example (id INTEGER)')
        source.execute('INSERT INTO example VALUES (99)')
        source.commit()
        source.close()
        self.init()
        self.assertFalse((self.data/'library.sqlite3.importing').exists())
        with sqlite3.connect(self.data/'library.sqlite3') as db:
            self.assertEqual(db.execute('SELECT id FROM example').fetchone()[0],99)

    def test_public_endpoints_and_install_page(self):
        with patch.dict(os.environ, {'CINEMA_PUBLIC_URL':'http://192.168.1.144:8093','TORRSERVER_PUBLIC_URL':'http://192.168.1.144:8090'}):
            client=TestClient(create_app(self.root/'app','test-token'))
            response=client.get('/api/v1/probe',headers={'Authorization':'Bearer test-token'}).json()
            self.assertEqual(response['stream_url'],'http://192.168.1.144:8093/probe-media')
            self.assertIn(public_url(),client.get('/install').text)
            self.assertNotIn('192.168.0.221',client.get('/install').text)
            self.assertEqual(torrserver_public_url(),'http://192.168.1.144:8090')

    def test_invalid_endpoint_rejected(self):
        for value in ('file:///etc/passwd','http://user:secret@host','http://host/path','http://host?token=x'):
            with patch.dict(os.environ, {'TEST_URL':value}):
                with self.assertRaises(ValueError): endpoint('TEST_URL','http://localhost')
