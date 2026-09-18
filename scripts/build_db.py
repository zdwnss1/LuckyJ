#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from luckyj.tenhou import TARGET, asdict, game_metadata, iter_decisions, read_log

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE games (
  log_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  target_seat INTEGER NOT NULL,
  names_json TEXT NOT NULL,
  owari_json TEXT,
  decision_count INTEGER NOT NULL
);
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  log_id TEXT NOT NULL REFERENCES games(log_id),
  hand_no INTEGER NOT NULL,
  round_index INTEGER NOT NULL,
  round_wind TEXT NOT NULL,
  kyoku INTEGER NOT NULL,
  honba INTEGER NOT NULL,
  riichi_sticks INTEGER NOT NULL,
  dealer_seat INTEGER NOT NULL,
  player_seat INTEGER NOT NULL,
  seat_wind TEXT NOT NULL,
  score_rank INTEGER NOT NULL,
  score0 INTEGER NOT NULL,
  score1 INTEGER NOT NULL,
  score2 INTEGER NOT NULL,
  score3 INTEGER NOT NULL,
  target_score INTEGER NOT NULL,
  turn INTEGER NOT NULL,
  draw_tile_id INTEGER,
  draw_tile TEXT,
  discard_tile_id INTEGER NOT NULL,
  discard_tile TEXT NOT NULL,
  tsumogiri INTEGER NOT NULL,
  riichi_state INTEGER NOT NULL,
  concealed_ids TEXT NOT NULL,
  concealed_hand TEXT NOT NULL,
  melds_json TEXT NOT NULL
);
CREATE INDEX idx_context ON decisions(round_wind, kyoku, honba, score_rank, target_score);
CREATE INDEX idx_discard ON decisions(discard_tile, draw_tile, tsumogiri);
CREATE INDEX idx_hand ON decisions(concealed_hand);
CREATE INDEX idx_log ON decisions(log_id, hand_no, turn);
"""

COLUMNS = [
    "log_id","hand_no","round_index","round_wind","kyoku","honba","riichi_sticks","dealer_seat","player_seat","seat_wind",
    "score_rank","score0","score1","score2","score3","target_score","turn","draw_tile_id","draw_tile","discard_tile_id","discard_tile",
    "tsumogiri","riichi_state","concealed_ids","concealed_hand","melds_json"
]


def build(data_dir: Path, output: Path) -> dict:
    raw_dir = data_dir / "raw"
    paths = sorted(raw_dir.rglob("*.xml.gz"))
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    con = sqlite3.connect(output)
    con.executescript(SCHEMA)
    parsed = decisions = 0
    failures = []
    for path in paths:
        log_id = path.name[:-7]
        try:
            xml = read_log(path)
            meta = game_metadata(xml, log_id)
            rows = [asdict(x) for x in iter_decisions(xml, log_id)]
            con.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?)", (
                log_id, str(path.relative_to(data_dir)), hashlib.sha256(xml).hexdigest(), meta["target_seat"],
                json.dumps(meta["names"], ensure_ascii=False, separators=(",", ":")),
                json.dumps(meta["owari"], ensure_ascii=False, separators=(",", ":")) if meta["owari"] is not None else None,
                len(rows)))
            if rows:
                marks = ",".join("?" for _ in COLUMNS)
                con.executemany(
                    f"INSERT INTO decisions ({','.join(COLUMNS)}) VALUES ({marks})",
                    [[r[c] for c in COLUMNS] for r in rows],
                )
            parsed += 1
            decisions += len(rows)
        except Exception as exc:
            failures.append({"path": str(path.relative_to(data_dir)), "error": str(exc)})
    built_at = datetime.now(timezone.utc).isoformat()
    meta_rows = {
        "schema_version":"1", "target":TARGET, "built_at":built_at,
        "raw_log_count":str(len(paths)), "parsed_game_count":str(parsed),
        "decision_count":str(decisions), "parse_failure_count":str(len(failures)),
    }
    con.executemany("INSERT INTO meta(key,value) VALUES (?,?)", meta_rows.items())
    con.commit()
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    report = {**meta_rows, "integrity_check":integrity, "failures":failures, "database":str(output)}
    (output.parent / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k:v for k,v in report.items() if k != "failures"}, ensure_ascii=False, indent=2))
    if failures or integrity != "ok":
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--output", type=Path, default=Path("dist/luckyj.sqlite3"))
    args = ap.parse_args()
    build(args.data, args.output)
