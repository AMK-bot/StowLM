# Copied from experiments/mvp/frontier/lab/c5/c5-peel/test_assignment_controller.py; local isolated imports and fixtures.
"""Control-policy regression tests with real D242 serialization and checker reports."""
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

HERE = Path(__file__).resolve().parent

def install_checks(monkeypatch, synthetic):
    monkeypatch.setattr(method, 'check', lambda instance, plan: synthetic.reports[plan.id])
    monkeypatch.setattr(method, '_reference_from_checked', lambda *args: None)

def test_assignment_l0_returns_exact_normal_result(monkeypatch, synthetic):
    s = synthetic
    install_checks(monkeypatch, s)
    plan = s.make('ordinary-pass', s.instance.load_list)
    result = SimpleNamespace(plan=plan)
    monkeypatch.setattr(method, 'generate', lambda *a, **k: pytest.fail('L0 pass must not peel'))
    actual, row = method.solve(s.instance, normal_call=lambda *a, **k: (result, {}))
    assert actual is result and actual.plan is plan
    assert row['level'] == 0 and row['completed']['level'] == 0
    assert row['plan'] == plan.model_dump(mode='json')
    assert len(row['trials']) == 1

@pytest.mark.parametrize('completion_level', [4, 5, None])
def test_assignment_keeps_safe_partial_and_peels_until_completed(monkeypatch, synthetic, completion_level):
    s = synthetic
    install_checks(monkeypatch, s)
    partial = s.make('safe-partial', s.instance.load_list[:3])
    rule = 'iacs.member_irs.container_securing' if completion_level == 4 else 'iscode.loading.gm'
    full = s.make('full', s.instance.load_list, rules=[rule])
    calls, finishing = [], []
    def generate(instance, *, level, partial=False, **kwargs):
        calls.append((level, partial))
        plans = [partial_plan] if level == 3 and partial else [full] if level == completion_level else []
        return plans, {'synthetic': True}
    partial_plan = partial
    monkeypatch.setattr(method, 'generate', generate)
    monkeypatch.setattr(method, 'finish_candidate', lambda instance, plan, bank, **kwargs: finishing.append((kwargs['level'], plan.id)))
    result, row = method.solve(s.instance, normal_call=lambda *a, **k: (None, {}))
    assert calls == [(1, False), (2, False), (3, False), (3, True)] + [(k, False) for k in range(4, (completion_level or 5)+1)]
    assert row['best_safe_partial']['boxes_aboard_percent'] == 75.
    assert row['best_safe_partial']['boxes_left_ashore'] == [s.instance.load_list[3]]
    if completion_level is None:
        assert row['completed'] is None and result.plan is partial
    else:
        assert result.plan is full
        assert row['completed']['level'] == completion_level
        assert row['completed']['boxes_left_ashore'] == []
        assert row['checker_report'] == s.reports[full.id].model_dump(mode='json')

def test_assignment_medium_ordinary_source_is_unchanged():
    import hashlib, json
    hashes = json.loads((HERE/'assignment-start-hashes.json').read_text())
    key = 'assignment_vendor/c5/c5-securing/method.py'
    assert hashlib.sha256((HERE/key).read_bytes().split(b'\n',1)[1]).hexdigest() == hashes[key]

def test_assignment_small_guard_uses_frozen_ordinary_engine(monkeypatch):
    import engine.planner
    expected = (object(), {'ordinary': True})
    seen = []
    monkeypatch.setattr(engine.planner, 'plan_and_check', lambda instance, **kwargs: (seen.append(kwargs), expected)[1])
    monkeypatch.setattr(method, 'ordinary_assignment', lambda *a, **k: pytest.fail('dense targets on small map'))
    assert method.normal(SimpleNamespace(load_list=['box'])) is expected
    assert seen[0]['budget_s'] == 240. and seen[0]['finalize_s'] == 120.

def test_optimizer_observer_preserves_returns_and_restores(monkeypatch, synthetic):
    s = synthetic
    plan = s.make('optimizer-input', s.instance.load_list)
    outcome = object()
    calls = []
    def original(instance, candidate, **kwargs):
        calls.append((instance, candidate, kwargs))
        return outcome
    module, bank = SimpleNamespace(optimize=original), SimpleNamespace()
    with method.observe_optimizer(module, bank):
        assert module.optimize(s.instance, plan, budget_s=120.) is outcome
    assert module.optimize is original
    assert calls == [(s.instance, plan, {'budget_s': 120.})]
    assert method._stow_key(plan) in bank._finished_stows
    with pytest.raises(RuntimeError):
        with method.observe_optimizer(module, bank):
            raise RuntimeError('sentinel')
    assert module.optimize is original

def test_rejected_stow_is_finished_only_once_per_invocation(monkeypatch, synthetic):
    s = synthetic
    plan = s.make('already-finished', s.instance.load_list,
                  rules=['iacs.member_irs.container_securing'])
    bank = method.Candidates(s.instance)
    bank._finished_stows = {method._stow_key(plan)}
    method.finish_candidate(s.instance, plan, bank, level=4, seed=0,
                            budget_s=120., label='repeated')
    assert bank.finishing_records[-1]['metadata']['reused_same_invocation_finishing']

def test_assignment_import_keeps_own_policy_reader_first():
    import assignment_runtime
    assignment_runtime.load_assignment()
    import tiers
    assert Path(tiers.__file__).resolve() == HERE/'tiers.py'
    assert Path(sys.path[0]).resolve() == HERE

def test_relaxed_reference_uses_measured_same_invocation_complete_stow(monkeypatch, synthetic):
    s = synthetic
    bank = method.Candidates(s.instance)
    partial = s.make('partial-reference', s.instance.load_list[:3])
    full = s.make('full-reference', s.instance.load_list,
                  rules=['iacs.member_irs.container_securing'])
    bank.observe(partial, s.reports[partial.id], source='partial')
    bank.observe(full, s.reports[full.id], source='complete')
    method.load_assignment()
    import chainlab
    import stowage.checker.full
    seen = []
    monkeypatch.setattr(stowage.checker.full, 'departure_condition', lambda instance, plan: (seen.append(plan.id), object())[1])
    monkeypatch.setattr(chainlab, 'condition_terms', lambda *args: dict(gm=1.4, draught=5.1, cb=.65))
    reference = method._reference_from_checked(s.instance, bank)
    assert seen == ['full-reference']
    assert reference['measured_gm'] == 1.4
    assert reference['gm'] == pytest.approx(1.45)
    assert reference['draught'] == 5.1 and reference['cb'] == .65
    assert reference['source'] == 'same-invocation checked complete'

def test_failed_optimizer_is_not_cached(synthetic):
    plan = synthetic.make('failed-optimizer', synthetic.instance.load_list)
    def failed(*args, **kwargs):
        raise OSError('synthetic optimizer error')
    module, bank = SimpleNamespace(optimize=failed), SimpleNamespace()
    with pytest.raises(OSError):
        with method.observe_optimizer(module, bank):
            module.optimize(synthetic.instance, plan)
    assert bank._finished_stows == set()
    assert module.optimize is failed

def test_different_ballast_start_is_not_an_identical_finishing_key(synthetic):
    from stowage.model import BallastCondition
    plan = synthetic.make('ballast-start', synthetic.instance.load_list)
    changed = plan.model_copy(update={'ballast': BallastCondition(tank_masses_kg={'FWD-P': 1.}, source='finisher')})
    assert method._stow_key(plan) != method._stow_key(changed)

def test_l0_observer_defers_analysis_outside_timed_method():
    calls = []
    plan, report = object(), object()
    original = lambda *args: report
    module = SimpleNamespace(check=original)
    bank = SimpleNamespace(observe=lambda *args, **kwargs: calls.append((args, kwargs)))
    with method.observe_checks(module, bank):
        assert module.check(None, plan) is report
        assert not calls
    assert calls[0][0] == (plan, report)
    assert module.check is original

def test_assignment_loader_rejects_ambient_vendor_module(monkeypatch):
    import assignment_runtime
    assignment_runtime.load_assignment()
    monkeypatch.setitem(sys.modules, 'repair', SimpleNamespace(__file__='/synthetic-foreign/repair.py'))
    with pytest.raises(RuntimeError, match='foreign assignment dependency: repair'):
        assignment_runtime.load_assignment()

def test_raw_goal_candidate_survives_worse_postprocessing(monkeypatch, synthetic):
    import assignment_proposal
    s = synthetic
    raw = s.make('raw-goal', s.instance.load_list, rules=['iacs.member_irs.container_securing'])
    processed = s.make('worse-postprocess', s.instance.load_list, rules=['iscode.loading.gm'])
    plans = {'raw': raw, 'processed': processed}
    problem = SimpleNamespace(mixes_hold_stack=lambda a: False,
        score=lambda actions: SimpleNamespace(plan=plans[actions[0][1]], actions=actions))
    ordinary = SimpleNamespace(Sequencer=lambda p: SimpleNamespace(order=lambda a: tuple(a.items())),
        assignment_key=lambda actions: tuple(actions), exact_ratio=lambda *args: (1.4, 1.03, 1))
    monkeypatch.setattr(assignment_proposal, 'check', lambda instance, plan: s.reports[plan.id])
    context = {'_candidate_plans': {}, '_candidate_reports': {}, '_candidate_kinds': {}}
    seen = {}
    assignment_proposal._remember(ordinary, s.instance, problem, context, seen, {'box': 'raw'}, kind='raw', level=4)
    assignment_proposal._remember(ordinary, s.instance, problem, context, seen, {'box': 'processed'}, kind='processed', level=4)
    bank = method.Candidates(s.instance)
    for key, plan in context['_candidate_plans'].items():
        bank.observe(plan, context['_candidate_reports'][key])
    assert bank.best_completed(4)['plan'] is raw
    assert context['_candidate_reports'][(('box', 'raw'),)] is s.reports[raw.id]

def test_ordinary_adapter_continues_after_first_finisher_error(monkeypatch, synthetic):
    s = synthetic
    install_checks(monkeypatch, s)
    first = s.make('first-candidate', s.instance.load_list, rules=['iacs.member_irs.container_securing'])
    second = s.make('second-candidate', s.instance.load_list)
    sequences = [tuple(SimpleNamespace(box=box, slot=str(index)) for box in s.instance.load_list) for index in (0, 1)]
    scored = [SimpleNamespace(plan=plan, actions=actions, objective=i) for i, (plan, actions) in enumerate(zip((first, second), sequences))]
    problem = SimpleNamespace(score=lambda actions: scored[int(actions[0].slot)])
    def finish(instance, plan, **kwargs):
        if plan is first:
            raise OSError('synthetic first-candidate error')
        return second, {'passed': True}
    ordinary = SimpleNamespace(install=lambda: None, check=lambda instance, plan: s.reports[plan.id],
        propose=lambda *a, **k: sequences, finish=finish)
    monkeypatch.setattr(method, 'load_assignment', lambda: ordinary)
    import alns
    monkeypatch.setattr(alns, 'Problem', lambda instance: problem)
    result, metadata = method.ordinary_assignment(s.instance)
    assert result.plan is second
    assert len(metadata['tries']) == 2 and 'error' in metadata['tries'][0]
