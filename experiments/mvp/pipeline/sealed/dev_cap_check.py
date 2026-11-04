"""Survey the fixed cap on existing development baselines, without planning.

This is a diagnostic, never a calibration release receipt. It can run before
dev.txt is published: requests.json supplies the complete public development
population. Missing maps cannot silently change ranks or the denominator.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .common import HERE, ROOT, HarnessError, atomic_json, canonical, object_hash
from .driver import public_path
from .instructions import CAP_REDUCTION, public_facts, select_rule

DEFAULT_SOURCES = (HERE.parent/'binding/measurements/rows',
                   HERE.parent/'lattice/screening', HERE.parent/'lattice/store')


def _read(path):
    path = public_path(path)
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def _load_development(path):
    from stowage.io import _check_version
    from stowage.model import Instance
    raw, digest = _read(path)
    _check_version(raw.get('schema_version'))
    return Instance.model_validate(raw), digest


def _current_profile():
    from experiments.mvp.pipeline.runner.profiles import get_profile
    profile = {k: asdict(v) if is_dataclass(v) else v for k,v in get_profile('standard').items()}
    return profile


def _baseline(row):
    # A missing rules field in a preview is not evidence of an empty brief.
    # Keep final snapshots, never choose an incumbent or a low-lift variant.
    if not isinstance(row, dict) or row.get('plan') is None:
        return False
    if row.get('rules') not in (None, []) or row.get('canonical_rules') not in (None, []):
        return False
    if row.get('rule_type') == 'baseline':
        return row.get('canonical_rule') is None
    if row.get('arm') == 'pipeline':
        return True
    return row.get('rules') == [] or row.get('canonical_rules') == []


def _analyse(instance, raw_plan, rank):
    from stowage.model import MovePlan
    from experiments.mvp.reader.schema import context_from_instance
    from .worker import _counterfactual
    plan = MovePlan.model_validate(raw_plan)
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise HarnessError('Baseline plan identity differs from the development map')
    context = context_from_instance(instance)
    bays = sorted(b for b in context['bays'] if b % 2)
    by_bay = []
    for index, bay in enumerate(bays):
        rule = select_rule(instance, 4*index+1)
        check = _counterfactual(instance, raw_plan, rule)
        by_bay.append(dict(bay=bay, rule=rule, rule_sha256=object_hash(rule),
            broke_rule=check['counterfactual_instruction_recheck'] is False,
            violations=check['counterfactual_instruction_violations']))
    selected = by_bay[(rank//4) % len(bays)] if rank is not None and bays else None
    return dict(plan_sha256=object_hash(raw_plan), by_bay=by_bay,
        binding_bays=sum(row['broke_rule'] for row in by_bay), physical_bays=len(bays),
        assigned_rule=selected,
        scope='Fresh intended-rule recheck only; full checker/completion/mask not rerun')


def check(*, dev_dir=None, requests_path=None, source_dirs=None, out_dir=None):
    """Return JSON evidence for every registered dev map and every baseline.

    Source files and map bytes are hash-pinned in this snapshot. All eligible
    final baselines are retained, including conflicting plans on the same map;
    a baseline's claimed pass is reported, never promoted to a fresh audit.
    """
    dev_dir = public_path(dev_dir or HERE.parent/'maps/dev')
    request_path = public_path(requests_path or HERE.parent/'maps/requests.json')
    request, request_hash = _read(request_path)
    expected = [public_path(row['path']) for row in request['specs'] if row['split'] == 'dev']
    if len(expected) != 32 or len(set(expected)) != 32 or any(p.parent != dev_dir for p in expected):
        raise HarnessError('Expected exactly the registered 32 development maps, within dev/')
    extra = sorted(p for p in dev_dir.glob('*.json') if public_path(p) not in expected)
    if extra:
        raise HarnessError('Unregistered development map; population substitution refused')
    loaded, maps = {}, []
    for path in sorted(expected):
        item = dict(map_path=str(path), map_sha256=None, baseline_candidates=[], status='missing_map')
        try:
            instance, digest = _load_development(path)
            loaded[path] = instance
            item.update(map_sha256=digest, instance_id=instance.id, status='no_baseline_plan')
        except FileNotFoundError:
            pass
        except Exception as error:
            item.update(status='map_error', error=f'{type(error).__name__}: {error}')
        maps.append(item)
    complete = len(loaded) == len(expected)
    # Even a generated map can rank differently while other maps are missing.
    # Never substitute an available-only allocation for the registered one.
    ranks = ({path:rank for rank,path in enumerate(sorted(loaded,
        key=lambda path:(canonical(public_facts(loaded[path])), str(path))))} if complete else {})
    index = {Path(row['map_path']):row for row in maps}
    source_errors = []
    profile = _current_profile()
    current_profile_hash = object_hash(profile)
    from experiments.mvp.pipeline.contract.schema import sha256_json
    for directory in source_dirs if source_dirs is not None else DEFAULT_SOURCES:
        directory = public_path(directory)
        for source in sorted(directory.glob('*.json')):
            try:
                row, digest = _read(source)
                if not _baseline(row):
                    continue
                name = row.get('map') or row.get('path') or row.get('map_path')
                if not name:
                    continue
                path = public_path(name)
                if path not in loaded:
                    continue  # Never open an old or nondevelopment map to match it.
                item, instance = index[path], loaded[path]
                candidate = dict(source=str(source.resolve()), source_sha256=digest,
                    reported_passed=row.get('passed') is True,
                    profile=row.get('profile'), current_profile_matches=False,
                    fresh_full_audit=False, release_eligible=False)
                raw_hash = row.get('map_sha256', row.get('file_sha256'))
                canonical_hash = row.get('instance_sha256')
                if raw_hash is not None and raw_hash != item['map_sha256']:
                    raise HarnessError('Source map byte hash differs from development map')
                if canonical_hash is not None and canonical_hash != sha256_json(instance.model_dump(mode='json')):
                    raise HarnessError('Source canonical instance hash differs from development map')
                candidate['input_hash_verified'] = raw_hash is not None or canonical_hash is not None
                candidate['current_profile_matches'] = (isinstance(row.get('profile'), dict)
                    and object_hash(row['profile']) == current_profile_hash)
                candidate.update(_analyse(instance, row['plan'], ranks.get(path)))
                item['baseline_candidates'].append(candidate)
                item['status'] = 'diagnostic_baseline_checked'
            except Exception as error:
                source_errors.append(dict(source=str(source), error=f'{type(error).__name__}: {error}'))
    count = sum(bool(row['baseline_candidates']) for row in maps)
    baselines = [base for row in maps for base in row['baseline_candidates']]
    needs = []
    if not complete:
        needs.append(f'Complete registered generation: {len(loaded)}/32 maps available; exact rank/bay allocation withheld.')
    if count < 32:
        needs.append(f'No-rule baseline plans for the remaining {32-count} development maps; no successful-subset denominator.')
    needs += ['Frozen measured standard profile, matching input hashes and baseline recipe.',
        'Fresh subprocess full-checker/completion/mask audits and assigned-rule counterfactuals; >=17 binding maps out of32.',
        'A sealed-tier-calibration/1 receipt accepted by calibration.verify_calibration before release.']
    result = dict(schema_version='sealed-dev-cap-diagnostic/1', split='development',
        checked_utc=datetime.now(timezone.utc).isoformat(), fixture_surrogate=False,
        release_eligible=False, cap_reduction=CAP_REDUCTION, expected_maps=32, available_maps=len(loaded),
        registered_population_complete=complete, allocation_rank_final=complete,
        maps_with_baselines=count, baseline_candidates=len(baselines),
        fresh_full_audits=0, verified_binding_maps=None,
        assigned_rule_diagnostic_breaks=(sum(b['assigned_rule']['broke_rule'] for b in baselines
            if b['assigned_rule'] is not None) if complete else None),
        needs=needs, maps=maps, source_errors=source_errors,
        source_sha256={str(request_path):request_hash, **{str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), HERE/'instructions.py', HERE/'worker.py',
                ROOT/'experiments/mvp/reader/pipeline.py', ROOT/'experiments/mvp/reader/schema.py',
                ROOT/'src/llm/instructions/apply.py')}},
        current_profile_sha256=current_profile_hash,
        standard_profile_measured=profile.get('pending_measurement') is False,
        limitations=['Existing final baseline snapshots only; no plan search, reader, or full native checker ran.',
                    'Bay diagnostics are correlated checks on the same plan, not independent map trials.',
                    'Missing maps keep the population at32 and cannot silently change the rule allocation.',
                    'No baseline is selected by lift count, cap violation or claimed pass; all matching final rows retained.'])
    if out_dir is not None:
        out = public_path(out_dir)
        if not out.is_relative_to(HERE.resolve()):
            raise HarnessError('Development check outputs must stay inside sealed/')
        atomic_json(out/'check.json', result)
        lines = ['# Development max_tiers check', '', 'Diagnostic only; release calibration NOT established.', '',
            f'Maps: {len(loaded)}/32; maps with existing baseline plans: {count}; fresh full audits:0.', '',
            '| Map | Baseline source | Binding bays / physical bays | Assigned rule |',
            '|---|---|---:|---|']
        for item in maps:
            for base in item['baseline_candidates']:
                lines.append(f"| {Path(item['map_path']).name} | {Path(base['source']).name} | "
                    f"{base['binding_bays']}/{base['physical_bays']} | "
                    f"{base['assigned_rule'] if complete else 'Withheld: population incomplete'} |")
        lines += ['', 'Needed:', '', *['- '+need for need in needs], '', *result['limitations'], '']
        (out/'CHECK.md').write_text('\n'.join(lines))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    result = check(out_dir=args.out_dir)
    print(f"Development cap check: {result['available_maps']}/32 maps; "
          f"{result['maps_with_baselines']} baseline maps; release_eligible=False")


if __name__ == '__main__':
    main()
