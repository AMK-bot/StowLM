"""CPU admission verifies an explicitly prepared cache snapshot before GPU spend."""
import json

import pytest

from llm.rl import modal_grpo as grpo
from llm.rl import preflight


def prepared(tmp_path):
    src, cache = tmp_path / 'src', tmp_path / 'cache'
    src.mkdir(); cache.mkdir()
    (src / 'physics.py').write_text('qualified = True\n')
    (cache / 'kn.npz').write_bytes(b'qualified synthetic table')
    manifest = grpo.PinManifest.capture('a' * 40, src)
    instance = {'id': 'synthetic', 'metadata': {'seed': 1}}
    summary = {'complete': True, 'ships': [{'complete': True}]}
    preflight.write_cache_receipt(instance, manifest, cache, warmup_summary=summary)
    return instance, manifest, cache


def test_cache_preflight_rejects_missing_receipt(tmp_path):
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    with pytest.raises(RuntimeError, match='receipt'):
        preflight.preflight_physics_cache({}, manifest, tmp_path)


def test_cache_receipt_requires_successful_explicit_warmup(tmp_path):
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    with pytest.raises(RuntimeError, match='warm-up'):
        preflight.write_cache_receipt({}, manifest, tmp_path, warmup_summary={'complete': False})
    with pytest.raises(RuntimeError, match='empty'):
        preflight.write_cache_receipt({}, manifest, tmp_path,
                                     warmup_summary={'complete': True, 'ships': [{'complete': True}]})


def test_cache_preflight_accepts_matching_content(tmp_path):
    instance, manifest, cache = prepared(tmp_path)
    receipt = preflight.preflight_physics_cache(instance, manifest, cache)
    assert receipt['assets']['kn.npz']['size'] == len(b'qualified synthetic table')


@pytest.mark.parametrize('change', ['bytes', 'missing', 'extra', 'instance', 'manifest', 'symlink'])
def test_cache_preflight_rejects_content_or_identity_drift(tmp_path, change):
    instance, manifest, cache = prepared(tmp_path)
    if change == 'bytes': (cache / 'kn.npz').write_bytes(b'stale synthetic table')
    elif change == 'missing': (cache / 'kn.npz').unlink()
    elif change == 'extra': (cache / 'unexpected.npz').write_bytes(b'extra')
    elif change == 'instance': instance['metadata']['seed'] = 2
    elif change == 'manifest': manifest.versions['checker'] = 'stale'
    else: (cache / 'link.npz').symlink_to(cache / 'kn.npz')
    with pytest.raises(RuntimeError, match='cache'):
        preflight.preflight_physics_cache(instance, manifest, cache)


def test_cache_receipt_does_not_trust_claimed_asset_paths(tmp_path):
    instance, manifest, cache = prepared(tmp_path)
    path = cache / preflight.RECEIPT_NAME
    receipt = json.loads(path.read_text())
    receipt['assets']['../outside'] = receipt['assets'].pop('kn.npz')
    path.write_text(json.dumps(receipt))
    with pytest.raises(RuntimeError, match='cache'):
        preflight.preflight_physics_cache(instance, manifest, cache)


def test_image_manifest_pins_current_checker_mask_feasibility_and_linear(tmp_path):
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    assert {'schema', 'checker', 'mask', 'feasibility', 'linear_model'} <= grpo.TRAINING_IMAGE_PINS.keys()
    grpo.verify_manifest(manifest, tmp_path)
    for component, expected in grpo.TRAINING_IMAGE_PINS.items():
        assert manifest.versions[component] == expected
        from dataclasses import replace
        changed = replace(manifest, versions={**manifest.versions, component: 'stale'})
        with pytest.raises(RuntimeError, match='pinned'):
            grpo.verify_manifest(changed, tmp_path)


def test_schema_19_image_qualification_and_legacy_pin_refusal(tmp_path, monkeypatch):
    from dataclasses import replace
    from stowage.model import Instance
    from stowage.model.fixtures import build_instance

    # C8 of experiments/fleet/orders/w2-x1-contract.md adds these schema fields.
    raw = build_instance().model_dump(mode='json')
    raw['port']['max_crane_height_tiers'] = 6
    raw['port']['quay_crane']['max_on_deck_tiers'] = 6
    raw['port']['berthing_side'] = 'starboard'
    raw['port']['yard']['blocks'][0]['truck_lane_side'] = 'high'
    raw['containers'][0]['direct_delivery'] = False
    instance = Instance.model_validate(raw)
    restored = Instance.model_validate_json(instance.model_dump_json())
    assert restored.schema_version == '1.9'
    assert restored.port.max_crane_height_tiers == 6
    assert restored.port.berthing_side == 'starboard'
    assert restored.port.yard.blocks[0].truck_lane_side == 'high'
    assert restored.containers[0].direct_delivery is False
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    # MVP v4 pins (falsifier 4, 29 Oct): every component version plus the rule-catalogue hash and RL version.
    assert {k: grpo.TRAINING_IMAGE_PINS[k] for k in ('schema', 'checker', 'feasibility', 'mask', 'linear_model',
                                                     'ballast')} == {
        'schema': '1.9', 'checker': '2.2', 'feasibility': '2.5',
        'mask': 'stowlm-mask@1.2', 'linear_model': '3.5', 'ballast': '4.0',
    }
    assert grpo.TRAINING_IMAGE_SCOPE['c2'].startswith('off') and grpo.TRAINING_IMAGE_SCOPE['rule_freeze_v1'] is False
    grpo.verify_manifest(manifest, tmp_path)
    legacy_versions = {**manifest.versions, 'schema': '1.8'}
    monkeypatch.setattr(grpo, 'current_versions', lambda: legacy_versions)
    with pytest.raises(RuntimeError, match='pinned'):
        grpo.verify_manifest(replace(manifest, versions=legacy_versions), tmp_path)


def test_manifest_rejects_training_image_config_drift(tmp_path, monkeypatch):
    manifest = grpo.PinManifest.capture('a' * 40, tmp_path)
    monkeypatch.setitem(grpo.TRAINING_IMAGE_PINS, 'schema', 'stale')
    with pytest.raises(RuntimeError, match='pinned'):
        grpo.verify_manifest(manifest, tmp_path)


def test_cache_preflight_rejects_unlisted_nested_receipt_named_asset(tmp_path):
    instance, manifest, cache = prepared(tmp_path)
    nested = cache / 'nested'
    nested.mkdir()
    (nested / preflight.RECEIPT_NAME).write_text('unlisted cache bytes')
    with pytest.raises(RuntimeError, match='cache'):
        preflight.preflight_physics_cache(instance, manifest, cache)
