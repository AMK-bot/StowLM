"""Hatch instructions remain strict when cargo completion is explicitly peeled."""
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.binding import adapter, allocation
from experiments.mvp.pipeline.binding.test_hooks import fixture_instance
from experiments.mvp.reader.constraints import compile_one_port_per_hatch
from experiments.mvp.reader.schema import OnePortPerHatch
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import CheckReport, PartReport, Placement, Violation
from stowage.moves import apply_plan


def ship():
    instance = fixture_instance()
    return instance.model_copy(update={'containers': [b.model_copy(update={'dg': None})
                                                     for b in instance.containers]})


def compiled(instance):
    # In this fixture row 1 shares one physical cover across both bays/zones;
    # row 2 shares the other. Both may remain partially or entirely empty.
    ports = {cover.id: ('THBKK' if cover.id.endswith('-1') else 'THSGZ')
             for cover in instance.vessel.hatch_covers}
    historical = compile_one_port_per_hatch(instance, ports)
    return historical, allocation.HatchInstructionConstraint.from_compiled(historical)


def partial(instance, *placements):
    return get_action_space('boxslot').expand(instance, [Place(box, slot) for box, slot in placements])


def test_compliant_occupied_hatch_does_not_make_omitted_cargo_an_instruction_violation():
    instance = ship()
    old, rule = compiled(instance)
    plan = partial(instance, (instance.load_list[0], '0010102'))
    replay = apply_plan(instance, plan)
    assert replay.legal and len(replay.final_state.ship.placements) == 1
    assert len(instance.load_list) == 4
    assert old.violations(plan) == [f'missing load containers: {sorted(instance.load_list[1:])}']
    assert rule.violations(plan) == [] and rule.honoured(plan)
    # Empty covers are permitted too; the full checker still classifies all omissions.
    assert rule.violations(partial(instance)) == []


def test_mixed_ports_on_one_physical_hatch_remain_rejected_on_a_partial_plan():
    instance = ship()
    _, rule = compiled(instance)
    plan = partial(instance, (instance.load_list[0], '0010102'),
                   (instance.load_list[1], '0030102'))
    assert apply_plan(instance, plan).legal
    errors = rule.violations(plan)
    assert any('mixed discharge ports' in reason for reason in errors)
    assert any('allocation mismatch' in reason for reason in errors)
    assert not any('missing load containers' in reason for reason in errors)


def test_unmixed_but_wrong_cover_allocation_and_destination_screen_remain_strict():
    instance = ship()
    _, rule = compiled(instance)
    plan = partial(instance, (instance.load_list[0], '0010202'))
    assert apply_plan(instance, plan).legal
    errors = rule.violations(plan)
    assert any('not allocated' in reason for reason in errors)
    assert any('allocation mismatch' in reason for reason in errors)
    assert not any('mixed discharge ports' in reason for reason in errors)


def test_unknown_physical_placement_and_illegal_replay_fail_closed():
    instance = ship()
    _, rule = compiled(instance)
    good = partial(instance, (instance.load_list[0], '0010102'))
    load = next(move for move in good.moves if move.kind == 'load')
    invalid = good.model_copy(update={'moves': [*good.moves, load.model_copy(update={'dst': '9990102'})]})
    assert not apply_plan(instance, invalid).legal
    errors = rule.violations(invalid)
    assert 'instruction: cell outside vessel' in errors
    assert 'instruction audit: illegal plan replay' in errors


def test_replay_exception_is_an_instruction_audit_failure(monkeypatch):
    instance = ship()
    _, rule = compiled(instance)
    plan = partial(instance, (instance.load_list[0], '0010102'))
    def invalid(*args):
        raise ValueError('invalid native replay')
    monkeypatch.setattr('stowage.moves.apply_plan', invalid)
    assert rule.violations(plan) == ['instruction audit: invalid plan replay']


def test_retained_cargo_cannot_change_slots_even_within_its_allocated_cover():
    instance = ship()
    retained = instance.load_list[0]
    onboard = instance.onboard.model_copy(update={
        'placements': [Placement(container_id=retained, slot='0010102')]})
    yard = instance.yard.model_copy(update={'stacks': [stack.model_copy(update={
        'boxes': [box for box in stack.boxes if box != retained]})
        for stack in instance.yard.stacks if any(box != retained for box in stack.boxes)]})
    instance = instance.model_copy(update={'stage': 'B', 'onboard': onboard, 'yard': yard,
                                           'load_list': instance.load_list[1:]})
    _, rule = compiled(instance)
    assert rule.violations(partial(instance)) == []
    moved = partial(instance, (retained, '0030102'))
    assert apply_plan(instance, moved).legal
    assert rule.forbids(retained, '0030102') is None
    assert rule.violations(moved) == [f'retained cargo changed final slot: {retained}']


def test_allocator_returns_instruction_only_audit_without_running_numerical_search(monkeypatch):
    instance = ship().model_copy(update={'load_list': ship().load_list[:1]})
    fake = NS(parameters=NS(), Solve=lambda _: allocation.cp_model.OPTIMAL,
              StatusName=lambda _: 'OPTIMAL', BooleanValue=lambda _: True)
    monkeypatch.setattr(allocation, 'wait_for_capacity', lambda: {'admitted': True})
    monkeypatch.setattr(allocation.cp_model, 'CpSolver', lambda: fake)
    rule, metadata = allocation.derive_hatch_allocation(instance)
    assert isinstance(rule, allocation.HatchInstructionConstraint)
    assert rule.violations(partial(instance)) == []
    assert 'completion' in metadata['instruction_audit_scope']


@pytest.mark.parametrize('mixed', [False, True])
def test_relaxed_release_uses_original_inspect_t3_while_hatch_instruction_stays_strict(monkeypatch, mixed):
    instance = ship()
    _, rule = compiled(instance)
    placements = [(instance.load_list[0], '0010102')]
    if mixed:
        placements.append((instance.load_list[1], '0030102'))
    plan = partial(instance, *placements)
    replay = apply_plan(instance, plan)
    assert replay.legal
    missing = sorted(set(instance.load_list) - {p.container_id for p in replay.final_state.ship.placements})
    violation = Violation(rule_id='objective.all_loaded', severity='hard',
                          call_index=instance.call_index,
                          message='Requested load containers left ashore', containers=missing)
    report = CheckReport(passed=False, count=replay.count, violations=[violation],
        shut_out=missing, parts={name: PartReport(name=name) for name in
                    ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')})
    # All move replay and engine.peel.inspect behaviour stays native. Only the
    # expensive numerical checker and search are replaced at their existing seams.
    checks = []
    def check(original, proposed):
        checks.append((original, proposed))
        return report
    monkeypatch.setattr(adapter, 'check', check)
    monkeypatch.setattr(adapter, 'wait_for_capacity', lambda: {'admitted': True})
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *a, **kw: (rule, {}))
    monkeypatch.setattr(adapter.engine_planner, 'plan_and_check', lambda *a, **kw:
        (None, {'stages': {'peel_seam': {'relaxed_plan': plan.model_dump(mode='json')}}}))
    result, evidence = adapter.plan_with_rules(instance, [OnePortPerHatch(type='one_port_per_hatch')], budget_s=0)
    assert checks and all(original is instance for original, _ in checks)
    assert evidence['passed'] is False
    if mixed:
        assert result is None and evidence['status'] == 'no_checked_plan'
        assert not evidence['instruction_recheck_passed']
    else:
        assert result is not None and evidence['status'] == 'relaxed_plan'
        assert result.status == 'dead_end' and not result.shippable
        assert evidence['level'] == 3 and evidence['tier'] == 'T2'
        assert evidence['broken_rules'] == ['objective.all_loaded']
        assert evidence['boxes_ashore'] == missing
        assert evidence['instruction_recheck_passed']
        assert evidence['rules'][0]['instruction_recheck'] == {'passed': True, 'violations': []}
