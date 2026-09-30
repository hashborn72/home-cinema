"""Dev-only bridge: classify via the PBR host in memory, persist only on autobot.

No files or packages are installed on the PBR host. Only fixed RuTor torrent
pages can be read. No Jackett keys or download links leave autobot.
"""
import concurrent.futures
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

def valid(url):
    p=urllib.parse.urlparse(url)
    return p.scheme in ('http','https') and p.hostname in ('rutor.info','rutor.is') and p.port in (None,80,443) and not p.username and p.path.startswith('/torrent/')

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not valid(newurl): raise ValueError('Redirect rejected')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def classify(rows):
    paths={'/kino':'movie','/nashe_kino':'movie','/nauchno_popularnoe':'movie','/seriali':'tv','/nashi_seriali':'tv','/tv':'tv','/multiki':'movie','/anime':'anime'}
    def one(row):
        url=row['url']; kind=None
        if valid(url):
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'HomeCinema/0.2'})
                with urllib.request.build_opener(Redirect).open(req,timeout=8) as r:
                    body=r.read(2_000_001)
                if len(body)>2_000_000: raise ValueError('Too large')
                m=re.search(r'''(?is)<td[^>]*>\s*Категория\s*</td>\s*<td[^>]*>\s*<a[^>]+href=["']([^"']+)''',body.decode('utf-8','replace'))
                if m: kind=paths.get(urllib.parse.urlparse(m[1]).path.rstrip('/'),'excluded')
                if kind=='anime': kind='tv' if re.search(r'(?i)\bS\d|\b\d+x\d|сезон|серии',row['title']) else 'movie'
            except Exception: pass
        return {'url':url,'kind':kind}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        print(json.dumps(list(pool.map(one,rows[:100])),ensure_ascii=False))

def export():
    sys.path.insert(0,'/root/home-cinema/backend')
    from catalog import fetch,parse_feed
    key=Path('/root/home-cinema/data/jackett-key').read_text().strip()
    params=urllib.parse.urlencode({'apikey':key,'t':'search','limit':100})
    rows=parse_feed(fetch('http://192.168.1.144:8091/api/v2.0/indexers/rutor/results/torznab/api?'+params),'rutor')
    print(json.dumps([{'url':r['details'],'title':r['raw']} for r in rows],ensure_ascii=False))

def import_categories():
    rows=json.load(sys.stdin)
    conn=sqlite3.connect('/root/home-cinema/data/library.sqlite3',timeout=10)
    try:
        with conn:
            for row in rows:
                if valid(row['url']) and row['kind'] in (None,'movie','tv','excluded'):
                    conn.execute('INSERT OR REPLACE INTO catalog_categories VALUES (?,?,?)',(row['url'],row['kind'],time.time()))
    finally: conn.close()
    print(json.dumps({'checked':len(rows),'video':sum(r['kind'] in ('movie','tv') for r in rows),'excluded':sum(r['kind']=='excluded' for r in rows),'unknown':sum(r['kind'] is None for r in rows)}))

if __name__=='__main__' and len(sys.argv)>1:
    if sys.argv[1]=='export': export()
    elif sys.argv[1]=='import': import_categories()
