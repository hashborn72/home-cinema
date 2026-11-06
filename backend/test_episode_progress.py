import json
import time
import unittest
from fastapi.testclient import TestClient
from pathlib import Path
from server import create_app
import test_server
from test_catalog import release
from episode_progress import episode_identity


class EpisodeProgressTests(unittest.TestCase):
    def setUp(self):
        test_server.PlaybackTests.setUp(self)
        self.cat=self.client.app.state.catalog
        self.cat.ingest('lostfilm',[release('hd','Dark Matter S01E01 (2024) 1080p',source='lostfilm',cats=[5000]),release('sd','Dark Matter S01E01 (2024) 720p',source='lostfilm',cats=[5000])])
        with self.cat.db() as db:
            for rid,h in [('hd','a'*40),('sd','b'*40)]:
                db.execute('INSERT INTO prepared_releases VALUES (?,?,?,?,?,?)',(rid,'ready',h,json.dumps([
                    dict(id=1,path='Dark.Matter.S01E02.'+rid+'.mkv',sample=False),
                    dict(id=2,path='Dark.Matter.S01E03.'+rid+'.mkv',sample=False),
                    dict(id=3,path='Dark.Matter.S02E02.'+rid+'.mkv',sample=False)]),time.time(),None))
    tearDown=test_server.PlaybackTests.tearDown
    result=test_server.PlaybackTests.result
    def start(self,rid='hd',fid=1,resume=False):
        return self.client.post('/api/v1/playback/file-sessions',json=dict(release_id=rid,file_id=fid,resume=resume)).json()
    def files(self,rid='sd'):
        return self.client.get('/api/v1/releases/'+rid+'/files').json()['files']
    def test_partial_progress_and_duration_visible_in_all_qualities_and_resume(self):
        self.result(self.start()['id'],position_ms=1200000,duration_ms=2400000)
        for rid in ['hd','sd']:
            row=self.files(rid)[0]
            self.assertEqual(row['position_ms'],1200000)
            self.assertEqual(row['duration_ms'],2400000)
            self.assertFalse(row['completed'])
        self.assertEqual(self.start('sd',resume=True)['start_position_ms'],1200000)
        self.assertIsNone(self.files()[1]['position_ms'])
        self.assertIsNone(self.files()[2]['position_ms'])
    def test_credits_completion_applies_to_all_qualities(self):
        self.result(self.start()['id'],position_ms=2200000,duration_ms=2400000,end_by='user')
        self.assertTrue(self.files()[0]['completed'])
        self.assertEqual(self.start('sd',resume=True)['start_position_ms'],0)
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertFalse(rows[0]['results']);self.assertTrue(rows[1]['results'])
    def test_delayed_result_from_old_quality_cannot_replace_newer_episode_progress(self):
        old=self.start();new=self.start('sd')
        self.result(new['id'],position_ms=1200000,duration_ms=2400000)
        self.result(old['id'],position_ms=500000,duration_ms=2400000)
        self.assertEqual(self.files('hd')[0]['position_ms'],1200000)
    def test_late_result_from_old_quality_does_not_reopen_completed_history(self):
        old=self.start();new=self.start('sd')
        self.result(new['id'],position_ms=2200000,duration_ms=2400000)
        self.result(old['id'],position_ms=500000,duration_ms=2400000)
        rows=self.client.get('/api/v1/library').json()['shelves']
        self.assertFalse(rows[0]['results'])
        self.assertEqual(rows[1]['results'][0]['resume_target']['position_ms'],2200000)

    def test_existing_history_backfilled_without_erasing_file_records(self):
        session=self.start()
        self.result(session['id'],position_ms=1200000,duration_ms=2400000)
        with self.cat.db() as db: db.execute('DELETE FROM episode_targets')
        other=TestClient(create_app(Path(self.temp.name),'test-token'))
        response=other.get('/api/v1/releases/sd/files',headers={'Authorization':'Bearer test-token'})
        self.assertEqual(response.json()['files'][0]['position_ms'],1200000)
        with self.cat.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM progress').fetchone()[0],1)
    def test_ambiguous_ranges_samples_and_movies_stay_file_scoped(self):
        r={'content':{'id':'show','media_type':'tv'}}
        self.assertEqual(episode_identity(r,{'path':'Show.S01E02.1080p.mkv'}),('show',1,2))
        for f in [dict(path='Show.S01E02-E03.mkv'),dict(path='Show.S01E02E03.mkv'),dict(path='Show.S01E02.E03.mkv'),dict(path='Show.S01E02.mkv',sample=True),dict(path='02.mkv')]:
            self.assertIsNone(episode_identity(r,f))
        self.assertIsNone(episode_identity({'content':{'id':'movie','media_type':'movie'}},{'path':'S01E02.mkv'}))
