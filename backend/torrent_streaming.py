"""Ticket-scoped relay to the internal TorrServer; media never touches disk."""
import re
import secrets
import time
from urllib.parse import quote, urlencode

import anyio
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from settings import public_url, torrserver_url
from torrents import video_files


class TorrentStreams:
    def __init__(self, catalog, torrents):
        self.catalog, self.torrents = catalog, torrents
        with catalog.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS torrent_stream_tickets (
                ticket TEXT PRIMARY KEY, release_id TEXT NOT NULL, hash TEXT NOT NULL,
                file_id INTEGER NOT NULL, path TEXT NOT NULL, expires_at REAL NOT NULL)''')

    def ticket(self, release_id, item):
        match = re.fullmatch(r'([a-f0-9]{40}):([1-9][0-9]*)', item['file_key'])
        if not match: raise ValueError('Invalid prepared torrent')
        token, now = secrets.token_urlsafe(32), time.time()
        with self.catalog.db() as db:
            db.execute('DELETE FROM torrent_stream_tickets WHERE expires_at<?', (now,))
            db.execute('INSERT INTO torrent_stream_tickets VALUES (?,?,?,?,?,?)',
                       (token, release_id, match[1], int(match[2]), item['title'], now + 43200))
        return public_url() + '/torrent-play/' + token

    def restore(self, row):
        # TorrServer's RAM state can expire/restart while prepared files remain in SQLite.
        status = self.torrents.add(self.torrents.release(row['release_id']))
        if status.get('hash', '').lower() != row['hash']:
            raise ValueError('Torrent changed')
        if status.get('file_stats'):
            file = next((f for f in video_files(status) if f['id'] == row['file_id']), None)
            if not file or file['path'] != row['path']: raise ValueError('Torrent file changed')

    async def stream(self, token, request: Request):
        with self.catalog.db() as db:
            row = db.execute('SELECT * FROM torrent_stream_tickets WHERE ticket=? AND expires_at>?',
                             (token, time.time())).fetchone()
        if not row: raise HTTPException(404, 'Playback link expired')
        byte_range = request.headers.get('range')
        if byte_range:
            match = re.fullmatch(r'bytes=(?:(\d+)-(\d*)|-(\d+))', byte_range)
            if (not match or (match[3] is not None and int(match[3]) == 0) or
                    (match[2] and int(match[2]) < int(match[1]))):
                raise HTTPException(416, 'Single byte range required')
        headers = {'Accept-Encoding': 'identity'}
        if byte_range: headers['Range'] = byte_range
        url = torrserver_url() + '/stream/' + quote(row['path'].rsplit('/', 1)[-1], safe='') + '?' + urlencode(
            {'link': row['hash'], 'index': row['file_id'], 'play': ''})
        client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=8),
                                   follow_redirects=False, trust_env=False)
        upstream = None

        async def close():
            # Disconnect cancellation must not cancel resource cleanup as well.
            with anyio.CancelScope(shield=True):
                try:
                    if upstream is not None: await upstream.aclose()
                finally:
                    await client.aclose()

        try:
            status = await client.post(torrserver_url() + '/torrents', json={'action': 'get', 'hash': row['hash']})
            if status.status_code == 404:
                await run_in_threadpool(self.restore, row)
            else:
                status.raise_for_status()
                if status.json().get('hash', '').lower() != row['hash']:
                    raise ValueError('Unexpected torrent')
            upstream = await client.send(client.build_request(request.method, url, headers=headers), stream=True)
            response_headers = {k: v for k, v in upstream.headers.items() if k.lower() in (
                'content-length', 'content-range', 'accept-ranges', 'content-type')}
            response_headers['Cache-Control'] = 'no-store'
            if upstream.status_code == 416:
                await close()
                return Response(status_code=416, headers={k: v for k, v in response_headers.items()
                                                          if k.lower() not in ('content-length', 'content-type')})
            if (upstream.status_code not in (200, 206) or
                    not upstream.headers.get('content-type', '').lower().startswith(
                        ('video/', 'application/octet-stream', 'application/x-matroska')) or
                    upstream.headers.get('content-encoding', 'identity').lower() != 'identity'):
                raise HTTPException(502, 'Source did not return video')
            if request.method == 'HEAD':
                await close()
                return Response(status_code=upstream.status_code, headers=response_headers)
        except HTTPException:
            await close()
            raise
        except BaseException as error:
            await close()
            if not isinstance(error, Exception): raise
            raise HTTPException(502, 'Video source unavailable') from None

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw(chunk_size=65536): yield chunk
            finally:
                await close()
        return StreamingResponse(chunks(), status_code=upstream.status_code, headers=response_headers,
                                 background=BackgroundTask(close))
