"""Fresh-process native-check and independent physical-graph audit of saved rows.

Default scope is exactly 32 frozen cohort maps in each of engine12/port_lp.
--allow-partial audits only completed output rows already present, for explicit
development checks; it never reads checkpoints or waives source mismatches.
Only standard-library imports execute before the binding preflight succeeds.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ARMS = ('engine12', 'port_lp')
FLOAT_TOLERANCE = dict(relative=1e-9, absolute=1e-9)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    return sha_bytes(Path(path).read_bytes())


def equal_evidence(expected, actual, *, path='root'):
    """Deep equality with explicit tight numeric tolerance; no ignored fields."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        if type(expected) is not type(actual) or expected != actual:
            raise AssertionError(f'{path}: {expected!r} != {actual!r}')
        return
    if isinstance(expected, (float, int)) and isinstance(actual, (float, int)):
        if not math.isfinite(expected) or not math.isfinite(actual):
            raise AssertionError(f'{path}: nonfinite numeric evidence')
        if not math.isclose(expected, actual, rel_tol=FLOAT_TOLERANCE['relative'], abs_tol=FLOAT_TOLERANCE['absolute']):
            raise AssertionError(f'{path}: {expected!r} != {actual!r}')
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            raise AssertionError(f'{path}: keys differ: {sorted(set(expected)^set(actual))}')
        for key in sorted(expected):
            equal_evidence(expected[key], actual[key], path=f'{path}.{key}')
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            raise AssertionError(f'{path}: lengths {len(expected)} != {len(actual)}')
        for i, (left, right) in enumerate(zip(expected, actual)):
            equal_evidence(left, right, path=f'{path}[{i}]')
        return
    if type(expected) is not type(actual) or expected != actual:
        raise AssertionError(f'{path}: {expected!r} != {actual!r}')


def hard_evidence(report):
    """All hard occurrences, retaining their complete raw measured/limit data."""
    records = [dict(v) for v in report.get('violations', ()) if v.get('severity') == 'hard']
    return sorted(records, key=lambda v: json.dumps(v, sort_keys=True, separators=(',', ':')))


def part_counts(report):
    count = report['count']
    if count is None:
        raise AssertionError('native report has no move count')
    future = count.get('future', [])
    return dict(relocations=count['relocations'], hatch_now=count['hatch_cover_lifts'],
                hatch_later=sum(f['hatch_cover_lifts'] for f in future),
                restow_now=count['restow_lifts'], restow_later=sum(f['restow_lifts'] for f in future))


def footprint(slot):
    """Validated native slot syntax, independently expanded without a Model."""
    if not isinstance(slot, str) or len(slot) != 7 or not slot.isdecimal():
        raise ValueError(f'invalid reported slot: {slot!r}')
    bay, row, tier = int(slot[:3]), int(slot[3:5]), int(slot[5:])
    if bay % 2:
        return (slot,)
    return (f'{bay-1:03d}{row:02d}{tier:02d}', f'{bay+1:03d}{row:02d}{tier:02d}')


def physical_projection(instance, placements):
    """Independent D15 top/hatch least closure from plain instance/ship JSON.

    No page, search model, solver variable or cached method count is consumed.
    Surviving long boxes bridge both physical columns; each departed bridge is
    removed before the next call. Every future call, including zero work, is
    returned. Modern crane-crossing closure is deliberately outside this graph.
    """
    vessel = instance['vessel']
    cells = {c['id']: c for c in vessel['cells']+vessel.get('fixed_load_cells', [])}
    stacks = {s['id']: s for s in vessel['stacks']+vessel.get('fixed_load_stacks', [])}
    covers = {h['id']: h for h in vessel['hatch_covers']}
    boxes = {b['id']: b for b in instance['containers']}
    start, calls = instance['call_index'], instance['rotation']['calls']
    next_call = {}
    for p in range(start+1, len(calls)):
        next_call.setdefault(calls[p]['locode'], p)
    slots = {p['container_id']: p['slot'] for p in placements}
    if len(slots) != len(placements):
        raise AssertionError('duplicate box in physical final ship')
    occupied = {}
    box_stacks = {}
    for box, slot in slots.items():
        support = []
        for cell in footprint(slot):
            if cell not in cells:
                raise AssertionError(f'missing physical cell {cell}')
            if cell in occupied:
                raise AssertionError(f'overlapping final physical cell {cell}')
            occupied[cell] = box
            support.append(cells[cell]['stack_id'])
        box_stacks[box] = tuple(support)
    destinations = {b: next_call.get(boxes[b]['pod']) for b in slots}
    active = set(slots)
    rows, used = [], set()
    lifts_per_box = instance['objective']['future_restow_lifts_per_box']
    for p in range(start+1, len(calls)):
        target = {b for b in active if destinations[b] == p}
        by_stack = defaultdict(list)
        for b in active:
            for stack in box_stacks[b]:
                by_stack[stack].append((int(slots[b][-2:]), b))
        upper = defaultdict(set)
        for members in by_stack.values():
            ordered = [b for _, b in sorted(members)]
            for low, high in zip(ordered, ordered[1:]):
                upper[low].add(high)
        opened = {stacks[j]['hatch_cover_id'] for b in target for j in box_stacks[b]
                  if not stacks[j]['on_deck']}
        used.update(opened)
        required = set(target)
        for cover in opened:
            for stack in covers[cover]['deck_stack_ids']:
                required.update(b for _, b in by_stack.get(stack, ()))
        pending = list(required)
        while pending:
            for b in upper[pending.pop()]:
                if b not in required:
                    required.add(b)
                    pending.append(b)
        blockers = sorted(required-target)
        rows.append(dict(call_index=p, targets=sorted(target), blockers=blockers,
                         covers=sorted(opened), restowed_boxes=len(blockers),
                         restow_lifts=len(blockers)*lifts_per_box,
                         hatch_cover_lifts=2*len(opened)))
        active -= target
    return dict(future=rows, current_hatch_lower_bound=2*len(used),
                restow_later=sum(r['restow_lifts'] for r in rows),
                hatch_later=sum(r['hatch_cover_lifts'] for r in rows),
                scope='physical final-stow top/hatch closure; modern crane crossings relaxed')


def validate_row_binding(row, entry, arm, bindings, driver):
    expected = dict(bindings, input=entry['path'], size=entry['size'], ports=entry['ports'],
                    vessel=entry['vessel'], fill_pct=entry['fill_pct'], development=entry['development'])
    if row.get('complete') is not True:
        raise AssertionError('row is not a completed atomic output row')
    for key, value in dict(instance_id=entry['instance_id'], input=entry['path'],
                           input_sha256=entry['sha256'], arm=arm, cold=True).items():
        equal_evidence(value, row.get(key), path=f'row.{key}')
    equal_evidence(expected, row['config'], path='row.config')
    equal_evidence(driver.run_key(expected, entry['sha256']), row['run_key'], path='row.run_key')


def validate_selected(row, instance, plan, replay, report, analyze, table, modern):
    """Runtime validation, separate from orchestration for small regressions."""
    saved = row.get('report')
    if saved is None:
        raise AssertionError('selected plan lacks its saved full report')
    fresh = report.model_dump(mode='json')
    if not replay.legal:
        raise AssertionError('fresh physical move replay is illegal')
    final = replay.final_state.ship
    placements = final.model_dump(mode='json')['placements']
    final_assignment = {p['container_id']: p['slot'] for p in placements}
    expected = set(instance.load_list)
    if len(final_assignment) != len(placements) or not expected <= set(final_assignment):
        raise AssertionError('not every expected load-list box is physically aboard exactly once')
    if fresh.get('shut_out'):
        raise AssertionError('completed plan has shut-out cargo')
    stored_assignment = {b: s for b, s in row['assignment'].items() if b in expected}
    equal_evidence(stored_assignment, {b: final_assignment[b] for b in expected}, path='final_load_assignment')
    equal_evidence(bool(report.passed), row['passed'], path='native_passed')
    equal_evidence(hard_evidence(saved), hard_evidence(fresh), path='all_hard_violation_evidence')
    equal_evidence(saved['count'], fresh['count'], path='native_move_count')
    equal_evidence(replay.count.model_dump(mode='json'), fresh['count'], path='independent_replay_move_count')
    equal_evidence(sorted(saved.get('shut_out', [])), sorted(fresh.get('shut_out', [])), path='native_shut_out')
    analysis = analyze(report, table, instance=instance)
    if not analysis['eligible'] or analysis['ashore_count']:
        raise AssertionError('fresh report is not tier-eligible completed cargo')
    equal_evidence(row['completed']['level'], analysis['level'], path='completed.level')
    equal_evidence(row['completed']['analysis'], analysis, path='completed.full_analysis')
    columns = ('T5', 'T4', 'T3', 'T2', 'T1', 'T0 only')
    equal_evidence(row['completed']['column'], columns[analysis['level']], path='completed.column')
    parts = part_counts(fresh)
    equal_evidence(row['parts'], parts, path='raw_lift_parts')
    equal_evidence(row['lifts'], float(report.count.weighted_nonproductive), path='real_total_lifts')
    w = instance.objective
    weighted = (w.relocate*parts['relocations']+w.hatch_cover*parts['hatch_now']
                +w.future_hatch_cover*parts['hatch_later']+w.restow*parts['restow_now']
                +w.future_restow*parts['restow_later'])
    equal_evidence(weighted, row['lifts'], path='weighted_sum_of_all_parts')
    graph = physical_projection(instance.model_dump(mode='json'), placements)
    native_future = {f['call_index']: f for f in fresh['count']['future']}
    for p in graph['future']:
        actual = native_future.get(p['call_index'])
        if actual is None:
            raise AssertionError(f'missing native future call {p["call_index"]}')
        for field in ('restowed_boxes', 'restow_lifts', 'hatch_cover_lifts'):
            if modern:
                if p[field] > actual[field]+1e-9:
                    raise AssertionError(f'modern top/hatch lower bound exceeds native {field}')
            else:
                equal_evidence(p[field], actual[field], path=f'future[{p["call_index"]}].{field}')
    equal_evidence(sorted(native_future), [p['call_index'] for p in graph['future']], path='future_call_domain')
    if not instance.onboard.placements and not instance.onboard.covers_off:
        if graph['current_hatch_lower_bound'] > parts['hatch_now']:
            raise AssertionError('assignment-induced current hatch bound exceeds real current hatch lifts')
        graph['current_hatch_bound_verified'] = True
    else:
        graph['current_hatch_bound_verified'] = False
    graph['exact_for_active_projection'] = not modern
    graph['rule_freeze_v1_enabled'] = modern
    return dict(all_expected_aboard=True, expected_boxes=len(expected), final_boxes=len(placements),
                full_pass=bool(report.passed), completed_level=analysis['level'],
                completed_column=columns[analysis['level']], lifts=row['lifts'], parts=parts,
                hard_evidence=hard_evidence(fresh), analysis=analysis,
                native_report_sha256=sha_bytes(json.dumps(fresh, sort_keys=True).encode()), projection=graph)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='results/main', help='saved output directory inside this experiment')
    parser.add_argument('--allow-partial', action='store_true', help='audit available final rows only; never checkpoints')
    parser.add_argument('--audit-json', default='AUDIT.json')
    parser.add_argument('--audit-log', default='AUDIT.log')
    args = parser.parse_args()
    output = (HERE/args.out).resolve()
    output.relative_to(HERE)
    if 'checkpoints' in output.parts:
        parser.error('checkpoint audit is outside this final-row auditor')
    output.mkdir(parents=True, exist_ok=True)
    audit_path, log_path = output/args.audit_json, output/args.audit_log
    audit_path.resolve().relative_to(HERE)
    log_path.resolve().relative_to(HERE)
    log_path.write_text('')
    def log(value):
        line = json.dumps(value, sort_keys=True, default=str)
        print(line, flush=True)
        with log_path.open('a') as stream:
            stream.write(line+'\n')
    started = perf_counter()
    result = dict(created_utc=datetime.now(timezone.utc).isoformat(), auditor_pid=os.getpid(),
                  auditor_sha256=sha_file(__file__), output=str(output.relative_to(HERE)),
                  allow_partial=args.allow_partial, numerical_threads=1,
                  evidence_numeric_tolerance=FLOAT_TOLERANCE, rows=[], errors=[])
    try:
        manifest_path = HERE/'cohort-manifest.json'
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        entries = manifest['maps']
        if len(entries) != 32 or len({e['instance_id'] for e in entries}) != 32:
            raise AssertionError('expected exactly32 unique fixed cohort maps')
        result['cohort_manifest_sha256'] = sha_bytes(manifest_bytes)
        spec = importlib.util.spec_from_file_location('_c6_port_audit_driver', HERE/'run.py')
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)  # stdlib only; native imports remain deferred.
        initial_bindings = {arm: driver.bindings(arm) for arm in ARMS}
        frozen_path = HERE/'FREEZE.json'
        frozen_bytes = frozen_path.read_bytes() if frozen_path.exists() else None
        if frozen_bytes is None and not args.allow_partial:
            raise AssertionError('strict final audit requires FREEZE.json')
        frozen = json.loads(frozen_bytes) if frozen_bytes is not None else None
        if frozen:
            equal_evidence(frozen['bindings'], initial_bindings, path='FREEZE.bindings')
        result.update(freeze_sha256=sha_bytes(frozen_bytes) if frozen_bytes else None,
                      bindings=initial_bindings, frozen_source_verified=frozen is not None)
        rows = []
        expected_names = {e['instance_id']+'.json' for e in entries}
        missing = []
        for arm in ARMS:
            actual_names = {p.name for p in (output/arm).glob('*.json')}
            unknown = actual_names-expected_names-{'summary.json'}
            if unknown:
                raise AssertionError(f'{arm}: unexpected output files {sorted(unknown)}')
            for entry in entries:
                path = output/arm/(entry['instance_id']+'.json')
                if not path.exists():
                    missing.append(dict(arm=arm, instance_id=entry['instance_id']))
                    continue
                payload = path.read_bytes()
                row = json.loads(payload)
                validate_row_binding(row, entry, arm, initial_bindings[arm], driver)
                instance_path = (driver.ROOT/entry['path']).resolve()
                instance_path.relative_to(HERE/'cohort')
                if sha_file(instance_path) != entry['sha256']:
                    raise AssertionError(f'{entry["instance_id"]}: changed instance input')
                rows.append((arm, entry, path, sha_bytes(payload), row, instance_path))
        result.update(expected_rows=64, available_rows=len(rows), missing_rows=missing)
        if not rows:
            raise AssertionError('no completed final output rows available to audit')
        if missing and not args.allow_partial:
            raise AssertionError(f'{len(missing)} final rows missing; strict audit requires32 per arm')
        log(dict(stage='stdlib_preflight_passed', rows=len(rows), missing=len(missing)))

        # Runtime begins only after every available row and input has passed
        # its source/config/hash bindings. This process never imports a solver.
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import frozen_env  # noqa: F401
        import stowage.checker as checker_module
        from stowage.io import load_instance
        from stowage.model import MovePlan
        from stowage.moves import apply_plan
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        from frozen_reporting.tiers import analyze_report, load_table
        native_check = checker_module.check
        checker_file = Path(inspect.getsourcefile(inspect.unwrap(native_check))).resolve()
        checker_file.relative_to(driver.ROOT/'src/stowage')
        if not native_check.__module__.startswith('stowage.checker'):
            raise AssertionError('public checker is not the unchanged native checker')
        result['native_checker'] = dict(module=native_check.__module__, name=native_check.__qualname__,
                                        source=str(checker_file.relative_to(driver.ROOT)),
                                        source_sha256=sha_file(checker_file))
        table = load_table()
        modern = bool(rule_freeze_v1_enabled())
        result['rule_freeze_v1_enabled'] = modern
        result['exact_for_active_projection'] = not modern
        if modern and not args.allow_partial:
            raise AssertionError('strict cohort audit expects bound legacy activation=false; modern graph is only a lower bound')
        for arm, entry, path, row_hash, row, instance_path in rows:
            evidence = dict(arm=arm, instance_id=entry['instance_id'], row_sha256=row_hash,
                            input_sha256=entry['sha256'], selected=bool(row.get('completed')))
            try:
                if not row.get('completed'):
                    if row.get('passed') or any(row.get(k) is not None for k in ('plan', 'report', 'assignment', 'lifts', 'parts')):
                        raise AssertionError('not-completed row contains inconsistent selected-result fields')
                    evidence.update(ok=True, status='not_completed_no_selected_plan_to_check')
                else:
                    instance = load_instance(instance_path)
                    if instance.id != entry['instance_id']:
                        raise AssertionError('loaded input identity mismatch')
                    plan = MovePlan.model_validate(row['plan'])
                    replay = apply_plan(instance, plan)
                    report = native_check(instance, plan)
                    evidence.update(validate_selected(row, instance, plan, replay, report,
                                                      analyze_report, table, modern))
                    evidence.update(ok=True, status='fresh_native_check_and_physical_projection_verified')
                if sha_file(path) != row_hash:
                    raise AssertionError('saved row changed while auditing')
            except Exception as error:
                evidence.update(ok=False, error=f'{type(error).__name__}: {error}')
                result['errors'].append(dict(arm=arm, instance_id=entry['instance_id'], error=evidence['error']))
            result['rows'].append(evidence)
            log(dict(arm=arm, instance_id=entry['instance_id'], ok=evidence['ok'],
                     level=evidence.get('completed_level'), lifts=evidence.get('lifts'), error=evidence.get('error')))
        if checker_module.check is not native_check:
            raise AssertionError('native checker binding changed during audit')
        equal_evidence(initial_bindings, {arm: driver.bindings(arm) for arm in ARMS}, path='post_audit_bindings')
        if manifest_path.read_bytes() != manifest_bytes:
            raise AssertionError('cohort manifest changed during audit')
        if frozen_bytes is not None and frozen_path.read_bytes() != frozen_bytes:
            raise AssertionError('FREEZE.json changed during audit')
        result['native_checker_identity_unchanged'] = True
        result['post_audit_source_bindings_unchanged'] = True
    except Exception as error:
        result['errors'].append(dict(stage='audit', error=f'{type(error).__name__}: {error}'))
        log(result['errors'][-1])
    levels = {arm: Counter(r.get('completed_column') for r in result['rows']
                           if r['arm'] == arm and r.get('ok') and r.get('selected')) for arm in ARMS}
    result['summary'] = {arm: dict(rows=sum(r['arm']==arm for r in result['rows']),
                                  checked_completed=sum(r['arm']==arm and r.get('ok') and r.get('selected') for r in result['rows']),
                                  full_passes=sum(r['arm']==arm and r.get('ok') and r.get('full_pass', False) for r in result['rows']),
                                  completion_counts=dict(levels[arm])) for arm in ARMS}
    result['ok'] = not result['errors']
    result['wall_s'] = perf_counter()-started
    audit_path.write_text(json.dumps(result, indent=2, default=str)+'\n')
    log(dict(stage='finished', ok=result['ok'], audit=str(audit_path), wall_s=result['wall_s'], summary=result['summary']))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
