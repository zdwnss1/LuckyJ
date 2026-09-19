"""Composite concealed-hand and declared-meld constraints, under ONE mapping.

[p]=pon, [c]=chi, [k]=any kan, [a]=ankan, [d]=daiminkan,
[kakan]=added kan, [999]=a suited nine pon, [999s]=nine-sou pon,
[c:456s@6s]=chi 456s called on 6s. Brackets are unordered distinct slots.
A bare concealed digit run is shorthand for query m; digits in brackets
without a suit match one suited family, never three independently chosen suits.
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import re
import time
from .analysis import INDEX, TOKENS, roles
from .tiles import kind
from .patterns import (Matcher as HandMatcher, Pattern, parse_pattern, one_tile,
                       map_token, remap37, _suit_maps, _honor_maps, normal_token)

KINDS = {'p': ('pon',), 'pon': ('pon',), 'c': ('chi',), 'chi': ('chi',),
         'k': ('ankan', 'daiminkan', 'kakan'), 'kan': ('ankan', 'daiminkan', 'kakan'),
         'a': ('ankan',), 'ankan': ('ankan',), 'd': ('daiminkan',),
         'daiminkan': ('daiminkan',), 'kakan': ('kakan',),
         '*': ('chi', 'pon', 'ankan', 'daiminkan', 'kakan')}


@dataclass(frozen=True)
class MeldSlot:
    kinds: tuple[str, ...]
    alternatives: tuple[tuple[str, ...], ...] = ()
    called: str | None = None

    @property
    def honors(self):
        return {kind(t)-27 for a in self.alternatives for t in a if kind(t) >= 27} | (
            {kind(self.called)-27} if self.called and kind(self.called) >= 27 else set())

    def accepts(self, meld, mapping, red):
        if meld['kind'] not in self.kinds:
            return False
        actual = [map_token(t, mapping) for t in meld['tiles']]
        if self.called:
            if not meld.get('called'):
                return False
            x = map_token(meld['called'], mapping)
            if (x != self.called if red or self.called.startswith('0') else kind(x) != kind(self.called)):
                return False
        if not self.alternatives:
            return True
        for wanted in self.alternatives:
            if len(wanted) != len(actual):
                continue
            # Literal red remains an explicit condition even when ordinary 5 is merged.
            if red:
                if Counter(wanted) == Counter(actual): return True
            elif Counter(map(kind, wanted)) == Counter(map(kind, actual)) and all(
                    actual.count('0'+s) >= wanted.count('0'+s) for s in 'mps'):
                return True
        return False


def parse_slot(text):
    text = re.sub(r'\s+', '', text.lower())
    if text in KINDS:
        return MeldSlot(KINDS[text])
    pieces = text.split('@')
    if len(pieces) > 2: raise ValueError('每个副露只允许一个 @鸣入牌')
    text = pieces[0]; called = pieces[1] if len(pieces) == 2 else None
    if called: one_tile(called)
    specified = None
    if ':' in text:
        tag, text = text.split(':', 1)
        if tag not in KINDS: raise ValueError('副露类型使用 p/c/k/a/d/kakan')
        specified = KINDS[tag]
    if re.fullmatch(r'[0-9]{3,4}', text):
        variants = [tuple(d+s for d in text) for s in 'mps']
    else:
        p = parse_pattern(text, red=True)
        if any(len(x) != 1 for x in p.slots): raise ValueError('具体副露必须列出完整牌；不限牌种请用 [p] 或 [c]')
        variants = [tuple(TOKENS[x[0]] for x in p.slots)]
    implied = None
    for variant in variants:
        for t in variant: one_tile(t)
        nums = sorted(map(kind, variant))
        if len(nums) == 3 and len(set(nums)) == 1: ks = ('pon',)
        elif len(nums) == 4 and len(set(nums)) == 1: ks = ('ankan', 'daiminkan', 'kakan')
        elif len(nums) == 3 and nums[0] < 27 and nums[0]//9 == nums[-1]//9 and nums == list(range(nums[0], nums[0]+3)):
            ks = ('chi',)
        else: raise ValueError('副露必须是顺子、刻子或四张同牌；[p]/[c] 表示未知具体牌')
        if any(variant.count('0'+s) > 1 for s in 'mps'): raise ValueError('副露不能包含两张同门赤五')
        if called and kind(called) not in nums: raise ValueError('@鸣入牌必须属于该副露')
        implied = ks
    kinds = tuple(k for k in (specified or implied) if k in implied)
    if not kinds: raise ValueError('副露类型与牌面矛盾')
    if called and kinds == ('ankan',): raise ValueError('暗杠没有他家鸣入牌')
    return MeldSlot(kinds, tuple(variants), called)


def split_composite(text):
    if len(text) > 600: raise ValueError('复合牌形过长')
    brackets = re.findall(r'\[([^\[\]]*)\]', text)
    rest = re.sub(r'\[[^\[\]]*\]', '', text).strip()
    if '[' in rest or ']' in rest: raise ValueError('副露方括号未配对或嵌套')
    if len(brackets) > 4: raise ValueError('最多四组固定面子')
    compact = re.sub(r'\s+', '', rest)
    if re.fullmatch(r'[0-9]+', compact): rest = compact+'m'
    return rest, tuple(parse_slot(x) for x in brackets)


class Matcher(HandMatcher):
    def __init__(self, q):
        text, self.meld_slots = split_composite(q.get('hand', ''))
        self.meld_mode = q.get('meld_mode', 'contains')
        if self.meld_mode not in ('contains', 'exact'): raise ValueError('meld_mode必须为contains或exact')
        super().__init__({**q, 'hand': text})
        if len(self.pattern.slots) + 3*len(self.meld_slots) > 14:
            raise ValueError('暗手约束张数 + 3×固定面子数不能超过14；一副露时切牌前暗手最多11张')
        self.anchors = tuple(sorted(set(self.anchors) | {h for m in self.meld_slots for h in m.honors}))
        self.has_constraints = self.has_constraints or bool(self.meld_slots) or self.meld_mode == 'exact'

    def melds_accept(self, melds, mapping):
        if len(melds) < len(self.meld_slots) or (self.meld_mode == 'exact' and len(melds) != len(self.meld_slots)):
            return False
        choices = [[i for i,m in enumerate(melds) if slot.accepts(m, mapping, self.red)] for slot in self.meld_slots]
        choices.sort(key=len)
        def assign(j, used):
            return j == len(choices) or any(i not in used and assign(j+1, used|{i}) for i in choices[j])
        return assign(0, set())

    def matches(self, hand, f, wind, seat_wind, draw=None, discard=None, deadline=None):
        if not self.meld_slots and self.meld_mode != 'exact':
            return super().matches(hand,f,wind,seat_wind,draw,discard,deadline)
        melds = f.get('own_melds')
        if melds is None: raise ValueError('索引缺少副露牌面，请重新 enrich；不将未知副露当空副露')
        source_roles=roles(wind,seat_wind)
        accepted=0;chosen=None;possible=set();attempted=0
        for sm in _suit_maps(self.exchange,self.reverse):
            for hm in _honor_maps(self.anchors,source_roles,self.target_roles,self.mode):
                attempted+=1
                if deadline and attempted%64==1 and time.monotonic()>deadline: raise TimeoutError('副露同形匹配超时，未返回部分统计')
                mapping=sm+hm
                if not self.melds_accept(melds,mapping): continue
                def eq(actual,wanted):
                    if actual is None: return False
                    a=map_token(actual,mapping)
                    return a==wanted if self.red or wanted.startswith('0') else kind(a)==kind(wanted)
                if self.draw and not eq(draw,self.draw): continue
                if self.discard and not eq(discard,self.discard): continue
                if not self.pattern.accepts(tuple(remap37(hand,mapping))): continue
                inverse={t:i for i,t in enumerate(mapping)}
                ok=True
                for c in self.supply:
                    src=inverse[kind(c.tile)]
                    if c.tile.startswith('0'): value=f[c.source+'37'][34+src//9]
                    elif self.red and kind(c.tile) in (4,13,22): value=f[c.source+'37'][src]
                    else: value=f[c.source+'34'][src]
                    if not c.low<=value<=c.high: ok=False;break
                if not ok or any(f['dora34'][inverse[t]]!=n for t,n in self.dora): continue
                accepted+=1
                if chosen is None or mapping<chosen: chosen=mapping
                if discard:
                    d=map_token(discard,mapping)
                    if kind(discard)>=27 and self.mode!='identity' and kind(d)-27 not in self.anchors:
                        possible.add(('非役字','单役字','双风')[source_roles[kind(discard)-27]]+'（未锚定）')
                    else: possible.add(d if self.red else normal_token(d))
        return ({'map':list(chosen),'possible_discards':sorted(possible),'mapping_count':accepted,
                 'anchored':True,'honor_anchors':list(self.anchors),'meld_slots':len(self.meld_slots)} if accepted else None)
