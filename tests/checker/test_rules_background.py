"""Background capacity is audited against original geometry, never claimed caps."""

import copy
import json

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Placement
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet_conditions import CONDITION_PREFIX, CONDITION_VERSION, slice_condition


def _write_audit(vessel, info):
    vessel.sources = [s for s in vessel.sources if not s.startswith(CONDITION_PREFIX)]
    vessel.sources.append(CONDITION_PREFIX + json.dumps(info))


def _case(builtin=False):
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_slices import _slice_stacks

    instance = build_instance()
    if builtin:
        parent = load_vessel('feeder_500', with_hull=False)
        # No loading-condition/hull builder is needed to audit a declared point.
        retained = parent.stacks[0]
        background = next(s for s in parent.stacks if not s.on_deck and s.id != retained.id)
        cell = next(c for c in parent.cells if c.id == background.cell_ids[0])
        from stowage.model.vessel import FixedLoad
        vessel = parent.model_copy(deep=True, update={
            'id': 'feeder_500.test_capacity', 'slice_of': 'feeder_500',
            'fixed_loads': [FixedLoad(x_m=cell.lcg_m, y_m=cell.tcg_m,
                                     z_m=cell.vcg_base_m+2.591/2, mass_kg=12000, kind='cargo')],
        })
        # Background cannot overlap the decision stack; retain its peer instead.
        vessel.cells = [c for c in vessel.cells if c.stack_id == retained.id]
        vessel.stacks = [s for s in vessel.stacks if s.id == retained.id]
        vessel.hatch_covers = [h.model_copy(update={
            'hold_stack_ids': [sid for sid in h.hold_stack_ids if sid == retained.id],
            'deck_stack_ids': [sid for sid in h.deck_stack_ids if sid == retained.id],
        }) for h in vessel.hatch_covers if retained.hatch_cover_id == h.id]
        _write_audit(vessel, {
            'version': CONDITION_VERSION, 'base_fixed_loads': [],
            'background_cells': [{'cell_id': cell.id, 'stack_id': background.id, 'mass_kg': 12000}],
            'background_stacks': [{'stack_id': background.id, 'mass_kg': 12000,
                                   'max_mass_20_kg': background.max_mass_20_kg or background.max_mass_kg,
                                   'max_cell_mass_kg': 30480, 'on_deck': False}],
        })
    else:
        parent = instance.vessel.model_copy(deep=True)
        parent.id, parent.slice_of, parent.fixed_loads, parent.sources = 'foreign_capacity_parent', None, [], []
        retained = next(s for s in parent.stacks if not s.on_deck)
        vessel = _slice_stacks(parent, {retained.id}, name='foreign_capacity_parent.test')
    instance.vessel = vessel
    box = instance.containers[1]
    box.length_ft, box.iso_type = 20, '22G1'
    box.height_m, box.gross_mass_kg, box.max_gross_mass_kg = 2.591, 12000, 30000
    box.reefer, box.dg, box.oog, box.special_stowage = False, None, None, []
    box.pod = 'THBKK'
    slot = retained.cell_ids[0]
    instance.onboard = instance.onboard.model_copy(update={'vessel_id': vessel.id, 'placements': []})
    ship = instance.onboard.model_copy(update={'placements': [Placement(container_id=box.id, slot=slot)]})
    return instance, box, ship, slot


def _check(case, entry):
    instance, box, ship, slot = case
    if entry == 'full':
        return check_static_rules(instance, ship, call_index=0).violations
    return placement_violations(instance, instance.onboard, box, slot, call_index=0)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('builtin', [False, True], ids=['foreign', 'builtin'])
def test_traceable_background_capacity_evidence_is_accepted(entry, builtin, monkeypatch):
    case = _case(builtin)
    import stowage.vessel.fleet_conditions as conditions
    from stowage.vessel import hull
    def forbidden(*args, **kwargs):
        raise AssertionError('background audit must use geometry only')
    monkeypatch.setattr(conditions, '_parent', forbidden)
    monkeypatch.setattr(conditions, '_raw_context', forbidden)
    monkeypatch.setattr(hull, 'build_hull_model', forbidden)
    assert not _check(case, entry)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('mutation', ['claimed_limit', 'actual_mass', 'actual_position', 'missing_record',
                                      'duplicate_cell', 'wrong_stack', 'stale_version', 'invalid_json'])
def test_background_missing_stale_or_mismatched_evidence_is_hard(entry, mutation):
    case = _case()
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    if mutation == 'claimed_limit':
        info['background_stacks'][0]['max_mass_20_kg'] *= 100
    elif mutation == 'actual_mass':
        vessel.fixed_loads[0].mass_kg += 1
    elif mutation == 'actual_position':
        vessel.fixed_loads[0].x_m += 1
    elif mutation == 'missing_record':
        info['background_cells'].pop()
    elif mutation == 'duplicate_cell':
        info['background_cells'].append(info['background_cells'][0])
    elif mutation == 'wrong_stack':
        info['background_cells'][0]['stack_id'] = case[0].vessel.stacks[0].id
    elif mutation == 'stale_version':
        info['version'] = 'c3-fixed-background-1'
    _write_audit(vessel, info)
    if mutation == 'invalid_json':
        vessel.sources = [s for s in vessel.sources if not s.startswith(CONDITION_PREFIX)]
        vessel.sources.append(CONDITION_PREFIX + '{')
    out = _check(case, entry)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               and 'background' in v.message.lower() for v in out)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_claimed_background_limit_cannot_hide_actual_parent_stack_overload(entry):
    case = _case()
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    record = next(r for r in info['background_stacks'] if not r['on_deck'])
    sid, cap = record['stack_id'], record['max_mass_20_kg']
    owned = [r for r in info['background_cells'] if r['stack_id'] == sid]
    scale = (cap + 100) / sum(r['mass_kg'] for r in owned)
    # Keep D42 box masses, fixed aggregates and cell audit in agreement while
    # forging the cap: inconsistent aggregates test a different failure first.
    from stowage.vessel.fleet_slices import recover_parent
    cells = {c.id: c for c in recover_parent(vessel).cells}
    for r in owned:
        c = cells[r['cell_id']]
        load = next(p for p in vessel.fixed_loads if p.x_m == c.lcg_m and p.y_m == c.tcg_m
                    and p.z_m == c.vcg_base_m + 2.591/2)
        load.mass_kg *= scale
        for box in load.cargo_boxes or []:
            if r['cell_id'] in box.cell_ids:
                box.gross_mass_kg *= scale
        r['mass_kg'] *= scale
    record['mass_kg'], record['max_mass_20_kg'] = cap + 100, cap * 100
    _write_audit(vessel, info)
    out = _check(case, entry)
    assert any(v.rule_id == 'expert.physical.stack_weight' and v.severity == 'hard'
               and v.limit == cap and v.measured == pytest.approx(cap+100) for v in out)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_inflated_audit_mass_is_integrity_failure_without_phantom_physical_overload(entry):
    case = _case()
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    record = next(r for r in info['background_stacks'] if not r['on_deck'])
    sid, cap = record['stack_id'], record['max_mass_20_kg']
    owned = [r for r in info['background_cells'] if r['stack_id'] == sid]
    scale = (cap + 100) / sum(r['mass_kg'] for r in owned)
    # Only audit claims are inflated. Original snapshot, actual aggregates and
    # D42 boxes remain consistent and safely within their physical stack limits.
    for cell in owned:
        cell['mass_kg'] *= scale
    record['mass_kg'] = cap + 100
    _write_audit(vessel, info)
    out = _check(case, entry)
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               and 'background' in v.message.lower() for v in out)
    assert not [v for v in out if v.rule_id == 'expert.physical.stack_weight']


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_builtin_background_requires_current_pinned_parameter_digest(entry):
    case = _case(True)
    case[0].vessel.sources = [s.replace('sha256:', 'sha256:stale-') if s.startswith('params/fleet/') else s
                              for s in case[0].vessel.sources]
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               for v in _check(case, entry))


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('field,value', [
    ('document', None), ('document', []), ('document', 1), ('document', 'audit'),
    ('background_cells', None), ('background_cells', {}),
    ('background_stacks', None), ('background_stacks', {}),
    ('base_fixed_loads', None), ('cell_record', None), ('stack_record', None),
    ('cell_mass', None), ('cell_mass', '12000'), ('cell_mass', True),
    ('cell_mass', float('nan')), ('cell_mass', float('inf')), ('cell_mass', -1),
    ('stack_cap', None), ('stack_cap', '60000'), ('stack_cap', float('nan')),
])
def test_malformed_background_audit_returns_hard_missing_data(entry, field, value):
    case = _case()
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    if field == 'document':
        info = value
    elif field == 'cell_record':
        info['background_cells'][0] = value
    elif field == 'stack_record':
        info['background_stacks'][0] = value
    elif field == 'cell_mass':
        info['background_cells'][0]['mass_kg'] = value
    elif field == 'stack_cap':
        info['background_stacks'][0]['max_mass_20_kg'] = value
    else:
        info[field] = value
    _write_audit(vessel, info)
    out = _check(case, entry)
    if value is None and field not in {'document', 'cell_record', 'stack_record'}:
        from stowage.checker.rules_evidence import background_capacity_violations
        notes, violations = [], []
        background_capacity_violations(vessel, lambda *args, **kwargs: violations.append(args), note=notes.append)
        assert notes and not violations
        assert not [v for v in out if 'background' in v.message.lower()]
    else:
        assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
                   and 'background' in v.message.lower() for v in out)


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_removed_actual_background_loads_cannot_reuse_positive_audit(entry):
    case = _case()
    case[0].vessel.fixed_loads = []
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               for v in _check(case, entry))


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_background_deck_silhouette_mutation_breaks_cell_binding(entry):
    case = _case()
    load = next(p for p in case[0].vessel.fixed_loads if p.silhouette is not None)
    load.silhouette.x_max_m += 1
    assert any(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard'
               for v in _check(case, entry))


@pytest.mark.parametrize('qualification', ['audit', 'background_cells', 'background_stacks',
                                            'base_fixed_loads', 'parent_snapshot', 'stack_record',
                                            'stack_cap', 'cell_mass', 'parent_digest', 'version'])
def test_absent_background_qualification_is_explicit_note_not_violation(qualification):
    from stowage.checker.rules_evidence import background_capacity_violations

    case = _case(qualification == 'parent_digest')
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    if qualification == 'audit':
        vessel.sources = [s for s in vessel.sources if not s.startswith(CONDITION_PREFIX)]
    elif qualification == 'parent_snapshot':
        vessel.sources = [s for s in vessel.sources if not s.startswith('stowage.slice_parent:')]
    elif qualification == 'parent_digest':
        vessel.sources = [s for s in vessel.sources if not s.startswith('params/fleet/')]
    else:
        if qualification == 'stack_record':
            info['background_stacks'].pop()
        elif qualification == 'stack_cap':
            info['background_stacks'][0].pop('max_mass_20_kg')
        elif qualification == 'cell_mass':
            info['background_cells'][0].pop('mass_kg')
        else:
            info.pop(qualification)
        _write_audit(vessel, info)
    violations, notes = [], []
    background_capacity_violations(vessel, lambda *args, **kwargs: violations.append(args), note=notes.append)
    assert not violations
    assert notes and all('ASSUMPTION: background stack capacity unverified' in message for message in notes)
    assert not [v for v in _check(case, 'full') if 'background' in v.message.lower()]
    assert not [v for v in _check(case, 'placement') if 'background' in v.message.lower()]


@pytest.mark.parametrize('entry', ['full', 'placement'])
def test_legacy_background_metadata_without_capacity_records_is_note_only(entry):
    from stowage.checker.rules_evidence import background_capacity_violations

    case = _case()
    _write_audit(case[0].vessel, {'version': 'c3-fixed-background-1', 'preset': 'tiny'})
    notes, violations = [], []
    background_capacity_violations(case[0].vessel, lambda *args, **kwargs: violations.append(args), note=notes.append)
    assert not violations
    assert notes and 'background_cells' in notes[0] and 'background_stacks' in notes[0]
    assert 'base_fixed_loads' in notes[0] and 'current_background_capacity_audit_version' in notes[0]
    assert not [v for v in _check(case, entry) if 'background' in v.message.lower()]


@pytest.mark.parametrize('omitted', ['background_cells', 'background_stacks'])
def test_partial_background_audit_still_enforces_supplied_parent_limit(omitted):
    from stowage.checker.rules_evidence import background_capacity_violations

    case = _case()
    vessel = case[0].vessel
    info = copy.deepcopy(slice_condition(vessel))
    audit = next(r for r in info['background_stacks'] if not r['on_deck'])
    sid, cap = audit['stack_id'], audit['max_mass_20_kg']
    if omitted == 'background_cells':
        # A supplied stack assertion itself admits overload even without cells.
        audit['mass_kg'] = cap+100
    else:
        # Cell attribution independently establishes overload when stack metadata
        # is absent; point binding is separately checked and cannot authorize it.
        records = [r for r in info['background_cells'] if r['stack_id'] == sid]
        records[0]['mass_kg'] = cap+100
    info.pop(omitted)
    _write_audit(vessel, info)
    notes, violations = [], []
    background_capacity_violations(vessel, lambda *args, **kwargs: violations.append((args, kwargs)), note=notes.append)
    assert any(args[0] == 'expert.physical.stack_weight' and kwargs['limit'] == cap
               for args, kwargs in violations)
    assert notes and omitted in notes[0]


@pytest.mark.parametrize('field,value', [('mass_kg', float('nan')), ('mass_kg', -1),
                                         ('x_m', float('nan')), ('x_m', float('inf')),
                                         ('silhouette_nan', float('nan')),
                                         ('silhouette_reversed', None)])
def test_invalid_physical_fixed_load_numbers_remain_hard_without_audit(field, value):
    from stowage.checker.rules_evidence import background_capacity_violations

    case = _case()
    vessel = case[0].vessel
    vessel.sources = [s for s in vessel.sources if not s.startswith(CONDITION_PREFIX)]
    if field.startswith('silhouette'):
        load = next(p for p in vessel.fixed_loads if p.silhouette is not None)
        load.silhouette.x_min_m = (load.silhouette.x_max_m+1 if field == 'silhouette_reversed' else value)
    else:
        setattr(vessel.fixed_loads[0], field, value)
    violations, notes = [], []
    background_capacity_violations(vessel, lambda *args, **kwargs: violations.append(args), note=notes.append)
    assert violations and violations[0][0] == 'checker.missing_vessel_data'


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('raw,invalid', [((70., 0., 4., 70000.), False),
                                       ((70., 0., 4.), True),
                                       ((70., 0., 4., 'invalid'), True),
                                       ({'mass_kg': 70000}, True),
                                       (object(), True)])
def test_raw_fixed_load_inputs_normalize_locally_or_fail_hard(entry, raw, invalid):
    case = _case()
    vessel = case[0].vessel
    vessel.sources = [s for s in vessel.sources if not s.startswith(CONDITION_PREFIX)]
    vessel.fixed_loads = [raw]
    out = _check(case, entry)
    background = [v for v in out if 'background' in v.message.lower()]
    assert bool(background) is invalid
    assert all(v.rule_id == 'checker.missing_vessel_data' and v.severity == 'hard' for v in background)
    assert vessel.fixed_loads[0] is raw
