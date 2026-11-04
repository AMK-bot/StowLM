"""Prepare development calibration evidence without running or retrying a planner.

The stopped D276 study is immutable. Missing rows stay unknown, and the full
32-map denominator remains fixed. This command never opens a sealed input.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from .common import HERE, HarnessError, atomic_json, read_json, sha256


def summarize(config, rows, stopped):
    maps = [m for m in config['maps'] if m['cohort'] == 'envelope24']
    if len(maps) != 32 or len({m['map_id'] for m in maps}) != 32:
        raise HarnessError('Calibration must retain all32 registered development maps')
    expected = {m['map_id'] for m in maps}
    baselines = {}
    for row in rows:
        if row.get('arm') != 'pipeline' or row.get('cohort') != 'envelope24':
            continue
        key = row['map_id']
        if key not in expected or key in baselines:
            raise HarnessError('Unknown or duplicate development baseline; no outcome-based selection')
        baselines[key] = row
    lost = {r['map_id']: r for r in stopped['lost_in_flight']}
    if set(lost) - expected or set(lost) & set(baselines):
        raise HarnessError('Lost trial identity conflicts with retained evidence')
    binding = checked = 0
    per_type = {name: dict(binding=0, already_obeyed=0, unknown=32)
                for name in ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')}
    for row in baselines.values():
        valid = (row.get('audit_verified') is True and row.get('audit_fresh_process') is True
                 and row.get('audit_pid') != row.get('planning_pid')
                 and row.get('passed') is True and row.get('full_checker_pass') is True
                 and row.get('level') in (0, 'L0'))
        checked += valid
        if not valid:
            continue
        for kind, result in per_type.items():
            recheck = (row.get('calibration_rechecks') or {}).get(kind, {}).get('counterfactual_instruction_recheck')
            if type(recheck) is bool:
                result['unknown'] -= 1
                result['already_obeyed' if recheck else 'binding'] += 1
                if kind == 'max_tiers' and not recheck:
                    binding += 1
    continuation = []
    for item in maps:
        if item['map_id'] in baselines:
            continue
        previous = lost.get(item['map_id'])
        phase = previous.get('phase') if previous else None
        # An audit-only loss need not repeat planning. Any continuation still
        # needs explicit authorization; this file itself dispatches nothing.
        continuation.append(dict(map_id=item['map_id'], path=item['path'], sha256=item['sha256'],
            prior_status='lost' if previous else 'not_started',
            previous_phase=phase, planner_needed=phase != 'audit',
            published_plan_requires_validation=phase == 'audit',
            reason='D276 leader stop; not a failed numerical trial'))
    required = 17
    ceiling = binding + len(continuation)
    planning_n = sum(r['planner_needed'] for r in continuation)
    # Scheduling bound for heterogeneous audit-only and planning+audit slots.
    child_audit_service = planning_n * 1200 + len(continuation) * 300
    bounded_slots = math.ceil(len(continuation)/4) * 1500
    return dict(schema_version='sealed-calibration-preparation/1', split='development', decision='D276',
        required_binding=required, denominator=32, completed_baselines=len(baselines),
        fresh_l0_baselines=checked, binding=binding, missing=len(continuation),
        rule_types=per_type, continuation=continuation, planner_retries=0,
        current_gate_passed=binding >= required, maximum_after_continuation=ceiling,
        feasible_without_repeating_completed_trials=ceiling >= required,
        continuation_time=dict(workers=4, planning_trials=planning_n, audit_trials=len(continuation),
            engine_wall_s=1800, outer_child_deadline_s=1200, audit_deadline_s=300,
            deadline_service_divided_by_workers_s=child_audit_service/4,
            bounded_slot_schedule_s=bounded_slots,
            includes_admission=False, includes_parent_verification=False,
            label='Projected deadline scheduling allowance, not measured runtime; admission unbounded'),
        release_blocked=binding < required, sealed_maps_opened=0,
        reason=('Strict17of32 gate is satisfied' if binding >= required else
                'Strict17of32 gate cannot be waived; no spent trial is retried or silently replaced'))


def prepare(study, out):
    from .driver import public_path
    from .real_dry_run import cap_receipt
    study, out = public_path(study), public_path(out)
    if not out.is_relative_to(HERE):
        raise HarnessError('Calibration preparation output must stay inside sealed/')
    source_paths = [study/'study-config.json', study/'run/rows.json',
                    study/'supplement-rows.json', study/'STUDY-STOPPED.json']
    config, sample, extra, stopped = [read_json(path) for path in source_paths]
    rows = sample + extra
    result = summarize(config, rows, stopped)
    out.mkdir(parents=True, exist_ok=True)
    verified = cap_receipt(out, rows, config)
    result.update(source_files={str(path): sha256(path) for path in source_paths},
                  cap_calibration_receipt=str(out/'cap-calibration.json'), verification=verified,
                  may_run_before_manifest=True,
                  running_now='No planners dispatched; existing audited evidence prepared only')
    atomic_json(out/'preparation.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', type=Path)
    parser.add_argument('--out', type=Path, default=HERE/'measurements/calibration-preflight-D276')
    args = parser.parse_args()
    result = prepare(args.study, args.out)
    print(f"Calibration: {result['binding']}/32; required17; "
          f"continuation ceiling{result['maximum_after_continuation']}/32; "
          f"{'READY' if not result['release_blocked'] else 'BLOCKED'}")


if __name__ == '__main__':
    main()
