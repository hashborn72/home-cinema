import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from catalog import Catalog, identify, parse_feed, category_html
from server import create_app

def release(rid, title, source='exkinoray', cats=None, **kwargs):
    return dict(id=rid,source=source,raw=title,categories=cats or [2000],seeders=8,size=1234,published_at=100,details='',download_url='http://jackett/?apikey=SECRET',**kwargs)

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cat=Catalog(Path(self.temp.name))
    def tearDown(self): self.temp.cleanup()
    def test_clean_real_lostfilm_and_group_episodes(self):
        a=identify('American Horror Story - S13E3 - Rauhnacht - rus 1080p WEBDL (LostFilm)','lostfilm','tv','a')
        b=identify('American Horror Story - S13E4 - Another - rus 720p WEBDL (LostFilm)','lostfilm','tv','b')
        self.assertEqual(a['title'],'American Horror Story')
        self.assertEqual((a['season'],a['episode']),(13,3))
        self.assertEqual(a['id'],b['id'])
    def test_cross_source_movie_and_remake_safety(self):
        a=identify('Нечто / The Thing (1982) BDRip 1080p | D','exkinoray','movie','a')
        b=identify('Нечто / The Thing (1982) Remux 2160p','rutor','movie','b')
        c=identify('Нечто / The Thing (2011) BDRip','rutor','movie','c')
        self.assertEqual(a['id'],b['id']); self.assertNotEqual(a['id'],c['id'])
        self.assertNotEqual(a['id'],identify('Нечто 2 (1982) WEB-DL','rutor','movie','d')['id'])
    def test_unknown_and_collection_never_silently_merge(self):
        for title in ('Нечто WEB-DL','Обитель зла: Anthology (2002-2023) BDRip'):
            self.assertNotEqual(identify(title,'rutor','movie','a')['id'],identify(title,'rutor','movie','b')['id'])
    def test_rutor_fail_closed_and_order(self):
        rows=[release('a','Фильм (2020) WEB-DL','rutor',[8000],kind='movie'),release('b','Музыка (2021)','rutor',[8000],kind='excluded'),release('c','Игра (2026)','rutor',[8000],kind=None),release('d','Другой (2022)','rutor',[8000],kind='tv')]
        rows[3]['seeders']=100
        self.cat.ingest('rutor',rows)
        cards=self.cat.home()['shelves'][2]['results']
        self.assertEqual([c['title'] for c in cards],['Другой','Фильм'])
    def test_category_html(self):
        self.assertEqual(category_html('<td>Категория</td><td><a href="/kino">Films</a>','film'),'movie')
        self.assertEqual(category_html('<td>Категория</td><td><a href="/igry">Games</a>','film'),'excluded')
        self.assertIsNone(category_html('<html>Challenge</html>','film'))
    def test_persistence_safe_public_detail_and_grouping(self):
        self.cat.ingest('exkinoray',[release('a','Фильм (2020) BDRip'),release('b','Фильм (2020) Remux')])
        self.cat.ingest('rutor',[release('c','Фильм (2020) 1080p','rutor',[8000],kind='movie')])
        other=Catalog(Path(self.temp.name))
        cards=other.search()['results']; self.assertEqual(len(cards),1)
        detail=other.detail(cards[0]['id']); self.assertEqual(len(detail['releases']),3)
        self.assertNotIn('SECRET',json.dumps(detail)); self.assertNotIn('download_url',json.dumps(detail))
    def test_stale_survives_upstream_failure(self):
        self.cat.ingest('lostfilm',[release('a','Series S01E01 WEB-DL','lostfilm',[5000])])
        with patch('catalog.fetch',side_effect=RuntimeError('apikey=SECRET')): self.cat.refresh('lostfilm')
        shelf=self.cat.home()['shelves'][0]
        self.assertEqual(len(shelf['results']),1); self.assertTrue(shelf['stale'])
        self.assertNotIn('SECRET',json.dumps(shelf))
    def test_empty_success_replaces_shelf_not_detail(self):
        self.cat.ingest('exkinoray',[release('a','Фильм (2020) BDRip')])
        cid=self.cat.search()['results'][0]['id']
        self.cat.ingest('exkinoray',[])
        self.assertEqual(self.cat.home()['shelves'][1]['results'],[])
        self.assertIsNotNone(self.cat.detail(cid))
    def test_feed_and_unsafe_xml(self):
        raw=b'<rss xmlns:t="http://torznab.com/schemas/2015/feed"><channel><item><title>Film (2020)</title><guid>a</guid><t:attr name="category" value="2000"/><t:attr name="seeders" value="10"/></item></channel></rss>'
        row=parse_feed(raw,'rutor')[0]; self.assertEqual(row['seeders'],10); self.assertEqual(row['categories'],[2000])
        with self.assertRaises(ValueError): parse_feed(b'<!DOCTYPE rss><rss/>','rutor')
    def test_api_auth_search_and_detail(self):
        app=create_app(Path(self.temp.name),'token')
        app.state.catalog.ingest('exkinoray',[release('a','Фильм / Movie (2020) BDRip')])
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/v1/catalog/home').status_code,401)
            client.headers['Authorization']='Bearer token'
            result=client.get('/api/v1/catalog/search?q=movie&kind=movie').json()
            self.assertEqual(result['total'],1)
            self.assertEqual(client.get('/api/v1/catalog/items/'+result['results'][0]['id']).status_code,200)
            self.assertEqual(client.get('/api/v1/catalog/items/no-such').status_code,404)
            self.assertEqual(client.get('/api/v1/catalog/search?kind=game').status_code,422)

if __name__=='__main__': unittest.main()
