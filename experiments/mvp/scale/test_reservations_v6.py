"""Analytic deck-pose ordering checks; no corpus or planner search."""
from collections import Counter
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import reservations_v5
import reservations_v6


def fixture(*, short_cells=3, remaining_short=True):
    from stowage.actions.space import Place
    ids = ["short","long"]
    slots = ["short0","short1","short2","low","tall","hold_low","hold_tall"]
    t = NS(box_ids=ids,box_index={"short":0,"long":1},length=np.array([20,40]),
           rank=np.array([1,1]),height=np.array([2.591,2.896]),slot_ids=slots,
           slot_stacks=np.array([[0,-1],[1,-1],[8,-1],[0,1],[2,3],[4,5],[6,7]]),
           stack_deck=np.array([1,1,1,1,0,0,0,0,1],bool),
           stack_base_z=np.array([12.5]*4+[1.5]*4+[12.5]))
    last = np.array([0.,0.,8.,8.,1.,1.,5.,5.,0.])
    boxes = {cid:NS(reefer=False,special_stowage=[],oog=None) for cid in ids}
    rows = {"short":np.array([i<short_cells for i in range(7)],bool),
            "long":np.array([0,0,0,1,1,1,1],bool)}
    remaining = ids if remaining_short else ["long"]
    future = NS(t=t,boxes=boxes,signatures={cid:cid for cid in ids},last_heights=last,
                address=[NS(on_deck=i<5) for i in range(7)],parts=[(slot,) for slot in slots],
                evaluate=lambda ex,pending:(np.array([t.box_index[c] for c in pending]),np.array([rows[c] for c in pending])))
    instance,node = NS(load_list=remaining),NS(ex=NS(where={},buffer=[]))
    actions = {slot:Place("long",slot) for slot in slots[3:]}
    return instance,node,future,actions


class ReservationsV6Tests(unittest.TestCase):
    def rank(self,module,instance,node,future,actions,moments=None):
        moments = moments or {}
        with patch("stowage.solver.scalable._moment_preference",side_effect=lambda node,cid,parts:moments.get(parts[0],0.)) as moment:
            result = module.rank_bookings(instance,node,future,actions,{},time.monotonic()+5)
        self.assertEqual(Counter(result),Counter(actions))
        return result,moment

    def test_lower_deck_roof_precedes_soft_mode_conservation(self):
        instance,node,future,a = fixture(short_cells=3)
        actions = [a["tall"],a["low"]]
        old,_ = self.rank(reservations_v5,instance,node,future,actions)
        new,_ = self.rank(reservations_v6,instance,node,future,actions)
        self.assertEqual(old,[a["tall"],a["low"]])
        self.assertEqual(new,[a["low"],a["tall"]])

    def test_pool_deficit_guidance_stays_ahead_of_roof_preference(self):
        instance,node,future,a = fixture(short_cells=2)
        result,_ = self.rank(reservations_v6,instance,node,future,[a["low"],a["tall"]])
        self.assertEqual(result,[a["tall"],a["low"]])

    def test_hold_order_is_identical_and_does_not_invoke_moment_hint(self):
        instance,node,future,a = fixture()
        actions = [a["hold_tall"],a["hold_low"]]
        old,_ = self.rank(reservations_v5,instance,node,future,actions)
        new,moment = self.rank(reservations_v6,instance,node,future,actions)
        self.assertEqual(new,old)
        moment.assert_not_called()

    def test_no_twenty_foot_pool_still_orders_deck_roofs(self):
        instance,node,future,a = fixture(remaining_short=False)
        result,_ = self.rank(reservations_v6,instance,node,future,[a["tall"],a["low"]])
        self.assertEqual(result,[a["low"],a["tall"]])

    def test_equal_roofs_use_native_spread_hint_before_mode_risk(self):
        instance,node,future,a = fixture()
        future.last_heights[2:4] = 0.
        result,_ = self.rank(reservations_v6,instance,node,future,[a["tall"],a["low"]],
                           moments={"tall":1.,"low":0.})
        self.assertEqual(result,[a["low"],a["tall"]])

    def test_physical_roof_uses_indexed_base_supports_and_complete_oog_height_once(self):
        _,_,future,_ = fixture()
        self.assertAlmostEqual(reservations_v6._post_roof(future,0,np.array([0])),15.091)
        self.assertAlmostEqual(reservations_v6._post_roof(future,1,np.array([2,3])),23.396)
        future.t.height[1] = 4.896  # table height already includes OOG overheight
        future.last_heights[2:4] = [1.,1.]
        self.assertAlmostEqual(reservations_v6._post_roof(future,1,np.array([2,3])),18.396)

    def test_expired_deadline_keeps_exact_original_list(self):
        instance,node,future,a = fixture()
        actions = [a["tall"],a["low"]]
        self.assertIs(reservations_v6.rank_bookings(instance,node,future,actions,{},time.monotonic()-1),actions)

    def test_no_pool_mixed_box_hold_subsequence_preserves_v5_input_order_without_hints(self):
        from stowage.actions.space import Place
        ids = ["ordinary","oog","reefer"]
        t = NS(box_ids=ids,box_index={cid:i for i,cid in enumerate(ids)},length=np.full(3,40),
               rank=np.ones(3,int),height=np.full(3,2.896),slot_ids=["hold0","hold1","hold2"],
               slot_stacks=np.array([[0,1],[2,3],[4,5]]),stack_deck=np.zeros(6,bool),
               stack_base_z=np.full(6,1.5))
        boxes = {cid:NS(reefer=cid=="reefer",oog=NS() if cid=="oog" else None,special_stowage=[]) for cid in ids}
        future = NS(t=t,boxes=boxes,signatures={cid:cid for cid in ids},
                    last_heights=np.array([0.,0.,1.,1.,2.,2.]),
                    address=[NS(on_deck=False) for _ in range(3)],parts=[(slot,) for slot in t.slot_ids],
                    evaluate=lambda ex,remaining:(np.arange(3),np.ones((3,3),bool)))
        # Deliberately make reefers/OOG scarcer: the old no-pool fallback still
        # preserved ordinary→OOG→reefer, rather than reapplying booking keys.
        cached = np.array([0,1,2,3,4,8])
        node = NS(ex=NS(where={},buffer=[]),engine=NS(backend=NS(_cur=NS(evaluated=True,mask=cached))))
        instance = NS(load_list=ids)
        actions = [Place(cid,slot) for cid,slot in zip(ids,t.slot_ids)]
        old,_ = self.rank(reservations_v5,instance,node,future,actions)
        new,moment = self.rank(reservations_v6,instance,node,future,actions)
        self.assertEqual(new,old)
        self.assertEqual(new,actions)
        moment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
