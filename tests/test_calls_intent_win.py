"""Meld grammar, response opportunity, source links, conditional value and inference tests."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_core import fixture
from test_research import snapshot, physical, sample_database
from luckyj.analysis import facts, counts
from luckyj.tiles import token
from luckyj.meld_patterns import Matcher
from luckyj.call_index import index_round, enumerate_calls, enumerate_kans
from luckyj.win_projection import project, matches, calculator_status
from luckyj.intent import load_model, infer_round, features
from luckyj.research import Research, validate
from luckyj.call_queries import opportunities, call_events


def indexed(raw,actor=0):
    es=[{'tag':e.tag,'attributes':dict(e.attrib),'event_seq':i} for i,e in enumerate(ET.fromstring(raw))]
    return index_round(es,actor,'2023041911gm-0001-0000-00000001',0,1)


def material(text, ms=()):
    s=snapshot(text);s['melds'][0]=list(ms);f=facts(s);f['own_melds']=list(ms)
    return s,f


class CompositeTests(unittest.TestCase):
    def test_types_and_same_suit_mapping(self):
        ms=[{'kind':'chi','tiles':['4p','5p','6p'],'called':'6p'}]
        s,f=material('123p44s1z',ms)
        def test(q):return Matcher(q).matches(f['hand37'],f,0,3,None,'1p')
        self.assertIsNotNone(test({'hand':'123m [c:456m@6m]'}))
        self.assertIsNone(test({'hand':'123m [c:456p@6p]'}))
        self.assertIsNone(test({'hand':'[p]'}))
        self.assertIsNotNone(test({'hand':'[c]'}))
    def test_bracket_unknown_and_number_shorthand(self):
        s,f=material('123m45p1z',[{'kind':'pon','tiles':['9s']*3,'called':'9s'}])
        for text in ('123 [p]','123m [999]','[999s]'):
            with self.subTest(text=text):self.assertIsNotNone(Matcher({'hand':text}).matches(f['hand37'],f,0,3,None,'1m'))
        with self.assertRaisesRegex(ValueError,'最多11'):Matcher({'hand':'111234565678 [999]'})
    def test_meld_exact_and_distinct_slots(self):
        s,f=material('123m',[{'kind':'pon','tiles':['9s']*3,'called':'9s'}])
        self.assertIsNone(Matcher({'hand':'[p][p]'}).matches(f['hand37'],f,0,3))
        self.assertIsNone(Matcher({'meld_mode':'exact'}).matches(f['hand37'],f,0,3))
        self.assertIsNotNone(Matcher({'hand':'[p]','meld_mode':'exact'}).matches(f['hand37'],f,0,3))
    def test_honors_share_identity_between_hand_and_meld(self):
        s,f=material('6z',[{'kind':'pon','tiles':['5z']*3,'called':'5z'}])
        self.assertIsNotNone(Matcher({'hand':'5z [111z]','honor_roles':'1,0,0,0,1,1,1'}).matches(f['hand37'],f,0,3))
        self.assertIsNone(Matcher({'hand':'1z [111z]'}).matches(f['hand37'],f,0,3))
    def test_red_in_fixed_meld_and_ankan_kind(self):
        s,f=material('123m',[{'kind':'chi','tiles':['4p','0p','6p'],'called':'6p'}])
        self.assertIsNotNone(Matcher({'hand':'[c:456m]'}).matches(f['hand37'],f,0,3))
        self.assertIsNone(Matcher({'hand':'[c:456m]','red':'1'}).matches(f['hand37'],f,0,3))
        self.assertIsNotNone(Matcher({'hand':'[c:406m]'}).matches(f['hand37'],f,0,3))
        with self.assertRaises(ValueError):Matcher({'hand':'[a:1111m@1m]'})


class OpportunitiesTests(unittest.TestCase):
    def test_chi_selected_and_source_ghost(self):
        r=indexed(fixture('<W100/><G8/><N who="0" m="2055"/><D12/>',hand=[0,4]+list(range(12,23)),source=(3,8)))
        selected=[o for o in r['opportunities'] if o['resolution']['status']=='selected']
        self.assertEqual(len(selected),1);o=selected[0]
        self.assertEqual(o['selected_kind'],'chi');self.assertEqual(sum(c['selected'] for c in o['candidates']),1)
        self.assertEqual(o['snapshot']['melds'][0],[])
        c=r['call_events'][0];t=next(iter(r['traces'].values()))['melds'][0][0]
        self.assertEqual(t['source_event_seq'],o['event_seq']);self.assertEqual(t['source_discard_number'],1)
        self.assertEqual(c['call_id'],t['call_id']);self.assertEqual(t['source_relative'],3)
    def test_pass_censored_and_win_separate(self):
        for tail,status in [('<T101/><D101/>','not_selected'),('<N who="2" m="2055"/>','censored'),('<AGARI who="0"/>','chose_win')]:
            # response() itself is independent of future malformed body; no call is replayed here.
            from luckyj.call_index import response
            root=ET.fromstring('<r><G8/>'+tail+'</r>')
            es=[{'tag':e.tag,'attributes':dict(e.attrib),'event_seq':i} for i,e in enumerate(root)]
            self.assertEqual(response(es,0,0,'external')['status'],status)
    def test_kakan_preserves_pon_link(self):
        r=indexed(fixture('<U100/><E2/><N who="0" m="1129"/><D8/><T3/><N who="0" m="1137"/><T101/><D101/>',hand=[0,1]+list(range(8,19)),reserved=(3,100,101),source=(1,2)))
        a,b=r['call_events'];self.assertEqual((a['kind'],b['kind']),('pon','kakan'))
        for field in ('call_id','link_number','origin_call_seq','source_event_seq','source_discard_number'):
            self.assertEqual(a[field],b[field])
        self.assertNotEqual(a['call_seq'],b['call_seq'])
        self.assertIn('kakan',[o['selected_kind'] for o in r['opportunities']])
    def test_self_ankan_selected_and_wait_unchanged(self):
        r=indexed(fixture('<T3/><N who="0" m="768"/><T100/><D100/>',hand=[0,1,2]+list(range(8,18)),reserved=(3,100)))
        self.assertEqual([o['selected_kind'] for o in r['opportunities'] if o['resolution']['status']=='selected'],['ankan'])
        s=snapshot('111z23m456p789s55z1z');s['hand136']=physical('111z23m456p789s55z')+[111];s['draw136']=111;s['riichi'][0]=True
        self.assertEqual([c['kind'] for c in enumerate_kans(s,0,20,0,True)],['ankan'])
        s['draw136']=s['hand136'][3]
        self.assertEqual(enumerate_kans(s,0,20,0,True),[])
    def test_last_tile_and_four_kans(self):
        s=snapshot('111m234p567s111z2z1m');s['hand136']=[0,1,2]+list(range(12,22))+[3];s['draw136']=3
        self.assertEqual(enumerate_kans(s,0,0,0,True),[])
        self.assertEqual(enumerate_kans(s,0,10,4,True),[])
        self.assertEqual(enumerate_calls(s,8,3,0,0,0,True),[])
    def test_chi_only_from_left_and_kuikae(self):
        s=snapshot('124m456p789s1123z');s['hand136']=physical('124m456p789s1123z');s['draw136']=None;s['draw']=None
        cs=enumerate_calls(s,8,3,0,20,0,True)
        self.assertTrue(any(c['kind']=='chi' for c in cs))
        self.assertFalse(any(c['kind']=='chi' for c in enumerate_calls(s,8,1,0,20,0,True)))
        self.assertTrue(all('3m' not in c['legal_post_discards'] for c in cs if c['kind']=='chi'))


@unittest.skipUnless(calculator_status()=='1.4.0','optional pinned scoring dependency not installed')
class WinningTests(unittest.TestCase):
    def score(self,text,cut,ms=(),ind=()):
        s=snapshot(text,cut);s['melds'][0]=list(ms);s['dora_indicators']=list(ind);f=facts(s);f.update(aka=True,game_type=1)
        return project(s,{'wind':0,'seat_wind':1,'riichi_declared':False},f)
    def test_ron_tsumo_are_distinct_and_no_future_bonus(self):
        p=self.score('23m456p789s111z55z7z','7z')
        ron=next(x for x in p['outcomes'] if x['method']=='ron');tsumo=next(x for x in p['outcomes'] if x['method']=='tsumo')
        self.assertGreater(tsumo['han'],ron['han'])
        self.assertFalse(any(y['code'] in ('riichi','ippatsu') for r in p['outcomes'] for y in r['yaku']))
        self.assertTrue(matches(p,{'win_yaku':'场风','win_method':'ron'}))
    def test_open_no_yaku_is_not_zero_han_win(self):
        p=self.score('23m456p789s55z7z','7z',[{'kind':'chi','tiles':['1p','2p','3p'],'called':'3p'}])
        self.assertTrue(p['outcomes']);self.assertTrue(all(x['error'] for x in p['outcomes']))
        self.assertFalse(matches(p,{'win_han_min':'0'}))
    def test_red_and_bonus_and_same_outcome_filter(self):
        p=self.score('34678m234p678s11z7z','7z',ind=('4m',))
        options=[x for x in p['outcomes'] if x['tile'] in ('5m','0m') and x['method']=='tsumo']
        by={r['tile']:r for r in options};self.assertEqual(by['0m']['han'],by['5m']['han']+1)
        self.assertEqual(by['0m']['yaku_han'],by['5m']['yaku_han'])
        self.assertFalse(matches(p,{'win_yaku':'清一色','win_han_min':'1'}))
    def test_yakuman_not_fake_regular_han(self):
        p=self.score('19m19p19s1234567z2m','2m')
        r=next(x for x in p['outcomes'] if not x['error']);self.assertIsNone(r['han']);self.assertGreaterEqual(r['yakuman_multiplier'],1)
        self.assertTrue(matches(p,{'win_yakuman_min':'1'}));self.assertFalse(matches(p,{'win_han_min':'13'}))
    def test_nontenpai_not_future_final_result(self):
        p=self.score('147m258p369s12345z','1m');self.assertEqual(p['status'],'not_tenpai')
    def test_own_river_keeps_furiten(self):
        p=self.score('23m456p789s111z55z1m','1m');self.assertTrue(p['permanent_furiten'])
        self.assertTrue(all(x['ron_blocked_by_permanent_furiten'] for x in p['outcomes'] if x['method']=='ron'))


class IntentTests(unittest.TestCase):
    def row(self,i,category='major_sacrifice_safer',forced=False):
        return {'event_seq':i,'pressure':'riichi','category':category,'likelihood_active':not forced,'forced':forced}
    def test_online_prefix_invariance(self):
        m=load_model();rs=[self.row(i) for i in range(5)]
        full=infer_round(rs,m);prefix=infer_round(rs[:2],m)
        for i in range(2):self.assertEqual(full[i]['online'],prefix[i]['online'])
        self.assertNotEqual(full[0]['review']['model_posterior'],prefix[0]['review']['model_posterior'])
        self.assertEqual(full[0]['future_evidence_events'],[1,2,3]);self.assertFalse(full[-1]['review']['uses_future'])
    def test_normalized_uncalibrated_and_sensitivity(self):
        r=infer_round([self.row(0)],load_model())[0]
        for mode in ('online','review'):
            self.assertAlmostEqual(sum(r[mode]['model_posterior'].values()),1)
            self.assertIsNone(r[mode]['calibrated_probability'])
        self.assertFalse(r['sensitivity_is_credible_interval']);self.assertTrue(r['model_sha256'])
    def test_forced_is_not_voluntary_inference(self):
        r=infer_round([self.row(0,forced=True)],load_model())[0]
        self.assertEqual(r['online']['label'],'forced');self.assertIsNone(r['online']['confidence'])
    def test_persistence_not_binary_retreat_rule(self):
        m=load_model();a=infer_round([self.row(i) for i in range(4)],m)
        self.assertGreater(a[-1]['online']['model_posterior']['fold'],a[0]['online']['model_posterior']['fold'])
        b=infer_round([self.row(i,'safe_preserve') for i in range(4)],m)
        self.assertGreater(b[-1]['online']['model_posterior']['mawashi'],b[-1]['online']['model_posterior']['fold'])
    def test_invalid_model_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            m=load_model();m['transition'][0][0]=5;p=Path(t)/'bad.json';p.write_text(json.dumps(m))
            with self.assertRaises(ValueError):load_model(p)


class IntegratedCallsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.data=Path(cls.tmp.name);sample_database(cls.data);cls.e=Research(cls.data)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_intent_and_projection_in_api_detail(self):
        d=self.e.decision(1)
        self.assertIn('online',d['intent']);self.assertIn('outcomes',d['win_projection'])
        self.assertIsNone(d['intent']['online']['calibrated_probability'])
    def test_meld_query_reaches_core_search(self):
        self.assertEqual(self.e.stats({'hand':'[p]'})['total'],0)
        self.assertEqual(self.e.stats({'meld_mode':'exact'})['total'],5)
    def test_opportunity_response_counts_not_pages(self):
        r=opportunities(self.e,{'limit':1})
        self.assertEqual(sum(r['status_counts'].values()),r['total'])
        self.assertLessEqual(len(r['items']),1);self.assertTrue(r['complete'])
        self.assertEqual(call_events(self.e,{})['total'],0)
    def test_call_api_and_mcp(self):
        import threading,urllib.request
        from http.server import ThreadingHTTPServer
        from luckyj.server import handler
        from luckyj.mcp import serve_mcp
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.data));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            for path in ('/api/research/opportunities?limit=1','/api/research/call-events?limit=1'):
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}'+path) as r:
                    self.assertTrue(json.load(r)['complete'])
        finally:server.shutdown();server.server_close();t.join()
        reqs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
              {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'luckyj_opportunities','arguments':{'limit':1}}}]
        out=io.StringIO();serve_mcp(self.data,io.StringIO('\n'.join(map(json.dumps,reqs))+'\n'),out)
        answer=json.loads(out.getvalue().splitlines()[-1])['result'];self.assertFalse(answer['isError'])
        self.assertTrue(json.loads(answer['content'][0]['text'])['complete'])
    def test_outcome_filter_does_not_fake_call_rate(self):
        r=opportunities(self.e,{'outcome':'not_selected'})
        self.assertIsNone(r['selected_rate_among_resolved']);self.assertTrue(r['rate_suppressed_by_outcome_filter'])
    def test_review_script_group_split_and_metrics(self):
        from scripts.intent_review import partition,metrics
        self.assertEqual(partition('same-game'),partition('same-game'))
        rows=[{'log_id':'same-game','label':'fold','posterior':{'push':.1,'mawashi':.1,'fold':.8}}]
        m=metrics(rows);self.assertEqual(m['accuracy'],1);self.assertEqual(m['games'],1)
        self.assertAlmostEqual(m['multiclass_brier_sum'],.06)
    def test_filter_validation(self):
        for q in ({'intent_state':'maybe'},{'intent_min':101},{'win_han_min':4,'win_han_max':2},{'win_yaku':'madeup'},{'intent_mode':'secret'}):
            with self.subTest(q=q),self.assertRaises(ValueError):validate(q)
        for q in ({'trigger':'bad'},{'source_relative':'4'},{'unknown':'1'}):
            with self.subTest(q=q),self.assertRaises(ValueError):opportunities(self.e,q)

if __name__=='__main__':unittest.main()
