"""Bounded on-demand provider browsing and series expansion; no credentials in responses."""
import concurrent.futures
import json
import re
import threading
import time
from urllib.parse import urlencode
import httpx
from catalog import SOURCES, digest, normal, identify, parse_feed, fetch
from settings import jackett_url

LOSTFILM='https://www.lostfilm.tv'

def lostfilm_directory(query, offset):
    headers={'User-Agent':'Mozilla/5.0 (compatible; HomeCinema)',
             'Referer':LOSTFILM+'/series/','X-Requested-With':'XMLHttpRequest'}
    with httpx.Client(timeout=20,follow_redirects=False,trust_env=False,headers=headers) as client:
        client.get(LOSTFILM+'/series/').raise_for_status()
        params={'act':'common','type':'search','val':query} if query else {'act':'serial','type':'search','o':offset,'s':3,'t':0}
        with client.stream('POST',LOSTFILM+'/ajaxik.php',data=params) as response:
            response.raise_for_status(); body=bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body)>2_000_000: raise ValueError('Directory too large')
        payload=json.loads(body)
    if payload.get('result')!='ok': raise ValueError('Directory unavailable')
    rows=payload.get('data') or ([] if not query else {})
    if query: rows=rows.get('series') or []
    if not isinstance(rows,list): raise ValueError('Invalid directory')
    has_more=(offset+20<len(rows)) if query else len(rows)>=20
    if query: rows=rows[offset:offset+20]
    cards=[]
    for row in rows[:20]:
        title=str(row.get('title_orig') or '').strip()[:200]
        local=str(row.get('title') or title).strip()[:200]
        link=str(row.get('link') or '')
        if not title or not re.fullmatch(r'/series/[A-Za-z0-9_-]+/?',link): continue
        card=identify(title+' S01E01','lostfilm','tv','directory')
        card.update(aliases=[local],season=None,episode=None,release_count=0,seeders=None,published_at=0,
                    sources=['lostfilm'],provider_title=local,directory=True)
        poster=str(row.get('img') or '')
        if not re.fullmatch(r'/Static/Images/\d+/Posters/image(?:_s\d+)?\.jpg',poster):poster=''
        card['metadata']={'provider':'LostFilm','title':local,'url':LOSTFILM+link,
                          'poster':LOSTFILM+poster if poster else None,'rating':row.get('rating'),
                          'description':str(row.get('genres') or '')[:1000],
                          'language':'ru','license':'','match':'source_directory'}
        cards.append(card)
    return cards,has_more

class Providers:
    def __init__(self,catalog):
        self.catalog=catalog; self.lock=threading.Lock(); self.active=set()
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS provider_jobs(key TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
            db.execute("UPDATE provider_jobs SET status='error' WHERE status='loading'")

    def key(self,source,query,offset,cid):
        if source not in SOURCES or not 0<=offset<=10000 or len(query)>120: raise ValueError('Invalid provider query')
        return digest(json.dumps([source,normal(query),offset,cid]))

    def state(self,source,query='',offset=0,cid=''):
        key=self.key(source,query,offset,cid)
        with self.catalog.db() as db: row=db.execute('SELECT * FROM provider_jobs WHERE key=?',(key,)).fetchone()
        if not row:return {'status':'new','results':[],'has_more':False,'next_offset':offset}
        return dict(json.loads(row['payload']),status=row['status'],updated_at=row['updated_at'])

    def start(self,source,query='',offset=0,cid=''):
        query=query.strip(); key=self.key(source,query,offset,cid)
        with self.lock:
            old=self.state(source,query,offset,cid)
            if key in self.active or (old['status']=='ready' and time.time()-old['updated_at']<600):return old
            if len(self.active)>=2:raise RuntimeError('Provider busy')
            self.active.add(key)
            with self.catalog.db() as db:
                db.execute('INSERT OR REPLACE INTO provider_jobs VALUES (?,?,?,?)',(key,'loading',json.dumps(old),time.time()))
                db.execute("DELETE FROM provider_jobs WHERE status!='loading' AND key NOT IN (SELECT key FROM provider_jobs ORDER BY updated_at DESC LIMIT 256)")
            self.pool.submit(self.run,key,source,query,offset,cid)
        return self.state(source,query,offset,cid)

    def expand(self,cid,start=False):
        card=self.catalog.detail(cid)
        if not card:raise KeyError(cid)
        sources=[s for s in card['sources'] if s!='anwap']
        if card['media_type']!='tv' or not sources:raise ValueError('Not a torrent series')
        # Identity is source-specific for series. Do not merge unrelated same-name shows.
        method=self.start if start else self.state
        return method(sources[0],card['title'][:120],0,cid)

    def jackett(self,source,query,offset,limit):
        key=(self.catalog.data_dir/'jackett-key').read_text().strip()
        params={'apikey':key,'t':'search','q':query,'limit':limit,'offset':offset}
        if source!='rutor':params['cat']='5000' if source=='lostfilm' else '2000,5000'
        raw=fetch(jackett_url()+'/api/v2.0/indexers/'+source+'/results/torznab/api?'+urlencode(params),timeout=150)
        rows=parse_feed(raw,source,limit=limit)
        if source=='rutor':
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                for row,kind in zip(rows,pool.map(self.catalog.rutor_kind,rows)):row['kind']=kind
            if rows and not any(r['kind'] for r in rows):raise ValueError('Categories unavailable')
        return rows

    def run(self,key,source,query,offset,cid):
        result={'results':[],'has_more':False,'next_offset':offset}; status='error'
        try:
            if source=='lostfilm' and not cid:
                cards,more=lostfilm_directory(query,offset)
                with self.catalog.db() as db:
                    for card in cards: db.execute('INSERT OR REPLACE INTO catalog_provider_items VALUES (?,?)',(card['id'],json.dumps(card)))
                result.update(results=cards,has_more=more,next_offset=offset+20)
            else:
                if source=='anwap':
                    from anwap import html_page, film_ids, parse_movie, Page
                    page=offset//10+1
                    path='/films/search/?'+urlencode({'slv':query,'vid':1,'page':page}) if query else ('/' if page==1 else '/films/p-'+str(page))
                    body=html_page(path)
                    rows=[parse_movie(html_page('/films/'+str(fid)),fid) for fid in film_ids(body)]
                    more=any(('page='+str(page+1) in a['href']) if query else a['href']=='/films/p-'+str(page+1) for a in Page(body).links)
                    step=10
                else:
                    step=1000 if cid else 100
                    rows=self.jackett(source,query,offset,step); more=len(rows)>=step
                if cid:
                    # Jackett may broaden a query. Only accept exact existing content identities.
                    rows=[r for r in rows if identify(r['raw'],source,'tv',r['id'])['id']==cid]
                self.catalog.ingest(source,rows,update_shelf=False)
                ids={r['id'] for r in rows}
                with self.catalog.db() as db:
                    stored=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM catalog_releases WHERE source=?',(source,))]
                cards=self.catalog.cards([r for r in stored if r['id'] in ids])
                cards.sort(key=lambda c:c['published_at'],reverse=True)
                result.update(results=cards,has_more=more and not cid,next_offset=offset+step,truncated=bool(cid and more))
                if offset and not cid and source in ('rutor','exkinoray'):
                    previous=self.state(source,query,max(0,offset-step))
                    if ids and {c['id'] for c in cards}=={c['id'] for c in previous.get('results',[])}:
                        result['has_more']=False
                        result['notice']='Источник повторил предыдущую страницу; используйте поиск по названию.'
            status='ready'
        except Exception:
            # Never forward exception strings containing Jackett API keys or tracker URLs.
            result['error']='Источник временно недоступен. Повторите запрос.'
        finally:
            with self.catalog.db() as db: db.execute('UPDATE provider_jobs SET status=?,payload=?,updated_at=? WHERE key=?',(status,json.dumps(result),time.time(),key))
            with self.lock:self.active.discard(key)
