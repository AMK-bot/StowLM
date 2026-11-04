"""Offline scheduler/admission and stored-release boundary regressions."""
from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from . import batch
from .instructions import make_spec, sentence


@pytest.mark.parametrize('row', [dict(mask=255, load1=45, t=1000), dict(mask='0xfff', load1=0, t=999)])
def test_capacity_exact_boundaries(tmp_path, row):
    path = tmp_path/'state.json'
    path.write_text(json.dumps(row))
    assert batch.capacity_reading(path, now=1000)['enabled_cores'] >= 8


@pytest.mark.parametrize('row', [dict(mask=127, load1=1, t=1000), dict(mask=4095, load1=45.01, t=1000),
                                dict(mask=4095, load1=1, t=880), dict(mask=4095, load1=1, t=1001),
                                dict(mask=-1, load1=1, t=1000), {}])
def test_capacity_rejects_unavailable_without_raising(tmp_path, row):
    path = tmp_path/'state.json'
    path.write_text(json.dumps(row))
    assert batch.capacity_reading(path, now=1000) is None


def test_admission_waits_for_missing_then_fresh_telemetry(tmp_path):
    path = tmp_path/'state.json'
    def refresh(_):
        path.write_text(json.dumps(dict(mask=4095, load1=20, t=batch.time.time())))
    assert batch.admit(path, poll_s=0, sleep=refresh)['load1'] == 20


@pytest.mark.parametrize('reason, expected', [
    ('the load list needs 20 TEU of cells but only 0 remain open after all instructions', 'proved_refusal'),
    ('strict physical-hatch allocation: INFEASIBLE', 'proved_refusal'),
    ('no instruction-allowed physical footprint: ABCU1234567', 'proved_refusal'),
    ('strict physical-hatch allocation: UNKNOWN', 'budget_failed'),
    ('Engine exceeded its900 second budget', 'budget_failed'),
    ('no original-instance checked plan obeying every confirmed instruction was found', 'budget_failed'),
])
def test_only_necessary_condition_proofs_are_impossible(reason, expected):
    assert batch.refusal_status({'reason': reason}) == expected


def test_rule_captions_are_not_keys():
    body = dict(type='max_tiers', bay=None, zone='deck', max_tiers=2)
    spec = make_spec('fallback', [body], base_violations=['old placement exceeds cap'])
    assert spec['confirmation_source'] == 'batch'
    assert not spec['base_already_obeys']
    assert spec['rules'][0]['constraint'] == body
    assert 'every bay' in sentence(body)


def test_binding_fingerprint_tracks_runtime_files_only(monkeypatch, tmp_path):
    lattice = tmp_path/'pipeline'/'lattice'
    binding = lattice.parent/'binding'
    lattice.mkdir(parents=True)
    binding.mkdir()
    for name in ('__init__.py', 'adapter.py', 'admission.py', 'allocation.py', 'hooks.py', 'runtime.py'):
        (binding/name).write_text('runtime')
    (binding/'measure.py').write_text('measurement launcher')
    (binding/'live_campaign.py').write_text('measurement campaign orchestrator')
    (binding/'measurement_identity.py').write_text('measurement-only identity')
    (binding/'test_adapter.py').write_text('test only')
    monkeypatch.setattr(batch, 'HERE', lattice)
    initial = batch.binding_fingerprint()
    (binding/'measure.py').write_text('new measurement launcher')
    (binding/'live_campaign.py').write_text('new measurement campaign orchestrator')
    (binding/'measurement_identity.py').write_text('new measurement-only identity')
    (binding/'test_adapter.py').write_text('new tests')
    assert batch.binding_fingerprint() == initial
    (binding/'identity.py').write_text('new runtime identity')
    assert 'identity.py' in batch.binding_source_files()
    assert batch.binding_fingerprint() != initial
    identity = batch.binding_fingerprint()
    (binding/'identity.py').write_text('changed runtime identity')
    assert batch.binding_fingerprint() != identity
    (binding/'provenance.py').write_text('runtime provenance')
    provenance = batch.binding_fingerprint()
    assert 'provenance.py' in batch.binding_source_files()
    (binding/'provenance.py').write_text('changed runtime provenance')
    assert batch.binding_fingerprint() != provenance


def test_campaign_control_blocks_legacy_batch_without_a_worker(monkeypatch, tmp_path):
    control = tmp_path/'control.json'
    control.write_text(json.dumps(dict(active_campaign='current-v2')))
    monkeypatch.setattr(batch, 'ProcessPoolExecutor', lambda **_: pytest.fail('superseded batch created workers'))
    assert batch.run_batch({}, control_path=control)['status'] == 'superseded'
    assert batch.run_batch({'campaign_id': 'legacy'}, control_path=control)['active_campaign'] == 'current-v2'
    assert batch.campaign_status({'campaign_id': 'current-v2'}, control_path=control) is None
    assert batch.campaign_status({}, control_path=tmp_path/'missing') is None
    control.write_text('bad JSON')
    assert batch.run_batch({}, control_path=control)['status'] == 'campaign_control_unavailable'


def test_superseded_queued_job_returns_before_reading_map(tmp_path):
    control = tmp_path/'control.json'
    control.write_text(json.dumps(dict(active_campaign='current-v2')))
    result = batch.compute(dict(campaign_id='legacy', campaign_control_path=str(control)))
    assert result['status'] == 'superseded'


def test_standard_pool_dispatch_precedes_binding_imports_but_follows_campaign_guard(monkeypatch,tmp_path):
    import sys
    calls=[]
    monkeypatch.setitem(sys.modules,batch.__package__+'.standard_plan_pool',
        SimpleNamespace(compute=lambda job:calls.append(job) or dict(status='checked_plan')))
    monkeypatch.setattr(batch,'binding_source_files',lambda:pytest.fail('pool route touched regular planner'))
    control=tmp_path/'control.json';control.write_text(json.dumps(dict(active_campaign='current-v6')))
    job=dict(campaign_id='current-v6',campaign_control_path=str(control),
             spec=dict(standard_plan_pool_donor_path='store/record.json'))
    assert batch.compute(job)==dict(status='checked_plan') and calls==[job]
    control.write_text(json.dumps(dict(active_campaign='newer')))
    assert batch.compute(job)['status']=='superseded' and len(calls)==1


def test_binding_change_requeues_behind_other_rules_but_budget_failure_does_not(monkeypatch, tmp_path):
    from concurrent.futures import Future
    calls = []
    class Pool:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def submit(self, function, job):
            identifier = job['spec']['id']
            status = ('rerun_required' if identifier == 'changed' and 'changed' not in calls else
                      'budget_failed' if identifier == 'timeout' else 'checked_plan')
            calls.append(identifier)
            future = Future()
            future.set_result(dict(status=status))
            return future
    monkeypatch.setattr(batch, 'ProcessPoolExecutor', lambda **_: Pool())
    monkeypatch.setattr(batch, 'capacity_reading', lambda: dict(load1=20))
    manifest = dict(instance_path='fake', base_plan=None, campaign_id='offline-test',
                    instructions=[dict(id=name) for name in ('changed', 'other', 'timeout')])
    batch.run_batch(manifest, workers=1, control_path=tmp_path/'missing-control.json')
    assert calls == ['changed', 'other', 'timeout', 'changed']


def test_live_camera_requests_follow_first_four_lattice_instructions():
    stored = [dict(id=f'stored-{index}') for index in range(25)]
    live = [dict(id=f'live-{index}') for index in range(3)]
    manifest = dict(instructions=stored, off_lattice=live)
    assert batch.ordered_requests(manifest) == stored[:4]+live+stored[4:]
    assert batch.ordered_requests(manifest, include_live=False) == stored
    assert manifest['instructions'] == stored


def test_resume_skips_are_explicit_and_preserve_full_manifest():
    stored = [dict(id=name) for name in ('max01', 'close03', 'hatch', 'draining', 'failed')]
    manifest = dict(instructions=stored, resume_skip_ids=['draining', 'failed'])
    assert [row['id'] for row in batch.ordered_requests(manifest)] == ['max01', 'close03', 'hatch']
    assert manifest['instructions'] == stored and len(stored) == 5
    assert batch.ordered_requests(dict(instructions=stored)) == stored


@pytest.mark.parametrize('row, complete', [
    ({'passed': True, 'plan': {'id': 'pending-gauges'}}, False),
    ({'passed': False, 'early_gauge_rejection': {'step': 0}}, False),
    ({'passed': False, 'early_gauge_rejection': {}}, False),
    ({'passed': False, 'plan': {'id': 'rejected'}, 'early_gauge_rejection': True}, False),
    ({'passed': False}, True),
    ({'passed': False, 'plan': {'id': 'stale'}, 'status': 'rerun_required'}, False),
    ({'passed': False, 'plan': {'id': 'stale'}, 'status': 'rerun_required', 'wall_s': 900,
      'computed_utc': '2026-11-02T02:00:00+00:00'}, True),
    ({'passed': False, 'plan': {'id': 'failed'}, 'screening_error': 'owned sweep ended'}, True),
    ({'passed': True, 'gate': {'passed': True}}, True),
    ({'passed': False, 'gauge': {'passed': False}, 'early_gauge_rejection': True}, True),
    ({'status': 'budget_failed', 'worker_released': True, 'release_utc': '2026-11-02T03:00:00+00:00'}, True),
    ({'status': 'error', 'worker_released': True, 'release_utc': '2026-11-02T03:00:00+00:00'}, True),
    ({'status': 'running', 'worker_released': True, 'release_utc': '2026-11-02T03:00:00+00:00'}, False),
])
def test_screen_reservation_waits_for_real_completion(row, complete):
    assert batch.screen_finished(row) is complete


def test_draining_campaign_retains_keys_until_final_release(tmp_path):
    target = tmp_path/'old-worker-final.json'
    queue = [dict(id='old-owned'), dict(id='fresh')]
    manifest = dict(reserved_request_paths={'old-owned': str(target)})
    assert batch.next_request_index(queue, manifest) == 1
    batch.write_json(target, dict(status='running'))
    assert batch.next_request_index(queue[:1], manifest) is None
    batch.write_json(target, dict(status='budget_failed', worker_released=True,
                                 release_utc='2026-11-02T03:00:00+00:00'))
    assert batch.next_request_index(queue, manifest) == 0
    assert batch.worker_slots(4, [str(target)]) == 4


def test_reserved_screens_limit_then_release_cpu_slots(tmp_path):
    first, second = tmp_path/'first.json', tmp_path/'second.json'
    paths = [str(first), str(second)]
    assert batch.worker_slots(4, paths) == 2
    second.write_text('corrupt')
    assert batch.worker_slots(6, paths, allow_six=True) == 2
    first.write_text(json.dumps({'gate': {'passed': True}}))
    assert batch.worker_slots(4, paths) == 3
    second.write_text(json.dumps({'passed': False}))
    assert batch.worker_slots(6, paths, allow_six=True) == 4
    assert batch.reserved_screen_count([str(first), str(first)]) == 0
    assert batch.worker_slots(6, [], allow_six=True) == 6


def test_instruction_selection_diversifies_and_reserves_three_keys(monkeypatch):
    from . import instructions
    from experiments.mvp.reader import pipeline
    from importlib import import_module
    apply = import_module('llm.instructions.apply')
    bays = list(range(1, 15, 2))
    context = dict(bays=bays, max_tiers={'hold': 4, 'deck': 4}, has_accommodation=True,
                   hatch_covers=['h1'], max_tiers_by_bay={str(b): {'hold': 4, 'deck': 4} for b in bays})
    monkeypatch.setattr(instructions, 'context_from_instance', lambda _: context)
    monkeypatch.setattr(apply, '_dg_classes', lambda _: {'3'})
    monkeypatch.setattr(pipeline, 'intrinsic_violations', lambda _, rule, plan:
                        [] if rule.type == 'one_port_per_hatch' else ['base violates'])
    instance = SimpleNamespace(load_list=['box'], containers=[SimpleNamespace(id='box')])
    result = instructions.select_instructions(instance, None)
    assert len(result['instructions']) == 25
    assert {s['rules'][0]['constraint']['type'] for s in result['instructions'][:4]} == {
        'max_tiers', 'dg_keep_away', 'close_bay', 'one_port_per_hatch'}
    assert result['instructions'][0]['backup_shot_6']
    assert result['instructions'][3]['base_already_obeys']
    first_dg = result['instructions'][1]['rules'][0]['constraint']
    assert first_dg['away_from'] == {'kind': 'accommodation'}
    assert first_dg['min_bays'] == 1
    key = lambda row: json.dumps(row['rules'], sort_keys=True)
    stored = {key(row) for row in result['instructions']}
    assert len(result['off_lattice']) == 3
    assert stored.isdisjoint({key(row) for row in result['off_lattice']})


@pytest.fixture
def compute_case(monkeypatch, tmp_path):
    import stowage.io
    from stowage.model import MovePlan
    from experiments.mvp.pipeline.binding import adapter
    from experiments.mvp.pipeline.runner import profiles
    from . import gates
    instance = SimpleNamespace(model_dump=lambda **_: {'id': 'fixture'})
    plan = MovePlan(id='fixture-plan', instance_id='fixture', call_index=0)
    monkeypatch.setattr(stowage.io, 'load_instance', lambda _: instance)
    monkeypatch.setattr(adapter, 'verify_manifest', lambda: dict(passed=True, manifest_sha256='a'*64))
    @dataclass
    class Config:
        test_only: bool = True
    monkeypatch.setattr(profiles, 'get_profile', lambda name: dict(name=name, config=Config(), budget_s=150.,
                        finalize_s=50., seed=0, engine_timeout_s=900.))
    monkeypatch.setattr(batch, 'admit', lambda _: dict(load1=20, enabled_cores=12, waited_s=0))
    # Empty rules need no physical vessel context in this orchestration test.
    monkeypatch.setattr(__import__('experiments.mvp.reader.schema', fromlist=['context_from_instance']),
                        'context_from_instance', lambda _: {})
    monkeypatch.setattr(gates, 'gate_plan', lambda *a, **kw: dict(passed=True, lifts=8, base_lifts=None,
                        lift_delta=None, what_moved=[], base_already_obeys=False))
    job = dict(instance_path='fake', spec=make_spec('base', [], base_violations=[]),
               run_dir=str(tmp_path/'runs'), store_dir=str(tmp_path/'store'), fixtures_dir=str(tmp_path/'fixtures'),
               campaign_id='offline-test', campaign_control_path=str(tmp_path/'test-campaign-control.json'))
    return job, plan


def test_compute_checkpoints_and_resume_skips_solver(compute_case, monkeypatch):
    from experiments.mvp.pipeline.runner import limits
    job, plan = compute_case
    calls = []
    def solve(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(plan=plan), dict(passed=True, status='checked_plan')
    monkeypatch.setattr(limits, 'call_with_limit', solve)
    assert batch.compute(job)['status'] == 'checked_plan'
    assert batch.compute(job)['status'] == 'checked_plan'
    assert len(calls) == 1
    assert len(list(__import__('pathlib').Path(job['store_dir']).glob('*.json'))) == 2
    output = __import__('pathlib').Path(job['run_dir']).parent
    assert json.loads((output/'progress.json').read_text())['stored'] == 1
    table = (output/'RULES.md').read_text()
    assert 'checked_plan' in table
    assert '| Admission load1 |' in table
    assert '20.00' in table
    assert 'Load1 is sampled at admission' in table


def test_approved_source_bridge_cache_hit_preserves_raw_evidence_and_store(compute_case, monkeypatch):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import store
    job, plan = compute_case
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    batch.compute(job)
    checkpoint = next(Path(job['run_dir']).glob('*.json'))
    cached = json.loads(checkpoint.read_text())
    cached.update(binding_source_sha256='approved-legacy-e0', binding_source_after_sha256='approved-legacy-e0',
                  binding_compatibility={'policy': 'reviewed-nonhatch-bridge'})
    batch.write_json(checkpoint, cached)
    raw_before = checkpoint.read_bytes()
    store_before = {p.name: p.read_bytes() for p in Path(job['store_dir']).glob('*') if p.is_file()}
    checked = []
    monkeypatch.setattr(store, '_binding_source_matches', lambda row: checked.append(row['binding_source_sha256']) or True)
    def no_compute(*a, **kw):
        pytest.fail('approved, checked source bridge reran planner/admission')
    monkeypatch.setattr(limits, 'call_with_limit', no_compute)
    monkeypatch.setattr(batch, 'admit', no_compute)
    assert batch.compute(job)['status'] == 'checked_plan'
    assert checked == ['approved-legacy-e0']
    assert checkpoint.read_bytes() == raw_before
    assert {p.name: p.read_bytes() for p in Path(job['store_dir']).glob('*') if p.is_file()} == store_before


@pytest.mark.parametrize('status', ['gate_failed', 'budget_failed', 'error', 'rerun_required'])
def test_failed_checkpoint_is_not_a_cache_hit_even_if_source_is_compatible(compute_case, monkeypatch, status):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import store
    job, plan = compute_case
    calls = []
    def solve(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(plan=plan), dict(passed=True)
    monkeypatch.setattr(limits, 'call_with_limit', solve)
    batch.compute(job)
    checkpoint = next(Path(job['run_dir']).glob('*.json'))
    cached = json.loads(checkpoint.read_text())
    cached['status'] = status
    batch.write_json(checkpoint, cached)
    monkeypatch.setattr(store, '_binding_source_matches', lambda row: True)
    assert batch.compute(job)['status'] == 'checked_plan'
    assert len(calls) == 2


def source_retry(compute_case, monkeypatch):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import store
    job, plan = compute_case
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    batch.compute(job)
    target = next(Path(job['run_dir']).glob('*.json'))
    row = json.loads(target.read_text())
    row.update(status='rerun_required', rerun_required=True,
        reason='Binding source changed during computation or release gates; rerun before storing.',
        binding_source_sha256='approved-legacy-e0', binding_source_after_sha256='approved-transition-b1',
        engine_wall_s=900.12, wall_s=900.12, total_wall_s=1001.25)
    # Historical gauge success is deliberately distinct from the next verdict.
    row['gate'] = dict(passed=True, historical_only=True, plan_sha256=batch.sha256_json(row['plan']))
    batch.write_json(target, row)
    monkeypatch.setattr(store, '_binding_source_matches', lambda _: True)
    monkeypatch.setattr(store, 'binding_compatibility', lambda _: dict(policy='approved-test-bridge'))
    return job, plan, target, row


def test_source_only_retry_reuses_engine_candidate_but_freshly_gates(compute_case, monkeypatch):
    from . import gates
    from experiments.mvp.pipeline.runner import limits
    job, plan, target, original = source_retry(compute_case, monkeypatch)
    raw = target.read_bytes()
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: pytest.fail('source-only retry reran engine'))
    calls = []
    def fresh_gate(instance, candidate, rules, **kwargs):
        calls.append(candidate.model_dump(mode='json'))
        assert candidate == plan
        assert 'physics' not in kwargs and 'frames' not in kwargs
        held = json.loads(target.with_suffix('.running.json').read_text())
        assert 'gate' not in held
        return dict(passed=True, lifts=7, fresh_native_all_frames=True)
    monkeypatch.setattr(gates, 'gate_plan', fresh_gate)
    assert batch.compute(job)['status'] == 'checked_plan'
    row = json.loads(target.read_text())
    assert len(calls) == 1 and row['gate'] == dict(passed=True, lifts=7, fresh_native_all_frames=True)
    from pathlib import Path
    reuse = row['engine_candidate_reuse']
    archive = Path(reuse['archive_path'])
    assert archive.read_bytes() == raw and batch.sha256_file(archive) == reuse['archive_sha256']
    for name in ('computed_utc', 'binding_source_sha256', 'binding_source_after_sha256', 'binding_evidence',
                 'engine_version', 'engine_manifest_sha256', 'engine_wall_s', 'wall_s', 'total_wall_s',
                 'profile', 'admission'):
        assert row[name] == original[name]
    assert row['release_binding_source_sha256'] == batch.binding_fingerprint()
    assert row['release_binding_source_after_sha256'] == batch.binding_fingerprint()
    assert row['planning_origin_binding_source_sha256'] == 'approved-legacy-e0'
    assert reuse['historical_physics_reused'] is False and row['release_wall_s'] >= 0


def test_source_retry_fresh_gauge_failure_cannot_reuse_historical_success(compute_case, monkeypatch):
    from . import gates
    from experiments.mvp.pipeline.runner import limits
    job, _, target, _ = source_retry(compute_case, monkeypatch)
    job['sequence_repair'] = False
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: pytest.fail('reran engine'))
    monkeypatch.setattr(gates, 'gate_plan', lambda *a, **kw: dict(passed=False, gauges={'passed': False}))
    assert batch.compute(job)['status'] == 'gate_failed'
    assert json.loads(target.read_text())['gate']['gauges']['passed'] is False


def test_source_retry_uses_same_admitted_labelled_sequence_repair(compute_case, monkeypatch):
    from . import gates, sequence
    from experiments.mvp.pipeline.runner import limits
    job, original, target, _ = source_retry(compute_case, monkeypatch)
    repaired = original.model_copy(update={'id': original.id+'.lattice-sequence'})
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: pytest.fail('reran engine'))
    admissions = []
    monkeypatch.setattr(batch, 'admit', lambda _: admissions.append(1) or dict(load1=20))
    def reject(*a, frame_checkpoint_path, **kw):
        batch.write_json(frame_checkpoint_path, dict(physics_epoch='fresh-epoch'))
        return dict(passed=False)
    monkeypatch.setattr(gates, 'gate_plan', reject)
    def repair(instance, candidate, frames, epoch, **kwargs):
        assert candidate == original and epoch == 'fresh-epoch'
        return repaired, dict(passed=True, sequence_repair=dict(producer='lattice.sequence', fresh_native_all_frames=True))
    monkeypatch.setattr(sequence, 'repair_candidate', repair)
    assert batch.compute(job)['status'] == 'checked_plan'
    row = json.loads(target.read_text())
    assert len(admissions) == 2 and row['plan_producer'] == 'lattice.sequence'
    assert row['original_binding_plan'] == original.model_dump(mode='json')
    assert row['plan'] == repaired.model_dump(mode='json')


@pytest.mark.parametrize('mutation', ['instance', 'instance_sha256', 'canonical_rules', 'rules', 'key',
    'engine_manifest_sha256', 'profile', 'original_binding_plan', 'source_guard', 'compatibility',
    'release_error', 'reason', 'missing_plan', 'evidence', 'plan_hash', 'unbound_original'])
def test_source_retry_uncertainty_falls_back_to_full_engine(compute_case, monkeypatch, mutation):
    from . import store
    from experiments.mvp.pipeline.runner import limits
    job, plan, target, row = source_retry(compute_case, monkeypatch)
    if mutation == 'source_guard':
        monkeypatch.setattr(store, '_binding_source_matches', lambda _: False)
    elif mutation == 'compatibility':
        monkeypatch.setattr(store, 'binding_compatibility', lambda _: None)
    elif mutation == 'original_binding_plan':
        row['original_binding_plan']['instance_id'] = 'other'
    elif mutation == 'unbound_original':
        row['original_binding_plan']['id'] += '.unbound'
    elif mutation == 'plan_hash':
        row['gate']['plan_sha256'] = 'wrong'
    elif mutation == 'missing_plan':
        row.pop('original_binding_plan'); row.pop('plan')
    elif mutation == 'profile':
        row['profile']['name'] = 'live'
    elif mutation == 'evidence':
        row['binding_evidence'] = dict(passed=False)
    else:
        row[mutation] = {'uncertain': True} if mutation in ('instance', 'release_error') else 'wrong'
    batch.write_json(target, row)
    calls = []
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw:
                        (calls.append(1) or SimpleNamespace(plan=plan), dict(passed=True)))
    assert batch.compute(job)['status'] == 'checked_plan'
    assert calls == [1] and 'engine_candidate_reuse' not in json.loads(target.read_text())


def test_source_retry_excludes_hatch_even_with_official_guard_approval(compute_case, monkeypatch):
    from types import SimpleNamespace
    job, _, _, row = source_retry(compute_case, monkeypatch)
    canonical = [{'constraint': {'type': 'one_port_per_hatch'}}]
    row.update(canonical_rules=canonical, rules=canonical)
    assert batch.reusable_candidate(row, instance=SimpleNamespace(model_dump=lambda **kw: row['instance']),
        canonical=canonical, key=row['key'], profile=row['profile'],
        identity=dict(passed=True, manifest_sha256=row['engine_manifest_sha256']), version=row['engine_version']) is None


def test_campaign_rotation_during_admission_prevents_solve(compute_case, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import limits
    job, _ = compute_case
    control = tmp_path/'control.json'
    control.write_text(json.dumps(dict(active_campaign='current-v2')))
    job.update(campaign_id='current-v2', campaign_control_path=str(control))
    def admitted(_):
        control.write_text(json.dumps(dict(active_campaign='current-v3')))
        return dict(load1=20, enabled_cores=12, waited_s=0)
    monkeypatch.setattr(batch, 'admit', admitted)
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: pytest.fail('superseded job started a solve'))
    assert batch.compute(job)['status'] == 'superseded'


def test_offline_compute_uses_its_own_campaign_control(compute_case, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner import limits
    job, plan = compute_case
    external = tmp_path/'external-control.json'
    external.write_text(json.dumps(dict(active_campaign='current-v2')))
    original = batch.campaign_status
    def check(manifest, *, control_path=None):
        return original(manifest, control_path=external if control_path is None else control_path)
    monkeypatch.setattr(batch, 'campaign_status', check)
    assert check(job)['status'] == 'superseded'
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    assert batch.compute(job)['status'] == 'checked_plan'


def test_timeout_keeps_checked_incumbent_but_regates(compute_case, monkeypatch):
    from experiments.mvp.pipeline.runner import limits
    from . import gates
    job, plan = compute_case
    checked = []
    def timeout(*args, **kwargs):
        kwargs['on_event'](dict(plan=plan.model_dump(mode='json')))
        raise limits.StageTimeout('engine', 900)
    def reject(*args, **kwargs):
        checked.append(1)
        return dict(passed=False)
    monkeypatch.setattr(limits, 'call_with_limit', timeout)
    monkeypatch.setattr(gates, 'gate_plan', reject)
    assert batch.compute(job)['status'] == 'gate_failed'
    assert checked == [1]
    assert not __import__('pathlib').Path(job['store_dir']).exists()


@pytest.mark.parametrize('failure, status', [
    (ValueError('physics sources changed since this process started: restart the app'), 'rerun_required'),
    (RuntimeError('unexpected release failure'), 'error'),
])
def test_gate_exceptions_preserve_engine_plan_clear_running_and_checkpoint_release(compute_case, monkeypatch, failure, status):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import gates
    job, plan = compute_case
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    def fail_gate(*args, **kwargs):
        running = next(Path(job['run_dir']).glob('*.running.json'))
        held = json.loads(running.read_text())
        assert held['status'] == 'release_gate_pending'
        assert held['engine_wall_s'] >= 0
        assert held['binding_evidence']['passed'] is True
        assert held['original_binding_plan'] == plan.model_dump(mode='json')
        raise failure
    monkeypatch.setattr(gates, 'gate_plan', fail_gate)
    assert batch.compute(job)['status'] == status
    assert not list(Path(job['run_dir']).glob('*.running.json'))
    final = json.loads(next(Path(job['run_dir']).glob('*.json')).read_text())
    assert final['worker_released'] is True and final['total_wall_s'] >= 0
    assert final['original_binding_plan'] == plan.model_dump(mode='json')
    assert str(failure) in final['reason']
    assert not Path(job['store_dir']).exists()


def test_scheduler_fallback_writes_canonical_and_error_release_receipts(tmp_path):
    from datetime import datetime, timezone
    run_dir = tmp_path/'runs'
    key = 'a'*64
    batch.write_json(run_dir/(key+'.running.json'), dict(id='failed-rule', key=key,
        campaign_id='test', computed_utc=datetime.now(timezone.utc).isoformat(),
        binding_source_sha256=batch.binding_fingerprint(), original_binding_plan={'id': 'held'}))
    result = batch.checkpoint_worker_error(dict(id='failed-rule'), RuntimeError('worker failed'), run_dir,
                                            campaign_id='test')
    assert result['status'] == 'error' and result['worker_released'] is True
    assert result['total_wall_s'] >= 0
    assert (run_dir/(key+'.json')).exists() and (run_dir/'failed-rule.error.json').exists()
    assert not (run_dir/(key+'.running.json')).exists()
    assert json.loads((tmp_path/'progress.json').read_text())['rows'][0]['id'] == 'failed-rule'
    assert len(json.loads((tmp_path/'progress.json').read_text())['rows']) == 1


def test_gauge_failure_can_store_only_explicitly_labelled_strictly_regated_repair(compute_case, monkeypatch):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import gates, sequence, sequence_blocks
    job, original = compute_case
    repaired = original.model_copy(update={'id': original.id+'.lattice-sequence'})
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=original), dict(passed=True)))
    def reject(i, p, rules, *, frame_checkpoint_path, **kwargs):
        batch.write_json(frame_checkpoint_path, dict(physics_epoch='test-epoch'))
        return dict(passed=False)
    monkeypatch.setattr(gates, 'gate_plan', reject)
    def repair(i, p, frames, epoch, **kwargs):
        assert p is original and epoch == 'test-epoch'
        assert kwargs['profile']['name'] == 'standard'
        return repaired, dict(passed=True, lifts=8, base_lifts=None, lift_delta=None,
            what_moved=[], base_already_obeys=False, sequence_repair=dict(producer='lattice.sequence'))
    monkeypatch.setattr(sequence, 'repair_candidate', repair)
    monkeypatch.setattr(sequence_blocks, 'repair_candidate', lambda *a, **kw: pytest.fail('successful adjacent repair fell through'))
    assert batch.compute(job)['status'] == 'checked_plan'
    row = next(json.loads(path.read_text()) for path in Path(job['run_dir']).glob('*.json')
               if not path.name.endswith('.frames.json'))
    assert row['plan_producer'] == 'lattice.sequence'
    assert row['producer_label'] == sequence.LABEL
    assert row['original_binding_plan'] == original.model_dump(mode='json')
    assert row['plan'] == repaired.model_dump(mode='json')


@pytest.mark.parametrize('block_passes', [True, False])
def test_bounded_blocks_follow_adjacent_failure_in_same_admitted_slot(compute_case, monkeypatch, block_passes):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from . import gates, sequence, sequence_blocks
    job, original = compute_case
    repaired = original.model_copy(update={'id': original.id+'.lattice-block-sequence'})
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw:
                        (SimpleNamespace(plan=original), dict(passed=True)))
    source_frames = dict(physics_epoch='fresh-test-epoch', exact_original_plan_hash=batch.sha256_json(original.model_dump(mode='json')))
    admissions, calls = [], []
    monkeypatch.setattr(batch, 'admit', lambda _: admissions.append(1) or dict(load1=20))
    def reject(*a, frame_checkpoint_path, **kw):
        batch.write_json(frame_checkpoint_path, source_frames)
        return dict(passed=False, gauges=dict(passed=False))
    monkeypatch.setattr(gates, 'gate_plan', reject)
    received = []
    def adjacent(instance, plan, frames, epoch, **kwargs):
        assert plan is original and frames == source_frames and epoch == 'fresh-test-epoch'
        received.append(frames)
        calls.append('adjacent')
        batch.write_json(kwargs['checkpoint_dir']/'attempt-1.json', dict(status='native_trial_frame_rejected', trial_failed_steps=[113]))
        return None
    def blocks(instance, plan, frames, epoch, **kwargs):
        calls.append('blocks')
        assert plan is original and frames is received[0] and epoch == 'fresh-test-epoch'
        assert {k: kwargs[k] for k in ('window', 'lookback', 'beam_width', 'max_candidates', 'budget_s')} == \
            dict(window=32, lookback=4, beam_width=4, max_candidates=12, budget_s=180.)
        assert kwargs['profile']['name'] == 'standard'
        if not block_passes:
            batch.write_json(kwargs['checkpoint_dir']/'candidate-1.json', dict(status='trial_budget_failed'))
            return None
        return repaired, dict(passed=True, lifts=8, fresh_native_all_frames=True,
            sequence_repair=dict(producer='lattice.sequence_blocks', final_release='shared gate_plan; fresh native all-frame sweep'))
    monkeypatch.setattr(sequence, 'repair_candidate', adjacent)
    monkeypatch.setattr(sequence_blocks, 'repair_candidate', blocks)
    assert batch.compute(job)['status'] == ('checked_plan' if block_passes else 'gate_failed')
    row = next(json.loads(p.read_text()) for p in Path(job['run_dir']).glob('*.json')
               if not p.name.endswith('.frames.json'))
    assert calls == ['adjacent', 'blocks'] and len(admissions) == 3
    attempts = row['sequence_repair_attempts']
    assert [a['producer'] for a in attempts] == ['lattice.sequence', 'lattice.sequence_blocks']
    assert attempts[0]['trial_checkpoints'][0]['trial_failed_steps'] == [113]
    assert all(a['historical_physics_is_release_certificate'] is False for a in attempts)
    assert row['original_binding_plan'] == original.model_dump(mode='json')
    if block_passes:
        assert row['plan_producer'] == 'lattice.sequence_blocks' and row['producer_label'] == sequence_blocks.LABEL
        assert row['gate']['fresh_native_all_frames'] and row['plan'] == repaired.model_dump(mode='json')
    else:
        assert attempts[1]['trial_checkpoints'][0]['status'] == 'trial_budget_failed'
        assert 'not an impossibility proof' in attempts[1]['reason']
        assert 'plan_producer' not in row and row['plan'] == original.model_dump(mode='json')
        assert not Path(job['store_dir']).exists()


def test_binding_drift_after_adjacent_failure_prevents_bounded_blocks(compute_case, monkeypatch):
    from experiments.mvp.pipeline.runner import limits
    from . import gates, sequence, sequence_blocks
    job, original = compute_case
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw:
                        (SimpleNamespace(plan=original), dict(passed=True)))
    def reject(*a, frame_checkpoint_path, **kw):
        batch.write_json(frame_checkpoint_path, dict(physics_epoch='fresh-test-epoch'))
        return dict(passed=False)
    monkeypatch.setattr(gates, 'gate_plan', reject)
    def drift(*a, **kw):
        monkeypatch.setattr(batch, 'binding_fingerprint', lambda: 'changed-after-adjacent')
        return None
    monkeypatch.setattr(sequence, 'repair_candidate', drift)
    monkeypatch.setattr(sequence_blocks, 'repair_candidate', lambda *a, **kw: pytest.fail('block ran after source drift'))
    assert batch.compute(job)['status'] == 'rerun_required'


def test_live_probe_never_enters_store(compute_case, monkeypatch):
    from experiments.mvp.pipeline.runner import limits
    job, plan = compute_case
    job['spec']['purpose'] = 'off_lattice'
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    assert batch.compute(job)['status'] == 'checked_live'
    assert not __import__('pathlib').Path(job['store_dir']).exists()


def test_binding_dg_replan_publishes_standalone_confirmed_fixture(compute_case, monkeypatch):
    from pathlib import Path
    from experiments.mvp.pipeline.runner import limits
    from experiments.mvp.reader import schema
    from . import gates
    job, plan = compute_case
    body = dict(type='dg_keep_away', imdg_class='3', min_bays=1, away_from=dict(kind='bay', bay=1))
    job['spec'] = make_spec('dg-demo', [body], base_violations=['old DG slot too close'])
    monkeypatch.setattr(schema, 'context_from_instance', lambda _: dict(bays=[1], has_accommodation=False))
    monkeypatch.setattr(limits, 'call_with_limit', lambda *a, **kw: (SimpleNamespace(plan=plan), dict(passed=True)))
    audit = dict(passed=True, binding=True, boxes_moved=2, lifts=11, base_lifts=8,
                 lift_delta=3, what_moved=[dict(container_id='box', before='a', after='b')], base_already_obeys=[False])
    monkeypatch.setattr(gates, 'gate_plan', lambda *a, **kw: audit)
    assert batch.compute(job)['status'] == 'checked_plan'
    directory = Path(job['fixtures_dir'])
    entry = json.loads((directory/'hero-replan.json').read_text())
    summary = json.loads((directory/'hero-instruction.json').read_text())
    assert entry['confirmation_source'] == 'batch'
    assert entry['canonical_rules'][0]['constraint'] == body
    assert entry['plan'] == plan.model_dump(mode='json')
    assert summary['canonical_rules'] == entry['canonical_rules']
    assert summary['sentences'] == entry['sentences']
    assert (summary['lifts_before'], summary['lifts_after'], summary['boxes_moved']) == (8, 11, 2)
    assert summary['computed_utc'] == entry['computed_utc']
    assert (batch.ROOT/summary['path']).resolve() == (directory/'hero-replan.json').resolve()
    assert summary['entry_sha256'] == batch.sha256_file(directory/'hero-replan.json')
    # Later successes preserve the first fixture until its binding source ages.
    later = dict(entry, id='later', sentences=['A different confirmed DG sentence'])
    assert not batch.save_hero_replan(later, fixtures_dir=directory)
    assert json.loads((directory/'hero-replan.json').read_text())['id'] == 'dg-demo'
    assert batch.save_hero_replan(dict(later, binding_source_sha256='new-source'), fixtures_dir=directory)
    assert json.loads((directory/'hero-replan.json').read_text())['id'] == 'later'


@pytest.mark.parametrize('binding, moved', [(False, 2), (True, 0), (True, -1)])
def test_hero_fixture_requires_visible_binding_change(tmp_path, binding, moved):
    row = dict(status='checked_plan', canonical_rules=[dict(constraint=dict(type='dg_keep_away'))],
               gate=dict(passed=True, binding=binding, boxes_moved=moved))
    assert not batch.save_hero_replan(row, fixtures_dir=tmp_path/'fixtures')
    assert not (tmp_path/'fixtures').exists()


def test_midrun_binding_change_prevents_storage_and_forces_next_launch_rerun(compute_case, monkeypatch):
    from experiments.mvp.pipeline.runner import limits
    from pathlib import Path
    job, plan = compute_case
    fingerprints = iter(['before', 'after', 'after', 'after'])
    monkeypatch.setattr(batch, 'binding_fingerprint', lambda: next(fingerprints))
    calls = []
    def solve(*a, **kw):
        calls.append(1)
        return SimpleNamespace(plan=plan), dict(passed=True)
    monkeypatch.setattr(limits, 'call_with_limit', solve)
    assert batch.compute(job)['status'] == 'rerun_required'
    assert not Path(job['store_dir']).exists()
    assert batch.compute(job)['status'] == 'checked_plan'
    assert calls == [1, 1]
    assert len(list((Path(job['run_dir'])/'archive').glob('*.json'))) == 1


def test_parallel_progress_refresh_keeps_complete_snapshots(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    run_dir = tmp_path/'runs'
    def checkpoint(index):
        batch.write_json(run_dir/f'{index}.json', dict(id=f'rule-{index}', status='checked_plan',
                         sentences=[f'Instruction {index}'], lifts=index, purpose='lattice'))
        batch.refresh_progress(run_dir)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(checkpoint, range(12)))
    data = json.loads((tmp_path/'progress.json').read_text())
    table = (tmp_path/'RULES.md').read_text()
    assert data['stored'] == 12
    assert len(data['rows']) == 12
    assert table.count('checked_plan') == 12
