from __future__ import annotations
import argparse
from pathlib import Path
from .store import build
from .server import serve


def main():
    p=argparse.ArgumentParser(description='LuckyJ：具体场况与具体切牌数据库')
    p.add_argument('command',choices=['build','serve'])
    p.add_argument('--data',type=Path,default=Path('data'))
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8000)
    a=p.parse_args()
    if a.command=='build':
        report=build(a.data)
        raise SystemExit(0 if report['all_linked_logs_indexed'] else 1)
    serve(a.data,a.host,a.port)


if __name__=='__main__':
    main()
