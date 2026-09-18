#!/usr/bin/env python3
"""LuckyJ phase 1: lossless source archive -> deterministic decision index -> local UI.
Python 3.10+, standard library only. No abstract strategy labels or hidden-hand features.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as dt
import gzip
import hashlib
import http.server
import json
import pathlib
import re
import sqlite3
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

PLAYER = "\u24ddLuckyJ"
VERSION = "1.0.0"
ROOT = pathlib.Path(__file__).resolve().parent
LOG_ID = re.compile(r"\d{10}gm-[0-9a-f]{4}-[0-9a-z]+-[0-9a-z]{8}", re.I)
TILE_TAG = re.compile(r"([TUVWDEFGtuvwdefg])(\d+)$")
TOKENS = [f"{n}{s}" for s in "mps" for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)] + ["0m", "0p", "0s"]
TOKEN_INDEX = {t: i for i, t in enumerate(TOKENS)}
RED_IDS = {16: "0m", 52: "0p", 88: "0s"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pack(value):
    return zlib.compress(dumps(value).encode(), 6)


def unpack(value):
    return json.loads(zlib.decompress(value))


def save_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def ints(value):
    return [int(v) for v in value.split(",")]


def normal(tile):
    return "5" + tile[1] if tile and tile[0] == "0" else tile


def tile_name(tile_id, aka=True):
    if not 0 <= tile_id < 136:
        raise ValueError(f"Invalid physical tile ID: {tile_id}")
    return RED_IDS[tile_id] if aka and tile_id in RED_IDS else TOKENS[tile_id // 4]


def tile_counts(tiles, merge_red=False):
    counts = [0] * 37
    for t in tiles:
        counts[TOKEN_INDEX[normal(t) if merge_red else t]] += 1
    return bytes(counts)


def parse_tiles(text, *, single=False, hand=False):
    """Compact notation: 123m405p77z; whitespace, commas and arrows are separators."""
    text = re.sub(r"[\s,，→>\-]", "", text.lower())
    if not text:
        raise ValueError("请输入至少一张有效牌")
    if len(text) > 200 or not re.fullmatch(r"(?:[0-9]+[mpsz])+", text):
        raise ValueError("牌型格式应为 123m405p77z；m/p/s/z 分别为万/筒/索/字")
    result = []
    for digits, suit in re.findall(r"([0-9]+)([mpsz])", text):
        for digit in digits:
            tile = digit + suit
            if tile not in TOKEN_INDEX:
                raise ValueError(f"无效牌：{tile}；字牌只允许 1z–7z")
            result.append(tile)
    if single and len(result) != 1:
        raise ValueError("摸牌与切牌条件各只能输入一张牌")
    if len(result) > (14 if hand else 32):
        raise ValueError("输入牌数过多")
    if hand and (max(collections.Counter(map(normal, result)).values(), default=0) > 4 or any(result.count(t) > 1 for t in ("0m", "0p", "0s"))):
        raise ValueError("手牌不能有五张同种牌或重复赤五")
    return result


def placements(scores, first_dealer):
    order = sorted(range(4), key=lambda s: (-scores[s], (s - first_dealer) % 4))
    return [order.index(s) + 1 for s in range(4)]


def decode_meld(m, who):
    """Decode Tenhou's public meld bitfield, preserving physical IDs and source seat."""
    source = (who + (m & 3)) % 4
    result = {"who": who, "from": source, "code": m}
    if m & 4:
        bc = m >> 10
        base, called = divmod(bc, 3)
        base = base // 7 * 9 + base % 7
        tiles = [4 * (base + i) + ((m >> (3 + i * 2)) & 3) for i in range(3)]
        result.update(kind="chi", ids=tiles, called_id=tiles[called])
    elif m & 0x18:
        spare = (m >> 5) & 3
        base, called = divmod(m >> 9, 3)
        tiles = [4 * base + i for i in range(4) if i != spare]
        if m & 8:
            result.update(kind="pon", ids=tiles, called_id=tiles[called])
        else:
            result.update(kind="kakan", ids=tiles + [4 * base + spare], called_id=tiles[called], added_id=4 * base + spare)
    elif m & 0x20:
        raise ValueError("Sanma/nuki is outside this four-player corpus")
    else:
        base, called = divmod(m >> 8, 4)
        tiles = list(range(4 * base, 4 * base + 4))
        result.update(kind="ankan" if source == who else "daiminkan", ids=tiles, called_id=None if source == who else tiles[called])
    if any(not 0 <= x < 136 for x in result["ids"]):
        raise ValueError("Meld contains an invalid tile ID")
    return result


def parse_game(raw, log_id):
    if not LOG_ID.fullmatch(log_id):
        raise ValueError("Invalid game ID")
    if len(raw) > 8_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Oversized or unsafe XML")
    root = ET.fromstring(raw)
    if root.tag != "mjloggm":
        raise ValueError("Not a Tenhou XML log")
    names = [None] * 4
    for un in root.iter("UN"):
        for i in range(4):
            if "n" + str(i) in un.attrib:
                value = urllib.parse.unquote(un.attrib["n" + str(i)])
                if names[i] is not None and names[i] != value:
                    raise ValueError("Player identity changes within game")
                names[i] = value
    if names.count(PLAYER) != 1 or any(n is None for n in names):
        raise ValueError("Exact LuckyJ identity or four-player roster missing")
    lucky = names.index(PLAYER)
    go = root.find("GO")
    if go is None:
        raise ValueError("Missing game rule tag")
    rule = int(go.get("type", "0"))
    if rule & 0x10:
        raise ValueError("Three-player game not supported")
    aka = not bool(rule & 2)
    first_init = root.find("INIT")
    if first_init is None:
        raise ValueError("Missing initial hand")
    tai = root.find("TAIKYOKU")
    first_dealer = int(tai.get("oya")) if tai is not None else int(first_init.get("oya"))
    if not 0 <= first_dealer <= 3:
        raise ValueError("Invalid first dealer")
    if not any("owari" in e.attrib for e in root):
        raise ValueError("Missing final game result (owari): incomplete source log")
    rounds, cases = [], []
    state = None
    current_round = None
    all_discards = 0
    for event_index, event in enumerate(root):
        tag, a = event.tag, dict(event.attrib)
        if tag == "INIT":
            if current_round is not None and not current_round["result"]:
                raise ValueError("Previous hand has no terminal event")
            seed, scores = ints(a["seed"]), [x * 100 for x in ints(a["ten"])]
            hands = [ints(a[f"hai{i}"]) for i in range(4)]
            if len(scores) != 4 or any(len(h) != 13 for h in hands):
                raise ValueError("Invalid INIT shape")
            physical = [x for h in hands for x in h]
            if len(set(physical)) != 52 or any(not 0 <= x < 136 for x in physical):
                raise ValueError("INIT contains duplicate/invalid physical tiles")
            ordinal = len(rounds)
            round_id = f"{log_id}:{ordinal}"
            state = {"hands": hands, "scores": scores, "start_scores": list(scores),
                     "round_no": seed[0], "honba": seed[1], "kyotaku": seed[2],
                     "dealer": int(a["oya"]), "rivers": [[], [], [], []],
                     "melds": [[], [], [], []], "riichi": [False] * 4,
                     "pending": [False] * 4, "draws": [None] * 4,
                     "dora_ids": [seed[5]], "seen": set(physical), "ended": False}
            current_round = {"id": round_id, "log_id": log_id, "ordinal": ordinal,
                             "round_no": seed[0], "honba": seed[1], "dealer": state["dealer"],
                             "start_scores": list(scores), "start_kyotaku": seed[2],
                             "events": [], "result": []}
            rounds.append(current_round)
        if current_round is not None:
            current_round["events"].append({"i": event_index, "tag": tag, "a": a})
        if state is None or tag == "INIT":
            continue
        match = TILE_TAG.fullmatch(tag)
        if match:
            letter, tile = match.group(1).upper(), int(match.group(2))
            if state["ended"]:
                raise ValueError("Tile action after terminal hand event")
            if not 0 <= tile < 136:
                raise ValueError("Invalid physical tile")
            if letter in "TUVW":
                who = "TUVW".index(letter)
                if tile in state["seen"]:
                    raise ValueError(f"Physical tile drawn twice: {tile}")
                state["seen"].add(tile)
                state["hands"][who].append(tile)
                state["draws"][who] = tile
            else:
                who = "DEFG".index(letter)
                hand = state["hands"][who]
                if tile not in hand:
                    raise ValueError(f"Discard {tile} not in seat {who}'s hand at event {event_index}")
                if len(hand) + 3 * len(state["melds"][who]) != 14:
                    raise ValueError(f"Wrong hand size before discard at event {event_index}")
                tsumogiri = state["draws"][who] == tile
                turn = len(state["rivers"][who]) + 1
                discard = {"id": tile, "tile": tile_name(tile, aka), "tsumogiri": tsumogiri,
                           "riichi": state["pending"][who], "called": False}
                all_discards += 1
                if who == lucky:
                    # Snapshot before mutation. Only LuckyJ's concealed hand is exposed.
                    draw = state["draws"][who]
                    hand_ids = sorted(hand)
                    hand_tiles = [tile_name(t, aka) for t in hand_ids]
                    ranks = placements(state["scores"], first_dealer)
                    sequence = [r["tile"] for r in state["rivers"][who]] + [discard["tile"]]
                    case = {"id": f"{log_id}:{ordinal}:{event_index}", "log_id": log_id,
                            "round_id": current_round["id"], "round_ordinal": ordinal,
                            "event_index": event_index, "turn": turn,
                            "round_no": state["round_no"], "honba": state["honba"],
                            "kyotaku": state["kyotaku"], "dealer": state["dealer"],
                            "lucky_seat": lucky, "first_dealer": first_dealer,
                            "scores": list(state["scores"]), "start_scores": list(state["start_scores"]),
                            "ranks": ranks, "rank": ranks[lucky],
                            "start_rank": placements(state["start_scores"], first_dealer)[lucky],
                            "names": names, "hand_ids": hand_ids, "hand": hand_tiles,
                            "draw_id": draw, "draw": tile_name(draw, aka) if draw is not None else None,
                            "discard_id": tile, "discard": discard["tile"], "tsumogiri": tsumogiri,
                            "riichi_declaration": state["pending"][lucky],
                            "riichi": list(state["riichi"]), "pending_riichi": list(state["pending"]),
                            "dora_indicators": [tile_name(t, aka) for t in state["dora_ids"]],
                            "dora_ids": list(state["dora_ids"]), "sequence": sequence,
                            "rivers": state["rivers"], "melds": state["melds"]}
                    # Serialization here is intentional: later calls must not alter earlier rivers.
                    snapshot = pack(case)
                    cases.append((unpack(snapshot), snapshot, tile_counts(hand_tiles), tile_counts(hand_tiles, True)))
                state["rivers"][who].append(discard)
                hand.remove(tile)
                state["draws"][who] = None
        elif tag == "N":
            who = int(a["who"])
            meld = decode_meld(int(a["m"]), who)
            if meld["kind"] == "kakan":
                previous = next((x for x in state["melds"][who]
                                 if x["kind"] == "pon" and x["ids"][0] // 4 == meld["ids"][0] // 4), None)
                if previous is None:
                    raise ValueError("Added kan without earlier pon")
                consumed = [meld["added_id"]]
                meld["from"], meld["called_id"] = previous["from"], previous["called_id"]
                state["melds"][who].remove(previous)
            elif meld["kind"] == "ankan":
                consumed = list(meld["ids"])
            else:
                source_river = state["rivers"][meld["from"]]
                if not source_river or source_river[-1]["id"] != meld["called_id"] or source_river[-1]["called"]:
                    raise ValueError("Meld does not match the source discard")
                source_river[-1]["called"] = True
                consumed = [t for t in meld["ids"] if t != meld["called_id"]]
            for t in consumed:
                if t not in state["hands"][who]:
                    raise ValueError(f"Meld consumes unheld tile {t}")
                state["hands"][who].remove(t)
            meld["tiles"] = [tile_name(t, aka) for t in meld["ids"]]
            state["melds"][who].append(meld)
            state["draws"][who] = None
        elif tag == "REACH":
            who, step = int(a["who"]), int(a["step"])
            if step == 1:
                state["pending"][who] = True
            elif step == 2:
                if state["riichi"][who]:
                    raise ValueError("Duplicate riichi acceptance")
                expected = list(state["scores"])
                expected[who] -= 1000
                actual = [x * 100 for x in ints(a["ten"])] if "ten" in a else expected
                if actual != expected:
                    raise ValueError("Riichi score transition is not exactly -1000")
                state["scores"] = actual
                state["kyotaku"] += 1
                state["riichi"][who], state["pending"][who] = True, False
            else:
                raise ValueError("Unknown REACH step")
        elif tag == "DORA":
            dora = int(a["hai"])
            tile_name(dora, aka)
            state["dora_ids"].append(dora)
        elif tag in ("AGARI", "RYUUKYOKU"):
            current_round["result"].append({"tag": tag, "a": a})
            state["ended"] = True
    if not rounds or not rounds[-1]["result"] or not cases:
        raise ValueError("No complete indexed game")
    return {"id": log_id, "lucky_seat": lucky, "names": names, "rule": rule,
            "first_dealer": first_dealer, "rounds": rounds, "cases": cases,
            "all_player_discards": all_discards, "sha256": hashlib.sha256(raw).hexdigest()}


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE source_records(row_no INTEGER PRIMARY KEY, log_id TEXT, status TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE games(id TEXT PRIMARY KEY, lucky_seat INTEGER NOT NULL, names TEXT NOT NULL, rule INTEGER NOT NULL,
 first_dealer INTEGER NOT NULL, raw_path TEXT NOT NULL, sha256 TEXT NOT NULL, starttime INTEGER);
CREATE TABLE rounds(id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id), ordinal INTEGER NOT NULL,
 round_no INTEGER NOT NULL, honba INTEGER NOT NULL, dealer INTEGER NOT NULL, start_scores TEXT NOT NULL,
 start_kyotaku INTEGER NOT NULL, events BLOB NOT NULL, result TEXT NOT NULL, UNIQUE(game_id,ordinal));
CREATE TABLE decisions(id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id),
 round_id TEXT NOT NULL REFERENCES rounds(id), event_index INTEGER NOT NULL, turn INTEGER NOT NULL,
 round_no INTEGER NOT NULL, honba INTEGER NOT NULL, rank INTEGER NOT NULL, start_rank INTEGER NOT NULL,
 score0 INTEGER NOT NULL,score1 INTEGER NOT NULL,score2 INTEGER NOT NULL,score3 INTEGER NOT NULL,
 start0 INTEGER NOT NULL,start1 INTEGER NOT NULL,start2 INTEGER NOT NULL,start3 INTEGER NOT NULL,
 discard TEXT NOT NULL, discard_norm TEXT NOT NULL, draw TEXT, draw_norm TEXT, tsumogiri INTEGER NOT NULL,
 hand BLOB NOT NULL, hand_norm BLOB NOT NULL, sequence TEXT NOT NULL, sequence_norm TEXT NOT NULL,
 snapshot BLOB NOT NULL, UNIQUE(game_id,event_index));
CREATE INDEX d_situation ON decisions(round_no,honba,rank,score0);
CREATE INDEX d_discard ON decisions(discard,round_no,rank);
CREATE INDEX d_discard_norm ON decisions(discard_norm,round_no,rank);
CREATE INDEX d_draw ON decisions(draw);
CREATE INDEX d_rank ON decisions(rank,score0);
CREATE INDEX d_game ON decisions(game_id,event_index);
CREATE INDEX d_round ON decisions(round_id,turn);
CREATE INDEX source_log ON source_records(log_id);
"""


def load_manifest(data_dir):
    for filename in ("manifest.json", "source-manifest.json"):
        path = data_dir / filename
        if path.exists():
            raw = path.read_bytes()
            value = json.loads(raw)
            if value.get("name") != PLAYER or not isinstance(value.get("list"), list) or not value["list"]:
                raise ValueError("Source manifest has wrong identity/schema or is empty")
            return value, hashlib.sha256(raw).hexdigest()
    raise FileNotFoundError("Missing source-manifest.json or manifest.json; run the downloader first")


def build(data_dir, database):
    data_dir, database = pathlib.Path(data_dir), pathlib.Path(database)
    manifest, manifest_sha = load_manifest(data_dir)
    expected_hashes = {}
    download_report = data_dir / "download-report.json"
    if download_report.exists():
        download_status = json.loads(download_report.read_text(encoding="utf-8"))
        if download_status.get("manifest_sha256") != manifest_sha:
            raise ValueError("Download report and source manifest disagree; refusing mixed snapshots")
        if "results" in download_status:
            expected_hashes = {r["log_id"]: r.get("sha256") for r in download_status["results"] if r.get("status") == "ok"}
        else:
            expected_hashes = {k: v.get("sha256") for k, v in download_status.get("logs", {}).items() if v.get("status") == "downloaded"}
    records, missing, invalid = {}, [], []
    source_rows = []
    for row_no, row in enumerate(manifest["list"]):
        if sum(row.get(f"player{i}") == PLAYER for i in range(1, 5)) != 1:
            invalid.append({"row": row_no, "reason": "player_identity"})
            source_rows.append((row_no, None, "invalid_identity", dumps(row)))
            continue
        if not row.get("url"):
            missing.append(row_no)
            source_rows.append((row_no, None, "source_has_no_url", dumps(row)))
            continue
        url = urllib.parse.urlsplit(row["url"])
        log_ids = urllib.parse.parse_qs(url.query).get("log", [])
        if url.hostname not in ("tenhou.net", "www.tenhou.net") or len(log_ids) != 1 or not LOG_ID.fullmatch(log_ids[0]):
            invalid.append({"row": row_no, "reason": "invalid_url"})
            source_rows.append((row_no, None, "invalid_url", dumps(row)))
            continue
        log_id = log_ids[0]
        records.setdefault(log_id, row)
        source_rows.append((row_no, log_id, "pending", dumps(row)))
    paths = {}
    for path in sorted((data_dir / "raw").rglob("*.xml.gz")):
        log_id = path.name.removesuffix(".xml.gz")
        if log_id in paths and path.read_bytes() != paths[log_id].read_bytes():
            raise ValueError(f"Conflicting raw files for {log_id}")
        paths[log_id] = path
    database.parent.mkdir(parents=True, exist_ok=True)
    temp = database.with_suffix(database.suffix + ".building")
    temp.unlink(missing_ok=True)
    db = sqlite3.connect(temp)
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO source_records VALUES(?,?,?,?)", source_rows)
    failures, absent = [], []
    successful, n_rounds, n_cases, n_all = 0, 0, 0, 0
    for log_id, row in sorted(records.items()):
        path = paths.get(log_id)
        if path is None:
            absent.append(log_id)
            db.execute("UPDATE source_records SET status='not_downloaded' WHERE log_id=?", (log_id,))
            continue
        try:
            with gzip.open(path, "rb") as stream:
                raw = stream.read(8_000_001)
            game = parse_game(raw, log_id)
            if expected_hashes.get(log_id) and game["sha256"] != expected_hashes[log_id]:
                raise ValueError("Raw XML SHA-256 differs from the download receipt")
            db.execute("SAVEPOINT game")
            db.execute("INSERT INTO games VALUES(?,?,?,?,?,?,?,?)", (log_id, game["lucky_seat"], dumps(game["names"]), game["rule"], game["first_dealer"], str(path.relative_to(data_dir)), game["sha256"], row.get("starttime")))
            for r in game["rounds"]:
                db.execute("INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?,?)", (r["id"], log_id, r["ordinal"], r["round_no"], r["honba"], r["dealer"], dumps(r["start_scores"]), r["start_kyotaku"], pack(r["events"]), dumps(r["result"])))
            for c, snapshot, hand, hand_norm in game["cases"]:
                seat = c["lucky_seat"]
                scores = [c["scores"][(seat+i) % 4] for i in range(4)]
                starts = [c["start_scores"][(seat+i) % 4] for i in range(4)]
                sequence = " " + " ".join(c["sequence"]) + " "
                sequence_norm = " " + " ".join(map(normal, c["sequence"])) + " "
                values = (c["id"], log_id, c["round_id"], c["event_index"], c["turn"], c["round_no"], c["honba"], c["rank"], c["start_rank"], *scores, *starts, c["discard"], normal(c["discard"]), c["draw"], normal(c["draw"]), int(c["tsumogiri"]), hand, hand_norm, sequence, sequence_norm, snapshot)
                db.execute("INSERT INTO decisions VALUES(" + ",".join("?" for _ in values) + ")", values)
            db.execute("UPDATE source_records SET status='indexed' WHERE log_id=?", (log_id,))
            db.execute("RELEASE game")
            successful += 1
            n_rounds += len(game["rounds"])
            n_cases += len(game["cases"])
            n_all += game["all_player_discards"]
        except Exception as exc:
            try:
                db.execute("ROLLBACK TO game")
                db.execute("RELEASE game")
            except sqlite3.OperationalError:
                pass
            failures.append({"log_id": log_id, "error": str(exc)})
            db.execute("UPDATE source_records SET status='parse_failed' WHERE log_id=?", (log_id,))
        if successful and successful % 100 == 0:
            db.commit()
            print(f"Indexed {successful}/{len(records)} games, {n_cases} decisions", flush=True)
    foreign = db.execute("PRAGMA foreign_key_check").fetchall()
    integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    report = {"player": PLAYER, "parser_version": VERSION,
              "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "manifest_sha256": manifest_sha, "source_records": len(source_rows),
              "linked_games": len(records), "records_without_url": len(missing),
              "duplicate_link_rows": sum(x[1] is not None for x in source_rows)-len(records),
              "invalid_source_records": invalid, "downloaded_games": len(set(paths) & set(records)),
              "indexed_games": successful, "rounds": n_rounds, "decisions": n_cases,
              "validated_all_player_discards": n_all, "not_downloaded": absent,
              "parse_failures": failures, "extra_raw_files": sorted(set(paths)-set(records)),
              "all_linked_games_indexed": bool(records) and successful == len(records) and not invalid,
              "complete_source_coverage": bool(records) and successful == len(records) and not missing and not invalid,
              "download_receipt_available": download_report.exists(),
              "sqlite_integrity": integrity, "foreign_key_errors": len(foreign),
              "score_semantics": "immediately_before_discard; riichi declaration paid only after REACH step=2",
              "rank_semantics": "descending current scores; equal scores use original seat order from first dealer"}
    db.execute("INSERT INTO meta VALUES('status',?)", (dumps(report),))
    db.execute("INSERT INTO meta VALUES('schema_version','1')")
    db.commit()
    db.execute("ANALYZE")
    db.close()
    if integrity != "ok" or foreign:
        raise ValueError("SQLite integrity failure; existing index not replaced")
    temp.replace(database)
    save_json(data_dir / "build-report.json", report)
    print(dumps({k:v for k,v in report.items() if k not in ("parse_failures", "not_downloaded", "extra_raw_files")}), flush=True)
    if failures:
        print(dumps(failures[:10]), file=sys.stderr)
    return report


def connection(database):
    uri = pathlib.Path(database).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    db.create_function("contains_tiles", 2, lambda actual, wanted: int(all(a >= w for a, w in zip(actual, wanted))), deterministic=True)
    # Bound worst-case query work while still allowing full-corpus concrete pattern scans.
    deadline = __import__("time").monotonic() + 20
    db.set_progress_handler(lambda: int(__import__("time").monotonic() > deadline), 10000)
    return db


def search(db, params):
    allowed = {"round_no", "honba", "rank", "score_basis", "discard", "draw", "tsumogiri", "hand", "hand_mode", "sequence", "merge_red", "page", "limit", "turn_min", "turn_max", "game_id"}
    allowed |= {f"score{i}_{suffix}" for i in range(4) for suffix in ("min", "max")}
    if set(params) - allowed:
        raise ValueError("未知检索条件：" + ", ".join(sorted(set(params) - allowed)))
    if any(len(str(value)) > 300 for value in params.values()):
        raise ValueError("检索条件过长")
    params = {k:v for k,v in params.items() if v != ""}
    def number(key, lo, hi, default=None):
        if key not in params:
            return default
        try:
            v = int(params[key])
        except (ValueError, TypeError):
            raise ValueError(f"{key} 必须为整数") from None
        if not lo <= v <= hi:
            raise ValueError(f"{key} 超出允许范围 {lo}–{hi}")
        return v
    page, limit = number("page", 1, 100000, 1), number("limit", 1, 100, 24)
    merged = number("merge_red", 0, 1, 0)
    basis = params.get("score_basis", "current")
    if basis not in ("current", "start"):
        raise ValueError("分数口径只能为 current 或 start")
    conditions, values = [], []
    for key, lo, hi in (("round_no", 0, 31), ("honba", 0, 99), ("rank", 1, 4), ("tsumogiri", 0, 1)):
        value = number(key, lo, hi)
        if value is not None:
            column = "start_rank" if key == "rank" and basis == "start" else key
            conditions.append(column + "=?")
            values.append(value)
    for i in range(4):
        minimum, maximum = [number(f"score{i}_{suffix}", -200000, 500000) for suffix in ("min", "max")]
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("分数下限不能高于上限")
        for value, op in ((minimum, ">="), (maximum, "<=")):
            if value is not None:
                conditions.append(("score" if basis == "current" else "start") + str(i) + op + "?")
                values.append(value)
    turn_min, turn_max = number("turn_min", 1, 32), number("turn_max", 1, 32)
    if turn_min is not None and turn_max is not None and turn_min > turn_max:
        raise ValueError("切牌次序下限不能高于上限")
    for value, op in ((turn_min, ">="), (turn_max, "<=")):
        if value is not None:
            conditions.append("turn" + op + "?")
            values.append(value)
    for key in ("draw", "discard"):
        if key in params:
            tile = parse_tiles(params[key], single=True)[0]
            conditions.append(key + ("_norm" if merged else "") + "=?")
            values.append(normal(tile) if merged else tile)
    hand_mode = params.get("hand_mode", "contains")
    if hand_mode not in ("contains", "exact"):
        raise ValueError("手牌模式只能为 contains 或 exact")
    if "hand" in params:
        hand = tile_counts(parse_tiles(params["hand"], hand=True), bool(merged))
        column = "hand_norm" if merged else "hand"
        conditions.append(column + "=?" if hand_mode == "exact" else "contains_tiles(" + column + ",?)=1")
        values.append(hand)
    if "sequence" in params:
        sequence = parse_tiles(params["sequence"])
        column = "sequence_norm" if merged else "sequence"
        pattern = " " + " ".join(map(normal, sequence) if merged else sequence) + " "
        conditions.append(column + " LIKE ?")
        values.append("%" + pattern)
    if "game_id" in params:
        if not LOG_ID.fullmatch(params["game_id"]):
            raise ValueError("无效对局 ID")
        conditions.append("game_id=?")
        values.append(params["game_id"])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    count = db.execute("SELECT COUNT(*) FROM decisions" + where, values).fetchone()[0]
    rows = db.execute("SELECT snapshot FROM decisions" + where + " ORDER BY game_id,event_index LIMIT ? OFFSET ?", [*values, limit, (page-1)*limit]).fetchall()
    items = []
    for row in rows:
        item = unpack(row[0])
        # Public details are fetched on demand, keeping list responses small.
        for key in ("rivers", "melds"):
            item.pop(key, None)
        items.append(item)
    return {"total": count, "page": page, "limit": limit, "items": items, "score_basis": basis}


def make_handler(database, data_dir):
    database, data_dir = pathlib.Path(database), pathlib.Path(data_dir)
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "LuckyJ/1.0"
        def respond(self, status, body, content_type="application/json; charset=utf-8", extra=None):
            if not isinstance(body, bytes):
                body = dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            try:
                parsed = urllib.parse.urlsplit(self.path)
                path = urllib.parse.unquote(parsed.path)
                if path in ("/", "/app.js", "/style.css"):
                    filename = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[filename]
                    return self.respond(200, (ROOT / "web" / filename).read_bytes(), mime + "; charset=utf-8")
                with contextlib.closing(connection(database)) as db:
                    if path == "/api/status":
                        return self.respond(200, json.loads(db.execute("SELECT value FROM meta WHERE key='status'").fetchone()[0]))
                    if path == "/api/search":
                        pairs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=40)
                        if any(len(v) != 1 for v in pairs.values()):
                            raise ValueError("不接受重复条件")
                        return self.respond(200, search(db, {k:v[0] for k,v in pairs.items()}))
                    if path.startswith("/api/decision/"):
                        case_id = path.removeprefix("/api/decision/")
                        if len(case_id) > 100:
                            raise ValueError("无效记录 ID")
                        row = db.execute("SELECT snapshot FROM decisions WHERE id=?", (case_id,)).fetchone()
                        return self.respond(200, unpack(row[0])) if row else self.respond(404, {"error": "记录不存在"})
                    if path == "/api/missing":
                        rows = db.execute("SELECT row_no,log_id,status,record FROM source_records WHERE status!='indexed' ORDER BY row_no").fetchall()
                        return self.respond(200, [{**dict(r), "record": json.loads(r["record"])} for r in rows])
                    if path.startswith("/api/raw/"):
                        log_id = path.removeprefix("/api/raw/")
                        if not LOG_ID.fullmatch(log_id):
                            raise ValueError("无效对局 ID")
                        row = db.execute("SELECT raw_path FROM games WHERE id=?", (log_id,)).fetchone()
                        if not row:
                            return self.respond(404, {"error": "牌谱不存在"})
                        target = (data_dir / row[0]).resolve()
                        if not target.is_relative_to(data_dir.resolve()):
                            raise ValueError("非法归档路径")
                        return self.respond(200, target.read_bytes(), "application/gzip", {"Content-Disposition": f'attachment; filename="{log_id}.xml.gz"'})
                return self.respond(404, {"error": "路径不存在"})
            except (ValueError, TypeError) as exc:
                return self.respond(400, {"error": str(exc)})
            except (sqlite3.Error, FileNotFoundError):
                return self.respond(503, {"error": "索引未就绪或查询超时；请先运行 build，或缩小检索条件"})
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "serve"):
        p = sub.add_parser(command)
        p.add_argument("--data", type=pathlib.Path, default=ROOT / "data")
        p.add_argument("--db", type=pathlib.Path)
        if command == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    database = args.db or args.data / "luckyj.sqlite"
    if args.command == "build":
        report = build(args.data, database)
        return 0 if report["all_linked_games_indexed"] else 1
    if not database.exists():
        parser.error("数据库不存在；先运行 python luckyj.py build --data data")
    with http.server.ThreadingHTTPServer((args.host, args.port), make_handler(database, args.data)) as server:
        print(f"LuckyJ: http://{args.host}:{args.port} (Ctrl+C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
