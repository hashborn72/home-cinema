"""Optional TMDB/TVmaze enrichment, independent of catalog and playback."""
import html
import json
import re
import threading
import time
import os
from settings import public_url
import hashlib
from pathlib import Path
from urllib.parse import urlencode, urlparse
from catalog import normal, fetch
import httpx
from tmdb import TMDB


def match_show(card, results):
    names={normal(n) for n in [card['title']]+card.get('aliases',[])}
    matches={r['show']['id']:r['show'] for r in results if normal(r['show']['name']) in names}
    # A release year is not a show's premiere year. It cannot resolve a remake.
    if len(matches)!=1: return None
    show=next(iter(matches.values()))
    poster=(show.get('image') or {}).get('medium')
    p=urlparse(poster or '')
    if p.scheme!='https' or p.netloc!='static.tvmaze.com': poster=None
    return {'provider':'TVmaze','provider_id':show['id'],'url':'https://www.tvmaze.com/shows/'+str(int(show['id'])),
            'title':show['name'],'description':html.unescape(re.sub('<[^>]*>','',show.get('summary') or ''))[:6000],
            'poster':poster,'rating':(show.get('rating') or {}).get('average'),
            'language':'en','license':'CC BY-SA','match':'unique_exact_title'}


class Metadata:
    def __init__(self,catalog):
        self.catalog=catalog
        self.stop_event=threading.Event()
        self.tmdb=TMDB(catalog.data_dir)
        self.retry_after=0
        self.last_error=None
        self.last_success=None
        self.poster_failures={}
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata_cache(content_id TEXT PRIMARY KEY,payload TEXT,checked_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_checks(content_id TEXT,provider TEXT,checked_at REAL NOT NULL,PRIMARY KEY(content_id,provider))')

    @staticmethod
    def choose(native,cached):
        # Keep native Anwap data as fallback, but prefer a confirmed TMDB match.
        return cached if cached and cached.get('provider')=='TMDB' else native or cached

    def status(self):
        with self.catalog.db() as db:
            count=sum(json.loads(r['payload']).get('provider')=='TMDB' for r in db.execute('SELECT payload FROM metadata_cache WHERE payload IS NOT NULL'))
        return {'tmdb_configured':self.tmdb.enabled(),'matched':count,'last_success':self.last_success,
                'last_error':self.last_error,'retry_after':self.retry_after,
                'poster_cooldowns':sum(until>time.time() for until in self.poster_failures.values())}

    def enrich(self,cards):
        with self.catalog.db() as db:
            cache={r['content_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM metadata_cache WHERE payload IS NOT NULL')}
        for card in cards:
            card['metadata']=self.choose(card.get('metadata'),cache.get(card['id']))
            if card['metadata'] and card['metadata'].get('poster'):
                card['metadata']=dict(card['metadata'],poster=public_url()+'/images/'+card['id'])
        return cards

    def poster(self,cid):
        card=self.catalog.detail(cid)
        if not card:raise KeyError(cid)
        with self.catalog.db() as db:
            row=db.execute('SELECT payload FROM metadata_cache WHERE content_id=?',(cid,)).fetchone()
        meta=self.choose(card.get('metadata'),json.loads(row['payload']) if row and row['payload'] else None)
        urls=list(dict.fromkeys(filter(None,[(meta or {}).get('poster'),(card.get('metadata') or {}).get('poster')])))
        if not urls:raise KeyError(cid)
        for url in urls:
            if self.poster_failures.get(url,0)>time.time():continue
            try:return self._poster_url(url)
            except Exception:
                self.poster_failures={k:v for k,v in self.poster_failures.items() if v>time.time()}
                self.poster_failures[url]=time.time()+900
        raise ValueError('Poster sources unavailable')

    def _poster_url(self,url):
        p=urlparse(url)
        if p.scheme!='https' or p.netloc not in ('mm.anwap.media','static.tvmaze.com','image.tmdb.org') or p.query or p.fragment:raise KeyError('Invalid poster')
        if p.netloc=='mm.anwap.media' and not re.fullmatch(r'/films/screen/\d+\.jpg',p.path):raise KeyError('Invalid poster')
        if p.netloc=='image.tmdb.org' and not re.fullmatch(r'/t/p/w500/[A-Za-z0-9]+\.(?:jpg|png)',p.path):raise KeyError('Invalid poster')
        folder=self.catalog.data_dir/'posters';folder.mkdir(mode=0o700,exist_ok=True)
        path=folder/hashlib.sha256(url.encode()).hexdigest()
        if path.is_file():return path
        with httpx.Client(timeout=10,follow_redirects=False,trust_env=False,headers={'User-Agent':'Mozilla/5.0 (compatible; HomeCinema/0.4)'}) as client:
            with client.stream('GET',url) as r:
                r.raise_for_status();body=bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body)>3_000_000:raise ValueError('Poster too large')
        if not (body.startswith(b'\xff\xd8\xff') or body.startswith(b'\x89PNG\r\n\x1a\n')):raise ValueError('Not an image')
        # Atomic replace: concurrent reads cannot observe a partial image.
        import tempfile
        fd,tmp=tempfile.mkstemp(dir=folder,prefix='poster-')
        try:
            with os.fdopen(fd,'wb') as out:out.write(body)
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return path

    def refresh(self,card):
        provider='TMDB' if self.tmdb.enabled() else 'TVmaze'
        if provider=='TMDB':metadata=self.tmdb.lookup(card)
        elif card['media_type']=='tv':
            results=[]
            for name in [card['title']]+card.get('aliases',[]):
                results.extend(json.loads(fetch('https://api.tvmaze.com/search/shows?'+urlencode({'q':name}),timeout=8,limit=1_000_000)))
            metadata=match_show(card,results)
        else:return
        with self.catalog.db() as db:
            db.execute('INSERT OR REPLACE INTO metadata_cache VALUES (?,?,?)',(card['id'],json.dumps(metadata) if metadata else None,time.time()))
            db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(card['id'],provider,time.time()))
        self.last_success=time.time();self.last_error=None

    def start(self):
        def run():
            while not self.stop_event.is_set():
                with self.catalog.db() as db:
                    checked={r['content_id']:r['checked_at'] for r in db.execute('SELECT * FROM metadata_checks WHERE provider=?',('TMDB' if self.tmdb.enabled() else 'TVmaze',))}
                cards={c['id']:c for row in self.catalog.home()['shelves'] for c in row['results']}
                for c in self.catalog.search()['results']:cards.setdefault(c['id'],c)
                for card in cards.values():
                    if self.stop_event.is_set(): return
                    if time.time()<self.retry_after:break
                    if time.time()-checked.get(card['id'],0)<86400: continue
                    try: self.refresh(card)
                    except Exception:
                        self.last_error='Metadata source unavailable'
                        self.retry_after=time.time()+900
                        break  # Preserve cached data and avoid hammering an unavailable API.
                    if self.stop_event.wait(1): return
                self.stop_event.wait(30)
        threading.Thread(target=run,daemon=True).start()

    def stop(self): self.stop_event.set()
