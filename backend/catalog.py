"""Small persistent catalogue. Upstream secrets/URLs never enter its public models."""
import concurrent.futures
from contextlib import contextmanager
import email.utils
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from settings import jackett_url

SOURCES = {'lostfilm': 'LostFilm — обновления сериалов', 'exkinoray': 'ExKinoRay — новые раздачи фильмов', 'rutor': 'RuTor — популярное среди последних раздач','anwap':'Anwap — последние фильмы и сериалы · 2020+'}
TTL = 600
EPISODE = re.compile(r'(?i)\bS(\d{1,2})(?:E(\d{1,3}))?|\b(\d{1,2})x(\d{1,3})\b|(?:сезон[ыа]?|сери[яий])\s*\d')
TECH = re.compile(r'(?i)(?<!\w)(?:\d{3,4}[pi]|BDRip|BDRemux|Blu[ -]?Ray|REMUX|WEB[ .-]?(?:DL(?:Rip)?|Rip)|WEBDL|HDRip|HDTV|DVDRip|DVD|UHD|HDR10?\+?|HEVC|AVC|x26[45]|H[ .]?26[45]|DUB|MVO|DVO|VO|AAC|DTS|FLAC|rus|eng|\d+(?:[.,]\d+)?\s*(?:GB|MB|ГБ|МБ))(?!\w)')
YEAR = re.compile(r'(?:\(|\[|\|\s*)((?:19|20)\d{2})(?=\s*[-)\]|])')
RUTOR_PATHS = {'/kino': 'movie', '/nashe_kino': 'movie', '/nauchno_popularnoe': 'movie', '/seriali': 'tv', '/nashi_seriali': 'tv', '/tv': 'tv', '/multiki': 'movie', '/anime': 'anime'}

def digest(text): return hashlib.sha256(text.encode()).hexdigest()[:24]
def normal(text): return re.sub(r'[^\w]+', ' ', text.casefold().replace('ё', 'е')).strip()
def merge_native(base,incoming):
    """Fresh nonempty fields win without discarding another source's usable cover."""
    if not base:return incoming
    if not incoming:return base
    previous_slug=lostfilm_slug(base.get('url',''))
    incoming_slug=lostfilm_slug(incoming.get('url',''))
    if previous_slug and incoming_slug and previous_slug!=incoming_slug:
        return dict(incoming)  # Same-name remakes must not share text, IDs or fallback artwork.
    merged=dict(base,**{k:v for k,v in incoming.items() if v not in (None,'',{},[])})
    covers=list(dict.fromkeys([u for item in (incoming,base) for u in [item.get('poster')]+item.get('poster_alternatives',[]) if u]))
    alternatives=[u for u in covers if u!=merged.get('poster')]
    if alternatives:merged['poster_alternatives']=alternatives
    else:merged.pop('poster_alternatives',None)
    return merged
def lostfilm_slug(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.hostname not in ('www.lostfilm.tv','lostfilm.tv'):return None
    parts=urllib.parse.unquote(parsed.path).split('/')
    return parts[2].strip() if len(parts)>2 and parts[1]=='series' and re.fullmatch(r'[A-Za-z0-9_-]+',parts[2].strip()) else None
def native_matches_source(card,metadata):
    selected=card.get('source_slug')
    actual=lostfilm_slug((metadata or {}).get('url',''))
    return not (selected and actual and selected!=actual)
def number(value):
    try: return max(0, int(value))
    except (ValueError, TypeError): return None

def identify(raw, source, kind, release_id):
    text = html.unescape(raw).replace('\xa0', ' ')
    ep = EPISODE.search(text)
    ym = YEAR.search(text)
    year = int(ym[1]) if ym else None
    collection = bool(re.search(r'(?i)антологи|anthology|collection|коллекци|\b(?:19|20)\d{2}\s*[-–]\s*(?:19|20)\d{2}\b', text))
    if ep: kind = 'tv'
    cut = min([m.start() for m in (ep, ym, TECH.search(text)) if m] or [len(text)])
    head = re.sub(r'\[[^\]]*\]', '', text[:cut])
    titles = [re.sub(r'\s+', ' ', re.sub(r'[._]+', ' ', p)).strip(' -–—|/([,:') for p in re.split(r'\s+[|/]\s*|\s*\|\s*', head)]
    titles = list(dict.fromkeys(t for t in titles if t))[:3]
    title = titles[0] if titles else text[:140]
    # Film year is identity, series release/season year is not reliably its premiere year.
    # Unknown/collection releases stay separate, rather than guessing a film identity.
    if kind == 'movie' and year and not collection:
        identity = f'movie:{normal(title)}:{year}'
        confidence = 'title_year'
    elif kind == 'tv' and source == 'lostfilm' and ep and not collection:
        identity = f'tv:lostfilm:{normal(title)}'
        confidence = 'source_series_title'
        year = None
    elif kind == 'tv' and year and not collection:
        identity = f'tv:{source}:{normal(title)}:{year}'
        confidence = 'source_title_year'
    else:
        identity = 'unmatched:' + release_id
        confidence = 'unmatched'
    season = number(ep[1] or ep[3]) if ep else None
    episode = number(ep[2] or ep[4]) if ep else None
    quality = re.search(r'(?i)\b(?:2160p|1080[pi]|720p|480p|BDRemux|BDRip|WEB-DL|WEBDL|WEBRip|HDTV)\b', text)
    return {'id':digest(identity),'title':title,'aliases':titles[1:], 'year':year,'media_type':kind,
            'match':confidence,'season':season,'episode':episode,'quality':quality[0] if quality else None}

def parse_feed(data, source, limit=100):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('Unsafe feed')
    root = ET.fromstring(data)
    if root.tag != 'rss': raise ValueError('Invalid feed')
    rows = []
    for item in root.findall('./channel/item')[:limit]:
        attrs, cats = {}, []
        for child in item:
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'attr':
                if child.get('name') == 'category': cats.append(number(child.get('value')) or 0)
                else: attrs[child.get('name')] = child.get('value')
            elif tag == 'category': cats.append(number(child.text) or 0)
        raw = item.findtext('title', '')[:1000]
        try: date = email.utils.parsedate_to_datetime(item.findtext('pubDate')).timestamp()
        except (ValueError, TypeError, AttributeError, OverflowError): date = 0
        guid = item.findtext('guid') or item.findtext('comments') or raw
        rows.append({'id':digest(source+':'+guid), 'source':source,'raw':raw,'categories':cats,
                     'seeders':number(attrs.get('seeders')), 'size':number(item.findtext('size') or attrs.get('size')),
                     'published_at':date,'details':item.findtext('comments', ''),
                     'download_url':item.findtext('link', '')})
    return rows

def category_html(body, title):
    m = re.search(r'''(?is)<td[^>]*>\s*Категория\s*</td>\s*<td[^>]*>\s*<a[^>]+href=["']([^"']+)''', body)
    if not m: return None
    kind = RUTOR_PATHS.get(urllib.parse.urlparse(m[1]).path.rstrip('/'), 'excluded')
    return ('tv' if EPISODE.search(title) else 'movie') if kind == 'anime' else kind

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise ValueError('Redirect rejected')

def fetch(url, timeout=25, limit=6_000_000):
    req = urllib.request.Request(url, headers={'User-Agent':'HomeCinema/0.2'})
    with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
        data = response.read(limit+1)
        if len(data) > limit: raise ValueError('Response too large')
        return data

class Catalog:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / 'library.sqlite3'
        self.stop_event = threading.Event()
        self.threads = []
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS catalog_releases(id TEXT PRIMARY KEY, source TEXT NOT NULL, content_id TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS catalog_release_content ON catalog_releases(content_id);
              CREATE TABLE IF NOT EXISTS catalog_sources(source TEXT PRIMARY KEY, fetched_at REAL NOT NULL DEFAULT 0, attempted_at REAL NOT NULL DEFAULT 0, error TEXT, received INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS catalog_categories(url TEXT PRIMARY KEY, kind TEXT, checked_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS catalog_provider_items(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS catalog_home_series(source TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(source,id));
            ''')

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn: yield conn
        finally: conn.close()

    def rutor_kind(self, row):
        url = row['details']
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http','https') or parsed.hostname not in ('rutor.info','rutor.is') or parsed.port not in (None,80,443) or parsed.username or not parsed.path.startswith('/torrent/'):
            return None
        with self.db() as db:
            old = db.execute('SELECT * FROM catalog_categories WHERE url=?',(url,)).fetchone()
        if old and time.time()-old['checked_at'] < 86400: return old['kind']
        try: kind = category_html(fetch(url, timeout=6, limit=2_000_000).decode('utf-8','replace'),row['raw'])
        except Exception: return None
        if kind:
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO catalog_categories VALUES (?,?,?)',(url,kind,time.time()))
        return kind

    def ingest(self, source, rows, update_shelf=True, provider_cards=None):
        accepted = []
        for row in rows:
            cats = row['categories']
            kind = row.get('kind') if source == 'rutor' else ('movie' if any(2000 <= c < 3000 for c in cats) else 'tv' if any(5000 <= c < 6000 for c in cats) else None)
            if kind not in ('movie','tv') or (source == 'lostfilm' and kind != 'tv'): continue
            item = dict(row, **{'content':identify(row['raw'],source,kind,row['id'])})
            if source=='anwap' and row.get('series_card'):
                item['content']=dict(row['series_card'])
            if row.get('metadata'):item['content']['metadata']=row['metadata']
            accepted.append(item)
        now = time.time()
        with self.db() as db:
            if update_shelf and provider_cards is not None:
                db.execute('DELETE FROM catalog_home_series WHERE source=?',(source,))
                for card in provider_cards:
                    db.execute('INSERT OR REPLACE INTO catalog_provider_items VALUES (?,?)',(card['id'],json.dumps(card,ensure_ascii=False)))
                    db.execute('INSERT INTO catalog_home_series VALUES (?,?,?)',(source,card['id'],json.dumps(card,ensure_ascii=False)))
            # Persist existing catalogue records for history/deep links; shelf membership is a snapshot.
            for row in accepted:
                old=db.execute('SELECT payload FROM catalog_releases WHERE id=?',(row['id'],)).fetchone()
                prior=json.loads(old['payload']) if old else {}
                # Release publication, discovery and source checking are separate clocks.
                # Unknown legacy discovery times stay unknown, not "new today".
                row['first_seen_at']=prior.get('first_seen_at') if old else now
                row['last_checked_at']=now
                row['seen_at'] = now
                if not update_shelf:
                    row['seen_at']=prior.get('seen_at',0)
                    if 'source_rank' in prior:row['source_rank']=prior['source_rank']
                db.execute('INSERT OR REPLACE INTO catalog_releases VALUES (?,?,?,?)',
                           (row['id'],source,row['content']['id'],json.dumps(row,ensure_ascii=False)))
            if update_shelf:db.execute('INSERT OR REPLACE INTO catalog_sources VALUES (?,?,?,?,?)',(source,now,now,None,len(rows)+len(provider_cards or [])))

    def refresh(self, source):
        try:
            if source=='anwap':
                from anwap import latest_home
                movies,series=latest_home()
                self.ingest(source,movies,provider_cards=series)
                return
            key = (self.data_dir/'jackett-key').read_text().strip()
            params = {'apikey':key,'t':'search','limit':100}
            # ExKinoRay includes genuine video mapped to TV; do not silently discard it.
            if source != 'rutor': params['cat'] = '5000' if source == 'lostfilm' else '2000,5000'
            rows = parse_feed(fetch(jackett_url()+'/api/v2.0/indexers/'+source+'/results/torznab/api?'+urllib.parse.urlencode(params)),source)
            if source == 'rutor':
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    for row,kind in zip(rows,pool.map(self.rutor_kind,rows)): row['kind'] = kind
                if rows and not any(r['kind'] for r in rows): raise ValueError('Categories unavailable')
            self.ingest(source,rows)
        except Exception:
            # Upstream exception strings may contain URLs with API keys.
            with self.db() as db:
                db.execute('INSERT INTO catalog_sources(source,attempted_at,error) VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET attempted_at=excluded.attempted_at,error=excluded.error',
                           (source,time.time(),'upstream_unavailable'))

    def start(self):
        def run(source):
            while not self.stop_event.is_set():
                with self.db() as db:
                    row = db.execute('SELECT * FROM catalog_sources WHERE source=?',(source,)).fetchone()
                if not row or time.time()-row['fetched_at'] >= TTL:
                    self.refresh(source)
                self.stop_event.wait(60)
        for source in SOURCES:
            thread = threading.Thread(target=run,args=(source,),daemon=True)
            self.threads.append(thread); thread.start()

    def stop(self): self.stop_event.set()

    def snapshot(self):
        with self.db() as db:
            states = {r['source']:dict(r) for r in db.execute('SELECT * FROM catalog_sources')}
            releases = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM catalog_releases')]
        return states,releases

    @staticmethod
    def cards(releases):
        groups = {}
        for release in releases:
            content = release['content']
            card = groups.setdefault(content['id'],dict(content,release_count=0,seeders=None,published_at=0,sources=[]))
            card['aliases']=list(dict.fromkeys(card.get('aliases',[])+content.get('aliases',[])+([content['title']] if content['title']!=card['title'] else [])))
            card['release_count'] += 1
            if content.get('metadata'):card['metadata']=merge_native(card.get('metadata'),content['metadata'])
            if release['seeders'] is not None: card['seeders'] = max(card['seeders'] or 0,release['seeders'])
            card['published_at'] = max(card['published_at'],release['published_at'])
            if release.get('source_rank') is not None:card['source_rank']=max(card.get('source_rank',0),release['source_rank'])
            if release['source'] not in card['sources']: card['sources'].append(release['source'])
        return list(groups.values())

    def home(self):
        states,releases = self.snapshot()
        shelves = []
        for source,title in SOURCES.items():
            state = states.get(source,{})
            fetched = state.get('fetched_at',0)
            selected = [r for r in releases if fetched and r['source']==source and r.get('seen_at')==fetched]
            cards = self.cards(selected)
            cards.sort(key=lambda r: (r['seeders'] or 0,r['published_at']) if source=='rutor' else (True,r.get('source_rank',0)) if source=='anwap' else ((r['media_type']=='movie') if source=='exkinoray' else True,r['published_at']),reverse=True)
            if source=='anwap':
                cards=[c for c in cards if (c.get('year') or 0)>=2020]
                with self.db() as db:
                    series=[json.loads(r[0]) for r in db.execute('SELECT payload FROM catalog_home_series WHERE source=? ORDER BY rowid',(source,))]
                series=[c for c in series if (c.get('year') or 0)>=2020]
                # The two source feeds have no comparable timestamps. Keep each feed's order.
                cards=[items[i] for i in range(max(len(cards),len(series))) for items in (cards,series) if i<len(items)]
            shelves.append({'id':source,'title':title,'results':cards[:40], 'fetched_at':fetched,
                            'stale':bool(fetched and (time.time()-fetched>=TTL or state.get('error'))),
                            'warming':not bool(state),'error':state.get('error'),'received':state.get('received',0),
                            'scope':'popular_in_latest_100' if source=='rutor' else 'latest_available'})
        return {'shelves':shelves,'cache_seconds':TTL}

    def search(self, query='', kind=None):
        _,releases = self.snapshot()
        cards = self.cards(releases)
        known={c['id'] for c in cards}
        with self.db() as db:
            for row in db.execute('SELECT payload FROM catalog_provider_items'):
                card=json.loads(row['payload'])
                if card['id'] not in known:cards.append(card);known.add(card['id'])
        terms = normal(query).split()
        metadata_titles={}
        if terms:
            with self.db() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata_cache'").fetchone():
                    metadata_titles={r['content_id']:json.loads(r['payload']).get('title') or '' for r in db.execute('SELECT content_id,payload FROM metadata_cache WHERE payload IS NOT NULL')}
        cards = [c for c in cards if (not kind or c['media_type']==kind) and all(t in normal(' '.join([c['title']]+c['aliases']+[metadata_titles.get(c['id'],''),(c.get('metadata') or {}).get('title') or ''])) for t in terms)]
        cards.sort(key=lambda c:c['published_at'],reverse=True)
        return {'results':cards[:200], 'total':len(cards)}

    def detail(self, content_id):
        with self.db() as db:
            releases = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM catalog_releases WHERE content_id=?',(content_id,))]
        with self.db() as db:
            entry=db.execute('SELECT payload FROM catalog_provider_items WHERE id=?',(content_id,)).fetchone()
        if not releases and not entry: return None
        source_slug=None
        if releases and all(r['source']=='lostfilm' for r in releases):
            # A title alone can match two remakes. Keep the exact provider series from the current feed.
            primary=max(releases,key=lambda r:(r.get('seen_at',0),r.get('published_at',0)))
            source_slug=lostfilm_slug(primary.get('details',''))
            if source_slug:releases=[r for r in releases if lostfilm_slug(r.get('details',''))==source_slug]
        card = self.cards(releases)[0] if releases else json.loads(entry['payload'])
        if source_slug:card['source_slug']=source_slug
        if entry:
            directory=json.loads(entry['payload'])
            if native_matches_source(card,directory.get('metadata')):
                card['provider_title']=directory.get('provider_title',card['title'])
                card['metadata']=merge_native(card.get('metadata'),directory.get('metadata'))
                card['aliases']=list(dict.fromkeys(card.get('aliases',[])+directory.get('aliases',[])))
        card['releases'] = [dict(id=r['id'],source=r['source'],title=r['raw'],kind='direct' if r['source']=='anwap' else 'torrent',seeders=r['seeders'],size=r['size'],published_at=r['published_at'],quality=r['content']['quality'],season=r['content']['season'],episode=r['content']['episode']) for r in sorted(releases,key=lambda r:(r['published_at'],r['seeders'] or 0),reverse=True)]
        return card
