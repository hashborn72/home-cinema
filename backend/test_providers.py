import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from catalog import Catalog, identify, parse_feed
from providers import Providers
from providers import lostfilm_directory
import httpx
from server import create_app
from test_catalog import release

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cat=Catalog(Path(self.temp.name));self.p=Providers(self.cat)
    def tearDown(self):self.p.pool.shutdown(wait=True);self.temp.cleanup()
    def wait(self,source,q='',offset=0,cid=''):
        for _ in range(200):
            state=self.p.state(source,q,offset,cid)
            if state['status']!='loading':return state
            time.sleep(.01)
        self.fail('Worker did not finish')
    def test_expansion_seven_episodes_keeps_home_and_identity(self):
        self.cat.ingest('lostfilm',[release('e7','Lanterns S01E07 1080p','lostfilm',[5000])])
        before=self.cat.home();cid=before['shelves'][0]['results'][0]['id']
        rows=[release('e'+str(i),'Lanterns S01E%02d 1080p'%i,'lostfilm',[5000]) for i in range(1,8)]
        rows.append(release('wrong','Lanterns Return S01E01','lostfilm',[5000]))
        with patch.object(self.p,'jackett',return_value=rows) as fetch:
            self.p.expand(cid,True);state=self.wait('lostfilm','Lanterns',cid=cid)
            self.assertEqual(state['status'],'ready');self.assertEqual(len(self.cat.detail(cid)['releases']),7)
            self.assertEqual(self.cat.home(),before)
            self.p.expand(cid,True);self.assertEqual(fetch.call_count,1)
        self.assertNotIn('SECRET',json.dumps(state))
        self.assertEqual(len(self.cat.search()['results']),1)
    def test_directory_card_opens_without_any_release(self):
        card=dict(identify('Archive Show S01E01','lostfilm','tv','x'),sources=['lostfilm'],release_count=0,provider_title='Архив')
        with patch('providers.lostfilm_directory',return_value=([card],True)):
            self.p.start('lostfilm');state=self.wait('lostfilm')
        self.assertEqual(state['next_offset'],20);self.assertTrue(state['has_more'])
        self.assertEqual(self.cat.detail(card['id'])['releases'],[])
        self.assertEqual(Catalog(Path(self.temp.name)).detail(card['id'])['provider_title'],'Архив')
        self.assertEqual(self.cat.home()['shelves'][0]['results'],[])
    def test_other_provider_search_is_source_scoped(self):
        with patch.object(self.p,'jackett',return_value=[release('a','Archive (2000) BDRip')]) as fetch:
            self.p.start('exkinoray','Archive',100);state=self.wait('exkinoray','Archive',100)
            fetch.assert_called_once_with('exkinoray','Archive',100,100)
        self.assertEqual(state['status'],'ready');self.assertEqual(len(state['results']),1)
        self.assertFalse(state['has_more']);self.assertEqual(next(s for s in self.cat.home()['shelves'] if s['id']=='exkinoray')['results'],[])
    def test_rutor_search_returns_top_torrent_after_jackett_id_is_remapped(self):
        top=dict(release('direct-id','Фильм / Movie (2026) WEB-DL','rutor',kind='movie'),
                 details='https://rutor.info/torrent/123/film',home_category='kino',home_rank=0)
        self.cat.ingest('rutor',[top])
        cid=self.cat.search()['results'][0]['id']
        search=dict(top,id='jackett-id',details='http://rutor.is/torrent/123/renamed')
        search.pop('home_category');search.pop('home_rank')
        with patch.object(self.p,'jackett',return_value=[search]):
            self.p.start('rutor','Фильм');state=self.wait('rutor','Фильм')
        self.assertEqual(state['status'],'ready')
        self.assertEqual([card['id'] for card in state['results']],[cid])
        self.assertEqual([r['id'] for r in self.cat.detail(cid)['releases']],['direct-id'])
        self.assertEqual(next(s for s in self.cat.home()['shelves'] if s['id']=='rutor')['scope'],'category_top')
    def test_paging_same_show_with_different_releases_is_not_the_end(self):
        rows=[release(str(i),'Same Show S01E%02d (2020) 1080p'%(i+1),cats=[5000]) for i in range(100)]
        with patch.object(self.p,'jackett',return_value=rows):
            self.p.start('exkinoray');self.wait('exkinoray')
        other=[dict(r,id='next'+r['id']) for r in rows]
        with patch.object(self.p,'jackett',return_value=other):
            self.p.start('exkinoray',offset=100);second=self.wait('exkinoray',offset=100)
            self.assertTrue(second['has_more'])
            self.p.start('exkinoray',offset=200);third=self.wait('exkinoray',offset=200)
            self.assertFalse(third['has_more'])
    def test_failure_redacts_secrets_and_can_retry(self):
        with patch.object(self.p,'jackett',side_effect=RuntimeError('apikey=SECRET')):
            self.p.start('rutor','test');state=self.wait('rutor','test')
        self.assertEqual(state['status'],'error');self.assertNotIn('SECRET',json.dumps(state))
        with patch.object(self.p,'jackett',return_value=[]):
            self.p.start('rutor','test');self.assertEqual(self.wait('rutor','test')['status'],'ready')
    def test_invalid_queries_and_expansion(self):
        for source,q,offset in [('bad','',0),('lostfilm','x'*121,0),('rutor','',-1)]:
            with self.assertRaises(ValueError):self.p.start(source,q,offset)
        with self.assertRaises(KeyError):self.p.expand('absent',True)
    def test_public_directory_search_and_safe_poster(self):
        rows=[{'title_orig':'Lanterns','title':'Фонари','link':'/series/Lanterns','img':'/Static/Images/1153/Posters/image.jpg'},
              {'title_orig':'Evil','link':'https://evil.invalid/','img':'http://127.0.0.1/secret'}]
        def handle(request):
            if request.method=='GET':return httpx.Response(200,text='directory')
            return httpx.Response(200,json={'result':'ok','data':{'series':rows}})
        client=httpx.Client(transport=httpx.MockTransport(handle))
        with patch('providers.httpx.Client',return_value=client): cards,more=lostfilm_directory('Фонари',0)
        self.assertEqual(len(cards),1);self.assertEqual(cards[0]['provider_title'],'Фонари')
        self.assertTrue(cards[0]['metadata']['poster'].startswith('https://www.lostfilm.tv/Static/'))
        self.assertFalse(more)
    def test_anwap_page_uses_upstream_search(self):
        import anwap
        with patch.object(anwap,'html_page',return_value='<a href="/films/search/?page=3">3</a>') as fetch,patch.object(anwap,'film_ids',return_value=[]):
            self.p.start('anwap','Матрица',10);state=self.wait('anwap','Матрица',10)
        self.assertEqual(state['status'],'ready');self.assertTrue(state['has_more']);self.assertIn('page=2',fetch.call_args[0][0])
    def test_bounded_workers_and_dedup(self):
        import threading
        event=threading.Event()
        with patch.object(self.p,'jackett',side_effect=lambda *a: (event.wait(2) and []) or []):
            try:
                self.p.start('rutor','one');self.p.start('rutor','two')
                self.assertEqual(self.p.start('rutor','one')['status'],'loading')
                with self.assertRaises(RuntimeError):self.p.start('rutor','three')
            finally:event.set();self.wait('rutor','one');self.wait('rutor','two')
    def test_feed_explicit_limit_does_not_cut_at_100(self):
        xml=('<rss><channel>'+''.join('<item><title>X S01E%d</title><guid>%d</guid></item>'%(i,i) for i in range(120))+'</channel></rss>').encode()
        self.assertEqual(len(parse_feed(xml,'lostfilm')),100)
        self.assertEqual(len(parse_feed(xml,'lostfilm',1000)),120)
    def test_api_protected_and_validation(self):
        app=create_app(Path(self.temp.name),'test')
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/v1/providers/lostfilm').status_code,401)
            client.headers['Authorization']='Bearer test'
            self.assertEqual(client.get('/api/v1/providers/unknown').status_code,422)
            self.assertEqual(client.post('/api/v1/providers/lostfilm',json={'offset':-1}).status_code,422)
            self.assertEqual(client.post('/api/v1/catalog/items/missing/expand').status_code,404)
            with patch.object(app.state.providers,'start',return_value={'status':'ready','results':[]}):
                self.assertEqual(client.post('/api/v1/providers/lostfilm',json={}).status_code,200)

if __name__=='__main__':unittest.main()
