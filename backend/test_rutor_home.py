import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import Catalog
from test_catalog import release


class RutorHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cat=Catalog(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def row(self,rid,tid,title,category='kino',seeders=10):
        return dict(release(rid,title,'rutor',kind='movie'),
            details='https://rutor.info/torrent/'+str(tid)+'/film',
            download_url='magnet:?xt=urn:btih:'+'a'*40,
            home_category=category,home_rank=0,seeders=seeders)

    def shelf(self):
        return next(s for s in self.cat.home()['shelves'] if s['id']=='rutor')

    def test_two_top_categories_group_qualities_keep_remakes_and_all_releases(self):
        rows=[self.row('a',1,'Нечто / The Thing (1982) BDRip',seeders=100),
              self.row('b',2,'Нечто / The Thing (1982) WEB-DL 1080p',seeders=200),
              self.row('c',3,'Нечто / The Thing (2011) BDRip',seeders=20),
              self.row('d',4,'Наш фильм (2026) WEB-DL','nashe_kino',50)]
        with patch('rutor_top.fetch_top',return_value=rows):self.cat.refresh('rutor')
        shelf=self.shelf();cards=shelf['results']
        self.assertEqual(len(cards),3)
        self.assertEqual([(c['title'],c['year']) for c in cards],[('Нечто',1982),('Наш фильм',2026),('Нечто',2011)])
        self.assertEqual(shelf['scope'],'category_top')
        self.assertEqual(shelf['received'],4)
        self.assertIn('Зарубежные фильмы',shelf['description'])
        self.assertEqual({r['id'] for r in self.cat.detail(cards[0]['id'])['releases']},{'a','b'})
        self.assertEqual([s['id'] for s in self.cat.home()['shelves']],['lostfilm','rutor','exkinoray','anwap'])
        self.assertNotIn('magnet:',json.dumps(shelf))

    def test_existing_release_ids_and_playback_links_survive_source_switch(self):
        old=self.row('jackett-id',123,'Фильм (2026) WEB-DL')
        old.pop('home_category');old.pop('home_rank')
        self.cat.ingest('rutor',[old])
        cid=self.shelf()['results'][0]['id']
        direct=self.row('direct-id',123,'Фильм (2026) WEB-DL')
        direct['details']='https://rutor.is/torrent/123/updated-slug'
        self.cat.ingest('rutor',[direct])
        with self.cat.db() as db:
            stored=[json.loads(r[0]) for r in db.execute("SELECT payload FROM catalog_releases WHERE source='rutor'")]
        self.assertEqual([r['id'] for r in stored],['jackett-id'])
        self.assertEqual(self.shelf()['results'][0]['id'],cid)
        self.assertEqual(self.cat.detail(cid)['releases'][0]['id'],'jackett-id')
        self.assertEqual(stored[0]['download_url'],direct['download_url'])

    def test_background_search_preserves_top_membership_and_does_not_add_search_results(self):
        row=self.row('top',1,'Фильм (2026) WEB-DL')
        self.cat.ingest('rutor',[row])
        update=dict(row,id='jackett-new-id');update.pop('home_category');update.pop('home_rank')
        unrelated=self.row('search',2,'Другой фильм (2026) WEB-DL')
        self.cat.ingest('rutor',[update,unrelated],update_shelf=False)
        self.assertEqual([c['title'] for c in self.shelf()['results']],['Фильм'])
        self.assertEqual(self.shelf()['scope'],'category_top')
        self.assertEqual(self.cat.detail(self.shelf()['results'][0]['id'])['release_count'],1)

    def test_failed_or_incomplete_top_retains_last_successful_shelf(self):
        self.cat.ingest('rutor',[self.row('a',1,'Фильм (2026) WEB-DL')])
        before=self.shelf()['results']
        with patch('rutor_top.fetch_top',side_effect=ValueError('Incomplete top')):self.cat.refresh('rutor')
        after=self.shelf()
        self.assertEqual(after['results'],before)
        self.assertTrue(after['stale'])
        self.assertEqual(after['scope'],'category_top')

    def test_unmigrated_snapshot_keeps_honest_description_until_top_fetch_succeeds(self):
        row=self.row('old',1,'Фильм (2026) WEB-DL');row.pop('home_category')
        self.cat.ingest('rutor',[row])
        self.assertEqual(self.shelf()['scope'],'popular_in_latest_100')
        self.assertIn('последних 100',self.shelf()['description'])


if __name__=='__main__':unittest.main()
