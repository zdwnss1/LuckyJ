#!/usr/bin/env python3
"""Archive the exact Nodocchi player manifest and every resolvable Tenhou XML.
Standard library only. Requests are globally rate limited; saved files are resumable.
"""
import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
import pathlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

PLAYER = '\u24ddLuckyJ'
LOG_ID = re.compile(r'^\d{10}gm-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{8}$')
UA = 'LuckyJ-Archive/0.1 (+https://github.com/zdwnss1/LuckyJ)'

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def validate_xml(raw):
    if len(raw) > 2_000_000 or b'<!DOCTYPE' in raw.upper():
        raise ValueError('Oversized or unsafe XML')
    root = ET.fromstring(raw)
    if root.tag != 'mjloggm' or root.find('INIT') is None:
        raise ValueError('Not a complete Tenhou game log')
    un = root.find('UN')
    names = [] if un is None else [urllib.parse.unquote(un.get('n'+str(i), '')) for i in range(4)]
    if names.count(PLAYER) != 1:
        raise ValueError('Exact player identity mismatch: ' + repr(names))
    if not any(e.tag in ('AGARI', 'RYUUKYOKU') for e in root):
        raise ValueError('No completed hand in XML')

class Client:
    def __init__(self, interval):
        self.interval, self.lock, self.next_request = interval, threading.Lock(), 0.0
    def get(self, url):
        for attempt in range(5):
            with self.lock:
                time.sleep(max(0, self.next_request - time.monotonic()))
                self.next_request = time.monotonic() + self.interval
            try:
                request = urllib.request.Request(url, headers={'User-Agent': UA})
                with urllib.request.urlopen(request, timeout=45) as r:
                    raw = r.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise ValueError('Response too large')
                    return raw
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                retry = e.headers.get('Retry-After', '')
                if retry.isdigit():
                    delay = max(int(retry), 2 ** (attempt + 1))
                else:
                    delay = 2 ** (attempt + 1)
                time.sleep(delay)
            except (TimeoutError, urllib.error.URLError):
                if attempt == 4:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError('Retry budget exhausted')

def run(out, manifest_path=None, interval=0.5, workers=2):
    out = pathlib.Path(out)
    (out / 'raw').mkdir(parents=True, exist_ok=True)
    client = Client(interval)
    manifest_url = 'https://nodocchi.moe/api/listuser.php?' + urllib.parse.urlencode({'name': PLAYER})
    if manifest_path:
        raw_manifest = pathlib.Path(manifest_path).read_bytes()
        source = json.loads(raw_manifest)
    else:
        for _ in range(8):
            raw_manifest = client.get(manifest_url)
            source = json.loads(raw_manifest)
            if not source.get('retry'):
                break
            time.sleep(max(1, float(source['retry'])))
        else:
            raise RuntimeError('Source manifest is still pending; refusing to claim completion')
    if source.get('name') != PLAYER or not isinstance(source.get('list'), list) or not source['list']:
        raise ValueError('Unexpected/empty source manifest')
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (out / 'manifests').mkdir(exist_ok=True)
    (out / 'manifests' / (stamp + '.json')).write_bytes(raw_manifest)
    (out / 'source-manifest.json').write_bytes(raw_manifest)
    ids, missing, invalid = {}, [], []
    for number, row in enumerate(source['list']):
        if sum(row.get('player'+str(i)) == PLAYER for i in range(1, 5)) != 1:
            invalid.append({'row': number, 'reason': 'player_identity', 'record': row})
            continue
        url = row.get('url')
        if not url:
            missing.append({'row': number, 'reason': 'source_has_no_log_url', 'record': row})
            continue
        log_id = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('log', [''])[0]
        if not LOG_ID.fullmatch(log_id):
            invalid.append({'row': number, 'reason': 'invalid_log_id', 'record': row})
            continue
        ids.setdefault(log_id, []).append(number)
    write_json(out / 'missing-links.json', missing)
    write_json(out / 'invalid-records.json', invalid)
    write_json(out / 'manifest-index.json', ids)
    def fetch(log_id):
        url = 'https://tenhou.net/0/log/?' + log_id
        path = out / 'raw' / (log_id + '.xml.gz')
        try:
            cached = False
            if path.exists():
                try:
                    raw = gzip.decompress(path.read_bytes())
                    validate_xml(raw)
                    cached = True
                except (OSError, ValueError, ET.ParseError):
                    cached = False
            if not cached:
                raw = client.get(url)
                validate_xml(raw)
                temp = path.with_suffix('.tmp')
                temp.write_bytes(gzip.compress(raw, mtime=0))
                temp.replace(path)
            return {'log_id': log_id, 'status': 'ok', 'cached': cached, 'url': url,
                    'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                    'path': str(path.relative_to(out))}
        except Exception as e:
            return {'log_id': log_id, 'status': 'error', 'url': url, 'error': str(e)}
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        with (out / 'downloads.jsonl').open('a', encoding='utf-8') as journal:
            for result in pool.map(fetch, sorted(ids)):
                results.append(result)
                journal.write(json.dumps(result, ensure_ascii=False) + '\n')
                journal.flush()
                if len(results) % 50 == 0 or result['status'] != 'ok':
                    print(len(results), '/', len(ids), json.dumps(result), flush=True)
    errors = [r for r in results if r['status'] != 'ok']
    report = {'player': PLAYER, 'fetched_at': stamp, 'manifest_url': manifest_url,
              'manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
              'source_records': len(source['list']), 'unique_log_ids': len(ids),
              'duplicate_link_rows': sum(len(rows)-1 for rows in ids.values()),
              'missing_link_records': len(missing), 'invalid_records': len(invalid),
              'downloaded': len(results)-len(errors), 'failed': len(errors),
              'all_resolvable_downloaded': bool(ids) and not errors,
              'complete_source_coverage': bool(ids) and not errors and not missing and not invalid,
              'results': results}
    write_json(out / 'download-report.json', report)
    print(json.dumps({k:v for k,v in report.items() if k != 'results'}, ensure_ascii=False), flush=True)
    return 1 if errors or invalid else 0

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='data')
    p.add_argument('--manifest', help='Use a previously saved complete source manifest')
    p.add_argument('--interval', type=float, default=0.5, help='Global minimum seconds between requests')
    p.add_argument('--workers', type=int, default=2)
    a = p.parse_args()
    if a.interval < 0.5 or not 1 <= a.workers <= 2:
        p.error('Use interval >= 0.5 seconds and 1-2 workers')
    raise SystemExit(run(a.out, a.manifest, a.interval, a.workers))
