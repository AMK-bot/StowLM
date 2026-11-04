"""Independent ext-1 recount from recorded raw evidence, without run imports.

The 3.1 definition in code11.md determines binding eligibility. Intended-rule
checks are the saved fresh audit's rechecks; this is not a second execution of
checker/planner code, and opens no map payload. The published ``passed`` flag
is compared only after our predicate has independently computed its verdict.
"""
from collections import Counter
from statistics import mean
from scipy.stats import beta

ARMS = ('pipeline', 'twostep', 'instruction')
RULE_TYPES = ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')


def cp(k, n):
    """Exact equal-tail 95% binomial interval; undefined for empty denominator."""
    if not isinstance(k, int) or not isinstance(n, int) or not 0 <= k <= n:
        raise ValueError('Require integers 0 <= successes <= trials')
    if not n:
        return None
    return [0.0 if k == 0 else float(beta.ppf(.025, k, n-k+1)),
            1.0 if k == n else float(beta.ppf(.975, k+1, n-k))]


def _dict(value):
    return value if isinstance(value, dict) else {}


def pass_failures(row):
    """Return failed requirements of the complete, fresh-audited L0 predicate."""
    r = row
    c, e, cl = (_dict(r.get(key)) for key in ('checker', 'evidence', 'classification'))
    failures = []
    def require(ok, label):
        if not ok:
            failures.append(label)
    require(r.get('status') in ('checked_plan', 'deadline_exceeded'), 'terminal_checked_status')
    require(type(r.get('level')) is int and r['level'] == 0, 'L0')
    require(r.get('relaxed_level') is None, 'unrelaxed')
    require(r.get('full_checker_pass') is True and c.get('passed') is True, 'full_checker_pass')
    for key in ('all_loaded', 'complete', 'replay_legal', 'audit_verified', 'audit_fresh_process'):
        require(r.get(key) is True, key)
    require(_dict(r.get('envelope')).get('in_envelope') is True, 'in_envelope')
    for key in ('audit_timed_out', 'audit_cleanup_failed', 'child_cleanup_failed',
                'fixture_surrogate', 'audit_watchdog_fired'):
        require(not r.get(key), 'no_' + key)
    require(r.get('audit_returncode') == 0, 'audit_returncode_zero')
    require(type(r.get('audit_pid')) is int and r['audit_pid'] > 0
            and type(r.get('planning_pid')) is int and r['planning_pid'] > 0
            and r['audit_pid'] != r['planning_pid'], 'independent_audit_pid')
    require(not any(r.get(key) for key in ('audit_cleanup_errors', 'audit_watchdog_errors')), 'clean_audit_watchdog')
    require(not c.get('violations') and not e.get('broken_rules') and
            not any(cl.get(key) for key in ('broken_rules', 'breaches', 'all_violations')), 'no_broken_rules')
    parts = c.get('parts')
    require(isinstance(parts, dict) and bool(parts), 'checker_parts_present')
    require(isinstance(parts, dict) and all(isinstance(p, dict) and
            not p.get('error') and not p.get('violations') for p in parts.values()), 'checker_parts_clean')
    if r.get('arm') == 'instruction':
        require(r.get('instruction_recheck') is True and not r.get('instruction_violations'), 'intended_instruction_obeyed')
    if r.get('status') == 'deadline_exceeded':
        require(r.get('deadline_with_incumbent') is True and
                r.get('deadline_incumbent_verified') is True, 'fresh_verified_predeadline_incumbent')
    return failures


def passed(row):
    return not pass_failures(row)


def _ratio(k, n):
    return {'passes': k, 'trials': n, 'exact_95': cp(k, n)}


def _groups(rows):
    result = _ratio(sum(passed(r) for r in rows), len(rows))
    result.update({
        'statuses': dict(sorted(Counter(r.get('status', 'unavailable') for r in rows).items())),
        'levels': dict(sorted(Counter('L' + str(r['level']) for r in rows if r.get('level') is not None).items())),
        'relaxed_levels': dict(sorted(Counter('L' + str(r['relaxed_level']) for r in rows if r.get('relaxed_level') is not None).items())),
        'reader_failures': sum(r.get('reader_failure') is True for r in rows),
        'engine_failures': sum(r.get('engine_failure') is True for r in rows),
        'out_of_envelope': sum(r.get('status') == 'out_of_envelope' or
                               _dict(r.get('envelope')).get('in_envelope') is False for r in rows),
        'audit_errors': sum(r.get('audit_timed_out') is True or r.get('audit_cleanup_failed') is True
                            or r.get('audit_returncode') not in (None, 0)
                            or (r.get('status') != 'not_run' and 'audit' in str(r.get('error', '')).lower()) for r in rows),
        'deadline_exceeded': sum(r.get('status') == 'deadline_exceeded' for r in rows),
        'not_run': sum(r.get('status') == 'not_run' for r in rows),
        'missing_synthesized': sum(r.get('_missing_synthesized') is True for r in rows),
    })
    return result


def changed_cells(baseline, instructed):
    """Count boxes whose physical occupancy differs; unavailable != zero."""
    b, i = baseline.get('final_cells'), instructed.get('final_cells')
    if not isinstance(b, dict) or not isinstance(i, dict):
        return None
    if not all(isinstance(v, list) for v in list(b.values()) + list(i.values())):
        return None
    return sum(sorted(b.get(box, [])) != sorted(i.get(box, [])) for box in set(b) | set(i))


def _instruction_group(map_ids, lookup, allocations):
    rs = [lookup[(mid, 'instruction')] for mid in map_ids]
    binding, already, unavailable, changed, by_map = [], [], [], [], []
    for mid, instructed in zip(map_ids, rs):
        baseline = lookup[(mid, 'pipeline')]
        recheck = baseline.get('counterfactual_instruction_recheck')
        if passed(baseline) and recheck is False:
            binding.append(instructed)
            category = 'binding'
        elif passed(baseline) and recheck is True:
            already.append(instructed)
            category = 'already_obeyed'
        else:
            unavailable.append(instructed)
            category = 'counterfactual_unavailable'
        count = changed_cells(baseline, instructed)
        if count is not None:
            changed.append(count)
        by_map.append({'map_id': mid, 'counterfactual': category,
                       'instructed_pass': passed(instructed), 'changed_cells': count})
    rechecks = Counter('passed' if r.get('instruction_recheck') is True else
                       'failed' if r.get('instruction_recheck') is False else 'unavailable' for r in rs)
    result = _groups(rs)
    result.update({'binding': _ratio(sum(passed(r) for r in binding), len(binding)),
                   'already_obeyed': len(already),
                   'already_obeyed_passes': sum(passed(r) for r in already),
                   'counterfactual_unavailable': len(unavailable),
                   'intended_rechecks': {key: rechecks[key] for key in ('passed', 'failed', 'unavailable')},
                   'changed_cells': {'n': len(changed), 'sum': sum(changed),
                                     'mean': mean(changed) if changed else None},
                   'per_map': by_map})
    return result


def recount(rows, maps, allocation):
    """JSON-compatible fixed-population recount; never mutates supplied objects.

    ``maps`` is config['maps'] or maps-manifest; ``allocation`` is the raw
    instruction-allocation list (instruction-draw also works). Missing trials
    become explicit not_run failures, unexpected/duplicate keys are rejected.
    The caller determines finality from controller/marker, not row counts.
    """
    maps = maps['maps'] if isinstance(maps, dict) else maps
    allocations = {a['map_id']: a for a in allocation}
    if len(allocations) != len(allocation):
        raise ValueError('Duplicate instruction allocation map')
    inventory = {m['map_id']: m for m in maps}
    if len(inventory) != len(maps):
        raise ValueError('Duplicate registered map')
    if len(maps) != 24:
        raise ValueError('ext-1 must retain exactly 24 registered maps')
    if set(allocations) != set(inventory):
        raise ValueError('Allocation must cover exactly the registered maps')
    lookup = {}
    issues = []
    for r in rows:
        key = (r['map_id'], r['arm'])
        if key in lookup:
            raise ValueError('Duplicate trial: ' + repr(key))
        if key[0] not in inventory or key[1] not in ARMS:
            raise ValueError('Unregistered trial: ' + repr(key))
        lookup[key] = dict(r)
    for mid, m in inventory.items():
        for arm in ARMS:
            key = (mid, arm)
            if key not in lookup:
                lookup[key] = {'map_id': mid, 'ship_id': m['ship_id'], 'arm': arm,
                               'status': 'not_run', '_missing_synthesized': True}
            r = lookup[key]
            if r.get('ship_id') != m['ship_id']:
                issues.append({'map_id': mid, 'arm': arm, 'issue': 'ship_id mismatch'})
            if r.get('map_sha256') not in (None, m.get('sha256')):
                issues.append({'map_id': mid, 'arm': arm, 'issue': 'map_sha256 mismatch'})
            if arm == 'instruction':
                kind = allocations[mid].get('instruction_type')
                if kind not in RULE_TYPES:
                    raise ValueError('Unknown registered instruction type: ' + str(kind))
                if r.get('instruction_type') not in (None, kind):
                    issues.append({'map_id': mid, 'arm': arm, 'issue': 'instruction_type mismatch'})
                r['instruction_type'] = kind
            if 'passed' in r and r['passed'] is not passed(r):
                issues.append({'map_id': mid, 'arm': arm, 'issue': 'stored passed differs',
                               'stored': r['passed'], 'recount': passed(r),
                               'failed_requirements': pass_failures(r)})
    all_rows = list(lookup.values())
    arms = {}
    for arm in ARMS:
        selected = [r for r in all_rows if r['arm'] == arm]
        arms[arm] = {'all': _groups(selected), 'by_ship': {ship: _groups([
            lookup[(mid, arm)] for mid, m in inventory.items() if m['ship_id'] == ship])
            for ship in sorted({m['ship_id'] for m in maps})}}
    instruction = {'all': _instruction_group(list(inventory), lookup, allocations),
                   'by_type': {kind: _instruction_group([
                       mid for mid in inventory if allocations[mid]['instruction_type'] == kind],
                       lookup, allocations) for kind in RULE_TYPES}}
    return {'population': {'maps': len(inventory), 'trials': len(all_rows),
                            'supplied_rows': len(rows),
                            'terminal_rows': sum(r.get('status') != 'not_run' for r in all_rows),
                            'not_run': sum(r.get('status') == 'not_run' for r in all_rows)},
            'arms': arms, 'instruction': instruction, 'integrity_issues': issues,
            'per_trial': [{'map_id': mid, 'arm': arm, 'status': r.get('status'),
                           'passed': passed(r), 'failed_requirements': pass_failures(r)}
                          for (mid, arm), r in sorted(lookup.items())]}
