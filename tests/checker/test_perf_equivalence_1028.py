"""Full-report equivalence on manuals, archived plans, convergence and boundaries."""
from __future__ import annotations

import importlib.util
import gzip
import hashlib
import json
from pathlib import Path

import pytest
import stowage.checker.full as full

_PATH = Path(__file__).resolve().parents[2] / 'experiments/checker/perf-1028/corpus.py'
_SPEC = importlib.util.spec_from_file_location('checker_perf1028_corpus', _PATH)
_CORPUS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_CORPUS)
_CASES = _CORPUS.cases()
_D171_REPORT = 'experiments/mvp/postfreeze/FAILURES-D171.md'
_D171_CURRENT_IDS = frozenset(
    f'current-{generator}-{ship}-{rung}'
    for generator in ('random_yard', 'simulated_yard')
    for ship in ('feeder_500', 'feeder_1000', 'feeder_1700')
    for rung in ('tiny-0', 'small-1', 'medium-2', 'tiny-3', 'small-4'))
_D171_BOUNDARY_IDS = frozenset(
    f'near-boundary-{ship}-{side}'
    for ship in ('feeder_1000', 'feeder_1700') for side in ('below', 'above'))


def _require_saved_parent_epoch(row, *, boundary=False):
    """Check pinned bytes/lineage before constructing expensive historical physics."""
    from stowage.vessel import fleet

    prefix = 'snapshot_' if boundary else ''
    path = _CORPUS.ROOT / row[prefix + 'instance']
    payload = path.read_bytes()
    assert _CORPUS.digest(payload) == row[prefix + 'instance_sha256'], f'corpus source changed: {path}'
    plan_path = _CORPUS.ROOT / row[prefix + 'plan']
    assert _CORPUS.digest(plan_path.read_bytes()) == row[prefix + 'plan_sha256'], f'corpus source changed: {plan_path}'
    saved = json.loads(gzip.decompress(payload) if path.suffix == '.gz' else payload)
    records = [source for source in saved['vessel']['sources'] if source.startswith('params/fleet/')]
    assert len(records) == 1
    ship = _CORPUS.canonical_ship(row['ship'])
    if ship != row['ship']:
        records = _CORPUS._rename(records, row['ship'], ship)
    expected = f'params/fleet/{ship}.toml sha256:' + hashlib.sha256(fleet._parameter_bytes(ship)).hexdigest()
    if records[0] != expected:
        cause = ('D171 D: current C1 manual differs from the pinned boundary physical epoch' if boundary else
                 'D171 B: saved generated vessel belongs to an obsolete parent-parameter epoch')
        pytest.skip(cause + '; see ' + _D171_REPORT)


def test_perf_corpus_covers_declared_sources():
    saved = [row for row in _CASES if row['kind'] == 'saved']
    assert len(saved) >= 60
    assert sum(row.get('current_generated', False) for row in saved) == 30
    assert {row['generator'] for row in saved} == {'random_yard', 'simulated_yard'}
    assert {row['ship'] for row in saved} == {'feeder_500', 'feeder_1000', 'feeder_1700'}
    assert {'tiny', 'small', 'medium'} <= {row['rung'] for row in saved}
    for kind in ('manual', 'convergence'):
        assert {(row['ship'], row['condition']) for row in _CASES if row['kind'] == kind} == {
            (ship, condition) for ship in ('feeder_500', 'feeder_1000', 'feeder_1700')
            for condition in ('C1', 'C2', 'C3')}
    assert len([row for row in _CASES if row['kind'] == 'near_boundary']) == 6


@pytest.mark.parametrize('row', _CASES, ids=[row['id'] for row in _CASES])
def test_perf_full_report_equivalence(row, record_property):
    if row['id'] in _D171_CURRENT_IDS:
        _require_saved_parent_epoch(row)
    elif row['id'] in _D171_BOUNDARY_IDS:
        _require_saved_parent_epoch(row, boundary=True)
    instance, plan = _CORPUS.prepare(row)
    original = instance.model_dump_json(), plan.model_dump_json()
    reference = full.check_reference(instance, plan)
    optimized = full.check(instance, plan)
    changes = _CORPUS.compare_reports(reference, optimized)
    record_property('non_bitwise_numeric_changes', json.dumps(changes))
    record_property('reference_passed', str(reference.passed))
    record_property('report_parts', str(len(reference.parts)))
    record_property('sampled_part_runs', json.dumps({
        key: part.ran for key, part in reference.parts.items() if key.startswith('condition:')}))
    if row['kind'] == 'near_boundary':
        departure = reference.parts[f'condition:{instance.voyage.departure.id}:stability']
        assert departure.ran, departure.error
        gm = departure.metrics['gm_m']
        actual_delta = row['minimum_gm'] - gm
        record_property('boundary_departure_gm_m', gm)
        record_property('boundary_minimum_gm_m', row['minimum_gm'])
        record_property('boundary_actual_delta_m', actual_delta)
        record_property('boundary_departure_gm_shift_m', gm - row['departure_gm_reference'])
        assert gm == pytest.approx(row['departure_gm_reference'], rel=0., abs=1e-9), (
            'Pinned boundary GM shifted', row['departure_gm_reference'], gm, actual_delta)
    if row.get('current_generated'):
        condition_ids = (instance.voyage.departure.id,
                         f'{instance.voyage.departure.id}:mid_voyage',
                         instance.voyage.arrival.id)
        for condition_id in condition_ids:
            for name in ('stability', 'strength', 'visibility', 'lashing'):
                key = f'condition:{condition_id}:{name}'
                for report in (reference, optimized):
                    part = report.parts[key]
                    assert part.ran, (key, part.error)
    assert (instance.model_dump_json(), plan.model_dump_json()) == original
    # A three-state input must retain every sampled part, even on failing plans.
    assert sum(key.startswith('condition:') and key.endswith(':stability')
               for key in reference.parts) == 3
