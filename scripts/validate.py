"""Independent corpus reconciliation and query smoke checks. No source requests."""
import argparse
import collections
import contextlib
import gzip
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import luckyj


def validate(data):
    report = json.loads((data/'build-report.json').read_text())
    failures = []
    checked = 0
    with contextlib.closing(luckyj.connection(data/'luckyj.sqlite')) as db:
        # Validation is an offline batch, not an HTTP query; do not impose the API deadline.
        db.set_progress_handler(None, 0)
        expected = {r[0]:r[1] for r in db.execute('SELECT game_id,COUNT(*) FROM decisions GROUP BY game_id')}
        observed = {}
        for row in db.execute('SELECT id,raw_path FROM games'):
            root = ET.fromstring(gzip.decompress((data/row['raw_path']).read_bytes()))
            un = root.find('UN')
            names = [urllib.parse.unquote(un.get('n'+str(i),'')) for i in range(4)]
            seat = names.index(luckyj.PLAYER)
            pattern = re.compile('['+'DEFG'[seat]+'defg'[seat]+']\\d+$')
            observed[row['id']] = sum(bool(pattern.fullmatch(e.tag)) for e in root)
        if observed != expected:
            failures.append('Independent raw discard counts disagree with the decision index')
        for row in db.execute('SELECT id,hand,hand_norm,score0,rank,snapshot FROM decisions'):
            c = luckyj.unpack(row['snapshot'])
            seat = c['lucky_seat']
            checks = [c['id']==row['id'], c['discard_id'] in c['hand_ids'],
                      len(c['rivers'][seat])==c['turn']-1,
                      c['sequence']==[t['tile'] for t in c['rivers'][seat]]+[c['discard']],
                      c['tsumogiri']==(c['draw_id']==c['discard_id']),
                      c['scores'][seat]==row['score0'], c['rank']==row['rank'],
                      row['hand']==luckyj.tile_counts(c['hand']),
                      row['hand_norm']==luckyj.tile_counts(c['hand'],True),
                      len(c['hand'])+3*len(c['melds'][seat])==14,
                      'hands' not in c]
            if not all(checks): failures.append('Snapshot mismatch: '+row['id'])
            checked += 1
        integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        foreign = len(db.execute('PRAGMA foreign_key_check').fetchall())
        source_statuses = dict(db.execute('SELECT status,COUNT(*) FROM source_records GROUP BY status').fetchall())
    examples=[]
    for params in ({}, {'round_no':'7','rank':'4','score0_max':'20000'},
                   {'hand':'4556m','discard':'5m'}, {'sequence':'1z9m5p'},
                   {'discard':'0p','tsumogiri':'0'}):
        started=time.perf_counter()
        with contextlib.closing(luckyj.connection(data/'luckyj.sqlite')) as db:
            result=luckyj.search(db,{**params,'limit':'3'})
        examples.append({'params':params,'matches':result['total'],
                         'first_ids':[x['id'] for x in result['items']],
                         'elapsed_seconds':round(time.perf_counter()-started,4)})
    output={'validated_games':len(observed),'raw_luckyj_discards':sum(observed.values()),
            'checked_snapshots':checked,'source_statuses':source_statuses,
            'raw_counts_match_index':observed==expected,'failures':failures,
            'sqlite_integrity':integrity,'foreign_key_errors':foreign,
            'queries':examples,'all_checks_passed':not failures and integrity=='ok' and foreign==0 and checked==report['decisions']}
    luckyj.save_json(data/'validation-report.json',output)
    print(json.dumps(output,ensure_ascii=False,indent=2))
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('data'))
    result=validate(parser.parse_args().data)
    raise SystemExit(0 if result['all_checks_passed'] else 1)
