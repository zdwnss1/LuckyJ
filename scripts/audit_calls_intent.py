"""Audit the full new index: keys, provenance, actual actions and probability algebra.
This is reconciliation, not an independent mahjong scorer or calibration study.
"""
import argparse
from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luckyj.research import Research
from luckyj.store import unpacked
from luckyj.tiles import meld as decode_meld
from luckyj.call_queries import opportunities


def audit(data,output):
    e=Research(data);e.ready();counts=Counter();failures=[];selected_events=set();actual=set();examples={}
    with closing(e.db()) as db:
        for r in db.execute('SELECT d.log_id,d.event_seq,d.snapshot,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id'):
            s=unpacked(r['snapshot']);f=unpacked(r['facts']);counts['decisions']+=1
            for who,ms in enumerate(f['trace']['melds']):
                for m in ms:
                    if m['source_event_seq'] is None:continue
                    rv=s['rivers'][m['source_relative']]
                    source=next((x for x in rv if x['event_seq']==m['source_event_seq']),None)
                    if source is None or source['tile136']!=m['called136'] or source['called_by'] is None or m['source_event_seq']>=r['event_seq'] or rv[m['source_discard_number']-1]['event_seq']!=m['source_event_seq']:
                        failures.append(['source_link',r['log_id'],r['event_seq'],m['call_id']])
                    counts['linked_meld_observations']+=1
                    if who==0 and m['kind']=='chi' and sorted(m['tiles'])==['4s','5s','6s'] and m['called']=='6s' and m['source_discard_number']==6:
                        examples.setdefault('sixth_6s',{'log_id':r['log_id'],'event_seq':r['event_seq']})
            inf=f['intent']
            for mode in ('online','review'):
                p=inf[mode]['model_posterior']
                if abs(sum(p.values())-1)>1e-10 or min(p.values())<0 or inf[mode]['calibrated_probability'] is not None:
                    failures.append(['posterior',r['log_id'],r['event_seq']])
            if inf['online']['uses_future'] or any(x<=r['event_seq'] for x in inf['future_evidence_events']):failures.append(['future_marker',r['log_id'],r['event_seq']])
            wp=f['win_projection'];counts['win_'+wp['status']]+=1
            if wp['future_information']:failures.append(['win_future',r['log_id'],r['event_seq']])
            for o in wp['outcomes']:
                if o['remaining']<0 or (o['yakuman_multiplier'] and o['han'] is not None):failures.append(['win_contract',r['log_id'],r['event_seq']])
                counts['win_outcomes']+=1
        for r in db.execute('SELECT payload FROM research.call_opportunities'):
            o=unpacked(r[0]);counts['opportunities']+=1;counts['opportunity_'+o['resolution']['status']]+=1
            choices=[c for c in o['candidates'] if c['selected']]
            if o['resolution']['status']=='selected':
                if len(choices)!=1:failures.append(['selected_candidate',o['id']])
                key=(o['log_id'],o['resolution']['event_seq'])
                if key in selected_events:failures.append(['duplicate_selected_event',key])
                selected_events.add(key)
            if len({c['key'] for c in o['candidates']})!=len(o['candidates']):failures.append(['duplicate_candidates',o['id']])
        # Independently count target N tags, and reconcile their keys against selections.
        for g in db.execute('SELECT log_id,actor,sha256 FROM games'):
            path=data/'raw'/g['log_id'][:4]/(g['log_id']+'.xml.gz')
            if not path.exists():path=next((data/'raw').rglob(g['log_id']+'.xml.gz'))
            raw=gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest()!=g['sha256']:failures.append(['raw_hash',g['log_id']])
            for i,node in enumerate(ET.fromstring(raw)):
                if node.tag=='N' and int(node.attrib['who'])==g['actor']:actual.add((g['log_id'],i))
            counts['games']+=1
        if selected_events!=actual:failures.append(['actual_selected_difference',len(actual-selected_events),len(selected_events-actual)])
        if db.execute('PRAGMA research.integrity_check').fetchone()[0]!='ok':failures.append(['sqlite_integrity'])
    probes=[]
    for q in ({'hand':'[c]','turn_max':'6'},{'win_yaku':'平和','win_method':'ron','turn_max':'6'},{'intent_state':'fold','intent_min':'80'}):
        r=e.stats(q);probes.append({k:r[k] for k in ('total','games','rounds','recipe','query_hash')})
    kan=opportunities(e,{'trigger':'self_draw','call_kind':'ankan','limit':'1'})
    report={'complete':not failures,'source_sha256':e.receipt['source_sha256'],'model_sha256':e.receipt['intent_model']['sha256'],
            'counts':dict(counts),'actual_call_events':len(actual),'actual_calls_equal_selected_opportunities':actual==selected_events,
            'failures':failures,'examples':examples,'query_checks':probes,
            'ankan_opportunities':{k:kan[k] for k in ('total','status_counts','resolved_response_opportunities','choice_counts')},
            'limits':['Meld decoding is shared; this is not an independent rules proof.','Yaku values use pinned upstream scoring; profile contract checked, not independently rescored here.','Posterior algebra checked, not accuracy or calibration.','No private opponent hands or final outcomes used for online inference.']}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({k:report[k] for k in ('complete','counts','actual_call_events','failures','examples')},ensure_ascii=False))
    if failures:raise SystemExit(1)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data'));p.add_argument('--output',type=Path,default=Path('data/calls-intent-audit.json'));a=p.parse_args();audit(a.data,a.output)
