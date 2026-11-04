"""D128 reporting: a complete cargo sequence can lack a ballast witness.

Keep finisher time limits separate from planner dead ends and checker expiry.
The 90/120-second finisher remains a clock-budget search, not a proof that no
valid ballast exists. No outcome here makes an unchecked plan shippable.
"""
from collections.abc import Mapping

from llm.exit.outcomes import checker_unavailable


def _mapping(value):
    if isinstance(value, Mapping):
        return value
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return {}


def finisher_timed_out(value):
    """Use structured finisher causes, never infer from elapsed wall time.

    ``not_found_cause=timeout:*`` comes from the deterministic ballast finisher.
    A bare ``status=timeout`` could be decoding/checking and is insufficient.
    """
    row = _mapping(value)
    if row.get('finisher_timeout') is True or row.get('outcome') == 'finisher_time_limit':
        return True
    cause = row.get('not_found_cause', row.get('finisher_not_found_cause'))
    if isinstance(cause, str) and cause.startswith('timeout:'):
        return True
    return any(finisher_timed_out(row[key]) for key in
               ('details', 'verification', 'finisher', 'metrics', 'generation_timings') if key in row)


def _planner_dead_end(value):
    row = _mapping(value)
    if row.get('planner_dead_end') is True or row.get('status') == 'dead_end':
        return True
    return any(_planner_dead_end(row[key]) for key in
               ('generation_timings', 'sample', 'metadata') if key in row)


def finisher_outcome_counts(rows):
    """Separate mutually exclusive row outcomes; unavailable is never physics FAIL.

    Use per-plan verification rows for finisher statistics, and raw generation
    rows for planner-dead-end statistics. Combining them would count one sample
    twice. A complete cargo sequence whose finisher runs out is a finisher
    timeout, even if older coarse metadata also called the sample a dead end.
    """
    counts = dict(total=0, passed=0, finisher_timeouts=0, planner_dead_ends=0,
                  checker_unavailable=0, other_not_passed=0)
    for raw in rows:
        row = _mapping(raw)
        counts['total'] += 1
        if finisher_timed_out(row):
            key = 'finisher_timeouts'
        elif checker_unavailable(row):
            key = 'checker_unavailable'
        elif row.get('passed') is True:
            key = 'passed'
        elif _planner_dead_end(row):
            key = 'planner_dead_ends'
        else:
            key = 'other_not_passed'
        counts[key] += 1
    return counts


def stowlm_row_labels(*, checker_passed, backtracking=False, polished=False,
                      polish_budget_s=15., mask_override_count=None, mask_decision_count=None):
    """Honest attribution for every model row; mask rates require actual counts.

    ``checker_passed`` must be the measured full-check result, never decoder
    grammar completion. Keep the LNS hybrid in its own row. No claim of a solver
    free plan is made: ballast is always attributed to deterministic finishing.
    """
    if not isinstance(checker_passed, bool):
        raise ValueError('checker_passed must be the measured boolean verdict')
    if (mask_override_count is None) != (mask_decision_count is None):
        raise ValueError('mask override reporting needs both measured counts')
    rate = None
    if mask_override_count is not None:
        if (not isinstance(mask_override_count, int) or isinstance(mask_override_count, bool)
                or not isinstance(mask_decision_count, int) or isinstance(mask_decision_count, bool)
                or not 0 <= mask_override_count <= mask_decision_count):
            raise ValueError('invalid measured mask override counts')
        rate = mask_override_count/mask_decision_count if mask_decision_count else None
    label = 'cargo sequence decoded by StowLM under the feasibility mask'
    if backtracking:
        label += ' (+ bounded rewinds)'
    label += '; ballast by the deterministic V4 finisher'
    label += '; full checker passed' if checker_passed else '; full checker pass not obtained'
    return dict(label=label, row_kind='model_plus_polish' if polished else 'model_alone',
                hybrid_label=f'model + {polish_budget_s:g} s LNS polish' if polished else None,
                cargo_sequence_source='StowLM under feasibility mask',
                ballast_source='deterministic V4 finisher',
                full_checker_passed=checker_passed,
                mask_override_count=mask_override_count,
                mask_decision_count=mask_decision_count, mask_override_rate=rate)
