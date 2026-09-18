from __future__ import annotations

import gzip
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator

TARGET = "ⓃLuckyJ"
DRAW_SEATS = {"T": 0, "U": 1, "V": 2, "W": 3}
DISCARD_SEATS = {"D": 0, "E": 1, "F": 2, "G": 3}
TILE_EVENT = re.compile(r"^([TUVWDEFGtuvwdefg])(\d+)$")
WINDS = ("E", "S", "W", "N")


def tile_type(tile_id: int) -> int:
    return tile_id // 4


def tile_code(tile_id: int) -> str:
    t = tile_type(tile_id)
    copy = tile_id % 4
    if t < 9:
        n, suit = t + 1, "m"
    elif t < 18:
        n, suit = t - 8, "p"
    elif t < 27:
        n, suit = t - 17, "s"
    else:
        n, suit = t - 26, "z"
    if suit != "z" and n == 5 and copy == 0:
        n = 0
    return f"{n}{suit}"


def canonical_tiles(tile_ids: Iterable[int]) -> str:
    return " ".join(tile_code(t) for t in sorted(tile_ids, key=lambda x: (tile_type(x), x % 4)))


def parse_scores(raw: str) -> list[int]:
    return [int(float(v) * 100) for v in raw.split(",")]


def score_rank(scores: list[int], seat: int) -> int:
    order = sorted(range(4), key=lambda s: (-scores[s], s))
    return order.index(seat) + 1


def decode_names(root: ET.Element) -> dict[int, str]:
    names: dict[int, str] = {}
    for node in root.iter("UN"):
        for seat in range(4):
            key = f"n{seat}"
            if key in node.attrib:
                names[seat] = urllib.parse.unquote(node.attrib[key])
    return names


def _remove_exact_or_type(hand: list[int], tile_id: int) -> None:
    if tile_id in hand:
        hand.remove(tile_id)
        return
    tt = tile_type(tile_id)
    for x in hand:
        if tile_type(x) == tt:
            hand.remove(x)
            return
    raise ValueError(f"meld tile {tile_id} is not in concealed hand")


def decode_meld(who: int, m: int) -> dict:
    rel = m & 0x3
    from_who = (who + rel) % 4
    if m & 0x4:
        t = (m >> 10) & 0x3F
        called_index = t % 3
        t //= 3
        base_type = (t // 7) * 9 + (t % 7)
        base = base_type * 4
        tiles = [
            base + ((m >> 3) & 0x3),
            base + 4 + ((m >> 5) & 0x3),
            base + 8 + ((m >> 7) & 0x3),
        ]
        kind = "chi"
    elif m & 0x18:
        t = (m >> 9) & 0x7F
        called_index = t % 3
        t //= 3
        base = t * 4
        if m & 0x8:
            unused = (m >> 5) & 0x3
            tiles = [base + i for i in range(4) if i != unused]
            kind = "pon"
        else:
            tiles = [base + i for i in range(4)]
            called_index = None
            kind = "kakan"
    elif m & 0x20:
        tile = (m >> 8) & 0xFF
        tiles = [tile]
        called_index = None
        kind = "nuki"
    else:
        tile = (m >> 8) & 0xFF
        base = (tile // 4) * 4
        tiles = [base + i for i in range(4)]
        if rel == 0:
            called_index = None
            kind = "ankan"
            from_who = who
        else:
            called_index = tile % 4
            kind = "minkan"
    return {
        "kind": kind,
        "who": who,
        "from_who": from_who,
        "tiles": tiles,
        "tile_codes": [tile_code(t) for t in tiles],
        "called_index": called_index,
        "raw_m": m,
    }


def apply_target_meld(hand: list[int], meld: dict) -> None:
    kind = meld["kind"]
    tiles = meld["tiles"]
    if kind in ("chi", "pon"):
        called = meld["called_index"]
        for i, tile in enumerate(tiles):
            if i != called:
                _remove_exact_or_type(hand, tile)
    elif kind == "minkan":
        tt = tile_type(tiles[0])
        for _ in range(3):
            for x in list(hand):
                if tile_type(x) == tt:
                    hand.remove(x)
                    break
            else:
                raise ValueError("open-kan tiles are not in concealed hand")
    elif kind == "ankan":
        tt = tile_type(tiles[0])
        for _ in range(4):
            for x in list(hand):
                if tile_type(x) == tt:
                    hand.remove(x)
                    break
            else:
                raise ValueError("closed-kan tiles are not in concealed hand")
    elif kind == "kakan":
        tt = tile_type(tiles[0])
        for x in list(hand):
            if tile_type(x) == tt:
                hand.remove(x)
                break
        else:
            raise ValueError("added-kan tile is not in concealed hand")
    elif kind == "nuki":
        _remove_exact_or_type(hand, tiles[0])


@dataclass(slots=True)
class Decision:
    log_id: str
    hand_no: int
    round_index: int
    round_wind: str
    kyoku: int
    honba: int
    riichi_sticks: int
    dealer_seat: int
    player_seat: int
    seat_wind: str
    score_rank: int
    score0: int
    score1: int
    score2: int
    score3: int
    target_score: int
    turn: int
    draw_tile_id: int | None
    draw_tile: str | None
    discard_tile_id: int
    discard_tile: str
    tsumogiri: int
    riichi_state: int
    concealed_ids: str
    concealed_hand: str
    melds_json: str


def iter_decisions(xml_bytes: bytes, log_id: str, target: str = TARGET) -> Iterator[Decision]:
    root = ET.fromstring(xml_bytes)
    if root.tag != "mjloggm":
        raise ValueError("not a Tenhou mjloggm document")
    names = decode_names(root)
    seats = [s for s, name in names.items() if name == target]
    if len(seats) != 1:
        raise ValueError(f"expected exactly one {target!r}; found {len(seats)}")
    target_seat = seats[0]

    hand: list[int] = []
    melds: list[dict] = []
    scores = [25000] * 4
    round_index = kyoku = honba = riichi_sticks = dealer = hand_no = 0
    turn = 0
    last_draw: int | None = None
    riichi_state = 0

    for node in root:
        tag = node.tag
        if tag == "INIT":
            seed = [int(x) for x in node.attrib["seed"].split(",")]
            round_index, honba, riichi_sticks = seed[:3]
            kyoku = round_index % 4 + 1
            dealer = int(node.attrib["oya"])
            scores = parse_scores(node.attrib["ten"])
            hand = [int(x) for x in node.attrib[f"hai{target_seat}"].split(",") if x]
            melds = []
            hand_no += 1
            turn = 0
            last_draw = None
            riichi_state = 0
            continue

        match = TILE_EVENT.match(tag)
        if match:
            event, raw_tile = match.groups()
            event = event.upper()
            tile = int(raw_tile)
            if event in DRAW_SEATS:
                if DRAW_SEATS[event] == target_seat:
                    hand.append(tile)
                    last_draw = tile
                continue
            if DISCARD_SEATS[event] == target_seat:
                turn += 1
                if tile not in hand:
                    raise ValueError(f"discarded tile {tile} absent from target hand in {log_id}")
                snapshot = list(hand)
                seat_wind = WINDS[(target_seat - dealer) % 4]
                yield Decision(
                    log_id=log_id,
                    hand_no=hand_no,
                    round_index=round_index,
                    round_wind=WINDS[min(round_index // 4, 3)],
                    kyoku=kyoku,
                    honba=honba,
                    riichi_sticks=riichi_sticks,
                    dealer_seat=dealer,
                    player_seat=target_seat,
                    seat_wind=seat_wind,
                    score_rank=score_rank(scores, target_seat),
                    score0=scores[0], score1=scores[1], score2=scores[2], score3=scores[3],
                    target_score=scores[target_seat],
                    turn=turn,
                    draw_tile_id=last_draw,
                    draw_tile=tile_code(last_draw) if last_draw is not None else None,
                    discard_tile_id=tile,
                    discard_tile=tile_code(tile),
                    tsumogiri=int(last_draw == tile),
                    riichi_state=riichi_state,
                    concealed_ids=",".join(str(x) for x in sorted(snapshot)),
                    concealed_hand=canonical_tiles(snapshot),
                    melds_json=json.dumps(melds, ensure_ascii=False, separators=(",", ":")),
                )
                hand.remove(tile)
                last_draw = None
                continue

        if tag == "N" and int(node.attrib["who"]) == target_seat:
            meld = decode_meld(target_seat, int(node.attrib["m"]))
            apply_target_meld(hand, meld)
            melds.append(meld)
            last_draw = None
            continue

        if tag == "REACH" and int(node.attrib.get("who", -1)) == target_seat:
            step = int(node.attrib.get("step", 0))
            if step == 1:
                riichi_state = 1
            elif step == 2:
                scores[target_seat] -= 1000
                riichi_sticks += 1
                riichi_state = 2
            continue


def read_log(path: Path) -> bytes:
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def game_metadata(xml_bytes: bytes, log_id: str, target: str = TARGET) -> dict:
    root = ET.fromstring(xml_bytes)
    names = decode_names(root)
    seats = [s for s, name in names.items() if name == target]
    if len(seats) != 1:
        raise ValueError(f"expected exactly one {target!r}")
    owari = None
    for node in reversed(list(root)):
        if "owari" in node.attrib:
            owari = [float(x) for x in node.attrib["owari"].split(",")]
            break
    return {"log_id": log_id, "target_seat": seats[0], "names": names, "owari": owari}
