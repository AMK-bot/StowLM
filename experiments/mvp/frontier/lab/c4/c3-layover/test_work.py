"""Construction determinism and strict count admission on synthetic TRAIN maps."""
import importlib.util
from pathlib import Path
import sys

import pytest
from stowage.model import Instance

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c4_layover_work_test_method', HERE/'method.py')
method = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = method
spec.loader.exec_module(method)


def tiny():
    path = HERE.parents[1]/'c3'/'c3-layover'/'cohort'/'train.layover.tiny.o30.s331101000.json'
    return Instance.model_validate_json(path.read_text())


def test_work_admission_never_exceeds_counter():
    work = method.WorkBudget.from_context({'work_config': {'limits': {'patterns': 2}}})
    work.take('patterns', 2)
    with pytest.raises(method.WorkExhausted):
        work.take('patterns')
    assert work.used['patterns'] == 2
    assert work.exhausted == ['patterns']


def test_invalid_counter_is_rejected():
    with pytest.raises(ValueError):
        method.WorkBudget.from_context({'work_config': {'limits': {'wall_s': 1}}})


def test_same_seed_same_candidates_at_widely_different_requested_seconds():
    instance = tiny()
    contexts = [{}, {}]
    short = method.propose(instance, seed=17, budget_s=.000001, context=contexts[0])
    long = method.propose(instance, seed=17, budget_s=1000000., context=contexts[1])
    assert short and short == long
    assert contexts[0]['method_metadata']['work'] == contexts[1]['method_metadata']['work']
    for ctx in contexts:
        work = ctx['method_metadata']['work']
        assert all(n <= work['limits'][key] for key,n in work['used'].items())


def test_zero_branch_limit_generates_no_work():
    context = {'work_config': {'limits': {'branches': 0}}}
    assert method.propose(tiny(), seed=0, budget_s=1000000., context=context) == []
    work = context['method_metadata']['work']
    assert not work['used']
    assert work['exhausted'] == ['branches']


def test_zero_pattern_limit_returns_no_partial_assignment():
    context = {'work_config': {'limits': {'patterns': 0}}}
    assert method.propose(tiny(), seed=0, budget_s=1000000., context=context) == []
    work = context['method_metadata']['work']
    assert work['used'].get('patterns', 0) == 0
    assert 'patterns' in work['exhausted']


def test_construction_has_no_wall_time_solver_limit():
    source = (HERE/'page_base.py').read_text()
    assert 'max_time_in_seconds' not in source
    assert 'monotonic(' not in source
    assert 'max_deterministic_time' in source
    assert 'max_number_of_conflicts' in source
    assert "'maxiter'" in source


def test_ballast_prices_use_background_tank_targets_and_physical_height():
    instance = tiny()
    working, _, _ = method.prepare(instance)
    helper = method._load('c4_work_test_ballast', HERE/'ballast_local.py')
    targets = helper.ballast_targets(instance)
    model = method.pages.Model(method.Problem(working), ballast_targets=targets)
    assert targets['kg_weight'] > 0
    assert targets.get('provenance')
    b = model.boxes[0]
    possible = [page for page in model.pages if len(page.slots) > 1]
    assert possible
    page = possible[0]
    assert model.moment_cost(page, 1, b) > model.moment_cost(page, 0, b)


def test_work_config_is_copied_and_solver_budgets_are_recorded():
    config = {'limits': {'patterns': 7}, 'cp_deterministic_ticks': .12,
              'cp_conflicts': 100, 'lp_maxiter': 30}
    work = method.WorkBudget.from_context({'work_config': config})
    work.limits['patterns'] = 3
    assert config['limits']['patterns'] == 7
    assert work.cp_deterministic_ticks == .12
    assert work.cp_conflicts == 100
    assert work.lp_maxiter == 30


def test_zero_solver_limits_are_graceful_work_stops():
    work = method.WorkBudget.from_context({'work_config': {
        'cp_deterministic_ticks': 0., 'cp_conflicts': 0, 'lp_maxiter': 0}})
    assert work.cp_conflicts == work.lp_maxiter == 0
    assert work.cp_deterministic_ticks == 0.


def test_failed_preserve_branch_cannot_spend_broad_restow_reservation():
    work = method.WorkBudget.from_context({})
    preserve = work.for_branch('preserve_prefixes', 4)
    assert preserve.limits['pricing_calls'] == 32
    preserve.take('pricing_calls', preserve.limits['pricing_calls'])
    with pytest.raises(method.WorkExhausted):
        preserve.take('pricing_calls')
    assert work.used['pricing_calls'] == 32
    assert work.exhausted == []
    broad = work.for_branch('all_prefixes', 3)
    assert broad.limits['pricing_calls'] == 77
    broad.take('pricing_calls', 77)
    assert work.used['pricing_calls'] == 109
    assert work.remaining('pricing_calls') == 51


def test_branch_reservations_charge_parent_once_and_never_overadmit():
    work = method.WorkBudget.from_context({'work_config': {'limits': {'patterns': 3}}})
    scoped = work.for_branch('all_prefixes', 1)
    scoped.take('patterns', 2)
    assert scoped.used['patterns'] == work.used['patterns'] == 2
    with pytest.raises(method.WorkExhausted):
        scoped.take('patterns')
    assert scoped.used['patterns'] == work.used['patterns'] == 2
    assert scoped.exhausted == ['patterns']
    assert work.exhausted == []


def test_zero_global_cap_is_marked_when_branch_cap_refuses_first():
    work = method.WorkBudget.from_context({'work_config': {'limits': {'patterns': 0}}})
    scoped = work.for_branch('preserve_prefixes', 4)
    with pytest.raises(method.WorkExhausted):
        scoped.take('patterns')
    assert scoped.exhausted == work.exhausted == ['patterns']
    assert not scoped.used and not work.used


def test_coverage_phase_shares_geometry_but_ballast_phase_keeps_position():
    import numpy as np
    instance = tiny()
    roots = method.restow_options(instance)[0][1]
    working, _, _ = method.prepare(instance, roots)
    helper = method._load('c4_phase_test_ballast', HERE/'ballast_local.py')
    model = method.pages.Model(method.Problem(working),
        ballast_targets=helper.ballast_targets(instance))
    coverage_types = model.pricing_types(1)
    physical_types = model.pricing_types(3)
    repeated = [group for group in coverage_types.values() if len(group) > 1]
    assert repeated
    assert len(physical_types) > len(coverage_types)
    checked = 0
    for group in repeated:
        patterns = model.price(group[0], np.ones(len(model.boxes)), phase=1)
        for _, boxes in patterns.values():
            for page_index in group:
                assert model.legal(model.pages[page_index], boxes)
                assert model.add(page_index, boxes)
                checked += 1
    assert checked > 1
    model.lcg_target = 0.
    assert model.pricing_types(1) is model.types
    model.lcg_target = None
    model.oracle = object()
    assert model.pricing_types(1) is model.types


def test_balancing_can_exchange_across_physical_price_groups():
    from types import SimpleNamespace
    import random
    import numpy as np
    model = method.pages.Model.__new__(method.pages.Model)
    model.ballast_targets = {}
    model.arrival_heel = 0.
    model.T = SimpleNamespace(mass=np.array([10000., 4000.]), slot_ids=['right0', 'left', 'right1'])
    model.p = SimpleNamespace(cells_tcg=lambda slot: -5. if slot == 'left' else 5.)
    model.pages = [method.pages.Page('20', (k,), (k,), 100000., 8., True, str(k)) for k in range(3)]
    model.columns = [method.pages.BASE.Pattern(0, (0,), 0.), method.pages.BASE.Pattern(2, (1,), 0.)]
    model.pool = {(0, (0,)): 0, (2, (1,)): 1}
    model.types = {'right0': [0], 'left': [1], 'right1': [2]}
    model.geometry_types = {'same_legal_geometry': [0, 1, 2]}
    model.work = method.WorkBudget.from_context({})
    checked = []
    model.legal = lambda page, boxes: not checked.append((page.stacks, boxes))
    model.lash_ok = lambda page, boxes: True
    model.pattern_restow_cost = lambda page, boxes: float(page.cover)
    assert model.moments([0, 1]) == 70.
    chosen, heel = model.balance([0, 1], random.Random(2), rounds=30)
    assert abs(heel) < 70.
    assert checked
    assert sorted(b for k in chosen for b in model.columns[k].boxes) == [0, 1]
    assert model.balance_types() is model.geometry_types
    for k in chosen:
        column = model.columns[k]
        if k > 1:
            assert column.cost == float(model.pages[column.page].cover)
