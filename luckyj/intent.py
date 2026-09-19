"""Inspectable Bayesian state filtering under EXPLICIT, uncalibrated assumptions.

This is a behavior annotation prototype, not a claim to know an agent's intent.
Only action opportunity costs and contemporaneous public evidence enter online
features. Retrospective lookahead is returned under separate, future-marked keys.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from .analysis import counts, effective
from .tiles import kind
from .shanten import shanten

STATES=('push','mawashi','fold')
LABELS={'push':'继续进攻倾向','mawashi':'回避／兜牌倾向','fold':'弃和倾向',
        'uncertain':'证据不足','forced':'受约束动作，暂不判意图'}
MODEL_PATH=Path(__file__).resolve().parents[1]/'models/intent-v1.json'


def load_model(path=None):
    raw=Path(path or MODEL_PATH).read_bytes();m=json.loads(raw)
    if m.get('states')!=list(STATES) or not 0.5<=m.get('temperature',0)<=4 or not 0<=m.get('context_reset',-1)<=1:
        raise ValueError('Invalid intent model configuration')
    if type(m.get('lookahead')) is not int or not 0<=m['lookahead']<=6:raise ValueError('lookahead must be 0..6')
    if set(m.get('context_priors',{}))!={'none','open','riichi','multi'}:raise ValueError('Context priors must cover none/open/riichi/multi')
    if set(m.get('tradeoff_likelihoods',{}))!={'neutral','safe_preserve','efficiency_loss_without_safety','small_sacrifice_safer','major_sacrifice_safer','unsafe_fast'}:raise ValueError('Invalid joint likelihood categories')
    for key in ('major_ukeire_loss','small_ukeire_loss','safer_margin','label_posterior'):
        if type(m.get('thresholds',{}).get(key)) not in (int,float) or not 0<=m['thresholds'][key]<=1:raise ValueError('Thresholds must be in0..1')
    if m['thresholds']['small_ukeire_loss']>m['thresholds']['major_ukeire_loss']:raise ValueError('Loss thresholds reversed')
    rows=[*m['context_priors'].values(),*m['transition']]
    if len(m['transition'])!=3 or any(len(x)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in x) or abs(sum(x)-1)>1e-8 for x in rows):
        raise ValueError('Intent priors/transitions must be positive normalized distributions')
    ls=m['tradeoff_likelihoods']
    if any(len(x)!=3 or any(v<=0 or not math.isfinite(v) for v in x) for x in ls.values()) or any(abs(sum(v[i] for v in ls.values())-1)>1e-8 for i in range(3)):
        raise ValueError('Each state likelihood column must sum to one')
    m['sha256']=hashlib.sha256(raw).hexdigest()
    return m


def normalize(v):
    total=sum(v)
    if total<=0 or not math.isfinite(total):raise ValueError('Invalid posterior normalization')
    return [x/total for x in v]


def safety_grade(t, opponent, snapshot, availability):
    """An ordinal public-safety proxy, NOT a deal-in probability."""
    own_river=snapshot['rivers'][opponent]
    discarded={kind(x['tile']) for x in own_river}
    if t in discarded:return 1.0,'genbutsu_own_river'
    if snapshot['riichi'][opponent]:
        declarations=[x['event_seq'] for x in own_river if x.get('riichi')]
        if declarations:
            start=min(declarations)
            after={kind(x['tile']) for rv in snapshot['rivers'] for x in rv if x['event_seq']>=start}
            if t in after:return 1.0,'passed_after_established_riichi'
    known=availability['hand34'][t]+availability['loss34'][t]
    if t>=27:
        return (0.78,'honor_three_or_more_known') if known>=3 else (0.30,'honor_two_known') if known==2 else (0.08,'honor_without_strong_safety')
    suji=any(t+d in discarded and 0<=t+d<27 and (t+d)//9==t//9 for d in (-3,3))
    # Suji does not protect against pair, closed, edge or special-hand waits.
    return (0.30,'suji_only_not_safe') if suji else (0.05,'no_public_safety_evidence')


def features(snapshot, decision, f, model):
    hand=tuple(f['hand34']);legal=f['legal_discards'];legal_types=set(map(kind,legal));actual=kind(snapshot['discard'])
    threats=[]
    for i in range(1,4):
        open_count=sum(m['kind']!='ankan' for m in snapshot['melds'][i])
        if snapshot['riichi'][i] or open_count>=3:
            threats.append({'seat':i,'kind':'riichi' if snapshot['riichi'][i] else 'three_open_melds_proxy'})
    pressure=('multi' if len(threats)>1 else 'riichi' if threats and threats[0]['kind']=='riichi' else 'open' if threats else 'none')
    forced=bool(snapshot['riichi'][0]) or len(legal_types)<=1
    candidates=[]
    need_efficiency=bool(threats) and not forced
    for t in sorted(legal_types):
        h=list(hand);h[t]-=1;h=tuple(h);s=shanten(h)
        incoming=effective(h) if need_efficiency else ()
        u=sum(f['remaining34'][x] for x in incoming) if need_efficiency else None
        by_threat=[]
        for th in threats:
            grade,reason=safety_grade(t,th['seat'],snapshot,f)
            by_threat.append({'seat':th['seat'],'grade':grade,'reason':reason})
        grades=[x['grade'] for x in by_threat]
        safety=0.7*min(grades)+0.3*sum(grades)/len(grades) if grades else None
        candidates.append({'tile_type':t,'after_shanten':s,'ukeire':u,'safety_grade':safety,'safety_evidence':by_threat})
    selected=next(x for x in candidates if x['tile_type']==actual)
    best_s=min(x['after_shanten'] for x in candidates)
    fast=[x for x in candidates if x['after_shanten']==best_s]
    best_u=max(x['ukeire'] for x in fast) if need_efficiency else None
    fastest=[x for x in fast if x['ukeire']==best_u] if need_efficiency else fast
    best_fast_safety=max(x['safety_grade'] for x in fastest) if threats else None
    safety_gain=selected['safety_grade']-best_fast_safety if threats else None
    gap=selected['after_shanten']-best_s
    relative_loss=(best_u-selected['ukeire'])/max(best_u,1) if need_efficiency and gap==0 else None
    ann=f['annotations'];transition=ann['shanten_transition'];retreat=transition['change_from_before_draw']
    sacrifice=(gap>0 or (relative_loss is not None and relative_loss>=model['thresholds']['small_ukeire_loss']))
    major=(gap>0 or (relative_loss is not None and relative_loss>=model['thresholds']['major_ukeire_loss']))
    safer=(safety_gain is not None and safety_gain>=model['thresholds']['safer_margin'])
    # If the fastest set includes the chosen tile, safety gain is zero. Compare
    # to other fast choices to recognize safe preservation without sacrifice.
    fast_min=min(x['safety_grade'] for x in fastest) if threats else None
    preserve_safer=bool(threats) and gap==0 and not sacrifice and selected['safety_grade']>=0.75 and selected['safety_grade']-fast_min>=0.28
    risky_fast=bool(threats) and gap==0 and not sacrifice and selected['safety_grade']<0.35 and any(x['safety_grade']>=0.9 for x in candidates)
    if pressure=='none' or forced:category='neutral';active=False
    elif major and safer:category='major_sacrifice_safer';active=True
    elif sacrifice and safer:category='small_sacrifice_safer';active=True
    elif sacrifice:category='efficiency_loss_without_safety';active=True
    elif preserve_safer:category='safe_preserve';active=True
    elif risky_fast:category='unsafe_fast';active=True
    else:category='neutral';active=True
    return {'event_seq':decision['event_seq'],'pressure':pressure,'threats':threats,
            'forced':forced,'category':category,'likelihood_active':active,
            'actual_tile':snapshot['discard'],'selected':selected,'candidates':candidates,
            'best_after_shanten':best_s,'best_ukeire':best_u,'shanten_gap':gap,
            'retreat_from_before_draw':retreat,'relative_ukeire_loss':relative_loss,'safety_gain_over_fastest':safety_gain,
            'follow_sources':ann['follow_sources'],
            'self_score':snapshot.get('scores',[None])[0],
            'self_rank':snapshot.get('ranks',[None])[0] if snapshot.get('ranks') else None,
            'known_dora_count':f['dora_count'],'red_count':f['red_count'],
            'context_not_weighted_yet':['point_situation','yaku_value','non_riichi_reading_below_three_open_melds'],
            'notes':['Safety grades are relative evidence, not deal-in probabilities.',
                     'Follow-discard is shown as provenance, not multiplied as a second independent likelihood.',
                     'Efficiency loss without a safer choice is weak evidence: yaku building and other explanations remain.']}


def _prior(model, pressure, fold_scale=1.0):
    p=list(model['context_priors'][pressure]);p[2]*=fold_scale
    return normalize(p)


def _run(records, model, fold_scale=1.0, temperature=None):
    temp=temperature or model['temperature'];emissions=[];matrices=[];post=[];priors=[]
    previous=None
    for f in records:
        context=_prior(model,f['pressure'],fold_scale)
        reset=model['context_reset']
        matrix=[[ (1-reset)*x+reset*context[j] for j,x in enumerate(row)] for row in model['transition']]
        prior=context if previous is None else [sum(previous[i]*matrix[i][j] for i in range(3)) for j in range(3)]
        likelihood=model['tradeoff_likelihoods'][f['category']] if f['likelihood_active'] else [1.0]*3
        emission=[x**(1/temp) for x in likelihood]
        previous=normalize([p*e for p,e in zip(prior,emission)])
        priors.append(prior);post.append(previous);emissions.append(emission);matrices.append(matrix)
    return priors,post,emissions,matrices


def infer_round(records, model):
    """Return one causal filter plus an explicitly future-marked bounded smoother."""
    priors,online,emissions,matrices=_run(records,model)
    alternatives=[_run(records,model,scale,temp)[1] for scale,temp in ((0.5,1.35),(2.0,1.35),(1.0,1.8))]
    output=[]
    for i,f in enumerate(records):
        beta=[1.0]*3;end=min(len(records)-1,i+model['lookahead'])
        for j in range(end,i,-1):
            beta=normalize([sum(matrices[j][a][b]*emissions[j][b]*beta[b] for b in range(3)) for a in range(3)])
        review=normalize([online[i][s]*beta[s] for s in range(3)])
        def result(p, mode):
            leader=max(range(3),key=lambda k:p[k]);confidence=p[leader]
            label=('forced' if f['forced'] else STATES[leader] if f['likelihood_active'] and confidence>=model['thresholds']['label_posterior'] else 'uncertain')
            return {'label':label,'label_zh':LABELS[label],'model_posterior':dict(zip(STATES,p)),
                    'confidence':None if f['forced'] else confidence,
                    'calibrated_probability':None,'calibration_status':'not_calibrated',
                    'mode':mode,'uses_future':mode.startswith('review_next') and end>i,
                    'entropy':-sum(x*math.log(x) for x in p)/math.log(3)}
        ranges={name:[min([online[i][k],*[a[i][k] for a in alternatives]]),max([online[i][k],*[a[i][k] for a in alternatives]])] for k,name in enumerate(STATES)}
        output.append({'model_version':model['version'],'model_sha256':model['sha256'],
                       'status':'experimental_expert_model','prior':dict(zip(STATES,priors[i])),
                       'online':result(online[i],'online'),'review':result(review,'review_next'+str(model['lookahead'])),
                       'future_evidence_events':[records[j]['event_seq'] for j in range(i+1,end+1)],
                       'sensitivity_envelope':ranges,'sensitivity_is_credible_interval':False,
                       'evidence':f,
                       'likelihood':dict(zip(STATES,model['tradeoff_likelihoods'][f['category']])) if f['likelihood_active'] else None,
                       'model_assumptions':'Hand-authored likelihoods and transition priors. Not trained on LuckyJ strategy summaries or calibrated labels.'})
    return output
