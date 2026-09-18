"""Rebuildable SQLite index, parameterized concrete filters and coverage audit."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
import sqlite3
import time
import urllib.parse
import zlib
from collections import Counter
from pathlib import Path
from .parser import parse_game, VERSION, TARGET
from .tiles import parse, kind, normal

SCHEMA_VERSION = 1
BASE = ['log_id', 'round_seq', 'event_seq', 'wind', 'hand_no', 'honba', 'kyotaku',
        'seat_wind', 'turn', 'tsumogiri', 'riichi_declared', 'draw', 'discard', 'draw34', 'discard34',
        'sequence', 'sequence_norm']
SCORES = [p + str(i) for p in ('s', 'rk', 'bs', 'br') for i in range(4)]
COUNTS = ['h' + str(i) for i in range(34)] + ['red' + str(i) for i in range(3)]
COLUMNS = BASE + SCORES + COUNTS + ['snapshot']
TEXT_COLUMNS = {'log_id', 'draw', 'discard', 'sequence', 'sequence_norm'}


def packed(value: object) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), 6)


def unpacked(value: bytes):
    return json.loads(zlib.decompress(value))


def create_schema(db: sqlite3.Connection) -> None:
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE games(log_id TEXT PRIMARY KEY, started_at INTEGER, names TEXT NOT NULL,
      actor INTEGER NOT NULL, initial_dealer INTEGER NOT NULL, game_type INTEGER NOT NULL,
      sha256 TEXT NOT NULL, parser_version TEXT NOT NULL);
    CREATE TABLE rounds(log_id TEXT NOT NULL REFERENCES games(log_id), round_seq INTEGER NOT NULL,
      kyoku INTEGER NOT NULL, honba INTEGER NOT NULL, kyotaku INTEGER NOT NULL, dealer INTEGER NOT NULL,
      scores_start TEXT NOT NULL, events BLOB NOT NULL, results TEXT NOT NULL,
      PRIMARY KEY(log_id,round_seq));
    ''')
    defs = ','.join(f'{c} {"BLOB" if c == "snapshot" else "TEXT" if c in TEXT_COLUMNS else "INTEGER"}' for c in COLUMNS)
    db.execute(f'''CREATE TABLE decisions(id INTEGER PRIMARY KEY, {defs},
                FOREIGN KEY(log_id,round_seq) REFERENCES rounds(log_id,round_seq),
                UNIQUE(log_id,event_seq))''')
    db.execute('CREATE INDEX context_idx ON decisions(wind,hand_no,honba,rk0,s0)')
    db.execute('CREATE INDEX action_idx ON decisions(discard34,draw34)')
    db.execute('CREATE INDEX rank_score_idx ON decisions(rk0,s0)')
    db.execute('CREATE INDEX initial_rank_score_idx ON decisions(br0,bs0)')
    db.execute('CREATE INDEX round_idx ON decisions(log_id,round_seq,event_seq)')


def insert_game(db: sqlite3.Connection, game: dict, sha: str, started_at: int | None) -> None:
    g = game
    with db:
        db.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?,?)',
                   (g['log_id'], started_at, json.dumps(g['names'], ensure_ascii=False), g['actor'],
                    g['initial_dealer'], g['game_type'], sha, VERSION))
        db.executemany('INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?)',
                       [(g['log_id'], r['round_seq'], r['kyoku'], r['honba'], r['kyotaku'], r['dealer'],
                         json.dumps(r['scores_start']), packed(r['events']), json.dumps(r['results'], ensure_ascii=False))
                        for r in g['rounds']])
        values = []
        for d in g['decisions']:
            s = d['snapshot']
            row = {k: d[k] for k in BASE if k in d}
            row.update(draw=s['draw'], discard=s['discard'], draw34=kind(s['draw']) if s['draw'] else None,
                       discard34=kind(s['discard']), sequence='|' + '|'.join(s['sequence']) + '|',
                       sequence_norm='|' + '|'.join(map(normal, s['sequence'])) + '|', snapshot=packed(s))
            for prefix, source in [('s', 'scores'), ('rk', 'ranks'), ('bs', 'scores_start'), ('br', 'ranks_start')]:
                row.update({prefix + str(i): s[source][i] for i in range(4)})
            counts = Counter(kind(t) for t in s['hand'])
            row.update({'h' + str(i): counts[i] for i in range(34)})
            row.update({'red' + str(i): s['hand'].count('0' + suit) for i, suit in enumerate('mps')})
            values.append(tuple(row[c] for c in COLUMNS))
        db.executemany(f'INSERT INTO decisions({",".join(COLUMNS)}) VALUES({",".join("?" for _ in COLUMNS)})', values)


def build(data: Path) -> dict:
    """Atomic complete rebuild from the manifest; failed games never enter the index."""
    manifest_path = data / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get('name') != TARGET or not isinstance(manifest.get('list'), list):
        raise ValueError('Unexpected target/manifest')
    records = {}
    no_url = []
    for r in manifest['list']:
        if not r.get('url'):
            no_url.append(r)
            continue
        u = urllib.parse.urlsplit(r['url'])
        ids = urllib.parse.parse_qs(u.query).get('log', [])
        if u.hostname not in ('tenhou.net', 'www.tenhou.net') or len(ids) != 1 or not __import__('re').fullmatch(r'\d{10}gm-[0-9a-f]{4}-[0-9a-z]+-[0-9a-z]{8}', ids[0], __import__('re').I):
            raise ValueError('Unrecognized source log URL')
        records[ids[0]] = r
    download_report = {}
    if (data / 'download-report.json').exists():
        download_report = json.loads((data / 'download-report.json').read_bytes())
    expected = download_report.get('logs', {})
    temp = data / f'luckyj.build-{os.getpid()}.sqlite'
    if temp.exists():
        temp.unlink()
    db = sqlite3.connect(temp)
    errors, missing, indexed = [], [], 0
    try:
        create_schema(db)
        for i, (log_id, r) in enumerate(sorted(records.items()), 1):
            path = data / 'raw' / log_id[:4] / (log_id + '.xml.gz')
            if not path.exists():
                missing.append(log_id)
                continue
            try:
                with gzip.open(path, 'rb') as f:
                    raw = f.read(8_000_001)
                sha = hashlib.sha256(raw).hexdigest()
                required = expected.get(log_id, {}).get('sha256')
                if required and required != sha:
                    raise ValueError('Raw SHA-256 differs from download report')
                game = parse_game(raw, log_id)
                insert_game(db, game, sha, r.get('starttime'))
                indexed += 1
            except Exception as exc:
                errors.append({'log_id': log_id, 'error': f'{type(exc).__name__}: {exc}'})
            if i % 100 == 0:
                print(f'Index: {i}/{len(records)} processed, {indexed} valid, {len(errors)} errors', flush=True)
        report = {'schema_version': SCHEMA_VERSION, 'parser_version': VERSION, 'target': TARGET,
                  'built_at_unix': int(time.time()), 'source_records': len(manifest['list']),
                  'unique_linked_logs': len(records), 'records_without_url': len(no_url),
                  'indexed_logs': indexed, 'missing_raw_count': len(missing), 'index_error_count': len(errors),
                  'rounds': db.execute('SELECT count(*) FROM rounds').fetchone()[0],
                  'decisions': db.execute('SELECT count(*) FROM decisions').fetchone()[0],
                  'all_linked_logs_indexed': indexed == len(records) and bool(records),
                  'all_source_records_indexed': indexed == len(manifest['list']) and not no_url,
                  'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  'source_captured_at': download_report.get('captured_at'),
                  'missing_raw': missing, 'errors': errors, 'unavailable': no_url}
        db.executemany('INSERT INTO metadata VALUES(?,?)', [('schema_version', str(SCHEMA_VERSION)), ('report', json.dumps(report, ensure_ascii=False))])
        db.commit()
        integrity = db.execute('PRAGMA integrity_check').fetchall()
        if integrity != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError(f'SQLite integrity check failed: {integrity}')
        db.execute('ANALYZE')
        db.commit()
    finally:
        db.close()
    temp.replace(data / 'luckyj.sqlite')
    (data / 'index-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('missing_raw','errors','unavailable')}, ensure_ascii=False), flush=True)
    return report


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    if db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != str(SCHEMA_VERSION):
        db.close()
        raise ValueError('数据库版本不匹配，请重新 build')
    return db


def filters(q: dict[str, str]) -> tuple[str, list]:
    known = {'basis','red','hand','hand_mode','draw','discard','sequence','wind','hand_no','honba_min','honba_max',
             'seat_wind','turn_min','turn_max','tsumogiri','riichi_declared','after','limit','log_id','round_seq'}
    known.update(f'{prefix}{i}{suffix}' for i in range(4) for prefix,suffixes in [('rank',['']),('score',['_min','_max'])] for suffix in suffixes)
    if set(q) - known:
        raise ValueError('未知筛选字段: ' + ','.join(sorted(set(q) - known)))
    q = {k:v for k,v in q.items() if v != ''}
    if q.get('basis', 'decision') not in ('decision','round_start') or q.get('red','1') not in ('0','1'):
        raise ValueError('分数口径或赤牌选项无效')
    if q.get('hand_mode','contains') not in ('contains','exact'):
        raise ValueError('手牌匹配模式无效')
    clauses, args = [], []
    def add(col, op, val):
        clauses.append(f'{col} {op} ?')
        args.append(val)
    def number(key, lo, hi):
        if key not in q:
            return None
        try:
            n = int(q[key])
        except (ValueError, TypeError):
            raise ValueError(f'{key} 必须是整数') from None
        if not lo <= n <= hi:
            raise ValueError(f'{key} 超出范围 {lo}..{hi}')
        return n
    for k, bounds in [('wind',(0,3)),('hand_no',(1,4)),('seat_wind',(0,3)),('tsumogiri',(0,1)),('riichi_declared',(0,1)),('round_seq',(0,9999))]:
        value = number(k,*bounds)
        if value is not None:
            add(k,'=',value)
    for base, lo, hi in [('honba',0,100),('turn',1,100)]:
        low, high = number(base+'_min',lo,hi), number(base+'_max',lo,hi)
        if low is not None and high is not None and low > high:
            raise ValueError(base + ' 最小值不能大于最大值')
        if low is not None: add(base,'>=',low)
        if high is not None: add(base,'<=',high)
    initial = q.get('basis') == 'round_start'
    for i in range(4):
        rank = number(f'rank{i}',1,4)
        if rank is not None: add(('br' if initial else 'rk')+str(i),'=',rank)
        lo = number(f'score{i}_min',-1000000,1000000)
        hi = number(f'score{i}_max',-1000000,1000000)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('分数最小值不能大于最大值')
        if lo is not None: add(('bs' if initial else 's')+str(i),'>=',lo)
        if hi is not None: add(('bs' if initial else 's')+str(i),'<=',hi)
    red = q.get('red','1') == '1'
    for field in ('draw','discard'):
        if field in q:
            tiles = parse(q[field])
            if len(tiles) != 1:
                raise ValueError('摸牌/切牌条件必须恰好一张牌')
            add(field if red else field+'34','=',tiles[0] if red else kind(tiles[0]))
    if 'hand' in q:
        tiles = parse(q['hand'])
        counts = Counter(kind(t) for t in tiles)
        reds = [tiles.count('0'+s) for s in 'mps']
        exact = q.get('hand_mode','contains') == 'exact'
        for i in range(34):
            if counts[i] or exact:
                if red and i in (4,13,22):
                    r = (i - 4) // 9
                    add(f'(h{i}-red{r})', '=' if exact else '>=',counts[i]-reds[r])
                else:
                    add(f'h{i}', '=' if exact else '>=',counts[i])
        if red:
            for i in range(3):
                if reds[i] or exact: add(f'red{i}', '=' if exact else '>=',reds[i])
    if 'sequence' in q:
        seq = parse(q['sequence'],sequence=True)
        if not red: seq = list(map(normal,seq))
        add('sequence' if red else 'sequence_norm','LIKE','%|'+'|'.join(seq)+'|')
    if 'log_id' in q:
        if len(q['log_id']) > 100: raise ValueError('log_id 过长')
        add('log_id','=',q['log_id'])
    number('after',0,2**63-1)
    number('limit',1,100)
    return ' AND '.join(clauses) or '1', args


def summary(row: sqlite3.Row, basis='decision') -> dict:
    s = unpacked(row['snapshot'])
    fields = ['id','log_id','round_seq','event_seq','wind','hand_no','honba','kyotaku','seat_wind','turn','tsumogiri','riichi_declared','draw','discard']
    result = {k:row[k] for k in fields}
    result.update(hand=s['hand'], scores=s['scores_start' if basis == 'round_start' else 'scores'],
                  ranks=s['ranks_start' if basis == 'round_start' else 'ranks'], melds=s['melds'][0])
    return result


def search(db: sqlite3.Connection, query: dict[str,str]) -> dict:
    where, args = filters(query)
    total = db.execute('SELECT count(*) FROM decisions WHERE '+where,args).fetchone()[0]
    after, limit = int(query.get('after') or 0), int(query.get('limit') or 30)
    rows = db.execute('SELECT * FROM decisions WHERE '+where+' AND id > ? ORDER BY id LIMIT ?',args+[after,limit+1]).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {'total':total, 'items':[summary(r,query.get('basis','decision')) for r in rows],
            'next_after': rows[-1]['id'] if has_more else None}


def detail(db: sqlite3.Connection, decision_id: int) -> dict | None:
    row = db.execute('SELECT * FROM decisions WHERE id=?',(decision_id,)).fetchone()
    if row is None: return None
    result = summary(row)
    result['snapshot'] = unpacked(row['snapshot'])
    g = db.execute('SELECT actor,started_at FROM games WHERE log_id=?',(row['log_id'],)).fetchone()
    result['tenhou_url'] = 'https://tenhou.net/3/?' + urllib.parse.urlencode({'log':row['log_id'],'tw':g['actor']})
    result['started_at'] = g['started_at']
    for name,op,sort in [('previous','<','DESC'),('next','>','ASC')]:
        n = db.execute(f'SELECT id FROM decisions WHERE log_id=? AND round_seq=? AND event_seq {op} ? ORDER BY event_seq {sort} LIMIT 1',
                       (row['log_id'],row['round_seq'],row['event_seq'])).fetchone()
        result[name] = n[0] if n else None
    return result
