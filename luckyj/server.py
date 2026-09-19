"""Local read-only UI/API. Use a proper reverse proxy for public deployment."""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .store import connect, search, detail

STATIC = Path(__file__).parent / 'static'


def handler(data: Path, good_budget=12000):
    from .research import Research, QueryLimitError, MetricUnknownError, schema
    import threading
    engine = None
    engine_lock = threading.Lock()
    def research():
        nonlocal engine
        with engine_lock:
            if engine is None: engine = Research(data,good_budget=good_budget)
            return engine
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, mime='application/json; charset=utf-8'):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            if isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if len(self.path) > 8192:
                return self.send(414,{'error':'请求过长'})
            url = urlsplit(self.path)
            static = {'/research':('research.html','text/html; charset=utf-8'),
                      '/research.js':('research.js','text/javascript; charset=utf-8'),
                      '/extensions.js':('extensions.js','text/javascript; charset=utf-8'),
                      '/extensions.css':('extensions.css','text/css; charset=utf-8'),
                      '/research.css':('research.css','text/css; charset=utf-8'),
                      '/':('index.html','text/html; charset=utf-8'),
                      '/app.js':('app.js','text/javascript; charset=utf-8'),
                      '/style.css':('style.css','text/css; charset=utf-8')}
            if url.path in static:
                file,mime = static[url.path]
                return self.send(200,(STATIC/file).read_bytes(),mime)
            if url.path == '/favicon.ico':
                return self.send(204,b'','image/x-icon')
            if not url.path.startswith('/api/'):
                return self.send(404,{'error':'未找到'})
            if not (data/'luckyj.sqlite').exists():
                return self.send(503,{'error':'尚无数据库。请先下载牌谱并执行 python -m luckyj build。'})
            try:
                q_multi = parse_qs(url.query,keep_blank_values=True,max_num_fields=60)
                if any(len(v) != 1 for v in q_multi.values()):
                    raise ValueError('不允许重复筛选字段')
                q = {k:v[0] for k,v in q_multi.items()}
                if url.path.startswith('/api/research/'):
                    if url.path in ('/api/research/opportunities','/api/research/call-events'):
                        from .call_queries import opportunities, call_events
                        return self.send(200,(opportunities if url.path.endswith('opportunities') else call_events)(research(),q))
                    if url.path == '/api/research/schema': return self.send(200,schema())
                    if url.path == '/api/research/search': return self.send(200,research().search(q))
                    if url.path == '/api/research/stats': return self.send(200,research().stats(q))
                    if url.path.startswith('/api/research/decisions/'):
                        identifier=url.path.rsplit('/',1)[-1]
                        if not identifier.isdigit() or len(identifier)>18:raise ValueError('切牌 ID 无效')
                        result=research().decision(int(identifier),q)
                        return self.send(200,result) if result else self.send(404,{'error':'未找到'})
                    return self.send(404,{'error':'未找到研究接口'})
                with closing(connect(data/'luckyj.sqlite')) as db:
                    if url.path == '/api/status':
                        report = json.loads(db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()[0])
                        return self.send(200,{k:v for k,v in report.items() if k not in ('errors','missing_raw','unavailable')})
                    if url.path == '/api/coverage':
                        return self.send(200,json.loads(db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()[0]))
                    if url.path == '/api/search':
                        return self.send(200,search(db,q))
                    if url.path == '/api/resolve':
                        if set(q) != {'log_id','event_seq'} or len(q['log_id']) > 100 or not q['event_seq'].isdigit() or len(q['event_seq']) > 9:
                            raise ValueError('原谱定位参数无效')
                        row = db.execute('SELECT id FROM decisions WHERE log_id=? AND event_seq=?',(q['log_id'],int(q['event_seq']))).fetchone()
                        return self.send(200,detail(db,row[0])) if row else self.send(404,{'error':'未找到该次切牌'})
                    if url.path.startswith('/api/decisions/'):

                        value = url.path.rsplit('/',1)[-1]
                        if not value.isdigit() or len(value) > 18:
                            raise ValueError('切牌 ID 无效')
                        d = detail(db,int(value))
                        return self.send(200,d) if d else self.send(404,{'error':'未找到该次切牌'})
                    return self.send(404,{'error':'未找到接口'})
            except ValueError as exc:
                return self.send(400,{'error':str(exc)})
            except (QueryLimitError,MetricUnknownError) as exc:
                return self.send(503,{'complete':False,'error':str(exc)})
            except sqlite3.Error:
                return self.send(500,{'error':'数据库读取失败；请检查版本与文件完整性'})

        def do_POST(self):
            if urlsplit(self.path).path!='/api/research/compare':return self.send(404,{'error':'未找到'})
            try:
                # Local JSON endpoint: reject cross-origin browser requests.
                origin=self.headers.get('Origin')
                if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
                    return self.send(403,{'error':'跨来源请求被拒绝'})
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    return self.send(415,{'error':'使用 application/json'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:return self.send(413,{'error':'请求体长度无效'})
                self.connection.settimeout(10)
                body=json.loads(self.rfile.read(length))
                return self.send(200,research().compare(body))
            except (ValueError,TypeError) as exc:return self.send(400,{'error':str(exc)})
            except TimeoutError:return self.send(408,{'error':'请求体读取超时'})
            except (QueryLimitError,MetricUnknownError) as exc:return self.send(503,{'complete':False,'error':str(exc)})
            except sqlite3.Error:return self.send(503,{'complete':False,'error':'数据库读取失败或查询超时'})

        def log_message(self, fmt, *args):
            print(fmt % args)
    return Handler


def serve(data: Path, host='127.0.0.1', port=8000, good_budget=12000):
    print(f'LuckyJ research: http://{host}:{port}/research  |  legacy: /  |  data={data.resolve()}', flush=True)
    with ThreadingHTTPServer((host,port),handler(data,good_budget=good_budget)) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
