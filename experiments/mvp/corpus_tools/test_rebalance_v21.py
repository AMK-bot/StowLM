"""D90 inventory contracts; no generation, solver, GPU or cloud work."""
from collections import Counter
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import rebalance_v21 as rebalance


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(rebalance, 'QUOTAS', {'train': (3, 3, 2), 'val': (3, 2, 1)})
    for split, base in [('train', 30_100_000), ('val', 40_100_000)]:
        tiny = build.specs_for(split, {'tiny': 3})
        small = []
        for i in range(12):
            vessel = build.VESSELS[i % 2]
            spec = build.specs_for(split, {'small': 1})[0]
            spec.update(seed=base + i, vessel=vessel,
                        generator=build.FAMILIES[(i // 2 + i % 2) % 2],
                        **rebalance.SETTINGS[vessel])
            small.append(spec)
        build.atomic_json(tmp_path / f'{split}.requested.json',
                          {'specs': tiny + small, 'counts': {'tiny': 3, 'small': 12, 'medium': 0}})
    build.atomic_json(tmp_path / 'token_allocation.json',
                      {'allocation': {'total_instances': 20, 'estimated_token_shares': None}})
    return tmp_path


def commit(root, spec):
    path = build.shard_path(spec, root)
    build.atomic_json(path, {'synthetic': True})
    build.atomic_json(path.with_suffix('.audit.json'),
        {'config': spec, 'accepted': True, 'rule_freeze_v1_enabled': False,
         'instance_sha256': build.sha256(path)})
    return path


def test_preserves_completed_and_tiny_and_balances_families(corpus):
    old = json.loads((corpus / 'train.requested.json').read_text())
    kept = next(s for s in old['specs'] if s['rung'] == 'small')
    path = commit(corpus, kept)
    before = path.read_bytes()
    result = rebalance.plan(corpus)
    for split, row in result.items():
        small = [s for s in row['specs'] if s['rung'] == 'small']
        assert Counter(s['vessel'] for s in small) == dict(zip(build.VESSELS, rebalance.QUOTAS[split]))
        assert len(set(Counter(s['generator'] for s in small).values())) == 1
        assert small[0]['vessel'] == 'feeder_1700'
        assert [s for s in row['specs'] if s['rung'] == 'tiny'] == build.specs_for(split, {'tiny': 3})
    assert kept in result['train']['specs']
    assert path.read_bytes() == before


def test_execute_is_idempotent_with_protected_hashes(corpus):
    old = json.loads((corpus / 'train.requested.json').read_text())
    commit(corpus, next(s for s in old['specs'] if s['rung'] == 'small'))
    first = rebalance.apply(corpus)
    bytes_before = {s: (corpus / f'{s}.requested.json').read_bytes() for s in ('train', 'val')}
    second = rebalance.apply(corpus)
    assert first == second
    assert bytes_before == {s: (corpus / f'{s}.requested.json').read_bytes() for s in ('train', 'val')}
    assert (corpus / 'archive/v2-before-D90/train.requested.json').exists()


def test_refuses_to_drop_completed_tail(corpus):
    old = json.loads((corpus / 'train.requested.json').read_text())
    tail = [s for s in old['specs'] if s['rung'] == 'small' and s['vessel'] == 'feeder_500'][-1]
    commit(corpus, tail)
    with pytest.raises(ValueError, match='exceed D90 prefix quota'):
        rebalance.plan(corpus)


def test_new_seed_namespace_is_disjoint(corpus):
    inventories = rebalance.plan(corpus)
    seeds = [s['seed'] for row in inventories.values() for s in row['specs']]
    assert len(set(seeds)) == len(seeds)
    for split, row in inventories.items():
        new = [s for s in row['specs'] if s['vessel'] == 'feeder_1700' and s['rung'] == 'small']
        assert all(s['seed'] >= {'train': 31_100_000, 'val': 41_100_000}[split] for s in new)
        assert all(s['booking_slack'] == .35 and s['booking_mass_fraction'] == .45 for s in new)
