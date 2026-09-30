"""Initial RAM-only TorrServer settings; subsequent starts preserve changes."""
import json
import urllib.request
from settings import torrserver_url


def configure(data):
    marker = data/'torrserver-setup-v1'
    if marker.exists(): return
    def request(body):
        req = urllib.request.Request(torrserver_url()+'/settings', data=json.dumps(body).encode(),
                                     headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    current = request({'action':'get'})
    if not isinstance(current, dict): raise RuntimeError('Unexpected TorrServer settings')
    current.update(CacheSize=64*1024*1024, UseDisk=False)
    request({'action':'set','sets':current})
    actual = request({'action':'get'})
    if actual.get('UseDisk') is not False or actual.get('CacheSize') != 64*1024*1024:
        raise RuntimeError('TorrServer RAM configuration not confirmed')
    marker.write_text('RAM cache configured\n')
    marker.chmod(0o600)
