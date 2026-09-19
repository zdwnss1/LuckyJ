"""Blind label export and grouped held-out evaluation; never trains on model guesses.

Export is uniform over eligible recorded decisions (not independent draws).
A deterministic game-level split prevents adjacent states leaking across splits.
Temperature fitting is optional and outputs a separate candidate calibrator;
it does not change the live engine or label its probabilities calibrated.
"""
import argparse
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from luckyj.research import Research
from luckyj.store import unpacked
STATES=('push','mawashi','fold')


def partition(log_id,seed=7301):
    return 'test' if int(hashlib.sha256(f'{seed}:{log_id}'.encode()).hexdigest()[:8],16)%5==0 else 'train'


def scaled(p,t):
    logs=[math.log(max(p[k],1e-15))/t for k in STATES];m=max(logs);v=[math.exp(x-m) for x in logs];total=sum(v)
    return dict(zip(STATES,[x/total for x in v]))


def metrics(rows,t=1.0):
    if not rows:return {'n':0,'status':'no_labels'}
    losses=[];briers=[];correct=0;bins=[[] for _ in range(10)];per_class={k:[[] for _ in range(10)] for k in STATES}
    for r in rows:
        p=scaled(r['posterior'],t);label=r['label'];best=max(p,key=p.get);conf=p[best]
        losses.append(-math.log(max(p[label],1e-15)));briers.append(sum((p[k]-int(label==k))**2 for k in STATES))
        correct+=best==label;bins[min(9,int(conf*10))].append((conf,int(best==label)))
        for k in STATES:per_class[k][min(9,int(p[k]*10))].append((p[k],int(label==k)))
    def summary(bs):return [{'range':[i/10,(i+1)/10],'n':len(v),'mean_prediction':sum(p for p,y in v)/len(v),'observed_rate':sum(y for p,y in v)/len(v)} for i,v in enumerate(bs) if v]
    return {'n':len(rows),'games':len({r['log_id'] for r in rows}), 'log_loss':sum(losses)/len(rows),
            'multiclass_brier_sum':sum(briers)/len(rows),'accuracy':correct/len(rows),
            'top_label_reliability':summary(bins),'class_reliability':{k:summary(v) for k,v in per_class.items()}}


def export(engine,path,limit,seed,query):
    cohort=engine.cohort(query);rng=random.Random(seed);ids=rng.sample(cohort['ids'],min(limit,len(cohort['ids'])))
    with closing(engine.db()) as db,path.open('x',encoding='utf8') as out:
        for i in ids:
            r=db.execute('SELECT d.log_id,d.event_seq,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.id=?',(i,)).fetchone();f=unpacked(r['facts'])
            if f['intent']['evidence']['forced']:continue
            record={'schema':'luckyj-human-label-v1','log_id':r['log_id'],'event_seq':r['event_seq'],
                    'human_label':None,'human_comment':'','labeler':'','label_view':'online',
                    'shown_model_prediction':False,'split':partition(r['log_id'],seed),
                    'source_sha256':engine.receipt['source_sha256'],'sampling':'uniform decisions before excluding constrained actions',
                    'query_hash':cohort['query_hash'],'seed':seed}
            out.write(json.dumps(record,ensure_ascii=False)+'\n')


def evaluate(engine,path,output,mode,fit,seed):
    raw=path.read_bytes();labels=[json.loads(x) for x in raw.splitlines() if x.strip()];seen=set();rows=[];excluded=0;anchored=0
    with closing(engine.db()) as db:
        for label in labels:
            y=label.get('human_label')
            if y not in STATES:excluded+=1;continue
            key=(label['log_id'],int(label['event_seq']))
            if key in seen:raise ValueError('Duplicate labeled state; resolve disagreement instead of counting twice')
            seen.add(key)
            r=db.execute('SELECT r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.log_id=? AND d.event_seq=?',key).fetchone()
            if r is None:raise ValueError('Labeled event not in this database')
            if label.get('source_sha256') not in (None,engine.receipt['source_sha256']):raise ValueError('Source hash mismatch')
            if label.get('label_view','online')!=mode:raise ValueError('Label information view differs from requested evaluation mode')
            f=unpacked(r[0]);inf=f['intent']
            if inf['evidence']['forced']:excluded+=1;continue
            anchored+=bool(label.get('shown_model_prediction'))
            rows.append({'log_id':key[0],'label':y,'posterior':inf[mode]['model_posterior']})
    train=[r for r in rows if partition(r['log_id'],seed)=='train'];test=[r for r in rows if partition(r['log_id'],seed)=='test']
    t=1.0;fitted=False
    if fit and len(train)>=20 and len({r['label'] for r in train})==3:
        candidates=[math.exp(-1.386294361+3.465735903*i/120) for i in range(121)]
        t=min(candidates,key=lambda x:metrics(train,x)['log_loss']);fitted=True
    report={'schema':'intent-evaluation-v1','source_sha256':engine.receipt['source_sha256'],
            'labels_sha256':hashlib.sha256(raw).hexdigest(),'model_sha256':engine.receipt['intent_model']['sha256'],
            'mode':mode,'split':'deterministic game-level 80/20','split_seed':seed,'excluded_unlabeled_uncertain_or_forced':excluded,
            'prediction_visible_to_labeler':anchored,'train':metrics(train),'heldout_raw':metrics(test),
            'candidate_temperature':t if fitted else None,'heldout_scaled':metrics(test,t) if fitted else None,
            'candidate_calibrator_status':'requires independent review; not installed' if fitted else 'not_fitted',
            'fit_requirements':'At least20 training labels, all3 classes; test never selects temperature.',
            'caveats':['No empirical claim without sufficient independent labels and games.',
                       'Labels seen alongside model predictions may be anchored; prefer blind export.',
                       'Handpicked or pattern-stratified labels do not establish population-wide calibration.',
                       'Repeated experiments on the same test partition invalidate a clean holdout.']}
    with output.open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['export','evaluate']);p.add_argument('--data',type=Path,default=Path('data'));p.add_argument('--output',type=Path,required=True);p.add_argument('--labels',type=Path);p.add_argument('--query',type=Path);p.add_argument('--limit',type=int,default=200);p.add_argument('--seed',type=int,default=7301);p.add_argument('--mode',choices=['online','review'],default='online');p.add_argument('--fit-temperature',action='store_true');a=p.parse_args()
    if not 1<=a.limit<=100000:p.error('limit must be1..100000')
    e=Research(a.data);e.ready()
    if a.command=='export':export(e,a.output,a.limit,a.seed,json.loads(a.query.read_text()) if a.query else {})
    elif not a.labels:p.error('--labels required')
    else:evaluate(e,a.labels,a.output,a.mode,a.fit_temperature,a.seed)
