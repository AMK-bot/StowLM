"""D75 shared in-port-aware lift resequencer: precedence soundness, objective, pipeline wiring, switch-off no-op."""
from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from stowage.model import BallastCondition, MovePlan, PlannerResult
from stowage.model.fixtures import build_instance
from stowage.model.moves import (
    Discharge, HatchCoverOff, HatchCoverOn, Load, Relocate, RestowOff, RestowOn, RestowShift,
)
from stowage.moves import apply_plan
from stowage.planner_resequence import _Lift, _analyse, _canonical, _dag, resequence
from stowage.rules.catalogue import rule_freeze_v1

ROOT = Path(__file__).resolve().parents[2]
HEEL = "stowage.planner_resequence._heel_interval"


def mixed_plan():
    """The legal layover scenario of tests/moves/test_mixed_replay.py in its authored order."""
    instance = build_instance(layover=True)
    forty, a, b, c, discharge, through = [box.id for box in instance.containers]
    moves = [
        HatchCoverOff(call_index=0, cover_id="HC-02-1"),
        RestowOff(call_index=0, container_id=through, src="0010104"),
        Discharge(call_index=0, container_id=discharge, src="0010102", dst="B-002-01-01"),
        RestowOn(call_index=0, container_id=through, dst="0010102"),
        Load(call_index=0, container_id=b, src="A-003-01-01", dst="0030102"),
        HatchCoverOn(call_index=0, cover_id="HC-02-1"),
        Load(call_index=0, container_id=forty, src="A-001-01-01", dst="0020182"),
        Load(call_index=0, container_id=a, src="B-001-01-01", dst="0010282"),
        RestowShift(call_index=0, container_id=a, src="0010282", dst="0030282"),
        RestowOff(call_index=0, container_id=a, src="0030282"),
        RestowOn(call_index=0, container_id=a, dst="0010282"),
        Relocate(call_index=0, container_id=c, src="A-004-01-01", dst="A-005-01-01"),
        Load(call_index=0, container_id=c, src="A-005-01-01", dst="0030282"),
    ]
    return instance, MovePlan(id="mixed.reseq", instance_id=instance.id, call_index=0, moves=moves)


def random_linear_extension(successors, indegree, rng):
    indegree, ready, order = list(indegree), [i for i, d in enumerate(indegree) if d == 0], []
    while ready:
        choice = ready.pop(rng.randrange(len(ready)))
        order.append(choice)
        for nxt in successors[choice]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    return order


def assert_extensions_replay_identically(instance, plan, trials, seed=0):
    original = apply_plan(instance, plan)
    assert original.legal, original.violations
    lifts, initial, heights, final, crane = _analyse(instance, plan, float("inf"))
    successors, indegree, _ = _dag(lifts, initial, heights, crane)
    rng, distinct = random.Random(seed), set()
    for _ in range(trials):
        order = random_linear_extension(successors, indegree, rng)
        assert sorted(order) == list(range(len(plan.moves)))
        distinct.add(tuple(order))
        result = apply_plan(instance, plan.model_copy(update={"moves": [plan.moves[i] for i in order]}))
        assert result.legal, (order, result.violations)
        assert result.count == original.count
        assert _canonical(result.final_state) == _canonical(final)
    return distinct


@pytest.mark.parametrize("enabled", [False, True])
def test_every_dag_order_of_a_mixed_plan_replays_to_the_same_world(enabled):
    with rule_freeze_v1(enabled=enabled):
        instance, plan = mixed_plan()
        distinct = assert_extensions_replay_identically(instance, plan, trials=60)
    # The DAG is a real partial order: independent chains do interleave.
    assert len(distinct) > 1


def test_crossed_stack_may_move_past_writes_below_crane_height_only():
    # Lift 0 crosses stack S; lifts 1-3 load S to heights 1, 2 and 3 with H = 3.
    lifts = [_Lift(0, crossed_stacks={"S"}), _Lift(1, writes={("stack", "S")}),
             _Lift(2, writes={("stack", "S")}), _Lift(3, writes={("stack", "S")})]
    heights = {"S": [(1, 1), (2, 2), (3, 3)]}
    successors, indegree, _ = _dag(lifts, {"S": 0}, heights, crane_height=3)
    assert successors[0] == [3]  # must precede the write that brings S to H, nothing earlier
    assert indegree[0] == 0
    # A crossing lift after S came down below H again must follow that write, not the earlier ones.
    lifts = [_Lift(0, writes={("stack", "S")}), _Lift(1, writes={("stack", "S")}),
             _Lift(2, crossed_stacks={"S"})]
    successors, indegree, _ = _dag(lifts, {"S": 2}, {"S": [(0, 3), (1, 2)]}, crane_height=3)
    assert successors[1] == [2] and 2 not in successors[0]


def test_strict_reads_sit_between_the_same_writes():
    lifts = [_Lift(0, writes={("cover", "C")}), _Lift(1, strict_reads={("cover", "C")}),
             _Lift(2, strict_reads={("cover", "C")}), _Lift(3, writes={("cover", "C")})]
    successors, indegree, _ = _dag(lifts, {}, {}, crane_height=None)
    assert successors[0] == [1, 2, 3] and successors[1] == [3] and successors[2] == [3]
    assert 2 not in successors[1]  # two reads commute


def test_switch_off_is_a_noop():
    with rule_freeze_v1(enabled=False):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan)
    assert outcome.plan is plan and not outcome.applied
    assert outcome.report["reason"] == "rule_freeze_v1 off"


def test_no_heel_pair_leaves_the_plan_unchanged():
    with rule_freeze_v1(enabled=True):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan)
    assert outcome.plan is plan and not outcome.applied
    assert outcome.report["reason"].startswith("no HEEL interval")


class _FixedPlanner:
    name, version, sets_ballast, deterministic = "test.fixed", "1", False, True

    def __init__(self, plan):
        self._plan = plan

    def plan(self, instance, *, seed=0, time_limit_s=None):
        return PlannerResult(status="ok", planner=self.name, planner_version=self.version, seed=seed,
                             plan=self._plan, provenance=instance.provenance)


def _ok(instance, plan):
    return PlannerResult(status="ok", planner="test.fixed", planner_version="1", seed=0, plan=plan,
                         provenance=instance.provenance)


def _record_pipeline(monkeypatch, reordered, calls):
    import stowage.planner_resequence as reseq_module
    import stowage.solver.ballast as ballast_module

    def fake_resequence(inst, candidate, **kwargs):
        calls.append(("resequence", candidate.moves))
        return reseq_module.ResequenceOutcome(plan=reordered, applied=True,
                                              report={"applied": True, "lifts_moved": 12})

    def fake_finish(inst, candidate, **kwargs):
        calls.append(("finish_ballast", candidate.moves))
        return SimpleNamespace(status="kept", plan=candidate.model_copy(update={"ballast": BallastCondition(
            tank_masses_kg={}, source="finisher")}), provenance=candidate.provenance, proof=None)

    monkeypatch.setattr(reseq_module, "resequence", fake_resequence)
    monkeypatch.setattr(ballast_module, "finish_ballast", fake_finish)


@pytest.mark.parametrize("enabled", [False, True])
def test_finalize_resequences_before_the_ballast_finisher(monkeypatch, enabled):
    """Leader decision 1: every finalize caller (gold, best-of-k, rewards, repair, rechecks) gets the step."""
    from stowage.planner import finalize

    instance, plan = mixed_plan()
    reordered = plan.model_copy(update={"moves": list(reversed(plan.moves))})  # stand-in output
    calls = []
    _record_pipeline(monkeypatch, reordered, calls)
    with rule_freeze_v1(enabled=enabled):
        result = finalize(instance, _ok(instance, plan))
    if enabled:
        assert [name for name, _ in calls] == ["resequence", "finish_ballast"]
        assert calls[0][1] == plan.moves and calls[1][1] == reordered.moves
        assert result.metrics["resequence"] == {"applied": True, "lifts_moved": 12}
    else:
        assert [name for name, _ in calls] == ["finish_ballast"] and calls[0][1] == plan.moves
        assert "resequence" not in result.metrics


def test_finalize_resequences_a_plan_with_planner_ballast_and_skips_failed_results(monkeypatch):
    from stowage.planner import finalize

    instance, plan = mixed_plan()
    own = plan.model_copy(update={"ballast": BallastCondition(tank_masses_kg={}, source="planner")})
    reordered = own.model_copy(update={"moves": list(reversed(own.moves))})
    calls = []
    _record_pipeline(monkeypatch, reordered, calls)
    with rule_freeze_v1(enabled=True):
        result = finalize(instance, _ok(instance, own))
        assert [name for name, _ in calls] == ["resequence"] and result.plan.moves == reordered.moves
        failed = _ok(instance, plan).model_copy(update={"status": "dead_end"})
        assert finalize(instance, failed) is failed and len(calls) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_run_planner_gets_the_resequencer_through_finalize(monkeypatch, enabled):
    import stowage.checker as checker_module
    import stowage.planner as planner_module
    from stowage.model import CheckReport

    instance, plan = mixed_plan()
    reordered = plan.model_copy(update={"moves": list(reversed(plan.moves))})
    calls = []
    _record_pipeline(monkeypatch, reordered, calls)
    checked = []
    monkeypatch.setattr(checker_module, "check", lambda inst, p: checked.append(p.moves) or CheckReport(passed=True))
    with rule_freeze_v1(enabled=enabled):
        result, _ = planner_module.run_planner(_FixedPlanner(plan), instance, allow_repair=False)
    assert [name for name, _ in calls] == (["resequence", "finish_ballast"] if enabled else ["finish_ballast"])
    assert checked == [reordered.moves if enabled else plan.moves]
    assert ("resequence" in result.metrics) is enabled


# Every production caller of the ballast finisher outside ``finalize``. The two solvers set their own ballast
# (sets_ballast=True) and their plans then go through finalize, which resequences them; the background generator
# finishes an empty arrival plan, not a planner's plan. Gold warm start rejects freeze-on before planning,
# so its explicitly scoped demo-rule finisher cannot bypass D75 (covered by the guard regression below).
_FINISHER_CALLERS = {"src/stowage/planner.py", "src/stowage/solver/exact.py", "src/stowage/solver/construct.py",
                     "src/stowage/generate/background_acceptance.py", "src/stowage/solver/gold_warmstart.py"}


def test_gold_warmstart_rejects_freeze_before_planning_or_finishing(monkeypatch):
    import stowage.solver.ballast_v4_dispatch as ballast
    import stowage.solver.twostep as twostep
    from stowage.solver.gold_warmstart import solve_gold

    def unexpected(*args, **kwargs):
        pytest.fail("freeze-on gold must stop before planning or ballast finishing")

    monkeypatch.setattr(twostep.TwoStepPlanner, "plan", unexpected)
    monkeypatch.setattr(ballast, "finish", unexpected)
    with rule_freeze_v1(enabled=True):
        result = solve_gold(build_instance())
    assert result.status == "error" and result.plan is None
    assert "freeze off only" in result.message


_FINALIZE_DRIVERS = ("experiments/gold-plans/tiny/driver.py", "experiments/campaign-g1/stage_worker.py",
                     "src/llm/stowlm/planner.py", "src/llm/rl/reward.py", "src/stowage/solver/repair.py")


def test_no_production_path_reaches_the_ballast_finisher_without_finalize():
    import ast

    offenders = []
    # Production code is src/ and app/; experiments/ holds diagnostics and frozen source copies. The production
    # drivers there that reach the finisher are listed in _FINALIZE_DRIVERS and checked below.
    for root in (ROOT / "src", ROOT / "app"):
        for path in root.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in rel or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if name in {"finish_ballast", "_finish_ballast", "_finish_ballast_active"} \
                            and rel not in _FINISHER_CALLERS and rel != "src/stowage/solver/ballast.py":
                        offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, f"call stowage.planner.finalize instead (it runs the D75 resequencer): {offenders}"
    for rel in _FINALIZE_DRIVERS:  # the bypasses logic-reseq named: each must go through finalize
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "finalize" in source and "finish_ballast(" not in source, rel
    finalize_source = (ROOT / "src/stowage/planner.py").read_text()
    assert finalize_source.index("_resequenced(instance, result") < finalize_source.index("finish_ballast(")


def test_predicted_failure_is_resequenced_with_identical_moves():
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        plan = plan.model_copy(update={"actions": [{"stand_in": True}]})
        outcome = resequence(instance, plan)
        again = resequence(instance, plan)
        assert outcome.applied and outcome.report["raw_order_predicted_list_fail"]
        assert outcome.report["lifts_moved"] > 0 and outcome.report["heel_excess_after_kgm"] == 0
        assert outcome.plan.moves == again.plan.moves
        assert sorted(m.model_dump_json() for m in outcome.plan.moves) == sorted(
            m.model_dump_json() for m in plan.moves)
        assert outcome.plan.actions is None and outcome.report["actions_dropped"]
        before, after = apply_plan(instance, plan), apply_plan(instance, outcome.plan)
        assert after.legal and after.count == before.count
        assert _canonical(after.final_state) == _canonical(before.final_state)


def test_a_plan_predicted_to_pass_is_never_reordered():
    """Leader decision 2: no reorder unless the original order is predicted to fail list."""
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -60000.0, 60000.0)):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan)
    assert outcome.plan is plan and not outcome.applied
    assert outcome.report["raw_order_predicted_list_fail"] is False
    assert outcome.report["reason"] == "original order not predicted to fail in-port list"


@pytest.mark.parametrize("guard", ["trim", "bending"])
def test_a_worse_guard_proxy_keeps_the_original_order(monkeypatch, guard):
    import stowage.planner_resequence as reseq_module

    real = reseq_module._profile
    calls = []

    def profile(order, lifts, m0, lo, hi):
        values = list(real(order, lifts, m0, lo, hi))
        calls.append(1)
        if len(calls) == 2:  # the candidate order
            values[2 if guard == "trim" else 3] = 2 * values[2 if guard == "trim" else 3] + 1.0
        return tuple(values)

    monkeypatch.setattr(reseq_module, "_profile", profile)
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan)
    assert outcome.plan is plan and outcome.report["reason"] == f"{guard} proxy would get worse"


def test_tied_excess_compares_utilisation(monkeypatch):
    """Code review: a 0-vs-tolerance lexicographic compare used to reject every utilisation-only improvement."""
    import stowage.planner_resequence as reseq_module

    real = reseq_module._profile
    calls = []

    def profile(order, lifts, m0, lo, hi):
        values = list(real(order, lifts, m0, lo, hi))
        calls.append(1)
        values[0] = 5000.0  # same peak excess before and after; only utilisation differs
        values[1] = 1.2 if len(calls) == 1 else 1.1
        return tuple(values)

    monkeypatch.setattr(reseq_module, "_profile", profile)
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        assert resequence(instance, plan).applied


@pytest.mark.parametrize("field,value", [("container_id", "ABCU9999999"), ("dst", "9990182")])
def test_an_illegal_original_returns_unchanged_and_never_raises(field, value):
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        load = next(i for i, m in enumerate(plan.moves) if m.kind == "load")
        moves = list(plan.moves)
        moves[load] = moves[load].model_copy(update={field: value})
        bad = plan.model_copy(update={"moves": moves})
        outcome = resequence(instance, bad)
    assert outcome.plan is bad and outcome.report["reason"].startswith("original plan does not replay")


def test_helper_failure_keeps_the_original_order():
    with rule_freeze_v1(enabled=True), patch(HEEL, side_effect=AttributeError("_pair_moment renamed")):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan)
    assert outcome.plan is plan and outcome.report["reason"].startswith("no HEEL interval")


def test_time_bound_covers_preparation():
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan, time_limit_s=0.0)
    assert outcome.plan is plan and outcome.report["reason"].startswith("time bound 0 s reached")


def test_time_bound_covers_the_final_engine_replay(monkeypatch):
    """Code review: a slow final apply_plan used to return applied=True past the bound."""
    import stowage.moves as moves_module
    import stowage.planner_resequence as reseq_module

    clock = [0.0]
    monkeypatch.setattr(reseq_module, "monotonic", lambda: clock[0])
    real_apply = moves_module.apply_plan

    def slow_apply(*args, **kwargs):
        clock[0] += 16.0
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(moves_module, "apply_plan", slow_apply)
    with rule_freeze_v1(enabled=True), patch(HEEL, return_value=(0.0, -40000.0, 40000.0)):
        instance, plan = mixed_plan()
        outcome = resequence(instance, plan, time_limit_s=30.0)
    assert outcome.plan is plan and not outcome.applied
    assert "apply_plan" in outcome.report["reason"]


def test_resequencer_version_enters_component_provenance():
    from stowage.frozen import current_provenance
    from stowage.versions import RESEQUENCER_VERSION

    with rule_freeze_v1(enabled=True):
        assert current_provenance().versions["resequencer"] == RESEQUENCER_VERSION
    with rule_freeze_v1(enabled=False):
        assert "resequencer" not in current_provenance().versions


# --- full-size T4 C1 plans (production vessel, port and 50 % HEEL berth arrival) ---------------------------------

def _t4(name, one_side_first):
    sys.path.insert(0, str(ROOT / "tests" / "checker"))
    from test_in_port_acceptance import _prepared_manual_plan

    return _prepared_manual_plan(name, one_side_first=one_side_first)


@pytest.mark.slow
def test_t4_one_side_plan_is_rebalanced_without_changing_moves():
    instance, plan = _t4("feeder_500", True)
    with rule_freeze_v1(enabled=True):
        first = resequence(instance, plan, time_limit_s=120.0)
        second = resequence(instance, plan, time_limit_s=120.0)
        assert first.applied
        assert [m.model_dump_json() for m in first.plan.moves] == [m.model_dump_json() for m in second.plan.moves]
        assert sorted(m.model_dump_json() for m in first.plan.moves) == sorted(
            m.model_dump_json() for m in plan.moves)
        assert first.report["heel_excess_before_kgm"] > 0 and first.report["heel_excess_after_kgm"] == 0
        assert first.report["heel_utilisation_after"] < first.report["heel_utilisation_before"]
        before, after = apply_plan(instance, plan), apply_plan(instance, first.plan)
        assert after.legal and after.count == before.count
        assert _canonical(after.final_state) == _canonical(before.final_state)
        # Soundness on a full-size plan with deck crossing at the production crane height.
        assert_extensions_replay_identically(instance, plan, trials=3)
