from __future__ import annotations
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description='LuckyJ：具体场况、同形检索与可复核研究')
    p.add_argument('command',choices=['build','enrich','serve','native','mcp','precompute'])
    p.add_argument('--data',type=Path,default=Path('data'))
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8000)
    p.add_argument('--native',action='store_true',help='Compile optional local accelerator (requires a C compiler)')
    p.add_argument('--query',type=Path,help='JSON query file for precompute')
    p.add_argument('--good-budget',type=int,default=12000,help='Exact proof node budget; unfinished good-shape results stay null')
    p.add_argument('--all-candidates',action='store_true',help='Precompute full candidate metrics instead of actual-discard metrics')
    a=p.parse_args()
    if a.native or a.command=='native':
        from .native_accel import compile_native
        path=compile_native()
        if a.command=='native':print(path);return
    if a.command=='build':
        from .store import build
        report=build(a.data)
        raise SystemExit(0 if report['all_linked_logs_indexed'] else 1)
    if a.command=='enrich':
        from .enrich import enrich
        enrich(a.data);return
    if a.command=='mcp':
        from .mcp import serve_mcp
        serve_mcp(a.data,good_budget=a.good_budget);return
    if a.command=='precompute':
        from .research import Research
        from .store import unpacked
        from contextlib import closing
        r=Research(a.data,good_budget=a.good_budget);q=json.loads(a.query.read_text()) if a.query else {}
        cohort=r.cohort(q);unknown=0
        with closing(r.db()) as db:
            for n,i in enumerate(cohort['ids'],1):
                row=db.execute('SELECT d.*,r.facts FROM decisions d JOIN research.observations r ON r.id=d.id WHERE d.id=?',(i,)).fetchone()
                f=unpacked(row['facts']);metrics=r.metrics({**dict(row),'aka':f['aka']},a.all_candidates)
                unknown+=any(c['good_status']!='exact' for c in metrics['choices'])
                if n%100==0:print(f'{n}/{cohort["total"]} cached; {unknown} states have unresolved good-shape proofs',flush=True)
        report={'complete':True,'states':cohort['total'],'good_unresolved_states':unknown,'all_candidates':a.all_candidates,'recipe':cohort['recipe']}
        (a.data/'precompute-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False));return
    from .server import serve
    serve(a.data,a.host,a.port,good_budget=a.good_budget)


if __name__=='__main__':main()
