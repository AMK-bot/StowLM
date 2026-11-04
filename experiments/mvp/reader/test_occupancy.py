"""Synthetic predicate regressions; these are not checker-certified real plans."""
from types import SimpleNamespace as NS

import pytest

from llm.instructions.apply import apply
from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers
from experiments.mvp.reader import frontier_driver, occupancy
from experiments.mvp.reader.constraints import InfeasibleInstruction
from experiments.mvp.reader.pipeline import compile_constraint, intrinsic_violations


def fixture(slot='0010102', *, discharge=False):
    dg = NS(imdg_class='8', subsidiary_risks=[], additional=[])
    return NS(id='synthetic-retained', vessel=NS(cells=[
        NS(id='0010102'), NS(id='0010104'), NS(id='0030102')]),
        containers=[NS(id='retained', dg=dg)], load_list=[],
        onboard=NS(placements=[NS(container_id='retained', slot=slot)]),
        discharge_list=['retained'] if discharge else [])


def tier():
    return MaxTiers(type='max_tiers', bay=1, zone='hold', max_tiers=0)


def dg():
    return DgKeepAway(type='dg_keep_away', imdg_class='8', min_bays=1,
                      away_from={'kind': 'bay', 'bay': 1})


def replay(placements, legal=True):
    return NS(legal=legal, final_state=NS(ship=NS(placements=placements)))


@pytest.mark.parametrize('answer', [tier(), dg()])
def test_untouched_retained_final_occupancy_is_not_honoured(answer, monkeypatch):
    instance = fixture()
    plan = NS(moves=[])
    # Reproduce the old omission, then prove the experiment predicate closes it.
    assert apply(instance, answer).honoured(plan)
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: replay(instance.onboard.placements))
    constrained = occupancy.apply_reader_constraint(instance, answer)
    assert not constrained.honoured(plan)
    assert intrinsic_violations(instance, answer, plan)[0].startswith('final retained')


@pytest.mark.parametrize('answer', [tier(), dg()])
def test_fixed_retained_conflict_rejected_before_any_planner(answer, monkeypatch):
    instance = fixture()
    with pytest.raises(InfeasibleInstruction, match='fixed retained cargo'):
        compile_constraint(instance, answer)
    monkeypatch.setattr(frontier_driver, '_bind', lambda *a: pytest.fail('planner entered'))
    # Direct driver callers cannot bypass the compiler preflight with a raw object.
    with pytest.raises(InfeasibleInstruction, match='fixed retained cargo'):
        frontier_driver.plan_and_check(instance, apply(instance, answer))


@pytest.mark.parametrize('answer', [tier(), dg()])
def test_discharge_exemption_and_compatible_retained_state(answer):
    assert compile_constraint(fixture(discharge=True), answer)[0]
    assert compile_constraint(fixture('0030102'), answer)[0]


def test_close_bay_keeps_loading_only_contract(monkeypatch):
    instance = fixture()
    answer = CloseBay(type='close_bay', bay=1)
    constrained, _ = compile_constraint(instance, answer)
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: pytest.fail('CloseBay final replay'))
    assert constrained.honoured(NS(moves=[]))
    assert not constrained.honoured(NS(moves=[NS(kind='restow_shift', container_id='retained', dst='0010102')]))


def test_composed_close_bay_does_not_leak_into_final_occupancy(monkeypatch):
    instance = fixture()
    base = apply(instance, CloseBay(type='close_bay', bay=1))
    base = apply(instance, MaxTiers(type='max_tiers', bay=3, zone='hold', max_tiers=0), base=base)
    constrained = occupancy.strengthen_constraint(base, reject_retained=True)
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: replay(instance.onboard.placements))
    assert constrained.honoured(NS(moves=[]))


@pytest.mark.parametrize('nested', [False, True])
def test_retained_dg_subsidiary_and_nested_classes_are_included(nested):
    instance = fixture()
    instance.containers[0].dg = NS(imdg_class='3', subsidiary_risks=[] if nested else ['8'],
        additional=[NS(imdg_class='8', subsidiary_risks=[], additional=[])] if nested else [])
    with pytest.raises(InfeasibleInstruction, match='fixed retained cargo'):
        compile_constraint(instance, dg())


@pytest.mark.parametrize('answer', [tier(), dg()])
def test_intermediate_destination_still_rejected_after_valid_final_state(answer, monkeypatch):
    instance = fixture('0030102')
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: replay(instance.onboard.placements))
    plan = NS(moves=[NS(kind='restow_on', container_id='retained', dst='0010102')])
    assert occupancy.apply_reader_constraint(instance, answer).violations(plan)[0].startswith('restow_on')


def test_failed_replay_and_long_footprint_fail_occupancy_audit(monkeypatch):
    instance = fixture('0030102')
    constrained = occupancy.apply_reader_constraint(instance, dg())
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: replay([], legal=False))
    assert constrained.violations(NS(moves=[])) == ['plan failed physical move replay']
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: replay([
        NS(container_id='retained', slot='0020102')]))
    assert constrained.violations(NS(moves=[]))  # even bay footprint touches bay 1


@pytest.mark.parametrize('gate', ['score', 'finish', 'return'])
@pytest.mark.parametrize('answer', [tier(), dg()])
def test_every_driver_gate_uses_final_occupancy(gate, answer, monkeypatch):
    instance = fixture('0030102')
    plan = NS(moves=[])
    bad_final = replay([NS(container_id='retained', slot='0010102')])
    monkeypatch.setattr(occupancy, 'apply_plan', lambda *a: bad_final)
    monkeypatch.setattr(frontier_driver, 'constrain_frontier_problem', lambda p, c: p)
    monkeypatch.setattr(frontier_driver.portfolio, 'Problem', lambda i: NS(score=lambda a: NS(plan=plan)))
    monkeypatch.setattr(frontier_driver.portfolio, '_finish', lambda *a, **kw: (NS(plan=plan), {'passed': True}))

    def bind(function, dependencies):
        def run(inst, **kwargs):
            if gate == 'score':
                assert dependencies['Problem'](inst).score(()) is None
                return None, {}
            if gate == 'finish':
                return dependencies['_finish'](inst, plan)
            return NS(plan=plan), {}
        return run

    monkeypatch.setattr(frontier_driver, '_bind', bind)
    result, record = frontier_driver.plan_and_check(instance, apply(instance, answer))
    assert result is None
    if gate == 'score':
        assert record['instruction_audit']['rejected'] == 1
    elif gate == 'finish':
        assert record['instruction_audit']['finish_rejected'] == 1
    else:
        assert record['final_instruction_violations']
