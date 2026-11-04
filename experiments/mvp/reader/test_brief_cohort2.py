"""Pure D222 cohort regressions; no paid API, planner, or checker workloads."""
from collections import Counter
import hashlib
import itertools
import json

import pytest

from experiments.mvp.reader import brief_cohort2 as module
from experiments.mvp.reader.brief_cohort import prepare as prepare_first
from experiments.mvp.reader.test_brief_cohort import fixture, ship


def first_cohort(root):
    fixture(root)
    return prepare_first(root=root, count=3)


def test_finite_universe_enumerates_real_bays_and_physical_tier_ranges():
    raw = ship()
    universe = module.candidate_universe(raw)
    closures = [c['expected']['constraint']['bay'] for c in universe if c['type'] == 'close_bay']
    assert closures == [1, 2, 3]
    tiers = [c['expected']['constraint'] for c in universe if c['type'] == 'max_tiers']
    assert len(tiers) == 4 * 2 * 3  # global + three physical scopes; two zones; caps 0..2
    assert {c['bay'] for c in tiers} == {None, 1, 2, 3}
    assert {c['max_tiers'] for c in tiers} == {0, 1, 2}
    assert not any(c['type'] == 'dg_keep_away' for c in universe)
    assert len({c['candidate_id'] for c in universe}) == len(universe)
    assert len({json.dumps(c['expected'], sort_keys=True) for c in universe}) == len(universe)


def test_dg_candidates_require_real_matching_active_cargo_and_normalize_division():
    raw = ship(dg={'imdg_class': '1.4S', 'subsidiary_risks': ['3'], 'additional': []})
    dg = [c for c in module.candidate_universe(raw) if c['type'] == 'dg_keep_away']
    assert dg
    assert {c['expected']['constraint']['imdg_class'] for c in dg} == {'1.4', '3'}
    assert all(c['matching_active_cargo'] == ['ABCU0000001'] and not c['vacuous'] for c in dg)
    assert all(c['expected']['constraint']['away_from']['bay'] in (1, 2, 3) for c in dg)
    raw['load_list'] = []
    assert not any(c['type'] == 'dg_keep_away' for c in module.candidate_universe(raw))


def test_admission_records_impossible_candidates_and_prefers_tightening():
    universe = module.candidate_universe(ship())
    assert any(c['static_screen']['proven_impossible'] for c in universe)
    assert any(not c['is_tightening'] for c in universe)
    admitted = module.eligible_candidates(universe)
    assert admitted
    assert all(c['is_tightening'] and not c['static_screen']['proven_impossible'] for c in admitted)
    physical_caps = [c for c in universe if not c['is_tightening']]
    assert module.eligible_candidates(physical_caps) == physical_caps


@pytest.mark.parametrize('available', [
    [set(module.TYPES)] * 6,
    [{'max_tiers', 'dg_keep_away'}] * 5,
    [{'close_bay', 'max_tiers'}, {'max_tiers', 'dg_keep_away'}, {'close_bay'}],
    [{'max_tiers'}, {'max_tiers'}, {'max_tiers', 'dg_keep_away'}, {'dg_keep_away'}],
])
def test_type_matching_is_seeded_admissible_and_globally_balanced(available):
    result = module.balanced_assignment(available)
    assert result == module.balanced_assignment(available)
    assert all(kind in kinds for kind, kinds in zip(result, available))
    def cost(assignment):
        counts = Counter(assignment)
        return sum(counts[kind] ** 2 for kind in module.TYPES)
    assert cost(result) == min(map(cost, itertools.product(*available)))


def test_missing_candidate_keeps_map_and_fails_instead_of_swapping():
    with pytest.raises(ValueError, match='no statically admitted instruction'):
        module.balanced_assignment([{'max_tiers'}, set()])


def test_build_keeps_exact_first_maps_order_and_finishes_draw_before_baseline_evaluation(tmp_path, monkeypatch):
    first = first_cohort(tmp_path)
    original_universe, original_violations = module.candidate_universe, module.baseline_violations
    calls = []
    def universe(raw):
        calls.append('universe')
        return original_universe(raw)
    def violations(*args):
        assert calls.count('universe') == 3
        calls.append('baseline')
        return original_violations(*args)
    monkeypatch.setattr(module, 'candidate_universe', universe)
    monkeypatch.setattr(module, 'baseline_violations', violations)
    cohort = module.build_cohort(root=tmp_path)
    assert [c['input'] for c in cohort['cases']] == [c['input'] for c in first['cases']]
    assert [c['instance_id'] for c in cohort['cases']] == [c['instance_id'] for c in first['cases']]
    assert calls == ['universe'] * 3 + ['baseline'] * 3
    assert all(not c['static_screen']['proven_impossible'] for c in cohort['cases'])
    assert cohort['no_matching_cargo_instruction_count'] == 0
    assert cohort['first_cohort_sha256'] == hashlib.sha256((tmp_path/'experiments/mvp/reader/runs/brief/cohort.json').read_bytes()).hexdigest()
    assert not cohort['selection_is_blind']
    assert cohort['candidate_universe_sha256'] == module._sha(module._bytes(cohort['candidate_universe']))


def test_draw_does_not_depend_on_stored_baseline_outcomes(tmp_path):
    roots = [tmp_path / 'one', tmp_path / 'two']
    results = []
    for index, root in enumerate(roots):
        directory = fixture(root)
        if index:
            for path in directory.glob('map-*.json'):
                record = json.loads(path.read_text())
                record.update(passed=not record['passed'], weighted=999)
                path.write_text(json.dumps(record))
        prepare_first(root=root, count=3)
        results.append(module.build_cohort(root=root))
    assert [c['expected'] for c in results[0]['cases']] == [c['expected'] for c in results[1]['cases']]
    assert results[0]['draw_manifest'] == results[1]['draw_manifest']
    assert results[0]['candidate_universe'] == results[1]['candidate_universe']


def test_atomic_freeze_is_idempotent_integrity_checked_and_preserves_first(tmp_path, monkeypatch):
    first_cohort(tmp_path)
    first_dir = tmp_path/'experiments/mvp/reader/runs/brief'
    before = {p.name: p.read_bytes() for p in first_dir.iterdir()}
    output = tmp_path/'second'
    cohort = module.prepare(output, root=tmp_path)
    assert {p.name: p.read_bytes() for p in first_dir.iterdir()} == before
    payload = (output/'cohort.json').read_bytes()
    prereg_payload = (output/'preregister.json').read_bytes()
    prereg = json.loads(prereg_payload)
    assert prereg['cohort_sha256'] == hashlib.sha256(payload).hexdigest()
    assert prereg['first_cohort_preserved']
    assert prereg['screen_admission_is_not_feasibility']
    monkeypatch.setattr(module, 'build_cohort', lambda **kwargs: pytest.fail('reselection'))
    assert module.prepare(output, root=tmp_path) == cohort
    assert (output/'cohort.json').read_bytes() == payload
    assert (output/'preregister.json').read_bytes() == prereg_payload
    with pytest.raises(ValueError, match='configuration'):
        module.prepare(output, root=tmp_path, seed=0)
    damaged = json.loads(payload)
    damaged['cases'][0]['sentence'] = 'tampered'
    (output/'cohort.json').write_text(json.dumps(damaged))
    with pytest.raises(ValueError, match='integrity'):
        module.prepare(output, root=tmp_path)


def test_source_drift_and_first_hash_mismatch_fail_closed(tmp_path):
    first = first_cohort(tmp_path)
    raw_path = tmp_path/first['cases'][0]['input']
    payload = raw_path.read_bytes()
    raw_path.write_bytes(payload+b' ')
    with pytest.raises(ValueError, match='input changed'):
        module.build_cohort(root=tmp_path)
    raw_path.write_bytes(payload)
    first_path = tmp_path/'experiments/mvp/reader/runs/brief/cohort.json'
    first_path.write_bytes(first_path.read_bytes()+b' ')
    with pytest.raises(ValueError, match='preregistration hash'):
        module.build_cohort(root=tmp_path)
