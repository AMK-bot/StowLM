"""Optional necessary weather-KG row on the unchanged joint packing model."""
from dataclasses import asdict
import math
from time import monotonic

import bound_joint
from medium_physics import add_row


def lower_bound(instance, *, budget_s=120., seed=0, tables=None, upper_bound=None):
    """A separately named experiment; certified.lower_bound remains unchanged.

    Every added row is a necessary final-state constraint; current moves and
    restows are unrestricted. This model drops lazy final-state cuts and the
    complete checker. Equality needs a separately checker-passed full plan.
    """
    started=monotonic()
    weights=instance.objective
    if any(not math.isfinite(float(v)) or v<0 for v in (
            weights.relocate,weights.restow,weights.hatch_cover,
            weights.future_restow,weights.future_hatch_cover)):
        raise ValueError('Finite nonnegative objective weights required')
    context=bound_joint.build_model(instance,budget_s=budget_s,seed=seed,tables=tables)
    if not isinstance(context,dict):
        return dict(result=asdict(context),physics=dict(added=False,reason='unsupported scope'),
                    wall_s=monotonic()-started)
    physics=add_row(context)
    result=bound_joint.solve_model(context)
    if upper_bound is not None and result.lower_bound>upper_bound+1e-7:
        raise AssertionError('Physics bound exceeds checker-passed upper')
    return dict(result=asdict(result),physics=physics,wall_s=monotonic()-started)
