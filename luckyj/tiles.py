"""Unambiguous 136/34 tile representations and human input notation."""
from __future__ import annotations
import re
from collections import Counter

RED_IDS = {16, 52, 88}


def token(tile: int, aka: bool = True) -> str:
    if not 0 <= tile < 136:
        raise ValueError(f'Invalid physical tile: {tile}')
    kind = tile // 4
    suit = 'mpsz'[kind // 9]
    number = 0 if aka and tile in RED_IDS else kind % 9 + 1
    return f'{number}{suit}'


def kind(tile: str) -> int:
    return 'mpsz'.index(tile[1]) * 9 + (5 if tile[0] == '0' else int(tile[0])) - 1


def normal(tile: str) -> str:
    return tile.replace('0', '5')


def parse(text: str, *, sequence: bool = False) -> list[str]:
    """123m05p123s11z. In sequence mode preserve order; repeats are unlimited."""
    text = re.sub(r'\s+', '', text.lower())
    if len(text) > 180:
        raise ValueError('牌串过长（最多 180 字符）')
    groups = list(re.finditer(r'([0-9]+)([mpsz])', text))
    if ''.join(m.group(0) for m in groups) != text:
        raise ValueError('牌串格式应为 123m05p789s11z；字牌为 1z–7z')
    tiles = []
    for match in groups:
        digits, suit = match.groups()
        for digit in digits:
            if suit == 'z' and digit not in '1234567':
                raise ValueError('字牌只能使用 1z–7z')
            tiles.append(digit + suit)
    if not sequence:
        counts = Counter(map(normal, tiles))
        if any(v > 4 for v in counts.values()) or len(tiles) > 14:
            raise ValueError('手牌最多 14 张，同种牌最多 4 张')
        if any(tiles.count('0' + s) > 1 for s in 'mps'):
            raise ValueError('本语法每门最多一张赤五')
    elif len(tiles) > 40:
        raise ValueError('切牌序列最多 40 张')
    return tiles


def ranks(scores: list[int], initial_dealer: int) -> list[int]:
    """Descending score, ties use initial east/south/west/north order."""
    seats = sorted(range(4), key=lambda i: (-scores[i], (i - initial_dealer) % 4))
    result = [0] * 4
    for rank, seat in enumerate(seats, 1):
        result[seat] = rank
    return result


def meld(code: int, who: int) -> dict:
    """Decode Tenhou's compact meld representation; retain physical identities."""
    if code < 0 or not 0 <= who < 4:
        raise ValueError('Invalid meld code/seat')
    relative = code & 3
    source = (who + relative) % 4
    if code & 4:
        value = (code >> 10) & 63
        called = value % 3
        base = value // 3
        base = base // 7 * 9 + base % 7
        tiles = [(base + i) * 4 + ((code >> (3 + 2 * i)) & 3) for i in range(3)]
        kind_ = 'chi'
    elif code & 24:
        excluded = (code >> 5) & 3
        value = (code >> 9) & 127
        called = value % 3
        base = value // 3
        tiles = [base * 4 + i for i in range(4) if i != excluded]
        kind_ = 'pon' if code & 8 else 'kakan'
        if kind_ == 'kakan':
            tiles.append(base * 4 + excluded)
    elif code & 32:
        raise ValueError('Sanma/nuki is not supported by the four-player parser')
    else:
        value = code >> 8
        base, called = divmod(value, 4)
        tiles = [base * 4 + i for i in range(4)]
        kind_ = 'ankan' if relative == 0 else 'daiminkan'
    if any(not 0 <= t < 136 for t in tiles):
        raise ValueError('Meld tile outside 0..135')
    return {'kind': kind_, 'who': who, 'source': source, 'tiles136': tiles,
            'called136': None if kind_ == 'ankan' else tiles[called], 'code': code}
