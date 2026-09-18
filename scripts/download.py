"""Download the exact Nodocchi corpus; Python 3.10+, standard library only."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TARGET = "\u24ddLuckyJ"
API = "https://nodocchi.moe/api/listuser.php"
LOG_ID = re.compile(r"\d{10}gm-[0-9a-f]{4}-[0-9a-z]+-[0-9a-z]{8}", re.I)
HEADERS = {"User-Agent": "LuckyJ-Archive/0.1 (https://github.com/zdwnss1/LuckyJ)", "Accept": "*/*"}


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def request(url: str) -> bytes:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=45) as response:
                return response.read(8_000_001)
        except urllib.error.HTTPError as exc:
            # Do not bypass bans or keep hammering a rate-limited service.
            if exc.code in (403, 429) or exc.code < 500:
                raise
            if attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(3 * 2 ** attempt)
    raise RuntimeError("unreachable")


def validate_xml(raw: bytes, target: str = TARGET) -> ET.Element:
    if len(raw) > 8_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Oversized or unsafe XML")
    root = ET.fromstring(raw)
    if root.tag != "mjloggm":
        raise ValueError("Not a Tenhou XML game log")
    names = {}
    for node in root.iter("UN"):
        for seat in range(4):
            if f"n{seat}" in node.attrib:
                names[seat] = urllib.parse.unquote(node.attrib[f"n{seat}"])
    if list(names.values()).count(target) != 1:
        raise ValueError(f"Expected exactly one {target!r} player")
    if not any("owari" in node.attrib for node in root):
        raise ValueError("Missing final game result (owari)")
    return root


def manifest_ids(data: dict, target: str = TARGET) -> tuple[dict, list]:
    if data.get("name") != target or not isinstance(data.get("list"), list):
        raise ValueError("Unexpected manifest schema or player name")
    records, unavailable = {}, []
    for row in data["list"]:
        if target not in [row.get(f"player{i}") for i in range(1, 5)]:
            raise ValueError("Manifest contains a record for another player")
        if not row.get("url"):
            unavailable.append({"reason": "source_has_no_url", "record": row})
            continue
        u = urllib.parse.urlsplit(row["url"])
        ids = urllib.parse.parse_qs(u.query).get("log", [])
        if u.scheme not in ("http", "https") or u.hostname not in ("tenhou.net", "www.tenhou.net") or len(ids) != 1 or not LOG_ID.fullmatch(ids[0]):
            raise ValueError(f"Unexpected source URL: {row['url']!r}")
        records[ids[0]] = row
    return records, unavailable


def download(data_dir: Path, delay: float = 0.5, manifest: Path | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    manifest_url = API + "?" + urllib.parse.urlencode({"name": TARGET})
    if manifest:
        raw_manifest = manifest.read_bytes()
    else:
        for attempt in range(3):
            raw_manifest = request(manifest_url)
            reply = json.loads(raw_manifest)
            if not reply.get("retry"):
                break
            wait = float(reply["retry"])
            if not 0 <= wait <= 180 or attempt == 2:
                raise RuntimeError(f"Source requested a retry; retry={wait}")
            time.sleep(wait + 1)
    data = json.loads(raw_manifest)
    records, unavailable = manifest_ids(data)
    digest = hashlib.sha256(raw_manifest).hexdigest()
    write_atomic(data_dir / "manifests" / f"{digest}.json", raw_manifest)
    write_atomic(data_dir / "manifest.json", raw_manifest)
    statuses = {}
    stopped = None
    for i, (log_id, row) in enumerate(records.items(), 1):
        path = data_dir / "raw" / log_id[:4] / (log_id + ".xml.gz")
        try:
            if path.exists():
                try:
                    raw = gzip.decompress(path.read_bytes())
                    validate_xml(raw)
                except Exception:
                    path.replace(path.with_suffix(".corrupt"))
                    raise ValueError("Existing cached log is corrupt; quarantined. Run again to fetch it.")
            else:
                time.sleep(max(0.5, delay))
                raw = request("https://tenhou.net/0/log/?" + log_id)
                validate_xml(raw)
                write_atomic(path, gzip.compress(raw, mtime=0))
            statuses[log_id] = {"status": "downloaded", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "path": str(path.relative_to(data_dir))}
        except Exception as exc:
            statuses[log_id] = {"status": "failed", "error": str(exc)}
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
                stopped = f"Stopped on HTTP {exc.code}; no further requests sent"
                break
        if i % 50 == 0 or i == len(records):
            print(f"{i}/{len(records)} processed; {sum(s['status']=='downloaded' for s in statuses.values())} downloaded", flush=True)
    successes = sum(s["status"] == "downloaded" for s in statuses.values())
    report = {"target": TARGET, "captured_at": now, "source_url": manifest_url, "manifest_sha256": digest,
              "source_records": len(data["list"]), "unique_linked_logs": len(records), "records_without_url": len(unavailable),
              "downloaded": successes, "failed": sum(s["status"] == "failed" for s in statuses.values()),
              "not_attempted": len(records) - len(statuses), "all_linked_logs_downloaded": successes == len(records),
              "all_source_records_have_logs": successes == len(data["list"]) and not unavailable,
              "stopped": stopped, "logs": statuses, "unavailable": unavailable}
    write_atomic(data_dir / "download-report.json", json.dumps(report, ensure_ascii=False, indent=2).encode())
    print(json.dumps({k: v for k, v in report.items() if k not in ("logs", "unavailable")}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--manifest", type=Path, help="Reuse an existing manifest without contacting Nodocchi")
    args = ap.parse_args()
    result = download(args.data, args.delay, args.manifest)
    raise SystemExit(0 if result["all_linked_logs_downloaded"] else 1)
