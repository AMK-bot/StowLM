"""Pure cohort tests: no paid requests, planners, checkers, or held-out data."""
import hashlib
import fcntl
import json

import pytest

from experiments.mvp.reader import brief_cohort as module


def ship(identifier='synthetic', *, dg=None):
    cells, stacks = [], []
    for bay in (1, 3):
        for deck in (False, True):
            sid = f's{bay}-{deck}'
            ids = [f'{bay:03d}01{tier:02d}' for tier in ((82, 84) if deck else (2, 4))]
            stacks.append({'id': sid, 'bay': bay, 'on_deck': deck, 'cell_ids': ids})
            cells.extend({'id': c, 'stack_id': sid, 'accepts_20': True,
                          'accepts_40': True, 'accepts_45': False} for c in ids)
    return {'id': identifier, 'vessel': {'cells': cells, 'stacks': stacks,
            'hatch_covers': [], 'exclusion_regions': [], 'bay_pairs': [{'even_bay': 2}]},
            'containers': [{'id': 'ABCU0000001', 'length_ft': 20, 'dg': dg}],
            'load_list': ['ABCU0000001'], 'discharge_list': [], 'onboard': {'placements': []},
            'rotation': {'calls': []}, 'call_index': 0}


def envelope(constraint):
    return {'schema_version': 'stowlm.reader/2', 'constraint': constraint}


def plan(*moves):
    return {'id': 'p', 'instance_id': 'synthetic', 'call_index': 0, 'moves': list(moves)}


def fixture(root, count=4):
    directory = root / 'experiments/mvp/frontier/runs/val-0.9'
    directory.mkdir(parents=True)
    for index in reversed(range(count)):
        raw = ship(f'synthetic-{index}')
        input_path = f'experiments/mvp/corpus/val/small/fake/map-{index}.json'
        destination = root / input_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(raw))
        row = {'input': input_path, 'input_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
               'passed': index != 0, 'weighted': index, 'plan': plan()}
        (directory / f'map-{index:02d}.json').write_text(json.dumps(row))
    (directory / 'AUDIT.json').write_text(json.dumps({'metadata': True}))
    return directory


def test_generator_is_seeded_context_valid_and_cycles_without_cherry_picking():
    raw = ship()
    results = [module.instruction_for(raw, index) for index in range(6)]
    assert [r['type'] for r in results] == list(module.TYPES) * 2
    assert results == [module.instruction_for(raw, index) for index in range(6)]
    assert results[2]['instruction_selection']['vacuous']
    assert results[2]['expected']['constraint']['imdg_class'] == '3'
    assert all(r['standing_rules'] == ['One port per hatch.'] for r in results)
    assert raw == ship()  # no mutation


def test_dg_uses_actual_cargo_including_subsidiary_risk():
    raw = ship(dg={'imdg_class': '8', 'subsidiary_risks': ['3'], 'additional': []})
    row = module.instruction_for(raw, 2)
    assert row['expected']['constraint']['imdg_class'] in ('3', '8')
    assert row['instruction_selection']['matching_active_cargo'] == ['ABCU0000001']
    assert not row['instruction_selection']['vacuous']


def test_size_footprint_screen_proves_impossible_without_global_shortfall():
    raw = ship(dg={'imdg_class': '3', 'subsidiary_risks': [], 'additional': []})
    raw['containers'][0]['length_ft'] = 40
    target = envelope({'type': 'dg_keep_away', 'imdg_class': '3', 'min_bays': 1,
                       'away_from': {'kind': 'bay', 'bay': 1}})
    screen = module.static_screen(raw, target)
    assert screen['proven_impossible']
    assert any(r['proof'] == 'no_size_compatible_allowed_footprint' for r in screen['reasons'])
    assert not any(r['proof'] == 'open_cell_capacity' for r in screen['reasons'])


def test_retained_tier_conflict_binds_but_closed_bay_unmoved_retained_does_not():
    raw = ship()
    raw['load_list'] = []
    raw['onboard']['placements'] = [{'container_id': 'ABCU0000001', 'slot': '0010104'}]
    tier = envelope({'type': 'max_tiers', 'bay': 1, 'zone': 'hold', 'max_tiers': 1})
    closed = envelope({'type': 'close_bay', 'bay': 1})
    assert module.static_screen(raw, tier)['proven_impossible']
    assert not module.static_screen(raw, closed)['proven_impossible']
    assert module.baseline_violations(raw, plan(), tier)
    assert not module.baseline_violations(raw, plan(), closed)


def test_baseline_placement_is_evaluated_against_independent_gold():
    raw = ship()
    target = envelope({'type': 'close_bay', 'bay': 2})
    bad = plan({'call_index': 0, 'kind': 'load', 'container_id': 'ABCU0000001', 'src': 'truck', 'dst': '0020102'})
    assert module.baseline_violations(raw, bad, target)
    assert not module.baseline_violations(raw, plan(), target)


def test_fixed_order_keeps_failed_baselines_and_freezes_all_before_screen(tmp_path, monkeypatch):
    fixture(tmp_path)
    calls = []
    original_instruction, original_screen = module.instruction_for, module.static_screen

    def instruction(*args):
        calls.append('instruction')
        return original_instruction(*args)

    def screen(*args):
        assert calls.count('instruction') == 3
        calls.append('screen')
        return original_screen(*args)

    monkeypatch.setattr(module, 'instruction_for', instruction)
    monkeypatch.setattr(module, 'static_screen', screen)
    cohort = module.build_cohort(root=tmp_path, count=3)
    assert [c['instance_id'] for c in cohort['cases']] == ['synthetic-0', 'synthetic-1', 'synthetic-2']
    assert not cohort['cases'][0]['baseline_passed']
    assert len(cohort['cases']) == 3
    assert calls == ['instruction'] * 3 + ['screen'] * 3


def test_prepare_is_idempotent_and_never_reselects_existing_cohort(tmp_path, monkeypatch):
    fixture(tmp_path)
    output = tmp_path / 'output'
    cohort = module.prepare(output, root=tmp_path, count=3)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    monkeypatch.setattr(module, 'build_cohort', lambda **kwargs: pytest.fail('attempted reselection'))
    assert module.prepare(output, root=tmp_path, count=3) == cohort
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
    with pytest.raises(ValueError, match='configuration'):
        module.prepare(output, root=tmp_path, count=3, seed=1)
    # A stable lock inode remains; ownership is released after every call.
    assert (output / '.prepare.lock').exists()


def test_integrity_mismatch_fails_without_overwriting(tmp_path):
    fixture(tmp_path)
    output = tmp_path / 'output'
    module.prepare(output, root=tmp_path, count=3)
    path = output / 'cohort.json'
    mutated = json.loads(path.read_text())
    mutated['cases'][0]['sentence'] = 'tampered'
    path.write_text(json.dumps(mutated))
    before = path.read_bytes()
    with pytest.raises(ValueError, match='integrity'):
        module.prepare(output, root=tmp_path, count=3)
    assert path.read_bytes() == before


def test_interrupted_preregister_creation_recovers_same_frozen_bytes(tmp_path):
    fixture(tmp_path)
    output = tmp_path / 'output'
    module.prepare(output, root=tmp_path, count=3)
    payload = (output / 'cohort.json').read_bytes()
    (output / 'preregister.json').unlink()
    module.prepare(output, root=tmp_path, count=3)
    assert (output / 'cohort.json').read_bytes() == payload
    assert json.loads((output / 'preregister.json').read_text())['cohort_sha256'] == hashlib.sha256(payload).hexdigest()


def test_no_nonvalidation_inputs_or_baseline_hash_drift(tmp_path):
    directory = fixture(tmp_path)
    target = directory / 'map-00.json'
    row = json.loads(target.read_text())
    row['input_sha256'] = '0' * 64
    target.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='hash mismatch'):
        module.build_cohort(root=tmp_path, count=3)
    row['input'] = row['input'].replace('/val/', '/test-v3/')
    target.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='non-validation'):
        module.build_cohort(root=tmp_path, count=3)


def test_lock_refuses_concurrent_preparation(tmp_path):
    path = tmp_path / '.prepare.lock'
    with path.open('a+b') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            module.prepare(tmp_path, count=3)
