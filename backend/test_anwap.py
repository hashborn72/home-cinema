import unittest
from unittest.mock import patch
from anwap import parse_movie,film_ids,allowed_media,resolve
import httpx
import tempfile
from pathlib import Path
from anwap import Search,latest,latest_home
from catalog import Catalog

HTML='''<meta property="og:title" content="Example (2020)"><meta property="og:type" content="video.movie">
<meta property="og:desc" content="A description"><h1>Example</h1>
<a href="/films/load/abc/2/123">Скачать MP4 320x240 200мб.</a>
<a href="/films/load/abc/3/123">Скачать MP4 1280x720 1.2гб.</a>
<a href="https://evil/films/load/abc/4/123">MP4</a><a href="/films/load/s/abc/3/123">MP4 sample</a>'''

class AnwapTests(unittest.TestCase):
    def test_home_paginates_and_filters_movies_and_series_since_2020(self):
        def page(path):
            if path=='/':return '<a href="/films/123">Old</a><a href="/films/p-2">Next</a>'
            if path=='/films/p-2':return '<a href="/films/124">New</a><a href="/films/125">Newer</a>'
            if path.startswith('/films/'):
                fid=path.rsplit('/',1)[1]
                return HTML.replace('/123','/'+fid).replace('(2020)','(1994)' if fid=='123' else '(2020)')
            if path=='/serials/':return '<a href="/serials/1">Old</a><a href="/serials/p-2">Next</a>'
            if path=='/serials/p-2':return '<a href="/serials/2">New</a>'
            sid=path.rsplit('/',1)[1];year=2019 if sid=='1' else 2026
            return f'<h1>Series {sid}</h1><meta property="og:url" content="https://mm.anwap.media/serials/{sid}"><a href="/serials/god-{year}">{year}</a>'
        with patch('anwap.html_page',side_effect=page):movies,series=latest_home(limit=2)
        self.assertEqual([m['film_id'] for m in movies],[124,125])
        self.assertEqual([m['source_rank'] for m in movies],[2,1])
        self.assertEqual([s['anwap_series_id'] for s in series],[2])
    def test_home_series_snapshot_is_independent_of_search_and_expansion(self):
        from anwap_series import series_card
        from test_catalog import release
        with tempfile.TemporaryDirectory() as tmp:
            cat=Catalog(Path(tmp))
            show=series_card('<h1>Series</h1><meta property="og:url" content="https://mm.anwap.media/serials/2"><a href="/serials/god-2026">2026</a>',2)
            cat.ingest('anwap',[parse_movie(HTML,123)],provider_cards=[show])
            shelf=cat.home()['shelves'][-1]['results']
            self.assertEqual([c['media_type'] for c in shelf],['movie','tv'])
            self.assertIsNotNone(cat.detail(show['id']))
            cat.ingest('anwap',[release('old','Old (1994)',source='anwap')],False)
            self.assertEqual([c['id'] for c in cat.home()['shelves'][-1]['results']],[c['id'] for c in shelf])
            self.assertEqual(len(cat.search('Old')['results']),1)
            with patch('anwap.latest_home',side_effect=TimeoutError()):cat.refresh('anwap')
            self.assertEqual([c['id'] for c in cat.home()['shelves'][-1]['results']],[c['id'] for c in shelf])
    def test_latest_preserves_source_order_without_fake_timestamp(self):
        def page(path):
            if path=='/':return '<a href="/films/124">A</a><a href="/films/123">B</a>'
            return HTML.replace('/123','/124') if path.endswith('124') else HTML
        with patch('anwap.html_page',side_effect=page):rows=latest()
        self.assertEqual([r['source_rank'] for r in rows],[2,1])
        self.assertEqual([r['published_at'] for r in rows],[0,0])

    def test_search_cache_and_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            search=Search(Catalog(Path(tmp)))
            with patch('anwap.threading.Thread') as thread:
                self.assertEqual(search.start('example')['status'],'searching')
                self.assertEqual(search.start('example')['status'],'searching')
                self.assertEqual(thread.call_count,1)
                with self.assertRaises(RuntimeError):search.start('different')
            with patch('anwap.html_page',return_value='<html></html>'):search.run('example')
            with patch('anwap.threading.Thread') as thread:
                self.assertEqual(search.start('example')['status'],'ready');thread.assert_not_called()

    def test_search_uses_title_mode_and_preserves_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            cat=Catalog(Path(tmp)); row=dict(parse_movie(HTML,123),source_rank=1); cat.ingest('anwap',[row])
            before=cat.home()['shelves'][-1]['results']
            search=Search(cat)
            def page(path):
                if path.startswith('/films/search/'):
                    self.assertIn('vid=1',path)
                    return '<a href="/films/124">Example Two</a>'
                return HTML.replace('Example','Example Two').replace('/123','/124')
            with patch('anwap.html_page',side_effect=page): search.run('example')
            self.assertEqual(cat.home()['shelves'][-1]['results'],before)
            self.assertEqual(len(cat.search('example')['results']),2)
    def test_parser_filters_sample_and_external(self):
        row=parse_movie(HTML,123)
        self.assertEqual(row['raw'],'Example (2020)');self.assertEqual([f['id'] for f in row['formats']],[2,3])
        self.assertEqual(row['metadata']['provider'],'Anwap')
        self.assertEqual(film_ids('<a href="/films/123">X</a><a href="/films/123">Y</a><a href="/films/top">No</a>'),[123])
    def test_allowlist(self):
        self.assertTrue(allowed_media('https://z37.anwap.be/on/a.mp4'))
        for url in ('http://z37.anwap.be/a','https://z37.anwap.be.evil/a','https://127.0.0.1/a','https://user@z37.anwap.be/a'):
            self.assertFalse(allowed_media(url))
    def test_resolve_refresh_and_stable_identity(self):
        def run(signature,size='1000'):
            def handle(req):
                if req.url.host=='mm.anwap.media':return httpx.Response(302,headers={'Location':'https://z37.anwap.be/on/'+signature+'/movie.mp4'})
                self.assertEqual(req.headers['range'],'bytes=0-0')
                return httpx.Response(206,headers={'Content-Type':'video/mp4','Content-Range':'bytes 0-0/'+size},content=b'x')
            with patch('anwap.html_page',return_value=HTML),patch('anwap.httpx.Client',return_value=httpx.Client(transport=httpx.MockTransport(handle))):return resolve(123,3)
        a=run('old');b=run('new');c=run('new','2000')
        self.assertEqual(a['file_key'],b['file_key']);self.assertNotEqual(b['file_key'],c['file_key'])
    def test_reject_html_and_non_range(self):
        with patch('anwap.html_page',return_value=HTML),patch('anwap.httpx.Client',return_value=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,headers={'Content-Type':'text/html'},content=b'login')))):
            with self.assertRaises(ValueError):resolve(123,3)
