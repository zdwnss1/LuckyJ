"""Versioned, pre-event decision annotations. No intent or hidden-state labels.

Flags describe real identities BEFORE display symmetry. Statistics are overlapping
sets of decisions, not disjoint action probabilities. Unknown comparisons stay null.
"""
from __future__ import annotations
from .tiles import kind

VERSION = 'decision-flags-1'
OPEN_KINDS = frozenset(('chi', 'pon', 'daiminkan', 'kakan'))
FLAG_DEFINITIONS = {
    'follow_discard': '本巡跟切：上次自己切牌之后、本次切牌之前，他家切过同种真实牌；赤五与普通五合并，不按同类字牌替代。',
    'follow_immediate': '紧跟最近一张他家弃牌，且该弃牌落在本巡窗口；不是因果判断。',
    'riichi_declaration': '本次立直宣言切牌；不等于宣言后来已成立。',
    'riichi_locked': '自己已经成立立直后的切牌，与宣言当次分开。',
    'damaten': '本次切后结构听牌、门清且未立直／未在本次宣言；暗杠不破门清。不判断役、振听或是否具备立直条件。',
    'dama_enter': '摸牌前未听、本次切后进入默听。',
    'dama_hold': '摸牌前已听、本次切后继续默听。',
    'shanten_retreat': '实际切后向听高于本次摸牌前；无本次摸牌时不可比较，记null。',
    'tenpai_break': '摸牌前听牌、本次切后不听。',
    'miss_minimum': '实际切后向听高于本次合法候选的最低值；与退向听不是同一口径。',
    'post_call_discard': '吃／碰后直接切牌，无本次摸牌；不是杠后岭上切牌。',
}
FLAG_KEYS = tuple(FLAG_DEFINITIONS)
MELD_COLUMNS = ('own_open_melds', 'own_closed_kans', 'opponents_open')


def annotate(snapshot: dict, decision: dict, before_draw: int | None,
             after: int, best: int) -> dict:
    """All seats in snapshots are relative to LuckyJ, never rotated by symmetry."""
    rivers = snapshot['rivers']
    lower = rivers[0][-1]['event_seq'] if rivers[0] else -1
    upper = decision['event_seq']
    recent = []
    for seat, river in enumerate(rivers[1:], 1):
        for discarded in river:
            if lower < discarded['event_seq'] < upper:
                recent.append({'seat': seat, 'tile': discarded['tile'],
                               'event_seq': discarded['event_seq'],
                               'riichi_declaration': bool(discarded['riichi']),
                               'tsumogiri': bool(discarded['tsumogiri'])})
    recent.sort(key=lambda r: (r['event_seq'], r['seat']))
    sources = [r for r in recent if kind(r['tile']) == kind(snapshot['discard'])]
    declared = bool(decision['riichi_declared'])
    locked = bool(snapshot['riichi'][0])
    meld_counts = []
    for melds in snapshot['melds']:
        counts = {name: sum(m['kind'] == name for m in melds)
                  for name in ('chi', 'pon', 'daiminkan', 'kakan', 'ankan')}
        if sum(counts.values()) != len(melds):
            raise ValueError('Unknown meld kind; cannot classify closed/open state')
        counts['open'] = sum(counts[name] for name in OPEN_KINDS)
        counts['fixed'] = len(melds)
        meld_counts.append(counts)
    closed = meld_counts[0]['open'] == 0
    dama = closed and not locked and not declared and after == 0
    flags = {
        'follow_discard': bool(sources),
        'follow_immediate': bool(sources) and sources[-1]['event_seq'] == recent[-1]['event_seq'],
        'riichi_declaration': declared,
        'riichi_locked': locked,
        'damaten': dama,
        'dama_enter': dama and before_draw is not None and before_draw > 0,
        'dama_hold': dama and before_draw == 0,
        'shanten_retreat': after > before_draw if before_draw is not None else None,
        'tenpai_break': after > 0 if before_draw == 0 else False if before_draw is not None else None,
        'miss_minimum': after > best,
        'post_call_discard': snapshot['draw'] is None and bool(snapshot['melds'][0])
                             and snapshot['melds'][0][-1]['kind'] in ('chi', 'pon'),
    }
    return {
        'version': VERSION, 'flags': flags, 'follow_sources': sources,
        'follow_window': {'after_event_seq': lower, 'before_event_seq': upper},
        'shanten_transition': {'before_draw': before_draw, 'actual_after': after,
                               'best_after': best,
                               'change_from_before_draw': after - before_draw if before_draw is not None else None,
                               'gap_from_best': after - best},
        'closed_hand': closed, 'meld_counts': meld_counts,
        'own_open_melds': meld_counts[0]['open'],
        'own_closed_kans': meld_counts[0]['ankan'],
        'opponents_open': sum(m['open'] > 0 for m in meld_counts[1:]),
    }


def sql_values(annotation: dict) -> tuple:
    values = [None if annotation['flags'][key] is None else int(annotation['flags'][key])
              for key in FLAG_KEYS]
    return tuple(values + [annotation[key] for key in MELD_COLUMNS])


def new_facets() -> dict:
    return {key: {'true': 0, 'false': 0, 'unknown': 0} for key in FLAG_KEYS}


def count_facets(facets: dict, flags: dict) -> None:
    for key in FLAG_KEYS:
        value = flags[key]
        facets[key]['unknown' if value is None else 'true' if value else 'false'] += 1
