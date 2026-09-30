import tempfile
import unittest
import uuid
from pathlib import Path
from fastapi.testclient import TestClient
from server import create_app

class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(Path(self.temp.name), 'test-token'))
        self.client.headers['Authorization'] = 'Bearer test-token'
    def tearDown(self): self.temp.cleanup()
    def start(self): return self.client.post('/api/v1/playback/sessions',json={}).json()['id']
    def result(self, session, **values):
        body = dict(event_id=str(uuid.uuid4()),result_ok=True,position_ms=42170,duration_ms=600000,end_by='user')
        body.update(values)
        return self.client.post('/api/v1/playback/sessions/'+session+'/result',json=body)
    def progress(self): return self.client.get('/api/v1/probe').json()['position_ms']
    def test_auth(self):
        self.assertEqual(self.client.get('/api/v1/probe',headers={'Authorization':''}).status_code,401)
    def test_public_sample_is_fixed_path_and_supports_range(self):
        self.assertEqual(self.client.get('/probe-media').status_code,404)
        Path(self.temp.name, 'BigBuckBunny_320x180.mp4').write_bytes(b'0123456789')
        response = self.client.get('/probe-media',headers={'Authorization':'','Range':'bytes=2-5'})
        self.assertEqual(response.status_code,206)
        self.assertEqual(response.content,b'2345')
        self.assertEqual(response.headers['content-range'],'bytes 2-5/10')
    def test_launch_is_not_progress(self): self.start(); self.assertIsNone(self.progress())
    def test_return_position_survives_restart(self):
        self.assertEqual(self.result(self.start()).status_code,200)
        with TestClient(create_app(Path(self.temp.name),'test-token')) as other:
            self.assertEqual(other.get('/api/v1/probe',headers={'Authorization':'Bearer test-token'}).json()['position_ms'],42170)
    def test_missing_result_preserves_known_position(self):
        self.result(self.start()); self.result(self.start(),result_ok=False,position_ms=None,duration_ms=None,end_by=None)
        self.assertEqual(self.progress(),42170)
    def test_completion_without_position(self):
        self.result(self.start()); self.result(self.start(),position_ms=None,duration_ms=None,end_by='playback_completion')
        self.assertEqual(self.progress(),42170)
        self.assertTrue(self.client.get('/api/v1/probe').json()['completed'])
    def test_idempotency(self):
        session=self.start(); event=str(uuid.uuid4())
        self.result(session,event_id=event)
        self.assertTrue(self.result(session,event_id=event).json()['duplicate'])
        self.assertEqual(self.result(session,event_id=event,position_ms=90000).status_code,409)
    def test_old_session_cannot_overwrite_new(self):
        old,new=self.start(),self.start(); self.result(new,position_ms=95000); self.result(old)
        self.assertEqual(self.progress(),95000)
    def test_reject_invalid_position(self):
        self.assertEqual(self.result(self.start(),position_ms=700000).status_code,422)
        self.assertIsNone(self.progress())

if __name__ == '__main__': unittest.main()
