"""Wall-clock budgets include every solver phase, using only tiny test doubles.

Long sleeps stand in for atomic native calls that cannot honor Python deadline
checks. These tests never run vessel calibration or claim physical feasibility.
"""
from __future__ import annotations

from time import monotonic, sleep

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.model import BallastCondition, CheckReport, Instance, MoveCount
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state


# Activated physical replay checks take longer than the legacy double's path.
# Keep enough setup time to reach the deliberately stalled phase, while the
# watchdog must still finish well before its 1.5-second indivisible call.
BUDGET = .25
STALL = 1.5
MAX_ELAPSED = .75


@pytest.fixture(autouse=True)
def preload_deadline_dependencies():
    # Cold checker imports are real budgeted production work, but these unit
    # tests target named atomic phases and retained checkpoints after setup.
    # Forked workers inherit this import even when a single test runs alone.
    import stowage.checker  # noqa: F401


def fixture_instance(*, inherited=False):
    data = build_instance().model_dump()
    boxes = data["containers"][1:3] if inherited else data["containers"][1:2]
    for index, box in enumerate(boxes):
        box.update(dg=None, oog=None, special_stowage=[], gross_mass_kg=8000.,
                   pod="THBKK" if index == 0 else "THSGZ")
    data.update(containers=boxes, load_list=[boxes[-1]["id"]], discharge_list=[])
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=data["load_list"])]
    data["onboard"]["placements"] = ([dict(container_id=boxes[0]["id"], slot="0010182")]
                                          if inherited else [])
    stacks = [s for s in data["vessel"]["stacks"] if s["id"] in ("00101D", "00301D")]
    for stack in stacks:
        stack["hatch_cover_id"] = None
    cells = {cid for stack in stacks for cid in stack["cell_ids"]}
    data["vessel"].update(stacks=stacks, hatch_covers=[])
    data["vessel"]["cells"] = [c for c in data["vessel"]["cells"] if c["id"] in cells]
    return Instance.model_validate(data)


class TinyEngine:
    def __init__(self, instance, *, stall_legal=False, preserve_case=False):
        self.instance = instance
        self.ex = Expander(instance)
        self.state = initial_state(instance)
        self.stall_legal = stall_legal
        self.preserve_case = preserve_case

    def copy(self):
        from copy import copy
        result = copy(self)
        result.ex = self.ex.copy()
        return result

    def is_complete(self):
        aboard = {p.container_id for p in self.state.ship.placements}
        if self.preserve_case and any(p.container_id in self.instance.load_list and p.slot == "0030182"
                                      for p in self.state.ship.placements):
            return False  # This competing branch stalls before its next action.
        return set(self.instance.load_list) <= aboard

    def legal_actions(self):
        if self.stall_legal or (self.preserve_case and any(p.container_id in self.instance.load_list
            and p.slot == "0030182" for p in self.state.ship.placements)):
            sleep(STALL)
        cid = self.instance.load_list[0]
        return ([Place(cid, "0010184"), Place(cid, "0030182")] if self.preserve_case
                else [Place(cid, "0010182")])

    def push(self, action):
        for move in self.ex.step(action):
            self.state = apply_move(self.instance, self.state, move)

    def dead_end(self):
        return None


def install_engine(monkeypatch, *, stall_legal=False, preserve_case=False):
    import stowage.solver.exact_engine as module
    monkeypatch.setattr(module, "make_feasibility_engine", lambda inst, space:
        TinyEngine(inst, stall_legal=stall_legal, preserve_case=preserve_case))


def accept_terminal(monkeypatch):
    import stowage.solver.exact as module
    monkeypatch.setattr(module, "_validate_terminal", lambda inst, plan, ship, **kw:
        ("yes", BallastCondition(source="planner", tank_masses_kg={}), plan.provenance))


@pytest.mark.parametrize("phase", ["constructor", "bound", "legal_actions", "checker", "ballast"])
def test_solve_budget_bounds_atomic_work_in_every_phase(monkeypatch, phase):
    import stowage.checker as checker
    import stowage.solver.ballast as ballast
    import stowage.solver.exact_engine as engine_module
    from stowage.solver.exact import solve_exact

    from multiprocessing import get_context
    reached = get_context("fork").Value("i", 0)

    def stall():
        reached.value = 1
        sleep(STALL)

    instance = fixture_instance()
    install_engine(monkeypatch)
    if phase == "legal_actions":
        def legal_actions(self):
            stall()
            return []
        monkeypatch.setattr(TinyEngine, "legal_actions", legal_actions)
    if phase == "constructor":
        def constructor(*args, **kwargs):
            stall()
            return TinyEngine(instance)
        monkeypatch.setattr(engine_module, "make_feasibility_engine", constructor)
    elif phase == "bound":
        def bound(*args, **kwargs):
            stall()
            return 0
        monkeypatch.setattr(engine_module, "_weighted_bound", bound)
    elif phase == "checker":
        def check(*args, **kwargs):
            stall()
            return CheckReport(passed=True, count=MoveCount(loads=1))
        monkeypatch.setattr(checker, "check", check)
    elif phase == "ballast":
        monkeypatch.setattr(checker, "check", lambda *a, **kw: CheckReport(passed=False))
        def finish(*args, **kwargs):
            stall()
        monkeypatch.setattr(ballast, "finish_ballast", finish)
    start = monotonic()
    result = solve_exact(instance, check_physics=True, time_limit_s=BUDGET)
    elapsed = monotonic() - start
    assert elapsed < MAX_ELAPSED, (phase, elapsed)
    assert reached.value == 1, f"watchdog expired before reaching {phase}"
    assert result.status == "TIME_LIMIT"
    assert result.plan is None and result.objective is None
    assert result.wall_time_s < MAX_ELAPSED
    if phase in ("checker", "ballast"):
        assert result.metrics["phase"] == "terminal_check"
        assert result.expanded_nodes >= 2
        assert not result.metrics["checkpoint_validated_incumbent"]
    elif phase == "legal_actions":
        assert result.metrics["phase"] == "search_expansion"
        assert result.expanded_nodes == 1


def test_watchdog_preserves_only_fully_validated_incumbent(monkeypatch):
    from stowage.solver.exact import solve_exact

    instance = fixture_instance(inherited=True)
    install_engine(monkeypatch, preserve_case=True)
    accept_terminal(monkeypatch)
    start = monotonic()
    result = solve_exact(instance, check_physics=True, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert result.status == "TIME_LIMIT"
    assert result.plan is not None and result.objective == 2
    assert result.plan.ballast is not None
    assert result.metrics["checker_passed"] and result.metrics["mask_verified"]
    assert result.lower_bound <= result.objective


@pytest.mark.parametrize("with_incumbent", [False, True])
def test_planner_reports_timeout_even_with_retained_incumbent(monkeypatch, with_incumbent):
    from stowage.solver.exact import ExactPlanner

    instance = fixture_instance(inherited=with_incumbent)
    install_engine(monkeypatch, stall_legal=not with_incumbent, preserve_case=with_incumbent)
    accept_terminal(monkeypatch)
    start = monotonic()
    result = ExactPlanner().plan(instance, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert result.status == "timeout"
    assert (result.plan is not None) is with_incumbent
    if with_incumbent:
        assert result.metrics["objective"] == 2


def test_relaxed_legacy_bound_is_inside_the_same_wall_budget(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact as module

    reached = get_context("fork").Value("i", 0)
    def bound(*args, **kwargs):
        reached.value = 1
        sleep(STALL)
    monkeypatch.setattr(module, "lower_bound", bound)
    start = monotonic()
    result = module.solve_exact(fixture_instance(), check_physics=False, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert reached.value == 1
    assert result.status == "TIME_LIMIT" and result.plan is None


def test_stalled_g1_replay_never_publishes_checker_only_candidate(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact_engine as module
    from stowage.solver.exact import solve_exact

    reached = get_context("fork").Value("i", 0)
    calls = 0
    def factory(inst, space):
        nonlocal calls
        calls += 1
        if calls > 1:
            reached.value = 1
            sleep(STALL)
        return TinyEngine(inst)
    monkeypatch.setattr(module, "make_feasibility_engine", factory)
    accept_terminal(monkeypatch)
    start = monotonic()
    result = solve_exact(fixture_instance(), check_physics=True, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert reached.value == 1
    assert result.status == "TIME_LIMIT" and result.plan is None
    assert not result.metrics.get("mask_verified", False)


def test_watchdog_also_bounds_calls_from_background_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import stowage.solver.exact_engine as module
    from stowage.solver.exact import solve_exact

    def factory(inst, space):
        sleep(STALL)
        return TinyEngine(inst)
    monkeypatch.setattr(module, "make_feasibility_engine", factory)
    instance = fixture_instance()
    start = monotonic()
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(solve_exact, instance, check_physics=True,
                                 time_limit_s=BUDGET).result(timeout=MAX_ELAPSED)
    assert monotonic() - start < MAX_ELAPSED
    assert result.status == "TIME_LIMIT" and result.plan is None


def test_worker_exceptions_propagate_and_planner_reports_error(monkeypatch):
    import stowage.solver.exact_engine as module
    from stowage.solver.exact import ExactPlanner, solve_exact

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic engine construction failure")
    monkeypatch.setattr(module, "make_feasibility_engine", broken)
    instance = fixture_instance()
    with pytest.raises(RuntimeError, match="synthetic engine construction failure"):
        solve_exact(instance, time_limit_s=.5)
    result = ExactPlanner().plan(instance, time_limit_s=.5)
    assert result.status == "error"
    assert "synthetic engine construction failure" in result.message


@pytest.mark.parametrize("phase", ["bound", "brute_force", "checker"])
def test_checker_oracle_budget_covers_every_atomic_phase(monkeypatch, phase):
    from multiprocessing import get_context
    from types import SimpleNamespace
    import stowage.solver.exact_oracle as module
    from stowage.model import MovePlan

    reached = get_context("fork").Value("i", 0)
    instance = fixture_instance()
    plan = MovePlan(id="deadline-oracle", instance_id=instance.id, call_index=instance.call_index)
    def stall(*args, **kwargs):
        reached.value = 1
        sleep(STALL)
    oracle = SimpleNamespace(status="optimal", objective=0., plan=plan, expanded_nodes=1,
                             wall_time_s=0.)
    monkeypatch.setattr(module, "brute_force_optimum", stall if phase == "brute_force"
                        else lambda *a, **kw: oracle)
    if phase == "bound":
        monkeypatch.setattr(module, "lower_bound", stall)
    if phase == "checker":
        monkeypatch.setattr(module, "check", stall)
    start = monotonic()
    result = module.checker_brute_force_certificate(instance, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert reached.value == 1
    assert result.status == "LIMIT"
    assert result.plan is None and not result.checker_passed
    assert not result.brute_force_agreement


def test_checker_oracle_retains_prior_checked_witness_when_next_check_stalls(monkeypatch):
    from multiprocessing import get_context
    from types import SimpleNamespace
    import stowage.solver.exact_oracle as module
    from stowage.model import MovePlan

    instance = fixture_instance()
    first = MovePlan(id="validated-first", instance_id=instance.id, call_index=instance.call_index)
    second = first.model_copy(update={"id": "stalled-second"})
    reached = get_context("fork").Value("i", 0)
    monkeypatch.setattr(module, "brute_force_optimum", lambda *a, **kw: SimpleNamespace(
        status="optimal", objective=0., plan=second, expanded_nodes=1, wall_time_s=0.))
    def check(inst, plan):
        if plan.id == second.id:
            reached.value = 1
            sleep(STALL)
        return CheckReport(passed=True, count=MoveCount(relocations=1))
    monkeypatch.setattr(module, "check", check)
    start = monotonic()
    result = module.checker_brute_force_certificate(instance, incumbent=first, time_limit_s=BUDGET)
    assert monotonic() - start < MAX_ELAPSED
    assert reached.value == 1
    assert result.status == "FEASIBLE"
    assert result.checker_passed and result.report.passed
    assert result.plan.id == first.id and result.objective == 1
    assert result.lower_bound == 0
    assert not result.brute_force_agreement


def test_early_constructor_timeout_keeps_requested_diagnostic_config(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact_engine as module
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.exact import solve_exact

    expected = FeasibilityConfig(extra={"global_screen": 0})
    reached = get_context("fork").Value("i", 0)
    def factory(inst, space, *, config):
        assert config == expected
        reached.value = 1
        sleep(STALL)
    monkeypatch.setattr(module, "make_feasibility_engine", factory)
    start = monotonic()
    result = solve_exact(fixture_instance(), time_limit_s=BUDGET, feasibility_config=expected)
    assert monotonic() - start < MAX_ELAPSED
    assert reached.value == 1 and result.status == "TIME_LIMIT"
    assert result.plan is None and not result.metrics["mask_verified"]
    assert result.metrics["requested_feasibility_config"]["extra"] == {"global_screen": 0}
    assert result.metrics["shippable"] is False


def test_stalled_index_mask_reports_search_phase_without_unchecked_plan(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact_engine as module
    from stowage.solver.exact import solve_exact

    reached = get_context("fork").Value("i", 0)
    class IndexStallEngine(TinyEngine):
        def legal_actions(self):
            raise AssertionError("index-capable path must remain allocation-free")
        def legal_index(self):
            reached.value = 1
            sleep(STALL)
            return ()
        def legal_discharges(self):
            return ()
        def decode_index(self, flat):
            raise AssertionError("stalled mask has not produced an action")

    monkeypatch.setattr(module, "make_feasibility_engine", lambda inst, space: IndexStallEngine(inst))
    result = solve_exact(fixture_instance(), time_limit_s=BUDGET)
    assert reached.value == 1 and result.status == "TIME_LIMIT"
    assert result.metrics["phase"] == "search_expansion"
    assert result.expanded_nodes == 1
    assert result.plan is None and result.objective is None
    assert not result.metrics["checkpoint_validated_incumbent"]


def test_terminal_progress_keeps_prior_checked_incumbent_when_better_check_stalls(monkeypatch):
    from multiprocessing import get_context
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as module
    from stowage.moves import count_moves

    reached = get_context("fork").Value("i", 0)
    class TwoTerminalEngine(TinyEngine):
        def is_complete(self):
            return set(self.instance.load_list) <= {p.container_id for p in self.state.ship.placements}
    monkeypatch.setattr(module, "make_feasibility_engine", lambda inst, space:
                        TwoTerminalEngine(inst, preserve_case=True))
    def terminal(inst, plan, ship, **kwargs):
        if count_moves(inst, plan).weighted_nonproductive < 2:
            reached.value = 1
            sleep(STALL)
        return "yes", BallastCondition(source="planner", tank_masses_kg={}), plan.provenance
    monkeypatch.setattr(exact, "_validate_terminal", terminal)
    result = exact.solve_exact(fixture_instance(inherited=True), time_limit_s=BUDGET)
    assert reached.value == 1 and result.status == "TIME_LIMIT"
    assert result.metrics["phase"] == "terminal_check"
    assert result.metrics["candidate_objective"] == 0
    assert result.expanded_nodes >= 3
    assert result.metrics["checkpoint_validated_incumbent"]
    assert result.plan is not None and result.objective == 2
    assert result.metrics["checker_passed"] and result.metrics["mask_verified"]
