"""Repair the currently failing exact ballast quantities with bounded LP steps.

Each negative, dimensionless margin is a failed exact limit. Small changes to
one tank at a time measure local slopes of those quantities only; a linear
program then chooses bounded tank changes that reduce the remaining deficits.
The next full exact check decides acceptance and discovers newly active limits.
The local model is an optimisation approximation, never a feasibility proof.
This is the trust-region successive-linear-programming idea described by
Conn, Gould and Toint (2000), *Trust Region Methods*, SIAM, chapter 15.

An extremely small secondary cost favours nearby empty/full endpoints, because
slack tanks have a free surface (IMO MSC.267(85), Part B 3.1). Endpoints are
also checked explicitly: a derivative cannot predict that discontinuous change.
The callback must check its deadline inside physics calls; this module checks
before every callback, every slope, and every native LP, whose own time limit
is the remaining budget. A solver result is never accepted without the callback.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from time import monotonic
import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, linprog

from stowage.solver.ballast_search import Deadline


def search(
    fills: Sequence[np.ndarray],
    evaluate: Callable[..., Mapping[str, float]],
    deadline: float,
    seed: int = 0,
    *,
    directions: np.ndarray | None = None,
    slack_weights: np.ndarray | None = None,
) -> None:
    """Search fills in [0, 1], leaving exact acceptance to ``evaluate``.

    ``evaluate(fill, active_keys=None)`` returns signed normalized exact margins.
    Full calls must include every limit and raise ``Found`` if all checks pass.
    Slope calls supply the tuple of currently violated keys, allowing the caller
    to skip unrelated costly curve/strength calculations. ``Deadline`` and
    ``Found`` propagate. Optional direction columns group tank perturbations;
    the default identity matrix treats each adjustable tank independently.
    Optional ``slack_weights`` increase the endpoint preference of tanks with
    larger upright free-surface moments; positive weights are normalized.
    ``seed`` is retained for a stable common search interface; no random work
    is needed. Failure to find a condition supplies no infeasibility evidence.
    """
    del seed
    if not fills:
        return
    starts = [np.clip(np.asarray(fill, dtype=float), 0., 1.) for fill in fills]
    size = starts[0].size
    if any(fill.shape != (size,) for fill in starts):
        raise ValueError('ballast search starts must have matching one-dimensional shapes')
    basis = np.eye(size) if directions is None else np.asarray(directions, dtype=float)
    if basis.ndim != 2 or basis.shape[0] != size or not np.all(np.isfinite(basis)):
        raise ValueError('ballast search directions must be a finite tank-by-direction matrix')

    weights = np.ones(size) if slack_weights is None else np.asarray(slack_weights, dtype=float)
    if weights.shape != (size,) or not np.all(np.isfinite(weights)) or np.any(weights < 0.):
        raise ValueError('ballast slack weights must be finite nonnegative tank weights')
    weights = weights / max(float(np.sum(weights)), 1.)

    def checkpoint():
        if monotonic() >= deadline:
            raise Deadline

    def sample(fill, keys=None):
        checkpoint()
        values = evaluate(np.clip(fill, 0., 1.), active_keys=keys)
        checkpoint()
        # Unavailable exact margins cannot count as success or finite slopes.
        return {key: float(value) if np.isfinite(value) else -1e6
                for key, value in values.items()}

    def complete(fill, margins, required):
        # The fast loading-stage callback can omit previously measured curve
        # margins. Recompute those explicitly rather than silently forgetting
        # a known active limit or comparing stale values from another fill.
        missing = tuple(sorted(key for key in required if key not in margins))
        if missing:
            margins.update(sample(fill, missing))
            for key in missing:
                margins.setdefault(key, -1e6)
        return margins

    def merit(margins):
        negative = np.minimum(np.array(list(margins.values())), 0.)
        return float(np.dot(negative, negative))

    if size == 0:
        sample(starts[0])
        return
    # Preserve the LP seed first. Endpoint starts provide different free-surface
    # regimes rather than a tank-count-sized combinatorial enumeration.
    starts.extend([np.zeros(size), np.ones(size)])
    seen = set()
    for start in starts:
        checkpoint()
        identity = start.tobytes()
        if identity in seen:
            continue
        seen.add(identity)
        x = start.copy()
        margins = sample(x)
        # Dry-to-slack FSM is discontinuous: infinitesimal fill slopes can
        # incorrectly steer an unstable empty condition away from all ballast.
        # Test the pressed-full basin before differentiating negative exact GM.
        if margins.get('stability/gm_m', 0.) < 0. and not np.all(x == 1.):
            endpoint = np.ones(size)
            endpoint_margins = sample(endpoint)
            if endpoint_margins.get('stability/gm_m', -1.) >= 0.:
                x, margins = endpoint, endpoint_margins
        radius = .35
        stagnant = 0
        discovered = set()
        for _ in range(24):
            checkpoint()
            complete(x, margins, discovered)
            previous_keys = tuple(margins)
            keys = tuple(key for key, value in margins.items()
                         if value < 0. or key in discovered)
            if not keys:
                # Only the exact callback can accept. No floating-point LP
                # status or missing margin vector is treated as a certificate.
                break
            before = merit(margins)
            base = np.array([margins[key] for key in keys])
            jacobian = np.empty((len(keys), size))
            if directions is None:
                for index in range(size):
                    checkpoint()
                    step = .002 if x[index] <= .998 else -.002
                    trial = x.copy()
                    trial[index] += step
                    measured = sample(trial, keys)
                    jacobian[:, index] = [(measured.get(key, -1e6)-margins[key])/step
                                          for key in keys]
            else:
                slopes = np.empty((len(keys), basis.shape[1]))
                actual = np.empty_like(basis)
                for index, direction in enumerate(basis.T):
                    checkpoint()
                    trial = np.clip(x + .002*direction, 0., 1.)
                    # Try the opposite direction if clipping removes its move.
                    if np.linalg.norm(trial-x) < 1e-12:
                        trial = np.clip(x-.002*direction, 0., 1.)
                    actual[:, index] = trial-x
                    measured = sample(trial, keys)
                    slopes[:, index] = [measured.get(key, -1e6)-margins[key] for key in keys]
                jacobian = slopes @ np.linalg.pinv(actual)
            checkpoint()
            # APPROXIMATION: finite-difference local slopes are trusted only
            # inside this bounded step; full exact physics verifies every trial.
            # Auxiliary nonnegative deficits make the LP possible even when a
            # local step cannot repair every failing limit at once.
            count = len(keys)
            objective = np.r_[1e-7*(1.-2.*x)*weights, np.ones(count)]
            constraints = np.c_[-jacobian, -np.eye(count)]
            bounds = [(max(-float(value), -radius), min(1.-float(value), radius))
                      for value in x] + [(0., None)]*count
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=OptimizeWarning,
                                        message='Unrecognized options detected.*')
                optimum = linprog(objective, A_ub=constraints, b_ub=base-1e-7,
                                  bounds=bounds, method='highs',
                                  options={'time_limit': max(.001, deadline-monotonic()), 'threads': 1})
            checkpoint()
            if optimum.x is None:
                break
            delta = optimum.x[:size]
            improved = False
            # A full check may reveal a previously legal quantity becoming
            # illegal. Backtracking then shrinks the local model's trust step.
            for factor in (1., .5, .25):
                checkpoint()
                trial = np.clip(x+factor*delta, 0., 1.)
                updated = complete(trial, sample(trial), previous_keys)
                # Keep quantities that failed an exact trial in the next local
                # LP, so rejected steps cannot repeat the same hidden failure.
                discovered.update(key for key, value in updated.items() if value < 0.)
                # Advancing past a fast loading failure can expose a much
                # larger curve/weather failure. Compare the exact quantities
                # known before this step; the newly exposed failures become
                # the next iteration's task rather than vetoing stage progress.
                prior_merit = merit({key: updated[key] for key in previous_keys})
                if prior_merit < before-max(1e-14, before*1e-7):
                    x, margins = trial, updated
                    improved = True
                    radius = min(.6, radius*1.5)
                    stagnant = 0
                    break
            if not improved:
                radius *= .35
                stagnant += 1
            # Empty/full removes slack-tank FSM discontinuously. Probe nearest
            # endpoints as one combined move, not 2*N costly exact trials.
            if stagnant or np.any((x > 0.) & (x < .01)) or np.any((x < 1.) & (x > .99)):
                snapped = x.copy()
                mask = ((x < .15) | (x > .85)) if not stagnant else np.ones(size, dtype=bool)
                snapped[mask] = (x[mask] >= .5).astype(float)
                if not np.array_equal(snapped, x):
                    updated = complete(snapped, sample(snapped), tuple(margins))
                    discovered.update(key for key, value in updated.items() if value < 0.)
                    if merit({key: updated[key] for key in margins}) < merit(margins)-1e-14:
                        x, margins = snapped, updated
                        stagnant = 0
            if radius < 1e-4 or stagnant >= 3:
                break
