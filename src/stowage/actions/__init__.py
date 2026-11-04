"""Action space + deterministic expander (plan 3.8). Frozen *shape* (plan 0.9): the concrete action type is decided
in 3.8, so everything that speaks actions (feasibility engine 4.4, solvers 4.6–4.8, format 4.5, mask 5.1, planners)
is generic over ``A``.

An **action** is one planner decision (maybe a single lift, maybe "this box → this slot" or "group → stack", 3.8
decides). An **action space** knows how to serialise actions, list them in a canonical order, and *expand* a
sequence of them into a full MovePlan deterministically (the expander picks relocation targets and hatch-cover
moves by a fixed rule), so a gold plan's move count reproduces exactly from its actions.

3.8 chose ``boxslot`` (``stowage.actions.space``): one action = one box and where it goes (``Place(box, slot)``,
plus ``Discharge``, explicit ``Relocate`` overrides and ``RestowOff`` for layovers); the expander
(``stowage.actions.expander``) adds yard relocations, ship restows-off and lazy hatch-cover lifts.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

from stowage.frozen import frozen
from stowage.model import Instance, MovePlan
from stowage.versions import ACTION_SPACE_VERSION as ACTION_SPACE_VERSION

A = TypeVar("A", bound=Hashable)  # concrete action type, chosen by 3.8 (must be hashable and immutable)
A_co = TypeVar("A_co", bound=Hashable, covariant=True)


@runtime_checkable
class ActionSpace(Protocol[A]):
    """What 3.8 must provide. ``name``/``version`` go into ``MovePlan.action_space`` as ``"name@version"``.

    Contract:
        encode/decode: JSON-able dict ↔ action, exact round trip (``decode(encode(a)) == a``).
        sort_key: total order used to list legal actions canonically (determinism of masks and solvers).
        expand: deterministic; same instance + actions → byte-identical MovePlan. Raises ValueError if the
            sequence can't be expanded (e.g. an action references a box not in the yard) — legality beyond that is
            the move engine's and the checker's job.
        from_plan: inverse of ``expand`` on plans produced by ``expand`` (round-trip property test of 3.8:
            ``expand(from_plan(expand(x))) == expand(x)``); raises ValueError for plans it can't represent.
    """

    name: str
    version: str

    def encode(self, action: A) -> dict[str, Any]: ...

    def decode(self, data: dict[str, Any]) -> A: ...

    def sort_key(self, action: A) -> tuple[Any, ...]: ...

    def expand(self, instance: Instance, actions: Sequence[A]) -> MovePlan: ...

    def from_plan(self, instance: Instance, plan: MovePlan) -> list[A]: ...


@frozen("actions.get_action_space", owner="3.8", implemented=True)
def get_action_space(name: str = "default") -> ActionSpace[Any]:
    """Return the registered action space ``name`` (``"default"`` = the one 3.8 picks for StowLM and solvers).
    Raises KeyError for an unknown name."""
    from stowage.actions.space import SPACES  # noqa: PLC0415 - space imports the model; keep this module light

    return SPACES[name]
