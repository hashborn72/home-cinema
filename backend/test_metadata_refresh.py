import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from catalog import Catalog
from metadata import Metadata
from native_metadata import parse_lostfilm, parse_tracker, season_years
from tmdb import TMDB, match
from test_catalog import release


class MetadataRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.cat=Catalog(Path(self.tmp.name));self.meta=Metadata(self.cat)
        self.cat.ingest('lostfilm',[dict(release('dm','Dark Matter S02E05',source='lostfilm',cats=[5000]),details='https://www.lostfilm.tv/series/Dark_Matter_2024%20/season_2/episode_5/')])
        self.card=self.cat.search()['results'][0]
    def tearDown(self):self.tmp.cleanup()
    def test_exact_source_localizes_remake_and_disambiguates_premiere(self):
        body='<h1>Темная материя</h1><meta property="og:description" content="История Джейсона"><meta itemprop="dateCreated" content="2024-05-08"><img src="/Static/Images/842/Posters/poster.jpg">'
        native=parse_lostfilm(body,'https://www.lostfilm.tv/series/Dark_Matter_2024/')
        card=dict(self.card,metadata=native)
        self.assertEqual(match(card,[{'id':1,'name':'Dark Matter','first_air_date':'2015-06-12'},{'id':2,'name':'Dark Matter','first_air_date':'2024-05-07'}])['id'],2)
        self.assertEqual(native['description'],'История Джейсона')
    def test_tracker_external_id_and_description(self):
        meta=parse_tracker('<b>Описание</b>: Семья в долине.<br><br><b>Качество</b>: HD <a href="http://www.imdb.com/title/tt21097264/">IMDb</a>',dict(self.card,title='К востоку от Эдема'),'https://rutor.info/torrent/1')
        self.assertEqual(meta['imdb_id'],'tt21097264');self.assertEqual(meta['description'],'Семья в долине.')
    def test_season_years_follow_provider_numbering(self):
        years=season_years('<h2>12 сезон</h2>Скоро<h2>11 сезон</h2>Год: 2026<div><h2>10 сезон</h2>Год: 2013<div>')
        self.assertEqual(years,{'11':'2026','10':'2013'})
    def test_native_russian_text_fills_tmdb_gaps(self):
        meta=self.meta.choose({'title':'Неведомая Япония','description':'Природа Японии','season_years':{'1':'2020'}},{'provider':'TMDB','title':'Hidden Japan','description':'','poster':'x'})
        self.assertEqual(meta['title'],'Неведомая Япония');self.assertEqual(meta['description'],'Природа Японии');self.assertEqual(meta['poster'],'x')
    def test_repeated_views_do_not_refetch_successful_metadata(self):
        with patch.object(self.meta,'refresh_native') as native,patch.object(self.meta,'refresh') as refresh,patch.object(self.meta,'warm_artwork'):
            self.meta.process(self.card);self.meta.process(self.card)
            self.assertEqual(native.call_count,1);self.assertEqual(refresh.call_count,1)
    def test_one_card_failure_does_not_block_another(self):
        with patch.object(self.meta,'refresh_native'),patch.object(self.meta,'refresh',side_effect=[TimeoutError(),None]) as refresh,patch.object(self.meta,'warm_artwork'):
            self.meta.process(self.card)
            self.cat.ingest('lostfilm',[release('other','Futurama S11E10',source='lostfilm',cats=[5000])])
            self.meta.process(self.cat.search('Futurama')['results'][0])
            self.assertEqual(refresh.call_count,2);self.assertEqual(self.meta.retry_after,0)
    def test_empty_lookup_preserves_saved_metadata(self):
        with self.cat.db() as db:db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(self.card['id'],json.dumps({'title':'Темная материя','description':'Сохранено'}),1))
        with patch.object(self.meta.tmdb,'enabled',return_value=True),patch.object(self.meta.tmdb,'lookup',return_value=None):self.meta.refresh(self.card)
        self.assertEqual(self.meta.enrich([self.card])[0]['metadata']['description'],'Сохранено')
    def test_tv_category_documentary_can_match_exact_movie(self):
        card={'title':'Неведомая Япония','aliases':['Hidden Japan'],'year':2020,'media_type':'tv','season':None}
        api=TMDB(Path(self.tmp.name));api.image_base='https://image.tmdb.org/t/p/w500'
        def request(path,params):
            return {'results':[{'id':820792,'title':'Hidden Japan','release_date':'2020-03-23','poster_path':'/p.jpg'}]} if path=='/search/movie' else {'results':[]}
        with patch.object(api,'request',side_effect=request):meta=api.lookup(card)
        self.assertEqual(meta['provider_id'],820792);self.assertIn('/movie/',meta['url'])
