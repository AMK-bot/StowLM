from copy import deepcopy

import pytest

from experiments.mvp.pipeline.contract.schema import sha256_json
from experiments.mvp.pipeline.lattice import store

SHA = 'a' * 64
CLOSE = {'type': 'close_bay', 'bay': 1}
CAP = {'type': 'max_tiers', 'bay': None, 'zone': 'deck', 'max_tiers': 1}


def entry():
    instance = {'synthetic': True}
    fingerprint = store.binding_fingerprint()
    return dict(instance=instance, instance_sha256=sha256_json(instance), canonical_rules=[CLOSE],
                plan={'id': 'fixture'}, computed_utc='2026-11-01T00:00:00Z',
                engine_version='0.13.0', engine_manifest_sha256='b' * 64,
                binding_source_sha256=fingerprint, binding_source_after_sha256=fingerprint)


def test_keys_are_sets_of_validated_rules_and_exact_instances():
    assert store.entry_key(SHA, [CLOSE, CAP, CLOSE]) == store.entry_key(SHA, [CAP, CLOSE])
    assert store.entry_key(SHA, [CLOSE]) != store.entry_key('b' * 64, [CLOSE])
    with pytest.raises((ValueError, TypeError)):
        store.entry_key(SHA, ['Bay 1 is closed.'])
    with pytest.raises(ValueError):
        store.entry_key('map-name', [CLOSE])


@pytest.fixture
def gated(monkeypatch):
    calls = []
    monkeypatch.setattr(store, '_current_token', lambda row: (sha256_json(row), 'epoch'))
    def regate(row):
        calls.append(deepcopy(row))
        return dict(row, rechecked_utc='now', checker={'passed': True})
    monkeypatch.setattr(store, '_regate_entry', regate)
    return calls


def test_checkpoint_lookup_is_labelled_and_regated_once(tmp_path, gated):
    row = entry()
    path = store.save_entry(row, store_dir=tmp_path)
    candidate = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert candidate['mode'] == candidate['label'] == 'stored'
    assert candidate['computed_utc'] == row['computed_utc']
    assert candidate['checker']['passed'] is True
    assert candidate['gate_source'] == 'fresh' and candidate['freshly_rechecked'] is True
    assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path) == sha256_json(candidate)
    memo = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert memo['gate_source'] == 'memo' and memo['freshly_rechecked'] is False
    assert memo['rechecked_utc'] == candidate['rechecked_utc']
    assert 'rechecked now' not in memo['stored_message']
    assert 'rechecked at '+candidate['rechecked_utc'] in memo['stored_message']
    assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path) == sha256_json(memo)
    assert len(gated) == 1
    candidate['plan']['id'] = 'changed by caller'
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)['plan']['id'] == 'fixture'
    assert path.exists()


@pytest.mark.parametrize('producer,repaired', [('lattice.sequence', True),
    ('lattice.sequence_blocks', True), ('lattice.recover_candidate', True), (None, False)])
def test_lookup_frame_claim_names_selected_sequence_provenance(tmp_path, gated, producer, repaired):
    row = entry()
    row['plan_producer'] = producer
    # A repaired comparison baseline must not relabel native instruction output.
    row['base_plan'] = {'id': 'base.lattice-sequence'}
    store.save_entry(row, store_dir=tmp_path)
    for _ in range(2):
        candidate = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
        provenance = candidate['frame_provenance']
        assert provenance['repaired'] is repaired
        assert provenance['base_sequence']['repaired'] is True
        expected = 'crane sequence repaired' if repaired else 'crane sequence not repaired'
        assert expected in candidate['stored_message']
        assert provenance['frame_claim_scope'] == 'crane frames walked on this plan'
        assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path) == sha256_json(candidate)


def test_tamper_after_launch_pin_is_a_miss(tmp_path, gated):
    row = entry()
    path = store.save_entry(row, store_dir=tmp_path)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    path.write_text('{}')
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None


def test_catalogue_decodes_the_exact_bytes_authenticated_before_replacement(tmp_path, monkeypatch):
    import hashlib
    import json
    from pathlib import Path
    catalogue = {'a'*64: 'b'*64}
    path = (tmp_path / 'catalogue.json').resolve()
    raw = json.dumps(catalogue).encode()
    path.write_bytes(raw)
    original = Path.read_bytes
    reads = []
    def replaced_after_read(candidate):
        data = original(candidate)
        if candidate == path:
            reads.append(candidate)
            candidate.write_text(json.dumps({'c'*64: 'd'*64}))
        return data
    monkeypatch.setattr(Path, 'read_bytes', replaced_after_read)
    actual = store._catalogue(tmp_path, refresh=True, expected_sha256=hashlib.sha256(raw).hexdigest())
    assert actual == catalogue
    assert reads == [path]


def test_entry_decodes_the_exact_bytes_authenticated_before_replacement(tmp_path, monkeypatch):
    from pathlib import Path
    row = entry()
    path = store.save_entry(row, store_dir=tmp_path).resolve()
    expected = store._read(path.stem, tmp_path)
    original = Path.read_bytes
    reads = []
    def replaced_after_read(candidate):
        data = original(candidate)
        if candidate == path:
            reads.append(candidate)
            candidate.write_text('{}')
        return data
    monkeypatch.setattr(Path, 'read_bytes', replaced_after_read)
    assert store._read(path.stem, tmp_path) == expected
    assert reads == [path]
    assert store._read(path.stem, tmp_path) is None


@pytest.mark.parametrize('catalogue', [[], {'not-a-sha': 'b'*64}, {'a'*64: None}])
def test_malformed_catalogue_fails_launch_closed(tmp_path, catalogue):
    import json
    (tmp_path / 'catalogue.json').write_text(json.dumps(catalogue))
    summary = store.regate(store_dir=tmp_path)
    assert 'malformed' in summary['errors']['catalogue']
    assert not summary['passed']


def test_wrong_rule_instance_and_no_gate_never_return_plan(tmp_path, gated, monkeypatch):
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    assert store.lookup(SHA, [CLOSE], store_dir=tmp_path) is None
    assert store.lookup(row['instance_sha256'], [CAP], store_dir=tmp_path) is None
    monkeypatch.setattr(store, '_regate_entry', lambda _: None)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None


def test_launch_regates_catalogue_and_checks_independent_pin(tmp_path, gated):
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    pin = store.catalogue_sha256(store_dir=tmp_path)
    summary = store.regate(store_dir=tmp_path, expected_catalogue_sha256=pin)
    assert len(summary['passed']) == 1
    assert store.regate(store_dir=tmp_path, expected_catalogue_sha256='f'*64)['errors']


def test_failed_launch_checker_cannot_reuse_an_earlier_success(tmp_path, gated, monkeypatch):
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    def unavailable(_):
        raise RuntimeError('checker unavailable')
    monkeypatch.setattr(store, '_regate_entry', unavailable)
    summary = store.regate(store_dir=tmp_path)
    assert 'RuntimeError: checker unavailable' in summary['errors'].values()
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None


def test_failed_launch_pin_withholds_plan_until_successful_refresh(tmp_path, gated):
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    pin = store.catalogue_sha256(store_dir=tmp_path)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert store.regate(store_dir=tmp_path, expected_catalogue_sha256='f'*64)['errors']
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert len(gated) == 1
    assert store.regate(store_dir=tmp_path, expected_catalogue_sha256=pin)['passed']
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert len(gated) == 2


def test_selective_launch_error_preserves_other_instance_gate(tmp_path, gated, monkeypatch):
    row, other = entry(), entry()
    other['instance'] = {'synthetic': 'other hero'}
    other['instance_sha256'] = sha256_json(other['instance'])
    for candidate in (row, other):
        store.save_entry(candidate, store_dir=tmp_path)
    for candidate in (row, other):
        assert store.lookup(candidate['instance_sha256'], [CLOSE], store_dir=tmp_path)
    def unavailable(_):
        raise RuntimeError('selected checker unavailable')
    monkeypatch.setattr(store, '_regate_entry', unavailable)
    summary = store.regate(row['instance_sha256'], store_dir=tmp_path)
    assert len(summary['errors']) == 1
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert store.lookup(other['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert len(gated) == 2


def test_checkpoint_rejects_changed_original(tmp_path):
    row = entry()
    row['instance']['synthetic'] = False
    with pytest.raises(ValueError, match='original instance'):
        store.save_entry(row, store_dir=tmp_path)


def test_runner_rule_order_retained_while_key_ignores_order(tmp_path, gated):
    row = entry()
    row['canonical_rules'] = [CLOSE, CAP]
    store.save_entry(row, store_dir=tmp_path)
    rules = store._rule_list([CAP, CLOSE])
    assert store.lookup(row['instance_sha256'], rules, store_dir=tmp_path)['canonical_rules'] == rules


def test_refusal_rechecks_proof_without_checker_tick(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.lattice import gates
    row = entry() | dict(status='proved_refusal', plan=None, proof={'historical': True})
    store.save_entry(row, store_dir=tmp_path)
    monkeypatch.setattr(gates, 'engine_identity', lambda: dict(passed=True, engine_version='0.13.0',
                                                           engine_manifest_sha256='b'*64))
    monkeypatch.setattr(store, '_refusal_proof', lambda _: {'proven_impossible': True, 'open_cells': 0})
    refusal = store.refusal_lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert refusal['proof'] == {'proven_impossible': True, 'open_cells': 0}
    assert refusal['checker'] is None and refusal['planning_permitted'] is False
    monkeypatch.setattr(store, '_refusal_proof', lambda _: None)
    assert store.refusal_lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None


def test_budget_failure_is_never_a_refusal_or_launch_plan(tmp_path, gated):
    row = entry() | dict(status='budget_failed', plan=None)
    store.save_entry(row, store_dir=tmp_path)
    assert store.refusal_lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    summary = store.regate(store_dir=tmp_path)
    assert len(summary['skipped']) == 1 and not summary['passed']


def test_compound_refusal_returns_fresh_capacity_arithmetic(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS
    from stowage.model.fixtures import build_instance
    from experiments.mvp.pipeline.binding import adapter, admission
    from experiments.mvp.pipeline.lattice import runtime
    instance = build_instance()
    row = dict(instance=instance.model_dump(mode='json'), canonical_rules=store.canonical_rule_set([CLOSE, CAP]),
               proof={'arithmetic': {'demand_cells': 999, 'open_cells': 999}})
    monkeypatch.setattr(admission, 'wait_for_capacity', lambda: {})
    monkeypatch.setattr(runtime, 'isolated_binding', nullcontext)
    monkeypatch.setattr(adapter, 'compile_constraint', lambda *a, **kw: (NS(), {}))
    monkeypatch.setattr(adapter, 'Intersection', lambda *args: NS(capacity_shortfall=lambda:
                        'the load list needs 23 TEU of cells but only 12 remain open after all instructions'))
    proof = store._refusal_proof(row)
    assert proof['proven_impossible'] is True
    assert (proof['demand_cells'], proof['open_cells'], proof['equation']) == (23., 12, '23 needed > 12 open')
    assert proof['scope'] == 'necessary combined cell-capacity screen'


@pytest.fixture
def screen_source(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    import json
    import os
    root = tmp_path / 'repo'
    here = root / 'experiments/mvp/pipeline/lattice'
    binding = here.parent / 'binding'
    binding.mkdir(parents=True)
    screening = here / 'screening'
    screening.mkdir(parents=True)
    started = 1800000000.
    source_files = {}
    for name in ('__init__.py', 'adapter.py', 'admission.py', 'allocation.py', 'hooks.py', 'runtime.py', 'confirmation.py', 'provenance.py'):
        path = binding / name
        path.write_text(f'# runtime {name}\n')
        os.utime(path, (started - 60, started - 60))
        relative = str(path.relative_to(root))
        source_files[relative] = dict(sha256=store.sha256_file(path), mtime_s=path.stat().st_mtime)
    source_files['experiments/mvp/pipeline/binding/measure.py'] = dict(sha256='changed', mtime_s=started+30)
    source = screening / 'candidate.json'
    progress = dict(pid=42, started_utc=datetime.fromtimestamp(started, timezone.utc).isoformat())
    snapshot = dict(pid=42, worker_started_unix=started, source_files=source_files,
                    binding_source_at_launch_known=False, all_source_mtimes_before_worker_start=False)
    source.with_suffix('.progress.json').write_text(json.dumps(progress))
    sidecar = source.with_suffix('.progress.source.json')
    sidecar.write_text(json.dumps(snapshot))
    monkeypatch.setattr(store, 'ROOT', root)
    monkeypatch.setattr(store, 'HERE', here)
    return dict(root=root, here=here, binding=binding, source=source, snapshot=snapshot,
                sidecar=sidecar, started=started)


def test_screen_checkpoint_binds_runtime_only_source_snapshot(screen_source, tmp_path):
    import json
    data = screen_source
    row = entry() | dict(source_screen=str(data['source'].relative_to(data['root'])))
    path = store.save_entry(row, store_dir=tmp_path / 'store')
    saved = json.loads(path.read_text())
    hashes = {name: store.sha256_file(data['binding'] / name) for name in store.binding_source_files()}
    assert saved['binding_source_sha256'] == saved['binding_source_after_sha256'] == sha256_json(hashes)
    assert saved['source_screen_provenance']['pid'] == 42
    assert saved['source_screen_provenance']['runtime_mtimes_before_worker_start'] is True


@pytest.mark.parametrize('change', ['pid', 'start', 'recorded_mtime', 'current_mtime', 'hash', 'missing'])
def test_screen_checkpoint_refuses_unknown_or_changed_runtime_source(screen_source, tmp_path, change):
    import json
    import os
    data = screen_source
    snapshot = data['snapshot']
    relative = 'experiments/mvp/pipeline/binding/adapter.py'
    if change == 'pid':
        snapshot['pid'] = 99
    elif change == 'start':
        snapshot['worker_started_unix'] += 1
    elif change == 'recorded_mtime':
        snapshot['source_files'][relative]['mtime_s'] = data['started'] + 1
    elif change == 'current_mtime':
        os.utime(data['binding'] / 'adapter.py', (data['started']+1, data['started']+1))
    elif change == 'hash':
        path = data['binding'] / 'adapter.py'
        path.write_text('# changed runtime\n')
        os.utime(path, (data['started']-1, data['started']-1))
    data['sidecar'].write_text(json.dumps(snapshot))
    if change == 'missing':
        data['sidecar'].unlink()
    row = entry() | dict(source_screen=str(data['source'].relative_to(data['root'])))
    destination = tmp_path / 'store'
    with pytest.raises(ValueError, match='rerun_required'):
        store.save_entry(row, store_dir=destination)
    assert not (destination / 'catalogue.json').exists()


def test_preview_checkpoint_uses_original_screen_worker_snapshot(screen_source, tmp_path):
    data = screen_source
    preview = data['source'].with_suffix('.preview.json')
    row = entry() | dict(source_screen=str(preview.relative_to(data['root'])))
    assert store.save_entry(row, store_dir=tmp_path / 'store').exists()


@pytest.mark.parametrize('change', ['missing', 'stale', 'changed_after', 'new_runtime_module'])
def test_lookup_rejects_missing_or_noncurrent_binding_fingerprint(tmp_path, gated, monkeypatch, change):
    row = entry()
    if change == 'missing':
        row.pop('binding_source_sha256')
    elif change == 'stale':
        row['binding_source_sha256'] = '0' * 64
    elif change == 'changed_after':
        row['binding_source_after_sha256'] = '0' * 64
    store.save_entry(row, store_dir=tmp_path)
    if change == 'new_runtime_module':
        # A new imported runtime module changes the live fingerprint too.
        monkeypatch.setattr(store, 'binding_fingerprint', lambda: 'f' * 64)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert not gated


def test_binding_fingerprint_dynamically_includes_new_modules_only(screen_source):
    data = screen_source
    before = store.binding_fingerprint()
    (data['binding'] / 'test_new.py').write_text('# test change\n')
    (data['binding'] / 'measure.py').write_text('# measurement change\n')
    (data['binding'] / 'measurement_identity.py').write_text('# launcher identity change\n')
    (data['binding'] / 'measurement_lifecycle.py').write_text('# launcher lifecycle change\n')
    (data['binding'] / 'live_campaign.py').write_text('# measurement-only campaign launcher change\n')
    assert store.binding_fingerprint() == before
    (data['binding'] / 'identity.py').write_text('# newly imported runtime\n')
    assert store.binding_fingerprint() != before
    assert 'identity.py' in store.binding_source_files()
    assert 'provenance.py' in store.binding_source_files()
    assert 'confirmation.py' in store.binding_source_files()


def test_screen_checkpoint_rejects_new_runtime_module_absent_from_snapshot(screen_source, tmp_path):
    import os
    data = screen_source
    path = data['binding'] / 'identity.py'
    path.write_text('# new runtime\n')
    os.utime(path, (data['started']-10, data['started']-10))
    row = entry() | dict(source_screen=str(data['source'].relative_to(data['root'])))
    with pytest.raises(ValueError, match='rerun_required'):
        store.save_entry(row, store_dir=tmp_path / 'store')


def test_direct_regate_rejects_stale_binding_before_expensive_gate(monkeypatch):
    from experiments.mvp.pipeline.lattice import gates
    row = entry() | dict(binding_source_sha256='0' * 64)
    monkeypatch.setattr(gates, 'engine_identity', lambda: pytest.fail('stale source reached engine gate'))
    assert store._regate_entry(row) is None


def test_lookup_fails_closed_when_fresh_checker_raises(tmp_path, monkeypatch):
    from experiments.mvp.pipeline.lattice import gates
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    monkeypatch.setattr(store, '_current_token', lambda row: (sha256_json(row), 'epoch'))
    monkeypatch.setattr(gates, 'engine_identity', lambda: dict(passed=True, engine_version='0.13.0',
                                                           engine_manifest_sha256='b' * 64))
    def broken_checker(*args, **kwargs):
        raise RuntimeError('native checker unavailable')
    monkeypatch.setattr(gates, 'gate_plan', broken_checker)
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None


def test_lookup_preserves_process_control_exceptions(tmp_path, monkeypatch):
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    def interrupted(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(store, '_current_token', interrupted)
    with pytest.raises(KeyboardInterrupt):
        store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)


def test_snapshot_is_immutable_and_concurrent_launch_never_reads_replacement_under_old_pin(tmp_path, gated, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    rows = [entry(), entry()]
    rows[1]['canonical_rules'] = [CAP]
    for row in rows:
        store.save_entry(row, store_dir=tmp_path)
    snapshot = store._catalogue(tmp_path, refresh=True)
    with pytest.raises(TypeError):
        snapshot[next(iter(snapshot))] = 'f'*64
    pin_a = store.catalogue_sha256(store_dir=tmp_path)
    first, release = threading.Event(), threading.Event()
    observed = []
    def gate(row):
        thread = threading.current_thread().name
        observed.append((thread, row['plan']['id']))
        if thread.startswith('launch-a') and not first.is_set():
            first.set()
            assert release.wait(10)
        return dict(row, rechecked_utc='2026-11-02T01:00:00Z', checker={'passed': True})
    monkeypatch.setattr(store, '_regate_entry', gate)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='launch-a') as executor:
        future = executor.submit(store.regate, store_dir=tmp_path, expected_catalogue_sha256=pin_a)
        assert first.wait(10)
        second_key = sorted(snapshot)[1]
        replacement = next(row for row in rows if store.entry_key(row['instance_sha256'], row['canonical_rules']) == second_key)
        replacement = deepcopy(replacement)
        replacement['plan']['id'] = 'replacement-only-in-catalogue-b'
        store.save_entry(replacement, store_dir=tmp_path)
        pin_b = store.catalogue_sha256(store_dir=tmp_path)
        newer = store.regate(store_dir=tmp_path, expected_catalogue_sha256=pin_b)
        release.set()
        older = future.result(timeout=10)
    assert len(newer['passed']) == 2
    assert not older['passed'] and 'superseded' in older['errors']['catalogue']
    assert not any(thread.startswith('launch-a') and name == 'replacement-only-in-catalogue-b' for thread, name in observed)
    assert store.lookup(replacement['instance_sha256'], replacement['canonical_rules'], store_dir=tmp_path)['plan']['id'] == replacement['plan']['id']


def test_failed_pin_during_inflight_lookup_prevents_stale_cache_publication(tmp_path, gated, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    entered, release = threading.Event(), threading.Event()
    def gate(candidate):
        entered.set()
        assert release.wait(10)
        return dict(candidate, rechecked_utc='now', checker={'passed': True})
    monkeypatch.setattr(store, '_regate_entry', gate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.lookup, row['instance_sha256'], [CLOSE], store_dir=tmp_path)
        assert entered.wait(10)
        assert store.regate(store_dir=tmp_path, expected_catalogue_sha256='f'*64)['errors']
        release.set()
        assert future.result(timeout=10) is None
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert not any(key[0] == str(tmp_path.resolve()) for key in store._gated)


def test_explicit_candidate_handoff_authenticates_across_threads_without_trusting_mutations(tmp_path, gated):
    from concurrent.futures import ThreadPoolExecutor
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    candidate = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    with ThreadPoolExecutor(max_workers=1) as executor:
        digest = executor.submit(store.candidate_sha256, row['instance_sha256'], [CLOSE],
                                 store_dir=tmp_path, candidate=candidate).result(timeout=10)
    assert digest == sha256_json(candidate)
    candidate['plan']['id'] = 'unauthenticated replacement'
    assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path, candidate=candidate) is None


@pytest.fixture
def isolated_reader(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    path = tmp_path/'reader_recheck.py'
    raw = b'LIMIT = 1\ndef validate(value):\n    return value <= LIMIT\n'
    path.write_bytes(raw)
    name = 'lattice_isolated_reader'
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(store, '_READER_MODULES', {name: path})
    monkeypatch.setattr(store, '_READER_AT_IMPORT', {name: raw})
    monkeypatch.setattr(store, '_reader_attested', {})
    return name, path, module


def test_reader_loaded_code_must_match_disk_even_before_first_identity(isolated_reader, monkeypatch):
    name, path, module = isolated_reader
    raw = path.read_bytes().replace(b'<= LIMIT', b'> LIMIT')
    path.write_bytes(raw)
    monkeypatch.setattr(store, '_READER_AT_IMPORT', {name: raw})
    with pytest.raises(ValueError, match='loaded implementation differs'):
        store._reader_identity()


def test_reader_loaded_constants_must_match_disk_even_before_first_identity(isolated_reader, monkeypatch):
    name, path, module = isolated_reader
    raw = path.read_bytes().replace(b'LIMIT = 1', b'LIMIT = 2')
    path.write_bytes(raw)
    monkeypatch.setattr(store, '_READER_AT_IMPORT', {name: raw})
    with pytest.raises(ValueError, match='loaded constant differs'):
        store._reader_identity()


@pytest.mark.parametrize('change', ['disk', 'loaded_code', 'loaded_constant'])
def test_reader_source_or_loaded_implementation_drift_withholds_cached_plan(tmp_path, gated, monkeypatch, isolated_reader, change):
    name, path, module = isolated_reader
    row = entry()
    store.save_entry(row, store_dir=tmp_path)
    monkeypatch.setattr(store, '_current_token', lambda row: (sha256_json(row), store._reader_identity()))
    fresh = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert fresh and len(gated) == 1
    if change == 'disk':
        path.write_text(path.read_text().replace('<= LIMIT', '> LIMIT'))
    elif change == 'loaded_code':
        module.validate = lambda value: True
    else:
        module.LIMIT = 99
    assert store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert store.candidate_sha256(row['instance_sha256'], [CLOSE], store_dir=tmp_path) is None
    assert len(gated) == 1


def test_current_gate_token_includes_reader_identity(monkeypatch):
    from experiments.mvp.pipeline.lattice import gates
    from app import stage_physics
    monkeypatch.setattr(gates, 'engine_identity', lambda: {'passed': True, 'engine_manifest_sha256': 'e'*64})
    monkeypatch.setattr(stage_physics, 'physics_drift', lambda: None)
    monkeypatch.setattr(stage_physics, 'physics_epoch', lambda: 'unchanged-physics')
    monkeypatch.setattr(store, '_reader_identity', lambda: 'reader-a')
    before = store._current_token(entry())
    monkeypatch.setattr(store, '_reader_identity', lambda: 'reader-b')
    assert store._current_token(entry()) != before


def test_runtime_view_marks_historical_timeout_allowance_unmeasured(tmp_path, gated):
    row = entry() | dict(budget_status='timeout_with_checked_incumbent', wall_s=900,
                         native_search_wall_s=900, profile={'engine_timeout_s': 900.})
    path = store.save_entry(row, store_dir=tmp_path)
    original = path.read_bytes()
    result = store.lookup(row['instance_sha256'], [CLOSE], store_dir=tmp_path)
    assert result['wall_s'] is result['native_search_wall_s'] is None
    assert result['timeout_allowance_s'] == 900 and result['historical_timeout_elapsed'] == 'unavailable'
    assert result['historical_nominal_timing']['wall_s'] == 900
    assert path.read_bytes() == original


def test_runtime_view_marks_historical_pool_retry_scope_unknown():
    result = store._lookup_label(dict(computed_utc='2026-11-01T01:00:00Z',
        rechecked_utc='2026-11-02T01:00:00Z', plan_producer='lattice.standard_plan_pool',
        release_wall_s=130., gate={'wall_s': 7.}), fresh=False)
    assert result['release_wall_s'] is None
    assert result['historical_release_including_admission_s'] == 130.
    assert result['native_gate_wall_s'] == 7.
    assert result['release_timing_scope'] == 'historical_mixed_scope'
