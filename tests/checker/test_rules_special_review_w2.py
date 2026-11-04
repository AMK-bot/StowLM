"""Review regressions: fixed physical OOG conflicts and lost capacity metrics."""
import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.checker.rules_special import killed_slots, special_violations
from stowage.model import Instance
from stowage.model.cargo import OutOfGauge
from stowage.model.vessel import FixedCargoBox, FixedLoad
from stowage.rules.catalogue import rule_freeze_v1
from tests.checker.test_rules_special_w2 import scenario


def fixed_case(*, long_decision=False, long_fixed=False, fixed_bay=1):
    instance, ship, _, _ = scenario(OutOfGauge(over_width_port_m=.3),
                                   "0020182" if long_decision else "0010182", long=long_decision)
    vessel = instance.vessel
    vessel.fixed_load_cells = [c for c in vessel.cells if c.stack_id[3:5] == "02"]
    vessel.fixed_load_stacks = [s for s in vessel.stacks if s.row == 2]
    vessel.fixed_load_hatch_covers = [h for h in vessel.hatch_covers if h.id == "HC-02-2"]
    vessel.cells = [c for c in vessel.cells if c not in vessel.fixed_load_cells]
    vessel.stacks = [s for s in vessel.stacks if s not in vessel.fixed_load_stacks]
    vessel.hatch_covers = [h for h in vessel.hatch_covers if h not in vessel.fixed_load_hatch_covers]
    vessel.nominal_teu = len(vessel.cells)
    ids = ["0010282", "0030282"] if long_fixed else [f"{fixed_bay:03}0282"]
    supports = [c for c in vessel.fixed_load_cells if c.id in ids]
    box = FixedCargoBox(cell_ids=ids, length_ft=40 if long_fixed else 20,
                        height_m=2.591, gross_mass_kg=12000,
                        x_m=sum(c.lcg_m for c in supports) / len(supports),
                        y_m=supports[0].tcg_m, z_m=supports[0].vcg_base_m + 2.591 / 2,
                        stack_id=supports[0].stack_id, tier=82)
    vessel.fixed_loads = [FixedLoad(x_m=box.x_m, y_m=box.y_m, z_m=box.z_m,
                                    mass_kg=box.gross_mass_kg, kind="cargo", cargo_boxes=[box])]
    return Instance.model_validate(instance.model_dump()), ship


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("entry", ["static", "incremental"])
@pytest.mark.parametrize("long_decision,long_fixed,fixed_bay", [
    (False, False, 1), (True, False, 1), (True, False, 3), (False, True, 1), (True, True, 1),
])
def test_fixed_cargo_in_killed_neighbour_is_hard(entry, enabled, long_decision, long_fixed, fixed_bay):
    instance, ship = fixed_case(long_decision=long_decision, long_fixed=long_fixed, fixed_bay=fixed_bay)
    p = ship.placements[0]
    box = next(b for b in instance.containers if b.id == p.container_id)
    # Reviewer reproduction: width 2.438 m, row pitch 2.6 m, overhang .3 m.
    assert 2.438 + .3 - 2.6 == pytest.approx(.138)
    with rule_freeze_v1(enabled=enabled):
        if entry == "static":
            violations = check_static_rules(instance, ship, call_index=0).violations
        else:
            partial = ship.model_copy(update={"placements": []})
            violations = placement_violations(instance, partial, box, p.slot, call_index=0)
        found = [v for v in violations if v.rule_id == "expert.e9.over_width_length"]
        assert bool(found) == enabled
        if enabled:
            assert all(v.severity == "hard" for v in found)
            assert all(v.containers == [box.id] for v in found)
            assert any(len(v.slots) == 2 for v in found)


def three_tiers(instance):
    for stack in instance.vessel.stacks:
        if stack.on_deck:
            previous = next(c for c in instance.vessel.cells if c.id == stack.cell_ids[-1])
            cid = previous.id[:5] + "86"
            instance.vessel.cells.append(previous.model_copy(update={"id": cid, "vcg_base_m": previous.vcg_base_m + 2.591}))
            stack.cell_ids.append(cid)
            stack.max_height_m = 9


@pytest.mark.parametrize("long", [False, True])
def test_lost_capacity_metric_prefers_top_stowage(long):
    instance, lower, _, _ = scenario(OutOfGauge(over_width_port_m=.01),
                                    "0020182" if long else "0010182", long=long)
    three_tiers(instance)
    top = lower.model_copy(deep=True)
    top.placements[0].slot = "0020186" if long else "0010186"
    with rule_freeze_v1():
        assert len(killed_slots(instance, lower)) == (6 if long else 3)
        assert len(killed_slots(instance, top)) == (2 if long else 1)


def test_overheight_capacity_closes_upward_without_changing_direct_hard_kills():
    instance, ship, _, other = scenario(OutOfGauge(over_height_m=.01))
    three_tiers(instance)
    with rule_freeze_v1():
        assert killed_slots(instance, ship) == {"0010184", "0010186"}
        # A support gap is independently illegal in the move engine; the metric
        # closure must not turn that into a new direct E9 killed-slot violation.
        ship.placements.append(ship.placements[0].model_copy(update={"container_id": other.id, "slot": "0010186"}))
        assert not any(v.rule_id == "expert.e9.over_height"
                       for v in special_violations(instance, ship, call_index=0))


def test_fixed_geometry_capacity_is_counted_in_static_report():
    instance, ship = fixed_case()
    with rule_freeze_v1():
        assert killed_slots(instance, ship) == {"0010282", "0010284"}
        assert check_static_rules(instance, ship, call_index=0).metrics["killed_slots"] == 2


@pytest.mark.parametrize("entry", ["static", "incremental"])
def test_fixed_biased_cg_does_not_move_its_physical_envelope(entry):
    instance, ship = fixed_case()
    candidate = next(b for b in instance.containers if b.id == ship.placements[0].container_id)
    candidate.oog.over_height_m = .4
    lower = instance.vessel.fixed_loads[0].cargo_boxes[0]
    # CG may lie anywhere inside the box; this is deliberately near its top.
    # The real upper box still starts at 11.591 m, overlapping the candidate's
    # 11.991 m top. A rectangle centered on this CG falsely starts at 12.8765 m.
    upper = lower.model_copy(update={"cell_ids": ["0010284"], "tier": 84,
                                     "z_m": 9 + 2 * 2.591 - .01})
    instance.vessel.fixed_loads.append(FixedLoad(x_m=upper.x_m, y_m=upper.y_m,
                                                z_m=upper.z_m, mass_kg=upper.gross_mass_kg,
                                                kind="cargo", cargo_boxes=[upper]))
    instance = Instance.model_validate(instance.model_dump())
    with rule_freeze_v1():
        if entry == "static":
            out = check_static_rules(instance, ship, call_index=0).violations
        else:
            out = placement_violations(instance, ship.model_copy(update={"placements": []}), candidate,
                                       ship.placements[0].slot, call_index=0)
        assert any(v.rule_id == "expert.e9.over_width_length" and "0010284" in v.slots for v in out)
