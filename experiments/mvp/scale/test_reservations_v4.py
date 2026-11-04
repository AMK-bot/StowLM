"""Bounded analytic search tests; no dataset and no solver workers."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reservations_v4


def native_style_shortlist(reps):
    selected = reps[:min(3, len(reps))]
    return selected


class ReservationsV4Tests(unittest.TestCase):
    def test_ast_change_only_widens_native_shortlist_preserving_original_choices(self):
        original = native_style_shortlist(list(range(9)))
        wider = reservations_v4.widen_choices(native_style_shortlist)
        self.assertEqual(wider(list(range(9))), list(range(6)))
        self.assertEqual(wider(list(range(9)))[:len(original)], original)
        self.assertEqual(wider([1]), [1])

    def test_constructor_scope_sets_config_and_restores_after_exception(self):
        from stowage.solver import construct_v4, twostep_construct
        original_choices, original_config = construct_v4._choices, twostep_construct.ScalableConfig
        with self.assertRaisesRegex(RuntimeError, "intentional"):
            with reservations_v4.constructor_search():
                config = twostep_construct.ScalableConfig(constructor_v4=True, max_neighborhoods=0)
                self.assertEqual(config.backtrack_limit, 512)
                self.assertEqual(config.max_restarts, 3)
                self.assertTrue(config.constructor_v4)
                self.assertIsNot(construct_v4._choices, original_choices)
                raise RuntimeError("intentional")
        self.assertIs(construct_v4._choices, original_choices)
        self.assertIs(twostep_construct.ScalableConfig, original_config)

    def test_booking_pool_avoids_consuming_all_reefer_cells_and_keeps_all_actions(self):
        from stowage.actions.space import Place
        ids = ["a", "b", "c", "long"]
        t = NS(box_index={cid: i for i, cid in enumerate(ids)}, length=np.array([20,20,20,40]),
               rank=np.array([1,1,1,1]), height=np.array([2.6,2.6,2.6,2.6]),
               slot_ids=["s0","s1","s2","s3","l0","l1"], stack_deck=np.zeros(4, bool),
               slot_stacks=np.array([[0,-1],[1,-1],[2,-1],[3,-1],[0,1],[2,3]]))
        domains = np.array([[1,1,0,0,0,0], [1,1,0,0,0,0], [1,1,1,1,0,0], [0,0,0,0,1,1]], bool)
        boxes = {cid: NS(reefer=cid in {"a","b"}, special_stowage=[]) for cid in ids}
        future = NS(t=t, boxes=boxes, signatures={"a":"restricted","b":"restricted","c":"flex","long":"long"},
                    evaluate=lambda ex, remaining: (np.arange(4), domains))
        node = NS(ex=NS(where={}, buffer=[]))
        actions = [Place("long", "l0"), Place("long", "l1")]
        metrics = {}
        result = reservations_v4.rank_bookings(NS(load_list=ids), node, future, actions, metrics, time.monotonic()+5)
        self.assertEqual(result[0], actions[1])
        self.assertEqual(set(result), set(actions))
        self.assertEqual(len(result), len(actions))
        self.assertGreaterEqual(metrics["scale_v4_booking_pool_max"], 2)

    def test_expired_ranking_preserves_original_action_order(self):
        actions = [NS(slot="a"), NS(slot="b")]
        result = reservations_v4.rank_bookings(None, None, None, actions, {}, time.monotonic()-1)
        self.assertIs(result, actions)

    def test_hold_access_precedes_booking_pool_preference_for_deck(self):
        from stowage.actions.space import Place
        ids = ["reefer", "long"]
        t = NS(box_index={cid: i for i, cid in enumerate(ids)}, length=np.array([20,40]),
               rank=np.array([1,1]), height=np.array([2.6,2.6]),
               slot_ids=["short_hold", "long_hold", "long_deck"], stack_deck=np.array([False,False,True,True]),
               slot_stacks=np.array([[0,-1],[0,1],[2,3]]))
        # Deck's long slot protects the reefer's sole future destination, but
        # must still wait behind the native hold phase to preserve hatch access.
        domains = np.array([[1,0,0],[0,1,1]], bool)
        future = NS(t=t, boxes={"reefer": NS(reefer=True, special_stowage=[]),
                              "long": NS(reefer=False, special_stowage=[])},
                    signatures={"reefer":"restricted","long":"long"},
                    address=[NS(on_deck=False),NS(on_deck=False),NS(on_deck=True)],
                    evaluate=lambda ex, remaining: (np.arange(2), domains))
        actions = [Place("long", "long_deck"),Place("long", "long_hold")]
        result = reservations_v4.rank_bookings(NS(load_list=ids), NS(ex=NS(where={},buffer=[])),
            future, actions, {}, time.monotonic()+5)
        self.assertEqual(result[0], actions[1])
        self.assertEqual(set(result), set(actions))

    def test_empty_choice_telemetry_is_bounded_uses_only_cached_mask_and_keeps_actions(self):
        ids = [f"box{i}" for i in range(10)]
        t = NS(box_ids=ids, box_index={cid:i for i,cid in enumerate(ids)},
               length=np.full(10,20), height=np.full(10,2.6), mass=np.full(10,12000.),rank=np.ones(10,int),
               slot_ids=["slot"],static=np.ones((10,1),bool),slot_stacks=np.array([[0,-1]]))
        future = NS(t=t, boxes={cid:NS(reefer=False,special_stowage=[]) for cid in ids},
                    evaluate=lambda ex,remaining:(np.array([t.box_index[c] for c in remaining]),
                                                 np.ones((len(remaining),1),bool)))
        def forbidden():
            self.fail("diagnostics must not make a native legality query")
        engine = NS(backend=NS(_cur=NS(evaluated=True,mask=np.array([],int),dead=None)),legal_pairs=forbidden)
        node = NS(ex=NS(where={},buffer=[]),engine=engine)
        metrics, actions = {}, []
        for cid in ids:
            result = reservations_v4.record_empty_choices(NS(load_list=[cid]),node,future,actions,
                                                          metrics,time.monotonic()+5)
            self.assertIs(result,actions)
        self.assertEqual(len(metrics["scale_v4_empty_choices"]),8)
        first = metrics["scale_v4_empty_choices"][0]
        self.assertEqual(first["diagnostic_status"],"recorded")
        self.assertEqual(first["boxes"][0]["native_cached_legal_count"],0)
        self.assertEqual(first["boxes"][0]["future_domain_count"],1)
        reservations_v4.record_empty_choices(NS(load_list=[ids[0]]),node,future,actions,metrics,time.monotonic()+5)
        self.assertEqual(len(metrics["scale_v4_empty_choices"]),8)


if __name__ == "__main__":
    unittest.main()
