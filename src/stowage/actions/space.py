"""The ``boxslot`` action space (plan 3.8): one action = one box and where it goes.

**Why this granularity.** A plan is a sequence of crane lifts, but most lifts are *forced* once you know which box
goes where and in what order: the boxes on top of it in the yard must move, the hatch cover must open, and so on.
So StowLM (and every solver) writes only the choices — *which* box next, and *which* slot — and a deterministic
expander (``stowage.actions.expander``) fills in the forced lifts. That makes both of the industry's two steps
(E10: which category goes to which slot, then which actual yard box fills it) one joint decision per action, which
is exactly where our claimed savings come from. Coarser actions ("this category → this stack, n boxes") would hand
the box choice to a rule and lose that.

Actions (structured, hashable, immutable):
    ``Place(box, slot)``     — put ``box`` into ship slot ``slot`` (BAPLIE ``BBBRRTT``). From the yard it is a load
                               (yard blockers relocated by the default rule first); from the quay buffer a
                               ``restow_on``; from another ship slot a ``restow_shift`` (1 lift).
                               With rule freeze v1 on, Stage A excludes this last case; Stage B enables it.
    ``Discharge(box, to)``   — ship → yard. ``to`` = yard stack key ``BLOCK-BBB-RR``, or None = default rule.
    ``Relocate(box, to)``    — one explicit yard rehandle of a top box to stack ``to``: overrides the default rule
                               (written just before the load it serves) or a voluntary pre-move.
    ``RestowOff(box)``       — ship → quay buffer (boxes above it go first, automatically).
There is no explicit end action: a plan ends when every load-list box is aboard, every discharge done and the quay
buffer empty; the format (4.5) emits EOS then.

**Completeness within the supported stage/action domain.** ``from_plan`` turns a legal MovePlan without gate moves
or stage-excluded ship shifts into actions whose
expansion has exactly the same box lifts; only hatch-cover lifts are re-timed to the lazy rule, which is never more
lifts (argument in the expander docstring). Overrides (``Relocate``, ``Discharge.to``) are emitted only where the
plan differs from the default rule, so the common case costs no extra tokens. Within this domain, every optimal
plan has an action sequence with the same move count; active Stage A excludes otherwise engine-legal aboard
shifts under D69. ``experiments/action-space`` measures on tiny instances how often the
optimum needs an override at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from stowage.actions.expander import COVER_KINDS, Expander
from stowage.frozen import current_provenance
from stowage.model import Instance, MovePlan
from stowage.model.moves import Move
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.versions import ACTION_SPACE_VERSION


@dataclass(frozen=True, slots=True)
class Place:
    box: str
    slot: str


@dataclass(frozen=True, slots=True)
class Discharge:
    box: str
    to: str | None = None


@dataclass(frozen=True, slots=True)
class Relocate:
    box: str
    to: str


@dataclass(frozen=True, slots=True)
class RestowOff:
    box: str


Action = Place | Discharge | Relocate | RestowOff
_OPS: dict[str, type] = {"P": Place, "D": Discharge, "R": Relocate, "O": RestowOff}
_OP_OF = {cls: op for op, cls in _OPS.items()}
_RANK = {"P": 0, "D": 1, "R": 2, "O": 3}
_FIELDS = {"P": ("box", "slot"), "D": ("box", "to"), "R": ("box", "to"), "O": ("box",)}


def box_moves(moves: Sequence[Move]) -> list[Move]:
    """The moves that are container lifts (everything except hatch-cover lifts)."""
    return [m for m in moves if m.kind not in COVER_KINDS]


class BoxSlotSpace:
    """``ActionSpace[Action]`` for StowLM and the solvers (registered as ``"boxslot"`` and ``"default"``)."""

    name = "boxslot"
    version = ACTION_SPACE_VERSION

    # ------------------------------------------------------------------ serialisation
    def encode(self, action: Action) -> dict[str, Any]:
        op = _OP_OF.get(type(action))
        if op is None:
            raise ValueError(f"not a {self.name} action: {action!r}")
        data: dict[str, Any] = {"op": op}
        for field in _FIELDS[op]:
            value = getattr(action, field)
            if value is not None:
                data[field] = value
        return data

    def decode(self, data: dict[str, Any]) -> Action:
        op = data.get("op")
        if op not in _OPS:
            raise ValueError(f"unknown action op: {op!r}")
        extra = set(data) - {"op", *_FIELDS[op]}
        if extra:
            raise ValueError(f"unknown fields for op {op}: {sorted(extra)}")
        try:
            return _OPS[op](**{k: v for k, v in data.items() if k != "op"})
        except TypeError as exc:
            raise ValueError(f"bad action {data!r}: {exc}") from exc

    def sort_key(self, action: Action) -> tuple[Any, ...]:
        op = _OP_OF[type(action)]
        target = getattr(action, "slot", None) or getattr(action, "to", None) or ""
        return (_RANK[op], action.box, target)

    # ------------------------------------------------------------------ expansion
    def expander(self, instance: Instance) -> Expander:
        """A fresh incremental expander (for the feasibility engine and solvers)."""
        return Expander(instance)

    def expand(self, instance: Instance, actions: Sequence[Action]) -> MovePlan:
        ex = Expander(instance)
        acts = [self.decode(a) if isinstance(a, dict) else a for a in actions]
        for action in acts:
            ex.step(action)
        ex.finish()
        # Store the canonical action form (a function of the lifts only): what SFT trains on, and what makes
        # expand ∘ from_plan ∘ expand == expand exact. Redundant explicit actions in ``actions`` collapse.
        acts = parse_moves(Expander(instance), box_moves(ex.moves))
        tag = f"{self.name}@{self.version}"
        prov = current_provenance(created_by=f"actions.{self.name}").merge(
            instance.provenance, parent_ids=[instance.id])
        return MovePlan(id=f"{instance.id}.{self.name}", instance_id=instance.id, call_index=instance.call_index,
                        moves=ex.moves, action_space=tag, actions=[self.encode(a) for a in acts],
                        provenance=prov)

    def from_plan(self, instance: Instance, plan: MovePlan) -> list[Action]:
        """Actions whose expansion reproduces ``plan``'s container lifts exactly (hatch-cover lifts re-timed to the
        lazy rule: never more). Raises ValueError for a plan it can't represent (gate moves, or a box lift the
        expander can't reproduce, e.g. a lift of a box that isn't on top).

        The result is *canonical*: it depends only on the container lifts (greedy longest match, below), so
        ``expand`` stores it and ``expand(from_plan(expand(x))) == expand(x)`` holds byte for byte."""
        if plan.instance_id != instance.id or plan.call_index != instance.call_index:
            raise ValueError("plan instance_id and call_index must match the instance")
        moves = box_moves(plan.moves)
        if any(m.kind in ("gate_in", "gate_out") for m in moves):
            raise ValueError("gate moves are simulator-only and not representable as actions")
        return parse_moves(Expander(instance), moves)


_ANCHORS = ("load", "restow_on", "restow_shift", "discharge")


def parse_moves(ex: Expander, moves: Sequence[Move]) -> list[Action]:
    """Cover ``moves`` (container lifts only) with actions, left to right, always taking the action whose expansion
    matches the *longest* run of upcoming lifts: the next anchor lift (load / restow / discharge) with the
    relocations and restows-off before it generated automatically, else a ``RestowOff`` that clears the boxes
    above it, else one explicit ``Relocate``/``RestowOff``. The world after a run depends only on the lifts, not
    on how they were grouped, and a single explicit action always matches, so the greedy parse never gets stuck on
    a reproducible plan. Mutates ``ex``."""
    out: list[Action] = []
    i, n = 0, len(moves)
    while i < n:
        k = i
        while k < n and moves[k].kind in ("relocate", "restow_off"):
            k += 1
        candidates: list[tuple[Action | None, int]] = []
        if k < n:
            if moves[k].kind not in _ANCHORS:
                raise ValueError(f"cannot represent move kind {moves[k].kind}")
            candidates.append((None, k + 1))  # anchor at k (built against the trial state)
        t = k - 1
        while t > i:  # RestowOff(b) with auto-cleared boxes above it, longest first
            if all(m.kind == "restow_off" for m in moves[i:t + 1]):
                candidates.append((RestowOff(moves[t].container_id), t + 1))
            t -= 1
        if k > i:
            candidates.append((_explicit(moves[i]), i + 1))
        for action, end in candidates:
            trial = ex.copy() if len(candidates) > 1 else ex
            try:
                if action is None:
                    action = _anchor(trial, moves[k])
                ok = box_moves(trial.step(action)) == list(moves[i:end])
            except ValueError:
                ok = False
            if ok:
                ex.__dict__.update(trial.__dict__)
                out.append(action)
                i = end
                break
        else:
            raise ValueError(f"cannot reproduce lift {i}: {moves[i]!r}")
    return out


def _explicit(m: Move) -> Action:
    if m.kind == "relocate":
        return Relocate(m.container_id, m.dst.rsplit("-", 1)[0])
    return RestowOff(m.container_id)


def _anchor(ex: Expander, m: Move) -> Action:
    if m.kind == "discharge":
        # The default target is judged on the current yard: the ship-side clearing a discharge triggers only
        # moves boxes to the quay, so the yard is the same when the discharge lifts.
        if m.dst == "truck":
            return Discharge(m.container_id)
        key = m.dst.rsplit("-", 1)[0]
        if rule_freeze_v1_enabled():
            # Active candidate validation needs the source accessible. Judge the
            # default after the same forced restows/hatch work as the anchor.
            try:
                default = ex.copy().step(Discharge(m.container_id))[-1].dst.rsplit("-", 1)[0]
            except ValueError:
                default = None
        else:
            default = ex.yard_target(m.container_id, None)
        return Discharge(m.container_id) if default == key \
            else Discharge(m.container_id, key)
    return Place(m.container_id, m.dst)


SPACES: dict[str, BoxSlotSpace] = {}
SPACES["boxslot"] = SPACES["default"] = BoxSlotSpace()
