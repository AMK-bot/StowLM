"""Review regressions for parent telemetry and unresolved A* terminals.

The tiny engine/checker doubles isolate certificate handling; they make no
claim that the fixture vessel has passed a physical departure check.
"""
from __future__ import annotations

from copy import copy

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.model import BallastCondition, BallastOutcome, CheckReport, Instance, PartReport
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, count_moves, initial_state
from stowage.solver.exact import ExactSearchResult
from stowage.solver.exact_deadline import DeadlineOutcome


@pytest.mark.parametrize('outcome_kind', ['timeout_without_checkpoint', 'timeout_with_checkpoint', 'completed'])
def test_parent_reports_budget_overrun_even_when_worker_is_killed(monkeypatch, outcome_kind):
    import stowage.solver.exact as exact

    clock = [10.]
    monkeypatch.setattr(exact, 'perf_counter', lambda: clock[0])
    checkpoint = ExactSearchResult('TIME_LIMIT', None, None, 0., None, 2, .5, {'phase': 'search_expansion'})

    def watchdog(function, args, kwargs, *, deadline):
        assert deadline == 11.
        clock[0] = 10.75 if outcome_kind == 'completed' else 11.25
        return DeadlineOutcome(value=checkpoint if outcome_kind == 'completed' else None,
            checkpoint=checkpoint if outcome_kind == 'timeout_with_checkpoint' else None,
            timed_out=outcome_kind != 'completed', worker_pid=12345)

    monkeypatch.setattr(exact, 'run_with_deadline', watchdog)
    result = exact.solve_exact(build_instance(), time_limit_s=1.)
    expected = 0. if outcome_kind == 'completed' else .25
    assert result.metrics['budget_overrun_s'] == expected
    assert result.wall_time_s == (clock[0] - 10.)
    assert result.metrics['watchdog_timeout'] == (outcome_kind != 'completed')


def one_box_instance():
    data = build_instance().model_dump()
    box = data['containers'][1]
    box.update(dg=None, oog=None, special_stowage=[], gross_mass_kg=8000., pod='THBKK')
    data.update(containers=[box], load_list=[box['id']], discharge_list=[])
    data['yard']['stacks'] = [dict(key='A-001-01', length_ft=20, boxes=[box['id']])]
    return Instance.model_validate(data)


class TwoTerminalEngine:
    """One deck terminal costs zero; one hold terminal costs four cover lifts."""

    def __init__(self, instance):
        self.instance = instance
        self.ex = Expander(instance)
        self.state = initial_state(instance)

    def copy(self):
        child = copy(self)
        child.ex = self.ex.copy()
        child.state = self.state.model_copy(deep=True)
        return child

    def is_complete(self):
        return bool(self.state.ship.placements)

    def legal_actions(self):
        return [] if self.is_complete() else [Place(self.instance.load_list[0], slot)
            for slot in ('0010182', '0010102')]

    def push(self, action):
        for move in self.ex.step(action):
            self.state = apply_move(self.instance, self.state, move)

    def dead_end(self):
        return None


@pytest.mark.parametrize('terminal_mode', ['unknown_verdict', 'finisher_not_found'])
def test_unknown_cheaper_engine_terminal_blocks_optimal_certificate(monkeypatch, terminal_mode):
    import stowage.checker as checker
    import stowage.checker.rules as rules
    import stowage.solver.ballast as ballast
    import stowage.solver.exact as exact
    import stowage.solver.exact_engine as engine

    monkeypatch.setattr(engine, 'make_feasibility_engine', lambda inst, space: TwoTerminalEngine(inst))
    observations = []
    if terminal_mode == 'unknown_verdict':
        def terminal(inst, plan, ship, **kwargs):
            objective = count_moves(inst, plan).weighted_nonproductive
            observations.append(objective)
            return ('unknown' if objective < 4. else 'yes',
                BallastCondition(source='planner', tank_masses_kg={}), plan.provenance)
        monkeypatch.setattr(exact, '_validate_terminal', terminal)
    else:
        # Exercise the real terminal validator and its public finisher boundary:
        # not_found is an unresolved search, never an infeasibility proof.
        monkeypatch.setattr(rules, 'check_static_rules', lambda *a, **kw: PartReport(name='rules'))
        monkeypatch.setattr(checker, 'check', lambda inst, plan:
            CheckReport(passed=count_moves(inst, plan).weighted_nonproductive >= 4.))
        def not_found(inst, plan, **kwargs):
            observations.append(count_moves(inst, plan).weighted_nonproductive)
            return BallastOutcome(status='not_found')
        monkeypatch.setattr(ballast, 'finish_ballast', not_found)

    result = engine.solve_engine(one_box_instance(), time_limit_s=2., max_nodes=32,
        seed=0, num_workers=1, default_relocations_only=False)
    assert 0. in observations
    assert result.metrics['algorithm'] == 'astar_branch_and_bound'
    assert result.status == 'FEASIBLE'
    assert result.objective == 4.
    assert result.lower_bound < result.objective
    assert result.gap > 0.
    assert result.plan is not None
    if terminal_mode == 'finisher_not_found':
        assert result.metrics['ballast_status_counts'] == {'not_found': 1}
        assert result.metrics['ballast_unknown'] == 1
