"""Measured development regressions; saved rows are never constructor inputs."""
import json
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parent


def rows(version):
    folder=HERE/'results'/f'quick-{version}'
    if not (folder/'summary.json').exists():
        pytest.skip(f'{version} quick measurement not available yet')
    return {r['instance_id']:r for f in folder.glob('*.json')
            for r in [json.loads(f.read_text())] if r.get('complete')}


def test_012_medium_keeps_both_011_passes_and_lift_ceilings():
    old,new=rows('0.11.0'),rows('0.12.0')
    for identity,ceiling in [('D192.medium-feeder_500-f60-high-s192200000',71.),
                            ('D192.medium-feeder_800-f60-low-s192200101',116.)]:
        assert new[identity]['input_sha256']==old[identity]['input_sha256']
        assert new[identity]['passed'] is True
        assert new[identity]['lifts']<=ceiling


def test_012_bound_and_proof_scope_respect_both_versions_checked_uppers():
    old,new=rows('0.11.0'),rows('0.12.0')
    assert len(new)==21
    for identity,row in new.items():
        evidence=row['method_metadata']['engine'];bound=evidence['lower_bound']
        if bound is not None:
            assert evidence['lower_bound_name']
            for upper in (row,old[identity]):
                if upper['passed']:
                    assert bound<=upper['lifts'],identity
        if evidence['proved_optimal']:
            assert row['passed'] and row['lifts']==bound
            scope=evidence['proof_scope']
            assert isinstance(scope,str) and evidence['lower_bound_name'] in scope
            assert 'D15' in scope and 'fixed stow' in scope and 'no later loads' in scope
