"""A manually edited truck endpoint is not an ordinary Place's expansion.

The diagnostic-preflip-20261029-023537 flagged_nonclass1 pair has identical
inputs/actions: only the defect's manual Load.src changes from yard to truck.
Pruning that action would also prune the checker's accepted baseline plan.
"""
import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import MovePlan
from stowage.model.yard import YardStack
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1


def test_manual_truck_defect_is_not_the_canonical_action(h):
    inst = tiny_instance(91, n_load=1, n_other=0, p40=0, max_tiers=2)
    inst.vessel.hull = None
    inst.vessel.fixed_loads = []
    box = inst.containers[0]
    inst.yard.stacks = [YardStack(key="A-001-01", length_ft=20, boxes=[box.id])]
    action = Place(box.id, "0010182")
    with rule_freeze_v1():
        moves = Expander(inst).step(action)
        canonical = MovePlan(id="canonical", instance_id=inst.id,
                             call_index=inst.call_index, moves=moves)
        manual = canonical.model_copy(deep=True)
        manual.moves[-1].src = "truck"
        assert box.dg is None and not box.direct_delivery
        assert canonical.moves[-1].src == "A-001-01-01"
        assert canonical.moves != manual.moves
        replay = apply_plan(inst, canonical)
        assert replay.legal, replay.violations
        refused = apply_plan(inst, manual)
        assert not refused.legal
        assert "port.dg.direct_delivery" in {v.rule_id for v in refused.violations}
        eng = h.engine(inst, h.DIAG)
        assert eng.is_legal(action)
        eng.push(action)
        assert eng.to_plan().moves == canonical.moves


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_nonclass1_flag_mismatch_is_screened(h, enabled):
    inst = tiny_instance(91, n_load=1, n_other=0, p40=0, max_tiers=2)
    inst.vessel.hull = None
    inst.vessel.fixed_loads = []
    box = inst.containers[0]
    inst.yard.stacks = [YardStack(key="A-001-01", length_ft=20, boxes=[box.id])]
    # Deliberately bypass model input validation to probe feasibility's guard.
    box.direct_delivery = True
    action = Place(box.id, "0010182")
    with rule_freeze_v1(enabled=enabled):
        eng = h.engine(inst, h.DIAG)
        assert eng.is_legal(action) is (not enabled)
        assert Expander(inst).can_expand(action) is (not enabled)
