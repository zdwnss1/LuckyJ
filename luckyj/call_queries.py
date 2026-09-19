"""Complete, read-only call-event and opportunity cohorts with explicit denominators."""
from collections import Counter
from contextlib import closing
import hashlib
import json
import time
from .analysis import facts, roles
from .patterns import one_tile
from .meld_patterns import Matcher
from .store import unpacked
from .call_index import VERSION

KINDS=('chi','pon','daiminkan','ankan','kakan')
BASE={'hand','supply','suits','reverse','red','honors','honor_roles','dora_position','dora_tiles','meld_mode'}
FIELDS=BASE|{'log_id','round_seq','event_seq','wind','hand_no','honba_min','honba_max','turn_min','turn_max',
             'seat_wind','rank','score_min','score_max','shanten_min','shanten_max','riichi_min','riichi_max',
             'call_kind','trigger','outcome','offered','source_relative','actor_relative','offset','limit'}
OUTCOMES=('selected','not_selected','censored','chose_win','eligibility_mismatch')
BOUNDS={'round_seq':(0,999),'event_seq':(0,99999),'wind':(0,3),'hand_no':(1,4),'seat_wind':(0,3),
        'honba_min':(0,100),'honba_max':(0,100),'turn_min':(1,100),'turn_max':(1,100),
        'rank':(1,4),'score_min':(-1000000,1000000),'score_max':(-1000000,1000000),
        'shanten_min':(-1,8),'shanten_max':(-1,8),'riichi_min':(0,3),'riichi_max':(0,3),
        'source_relative':(0,3),'actor_relative':(0,3),'offset':(0,10**9),'limit':(1,100)}


def validate(q,events=False):
    if not isinstance(q,dict) or set(q)-FIELDS:raise ValueError('未知鸣牌查询字段；见call_query_schema')
    if any(type(v) not in (str,int) or len(str(v))>600 for v in q.values()):raise ValueError('鸣牌查询字段类型或长度无效')
    q={k:str(v) for k,v in q.items() if v!=''}
    for k,(lo,hi) in BOUNDS.items():
        if k in q:
            try:v=int(q[k])
            except ValueError:raise ValueError(k+'必须为整数') from None
            if str(v)!=q[k] or not lo<=v<=hi:raise ValueError(k+'超出范围')
    for stem in ('honba','turn','score','shanten','riichi'):
        if stem+'_min' in q and stem+'_max' in q and int(q[stem+'_min'])>int(q[stem+'_max']):raise ValueError(stem+'下限大于上限')
    if q.get('call_kind','') not in ('',*KINDS):raise ValueError('call_kind无效')
    if q.get('trigger','') not in ('','external','self_draw'):raise ValueError('trigger无效')
    if q.get('outcome','') not in ('',*OUTCOMES):raise ValueError('outcome无效')
    if q.get('offered'):one_tile(q['offered'])
    if events:
        unsupported=set(q)-{'log_id','round_seq','event_seq','call_kind','source_relative','actor_relative','offset','limit'}
        if unsupported:raise ValueError('真实鸣牌事件接口只接受对局、事件、类型与座位；场况/牌形应查机会接口')
    elif 'actor_relative' in q and q['actor_relative']!='0':raise ValueError('机会索引只重建LuckyJ自身，不推断他家暗手')
    Matcher({**{k:v for k,v in q.items() if k in BASE},**({'discard':q['offered']} if q.get('offered') else {})})
    return q


def query_schema():
    return {'version':VERSION,'fields':sorted(FIELDS),'kinds':list(KINDS),'outcomes':list(OUTCOMES),
            'opportunity_grain':'One opponent discard or own draw with at least one legal call/kan candidate; candidates are not independent denominators.',
            'hand_semantics':'Pre-response concealed tiles + already fixed melds. offered follows the SAME map; no future replacement draw.',
            'censoring':'Other-player priority call/win or terminal event cannot establish a voluntary pass.',
            'no_call':'Registered no call, possibly automatic; does not establish intent.',
            'events':'Actual calls; actor_relative defaults to 0 (LuckyJ). These are not per-discard meld states.'}


def opportunities(engine,query,seconds=60):
    from .research import QueryLimitError
    q=validate(query);deadline=time.monotonic()+seconds
    matcher=Matcher({**{k:v for k,v in q.items() if k in BASE},**({'discard':q['offered']} if q.get('offered') else {})})
    offset=int(q.get('offset',0));limit=int(q.get('limit',30));items=[];n=0;games=set();rounds=set();statuses=Counter();choices=Counter()
    where=[];args=[]
    for key in ('log_id','round_seq','event_seq','wind','hand_no','source_relative','trigger','outcome'):
        if key in q:where.append(key+'=?');args.append(q[key])
    for key,column,op in [('honba_min','honba','>='),('honba_max','honba','<='),('turn_min','turn','>='),('turn_max','turn','<='),('shanten_min','shanten_before','>='),('shanten_max','shanten_before','<=')]:
        if key in q:where.append(column+op+'?');args.append(int(q[key]))
    with closing(engine.db()) as db:
        for row in db.execute('SELECT payload FROM research.call_opportunities'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY log_id,round_seq,event_seq',args):
            if time.monotonic()>deadline:raise QueryLimitError('鸣牌机会统计超时，未返回部分计数')
            p=unpacked(row[0]);s=p['snapshot'];f=p['availability']
            if q.get('call_kind') and not any(c['kind']==q['call_kind'] for c in p['candidates']):continue
            if 'seat_wind' in q and s['seat_wind']!=int(q['seat_wind']):continue
            if 'rank' in q and s['ranks'][0]!=int(q['rank']):continue
            if 'score_min' in q and s['scores'][0]<int(q['score_min']):continue
            if 'score_max' in q and s['scores'][0]>int(q['score_max']):continue
            if 'riichi_min' in q and f['opponent_riichi']<int(q['riichi_min']):continue
            if 'riichi_max' in q and f['opponent_riichi']>int(q['riichi_max']):continue
            try:match=matcher.matches(f['hand37'],f,s['wind'],s['seat_wind'],s.get('draw'),p['offered'],deadline=deadline)
            except TimeoutError as exc:raise QueryLimitError(str(exc)) from exc
            if match is None:continue
            status=p['resolution']['status'];statuses[status]+=1
            choice=('queried_kind' if p['selected_kind']==q.get('call_kind') else p['selected_kind']) if p['selected_kind'] else status
            choices[choice]+=1
            games.add(p['log_id']);rounds.add((p['log_id'],p['round_seq']))
            if offset<=n<offset+limit:items.append({**p,'alignment':match})
            n+=1
    rec={'schema':query_schema(),'filters':{k:v for k,v in q.items() if k not in ('offset','limit')},'source_sha256':engine.receipt['source_sha256']}
    denominator=statuses['selected']+statuses['not_selected']
    selected=choices['queried_kind'] if q.get('call_kind') else statuses['selected']
    return {'complete':True,'total':n,'games':len(games),'rounds':len(rounds),'status_counts':dict(statuses),
            'choice_counts':dict(choices),'resolved_response_opportunities':denominator,
            'selected_rate_among_resolved':selected/denominator if denominator and 'outcome' not in q else None,
            'rate_suppressed_by_outcome_filter':'outcome' in q,
            'denominator_note':'Excludes censored and own win. Choice-conditioned outcome filters change the denominator; this is registered behavior, not free-will call propensity.',
            'items':items,'offset':offset,'next_offset':offset+len(items) if offset+len(items)<n else None,
            'recipe':rec,'query_hash':hashlib.sha256(json.dumps(rec,sort_keys=True).encode()).hexdigest()}


def call_events(engine,query):
    q=validate(query,True);q.setdefault('actor_relative','0');where=[];args=[]
    for k in ('log_id','round_seq','event_seq','source_relative','actor_relative','call_kind'):
        if k in q:where.append(('kind' if k=='call_kind' else k)+'=?');args.append(q[k])
    cond=' WHERE '+' AND '.join(where);offset=int(q.get('offset',0));limit=int(q.get('limit',30))
    with closing(engine.db()) as db:
        total=db.execute('SELECT count(*) FROM research.call_events'+cond,args).fetchone()[0]
        kinds=dict(db.execute('SELECT kind,count(*) FROM research.call_events'+cond+' GROUP BY kind',args))
        items=[unpacked(r[0]) for r in db.execute('SELECT payload FROM research.call_events'+cond+' ORDER BY log_id,round_seq,event_seq LIMIT ? OFFSET ?',args+[limit,offset])]
    return {'complete':True,'total':total,'kind_counts':kinds,'items':items,'offset':offset,
            'next_offset':offset+len(items) if offset+len(items)<total else None,
            'recipe':{'version':VERSION,'source_sha256':engine.receipt['source_sha256'],'filters':q,'grain':'actual calls, not per-discard states'}}
