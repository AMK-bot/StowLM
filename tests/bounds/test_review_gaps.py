"""Regression witnesses for rule restrictions omitted by the counting bounds.

The oracle enumerates the move-engine relaxation, with static departure checks
disabled. These tiny cases isolate crossing, refusal and future crane height;
they are not full-physics loading certificates.
"""

from stowage.bounds import lower_bound
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves.engine import apply_plan


def _tiny_data(*, boxes=2, second_ship_stack=False):
    data = build_instance().model_dump()
    stack_ids = {"00101D"} | ({"00301D"} if second_ship_stack else set())
    stacks = [s for s in data["vessel"]["stacks"] if s["id"] in stack_ids]
    for stack in stacks:
        stack.update(hatch_cover_id=None)
        if stack["id"] == "00101D":
            stack["cell_ids"] = ["0010182", "0010184"]
        else:
            stack["cell_ids"] = ["0030182"]
    cells = {cell for stack in stacks for cell in stack["cell_ids"]}
    data["vessel"].update(stacks=stacks, fixed_loads=[], hatch_covers=[])
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in cells]
    data["containers"] = data["containers"][1:1 + boxes]
    for box in data["containers"]:
        box.update(length_ft=20, iso_type="22G1", height_m=2.591,
                   tare_kg=2200, gross_mass_kg=12000, max_gross_mass_kg=30000,
                   reefer=False, reefer_setpoint_c=None, dg=None, oog=None,
                   special_stowage=[], direct_delivery=False, pod="THBKK")
    block = data["port"]["yard"]["blocks"][0]
    block.update(n_bays=2, n_rows=2, max_tiers=2, truck_lane_side="low")
    data["port"]["yard"]["blocks"] = [block]
    data["yard"].update(stacks=[], quay_buffer=[])
    data["onboard"].update(placements=[], covers_off={})
    data.update(load_list=[], discharge_list=[])
    for component in ("relocate", "restow", "hatch_cover", "future_restow", "future_hatch_cover"):
        data["objective"][component] = 1
    return data


def _solve(data):
    instance = Instance.model_validate(data)
    result = brute_force_optimum(instance, check_static_rules=False,
                                 time_limit_s=None, max_nodes=10000)
    if result.status == "optimal":
        assert lower_bound(instance).weighted <= result.objective
        replay = apply_plan(instance, result.plan)
        assert replay.legal, replay.violations
        assert replay.count.weighted_nonproductive == result.objective
    return instance, result


def test_binding_two_row_yard_crossing(bounds_rule_freeze):
    data = _tiny_data(boxes=3)
    target, lower, upper = (box["id"] for box in data["containers"])
    data["load_list"] = [target]
    data["yard"]["stacks"] = [
        dict(key="A-001-02", length_ft=20, boxes=[target]),
        dict(key="A-001-01", length_ft=20, boxes=[lower, upper]),
    ]
    instance, result = _solve(data)
    assert lower_bound(instance).weighted == 0
    assert result.status == "optimal"
    assert result.objective == int(bounds_rule_freeze)
    if bounds_rule_freeze:
        assert [m.kind for m in result.plan.moves] == ["relocate", "load"]
        relocation = result.plan.moves[0]
        assert relocation.src == "A-001-01-02"
        assert relocation.dst.startswith("A-002-")
    else:
        assert [m.kind for m in result.plan.moves] == ["load"]


def test_refused_class_through_blocker(bounds_rule_freeze):
    data = _tiny_data(second_ship_stack=True)
    target, blocker = data["containers"]
    target["pod"] = data["port"]["locode"]
    blocker["dg"] = dict(un_number="2915", imdg_class="7", stowage_category="01")
    data["port"]["port_rules"] = [
        dict(rule_id="port.dg.refused_class", params={"classes": ["7"]})]
    data["discharge_list"] = [target["id"]]
    data["onboard"]["placements"] = [
        dict(slot="0010182", container_id=target["id"]),
        dict(slot="0010184", container_id=blocker["id"]),
    ]
    instance, result = _solve(data)
    assert lower_bound(instance).weighted == 1
    if bounds_rule_freeze:
        assert result.status == "infeasible"
        assert result.plan is None
    else:
        assert result.status == "optimal"
        assert result.objective == 1


def test_unrepairable_future_height_rejects_terminal_candidate(bounds_rule_freeze):
    data = _tiny_data()
    target, support = data["containers"]
    support["pod"] = "THSGZ"
    data["onboard"]["placements"] = [
        dict(slot="0010182", container_id=support["id"]),
        dict(slot="0010184", container_id=target["id"]),
    ]
    data["rotation"]["calls"][1]["max_on_deck_tiers"] = 1
    instance, result = _solve(data)
    assert lower_bound(instance).weighted == 0
    if bounds_rule_freeze:
        assert result.status == "infeasible"
        assert result.plan is None
    else:
        assert result.status == "optimal"
        assert result.objective == 0
