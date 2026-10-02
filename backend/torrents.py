"""Private Jackett download -> TorrServer metadata. Never publish upstream URLs."""
import concurrent.futures
import json
import os
import re
import threading
import time
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import httpx

from settings import jackett_url as jackett_endpoint, torrserver_url
VIDEO = {'.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.m2ts', '.webm', '.mpg', '.mpeg'}


def valid_magnet(link):
    # Forward only the infohash: trackers/webseeds supplied by an untrusted feed
    # must not become arbitrary URL fetches on the production TorrServer.
    if urlparse(link).scheme != 'magnet': raise ValueError('Not a magnet')
    for value in parse_qs(urlparse(link).query).get('xt', []):
        if re.fullmatch(r'urn:btih:(?:[a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', value):
            return 'magnet:?xt=' + value
    raise ValueError('Invalid magnet')


def jackett_url(link, source, key):
    p = urlparse(link)
    allowed = urlparse(jackett_endpoint())
    origins = {(allowed.scheme, allowed.netloc)}
    for value in os.environ.get('JACKETT_LEGACY_URLS', '').split(','):
        if value.strip():
            legacy = urlparse(value.strip())
            if legacy.scheme not in ('http','https') or not legacy.netloc or legacy.username or legacy.password or legacy.path or legacy.query or legacy.fragment:
                raise ValueError('Invalid legacy Jackett origin')
            origins.add((legacy.scheme, legacy.netloc))
    if ((p.scheme, p.netloc) not in origins or p.fragment or
            p.path != '/dl/' + source + '/'):
        raise ValueError('Unexpected download endpoint')
    params = parse_qs(p.query)
    params['jackett_apikey'] = [key]
    return urlunparse((allowed.scheme, allowed.netloc, p.path, '', urlencode(params, doseq=True), ''))


def video_files(status):
    if not re.fullmatch(r'[a-fA-F0-9]{40}', status.get('hash', '')):
        raise ValueError('Invalid torrent hash')
    files = []
    seen = set()
    for file in status.get('file_stats') or []:
        name = file.get('path', '')
        index = file.get('id')
        if (not isinstance(name, str) or '.' not in name or '.' + name.rsplit('.', 1)[-1].lower() not in VIDEO
                or type(index) is not int or index < 1 or index in seen):
            continue
        seen.add(index)
        files.append({'id': index, 'path': name[:2000], 'size': file.get('length'),
                      'sample': bool(re.search(r'(?i)(?:^|[/_. -])sample(?:[/_. -]|$)', name))})
    return sorted(files, key=lambda f: (f['sample'], f['path'].casefold()))


class Torrents:
    def __init__(self, catalog):
        self.catalog = catalog
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.lock = threading.Lock()
        self.pending = set()
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS prepared_releases(release_id TEXT PRIMARY KEY, status TEXT NOT NULL, hash TEXT, files TEXT NOT NULL DEFAULT "[]", updated_at REAL NOT NULL, error TEXT)')
            db.execute("UPDATE prepared_releases SET status='error',error='preparation_interrupted' WHERE status='preparing'")

    def release(self, rid):
        with self.catalog.db() as db:
            r = db.execute('SELECT payload FROM catalog_releases WHERE id=?', (rid,)).fetchone()
        if not r: raise KeyError(rid)
        return json.loads(r['payload'])

    def state(self, rid):
        self.release(rid)
        with self.catalog.db() as db:
            row = db.execute('SELECT * FROM prepared_releases WHERE release_id=?', (rid,)).fetchone()
        if not row: return {'release_id': rid, 'status': 'new', 'files': []}
        return {'release_id': rid, 'status': row['status'], 'files': json.loads(row['files']) if row['status']=='ready' else [], 'error': row['error']}

    def prepare(self, rid):
        self.release(rid)
        with self.lock:
            if rid in self.pending: return self.state(rid)
            if len(self.pending) >= 2: raise RuntimeError('busy')
            self.pending.add(rid)
            with self.catalog.db() as db:
                db.execute("INSERT INTO prepared_releases(release_id,status,updated_at) VALUES (?,'preparing',?) ON CONFLICT(release_id) DO UPDATE SET status='preparing',error=NULL,updated_at=excluded.updated_at", (rid,time.time()))
            self.pool.submit(self._prepare, rid)
        return self.state(rid)

    def add(self, release):
        link = release['download_url']
        with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
            if link.startswith('magnet:'):
                result = client.post(torrserver_url() + '/torrents', json={'action':'add','link':valid_magnet(link),'save_to_db':False})
            else:
                key = (self.catalog.data_dir/'jackett-key').read_text().strip()
                url = jackett_url(link,release['source'],key)
                with client.stream('GET',url) as response:
                    if response.status_code in (301,302,303,307,308):
                        magnet = valid_magnet(response.headers.get('location',''))
                        result = client.post(torrserver_url()+'/torrents',json={'action':'add','link':magnet,'save_to_db':False})
                    else:
                        response.raise_for_status()
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > 4_000_000: raise ValueError('Torrent too large')
                        if not body.startswith(b'd') or not body.endswith(b'e'): raise ValueError('Not a torrent')
                        # Multipart prevents the Jackett key being logged as a link by TorrServer.
                        result = client.post(torrserver_url()+'/torrent/upload',files={'file':('release.torrent',bytes(body),'application/x-bittorrent')})
            result.raise_for_status()
            status = result.json()
            if isinstance(status,list):
                if len(status)!=1: raise ValueError('Unexpected upload result')
                status=status[0]
            return status

    def _prepare(self, rid):
        try:
            release=self.release(rid)
            if release['source']=='anwap':
                from anwap import source_page
                fresh=source_page(release['film_id'])
                files=[{k:f[k] for k in ('id','path','size','sample')} for f in fresh['formats']]
                with self.catalog.db() as db:
                    db.execute("UPDATE prepared_releases SET status='ready',files=?,updated_at=?,error=NULL WHERE release_id=?",(json.dumps(files),time.time(),rid))
                return
            status=self.add(release)
            # Magnets may not have metadata yet. Bound polling and offer an explicit retry.
            deadline=time.monotonic()+70
            while not status.get('file_stats'):
                video_files(status)  # validate hash before sending it upstream
                if time.monotonic() > deadline: raise TimeoutError()
                time.sleep(2)
                with httpx.Client(timeout=8,trust_env=False) as client:
                    response=client.post(torrserver_url()+'/torrents',json={'action':'get','hash':status['hash']})
                    response.raise_for_status(); status=response.json()
            files=video_files(status)
            if not files: raise ValueError('No video')
            with self.catalog.db() as db:
                db.execute("UPDATE prepared_releases SET status='ready',hash=?,files=?,updated_at=?,error=NULL WHERE release_id=?",(status['hash'].lower(),json.dumps(files),time.time(),rid))
        except Exception:
            # Exception messages can include authenticated download URLs: do not log them.
            with self.catalog.db() as db:
                db.execute("UPDATE prepared_releases SET status='error',error='torrent_unavailable',updated_at=? WHERE release_id=?",(time.time(),rid))
        finally:
            with self.lock: self.pending.discard(rid)

    def selection(self, rid, file_id, resolve_stream=True):
        release=self.release(rid)
        with self.catalog.db() as db:
            r=db.execute("SELECT * FROM prepared_releases WHERE release_id=? AND status='ready'",(rid,)).fetchone()
        if not r: raise ValueError('Release not ready')
        file=next((f for f in json.loads(r['files']) if f['id']==file_id),None)
        if not file: raise ValueError('File not found')
        if release['source']=='anwap':
            if not resolve_stream:
                return {'content_id':release['content']['id'],'file_key':'anwap:pending'}
            from anwap import resolve
            item=resolve(release['film_id'],file_id)
            return dict(item,content_id=release['content']['id'])
        file_key=r['hash']+':'+str(file_id)
        return {'content_id':release['content']['id'],'file_key':file_key,'title':file['path']}
