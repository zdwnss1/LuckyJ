"""Deterministic pre-discard facts and counterfactual discard analysis.

No hidden hands, future dora or outcomes are used. `remaining` means unseen
from LuckyJ's current information set, not an omniscient live-wall count.
"""
from __future__ import annotations
from collections import Counter
from functools import lru_cache
from .tiles import kind, token, normal
from .shanten import shanten, regular

VERSION = 'research-1.1'
GOOD_RULE = 'shortest-ryanmen-v1'
TOKENS = tuple(f'{i%9+1}{"mpsz"[i//9]}' for i in range(34)) + ('0m','0p','0s')
INDEX = {t:i for i,t in enumerate(TOKENS)}


def counts(tiles):
    c = [0]*34
    for t in tiles: c[kind(t)] += 1
    return tuple(c)


def roles(wind, seat_wind):
    return tuple((int(i == wind)+int(i == seat_wind)) if i < 4 else 1 for i in range(7))


def dora_type(t):
    i = kind(t)
    if i < 27: return i//9*9+(i%9+1)%9
    if i < 31: return 27+(i-27+1)%4
    return 31+(i-31+1)%3


def facts(s, aka=True):
    """37 slots separate red/ordinary fives; 34 slots combine them."""
    public = []
    for river in s['rivers']:
        public.extend(r['tile'] for r in river if r.get('called_by') is None)
    for melds in s['melds']:
        for m in melds: public.extend(m['tiles'])
    public.extend(s['dora_indicators'])
    hand37 = [0]*37; loss37 = [0]*37
    for t in s['hand']: hand37[INDEX[t]] += 1
    for t in public: loss37[INDEX[t]] += 1
    total = [4]*34+[int(aka)]*3
    for i in (4,13,22): total[i] = 3 if aka else 4
    remaining37 = [a-b-c for a,b,c in zip(total,hand37,loss37)]
    if any(x < 0 for x in remaining37):
        raise ValueError('Visible tile counts exceed supply; refuse inconsistent snapshot')
    hand34 = list(counts(s['hand'])); loss34 = list(counts(public))
    remaining34 = [4-a-b for a,b in zip(hand34,loss34)]
    dora = [0]*34
    for t in s['dora_indicators']: dora[dora_type(t)] += 1
    own = list(s['hand'])
    for m in s['melds'][0]: own.extend(m['tiles'])
    return {'hand34':hand34, 'hand37':hand37, 'loss34':loss34, 'loss37':loss37,
            'remaining34':remaining34, 'remaining37':remaining37, 'dora34':dora,
            'dora_count':sum(dora[kind(t)] for t in own),
            'red_count':sum(t.startswith('0') for t in own),
            'opponent_riichi':sum(s['riichi'][1:]), 'self_riichi':bool(s['riichi'][0])}


def legal_discards(s):
    """Discard candidates only; not a win/kan/riichi action recommender."""
    available = sorted(set(s['hand']), key=lambda t: (kind(t),t))
    if s['riichi'][0]:
        return [s['draw']] if s.get('draw') else []
    forbidden = set()
    if s.get('draw') is None and s['melds'][0]:
        m = s['melds'][0][-1]
        if m['kind'] in ('chi','pon'):
            called = kind(m['called']); forbidden.add(called)
            if m['kind'] == 'chi':
                lo, hi = min(map(kind,m['tiles'])), max(map(kind,m['tiles']))
                if called == lo and lo%9 <= 5: forbidden.add(lo+3)
                if called == hi and lo%9 >= 1: forbidden.add(lo-1)
    result = [t for t in available if kind(t) not in forbidden]
    if s.get('riichi_pending',[False]*4)[0]:
        h=list(counts(s['hand']))
        result=[t for t in result if after_shanten(tuple(h),kind(t))==0]
    return result


@lru_cache(maxsize=100_000)
def after_shanten(hand, tile):
    h=list(hand);h[tile]-=1
    return shanten(tuple(h))


@lru_cache(maxsize=150_000)
def effective(hand):
    """Structural improving tile types, including zero-live types for inspection."""
    current=shanten(hand); result=[]
    for t,n in enumerate(hand):
        if n >= 4: continue
        h=list(hand);h[t]+=1
        if shanten(tuple(h)) < current: result.append(t)
    return tuple(result)


@lru_cache(maxsize=40_000)
def meldable(hand):
    if not any(hand): return True
    i=next(i for i,n in enumerate(hand) if n)
    h=list(hand)
    if h[i]>=3:
        h[i]-=3
        if meldable(tuple(h)): return True
        h[i]+=3
    if i<27 and i%9<7 and h[i+1] and h[i+2]:
        for j in (i,i+1,i+2):h[j]-=1
        if meldable(tuple(h)):return True
    return False


@lru_cache(maxsize=50_000)
def ryanmen_waits(hand):
    """Exact existence of 3m+2 complete remainder plus a 23..78 two-sided block.
    A fifth-copy endpoint is excluded. No yaku/furiten claim is made.
    """
    waits=set()
    for base in range(0,27,9):
        for low in range(base+1,base+7):
            if not hand[low] or not hand[low+1]: continue
            h=list(hand);h[low]-=1;h[low+1]-=1
            for pair,n in enumerate(h):
                if n<2:continue
                h[pair]-=2
                ok=meldable(tuple(h))
                h[pair]+=2
                if ok:
                    for t in (low-1,low+2):
                        if hand[t]<4:waits.add(t)
                    break
    return tuple(sorted(waits))


class AnalysisBudgetExceeded(RuntimeError):
    pass


class GoodSearch:
    """Tri-state bounded exact search: never label an unfinished proof as false.

    Goal: a standard ryanmen tenpai reachable using only shanten-reducing draws.
    Future availability is not presumed known; immediate tile weights are real.
    """
    def __init__(self,budget=12_000):
        self.left=budget; self.memo={}

    def reachable(self,hand):
        if hand in self.memo:return self.memo[hand]
        self.left-=1
        if self.left<0:raise AnalysisBudgetExceeded('good-shape proof budget exceeded')
        s=shanten(hand)
        if s==0:answer=bool(ryanmen_waits(hand))
        elif regular(hand) > s: answer=False
        else:
            answer=False
            for t in effective(hand):
                drawn=list(hand);drawn[t]+=1
                for d,n in enumerate(drawn):
                    if not n:continue
                    drawn[d]-=1; nxt=tuple(drawn);drawn[d]+=1
                    if shanten(nxt)==s-1 and self.reachable(nxt):
                        answer=True;break
                if answer:break
        self.memo[hand]=answer
        return answer

    def improving_is_good(self,hand,t):
        s=shanten(hand)
        if s==0:return t in ryanmen_waits(hand)
        drawn=list(hand);drawn[t]+=1
        for d,n in enumerate(drawn):
            if not n:continue
            drawn[d]-=1;nxt=tuple(drawn);drawn[d]+=1
            if shanten(nxt)==s-1 and self.reachable(nxt):return True
        return False


@lru_cache(maxsize=16_000)
def structure(hand, good_budget=12_000, only_tile=None):
    search=GoodSearch(good_budget); rows={}
    for t,n in enumerate(hand):
        if not n or (only_tile is not None and t != only_tile):continue
        h=list(hand);h[t]-=1;h=tuple(h)
        e=effective(h); good={}
        for draw in e:
            try:good[draw]=search.improving_is_good(h,draw)
            except AnalysisBudgetExceeded:good[draw]=None
        rows[t]={'shanten':shanten(h),'effective':e,'good':good}
    return rows


def analyze(s, aka=True, good_budget=12_000, only_actual=False):
    f=facts(s,aka); hand=tuple(f['hand34']); legal=legal_discards(s)
    if not legal:raise ValueError('No legal discards at this snapshot')
    if s['discard'] not in legal:raise ValueError('Recorded discard is outside reconstructed legal set')
    if only_actual:legal=[s['discard']]
    core=structure(hand,good_budget,kind(s['discard']) if only_actual else None); choices=[]
    for tile in legal:
        t=kind(tile);r=core[t]; e=r['effective'];g=r['good']; incoming=[]
        for draw in e:
            pieces=[{'tile':TOKENS[i],'remaining':f['remaining37'][i]} for i in range(37) if kind(TOKENS[i])==draw]
            incoming.append({'tile':TOKENS[draw], 'remaining':f['remaining34'][draw],
                             'loss':f['loss34'][draw], 'good':g[draw], 'copies':pieces})
        unknown=sum(f['remaining34'][x] for x in e if g[x] is None)
        proven=sum(f['remaining34'][x] for x in e if g[x] is True)
        choices.append({'tile':tile,'selected':tile==s['discard'], 'shanten':r['shanten'],
                        'ukeire':sum(f['remaining34'][x] for x in e),
                        'ukeire_kinds':sum(f['remaining34'][x]>0 for x in e),
                        'structural_kinds':len(e),'good_ukeire':None if unknown else proven,
                        'good_lower_bound':proven,'good_upper_bound':proven+unknown,
                        'good_status':'budget_exceeded' if unknown else 'exact', 'incoming':incoming})
    if not any(c['selected'] for c in choices):raise ValueError('Recorded discard is outside reconstructed legal set')
    minimum=min(c['shanten'] for c in choices)
    best_u=max(c['ukeire'] for c in choices if c['shanten']==minimum)
    pool=[c for c in choices if c['shanten']==minimum]
    good_known=all(c['good_ukeire'] is not None for c in pool)
    best_g=max(c['good_ukeire'] for c in pool) if good_known else None
    for c in choices:
        c['min_shanten']=c['shanten']==minimum
        c['max_ukeire']=c['min_shanten'] and c['ukeire']==best_u
        c['ukeire_gap']=best_u-c['ukeire'] if c['min_shanten'] else None
        c['max_good']=c['min_shanten'] and c['good_ukeire']==best_g if good_known else None
    if only_actual:
        for c in choices:
            for key in ('min_shanten','max_ukeire','ukeire_gap','max_good'):c[key]=None
    return {'scope':'actual' if only_actual else 'all_legal', 'version':VERSION,'good_rule':GOOD_RULE,'good_proof_budget':good_budget,'good_future_availability':'structural-only',
            'shanten_current':shanten(hand),'best_after_shanten':None if only_actual else minimum,'best_ukeire':None if only_actual else best_u,
            'best_good':None if only_actual else best_g,'max_ukeire_ties':None if only_actual else sum(c['max_ukeire'] is True for c in choices),
            'max_good_ties':sum(c['max_good'] is True for c in choices) if good_known and not only_actual else None,
            'selected':next(c for c in choices if c['selected']), 'choices':choices,
            'availability':f, 'safety_note':'Structural efficiency, not yaku/furiten/EV or omniscient wall knowledge.'}
