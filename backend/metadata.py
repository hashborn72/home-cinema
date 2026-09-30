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
        self.priority={}
        self.priority_lock=threading.Lock()
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata_cache(content_id TEXT PRIMARY KEY,payload TEXT,checked_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_checks(content_id TEXT,provider TEXT,checked_at REAL NOT NULL,PRIMARY KEY(content_id,provider))')

    @staticmethod
    def choose(native,cached):
        # Keep native Anwap data as fallback, but prefer a confirmed TMDB match.
        if cached and cached.get('provider')=='TMDB':
            return dict(cached,poster=cached.get('poster') or (native or {}).get('poster'))
        return native or cached

    def prioritize(self,cards):
        with self.priority_lock:
            for card in cards[:40]: self.priority[card['id']]=card
            while len(self.priority)>200: self.priority.pop(next(iter(self.priority)))

    def status(self):
        with self.catalog.db() as db:
            count=sum(json.loads(r['payload']).get('provider')=='TMDB' for r in db.execute('SELECT payload FROM metadata_cache WHERE payload IS NOT NULL'))
        return {'tmdb_configured':self.tmdb.enabled(),'matched':count,'last_success':self.last_success,
                'last_error':self.last_error,'retry_after':self.retry_after,
                'poster_cooldowns':sum(until>time.time() for until in self.poster_failures.values())}

    def enrich(self,cards):
        with self.catalog.db() as db:
            cache={r['content_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM metadata_cache WHERE payload IS NOT NULL')}
            native={r['id']:json.loads(r['payload']).get('metadata') for r in db.execute('SELECT * FROM catalog_provider_items')}
        for card in cards:
            card['metadata']=self.choose(card.get('metadata') or native.get(card['id']),cache.get(card['id']))
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
        if p.scheme!='https' or p.netloc not in ('mm.anwap.media','static.tvmaze.com','image.tmdb.org','www.lostfilm.tv') or p.query or p.fragment:raise KeyError('Invalid poster')
        if p.netloc=='www.lostfilm.tv' and not re.fullmatch(r'/Static/Images/\d+/Posters/(?:image(?:_s\d+)?|poster)\.jpg',p.path):raise KeyError('Invalid poster')
        if p.netloc=='mm.anwap.media' and not re.fullmatch(r'/(?:films/screen|serials/posts)/\d+\.jpg',p.path):raise KeyError('Invalid poster')
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

    def refresh_native(self,card):
        if card.get('sources')==['lostfilm']:
            detail=self.catalog.detail(card['id']) or card
            slug=detail.get('source_slug')
            check_provider='LostFilm:'+str(slug or '')
            with self.catalog.db() as db:
                native=db.execute('SELECT payload FROM catalog_provider_items WHERE id=?',(card['id'],)).fetchone()
                check=db.execute('SELECT checked_at FROM metadata_checks WHERE content_id=? AND provider=?',(card['id'],check_provider)).fetchone()
            native_meta=(json.loads(native[0]).get('metadata') or {}) if native else {}
            mismatch=slug and native_meta.get('url','').rstrip('/')!='https://www.lostfilm.tv/series/'+slug
            if (not native_meta.get('poster') or mismatch) and (not check or time.time()-check[0]>86400):
                # Ambiguous TMDB titles (e.g. remakes) still get the provider's exact-ID poster.
                try:
                    from providers import lostfilm_directory
                    found,_=lostfilm_directory(card['title'],0)
                    matches=[m for m in found if m['id']==card['id'] and (not slug or m['metadata']['url'].rstrip('/').endswith('/'+slug))]
                    match=matches[0] if len(matches)==1 else dict(card,metadata={})
                    if slug:
                        url='https://www.lostfilm.tv/series/'+slug+'/'
                        body=fetch(url,timeout=10,limit=2_000_000).decode('utf-8','replace')
                        poster=re.search(r'/Static/Images/\d+/Posters/poster\.jpg',body)
                        if poster:match=dict(match,metadata=dict(match.get('metadata') or {},provider='LostFilm',url=url,poster='https://www.lostfilm.tv'+poster[0]))
                    with self.catalog.db() as db:
                        if match.get('metadata',{}).get('poster'):
                            db.execute('INSERT OR REPLACE INTO catalog_provider_items VALUES (?,?)',(match['id'],json.dumps(match)))
                        db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(card['id'],check_provider,time.time()))
                except Exception:
                    with self.catalog.db() as db:db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(card['id'],check_provider,time.time()-86100))

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
                    for row in db.execute('SELECT content_id,payload FROM metadata_cache WHERE payload IS NOT NULL'):
                        old=json.loads(row['payload'])
                        if old.get('provider')=='TMDB' and 'year' not in old:checked.pop(row['content_id'],None)
                with self.priority_lock:
                    cards=self.priority.copy();self.priority.clear()
                for row in self.catalog.home()['shelves']:
                    for c in row['results']:cards.setdefault(c['id'],c)
                with self.catalog.db() as db:
                    for row in db.execute('SELECT payload FROM catalog_provider_items LIMIT 2000'):
                        c=json.loads(row['payload']);cards.setdefault(c['id'],c)
                for c in self.catalog.cards(self.catalog.snapshot()[1]):cards.setdefault(c['id'],c)
                for card in cards.values():
                    if self.stop_event.is_set(): return
                    self.refresh_native(card)
                    if time.time()>=self.retry_after and time.time()-checked.get(card['id'],0)>=86400:
                        try: self.refresh(card)
                        except Exception:
                            self.last_error='Metadata source unavailable'
                            self.retry_after=time.time()+900
                    # Cache image bytes as well as metadata; provider posters work even if TMDB is down.
                    try: self.poster(card['id'])
                    except Exception: pass
                    if self.stop_event.wait(.5): return
                    with self.priority_lock:
                        if self.priority: break
                self.stop_event.wait(2)
        threading.Thread(target=run,daemon=True).start()

    def stop(self): self.stop_event.set()
