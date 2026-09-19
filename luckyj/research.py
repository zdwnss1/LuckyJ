"""Read-only research API: exact cohorts, bounded computation, auditable recipes.

The mutable metrics cache is separate from both immutable source and facts DBs.
No eval, model-authored SQL, private-opponent-hand access or sample-based totals.
Experimental intent hypotheses are separately versioned and explicitly uncalibrated.
"""
from __future__ import annotations
import bisect
from collections import Counter, OrderedDict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from .analysis import VERSION, GOOD_RULE, analyze, TOKENS
from .enrich import file_sha
from .patterns import map_token
from .meld_patterns import Matcher
from . import win_projection
from .store import connect, filters, summary, detail, packed, unpacked
from .tiles import kind, normal
from .decision_flags import VERSION as FLAGS_VERSION, FLAG_KEYS, FLAG_DEFINITIONS, new_facets, count_facets

LEGACY={'basis','wind','hand_no','honba_min','honba_max','seat_wind','turn_min','turn_max',
        'tsumogiri','riichi_declared','log_id','round_seq'}
LEGACY.update(f'{p}{i}{s}' for i in range(4) for p,sufs in [('rank',['']),('score',['_min','_max'])] for s in sufs)
PATTERN={'hand','supply','suits','reverse','honors','honor_roles','red','draw','discard','dora_position','dora_tiles','meld_mode'}
RANGES={'riichi':(0,3,'opponent_riichi'),'dora':(0,100,'dora_count'),'red_count':(0,3,'red_count'),
        'gap_above':(0,1000000,'gap_above'),'gap_below':(0,1000000,'gap_below'),
        'gap_leader':(0,1000000,'gap_leader'),'gap_last':(0,1000000,'gap_last')}
RANGES.update({'open_melds':(0,4,'own_open_melds'), 'closed_kans':(0,4,'own_closed_kans'),
               'opponents_open':(0,3,'opponents_open')})
SHANTEN_BASES={'initial':'initial_shanten','before_draw':'before_draw_shanten','current':'current_shanten','after':'after_shanten','best':'best_shanten'}
KNOWN=LEGACY|PATTERN|set(FLAG_KEYS)|{'shanten','shanten_min','shanten_max','shanten_basis','exclude_locked','sanshoku',
                    'max_ukeire','max_good','ukeire_min','ukeire_max','good_min','good_max',
                    'after','limit','bucket','raw_bucket','metrics','initial_shanten'}
KNOWN.update(p+'_'+s for p in RANGES for s in ('min','max'))
KNOWN.update(win_projection.FILTERS|{'win_enabled','intent_state','intent_min','intent_mode'})


class QueryLimitError(RuntimeError):pass
class MetricUnknownError(RuntimeError):pass


def integer(q,key,low,high,default=None):
    if key not in q or q[key]=='':return default
    try:n=int(q[key])
    except (ValueError,TypeError):raise ValueError(key+' 必须为整数') from None
    if str(n)!=str(q[key]) or not low<=n<=high:raise ValueError(f'{key} 必须为 {low}..{high} 的整数')
    return n


def validate(q):
    if not isinstance(q,dict):raise ValueError('检索条件必须为对象')
    if set(q)-KNOWN:raise ValueError('未知检索字段: '+','.join(sorted(set(q)-KNOWN)))
    if any(not isinstance(v,(str,int)) or len(str(v))>600 for v in q.values()):raise ValueError('检索参数类型或长度无效')
    q={k:str(v) for k,v in q.items() if v!=''}
    Matcher(q)
    win_projection.validate_filters(q)
    integer(q,'win_enabled',0,1)
    if q.get('intent_state','') not in ('','push','mawashi','fold'):raise ValueError('intent_state应为push/mawashi/fold')
    if q.get('intent_mode','online') not in ('online','review'):raise ValueError('intent_mode应为online/review')
    integer(q,'intent_min',0,100)
    if 'intent_min' in q and not q.get('intent_state'):raise ValueError('置信筛选需指定intent_state')
    filters({k:v for k,v in q.items() if k in LEGACY})
    for key in ('exclude_locked','max_ukeire','max_good','metrics'):
        integer(q,key,0,1)
    for key in FLAG_KEYS:integer(q,key,0,1)
    if q.get('riichi_locked')=='1' and q.get('exclude_locked','1')=='1':
        raise ValueError('要检索立直后切牌，请将 exclude_locked 设为0')
    integer(q,'after',0,2**63-1);integer(q,'limit',1,100)
    integer(q,'initial_shanten',-1,8)
    if q.get('shanten_basis','after') not in SHANTEN_BASES:raise ValueError('向听时点无效')
    if q.get('sanshoku','any') not in ('any','exclude','require'):raise ValueError('三色规则为 any/exclude/require')
    if 'shanten' in q and ('shanten_min' in q or 'shanten_max' in q):raise ValueError('向听精确值和区间不能同时填写')
    integer(q,'shanten',-1,8)
    for prefix,(lo,hi,_) in {**RANGES,'shanten':(-1,8,''),'ukeire':(0,136,''),'good':(0,136,'')}.items():
        a=integer(q,prefix+'_min',lo,hi);b=integer(q,prefix+'_max',lo,hi)
        if a is not None and b is not None and a>b:raise ValueError(prefix+' 下限大于上限')
    return q


def clauses(q):
    where,args=filters({k:v for k,v in q.items() if k in LEGACY});parts=[where]
    if q.get('exclude_locked','1')=='1':parts.append('r.self_riichi=0')
    for key in FLAG_KEYS:
        if key in q:parts.append('r.'+key+'=?');args.append(int(q[key]))
    for prefix,(lo,hi,column) in RANGES.items():
        for suffix,op in (('min','>='),('max','<=')):
            value=integer(q,prefix+'_'+suffix,lo,hi)
            if value is not None:parts.append(f'r.{column}{op}?');args.append(value)
    column=SHANTEN_BASES[q.get('shanten_basis','after')]
    for k,op in [('shanten','='),('shanten_min','>='),('shanten_max','<=')]:
        v=integer(q,k,-1,8)
        if v is not None:parts.append(f'r.{column}{op}?');args.append(v)
    if 'initial_shanten' in q:parts.append('r.initial_shanten=?');args.append(int(q['initial_shanten']))
    if q.get('sanshoku','any')!='any':parts.append('r.sanshoku_obvious=?');args.append(int(q['sanshoku']=='require'))
    if win_projection.active(q):parts.append('r.after_shanten=0')
    if q.get('intent_state'):
        prefix='intent' if q.get('intent_mode','online')=='online' else 'review'
        parts.append('r.'+prefix+'_'+q['intent_state']+'>=?')
        args.append(int(q.get('intent_min','62'))/100)
    return ' AND '.join(parts),args


def recipe(q):
    return {'schema':'luckyj-query-v1','metric_version':VERSION,'good_rule':GOOD_RULE,
            'decision_flags_version':FLAGS_VERSION,'decision_flags':FLAG_DEFINITIONS,
            'meld_scope':'fixed meld brackets and concealed tiles share one mapping; ankan is separate; event/opportunity endpoints have independent denominators',
            'win_projection':{'version':win_projection.VERSION,'scope':'conditional values after actual discard, tenpai only; no future result/ura/ippatsu'},
            'intent':{'mode':q.get('intent_mode','online'),'status':'expert HMM; NOT empirically calibrated probability','review_uses_future':q.get('intent_mode')=='review'},
            'filters':q,'defaults':{'suits':True,'reverse':False,'honors':'roles','red':False,
                                  'honor_roles':[1,0,0,0,1,1,1],'exclude_locked':True,'shanten_basis':'after'},
            'remaining':'unseen, not omniscient live wall; signed supply constraints never modify a sample',
            'turn':'LuckyJ discard ordinal, includes post-call discards',
            'sanshoku':'two-equal-sequences-plus-two-v1 (explicit structural predicate, not inferred intent)',
            'good':'shortest shanten-reducing route to ryanmen; immediate counts weighted by real unseen supply; future route is structural',
            'normalization':'one state counted once; ambiguous action mappings kept as joint buckets',
            'scope':'actual discard decisions only; not win/kan choices or causal/EV estimates'}


class Research:
    def __init__(self,data,good_budget=12000):
        if type(good_budget) is not int or not 0<=good_budget<=1000000:raise ValueError("好形预算必须为0..1000000")
        self.good_budget=good_budget
        self.data=Path(data);self.source=self.data/'luckyj.sqlite';self.index=self.data/'research.sqlite'
        self.lock=threading.RLock();self.active=threading.BoundedSemaphore(2)
        self.cohorts=OrderedDict();self.stamp=None;self.receipt=None
        self.cache=self.data/'research-metrics.sqlite'
        with closing(sqlite3.connect(self.cache)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY,body BLOB NOT NULL)');db.commit()

    def ready(self):
        if not self.index.exists():raise ValueError('研究索引尚未建立：运行 python -m luckyj enrich --data data')
        stat=self.source.stat();ist=self.index.stat();stamp=(stat.st_size,stat.st_mtime_ns,ist.st_mtime_ns)
        with self.lock:
            if self.stamp==stamp:return
            with closing(sqlite3.connect(self.index.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                meta=dict(db.execute('SELECT key,value FROM metadata'))
            if meta.get('version')!=VERSION or meta.get('source_sha256')!=file_sha(self.source):
                raise ValueError('研究索引已过期或不属于此数据包，请重新 enrich；禁止混用数据库')
            self.receipt=json.loads(meta['report']);self.stamp=stamp;self.cohorts.clear()

    def db(self):
        self.ready();db=connect(self.source)
        db.execute('ATTACH DATABASE ? AS research',(self.index.resolve().as_uri()+'?mode=ro',))
        return db

    def metrics(self,row,full=False):
        key=hashlib.sha256((VERSION+':'+str(self.good_budget)+('full' if full else 'actual')).encode()+row['snapshot']+bytes([row['aka']])).hexdigest()
        with closing(sqlite3.connect(self.cache,timeout=10)) as db:
            cached=db.execute('SELECT body FROM metrics WHERE key=?',(key,)).fetchone()
        if cached:return unpacked(cached[0])
        a=analyze(unpacked(row['snapshot']),aka=bool(row['aka']),only_actual=not full,good_budget=self.good_budget)
        with closing(sqlite3.connect(self.cache,timeout=10)) as db:
            db.execute('INSERT OR IGNORE INTO metrics VALUES(?,?)',(key,packed(a)));db.commit()
        return a

    def _metric_filter(self,row,q):
        need_full='max_ukeire' in q or 'max_good' in q
        if not need_full and not any(x in q for x in ('ukeire_min','ukeire_max','good_min','good_max')):return True
        a=self.metrics(row,need_full)['selected']
        for k in ('max_ukeire','max_good'):
            if k in q:
                if a[k] is None:raise MetricUnknownError('部分好形路径未在计算预算内证明；没有输出不完整统计')
                if int(a[k])!=int(q[k]):return False
        for prefix,key in (('ukeire','ukeire'),('good','good_ukeire')):
            if (prefix+'_min' in q or prefix+'_max' in q) and a[key] is None:raise MetricUnknownError('好形计算有未决项；请缩小范围或离线重算')
            if prefix+'_min' in q and a[key]<int(q[prefix+'_min']):return False
            if prefix+'_max' in q and a[key]>int(q[prefix+'_max']):return False
        return True

    def cohort(self,query,seconds=60):
        q=validate(query);q={k:v for k,v in q.items() if k not in ('after','limit','bucket','raw_bucket','metrics')}
        self.ready();key=json.dumps(q,sort_keys=True,separators=(',',':'))
        with self.lock:
            if key in self.cohorts:
                self.cohorts.move_to_end(key);return self.cohorts[key]
        if not self.active.acquire(blocking=False):raise QueryLimitError('已有两项查询在运行，请稍后重试')
        start=time.monotonic();deadline=start+seconds;matcher=Matcher(q)
        try:
            where,args=clauses(q);ids=[];buckets={};raw_buckets={};raw=Counter();normalized=Counter();games=set();rounds=set();ambiguous=0
            facets=new_facets()
            expensive=any(k in q for k in ('max_ukeire','max_good','ukeire_min','ukeire_max','good_min','good_max'))
            with closing(self.db()) as db:
                db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
                sql=('SELECT d.id,d.log_id,d.round_seq,d.wind,d.seat_wind,d.draw,d.discard,r.facts'+
                     (',d.snapshot' if expensive else '')+
                     ' FROM decisions d JOIN research.observations r ON r.id=d.id WHERE '+where+' ORDER BY d.id')
                for r in db.execute(sql,args):
                    if time.monotonic()>deadline:raise QueryLimitError('查询达到计算预算；未返回部分计数。请缩小条件或使用离线预计算')
                    f=unpacked(r['facts'])
                    match=matcher.matches(f['hand37'],f,r['wind'],r['seat_wind'],r['draw'],r['discard'],deadline=deadline)
                    if match is None:continue
                    if win_projection.active(q) and not win_projection.matches(f['win_projection'],q):continue
                    if expensive and not self._metric_filter({**dict(r),'aka':f['aka']},q):continue
                    count_facets(facets,f['annotations']['flags'])
                    labels=match['possible_discards']
                    bucket=' / '.join(labels) if labels else r['discard']
                    if len(labels)>1:ambiguous+=1
                    ids.append(r['id']);buckets[r['id']]=bucket;raw_buckets[r['id']]=r['discard']
                    raw[r['discard']]+=1;normalized[bucket]+=1
                    games.add(r['log_id']);rounds.add((r['log_id'],r['round_seq']))
            receipt=recipe(q);receipt['source_sha256']=self.receipt['source_sha256'];receipt['good_proof_budget']=self.good_budget;receipt['intent_model']=self.receipt.get('intent_model')
            digest=hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest()
            c={'ids':ids,'buckets':buckets,'raw_buckets':raw_buckets,'total':len(ids),'games':len(games),'rounds':len(rounds),
               'raw':dict(raw),'normalized':dict(normalized),'ambiguous_states':ambiguous,
               'decision_flags':facets,'flags_denominator':len(ids),'flags_overlap':True,
               'recipe':receipt,'query_hash':digest,'complete':True,'elapsed_seconds':round(time.monotonic()-start,3)}
            with self.lock:
                self.cohorts[key]=c
                while len(self.cohorts)>4:self.cohorts.popitem(last=False)
            return c
        except TimeoutError as exc:raise QueryLimitError(str(exc)) from exc
        except sqlite3.OperationalError as exc:
            if 'interrupt' in str(exc).lower():raise QueryLimitError('查询超时，未返回部分统计') from exc
            raise
        finally:self.active.release()

    def search(self,query):
        q=validate(query);c=self.cohort(q);ids=c['ids'];bucket=q.get('bucket')
        if bucket:
            if bucket not in c['normalized']:raise ValueError('未知统计桶')
            ids=[i for i in ids if c['buckets'][i]==bucket]
        if q.get('raw_bucket'):
            if bucket:raise ValueError('不能同时选择原样与同形统计桶')
            ids=[i for i in ids if c['raw_buckets'][i]==q['raw_bucket']]
        offset=bisect.bisect(ids,int(q.get('after','0')));limit=int(q.get('limit','20'));page=ids[offset:offset+limit]
        matcher=Matcher(q);items=[]
        with closing(self.db()) as db:
            for i in page:
                row=db.execute('SELECT d.*,r.facts,r.initial_shanten,r.before_draw_shanten,r.current_shanten,r.after_shanten,r.best_shanten FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.id=?',(i,)).fetchone()
                f=unpacked(row['facts']);item=summary(row,q.get('basis','decision'))
                item['alignment']=matcher.matches(f['hand37'],f,row['wind'],row['seat_wind'],row['draw'],row['discard'])
                item['availability']=f
                item['annotations']=f['annotations']
                item['own_melds']=f['own_melds'];item['intent']=f['intent'];item['win_projection']=f['win_projection']
                item['shanten']={k:row[k+'_shanten'] for k in ('initial','before_draw','current','after','best')}
                if q.get('metrics','1')=='1':
                    a=self.metrics({**dict(row),'aka':f['aka']});item['analysis']=a['selected']
                item['bucket']=c['buckets'][i];items.append(item)
        return {'total':len(ids),'cohort_total':c['total'],'items':items,
                'next_after':page[-1] if offset+limit<len(ids) and page else None,
                'query_hash':c['query_hash'],'complete':True}

    def stats(self,q):
        c=self.cohort(q)
        return {k:v for k,v in c.items() if k not in ('ids','buckets','raw_buckets')}

    def decision(self,identifier,q=None):
        q=validate(q or {});matcher=Matcher(q)
        with closing(self.db()) as db:
            d=detail(db,identifier)
            if not d:return None
            r=db.execute('SELECT d.*,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.id=?',(identifier,)).fetchone()
            f=unpacked(r['facts']);d['analysis']=self.metrics({**dict(r),'aka':f['aka']},True)
            d['alignment']=matcher.matches(f['hand37'],f,r['wind'],r['seat_wind'],r['draw'],r['discard'])
            d['facts']=f
            d['annotations']=f['annotations']
            d['snapshot']['melds']=f['trace']['melds']
            d['intent']=f['intent'];d['win_projection']=f['win_projection']
            return d

    def compare(self,body):
        if not isinstance(body,dict) or set(body)-{'filters','a','b'}:raise ValueError('比较格式为 {filters,a,b}')
        a=ActionPredicate(body.get('a',{}));b=ActionPredicate(body.get('b',{}))
        c=self.cohort(body.get('filters',{}));deadline=time.monotonic()+60
        eligible_a=eligible_b=common=0;all_choices=Counter();joint=Counter();samples={k:[] for k in ('a','b','both','other')}
        games=set();rounds=set()
        with closing(self.db()) as db:
            for i in c['ids']:
                if time.monotonic()>deadline:raise QueryLimitError('比较超时，没有输出部分统计')
                r=db.execute('SELECT d.discard,d.log_id,d.round_seq,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.id=?',(i,)).fetchone()
                f=unpacked(r['facts']);av=a.available(f);bv=b.available(f);actual=kind(r['discard'])
                label='both' if actual in av and actual in bv else 'a' if actual in av else 'b' if actual in bv else 'other'
                all_choices[label]+=1;eligible_a+=bool(av);eligible_b+=bool(bv)
                if av and bv:
                    common+=1;joint[label]+=1;games.add(r['log_id']);rounds.add((r['log_id'],r['round_seq']))
                    if len(samples[label])<20:samples[label].append(i)
        comparison_recipe={**c['recipe'],'a':a.spec,'b':b.spec}
        comparison_hash=hashlib.sha256(json.dumps(comparison_recipe,sort_keys=True).encode()).hexdigest()
        return {'complete':True,'cohort_total':c['total'],'eligible_a':eligible_a,'eligible_b':eligible_b,
                'raw_choice_counts':dict(all_choices),'common_opportunities':common,'common_games':len(games),
                'common_rounds':len(rounds),'common_choice_counts':dict(joint),
                'common_choice_rates':{k:v/common for k,v in joint.items()} if common else {},
                'example_ids':samples,'recipe':comparison_recipe,'query_hash':comparison_hash,
                'interpretation':'Opportunity-conditioned frequencies, not causal preferences; same-round samples are correlated.'}


class ActionPredicate:
    def __init__(self,spec):
        if not isinstance(spec,dict):raise ValueError('动作规则必须为对象')
        self.spec=spec;kind_=spec.get('kind')
        if kind_=='follow_honor':
            if set(spec)-{'kind','role','singleton'}:raise ValueError('未知跟切字牌参数')
            if type(spec.get('role',0)) is not int or spec.get('role',0) not in (0,1,2):raise ValueError('字牌角色为0/1/2')
            if type(spec.get('singleton',True)) is not bool:raise ValueError('singleton必须为布尔')
        elif kind_=='local':
            if set(spec)-{'kind','patterns','cut','window','reverse'}:raise ValueError('未知局部形参数')
            ps=spec.get('patterns',['14','134','124']);w=spec.get('window',[1,4]);cut=spec.get('cut',1)
            if not isinstance(ps,list) or not 1<=len(ps)<=10 or any(not isinstance(p,str) or not p.isascii() or not p.isdigit() or not 1<=len(p)<=14 for p in ps):raise ValueError('局部形应为数字字符串数组')
            if not isinstance(w,list) or len(w)!=2 or any(type(x) is not int for x in w) or not 1<=w[0]<=w[1]<=9:raise ValueError('window必须为1..9范围')
            if type(cut) is not int or not w[0]<=cut<=w[1] or any(str(cut) not in p or any(not w[0]<=int(d)<=w[1] for d in p) for p in ps):raise ValueError('局部形与切牌必须落在window内')
            if type(spec.get('reverse',False)) is not bool:raise ValueError('reverse必须为布尔')
        else:raise ValueError('动作规则支持 follow_honor 或 local；不执行自由代码或SQL')

    def available(self,f):
        legal=set(map(kind,f['legal_discards']));h=f['hand34'];s=self.spec
        if s['kind']=='follow_honor':
            seen={kind(r['tile']) for r in f['follow_honors']}
            return {i for i in legal&seen if i>=27 and f['roles'][i-27]==s.get('role',0) and (not s.get('singleton',True) or h[i]==1)}
        found=set();lo,hi=s.get('window',[1,4]);cut=s.get('cut',1)
        for base in (0,9,18):
            for reverse in (False,True) if s.get('reverse',False) else (False,):
                def ix(rank):return base+(9-rank if reverse else rank-1)
                for p in s.get('patterns',['14','134','124']):
                    if all(h[ix(rank)]==p.count(str(rank)) for rank in range(lo,hi+1)) and ix(cut) in legal:found.add(ix(cut))
        return found


def schema():
    from .call_queries import query_schema
    return {'call_query_schema':query_schema(),'version':VERSION,'query':recipe({}),'fields':sorted(KNOWN),
            'patterns':['233m','x{12}77z','111234556789m(1-5p)(1-3s)','(1m|4p|7z)','*'],
            'meld_patterns':['[p]','[c]','[999]','[999m]','[c:456s@6s]','[a]','[kakan]'],
            'win_yaku':win_projection.ALIASES,'win_codes':sorted(set(win_projection.CATALOG.values())),
            'intent':{'states':['push','mawashi','fold'],'intent_min':'0..100 percent of UNCALIBRATED model posterior; forced actions excluded','intent_mode':'online (prefix-only) or review (bounded future evidence)'},
            'supply':['7m-2','8m+1','7m-[1..2]','8m+[0..1]'],
            'shanten_bases':SHANTEN_BASES, 'decision_flags':FLAG_DEFINITIONS,
            'meld_filters':{'open_melds_min/max':'自己的明副露面子数，不含暗杠',
                            'closed_kans_min/max':'自己的暗杠数',
                            'opponents_open_min/max':'有明副露的他家人数',
                            'post_call_discard':'吃碰后直接切牌'},
            'action_predicates':{
                'follow_honor':{'kind':'follow_honor','role':0,'singleton':True},
                'local':{'kind':'local','patterns':['14','134','124'],'cut':1,'window':[1,4],'reverse':False}},
            'safety':'Read-only bounded queries; totals are complete or an error, never extrapolated from a page.'}
