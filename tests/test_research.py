"""Research regressions. Synthetic games never become corpus observations."""
from contextlib import closing, redirect_stdout
import copy
import gzip
import io
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import urllib.request
import urllib.error

from test_core import fixture, TARGET
from luckyj.analysis import counts, facts, legal_discards, analyze, roles, TOKENS, INDEX, ryanmen_waits
from luckyj.patterns import Matcher, parse_pattern, parse_supply, map_token
from luckyj.shanten import shanten, regular, _Regular
from luckyj.store import build
from luckyj.enrich import enrich, file_sha
from luckyj.research import Research, ActionPredicate, QueryLimitError, MetricUnknownError, validate
from luckyj.mcp import serve_mcp
from luckyj.server import handler
from luckyj.tiles import parse, kind


def snapshot(text,discard=None):
    ts=parse(text)
    return {'hand':ts,'discard':discard or ts[-1],'draw':ts[-1],
            'rivers':[[],[],[],[]],'melds':[[],[],[],[]],
            'riichi':[False]*4,'riichi_pending':[False]*4,'dora_indicators':[]}


def physical(text,avoid=()):
    used=set(avoid);out=[]
    for t in parse(text):
        base=4*kind(t)
        choices=[base] if t.startswith('0') else list(range(base+(kind(t) in (4,13,22)),base+4))
        tile=next(x for x in choices if x not in used)
        out.append(tile);used.add(tile)
    return out


def sample_database(data):
    data=Path(data);(data/'raw/2023').mkdir(parents=True,exist_ok=True)
    samples=[
      (fixture('<T132/><D0/><T128/><D128/>',hand=physical('1123m456p3479s55z'),reserved=(132,128)), '00000001'),
      (fixture('<T112/><REACH who="0" step="1"/><D112/><REACH who="0" step="2" ten="240,250,250,250"/><T116/><D116/>',hand=physical('123m456p789s111z5z'),reserved=(112,116)), '00000002'),
      (fixture('<T132/><D132/><U100/><E117/><T128/><D116/>',hand=physical('14m4569p3479s3z55z'),reserved=(100,128,132),source=(1,117)), '00000003'),
    ]
    rows=[]
    for raw,end in samples:
        log='2023041911gm-0001-0000-'+end
        (data/'raw/2023'/(log+'.xml.gz')).write_bytes(gzip.compress(raw,mtime=0))
        rows.append({'player1':TARGET,'url':'https://tenhou.net/0/?log='+log})
    (data/'manifest.json').write_text(json.dumps({'name':TARGET,'list':rows}),encoding='utf8')
    with redirect_stdout(io.StringIO()):
        build(data);enrich(data)


class SupplyTests(unittest.TestCase):
    def test_loss_and_remaining(self):
        s=snapshot('11123455678999m','5m')
        s['rivers'][1]=[{'tile':t,'called_by':None} for t in ('7m','7m','8m','8m')]
        f=facts(s)
        self.assertEqual((f['loss34'][6],f['remaining34'][6],f['remaining34'][7]),(2,1,1))
        r=analyze(s,only_actual=True)['selected']
        self.assertEqual((r['shanten'],r['ukeire']),(0,18))
        self.assertEqual(next(x for x in r['incoming'] if x['tile']=='5m')['remaining'],2)
    def test_called_tile_counted_once(self):
        s=snapshot('123m')
        s['rivers'][1]=[{'tile':'3p','called_by':2}]
        s['melds'][2]=[{'kind':'chi','tiles':['2p','3p','4p'],'called':'3p'}]
        f=facts(s)
        self.assertEqual(f['loss34'][11],1)
        self.assertEqual(f['remaining34'][11],3)
    def test_red_capacity_and_indicator_not_dora(self):
        s=snapshot('0m5m');s['dora_indicators']=['4m']
        f=facts(s)
        self.assertEqual((f['remaining37'][34],f['remaining37'][4]),(0,2))
        self.assertEqual((f['dora_count'],f['loss34'][3],f['loss34'][4]),(2,1,0))
    def test_four_copy_supply_rejected(self):
        s=snapshot('1111m');s['dora_indicators']=['1m']
        with self.assertRaises(ValueError):facts(s)
    def test_range_syntax(self):
        c=parse_supply('7m-[1..2] 8m+[0..1]')
        self.assertEqual((c[0].source,c[0].low,c[1].high),('loss',1,1))
        for value in ('1z+5','8z-1','7m-[2..1]','7m+','7m--2'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_supply(value)


class ShapeTests(unittest.TestCase):
    def match(self,query,text,discard=None,wind=0,seat=3,mutate=None):
        s=snapshot(text,discard)
        if mutate:mutate(s)
        f=facts(s)
        return Matcher(query).matches(f['hand37'],f,wind,seat,s['draw'],s['discard'])
    def test_partial_full_wildcard(self):
        self.assertIsNotNone(self.match({'hand':'23m'},'123m456p789s11223z'))
        self.assertIsNone(self.match({'hand':'x{12}77z'},'123m456p789s77z'))
        self.assertIsNotNone(self.match({'hand':'x{12}77z'},'123m456p789s12377z'))
    def test_overlapping_slots_consume_distinct_copies(self):
        p=parse_pattern('(1-2m)(2-3m)')
        s=snapshot('2m9p');self.assertFalse(p.accepts(facts(s)['hand37']))
        s=snapshot('22m');self.assertTrue(p.accepts(facts(s)['hand37']))
    def test_overlapping_backtracking_assignment(self):
        p=parse_pattern('(1m|2m)(1m|3m)(1m|3m)')
        self.assertTrue(p.accepts(facts(snapshot('123m'))['hand37']))
        self.assertFalse(p.accepts(facts(snapshot('122m'))['hand37']))
    def test_suit_permutation_and_whole_suit_consistency(self):
        self.assertIsNotNone(self.match({'hand':'12355m'},'12355p'))
        self.assertIsNone(self.match({'hand':'12355m'},'123p55s'))
        self.assertIsNone(self.match({'hand':'12355m','suits':'0'},'12355p'))
    def test_reversal_opt_in_no_translation(self):
        self.assertIsNone(self.match({'hand':'12344m'},'66789p'))
        self.assertIsNotNone(self.match({'hand':'12344m','reverse':'1'},'66789p'))
        self.assertIsNone(self.match({'hand':'12344m','reverse':'1'},'23455p'))
    def test_honor_role_renaming(self):
        m=self.match({'hand':'1z3z'},'2z5z',wind=0,seat=3)
        self.assertIsNotNone(m)
        self.assertEqual((map_token('5z',m['map']),map_token('2z',m['map'])),('1z','3z'))
        self.assertIsNone(self.match({'hand':'11z'},'5z6z'))
        self.assertIsNone(self.match({'hand':'1z','honor_roles':'2,0,0,0,1,1,1'},'5z'))
    def test_joint_constraints_share_map(self):
        s=snapshot('123m456p','1m');s['rivers'][1]=[{'tile':'1m','called_by':None}]*2
        f=facts(s)
        self.assertIsNotNone(Matcher({'hand':'123p','supply':'1p-2'}).matches(f['hand37'],f,0,3,None,'1m'))
        self.assertIsNone(Matcher({'hand':'123p','supply':'1m-2'}).matches(f['hand37'],f,0,3,None,'1m'))
    def test_dora_position_is_value_and_same_map(self):
        s=snapshot('123p');s['dora_indicators']=['2p'];f=facts(s)
        m=Matcher({'hand':'123m','dora_position':'1','dora_tiles':'3m'})
        self.assertIsNotNone(m.matches(f['hand37'],f,0,3,None,'1p'))
        with self.assertRaises(ValueError):Matcher({'dora_tiles':'3m'})
    def test_red_explicit_and_optional(self):
        self.assertIsNotNone(self.match({'hand':'5m'},'0p'))
        self.assertIsNone(self.match({'hand':'5m','red':'1'},'0p'))
        self.assertIsNone(self.match({'hand':'0m'},'5p'))
    def test_self_symmetry_reports_joint_action_not_duplication(self):
        q={'hand':'11123455678999m','reverse':'1'}
        m=self.match(q,'11123455678999p','3p')
        self.assertEqual(m['possible_discards'],['3m','7m'])
        self.assertGreater(m['mapping_count'],1)
    def test_empty_query_has_unanchored_actions(self):
        m=self.match({},'0m')
        self.assertEqual(m['possible_discards'],['5m','5p','5s'])
        self.assertIn('未锚定',self.match({},'2z')['possible_discards'][0])
    def test_match_deadline(self):
        s=snapshot('123m');f=facts(s)
        with self.assertRaises(TimeoutError):Matcher({'hand':'123m'}).matches(f['hand37'],f,0,3,None,'1m',deadline=0)
    def test_invalid_patterns(self):
        for value in ('x{0}','x{15}','(3-1m)','(1m|bad)','0z','11111m','00m','123m junk'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_pattern(value)


class EfficiencyTests(unittest.TestCase):
    def test_three_shanten_types(self):
        for text,expected in [('11123455678999m',-1),('1122334455667z',0),('19m19p19s1234567z',0),('1111m111122233z',1)]:
            with self.subTest(text=text):self.assertEqual(shanten(counts(parse(text))),expected)
        self.assertEqual(shanten(counts(parse('1m'))),0)
    def test_ryanmen_exact(self):
        self.assertEqual(ryanmen_waits(counts(parse('23m456p789s111z55z'))),(0,3))
        self.assertEqual(ryanmen_waits(counts(parse('13m456p789s111z55z'))),())
    def test_weighted_good_ukeire(self):
        s=snapshot('1123m456p3479s55z7z','1m')
        a=analyze(s,only_actual=True)['selected']
        self.assertEqual((a['shanten'],a['ukeire'],a['good_ukeire']),(1,12,4))
        self.assertEqual([x['tile'] for x in a['incoming'] if x['good']],['8s'])
        s['rivers'][1]=[{'tile':'8s','called_by':None}]*2
        a=analyze(s,only_actual=True)['selected'];self.assertEqual((a['ukeire'],a['good_ukeire']),(10,2))
    def test_budget_unknown_is_not_zero(self):
        a=analyze(snapshot('1123m456p3479s55z7z','1m'),only_actual=True,good_budget=0)['selected']
        self.assertIsNone(a['good_ukeire']);self.assertEqual(a['good_status'],'budget_exceeded')
        self.assertIsNone(a['max_ukeire']);self.assertIsNone(a['max_good'])
    def test_kuikae(self):
        s=snapshot('124m7p');s['draw']=None
        s['melds'][0]=[{'kind':'chi','tiles':['1m','2m','3m'],'called':'1m'}]
        self.assertEqual(set(legal_discards(s)),{'2m','7p'})
        s['melds'][0]=[{'kind':'pon','tiles':['5p']*3,'called':'5p'}]
        s['hand']=['0p','5p','7p'];self.assertEqual(legal_discards(s),['7p'])
    def test_riichi_locked_and_pending(self):
        s=snapshot('123m456p789s111z5z2z','2z');s['riichi_pending'][0]=True
        self.assertIn('2z',legal_discards(s));self.assertNotIn('1m',legal_discards(s))
        s['riichi'][0]=True;self.assertEqual(legal_discards(s),['2z'])
    def test_all_ties_are_maximum(self):
        a=analyze(snapshot('1123m456p3479s55z7z','1m'))
        m=a['best_after_shanten'];u=a['best_ukeire']
        self.assertEqual(a['max_ukeire_ties'],sum(c['shanten']==m and c['ukeire']==u for c in a['choices']))
    def test_native_matches_python_random(self):
        rng=random.Random(7301)
        for _ in range(500):
            n=rng.choice((1,2,4,5,7,8,10,11,13,14));ids=rng.sample(range(136),n)
            h=[0]*34
            for i in ids:h[i//4]+=1
            self.assertEqual(regular(tuple(h)),_Regular(tuple(h)).calculate(n))


class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.data=Path(cls.tmp.name);sample_database(cls.data);cls.engine=Research(cls.data)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_full_cohort_and_locked_default(self):
        a=self.engine.stats({});b=self.engine.stats({'exclude_locked':'0'})
        self.assertEqual((a['total'],b['total']),(5,6))
        self.assertEqual(sum(a['normalized'].values()),a['total'])
        self.assertEqual(sum(a['raw'].values()),a['total'])
    def test_histogram_denominator_pagination(self):
        s=self.engine.stats({});label=next(iter(s['normalized']))
        a=self.engine.search({'bucket':label,'limit':1,'metrics':0})
        self.assertEqual(a['cohort_total'],5);self.assertEqual(a['total'],s['normalized'][label])
        if a['next_after']:
            b=self.engine.search({'bucket':label,'limit':1,'metrics':0,'after':a['next_after']})
            self.assertNotEqual(a['items'][0]['id'],b['items'][0]['id'])
    def test_initial_shanten_independent_of_actual_action(self):
        a=self.engine.search({'limit':100,'metrics':0})
        self.assertTrue(all('initial' in x['shanten'] for x in a['items']))
        with closing(self.engine.db()) as db:
            values=db.execute('SELECT initial_shanten FROM research.observations WHERE id IN (3,4)').fetchall()
        self.assertEqual([r[0] for r in values],[0,0])
    def test_compare_common_opportunities(self):
        body={'filters':{'turn_max':2},'a':{'kind':'follow_honor','role':0},'b':{'kind':'local'}}
        c=self.engine.compare(body)
        self.assertEqual(sum(c['raw_choice_counts'].values()),5)
        self.assertEqual(c['common_opportunities'],1);self.assertEqual(c['common_choice_counts'],{'a':1})
        self.assertEqual(sum(c['common_choice_rates'].values()),1)
    def test_follow_is_actual_identity_not_role_alias(self):
        f={'legal_discards':['2z','3z'],'hand34':list(counts(['2z','3z'])),'roles':[1,0,0,1,1,1,1],
           'follow_honors':[{'tile':'2z'}]}
        self.assertEqual(ActionPredicate({'kind':'follow_honor'}).available(f),{28})
    def test_query_rejects_code_and_invalid_ranges(self):
        for q in (None,[],{'sql':'select 1'},{'shanten':'x'},{'shanten':2,'shanten_min':1},{'riichi_min':4},{'max_good':2},{'hand':'x{15}'},{'good_min':4,'good_max':1}):
            with self.subTest(q=q),self.assertRaises(ValueError):validate(q)
    def test_timeout_no_partial_result_or_cache(self):
        with self.assertRaises(QueryLimitError):self.engine.cohort({'hand':'x{3}','reverse':'1'},seconds=-1)
    def test_unknown_metric_not_silently_excluded(self):
        with patch.object(self.engine,'metrics',return_value={'selected':{'max_good':None}}):
            with self.assertRaises(MetricUnknownError):self.engine._metric_filter({}, {'max_good':'1'})
    def test_detail_has_all_candidates_and_original_state(self):
        d=self.engine.decision(1,{'hand':'23p'})
        self.assertEqual(d['analysis']['scope'],'all_legal');self.assertIn('rivers',d['snapshot'])
        self.assertEqual(d['analysis']['selected']['tile'],d['discard'])
        self.assertIsNotNone(d['alignment'])
    def test_source_index_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);sample_database(p)
            with sqlite3.connect(p/'luckyj.sqlite') as db:db.execute("INSERT INTO metadata VALUES('changed','1')")
            with self.assertRaisesRegex(ValueError,'过期'):Research(p).stats({})
    def test_mcp_protocol_and_readonly_tools(self):
        requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'luckyj_stats','arguments':{}}},
                  {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'luckyj_stats','arguments':{'sql':'DROP'}}}]
        output=io.StringIO();serve_mcp(self.data,io.StringIO('\n'.join(map(json.dumps,requests))+'\n'),output)
        messages=list(map(json.loads,output.getvalue().splitlines()))
        self.assertEqual(len(messages),4);self.assertEqual(messages[1]['result']['tools'][0]['annotations']['readOnlyHint'],True)
        result=json.loads(messages[2]['result']['content'][0]['text']);self.assertEqual(result['total'],5)
        self.assertTrue(messages[3]['result']['isError'])
    def test_http_research_and_origin_boundary(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.data));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ('/research','/research.js','/research.css','/api/research/schema','/api/research/stats','/api/research/search?metrics=0','/api/research/decisions/1'):
                with urllib.request.urlopen(base+path) as r:self.assertEqual(r.status,200)
            req=urllib.request.Request(base+'/api/research/compare',json.dumps({'a':{'kind':'local'},'b':{'kind':'follow_honor'}}).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['common_opportunities'],1)
            req.add_header('Origin','https://untrusted.example')
            with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
            self.assertEqual(e.exception.code,403)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
