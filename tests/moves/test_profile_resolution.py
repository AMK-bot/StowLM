"""Review regressions: invalid later profiles fail closed at move boundaries."""
import pytest

from stowage.model import Instance, MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.moves import Load
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state, project_future_moves
from stowage.rules.catalogue import rule_freeze_v1


def _invalid_profile(archetype, *, aboard):
    payload = build_instance().model_dump()
    box = next(box for box in payload['containers']
               if box['length_ft'] == 20 and box['pod'] == payload['rotation']['calls'][1]['locode'])
    box['dg'] = None
    payload['containers'] = [box]
    payload['rotation']['calls'][1]['archetype'] = archetype
    payload['discharge_list'] = []
    payload['load_list'] = [] if aboard else [box['id']]
    payload['yard']['stacks'] = [] if aboard else [
        dict(key='B-001-01', length_ft=20, boxes=[box['id']])]
    payload['onboard']['placements'] = [dict(slot='0010182', container_id=box['id'])] if aboard else []
    return Instance.model_validate(payload), box['id']


def _diagnostic(violation, *, reason, cid, move_index):
    assert violation.rule_id == 'port.call.profile_resolution'
    assert violation.severity == 'hard'
    assert violation.call_index == 1
    assert violation.move_index == move_index
    assert violation.containers == [cid]
    assert 'THBKK' in violation.message and reason in violation.message


@pytest.mark.parametrize('archetype,reason', [
    ('laem_chabang_like', 'LOCODE'), ('not_a_port', 'unknown port archetype'),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_load_profile_errors_are_hard_diagnostics(archetype, reason, enabled):
    instance, cid = _invalid_profile(archetype, aboard=False)
    move = Load(call_index=0, container_id=cid, src='B-001-01-01', dst='0010182')
    plan = MovePlan(id='invalid.profile.load', instance_id=instance.id, call_index=0, moves=[move])
    before = instance.model_dump_json()
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                apply_move(instance, initial_state(instance), move, move_index=7)
            _diagnostic(caught.value.violation, reason=reason, cid=cid, move_index=7)
            result = apply_plan(instance, plan)
            assert not result.legal and result.count.loads == 0
            _diagnostic(result.violations[0], reason=reason, cid=cid, move_index=0)
        else:
            assert apply_plan(instance, plan).legal
    assert instance.model_dump_json() == before


@pytest.mark.parametrize('archetype,reason', [
    ('laem_chabang_like', 'LOCODE'), ('not_a_port', 'unknown port archetype'),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_projection_profile_errors_are_hard_diagnostics(archetype, reason, enabled):
    instance, cid = _invalid_profile(archetype, aboard=True)
    plan = MovePlan(id='invalid.profile.future', instance_id=instance.id, call_index=0)
    with rule_freeze_v1(enabled=enabled):
        if enabled:
            with pytest.raises(IllegalMoveError) as caught:
                project_future_moves(instance, instance.onboard, from_call=0)
            _diagnostic(caught.value.violation, reason=reason, cid=cid, move_index=None)
            result = apply_plan(instance, plan)
            assert not result.legal and result.count.future == []
            _diagnostic(result.violations[0], reason=reason, cid=cid, move_index=None)
        else:
            assert apply_plan(instance, plan).legal


def test_crane_profile_resolution_errors_are_also_translated(monkeypatch):
    instance, cid = _invalid_profile(None, aboard=True)
    def unavailable(*args, **kwargs):
        raise ValueError('crane profile unavailable')
    monkeypatch.setattr('stowage.moves._projection.crane_height_at_call', unavailable)
    with rule_freeze_v1():
        with pytest.raises(IllegalMoveError) as caught:
            project_future_moves(instance, instance.onboard, from_call=0)
    _diagnostic(caught.value.violation, reason='crane profile unavailable', cid=cid, move_index=None)
