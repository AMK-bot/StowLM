"""Checker availability is separate from a measured physical verdict (D124).

This module intentionally has no numerical/MLX imports. A watchdog's latched
expiry wins over every partial result, including a completed violation or a
contradictory ``passed=True`` flag in an interrupted worker's output.
"""
from collections.abc import Mapping


_PARTS = frozenset(('moves', 'rules', 'voyage', 'stability', 'strength',
                    'visibility', 'lashing', 'constraints'))


def _mapping(value):
    if isinstance(value, Mapping):
        return value
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return {}


def checker_unavailable(value):
    """Recognize explicit expiry and unavailable checker reports at any wrapper.

    Never infer a timeout from free text or from ``passed=False``. Walk only
    verification/report wrappers, so an unrelated candidate's failed check does
    not contaminate an already-verified winner.
    """
    row = _mapping(value)
    if not row:
        return False
    if (row.get('checker_available') is False or row.get('checker_unavailable') is True
            or row.get('unavailable') is True
            or row.get('status') in ('unavailable', 'checker_unavailable', 'watchdog_expired')
            or row.get('availability') == 'unavailable'):
        return True
    for key in ('watchdog', 'checker_watchdog'):
        watchdog = _mapping(row.get(key))
        if watchdog.get('expired') is True or watchdog.get('expiry_latched') is True:
            return True
    for part in _mapping(row.get('parts')).values():
        part = _mapping(part)
        if part.get('ran') is False or part.get('error'):
            return True
    violations = list(row.get('violations') or ())
    for part in _mapping(row.get('parts')).values():
        violations.extend(_mapping(part).get('violations') or ())
    if any(str(_mapping(v).get('rule_id', '')).startswith('checker.') for v in violations):
        return True
    return any(checker_unavailable(row[key]) for key in
               ('details', 'verification', 'report', 'checker_result') if key in row)


def completed_physics_failure(value):
    """True only for an available full report containing a hard plan violation."""
    row = _mapping(value)
    if checker_unavailable(row):
        return False
    for key in ('details', 'verification', 'report', 'checker_result'):
        if key in row and completed_physics_failure(row[key]):
            return True
    parts = _mapping(row.get('parts'))
    if row.get('passed') is not False or not _PARTS.issubset(parts):
        return False
    if any(_mapping(part).get('ran', True) is not True for part in parts.values()):
        return False
    violations = list(row.get('violations') or ())
    for part in parts.values():
        violations.extend(_mapping(part).get('violations') or ())
    return any(_mapping(v).get('severity') == 'hard' for v in violations)


def outcome_counts(rows):
    """Count mutually exclusive labels; uncertainty is never a physics failure.

    ``other_not_passed`` includes incomplete generation and mask/provenance
    refusals. These block training/shipping but do not assert physical failure.
    ``fallback_unavailable`` is explicit for the deployment fallback report.
    """
    counts = dict(total=0, passed=0, physics_failed=0, unavailable=0, other_not_passed=0)
    for raw in rows:
        row = _mapping(raw)
        counts['total'] += 1
        if checker_unavailable(row):
            key = 'unavailable'
        elif row.get('passed', row.get('own_plan_passed')) is True:
            key = 'passed'
        elif completed_physics_failure(row):
            key = 'physics_failed'
        else:
            key = 'other_not_passed'
        counts[key] += 1
    counts['fallback_unavailable'] = counts['unavailable']
    return counts


def selection_checker_rows(selection):
    """Actual full-check attempts, including greedy checks lazy selection skipped.

    A greedy result is cached and reused by selection. Count its candidate once,
    rather than counting the same checker invocation again for fitness.
    Pre-check ballast/mask/provenance refusals are not full-check attempts.
    """
    def attempted(row):
        row = _mapping(row)
        return bool(row.get('report') or 'checker_available' in row
                    or row.get('checker_watchdog'))
    checks, seen = [], set()
    for row in selection.get('verifications', ()):
        if row.get('stage') == 'verify' and attempted(row):
            checks.append(row)
            seen.add(row.get('candidate_id'))
    candidates = selection.get('candidates', ())
    for adapter, row in selection.get('greedy_fitness', {}).items():
        candidate = next((c for c in candidates
                          if c.get('source') == 'model'
                          and c.get('metadata', {}).get('adapter') == adapter
                          and c.get('metadata', {}).get('sample') == 0), None)
        cid = candidate.get('candidate_id') if candidate else f'fitness:{adapter}'
        if cid not in seen and attempted(row.get('verification')):
            checks.append(row)
            seen.add(cid)
    return checks
