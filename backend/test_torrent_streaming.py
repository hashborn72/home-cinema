import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

import httpx
from fastapi import Request
from fastapi.testclient import TestClient
from server import create_app
from test_catalog import release
from torrent_streaming import TorrentStreams


class Bytes(httpx.AsyncByteStream):
    def __init__(self, data=b'2345'):
        self.data, self.closed, self.iterated = data, False, False
    async def __aiter__(self):
        self.iterated = True
        yield self.data
    async def aclose(self): self.closed = True


class TorrentStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name), 'token')
        self.client = TestClient(self.app, headers={'Authorization': 'Bearer token'})
        self.cat = self.app.state.catalog
        self.cat.ingest('exkinoray', [release('release-a', 'Film (2020)')])
        with self.cat.db() as db:
            db.execute('INSERT INTO prepared_releases VALUES (?,?,?,?,?,?)', (
                'release-a', 'ready', 'a'*40, json.dumps([{'id': 1, 'path': 'folder/film.mkv'}]), time.time(), None))
        with patch.dict(os.environ, {'CINEMA_PUBLIC_URL': 'http://192.168.1.144:8093'}):
            item = self.client.post('/api/v1/playback/file-sessions', json={
                'release_id': 'release-a', 'file_id': 1}).json()
        self.path = urlsplit(item['stream_url']).path
        self.assertTrue(item['stream_url'].startswith('http://192.168.1.144:8093/torrent-play/'))
        self.env = patch.dict(os.environ, {'TORRSERVER_URL': 'http://torrserver:8090'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def response(self, status=206, data=b'2345', headers=None):
        self.body = Bytes(data)
        return httpx.Response(status, stream=self.body, headers=headers or {
            'Content-Type': 'video/x-matroska', 'Content-Range': 'bytes 2-5/10',
            'Accept-Ranges': 'bytes', 'Content-Length': str(len(data))})

    def transport(self, handle):
        def request(req):
            if req.method == 'POST': return httpx.Response(200, json={'hash': 'a'*40})
            return handle(req)
        return httpx.MockTransport(request)

    def test_ranges_and_internal_target_without_device_credentials(self):
        for value in ('bytes=2-5', 'bytes=2-', 'bytes=-4'):
            def handle(req):
                self.assertEqual(req.url.host, 'torrserver')
                self.assertEqual(req.url.port, 8090)
                self.assertEqual(req.url.path, '/stream/film.mkv')
                self.assertEqual(parse_qs(req.url.query.decode())['link'], ['a'*40])
                self.assertEqual(parse_qs(req.url.query.decode())['index'], ['1'])
                self.assertEqual(req.headers['range'], value)
                self.assertEqual(req.headers['accept-encoding'], 'identity')
                self.assertNotIn('authorization', req.headers)
                self.assertNotIn('cookie', req.headers)
                return self.response()
            client = httpx.AsyncClient(transport=self.transport(handle))
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
                response = self.client.get(self.path+'?url=http://evil/admin', headers={'Range': value, 'Cookie': 'private'})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.content, b'2345')
            self.assertEqual(response.headers['content-range'], 'bytes 2-5/10')
            self.assertEqual(response.headers['content-length'], '4')
            self.assertTrue(self.body.closed)
            self.assertTrue(client.is_closed)

    def test_head_preserves_metadata_and_closes_without_reading(self):
        def handle(req):
            self.assertEqual(req.method, 'HEAD')
            return self.response()
        client = httpx.AsyncClient(transport=self.transport(handle))
        with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
            response = self.client.head(self.path, headers={'Range': 'bytes=2-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'')
        self.assertEqual(response.headers['content-range'], 'bytes 2-5/10')
        self.assertEqual(response.headers['content-length'], '4')
        self.assertTrue(client.is_closed)
        self.assertTrue(self.body.closed)
        self.assertFalse(self.body.iterated)

    def test_upstream_unsatisfiable_range_preserves_total_size(self):
        client = httpx.AsyncClient(transport=self.transport(lambda req: self.response(
            416, b'private upstream error', {'Content-Type': 'text/plain', 'Content-Range': 'bytes */10'})))
        with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
            response = self.client.get(self.path, headers={'Range': 'bytes=20-'})
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response.headers['content-range'], 'bytes */10')
        self.assertEqual(response.content, b'')
        self.assertTrue(self.body.closed)

    def test_expiry_invalid_ranges_and_missing_ticket_do_not_contact_source(self):
        with patch('torrent_streaming.httpx.AsyncClient') as client:
            for value in ('bytes=5-1', 'bytes=-0', 'bytes=1-2,4-5', 'cats=1-2'):
                self.assertEqual(self.client.get(self.path, headers={'Range': value}).status_code, 416)
            self.assertEqual(self.client.get('/torrent-play/missing').status_code, 404)
            with self.cat.db() as db: db.execute('UPDATE torrent_stream_tickets SET expires_at=0')
            self.assertEqual(self.client.get(self.path).status_code, 404)
            client.assert_not_called()

    def test_redirect_html_and_encoded_responses_are_not_forwarded(self):
        for status, headers in ((302, {'Location': 'http://evil/private'}),
                                (200, {'Content-Type': 'text/html'}),
                                (200, {'Content-Type': 'video/mp4', 'Content-Encoding': 'gzip'})):
            client = httpx.AsyncClient(transport=self.transport(lambda req: self.response(status, b'private', headers)))
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
                response = self.client.get(self.path)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('private', response.text)
            self.assertNotIn('location', response.headers)
            self.assertTrue(client.is_closed)

    def test_restart_restores_same_torrent_from_private_release(self):
        calls = []
        def handle(req):
            calls.append(req)
            return httpx.Response(404) if req.method == 'POST' else self.response()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        restored = {'hash': 'a'*40, 'file_stats': [{'id': 1, 'path': 'folder/film.mkv'}]}
        with patch('torrent_streaming.httpx.AsyncClient', return_value=client), patch.object(
                self.app.state.torrents, 'add', return_value=restored) as add:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(calls), 2)
        self.assertEqual(add.call_args.args[0]['id'], 'release-a')

    def test_changed_torrent_or_file_is_not_played_after_restore(self):
        for status in ({'hash': 'b'*40}, {'hash': 'a'*40, 'file_stats': [{'id': 1, 'path': 'different.mkv'}]}):
            calls = []
            def handle(req):
                calls.append(req)
                return httpx.Response(404)
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client), patch.object(
                    self.app.state.torrents, 'add', return_value=status):
                response = self.client.get(self.path)
            self.assertEqual(response.status_code, 502)
            self.assertEqual(len(calls), 1)
            self.assertTrue(client.is_closed)

    def test_early_consumer_close_closes_upstream(self):
        async def check():
            streams = TorrentStreams(self.cat, self.app.state.torrents)
            client = httpx.AsyncClient(transport=self.transport(lambda req: self.response(200, b'x'*131072)))
            request = Request({'type': 'http', 'method': 'GET', 'headers': []})
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
                response = await streams.stream(self.path.rsplit('/', 1)[1], request)
                iterator = response.body_iterator
                self.assertEqual(len(await anext(iterator)), 65536)
                await iterator.aclose()
            self.assertTrue(self.body.closed)
            self.assertTrue(client.is_closed)
        asyncio.run(check())

    def test_disconnect_cancellation_closes_pending_stream(self):
        class BlockingBytes(Bytes):
            async def __aiter__(self):
                yield b'x'*65536
                await asyncio.Event().wait()
        async def check():
            body = BlockingBytes()
            client = httpx.AsyncClient(transport=self.transport(lambda req: httpx.Response(
                200, stream=body, headers={'Content-Type': 'video/mp4'})))
            request = Request({'type': 'http', 'method': 'GET', 'headers': []})
            streams = TorrentStreams(self.cat, self.app.state.torrents)
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
                response = await streams.stream(self.path.rsplit('/', 1)[1], request)
                self.assertEqual(len(await anext(response.body_iterator)), 65536)
                pending = asyncio.create_task(anext(response.body_iterator))
                await asyncio.sleep(0)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError): await pending
            self.assertTrue(body.closed)
            self.assertTrue(client.is_closed)
        asyncio.run(check())

    def test_read_failure_closes_both_resources(self):
        class FailedBytes(Bytes):
            async def __aiter__(self):
                yield b'x'*65536
                raise httpx.ReadError('upstream lost')
        async def check():
            body = FailedBytes()
            client = httpx.AsyncClient(transport=self.transport(lambda req: httpx.Response(
                200, stream=body, headers={'Content-Type': 'video/mp4'})))
            request = Request({'type': 'http', 'method': 'GET', 'headers': []})
            streams = TorrentStreams(self.cat, self.app.state.torrents)
            with patch('torrent_streaming.httpx.AsyncClient', return_value=client):
                response = await streams.stream(self.path.rsplit('/', 1)[1], request)
                await anext(response.body_iterator)
                with self.assertRaises(httpx.ReadError): await anext(response.body_iterator)
            self.assertTrue(body.closed)
            self.assertTrue(client.is_closed)
        asyncio.run(check())
