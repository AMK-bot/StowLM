"""Exact resolver parity and TRAIN initial-mask/local-rule regression checks."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent))
import oracle_v5

RULE = "expert.e9.over_width_length"


def parameters(instance=None, port=None):
    return NS(rule_overrides={RULE:instance} if instance is not None else {},
              port=NS(port_rules=[NS(rule_id=RULE,params=port)] if port is not None else []))


class ResolverTests(unittest.TestCase):
    def test_catalogue_default_and_checker_functions_unchanged(self):
        from stowage.feasibility import _engine
        from stowage.checker import rules_special
        from stowage.rules.catalogue import rule_freeze_v1
        originals = (rules_special._parameter,rules_special._clearance_parameter,rules_special.special_violations)
        with rule_freeze_v1(enabled=False),oracle_v5.install():
            self.assertEqual(_engine._parameter(parameters(),RULE,"oog_clearance","minimum_clearance_m"),0.)
            self.assertEqual(rules_special._parameter(parameters(),RULE,"oog_clearance"),None)
            self.assertEqual(originals,(rules_special._parameter,rules_special._clearance_parameter,rules_special.special_violations))

    def test_aliases_invalid_values_and_port_precedence_match_public_resolver(self):
        from stowage.feasibility import _engine
        from stowage.checker.rules_special import _clearance_parameter,_nonnegative
        from stowage.rules.catalogue import rule_freeze_v1
        cases = [(parameters({"oog_clearance":.3},{"oog_clearance":.8}),.3),
                 (parameters({"minimum_clearance_m":.2},{"oog_clearance":.9}),.2),
                 (parameters(None,{"oog_clearance":.4}),.4),
                 (parameters(None,{"minimum_clearance_m":.6}),.6)]
        with rule_freeze_v1(enabled=False),oracle_v5.install():
            for instance,expected in cases:
                self.assertEqual(_engine._parameter(instance,RULE,"minimum_clearance_m"),expected)
                self.assertEqual(_engine._parameter(instance,RULE,"oog_clearance","minimum_clearance_m"),
                                 _clearance_parameter(instance,RULE,"oog_clearance","minimum_clearance_m"))
            for value in (None,-1,True,"bad",{"value":None}):
                instance = parameters({"oog_clearance":value},{"oog_clearance":.7})
                resolved = _engine._parameter(instance,RULE,"oog_clearance","minimum_clearance_m")
                self.assertIsNone(_nonnegative(resolved))

    def test_non_e9_and_activated_rules_delegate_and_nested_ownership_restores(self):
        from stowage.feasibility import _engine
        from stowage.checker.rules_special import _parameter
        from stowage.rules.catalogue import rule_freeze_v1
        originals = {name:getattr(_engine.IncrementalEngine,name) for name in oracle_v5.METHODS}
        had_global = hasattr(_engine,"_parameter")
        with rule_freeze_v1(enabled=False):
            outer = oracle_v5.install()
            resolver = _engine._parameter
            inner = oracle_v5.install()
            outer.restore()
            self.assertIs(_engine._parameter,resolver)
            instance = NS(rule_overrides={"other":{"x":7}},port=NS(port_rules=[]))
            self.assertEqual(resolver(instance,"other","x"),_parameter(instance,"other","x"))
            with rule_freeze_v1(enabled=True):
                self.assertIsNone(resolver(parameters(),RULE,"oog_clearance","minimum_clearance_m"))
            inner.restore()
            inner.restore()
        self.assertEqual(hasattr(_engine,"_parameter"),had_global)
        for name,method in originals.items():
            self.assertIs(getattr(_engine.IncrementalEngine,name),method)


class NativeTrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig
        from stowage.io import load_instance
        from stowage.rules.catalogue import rule_freeze_v1
        from stowage.solver.twostep import _canonical_instance
        from stowage.solver.twostep_adapter import make_planning_engine
        root = Path(__file__).resolve().parents[3]
        path = root/"experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-high/seed-192200100.json"
        cls.canonical = _canonical_instance(load_instance(path))
        with rule_freeze_v1(enabled=False),oracle_v5.install():
            scoped = make_planning_engine(cls.canonical,get_action_space("boxslot"),
                                          config=FeasibilityConfig(extra={"global_screen":0}))
            cls.engine = scoped._native
            cls.bis,cls.sis = cls.engine.legal_pairs()
            cls.legal = set(zip(map(int,cls.bis),map(int,cls.sis)))

    def test_real_initial_mask_accepts_both_previously_absent_oog_bookings(self):
        counts = {cid:int(np.count_nonzero(self.bis==self.engine.T.box_index[cid]))
                  for cid in ("QZXU0000307","QZXU0001258")}
        for cid,count in counts.items():
            self.assertGreater(count,0,cid)
        print("oracle_v5 TRAIN initial native counts",counts,flush=True)

    def test_invalid_clearance_still_rejects_native_oog_batch(self):
        from stowage.rules.catalogue import rule_freeze_v1
        t = self.engine.T
        bi = t.box_index["QZXU0000307"]
        si = int(self.sis[self.bis==bi][0])
        with rule_freeze_v1(enabled=False),oracle_v5.install():
            for value in (None,-1,True,"bad"):
                native = self.engine.copy()
                native.instance = self.canonical.model_copy(update={"rule_overrides":{
                    **self.canonical.rule_overrides,RULE:{"oog_clearance":value}}})
                self.assertFalse(bool(native._e9_batch(native._cur,np.array([bi]),np.array([si]),all_boxes=True)[0]))

    def test_bad_oog_neighbor_is_rejected_by_both_unchanged_public_rules_and_native_mask(self):
        from stowage.actions.space import Place
        from stowage.checker.rules import placement_violations
        from stowage.rules.catalogue import rule_freeze_v1
        t,boxes = self.engine.T,self.engine._boxes
        target = "QZXU0000307"
        target_bi = t.box_index[target]
        target_slots = [int(si) for si in self.sis[self.bis==target_bi]
                        if t.slot_deck[si] and int(max(t.slot_k[si]))==0]
        found = None
        for si in target_slots:
            x,y = self.engine._slot_xy[si]
            for bj,sj in self.legal:
                box = boxes[t.box_ids[bj]]
                if box.length_ft!=40 or box.oog or box.dg or box.reefer or not t.slot_deck[sj]:
                    continue
                xx,yy = self.engine._slot_xy[sj]
                if abs(xx-x)<.001 and 2.438<abs(yy-y)<2.688:
                    found = si,bj,sj
                    break
            if found:
                break
        self.assertIsNotNone(found,"TRAIN fixture must have a neighboring deck slot")
        si,bj,sj = found
        with rule_freeze_v1(enabled=False),oracle_v5.install():
            native = self.engine.copy()
            native.push(Place(t.box_ids[bj],t.slot_ids[sj]))
            ship = native.instance.onboard.model_copy(update={"placements":native._ship(native._cur).placements})
            violations = placement_violations(native.instance,ship,boxes[target],t.slot_ids[si],
                                              call_index=native.instance.call_index)
            self.assertIn(RULE,{v.rule_id for v in violations if v.severity=="hard"})
            self.assertFalse(native.is_legal(Place(target,t.slot_ids[si])))
            print("oracle_v5 conflicting neighbor rejected",t.box_ids[bj],t.slot_ids[sj],target,t.slot_ids[si],flush=True)


if __name__ == "__main__":
    unittest.main()
