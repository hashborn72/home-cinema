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
from catalog import normal, fetch, lostfilm_slug
import httpx
from tmdb import TMDB
from artwork import dimensions, role


def match_show(card, results):
    if card.get('match',card.get('confidence'))=='unmatched':return None
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
        self.image_refresh_failures={}
        self.priority={}
        self.priority_lock=threading.Lock()
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata_cache(content_id TEXT PRIMARY KEY,payload TEXT,checked_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_checks(content_id TEXT,provider TEXT,checked_at REAL NOT NULL,PRIMARY KEY(content_id,provider))')
            db.execute('CREATE TABLE IF NOT EXISTS image_assets(url TEXT PRIMARY KEY,width INTEGER NOT NULL,height INTEGER NOT NULL,version TEXT NOT NULL,checked_at REAL NOT NULL)')

    @staticmethod
    def choose(native,cached):
        # Keep native Anwap data as fallback, but prefer a confirmed TMDB match.
        if cached and cached.get('provider')=='TMDB':
            return dict(cached,**{kind:cached.get(kind) or (native or {}).get(kind) for kind in ('poster','backdrop','episode_still')})
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
            assets={r['url']:dict(r) for r in db.execute('SELECT * FROM image_assets')}
        for card in cards:
            source_meta=card.get('metadata') or native.get(card['id'])
            cached=cache.get(card['id']) if card.get('match')!='unmatched' else None
            meta=self.choose(source_meta,cached)
            if not meta:
                card['metadata']=None
                continue
            output=dict(meta,poster=None,backdrop=None,episode_still=None,artwork={})
            for hint,url in self._candidates(meta,source_meta):
                asset=assets.get(url)
                if not asset:continue  # The worker verifies geometry; never download during catalogue reads.
                kind=role(asset['width'],asset['height'],hint)
                if not kind or output[kind]:continue
                output[kind]=public_url()+'/images/'+card['id']+'?'+urlencode({'kind':kind,'v':asset['version']})
                output['artwork'][kind]={'width':asset['width'],'height':asset['height'],'version':asset['version']}
            card['metadata']=output
        return cards

    @staticmethod
    def _candidates(meta,native):
        seen=set()
        for item in (meta,native):
            for kind in ('poster','backdrop','episode_still'):
                url=(item or {}).get(kind)
                if url and url not in seen:
                    seen.add(url)
                    yield kind,url

    def _card_art(self,cid):
        card=self.catalog.detail(cid)
        if not card:raise KeyError(cid)
        with self.catalog.db() as db:
            row=db.execute('SELECT payload FROM metadata_cache WHERE content_id=?',(cid,)).fetchone()
        cached=json.loads(row['payload']) if row and row['payload'] and card.get('match')!='unmatched' else None
        return self.choose(card.get('metadata'),cached),card.get('metadata')

    def warm_artwork(self,cid):
        for _,url in self._candidates(*self._card_art(cid)):
            if self.poster_failures.get(url,0)>time.time():continue
            try:self._poster_url(url)
            except Exception:
                self.poster_failures={k:v for k,v in self.poster_failures.items() if v>time.time()}
                self.poster_failures[url]=time.time()+900

    def poster(self,cid,kind='poster',version=None):
        if kind not in ('poster','backdrop','episode_still'):raise KeyError(kind)
        candidates=list(self._candidates(*self._card_art(cid)))
        if not candidates:raise KeyError(cid)
        failed=False
        for hint,url in candidates:
            if self.poster_failures.get(url,0)>time.time():continue
            try:
                path=self._poster_url(url)
                with self.catalog.db() as db:asset=db.execute('SELECT * FROM image_assets WHERE url=?',(url,)).fetchone()
                if asset and role(asset['width'],asset['height'],hint)==kind and (not version or version==asset['version']):return path
            except Exception:
                failed=True
                self.poster_failures[url]=time.time()+900
        if failed:raise ValueError('Image sources unavailable')
        raise KeyError('No matching artwork')

    def _record_image(self,url,body):
        width,height=dimensions(body)
        version=hashlib.sha256(url.encode()+body).hexdigest()[:20]
        with self.catalog.db() as db:
            db.execute('INSERT OR REPLACE INTO image_assets VALUES (?,?,?,?,?)',(url,width,height,version,time.time()))

    def _poster_url(self,url):
        p=urlparse(url)
        if p.scheme!='https' or p.netloc not in ('mm.anwap.media','static.tvmaze.com','image.tmdb.org','www.lostfilm.tv') or p.query or p.fragment:raise KeyError('Invalid poster')
        if p.netloc=='www.lostfilm.tv' and not re.fullmatch(r'/Static/Images/\d+/Posters/(?:image(?:_s\d+)?|poster)\.jpg',p.path):raise KeyError('Invalid poster')
        if p.netloc=='mm.anwap.media' and not re.fullmatch(r'/(?:films/screen|serials/posts)/\d+\.jpg',p.path):raise KeyError('Invalid poster')
        if p.netloc=='image.tmdb.org' and not re.fullmatch(r'/t/p/(?:w500|w780)/[A-Za-z0-9]+\.(?:jpg|png)',p.path):raise KeyError('Invalid poster')
        folder=self.catalog.data_dir/'posters';folder.mkdir(mode=0o700,exist_ok=True)
        path=folder/hashlib.sha256(url.encode()).hexdigest()
        asset=None
        if path.is_file():
            with self.catalog.db() as db:asset=db.execute('SELECT checked_at FROM image_assets WHERE url=?',(url,)).fetchone()
            if not asset:
                try:
                    self._record_image(url,path.read_bytes())
                    return path
                except ValueError:pass  # Repair a bad legacy file from the same allowlisted source.
            elif time.time()-asset['checked_at']<86400 or self.image_refresh_failures.get(url,0)>time.time():return path
        try:
            with httpx.Client(timeout=10,follow_redirects=False,trust_env=False,headers={'User-Agent':'Mozilla/5.0 (compatible; HomeCinema/0.4)'}) as client:
                with client.stream('GET',url) as r:
                    r.raise_for_status();body=bytearray()
                    for chunk in r.iter_bytes():
                        body.extend(chunk)
                        if len(body)>3_000_000:raise ValueError('Poster too large')
            dimensions(body)
        except Exception:
            if path.is_file() and asset:
                self.image_refresh_failures={k:v for k,v in self.image_refresh_failures.items() if v>time.time()}
                self.image_refresh_failures[url]=time.time()+900
                return path  # Retain the verified picture/version during upstream outages.
            raise
        # Atomic replace: concurrent reads cannot observe a partial image.
        import tempfile
        fd,tmp=tempfile.mkstemp(dir=folder,prefix='poster-')
        try:
            with os.fdopen(fd,'wb') as out:out.write(body)
            os.replace(tmp,path)
            self._record_image(url,body)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return path

    def refresh_native(self,card):
        if card.get('sources')==['lostfilm']:
            detail=self.catalog.detail(card['id']) or card
            slug=detail.get('source_slug')
            with self.catalog.db() as db:
                native=db.execute('SELECT payload FROM catalog_provider_items WHERE id=?',(card['id'],)).fetchone()
            native_meta=(json.loads(native[0]).get('metadata') or {}) if native else {}
            slug=slug or lostfilm_slug(native_meta.get('url',''))
            check_provider='LostFilm:portrait-v2:'+str(slug or '')
            with self.catalog.db() as db:
                check=db.execute('SELECT checked_at FROM metadata_checks WHERE content_id=? AND provider=?',(card['id'],check_provider)).fetchone()
            mismatch=slug and native_meta.get('url','').rstrip('/')!='https://www.lostfilm.tv/series/'+slug
            directory_art='/Posters/image' in native_meta.get('poster','')
            if (not native_meta.get('poster') or directory_art or mismatch) and (not check or time.time()-check[0]>86400):
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
        provider='TMDB:art-v2' if self.tmdb.enabled() else 'TVmaze:art-v2'
        if self.tmdb.enabled():metadata=self.tmdb.lookup(card)
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
                    checked={r['content_id']:r['checked_at'] for r in db.execute('SELECT * FROM metadata_checks WHERE provider=?',('TMDB:art-v2' if self.tmdb.enabled() else 'TVmaze:art-v2',))}
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
                    try: self.warm_artwork(card['id'])
                    except Exception: pass
                    if self.stop_event.wait(.5): return
                    with self.priority_lock:
                        if self.priority: break
                self.stop_event.wait(2)
        threading.Thread(target=run,daemon=True).start()

    def stop(self): self.stop_event.set()
