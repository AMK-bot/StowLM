"""Deterministic expander (plan 3.8): action sequence → crane lifts (``Move``s). Exposed through
``ActionSpace.expand``; the incremental ``Expander`` is also what the feasibility engine (4.4) can step one action at a
time.

**What the planner decides and what the expander fills in.** One action names a box and where it goes
(``Place(box, slot)``, ``Discharge``, ``Relocate``, ``RestowOff`` in ``stowage.actions.space``). Everything a crane
must do *before* that lift is forced or can be fixed by a rule without losing optimality, so the expander adds it:

1. **Yard blockers** (boxes stacked on top of the box being loaded) are relocated top-down. Each goes to the yard
   stack chosen by ``Expander.yard_target`` (the *default relocation rule*, below). The planner overrides the rule by
   writing an explicit ``Relocate(box, stack)`` action before the load; the expander then only handles what is left.
   In blocks-relocation-problem (BRP) terms this is the "restricted" relocation model (only boxes above the next
   target move) with the target chosen by a rule, plus explicit actions for anything else, so *unrestricted*
   relocation plans (moving a box that isn't blocking yet) stay representable. Source: Caserta, Schwarze & Voß (2011)
   "A mathematical formulation and complexity considerations for the blocks relocation problem", EJOR 219(1);
   Petering & Hussein (2013) on restricted vs unrestricted relocations.
2. **Ship blockers** (boxes above a ship box that must come off, incl. boxes on the deck of a hatch cover that must
   open) go to the quay buffer by ``restow_off`` in a canonical order; the planner puts them back with ``Place``.
   A cheaper direct shift (``restow_shift``, 1 lift) is the planner's choice: ``Place`` the blocker elsewhere first.
3. **Hatch covers** are opened *lazily* (just before the first hold access that needs it, to the quay) and closed
   lazily (just before the next deck access on that cover, or at the end of the plan). For a fixed sequence of box
   lifts this is optimal: a cover must be off for every hold access and on for every deck access on it and at
   departure, so its lifts are at least 2 × (number of maximal runs of hold accesses), which the lazy rule achieves.
   Where a cover rests does not change the count, so the quay is used always.

**Default relocation rule** (``yard_target``), a deterministic function of the current yard and the load list only
(never of later actions, so a partial plan has one well-defined state for the mask): among legal positions in the
source block (a discharge searches every block), prefer, in order:
    (a) a *clean* stack: holds no box still to be loaded (relocating there can never cause another rehandle in this
        plan) — the classic BRP heuristic of avoiding stacks with pending retrievals (Caserta et al. 2011, §5);
    (b) if none is clean, the stack with the fewest boxes still to be loaded;
    (c) the same yard bay as the source (an RTG moves across rows without gantrying), then the nearest bay and row;
    (d) an existing stack before a fresh ground slot (keeps ground free); then the stack key.
APPROXIMATION: yard placement restrictions beyond what the move engine checks are labelled assumptions: a reefer in a
reefer row stays in reefer rows when relocated, a discharged reefer goes to a reefer row and a discharged DG box to a
DG block when the port has them. Relocations stay inside their block (one yard-crane lift); the move engine itself
would accept a cross-block "lift", which isn't physically one move.

With the activation switch off, expansion retains the legacy deterministic rule.
With rule freeze v1 on, yard candidates and each completed action's lifts are
validated by the move engine; rejected actions leave the expander unchanged.
Yard crossing blockers at each block's effective yard limit are lowered through counted relocations. Ship-side
crossing obstacles remain the planner's sequencing responsibility (D69).
Active validation copies/replays world states; full-fleet mask latency is unmeasured.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from contextvars import copy_context
from functools import wraps
from typing import TYPE_CHECKING

from stowage.conventions import cells_of_slot, format_yard, parse_slot, parse_yard
from stowage.model import Instance
from stowage.model.moves import (
    Discharge,
    HatchCoverOff,
    HatchCoverOn,
    Load,
    Move,
    Relocate,
    RestowOff,
    RestowOn,
    RestowShift,
)
from stowage.model.ship import Placement, WorldState
from stowage.model.yard import YardStack
from stowage.moves import IllegalMoveError, apply_move, crossing_paths, pending_lift_rows
from stowage.moves.engine import (apply_move as _NATIVE_APPLY_MOVE,
                                  initial_state as _NATIVE_INITIAL_STATE)
from stowage.ports import yard_blocking_tiers
from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled

if TYPE_CHECKING:
    from stowage.actions.space import Action

COVER_KINDS = frozenset({"hatch_cover_off", "hatch_cover_on"})


class _CatalogueContext:
    """Read-only captured policy; each invocation enters an independent copy."""

    def __init__(self):
        previous = copy_context()
        with rule_freeze_v1(enabled=rule_freeze_v1_enabled()):
            # Capture only bindings established by the catalogue helper, not
            # unrelated temporary stubs, checker caches or caller deadlines.
            self._bindings = tuple((variable, value) for variable, value in copy_context().items()
                                   if variable not in previous or previous[variable] is not value)

    def run(self, method, *args, **kwargs):
        context = copy_context()
        for variable, value in self._bindings:
            context.run(variable.set, value)
        return context.run(method, *args, **kwargs)

    def __deepcopy__(self, memo):
        # This is static policy, shared like the expander's other indices. The
        # stored bindings are never mutated, including during probes.
        return self


def _in_catalogue_context(method):
    """Use the construction snapshot even when a caller changes worker threads."""
    @wraps(method)
    def bound(self, *args, **kwargs):
        # Contexts cannot be entered concurrently. Copies also support nested
        # public/private preparation calls without sharing mutable entry state.
        return self._catalogue_context.run(method, self, *args, **kwargs)

    return bound


class Expander:
    """Incremental world state that turns actions into lifts. ``step(action)`` returns the lifts it adds (and keeps
    them in ``moves``); ``finish()`` closes the covers still open. Raises ValueError for an action that can't be
    expanded (box not where the action needs it, unknown slot, no legal relocation target, a shift that needs a
    cover both open and closed). Active rule freeze v1 also rejects any expansion
    the move engine (2.3) cannot replay; legacy expansion retains its previous checks.
    The construction-time catalogue snapshot governs copies, probes and private
    yard preparation even when they run in another thread or caller context."""

    def __init__(self, instance: Instance, state: WorldState | None = None) -> None:
        # Pin the complete effective snapshot, including default catalogue
        # settings. A worker thread does not inherit a parent's ContextVars.
        # The public override helper preserves the content identity when the
        # requested activation value already matches the current catalogue.
        self._catalogue_context = _CatalogueContext()
        state = state or WorldState(call_index=instance.call_index, yard=instance.yard, ship=instance.onboard)
        self._base_state = state.model_copy(deep=True)
        self.instance = instance
        self.call = instance.call_index
        self.boxes = {box.id: box for box in instance.containers}
        self.cells = {cell.id: cell for cell in instance.vessel.cells}
        self.stacks = {stack.id: stack for stack in instance.vessel.stacks}
        self.covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
        self.blocks = {block.id: block for block in instance.port.yard.blocks}
        equipment = {item.id: item for item in instance.port.equipment}
        self.limit = {b.id: min(b.max_tiers, instance.port.yard_max_tiers, equipment[b.equipment_id].stack_tiers)
                      for b in instance.port.yard.blocks}
        self.cell_pos = {cid: (stack.id, i) for stack in instance.vessel.stacks for i, cid in enumerate(stack.cell_ids)}
        self.load_set = frozenset(instance.load_list)
        # mutable state
        self.yard: dict[str, list[str]] = {s.key: list(s.boxes) for s in state.yard.stacks}
        self.ylen: dict[str, int] = {s.key: s.length_ft for s in state.yard.stacks}
        self.foot: dict[tuple[str, int, int], str] = {}
        self.where: dict[str, tuple[str, str]] = {}
        self.pending: dict[str, int] = {}  # yard stack → number of load-list boxes in it (still to be loaded)
        for key, boxes in self.yard.items():
            self._claim(key, self.ylen[key])
            for cid in boxes:
                self.where[cid] = ("yard", key)
            self.pending[key] = sum(cid in self.load_set for cid in boxes)
        self.occ: dict[str, str] = {}
        for p in state.ship.placements:
            self.where[p.container_id] = ("ship", p.slot)
            for cell in cells_of_slot(p.slot):
                self.occ[cell] = p.container_id
        self.buffer: list[str] = list(state.yard.quay_buffer)
        for cid in self.buffer:
            self.where[cid] = ("quay", "quay")
        self.covers_off: dict[str, str] = dict(state.ship.covers_off)
        self.moves: list[Move] = []

    # ------------------------------------------------------------------ copying
    def copy(self) -> Expander:
        """Independent copy (O(state)); static indices are shared."""
        new = object.__new__(Expander)
        new.__dict__.update(self.__dict__)
        new.yard = {k: list(v) for k, v in self.yard.items()}
        new.ylen = dict(self.ylen)
        new.foot = dict(self.foot)
        new.where = dict(self.where)
        new.pending = dict(self.pending)
        new.occ = dict(self.occ)
        new.buffer = list(self.buffer)
        new.covers_off = dict(self.covers_off)
        new.moves = list(self.moves)
        return new

    # ------------------------------------------------------------------ public API
    def world_state(self) -> WorldState:
        """Independent engine snapshot of the current incremental state."""
        return self._base_state.model_copy(update={
            "yard": self._base_state.yard.model_copy(update={
                "stacks": [YardStack(key=k, length_ft=self.ylen[k], boxes=list(v))
                           for k, v in sorted(self.yard.items())],
                "quay_buffer": list(self.buffer)}),
            "ship": self._base_state.ship.model_copy(update={
                "placements": sorted([Placement(container_id=cid, slot=loc[1])
                                      for cid, loc in self.where.items() if loc[0] == "ship"],
                                     key=lambda p: p.slot),
                "covers_off": dict(self.covers_off)})}, deep=True)

    def _validated_commit(self, trial: Expander, moves: list[Move]) -> list[Move]:
        from stowage.frozen import FROZEN, active_stubs
        import stowage.moves as moves_public
        from stowage.moves._physical import Replay

        state = self.world_state()
        # A current public hook takes precedence over the alias imported here.
        # The owner identity is independent of pre-import public replacements;
        # an instrumented owner also requires the conservative public path.
        validate_move = (moves_public.apply_move
                         if moves_public.apply_move is not _NATIVE_APPLY_MOVE else apply_move)
        try:
            if ('moves.apply_move' in active_stubs() or validate_move is not _NATIVE_APPLY_MOVE
                    or getattr(_NATIVE_APPLY_MOVE, '__code__', None)
                    is not _NATIVE_INITIAL_STATE.__code__
                    or getattr(_NATIVE_APPLY_MOVE, '__wrapped__', None)
                    is not FROZEN['moves.apply_move'].func):
                # Preserve frozen dispatch and caller instrumentation exactly.
                for move in moves:
                    state = validate_move(self.instance, state, move)
            else:
                # The trial already contains the successor. Validate its lifts
                # without constructing discarded intermediate world snapshots.
                replay = Replay(self.instance, state)
                for move in moves:
                    replay.move(move)
        except IllegalMoveError as exc:
            raise ValueError(f"unexpandable: {exc.violation.rule_id}: {exc.violation.message}") from exc
        self.__dict__.update(trial.__dict__)
        return moves

    @_in_catalogue_context
    def step(self, action: Action) -> list[Move]:
        """Expand atomically with engine validation when rule freeze v1 is active."""
        if not rule_freeze_v1_enabled():
            return self._step(action)
        trial = self.copy()
        return self._validated_commit(trial, trial._step(action))

    @_in_catalogue_context
    def can_expand(self, action: Action, *,
                   pending_load_targets: Mapping[str, Collection[str]] | None = None) -> bool:
        """Pure mask probe: physical expansion plus Stage A's D69 H-high screen.

        Runs the same clearance search as ``step`` under the same captured
        catalogue, including when a decoder's worker has a different context.
        Optional target domains must be exhaustive eventually viable alternatives,
        including temporarily unavailable slots. Missing domains mean unknown.
        This is not a full checker/feasibility or future-completion certificate.
        Forced hatch clearing, both in this action and possible pending hold
        accesses, is accounted for before interpreting a row as self-blocked.
        Such an escape suppresses this necessary screen, not other mask checks.
        """
        from stowage.actions.space import Place

        trial = self.copy()
        try:
            trial.step(action)
            h = self.instance.port.max_crane_height_tiers
            if (rule_freeze_v1_enabled() and self.instance.stage == "A"
                    and h is not None and isinstance(action, Place)):
                raised = []
                for cell in trial._cells(action.slot):
                    sid, index = trial.cell_pos[cell]
                    if trial.stacks[sid].on_deck and index + 1 >= h:
                        raised.append(sid)
                if raised:
                    addr = parse_slot(action.slot)
                    # Preserve the addressed even bay: the shared helper must
                    # query the union of both long-box sub-bays in one call.
                    if pending_lift_rows(self.instance, trial.world_state(), addr.bay, addr.row,
                                         pending_load_targets=pending_load_targets,
                                         placing_container_id=action.box) and not any(
                            trial._pending_hatch_can_clear(sid, pending_load_targets) for sid in raised):
                        return False
        except (ValueError, IllegalMoveError):
            return False
        return True

    def _pending_hatch_can_clear(self, sid: str,
                                 domains: Mapping[str, Collection[str]] | None) -> bool:
        """A future forced hatch opening may legally lower this deck row.

        Probe only hatch work, allowing eventual targets whose support is not
        yet built. Suppressing a necessary screen on an escape is conservative:
        the mask still has to establish the viability of that target/action.
        All geometry and lift legality remain owned by the move engine.
        """
        cover = self.stacks[sid].hatch_cover_id
        if cover is None:
            return False
        targets = [self.where[cid][1] for cid in self.instance.discharge_list
                   if self.where.get(cid, (None,))[0] == "ship"]
        if domains is not None:
            for cid in (self.load_set | set(self.buffer)):
                if self.where.get(cid, (None,))[0] != "ship":
                    targets.extend(domains.get(cid, ()))
        for slot in sorted(set(targets)):
            cells = self._cells(slot)
            if not self._needs(cells).get(cover, False):
                continue
            probe = self.copy()
            start = len(probe.moves)
            try:
                probe._access(cells)
                state = self.world_state()
                for move in probe.moves[start:]:
                    state = apply_move(self.instance, state, move)
            except (ValueError, IllegalMoveError):
                continue
            h = self.instance.port.max_crane_height_tiers
            remaining_height = max((i + 1 for i, cell in enumerate(self.stacks[sid].cell_ids)
                                    if cell in probe.occ), default=0)
            if h is not None and remaining_height < h:
                return True
        return False

    def _step(self, action: Action) -> list[Move]:
        from stowage.actions.space import Discharge as DischargeA
        from stowage.actions.space import Place, RestowOff as RestowOffA
        from stowage.actions.space import Relocate as RelocateA

        start = len(self.moves)
        if isinstance(action, Place):
            self._place(action.box, action.slot)
        elif isinstance(action, DischargeA):
            self._discharge(action.box, action.to)
        elif isinstance(action, RelocateA):
            self._relocate_explicit(action.box, action.to)
        elif isinstance(action, RestowOffA):
            self._restow_off_with_blockers(action.box)
        else:
            raise ValueError(f"not an action of this space: {action!r}")
        return self.moves[start:]

    @_in_catalogue_context
    def finish(self) -> list[Move]:
        if not rule_freeze_v1_enabled():
            return self._finish()
        trial = self.copy()
        return self._validated_commit(trial, trial._finish())

    def _finish(self) -> list[Move]:
        """Put back every cover still off (departure needs all covers on)."""
        start = len(self.moves)
        while self.covers_off:
            for cover in sorted(self.covers_off):
                if cover not in self.covers_off.values():  # nothing resting on it
                    self._cover_on(cover)
                    break
            else:  # pragma: no cover - a cycle is impossible in a valid ShipState
                raise ValueError("hatch covers rest on each other in a cycle")
        return self.moves[start:]

    def location(self, box: str) -> tuple[str, str] | None:
        return self.where.get(box)

    @_in_catalogue_context
    def yard_target(self, box: str, src_key: str | None, *,
                    avoid_ground: frozenset[tuple[str, int, int]] = frozenset(),
                    avoid_keys: frozenset[str] = frozenset()) -> str | None:
        """The default relocation rule (module docstring): the yard stack key ``box`` goes to, or None if no legal
        position exists. ``src_key`` = the stack it leaves (None for a discharge from the ship)."""
        b = self.boxes[box]
        active = rule_freeze_v1_enabled()
        state = self.world_state() if active else None
        width = 1 if b.length_ft == 20 else 2
        if src_key is not None:
            sa = parse_yard(src_key + "-01")
            blocks = [self.blocks[sa.block]]
            keep_reefer_row = b.reefer and sa.row in self.blocks[sa.block].reefer_rows
        else:
            sa = None
            blocks = [blk for _, blk in sorted(self.blocks.items())]
            keep_reefer_row = b.reefer and any(blk.reefer_rows for blk in blocks)
            if b.dg is not None and any(blk.dg_block for blk in blocks):
                blocks = [blk for blk in blocks if blk.dg_block]
        best: tuple | None = None
        best_key: str | None = None
        candidates = []
        for blk in blocks:
            if b.length_ft not in blk.allowed_lengths_ft:
                continue
            lim = self.limit[blk.id]
            for row in range(1, blk.n_rows + 1):
                if keep_reefer_row and row not in blk.reefer_rows:
                    continue
                for bay in range(1, blk.n_bays - width + 2):
                    key = f"{blk.id}-{bay:03d}-{row:02d}"
                    if key == src_key or key in avoid_keys:
                        continue
                    if key in self.yard:
                        if self.ylen[key] != b.length_ft or len(self.yard[key]) >= lim:
                            continue
                        fresh = 0
                        pend = self.pending[key]
                    else:
                        if any((blk.id, bay + o, row) in self.foot for o in range(width)):
                            continue
                        fresh, pend = 1, 0
                    if active:
                        height = len(self.yard.get(key, [])) + 1
                        blocking_tiers = yard_blocking_tiers(self.instance.port, blk)
                        if height >= blocking_tiers and any(
                                (blk.id, bay + o, row) in avoid_ground for o in range(width)):
                            continue
                        dst = self._yard_addr(key, height)
                        if src_key is not None:
                            move = Relocate(call_index=self.call, container_id=box,
                                            src=self._yard_addr(src_key, len(self.yard[src_key])), dst=dst)
                        else:
                            loc = self.where.get(box)
                            if loc is None or loc[0] != "ship":
                                continue
                            move = Discharge(call_index=self.call, container_id=box, src=loc[1], dst=dst)
                    if sa is not None:
                        dist = (int(bay != sa.bay), abs(bay - sa.bay), abs(row - sa.row))
                    else:
                        dist = (0, 0, 0)
                    score = (int(pend > 0), pend, dist, fresh, blk.id, bay, row)
                    if active:
                        candidates.append((score, key, move))
                        continue
                    if best is None or score < best:
                        best, best_key = score, key
        # Search in the exact legacy preference order, replaying only until the
        # first legal candidate; do not rebuild the engine for worse targets.
        for _, key, move in sorted(candidates):
            try:
                apply_move(self.instance, state, move)
            except IllegalMoveError:
                continue
            return key
        return best_key

    # ------------------------------------------------------------------ yard helpers
    def _claim(self, key: str, length: int) -> None:
        a = parse_yard(key + "-01")
        for o in range(1 if length == 20 else 2):
            self.foot[(a.block, a.bay + o, a.row)] = key

    def _yard_addr(self, key: str, tier: int) -> str:
        a = parse_yard(key + "-01")
        return format_yard(a.block, a.bay, a.row, tier)

    def _yard_pop(self, key: str) -> tuple[str, str]:
        boxes = self.yard[key]
        src = self._yard_addr(key, len(boxes))
        cid = boxes.pop()
        self.pending[key] -= cid in self.load_set
        del self.where[cid]
        if not boxes:
            a = parse_yard(key + "-01")
            for o in range(1 if self.ylen[key] == 20 else 2):
                self.foot.pop((a.block, a.bay + o, a.row), None)
            del self.yard[key], self.ylen[key], self.pending[key]
        return cid, src

    def _yard_push(self, cid: str, key: str) -> str:
        try:
            parse_yard(key + "-01")
        except ValueError as exc:
            raise ValueError(f"not a yard stack key: {key!r}") from exc
        if key not in self.yard:
            self.yard[key], self.ylen[key], self.pending[key] = [], self.boxes[cid].length_ft, 0
            self._claim(key, self.boxes[cid].length_ft)
        self.yard[key].append(cid)
        self.pending[key] += cid in self.load_set
        self.where[cid] = ("yard", key)
        return self._yard_addr(key, len(self.yard[key]))

    def _relocate(self, cid: str, dst_key: str) -> None:
        loc = self.where.get(cid)
        if loc is None or loc[0] != "yard" or self.yard[loc[1]][-1] != cid:
            raise ValueError(f"relocate: {cid} is not on top of a yard stack")
        if dst_key == loc[1]:
            raise ValueError(f"relocate: {cid} already in stack {dst_key}")
        _, src = self._yard_pop(loc[1])
        dst = self._yard_push(cid, dst_key)
        self.moves.append(Relocate(call_index=self.call, container_id=cid, src=src, dst=dst))

    def _relocate_explicit(self, cid: str, dst_key: str) -> None:
        self._relocate(cid, dst_key)

    @_in_catalogue_context
    def _clear_yard_crossings(self, move: Move, protected_key: str) -> None:
        """Lower yard-limit obstacles using shared per-block limits and path geometry."""
        if not rule_freeze_v1_enabled():
            return
        path = crossing_paths(self.instance, move)
        avoid = frozenset(path.yard_ground_slots)
        while True:
            blockers = sorted({self.foot[g] for g in avoid if g in self.foot
                               and len(self.yard[self.foot[g]]) >= yard_blocking_tiers(self.instance.port, g[0])})
            if not blockers:
                return
            for key in blockers:
                top = self.yard[key][-1]
                target = self.yard_target(top, key, avoid_ground=avoid,
                                          avoid_keys=frozenset({protected_key}))
                if target is not None:
                    self._relocate(top, target)
                    break
            else:
                raise ValueError(f"no legal yard position to clear crossing stacks {blockers}")

    @_in_catalogue_context
    def _dig_out(self, cid: str, slot: str | None = None) -> tuple[str, str]:
        """Relocate the yard boxes above ``cid`` by the default rule; return (stack key, source address)."""
        key = self.where[cid][1]
        boxes = self.yard[key]
        avoid = frozenset()
        if slot is not None and rule_freeze_v1_enabled():
            # The source's vertical blockers may themselves be trapped behind an
            # yard-limit lane obstacle. Lower those obstacles before the first lift.
            lift = Load(call_index=self.call, container_id=cid,
                        src=self._yard_addr(key, boxes.index(cid) + 1), dst=slot)
            self._clear_yard_crossings(lift, key)
            avoid = frozenset(crossing_paths(self.instance, lift).yard_ground_slots)
        while boxes[-1] != cid:
            top = boxes[-1]
            target = self.yard_target(top, key, avoid_ground=avoid)
            if target is None:
                raise ValueError(f"no legal yard position to relocate {top} (blocking {cid})")
            self._relocate(top, target)
        src = self._yard_addr(key, len(boxes))
        if slot is not None:
            self._clear_yard_crossings(Load(call_index=self.call, container_id=cid, src=src, dst=slot), key)
        return key, src

    # ------------------------------------------------------------------ ship helpers
    def _cells(self, slot: str) -> tuple[str, ...]:
        try:
            cells = cells_of_slot(slot)
        except ValueError as exc:
            raise ValueError(f"not a ship slot: {slot!r}") from exc
        if any(c not in self.cells for c in cells):
            raise ValueError(f"slot {slot} is not a cell of this vessel")
        return cells

    def _above(self, roots: Iterable[str]) -> set[str]:
        """Boxes that must come off before every box in ``roots`` is liftable (upward closure over cells)."""
        todo, seen = list(roots), set()
        while todo:
            cid = todo.pop()
            for cell in cells_of_slot(self.where[cid][1]):
                sid, i = self.cell_pos[cell]
                for upper in self.stacks[sid].cell_ids[i + 1:]:
                    occ = self.occ.get(upper)
                    if occ is not None and occ not in seen:
                        seen.add(occ)
                        todo.append(occ)
        return seen - set(roots)

    def _clear(self, blockers: set[str]) -> None:
        """Restow-off ``blockers`` in canonical order: repeatedly the free box (nothing above it within the set)
        with the highest (tier, slot)."""
        blockers = set(blockers)
        while blockers:
            free = [b for b in blockers if not (self._above([b]) & blockers)]
            if not free:  # pragma: no cover - stacks are acyclic
                raise ValueError("cannot order ship blockers")
            box = max(free, key=lambda b: (parse_slot(self.where[b][1]).tier, self.where[b][1]))
            self._restow_off(box)
            blockers.discard(box)

    def _restow_off(self, cid: str) -> None:
        slot = self.where[cid][1]
        self._access(self._cells(slot))
        self._ship_remove(cid)
        self.buffer.append(cid)
        self.where[cid] = ("quay", "quay")
        self.moves.append(RestowOff(call_index=self.call, container_id=cid, src=slot))

    def _restow_off_with_blockers(self, cid: str) -> None:
        loc = self.where.get(cid)
        if loc is None or loc[0] != "ship":
            raise ValueError(f"restow_off: {cid} is not on the ship")
        self._clear(self._above([cid]))
        self._restow_off(cid)

    def _ship_remove(self, cid: str) -> str:
        slot = self.where.pop(cid)[1]
        for cell in cells_of_slot(slot):
            del self.occ[cell]
        return slot

    def _ship_add(self, cid: str, slot: str) -> None:
        for cell in cells_of_slot(slot):
            self.occ[cell] = cid
        self.where[cid] = ("ship", slot)

    # ------------------------------------------------------------------ hatch covers
    def _cover_of(self, cell: str) -> tuple[str | None, bool]:
        stack = self.stacks[self.cell_pos[cell][0]]
        return stack.hatch_cover_id, stack.on_deck

    def _needs(self, cells: Iterable[str]) -> dict[str, bool]:
        """cover → must be off (True: hold access) / on (False: deck access) to reach ``cells``."""
        need: dict[str, bool] = {}
        for cell in cells:
            cover, deck = self._cover_of(cell)
            if cover is None:
                continue
            want_off = not deck
            if need.setdefault(cover, want_off) != want_off:
                raise ValueError(f"cells {sorted(cells)} need hatch cover {cover} both open and closed")
        return need

    @_in_catalogue_context
    def _access(self, cells: Iterable[str], keep: dict[str, bool] | None = None) -> None:
        """Lazily open/close covers so ``cells`` are reachable; ``keep`` = states another end of the same lift
        needs (a conflict is unexpandable)."""
        need = self._needs(cells)
        for cover, want_off in sorted(need.items()):
            if keep is not None and keep.get(cover, want_off) != want_off:
                raise ValueError(f"one lift needs hatch cover {cover} both open and closed")
            # a cover resting on this one must go back to its own hatch first
            for other, rest in sorted(self.covers_off.items()):
                if rest == cover:
                    self._cover_on(other)
            if want_off and cover not in self.covers_off:
                deck = {self.occ[c] for sid in self.covers[cover].deck_stack_ids
                        for c in self.stacks[sid].cell_ids if c in self.occ}
                if deck:
                    self._clear(deck | self._above(deck))
                self.covers_off[cover] = "quay"
                self.moves.append(HatchCoverOff(call_index=self.call, cover_id=cover, dst="quay"))
            elif not want_off and cover in self.covers_off:
                self._cover_on(cover)

    def _cover_on(self, cover: str) -> None:
        for other, rest in sorted(self.covers_off.items()):
            if rest == cover:
                self._cover_on(other)
        del self.covers_off[cover]
        self.moves.append(HatchCoverOn(call_index=self.call, cover_id=cover))

    # ------------------------------------------------------------------ actions
    def _place(self, cid: str, slot: str) -> None:
        loc = self.where.get(cid)
        if (loc is None and rule_freeze_v1_enabled() and cid in self.load_set
                and self.boxes[cid].direct_delivery):
            loc = ("truck", "truck")
        if loc is None:
            raise ValueError(f"place: unknown or unlocated container {cid}")
        dst_cells = self._cells(slot)
        # Check the entire footprint before dig-out, cover access, or source removal.
        # Otherwise a second placement overwrites occ while both where entries survive.
        # A ship shift may retain its own cells, as in the move engine's removing=cid.
        occupied = [cell for cell in dst_cells if self.occ.get(cell) not in (None, cid)]
        if occupied:
            raise ValueError(f"place: destination {slot} has occupied cells {occupied}")
        if rule_freeze_v1_enabled() and self.instance.stage == "A" and loc[0] == "ship":
            # D69: Place already expresses a one-lift ship shift, but Stage A's
            # sequencing screen is relative to a domain that excludes it.
            raise ValueError("place: Stage A excludes shifting a container already aboard")
        if loc[0] == "truck":
            # APPROXIMATION: trucks are available at lift time, with sequencing
            # position unconstrained (optimistic for class-1 cargo); no timing model.
            self._access(dst_cells)
            self._ship_add(cid, slot)
            self.moves.append(Load(call_index=self.call, container_id=cid, src="truck", dst=slot))
        elif loc[0] == "yard":
            key, src = self._dig_out(cid, slot)
            self._access(dst_cells)
            self._yard_pop(key)
            self._ship_add(cid, slot)
            self.moves.append(Load(call_index=self.call, container_id=cid, src=src, dst=slot))
        elif loc[0] == "quay":
            self._access(dst_cells)
            self.buffer.remove(cid)
            del self.where[cid]
            self._ship_add(cid, slot)
            self.moves.append(RestowOn(call_index=self.call, container_id=cid, dst=slot))
        else:
            src = loc[1]
            src_cells = self._cells(src)
            self._clear(self._above([cid]))
            self._access(src_cells)
            self._access(dst_cells, keep=self._needs(src_cells))
            self._ship_remove(cid)
            self._ship_add(cid, slot)
            self.moves.append(RestowShift(call_index=self.call, container_id=cid, src=src, dst=slot))

    def _discharge(self, cid: str, to: str | None) -> None:
        loc = self.where.get(cid)
        if loc is None or loc[0] != "ship":
            raise ValueError(f"discharge: {cid} is not on the ship")
        src = loc[1]
        self._clear(self._above([cid]))
        self._access(self._cells(src))
        if rule_freeze_v1_enabled() and self.boxes[cid].direct_delivery:
            if to not in (None, "truck"):
                raise ValueError("direct-delivery discharge must go to truck")
            self._ship_remove(cid)
            self.moves.append(Discharge(call_index=self.call, container_id=cid, src=src, dst="truck"))
            return
        if to is None:
            to = self.yard_target(cid, None)
            if to is None:
                raise ValueError(f"no legal yard position to discharge {cid}")
        self._ship_remove(cid)
        dst = self._yard_push(cid, to)
        self.moves.append(Discharge(call_index=self.call, container_id=cid, src=src, dst=dst))
