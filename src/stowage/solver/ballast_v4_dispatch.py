"""Default-off MVP finisher dispatch through the existing owned worker.

``STOWAGE_BALLAST_V4=1`` selects this path only with rule freeze OFF. The
worker's exact/full-checker acceptance is unchanged; the process deadline
prevents an opaque numerical call from overrunning the caller's budget.
Only the existing deadline helper's recorded worker can be stopped.
"""
from __future__ import annotations

import math
import time

from stowage.solver.ballast_deadline import BallastDeadline, ExactDeadline, run_with_deadline


def _worker(instance, plan, *, time_limit_s, seed, deadline):
    from stowage.solver.ballast_v4 import finish
    return finish(instance, plan, time_limit_s=time_limit_s, seed=seed,
                  _deadline=deadline)


def finish(instance, plan, *, time_limit_s=30., seed=0):
    """Preserve replay validity and report unknown progress on a hard timeout."""
    from stowage.solver.ballast import _provenance, _validated_replay
    from stowage.solver.ballast_v4 import BallastV4Outcome

    started = time.monotonic()
    provenance = _provenance(instance, plan, seed)
    _validated_replay(instance, plan)

    def failure(cause):
        return BallastV4Outcome(status='not_found', provenance=provenance,
            wall_time_s=time.monotonic()-started, not_found_cause=cause,
            diagnostics={'termination': cause, 'worker_progress_available': False,
                         'candidate_count': None, 'full_checker_passed': None})

    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        return failure('timeout:no_budget')
    deadline = started+time_limit_s
    if time.monotonic() >= deadline:
        return failure('timeout:replay')
    try:
        result = run_with_deadline(_worker, (instance, plan),
            {'time_limit_s': time_limit_s, 'seed': seed,
             'deadline': deadline-min(.05, time_limit_s*.05)}, deadline=deadline)
    except (BallastDeadline, ExactDeadline):
        return failure('timeout:worker')
    merged = provenance.merge(result.provenance)
    updates = {'provenance': merged, 'wall_time_s': time.monotonic()-started}
    if result.plan is not None:
        updates['plan'] = result.plan.model_copy(update={
            'provenance': merged.merge(result.plan.provenance)})
    return result.model_copy(update=updates)
