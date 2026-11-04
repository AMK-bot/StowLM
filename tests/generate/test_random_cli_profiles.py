"""CLI validation must precede both fresh generation and resumable shard lookup."""
import pytest

from stowage.generate import random_yard as random
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset


@pytest.mark.parametrize('resume', [False, True])
def test_off_cli_refuses_stress_even_when_ordinary_shard_can_resume(monkeypatch, tmp_path, resume):
    geometry = load_fleet_preset('feeder_500', 'full', with_hull=False)
    monkeypatch.setattr(random, '_full_vessel', lambda name: geometry)
    monkeypatch.setattr(random, 'accept_background_candidate', lambda *args: object())
    args = ['--size', 'tiny', '--seed', '13', '--output', str(tmp_path)]
    shard = tmp_path / 'tiny' / 'seed-00000013.json'
    sidecar = shard.with_suffix('.audit.json')
    with rule_freeze_v1(enabled=False):
        if resume:
            assert random.main(args) == 0
        before = (shard.read_bytes(), sidecar.read_bytes()) if resume else None
        with pytest.raises(ValueError, match='activation'):
            random.main([*args, '--cargo-profile', 'dg_stress'])
    if resume:
        assert (shard.read_bytes(), sidecar.read_bytes()) == before
    else:
        assert not shard.exists()
