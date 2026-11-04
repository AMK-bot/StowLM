"""Pinned equivalence inputs; no solver, checker mocks, or verdict cache.

ASSUMPTION: production defines C1/C2/C3 per ship (nine recipes total).
The order's 'nine per ship' is not silently reinterpreted as nine recipes.
Archived pairs retain their cargo, moves, fixed loads and liquid masses. Only
current canonical hull and derived voyage metadata, parameter identity, and the D33
ship spelling are refreshed; absent voyages use the production declaration.
Both compared paths receive the same freshly built inputs.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import gzip
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'corpus-manifest.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_ship(name):
    from stowage.vessel.fleet import list_fleet
    names = list_fleet()
    if name in names:
        return name
    if name == 'feeder_1000' and 'feeder_800' in names:
        return 'feeder_800'
    raise ValueError(f'unknown corpus ship {name}')


@lru_cache(maxsize=3)
def vessel_for(name):
    from stowage.vessel.fleet import load_vessel
    return load_vessel(canonical_ship(name))


def _rename(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_rename(item, old, new) for item in value]
    if isinstance(value, dict):
        return {_rename(key, old, new): _rename(item, old, new) for key, item in value.items()}
    return value


def load_saved(row):
    from stowage.model import Instance, MovePlan
    from stowage.generate.instances_voyage import attach_default_voyage
    values = []
    for kind in ('instance', 'plan'):
        path = ROOT / row[kind]
        payload = path.read_bytes()
        assert digest(payload) == row[kind + '_sha256'], f'corpus source changed: {path}'
        values.append(json.loads(gzip.decompress(payload) if path.suffix == '.gz' else payload))
    raw, plan_raw = values
    name = canonical_ship(row['ship'])
    if name != row['ship']:
        raw, plan_raw = (_rename(value, row['ship'], name) for value in (raw, plan_raw))
    if row.get('current_generated'):
        return Instance.model_validate(raw), MovePlan.model_validate(plan_raw)
    canonical = vessel_for(row['ship'])
    old_vessel = raw['vessel']
    for field in ('hull',):
        value = getattr(canonical, field)
        old_vessel[field] = ([item.model_dump(mode='python') for item in value]
                             if isinstance(value, list) else value.model_dump(mode='python'))
    old_vessel['sources'] = [source for source in old_vessel['sources']
                             if not source.startswith('params/fleet/')]
    old_vessel['sources'].extend(source for source in canonical.sources
                                 if source.startswith('params/fleet/'))
    from stowage.model import Tank
    from stowage.vessel.fleet_voyage import build_voyage_policy
    old_vessel['voyage_policy'] = build_voyage_policy(
        [Tank.model_validate(item) for item in old_vessel['tanks']],
        parameter_source=f"{row['instance']} sha256:{row['instance_sha256']}",
        vessel_id=old_vessel['id']).model_dump(mode='python')
    instance = Instance.model_validate(raw)
    if instance.voyage is None:
        instance = attach_default_voyage(instance)
    return instance, MovePlan.model_validate(plan_raw)


def manual_case(ship, condition):
    from stowage.vessel.fleet import load_ship_params
    from stowage.vessel.fleet_manual import standard_manual_conditions
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    # Same production-only instance constructor as the existing manual integration
    # suite; no monkeypatches or historical harness spies are imported here.
    from tests.vessel.test_fleet_manual_restore import _manual_instance
    vessel = vessel_for(ship).model_copy(deep=True)
    recipe = next(row for row in standard_manual_conditions(vessel, load_ship_params(canonical_ship(ship)))
                  if row['condition'] == condition)
    instance = _manual_instance(vessel, recipe)
    plan = MovePlan(id=f'{instance.id}.perf-equivalence', instance_id=instance.id,
        call_index=instance.call_index, ballast=BallastCondition(source='planner',
            tank_masses_kg={tank.id: recipe['departure_tank_masses_kg'][tank.id]
                            for tank in vessel.tanks if tank.adjustable}))
    return instance, plan


@lru_cache(maxsize=1)
def _historical_runner():
    path = ROOT / 'experiments/physics-review/rerun/fix-t2-vessel/recheck.py'
    spec = importlib.util.spec_from_file_location('perf1028_historical', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def convergence_case(ship, condition):
    from stowage.vessel.fleet import load_ship_params
    from stowage.model import Instance, MovePlan
    from stowage.model.plan import BallastCondition
    runner = _historical_runner()
    harness = runner._load_harness()  # removes the historical passive spies
    from regression import witness_masses
    source = runner.REVIEW / f'search_{ship}_{condition}.json'
    historical = json.loads(source.read_text()) if source.exists() else dict(
        witness=None, candidates=[dict(label='DB+HEEL', passed=False)])
    candidate = historical.get('witness') or historical['candidates'][0]
    vessel = vessel_for(ship).model_copy(deep=True)
    masses = witness_masses(vessel, candidate['label'])
    instance, _ = harness.build(canonical_ship(ship), vessel,
        load_ship_params(canonical_ship(ship)), condition, sum(masses.values()))
    for box in instance.containers:
        box.max_gross_mass_kg = 30480.
    instance = runner.attach_voyage(Instance.model_validate(instance.model_dump()))
    plan = MovePlan(id=f'{instance.id}.perf-convergence', instance_id=instance.id,
        call_index=instance.call_index, ballast=BallastCondition(source='planner', tank_masses_kg=masses))
    return instance, plan


def cases():
    return json.loads(MANIFEST.read_text())['cases']


def prepare(row):
    if row['kind'] == 'saved':
        return load_saved(row)
    if row['kind'] == 'manual':
        return manual_case(row['ship'], row['condition'])
    if row['kind'] == 'convergence':
        return convergence_case(row['ship'], row['condition'])
    if row['kind'] == 'near_boundary':
        instance, plan = manual_case(row['ship'], 'C1')
        # The unconstrained pilot used qualified interpolation; tightening its
        # GM floor triggered the existing near-limit direct-geometry recheck.
        # Pin this boundary to that measured direct departure GM +/- 1e-8 m.
        # The scalar is materialized in the manifest, never recomputed from the
        # optimized path while verifying equivalence.
        instance.rule_overrides['iscode.loading.gm'] = {'minimum_gm': row['minimum_gm']}
        return instance, plan
    raise ValueError(row['kind'])


def compare_reports(old, new):
    """Every verdict/shape/string exact; every number <=1e-9 rel or abs.

    Only Provenance.created_at is informational under the model's documented
    determinism contract. All measured values, limits, margins and diagnostics
    remain compared. Return non-bitwise numeric changes for explicit reporting.
    """
    import math
    changes = []

    def visit(a, b, path='report'):
        if isinstance(a, dict):
            assert isinstance(b, dict) and a.keys() == b.keys(), path
            for key in a:
                if key == 'created_at' and path.endswith('.provenance'):
                    continue
                visit(a[key], b[key], f'{path}.{key}')
        elif isinstance(a, list):
            assert isinstance(b, list) and len(a) == len(b), path
            for index, (left, right) in enumerate(zip(a, b)):
                visit(left, right, f'{path}[{index}]')
        elif isinstance(a, (int, float)) and not isinstance(a, bool):
            assert isinstance(b, (int, float)) and not isinstance(b, bool), path
            assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), (path, a, b)
            if a != b or (isinstance(a, float) and isinstance(b, float) and a.hex() != b.hex()):
                changes.append(dict(path=path, reference=a, optimized=b))
        else:
            assert type(a) is type(b) and a == b, (path, a, b)

    visit(old.model_dump(mode='json'), new.model_dump(mode='json'))
    return changes
