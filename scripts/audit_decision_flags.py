"""Check every materialized label against its source snapshot and scalar fields.
This is consistency auditing, not an independent shanten proof. No network access.
"""
from pathlib import Path
from contextlib import closing
from collections import Counter
import argparse
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luckyj.research import Research
from luckyj.decision_flags import FLAG_KEYS
from luckyj.store import unpacked
from luckyj.tiles import kind, meld


def audit(data):
    engine=Research(data);engine.ready();checked=0;bad=[];state_counts=Counter();calls=Counter()
    with closing(engine.db()) as db:
        sql='SELECT d.snapshot,d.event_seq,r.* FROM decisions d JOIN research.observations r ON r.id=d.id'
        for row in db.execute(sql):
            f=unpacked(row['facts']);a=f['annotations'];s=unpacked(row['snapshot']);flags=a['flags']
            tests=[row[k] == (None if flags[k] is None else int(flags[k])) for k in FLAG_KEYS]
            low=s['rivers'][0][-1]['event_seq'] if s['rivers'][0] else -1
            src=[(i,t['event_seq'],t['tile']) for i,river in enumerate(s['rivers'][1:],1) for t in river
                 if low<t['event_seq']<row['event_seq'] and kind(t['tile'])==kind(s['discard'])]
            tests.append(sorted(src)==sorted((t['seat'],t['event_seq'],t['tile']) for t in a['follow_sources']))
            tests.append(flags['follow_discard']==bool(src))
            tests.append(flags['riichi_locked']==bool(s['riichi'][0]))
            before=row['before_draw_shanten'];after=row['after_shanten'];best=row['best_shanten']
            tests.append(flags['shanten_retreat']==(after>before if before is not None else None))
            tests.append(flags['miss_minimum']==(after>best))
            own_open=sum(m['kind']!='ankan' for m in s['melds'][0])
            tests.append(flags['damaten']==(after==0 and own_open==0 and not s['riichi'][0] and not flags['riichi_declaration']))
            tests.append(row['own_open_melds']==own_open)
            if not all(tests):bad.append(row['id'])
            state_counts[own_open]+=1;checked+=1
        actors={r['log_id']:r['actor'] for r in db.execute('SELECT log_id,actor FROM games')}
        for r in db.execute('SELECT log_id,events FROM rounds'):
            for e in unpacked(r['events']):
                if e['tag']=='N' and int(e['attributes']['who'])==actors[r['log_id']]:
                    calls[meld(int(e['attributes']['m']),actors[r['log_id']])['kind']]+=1
        integrity=db.execute('PRAGMA research.integrity_check').fetchone()[0]
    sets={}
    for name,q in [('default',{}),('all_discard_states',{'exclude_locked':0})]:
        stats=engine.stats(q)
        if not all(sum(n.values())==stats['total'] for n in stats['decision_flags'].values()):
            raise ValueError('Facet totals do not reconcile')
        sets[name]={k:stats[k] for k in ('total','games','rounds','decision_flags','flags_denominator','flags_overlap','query_hash')}
    result={'complete':not bad,'source_sha256':engine.receipt['source_sha256'],
            'checked_decisions':checked,'inconsistencies':bad,'sqlite_integrity':integrity,
            'cohorts':sets,'open_meld_state_counts':dict(state_counts),'actual_meld_event_counts':dict(calls),
            'limitations':'Source snapshot and scalar consistency only. No new independent shanten proof; no call-opportunity or public deployment claim.'}
    (Path(data)/'decision-flags-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if bad or integrity!='ok':raise SystemExit(1)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'))
    audit(p.parse_args().data)
