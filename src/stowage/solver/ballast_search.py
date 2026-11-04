"""Bounded local search for ballast checked with the exact physics.

Tank variables are fractions of their permitted fill ranges. Negative margins
mean a physical limit failed. SLSQP repeatedly estimates how a small fill change
changes those margins, then solves a local quadratic subproblem to reduce their
squared negative values. It is a search, never an infeasibility proof. The exact
callback alone can accept a condition. Source: Kraft (1988), *A software package
for sequential quadratic programming*, and SciPy's ``minimize(method='SLSQP')``
documentation, https://docs.scipy.org/doc/scipy/reference/optimize.minimize-slsqp.html.

Empty and full tanks need explicit trials: an infinitesimal change to an endpoint
can introduce a free surface, so derivatives alone cannot discover every useful
endpoint. Seeded restarts and complementary endpoint patterns also explore these
conditions. Wall-clock cutoffs can change how many trials finish; their order is
deterministic for a given seed. Exhausting the budget says only "not found".
"""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

import numpy as np
from scipy.optimize import minimize


class Deadline(Exception):
    """The caller's overall wall-clock budget expired."""


class Found(Exception):
    """The caller accepted an exactly checked condition; stop searching."""


class _RestartDeadline(Exception):
    """Reserve some of the remaining budget for other starting conditions."""


def search(
    fills: list[np.ndarray],
    evaluate: Callable[[np.ndarray], np.ndarray],
    deadline: float,
    seed: int,
) -> None:
    """Try normalized fills, leaving acceptance and best tracking to ``evaluate``.

    ``evaluate`` returns the same ordered vector of normalized exact margins on
    every call, raises ``Found`` on an acceptable condition, and may raise
    ``Deadline``. Both exceptions propagate. No returned optimizer flag is used
    as evidence of feasibility or infeasibility. At least one fill vector is
    required to establish the number of adjustable tanks.
    """
    if not fills:
        return
    dimension = len(fills[0])
    if any(np.asarray(fill).shape != (dimension,) for fill in fills):
        raise ValueError("ballast search starts must have matching one-dimensional shapes")
    rng = np.random.default_rng(seed)
    starts = [np.clip(np.asarray(fills[0], dtype=float), 0.0, 1.0)]
    starts.extend([np.zeros(dimension), np.ones(dimension), np.full(dimension, 0.5)])
    primary_keys = {tuple(start) for start in starts}
    starts.extend(np.clip(np.asarray(fill, dtype=float), 0.0, 1.0) for fill in fills[1:])
    if dimension == 0:
        if monotonic() >= deadline:
            raise Deadline
        evaluate(starts[0])
        return

    # Complementary patterns cover both sides of symmetric tank arrangements.
    # Each explicit endpoint is checked before gradients can move it into slack.
    for parity in (0, 1):
        starts.append(np.array([(index + parity) % 2 for index in range(dimension)], dtype=float))
    for index in range(dimension):
        one_full = np.zeros(dimension)
        one_full[index] = 1.0
        starts.extend((one_full, 1.0 - one_full))
    for _ in range(4):
        corner = rng.integers(0, 2, dimension).astype(float)
        starts.extend((corner, rng.uniform(0.0, 1.0, dimension)))

    unique = []
    seen = set()
    for start in starts:
        key = tuple(start)
        if key not in seen:
            seen.add(key)
            unique.append(start)

    candidates = []
    evaluation_seconds = 0.0

    def score(fill: np.ndarray) -> float:
        nonlocal evaluation_seconds
        if monotonic() >= deadline:
            raise Deadline
        before = monotonic()
        margins = np.asarray(evaluate(np.clip(fill, 0.0, 1.0)), dtype=float)
        evaluation_seconds = max(evaluation_seconds, monotonic() - before)
        # A missing exact calculation is never interpreted as a good margin.
        negative = np.minimum(np.nan_to_num(margins, nan=-1e6, neginf=-1e6, posinf=1e6), 0.0)
        return float(np.dot(negative, negative))

    # Screen only the supplied seed and empty/full/half conditions. Full exact
    # checks can take seconds; corners must not consume the correction budget.
    for start in unique:
        candidates.append((score(start) if tuple(start) in primary_keys else float("inf"), start))
    candidates.sort(key=lambda candidate: candidate[0])

    for index, (_, start) in enumerate(candidates):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise Deadline
        # Leave enough time for at least one finite-difference gradient when
        # exact evaluations are expensive, rather than restarting mid-gradient.
        allowance = max(remaining * 0.7, evaluation_seconds * (dimension + 2))
        local_deadline = monotonic() + min(remaining, allowance)

        def objective(fill: np.ndarray) -> float:
            if monotonic() >= deadline:
                raise Deadline
            if monotonic() >= local_deadline:
                raise _RestartDeadline
            return score(fill)

        try:
            optimum = minimize(
                objective,
                start,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * dimension,
                options={"maxiter": 80, "ftol": 1e-12, "eps": 1e-5, "disp": False},
            )
            # Regardless of the numerical solver's success flag, only the exact
            # callback can decide whether its final condition is acceptable.
            score(optimum.x)
        except _RestartDeadline:
            continue
