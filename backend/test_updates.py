import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.release=self.root/'releases';self.release.mkdir()
        self.env=patch.dict(os.environ,{'CINEMA_RELEASES':str(self.release),'CINEMA_PUBLIC_URL':'http://192.168.1.144:8093'})
        self.env.start()
        self.client=TestClient(create_app(self.root/'data','test-token'))
        self.apk=b'verified-apk-fixture'
        self.sha=hashlib.sha256(self.apk).hexdigest()
        self.name=f'home-cinema-20-{self.sha}.apk'
        self.info=dict(versionCode=20,versionName='0.9.8',changes='OTA',size=len(self.apk),sha256=self.sha,minSdk=23)
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def publish(self):
        (self.release/self.name).write_bytes(self.apk)
        (self.release/'latest.json').write_text(json.dumps(self.info))
    def test_public_manifest_matches_immutable_apk_and_supports_range(self):
        self.publish()
        response=self.client.get('/api/v1/app-update')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        info=response.json()
        self.assertEqual(info['sha256'],self.sha)
        self.assertEqual(info['apkUrl'],'http://192.168.1.144:8093/downloads/'+self.name)
        result=self.client.get('/downloads/'+self.name,headers={'Range':'bytes=1-4'})
        self.assertEqual(result.status_code,206)
        self.assertEqual(result.content,self.apk[1:5])
        self.assertEqual(self.client.head('/downloads/'+self.name).headers['content-length'],str(len(self.apk)))
    def test_no_incomplete_or_corrupted_release_is_advertised(self):
        self.assertEqual(self.client.get('/api/v1/app-update').status_code,503)
        self.publish();(self.release/self.name).write_bytes(b'x'*len(self.apk))
        self.assertEqual(self.client.get('/api/v1/app-update').status_code,503)
    def test_arbitrary_files_not_exposed(self):
        for name in ['latest.json','device-token','evil.apk','home-cinema-20-bad.apk']:
            self.assertEqual(self.client.get('/downloads/'+name).status_code,404)
    def test_old_immutable_apk_remains_available_after_new_manifest(self):
        self.publish()
        self.info['versionCode']=21
        next_name=f'home-cinema-21-{self.sha}.apk'
        (self.release/next_name).write_bytes(self.apk)
        (self.release/'latest.json').write_text(json.dumps(self.info))
        self.assertEqual(self.client.get('/api/v1/app-update').json()['versionCode'],21)
        self.assertEqual(self.client.get('/downloads/'+self.name).content,self.apk)
