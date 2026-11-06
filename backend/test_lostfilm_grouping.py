"""Real LostFilm additional release titles must share a series card, retaining all old state."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from catalog import Catalog, lostfilm_slug
from server import create_app
from test_catalog import release

SLUG='It_Welcome_to_Derry'
URL='https://www.lostfilm.tv/series/'+SLUG+'/'
TITLE='It: Welcome to Derry'


def special(rid, quality='', slug=SLUG):
    row=release(rid,TITLE+' - A Story of Derry - rus '+quality+' WEBDL (LostFilm)',
                source='lostfilm',cats=[5000])
    row['details']='https://www.lostfilm.tv/series/'+slug+'/additional/episode_1/'
    return row


class LostFilmGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(Path(self.temp.name),'test')
        self.client=TestClient(self.app,headers={'Authorization':'Bearer test'})
        self.cat=self.app.state.catalog
        self.normal=release('season',TITLE+' - S1 - rus WEBDL (LostFilm)',source='lostfilm',cats=[5000])
        self.normal['details']=URL+'seasons'
    def tearDown(self):self.temp.cleanup()
    def ingest(self,normal=True):
        if normal:self.cat.ingest('lostfilm',[self.normal],update_shelf=False)
        self.cat.ingest('lostfilm',[special('sd'),special('fullhd','1080p'),special('hd','720p')])
        with self.cat.db() as db:
            self.old_ids=[r[0] for r in db.execute("SELECT content_id FROM catalog_releases WHERE id!='season' ORDER BY id")]
    def home(self):return self.client.get('/api/v1/catalog/home').json()['shelves'][0]['results']
    def test_real_three_qualities_are_one_series_card_and_all_releases_remain(self):
        self.ingest()
        cards=self.home();self.assertEqual(len(cards),1)
        card=cards[0];self.assertEqual(card['title'],TITLE);self.assertEqual(card['release_count'],3)
        self.assertEqual(card['source_slug'],SLUG)
        detail=self.cat.detail(card['id'])
        self.assertEqual({r['id'] for r in detail['releases']},{'season','sd','hd','fullhd'})
        self.assertEqual(self.cat.search('A Story of Derry')['total'],1)
        with self.cat.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(DISTINCT content_id) FROM catalog_releases WHERE id!='season'").fetchone()[0],3)
    def test_every_old_card_link_resolves_and_keeps_favorite_watch_later_and_resume(self):
        self.ingest();canonical=self.home()[0]['id']
        old=self.old_ids[0]
        self.assertEqual(self.client.post('/api/v1/library/'+old,json={'favorite':True,'watch_later':True}).status_code,200)
        with self.cat.db() as db:
            rid=db.execute('SELECT id FROM catalog_releases WHERE content_id=?',(old,)).fetchone()[0]
            db.execute('INSERT INTO prepared_releases VALUES (?,?,?,?,?,?)',(rid,'ready','a'*40,json.dumps([dict(id=1,path='A Story of Derry.mkv',sample=False)]),time.time(),None))
        started=self.client.post('/api/v1/playback/file-sessions',json={'release_id':rid,'file_id':1}).json()
        import uuid
        self.client.post('/api/v1/playback/sessions/'+started['id']+'/result',json=dict(
            event_id=str(uuid.uuid4()),result_ok=True,position_ms=50000,duration_ms=2000000,end_by='user'))
        for cid in self.old_ids:
            detail=self.client.get('/api/v1/catalog/items/'+cid).json()
            self.assertEqual(detail['id'],canonical);self.assertTrue(detail['library']['favorite'])
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertEqual(rows[0]['results'][0]['resume_target']['position_ms'],50000)
        self.assertEqual(rows[2]['results'][0]['id'],canonical)
        resumed=self.client.post('/api/v1/playback/file-sessions',json={'release_id':rid,'file_id':1,'resume':True}).json()
        self.assertEqual(resumed['start_position_ms'],50000)
    def test_preexisting_personal_flags_on_two_old_ids_are_merged_and_can_be_cleared(self):
        self.ingest();a,b=self.old_ids[:2]
        with self.cat.db() as db:
            db.execute('INSERT INTO library_flags VALUES (?,?,?,?,?,?)',('main',a,1,0,0,1))
            db.execute('INSERT INTO library_flags VALUES (?,?,?,?,?,?)',('main',b,0,1,0,2))
        cid=self.home()[0]['id'];detail=self.client.get('/api/v1/catalog/items/'+cid).json()
        self.assertEqual(detail['library'],dict(favorite=True,watch_later=True,watched=False))
        response=self.client.post('/api/v1/library/'+a,json={'favorite':False})
        self.assertFalse(response.json()['favorite']);self.assertTrue(response.json()['watch_later'])
    def test_source_url_fallback_is_stable_and_rejoins_numbered_series_later(self):
        self.ingest(normal=False);first=self.home()[0]['id']
        self.assertEqual(len(self.home()),1)
        self.client.post('/api/v1/library/'+first,json={'favorite':True})
        self.cat.ingest('lostfilm',[self.normal],update_shelf=False)
        current=self.home()[0]['id'];self.assertNotEqual(first,current)
        self.assertEqual(self.cat.detail(first)['id'],current)
        self.assertTrue(self.client.get('/api/v1/catalog/items/'+current).json()['library']['favorite'])
    def test_same_title_without_verified_same_source_is_not_merged(self):
        a=special('one');b=special('two');b['details']=URL.replace('www.lostfilm.tv','www.lostfilm.tv.evil.test')
        self.cat.ingest('lostfilm',[a,b]);self.assertEqual(len(self.home()),2)
        self.cat.ingest('lostfilm',[special('three',slug='Different_Remake'),special('four')])
        self.assertEqual(len(self.home()),2)
    def test_source_identity_rejects_credentials_bad_ports_and_other_hosts(self):
        for value in ['ftp://www.lostfilm.tv/series/It_Welcome_to_Derry/',URL.replace('www.','user:pass@www.'),
                      URL.replace('www.lostfilm.tv','www.lostfilm.tv:bad'),URL.replace('www.lostfilm.tv','evil.test')]:
            self.assertIsNone(lostfilm_slug(value))
    def test_merged_card_reuses_existing_verified_series_poster(self):
        self.ingest();cid=self.home()[0]['id']
        cover='https://www.lostfilm.tv/Static/Images/686/Posters/poster.jpg'
        with self.cat.db() as db:
            metadata=dict(provider='LostFilm',title='Derry',url=URL,poster=cover)
            db.execute('INSERT INTO catalog_provider_items VALUES (?,?)',(cid,json.dumps(dict(self.cat.detail(cid),metadata=metadata))))
            db.execute('INSERT INTO image_assets VALUES (?,?,?,?,?)',(cover,200,300,'a'*20,1))
        card=self.home()[0]
        self.assertIsNotNone(card['metadata']['poster']);self.assertIn('/images/'+cid,card['metadata']['poster'])
    def test_old_provider_cache_does_not_reintroduce_duplicates_or_background_jobs(self):
        self.ingest();cid=self.home()[0]['id']
        with self.cat.db() as db:
            for old in self.old_ids:
                db.execute('INSERT INTO catalog_provider_items VALUES (?,?)',(old,json.dumps(dict(self.cat.detail(cid),id=old))))
        self.assertEqual(self.cat.search('Derry')['total'],1)
        from metadata import Metadata
        jobs=Metadata(self.cat).background_cards()
        self.assertEqual(len([c for c in jobs if 'Derry' in c['title']]),1)
