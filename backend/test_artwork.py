import json
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
from artwork import dimensions, role
from catalog import Catalog, identify
from metadata import Metadata
from test_catalog import release
from tmdb import TMDB, match


def png(width=500,height=750):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x20'*width*3)*height))+chunk(b'IEND',b'')


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cat=Catalog(Path(self.temp.name));self.meta=Metadata(self.cat)
        self.url='https://image.tmdb.org/t/p/w500/portrait.png'
        self.cat.ingest('exkinoray',[release('film','The Matrix (1999) WEB-DL',metadata={'provider':'Native','poster':self.url})])
        self.cid=self.cat.search()['results'][0]['id']
    def tearDown(self):self.temp.cleanup()
    def seed(self,url,body,refresh=False):
        original=httpx.Client
        with patch('metadata.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=body)))):
            return self.meta._poster_url(url,refresh=refresh)
    def card(self):return self.meta.enrich([self.cat.detail(self.cid)])[0]
    def test_png_and_jpeg_dimensions_and_roles(self):
        self.assertEqual(dimensions(png()),(500,750))
        jpeg=b'\xff\xd8\xff\xe0\x00\x04xx\xff\xc2\x00\x0b\x08'+struct.pack('>HH',750,500)+b'\x01\x01\x11\0\xff\xd9'
        self.assertEqual(dimensions(jpeg),(500,750))
        self.assertEqual(role(500,750),'poster')
        self.assertEqual(role(1920,1080),'backdrop')
        self.assertEqual(role(1920,1080,'episode_still'),'episode_still')
        self.assertIsNone(role(500,500));self.assertIsNone(role(4000,200))
        for body in (b'\xff\xd8\xfftest',b'not an image',png(1,1),b'\x89PNG\r\n\x1a\n'):
            with self.assertRaises(ValueError):dimensions(body)
    def test_unverified_images_are_not_exposed_and_enrich_does_no_network(self):
        with patch('metadata.httpx.Client',side_effect=AssertionError('Unexpected network')):
            self.assertIsNone(self.card()['metadata']['poster'])
    def test_portrait_is_versioned_and_wide_image_is_not_a_poster(self):
        self.seed(self.url,png())
        first=self.card()['metadata']
        self.assertIn('v=',first['poster']);self.assertIsNone(first['backdrop'])
        wide='https://image.tmdb.org/t/p/w780/wide.png'
        self.seed(wide,png(800,450))
        with self.cat.db() as db:db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(self.cid,json.dumps({'provider':'TMDB','poster':wide}),time.time()))
        result=self.card()['metadata']
        self.assertEqual(result['poster'],first['poster'])
        self.assertIn('kind=backdrop',result['backdrop'])
        self.assertEqual(result['artwork']['backdrop']['width'],800)
        self.assertNotIn('image.tmdb.org',json.dumps(result))
    def test_changed_source_changes_version_and_old_url_never_serves_new_art(self):
        self.seed(self.url,png());first=self.card()['metadata']['poster']
        other='https://image.tmdb.org/t/p/w500/new.png';self.seed(other,png(400,600))
        with self.cat.db() as db:
            db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(self.cid,json.dumps({'provider':'TMDB','poster':other}),time.time()))
        second=self.card()['metadata']['poster'];self.assertNotEqual(first,second)
        version=parse_qs(urlsplit(second).query)['v'][0]
        self.assertTrue(self.meta.poster(self.cid,version=version).is_file())
        with self.assertRaises(KeyError):self.meta.poster(self.cid,version='0'*20)
    def test_same_url_changed_bytes_get_new_version_after_refresh(self):
        self.seed(self.url,png());first=self.card()['metadata']['poster']
        with self.cat.db() as db:db.execute('UPDATE image_assets SET checked_at=0')
        self.seed(self.url,png(400,600),refresh=True)
        self.assertNotEqual(first,self.card()['metadata']['poster'])
    def test_square_art_and_invalid_image_do_not_get_poster_urls(self):
        self.seed(self.url,png(300,300));self.assertIsNone(self.card()['metadata']['poster'])
        with self.assertRaises(ValueError):self.seed('https://image.tmdb.org/t/p/w500/bad.png',b'<html>blocked</html>')
    def test_existing_file_is_classified_without_downloading(self):
        path=self.seed(self.url,png())
        with self.cat.db() as db:db.execute('DELETE FROM image_assets')
        with patch('metadata.httpx.Client',side_effect=AssertionError('Unexpected network')):
            self.assertEqual(self.meta._poster_url(self.url),path)
        self.assertIsNotNone(self.card()['metadata']['poster'])
    def test_old_cached_image_is_served_without_network_on_repeated_views(self):
        path=self.seed(self.url,png());version=self.card()['metadata']['poster']
        with self.cat.db() as db:db.execute('UPDATE image_assets SET checked_at=0')
        with patch('metadata.httpx.Client',side_effect=AssertionError('Unexpected download')):
            self.assertEqual(self.meta.poster(self.cid),path)
            self.assertEqual(self.meta.poster(self.cid),path)
        self.assertEqual(self.card()['metadata']['poster'],version)
    def test_upstream_outage_retains_verified_cached_image_and_version(self):
        path=self.seed(self.url,png());version=self.card()['metadata']['poster']
        with self.cat.db() as db:db.execute('UPDATE image_assets SET checked_at=0')
        with patch('metadata.httpx.Client',side_effect=TimeoutError('unavailable')) as client:
            self.assertEqual(self.meta._poster_url(self.url,refresh=True),path)
            self.assertEqual(self.meta._poster_url(self.url,refresh=True),path)
            self.assertEqual(client.call_count,1)
        self.assertEqual(self.card()['metadata']['poster'],version)
    def test_upgrade_classifies_old_cache_without_network(self):
        self.seed(self.url,png())
        with self.cat.db() as db:db.execute('DELETE FROM image_assets')
        with patch('metadata.httpx.Client',side_effect=AssertionError('Unexpected network')):
            updated=Metadata(self.cat)
            self.assertIsNotNone(updated.enrich([self.cat.detail(self.cid)])[0]['metadata']['poster'])
    def test_native_episode_still_keeps_its_role(self):
        url='https://image.tmdb.org/t/p/w780/still.png';self.seed(url,png(800,450))
        with self.cat.db() as db:
            db.execute('INSERT INTO metadata_cache VALUES (?,?,?)',(self.cid,json.dumps({'provider':'TMDB','episode_still':url}),1))
        meta=self.card()['metadata']
        self.assertIsNone(meta['backdrop']);self.assertIn('kind=episode_still',meta['episode_still'])
    def test_endpoint_rejects_wrong_role_and_invalid_version(self):
        from fastapi.testclient import TestClient
        from server import create_app
        self.seed(self.url,png());client=TestClient(create_app(self.cat.data_dir,'device'))
        image=self.card()['metadata']['poster'];parts=urlsplit(image)
        self.assertEqual(client.get(parts.path+'?'+parts.query).status_code,200)
        self.assertEqual(client.get(parts.path+'?kind=backdrop').status_code,404)
        self.assertEqual(client.get(parts.path+'?v=../../secret').status_code,422)
        self.assertEqual(client.get(parts.path+'?kind=external').status_code,422)
    def test_discovery_not_reset_on_refresh(self):
        _,rows=self.cat.snapshot();first=rows[0]['first_seen_at']
        self.cat.ingest('exkinoray',[release('film','The Matrix (1999) WEB-DL')])
        row=self.cat.snapshot()[1][0]
        self.assertEqual(row['first_seen_at'],first);self.assertGreaterEqual(row['last_checked_at'],first)
        self.assertNotEqual(row['published_at'],first)


class IdentifyLookupTests(unittest.TestCase):
    def test_real_unmatched_card_never_queries_tmdb(self):
        card=identify('Dark Matter WEB-DL','rutor','tv','unknown')
        self.assertEqual(card['match'],'unmatched')
        with tempfile.TemporaryDirectory() as folder:
            api=TMDB(Path(folder))
            with patch.object(api,'request') as request:
                self.assertIsNone(api.lookup(card));request.assert_not_called()
        self.assertIsNone(match(card,[{'id':1,'name':'Dark Matter'}]))
    def test_real_identified_film_matches_and_remake_does_not(self):
        card=identify('Матрица / The Matrix (1999) WEB-DL 1080p','exkinoray','movie','film')
        item={'id':603,'title':'Матрица','original_title':'The Matrix','release_date':'1999-03-30','poster_path':'/p.jpg'}
        with tempfile.TemporaryDirectory() as folder:
            api=TMDB(Path(folder));api.image_base='https://image.tmdb.org/t/p/w500'
            with patch.object(api,'request',return_value={'results':[item],'total_pages':1}):
                self.assertEqual(api.lookup(card)['provider_id'],603)
            self.assertIsNone(match(card,[dict(item,release_date='2026-01-01')]))
    def test_real_collection_is_not_matched_to_one_film(self):
        card=identify('Матрица / The Matrix Collection (1999-2021) BDRip','rutor','movie','collection')
        self.assertEqual(card['match'],'unmatched')
        self.assertIsNone(match(card,[{'id':603,'title':card['title'],'release_date':'1999-01-01'}]))
