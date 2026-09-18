"""Import a downloaded raw artifact into this project's canonical layout.

Supports the canonical layout and the earlier phase1-database archive layout.
No network calls. Original provenance is preserved; every imported log is checked.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from download import manifest_ids, validate_xml, write_atomic, TARGET


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--data',type=Path,default=Path('data'))
    a=p.parse_args()
    source,data=a.source,a.data
    if source.resolve()==data.resolve():raise ValueError('Source and output must differ')
    canonical=(source/'manifest.json').exists()
    manifest_path=source/('manifest.json' if canonical else 'source-manifest.json')
    raw_manifest=manifest_path.read_bytes()
    manifest=json.loads(raw_manifest)
    records,unavailable=manifest_ids(manifest)
    report=json.loads((source/'download-report.json').read_bytes())
    sha=hashlib.sha256(raw_manifest).hexdigest()
    if report.get('manifest_sha256')!=sha:raise ValueError('Manifest SHA-256 mismatch')
    expected=report.get('logs',{}) if canonical else {r['log_id']:r for r in report['results']}
    if len(expected)!=len(records):raise ValueError('Manifest/report log count mismatch')
    statuses={}
    for log_id in records:
        src=source/'raw'/log_id[:4]/(log_id+'.xml.gz') if canonical else source/'raw'/(log_id+'.xml.gz')
        with gzip.open(src,'rb') as f:raw=f.read(8_000_001)
        validate_xml(raw)
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=expected[log_id]['sha256']:raise ValueError('Raw SHA-256 mismatch: '+log_id)
        dest=data/'raw'/log_id[:4]/src.name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dest)
        statuses[log_id]={'status':'downloaded','sha256':digest,'bytes':len(raw),'path':str(dest.relative_to(data)).replace('\\','/')}
    write_atomic(data/'manifest.json',raw_manifest)
    write_atomic(data/'manifests'/(sha+'.json'),raw_manifest)
    for name in ('download-report.json','downloads.jsonl','missing-links.json'):
        if (source/name).exists():write_atomic(data/'provenance'/('imported-'+name),(source/name).read_bytes())
    captured=report.get('captured_at')
    if not captured and report.get('fetched_at'):
        captured=datetime.strptime(report['fetched_at'],'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).isoformat()
    normalized={'target':TARGET,'captured_at':captured,'source_url':report.get('source_url') or report.get('manifest_url'),
                'manifest_sha256':sha,'source_records':len(manifest['list']),'unique_linked_logs':len(records),
                'records_without_url':len(unavailable),'downloaded':len(statuses),'failed':0,'not_attempted':0,
                'all_linked_logs_downloaded':len(statuses)==len(records),
                'all_source_records_have_logs':not unavailable and len(statuses)==len(manifest['list']),
                'imported_from_layout':'canonical' if canonical else 'phase1-database',
                'logs':statuses,'unavailable':unavailable}
    write_atomic(data/'download-report.json',json.dumps(normalized,ensure_ascii=False,indent=2).encode())
    print(json.dumps({k:v for k,v in normalized.items() if k not in ('logs','unavailable')},ensure_ascii=False))

if __name__=='__main__':main()
