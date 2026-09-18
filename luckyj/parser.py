"""Replay complete Tenhou XML logs to pre-discard public-state snapshots."""
from __future__ import annotations
import copy
import re
import urllib.parse
import xml.etree.ElementTree as ET
from .tiles import meld, token, ranks

VERSION = '1.0.0'
TARGET = '\u24ddLuckyJ'
MOVE = re.compile(r'^([TUVWDEFGtuvwdefg])(\d+)$')


def ints(value: str) -> list[int]:
    return [int(v) for v in value.split(',')]


def parse_game(raw: bytes, log_id: str, target: str = TARGET) -> dict:
    if len(raw) > 8_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Oversized or unsafe XML')
    root = ET.fromstring(raw)
    if root.tag != 'mjloggm':
        raise ValueError('Expected mjloggm XML')
    names = ['', '', '', '']
    go = None
    for e in root:
        if e.tag == 'GO':
            go = int(e.attrib['type'])
        if e.tag == 'UN':
            for i in range(4):
                if 'n' + str(i) in e.attrib:
                    names[i] = urllib.parse.unquote(e.attrib['n' + str(i)])
    if names.count(target) != 1:
        raise ValueError(f'Expected exactly one {target!r}; got {names!r}')
    if go is None or go & 16:
        raise ValueError('Missing GO metadata or unsupported three-player game')
    aka = not bool(go & 2)
    actor = names.index(target)
    order = [(actor + i) % 4 for i in range(4)]
    game = {'log_id': log_id, 'names': names, 'actor': actor, 'game_type': go, 'aka': aka,
            'parser_version': VERSION, 'rounds': [], 'decisions': []}
    state = None
    completed = False
    initial_dealer = None
    for event_seq, e in enumerate(root):
        a, tag = e.attrib, e.tag
        if tag == 'INIT':
            if state is not None and not state['ended']:
                raise ValueError(f'Round ended without result at event {event_seq}')
            seed = ints(a['seed'])
            kyoku, honba, sticks = seed[:3]
            dealer = int(a['oya'])
            hands = [ints(a[f'hai{i}']) for i in range(4)]
            scores = [v * 100 for v in ints(a['ten'])]
            physical = [t for hand in hands for t in hand] + [seed[5]]
            if len(scores) != 4 or any(len(h) != 13 for h in hands) or len(set(physical)) != 53 or any(not 0 <= t < 136 for t in physical):
                raise ValueError('Invalid initial hands/scores/dora')
            if initial_dealer is None:
                if kyoku != 0:
                    raise ValueError('Partial game: first round is not East 1')
                initial_dealer = dealer
                game['initial_dealer'] = dealer
            elif dealer != (initial_dealer + kyoku) % 4:
                raise ValueError('Dealer/round inconsistency')
            round_seq = len(game['rounds'])
            rnd = {'round_seq': round_seq, 'kyoku': kyoku, 'honba': honba, 'kyotaku': sticks,
                   'dealer': dealer, 'scores_start': scores[:], 'events': [], 'results': []}
            game['rounds'].append(rnd)
            state = {'hands': hands, 'scores': scores, 'start': scores[:], 'sticks': sticks,
                     'rivers': [[], [], [], []], 'melds': [[], [], [], []], 'last_draw': [None] * 4,
                     'riichi': [False] * 4, 'pending': [False] * 4, 'dora': [seed[5]],
                     'seen': set(physical), 'ended': False, 'last_discard': None, 'sequence': []}
            completed = False
        if state is not None:
            rnd['events'].append({'event_seq': event_seq, 'tag': tag, 'attributes': dict(a)})
        if tag == 'INIT':
            continue
        match = MOVE.fullmatch(tag)
        if match:
            if state is None or state['ended']:
                raise ValueError('Move outside an active round')
            letter, value = match.groups()
            letter, tile = letter.upper(), int(value)
            if not 0 <= tile < 136:
                raise ValueError('Invalid tile')
            if letter in 'TUVW':
                who = 'TUVW'.index(letter)
                if tile in state['seen']:
                    raise ValueError(f'Duplicate physical draw at {event_seq}: {tile}')
                state['seen'].add(tile)
                state['hands'][who].append(tile)
                state['last_draw'][who] = tile
                state['last_discard'] = None
            else:
                who = 'DEFG'.index(letter)
                hand = state['hands'][who]
                if tile not in hand or len(hand) + 3 * len(state['melds'][who]) != 14:
                    raise ValueError(f'Illegal reconstructed discard at {event_seq}: seat={who}, tile={tile}, hand={hand}')
                tsumo = state['last_draw'][who] == tile
                declaration = state['pending'][who]
                if who == actor:
                    current_ranks = ranks(state['scores'], initial_dealer)
                    start_ranks = ranks(state['start'], initial_dealer)
                    state['sequence'].append(token(tile, aka))
                    snapshot = {
                        'hand136': sorted(hand), 'hand': [token(t, aka) for t in sorted(hand)],
                        'draw136': state['last_draw'][who],
                        'draw': token(state['last_draw'][who], aka) if state['last_draw'][who] is not None else None,
                        'discard136': tile, 'discard': token(tile, aka),
                        'scores': [state['scores'][i] for i in order], 'ranks': [current_ranks[i] for i in order],
                        'scores_start': [state['start'][i] for i in order], 'ranks_start': [start_ranks[i] for i in order],
                        'names': [names[i] for i in order], 'rivers': [], 'melds': [],
                        'riichi': [state['riichi'][i] for i in order],
                        'riichi_pending': [state['pending'][i] for i in order],
                        'dora_indicators': [token(t, aka) for t in state['dora']],
                        'dealer_relative': (dealer - actor) % 4,
                        'sequence': state['sequence'][:],
                    }
                    # Public state only: never serialize opponents' concealed tiles.
                    for seat in order:
                        snapshot['rivers'].append(copy.deepcopy(state['rivers'][seat]))
                        public_melds = []
                        for m in state['melds'][seat]:
                            public_melds.append({'kind': m['kind'], 'tiles': [token(t, aka) for t in m['tiles136']],
                                                 'source_relative': (m['source'] - actor) % 4,
                                                 'called': token(m['called136'], aka) if m['called136'] is not None else None})
                        snapshot['melds'].append(public_melds)
                    decision = {'log_id': log_id, 'event_seq': event_seq, 'round_seq': round_seq,
                                'wind': kyoku // 4, 'hand_no': kyoku % 4 + 1, 'honba': honba,
                                'kyotaku': state['sticks'], 'seat_wind': (actor - dealer) % 4,
                                'turn': len(state['rivers'][who]) + 1, 'tsumogiri': int(tsumo),
                                'riichi_declared': int(declaration), 'snapshot': snapshot}
                    game['decisions'].append(decision)
                state['rivers'][who].append({'tile': token(tile, aka), 'tile136': tile,
                                             'tsumogiri': tsumo, 'riichi': declaration,
                                             'called_by': None, 'event_seq': event_seq})
                hand.remove(tile)
                state['last_draw'][who] = None
                state['last_discard'] = (who, tile)
        elif tag == 'N':
            if state is None or state['ended']:
                raise ValueError('Meld outside active round')
            who = int(a['who'])
            m = meld(int(a['m']), who)
            own = state['hands'][who]
            if m['kind'] == 'kakan':
                existing = [x for x in state['melds'][who] if x['kind'] == 'pon' and x['tiles136'][0] // 4 == m['tiles136'][0] // 4]
                if len(existing) != 1:
                    raise ValueError('Added kan without previous pon')
                consume = [m['tiles136'][-1]]
                previous = existing[0]
                m['source'], m['called136'] = previous['source'], previous['called136']
                state['melds'][who].remove(previous)
            elif m['kind'] == 'ankan':
                consume = m['tiles136'][:]
            else:
                if state['last_discard'] != (m['source'], m['called136']):
                    raise ValueError(f'Meld does not match preceding discard: {event_seq}, {m}')
                consume = [t for t in m['tiles136'] if t != m['called136']]
                state['rivers'][m['source']][-1]['called_by'] = (who - actor) % 4
            for t in consume:
                if t not in own:
                    raise ValueError(f'Meld tile {t} not in concealed hand at {event_seq}')
                own.remove(t)
            state['melds'][who].append(m)
            state['last_draw'][who] = None
            state['last_discard'] = None
        elif tag == 'REACH':
            who = int(a['who'])
            if a['step'] == '1':
                state['pending'][who] = True
            elif a['step'] == '2':
                if not state['pending'][who] or state['riichi'][who]:
                    raise ValueError('Riichi acceptance without a new declaration')
                expected = state['scores'][:]
                expected[who] -= 1000
                if 'ten' in a and [v * 100 for v in ints(a['ten'])] != expected:
                    raise ValueError('Unexpected riichi score change')
                state['scores'] = expected
                state['sticks'] += 1
                state['riichi'][who] = True
                state['pending'][who] = False
            else:
                raise ValueError('Unknown REACH step')
        elif tag == 'DORA':
            tile = int(a['hai'])
            if tile in state['seen'] or not 0 <= tile < 136:
                raise ValueError('Invalid or reused dora indicator')
            state['seen'].add(tile)
            state['dora'].append(tile)
        elif tag in ('AGARI', 'RYUUKYOKU'):
            if state is None:
                raise ValueError('Result without round')
            state['ended'] = True
            rnd['results'].append({'tag': tag, **dict(a)})
            if 'owari' in a:
                completed = True
        elif tag not in ('mjloggm', 'SHUFFLE', 'GO', 'UN', 'TAIKYOKU', 'BYE'):
            raise ValueError(f'Unsupported event {tag!r}; refusing partial reconstruction')
    if not completed or not game['decisions']:
        raise ValueError('Incomplete log or no target discards')
    return game
