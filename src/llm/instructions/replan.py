"""Replan under instruction constraints, check with the full checker, and verify the constraint is honoured.

The constraint enters the planner as one more hard local rule on each candidate placement (``forbids``), at the
same point the solver applies its securing-geometry screen. Task 9.3 swaps the planner: pass a ``planner_factory``
whose decoding mask also consults ``ConstrainedInstance.forbids``; nothing else changes.

APPROXIMATION: for the scalable solver the rule is injected by a scoped, single-threaded patch of the module-level
``securing_geometry_violation`` name in the solver modules (no solver API for extra per-action rules exists, and
the solver is outside this task's fence). The patch is undone on exit, and ``honoured()`` re-verifies the finished
plan, so a leak would be reported, never shown as honoured.

Single worker: the patch is process-wide, so ``constraint_rule`` (and therefore ``replan``) holds a process lock and
refuses concurrent or nested use with ``ReplanBusy`` rather than letting two instructions share one solver. The app
must run instruction replans on one worker (one at a time per process). The ballast finisher's spawned child
processes are separate interpreters and are unaffected.
"""

from __future__ import annotations

import importlib
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

from stowage.model import CheckReport, MovePlan, PlannerResult

from llm.instructions.apply import ConstrainedInstance

_HOOKED_MODULES = ("stowage.solver.scalable", "stowage.solver.exact_engine", "stowage.solver.construct_sequence")
# D79: the MVP runs with the C2 global stability screen off for the mask and the scalable solver; the full checker
# remains the gate.
GLOBAL_SCREEN_OFF = {"global_screen": 0}


class ReplanBusy(RuntimeError):
    """Another instruction replan holds the process-wide solver hook."""


_HOOK_LOCK = threading.Lock()


class _TimeBound:
    """A planner whose ``plan`` never runs longer than ``time_limit_s`` (the caller's limit, if tighter, wins)."""

    def __init__(self, inner: Any, time_limit_s: float | None) -> None:
        self.inner, self.time_limit_s = inner, time_limit_s
        self.name, self.version = inner.name, inner.version
        self.sets_ballast, self.deterministic = inner.sets_ballast, inner.deterministic

    def plan(self, instance, *, seed: int = 0, time_limit_s: float | None = None):
        limits = [t for t in (time_limit_s, self.time_limit_s) if t is not None]
        return self.inner.plan(instance, seed=seed, time_limit_s=min(limits) if limits else None)


def scalable_planner(time_limit_s: float | None = None):
    """The scalable solver with the D79 config, bounded by ``time_limit_s`` (ScalableConfig has no overall limit;
    ``plan`` takes it per call)."""
    from stowage.feasibility import FeasibilityConfig
    from stowage.solver.scalable import ScalableConfig, ScalableSolver

    return _TimeBound(ScalableSolver(ScalableConfig(feasibility_config=FeasibilityConfig(
        extra=dict(GLOBAL_SCREEN_OFF)))), time_limit_s)


@contextmanager
def constraint_rule(constrained: ConstrainedInstance):
    """While active, solver placement screens also reject placements the constraints forbid."""
    from stowage.actions.space import Place

    if not _HOOK_LOCK.acquire(blocking=False):
        raise ReplanBusy("an instruction replan is already running in this process; the solver hook is "
                         "process-wide, so run instruction replans on one worker, one at a time")
    originals = {}
    instance_id = constrained.instance.id

    def wrap(original):
        def screened(instance, expander, action, *args, **kwargs):
            if isinstance(action, Place) and instance.id == instance_id:
                reason = constrained.forbids(action.box, action.slot)
                if reason:
                    return reason
            return original(instance, expander, action, *args, **kwargs)
        return screened

    try:  # installation is inside the try: a failure after a partial install still restores (rev-v4-a P2)
        for name in _HOOKED_MODULES:
            module = importlib.import_module(name)
            if hasattr(module, "securing_geometry_violation"):
                originals[module] = module.securing_geometry_violation
                module.securing_geometry_violation = wrap(originals[module])
        yield
    finally:
        for module, original in originals.items():
            module.securing_geometry_violation = original
        _HOOK_LOCK.release()


@dataclass
class Replan:
    """One planner run: the result, the full-checker report, and the instruction check."""

    result: PlannerResult
    report: CheckReport | None
    constraint_violations: list[str] = field(default_factory=list)
    wall_s: float = 0.0
    precheck: str | None = None

    @property
    def plan(self) -> MovePlan | None:
        return self.result.plan if self.result is not None else None

    @property
    def honoured(self) -> bool:
        return self.plan is not None and not self.constraint_violations

    @property
    def shown(self) -> bool:
        """Only a plan the full checker passed and that honours every instruction may be shown as the answer."""
        return bool(self.honoured and self.report is not None and self.report.passed and self.result.shippable)


def replan(constrained: ConstrainedInstance, *, time_limit_s: float = 240.0, seed: int = 0,
           planner_factory: Callable[..., Any] = scalable_planner) -> Replan:
    """Plan ``constrained.instance`` under its constraints, then run the full checker (inside ``run_planner``).

    ``allow_repair=False``: the repair path is a different solver configuration and could bypass the hook; a
    failure is reported as such instead.
    """
    from stowage.planner import run_planner

    shortfall = constrained.capacity_shortfall()
    if shortfall:
        return Replan(None, None, precheck=shortfall)  # type: ignore[arg-type]
    started = time.monotonic()
    with constraint_rule(constrained):
        result, report = run_planner(planner_factory(time_limit_s), constrained.instance, seed=seed,
                                     time_limit_s=time_limit_s, allow_repair=False)
    violations = constrained.violations(result.plan) if result.plan is not None else []
    return Replan(result, report, violations, time.monotonic() - started)
