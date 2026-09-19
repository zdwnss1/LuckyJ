"""Minimal stdio MCP server (2025-06-18), read-only LuckyJ tools.

Line-delimited JSON-RPC; stdout contains protocol messages only. This is not
an HTTP MCP gateway and does not collect API keys or choose an LLM provider.
"""
import json
import sys
from .research import Research, KNOWN, schema
from .call_queries import FIELDS as CALL_FIELDS, opportunities, call_events

QUERY_SCHEMA={'type':'object','properties':{k:{'type':['string','integer']} for k in sorted(KNOWN)},'additionalProperties':False}
EMPTY={'type':'object','properties':{},'additionalProperties':False}
CALL_SCHEMA={'type':'object','properties':{k:{'type':['string','integer']} for k in sorted(CALL_FIELDS)},'additionalProperties':False}
TOOLS=[
    ('luckyj_opportunities','Pre-call/kan opportunities with selected, registered no-call and censored outcomes. Read schema; one trigger is one opportunity.',CALL_SCHEMA),
    ('luckyj_call_events','Actual chi/pon/kan events with river-source provenance. Not a call-rate denominator.',CALL_SCHEMA),
    ('luckyj_schema','Read metric definitions and query syntax before statistical analysis.',EMPTY),
    ('luckyj_search','Find actual decisions. A result page is NOT a statistical sample. Use stats for complete counts.',QUERY_SCHEMA),
    ('luckyj_stats','Exact full-cohort original/aligned discard counts with source hash and recipe; fails rather than returns partial counts.',QUERY_SCHEMA),
    ('luckyj_decision','Inspect one actual pre-discard state and all legal discard candidates.',{'type':'object','properties':{'id':{'type':'integer','minimum':1},'query':QUERY_SCHEMA},'required':['id'],'additionalProperties':False}),
    ('luckyj_compare','Compare follow_honor and/or local action definitions, both raw frequency and common legal opportunities. No causal conclusion.',{'type':'object','properties':{'filters':QUERY_SCHEMA,'a':{'type':'object'},'b':{'type':'object'}},'required':['a','b'],'additionalProperties':False}),
]

TOOLS.sort(key=lambda t: 0 if t[0]=='luckyj_schema' else 1)

def serve_mcp(data, stdin=None, stdout=None, good_budget=12000):
    stdin=stdin or sys.stdin;stdout=stdout or sys.stdout;engine=Research(data,good_budget=good_budget);initialized=False
    def send(message):
        stdout.write(json.dumps(message,ensure_ascii=False,separators=(',',':'))+'\n');stdout.flush()
    while True:
        line=stdin.readline(65537)
        if not line:break
        identifier=None
        try:
            if len(line)>65536:
                while line and not line.endswith('\n'):line=stdin.readline(65537)
                raise ValueError('MCP message too large')
            msg=json.loads(line)
            if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0' or not isinstance(msg.get('method'),str):raise ValueError('Invalid JSON-RPC request')
            identifier=msg.get('id');method=msg['method'];params=msg.get('params',{})
            if 'id' not in msg:continue
            if not isinstance(params,dict):raise ValueError('params must be an object')
            if method=='initialize':
                version=params.get('protocolVersion')
                if version not in ('2024-11-05','2025-03-26','2025-06-18'):version='2025-06-18'
                initialized=True
                result={'protocolVersion':version,'capabilities':{'tools':{'listChanged':False}},
                        'serverInfo':{'name':'LuckyJ Research','version':'0.2.0'},
                        'instructions':'Read luckyj_schema. Use full-cohort stats/compare, not page counts. Report recipes and distinguish common opportunities from raw frequency.'}
            elif method=='ping':result={}
            elif not initialized:raise ValueError('initialize first')
            elif method=='tools/list':
                result={'tools':[{'name':n,'description':d,'inputSchema':s,'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}} for n,d,s in TOOLS]}
            elif method=='tools/call':
                name=params.get('name');args=params.get('arguments',{})
                if not isinstance(args,dict):raise ValueError('arguments must be an object')
                try:
                    if name=='luckyj_schema':
                        if args:raise ValueError('schema accepts no arguments')
                        value=schema()
                    elif name=='luckyj_opportunities':value=opportunities(engine,args)
                    elif name=='luckyj_call_events':value=call_events(engine,args)
                    elif name=='luckyj_search':value=engine.search(args)
                    elif name=='luckyj_stats':value=engine.stats(args)
                    elif name=='luckyj_compare':value=engine.compare(args)
                    elif name=='luckyj_decision':
                        if set(args)-{'id','query'} or type(args.get('id')) is not int or args['id']<1:raise ValueError('decision requires positive id')
                        value=engine.decision(args['id'],args.get('query'))
                        if value is None:raise ValueError('Decision not found')
                    else:raise ValueError('Unknown tool')
                    result={'content':[{'type':'text','text':json.dumps(value,ensure_ascii=False)}],'isError':False}
                except Exception as exc:
                    result={'content':[{'type':'text','text':json.dumps({'complete':False,'error':str(exc)},ensure_ascii=False)}],'isError':True}
            else:
                send({'jsonrpc':'2.0','id':identifier,'error':{'code':-32601,'message':'Method not found'}});continue
            send({'jsonrpc':'2.0','id':identifier,'result':result})
        except (ValueError,TypeError) as exc:
            send({'jsonrpc':'2.0','id':identifier,'error':{'code':-32602,'message':str(exc)}})
