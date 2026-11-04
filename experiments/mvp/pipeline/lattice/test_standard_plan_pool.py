from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import standard_plan_pool as pool
from experiments.mvp.pipeline.lattice.store import canonical_rule_set, entry_key

RULE = {'type': 'close_bay', 'bay': 1}
DONOR_RULE = {'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 2}
IDENTITY = {'passed': True, 'engine_version': '0.13.0', 'engine_manifest_sha256': 'c'*64}
PROFILE = {'name': 'standard', 'engine_timeout_s': 900., 'config': {'seed': 0}}


def audit(row, *, green):
    frames = [dict(step=step, stability_passed=green or step != 1, strength_passed=True)
              for step in range(len(row['plan']['moves'])+1)]
    gauges = pool.gauge_verdict({'frames': frames, 'physics_epoch': 'native-unit-epoch'}, len(row['plan']['moves']))
    return dict(passed=green, checker={'passed': True, 'count': {'weighted_nonproductive': 2}},
                instance_sha256=row['instance_sha256'], plan_sha256=sha256_json(row['plan']),
                rules=[dict(envelope=r, passed=True) for r in row['canonical_rules']], gauges=gauges,
                engine_identity=IDENTITY, binding=True, base_already_obeys=[False], lifts=2,
                base_lifts=1, lift_delta=1, what_moved=[{'container_id': 'unit-box'}])


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    here = root / 'experiments/mvp/pipeline/lattice'
    here.mkdir(parents=True)
    monkeypatch.setattr(pool, 'ROOT', root)
    monkeypatch.setattr(pool, 'HERE', here)
    monkeypatch.setattr(pool, 'engine_identity', lambda: deepcopy(IDENTITY))
    monkeypatch.setattr(pool, '_standard_profile', lambda: deepcopy(PROFILE))
    monkeypatch.setattr(pool, 'binding_fingerprint', lambda: 'd'*64)
    monkeypatch.setattr(pool, '_binding_source_matches', lambda r: True)
    monkeypatch.setattr(pool, 'binding_compatibility', lambda r: {'approved': 'unit proof'})
    monkeypatch.setattr(pool, '_admit', lambda j: {'enabled_cores': 8, 'load1': 20, 'reading_age_s': 0})
    monkeypatch.setattr(pool, '_refresh_progress', lambda run_dir: None)
    instance = {'id': 'synthetic-original'}
    base = {'instance_id': instance['id'], 'moves': []}
    own = dict(id='request', status='gate_failed', purpose='lattice', instance=instance,
               instance_sha256=sha256_json(instance), plan={'instance_id': instance['id'], 'moves': [{'own': 1}, {'own': 2}]},
               canonical_rules=canonical_rule_set([RULE]), profile=PROFILE, engine_wall_s=900.1,
               engine_version=IDENTITY['engine_version'], engine_manifest_sha256=IDENTITY['engine_manifest_sha256'],
               computed_utc='2026-11-01T01:00:00Z', instance_path='synthetic-unit-only.json', base_plan=base,
               binding_evidence={'passed': False, 'recovered_incumbent': True})
    own['key'] = entry_key(own['instance_sha256'], own['canonical_rules'])
    own['gate'] = audit(own, green=False)
    donor = deepcopy(own)
    donor.update(id='donor', status='checked_plan', computed_utc='2026-11-01T02:00:00Z',
                 plan={'instance_id': instance['id'], 'moves': [{'different': 'placement and ordering'}]},
                 canonical_rules=canonical_rule_set([DONOR_RULE]))
    donor['key'] = entry_key(donor['instance_sha256'], donor['canonical_rules'])
    donor['gate'] = audit(donor, green=True)
    spec = dict(id=own['id'], rules=own['canonical_rules'], sentences=['Do not load in bay 01.'], purpose='lattice')
    paths = {}
    for name, row in [('requested', own), ('donor', donor)]:
        path = here / f'{name}.json'
        path.write_text(json.dumps(row, indent=2)+'\n')
        spec[f'standard_plan_pool_{name}_path'] = str(path)
        spec[f'standard_plan_pool_{name}_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        paths[name] = path
    run_dir = here/'runs'
    run_dir.mkdir()
    target = run_dir/(own['key']+'.json')
    target.write_bytes(paths['requested'].read_bytes())
    calls, saved = [], []
    def gate(instance, plan, rules, **kwargs):
        calls.append((deepcopy(instance), deepcopy(plan), deepcopy(rules), kwargs))
        candidate = dict(instance_sha256=sha256_json(instance), plan=plan, canonical_rules=rules)
        return audit(candidate, green=True)
    monkeypatch.setattr(pool, 'gate_plan', gate)
    monkeypatch.setattr(pool, 'save_entry', lambda row, **kw: saved.append(deepcopy(row)))
    job = dict(spec=spec, base_plan=base, run_dir=run_dir, store_dir=here/'store',
               checkpoint_dir=here/'checkpoints')
    return dict(root=root, here=here, own=own, donor=donor, paths=paths, job=job, calls=calls, saved=saved,
                target=target, original=target.read_bytes())


def change_source(data, name, change):
    path = data['paths'][name]
    row = json.loads(path.read_text())
    change(row)
    path.write_text(json.dumps(row))
    data['job']['spec'][f'standard_plan_pool_{name}_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_pool_selects_a_different_plan_with_two_origins_and_fresh_full_gate(data):
    result = pool.compute(data['job'])
    assert result['status'] == 'checked_plan'
    assert len(data['calls']) == len(data['saved']) == 1
    instance, plan, rules, kwargs = data['calls'][0]
    assert instance == data['own']['instance'] and plan == data['donor']['plan']
    assert rules == data['own']['canonical_rules']
    assert kwargs['base_plan'] == data['own']['base_plan']
    assert kwargs['frame_checkpoint_path'].name == 'native.frames.json'
    row = data['saved'][0]
    assert row['plan_producer'] == pool.PRODUCER and row['native_engine_output'] is False
    assert row['computed_utc'] == data['donor']['computed_utc']
    assert row['requested_computed_utc'] == data['own']['computed_utc']
    assert row['binding_evidence']['native_binding_called'] is False
    assert row['engine_wall_s'] == row['requested_engine_wall_s'] == data['own']['engine_wall_s']
    assert row['donor_engine_wall_s'] == data['donor']['engine_wall_s']
    assert row['wall_s'] == data['own']['engine_wall_s'] and row['total_wall_s'] is None
    assert row['portfolio_native_search_s'] == 0 and row['release_wall_s'] >= 0
    proof = row['standard_plan_pool']
    for name in ('requested', 'donor'):
        source = proof[name]
        archive = data['root']/source['exact_raw_archive']
        assert archive.read_bytes() == data['paths'][name].read_bytes()
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == source['raw_sha256']
        assert source['record'] == data['own' if name == 'requested' else 'donor']
    assert proof['requested_binding_produced_selected_plan'] is False
    assert proof['sequence_repair'] is proof['move_multiset_equality_claimed'] is False
    assert json.loads(data['target'].read_text())['plan'] == data['donor']['plan']


def test_retry_work_clock_excludes_preflight_and_initial_admission(data, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(pool.time, 'monotonic', lambda: clock[0])
    validate = pool._validate
    def validation(*args):
        clock[0] += 1.5
        return validate(*args)
    def admission(job):
        clock[0] += 120.
        return {'waited_s': 120., 'load1': 20., 'enabled_cores': 8}
    gate = pool.gate_plan
    def native_gate(*args, **kwargs):
        clock[0] += 7.
        return gate(*args, **kwargs)
    monkeypatch.setattr(pool, '_validate', validation)
    monkeypatch.setattr(pool, '_admit', admission)
    monkeypatch.setattr(pool, 'gate_plan', native_gate)
    assert pool.compute(data['job'])['status'] == 'checked_plan'
    row = data['saved'][0]
    assert row['release_wall_s'] == 7.
    assert row['preflight_wall_s'] == 3.
    assert row['initial_admission_wall_s'] == 120.
    assert row['native_gate_wall_s'] == 7.
    assert row['portfolio_total_wall_s'] == 130.
    assert row['release_timing_scope'] == 'after_initial_admission'
    assert data['calls'][0][3]['admit'] is False


@pytest.mark.parametrize('problem', ['live', 'profile_config', 'manifest', 'instance', 'runtime',
                                  'donor_missing_frame', 'donor_gate_plan', 'donor_gate_rules',
                                  'own_already_obeys', 'own_not_physical_failure', 'own_no_standard_candidate',
                                  'no_source_pin', 'tamper', 'pool_chain'])
def test_invalid_source_never_reaches_gate_or_overwrites_original(data, monkeypatch, problem):
    if problem == 'runtime':
        monkeypatch.setattr(pool, '_binding_source_matches', lambda r: False)
    elif problem == 'no_source_pin':
        data['job']['spec']['standard_plan_pool_donor_sha256'] = None
    elif problem == 'tamper':
        data['paths']['donor'].write_text('{}')
    else:
        def change(row):
            if problem == 'live': row['profile'] = dict(PROFILE, name='live', engine_timeout_s=720.)
            elif problem == 'profile_config': row['profile'] = dict(PROFILE, config={'seed': 99})
            elif problem == 'manifest': row['engine_manifest_sha256'] = 'e'*64
            elif problem == 'instance': row['instance_sha256'] = 'f'*64
            elif problem == 'donor_missing_frame': row['gate']['gauges']['physics']['frames'].pop()
            elif problem == 'donor_gate_plan': row['gate']['plan_sha256'] = 'f'*64
            elif problem == 'donor_gate_rules': row['gate']['rules'][0]['envelope'] = canonical_rule_set([RULE])[0]
            elif problem == 'own_already_obeys': row['gate']['base_already_obeys'] = [True]
            elif problem == 'own_not_physical_failure': row['gate']['gauges']['passed'] = True
            elif problem == 'own_no_standard_candidate': row['binding_evidence'] = {'passed': False}
            elif problem == 'pool_chain': row['plan_producer'] = pool.PRODUCER
        change_source(data, 'requested' if problem.startswith('own_') else 'donor', change)
    result = pool.compute(data['job'])
    assert result['status'] == 'portfolio_failed'
    assert not data['calls'] and not data['saved']
    assert data['target'].read_bytes() == data['original']
    if problem in ('tamper', 'no_source_pin'):
        receipts = list((data['here']/'standard-plan-pool/errors').glob('*.json'))
        assert len(receipts) == 1
        receipt = json.loads(receipts[0].read_text())
        assert receipt['worker_released'] is True and receipt['release_utc']


@pytest.mark.parametrize('problem', ['gauge', 'rule', 'binding', 'runtime_drift', 'source_drift'])
def test_failed_fresh_gate_is_checkpointed_without_publishing(data, monkeypatch, problem):
    original = pool.gate_plan
    def gate(*args, **kwargs):
        result = original(*args, **kwargs)
        if problem == 'gauge': result.update(passed=False)
        elif problem == 'rule': result['rules'][0]['passed'] = False
        elif problem == 'binding': result['binding'] = False
        elif problem == 'runtime_drift': monkeypatch.setattr(pool, 'binding_fingerprint', lambda: 'e'*64)
        elif problem == 'source_drift': data['paths']['donor'].write_text('{}')
        return result
    monkeypatch.setattr(pool, 'gate_plan', gate)
    result = pool.compute(data['job'])
    assert result['status'] in ('portfolio_failed', 'portfolio_gate_failed', 'rerun_required')
    assert len(data['calls']) == 1 and not data['saved']
    assert data['target'].read_bytes() == data['original']
    checkpoint = data['job']['checkpoint_dir']/'result.json'
    row = json.loads(checkpoint.read_text())
    assert row['standard_plan_pool']['requested']['record']['gate']['passed'] is False
    assert row['standard_plan_pool']['donor']['record']['gate']['passed'] is True
    assert 'gate' in row


def test_gate_exception_and_admission_checkpoint_keep_both_raw_sources(data, monkeypatch):
    def gate(*args, **kwargs):
        raise RuntimeError('native check unavailable')
    monkeypatch.setattr(pool, 'gate_plan', gate)
    result = pool.compute(data['job'])
    assert result['status'] == 'portfolio_failed'
    assert 'native check unavailable' in result['reason']
    assert not data['saved'] and data['target'].read_bytes() == data['original']
    directory = data['job']['checkpoint_dir']
    assert (directory/'requested-source.json').read_bytes() == data['paths']['requested'].read_bytes()
    assert (directory/'donor-source.json').read_bytes() == data['paths']['donor'].read_bytes()


def test_default_checkpoints_preserve_separate_failed_attempts(data, monkeypatch):
    data['job'].pop('checkpoint_dir')
    def gate(*args, **kwargs):
        raise RuntimeError('native check unavailable')
    monkeypatch.setattr(pool, 'gate_plan', gate)
    for _ in range(2):
        assert pool.compute(data['job'])['status'] == 'portfolio_failed'
    results = list((data['here']/'standard-plan-pool'/data['own']['key']).glob('*/result.json'))
    assert len(results) == 2
    for path in results:
        row = json.loads(path.read_text())
        assert row['standard_plan_pool']['requested']['record'] == data['own']
        assert row['standard_plan_pool']['donor']['record'] == data['donor']


def test_supplied_instance_path_must_be_the_requested_original(data):
    data['job']['instance_path'] = 'another-original.json'
    assert pool.compute(data['job'])['status'] == 'portfolio_failed'
    assert not data['calls'] and not data['saved']


def test_running_receipt_reserves_gate_worker_and_is_removed_after_release(data, monkeypatch):
    phases, refreshed = [], []
    running = data['target'].with_suffix('.running.json')
    def admit(job):
        row = json.loads(running.read_text())
        assert row['worker_released'] is False and row['process_pid'] > 0
        phases.append(row['release_stage'])
        assert data['target'].read_bytes() == data['original']
        return {'enabled_cores': 8, 'load1': 20}
    original = pool.gate_plan
    def gate(*args, **kwargs):
        phases.append(json.loads(running.read_text())['release_stage'])
        return original(*args, **kwargs)
    monkeypatch.setattr(pool, '_admit', admit)
    monkeypatch.setattr(pool, 'gate_plan', gate)
    monkeypatch.setattr(pool, '_refresh_progress', lambda path: refreshed.append(path))
    assert pool.compute(data['job'])['status'] == 'checked_plan'
    assert phases == ['standard_plan_pool_gate_admission_pending', 'standard_plan_pool_native_gate_running']
    assert not running.exists() and refreshed == [data['target'].parent]
