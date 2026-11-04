"""Cheap completion/cargo policy tests; no solver or numerical job is launched."""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import method
import finishing
import masks
import search
from stowage.model import CheckReport, MoveCount, MovePlan, Violation

spec = importlib.util.spec_from_file_location('leader_completion_readonly', HERE.parent/'leader'/'completion.py')
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)


def test_completion_classifies_complete_separately_from_safe_partial():
    partial = {'level': 3, 'boxes_left_ashore': ['QZXU0000020'], 'boxes_aboard_percent': 75.}
    # The existence of an explicit completed field controls classification.
    assert completion.classify({'level': 3, 'completed': None,
                                'best_safe_partial': partial}) is None
    assert completion.classify({'level': 3, 'boxes_left_ashore': ['QZXU0000020']}) is None
    assert completion.classify({'completed': {'level': 4},
                                'best_safe_partial': partial}) == 'T1'
    assert completion.classify({'passed': True}) == 'T5'
    rows = {'partial': {'instance_id': 'partial', 'completed': None,
                        'best_safe_partial': partial},
            'complete': {'instance_id': 'complete', 'completed': {'level': 4},
                         'best_safe_partial': partial}}
    summary = completion.table(rows)
    assert summary['counts']['T1'] == 1
    assert summary['not_completed'] == 1
    assert summary['safe_partials'] == 2
    assert summary['mean_percent_boxes_aboard_in_safe_partials'] == 75.


@pytest.fixture
def synthetic(monkeypatch):
    ids = ('QZXU0000014', 'QZXU0000020', 'QZXU0000035', 'QZXU0000040')
    boxes = [SimpleNamespace(id=box, length_ft=length, gross_mass_kg=mass)
             for box, length, mass in [('QZXU0000014', 20, 1000.), ('QZXU0000020', 40, 2000.),
                                       ('QZXU0000035', 20, 5000.), ('QZXU0000040', 20, 3000.)]]
    instance = SimpleNamespace(id='synthetic-completion', call_index=0,
                               load_list=list(ids), containers=boxes)
    loaded, reports = {}, {}

    def make(name, aboard, *, rules=(), lifts=1., breach=1.):
        plan = MovePlan(id=name, instance_id=instance.id, call_index=0)
        loaded[plan.id] = set(aboard)
        left = sorted(set(ids) - set(aboard))
        broken = [('objective.all_loaded', None, None)] if left else []
        broken += [(rule, breach + 1., 1.) for rule in rules]
        reports[plan.id] = CheckReport(passed=not broken, shut_out=left,
            count=MoveCount(loads=len(aboard), weighted_nonproductive=lifts),
            violations=[Violation(rule_id=rule, severity='hard', call_index=0,
                measured=measured, limit=limit, message='Synthetic policy test.')
                for rule, measured, limit in broken])
        return plan

    def replay(instance, plan, **kwargs):
        return SimpleNamespace(legal=True, final_state=SimpleNamespace(ship=SimpleNamespace(
            placements=[SimpleNamespace(container_id=box) for box in loaded[plan.id]])))

    space = SimpleNamespace(from_plan=lambda instance, plan: tuple(
        SimpleNamespace(box=box, slot='0010002') for box in loaded[plan.id]))
    import stowage.actions
    import stowage.moves
    monkeypatch.setattr(stowage.actions, 'get_action_space', lambda *args: space)
    monkeypatch.setattr(stowage.moves, 'apply_plan', replay)
    # Support either local imports or explicit module aliases in policy code.
    monkeypatch.setattr(finishing, 'apply_plan', replay, raising=False)
    monkeypatch.setattr(finishing, 'get_action_space', lambda *args: space, raising=False)
    monkeypatch.setattr(method, 'check', lambda instance, plan: reports[plan.id])
    monkeypatch.setattr(finishing, 'check', lambda instance, plan: reports[plan.id])
    return SimpleNamespace(instance=instance, make=make, reports=reports, loaded=loaded)


def test_candidates_rank_cargo_before_rule_breach_and_lifts(synthetic):
    s = synthetic
    bank = finishing.Candidates(s.instance)
    # Two more preference/booking breaches and many lifts must not make the
    # controller prefer leaving an additional customer's box ashore.
    more = s.make('more-cargo', ['QZXU0000014', 'QZXU0000020', 'QZXU0000035'],
        rules=['expert.e8.away_from_heat', 'expert.e6.heavy_under_deck'], lifts=99., breach=100.)
    fewer = s.make('fewer-cargo', ['QZXU0000014', 'QZXU0000020'], lifts=0.)
    more_row = bank.observe(more, s.reports[more.id])
    fewer_row = bank.observe(fewer, s.reports[fewer.id])
    assert bank.rank(more_row) < bank.rank(fewer_row)
    assert bank.best_safe_partial()['plan'] is more
    assert bank.best_completed(3) is None


def test_candidates_break_cargo_ties_by_teu_then_mass(synthetic):
    s = synthetic
    bank = finishing.Candidates(s.instance)
    leave_40 = s.make('left-B-40ft', ['QZXU0000014', 'QZXU0000035', 'QZXU0000040'])
    leave_heavy_20 = s.make('left-C-heavy20', ['QZXU0000014', 'QZXU0000020', 'QZXU0000040'])
    leave_light_20 = s.make('left-A-light20', ['QZXU0000020', 'QZXU0000035', 'QZXU0000040'])
    rows = [bank.observe(plan, s.reports[plan.id])
            for plan in (leave_40, leave_heavy_20, leave_light_20)]
    assert bank.rank(rows[2]) < bank.rank(rows[1]) < bank.rank(rows[0])
    assert bank.best_safe_partial()['plan'] is leave_light_20


@pytest.mark.parametrize('complete_level', [4, 5, None])
def test_controller_continues_after_safe_l3_partial_and_keeps_both(monkeypatch, synthetic, complete_level):
    s = synthetic
    partial = s.make('safe-L3-partial', ['QZXU0000014', 'QZXU0000020'])
    unsafe_partial = s.make('unsafe-L4-partial', ['QZXU0000014', 'QZXU0000020', 'QZXU0000035'],
                            rules=['iacs.member_irs.container_securing'])
    full_rule = ('iacs.member_irs.container_securing' if complete_level == 4
                 else 'iscode.loading.gm')
    complete = s.make('completed', list(s.instance.load_list), rules=[full_rule])
    visited = []

    @contextmanager
    def capture():
        yield []

    @contextmanager
    def retain(bank):
        yield bank

    @contextmanager
    def mask(level):
        yield

    def generate(instance, *, level, **kwargs):
        visited.append(level)
        if level == 3:
            return [partial]
        if level == complete_level:
            return [complete]
        if level == 4:
            return [unsafe_partial]
        return []

    def record(instance, row, *, searched_level, trials):
        plan, report = row['plan'], row['report']
        aboard = s.loaded[plan.id]
        return {'level': row['analysis']['level'], 'searched_level': searched_level,
            'boxes_left_ashore': sorted(set(instance.load_list)-aboard),
            'boxes_aboard_percent': 100.*len(aboard)/len(instance.load_list),
            'teu_aboard': sum(box.length_ft/20 for box in instance.containers if box.id in aboard),
            'lifts': report.count.weighted_nonproductive, 'plan': plan.model_dump(mode='json'),
            'trials': list(trials)}

    monkeypatch.setattr(method, 'capture_checked', capture)
    monkeypatch.setattr(method, 'retain_failures', retain)
    monkeypatch.setattr(method, 'finish_relaxed', lambda *args, **kwargs: None)
    monkeypatch.setattr(method, '_record', record)
    monkeypatch.setattr(masks, 'peeled_masks', mask)
    monkeypatch.setattr(search, 'generate', generate)
    result, row = method.solve(s.instance, normal_call=lambda *args, **kwargs: (None, {}))
    assert visited == list(range(1, (complete_level or 5)+1))
    safe = row['best_safe_partial']
    assert safe['plan']['id'] == partial.id
    assert safe['boxes_left_ashore'] == ['QZXU0000035', 'QZXU0000040']
    assert safe['boxes_aboard_percent'] == 50.
    assert safe['teu_aboard'] == 3.
    assert safe['lifts'] == 1.
    if complete_level is None:
        assert row['completed'] is None
        assert completion.classify(row) is None
    else:
        assert result.plan is complete
        assert row['completed']['level'] == complete_level
        assert row['completed']['boxes_left_ashore'] == []
        assert row['completed']['boxes_aboard_percent'] == 100.
        assert row['completed']['plan']['id'] == complete.id
        assert completion.classify(row) == completion.LEVEL_COLUMN[complete_level]


def test_partial_record_exposes_replayed_cargo_and_unchanged_checker_report(synthetic):
    s = synthetic
    plan = s.make('record-safe-partial', ['QZXU0000014', 'QZXU0000020'])
    bank = finishing.Candidates(s.instance)
    row = bank.observe(plan, s.reports[plan.id])
    record = method._record(s.instance, row, searched_level=3, trials=[])
    assert record['boxes_left_ashore'] == ['QZXU0000035', 'QZXU0000040']
    assert record['boxes_aboard_percent'] == 50.
    assert record['teu_aboard'] == 3.
    assert record['lifts'] == 1.
    assert record['plan'] == plan.model_dump(mode='json')
    assert record['checker_report'] == s.reports[plan.id].model_dump(mode='json')
    assert record['passed'] is False
