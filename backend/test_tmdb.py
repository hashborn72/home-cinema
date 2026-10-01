import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from catalog import Catalog
from metadata import Metadata
from server import create_app
from test_catalog import release
from tmdb import TMDB, match


class TMDBTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.api=TMDB(self.root)
        self.card={'title':'Матрица','aliases':['The Matrix'],'year':1999,'media_type':'movie','confidence':'title_year'}
        self.movie={'id':603,'title':'Матрица','original_title':'The Matrix','release_date':'1999-03-30','overview':'Описание','poster_path':'/poster.jpg','vote_count':5,'vote_average':8.2}
    def tearDown(self):self.temp.cleanup()
    def test_film_requires_exact_name_and_year(self):
        self.assertEqual(match(self.card,[self.movie])['id'],603)
        self.assertIsNone(match(dict(self.card,year=2000),[self.movie]))
        self.assertIsNone(match(dict(self.card,title='Матрица 2',aliases=[]),[self.movie]))
        self.assertIsNone(match(dict(self.card,confidence='unmatched'),[self.movie]))
    def test_ambiguity_and_adult_rejected(self):
        self.assertIsNone(match(self.card,[self.movie,dict(self.movie,id=604)]))
        self.assertIsNone(match(self.card,[dict(self.movie,adult=True)]))
        self.assertEqual(match(self.card,[self.movie,self.movie])['id'],603)
    def test_series_year_not_used_to_guess_remake(self):
        card={'title':'Dark','media_type':'tv','year':2026}
        show={'id':1,'name':'Dark','first_air_date':'2017-01-01'}
        self.assertEqual(match(card,[show])['id'],1)
        self.assertIsNone(match(card,[show,dict(show,id=2,first_air_date='2026-01-01')]))
    def test_header_auth_and_russian_metadata(self):
        (self.root/'tmdb-token').write_text('PRIVATE-TMDB-TOKEN')
        def handler(req):
            self.assertEqual(req.headers['Authorization'],'Bearer PRIVATE-TMDB-TOKEN')
            self.assertNotIn('PRIVATE',str(req.url))
            if req.url.path.endswith('/configuration'):
                return httpx.Response(200,json={'images':{'secure_base_url':'https://image.tmdb.org/t/p/','poster_sizes':['w500']}})
            self.assertEqual(req.url.params['language'],'ru-RU')
            self.assertEqual(req.url.params['primary_release_year'],'1999')
            return httpx.Response(200,json={'results':[self.movie],'total_pages':1})
        original=httpx.Client
        def make(**kwargs):return original(transport=httpx.MockTransport(handler))
        with patch('tmdb.httpx.Client',side_effect=make):result=self.api.lookup(self.card)
        self.assertEqual(result['provider'],'TMDB');self.assertEqual(result['poster'],'https://image.tmdb.org/t/p/w500/poster.jpg')
        self.assertNotIn('PRIVATE',json.dumps(result))
    def test_errors_do_not_expose_token_or_response_body(self):
        (self.root/'tmdb-token').write_text('PRIVATE-TMDB-TOKEN')
        original=httpx.Client
        with patch('tmdb.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda _:httpx.Response(401,text='PRIVATE-RESPONSE')))):
            with self.assertRaisesRegex(ValueError,'HTTP 401') as error:self.api.request('/configuration')
        self.assertNotIn('PRIVATE',str(error.exception))
    def test_uninspected_pages_fail_closed(self):
        with patch.object(self.api,'request',return_value={'total_pages':2,'results':[self.movie]}):
            self.assertIsNone(self.api.lookup(self.card))
    def test_bad_poster_and_zero_votes_not_presented(self):
        with patch.object(self.api,'request',return_value={'results':[dict(self.movie,poster_path='https://evil/a',vote_count=0)]}):
            self.api.image_base='https://image.tmdb.org/t/p/w500';result=self.api.lookup(self.card)
        self.assertIsNone(result['poster']);self.assertIsNone(result['rating'])
    def test_opt_in_failure_preserves_catalog_and_metadata(self):
        cat=Catalog(self.root);cat.ingest('exkinoray',[release('a','Матрица (1999) WEB-DL')]);meta=Metadata(cat)
        card=cat.search()['results'][0]
        self.assertFalse(meta.tmdb.enabled())
        (self.root/'tmdb-token').write_text('PRIVATE')
        with patch.object(meta.tmdb,'lookup',return_value={'provider':'TMDB','poster':None,'title':'Матрица'}):meta.refresh(card)
        before=meta.enrich([dict(card)])[0]
        with patch.object(meta.tmdb,'lookup',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):meta.refresh(card)
        self.assertEqual(meta.enrich([dict(card)])[0],before)
        self.assertEqual(cat.search()['results'][0]['id'],card['id'])
    def test_status_requires_auth_and_contains_no_secret(self):
        (self.root/'tmdb-token').write_text('PRIVATE')
        client=TestClient(create_app(self.root,'device'))
        self.assertEqual(client.get('/api/v1/metadata/status').status_code,401)
        r=client.get('/api/v1/metadata/status',headers={'Authorization':'Bearer device'})
        self.assertTrue(r.json()['tmdb_configured']);self.assertNotIn('PRIVATE',r.text)

    def test_search_accepts_localized_title_without_changing_identity(self):
        cat=Catalog(self.root);cat.ingest('lostfilm',[release('tv','Lanterns - S01E01 - Pilot',source='lostfilm',cats=[5000])])
        Metadata(cat)
        cid=cat.search('Lanterns')['results'][0]['id']
        with cat.db() as db:db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(cid,json.dumps({'provider':'TMDB','title':'Фонари'}),1))
        self.assertEqual(cat.search('Фонари')['results'][0]['id'],cid)
        self.assertEqual(cat.search('Lanterns')['results'][0]['id'],cid)
        self.assertEqual(cat.search('Фонари',kind='movie')['results'],[])

    def test_poster_falls_back_to_native_and_cools_down(self):
        cat=Catalog(self.root)
        native={'provider':'Anwap','poster':'https://mm.anwap.media/films/screen/123.jpg'}
        cat.ingest('anwap',[release('a','Матрица (1999)',source='anwap',metadata=native)])
        card=cat.search()['results'][0];meta=Metadata(cat)
        with cat.db() as db:db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(card['id'],json.dumps({'provider':'TMDB','poster':'https://image.tmdb.org/t/p/w500/p.jpg'}),1))
        calls=[]
        def poster(url):
            calls.append(url)
            if 'image.tmdb.org' in url:raise TimeoutError()
            from test_artwork import png
            meta._record_image(url,png())
            return Path('cached-native')
        with patch.object(meta,'_poster_url',side_effect=poster):
            self.assertEqual(meta.poster(card['id']),Path('cached-native'))
            self.assertEqual(meta.poster(card['id']),Path('cached-native'))
        self.assertEqual(sum('image.tmdb.org' in u for u in calls),1)

    def test_poster_download_never_receives_api_token(self):
        (self.root/'tmdb-token').write_text('PRIVATE')
        meta=Metadata(Catalog(self.root));original=httpx.Client
        def handle(req):
            self.assertNotIn('authorization',req.headers)
            from test_artwork import png
            return httpx.Response(200,content=png())
        with patch('metadata.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle))):
            path=meta._poster_url('https://image.tmdb.org/t/p/w500/p.jpg')
        self.assertTrue(path.is_file())
        for url in ['http://image.tmdb.org/t/p/w500/p.jpg','https://evil/p.jpg','https://image.tmdb.org/t/p/w500/../key']:
            with self.assertRaises(KeyError):meta._poster_url(url)
