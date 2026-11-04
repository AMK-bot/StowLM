"""Development-only deterministic counterfactual probes; no planner or native audit.

Run via ``python -m experiments.mvp.pipeline.sealed.rule_binding_study``. Existing
no-rule plans are selected before any instruction probe, by immutable identity,
profile, publication phase, then earliest timestamp and source path. Failed and
missing baselines remain explicit; a later passing plan cannot replace them.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from .common import HERE, ROOT, HarnessError, atomic_json, canonical, object_hash, resolve, sha256
from .driver import public_path
from .instructions import RULE_TYPES, instruction_targets, public_facts, select_rule

DEFAULT_SOURCES = (
    HERE.parent/'binding/measurements/deadline-live-20261102T043801Z',
    HERE.parent/'binding/measurements/rows',
    HERE.parent/'lattice/screening', HERE.parent/'lattice/store')
SELECTION = ('Keep profiles and preview/final phases separate; within each map/profile/phase '
             'select earliest timestamp, then source path and row index, without considering '
             'pass status or instruction outcomes. Prefer a matching measured frozen standard '
             'profile for the diagnostic headline; otherwise use the earliest profile group. '
             'Never replace a selected failed baseline with a later passing one.')


def load_development(path):
    """Hash only the explicitly listed public development inputs, then fix ranks."""
    path = public_path(path)
    result = []
    for line in path.read_text().splitlines():
        name = line.split('#', 1)[0].strip()
        if not name:
            continue
        source = public_path(name)
        payload = source.read_bytes()
        raw = json.loads(payload)
        map_id = 'dev:'+str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else 'dev:'+str(source)
        result.append(dict(map_id=map_id, path=str(source),
            map_sha256=hashlib.sha256(payload).hexdigest(), instance_id=raw['id'],
            facts_sha256=object_hash(public_facts(raw)), raw=raw))
    if not result or len({m['path'] for m in result}) != len(result):
        raise HarnessError('Development list must be nonempty with unique paths')
    order = sorted(result, key=lambda m: (canonical(public_facts(m['raw'])), m['map_id']))
    ranks = {m['map_id']: rank for rank, m in enumerate(order)}
    for item in result:
        item['cohort_index'] = ranks[item['map_id']]
        item['assigned_type'] = RULE_TYPES[item['cohort_index'] % 4]
    return result


def _timestamp(row):
    for name in ('started_at', 'started_utc', 'computed_utc', 'finished_at'):
        value = row.get(name)
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except ValueError:
                pass
    return float('inf')


def normalize_source(row, source, digest, index=None):
    """Extract explicit no-rule evidence without treating constrained plans as baselines."""
    if not isinstance(row, dict):
        return None
    evidence = row.get('evidence') if isinstance(row.get('evidence'), dict) else {}
    identity = row.get('measurement_identity', {}).get('payload', {})
    if any(rules is not None and rules != [] for rules in
           (row.get('rules'), row.get('canonical_rules'), evidence.get('rules'))):
        return None
    if row.get('rule_type') == 'baseline':
        if row.get('canonical_rule') is not None or identity.get('canonical_rule') is not None:
            return None
    else:
        rules = row.get('canonical_rules', row.get('rules', evidence.get('rules')))
        if rules != []:
            return None
    name = row.get('map') or row.get('path') or row.get('instance_path') or identity.get('input_path')
    raw_hash = row.get('input_sha256') or row.get('file_sha256') or identity.get('input_sha256')
    if not name or not raw_hash:
        return None
    profile = row.get('profile') or identity.get('profile') or 'unknown'
    profile_name = profile.get('name', 'unknown') if isinstance(profile, dict) else str(profile)
    recipe = (profile if isinstance(profile, dict) else
              identity.get('resolved_settings') or dict(budget_s=row.get('budget_s'),
                                                       finalize_s=row.get('finalize_s'), config=row.get('config')))
    # Deadline supervisors label the same work recipe with an additional hard
    # wall; preserve it as a different profile, rather than silently pooling.
    recipe = dict(profile=recipe, hard_wall_s=row.get('hard_wall_s'))
    phase = 'preview' if row.get('selected_before_profile_completion') is True or '.preview.' in source.name else 'final'
    group = profile_name+':'+object_hash(recipe)[:12]+':'+phase
    checker = row.get('full_checker') or row.get('checker') or evidence.get('checker') or {}
    level = row.get('level', evidence.get('level'))
    status = row.get('status') or evidence.get('status')
    attested = (isinstance(checker, dict) and checker.get('passed') is True
                and level in (None, 0, 'L0') and status == 'checked_plan'
                and (row.get('passed') is True or evidence.get('passed') is True))
    stamp = _timestamp(row)
    return dict(source_path=str(source), source_sha256=digest, source_row_index=index,
        map_path=str(resolve(name)), input_sha256=raw_hash,
        instance_id=row.get('instance_id') or (row.get('id') if row.get('rule_type') == 'baseline' else None),
        profile=profile_name, profile_group=group, profile_recipe=recipe, phase=phase,
        selected_timestamp=stamp if stamp != float('inf') else None,
        source_order=(stamp, str(source), -1 if index is None else index),
        recorded_passed=row.get('passed'), recorded_status=status,
        recorded_level=level, recorded_full_checker_pass=attested,
        plan=row.get('plan') if isinstance(row.get('plan'), dict) else None)


def collect_sources(roots=DEFAULT_SOURCES):
    """Inspect only the supplied public result stores, retaining source byte hashes."""
    records, inventory, errors = [], {}, []
    for root in roots:
        root = public_path(root)
        if not root.exists():
            errors.append(dict(source_path=str(root), error='source directory absent'))
            continue
        files = ([root] if root.is_file() else
                 sorted(root.glob('attempts/*/result.json'))+[root/'reused.json']
                 if root.name.startswith('deadline-live-') else sorted(root.rglob('*.json')))
        for path in files:
            if not path.is_file():
                continue
            path = public_path(path)
            try:
                payload = path.read_bytes()
                digest = hashlib.sha256(payload).hexdigest()
                value = json.loads(payload)
                inventory[str(path)] = digest
                rows = value.get('rows') if isinstance(value, dict) else None
                for index, row in enumerate(rows if isinstance(rows, list) else [value]):
                    record = normalize_source(row, path, digest, index if isinstance(rows, list) else None)
                    if record is not None:
                        records.append(record)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                errors.append(dict(source_path=str(path), error=type(error).__name__+': '+str(error)))
    return records, inventory, errors


def choose_baselines(maps, records):
    """Identity first, then fixed chronological choice; never prefer a passing row."""
    by_path = {m['path']: m for m in maps}
    groups, rejected = {}, []
    for record in records:
        item = by_path.get(record['map_path'])
        if item is None:
            continue
        if record['input_sha256'] != item['map_sha256']:
            rejected.append(dict(source_path=record['source_path'], map_path=item['path'],
                                 reason='baseline raw input SHA256 differs'))
            continue
        plan = record.get('plan')
        if (plan is not None and (plan.get('instance_id') != item['instance_id']
                                 or plan.get('call_index') != item['raw']['call_index'])):
            rejected.append(dict(source_path=record['source_path'], map_path=item['path'],
                                 reason='baseline plan instance/call identity differs'))
            continue
        if record.get('instance_id') is not None and record['instance_id'] != item['instance_id']:
            rejected.append(dict(source_path=record['source_path'], map_path=item['path'],
                                 reason='baseline row instance identity differs'))
            continue
        bucket = groups.setdefault(record['profile_group'], {})
        previous = bucket.get(item['map_id'])
        if previous is None or record['source_order'] < previous['source_order']:
            bucket[item['map_id']] = record
    return groups, rejected


def candidate_rules(raw, rank):
    """Current draw plus public-geometry-only tier alternatives; no plan argument."""
    from experiments.mvp.reader.schema import context_from_instance
    rules = {kind: select_rule(raw, rank//4*4+index) for index, kind in enumerate(RULE_TYPES)}
    cap = rules['max_tiers']['constraint']
    context = context_from_instance(raw)
    bay_physical = context['max_tiers_by_bay'][str(cap['bay'])][cap['zone']]
    alternatives = {'bay_physical_minus3': dict(schema_version='stowlm.reader/2',
        constraint=dict(cap, max_tiers=max(0, bay_physical-3)))}
    zone = 'deck' if context['max_tiers']['deck'] else 'hold'
    for reduction in (2, 3):
        alternatives['global_physical_minus'+str(reduction)] = dict(schema_version='stowlm.reader/2',
            constraint=dict(type='max_tiers', bay=None, zone=zone,
                            max_tiers=max(0, context['max_tiers'][zone]-reduction)))
    return rules, alternatives


def probe(instance, plan, rule):
    """Production instruction semantics and cheap geometry; no full checker calls."""
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.occupancy import apply_reader_constraint
    answer = validate_answer(json.dumps(rule), context_from_instance(instance))
    errors = list(intrinsic_violations(instance, answer, plan))
    shortfall, retained = None, []
    if rule['constraint']['type'] != 'one_port_per_hatch':
        constrained = apply_reader_constraint(instance, answer)
        shortfall = constrained.capacity_shortfall()
        retained = constrained.retained_violations()
    return dict(rule=rule, rule_sha256=object_hash(rule), binds=bool(errors),
        counterfactual_recheck=not errors, violations=errors,
        simple_capacity_shortfall=shortfall, retained_cargo_conflicts=retained,
        **instruction_targets(instance, rule))


def _counts(rows, field, kind):
    values = [r.get(field, {}).get(kind) for r in rows]
    known = [v for v in values if v is not None and 'binds' in v]
    return dict(maps=len(rows), observed=len(known), binding=sum(v['binds'] for v in known),
        already_obeyed=sum(not v['binds'] for v in known), unavailable=len(rows)-len(known),
        simple_capacity_shortfalls=sum(v.get('simple_capacity_shortfall') is not None for v in known),
        retained_conflicts=sum(bool(v.get('retained_cargo_conflicts')) for v in known))


def build_study(maps, records):
    from stowage.model import Instance, MovePlan
    from stowage.moves import apply_plan
    groups, rejected = choose_baselines(maps, records)
    reports = {}
    for name, selected in sorted(groups.items()):
        rows = []
        for item in maps:
            base = selected.get(item['map_id'])
            row = {k: item[k] for k in ('map_id', 'path', 'map_sha256', 'instance_id',
                                        'facts_sha256', 'cohort_index', 'assigned_type')}
            row.update(baseline_status='missing', assigned={}, forced={}, tier_candidates={})
            rules, alternatives = candidate_rules(item['raw'], item['cohort_index'])
            row['drawn_rule'] = rules[item['assigned_type']]
            if base is not None:
                row['baseline'] = {k:v for k,v in base.items() if k not in {'plan', 'source_order'}}
                row['baseline_status'] = 'no_recorded_checked_plan'
                if base['recorded_full_checker_pass'] and base.get('plan') is not None:
                    try:
                        instance, plan = Instance.model_validate(item['raw']), MovePlan.model_validate(base['plan'])
                        replay = apply_plan(instance, plan)
                        loaded = {p.container_id for p in replay.final_state.ship.placements}
                        if not replay.legal or not set(instance.load_list) <= loaded:
                            raise ValueError('baseline failed cheap physical replay or left load boxes ashore')
                        row.update(baseline_status='recorded_checked_plan_replayed',
                                   plan_sha256=object_hash(base['plan']), replay_legal=True, all_loaded=True)
                        row['forced'] = {kind: probe(instance, plan, rule) for kind, rule in rules.items()}
                        row['assigned'] = {item['assigned_type']: row['forced'][item['assigned_type']]}
                        row['tier_candidates'] = {kind: probe(instance, plan, rule)
                                                  for kind, rule in alternatives.items()}
                    except Exception as error:
                        row.update(baseline_status='probe_error', error=type(error).__name__+': '+str(error))
            rows.append(row)
        example = next(iter(selected.values()))
        reports[name] = dict(profile=example['profile'], profile_recipe=example['profile_recipe'],
            phase=example['phase'], expected_maps=len(maps),
            statuses=dict(Counter(r['baseline_status'] for r in rows)), rows=rows,
            assigned={kind: _counts([r for r in rows if r['assigned_type'] == kind], 'assigned', kind)
                      for kind in RULE_TYPES},
            forced={kind: _counts(rows, 'forced', kind) for kind in RULE_TYPES},
            tier_candidates={kind: _counts(rows, 'tier_candidates', kind) for kind in
                ('bay_physical_minus3', 'global_physical_minus2', 'global_physical_minus3')})
    measured_standard = []
    try:
        from dataclasses import asdict, is_dataclass
        from experiments.mvp.pipeline.runner.profiles import get_profile
        standard = {k:asdict(v) if is_dataclass(v) else v for k,v in get_profile('standard').items()}
        if standard.get('pending_measurement') is False:
            measured_standard = [name for name, values in reports.items()
                if values['profile'] == 'standard' and values['phase'] == 'final'
                and values['profile_recipe']['profile'] == standard]
    except (ImportError, ValueError, TypeError):
        pass
    options = measured_standard or list(reports)
    preferred = min(options, key=lambda name: min(r['source_order'] for r in groups[name].values())) if options else None
    return dict(schema_version='development-rule-binding/1', split='development',
        preferred_profile_group=preferred,
        preferred_profile_basis='matching measured frozen standard' if measured_standard else 'earliest recorded profile; developmental only',
        fixture_surrogate=False, expected_maps=len(maps), selection=SELECTION,
        development_maps=[{k:v for k,v in item.items() if k != 'raw'} for item in maps],
        full_checker_audit_performed=False, release_calibration_eligible=False,
        notes=['Counterfactual probes of recorded no-rule plans, not new planning trials or reader measurements.',
               'Recorded checker attestations and fresh physical replay are not a fresh full checker audit.',
               'Profile variants and preview/final phases are separate; no passing-row substitution.',
               'A majority among observed maps does not establish a majority of the full development cohort.'],
        profiles=reports, rejected_baselines=rejected)


def run_study(out_dir, *, dev_list=None, sources=DEFAULT_SOURCES):
    started = time.time()
    out = resolve(out_dir)
    if not out.is_relative_to(HERE/'fixtures'):
        raise HarnessError('Study outputs must stay under sealed/fixtures')
    if out.exists() and any(out.iterdir()):
        raise HarnessError('Study output must be a new empty directory')
    dev_list = public_path(dev_list or HERE.parent/'maps/dev.txt')
    maps = load_development(dev_list)
    records, inventory, errors = collect_sources(sources)
    result = build_study(maps, records)
    result.update(computed_utc=datetime.now(timezone.utc).isoformat(), source_errors=errors,
        input_hashes={str(dev_list): sha256(dev_list), **{m['path']:m['map_sha256'] for m in maps}},
        source_hashes=inventory,
        code_hashes={str(path):sha256(path) for path in
            (Path(__file__), HERE/'instructions.py', HERE.parent.parent/'reader/schema.py',
             HERE.parent.parent/'reader/pipeline.py', HERE.parent.parent/'reader/occupancy.py',
             ROOT/'src/llm/instructions/apply.py', ROOT/'src/stowage/moves/__init__.py',
             HERE.parent/'runner/profiles.py', *sorted((ROOT/'src/stowage/moves').glob('*.py')),
             *sorted((ROOT/'src/stowage/model').glob('*.py'))) },
        wall_s=time.time()-started)
    out.mkdir(parents=True, exist_ok=True)
    selected_paths = {r['baseline']['source_path'] for p in result['profiles'].values()
                      for r in p['rows'] if 'baseline' in r}
    snapshots = {}
    for name in sorted(selected_paths):
        payload = Path(name).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != inventory[name]:
            raise HarnessError('Selected source changed during study; restart into a new directory')
        destination = out/'source-snapshots'/(digest+'.json')
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(payload)
        snapshots[name] = str(destination)
    result['source_snapshots'] = snapshots
    atomic_json(out/'study.json', result)
    lines = ['# Development rule-binding study', '', SELECTION, '', *result['notes'], '',
             '| Profile / phase | Probe | Binding / observed | Already obeyed | Missing/failed | Capacity shortfall | Retained conflict |',
             '|---|---|---:|---:|---:|---:|---:|']
    for name, profile in result['profiles'].items():
        for group in ('assigned', 'forced', 'tier_candidates'):
            for kind, count in profile[group].items():
                lines.append(f"| {name} | {group}: {kind} | {count['binding']}/{count['observed']} | "
                    f"{count['already_obeyed']} | {count['unavailable']} | "
                    f"{count['simple_capacity_shortfalls']} | {count['retained_conflicts']} |")
    (out/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--dev-list')
    parser.add_argument('--source', action='append')
    args = parser.parse_args()
    result = run_study(args.out_dir, dev_list=args.dev_list,
                       sources=args.source or DEFAULT_SOURCES)
    print(json.dumps(dict(expected_maps=result['expected_maps'],
        profiles={k:dict(statuses=v['statuses'], forced=v['forced'],
                        tier_candidates=v['tier_candidates']) for k,v in result['profiles'].items()},
        wall_s=result['wall_s'])))


if __name__ == '__main__':
    main()
