"""Audit generator epoch and report selection without changing allocated specs.

Raw booking draws are not retained by the current generator. Their mass/DG mix
is explicitly unavailable, never inferred from the accepted, conditioned sample.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from eval.snapshot_io import read_snapshot

ROOT = Path(__file__).resolve().parents[2]


def audit_instance(instance, audit, config, *, frozen=None):
    """Require current own-generator/condition/campaign and fresh D58 evidence."""
    from stowage.versions import component_versions, RANDOM_YARD_VERSION, SIMULATED_INSTANCES_VERSION, CARGO_GENERATOR_VERSION
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    from stowage.generate.instances_v2_random import RANDOM_V2_VERSION
    from stowage.generate.background_acceptance import requires_background_gate
    from stowage.vessel.fleet_conditions import CONDITION_VERSION
    from stowage.generate.instances_cli import _validate_background_binding
    versions = instance.provenance.versions
    expected = {**component_versions(), 'instances_campaign': CAMPAIGN_VERSION}
    if config['generator']=='random_yard':
        expected.update(random_yard=RANDOM_YARD_VERSION, random_yard_v2=RANDOM_V2_VERSION)
    else:
        expected.update(instances=SIMULATED_INSTANCES_VERSION, cargo_generator=CARGO_GENERATOR_VERSION)
    gated = requires_background_gate(instance.vessel, size=instance.size_class)
    if gated:
        expected['slice_condition'] = CONDITION_VERSION
    stale = {name: dict(expected=value, observed=versions.get(name)) for name, value in expected.items()
             if versions.get(name) != value}
    if stale:
        raise ValueError('stale emitted instance component/generator/condition/campaign versions: ' + json.dumps(stale, sort_keys=True))
    if audit.get('instance_versions') != versions:
        raise ValueError('emitted instance own versions differ from producer audit')
    if any(not math.isfinite(c.gross_mass_kg) or c.gross_mass_kg > 30500 for c in instance.containers):
        raise ValueError('emitted instance gross mass exceeds D60 30500 kg cap')
    if config['size']=='medium' and (config.get('c2_screened') is not True or audit.get('c2_screened') is not True):
        raise ValueError('medium emitted instance lacks mandatory C2 screen')
    physics = audit.get('physics')
    if not isinstance(physics, dict):
        raise ValueError('emitted instance lacks current producer physics binding')
    base = {k:v for k,v in physics.items() if k not in ('sha256','input_sha256','c2_screened')}
    input_sha = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    outer = {**base, 'sha256':input_sha, 'input_sha256':input_sha, 'c2_screened':bool(config.get('c2_screened'))}
    outer_sha = hashlib.sha256(json.dumps(outer, sort_keys=True).encode()).hexdigest()
    if physics.get('input_sha256') != input_sha or physics.get('sha256') != outer_sha:
        raise ValueError('emitted instance producer physics fingerprint is corrupt')
    if physics.get('components') != component_versions() or physics.get('condition_version') != CONDITION_VERSION:
        raise ValueError('emitted instance physics versions are stale')
    if frozen is None:
        raise ValueError('post-regeneration audit requires the frozen source binding')
    expected_sources = {name:digest for name,digest in frozen['files'].items()
        if (name.startswith('src/stowage/') or name.startswith('params/')) and Path(name).suffix in {'.py','.json','.toml'}}
    if not expected_sources or physics.get('sources') != expected_sources:
        raise ValueError('emitted instance producer physics sources differ from frozen source')
    _validate_background_binding(instance, audit, input_sha)
    return dict(passed=True, versions=expected, max_gross_mass_kg=max((c.gross_mass_kg for c in instance.containers), default=0),
        background_gate_required=gated, background_gate_physics_sha256=input_sha if gated else None,
        c2_screened=bool(config.get('c2_screened')))


def box_metrics(boxes):
    """Measured booking-box mix; fixed onboard background is excluded by caller."""
    boxes = list(boxes)
    masses = sorted(float(c.gross_mass_kg) for c in boxes)
    classes, uns = Counter(), Counter()
    for box in boxes:
        if box.dg is not None:
            classes[str(box.dg.imdg_class)] += 1
            uns[str(box.dg.un_number)] += 1
    # This records general-table demand, not a claim that a particular plan's
    # geometric separation is binding or that compound-DG provisions were tested.
    from stowage.rules.imdg.tables import general_segregation
    pair_types = {}
    observed = sorted(classes)
    for i, left in enumerate(observed):
        for right in observed[i:]:
            if left == right and classes[left] < 2:
                continue
            try:
                cell = general_segregation(left, right)
                if cell.value != 'X':
                    pair_types[f'{left}/{right}'] = str(cell.value)
            except KeyError:
                pair_types[f'{left}/{right}'] = 'unknown class table cell'
    return dict(boxes=len(boxes), mass_sum_kg=sum(masses),
        mass_mean_kg=sum(masses)/len(masses) if masses else None,
        mass_p95_kg=masses[max(0, math.ceil(.95*len(masses))-1)] if masses else None,
        over_25000_kg=sum(m>25000 for m in masses), dg=sum(c.dg is not None for c in boxes),
        reefer=sum(bool(c.reefer) for c in boxes), oog=sum(c.oog is not None for c in boxes),
        flat_rack=sum(c.iso_type[2:3] in {'P'} for c in boxes),
        dg_class_counts=dict(sorted(classes.items())), dg_un_counts=dict(sorted(uns.items())),
        dg_histogram_scope='Primary declared class and UN of booking boxes; fixed context excluded',
        general_segregation_non_x_pair_types=pair_types,
        general_segregation_non_x_pair_type_count=len(pair_types))


def _summarize_population(release, records, instances, *, frozen=None, physical=False, requested_recipe=None):
    """Return all allocated split×rung×family denominators, without backfilling."""
    from eval.splits import spec_key
    groups = {}
    for entry in release['entries']:
        key, identity = _group_identity(entry, physical=physical)
        group = groups.setdefault(key, dict(**identity,
            allocated=0, accepted=0, rejected=0, not_generated=0, attempts=0, rejection_reasons={}, _boxes=[], _booking_masses=[]))
        group['allocated'] += 1
        if physical:
            panels=group.setdefault('test_families',[])
            panel=entry.get('test_family') or 'in_distribution'
            if panel not in panels:
                panels.append(panel)
                panels.sort()
    seen = set()
    for row in records:
        job, result = row['job'], row['result']
        entry = job['allocated']; identity = spec_key(entry['spec'])
        if identity in seen:
            raise ValueError('duplicate attempted spec in population audit')
        seen.add(identity)
        key, _ = _group_identity(entry, physical=physical)
        group = groups[key]; audit=result['audit']
        group['attempts'] += audit.get('attempts', 0)
        group['rejection_reasons'] = dict(Counter(group['rejection_reasons']) + Counter(audit.get('rejections', {})))
        if audit.get('status')=='rejected':
            group['rejected'] += 1
        else:
            group['accepted'] += 1
            instance = instances[identity]
            targets=set(instance.load_list)
            booking=[c for c in instance.containers if c.id in targets]
            group['_boxes'].extend(booking)
            group['_booking_masses'].append(sum(c.gross_mass_kg for c in booking))
    warnings = []
    for key, group in groups.items():
        group['not_generated'] = group['allocated']-group['accepted']-group['rejected']
        group['emitted'] = box_metrics(group.pop('_boxes'))
        bookings=sorted(group.pop('_booking_masses'))
        group['emitted'].update(booking_mass_mean_kg=sum(bookings)/len(bookings) if bookings else None,
            booking_mass_p95_kg=bookings[max(0,math.ceil(.95*len(bookings))-1)] if bookings else None,
            mass_statistics_unit='mass_mean_kg/mass_p95_kg describe individual booking boxes; booking_mass_* describes total mass per accepted call')
        attempted=group['accepted']+group['rejected']
        group['attempts_per_attempted_spec'] = group['attempts']/attempted if attempted else None
        group['feasibility_status_counts']=dict(feasible_witnessed=0,proven_infeasible=0,unknown=group['accepted'],
            generation_rejected=group['rejected'],not_generated=group['not_generated'])
        group['acceptance_fraction_of_allocation'] = group['accepted']/group['allocated'] if group['allocated'] else None
        group['requested'] = dict(status='raw_draw_metrics_unavailable',
            mass_mean_kg=None, mass_p95_kg=None, over_25000_kg=None, dg=None, reefer=None, oog=None, flat_rack=None,
            dg_class_counts=None, dg_un_counts=None,
            reason='Generator does not persist pre-conditioning raw booking draws; profile recipe is pinned separately. No reconstruction or resampling performed.')
        if not physical and group['split']=='test' and group['family']!='in_distribution' and group['accepted']<10:
            warnings.append(f'{key}: held-out panel has {group["accepted"]} accepted of {group["allocated"]} allocated (<10); retain allocated denominator, do not backfill')
    if requested_recipe is None:
        recipe_root = Path(frozen['root']) if frozen and frozen.get('root') else ROOT
        expected_recipe = (frozen or {}).get('files', {}).get('params/cargo/feeder_export.toml')
        recipe=read_snapshot(recipe_root/'params/cargo/feeder_export.toml', expected_sha256=expected_recipe)
        requested_recipe=dict(path=str(recipe.path), sha256=recipe.sha256,
            text=recipe.data.decode('utf-8'), status='profile_intent_not_observed_raw_draws')
    return dict(schema_version=1, requested_recipe=requested_recipe,
        groups=groups, warnings=warnings, backfill=False,
        population='Emitted bookings are conditioned on cargo, C1, D58 and (medium) C2 screens; all allocated requests remain in denominators.')


def _group_identity(entry, *, physical=False):
    """Keep physical families distinct from held-out evaluation panel labels."""
    from stowage.conventions import canonical_vessel_id
    s=entry['spec']
    panel=entry.get('test_family') or 'in_distribution'
    identity=dict(split=entry['split'],rung=s['rung'],family=panel)
    if physical:
        noise=s['blocking_ratio'] if s['generator']=='random_yard' else s['noise']
        # Match the archived split convention: random yards use blocking ratio,
        # simulated yards use simulator noise. Canonicalize only ship aliases.
        noise=float(noise) if noise is not None else None
        fields=dict(ship=canonical_vessel_id(s['vessel']),generator=s['generator'],port=s['port'],noise=noise)
        family='/'.join(str(fields[name]) for name in ('ship','generator','port','noise'))
        identity.update(family=family,**fields)
    return '/'.join((identity['split'],identity['rung'],identity['family'])),identity


def summarize_mix(release, records, instances, *, frozen=None):
    """Report both held-out panels and split×rung×ship×generator×port×noise."""
    records=list(records)
    result=_summarize_population(release,records,instances,frozen=frozen)
    physical=_summarize_population(release,records,instances,frozen=frozen,physical=True,
        requested_recipe=result['requested_recipe'])
    result.update(per_family=physical['groups'],
        family_definition='canonical ship × generator × port × noise (random-yard blocking ratio / simulated-yard noise)')
    return result
