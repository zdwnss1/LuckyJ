"""Capacity-aware partial tile patterns with one consistent mapping per record.

Numbers never translate. Suit reversal is opt-in. Honor identities may rename
individually, injectively, while preserving the configured query-slot role.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import permutations, product
from functools import lru_cache
import re
import time
from .analysis import TOKENS, INDEX, roles
from .tiles import kind

ALL = tuple(range(37))
DEFAULT_ROLES = (1,0,0,0,1,1,1)


def one_tile(t):
    if t not in INDEX:raise ValueError(f'无效牌 {t!r}，使用 1m..9s、1z..7z、0m/0p/0s')
    return INDEX[t]


def domain(t, red):
    i=one_tile(t)
    if not red and i in (4,13,22):return (i,34+(i-4)//9)
    return (i,)


@dataclass(frozen=True)
class Pattern:
    slots: tuple[tuple[int,...], ...]
    honors: tuple[int,...]

    def accepts(self, hand):
        if sum(hand)<len(self.slots):return False
        fixed=[0]*37;remaining=[]
        for slot in self.slots:
            if len(slot)==1:fixed[slot[0]]+=1
            else:remaining.append(slot)
        h=tuple(a-b for a,b in zip(hand,fixed))
        if min(h)<0:return False
        # Repeated wildcards consume distinct copies. Overlapping domains use
        # a capacity allocation, NOT independent "contains" predicates.
        constrained=tuple(sorted((s for s in remaining if s!=ALL),key=len))
        # Bipartite augmenting paths: each concealed copy can satisfy one slot.
        # At most 14 copies, so even adversarial overlapping ranges are bounded.
        copies=[i for i,n in enumerate(h) for _ in range(n)]
        owner=[-1]*len(copies)
        def augment(slot,seen):
            for copy,t in enumerate(copies):
                if copy in seen or t not in constrained[slot]:continue
                seen.add(copy)
                if owner[copy]<0 or augment(owner[copy],seen):
                    owner[copy]=slot;return True
            return False
        return all(augment(i,set()) for i in range(len(constrained)))



def parse_pattern(text, red=False):
    text=re.sub(r'[\s,，]+','',text.lower())
    if text in ('','*'):return Pattern((),())
    if len(text)>300:raise ValueError('牌形过长')
    slots=[];honors=set();literals=[];pos=0
    def append(tokens, is_literal=False):
        allowed=set()
        for t in tokens:
            allowed.update(domain(t,red))
            if t.endswith('z'):honors.add(int(t[0])-1)
        if not allowed:raise ValueError('空的牌范围')
        slots.append(tuple(sorted(allowed)))
        if is_literal:literals.append(tokens[0])
    while pos<len(text):
        if text[pos]=='x':
            m=re.match(r'x(?:\{([0-9]+)\})?',text[pos:]);count=int(m[1] or 1)
            if not 1<=count<=14:raise ValueError('通配张数必须为1..14')
            slots.extend([ALL]*count);pos+=len(m[0]);continue
        if text[pos]=='(':
            end=text.find(')',pos)
            if end<0:raise ValueError('范围缺少右括号')
            inner=text[pos+1:end]
            m=re.fullmatch(r'([0-9])-([0-9])([mpsz])',inner)
            if m:
                lo,hi=int(m[1]),int(m[2]);suit=m[3]
                if lo>hi:raise ValueError('牌范围下限大于上限')
                append([str(i)+suit for i in range(lo,hi+1)])
            else:
                ts=inner.split('|')
                if any(not re.fullmatch(r'[0-9][mpsz]',t) for t in ts):raise ValueError('范围使用 (1-5p) 或 (1m|4p|7z)')
                append(ts)
            pos=end+1;continue
        m=re.match(r'([0-9]+)([mpsz])',text[pos:])
        if not m:raise ValueError(f'无法解析牌形第{pos+1}字符；支持 123m、x{{12}}、(1-5p)')
        for digit in m[1]:append([digit+m[2]],True)
        pos+=len(m[0])
    if len(slots)>14:raise ValueError('最多14张暗手，x和每个范围都占一张')
    counts=[0]*34
    for t in literals:counts[kind(t)]+=1
    if max(counts)>4 or any(literals.count('0'+s)>1 for s in 'mps'):
        raise ValueError('固定牌超过实体牌供给')
    return Pattern(tuple(slots),tuple(sorted(honors)))


@dataclass(frozen=True)
class SupplyConstraint:
    tile: str
    source: str
    low: int
    high: int


def parse_supply(text):
    text=re.sub(r'[\s,，]+','',text.lower());pos=0;result=[]
    if len(text)>500:raise ValueError('损存条件过长')
    while pos<len(text):
        m=re.match(r'([0-9][mpsz])([+-])(?:([0-4])|\[([0-4])\.\.([0-4])\])',text[pos:])
        if not m:raise ValueError('损存格式：7m-2 8m+1 7m-[1..2]；+表示存，-表示手外已见')
        one_tile(m[1]);lo=int(m[3] if m[3] is not None else m[4]);hi=int(m[3] if m[3] is not None else m[5])
        if lo>hi:raise ValueError('损存下限大于上限')
        result.append(SupplyConstraint(m[1],'remaining' if m[2]=='+' else 'loss',lo,hi));pos+=len(m[0])
    return tuple(result)


def parse_dora(text):
    result=[]
    for item in re.split(r'[\s,，]+',text.strip().lower()):
        if not item:continue
        m=re.fullmatch(r'([1-9][mpsz])(?::([1-5]))?',item)
        if not m:raise ValueError('宝牌位置用 3m 或 3m:2（重数），不是指示牌')
        one_tile(m[1]);result.append((kind(m[1]),int(m[2] or 1)))
    return tuple(result)


@lru_cache(maxsize=128)
def _suit_maps(exchange,reverse):
    result=[]
    for p in permutations(range(3)) if exchange else [(0,1,2)]:
        for flips in product((False,True),repeat=3) if reverse else [(False,)*3]:
            result.append(tuple(p[i//9]*9+(8-i%9 if flips[i//9] else i%9) for i in range(27)))
    return tuple(result)


@lru_cache(maxsize=1024)
def _honor_maps(anchors, source_roles, target_roles, mode):
    if mode=='identity':return (tuple(range(27,34)),)
    result=[]
    def recurse(j,assigned,used):
        if j==len(anchors):
            # Completion is for deterministic display only. Unanchored honors
            # never acquire a concrete statistical identity from this choice.
            m=[None]*7
            for dest,src in assigned.items():m[src]=27+dest
            unused=set(range(7))-set(assigned)
            for src in range(7):
                if m[src] is None and src in unused:m[src]=27+src;unused.remove(src)
            for src in range(7):
                if m[src] is None:
                    dest=min(unused);unused.remove(dest);m[src]=27+dest
            result.append(tuple(m));return
        dest=anchors[j]
        for src in range(7):
            if src not in used and (mode=='any' or source_roles[src]==target_roles[dest]):
                assigned[dest]=src;recurse(j+1,assigned,used|{src});del assigned[dest]
    recurse(0,{},set())
    return tuple(result)


def map_token(t,mapping):
    i=kind(t);dest=mapping[i]
    if t.startswith('0'):return '0'+'mps'[dest//9]
    return TOKENS[dest]


def remap37(values,mapping):
    out=[0]*37
    for i,n in enumerate(values):out[INDEX[map_token(TOKENS[i],mapping)]]+=n
    return out


class Matcher:
    def __init__(self,q):
        self.exchange=q.get('suits','1')=='1';self.reverse=q.get('reverse','0')=='1'
        self.red=q.get('red','0')=='1';self.mode=q.get('honors','roles')
        if self.mode not in ('roles','identity','any'):raise ValueError('字牌对称为 roles/identity/any')
        for key in ('suits','reverse','red','dora_position'):
            if q.get(key,'0') not in ('0','1'):raise ValueError(f'{key} 必须为0或1')
        try:self.target_roles=tuple(int(x) for x in q.get('honor_roles','1,0,0,0,1,1,1').split(','))
        except ValueError:raise ValueError('字牌角色应为七个0/1/2') from None
        if len(self.target_roles)!=7 or any(x not in (0,1,2) for x in self.target_roles):raise ValueError('字牌角色应为七个0/1/2')
        self.pattern=parse_pattern(q.get('hand',''),self.red)
        self.supply=parse_supply(q.get('supply',''))
        self.dora=parse_dora(q.get('dora_tiles',''))
        if self.dora and q.get('dora_position')!='1':raise ValueError('填写宝牌位置时须开启位置约束')
        self.draw=q.get('draw','');self.discard=q.get('discard','')
        for t in (self.draw,self.discard):
            if t:one_tile(t)
        refs=[c.tile for c in self.supply]+[TOKENS[i] for i,_ in self.dora]+[t for t in (self.draw,self.discard) if t]
        self.anchors=tuple(sorted(set(self.pattern.honors)|{kind(t)-27 for t in refs if kind(t)>=27}))
        self.has_constraints=bool(self.pattern.slots or self.supply or self.dora or self.draw or self.discard)

    def matches(self,hand,f,wind,seat_wind,draw=None,discard=None,deadline=None):
        source_roles=roles(wind,seat_wind)
        if not self.has_constraints:
            if discard is None:labels=[]
            elif kind(discard)>=27 and self.mode!='identity':
                labels=[('非役字','单役字','双风')[source_roles[kind(discard)-27]]+'（未锚定）']
            else:
                labels=sorted({map_token(discard,sm+tuple(range(27,34))) for sm in _suit_maps(self.exchange,self.reverse)})
                if not self.red:labels=sorted(set(map(normal_token,labels)))
            return {'map':list(range(34)),'possible_discards':labels,
                    'mapping_count':len(_suit_maps(self.exchange,self.reverse)),'anchored':False,'honor_anchors':[]}
        good_count=0;possible=set();chosen=None;attempted=0
        suit_maps=_suit_maps(self.exchange,self.reverse)
        honor_maps=_honor_maps(self.anchors,source_roles,self.target_roles,self.mode)
        for sm in suit_maps:
            for hm in honor_maps:
                attempted+=1
                if deadline is not None and attempted%64==1 and time.monotonic()>deadline:
                    raise TimeoutError('牌形匹配超过预算，没有输出部分计数')
                mapping=sm+hm
                def eq(actual,wanted):
                    if actual is None:return False
                    a=map_token(actual,mapping)
                    return a==wanted if self.red or wanted.startswith('0') else kind(a)==kind(wanted)
                if self.draw and not eq(draw,self.draw):continue
                if self.discard and not eq(discard,self.discard):continue
                if not self.pattern.accepts(tuple(remap37(hand,mapping))):continue
                inverse={t:i for i,t in enumerate(mapping)}
                ok=True
                for c in self.supply:
                    source=inverse[kind(c.tile)]
                    if c.tile.startswith('0'):
                        value=f[c.source+'37'][34+source//9]
                    elif self.red and kind(c.tile) in (4,13,22):value=f[c.source+'37'][source]
                    else:value=f[c.source+'34'][source]
                    if not c.low<=value<=c.high:ok=False;break
                if not ok:continue
                if any(f['dora34'][inverse[t]]!=count for t,count in self.dora):continue
                good_count+=1
                if chosen is None or mapping<chosen:chosen=mapping
                if discard:
                    d=map_token(discard,mapping)
                    if kind(discard)>=27 and self.mode!='identity' and kind(d)-27 not in self.anchors:
                        possible.add(('非役字','单役字','双风')[source_roles[kind(discard)-27]]+'（未锚定）')
                    else:possible.add(d if self.red else normal_token(d))
        if not good_count:return None
        return {'map':list(chosen),'possible_discards':sorted(possible),
                'mapping_count':good_count,'anchored':True,'honor_anchors':list(self.anchors)}


def normal_token(t):
    return '5'+t[1] if t.startswith('0') else t
