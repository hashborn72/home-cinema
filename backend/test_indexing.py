import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from catalog import Catalog
from metadata import Metadata
from providers import Providers
from indexing import Indexer
from test_catalog import release
from anwap_series import series_card,episode_formats,expand_series


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cat=Catalog(Path(self.temp.name))
        self.meta=Metadata(self.cat);self.p=Providers(self.cat);self.idx=Indexer(self.cat,self.p,self.meta)
    def tearDown(self):
        self.p.pool.shutdown(wait=True);self.temp.cleanup()
    def test_background_preserves_interactive_capacity_and_ttl(self):
        self.p.active.add('interactive')
        with patch.object(self.p,'run') as run:self.idx.tick();run.assert_not_called()
        self.p.active.clear()
        with patch('providers.lostfilm_directory',return_value=([],False)) as fetch:
            self.idx.tick();fetch.assert_called_once()
            with patch.object(self.p,'jackett',return_value=[]):self.idx.tick()
            fetch.assert_called_once()
        self.assertEqual(self.p.state('lostfilm')['status'],'ready')
    def test_stale_success_is_returned_during_refresh_and_preserved_on_failure(self):
        key=self.p.key('rutor','',0,'')
        card={'id':'one'}
        with self.cat.db() as db:db.execute('INSERT INTO provider_jobs VALUES (?,?,?,?)',(key,'ready',json.dumps({'results':[card]}),time.time()-900))
        self.p.active.add(key)
        state=self.p.start('rutor');self.assertEqual(state['status'],'ready');self.assertTrue(state['refreshing'])
        with patch.object(self.p,'jackett',side_effect=RuntimeError('secret')):self.p.run(key,'rutor','',0,'')
        state=self.p.state('rutor');self.assertTrue(state['stale']);self.assertFalse(state['refreshing'])
        self.assertEqual(state['results'],[card]);self.assertNotIn('secret',json.dumps(state))
    def test_new_background_job_is_loading_without_database_row(self):
        self.p.active.add(self.p.key('lostfilm','',0,''))
        self.assertEqual(self.p.state('lostfilm')['status'],'loading')
    def test_directory_metadata_is_used_for_home_without_guessing_a_remake(self):
        self.cat.ingest('lostfilm',[release('dm','Dark Matter S01E01',source='lostfilm',cats=[5000])])
        card=self.cat.search()['results'][0]
        entry=dict(card,metadata={'provider':'LostFilm','poster':'https://www.lostfilm.tv/Static/Images/1/Posters/image.jpg'})
        with self.cat.db() as db:db.execute('INSERT INTO catalog_provider_items VALUES (?,?)',(card['id'],json.dumps(entry)))
        out=self.meta.enrich([card])[0]
        self.assertEqual(out['metadata']['provider'],'LostFilm');self.assertIn('/images/',out['metadata']['poster'])
    def test_priority_bounded_and_interests_persist(self):
        cards=[{'id':str(i),'media_type':'tv'} for i in range(500)]
        for offset in range(0,500,40):self.idx.touch(cards[offset:offset+40])
        self.assertLessEqual(len(self.meta.priority),200)
        with self.cat.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM index_interests').fetchone()[0],120)
    def test_cached_aliases_search_both_names(self):
        card={'id':'decker','title':'Ар-Джей Декер','aliases':['RJ Decker'],'year':2026,'media_type':'tv','published_at':0}
        with self.cat.db() as db:db.execute('INSERT INTO catalog_provider_items VALUES (?,?)',(card['id'],json.dumps(card)))
        self.assertIn('RJ Decker',self.p.query_names('Ар-Джей Декер'))
        self.assertIn('Ар-Джей Декер',self.p.query_names('RJ Decker'))
    def test_same_title_expansion_uses_exact_feed_series(self):
        current=dict(release('new','Dark Matter S02E05',source='lostfilm',cats=[5000]),details='https://www.lostfilm.tv/series/Dark_Matter_2024%20/season_2/episode_5/')
        old=dict(release('old','Dark Matter S03',source='lostfilm',cats=[5000]),details='https://www.lostfilm.tv/series/Dark_Matter/seasons')
        self.cat.ingest('lostfilm',[current])
        self.cat.ingest('lostfilm',[old],False)
        cid=self.cat.home()['shelves'][0]['results'][0]['id']
        detail=self.cat.detail(cid)
        self.assertEqual(detail['source_slug'],'Dark_Matter_2024')
        self.assertEqual([r['id'] for r in detail['releases']],['new'])
        key=self.p.key('lostfilm','Dark Matter',0,cid)
        with patch.object(self.p,'jackett',return_value=[current,old]):self.p.run(key,'lostfilm','Dark Matter',0,cid)
        self.assertEqual([r['id'] for r in self.cat.detail(cid)['releases']],['new'])
    def test_native_poster_is_derived_from_exact_series_page(self):
        row=dict(release('a','Dark Matter S02E05',source='lostfilm',cats=[5000]),details='https://www.lostfilm.tv/series/Dark_Matter_2024%20/season_2/episode_5/')
        self.cat.ingest('lostfilm',[row]);card=self.cat.home()['shelves'][0]['results'][0]
        with patch('providers.lostfilm_directory',return_value=([],False)),patch('metadata.fetch',return_value=b'<img src="/Static/Images/842/Posters/poster.jpg">') as fetch:
            self.meta.refresh_native(card)
            fetch.assert_called_once_with('https://www.lostfilm.tv/series/Dark_Matter_2024/',timeout=10,limit=2_000_000)
        self.assertEqual(self.cat.detail(card['id'])['metadata']['poster'],'https://www.lostfilm.tv/Static/Images/842/Posters/poster.jpg')


class AnwapSeriesTests(unittest.TestCase):
    def body(self):
        return '<h1>Ар-Джей Декер</h1><meta property="og:url" content="https://mm.anwap.media/serials/4227"><meta property="og:title" content="Скачать Ар-Джей Декер / RJ Decker на телефон"><a href="/serials/god-2026">2026</a><a href="/serials/s8165">2 Сезон(3)</a>'
    def test_directory_alias_identity_and_episode_ingest(self):
        card=series_card(self.body(),4227)
        self.assertEqual(card['aliases'],['RJ Decker']);self.assertEqual(card['year'],2026)
        with patch('anwap_series.html_page',side_effect=[self.body(),'<a href="/serials/down/101249">Ар-Джей Декер (2 сезон) - 3 серия</a>']):rows=expand_series(card)
        self.assertEqual(rows[0]['film_id'],-101249);self.assertIn('S02E03',rows[0]['raw'])
        with tempfile.TemporaryDirectory() as root:
            cat=Catalog(Path(root));cat.ingest('anwap',rows,False)
            result=cat.detail(card['id']);self.assertEqual(result['media_type'],'tv');self.assertEqual(len(result['releases']),1)
    def test_episode_formats_allow_only_fixed_mp4_paths(self):
        body='<h1>Эпизод</h1><meta property="og:type" content="video.episode"><meta property="og:url" content="https://mm.anwap.media/serials/down/100">'
        body+='<a href="/serials/load/mp4/abc/100">320x240 MP4</a><a href="/serials/load/bmp4/abc/100">710x400 MP4</a><a href="https://evil/serials/load/mp4/abc/100">MP4</a>'
        formats=episode_formats(body,100)['formats']
        self.assertEqual([f['id'] for f in formats],[1,2])
        with self.assertRaises(ValueError):episode_formats(body,101)


if __name__=='__main__':unittest.main()
