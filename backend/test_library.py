import unittest
import tempfile
import json
from pathlib import Path
from fastapi.testclient import TestClient
from server import create_app
import test_torrents

class LibraryTests(unittest.TestCase):
    setUp=test_torrents.FilePlaybackTests.setUp
    tearDown=test_torrents.FilePlaybackTests.tearDown
    result=test_torrents.FilePlaybackTests.result
    progress=test_torrents.FilePlaybackTests.progress
    file_start=test_torrents.FilePlaybackTests.file_start
    def test_flags_independent_reversible_persistent(self):
        cid=self.cat.search()['results'][0]['id']
        path='/api/v1/library/'+cid
        self.assertEqual(self.client.post(path,json={'favorite':True,'watch_later':True}).status_code,200)
        self.assertEqual(self.client.post(path,json={'watched':True}).json(),dict(favorite=True,watch_later=True,watched=True))
        self.client.post(path,json={'watched':False})
        other=TestClient(create_app(Path(self.temp.name),'test-token'))
        r=other.get('/api/v1/catalog/items/'+cid,headers={'Authorization':'Bearer test-token'}).json()
        self.assertTrue(r['library']['favorite']);self.assertFalse(r['library']['watched'])
        self.assertIsNone(self.progress())
    def test_launch_not_history_or_watched_and_completion_separate(self):
        session=self.file_start().json()
        shelves=self.client.get('/api/v1/library').json()['shelves']
        self.assertFalse(shelves[0]['results']);self.assertFalse(shelves[1]['results'])
        self.result(session['id'],position_ms=12345)
        shelves=self.client.get('/api/v1/library').json()['shelves']
        self.assertEqual(shelves[0]['results'][0]['resume_target']['file_id'],1)
        self.assertEqual(shelves[0]['results'][0]['resume_target']['position_ms'],12345)
        self.result(self.file_start().json()['id'],end_by='playback_completion')
        shelves=self.client.get('/api/v1/library').json()['shelves']
        self.assertFalse(shelves[0]['results']);self.assertTrue(shelves[1]['results']);self.assertFalse(shelves[4]['results'])
    def test_flags_validate_auth_and_unknown_content(self):
        cid=self.cat.search()['results'][0]['id'];p='/api/v1/library/'+cid
        for value in ({},{'watched':None},{'favorite':'yes'},{'file':'secret'}):
            self.assertEqual(self.client.post(p,json=value).status_code,422)
        self.assertEqual(self.client.post('/api/v1/library/missing',json={'watched':True}).status_code,404)
        self.assertEqual(self.client.get('/api/v1/library',headers={'Authorization':''}).status_code,401)

    def test_watched_hides_continue_without_erasing_history(self):
        s=self.file_start().json();self.result(s['id'],position_ms=45000)
        cid=s['content_id'];p='/api/v1/library/'+cid
        self.client.post(p,json={'watched':True,'favorite':True,'watch_later':True})
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertEqual([len(r['results']) for r in rows],[0,1,1,1,1])
        self.client.post(p,json={'watched':False})
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertEqual([len(r['results']) for r in rows],[1,1,1,1,0])
        self.assertEqual(rows[0]['results'][0]['resume_target']['position_ms'],45000)

    def test_completed_latest_file_does_not_offer_older_file(self):
        self.result(self.file_start(fid=1).json()['id'],position_ms=45000)
        self.result(self.file_start(fid=2).json()['id'],end_by='playback_completion')
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertFalse(rows[0]['results'])
        self.assertEqual(rows[1]['results'][0]['resume_target']['file_id'],2)
