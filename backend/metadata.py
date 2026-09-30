"""Optional TVmaze enrichment. Never guess when several shows share a name."""
import html
import json
import re
import threading
import time
from urllib.parse import urlencode, urlparse
from catalog import normal, fetch


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
        with catalog.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS metadata_cache(content_id TEXT PRIMARY KEY,payload TEXT,checked_at REAL NOT NULL)')

    def enrich(self,cards):
        with self.catalog.db() as db:
            cache={r['content_id']:json.loads(r['payload']) for r in db.execute('SELECT * FROM metadata_cache WHERE payload IS NOT NULL')}
        for card in cards: card['metadata']=cache.get(card['id'])
        return cards

    def refresh(self,card):
        results=[]
        for name in [card['title']]+card.get('aliases',[]):
            results.extend(json.loads(fetch('https://api.tvmaze.com/search/shows?'+urlencode({'q':name}),timeout=8,limit=1_000_000)))
        metadata=match_show(card,results)
        with self.catalog.db() as db:
            db.execute('INSERT OR REPLACE INTO metadata_cache VALUES (?,?,?)',(card['id'],json.dumps(metadata) if metadata else None,time.time()))

    def start(self):
        def run():
            while not self.stop_event.is_set():
                with self.catalog.db() as db:
                    checked={r['content_id']:r['checked_at'] for r in db.execute('SELECT * FROM metadata_cache')}
                for card in self.catalog.search(kind='tv')['results']:
                    if self.stop_event.is_set(): return
                    if time.time()-checked.get(card['id'],0)<86400: continue
                    try: self.refresh(card)
                    except Exception: pass  # Keep existing metadata on transient failure.
                    if self.stop_event.wait(1): return
                self.stop_event.wait(600)
        threading.Thread(target=run,daemon=True).start()

    def stop(self): self.stop_event.set()
