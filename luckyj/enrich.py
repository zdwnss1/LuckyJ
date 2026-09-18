"""Offline, atomic, source-bound research index. Never modifies phase-one data."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from .analysis import VERSION, counts, facts, legal_discards, roles
from .shanten import shanten
from .store import connect, packed, unpacked
from .tiles import token, kind

COLUMNS = ('id','initial_shanten','before_draw_shanten','current_shanten','after_shanten','best_shanten',
           'self_riichi','opponent_riichi','dora_count','red_count','sanshoku_obvious','gap_above','gap_below',
           'gap_leader','gap_last','facts')


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def sanshoku_obvious(s):
    """Version 1: two complete equal sequences, third suit holds >=2 target ranks.
    Uses current own tiles (including fixed meld tiles), no inferred intention.
    """
    own=list(s['hand'])
    for m in s['melds'][0]:own.extend(m['tiles'])
    h=counts(own)
    for start in range(7):
        present=[sum(h[9*suit+start+i]>0 for i in range(3)) for suit in range(3)]
        if present.count(3)>=2 and min(present)>=2:return True
    return False


def enrich(data: Path):
    data=Path(data); base=data/'luckyj.sqlite';target=data/'research.sqlite'
    source_sha=file_sha(base);before=base.stat();tmp=target.with_name(f'research.build-{os.getpid()}.sqlite')
    tmp.unlink(missing_ok=True);out=sqlite3.connect(tmp);started=time.monotonic()
    src=connect(base)
    try:
        src.execute('BEGIN')
        out.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        out.execute('CREATE TABLE observations('+','.join(c+(' BLOB' if c=='facts' else ' INTEGER')+(' PRIMARY KEY' if c=='id' else '') for c in COLUMNS)+')')
        games={r['log_id']:dict(r) for r in src.execute('SELECT log_id,actor,game_type FROM games')}
        initial={}
        for r in src.execute('SELECT log_id,round_seq,events FROM rounds'):
            events=unpacked(r['events']);init=next(e['attributes'] for e in events if e['tag']=='INIT')
            ids=[int(x) for x in init['hai'+str(games[r['log_id']]['actor'])].split(',')]
            initial[(r['log_id'],r['round_seq'])]=shanten(counts(token(t) for t in ids))
        n=0;batch=[]
        for r in src.execute('SELECT * FROM decisions ORDER BY id'):
            s=unpacked(r['snapshot']);aka=not bool(games[r['log_id']]['game_type'] & 2)
            f=facts(s,aka);h=tuple(f['hand34']);after=list(h);after[kind(s['discard'])]-=1
            before_draw=None
            if s['draw'] is not None:
                hd=list(h);hd[kind(s['draw'])]-=1;before_draw=shanten(tuple(hd))
            legal=legal_discards(s)
            if s['discard'] not in legal:raise ValueError(f'Actual discard is not legal: {r["id"]}')
            best=8
            for t in set(map(kind,legal)):
                x=list(h);x[t]-=1;best=min(best,shanten(tuple(x)))
            f['aka']=aka;f['legal_discards']=legal;f['roles']=list(roles(r['wind'],r['seat_wind']))
            last=s['rivers'][0][-1]['event_seq'] if s['rivers'][0] else -1
            follows=[]
            for seat,river in enumerate(s['rivers'][1:],1):
                for d in river:
                    if d['event_seq']>last and kind(d['tile'])>=27:
                        follows.append({'tile':d['tile'],'seat':seat,'event_seq':d['event_seq']})
            f['follow_honors']=follows
            ordered=sorted(s['scores'],reverse=True);rank=s['ranks'][0];mine=s['scores'][0]
            values=(r['id'],initial[(r['log_id'],r['round_seq'])],before_draw,shanten(h),shanten(tuple(after)),best,
                    int(f['self_riichi']),f['opponent_riichi'],f['dora_count'],f['red_count'],int(sanshoku_obvious(s)),
                    ordered[rank-2]-mine if rank>1 else None, mine-ordered[rank] if rank<4 else None,
                    ordered[0]-mine,mine-ordered[-1],packed(f))
            batch.append(values);n+=1
            if len(batch)==1000:
                out.executemany('INSERT INTO observations VALUES('+','.join('?'*len(COLUMNS))+')',batch);out.commit();batch=[]
            if n%20000==0:print(f'Research facts: {n} states',flush=True)
        if batch:out.executemany('INSERT INTO observations VALUES('+','.join('?'*len(COLUMNS))+')',batch)
        out.executescript('CREATE INDEX obs_shanten ON observations(initial_shanten,after_shanten); CREATE INDEX obs_riichi ON observations(self_riichi,opponent_riichi);')
        report={'version':VERSION,'source_sha256':source_sha,'observations':n,'complete':True,
                'built_at_unix':int(time.time()),'elapsed_seconds':round(time.monotonic()-started,3),
                'remaining_semantics':'unseen=4-own_concealed-public_unique; not omniscient live wall',
                'sanshoku_definition':'two-equal-sequences-plus-two-v1','candidate_metrics':'computed on demand; optional precompute'}
        out.executemany('INSERT INTO metadata VALUES(?,?)',[('report',json.dumps(report)),('version',VERSION),('source_sha256',source_sha)])
        out.commit()
        if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Research index integrity failed')
        after=base.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Base changed during build')
    except BaseException:
        out.close();tmp.unlink(missing_ok=True);raise
    finally:src.close()
    out.close();os.replace(tmp,target)
    (data/'research-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return report
