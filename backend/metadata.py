"""Optional TMDB/TVmaze enrichment, independent of catalog and playback."""
import html
import json
import re
import threading
import time
import os
import concurrent.futures
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
        self.wake=threading.Event()
        self.card_failures={}
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata_cache(content_id TEXT PRIMARY KEY,payload TEXT,checked_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_checks(content_id TEXT,provider TEXT,checked_at REAL NOT NULL,PRIMARY KEY(content_id,provider))')
            db.execute('CREATE TABLE IF NOT EXISTS image_assets(url TEXT PRIMARY KEY,width INTEGER NOT NULL,height INTEGER NOT NULL,version TEXT NOT NULL,checked_at REAL NOT NULL)')
        self.classify_cached_artwork()

    def classify_cached_artwork(self):
        # A release must not hide existing posters while slow metadata lookups warm up.
        # Classify local cache files immediately, without contacting any provider.
        with self.catalog.db() as db:
            known={r[0] for r in db.execute('SELECT url FROM image_assets')}
            payloads=[json.loads(r[0]) for r in db.execute('SELECT payload FROM metadata_cache WHERE payload IS NOT NULL')]
            payloads.extend((json.loads(r[0]).get('metadata') or {}) for r in db.execute('SELECT payload FROM catalog_provider_items'))
            payloads.extend((json.loads(r[0]).get('content',{}).get('metadata') or {}) for r in db.execute('SELECT payload FROM catalog_releases'))
        for item in payloads:
            for _,url in self._candidates(item,None):
                if url in known:continue
                known.add(url)
                path=self.catalog.data_dir/'posters'/hashlib.sha256(url.encode()).hexdigest()
                if path.is_file():
                    try:self._record_image(url,path.read_bytes())
                    except (OSError,ValueError):pass

    @staticmethod
    def choose(native,cached):
        if not cached:return native
        if not native:return cached
        merged=dict(native,**{k:v for k,v in cached.items() if v not in (None,'',{},[])})
        # Localized source text fills gaps in TMDB and avoids English-only display names.
        for key in ('title','description'):
            if native.get(key) and (not cached.get(key) or not re.search('[А-Яа-яЁё]',cached[key])):
                merged[key]=native[key]
        if native.get('season_years'):merged['season_years']=native['season_years']
        return merged

    def prioritize(self,cards):
        with self.priority_lock:
            for card in cards[:40]: self.priority[card['id']]=card
            while len(self.priority)>200: self.priority.pop(next(iter(self.priority)))
        self.wake.set()

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
            source_meta=native.get(card['id']) or card.get('metadata')
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
            for release in card.get('releases',[]):
                release['season_year']=(meta.get('season_years') or {}).get(str(release.get('season')))
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

    def warm_artwork(self,cid,refresh=False):
        for _,url in self._candidates(*self._card_art(cid)):
            if self.poster_failures.get(url,0)>time.time():continue
            try:self._poster_url(url,refresh=refresh)
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

    def _poster_url(self,url,refresh=False):
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
            elif not refresh or self.image_refresh_failures.get(url,0)>time.time():return path
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
        from native_metadata import lookup
        native=lookup(self.catalog,card)
        if native:
            with self.catalog.db() as db:
                old=db.execute('SELECT payload FROM catalog_provider_items WHERE id=?',(card['id'],)).fetchone()
                entry=json.loads(old[0]) if old else dict(card)
                previous=entry.get('metadata') or {}
                entry['metadata']=dict(previous,**{k:v for k,v in native.items() if v not in (None,'',{},[])})
                db.execute('INSERT OR REPLACE INTO catalog_provider_items VALUES (?,?)',(card['id'],json.dumps(entry)))
        return native

    def refresh(self,card):
        provider='TMDB:art-v2' if self.tmdb.enabled() else 'TVmaze:art-v2'
        detail=self.catalog.detail(card['id']) or card
        if self.tmdb.enabled():metadata=self.tmdb.lookup(detail)
        elif card['media_type']=='tv':
            results=[]
            for name in [card['title']]+card.get('aliases',[]):
                results.extend(json.loads(fetch('https://api.tvmaze.com/search/shows?'+urlencode({'q':name}),timeout=8,limit=1_000_000)))
            metadata=match_show(card,results)
        else:return
        with self.catalog.db() as db:
            if metadata:
                old=db.execute('SELECT payload FROM metadata_cache WHERE content_id=?',(card['id'],)).fetchone()
                previous=json.loads(old[0]) if old and old[0] else {}
                if previous.get('provider_id')!=metadata.get('provider_id'):previous={}
                metadata=dict(previous,**{k:v for k,v in metadata.items() if v not in (None,'',{},[])})
                db.execute('INSERT OR REPLACE INTO metadata_cache VALUES (?,?,?)',(card['id'],json.dumps(metadata),time.time()))
            db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(card['id'],provider,time.time()))
        self.last_success=time.time();self.last_error=None

    def process(self,card):
        cid=card['id']
        if self.card_failures.get(cid,0)>time.time():return
        detail=self.catalog.detail(cid) or card
        revision=hashlib.sha256(json.dumps([detail.get('source_slug'),detail.get('published_at'),
            sorted({r.get('season') for r in detail.get('releases',[]) if r.get('season') is not None})]).encode()).hexdigest()[:16]
        key='content-v3:'+revision
        with self.catalog.db() as db:
            check=db.execute('SELECT checked_at FROM metadata_checks WHERE content_id=? AND provider=?',(cid,key)).fetchone()
        if check and time.time()-check[0]<7*86400:
            self.warm_artwork(cid)
            return
        failed=False
        try:self.refresh_native(detail)
        except Exception:failed=True
        # A provider error affects this card only; other visible cards keep moving.
        try:self.refresh(detail)
        except Exception:failed=True
        # Recheck unchanged URLs only when the season changes. A changed URL is fetched automatically.
        art_key='season-art-v1:'+hashlib.sha256(json.dumps([detail.get('source_slug'),
            sorted({r.get('season') for r in detail.get('releases',[]) if r.get('season') is not None})]).encode()).hexdigest()[:16]
        with self.catalog.db() as db:
            art_check=db.execute('SELECT checked_at FROM metadata_checks WHERE content_id=? AND provider=?',(cid,art_key)).fetchone()
        self.warm_artwork(cid,refresh=art_check is None)
        with self.catalog.db() as db:
            db.execute("DELETE FROM metadata_checks WHERE content_id=? AND provider LIKE 'season-art-v1:%'",(cid,))
            db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(cid,art_key,time.time()))
        if failed:
            self.card_failures[cid]=time.time()+60
            self.last_error='Some metadata sources are unavailable; cached cards retained'
        else:
            with self.catalog.db() as db:
                db.execute("DELETE FROM metadata_checks WHERE content_id=? AND provider LIKE 'content-v3:%'",(cid,))
                db.execute('INSERT OR REPLACE INTO metadata_checks VALUES (?,?,?)',(cid,key,time.time()))

    def start(self):
        def run():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4,thread_name_prefix='metadata') as pool:
                pending={}
                background=[]
                refill=0
                while not self.stop_event.is_set():
                    for cid,future in list(pending.items()):
                        if future.done():
                            try:future.result()
                            except Exception:self.card_failures[cid]=time.time()+60
                            del pending[cid]
                    if not background and time.time()>=refill:
                        cards={}
                        for shelf in self.catalog.home()['shelves']:
                            for card in shelf['results']:cards.setdefault(card['id'],card)
                        for card in self.catalog.search()['results']:cards.setdefault(card['id'],card)
                        background=list(cards.values());refill=time.time()+60
                    while len(pending)<4:
                        with self.priority_lock:
                            cid=next((k for k in self.priority if k not in pending),None)
                            card=self.priority.pop(cid) if cid else None
                        if card is None:
                            if not background:break
                            card=background.pop(0)
                        if card['id'] in pending:continue
                        pending[card['id']]=pool.submit(self.process,card)
                    self.wake.wait(.2);self.wake.clear()
        threading.Thread(target=run,name='metadata-dispatch',daemon=True).start()

    def stop(self):
        self.stop_event.set();self.wake.set()
