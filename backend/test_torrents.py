import json
import time
import unittest
import httpx
from unittest.mock import patch
import test_server
from test_catalog import release
from torrents import video_files, jackett_url, valid_magnet
from metadata import match_show

class FilePlaybackTests(unittest.TestCase):
    def setUp(self):
        test_server.PlaybackTests.setUp(self)
        self.app=self.client.app
        self.cat=self.app.state.catalog
        self.cat.ingest('exkinoray',[release('release-a','Film (2020) WEB-DL'),release('release-b','Film (2020) Remux')])
        for rid,h in [('release-a','a'*40),('release-b','b'*40)]:
            with self.cat.db() as db:
                db.execute('INSERT INTO prepared_releases VALUES (?,?,?,?,?,?)',(rid,'ready',h,json.dumps([{'id':1,'path':'film.mkv','size':1024,'sample':False},{'id':2,'path':'extra.mp4','size':50,'sample':True}]),time.time(),None))

    tearDown=test_server.PlaybackTests.tearDown
    result=test_server.PlaybackTests.result
    progress=test_server.PlaybackTests.progress

    def test_real_http_contract_without_network(self):
        calls=[]
        def handle(req):
            calls.append(req)
            if req.method=='GET': return httpx.Response(200,content=b'd4:infodee')
            return httpx.Response(200,json={'hash':'c'*40,'file_stats':[{'id':1,'path':'movie.mkv'}]})
        client=httpx.Client(transport=httpx.MockTransport(handle))
        (self.cat.data_dir/'jackett-key').write_text('PRIVATE-KEY')
        row=release('x','Film (2020)')
        row['download_url']='http://192.168.1.144:8091/dl/exkinoray/?path=opaque'
        with patch('torrents.httpx.Client',return_value=client): self.app.state.torrents.add(row)
        self.assertIn('PRIVATE-KEY',str(calls[0].url))
        self.assertEqual(calls[1].url.path,'/torrent/upload')
        self.assertNotIn(b'PRIVATE-KEY',calls[1].content)
        self.assertNotIn('save',calls[1].content.decode())

    def test_redirect_cannot_fetch_another_host(self):
        client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(302,headers={'Location':'http://127.0.0.1/private'})))
        (self.cat.data_dir/'jackett-key').write_text('PRIVATE-KEY')
        row=release('x','Film (2020)');row['download_url']='http://192.168.1.144:8091/dl/exkinoray/'
        with patch('torrents.httpx.Client',return_value=client):
            with self.assertRaises(ValueError): self.app.state.torrents.add(row)

    def file_start(self,rid='release-a',fid=1,resume=False):
        return self.client.post('/api/v1/playback/file-sessions',json={'release_id':rid,'file_id':fid,'resume':resume})

    def test_resume_is_bound_to_exact_file_and_torrent(self):
        s=self.file_start().json()
        self.assertEqual(s['start_position_ms'],0)
        self.result(s['id'],position_ms=50000)
        self.assertEqual(self.file_start(resume=True).json()['start_position_ms'],50000)
        self.assertEqual(self.file_start(fid=2,resume=True).json()['start_position_ms'],0)
        self.assertEqual(self.file_start(rid='release-b',resume=True).json()['start_position_ms'],0)
        self.assertIsNone(self.progress())
        self.assertNotIn('SECRET',json.dumps(s))
        self.assertIn('index=1',s['stream_url'])

    def test_file_selection_validation_and_auth(self):
        self.assertEqual(self.file_start(fid=99).status_code,409)
        self.assertEqual(self.file_start(rid='missing').status_code,404)
        self.assertEqual(self.client.get('/api/v1/releases/release-a/files',headers={'Authorization':''}).status_code,401)
        self.assertEqual(self.client.post('/api/v1/playback/file-sessions',json={'release_id':'release-a','file_id':True}).status_code,422)

    def test_preparation_persistence_and_secret_redaction(self):
        manager=self.app.state.torrents
        with patch.object(manager,'add',return_value={'hash':'c'*40,'file_stats':[{'id':3,'path':'Movie.mkv','length':123},{'id':4,'path':'readme.txt','length':30}]}):
            manager._prepare('release-a')
        self.assertEqual(manager.state('release-a')['files'][0]['id'],3)
        with patch.object(manager,'add',side_effect=ValueError('SECRET URL')):
            manager._prepare('release-a')
        self.assertNotIn('SECRET',json.dumps(manager.state('release-a')))
        self.assertEqual(self.file_start(fid=3).status_code,409)

    def test_complete_does_not_resume_at_end(self):
        s=self.file_start().json()
        self.result(s['id'],end_by='playback_completion')
        self.assertEqual(self.file_start(resume=True).json()['start_position_ms'],0)

class ValidationTests(unittest.TestCase):
    def test_download_allowlist(self):
        for url in ['http://127.0.0.1/admin','http://192.168.1.144:8091/config','https://evil/dl/rutor/']:
            with self.assertRaises(ValueError): jackett_url(url,'rutor','key')
        self.assertIn('jackett_apikey=NEW',jackett_url('http://192.168.1.144:8091/dl/rutor/?jackett_apikey=old','rutor','NEW'))
        with self.assertRaises(ValueError): valid_magnet('https://evil/?xt=anything')
        self.assertNotIn('&',valid_magnet('magnet:?xt=urn:btih:'+('a'*40)+'&ws=http://127.0.0.1'))

    def test_video_filter_and_index(self):
        files=video_files({'hash':'a'*40,'file_stats':[{'id':7,'path':'a.mkv','length':2},{'id':8,'path':'a.exe'},{'id':0,'path':'bad.mp4'},{'id':9,'path':'sample.mp4'}]})
        self.assertEqual([f['id'] for f in files],[7,9])

    def test_metadata_ambiguity(self):
        card={'title':'Dark Matter','aliases':[]}
        a={'show':{'id':1,'name':'Dark Matter','summary':'<p>A &amp; B</p>','image':{'medium':'http://evil/image'}}}
        b={'show':{'id':2,'name':'Dark Matter'}}
        self.assertIsNone(match_show(card,[a,b]))
        good=match_show(card,[a]);self.assertIsNone(good['poster']);self.assertEqual(good['description'],'A & B')
        self.assertIsNone(match_show({'title':'Dark Matter 2'},[a]))
