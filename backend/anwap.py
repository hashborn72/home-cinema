"""Small HTML adapter: fixed movie pages and visible MP4 download links only."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin,urlparse
import httpx
from catalog import digest
from catalog import normal
from urllib.parse import urlencode
import threading
import time

BASE='https://mm.anwap.media'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; HomeCinema/0.4)'}

class Page(HTMLParser):
    def __init__(self,body):
        super().__init__(convert_charrefs=True)
        self.meta={};self.links=[];self.anchor=None;self.heading='';self.in_heading=False
        self.feed(body)
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta': self.meta[a.get('property',a.get('name',''))]=a.get('content','')
        if tag=='h1' and not self.heading: self.in_heading=True
        if tag=='a': self.anchor={'href':a.get('href',''),'text':''}
        if tag=='img' and self.anchor: self.anchor['image']=a.get('src','');self.anchor['alt']=a.get('alt','')
    def handle_data(self,data):
        if self.in_heading:self.heading+=data
        if self.anchor:self.anchor['text']+=data
    def handle_endtag(self,tag):
        if tag=='h1':self.in_heading=False
        if tag=='a' and self.anchor:self.links.append(self.anchor);self.anchor=None

def html_page(path):
    with httpx.Client(timeout=12,follow_redirects=False,trust_env=False,headers=HEADERS) as client:
        with client.stream('GET',BASE+path) as r:
            r.raise_for_status();body=bytearray()
            for part in r.iter_bytes():
                body.extend(part)
                if len(body)>2_000_000:raise ValueError('Page too large')
    return body.decode('utf-8','replace')

def film_ids(body):
    return list(dict.fromkeys(int(m[1]) for a in Page(body).links if (m:=re.fullmatch(r'/films/(\d+)',a['href']))))[:10]

def parse_movie(body,film_id):
    page=Page(body)
    title=page.heading.strip()
    m=re.search(r'\(((?:19|20)\d{2})\)',page.meta.get('og:title',''))
    if not title or not m or page.meta.get('og:type')!='video.movie':raise ValueError('Not a movie page')
    year=int(m[1]); formats=[]
    for a in page.links:
        path=urlparse(urljoin(BASE,a['href']))
        match=re.fullmatch(r'/films/load/[a-zA-Z0-9]+/(\d+)/'+str(film_id),path.path)
        if path.netloc!='mm.anwap.media' or not match or 'MP4' not in a['text']:continue
        size=re.search(r'(\d+(?:[.,]\d+)?)\s*(мб|гб)',a['text'],re.I)
        resolution=re.search(r'(\d{2,4})x(\d{2,4})',a['text'])
        label=' '.join(a['text'].split())
        formats.append({'id':int(match[1]),'path':label,'load':path.path,
                        'size':int(float(size[1].replace(',','.'))*(1024**2 if size[2].lower()=='мб' else 1024**3)) if size else None,
                        'resolution':resolution[0] if resolution else None,'sample':False})
    if not formats:raise ValueError('No visible MP4 formats')
    return {'id':digest('anwap:'+str(film_id)),'source':'anwap','raw':f'{title} ({year})',
            'categories':[2000],'seeders':None,'size':None,'published_at':0,
            'details':BASE+'/films/'+str(film_id),'download_url':'','film_id':film_id,'formats':formats,
            'metadata':{'provider':'Anwap','url':BASE+'/films/'+str(film_id),'title':title,
                        'description':page.meta.get('og:desc',page.meta.get('description',''))[:6000],
                        'poster':BASE+'/films/screen/'+str(film_id)+'.jpg','rating':None,'language':'ru','license':'','match':'source_id'}}

def latest():
    rows=[]
    ids=film_ids(html_page('/'))
    for index,fid in enumerate(ids):
        rows.append(dict(parse_movie(html_page('/films/'+str(fid)),fid),source_rank=len(ids)-index))
    if not rows:raise ValueError('Empty or changed listing')
    return rows

def allowed_media(url):
    p=urlparse(url)
    return (p.scheme=='https' and p.port in (None,443) and not p.username and not p.password
            and bool(re.fullmatch(r'[a-z]\d+\.anwap\.be',p.hostname or '')))

class Search:
    def __init__(self,catalog):
        self.catalog=catalog;self.lock=threading.Lock();self.busy=False
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS anwap_search(query TEXT PRIMARY KEY,status TEXT NOT NULL,updated_at REAL NOT NULL,count INTEGER NOT NULL DEFAULT 0)')
            db.execute("UPDATE anwap_search SET status='error' WHERE status='searching'")
    def state(self,query):
        with self.catalog.db() as db:
            row=db.execute('SELECT * FROM anwap_search WHERE query=?',(normal(query),)).fetchone()
        return dict(row) if row else {'status':'new','count':0}
    def start(self,query):
        query=normal(query)
        if not 2<=len(query)<=120:raise ValueError('Query length')
        with self.lock:
            old=self.state(query)
            if old['status']=='ready' and time.time()-old['updated_at']<600:return old
            if self.busy:
                if old['status']=='searching':return old
                raise RuntimeError('Search busy')
            self.busy=True
            with self.catalog.db() as db:db.execute('INSERT OR REPLACE INTO anwap_search VALUES (?,?,?,?)',(query,'searching',time.time(),0))
            threading.Thread(target=self.run,args=(query,),daemon=True).start()
        return self.state(query)
    def run(self,query):
        count=0;status='error'
        try:
            rows=[]
            for fid in film_ids(html_page('/films/search/?'+urlencode({'slv':query,'vid':1}))):
                row=parse_movie(html_page('/films/'+str(fid)),fid)
                if all(t in normal(row['raw']) for t in query.split()):rows.append(row)
            rows.sort(key=lambda row:normal(row['metadata']['title'])!=query)
            rows=rows[:5]
            self.catalog.ingest('anwap',rows,update_shelf=False)
            count=len(rows);status='ready'
        except Exception:pass
        finally:
            with self.catalog.db() as db:db.execute('UPDATE anwap_search SET status=?,updated_at=?,count=? WHERE query=?',(status,time.time(),count,query))
            with self.lock:self.busy=False

def resolve(film_id,format_id):
    fresh=parse_movie(html_page('/films/'+str(film_id)),film_id)
    file=next((f for f in fresh['formats'] if f['id']==format_id),None)
    if file is None:raise ValueError('Format no longer available')
    url=BASE+file['load']
    with httpx.Client(timeout=12,follow_redirects=False,trust_env=False,headers=HEADERS) as client:
        for step in range(4):
            if step and not allowed_media(url):raise ValueError('Unexpected media host')
            with client.stream('GET',url,headers={'Range':'bytes=0-0','Referer':fresh['details']}) as r:
                if r.status_code in (301,302,303,307,308):
                    url=urljoin(url,r.headers.get('location',''));continue
                if not allowed_media(url) or r.status_code!=206:raise ValueError('Seekable media not available')
                cr=re.fullmatch(r'bytes 0-0/(\d+)',r.headers.get('content-range',''))
                if not cr or not r.headers.get('content-type','').lower().startswith(('video/','application/octet-stream')):raise ValueError('Not a video response')
                # Signed URLs change; identity is bound to source, format and actual file revision.
                revision=r.headers.get('etag') or r.headers.get('last-modified','')
                key=digest(f'anwap:{film_id}:{format_id}:{cr[1]}:{revision}')
                return {'stream_url':url,'file_key':'anwap:'+key,'title':fresh['raw']+' · '+file['path']}
    raise ValueError('Too many redirects')
