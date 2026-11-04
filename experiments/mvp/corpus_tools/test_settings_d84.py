"""D84 corpus inventory/settings contracts; no solver or generation calls."""
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build
import campaign

SETTINGS = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
            "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45}}


def test_d84_inventory_preserves_tiny_balances_ships_and_families(tmp_path):
    allocation = tmp_path / 'allocation.json'
    train_counts = {'tiny': 870, 'small': 1242, 'medium': 738}
    val_counts = {'tiny': 48, 'small': 66, 'medium': 36}
    historical = {'counts_by_rung': {'tiny': 918, 'small': 1308, 'medium': 774},
                  'total_instances': 3000, 'estimated_token_shares': {'tiny': .1, 'small': .3, 'medium': .6},
                  'estimated_tokens_by_rung': {'tiny': 10, 'small': 30, 'medium': 60},
                  'target_token_shares': {'tiny': .1, 'small': .3, 'medium': .6},
                  'method': 'v1 measured lower bounds', 'count_granularity': 6}
    build.atomic_json(allocation, {'allocation': {'train_counts_by_rung': train_counts,
                      'val_counts_by_rung': val_counts, **historical}})
    root = tmp_path / 'corpus'
    original = campaign.prepare(allocation, root)
    current = campaign.prepare(allocation, root, ship_settings=SETTINGS,
                               small_train=2700, small_val=70, migrate=True)
    seeds = set()
    for split, n in [('train', 2700), ('val', 70)]:
        assert [s for s in current[split] if s['rung'] == 'tiny'] == [s for s in original[split] if s['rung'] == 'tiny']
        small = [s for s in current[split] if s['rung'] == 'small']
        assert len(small) == n
        assert not any(s['rung'] == 'medium' for s in current[split])
        for vessel in SETTINGS:
            rows = [s for s in small if s['vessel'] == vessel]
            assert len(rows) == n // 2
            assert all(all(s[k] == v for k, v in SETTINGS[vessel].items()) for s in rows)
        assert sum(s['generator'] == 'random_yard' for s in small) == n // 2
        for spec in current[split]:
            assert spec['seed'] not in seeds
            seeds.add(spec['seed'])
    metadata_path = root / 'token_allocation.json'
    metadata = json.loads(metadata_path.read_text())['allocation']
    assert metadata['counts_by_rung'] == {'tiny': 918, 'small': 2770, 'medium': 0}
    assert metadata['total_instances'] == 3688
    assert metadata['train_counts_by_rung'] == {'tiny': 870, 'small': 2700, 'medium': 0}
    assert metadata['val_counts_by_rung'] == {'tiny': 48, 'small': 70, 'medium': 0}
    assert metadata['estimated_token_shares'] is None
    assert metadata['estimated_tokens_by_rung'] is None
    assert 'unmeasured' in metadata['limitation']
    assert metadata['historical_v1_token_sizing'] == historical
    assert 'target_token_shares' not in metadata
    assert 'count_granularity' not in metadata
    metadata_before_repeat = metadata_path.read_bytes()
    assert campaign.prepare(allocation, root, ship_settings=SETTINGS,
                            small_train=2700, small_val=70, migrate=True) == current
    assert metadata_path.read_bytes() == metadata_before_repeat
    queued = campaign.jobs(root)
    assert all(s['split'] == 'train' for s in queued[:len(current['train'])])


def test_mass_api_validation_and_settings_file(tmp_path):
    def producer(size, *, booking_slack=None, booking_mass_fraction=None):
        pass
    assert build.booking_slack_kwargs(producer, .25, .55) == SETTINGS['feeder_500']
    def unsupported(size, *, booking_slack=None):
        pass
    with pytest.raises(ValueError, match='booking_mass_fraction producer API'):
        build.booking_slack_kwargs(unsupported, .25, .55)
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps(SETTINGS))
    assert build.booking_settings(ship_settings=path) == SETTINGS
    assert build.booking_settings(ship_settings=json.dumps(SETTINGS)) == SETTINGS
    with pytest.raises(ValueError):
        build.booking_settings(ship_settings={'feeder_500': {'booking_slack': True}})


def test_generation_environment_restores_even_on_failure(monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4', '1')
    with pytest.raises(RuntimeError):
        with build.generation_environment() as stage:
            assert 'STOWAGE_BALLAST_V4' not in os.environ
            assert stage['finalization']['STOWAGE_BALLAST_V4'] == '1'
            raise RuntimeError('producer failed')
    assert os.environ['STOWAGE_BALLAST_V4'] == '1'
    monkeypatch.delenv('STOWAGE_BALLAST_V4')
    with build.generation_environment():
        assert 'STOWAGE_BALLAST_V4' not in os.environ
    assert 'STOWAGE_BALLAST_V4' not in os.environ
