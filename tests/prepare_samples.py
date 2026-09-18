"""Build a checksum-pinned real-game test corpus; independent of expiring artifacts.

Pass --source to use an already downloaded source-probe directory without network.
A cached tests/fixtures.json.gz also works offline. Fixtures are not a full corpus.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from luckyj.parser import TARGET
from luckyj.store import build

SAMPLES = {
 '2023041911gm-0001-0000-cba4cded': ('24e75e5663c2eb0a939b8cb3c91b54fd53853d99498beaa02b683c89eb1d8f34',1681869780),
 '2023041911gm-0009-0000-24ce7cb7': ('d63796666bd30e23a45da11403544a45ed77ae7ed0e196b67dead4b904bdda95',1681870620),
 '2023041911gm-0009-0000-76965bea': ('0bc4b4cd21cc39dcac416e90370446d2cc9532adb0a9ffe9fb63d19beafe59ef',1681872180),
}

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--source',type=Path)
 p.add_argument('--data',type=Path,default=Path('test-data'))
 a=p.parse_args()
 fixture=Path(__file__).with_name('fixtures.json.gz')
 cached=json.loads(gzip.decompress(fixture.read_bytes())) if fixture.exists() else {}
 xml=cached.get('xml',{})
 manifest={'name':TARGET,'test_subset':True,'list':[]}
 for log_id,(sha,started_at) in SAMPLES.items():
  if log_id in xml: raw=xml[log_id].encode()
  elif a.source: raw=(a.source/(log_id+'.xml')).read_bytes()
  else:
   time.sleep(1)
   req=urllib.request.Request('https://tenhou.net/0/log/?'+log_id,headers={'User-Agent':'LuckyJ-Archive/0.1 regression-test'})
   with urllib.request.urlopen(req,timeout=45) as response:raw=response.read(8_000_001)
  if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Pinned sample SHA-256 mismatch: '+log_id)
  xml[log_id]=raw.decode()
  path=a.data/'raw'/log_id[:4]/(log_id+'.xml.gz');path.parent.mkdir(parents=True,exist_ok=True)
  path.write_bytes(gzip.compress(raw,mtime=0))
  manifest['list'].append({'url':'https://tenhou.net/3/?log='+log_id,'starttime':started_at})
 (a.data/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf8')
 fixture.write_bytes(gzip.compress(json.dumps({'purpose':'Pinned three-game test subset, not the full corpus','xml':xml},ensure_ascii=False).encode(),mtime=0))
 report=build(a.data)
 assert (report['indexed_logs'],report['rounds'],report['decisions'],report['index_error_count'])==(3,26,293,0),report

if __name__=='__main__':main()
