"""Admissible remaining-work bounds and conservative state dominance for A*."""
from __future__ import annotations

from fractions import Fraction

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place, RestowOff
from stowage.bounds.bruteforce import brute_force_optimum
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state


def layover_slice():
    """One real fixture hatch and its two hold/deck columns, two 20' boxes."""
    data = build_instance(layover=True).model_dump()
    boxes = data["containers"][4:6]
    for box in boxes:
        box.update(dg=None, special_stowage=[], oog=None, gross_mass_kg=8000.)
    data.update(containers=boxes, load_list=[])
    data["yard"]["stacks"] = []
    data["port"]["yard"]["blocks"] = [data["port"]["yard"]["blocks"][0]]
    data["port"]["yard"]["blocks"][0].update(n_bays=1, n_rows=1)
    kept = {"00101H", "00301H", "00101D", "00301D"}
    data["vessel"]["stacks"] = [s for s in data["vessel"]["stacks"] if s["id"] in kept]
    cells = {c for s in data["vessel"]["stacks"] for c in s["cell_ids"]}
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in cells]
    data["vessel"]["hatch_covers"] = data["vessel"]["hatch_covers"][:1]
    data["rule_overrides"] = {
        "expert.e6.heavy_under_deck": {"heavy_threshold": 30000.},
        "expert.e1.heavy_bottom": {"heavy_threshold": 30000.},
        "expert.e2.weight_order": {"tolerance": 10000.},
    }
    return Instance.model_validate(data)


def prefixes(instance):
    ex, state = Expander(instance), initial_state(instance)
    yield "pending", state
    target = instance.discharge_list[0]
    through = next(p.container_id for p in state.ship.placements if p.container_id != target)
    for name, action in [("quay_and_open_cover", RestowOff(through)),
                         ("discharged_with_quay", Discharge(target)),
                         ("restored_with_open_cover", Place(through, "0010102"))]:
        for move in ex.step(action):
            state = apply_move(instance, state, move)
        yield name, state


def residual_instance(instance, state):
    """Independent oracle starts at the partial world, with pending targets only.

    model_copy is intentional: this is a continuation, including temporary quay
    storage, rather than a new externally serialized arrival manifest.
    """
    aboard = {p.container_id for p in state.ship.placements}
    return instance.model_copy(update={
        "yard": state.yard, "onboard": state.ship,
        "load_list": [cid for cid in instance.load_list if cid not in aboard],
        "discharge_list": [cid for cid in instance.discharge_list if cid in aboard],
    })


@pytest.mark.parametrize("prefix_index", range(4))
def test_partial_bound_never_exceeds_independent_remaining_optimum(prefix_index):
    from stowage.solver.exact_engine import partial_lower_bound

    instance = layover_slice()
    name, state = list(prefixes(instance))[prefix_index]
    result = brute_force_optimum(residual_instance(instance, state),
                                 check_static_rules=True, time_limit_s=5., max_nodes=100_000)
    assert result.status == "optimal", (name, result)
    bound = partial_lower_bound(instance, state)
    assert isinstance(bound, Fraction)
    assert 0 <= bound <= result.objective, (name, bound, result.objective)
    if state.ship.covers_off:
        assert bound >= instance.objective.hatch_cover
    if state.yard.quay_buffer:
        assert bound >= instance.objective.restow + instance.objective.hatch_cover
    if name.startswith("discharged"):
        # The already delivered box may not be charged another access cycle.
        assert bound == instance.objective.restow + instance.objective.hatch_cover


def test_partial_bound_uses_exact_fractional_weights_without_recharging_paid_lifts():
    from stowage.solver.exact_engine import partial_lower_bound

    instance = layover_slice()
    instance.objective.restow = .125
    instance.objective.hatch_cover = .375
    _, state = list(prefixes(instance))[2]
    assert partial_lower_bound(instance, state) == Fraction(1, 2)
    oracle = brute_force_optimum(residual_instance(instance, state), check_static_rules=True)
    assert oracle.status == "optimal" and oracle.objective == .5


def test_partial_bound_removes_already_loaded_targets():
    from stowage.solver.exact_engine import partial_lower_bound

    data = build_instance().model_dump()
    box = data["containers"][1]
    box.update(dg=None, oog=None, special_stowage=[])
    data.update(containers=[box], load_list=[box["id"]], discharge_list=[])
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[box["id"]])]
    instance = Instance.model_validate(data)
    ex = Expander(instance)
    state = initial_state(instance)
    for move in ex.step(Place(box["id"], "0010182")):
        state = apply_move(instance, state, move)
    assert partial_lower_bound(instance, state) == 0
    oracle = brute_force_optimum(residual_instance(instance, state))
    assert oracle.status == "optimal" and oracle.objective == 0


class PhysicalTestEngine:
    """A deliberate test-only engine constraint model; no physics certificate.

    Production engine integration uses real hulls in test_exact_integration.
    This double isolates A* expansion/costs from its expensive physics screens.
    """
    def __init__(self, instance, *, rejected_slot=None, reject_push=False, force_incomplete=False):
        self.instance = instance
        self.ex = Expander(instance)
        self.state = initial_state(instance)
        self.rejected_slot = rejected_slot
        self.reject_push = reject_push
        self.force_incomplete = force_incomplete

    def copy(self):
        from copy import copy
        result = copy(self)
        result.ex = self.ex.copy()
        result.state = self.state.model_copy(deep=True)
        return result

    def is_complete(self):
        aboard = {p.container_id for p in self.state.ship.placements}
        return (not self.force_incomplete and set(self.instance.load_list) <= aboard
                and not set(self.instance.discharge_list) & aboard
                and not self.state.yard.quay_buffer)

    def dead_end(self):
        return None

    def legal_actions(self):
        from stowage.solver.exact import _actions, _slots
        if self.is_complete():
            return []
        keys = tuple(f"{b.id}-{bay:03d}-{row:02d}" for b in self.instance.port.yard.blocks
                     for bay in range(1, b.n_bays + 1) for row in range(1, b.n_rows + 1))
        result = []
        for action in _actions(self.instance, self.ex, _slots(self.instance), keys, False):
            # Public v2 offers remaining-box placements and default discharges;
            # explicit relocations and ship shifts are outside that domain.
            if not isinstance(action, (Place, Discharge)):
                continue
            if isinstance(action, Discharge) and action.to is not None:
                continue
            if isinstance(action, Place) and self.ex.where[action.box][0] == "ship":
                continue
            if isinstance(action, Place) and action.slot == self.rejected_slot:
                continue
            try:
                self.copy().push(action)
                result.append(action)
            except ValueError:
                pass
        return result

    def push(self, action):
        from stowage.feasibility import IllegalActionError
        from stowage.moves import IllegalMoveError
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        if self.reject_push:
            raise IllegalActionError("synthetic canonical replay rejection")
        try:
            for move in self.ex.step(action):
                self.state = apply_move(self.instance, self.state, move)
            if rule_freeze_v1_enabled():
                # The comparison oracle below checks active static rules.
                # Keep this physical double in that same kg/killed domain.
                from stowage.checker.rules import check_static_rules
                report = check_static_rules(self.instance, self.state.ship,
                                             call_index=self.instance.call_index)
                if any(v.severity == "hard" for v in report.violations):
                    raise IllegalActionError("active static checker rejected test-engine placement")
        except IllegalMoveError as error:
            raise IllegalActionError(str(error)) from error


def install_test_engine(monkeypatch, *, factory=None):
    import stowage.feasibility as feasibility
    import stowage.solver.exact as exact_module
    import stowage.solver.exact_engine as engine_module
    from stowage.model import BallastCondition

    def accepted_terminal(inst, plan, ship, **kwargs):
        assert kwargs["physics"]
        return "yes", BallastCondition(tank_masses_kg={}, source="planner"), plan.provenance

    if factory is None:
        factory = lambda inst, space, config: PhysicalTestEngine(inst)
    monkeypatch.setattr(exact_module, "_validate_terminal", accepted_terminal)
    def public_factory(inst, space, **kwargs):
        assert not kwargs, "exact solver must use the public default engine configuration"
        return factory(inst, space, config=None)

    monkeypatch.setattr(feasibility, "make_feasibility_engine", public_factory)
    monkeypatch.setattr(engine_module, "make_feasibility_engine", public_factory)


def tiny_fixture(seed):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "experiments/exact-solver/benchmark.py"
    spec = importlib.util.spec_from_file_location("exact_astar_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.tiny_instance(seed)


@pytest.mark.parametrize("seed", range(50))
def test_engine_astar_matches_brute_when_engine_preserves_tiny_optimum(monkeypatch, seed):
    from stowage.solver.exact import solve_exact

    install_test_engine(monkeypatch)
    instance = tiny_fixture(seed)
    result = solve_exact(instance, check_physics=True, time_limit_s=5., max_nodes=100_000)
    oracle = brute_force_optimum(instance, check_static_rules=True, time_limit_s=5.)
    assert result.status == "OPTIMAL" and oracle.status == "optimal"
    assert result.objective == oracle.objective
    assert result.metrics["algorithm"] == "astar_branch_and_bound"
    assert result.metrics["optimality_scope"] == "engine_model_weighted_lifts"
    assert result.metrics["configured_engine_replay_verified"]
    assert result.metrics["checker_passed"]
    assert result.metrics["mask_verified"]
    assert result.metrics["shippable"]
    assert result.metrics["g1_status"] == "verified"


@pytest.mark.parametrize("mode", ["reject", "incomplete", "unavailable"])
def test_astar_cannot_ship_when_engine_gate_fails(monkeypatch, mode):
    from stowage.solver.exact import ExactPlanner, solve_exact

    calls = 0
    def factory(inst, space, config):
        nonlocal calls
        calls += 1
        if mode == "unavailable":
            raise NotImplementedError("synthetic unavailable engine")
        # Search uses a valid physical test domain; canonical final replay is
        # the independently failing gate this regression exercises.
        return PhysicalTestEngine(inst, reject_push=calls > 1 and mode == "reject",
                                  force_incomplete=calls > 1 and mode == "incomplete")

    install_test_engine(monkeypatch, factory=factory)
    instance = tiny_fixture(3)
    if mode == "unavailable":
        with pytest.raises(NotImplementedError):
            solve_exact(instance, check_physics=True)
    else:
        result = solve_exact(instance, check_physics=True, time_limit_s=5., max_nodes=64)
        assert result.status != "OPTIMAL"
        assert result.plan is None and result.objective is None and result.gap is None
        assert result.metrics["mask_rejections"] > 0
    calls = 0
    planned = ExactPlanner(num_workers=1, max_nodes=64).plan(instance, time_limit_s=5.)
    assert planned.status != "ok" and planned.plan is None


def equivalent_pair():
    instance = tiny_fixture(3)
    a, b = instance.load_list
    template = next(box for box in instance.containers if box.id == a)
    instance.containers = [template.model_copy(update={"id": cid, "category": "same_label"})
                           for cid in (a, b)]
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        "key": f"A-{i:03d}-01", "boxes": [cid]}) for i, cid in enumerate((a, b), 1)]
    return instance


def swapped_world(instance):
    state = initial_state(instance)
    swapped = state.model_copy(deep=True)
    swapped.yard.stacks[0].boxes, swapped.yard.stacks[1].boxes = (
        swapped.yard.stacks[1].boxes, swapped.yard.stacks[0].boxes)
    return state, swapped


def test_identical_semantic_boxes_merge_but_geometry_is_retained():
    from stowage.solver.exact_engine import canonical_state_key

    instance = equivalent_pair()
    state, swapped = swapped_world(instance)
    assert canonical_state_key(instance, state) == canonical_state_key(instance, swapped)
    # Renaming box identities is safe; moving a whole stack to another physical
    # yard key is not the same symmetry and must retain a distinct state.
    moved = swapped.model_copy(deep=True)
    moved.yard.stacks[1].key = "A-003-01"
    assert canonical_state_key(instance, state) != canonical_state_key(instance, moved)


@pytest.mark.parametrize("difference", ["mass", "pod", "dg", "special", "role"])
def test_category_label_never_merges_semantically_different_boxes(difference):
    from stowage.solver.exact_engine import canonical_state_key

    instance = equivalent_pair()
    second = instance.containers[1]
    if difference == "mass":
        second.gross_mass_kg += 1
    elif difference == "pod":
        second.pod = "THSGZ" if second.pod == "THBKK" else "THBKK"
    elif difference == "dg":
        second.dg = build_instance().containers[1].dg
    elif difference == "special":
        from stowage.model.cargo import SpecialStowage
        second.special_stowage = [SpecialStowage.UNDER_DECK]
    else:
        instance.load_list = instance.load_list[:1]
    state, swapped = swapped_world(instance)
    assert canonical_state_key(instance, state) != canonical_state_key(instance, swapped)


def test_semantic_dominance_reduces_states_without_losing_optimum(monkeypatch):
    import stowage.solver.exact_engine as engine_module
    from stowage.solver.exact import solve_exact

    install_test_engine(monkeypatch)
    instance = equivalent_pair()
    symmetric = solve_exact(instance, check_physics=True)

    def identity_key(inst, state, **kwargs):
        from stowage.solver.exact import _key
        return _key(state)

    monkeypatch.setattr(engine_module, "canonical_state_key", identity_key)
    separate = solve_exact(instance, check_physics=True)
    assert symmetric.status == separate.status == "OPTIMAL"
    assert symmetric.objective == separate.objective
    assert symmetric.metrics["states_seen"] <= separate.metrics["states_seen"]
    assert symmetric.metrics["dominance_scope"] == "physical_state_and_semantic_action_history"


def test_engine_restriction_is_respected_and_optimality_scope_is_explicit(monkeypatch):
    from stowage.solver.exact import solve_exact

    data = build_instance().model_dump()
    box = data["containers"][1]
    box.update(dg=None, oog=None, special_stowage=[])
    data.update(containers=[box], load_list=[box["id"]], discharge_list=[])
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[box["id"]])]
    instance = Instance.model_validate(data)

    class HoldOnlyEngine(PhysicalTestEngine):
        def legal_actions(self):
            return [a for a in super().legal_actions()
                    if not isinstance(a, Place) or int(a.slot[-2:]) < 80]

    install_test_engine(monkeypatch, factory=lambda inst, space, config: HoldOnlyEngine(inst))
    result = solve_exact(instance, check_physics=True, time_limit_s=5.)
    unrestricted = brute_force_optimum(instance, time_limit_s=5.)
    assert unrestricted.status == "optimal" and unrestricted.objective == 0
    assert result.status == "OPTIMAL" and result.objective == 4
    assert result.metrics["optimality_scope"] == "engine_model_weighted_lifts"
    # A restricted OPTIMAL certificate must not be mistaken for a certificate
    # over checker-feasible physics, even when its witness is accepted.
    assert result.metrics["physical_domain_optimality_certified"] is False
    assert result.metrics["objective_components_certified"] == ["weighted_nonproductive_lifts"]
    assert "public_engine_actions" in result.metrics["domain_restrictions"]
    assert "stop_at_manifest_completion" in result.metrics["domain_restrictions"]
    assert result.metrics["screening_guarantee"] == "not_reported"
    assert all(int(a["slot"][-2:]) < 80 for a in result.plan.actions if a["op"] == "P")


def test_initial_onboard_slots_remain_distinct_for_inherited_rule_tolerance():
    from stowage.model import Placement
    from stowage.solver.exact_engine import canonical_state_key

    instance = equivalent_pair()
    a, b = instance.load_list
    instance.stage = "B"
    instance.load_list = []
    instance.yard.stacks = []
    instance.onboard.placements = [Placement(container_id=a, slot="0010182"),
                                    Placement(container_id=b, slot="0030182")]
    state = initial_state(instance)
    swapped = state.model_copy(deep=True)
    swapped.ship.placements = [Placement(container_id=b, slot="0010182"),
                               Placement(container_id=a, slot="0030182")]
    # Static-rule grandfathering uses the exact arrival (identity,slot) pair.
    # Identical cargo in different arrival slots is therefore not exchangeable.
    assert canonical_state_key(instance, state) != canonical_state_key(instance, swapped)


@pytest.mark.parametrize("seed", range(50))
def test_real_public_engine_tiny_domain_matches_oracle_without_physics_claim(monkeypatch, seed):
    import stowage.solver.exact as exact_module
    from stowage.feasibility import make_feasibility_engine
    from stowage.model import BallastCondition

    instance = tiny_fixture(seed)
    instance.rule_overrides = {}
    instance.vessel.hull = None

    def terminal(inst, plan, ship, **kwargs):
        # Unit-only isolation: the real engine still enforces local placement,
        # capacity and access rules. No hull means no global physics screen;
        # accepting a synthetic ballast condition does not certify physics.
        return "yes", BallastCondition(tank_masses_kg={}, source="planner"), plan.provenance

    monkeypatch.setattr(exact_module, "_validate_terminal", terminal)
    result = exact_module.solve_exact(instance, check_physics=True)
    oracle = brute_force_optimum(instance, check_static_rules=True)
    assert result.status == "OPTIMAL" and oracle.status == "optimal"
    assert result.objective == oracle.objective
    assert result.metrics["algorithm"] == "astar_branch_and_bound"
    assert result.metrics["feasibility_config"]["extra"] == {}
    from stowage.actions import get_action_space
    engine = make_feasibility_engine(instance, get_action_space())
    for action in get_action_space().from_plan(instance, result.plan):
        assert action in engine.legal_actions()
        engine.push(action)
    assert engine.is_complete()


def test_unavailable_global_model_is_not_an_infeasibility_proof(monkeypatch):
    from stowage.solver.exact import ExactPlanner, solve_exact

    class UnavailableModelEngine(PhysicalTestEngine):
        def legal_actions(self):
            return []

        def dead_end(self):
            from stowage.feasibility import DeadEnd
            return DeadEnd(depth=0, reason="synthetic missing calibration coefficients",
                           rule_ids=("checker.missing_vessel_data",))

    install_test_engine(monkeypatch, factory=lambda inst, space, config: UnavailableModelEngine(inst))
    instance = tiny_fixture(3)
    result = solve_exact(instance, check_physics=True)
    assert result.status == "TIME_LIMIT"
    assert result.plan is None and result.objective is None
    assert result.metrics["model_unavailable"]
    assert "missing calibration coefficients" in result.metrics["model_unavailable_reason"]
    planned = ExactPlanner().plan(instance)
    assert planned.status == "error" and planned.plan is None
    assert planned.metrics["optimality_status"] == "TIME_LIMIT"


def test_d25_same_weight_class_different_vgm_is_not_physics_symmetry():
    from stowage.rules.catalogue import load_catalogue
    from stowage.solver.exact_engine import canonical_state_key

    instance = equivalent_pair()
    boundaries = load_catalogue().weight_classes
    instance.containers[0].gross_mass_kg = 12000.
    instance.containers[1].gross_mass_kg = 12001.
    assert boundaries.classify(12000.) == boundaries.classify(12001.)
    # D25 may permit either mass order within one class. Physics still depends
    # on exact VGM and position, so this cannot become an identity symmetry.
    state, swapped = swapped_world(instance)
    assert canonical_state_key(instance, state) != canonical_state_key(instance, swapped)


@pytest.mark.parametrize("n_boxes,distinct_pods,expected", [(1, False, 2), (2, False, 2), (2, True, 4)])
def test_locked_hold_hatch_bound_counts_distinct_cover_and_call_pairs(n_boxes, distinct_pods, expected):
    from stowage.moves import project_future_moves
    from stowage.solver.exact_engine import locked_hold_future_hatches

    instance = layover_slice()
    instance.discharge_list = []
    instance.onboard.placements = instance.onboard.placements[:n_boxes]
    for index, box in enumerate(instance.containers):
        box.pod = "THSGZ" if distinct_pods and index else "THBKK"
    state = initial_state(instance)
    bound = locked_hold_future_hatches(instance, state)
    assert bound == expected
    # With no outstanding load/discharge, v2 terminates immediately: its
    # independent move-engine projection is this continuation's exact cost.
    future = project_future_moves(instance, state.ship, from_call=instance.call_index)
    assert bound <= sum(port.hatch_cover_lifts for port in future)


@pytest.mark.parametrize("discharge_to_quay", [False, True])
def test_locked_hold_hatch_bound_waits_until_current_discharge_reaches_yard(discharge_to_quay):
    from stowage.solver.exact_engine import locked_hold_future_hatches

    instance = layover_slice()
    state = initial_state(instance)
    if discharge_to_quay:
        ex = Expander(instance)
        for move in ex.step(RestowOff(instance.discharge_list[0])):
            state = apply_move(instance, state, move)
    assert locked_hold_future_hatches(instance, state) == 0
