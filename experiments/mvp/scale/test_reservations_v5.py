"""OOG priority, native-shortlist preservation and scoped restoration tests."""
import ast
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import reservations_v5


def native_style_choices(boxes, legal_counts):
    future = NS(representative_signatures={cid:cid for cid in boxes})
    by_box = {cid:list(range(legal_counts[cid])) for cid in boxes}
    def box_key(cid):
        box = boxes[cid]
        no_hold = box.deck
        scarcity = box.optimistic
        common = (int(no_hold), int(scarcity > 2.0))
        return (*common,box.order,cid)
    ordered = sorted(boxes,key=box_key)
    reps = ordered
    selected = reps[:min(3, len(reps))]
    return selected


class ReservationsV5Tests(unittest.TestCase):
    def test_ast_changes_only_common_and_selected_assignments(self):
        wider = reservations_v5.widen_choices(native_style_choices)
        before = ast.parse(inspect.getsource(native_style_choices))
        after = ast.parse(wider._scale_transformed_source)
        original = {target.id:node.value for node in ast.walk(before) if isinstance(node,ast.Assign)
                    for target in node.targets if isinstance(target,ast.Name) and target.id in {"common","selected"}}
        restored = set()
        for node in ast.walk(after):
            if isinstance(node,ast.Assign):
                for target in node.targets:
                    if isinstance(target,ast.Name) and target.id in original:
                        node.value = original[target.id]
                        restored.add(target.id)
        self.assertEqual(restored,{"common","selected"})
        self.assertEqual(ast.dump(before),ast.dump(after))

    def test_oog_enters_six_profile_shortlist_and_original_actions_are_retained(self):
        boxes = {f"normal{i}":NS(deck=False,optimistic=8.,oog=None,reefer=False,order=i) for i in range(9)}
        boxes.update(scarce=NS(deck=False,optimistic=8.,oog=NS(),reefer=False,order=99),
                     abundant=NS(deck=False,optimistic=8.,oog=NS(),reefer=False,order=98))
        counts = {cid:20 for cid in boxes}
        counts["scarce"],counts["abundant"] = 1,4
        result = reservations_v5.widen_choices(native_style_choices)(boxes,counts)
        self.assertEqual(result[:2],["scarce","abundant"])
        self.assertTrue(set(native_style_choices(boxes,counts)) <= set(result))
        self.assertTrue({f"normal{i}" for i in range(6)} <= set(result))
        self.assertEqual(len(result),len(set(result)))

    def test_non_oog_original_order_remains_stable(self):
        boxes = {str(i):NS(deck=False,optimistic=8.,oog=None,reefer=False,order=i) for i in range(8)}
        result = reservations_v5.widen_choices(native_style_choices)(boxes,{cid:5 for cid in boxes})
        self.assertEqual(result,list(boxes)[:6])

    def test_cached_native_scarcity_orders_oog_but_holds_still_precede_deck(self):
        from stowage.actions.space import Place
        ids = ["short","sparse","many","normal","deck_oog"]
        t = NS(box_ids=ids,box_index={cid:i for i,cid in enumerate(ids)},length=np.array([20,40,40,40,40]),
               rank=np.ones(5,int),height=np.full(5,2.6),slot_ids=["short_slot","sparse_hold","many_hold","normal_hold","deck"],
               stack_deck=np.array([0,0,0,0,0,0,1,1],bool),
               slot_stacks=np.array([[0,-1],[0,1],[2,3],[4,5],[6,7]]))
        domains = np.zeros((5,5),bool)
        domains[0,0] = True
        domains[1:,1:] = True
        boxes = {cid:NS(reefer=False,special_stowage=[],oog=NS() if cid in {"sparse","many","deck_oog"} else None)
                 for cid in ids}
        future = NS(t=t,boxes=boxes,signatures={cid:cid for cid in ids},
                    address=[NS(on_deck=i==4) for i in range(5)],
                    evaluate=lambda ex,remaining:(np.arange(5),domains))
        def forbidden():
            self.fail("ranking must use cached mask without a native query")
        cached = np.array([6,12,13,18,24])
        engine = NS(backend=NS(_cur=NS(evaluated=True,mask=cached)),legal_pairs=forbidden,is_legal=forbidden)
        node = NS(engine=engine,ex=NS(where={},buffer=[]))
        actions = [Place("deck_oog","deck"),Place("normal","normal_hold"),
                   Place("many","many_hold"),Place("sparse","sparse_hold")]
        result = reservations_v5.rank_bookings(NS(load_list=ids),node,future,actions,{},time.monotonic()+5)
        self.assertEqual([action.box for action in result],["sparse","many","normal","deck_oog"])
        self.assertEqual(set(result),set(actions))

    def test_constructor_context_restores_on_exception(self):
        from stowage.solver import construct_v4,twostep_construct
        original_choices,original_config = construct_v4._choices,twostep_construct.ScalableConfig
        with self.assertRaisesRegex(RuntimeError,"intentional"):
            with reservations_v5.constructor_search():
                config = twostep_construct.ScalableConfig(constructor_v4=True,max_neighborhoods=0)
                self.assertEqual((config.backtrack_limit,config.max_restarts),(512,3))
                raise RuntimeError("intentional")
        self.assertIs(construct_v4._choices,original_choices)
        self.assertIs(twostep_construct.ScalableConfig,original_config)

    def test_reefer_enters_before_oog_and_original_shortlist_still_reconstructs(self):
        boxes = {f"normal{i}":NS(deck=False,optimistic=8.,oog=None,reefer=False,order=i) for i in range(8)}
        boxes.update(reefer=NS(deck=False,optimistic=8.,oog=None,reefer=True,order=99),
                     oog=NS(deck=False,optimistic=8.,oog=NS(),reefer=False,order=98))
        counts = {cid:5 for cid in boxes}
        counts["reefer"],counts["oog"] = 3,1
        result = reservations_v5.widen_choices(native_style_choices)(boxes,counts)
        self.assertEqual(result[:2],["reefer","oog"])
        self.assertTrue({f"normal{i}" for i in range(6)}<=set(result))

    def test_reefer_hold_precedes_oog_that_would_consume_its_only_plugged_cell(self):
        from stowage.actions.space import Place
        ids = ["reefer","oog","ordinary","deck_reefer"]
        t = NS(box_ids=ids,box_index={cid:i for i,cid in enumerate(ids)},length=np.array([20,40,40,20]),
               rank=np.ones(4,int),height=np.full(4,2.6),slot_ids=["plugged_hold","oog_hold","ordinary_hold","plugged_deck"],
               stack_deck=np.array([0,0,0,0,1],bool),slot_stacks=np.array([[0,-1],[0,1],[2,3],[4,-1]]))
        domains = np.eye(4,dtype=bool)
        boxes = {cid:NS(reefer=cid in {"reefer","deck_reefer"},special_stowage=[],oog=NS() if cid=="oog" else None)
                 for cid in ids}
        future = NS(t=t,boxes=boxes,signatures={cid:cid for cid in ids},
                    address=[NS(on_deck=i==3) for i in range(4)],
                    evaluate=lambda ex,remaining:(np.arange(4),domains))
        cached = np.array([0,5,10,15])
        node = NS(engine=NS(backend=NS(_cur=NS(evaluated=True,mask=cached))),ex=NS(where={},buffer=[]))
        actions = [Place("oog","oog_hold"),Place("ordinary","ordinary_hold"),
                   Place("deck_reefer","plugged_deck"),Place("reefer","plugged_hold")]
        result = reservations_v5.rank_bookings(NS(load_list=ids),node,future,actions,{},time.monotonic()+5)
        self.assertEqual([a.box for a in result],["reefer","oog","ordinary","deck_reefer"])
        self.assertEqual(set(result),set(actions))


if __name__ == "__main__":
    unittest.main()
