#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import mimetypes
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEB = ROOT / "web"
ALLOWED = {
    "round_wind": ("round_wind = ?", str), "kyoku": ("kyoku = ?", int), "honba": ("honba = ?", int),
    "rank": ("score_rank = ?", int), "seat_wind": ("seat_wind = ?", str), "turn": ("turn = ?", int),
    "target_score": ("target_score = ?", int), "score0": ("score0 = ?", int), "score1": ("score1 = ?", int),
    "score2": ("score2 = ?", int), "score3": ("score3 = ?", int), "draw": ("draw_tile = ?", str),
    "discard": ("discard_tile = ?", str), "tsumogiri": ("tsumogiri = ?", int), "riichi": ("riichi_state = ?", int),
    "hand": ("concealed_hand = ?", str), "log_id": ("log_id = ?", str),
}


class App(BaseHTTPRequestHandler):
    db_path: Path
    web_root: Path

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/api/meta":
            with sqlite3.connect(self.db_path) as con:
                meta = dict(con.execute("SELECT key,value FROM meta"))
            return self._json(meta)
        if u.path == "/api/search":
            return self.search(parse_qs(u.query))
        path = "index.html" if u.path == "/" else u.path.lstrip("/")
        target = (self.web_root / path).resolve()
        if not str(target).startswith(str(self.web_root.resolve())) or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def search(self, qs):
        where, args = [], []
        try:
            for key, (sql, cast) in ALLOWED.items():
                if key in qs and qs[key][0] != "":
                    where.append(sql)
                    args.append(cast(qs[key][0]))
            limit = min(max(int(qs.get("limit", [100])[0]), 1), 500)
            offset = max(int(qs.get("offset", [0])[0]), 0)
        except (ValueError, TypeError):
            return self._json({"error":"invalid query parameter"}, 400)
        cond = " WHERE " + " AND ".join(where) if where else ""
        select = """id,log_id,round_wind,kyoku,honba,riichi_sticks,seat_wind,score_rank,score0,score1,score2,score3,target_score,turn,draw_tile,discard_tile,tsumogiri,riichi_state,concealed_hand,melds_json"""
        with sqlite3.connect(self.db_path) as con:
            con.row_factory = sqlite3.Row
            total = con.execute("SELECT count(*) FROM decisions" + cond, args).fetchone()[0]
            rows = [
                dict(r) for r in con.execute(
                    f"SELECT {select} FROM decisions{cond} ORDER BY log_id, hand_no, turn LIMIT ? OFFSET ?",
                    [*args, limit, offset],
                )
            ]
        for r in rows:
            r["melds"] = json.loads(r.pop("melds_json"))
            r["tenhou_url"] = "https://tenhou.net/0/?log=" + r["log_id"]
        return self._json({"total":total,"limit":limit,"offset":offset,"rows":rows})

    def log_message(self, fmt, *args):
        print(fmt % args)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "dist/luckyj.sqlite3")
    ap.add_argument("--web", type=Path, default=DEFAULT_WEB)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    App.db_path, App.web_root = args.db, args.web
    print(f"LuckyJ: http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), App).serve_forever()
