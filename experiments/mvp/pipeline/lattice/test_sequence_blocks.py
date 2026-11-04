import json

import pytest

from stowage.model.moves import HatchCoverOff, HatchCoverOn, GateOut, Load, Relocate
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from . import sequence_blocks
from .test_sequence import example, repair_case


def test_bounded_permutation_preserves_legal_block_world_and_lifts():
    instance, plan = example()
    candidates = sequence_blocks.block_candidates(instance, plan, 1)
    assert candidates
    for candidate, proof in candidates:
        assert proof['every_move_public_replay_legal']
        assert proof['normalized_reached_world_equal']
        assert sorted(proof['permutation']) == [0, 1]
        assert apply_plan(instance, candidate).legal
        assert apply_plan(instance, candidate).count == apply_plan(instance, plan).count


def test_block_stops_at_unapproved_action():
    instance, plan = example()
    boundary = GateOut(call_index=0, container_id=instance.containers[0].id, src='A-001-01-01')
    plan = plan.model_copy(update={'moves': [*plan.moves, boundary]})
    candidates = sequence_blocks.block_candidates(instance, plan, 1)
    assert candidates
    assert all(candidate.moves[-1] == boundary and proof['end_move_index'] == 1 for candidate, proof in candidates)


@pytest.mark.parametrize('failed_step', [1, 3])
def test_cover_permutations_replay_legally_preserve_cover_state_and_lifts(failed_step, monkeypatch):
    instance, plan = example()
    first, second = plan.moves
    hold_load = Load(call_index=0, container_id=first.container_id, src=first.src, dst='0010102')
    off, on = HatchCoverOff(call_index=0, cover_id='HC-02-1'), HatchCoverOn(call_index=0, cover_id='HC-02-1')
    plan = plan.model_copy(update={'moves': [off, hold_load, on, second]})
    original = apply_plan(instance, plan)
    assert original.legal and not original.final_state.ship.covers_off
    rejected = []
    def checked(instance, state, move, **kwargs):
        try:
            return apply_move(instance, state, move, **kwargs)
        except IllegalMoveError:
            rejected.append(move.kind)
            raise
    monkeypatch.setattr(sequence_blocks, 'apply_move', checked)
    candidates = sequence_blocks.block_candidates(instance, plan, failed_step, window=8, lookback=0)
    assert candidates
    for candidate, proof in candidates:
        report = apply_plan(instance, candidate)
        assert report.legal and report.count == original.count
        assert sequence_blocks.normalized_world(report.final_state) == sequence_blocks.normalized_world(original.final_state)
        assert report.final_state.ship.covers_off == {}
        assert proof['hatch_cover_state_in_world_equality']
        assert set(proof['allowed_move_kinds']) == {'load','relocate','hatch_cover_off','hatch_cover_on'}
        assert sorted(proof['permutation']) == list(range(len(proof['original_block'])))
        assert proof['move_multiset_unchanged']
    if failed_step == 1:
        assert 'hatch_cover_on' in rejected  # The cover cannot go on before it is taken off.
    else:
        assert all(candidate.moves[:2] == plan.moves[:2] for candidate, _ in candidates)


def test_cover_locations_are_part_of_exact_world_equality_and_not_cargo_proxy():
    instance, _ = example()
    closed = initial_state(instance)
    opened = apply_move(instance, closed, HatchCoverOff(call_index=0, cover_id='HC-02-1'))
    assert sequence_blocks.normalized_world(closed) != sequence_blocks.normalized_world(opened)
    assert sequence_blocks.cargo_moment(instance, closed) == sequence_blocks.cargo_moment(instance, opened)


def test_neutral_yard_permutations_share_a_slot_and_alternative_load_order_survives():
    instance,plan=example()
    first,second=plan.moves
    box,dg=instance.containers[:2]
    out=Relocate(call_index=0,container_id=box.id,src='A-001-01-01',dst='A-005-01-01')
    back=Relocate(call_index=0,container_id=box.id,src='A-005-01-01',dst='A-001-01-01')
    dg_out=Relocate(call_index=0,container_id=dg.id,src='B-001-01-01',dst='B-002-01-01')
    dg_back=Relocate(call_index=0,container_id=dg.id,src='B-002-01-01',dst='B-001-01-01')
    plan=plan.model_copy(update={'moves':[first,out,dg_out,back,dg_back,second]})
    original=apply_plan(instance,plan)
    assert original.legal
    candidates=sequence_blocks.block_candidates(instance,plan,1)
    paths=[proof['candidate_physical_trajectory_sha256'] for _,proof in candidates]
    assert len(paths)==len(set(paths))==2
    orders={tuple(move.container_id for move in candidate.moves if move.kind=='load') for candidate,_ in candidates}
    assert orders=={(first.container_id,second.container_id),(second.container_id,first.container_id)}
    for candidate,proof in candidates:
        replay=apply_plan(instance,candidate)
        assert replay.legal and replay.count==original.count
        assert sequence_blocks.normalized_world(replay.final_state)==sequence_blocks.normalized_world(original.final_state)
        assert proof['physical_event_count']==2 and proof['diversity_policy']==sequence_blocks.DIVERSITY_POLICY


def test_yard_relocation_cannot_be_merged_as_neutral_if_it_changes_ship(monkeypatch):
    instance,plan=example()
    box=instance.containers[0]
    relocation=Relocate(call_index=0,container_id=box.id,src='A-001-01-01',dst='A-005-01-01')
    plan=plan.model_copy(update={'moves':[relocation,*plan.moves]})
    def altered(instance,state,move,**kwargs):
        reached=apply_move(instance,state,move,**kwargs)
        if move.kind=='relocate':
            masses=dict(reached.ship.tank_masses_kg)
            masses['WB-P']=masses.get('WB-P',0)+1
            reached=reached.model_copy(update={'ship':reached.ship.model_copy(update={'tank_masses_kg':masses})})
        return reached
    monkeypatch.setattr(sequence_blocks,'apply_move',altered)
    assert sequence_blocks.block_candidates(instance,plan,1)==[]


def test_cover_destinations_remain_distinct_physical_events():
    instance,plan=example()
    quay=HatchCoverOff(call_index=0,cover_id='HC-02-1',dst='quay')
    staged=HatchCoverOff(call_index=0,cover_id='HC-02-1',dst='HC-02-2')
    on=HatchCoverOn(call_index=0,cover_id='HC-02-1')
    plan=plan.model_copy(update={'moves':[quay,on,staged,on]})
    original=apply_plan(instance,plan)
    assert original.legal
    candidates=sequence_blocks.block_candidates(instance,plan,1)
    assert candidates
    assert any(candidate.moves[0]==staged for candidate,_ in candidates)
    assert len({proof['candidate_physical_trajectory_sha256'] for _,proof in candidates})==len(candidates)
    assert all(apply_plan(instance,candidate).legal for candidate,_ in candidates)


def test_cover_occupied_by_deck_cargo_cannot_be_lifted_by_permutation():
    instance, plan = example()
    illegal = plan.model_copy(update={'moves': [plan.moves[0], HatchCoverOff(call_index=0, cover_id='HC-02-1'), plan.moves[1]]})
    assert sequence_blocks.block_candidates(instance, illegal, 2) == []


def test_lookback_can_move_a_cover_before_a_preceding_independent_load():
    instance, plan = example()
    first, second = plan.moves
    off = HatchCoverOff(call_index=0, cover_id='HC-02-2')
    on = HatchCoverOn(call_index=0, cover_id='HC-02-2')
    plan = plan.model_copy(update={'moves': [first, off, on, second]})
    original = apply_plan(instance, plan)
    assert original.legal
    assert sequence_blocks.block_candidates(instance, plan, 2, lookback=0) == []
    candidates = sequence_blocks.block_candidates(instance, plan, 2)
    assert candidates and any(candidate.moves.index(off) < candidate.moves.index(first) for candidate,_ in candidates)
    for candidate, proof in candidates:
        replay = apply_plan(instance, candidate)
        assert replay.legal and replay.count == original.count
        assert sequence_blocks.normalized_world(replay.final_state) == sequence_blocks.normalized_world(original.final_state)
        assert proof['start_move_index'] == 0 and proof['lookback'] == 4 and proof['first_failed_step'] == 2


def test_default_window_reaches_balancing_load_beyond_eight_moves():
    instance, plan = example()
    first, second = plan.moves
    box = instance.containers[0]
    out = Relocate(call_index=0, container_id=box.id, src='A-001-01-01', dst='A-005-01-01')
    back = Relocate(call_index=0, container_id=box.id, src='A-005-01-01', dst='A-001-01-01')
    plan = plan.model_copy(update={'moves': [first, *([out,back]*6), second]})
    original = apply_plan(instance, plan)
    assert original.legal
    candidates = sequence_blocks.block_candidates(instance, plan, 1)
    assert candidates
    for candidate, proof in candidates:
        assert proof['end_move_index'] == 13 and len(proof['original_block']) == 14
        replay = apply_plan(instance, candidate)
        assert replay.legal and replay.count == original.count
        assert sequence_blocks.normalized_world(replay.final_state) == sequence_blocks.normalized_world(original.final_state)
    assert all(proof['end_move_index'] == 7 for _,proof in
               sequence_blocks.block_candidates(instance, plan, 1, window=8, lookback=0))


def test_lookback_rechecks_previously_green_changed_prefix_and_rejects_it(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    from .test_sequence import frame
    instance, plan, _, epoch, directory = repair_case
    first, second = plan.moves
    plan = plan.model_copy(update={'moves': [first, HatchCoverOff(call_index=0, cover_id='HC-02-2'),
        HatchCoverOn(call_index=0, cover_id='HC-02-2'), second]})
    envelope = dict(**stage_physics.input_digests(instance, plan), physics_epoch=epoch,
        frames=[frame(0),frame(1),frame(2,False)])
    measured=[]
    monkeypatch.setattr(stage_physics, 'compute_frame', lambda i,p,k: measured.append(k) or frame(k,k != 1))
    monkeypatch.setattr(gates, 'gate_plan', lambda *a,**kw: pytest.fail('failed changed prefix reached release'))
    assert sequence_blocks.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None
    assert measured and set(measured) == {1}
    metadata=json.loads((directory/'original-source.json').read_text())
    assert metadata['trial_only_blocks']==[]
    assert all(attempt['status']=='native_trial_frame_rejected' and 1 in attempt['trial_failed_steps']
               for attempt in metadata['attempts'])


@pytest.mark.parametrize('options', [dict(window=33), dict(lookback=5), dict(lookback=-1), dict(beam_width=5), dict(max_candidates=13)])
def test_search_bounds_cannot_silently_expand(options):
    instance, plan = example()
    with pytest.raises(ValueError):
        sequence_blocks.block_candidates(instance, plan, 1, **options)


def test_passing_trial_requires_fresh_shared_release_gate(repair_case, monkeypatch):
    from . import gates
    instance, plan, envelope, epoch, directory = repair_case
    calls = []
    def gate(i, p, rules, **kwargs):
        calls.append(p)
        assert kwargs['frame_checkpoint_path'].name.endswith('.native-frames.json')
        return dict(passed=True, checker=dict(count=apply_plan(i, p).count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', gate)
    repaired, audit = sequence_blocks.repair_candidate(instance, plan, envelope, epoch,
        checkpoint_dir=directory, profile={'name': 'standard'})
    assert len(calls) == 1
    assert audit['sequence_repair']['producer'] == 'lattice.sequence_blocks'
    assert audit['sequence_repair']['original_profile'] == {'name': 'standard'}
    assert audit['sequence_repair']['native_engine_output'] is False
    assert repaired.moves != plan.moves


def test_green_trial_does_not_override_failed_shared_gate(repair_case, monkeypatch):
    from . import gates
    instance, plan, envelope, epoch, directory = repair_case
    monkeypatch.setattr(gates, 'gate_plan', lambda i, p, r, **kw: dict(passed=False,
        checker=dict(count=apply_plan(i, p).count.model_dump(mode='json'))))
    assert sequence_blocks.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None


@pytest.mark.parametrize('candidate_cap, expected_release', [(2, True), (1, False)])
def test_two_separated_bad_blocks_compose_trial_only_before_one_fresh_gate(repair_case, monkeypatch, candidate_cap, expected_release):
    from app import stage_physics
    from . import gates
    from .test_sequence import frame
    instance, plan, _, epoch, directory = repair_case
    box = instance.containers[0]
    out = Relocate(call_index=0, container_id=box.id, src='A-001-01-01', dst='A-005-01-01')
    back = Relocate(call_index=0, container_id=box.id, src='A-005-01-01', dst='A-001-01-01')
    plan = plan.model_copy(update={'moves': [HatchCoverOff(call_index=0,cover_id='HC-02-1'),
        HatchCoverOff(call_index=0,cover_id='HC-02-2'),HatchCoverOn(call_index=0,cover_id='HC-02-1'),
        HatchCoverOn(call_index=0,cover_id='HC-02-2'),out,back,out,back,*plan.moves]})
    original = apply_plan(instance, plan)
    assert original.legal
    envelope = dict(**stage_physics.input_digests(instance, plan), physics_epoch=epoch,
                    frames=[frame(0), frame(1, False)])
    measured, releases = [], []
    def native(instance, candidate, step):
        measured.append((candidate.id, step))
        bad = (step == 1 and candidate.moves[0] == plan.moves[0] or
               step == 9 and candidate.moves[8] == plan.moves[8])
        return frame(step, not bad)
    monkeypatch.setattr(stage_physics, 'compute_frame', native)
    def fresh_gate(instance, candidate, rules, **kwargs):
        releases.append(candidate)
        assert not {'physics','frames','known_frames'} & set(kwargs)
        replay = apply_plan(instance, candidate)
        assert replay.legal and replay.count == original.count
        assert sequence_blocks.normalized_world(replay.final_state) == sequence_blocks.normalized_world(original.final_state)
        return dict(passed=True, checker=dict(count=replay.count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', fresh_gate)
    result = sequence_blocks.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory,
        window=2, lookback=0, max_candidates=candidate_cap)
    assert len(releases) == int(expected_release)
    committed = json.loads((directory/'block-1.trial-frames.json').read_text())
    assert committed['release_certificate'] is False
    assert committed['plan_sha256'] != stage_physics.input_digests(instance, plan)['plan_sha256']
    if not expected_release:
        assert result is None
        history = json.loads((directory/'original-source.json').read_text())
        assert history['total_candidates'] == 1 and history['status'] == 'bounded_trial_exhausted_not_impossible'
        return
    candidate, gate = result
    metadata = gate['sequence_repair']
    assert metadata['original_plan_sha256'] == stage_physics.input_digests(instance, plan)['plan_sha256']
    assert metadata['total_candidates'] == metadata['composed_blocks'] == 2
    assert metadata['attempts'][0]['status'] == 'committed_trial_only_next_failure_invariant'
    assert metadata['attempts'][0]['next_failed_step'] == 9
    assert metadata['attempts'][1]['input_plan_sha256'] == metadata['attempts'][0]['candidate_plan_sha256']
    assert len(metadata['trial_only_blocks']) == 1 and metadata['bounds']['max_blocks'] == 8
    assert candidate.moves[:2] == list(reversed(plan.moves[:2]))
    assert candidate.moves[8:] == list(reversed(plan.moves[8:]))
    assert sum(step == 1 for _,step in measured) == 1  # Certified unchanged prefix is trial guidance only.


def test_failure_inside_block_is_rejected_without_trial_commit_or_release(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    from .test_sequence import frame
    instance, plan, envelope, epoch, directory = repair_case
    monkeypatch.setattr(stage_physics, 'compute_frame', lambda i,p,k: frame(k, k != 1))
    monkeypatch.setattr(gates, 'gate_plan', lambda *a, **kw: pytest.fail('inside-block failure reached release'))
    assert sequence_blocks.repair_candidate(instance, plan, envelope, epoch, checkpoint_dir=directory) is None
    metadata = json.loads((directory/'original-source.json').read_text())
    assert metadata['trial_only_blocks'] == []
    assert metadata['attempts'][0]['status'] == 'native_trial_frame_rejected'
    assert not list(directory.glob('block-*.trial-frames.json'))


def adaptive_example(repair_case, monkeypatch):
    from app import stage_physics
    from .test_sequence import frame
    instance, plan, _, epoch, directory = repair_case
    first, second = plan.moves
    box = instance.containers[0]
    out = Relocate(call_index=0, container_id=box.id, src='A-001-01-01', dst='A-005-01-01')
    back = Relocate(call_index=0, container_id=box.id, src='A-005-01-01', dst='A-001-01-01')
    plan = plan.model_copy(update={'moves': [first,*([out,back]*6),second]})
    assert apply_plan(instance, plan).legal
    envelope = dict(**stage_physics.input_digests(instance, plan), physics_epoch=epoch,
                    frames=[frame(0),frame(1,False)])
    widths, by_plan = [], {}
    original = sequence_blocks.block_candidates
    def candidates(*args, **kwargs):
        width = kwargs['window']; widths.append(width)
        result = original(*args, **kwargs)
        for candidate, proof in result:
            by_plan[sequence_blocks.sha256_json(candidate.model_dump(mode='json'))] = width
            assert proof['window']==width
            replay = apply_plan(instance,candidate)
            assert replay.legal and replay.count==apply_plan(instance,plan).count
        return result
    monkeypatch.setattr(sequence_blocks,'block_candidates',candidates)
    def native(i,p,k):
        width=by_plan[sequence_blocks.sha256_json(p.model_dump(mode='json'))]
        return frame(k, not (width==8 and k==1))
    monkeypatch.setattr(stage_physics,'compute_frame',native)
    return instance,plan,envelope,epoch,directory,widths,by_plan


@pytest.mark.parametrize('candidate_cap, release', [(12,True),(1,False)])
def test_adaptive_old_eight_move_caller_widens_under_global_candidate_cap(repair_case, monkeypatch, candidate_cap, release):
    from . import gates
    instance,plan,envelope,epoch,directory,widths,_=adaptive_example(repair_case,monkeypatch)
    calls=[]
    def gate(i,p,rules,**kwargs):
        calls.append(p)
        assert not {'physics','frames'} & set(kwargs)
        return dict(passed=True,checker=dict(count=apply_plan(i,p).count.model_dump(mode='json')))
    monkeypatch.setattr(gates,'gate_plan',gate)
    result=sequence_blocks.repair_candidate(instance,plan,envelope,epoch,checkpoint_dir=directory,
        window=8,lookback=0,max_candidates=candidate_cap)
    assert len(calls)==int(release)
    if release:
        metadata=result[1]['sequence_repair']
        assert widths==[8,16]
        assert metadata['bounds']['requested_initial_window']==8 and metadata['bounds']['adaptive_max_window']==32
        assert metadata['total_candidates']<=12
        assert [stage['window'] for stage in metadata['width_stages']]==[8,16]
    else:
        assert result is None and widths==[8]
        assert json.loads((directory/'original-source.json').read_text())['total_candidates']==1


def test_adaptive_widths_share_one_trial_wall_allowance(repair_case, monkeypatch):
    from app import stage_physics
    from . import gates
    from .test_sequence import frame
    instance,plan,envelope,epoch,directory,widths,by_plan=adaptive_example(repair_case,monkeypatch)
    clock=[0.]
    monkeypatch.setattr(sequence_blocks.time,'monotonic',lambda:clock[0])
    def native(i,p,k):
        width=by_plan[sequence_blocks.sha256_json(p.model_dump(mode='json'))]
        clock[0]+=20 if width==8 else 180
        return frame(k,not (width==8 and k==1))
    monkeypatch.setattr(stage_physics,'compute_frame',native)
    monkeypatch.setattr(gates,'gate_plan',lambda *a,**kw:pytest.fail('global trial budget was reset during widening'))
    assert sequence_blocks.repair_candidate(instance,plan,envelope,epoch,checkpoint_dir=directory,
        window=8,lookback=0,budget_s=180) is None
    assert widths==[8,16]
    records=[json.loads(p.read_text()) for p in directory.glob('candidate-*.json')
             if not p.name.endswith(('.trial-frames.json','.native-frames.json'))]
    assert any(record['status']=='trial_budget_failed' for record in records)


@pytest.mark.parametrize('policy',[dict(resume=True),dict(adaptive_max_window=8)])
def test_resume_or_explicit_eight_cap_keeps_historical_candidate_width(repair_case, monkeypatch, policy):
    from . import gates
    instance,plan,envelope,epoch,directory,widths,_=adaptive_example(repair_case,monkeypatch)
    monkeypatch.setattr(gates,'gate_plan',lambda *a,**kw:pytest.fail('all narrow native trials failed'))
    assert sequence_blocks.repair_candidate(instance,plan,envelope,epoch,checkpoint_dir=directory,
        window=8,lookback=0,**policy) is None
    assert widths==[8]
    metadata=json.loads((directory/'original-source.json').read_text())
    assert metadata['bounds']['adaptive_max_window']==8


@pytest.mark.parametrize('mismatch', [None, 'plan_sha256', 'physics_epoch'])
def test_resume_uses_only_exact_candidate_frames_and_preserves_budget_failure(repair_case, monkeypatch, mismatch):
    from app import stage_physics
    from . import gates
    from .test_sequence import frame
    instance, plan, envelope, epoch, directory = repair_case
    candidate = sequence_blocks.block_candidates(instance, plan, 1)[0][0]
    cached = dict(**stage_physics.input_digests(instance, candidate), physics_epoch=epoch,
                  frames=[frame(0), frame(1), frame(2)])
    if mismatch:
        cached[mismatch] = 'wrong'
    (directory/'candidate-1.trial-frames.json').write_text(json.dumps(cached))
    (directory/'candidate-1.json').write_text(json.dumps(dict(status='trial_budget_failed')))
    (directory/'original-source.json').write_text(json.dumps(dict(bounds={'budget_s': 180}, producer_source_sha256='previous')))
    measured, release_calls = [], []
    monkeypatch.setattr(stage_physics, 'compute_frame', lambda i, p, k: measured.append(k) or frame(k))
    def gate(i, p, r, **kw):
        release_calls.append(kw)
        return dict(passed=True, checker=dict(count=apply_plan(i, p).count.model_dump(mode='json')))
    monkeypatch.setattr(gates, 'gate_plan', gate)
    repaired, audit = sequence_blocks.repair_candidate(instance, plan, envelope, epoch,
        checkpoint_dir=directory, budget_s=600, resume=True)
    assert measured == ([] if mismatch is None else [1, 2])
    assert len(release_calls) == 1 and 'physics' not in release_calls[0]
    metadata = audit['sequence_repair']
    assert metadata['bounds']['budget_s'] == 600
    assert metadata['resume_provenance']['previous_bounds'] == {'budget_s': 180}
    archive = __import__('pathlib').Path(metadata['resume_provenance']['archive'])
    assert json.loads((archive/'candidate-1.json').read_text())['status'] == 'trial_budget_failed'
    assert metadata['attempts'][0]['resume_checkpoint_accepted'] is (mismatch is None)
