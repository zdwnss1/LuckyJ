"""Synthetic protocol fixtures are tests, never corpus records."""
import gzip
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from luckyj.tiles import token, parse, kind, meld, ranks
from luckyj.parser import parse_game, TARGET
from luckyj.store import create_schema, insert_game, search, detail, build, connect
from luckyj.server import handler

LOG='2023041911gm-0001-0000-cba4cded'


def fixture(events='<T100/><D100/>', hand=None, reserved=(100,101,104), source=None, aka=True):
    hand=hand or list(range(13))
    used=set(hand)|set(reserved)|{135}
    hands=[hand]
    for seat in range(1,4):
        h=[source[1]] if source and seat==source[0] else []
        used.update(h)
        for t in range(135):
            if len(h)==13:break
            if t not in used and (not source or t!=source[1]):h.append(t);used.add(t)
        hands.append(h)
    attrs=' '.join(f'hai{i}="'+','.join(map(str,h))+'"' for i,h in enumerate(hands))
    init=f'<INIT seed="0,0,0,1,1,135" ten="250,250,250,250" oya="0" {attrs}/>'
    return (f'<mjloggm><GO type="{1 if aka else 3}"/><UN n0="{TARGET}" n1="A" n2="B" n3="C"/>'
            +init+events+'<RYUUKYOKU owari="250,0,250,0,250,0,250,0"/></mjloggm>').encode()


class Tiles(unittest.TestCase):
    def test_red_physical(self):
        self.assertEqual([token(t) for t in (16,52,88)],['0m','0p','0s'])
        self.assertEqual(token(16,False),'5m')
        self.assertEqual(token(135),'7z')
    def test_parse_groups(self):
        self.assertEqual(parse('233m 05p77z'),['2m','3m','3m','0p','5p','7z','7z'])
    def test_bad_notation(self):
        for value in ['0z','8z','11111m','00p','abc','1m OR 1=1','1','m']:
            with self.subTest(value=value),self.assertRaises(ValueError):parse(value)
    def test_sequences_may_repeat_tiles(self):
        self.assertEqual(len(parse('11111m',sequence=True)),5)
    def test_tie_break_initial_not_current_dealer(self):
        self.assertEqual(ranks([25000]*4,0),[1,2,3,4])
        self.assertEqual(ranks([25000]*4,2),[3,4,1,2])
    def test_meld_types(self):
        expected=[(2055,'chi',[0,4,8]),(1129,'pon',[0,1,2]),(1137,'kakan',[0,1,2,3]),(768,'ankan',[0,1,2,3]),(769,'daiminkan',[0,1,2,3])]
        for code,name,tiles in expected:
            m=meld(code,0);self.assertEqual((m['kind'],m['tiles136']),(name,tiles))
    def test_nuki_rejected(self):
        with self.assertRaises(ValueError):meld(32,0)


class Replay(unittest.TestCase):
    def test_tsumogiri(self):
        d=parse_game(fixture(),LOG)['decisions'][0]
        self.assertEqual(d['tsumogiri'],1)
        self.assertEqual(len(d['snapshot']['hand']),14)
        self.assertEqual(d['snapshot']['rivers'][0],[])
    def test_same_type_not_same_physical(self):
        raw=fixture('<T3/><D0/>',hand=[0]+list(range(4,16)),reserved=(3,100))
        d=parse_game(raw,LOG)['decisions'][0]
        self.assertEqual(d['snapshot']['draw'],d['snapshot']['discard'])
        self.assertEqual(d['tsumogiri'],0)
    def test_riichi_scores_and_rank(self):
        raw=fixture('<T100/><REACH who="0" step="1"/><D100/><REACH who="0" step="2" ten="240,250,250,250"/><T104/><D104/>')
        a,b=parse_game(raw,LOG)['decisions']
        self.assertEqual((a['snapshot']['scores'][0],a['snapshot']['ranks'][0],a['kyotaku'],a['riichi_declared']),(25000,1,0,1))
        self.assertEqual((b['snapshot']['scores'][0],b['snapshot']['ranks'][0],b['kyotaku'],b['riichi_declared']),(24000,4,1,0))
        self.assertEqual(b['snapshot']['scores_start'][0],25000)
    def test_no_opponents_hands(self):
        s=parse_game(fixture(),LOG)['decisions'][0]['snapshot']
        self.assertNotIn('hands',s)
        self.assertEqual(len(s['hand136']),14)
    def test_chi_and_no_stale_draw(self):
        raw=fixture('<W100/><G8/><N who="0" m="2055"/><D12/>',hand=[0,4]+list(range(12,23)),source=(3,8))
        d=parse_game(raw,LOG)['decisions'][0]
        self.assertIsNone(d['snapshot']['draw'])
        self.assertEqual(d['tsumogiri'],0)
        self.assertEqual(d['snapshot']['melds'][0][0]['kind'],'chi')
        self.assertEqual(d['snapshot']['rivers'][3][0]['called_by'],0)
    def test_pon_then_added_kan(self):
        raw=fixture('<U100/><E2/><N who="0" m="1129"/><D8/><T3/><N who="0" m="1137"/><T101/><D101/>',hand=[0,1]+list(range(8,19)),reserved=(3,100,101),source=(1,2))
        ds=parse_game(raw,LOG)['decisions']
        self.assertEqual([d['snapshot']['melds'][0][0]['kind'] for d in ds],['pon','kakan'])
        self.assertEqual(len(ds[1]['snapshot']['hand']),11)
    def test_ankan(self):
        raw=fixture('<T3/><N who="0" m="768"/><T100/><D100/>',hand=[0,1,2]+list(range(8,18)),reserved=(3,100))
        d=parse_game(raw,LOG)['decisions'][0]
        self.assertEqual(d['snapshot']['melds'][0][0]['kind'],'ankan')
        self.assertEqual(len(d['snapshot']['hand']),11)
    def test_daiminkan(self):
        raw=fixture('<U100/><E3/><N who="0" m="769"/><T101/><D101/>',hand=[0,1,2]+list(range(8,18)),reserved=(100,101),source=(1,3))
        d=parse_game(raw,LOG)['decisions'][0]
        self.assertEqual(d['snapshot']['melds'][0][0]['kind'],'daiminkan')
    def test_repeated_round_identity(self):
        raw=fixture();start=raw.index(b'<INIT');end=raw.index(b'</mjloggm>');body=raw[start:end]
        g=parse_game(raw[:end]+body+b'</mjloggm>',LOG)
        self.assertEqual([d['round_seq'] for d in g['decisions']],[0,1])
    def test_invalid_target(self):
        with self.assertRaises(ValueError):parse_game(fixture().replace(TARGET.encode(),b'LuckyJ'),LOG)
    def test_missing_result(self):
        raw=fixture().replace(b' owari="250,0,250,0,250,0,250,0"',b'')
        with self.assertRaises(ValueError):parse_game(raw,LOG)
    def test_duplicate_draw(self):
        with self.assertRaises(ValueError):parse_game(fixture('<T0/><D0/>'),LOG)
    def test_unknown_event_rejected(self):
        with self.assertRaises(ValueError):parse_game(fixture('<UNKNOWN/><T100/><D100/>'),LOG)
    def test_future_dora_does_not_leak(self):
        raw=fixture('<T100/><D100/><DORA hai="104"/><T101/><D101/>')
        a,b=parse_game(raw,LOG)['decisions']
        self.assertEqual(len(a['snapshot']['dora_indicators']),1)
        self.assertEqual(len(b['snapshot']['dora_indicators']),2)


class Index(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;create_schema(self.db)
        hand=[16,17,18,19]+list(range(20,29))
        raw=fixture('<T100/><D100/><T101/><D101/>',hand=hand)
        self.game=parse_game(raw,LOG);insert_game(self.db,self.game,'abc',None)
    def tearDown(self):self.db.close()
    def test_counts_and_pagination(self):
        first=search(self.db,{'limit':'1'});second=search(self.db,{'limit':'1','after':str(first['next_after'])})
        self.assertEqual(first['total'],2);self.assertIsNone(second['next_after']);self.assertNotEqual(first['items'][0]['id'],second['items'][0]['id'])
    def test_red_vs_ordinary_multiplicity(self):
        self.assertEqual(search(self.db,{'hand':'5555m'})['total'],0)
        self.assertEqual(search(self.db,{'hand':'5555m','red':'0'})['total'],2)
        self.assertEqual(search(self.db,{'hand':'0555m'})['total'],2)
    def test_exact_hand(self):
        hand=''.join(self.game['decisions'][0]['snapshot']['hand'])
        self.assertEqual(search(self.db,{'hand':hand,'hand_mode':'exact'})['total'],2)
        self.assertEqual(search(self.db,{'hand':'05m','hand_mode':'exact'})['total'],0)
    def test_sequence_anchored_to_current(self):
        d=self.game['decisions'][0]['snapshot']['discard']
        self.assertEqual(search(self.db,{'sequence':d+d})['total'],1)
    def test_score_ranges(self):
        self.assertEqual(search(self.db,{'score0_min':'26000'})['total'],0)
        self.assertEqual(search(self.db,{'score0_min':'25000','score0_max':'25000','rank0':'1'})['total'],2)
    def test_invalid_queries(self):
        for query in [{'limit':'101'},{'after':'-1'},{'rank0':'0'},{'hand':"1m' OR 1=1"},{'draw':'12m'},{'score0_min':'30000','score0_max':'20000'},{'wat':'a'}]:
            with self.subTest(query=query),self.assertRaises(ValueError):search(self.db,query)
    def test_source_injection_is_parameterized(self):
        self.assertEqual(search(self.db,{'log_id':"' OR 1=1 --"})['total'],0)
    def test_detail_navigation(self):
        a=detail(self.db,1);b=detail(self.db,a['next'])
        self.assertIsNone(a['previous']);self.assertEqual(b['previous'],1);self.assertIsNone(b['next'])
    def test_transaction_rollback(self):
        before=self.db.execute('SELECT count(*) FROM decisions').fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):insert_game(self.db,self.game,'abc',None)
        self.assertEqual(self.db.execute('SELECT count(*) FROM decisions').fetchone()[0],before)


class BuildAndAPI(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.manifest={'name':TARGET,'list':[{'url':'https://tenhou.net/3/?log='+LOG,'starttime':1681869780,'player1':TARGET},{'player1':TARGET}]}
        (self.path/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf8')
        p=self.path/'raw'/'2023';p.mkdir(parents=True);(p/(LOG+'.xml.gz')).write_bytes(gzip.compress(fixture(),mtime=0))
    def tearDown(self):self.tmp.cleanup()
    def test_rebuild_idempotent_and_coverage(self):
        a=build(self.path);b=build(self.path)
        self.assertEqual((a['indexed_logs'],a['decisions']),(b['indexed_logs'],b['decisions']))
        self.assertTrue(a['all_linked_logs_indexed']);self.assertFalse(a['all_source_records_indexed']);self.assertEqual(a['records_without_url'],1)
    def test_corrupt_file_quarantined_from_index(self):
        (self.path/'raw'/'2023'/(LOG+'.xml.gz')).write_bytes(b'bad')
        report=build(self.path);self.assertEqual(report['index_error_count'],1);self.assertEqual(report['decisions'],0)
    def test_sha_mismatch(self):
        (self.path/'download-report.json').write_text(json.dumps({'logs':{LOG:{'sha256':'wrong'}}}))
        report=build(self.path);self.assertEqual(report['index_error_count'],1)
    def test_http_endpoints(self):
        build(self.path)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.path));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for url in ['/','/style.css','/app.js','/api/status','/api/coverage','/api/search','/api/decisions/1',f'/api/resolve?log_id={LOG}&event_seq=4']:
                with urllib.request.urlopen(base+url) as r:self.assertEqual(r.status,200)
            for url,code in [('/api/search?rank0=0',400),('/api/search?rank0=1&rank0=2',400),('/api/decisions/99999',404),('/../../etc/passwd',404)]:
                with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+url)
                self.assertEqual(e.exception.code,code)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
