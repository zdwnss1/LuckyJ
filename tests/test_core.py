import collections
import contextlib
import gzip
import http.server
import json
import pathlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import luckyj

FIXTURES = pathlib.Path(__file__).parent / 'fixtures'
ID = '2023041911gm-0001-0000-cba4cded'


def synthetic(actions, h0=None, first_dealer=0, scores='250,250,250,250'):
    h0 = h0 or [0,4,8,12,16,20,24,28,32,36,40,44,48]
    others = [x for x in range(136) if x not in h0][:39]
    attrs = {'seed':'0,0,0,1,2,135', 'ten':scores, 'oya':str(first_dealer), 'hai0':','.join(map(str,h0))}
    for i in range(1,4): attrs[f'hai{i}'] = ','.join(map(str,others[(i-1)*13:i*13]))
    init = ET.tostring(ET.Element('INIT', attrs), encoding='unicode')
    return (f'<mjloggm><GO type="1"/><UN n0="%E2%93%9DLuckyJ" n1="A" n2="B" n3="C"/>'
            f'<TAIKYOKU oya="{first_dealer}"/>{init}{actions}'
            '<RYUUKYOKU sc="250,0,250,0,250,0,250,0" owari="250,0,250,0,250,0,250,0"/></mjloggm>').encode()


class NotationTests(unittest.TestCase):
    def test_compact_and_red(self):
        self.assertEqual(luckyj.parse_tiles('123m 40p → 77z'), ['1m','2m','3m','4p','0p','7z','7z'])
        self.assertNotEqual(luckyj.tile_counts(['0m']), luckyj.tile_counts(['5m']))
        self.assertEqual(luckyj.tile_counts(['0m'],True), luckyj.tile_counts(['5m'],True))
    def test_invalid_notation(self):
        for text in ('8z','0z','abc',"5m' OR 1=1",'m123','---'):
            with self.subTest(text=text), self.assertRaises(ValueError): luckyj.parse_tiles(text)
        with self.assertRaises(ValueError): luckyj.parse_tiles('11111m',hand=True)
        with self.assertRaises(ValueError): luckyj.parse_tiles('00m',hand=True)
        with self.assertRaises(ValueError): luckyj.parse_tiles('12m',single=True)
    def test_physical_red(self):
        self.assertEqual(luckyj.tile_name(16),'0m')
        self.assertEqual(luckyj.tile_name(16,False),'5m')
        self.assertEqual(luckyj.tile_name(135),'7z')
    def test_tie_break_uses_first_dealer(self):
        self.assertEqual(luckyj.placements([25000]*4,2),[3,4,1,2])
        self.assertEqual(luckyj.placements([24000,25000,25000,26000],2),[4,3,2,1])


class ParserTests(unittest.TestCase):
    def test_real_games(self):
        expected = {'cba4cded':(5,53,0),'24ce7cb7':(10,118,3),'76965bea':(11,122,3)}
        for file in FIXTURES.glob('*.xml'):
            g=luckyj.parse_game(file.read_bytes(),file.stem)
            self.assertEqual((len(g['rounds']),len(g['cases']),g['lucky_seat']),expected[file.stem[-8:]])
            for c,packed,_,_ in g['cases']:
                self.assertEqual(c,luckyj.unpack(packed))
                self.assertEqual(len(c['hand']) + 3*len(c['melds'][c['lucky_seat']]),14)
                self.assertEqual(len(c['rivers'][c['lucky_seat']]),c['turn']-1)
                self.assertEqual(c['sequence'][-1],c['discard'])
                self.assertNotIn('hands',c)
    def test_json_export_round_scores_and_discard_counts(self):
        # Independently exported Tenhou JSON checks hand numbering, scores and discard count.
        for file in FIXTURES.glob('*.xml'):
            g=luckyj.parse_game(file.read_bytes(),file.stem)
            export=json.loads(file.with_suffix('.json').read_text())
            self.assertEqual(len(g['rounds']),len(export['log']))
            for r,j in zip(g['rounds'],export['log']):
                self.assertEqual([r['round_no'],r['honba'],r['start_kyotaku']],j[0])
                self.assertEqual(r['start_scores'],j[1])
                sequence=j[6+3*g['lucky_seat']]
                # Kan notation replaces a discard slot; it is not a physical discard.
                count=sum(not (isinstance(x,str) and any(k in x for k in ('a','k','m'))) for x in sequence)
                self.assertEqual(sum(c[0]['round_id']==r['id'] for c in g['cases']),count)
    def test_reject_wrong_player_or_truncated_log(self):
        raw=(FIXTURES/(ID+'.xml')).read_bytes()
        with self.assertRaises(ValueError): luckyj.parse_game(raw.replace(b'%4C%75%63%6B%79%4A',b'Other'),ID)
        root=ET.fromstring(raw)
        for node in root: node.attrib.pop('owari',None)
        with self.assertRaises(ValueError): luckyj.parse_game(ET.tostring(root),ID)
    def test_no_future_river_leak(self):
        game=luckyj.parse_game((FIXTURES/(ID+'.xml')).read_bytes(),ID)
        first=game['cases'][0][0]
        self.assertEqual(first['rivers'],[[],[],[],[]])
        self.assertEqual(first['sequence'],[first['discard']])
    def test_same_type_different_copy_is_not_tsumogiri(self):
        # Physical tile 1 is already in an opponent hand in default setup, so move it out.
        hand=[0,1,8,12,16,20,24,28,32,36,40,44,48]
        raw=synthetic('<T53/><D0/>',hand)
        g=luckyj.parse_game(raw,ID)
        self.assertFalse(g['cases'][0][0]['tsumogiri'])
        raw=synthetic('<T53/><D53/>',hand)
        self.assertTrue(luckyj.parse_game(raw,ID)['cases'][0][0]['tsumogiri'])
        # More direct case: draw another copy of a tile already held, then cut the old copy.
        hand=[52,0,4,8,12,16,20,24,28,32,36,40,44]
        c=luckyj.parse_game(synthetic('<T53/><D52/>',hand),ID)['cases'][0][0]
        self.assertEqual(luckyj.normal(c['draw']),luckyj.normal(c['discard']))
        self.assertFalse(c['tsumogiri'])
    def test_riichi_timing(self):
        actions='<T53/><REACH who="0" step="1"/><D53/><REACH who="0" step="2" ten="240,250,250,250"/><T54/><D54/>'
        cases=luckyj.parse_game(synthetic(actions),ID)['cases']
        self.assertEqual(cases[0][0]['scores'][0],25000)
        self.assertTrue(cases[0][0]['riichi_declaration'])
        self.assertFalse(cases[0][0]['riichi'][0])
        self.assertEqual(cases[1][0]['scores'][0],24000)
        self.assertEqual(cases[1][0]['kyotaku'],1)
        self.assertEqual(cases[1][0]['start_scores'][0],25000)
        self.assertEqual(cases[1][0]['rank'],4)
    def test_invalid_physical_discard(self):
        with self.assertRaises(ValueError): luckyj.parse_game(synthetic('<T53/><D100/>'),ID)
    def test_kan_bitfields(self):
        # Tile type 13, missing copy 2, originally called copy 0.
        pon=luckyj.decode_meld((13*3 << 9) | (2<<5) | 8 | 1,0)
        kakan=luckyj.decode_meld((13*3 << 9) | (2<<5) | 16 | 1,0)
        self.assertEqual(pon['ids'],[52,53,55])
        self.assertEqual(kakan['added_id'],54)
        self.assertEqual(kakan['kind'],'kakan')
        ankan=luckyj.decode_meld((13*4 << 8),2)
        daiminkan=luckyj.decode_meld((13*4 << 8)|3,2)
        self.assertEqual(ankan['kind'],'ankan')
        self.assertEqual(daiminkan['from'],1)
        self.assertEqual(daiminkan['kind'],'daiminkan')
    def test_unsafe_xml(self):
        with self.assertRaises(ValueError): luckyj.parse_game(b'<!DOCTYPE evil><mjloggm/>',ID)


class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.data=pathlib.Path(cls.temp.name)
        (cls.data/'raw').mkdir()
        rows=[]
        for file in sorted(FIXTURES.glob('*.xml')):
            (cls.data/'raw'/(file.name+'.gz')).write_bytes(gzip.compress(file.read_bytes(),mtime=0))
            rows.append({'player1':luckyj.PLAYER,'player2':'x','player3':'y','player4':'z','url':'https://tenhou.net/0/?log='+file.stem})
        # Preserve a source row with no log URL; linked coverage is not source coverage.
        rows.append({'player1':luckyj.PLAYER,'player2':'x','player3':'y','player4':'z'})
        (cls.data/'manifest.json').write_text(json.dumps({'name':luckyj.PLAYER,'list':rows}))
        cls.db=cls.data/'luckyj.sqlite'
        with contextlib.redirect_stdout(__import__('io').StringIO()): cls.report=luckyj.build(cls.data,cls.db)
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def query(self,**params):
        with contextlib.closing(luckyj.connection(self.db)) as db: return luckyj.search(db,params)
    def test_coverage_and_foreign_keys(self):
        self.assertEqual(self.report['decisions'],293)
        self.assertTrue(self.report['all_linked_games_indexed'])
        self.assertFalse(self.report['complete_source_coverage'])
        self.assertEqual(self.report['records_without_url'],1)
        self.assertEqual(self.report['foreign_key_errors'],0)
    def test_discard_draw_rank_score_filters(self):
        cases=self.query(discard='1z',limit='100')['items']
        self.assertTrue(cases)
        self.assertTrue(all(c['discard']=='1z' for c in cases))
        cases=self.query(rank='4',score0_max='25000',limit='100')['items']
        self.assertTrue(cases)
        self.assertTrue(all(c['rank']==4 and c['scores'][c['lucky_seat']]<=25000 for c in cases))
    def test_exact_hand_and_contains_multiplicity(self):
        c=self.query()['items'][0]
        result=self.query(hand=''.join(c['hand']),hand_mode='exact')
        self.assertIn(c['id'],[x['id'] for x in result['items']])
        cases=self.query(hand='77z',limit='100')['items']
        self.assertTrue(cases)
        self.assertTrue(all(c['hand'].count('7z')>=2 for c in cases))
    def test_sequence_ends_here(self):
        all_items=self.query(limit='100')['items']
        c=next(x for x in all_items if x['turn']>2)
        seq=c['sequence'][-2:]
        result=self.query(sequence=''.join(seq),limit='100')
        self.assertIn(c['id'],[x['id'] for x in result['items']])
        self.assertTrue(all(x['sequence'][-2:]==seq for x in result['items']))
    def test_pagination_stable(self):
        first=self.query(limit='20',page='1');second=self.query(limit='20',page='2')
        self.assertEqual(first['total'],293)
        self.assertEqual(len(first['items']),20)
        self.assertFalse(set(x['id'] for x in first['items']) & set(x['id'] for x in second['items']))
    def test_invalid_filters_and_readonly(self):
        for params in ({'sort':'DROP TABLE games'}, {'rank':'5'}, {'score0_min':'2','score0_max':'1'}, {'limit':'999'}, {'discard':'8z'}, {'score_basis':'future'}):
            with self.subTest(params=params),self.assertRaises(ValueError): self.query(**params)
        with contextlib.closing(luckyj.connection(self.db)) as db:
            with self.assertRaises(sqlite3.OperationalError): db.execute('DELETE FROM decisions')
    def test_http_and_path_traversal(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),luckyj.make_handler(self.db,self.data))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urllib.request.urlopen(base+'/api/status') as response:
                self.assertEqual(json.load(response)['decisions'],293)
            with urllib.request.urlopen(base+'/api/search?discard=1z&limit=1') as response:
                result=json.load(response)
            case_id=result['items'][0]['id']
            with urllib.request.urlopen(base+'/api/decision/'+urllib.parse.quote(case_id)) as response:
                detail=json.load(response);self.assertIn('rivers',detail);self.assertNotIn('hands',detail)
            with urllib.request.urlopen(base+'/api/raw/'+ID) as response:
                self.assertEqual(gzip.decompress(response.read()),(FIXTURES/(ID+'.xml')).read_bytes())
            for path,code in (('/api/search?rank=8',400),('/api/search?rank=1&rank=2',400),('/%2e%2e/luckyj.py',404)):
                with self.assertRaises(urllib.error.HTTPError) as ctx: urllib.request.urlopen(base+path)
                self.assertEqual(ctx.exception.code,code)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__': unittest.main()
