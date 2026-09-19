"""Atomic research index: public facts, call opportunities, conditional wins and intent."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from collections import Counter
from .analysis import VERSION, counts, facts, legal_discards, roles
from .shanten import shanten
from .store import connect, packed, unpacked
from .tiles import token, kind
from .decision_flags import VERSION as FLAGS_VERSION, FLAG_KEYS, MELD_COLUMNS, annotate, sql_values
from .call_index import index_round, VERSION as CALL_VERSION
from .win_projection import project, calculator_status, VERSION as WIN_VERSION
from .intent import load_model, features, infer_round

INTENT_COLUMNS=('intent_push','intent_mawashi','intent_fold','review_push','review_mawashi','review_fold')
COLUMNS = ('id','initial_shanten','before_draw_shanten','current_shanten','after_shanten','best_shanten',
           'self_riichi','opponent_riichi','dora_count','red_count','sanshoku_obvious','gap_above','gap_below',
           'gap_leader','gap_last') + FLAG_KEYS + MELD_COLUMNS + INTENT_COLUMNS + ('facts',)


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def sanshoku_obvious(s):
    own=list(s['hand'])
    for m in s['melds'][0]:own.extend(m['tiles'])
    h=counts(own)
    for start in range(7):
        present=[sum(h[9*suit+start+i]>0 for i in range(3)) for suit in range(3)]
        if present.count(3)>=2 and min(present)>=2:return True
    return False


def enrich(data: Path, intent_model: Path | None = None):
    data=Path(data);base=data/'luckyj.sqlite';target=data/'research.sqlite'
    model=load_model(intent_model);scorer=calculator_status()
    source_sha=file_sha(base);before=base.stat();tmp=target.with_name(f'research.build-{os.getpid()}.sqlite')
    tmp.unlink(missing_ok=True);out=sqlite3.connect(tmp);started=time.monotonic();src=connect(base)
    n=0;call_count=Counter();opportunity_count=Counter();win_status=Counter();intent_labels=Counter();mismatches=[]
    try:
        src.execute('BEGIN')
        out.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        defs=','.join(c+(' BLOB' if c=='facts' else ' REAL' if c in INTENT_COLUMNS else ' INTEGER')+(' PRIMARY KEY' if c=='id' else '') for c in COLUMNS)
        out.execute('CREATE TABLE observations('+defs+')')
        out.executescript('''
            CREATE TABLE call_events(log_id TEXT,round_seq INTEGER,event_seq INTEGER,actor_relative INTEGER,
              kind TEXT,source_relative INTEGER,payload BLOB,PRIMARY KEY(log_id,event_seq));
            CREATE TABLE call_opportunities(id TEXT PRIMARY KEY,log_id TEXT,round_seq INTEGER,event_seq INTEGER,
              trigger TEXT,offered TEXT,wind INTEGER,hand_no INTEGER,honba INTEGER,turn INTEGER,
              shanten_before INTEGER,source_relative INTEGER,outcome TEXT,selected_kind TEXT,payload BLOB);
            CREATE INDEX call_events_kind ON call_events(actor_relative,kind);
            CREATE INDEX opportunity_context ON call_opportunities(trigger,wind,hand_no,shanten_before);
            CREATE INDEX opportunity_result ON call_opportunities(outcome,selected_kind);
        ''')
        games={r['log_id']:dict(r) for r in src.execute('SELECT log_id,actor,game_type FROM games')}
        for rnd in src.execute('SELECT * FROM rounds ORDER BY log_id,round_seq'):
            log=rnd['log_id'];rs=rnd['round_seq'];g=games[log];aka=not bool(g['game_type']&2)
            events=unpacked(rnd['events']);init=next(e['attributes'] for e in events if e['tag']=='INIT')
            ids=[int(x) for x in init['hai'+str(g['actor'])].split(',')]
            initial=shanten(counts(token(t,aka) for t in ids))
            indexed=index_round(events,g['actor'],log,rs,g['game_type'])
            for event in indexed['call_events']:
                out.execute('INSERT INTO call_events VALUES(?,?,?,?,?,?,?)',
                            (log,rs,event['event_seq'],event['actor_relative'],event['kind'],event['source_relative'],packed(event)))
                if event['actor_relative']==0:call_count[event['kind']]+=1
            for opp in indexed['opportunities']:
                s=opp['snapshot'];outcome=opp['resolution']['status']
                if outcome=='eligibility_mismatch':mismatches.append({'id':opp['id'],'reason':opp['resolution']['reason']})
                opp['availability']['own_melds']=s['melds'][0]
                opp['availability']['roles']=list(roles(s['wind'],s['seat_wind']))
                opp['availability']['aka']=aka
                out.execute('INSERT INTO call_opportunities VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (opp['id'],log,rs,opp['event_seq'],opp['trigger'],opp['offered'],s['wind'],s['hand_no'],
                     s['honba'],s['turn'],opp['shanten_before'],opp['source_relative'],outcome,opp['selected_kind'],packed(opp)))
                opportunity_count[outcome]+=1
            pending=[];evidence=[]
            for r in src.execute('SELECT * FROM decisions WHERE log_id=? AND round_seq=? ORDER BY event_seq',(log,rs)):
                s=unpacked(r['snapshot']);f=facts(s,aka);h=tuple(f['hand34']);after=list(h);after[kind(s['discard'])]-=1
                before_draw=None
                if s['draw'] is not None:
                    hd=list(h);hd[kind(s['draw'])]-=1;before_draw=shanten(tuple(hd))
                legal=legal_discards(s)
                if s['discard'] not in legal:raise ValueError(f'Actual discard is not legal: {r["id"]}')
                best=min(shanten(tuple(v-int(i==t) for i,v in enumerate(h))) for t in set(map(kind,legal)))
                after_s=shanten(tuple(after));ann=annotate(s,dict(r),before_draw,after_s,best)
                f.update(annotations=ann,aka=aka,game_type=g['game_type'],legal_discards=legal,
                         roles=list(roles(r['wind'],r['seat_wind'])),own_melds=s['melds'][0])
                trace=indexed['traces'][r['event_seq']]
                # Prove provenance is for precisely the already-present public melds.
                if [[(m['kind'],m['tiles']) for m in ms] for ms in trace['melds']] != [[(m['kind'],m['tiles']) for m in ms] for ms in s['melds']]:
                    raise ValueError('Meld provenance differs from immutable base snapshot')
                f['trace']=trace;f['own_melds']=trace['melds'][0]
                # follow_honors is the full opportunity window, not only the selected tile.
                lower=s['rivers'][0][-1]['event_seq'] if s['rivers'][0] else -1
                f['follow_honors']=[{'tile':d['tile'],'seat':seat,'event_seq':d['event_seq']}
                     for seat,river in enumerate(s['rivers'][1:],1) for d in river if d['event_seq']>lower and kind(d['tile'])>=27]
                f['win_projection']=project(s,dict(r),f,trace)
                win_status[f['win_projection']['status']]+=1
                ev=features(s,dict(r),f,model);evidence.append(ev)
                ordered=sorted(s['scores'],reverse=True);rank=s['ranks'][0];mine=s['scores'][0]
                vals=(r['id'],initial,before_draw,shanten(h),after_s,best,
                      int(f['self_riichi']),f['opponent_riichi'],f['dora_count'],f['red_count'],int(sanshoku_obvious(s)),
                      ordered[rank-2]-mine if rank>1 else None,mine-ordered[rank] if rank<4 else None,
                      ordered[0]-mine,mine-ordered[-1],*sql_values(ann))
                pending.append((vals,f))
            inferred=infer_round(evidence,model)
            rows=[]
            for (vals,f),inf in zip(pending,inferred):
                f['intent']=inf
                online=inf['online']['model_posterior'];review=inf['review']['model_posterior'];forced=inf['evidence']['forced']
                extra=tuple(None if forced else p[k] for p in (online,review) for k in ('push','mawashi','fold'))
                rows.append((*vals,*extra,packed(f)));n+=1;intent_labels[inf['online']['label']]+=1
            if rows:out.executemany('INSERT INTO observations VALUES('+','.join('?'*len(COLUMNS))+')',rows)
            if n and n%1000<len(rows):
                out.commit()
                if n%10000<1000:print(f'Research facts, calls, win values, intent: {n} states',flush=True)
        out.executescript('CREATE INDEX obs_shanten ON observations(initial_shanten,after_shanten); CREATE INDEX obs_riichi ON observations(self_riichi,opponent_riichi); CREATE INDEX obs_intent ON observations(intent_fold,intent_mawashi);')
        report={'version':VERSION,'decision_flags_version':FLAGS_VERSION,'source_sha256':source_sha,'observations':n,'complete':not mismatches,
                'built_at_unix':int(time.time()),'elapsed_seconds':round(time.monotonic()-started,3),
                'remaining_semantics':'unseen=4-own_concealed-public_unique; not omniscient live wall',
                'sanshoku_definition':'two-equal-sequences-plus-two-v1','candidate_metrics':'computed on demand; optional precompute',
                'call_index_version':CALL_VERSION,'registered_luckyj_calls':dict(call_count),'opportunity_resolutions':dict(opportunity_count),
                'eligibility_mismatches':mismatches,'win_projection_version':WIN_VERSION,'win_projection_status':dict(win_status),
                'scoring_dependency':scorer,'intent_model':model,'intent_labels_uncalibrated':dict(intent_labels),
                'intent_calibrated':False}
        out.executemany('INSERT INTO metadata VALUES(?,?)',[('report',json.dumps(report,ensure_ascii=False)),('version',VERSION),('source_sha256',source_sha)])
        out.commit()
        if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Research index integrity failed')
        after=base.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Base changed during build')
        if mismatches:raise ValueError(f'{len(mismatches)} registered calls outside opportunity enumeration: {mismatches[:3]}')
    except BaseException:
        out.close();tmp.unlink(missing_ok=True);raise
    finally:src.close()
    out.close();os.replace(tmp,target)
    (data/'research-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='intent_model'},ensure_ascii=False),flush=True)
    return report
