"""Read-only feed inspection, with no credential/download URL output."""
import concurrent.futures
from pathlib import Path
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

key = Path('/root/home-cinema/data/jackett-key').read_text().strip()
def inspect(slug):
    params = urllib.parse.urlencode({'apikey':key,'t':'search','limit':100})
    url = 'http://192.168.1.144:8091/api/v2.0/indexers/'+slug+'/results/torznab/api?'+params
    with urllib.request.urlopen(url, timeout=35) as r:
        root = ET.fromstring(r.read(6_000_001))
    rows = root.findall('./channel/item')
    return {'source':slug,'count':len(rows),'examples':[{'title':r.findtext('title'), 'categories':[x.get('value') for x in r if x.get('name')=='category'], 'attribute_names':[x.get('name') for x in r if x.tag.endswith('attr')]} for r in rows[:3]]}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    for result in pool.map(inspect, ['lostfilm','exkinoray','rutor']):
        print(json.dumps(result, ensure_ascii=False),flush=True)
