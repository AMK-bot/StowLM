"""Hand-solved move-domain optima; synthetic geometry, no physics certificate."""

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import count_moves
from stowage.solver.exact import solve_exact


def _deck_instance(*, alternate_stack):
    data = build_instance().model_dump()
    boxes = data["containers"][1:3]
    for box, pod in zip(boxes, ("THLCH", "THBKK")):
        box.update(dg=None, oog=None, special_stowage=[], pod=pod,
                   max_gross_mass_kg=30000)
    target, blocker = [box["id"] for box in boxes]
    data.update(containers=boxes, stage="B", load_list=[], discharge_list=[target])
    data["yard"].update(stacks=[], quay_buffer=[])
    data["onboard"].update(placements=[
        dict(slot="0010182", container_id=target),
        dict(slot="0010184", container_id=blocker),
    ], covers_off={})
    keep = {"00101D", "00301D"} if alternate_stack else {"00101D"}
    stacks = [stack for stack in data["vessel"]["stacks"] if stack["id"] in keep]
    for stack in stacks:
        stack["hatch_cover_id"] = None
    cells = {cid for stack in stacks for cid in stack["cell_ids"]}
    data["vessel"].update(stacks=stacks, hatch_covers=[], fixed_loads=[])
    data["vessel"]["cells"] = [cell for cell in data["vessel"]["cells"] if cell["id"] in cells]
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=1, n_rows=1)
    data["port"]["yard"]["blocks"] = [block]
    data["port"]["max_crane_height_tiers"] = 2
    data["objective"].update(restow=1.25, future_restow=0, future_hatch_cover=0)
    return Instance.model_validate(data)


@pytest.mark.parametrize("alternate_stack, lifts", [(False, 2), (True, 1)])
def test_one_lift_bound_is_safe_for_both_off_on_and_direct_shift(alternate_stack, lifts):
    instance = _deck_instance(alternate_stack=alternate_stack)
    # No alternate ship stack means the blocker MUST go off/on (two lifts).
    # With another stack it can shift once. A universal 2x bound is unsafe.
    oracle = brute_force_optimum(instance, time_limit_s=None)
    exact = solve_exact(instance, check_physics=False, check_static_rules=False,
                        time_limit_s=5, max_nodes=10000, num_workers=1)
    assert oracle.status == "optimal"
    assert exact.status == "OPTIMAL"
    assert oracle.objective == exact.objective == 1.25 * lifts
    assert count_moves(instance, oracle.plan).restow_lifts == lifts
    assert count_moves(instance, exact.plan).restow_lifts == lifts
    bound = lower_bound(instance)
    assert bound.restow_lifts == 1
    assert bound.weighted == 1.25 <= exact.objective


def test_soft_e6_introduces_no_under_deck_work_in_bound(bounds_rule_freeze):
    data = _deck_instance(alternate_stack=False).model_dump()
    box = data["containers"][0]
    box.update(pod="THBKK", gross_mass_kg=20000)
    data.update(containers=[box], load_list=[box["id"]], discharge_list=[])
    data["onboard"]["placements"] = []
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[box["id"]])]
    # Explicit legacy policy: disable its no-compatible-hold exception so the
    # switch actually enlarges the feasible domain in this deck-only fixture.
    data["rule_overrides"] = {"expert.e6.heavy_under_deck": {
        "heavy_threshold": 20000, "allow_on_deck_if_no_compatible_hold_cell": False,
    }}
    instance = Instance.model_validate(data)
    assert lower_bound(instance).weighted == 0
    oracle = brute_force_optimum(instance, check_static_rules=True, time_limit_s=None)
    # This deck-only instance has no legacy E6-feasible departure. Turning E6
    # soft permits a productive load, with zero extra lifts.
    assert oracle.status == ("optimal" if bounds_rule_freeze else "infeasible")
    assert oracle.objective == (0 if bounds_rule_freeze else None)
