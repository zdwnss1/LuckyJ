"""Four-player shanten. 0=tenpai, -1=complete; fixed melds inferred from length.

The regular-hand DFS below is adapted from MahjongRepository/mahjong,
mahjong/shanten.py at d16e68f378a7ff4457073f9eec52c5f8d965e03c (MIT).
Copyright (c) 2017 mahjong Python library contributors. See licenses/mahjong.txt.
Changes: four-player-only API, compact state updates, validation, bounded cache.
Special hands are disabled whenever a fixed meld (including ankan) exists.
"""
from functools import lru_cache

ORPHANS = (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)


from .native_accel import load
_NATIVE = load()


@lru_cache(maxsize=200_000)
def shanten(hand: tuple[int, ...]) -> int:
    n = sum(hand)
    if len(hand) != 34 or n not in (1,2,4,5,7,8,10,11,13,14) or any(type(x) is not int or not 0 <= x <= 4 for x in hand):
        raise ValueError('Invalid 34-count hand (1..14 tiles, length 3n+1 or 3n+2)')
    best = regular(hand)
    if n >= 13:
        pairs = sum(x >= 2 for x in hand)
        kinds = sum(x > 0 for x in hand)
        chiitoi = 6 - pairs + max(0, 7-kinds)
        kokushi = 13 - sum(hand[i] > 0 for i in ORPHANS) - int(any(hand[i] >= 2 for i in ORPHANS))
        best = min(best, chiitoi, kokushi)
    return best


class _Regular:
    def __init__(self, tiles):
        self.h = list(tiles)
        self.m = self.t = self.p = self.j = self.four = self.iso = 0
        self.best = 8

    def calculate(self, n):
        four = isolated = 0
        for i in range(27,34):
            count = self.h[i]
            if count == 4:
                self.m += 1; self.j += 1; four |= 1 << (i-27); isolated |= 1 << (i-27)
            elif count == 3: self.m += 1
            elif count == 2: self.p += 1
            elif count == 1: isolated |= 1 << (i-27)
        if self.j and n % 3 == 2: self.j -= 1
        if isolated:
            self.iso |= 1 << 27
            if (four | isolated) == four: self.four |= 1 << 27
        for i in range(27): self.four |= (self.h[i] == 4) << i
        self.m += (14-n)//3
        self.run(0)
        return self.best

    def branch(self, indices, field, depth):
        for i in indices: self.h[i] -= 1
        setattr(self, field, getattr(self, field)+1)
        self.run(depth)
        setattr(self, field, getattr(self, field)-1)
        for i in indices: self.h[i] += 1

    def isolated(self, k):
        self.h[k] -= 1; self.iso |= 1 << k
        self.run(k+1)
        self.h[k] += 1; self.iso &= ~(1 << k)

    def run(self, k):
        if self.best == -1: return
        while k < 27 and not self.h[k]: k += 1
        if k == 27:
            value = 8-2*self.m-self.t-self.p
            candidates = self.m+self.t
            if self.p: candidates += self.p-1
            elif self.four and self.iso and (self.four | self.iso) == self.four: value += 1
            value += max(0,candidates-4)
            if value != -1: value = max(value,self.j)
            self.best = min(self.best,value)
            return
        n = k % 9
        if self.h[k] == 4:
            self.h[k] -= 3; self.m += 1
            if n < 7 and self.h[k+2]:
                if self.h[k+1]: self.branch((k,k+1,k+2),'m',k+1)
                self.branch((k,k+2),'t',k+1)
            if n < 8 and self.h[k+1]: self.branch((k,k+1),'t',k+1)
            self.isolated(k)
            self.h[k] += 3; self.m -= 1
            self.h[k] -= 2; self.p += 1
            if n < 7 and self.h[k+2]:
                if self.h[k+1]: self.branch((k,k+1,k+2),'m',k)
                self.branch((k,k+2),'t',k+1)
            if n < 8 and self.h[k+1]: self.branch((k,k+1),'t',k+1)
            self.h[k] += 2; self.p -= 1
        if self.h[k] == 3:
            self.branch((k,k,k),'m',k+1)
            self.h[k] -= 2; self.p += 1
            if n < 7 and self.h[k+1] and self.h[k+2]: self.branch((k,k+1,k+2),'m',k+1)
            else:
                if n < 7 and self.h[k+2]: self.branch((k,k+2),'t',k+1)
                if n < 8 and self.h[k+1]: self.branch((k,k+1),'t',k+1)
            self.h[k] += 2; self.p -= 1
            if n < 7 and self.h[k+1] >= 2 and self.h[k+2] >= 2:
                for i in (k,k+1,k+2): self.h[i] -= 2
                self.m += 2; self.run(k); self.m -= 2
                for i in (k,k+1,k+2): self.h[i] += 2
        if self.h[k] == 2:
            self.branch((k,k),'p',k+1)
            if n < 7 and self.h[k+1] and self.h[k+2]: self.branch((k,k+1,k+2),'m',k)
        if self.h[k] == 1:
            if n < 6 and self.h[k+1] == 1 and self.h[k+2] and self.h[k+3] != 4:
                self.branch((k,k+1,k+2),'m',k+2)
            else:
                self.isolated(k)
                if n < 7 and self.h[k+2]:
                    if self.h[k+1]: self.branch((k,k+1,k+2),'m',k+1)
                    self.branch((k,k+2),'t',k+1)
                if n < 8 and self.h[k+1]: self.branch((k,k+1),'t',k+1)


def regular(hand):
    return _NATIVE(bytes(hand)) if _NATIVE is not None else _Regular(hand).calculate(sum(hand))
