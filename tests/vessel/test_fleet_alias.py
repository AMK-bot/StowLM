"""D33 aliases change lookup labels while preserving stored and physical identity."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from stowage.conventions import canonical_vessel_id, LEGACY_FLEET_PARAMETER_PINS
from stowage.vessel.fleet import load_ship_params, load_vessel, _parameter_bytes
from stowage.vessel.fleet_conditions import (
    _nonlabel_parameter_fingerprint, _verify_parent_parameter_sources,
)


@pytest.mark.parametrize(('old', 'new'), [
    ('feeder_1000', 'feeder_800'),
    ('feeder_1000.slice_b02-b10', 'feeder_800.slice_b02-b10'),
    ('feeder_1000.small', 'feeder_800.small'),
    ('realistic_1000', 'realistic_800'),
    ('feeder_500', 'feeder_500'),
    ('feeder_1700.custom', 'feeder_1700.custom'),
    ('foreign_feeder_1000', 'foreign_feeder_1000'),
])
def test_resolver_preserves_suffix_and_unknown_ids(old, new):
    assert canonical_vessel_id(old) == new
    assert canonical_vessel_id(new) == new


def test_alias_loads_identical_complete_physics(fleet_vessel):
    old = fleet_vessel('feeder_1000')
    new = fleet_vessel('feeder_800')
    assert old.model_dump(mode='json') == new.model_dump(mode='json')
    assert old.id == new.id == 'feeder_800'
    assert old.nominal_teu == new.nominal_teu == 800
    assert sum(cell.accepts_20 for cell in new.cells) == 816
    assert load_ship_params('feeder_1000') == load_ship_params('feeder_800')


def test_stored_model_labels_remain_unmodified(fleet_vessel):
    from stowage.model import Vessel

    raw = fleet_vessel('feeder_800', with_hull=False).model_dump(mode='json')
    raw.update(id='feeder_1000', name='Synthetic feeder 1000', nominal_teu=1000)
    payload = json.dumps(raw)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    loaded = Vessel.model_validate_json(payload)
    assert loaded.id == 'feeder_1000'
    assert loaded.nominal_teu == 1000
    assert hashlib.sha256(payload.encode()).hexdigest() == digest


def test_pinned_legacy_source_rejects_the_new_vessel_design_epoch():
    """Tank-pair/thermal geometry declarations are real parameter changes, not aliases."""
    payload = _parameter_bytes('feeder_800')
    old_raw, nonlabel_pin = LEGACY_FLEET_PARAMETER_PINS['feeder_800']
    vessel = SimpleNamespace(sources=[f'params/fleet/feeder_1000.toml sha256:{old_raw}'])
    assert _nonlabel_parameter_fingerprint(load_ship_params('feeder_800')) != nonlabel_pin
    with pytest.raises(ValueError, match='parameters changed'):
        _verify_parent_parameter_sources(vessel, 'feeder_800', payload)
    vessel.sources = ['params/fleet/feeder_800.toml sha256:' + hashlib.sha256(payload).hexdigest()]
    _verify_parent_parameter_sources(vessel, 'feeder_800', payload)
    vessel.sources = ['params/fleet/feeder_1000.toml sha256:' + '0'*64]
    with pytest.raises(ValueError, match='parameters changed'):
        _verify_parent_parameter_sources(vessel, 'feeder_800', payload)


def test_nonlabel_fingerprint_retains_every_other_field(monkeypatch):
    """Inspect hash preimage: every non-label leaf remains exactly represented."""
    from stowage.vessel import fleet_conditions

    params = load_ship_params('feeder_800')
    expected = deepcopy(params)
    for key in ('id', 'name', 'nominal_teu', 'teu_tolerance_fraction'):
        expected.pop(key)
    expected['hull'].pop('hull_offsets_ref')
    original = hashlib.sha256
    preimages = []

    def recording_sha256(value):
        preimages.append(json.loads(value))
        return original(value)

    monkeypatch.setattr(fleet_conditions.hashlib, 'sha256', recording_sha256)
    _nonlabel_parameter_fingerprint(params)
    assert preimages == [expected]


@pytest.mark.parametrize('field', ['id', 'name', 'nominal_teu', 'teu_tolerance_fraction'])
def test_label_fields_do_not_change_nonlabel_fingerprint(field):
    params = load_ship_params('feeder_800')
    before = _nonlabel_parameter_fingerprint(params)
    params[field] = 'another label'
    assert _nonlabel_parameter_fingerprint(params) == before


def test_legacy_and_canonical_linear_cache_labels_share_original_key():
    from stowage.linear.cache import cache_key

    common = dict(kind='vessel', params={'shape': [1., 2., 3.]},
                  inputs={'limit': 4.}, source_hash='test-explicit-numerical-source')
    old = cache_key(vessel_id='feeder_1000.slice_b02-b10', **common)
    assert cache_key(vessel_id='feeder_800.slice_b02-b10', **common) == old
    assert cache_key(vessel_id='feeder_500.slice_b02-b10', **common) != old
    assert cache_key(vessel_id='feeder_800.slice_b02-b10',
                     **{**common, 'inputs': {'limit': 5.}}) != old
    assert cache_key(vessel_id='feeder_800.slice_b02-b10',
                     **{**common, 'params': {'shape': [1., 2., 4.]}}) != old


def test_margin_policy_resolves_legacy_registry_and_still_rejects_missing_epoch():
    from stowage.linear.margin_policy import _known_production_missing

    bank = {'known_vessel_ids': ['feeder_1000'], 'entries': {}}
    family = SimpleNamespace(parameters={'id': 'feeder_800'})
    assert _known_production_missing(family, 'unknown-response', bank)
    bank['entries']['known-response'] = {}
    assert not _known_production_missing(family, 'known-response', bank)


def test_frozen_split_spec_validation_preserves_legacy_content_key():
    from eval.splits import InstanceSpec, canonical_spec, spec_key

    spec = InstanceSpec('random_yard', 'small', 'feeder_1000', 'small',
                        'laem_chabang_like', None, .2, 7)
    content = canonical_spec(spec)
    assert content['vessel'] == 'feeder_1000'
    assert spec_key(spec) == hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def test_alias_labels_preserve_response_qualification_fingerprint():
    from stowage.linear.cache import response_calibration_identity
    from stowage.linear.model import ValidityBand

    fields = ('water_density_kg_m3', 'station_x_m', 'tank_ids', 'tank_lcg_m',
              'tank_tcg_m', 'tank_vcg_m', 'tank_capacity_kg', 'tank_fsm_max_kgm',
              'tank_adjustable', 'sf_per_kg_tank', 'bm_per_kg_tank', 'const_mass_kg',
              'const_moment_x_kgm', 'const_moment_y_kgm', 'const_moment_z_kgm',
              'km_line', 'trim_line', 'draught_line', 'gm_min_m')
    template = SimpleNamespace(**dict.fromkeys(fields, 1.))
    template.band = ValidityBand(1., 2., .1, 1., .1, .1)
    parameters = load_ship_params('feeder_800')
    family = SimpleNamespace(template=template, parameters=parameters,
        **dict.fromkeys(('response_sf', 'response_bm', 'buoyancy_sf', 'buoyancy_bm',
                        'lightship_sf', 'lightship_bm'), 1.))
    canonical = response_calibration_identity(family)
    parameters.update(id='feeder_1000', name='Synthetic feeder 1000', nominal_teu=1000,
                      teu_tolerance_fraction=.184)
    parameters['hull']['hull_offsets_ref'] = 'params/fleet/feeder_1000.toml#hull_form'
    assert response_calibration_identity(family) == canonical
    parameters['tanks'][0]['content_density_kg_m3'] += 1.
    assert response_calibration_identity(family) != canonical



@pytest.mark.parametrize('case_index', [0, 1])
def test_archived_instances_keep_verdict_and_every_numeric_measurement(case_index):
    """Stored full/realistic instances substitute for unavailable tiny/small cases."""
    from pathlib import Path
    from stowage.checker.full import check
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.params import repo_root

    root = repo_root()
    baseline = json.loads((root / 'data/fixtures/d33_legacy_numeric_reports.json').read_text())
    case = baseline['cases'][case_index]
    source = root / case['source']
    if not source.exists():
        pytest.skip('stored experiment instances are not distributed with the repository')
    before = source.read_bytes()
    assert hashlib.sha256(before).hexdigest() == case['sha256']
    instance = load_instance(source)
    assert instance.vessel.id == 'feeder_1000'
    report = check(instance, MovePlan(id='d33-legacy-regression', instance_id=instance.id,
                                      call_index=instance.call_index))
    measurements = {
        part: {kind: {key: value for key, value in getattr(body, kind).items()
                      if not key.startswith('note:') and '.note:' not in key}
               for kind in ('metrics', 'margins')}
        for part, body in report.parts.items()
    }
    assert report.passed == case['passed']
    assert measurements == case['measurements']
    assert source.read_bytes() == before


def test_live_snapshot_alias_does_not_reuse_archived_pins_after_design_changes(monkeypatch):
    from pathlib import Path
    import runpy
    import re
    from stowage.vessel import fleet

    snapshot = runpy.run_path(str(Path(__file__).resolve().parents[2]
                                 / 'experiments/linear-model/narrow.py'))['input_snapshot']
    original = fleet.load_ship_params
    old = snapshot('feeder_1000')
    assert 'params/fleet/feeder_1000.toml' not in old
    assert old['params/fleet/feeder_800.toml'] == hashlib.sha256(_parameter_bytes('feeder_800')).hexdigest()
    assert old == snapshot('feeder_800')
    changed = original('feeder_800')
    changed['hull']['lpp_m'] += 1.
    changed_payload, replacements = re.subn(
        rb'(?m)^lpp_m = [^\n]+$',
        f"lpp_m = {changed['hull']['lpp_m']}".encode(),
        _parameter_bytes('feeder_800'), count=1)
    assert replacements == 1
    monkeypatch.setattr(fleet, 'load_ship_params', lambda name: deepcopy(changed))
    read_bytes = Path.read_bytes
    parameter_path = Path(fleet.__file__).resolve().parents[3] / 'params/fleet/feeder_800.toml'
    monkeypatch.setattr(Path, 'read_bytes', lambda path: changed_payload
                        if path == parameter_path else read_bytes(path))
    assert snapshot('feeder_1000') != old
    assert 'params/fleet/feeder_1000.toml' not in snapshot('feeder_1000')
