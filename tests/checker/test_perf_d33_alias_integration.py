"""D33 checker registry aliases preserve stored physics and reject stale claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from stowage.checker import stability
from stowage.checker.rules_background import d42_background_capacity_violations
from stowage.checker.rules_evidence import background_capacity_violations
from stowage.conventions import LEGACY_FLEET_PARAMETER_PINS
from stowage.io import load_instance
from stowage.model import FixedCargoBox, FixedLoad, MovePlan
from stowage.vessel import fleet
from stowage.vessel.fleet_conditions import CONDITION_PREFIX, CONDITION_VERSION

ROOT = Path(__file__).resolve().parents[2]
BASELINE = json.loads((ROOT/'data/fixtures/d33_legacy_numeric_reports.json').read_text())
LEGACY_SOURCE = 'params/fleet/feeder_1000.toml sha256:' + LEGACY_FLEET_PARAMETER_PINS['feeder_800'][0]
_D171_REPORT = 'experiments/mvp/postfreeze/FAILURES-D171.md'


def _require_admitted_legacy_epoch():
    from stowage.vessel.fleet_conditions import _nonlabel_parameter_fingerprint

    if _nonlabel_parameter_fingerprint(fleet.load_ship_params('feeder_800')) != LEGACY_FLEET_PARAMETER_PINS['feeder_800'][1]:
        pytest.skip('D171 B: positive D33 alias case requires the original nonlabel physical-parameter epoch; see ' + _D171_REPORT)


@pytest.fixture(scope='module')
def legacy_vessel():
    # The saved numeric fixtures intentionally carry an older, stale digest.
    # Exercise the actually admitted D33 pin on unchanged current physics here.
    vessel = fleet.load_vessel('feeder_800')
    vessel.id = 'feeder_1000'
    vessel.sources = [value for value in vessel.sources if not value.startswith('params/fleet/')]
    vessel.sources.append(LEGACY_SOURCE)
    return vessel


@pytest.fixture(scope='module')
def middle_geometry():
    return fleet.load_vessel('feeder_800', with_hull=False)


def test_legacy_policy_keeps_mutable_canonical_comparison(legacy_vessel):
    _require_admitted_legacy_epoch()
    vessel = legacy_vessel.model_copy(deep=True)
    before = vessel.model_dump_json()
    stability._canonical_fleet_policy(vessel)
    assert vessel.model_dump_json() == before
    vessel.hull.max_trim_m += .001
    with pytest.raises(ValueError, match='max_trim_m.*canonical'):
        stability._canonical_fleet_policy(vessel)


@pytest.mark.parametrize('mutation', ['stale', 'malformed', 'duplicate', 'wrong_root', 'canonical_label_old_pin'])
def test_legacy_policy_rejects_invalid_parameter_claims(legacy_vessel, mutation):
    vessel = legacy_vessel.model_copy(deep=True)
    vessel.sources = [value for value in vessel.sources if not value.startswith('params/fleet/')]
    source = LEGACY_SOURCE
    replacements = {
        'stale': [source.rsplit(':', 1)[0] + ':' + '0'*64],
        'malformed': ['params/fleet/feeder_1000.toml malformed'],
        'duplicate': [source, source],
        'wrong_root': [source.replace('feeder_1000', 'feeder_500')],
        'canonical_label_old_pin': [source.replace('feeder_1000', 'feeder_800')],
    }
    vessel.sources.extend(replacements[mutation])
    with pytest.raises(ValueError, match='parameter source'):
        stability._canonical_fleet_policy(vessel)


@pytest.mark.parametrize('field', ['max_trim_m', 'content_density_kg_m3'])
def test_pinned_legacy_source_rejects_changed_nonlabel_inputs(monkeypatch, legacy_vessel, field):
    original = fleet._parameter_bytes('feeder_800')
    match = re.search(rb'(?m)^' + field.encode() + rb'\s*=\s*([-+0-9.eE]+)', original)
    assert match is not None
    changed = original[:match.start(1)] + repr(float(match[1]) + 1.).encode() + original[match.end(1):]
    monkeypatch.setattr(fleet, '_parameter_bytes', lambda name: changed)
    with pytest.raises(ValueError, match='parameter source'):
        stability._canonical_fleet_policy(legacy_vessel)


def _capacity_case(parent, *, per_box=False, stale=False):
    vessel = parent.model_copy(deep=True)
    retained = vessel.stacks[0]
    support_stack = next(s for s in vessel.stacks if not s.on_deck and s.id != retained.id)
    cell = next(c for c in vessel.cells if c.id == support_stack.cell_ids[0])
    original_limit = support_stack.max_mass_20_kg or support_stack.max_mass_kg
    mass = original_limit + 1. if per_box else 12000.
    vessel.id, vessel.slice_of = 'feeder_1000.alias_capacity', 'feeder_1000'
    vessel.cells = [c for c in vessel.cells if c.stack_id == retained.id]
    vessel.stacks = [retained]
    vessel.fixed_loads = [FixedLoad(x_m=cell.lcg_m, y_m=cell.tcg_m,
        z_m=cell.vcg_base_m+2.591/2, mass_kg=mass, kind='cargo')]
    source = LEGACY_SOURCE if not stale else LEGACY_SOURCE.rsplit(':', 1)[0]+':'+'0'*64
    vessel.sources = [source]
    if per_box:
        # A forged local capacity must never replace the recovered parent's cap.
        local_stack = support_stack.model_copy(deep=True)
        local_stack.max_mass_kg = local_stack.max_mass_20_kg = 1e12
        vessel.fixed_load_cells = [c.model_copy(deep=True) for c in parent.cells
                                    if c.stack_id == support_stack.id]
        vessel.fixed_load_stacks = [local_stack]
        box = FixedCargoBox(cell_ids=[cell.id], stack_id=support_stack.id,
            tier=int(cell.id[-2:]), length_ft=20, height_m=2.591,
            gross_mass_kg=mass, x_m=cell.lcg_m, y_m=cell.tcg_m,
            z_m=cell.vcg_base_m+2.591/2)
        vessel.fixed_loads[0].cargo_boxes = [box]
    else:
        vessel.sources.append(CONDITION_PREFIX+json.dumps(dict(
            version=CONDITION_VERSION, base_fixed_loads=[],
            background_cells=[dict(cell_id=cell.id, stack_id=support_stack.id, mass_kg=mass)],
            background_stacks=[dict(stack_id=support_stack.id, mass_kg=mass,
                max_mass_20_kg=original_limit, max_cell_mass_kg=30480., on_deck=False)])))
    return vessel, mass, original_limit


def _emit_into(rows):
    def emit(rule, message, *args, **kwargs):
        rows.append(dict(rule=rule, message=message, **kwargs))
    return emit


@pytest.mark.parametrize('stale', [False, True])
def test_aggregate_background_recovers_legacy_parent_and_checks_pin(monkeypatch, middle_geometry, stale):
    if not stale:
        _require_admitted_legacy_epoch()
    vessel, _, _ = _capacity_case(middle_geometry, stale=stale)
    rows, notes, resolved = [], [], []
    original = fleet.load_vessel
    def load(name, *, with_hull):
        resolved.append((name, with_hull))
        return original(name, with_hull=with_hull)
    monkeypatch.setattr(fleet, 'load_vessel', load)
    background_capacity_violations(vessel, _emit_into(rows), note=notes.append)
    if stale:
        assert any(row['rule']=='checker.missing_vessel_data' and 'digest' in row['message'] for row in rows)
        assert not resolved
    else:
        assert not rows, rows
        assert not notes, notes
        assert resolved == [('feeder_800', False)]


@pytest.mark.parametrize('stale', [False, True])
def test_per_box_background_uses_legacy_parent_cap_and_rejects_stale_pin(middle_geometry, stale):
    if not stale:
        _require_admitted_legacy_epoch()
    vessel, mass, limit = _capacity_case(middle_geometry, per_box=True, stale=stale)
    rows = []
    d42_background_capacity_violations(SimpleNamespace(vessel=vessel, containers=[]),
        SimpleNamespace(placements=[]), _emit_into(rows))
    if stale:
        assert any(row['rule']=='checker.missing_vessel_data' and 'digest' in row['message'] for row in rows)
    else:
        assert any(row['rule']=='expert.physical.stack_weight' and row.get('measured')==mass
                   and row.get('limit')==limit for row in rows), rows
        assert not any(row['rule']=='checker.missing_vessel_data' for row in rows), rows


@pytest.mark.parametrize('case', BASELINE['cases'], ids=['stored-full', 'stored-realistic'])
def test_stored_legacy_reports_preserve_every_physical_number(case, record_property):
    from stowage.checker.full import check
    source = ROOT/case['source']
    before = source.read_bytes()
    assert hashlib.sha256(before).hexdigest() == case['sha256']
    instance = load_instance(source)
    assert instance.vessel.id == 'feeder_1000'
    original = instance.model_dump_json()
    report = check(instance, MovePlan(id='d33-legacy-regression', instance_id=instance.id,
                                     call_index=instance.call_index))
    measured = {part: {kind: {key: value for key, value in getattr(body, kind).items()
                             if not key.startswith('note:') and '.note:' not in key}
                       for kind in ('metrics', 'margins')}
                for part, body in report.parts.items()}
    assert report.passed == case['passed']
    assert measured == case['measurements']
    record_property('physical_numeric_count', sum(len(values) for body in measured.values() for values in body.values()))
    record_property('maximum_numeric_delta', 0.)
    assert source.read_bytes() == before
    assert instance.model_dump_json() == original
