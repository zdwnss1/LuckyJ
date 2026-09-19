"""Boundary tests for observational labels, not inferred motives or win legality."""
from contextlib import closing
import copy
from pathlib import Path
import tempfile
import unittest
from test_research import sample_database, snapshot
from luckyj.decision_flags import annotate, FLAG_KEYS, new_facets, count_facets
from luckyj.research import Research, validate


def state(discard='2z'):
    s=snapshot('123m456p789s123z5z2z',discard)
    s['rivers'][0]=[{'tile':'7s','event_seq':10,'riichi':False,'tsumogiri':False}]
    return s


def river(tile,seq,reach=False):
    return {'tile':tile,'event_seq':seq,'riichi':reach,'tsumogiri':False}


def mark(s,before=1,after=1,best=1,declared=False):
    return annotate(s,{'event_seq':20,'riichi_declared':declared},before,after,best)


class Flags(unittest.TestCase):
    def test_follow_all_tiles_same_actual_identity(self):
        s=state('3m');s['rivers'][1]=[river('3m',12)]
        a=mark(s)
        self.assertTrue(a['flags']['follow_discard'])
        self.assertEqual(a['follow_sources'][0]['seat'],1)
        self.assertEqual(a['follow_window'],{'after_event_seq':10,'before_event_seq':20})
        s['discard']='3p'
        self.assertFalse(mark(s)['flags']['follow_discard'])
    def test_role_equivalence_not_follow(self):
        s=state('3z');s['rivers'][1]=[river('2z',12)]
        self.assertFalse(mark(s)['flags']['follow_discard'])
    def test_red_copy_still_same_tile_kind(self):
        s=state('0m');s['rivers'][1]=[river('5m',12)]
        self.assertTrue(mark(s)['flags']['follow_discard'])
    def test_old_and_future_events_excluded(self):
        s=state();s['rivers'][1]=[river('2z',9),river('2z',20),river('2z',21)]
        self.assertFalse(mark(s)['flags']['follow_discard'])
    def test_first_turn_has_no_fake_source(self):
        s=state();s['rivers']=[[],[],[],[]]
        self.assertFalse(mark(s)['flags']['follow_discard'])
    def test_immediate_vs_same_cycle(self):
        s=state();s['rivers'][1]=[river('2z',12)];s['rivers'][2]=[river('3m',15)]
        self.assertTrue(mark(s)['flags']['follow_discard'])
        self.assertFalse(mark(s)['flags']['follow_immediate'])
    def test_declaration_not_locked_and_not_dama(self):
        s=state();a=mark(s,before=1,after=0,best=0,declared=True)
        self.assertTrue(a['flags']['riichi_declaration'])
        self.assertFalse(a['flags']['riichi_locked'])
        self.assertFalse(a['flags']['damaten'])
    def test_established_riichi_not_dama(self):
        s=state();s['riichi'][0]=True;a=mark(s,0,0,0)
        self.assertTrue(a['flags']['riichi_locked'])
        self.assertFalse(a['flags']['damaten'])
    def test_damaten_enter_and_hold(self):
        a=mark(state(),1,0,0);b=mark(state(),0,0,0)
        self.assertTrue(a['flags']['dama_enter']);self.assertFalse(a['flags']['dama_hold'])
        self.assertTrue(b['flags']['dama_hold']);self.assertFalse(b['flags']['dama_enter'])
    def test_ankan_closed_not_open_tenpai(self):
        s=state();s['melds'][0]=[{'kind':'ankan'}]
        self.assertTrue(mark(s,1,0,0)['flags']['damaten'])
        self.assertEqual(mark(s)['own_closed_kans'],1)
        s['melds'][0]=[{'kind':'pon'}]
        self.assertFalse(mark(s,1,0,0)['flags']['damaten'])
    def test_retreat_not_missed_improvement(self):
        a=mark(state(),1,1,0)
        self.assertFalse(a['flags']['shanten_retreat'])
        self.assertTrue(a['flags']['miss_minimum'])
        b=mark(state(),1,2,1)
        self.assertTrue(b['flags']['shanten_retreat'])
        self.assertEqual(b['shanten_transition']['change_from_before_draw'],1)
    def test_break_tenpai(self):
        a=mark(state(),0,1,0)
        self.assertTrue(a['flags']['tenpai_break'])
        self.assertTrue(a['flags']['shanten_retreat'])
    def test_post_call_comparison_unknown_not_false(self):
        s=state();s['draw']=None;s['melds'][0]=[{'kind':'chi'}]
        a=mark(s,None,1,0)
        self.assertIsNone(a['flags']['shanten_retreat'])
        self.assertIsNone(a['flags']['tenpai_break'])
        self.assertTrue(a['flags']['post_call_discard'])
        self.assertTrue(a['flags']['miss_minimum'])
    def test_added_kan_one_open_meld(self):
        s=state();s['melds'][0]=[{'kind':'kakan'}]
        a=mark(s)
        self.assertEqual(a['own_open_melds'],1)
        self.assertEqual(a['meld_counts'][0]['fixed'],1)
    def test_tags_overlap_and_unknown_accounting(self):
        s=state();s['rivers'][1]=[river('2z',12)]
        a=mark(s,1,0,0);facets=new_facets();count_facets(facets,a['flags'])
        self.assertEqual(facets['follow_discard']['true'],1)
        self.assertEqual(facets['damaten']['true'],1)
        for key in FLAG_KEYS:self.assertEqual(sum(facets[key].values()),1)
    def test_unknown_meld_kind_rejected(self):
        s=state();s['melds'][0]=[{'kind':'invented'}]
        with self.assertRaises(ValueError):mark(s)


class FlagAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.path=Path(cls.tmp.name)
        sample_database(cls.path);cls.r=Research(cls.path)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_stats_are_full_cohort_not_page(self):
        s=self.r.stats({});one=self.r.search({'metrics':0,'limit':1})
        self.assertEqual(s['flags_denominator'],5)
        self.assertEqual(s['decision_flags']['riichi_declaration']['true'],1)
        self.assertEqual(len(one['items']),1)
        for facet in s['decision_flags'].values():self.assertEqual(sum(facet.values()),5)
    def test_filters_match_annotation_and_sql(self):
        s=self.r.search({'follow_discard':1,'metrics':0,'limit':100})
        self.assertTrue(s['items'])
        self.assertTrue(all(x['annotations']['flags']['follow_discard'] for x in s['items']))
        self.assertEqual(s['total'],self.r.stats({})['decision_flags']['follow_discard']['true'])
    def test_locked_explicit_not_silent_empty_set(self):
        with self.assertRaisesRegex(ValueError,'exclude_locked'):validate({'riichi_locked':'1'})
        s=self.r.search({'riichi_locked':'1','exclude_locked':'0','metrics':'0'})
        self.assertEqual(s['total'],1)
    def test_meld_state_filters(self):
        self.assertEqual(self.r.stats({'open_melds_max':0})['total'],5)
        self.assertEqual(self.r.stats({'open_melds_min':1})['total'],0)
        self.assertEqual(self.r.stats({'closed_kans_min':1})['total'],0)
    def test_schema_values_reject_invalid(self):
        for q in ({'follow_discard':2},{'damaten':'true'},{'open_melds_max':5},
                  {'opponents_open_min':4},{'closed_kans_min':2,'closed_kans_max':1}):
            with self.subTest(q=q),self.assertRaises(ValueError):validate(q)
    def test_annotations_are_stable_under_symmetry_query(self):
        a=self.r.decision(1,{'suits':'0'});b=self.r.decision(1,{'suits':'1'})
        self.assertEqual(a['annotations'],b['annotations'])
    def test_each_flag_sql_count_matches_full_facets(self):
        s=self.r.stats({'exclude_locked':0})
        for key in FLAG_KEYS:
            with self.subTest(key=key):
                t=self.r.stats({'exclude_locked':0,key:1})
                f=self.r.stats({'exclude_locked':0,key:0})
                self.assertEqual(t['total'],s['decision_flags'][key]['true'])
                self.assertEqual(f['total'],s['decision_flags'][key]['false'])

if __name__=='__main__':unittest.main()
