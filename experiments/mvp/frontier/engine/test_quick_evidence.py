"""Cross-map regression of measured gates; no saved result is a planner start."""
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _rows(version):
    folder = HERE / 'results' / ('quick-' + version)
    summary = folder / 'summary.json'
    if not summary.exists():
        pytest.skip('full ' + version + ' quick measurement is not available yet')
    return {r['instance_id']: r for p in folder.glob('*.json') if p.name != 'summary.json'
            for r in [json.loads(p.read_text())] if r.get('complete')}


def test_medium_scaling_merge_retains_or_improves_both_checked_stows():
    old, new = _rows('0.10.0'), _rows('0.11.0')
    for identity, ceiling in [('D192.medium-feeder_500-f60-high-s192200000', 79.),
                              ('D192.medium-feeder_800-f60-low-s192200101', 184.)]:
        assert new[identity]['input_sha256'] == old[identity]['input_sha256']
        assert new[identity]['passed'] is True
        assert new[identity]['lifts'] <= ceiling


def test_every_reported_map_bound_respects_both_checked_upper_bounds():
    old, new = _rows('0.10.0'), _rows('0.11.0')
    assert len(new) == 21
    for identity, row in new.items():
        evidence = row['method_metadata']['engine']
        bound = evidence['lower_bound']
        if bound is None:
            continue
        for upper in (row, old[identity]):
            if upper['passed']:
                assert bound <= upper['lifts'], identity
        if evidence['proved_optimal']:
            assert row['passed'] and row['lifts'] == bound
