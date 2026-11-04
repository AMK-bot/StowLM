"""Summarize development-only budget checkpoints; never launch an engine."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics

from experiments.mvp.pipeline.contract.schema import sha256_json

LABELS = ('60', '120', '300', 'default')


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _stats(values):
    values = [v for v in values if _number(v)]
    return dict(n=len(values), mean=statistics.mean(values) if values else None,
                median=statistics.median(values) if values else None,
                min=min(values) if values else None, max=max(values) if values else None)


def _pass(row):
    return (row.get('status') == 'checked_plan'
            and row.get('full_pass', row.get('passed')) is True)


def _label(row):
    return str(row['budget_label'])


def _key(row):
    return (str(row.get('map_id', row.get('map'))), row['rule_mode'])


def _rows(rows):
    """One completed checkpoint per job; latest input wins on resumed jobs."""
    unique = {}
    for row in rows:
        if row.get('status') in {'interrupted', 'running', 'pending'}:
            continue
        key = row.get('job_id') or (*_key(row), _label(row))
        unique[key] = row
    return list(unique.values())


def summarize(rows):
    """Full passes alone contribute lifts; relaxed counts stay separate."""
    cells = defaultdict(list)
    for row in _rows(rows):
        cells[(row['size_class'], row['rule_mode'], _label(row))].append(row)
    result = []
    for (size, mode, label), group in sorted(cells.items(), key=lambda item:
            (item[0][0], item[0][1], LABELS.index(item[0][2]) if item[0][2] in LABELS else len(LABELS))):
        passed = [r for r in group if _pass(r)]
        relaxed = [r for r in group if r.get('status') == 'relaxed_plan']
        result.append(dict(
            size_class=size, rule_mode=mode, budget_label=label,
            attempts=len(group), passes=len(passed), relaxed=len(relaxed),
            timed_out=sum(r.get('timed_out') is True or r.get('status') == 'timeout' for r in group),
            errors=sum(r.get('status') == 'error' for r in group),
            pass_rate=len(passed) / len(group),
            lifts_passing=_stats([r.get('lifts') for r in passed]),
            first_legal_s=_stats([r.get('first_legal_s') for r in group]),
            total_s=_stats([r.get('total_s', r.get('wall_s')) for r in group]),
            engine_s=_stats([r.get('engine_s') for r in group]),
            load=dict(start_load1=_stats([r.get('start', {}).get('load1') for r in group]),
                      end_load1=_stats([r.get('end', {}).get('load1') for r in group]),
                      start_enabled_cores=_stats([r.get('start', {}).get('enabled_cores') for r in group]),
                      end_enabled_cores=_stats([r.get('end', {}).get('enabled_cores') for r in group]))))
    return result


def paired_costs(rows):
    """Pair each shorter arm to default on the same map and instruction."""
    cells = defaultdict(dict)
    for row in _rows(rows):
        cells[(row['size_class'], row['rule_mode'])][(_key(row)[0], _label(row))] = row
    result = []
    for (size, mode), group in sorted(cells.items()):
        for label in LABELS[:-1]:
            pairs = [(r, group[(map_id, 'default')]) for (map_id, budget), r in group.items()
                     if budget == label and (map_id, 'default') in group]
            if not pairs and not any(budget == label for _, budget in group):
                continue
            both = [(a, b) for a, b in pairs if _pass(a) and _pass(b)]
            differences = [a['lifts'] - b['lifts'] for a, b in both
                           if _number(a.get('lifts')) and _number(b.get('lifts'))]
            result.append(dict(size_class=size, rule_mode=mode, budget_label=label,
                reference='default', paired_attempts=len(pairs),
                missing_reference=sum(budget == label and (map_id, 'default') not in group
                                      for map_id, budget in group),
                short_passes=sum(_pass(a) for a, _ in pairs),
                default_passes=sum(_pass(b) for _, b in pairs),
                passes_lost=sum(not _pass(a) and _pass(b) for a, b in pairs),
                passes_gained=sum(_pass(a) and not _pass(b) for a, b in pairs),
                both_pass=len(both), extra_lifts_short_minus_default=_stats(differences)))
    return result


def _expected_ids(expected_jobs):
    if expected_jobs is None:
        return None
    return {str(j['job_id']) if isinstance(j, dict) else str(j) for j in expected_jobs}


def _completeness(rows, expected_jobs):
    medium = [r for r in rows if r.get('size_class') == 'medium']
    observed = {(_key(r), _label(r)) for r in medium}
    map_ids = {_key(r)[0] for r in medium}
    keys = {(map_id, mode) for map_id in map_ids for mode in ('none', 'one_port_per_hatch')}
    modes = {r['rule_mode'] for r in medium}
    labels = {_label(r) for r in medium}
    missing_cells = [(key[0], key[1], label) for key in sorted(keys) for label in LABELS
                     if (key, label) not in observed]
    ids = _expected_ids(expected_jobs)
    done = {str(r['job_id']) for r in rows if r.get('job_id') is not None}
    missing_jobs = sorted(ids - done) if ids is not None else None
    complete = (bool(medium) and {'none', 'one_port_per_hatch'} <= modes
                and set(LABELS) <= labels and not missing_cells
                and (missing_jobs is None or not missing_jobs))
    return dict(complete=complete, expected_jobs=len(ids) if ids is not None else None,
                completed_jobs=len(done), missing_jobs=missing_jobs,
                medium_missing_cells=missing_cells,
                caveat=('Completeness is against the supplied manifest.' if ids is not None else
                        'No manifest supplied: completeness covers observed maps only.'))


def _profile(rows, label, cap=None):
    selected = [r for r in rows if _label(r) == label]
    source = selected[0]
    profile = source.get('profile', {})
    kwargs = source.get('actual_kwargs') or {
        k: source.get(k, profile.get(k)) for k in ('budget_s', 'finalize_s', 'seed', 'config')}
    durations = [r['engine_s'] for r in selected if _number(r.get('engine_s'))]
    measured_max = max(durations) if durations else None
    deadline = math.ceil(measured_max * 1.2 / 30) * 30 if measured_max is not None else None
    if cap is not None and deadline is not None:
        deadline = min(cap, deadline)
    return dict(budget_label=label, engine_kwargs=kwargs, engine_timeout_s=deadline,
                measured_engine_max_s=measured_max,
                measured_first_legal_s=_stats([r.get('first_legal_s') for r in selected]),
                measured_total_s=_stats([r.get('total_s', r.get('wall_s')) for r in selected]),
                deadline_basis='Maximum observed engine duration plus 20%, rounded up to 30 s'
                    + ('; capped at 900 s for standard.' if cap is not None else '.'))


def recommend(rows, *, expected_jobs=None):
    """Recommend only complete medium arms; no claim of optimal settings."""
    completed = _rows(rows)
    completeness = _completeness(completed, expected_jobs)
    medium = [r for r in completed if r.get('size_class') == 'medium']
    if not completeness['complete']:
        return dict(provisional=True, live=None, standard=None, completeness=completeness,
                    reasoning=['Study incomplete: retain provisional profiles until every expected medium arm finishes.'])
    groups = {label: [r for r in medium if _label(r) == label] for label in LABELS}
    keys = {_key(r) for r in medium}
    # Every candidate must cover the same maps and both instruction arms.
    if any({_key(r) for r in group} != keys for group in groups.values()):
        return dict(provisional=True, live=None, standard=None, completeness=completeness,
                    reasoning=['Budget arms cover different maps: complete paired measurements first.'])
    pass_keys = {label: {_key(r) for r in group if _pass(r)} for label, group in groups.items()}
    union = set().union(*pass_keys.values())
    best = max(len(v) for v in pass_keys.values())
    if best == 0:
        return dict(provisional=True, live=None, standard=None, completeness=completeness,
                    reasoning=['No measured medium arm produced a full checker pass; no profile can be calibrated.'])
    retain = [label for label in LABELS if len(pass_keys[label]) == best]
    no_default_loss = [label for label in retain if pass_keys['default'] <= pass_keys[label]]
    live = (no_default_loss or retain)[0]
    eligible = [label for label in retain if pass_keys[live] <= pass_keys[label]
                if all(_number(r.get('engine_s')) and r['engine_s'] <= 900 for r in groups[label])]
    reasons = [f'Live chooses the smallest measured budget retaining {best}/{len(keys)} full passes, '
               'preferring retention of every default-passing arm when possible, '
               f'across paired medium map/instruction arms; {len(union - pass_keys[live])} '
               'passes found by another budget are absent.']
    if 'default' in groups:
        lost = len(pass_keys['default'] - pass_keys[live])
        reasons.append(f'Live loses {lost} default-passing arms; relaxed plans never count as passes.')
    standard = None
    if eligible:
        common = set.intersection(*(pass_keys[label] for label in eligible))
        # Require finite lifts for all common arms before using an average.
        metric = {}
        for label in eligible:
            values = { _key(r): r.get('lifts') for r in groups[label] if _key(r) in common }
            metric[label] = (statistics.mean(values.values()) if common and
                             all(_number(v) for v in values.values()) else None)
        measurable = [label for label in eligible if metric[label] is not None]
        chosen = min(measurable, key=lambda label: (metric[label], LABELS.index(label))) if measurable else eligible[0]
        standard = _profile(medium, chosen, cap=900)
        standard['paired_lift_arms'] = len(common)
        standard['paired_mean_lifts'] = metric[chosen]
        reasons.append(f'Standard retains {best}/{len(keys)} passes and has paired mean lifts '
                       f'{metric[chosen]} on {len(common)} common passing arms among '
                       f'{eligible}; all its measured engine calls finish within 900 s.')
        if not measurable:
            reasons.append('No common finite lift sample supports a lift ranking; smallest eligible budget chosen.')
    else:
        reasons.append('No equally passing profile finished every measured engine call within 900 s; standard remains provisional.')
    binding_rows = [r for r in medium if r.get('rule_mode') == 'one_port_per_hatch']
    binding_audits = sum(r.get('baseline_audit_status') == 'checked' for r in binding_rows)
    binding_violations = sum(r.get('baseline_violates_rule') is True for r in binding_rows)
    return dict(provisional=standard is None, live=_profile(medium, live), standard=standard,
                binding_evidence=dict(audits=binding_audits, baseline_plans_violating_rule=binding_violations),
                completeness=completeness, reasoning=reasons,
                limitations=['Development measurements only; synthetic medium sections, not whole ships.',
                    'Lifts include projected later-port lifts (D15). Means use full passes only.',
                    'Engine-only study excludes runner assembly, reader, package and final orchestration overhead.',
                    'Shared machine: GPU job uses six cores and another engine benchmark runs until about 21:00; quiet-machine timing may improve.',
                    'Deadlines allow measured headroom, not a whole-run guarantee or optimality claim.'])


def analyze(rows, *, expected_jobs=None):
    raw_rows = list(rows)
    rows = _rows(raw_rows)
    data_hash = sha256_json(sorted(rows, key=lambda r: str(r.get('job_id', (_key(r), _label(r))))))
    decision = dict(recommend(rows, expected_jobs=expected_jobs), data_sha256=data_hash)
    return dict(excluded=dict(censored=sum(r.get('censored') is True for r in raw_rows),
                              interrupted=sum(r.get('status') == 'interrupted' and not r.get('censored') for r in raw_rows)),
                schema='stowlm.profile-study-analysis/1', generated_utc=datetime.now(timezone.utc).isoformat(),
                data_sha256=data_hash,
                measured_rows=len(rows), budget_table=summarize(rows), paired_costs=paired_costs(rows),
                recommendation=decision)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--rows', required=True, type=Path)
    cli.add_argument('--out', required=True, type=Path)
    cli.add_argument('--manifest', type=Path)
    args = cli.parse_args(argv)
    try:
        if args.rows.is_dir():
            rows = [json.loads(p.read_text()) for p in sorted(args.rows.glob('*.json'))]
        else:
            value = json.loads(args.rows.read_text())
            rows = value['rows'] if isinstance(value, dict) else value
        manifest = json.loads(args.manifest.read_text()) if args.manifest else None
        jobs = manifest.get('jobs') if isinstance(manifest, dict) else manifest
        result = analyze(rows, expected_jobs=jobs)
        args.out.mkdir(parents=True, exist_ok=True)
        for name, value in (('summary.json', result), ('recommendation.json', result['recommendation'])):
            target = args.out / name
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
            temporary.replace(target)
        print(json.dumps(result['recommendation'], indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps(dict(status='error', message=f'Profile analysis could not complete: {error}')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
