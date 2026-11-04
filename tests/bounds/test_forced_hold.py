"""Deck-capacity witnesses for unavoidable current and D15 cover lifts.

These exact move-engine relaxation optima isolate cover work: the current
arrival is empty and all retained cargo discharges together at the next call.
Deck cargo therefore discharges productively before the hold cover opens.
They do not certify ballast, stability, or other full-physics constraints.
"""

import pytest

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.bounds.components import (
    current_hatch_cover_bound, forced_hold_cover_bounds, future_hatch_cover_bound,
)
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import apply_plan


def _instance(*, loads=3, initially_off=False, discharge=False, spanning=False,
              current_weight=1, future_weight=1):
    data = build_instance(layover=True).model_dump()
    boxes = data["containers"][:loads + int(discharge)]
    for box in boxes:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
                   tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000,
                   reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], direct_delivery=False, pod="THBKK")
    if spanning:
        boxes[0].update(length_ft=40, iso_type="42G1", tare_kg=4200)
    load_ids = [box["id"] for box in boxes[:loads]]
    discharge_ids = [boxes[-1]["id"]] if discharge else []
    if discharge:
        boxes[-1]["pod"] = data["port"]["locode"]
    data.update(containers=boxes, load_list=load_ids, discharge_list=discharge_ids,
                stage="B" if discharge else "A")
    # Two raw deck cells. A spanning box consumes both, so its demand is two
    # cells even though it is a single container identity.
    keep = {"00101D", "00301D", "00101H"} | ({"00301H"} if spanning else set())
    stacks = [s for s in data["vessel"]["stacks"] if s["id"] in keep]
    for stack in stacks:
        stack["hatch_cover_id"] = "HC-02-1"
        if stack["on_deck"] or spanning:
            stack["cell_ids"] = stack["cell_ids"][:1]
        else:
            stack["cell_ids"] = stack["cell_ids"][:2]
    cells = {cell for stack in stacks for cell in stack["cell_ids"]}
    cover = data["vessel"]["hatch_covers"][0]
    cover.update(bays=[1, 3], hold_stack_ids=[s["id"] for s in stacks if not s["on_deck"]],
                 deck_stack_ids=[s["id"] for s in stacks if s["on_deck"]])
    data["vessel"].update(stacks=stacks, hatch_covers=[cover], fixed_loads=[])
    data["vessel"]["cells"] = [cell for cell in data["vessel"]["cells"] if cell["id"] in cells]
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=3 if spanning else 2, n_rows=1, max_tiers=4,
                 allowed_lengths_ft=[20, 40] if spanning else [20])
    data["port"]["yard"]["blocks"] = [block]
    if spanning:
        yard = [dict(key="A-001-01", length_ft=40, boxes=load_ids[:1]),
                dict(key="A-003-01", length_ft=20, boxes=load_ids[1:])]
    else:
        yard = [dict(key="A-001-01", length_ft=20, boxes=load_ids)]
    data["yard"].update(stacks=yard, quay_buffer=[])
    data["onboard"].update(placements=[dict(slot="0010102", container_id=discharge_ids[0])]
                            if discharge else [],
                            covers_off={"HC-02-1": "quay"} if initially_off else {})
    data["objective"].update(relocate=1, restow=1, hatch_cover=current_weight,
                             future_restow=1, future_hatch_cover=future_weight)
    return Instance.model_validate(data)


def _exact(instance, expected):
    result = brute_force_optimum(instance, max_nodes=10000, time_limit_s=None)
    assert result.status == "optimal", result
    assert result.objective == pytest.approx(expected)
    bound = lower_bound(instance)
    assert bound.weighted <= result.objective
    replay = apply_plan(instance, result.plan)
    assert replay.legal, replay.violations
    assert replay.count.weighted_nonproductive == pytest.approx(expected)
    return bound


@pytest.mark.parametrize(("current_weight", "future_weight", "expected"),
                         [(1, 1, 4), (3, 3, 12), (6, 6, 24), (1.5, 0.25, 3.5)])
def test_forced_hold_cover_count_equals_exact_optimum(current_weight, future_weight, expected):
    instance = _instance(current_weight=current_weight, future_weight=future_weight)
    assert forced_hold_cover_bounds(instance) == (2, 2)
    bound = _exact(instance, expected)
    assert (bound.hatch_cover_lifts, bound.future_hatch_cover_lifts) == (2, 2)
    assert bound.weighted == pytest.approx(expected)


@pytest.mark.parametrize(("current_weight", "future_weight", "expected"), [(1, 0, 2), (0, 1, 2)])
def test_forced_current_and_future_cover_counts_are_independent_minima(current_weight,
                                                                    future_weight, expected):
    instance = _instance(current_weight=current_weight, future_weight=future_weight)
    bound = _exact(instance, expected)
    assert current_hatch_cover_bound(instance) == 2
    assert future_hatch_cover_bound(instance) == 2
    assert bound.weighted == expected


def test_loads_that_fit_on_deck_do_not_force_cover_work():
    instance = _instance(loads=2)
    assert forced_hold_cover_bounds(instance) == (0, 0)
    assert _exact(instance, 0).weighted == 0


def test_initially_open_cover_only_needs_current_closure():
    instance = _instance(initially_off=True)
    bound = _exact(instance, 3)
    assert (bound.hatch_cover_lifts, bound.future_hatch_cover_lifts) == (1, 2)
    assert bound.weighted == 3


def test_mandatory_discharge_and_forced_load_share_one_current_opening():
    instance = _instance(discharge=True)
    bound = _exact(instance, 4)
    assert (bound.hatch_cover_lifts, bound.future_hatch_cover_lifts) == (2, 2)
    assert bound.weighted == 4


def test_forty_foot_box_uses_two_cells_in_forced_hold_demand():
    instance = _instance(loads=2, spanning=True)
    assert forced_hold_cover_bounds(instance) == (2, 2)
    bound = _exact(instance, 4)
    assert bound.weighted == 4


def test_cover_reporting_distinguishes_current_and_future_weighted_costs():
    instance = _instance(current_weight=1.5, future_weight=0.25)
    bound = lower_bound(instance)
    assert bound.current_weighted == 3
    assert bound.future_weighted == 0.5
    assert bound.weighted == bound.current_weighted + bound.future_weighted
    assert bound.gap_informative
    assert bound.current_gap_informative


@pytest.mark.parametrize("zero_weights", [False, True])
def test_zero_lower_bound_explicitly_reports_no_certificate(zero_weights):
    instance = _instance(loads=3 if zero_weights else 2,
                         current_weight=0 if zero_weights else 1,
                         future_weight=0 if zero_weights else 1)
    bound = lower_bound(instance)
    assert bound.weighted == bound.current_weighted == bound.future_weighted == 0
    assert not bound.gap_informative
    assert bound.gap_note == "no certificate (L = 0)"
    assert not bound.current_gap_informative
    assert bound.current_gap_note == "no certificate (L_current = 0)"


def test_future_only_bound_does_not_certify_the_current_work_gap():
    instance = _instance(current_weight=0, future_weight=1)
    bound = lower_bound(instance)
    assert bound.current_weighted == 0
    assert bound.future_weighted == bound.weighted == 2
    assert bound.gap_informative
    assert not bound.current_gap_informative
    assert bound.current_gap_note == "no certificate (L_current = 0)"


def test_forced_hold_count_can_require_two_distinct_covers():
    data = _instance().model_dump()
    source = build_instance(layover=True).model_dump()
    first_hold = next(s for s in data["vessel"]["stacks"] if s["id"] == "00101H")
    first_hold["cell_ids"] = first_hold["cell_ids"][:1]
    data["vessel"]["stacks"] = [s for s in data["vessel"]["stacks"] if s["id"] != "00301D"]
    second_hold = next(s for s in source["vessel"]["stacks"] if s["id"] == "00301H")
    second_hold.update(cell_ids=["0030102"], hatch_cover_id="HC-02-2")
    data["vessel"]["stacks"].append(second_hold)
    cell_ids = {cid for s in data["vessel"]["stacks"] for cid in s["cell_ids"]}
    data["vessel"]["cells"] = [c for c in source["vessel"]["cells"] if c["id"] in cell_ids]
    data["vessel"]["hatch_covers"][0].update(
        hold_stack_ids=["00101H"], deck_stack_ids=["00101D"])
    second_cover = source["vessel"]["hatch_covers"][1]
    second_cover.update(hold_stack_ids=["00301H"], deck_stack_ids=[])
    data["vessel"]["hatch_covers"].append(second_cover)
    instance = Instance.model_validate(data)
    assert forced_hold_cover_bounds(instance) == (4, 4)
    bound = _exact(instance, 8)
    assert bound.hatch_cover_lifts == bound.future_hatch_cover_lifts == 4
    assert bound.weighted == 8
