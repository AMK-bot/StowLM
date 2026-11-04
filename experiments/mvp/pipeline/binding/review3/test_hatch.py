"""Staged hatch preference/portfolio tests; no native engine or physics solve."""
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter
from experiments.mvp.pipeline.binding.review3 import hatch
from experiments.mvp.pipeline.binding.test_allocation import Record
from experiments.mvp.pipeline.binding.test_hooks import compiled_rule, fixture_instance
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import CloseBay
from experiments.mvp.pipeline.binding.test_process_wait import subprocess_guard


def panel_ship():
    cells, stacks, covers = [], [], []
    for bays in ((1, 3), (5, 7)):
        for row, side in ((1, 'P'), (2, 'S')):
            cover = f'HC-{bays[0]+1:03d}-{side}'
            members = []
            for bay in bays:
                sid = f's-{bay}-{row}'
                members.append(sid)
                cells.append(NS(id=f'{bay:03d}{row:02d}02', stack_id=sid,
                                accepts_20=True, accepts_40=True, accepts_45=True))
                stacks.append(NS(id=sid, hatch_cover_id=cover, on_deck=False))
            covers.append(NS(id=cover, bays=list(bays), hold_stack_ids=members, deck_stack_ids=[]))
    boxes = [NS(id=f'box-{i}', length_ft=20, pod=pod)
             for i, pod in enumerate(('SGSIN', 'SGSIN', 'MYTPP', 'MYTPP'))]
    return Record(id='whole-panel-test', vessel=NS(cells=cells, stacks=stacks, hatch_covers=covers),
                  containers=boxes, load_list=[box.id for box in boxes], discharge_list=[],
                  onboard=Record(placements=[]),
                  rotation=NS(calls=[NS(locode=pod, index=i) for i, pod in enumerate(('SGSIN', 'MYTPP'))]))


@subprocess_guard(timeout_s=60)
def test_real_tiny_cp_prefers_whole_hatches_then_demand_ratio_and_excludes_prior_mappings(monkeypatch):
    # Admission is obtained before real numerical work. There is no full checker
    # or engine call in this regression, only an eight-cell CP-SAT model.
    hatch.wait_for_capacity()
    monkeypatch.setattr(hatch, 'wait_for_capacity', lambda: {'already_admitted': True})
    original_model, original_solver = hatch.allocation.cp_model.CpModel, hatch.allocation.cp_model.CpSolver
    rows, evidence = hatch.enumerate_hatch_mappings(panel_ship())
    assert len(rows) == 3 and evidence['mappings_generated'] == 3
    assert len({tuple(sorted(compiled.cover_ports.items())) for compiled, _ in rows}) == 3
    assert [metadata['preference']['split_panel_pairs'] for _, metadata in rows] == [0, 0, 1]
    assert [metadata['preference']['capacity_ratio_deviation'] for _, metadata in rows[:2]] == [0, 0]
    assert all(isinstance(compiled, hatch.allocation.HatchInstructionConstraint) for compiled, _ in rows)
    assert all(not metadata['checker_certificate'] for _, metadata in rows)
    assert hatch.allocation.cp_model.CpModel is original_model
    assert hatch.allocation.cp_model.CpSolver is original_solver


@pytest.mark.parametrize('status,kind', [
    (hatch.cp_model.UNKNOWN, 'solver_timeout'), (hatch.cp_model.INFEASIBLE, 'proved_full_load')])
def test_allocator_timeout_and_proof_are_distinct_and_versioned(monkeypatch, status, kind):
    class Solver:
        def __init__(self):
            self.parameters = NS()
        def Solve(self, model):
            return status
        def StatusName(self, value):
            return 'UNKNOWN' if value == hatch.cp_model.UNKNOWN else 'INFEASIBLE'
    monkeypatch.setattr(hatch.cp_model, 'CpSolver', Solver)
    monkeypatch.setattr(hatch, 'wait_for_capacity', lambda: {})
    rows, evidence = hatch.enumerate_hatch_mappings(panel_ship())
    assert rows == [] and evidence['refusal_kind'] == kind
    assert evidence['portfolio_policy_version'] == 'logic3-hatch-portfolio-v1'
    if kind == 'solver_timeout':
        assert 'does not prove' in evidence['reason']
    else:
        assert evidence['reason'] == 'the physical hatch-panel capacity model is infeasible'
        assert evidence['partial_plan_tried'] is False


@pytest.fixture
def portfolio(monkeypatch):
    instance = fixture_instance()
    answers = [CloseBay(type='close_bay', bay=1), OnePortPerHatch(type='one_port_per_hatch')]
    compiled = compiled_rule(instance, 'one_port_per_hatch')
    rows = [(compiled, {'cover_ports': dict(compiled.cover_ports), 'mapping_index': i}) for i in (1, 2, 3)]
    identity = {'passed': True, 'test_identity': True}
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: dict(identity))
    monkeypatch.setattr(adapter, 'same_identity', lambda left, right: left == right)
    monkeypatch.setattr(adapter, 'require_clean', lambda *args, **kwargs: None)
    monkeypatch.setattr(adapter, 'active_stubs', lambda: {})
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *args, **kwargs:
                        (NS(instance=instance, forbids=lambda *_: None), {}))
    monkeypatch.setattr(hatch, 'enumerate_hatch_mappings', lambda *args, **kwargs:
                        (rows, {'mappings_generated': 3}))
    def evidence(status, *, level=0, lifts=9):
        return dict(status=status, passed=status == 'checked_plan', engine_manifest=dict(identity),
                    lifts=lifts, level=level, boxes_ashore=[], broken_rules=[], rules=[{}, {}], wall_s=1.)
    return instance, answers, evidence


def test_portfolio_stops_on_first_full_pass_and_shares_explicit_budgets(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    calls = []
    def trial(inst, rules, compiled, metadata, kwargs):
        calls.append((inst, list(rules), dict(kwargs)))
        return (None, evidence('no_checked_plan')) if len(calls) == 1 else (NS(plan='pass'), evidence('checked_plan'))
    monkeypatch.setattr(hatch, '_trial', trial)
    result, record = hatch.plan_hatch(instance, answers, budget_s=90, finalize_s=30)
    assert result.plan == 'pass' and record['mappings_tried'] == 2
    assert all(inst is instance and rules == answers for inst, rules, _ in calls)
    assert all(kwargs['budget_s'] == 30 and kwargs['finalize_s'] == 10 for _, _, kwargs in calls)
    assert all(kwargs['config'].page_share == .15 for _, _, kwargs in calls)
    assert record['budget_s'] == 90 and record['finalize_s'] == 30
    assert record['per_trial_budgets'] == [dict(index=i, budget_s=30., finalize_s=10.) for i in (1, 2)]
    assert record['portfolio_policy_version'] == 'logic3-hatch-portfolio-v1'


def test_relaxed_candidate_waits_until_all_mappings_tried_and_lowest_level_wins(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    calls = []
    def trial(*args):
        index = len(calls)
        calls.append(index)
        if index == 0:
            return NS(plan='milder'), evidence('relaxed_plan', level=1, lifts=12)
        if index == 1:
            return NS(plan='cheaper-but-worse-tier'), evidence('relaxed_plan', level=4, lifts=2)
        return None, evidence('no_checked_plan')
    monkeypatch.setattr(hatch, '_trial', trial)
    result, record = hatch.plan_hatch(instance, answers)
    assert len(calls) == 3 and result.plan == 'milder'
    assert record['mappings_tried'] == 3 and record['selected_mapping_index'] == 1
    assert record['hatch_search_note'] == "no plan on the 3 hatch mappings tried; this doesn't prove none exists"
    assert record['passed'] is False
    assert all(budget['budget_s'] is None and budget['finalize_s'] is None
               for budget in record['per_trial_budgets'])


def test_no_checked_plan_reports_exact_bounded_mapping_search(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    monkeypatch.setattr(hatch, '_trial', lambda *args: (None, evidence('no_checked_plan')))
    result, record = hatch.plan_hatch(instance, answers)
    assert result is None and record['status'] == 'no_checked_plan'
    assert record['reason'] == "no plan on the 3 hatch mappings tried; this doesn't prove none exists"
    assert record['hatch_search_message'] == record['hatch_search_note'] == record['reason']


def test_streaming_only_publishes_global_improvements_across_mapping_trials(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    events, calls = [], []
    costs = [10, 12, 8]
    def trial(inst, rules, compiled, metadata, kwargs):
        index = len(calls)
        calls.append(index)
        kwargs['on_incumbent']({'passed': True, 'lifts': costs[index], 'wall_s': 1.})
        return None, evidence('no_checked_plan')
    monkeypatch.setattr(hatch, '_trial', trial)
    hatch.plan_hatch(instance, answers, on_incumbent=events.append)
    assert [event['lifts'] for event in events] == [10, 8]
    assert [event['hatch_mapping_index'] for event in events] == [1, 3]


def test_trial_identity_refusal_is_preserved_and_stops_further_search(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    calls = []
    def trial(*args):
        calls.append(True)
        row = evidence('constraint_unavailable')
        row.update(refusal_kind='engine_identity', reason='pinned manifest invalid')
        return None, row
    monkeypatch.setattr(hatch, '_trial', trial)
    result, record = hatch.plan_hatch(instance, answers)
    assert result is None and len(calls) == 1
    assert record['status'] == 'constraint_unavailable' and record['refusal_kind'] == 'engine_identity'
    assert record['reason'] == 'pinned manifest invalid'


def test_private_trial_clone_does_not_replace_original_adapter_or_compiler(monkeypatch, portfolio):
    instance, answers, _ = portfolio
    compiled = compiled_rule(instance, 'one_port_per_hatch')
    source_compile = adapter.compile_constraint
    original_plan = adapter.plan_with_rules
    # A minimal body makes the namespace isolation directly observable without
    # running the real engine. Its copied code resolves the compile dependency.
    namespace = {'compile_constraint': lambda *args, **kwargs: ('original', {})}
    exec('def body(instance, answers, **kwargs):\n'
         '    return compile_constraint(instance, answers[0]), {}\n', namespace)
    fake = NS(__wrapped__=namespace['body'])
    monkeypatch.setattr(adapter, 'plan_with_rules', fake)
    result, _ = hatch._trial(instance, [answers[1]], compiled, {'trial': True}, {})
    assert result == (compiled, {'trial': True})
    assert namespace['body'](instance, answers)[0] == ('original', {})
    assert adapter.compile_constraint is source_compile and adapter.plan_with_rules is fake
    monkeypatch.setattr(adapter, 'plan_with_rules', original_plan)


def test_holding_a_relaxed_plan_does_not_bypass_final_manifest_change(monkeypatch, portfolio):
    instance, answers, evidence = portfolio
    calls = []
    def trial(*args):
        calls.append(True)
        return NS(plan='relaxed'), evidence('relaxed_plan', level=1)
    monkeypatch.setattr(hatch, '_trial', trial)
    identities = iter([{'passed': True, 'test_identity': True}, {'passed': False}])
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: next(identities))
    result, record = hatch.plan_hatch(instance, answers)
    assert len(calls) == 3 and result is None and record['refusal_kind'] == 'engine_identity'
    assert record['status'] == 'constraint_unavailable' and not record['passed']
