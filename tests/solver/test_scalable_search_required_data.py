"""Required metadata errors use the shared static checker, never assumed physics."""
from types import SimpleNamespace

import pytest

from stowage.actions import get_action_space
from stowage.actions.samples import tiny_instance
from stowage.model import OutOfGauge
from stowage.model.cargo import SpecialStowage
from stowage.solver.scalable import ScalableSolver
from stowage.solver import scalable_oracle


@pytest.fixture
def legacy_rules():
    """These assertions specify the frozen legacy class/E6/clearance contract."""
    from stowage.rules.catalogue import rule_freeze_v1
    with rule_freeze_v1(enabled=False):
        yield


def missing_clearance_instance():
    instance = tiny_instance(329, n_load=1, n_other=0, p40=0)
    instance.containers = [box.model_copy(update={
        "special_stowage": ["away_from_heat"],
        "oog": OutOfGauge(over_width_port_m=0.2)}) for box in instance.containers]
    # D32 now supplies catalogue defaults. Explicit invalid overrides exercise
    # unavailable required data independently of changing production defaults.
    instance.rule_overrides = {
        "expert.e8.away_from_heat": {"minimum_clearance": None},
        "expert.e9.over_width_length": {"oog_clearance": None}}
    return instance


def test_global_required_data_checks_once_and_matches_exhaustive_outcome(legacy_rules, monkeypatch):
    from stowage.solver.scalable import _slots
    from stowage.checker import rules
    from stowage.model import Placement
    instance = missing_clearance_instance()
    original, calls = rules.check_static_rules, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(rules, "check_static_rules", counted)
    violations = scalable_oracle.required_data_violations(instance)
    assert len(calls) == 1
    assert len(violations) == 1
    entry = violations[0]
    assert entry["container"] == instance.load_list[0]
    assert entry["slots_checked"] == 1
    assert entry["slots_affected"] == len(_slots(instance)[20])
    assert entry["scope"] == "instance-wide rule parameter"
    observed = {v["message"] for v in entry["violations"]}
    for slot in _slots(instance)[20]:
        ship = instance.onboard.model_copy(deep=True)
        ship.placements.append(Placement(container_id=instance.load_list[0], slot=slot))
        exhaustive = original(instance, ship, call_index=instance.call_index)
        assert observed <= {v.message for v in exhaustive.violations}
    assert {v["rule_id"] for v in entry["violations"]} == {"checker.missing_vessel_data"}
    messages = " ".join(v["message"] for v in entry["violations"])
    assert "minimum_clearance must be finite and nonnegative" in messages
    assert "oog_clearance must be finite and nonnegative" in messages


def test_native_factory_is_never_called_when_required_data_is_unavailable(monkeypatch):
    calls = []

    def native(instance, space, *, config):
        calls.append(instance)
        raise AssertionError("Required-data preflight must run before engine initialization")

    monkeypatch.setattr(scalable_oracle, "make_feasibility_engine", native)
    with pytest.raises(scalable_oracle.RequiredDataUnavailable) as caught:
        scalable_oracle.build_engine(missing_clearance_instance(), get_action_space(), factory=native)
    assert not calls
    assert caught.value.rule_ids == ("checker.missing_vessel_data",)
    assert caught.value.violations[0]["container"]


def test_missing_data_is_planner_error_with_structured_diagnostics():
    result = ScalableSolver().plan(missing_clearance_instance(), time_limit_s=2)
    assert result.status == "error"
    assert result.plan is None
    assert result.metrics["candidate_probes"] == 0
    assert result.metrics["required_data_rule_ids"] == ["checker.missing_vessel_data"]
    assert len(result.metrics["required_data_violations"]) == 1
    assert "metadata unavailable" in result.message


def test_supplied_clearance_data_is_not_reported_missing():
    instance = missing_clearance_instance()
    instance.rule_overrides = {
        "expert.e8.away_from_heat": {"minimum_clearance": 0.5},
        "expert.e9.over_width_length": {"oog_clearance": 0.5}}
    assert scalable_oracle.required_data_violations(instance) == []


def test_custom_search_oracle_can_skip_preflight_without_native_claims():
    calls = []

    def custom(instance, space, *, config):
        calls.append(instance)
        return SimpleNamespace(instance=instance, space=space)

    adapter = scalable_oracle.build_engine(missing_clearance_instance(), get_action_space(), factory=custom)
    assert adapter.mode == "custom"
    assert len(calls) == 1


def test_nonallowlisted_required_data_still_checks_every_geometric_slot(monkeypatch):
    from stowage.checker import rules
    from stowage.model import Violation
    from stowage.solver.scalable import _slots
    instance = missing_clearance_instance()
    calls = []

    def slot_specific(instance, ship, *, call_index):
        calls.append(ship.placements[-1].slot)
        return SimpleNamespace(violations=[Violation(
            rule_id="checker.missing_vessel_data", severity="hard", call_index=call_index,
            containers=[instance.load_list[0]], slots=[ship.placements[-1].slot],
            message="expert.e9.over_width_length: other slot policy must be explicitly supplied")])

    monkeypatch.setattr(rules, "check_static_rules", slot_specific)
    violations = scalable_oracle.required_data_violations(instance)
    assert calls == _slots(instance)[20]
    assert len(violations) == 1
    assert violations[0]["slots_checked"] == len(calls)
    assert "scope" not in violations[0]


@pytest.mark.parametrize("scenario", ["boxes", "slots", "last-probe"])
def test_native_preflight_deadline_stops_between_slow_probes(monkeypatch, scenario):
    """A probe is indivisible; the remaining preflight is not one big probe."""
    from stowage.checker import rules
    from stowage.model import Violation
    from stowage.solver import scalable

    instance = tiny_instance(329, n_load=3 if scenario == "boxes" else 1,
                             n_other=0, p40=0)
    instance.containers = [box.model_copy(update={"special_stowage": [SpecialStowage.AWAY_FROM_HEAT]})
                           for box in instance.containers]
    clock, probes, native_calls = SimpleNamespace(now=0.0), [], []
    monkeypatch.setattr(scalable, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(scalable_oracle, "monotonic", lambda: clock.now)
    monkeypatch.setattr(scalable, "lower_bound", lambda *args, **kwargs: SimpleNamespace(weighted=0))

    def slow_probe(instance, ship, *, call_index):
        probes.append(ship.placements[-1].container_id)
        clock.now += 1.1 if scenario == "last-probe" else 0.6
        if scenario == "last-probe":
            return SimpleNamespace(violations=[])
        message = ("expert.e8.away_from_heat: minimum_clearance must be explicitly supplied"
                   if scenario == "boxes" else
                   "expert.e9.over_width_length: other slot policy must be explicitly supplied")
        return SimpleNamespace(violations=[Violation(
            rule_id="checker.missing_vessel_data", severity="hard", call_index=call_index,
            containers=[ship.placements[-1].container_id], message=message)])

    def native(instance, space, *, config):
        native_calls.append(instance)
        raise AssertionError("An expired preflight must not initialize the native engine")

    monkeypatch.setattr(rules, "check_static_rules", slow_probe)
    monkeypatch.setattr(scalable_oracle, "make_feasibility_engine", native)
    monkeypatch.setattr(scalable, "make_feasibility_engine", native)
    result = ScalableSolver().plan(instance, time_limit_s=1)

    assert result.status == "timeout"
    assert len(probes) == (1 if scenario == "last-probe" else 2)
    assert not native_calls
    assert result.metrics["deadline_enforced"] is True
    assert result.metrics["required_data_probes"] == len(probes)
    observed = result.metrics["required_data_violations"]
    assert [entry["container"] for entry in observed] == (probes if scenario == "boxes" else [])
    if observed:
        assert result.metrics["required_data_rule_ids"] == ["checker.missing_vessel_data"]
        assert all(entry["slots_checked"] == 1 for entry in observed)


def test_native_empty_root_is_reused_only_within_one_solve(monkeypatch):
    """LNS clones the validated empty state; a later solve validates again."""
    from stowage.solver import scalable

    instance = tiny_instance(329, n_load=2, n_other=0, p40=0)
    instance.vessel.hull = None  # Local integration fixture, not gold evidence.
    instance.containers = [box.model_copy(update={"special_stowage": [SpecialStowage.AWAY_FROM_HEAT]})
                           for box in instance.containers]
    instance.rule_overrides = {"expert.e8.away_from_heat": {"minimum_clearance": 0.5}}
    original_factory = scalable_oracle.make_feasibility_engine
    original_preflight = scalable_oracle.required_data_violations
    setups, preflights = [], []

    def native(*args, **kwargs):
        setups.append(1)
        return original_factory(*args, **kwargs)

    def counted_preflight(*args, **kwargs):
        preflights.append(1)
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(scalable_oracle, "make_feasibility_engine", native)
    monkeypatch.setattr(scalable, "make_feasibility_engine", native)
    monkeypatch.setattr(scalable_oracle, "required_data_violations", counted_preflight)
    # Force LNS even if the synthetic local objective is already at its bound.
    monkeypatch.setattr(scalable, "lower_bound", lambda *args, **kwargs: SimpleNamespace(weighted=-1))
    config = scalable.ScalableConfig(beam_width=1, branch_factor=2, max_neighborhoods=2,
                                    exact_window_time_limit_s=0, exact_whole_plan_time_limit_s=0)
    result = ScalableSolver(config).plan(instance, time_limit_s=5)
    assert result.status == "ok", result.message
    assert result.metrics["neighborhoods"] == 2
    assert result.metrics["oracle_initializations"] == 1
    assert result.metrics["oracle_root_reuses"] == 2
    assert len(setups) == len(preflights) == 1

    # Even a same-ID mutable input must not inherit validation from the last call.
    instance.rule_overrides["expert.e8.away_from_heat"]["minimum_clearance"] = None
    next_result = ScalableSolver(config).plan(instance, time_limit_s=5)
    assert next_result.status == "error"
    assert next_result.metrics["required_data_rule_ids"] == ["checker.missing_vessel_data"]
    assert len(setups) == 1
    assert len(preflights) == 2
