import unittest
from luckyj.tenhou import iter_decisions, tile_code

XML = b'''<mjloggm ver="2.3">
<UN n0="%E2%93%83LuckyJ" n1="A" n2="B" n3="C"/>
<INIT seed="0,1,0,0,0,16" ten="250,260,240,250" oya="0" hai0="0,4,8,12,16,20,24,28,32,36,40,44,48" hai1="" hai2="" hai3=""/>
<T52/><D52/><U60/><E60/><V64/><F64/><W68/><G68/>
<T53/><REACH who="0" step="1"/><D16/><REACH who="0" step="2"/>
<AGARI who="1" fromWho="0" ten="30,1000" sc="240,-10,270,20,240,0,250,0" owari="240,-10,270,20,240,0,250,-10"/>
</mjloggm>'''

class ParserTests(unittest.TestCase):
    def test_tile_codes(self):
        self.assertEqual(tile_code(16), '0m')
        self.assertEqual(tile_code(17), '5m')
        self.assertEqual(tile_code(108), '1z')

    def test_decisions(self):
        rows=list(iter_decisions(XML,'2026010100gm-0000-test0000'))
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0].round_wind,'E')
        self.assertEqual(rows[0].kyoku,1)
        self.assertEqual(rows[0].honba,1)
        self.assertEqual(rows[0].draw_tile,'0p')
        self.assertEqual(rows[0].discard_tile,'0p')
        self.assertEqual(rows[0].tsumogiri,1)
        self.assertEqual(rows[1].discard_tile,'0m')
        self.assertEqual(rows[1].riichi_state,1)
        self.assertEqual(rows[0].target_score,25000)
        self.assertEqual(rows[0].score_rank,2)

if __name__=='__main__':
    unittest.main()
