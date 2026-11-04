"""Frozen-interface registry and the tiny-run stub mechanism (plan 0.9, rule "stubs allowed only for the tiny run").

Every frozen function is declared with ``@frozen("<interface name>")``. That does three things:

1. Registers it in ``FROZEN`` so tests can snapshot its signature (a signature change fails
   ``tests/test_frozen_interfaces.py`` — that's the X2 freeze) and check unimplemented ones raise
   NotImplementedError.
2. Lets a tiny run swap in a stub behind the same interface::

       with stub_override("checker.check", my_tiny_checker, reason="3.1 not done; tiny run only"):
           result = run_tiny_chain(...)

   While the override is active, calls to ``stowage.checker.check`` go to ``my_tiny_checker``.
3. Taints provenance: ``current_provenance()`` lists every active override, and every artifact (MovePlan,
   PlannerResult, CheckReport, Instance) carries a ``Provenance``. ``require_clean`` refuses tainted inputs; the
   gold-plan writer (6.1), dataset builder (7.1) and eval harness (5.2) must call it, and ``stowage.io.save_plan``
   refuses tainted plans unless ``allow_stubs=True`` (only tiny-run folders pass it).

Overrides live in a ``contextvars.ContextVar``, so they are scoped to the ``with`` block and to the current thread
or asyncio task. Override state is tied to its process ID and cleared after fork, so worker processes don't
inherit it even through copied contexts or inherited context-manager tokens. If a tiny run needs stubs in workers,
it installs them in the worker and the workers' results are tainted too.

Implementers: replace the body of the stub and set ``implemented=True`` in its decorator in the same change. Keep the
signature unless the change goes through the interface owner (X2).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class StubTaintError(RuntimeError):
    """An artifact produced with a stub reached a place that only accepts real results."""


@dataclass(frozen=True)
class FrozenInterface:
    name: str  # dotted interface name, e.g. "moves.apply_plan"
    owner_task: str  # plan task that implements it, e.g. "2.3"
    func: Callable[..., Any]  # the undecorated declared function
    implemented: bool

    @property
    def signature(self) -> inspect.Signature:
        return inspect.signature(self.func)


FROZEN: dict[str, FrozenInterface] = {}
_overrides: contextvars.ContextVar[tuple[int, dict[str, tuple[Callable[..., Any], str]]] | None] = contextvars.ContextVar(
    "stowage_stub_overrides", default=None
)


def _active_overrides() -> dict[str, tuple[Callable[..., Any], str]]:
    """Ignore parent state even if a copied Context or inherited token restores it after fork."""
    state = _overrides.get()
    return state[1] if state is not None and state[0] == os.getpid() else {}


def _clear_inherited_overrides() -> None:
    """Fork copies ContextVars; tiny-run overrides must remain in the parent."""
    _overrides.set(None)


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_clear_inherited_overrides)


def frozen(name: str, *, owner: str, implemented: bool = False) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Declare a frozen interface. ``owner`` is the plan task id that implements it."""

    def deco(func: Callable[P, R]) -> Callable[P, R]:
        ident = f"{func.__module__}.{func.__qualname__}"
        if name in FROZEN and f"{FROZEN[name].func.__module__}.{FROZEN[name].func.__qualname__}" != ident:
            raise RuntimeError(f"frozen interface {name!r} declared twice")
        FROZEN[name] = FrozenInterface(name, owner, func, implemented)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            active = _active_overrides()
            if active and name in active:
                return _stamp(active[name][0](*args, **kwargs), name, active[name][1])
            return func(*args, **kwargs)

        wrapper.__frozen_name__ = name  # type: ignore[attr-defined]
        return wrapper

    return deco


@contextlib.contextmanager
def stub_override(name: str, impl: Callable[..., Any], *, reason: str) -> Iterator[None]:
    """Route calls to frozen interface ``name`` to ``impl`` inside the ``with`` block (tiny run only).

    ``reason`` is recorded in provenance (e.g. "3.1 stability not done; tiny run"). The stub must keep the frozen
    signature. Nested overrides of the same name: the innermost wins.
    """
    if name not in FROZEN:
        raise KeyError(f"unknown frozen interface {name!r}; known: {sorted(FROZEN)}")
    if not reason.strip():
        raise ValueError("a stub override needs a reason")
    current = dict(_active_overrides())
    current[name] = (impl, reason)
    token = _overrides.set((os.getpid(), current))
    try:
        yield
    finally:
        _overrides.reset(token)


def active_stubs() -> dict[str, str]:
    """Active overrides in this context: interface name → reason."""
    return {k: v[1] for k, v in _active_overrides().items()}


def unimplemented_interfaces() -> list[str]:
    """Frozen interfaces whose real implementation hasn't landed (calling them raises NotImplementedError)."""
    return sorted(n for n, f in FROZEN.items() if not f.implemented)


def current_provenance(**extra: Any):  # -> stowage.model.Provenance (lazy import avoids a cycle)
    """Provenance for an artifact created *now*: component versions + active stubs + ``extra`` fields
    (e.g. ``seed=``, ``created_by=``). Merge with the inputs' provenance via ``Provenance.merge``."""
    from stowage.model.provenance import Provenance  # noqa: PLC0415
    from stowage.versions import component_versions  # noqa: PLC0415

    return Provenance(versions=component_versions(), stubs=active_stubs(), **extra)


def _stamp(result: Any, name: str, reason: str) -> Any:
    """Taint a stub's return value: any pydantic artifact with a ``provenance`` field (directly, or a
    PlannerResult's ``plan``) gets ``{name: reason}`` merged into its stubs. Values without provenance (e.g. a
    WorldState from a stubbed move engine) can't carry taint themselves; the caller's ``current_provenance()``
    still lists the active override, so artifacts built inside the ``with`` block are tainted."""
    from stowage.model.provenance import Provenance  # noqa: PLC0415

    stub = Provenance(stubs={name: reason})
    prov = getattr(result, "provenance", None)
    if isinstance(prov, Provenance):
        result = result.model_copy(update={"provenance": prov.merge(stub)})
        plan = getattr(result, "plan", None)
        if plan is not None and isinstance(getattr(plan, "provenance", None), Provenance):
            result = result.model_copy(update={"plan": plan.model_copy(
                update={"provenance": plan.provenance.merge(stub)})})
    return result


def _artifact_stubs(*items: Any) -> dict[str, str]:
    """Collect nested artifact taint without mutation, including cycles and shared references.

    The planner retains this taint before finalization or repair replaces an input artifact.
    Unlike ``require_clean``, this internal collector also accepts container roots.
    """
    from pydantic import BaseModel  # noqa: PLC0415

    from stowage.model.provenance import Provenance  # noqa: PLC0415

    tainted: dict[str, str] = {}
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, Provenance):
            tainted.update(value.stubs)
            return
        # Inspect .provenance even when it is a property rather than a model field.
        # Read stubs directly: a property can return a fresh, short-lived object.
        prov = getattr(value, "provenance", None)
        if isinstance(prov, Provenance):
            tainted.update(prov.stubs)
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                visit(getattr(value, name))
        elif isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                visit(nested)
        else:
            # Preserve support for non-Pydantic artifacts exposing .provenance.
            if isinstance(prov, Provenance):
                visit(getattr(value, "plan", None))
                visit(getattr(value, "later_plans", None))

    for item in items:
        visit(item)
    return tainted


def require_clean(*items: Any, what: str) -> None:
    """Raise StubTaintError if any item is tainted. Items are ``Provenance`` objects or artifacts with a
    ``.provenance`` (Instance, MovePlan, PlannerResult, CheckReport...); anything else is a TypeError (so a wrong
    argument can't pass silently). ``what`` names the consumer, e.g. ``"gold-plan writer"``. Call at every boundary
    where shipped results are made (6.1, 7.1, 5.2, 9.1). Nested model fields and containers are checked too,
    including every later plan and checker report; a clean wrapper cannot hide tainted inputs."""
    from stowage.model.provenance import Provenance  # noqa: PLC0415

    for item in items:
        prov = item if isinstance(item, Provenance) else getattr(item, "provenance", None)
        if not isinstance(prov, Provenance):
            raise TypeError(f"require_clean needs a Provenance or an artifact with .provenance, got {type(item)!r}")
    tainted = _artifact_stubs(*items)
    if tainted:
        raise StubTaintError(f"{what} refuses stub-tainted input; stubs: {tainted}")
