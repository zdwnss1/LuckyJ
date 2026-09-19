"""Independent physical-tile supply reconciliation and full-cohort examples.

Does not call analysis.facts or the main replay parser for the raw supply audit.
Meld bitfield decoding alone is shared with the protocol layer.
"""
from collections import Counter
from contextlib import closing
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luckyj.research import Research
from luckyj.tiles import meld, token
from luckyj.store import unpacked


def audit(data):
    engine=Research(data);engine.ready();states=games=0;failures=[]
    tokens=tuple(f'{i%9+1}{"mpsz"[i//9]}' for i in range(34))+('0m','0p','0s')
    with closing(engine.db()) as db:
        for game in db.execute('SELECT log_id,actor,sha256,game_type FROM games ORDER BY log_id'):
            log=game['log_id'];paths=list((data/'raw').rglob(log+'.xml.gz'))
            if len(paths)!=1:raise ValueError('Expected one raw file: '+log)
            raw=gzip.decompress(paths[0].read_bytes())
            if hashlib.sha256(raw).hexdigest()!=game['sha256']:raise ValueError('Raw SHA mismatch: '+log)
            expected={r['event_seq']:unpacked(r['facts']) for r in db.execute('SELECT d.event_seq,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.log_id=?',(log,))}
            actor=game['actor'];aka=not bool(game['game_type']&2);seen_events=set();hand=set();public=set()
            for seq,e in enumerate(ET.fromstring(raw)):
                if e.tag=='INIT':
                    hand=set(map(int,e.attrib['hai'+str(actor)].split(',')))
                    public={int(e.attrib['seed'].split(',')[5])}
                elif e.tag=='DORA':public.add(int(e.attrib['hai']))
                elif e.tag=='N':
                    who=int(e.attrib['who']);m=meld(int(e.attrib['m']),who)
                    public.update(m['tiles136'])
                    if who==actor:
                        consume=[m['tiles136'][-1]] if m['kind']=='kakan' else [t for t in m['tiles136'] if t!=m['called136']]
                        for t in consume:hand.remove(t)
                else:
                    m=re.fullmatch(r'([TUVWDEFGtuvwdefg])(\d+)',e.tag)
                    if not m:continue
                    letter=m[1].upper();tile=int(m[2])
                    if letter in 'TUVW':
                        if 'TUVW'.index(letter)==actor:hand.add(tile)
                    else:
                        if 'DEFG'.index(letter)==actor:
                            f=expected.get(seq)
                            if f is None:raise ValueError('Raw event missing from research index')
                            hc=Counter(t//4 for t in hand);pc=Counter(t//4 for t in public)
                            ok=not (hand&public) and all(f['hand34'][i]==hc[i] and f['loss34'][i]==pc[i] and f['remaining34'][i]==4-hc[i]-pc[i] for i in range(34))
                            hc37=Counter(token(t,aka) for t in hand);pc37=Counter(token(t,aka) for t in public)
                            totals=[4]*34+[int(aka)]*3
                            for i in (4,13,22):totals[i]=3 if aka else 4
                            ok=ok and all(f['hand37'][i]==hc37[t] and f['loss37'][i]==pc37[t] and f['remaining37'][i]==totals[i]-hc37[t]-pc37[t] for i,t in enumerate(tokens))
                            if not ok:failures.append({'log_id':log,'event_seq':seq,'reason':'physical supply mismatch'})
                            hand.remove(tile);seen_events.add(seq);states+=1
                        public.add(tile)
            if seen_events!=set(expected):raise ValueError('Extra indexed events: '+log)
            games+=1
    queries=[]
    for q in ({'seat_wind':'0','turn_max':'1','shanten_basis':'initial','shanten':'4'},
              {'hand':'233m','discard':'2m'},{'hand':'x{12}77z','turn_max':'2'}):
        s=engine.stats(q)
        queries.append({k:s[k] for k in ('total','rounds','games','ambiguous_states','complete','query_hash','recipe')})
    comparison=engine.compare({'filters':{'turn_min':'1','turn_max':'2','sanshoku':'exclude'},
        'a':{'kind':'follow_honor','role':0,'singleton':True},
        'b':{'kind':'local','patterns':['14','134','124'],'cut':1,'window':[1,4],'reverse':False}})
    receipt={'complete':not failures,'source_sha256':engine.receipt['source_sha256'],
             'games':games,'states':states,'physical_supply_failures':failures,
             'queries':queries,'comparison':comparison,
             'limits':'Supply and keys reconciled for all states; full counterfactual metrics are lazy, not exhaustively precomputed. Meld decoder shared; no formal correctness claim.'}
    (data/'research-audit.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('queries','comparison')},ensure_ascii=False),flush=True)
    if failures:raise SystemExit(1)
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'))
    audit(p.parse_args().data)
