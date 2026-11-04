"""Exact tiny comparisons with spanning boxes and independently opened covers.

The cells and legal destinations remain the move engine's responsibility; the
fixture shrinks physical geometry rather than pruning the oracle's moves.
"""

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.checker.rules import check_static_rules
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import apply_plan


def spanning_instance(*, mixed_yard: bool, initially_open: bool = False,
                      length_ft: int = 40) -> Instance:
    data = build_instance(layover=True).model_dump()
    boxes = data["containers"][:3 if mixed_yard else 4]
    a, b, c = [box["id"] for box in boxes[:3]]
    for box in boxes:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
                   tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30480,
                   reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], pod="THBKK")
    boxes[0]["pod"] = "THLCH"
    spanning_box = boxes[0 if mixed_yard else 1]
    spanning_box.update(length_ft=length_ft, iso_type="42G1" if length_ft == 40 else "L2G1",
                        tare_kg=4200)
    data.update(id=f"bounds_spanning_{mixed_yard}_{initially_open}_{length_ft}", containers=boxes,
                load_list=[b] if mixed_yard else [], discharge_list=[a])
    data["rule_overrides"] = {
        "expert.e1.heavy_bottom": {"heavy_threshold": 20000},
        "expert.e2.weight_order": {"tolerance": 0},
        "expert.e6.heavy_under_deck": {"heavy_threshold": 20000},
    }
    # No unrelated placeholder cargo: these tests isolate actual modeled cells.
    data["vessel"]["fixed_loads"] = []
    keep = {"00101H", "00301H", "00101D", "00301D"}
    data["vessel"]["stacks"] = [s for s in data["vessel"]["stacks"] if s["id"] in keep]
    for stack in data["vessel"]["stacks"]:
        stack["hatch_cover_id"] = "HC-02-1" if stack["bay"] == 1 else "HC-02-2"
        # Keep two hold tiers only when the span is itself an upper blocker.
        if stack["on_deck"] or mixed_yard:
            stack["cell_ids"] = stack["cell_ids"][:1]
    cell_ids = {cid for s in data["vessel"]["stacks"] for cid in s["cell_ids"]}
    data["vessel"]["cells"] = [cell for cell in data["vessel"]["cells"] if cell["id"] in cell_ids]
    for cover, bay in zip(data["vessel"]["hatch_covers"], (1, 3)):
        cover.update(bays=[bay], hold_stack_ids=[f"{bay:03d}01H"],
                     deck_stack_ids=[f"{bay:03d}01D"])
    block = data["port"]["yard"]["blocks"][0]
    # A discharged 40' box reserves two yard bays; the third bay leaves room
    # for the unrelated 20' blocker after the load stack has been cleared.
    block.update(n_bays=3 if mixed_yard else 2, n_rows=1, max_tiers=2,
                 allowed_lengths_ft=[20, length_ft])
    data["port"]["yard"]["blocks"] = [block]
    data["yard"].update(quay_buffer=[], stacks=[
        dict(key="A-001-01", length_ft=20, boxes=[b, c])
    ] if mixed_yard else [])
    data["onboard"].update(covers_off={"HC-02-1": "quay"} if initially_open else {}, placements=[
        dict(slot="0020102", container_id=a),
    ] if mixed_yard else [
        dict(slot="0010102", container_id=a),
        dict(slot="0020104", container_id=b),
        dict(slot="0030102", container_id=c),
        dict(slot="0030182", container_id=boxes[3]["id"]),
    ])
    data["objective"].update(relocate=1.5, restow=0.75, hatch_cover=2.25,
                             future_restow=0.5, future_hatch_cover=0.25)
    return Instance.model_validate(data)


@pytest.mark.parametrize("mixed_yard", [False, True])
@pytest.mark.parametrize("initially_open", [False, True])
@pytest.mark.parametrize("length_ft", [40, 45])
def test_spanning_two_cover_bounds_against_exact_feasible_oracle(mixed_yard, initially_open,
                                                                length_ft, record_property):
    instance = spanning_instance(mixed_yard=mixed_yard, initially_open=initially_open,
                                 length_ft=length_ft)
    # No scheduling-dependent wall cap: a finite number of expanded states
    # determines whether the exhaustive proof is complete.
    result = brute_force_optimum(instance, max_nodes=10000,
                                 time_limit_s=None, check_static_rules=True)
    assert result.status == "optimal", result
    assert result.plan is not None
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert set(instance.load_list) <= {p.container_id for p in replay.final_state.ship.placements}
    static = check_static_rules(instance, replay.final_state.ship, call_index=instance.call_index)
    assert static.ran and not [v for v in static.violations if v.severity == "hard"]
    bound = lower_bound(instance)
    assert bound.hatch_cover_lifts == (3 if initially_open else 4)
    assert bound.relocations == int(mixed_yard)
    assert bound.restow_lifts == (0 if mixed_yard else 2)
    assert bound.weighted <= result.objective + 1e-9
    assert replay.count.weighted_nonproductive == pytest.approx(result.objective)
    record_property("oracle_nodes", result.expanded_nodes)
    record_property("exact_objective", result.objective)
