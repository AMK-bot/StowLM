"""Incremental feasibility engine v2 (plan 4.4); guarantees and semantics in ``engine.py``.

Layout: ``Tables`` (static, shared) + one immutable ``_State`` per pushed prefix (copy-on-write numpy arrays and an
``Expander``), kept on a history stack, so ``pop`` is O(1) and ``copy`` shares every snapshot.

Per state, ``legal_actions`` evaluates all (remaining box × open slot) pairs at once with numpy:
    L1  open slot (next free cell of each supporting stack), crane reach, footprint, no 20' on 40', the box can be
        lifted (yard blockers have legal default relocation targets: counted, with an exact trial when tight);
    L2  2.4 rules: static mask (tables.py) + stack weight (E7, mixed/20/40 limit), stack height, E2 weight order,
        E1 heavy-over-nonheavy, top-stowage / over-height boxes below, inherited hard violations (stage B), IMDG
        segregation against every DG box aboard (2.4's ``_pair``, memoised), and for OOG / away-from-heat boxes and
        slots near OOG cargo an exact call of 2.4's ``placement_violations`` (cached on stack versions);
    C1  capacity (capacity.py), C2 global LP screen (global_bounds.py).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from time import perf_counter

import numpy as np

from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.frozen import current_provenance
from stowage.model import Placement

from .capacity import ClassFlow, NestedHall
from stowage.feasibility.engine import DeadEnd, FeasibilityStats, IllegalActionError
from .global_bounds import GLOBAL_RULE_IDS, GlobalScreen
from .tables import Tables, build_tables, class_params, class_rank

_EPS = 1e-6
_NORANK = 1 << 20


class _Recorder(dict):
    """A cells mapping that records whether it was read (DG pair geometry detection)."""

    def __init__(self, base):
        super().__init__(base)
        self.read = False

    def __getitem__(self, key):
        self.read = True
        return super().__getitem__(key)

    def __contains__(self, key):
        self.read = True
        return super().__contains__(key)


@dataclass
class _State:
    ex: Expander
    fill: np.ndarray        # (J,) occupied cells per stack
    smass: np.ndarray       # (J,) kg (share per stack)
    sh: np.ndarray          # (J,) m (incl. over-height)
    n20: np.ndarray         # (J,)
    nlong: np.ndarray       # (J,)
    lowrank: np.ndarray     # (J,) lowest D25 class rank of the valid-mass boxes in the stack, _NORANK if none
    nonheavy: np.ndarray    # (J,) bool: a box lighter than the E1 threshold is in the stack
    closed: np.ndarray      # (J,) bool: top-stowage / over-height box in the stack
    poisoned: np.ndarray    # (J,) bool: inherited hard violation (stage B)
    ver: np.ndarray         # (J,) int stack versions
    rem: np.ndarray         # (B,) bool: placeable box not aboard (yard load box or quay buffer)
    dg_aboard: tuple        # ((container_id, slot), ...) sorted
    ref: object = None      # global_bounds.Reference or None
    c2_dead: bool = False
    action: object = None
    # per-state caches
    legal: tuple | None = None
    mask: np.ndarray | None = None  # sorted flat indices box*S + slot of legal Place actions
    discharges: tuple = ()
    dead: DeadEnd | None = None
    c2_sols: dict = field(default_factory=dict)


class IncrementalEngine:
    def __init__(self, instance, space, config):
        if space.name != "boxslot":
            raise ValueError("only the boxslot action space is supported")
        if not math.isfinite(config.stability_margin_scale) or config.stability_margin_scale < 1:
            raise ValueError("stability_margin_scale must be finite and >= 1")
        if config.lookahead < 0:
            raise ValueError("lookahead must be nonnegative")
        self.instance, self.space, self.config = instance, space, config
        t0 = perf_counter()
        self.T: Tables = build_tables(instance)
        T = self.T
        self._lp_budget = int(config.extra.get("lp_budget", 2))
        self._recenter_below = float(config.extra.get("recenter_below", 0.5))
        # diagnostic only: 0 = list L1∧L2 actions without the completion screens (tests compare with 2.4/2.3)
        self._screens = bool(config.extra.get("completion_screens", 1))
        self.diagnostics = dict(construct_s=0.0, lp_calls=0, lp_time_s=0.0, c2_unresolved=0, c2_slot_lps=0,
                                recenters=0, exact_calls=0, dig_trials=0, c1a_flows=0, global_screen="off")
        self._stats = FeasibilityStats()
        # every container's attributes (incl. discharge boxes aboard)
        cp = class_params(instance)
        self._attr = {b.id: (float(b.gross_mass_kg), 1 if b.length_ft == 20 else 2,
                             b.height_m + (b.oog.over_height_m if b.oog else 0.0), b.length_ft,
                             "top_stowage" in b.special_stowage or bool(b.oog and b.oog.over_height_m),
                             math.isfinite(b.gross_mass_kg) and b.gross_mass_kg > 0 and b.gross_mass_kg >= b.tare_kg,
                             b.dg is not None, b.oog is not None, class_rank(cp, float(b.gross_mass_kg)))
                      for b in instance.containers}
        self._boxes = {b.id: b for b in instance.containers}
        self._arrival = {(p.container_id, p.slot) for p in instance.onboard.placements}
        # stack reach (deck tiers the cranes can serve), for capacity counts
        J = len(T.stack_ids)
        port_limit = instance.port.quay_crane.max_on_deck_tiers
        lim = min(port_limit, T.crane_limit) if T.crane_limit is not None else 0
        self._reach = np.array([len(c) if not T.stack_deck[j] else min(len(c), int(lim))
                                for j, c in enumerate(T.stack_cells)])
        # C1a static grouping: box may use a cell if some statically allowed slot contains it
        S, C = len(T.slot_ids), len(T.cell_ids)
        inc = np.zeros((S, C), dtype=bool)
        for n in range(2):
            ok = T.slot_cells[:, n] >= 0
            inc[np.flatnonzero(ok), T.slot_cells[ok, n]] = True
        self._cellcompat = (T.static.astype(np.int32) @ inc.astype(np.int32)) > 0
        self._flow = ClassFlow(self._cellcompat) if len(T.box_ids) else None
        # C1c: stack pairs a long box can span (from the 40'/45' slots)
        long_slots = T.slot_n == 2
        pairs = sorted({tuple(sorted(p)) for p in T.slot_stacks[long_slots].tolist()})
        self._pairs = np.array(pairs, dtype=np.int64).reshape(-1, 2)
        H = max([len(c) for c in T.stack_cells] + [1])
        self._stack_cells_pad = np.full((J, H), -1, dtype=np.int64)
        for j, c in enumerate(T.stack_cells):
            self._stack_cells_pad[j, :len(c)] = c
        member = [[] for _ in range(J)]
        for pi, (a, b) in enumerate(pairs):
            member[a].append(pi)
            member[b].append(pi)
        width = max([len(m) for m in member] + [1])
        self._member = np.full((J, width), -1, dtype=np.int64)
        for j, m in enumerate(member):
            self._member[j, :len(m)] = m
        # Place object cache, flat index box*S + slot
        self._objs = np.empty(len(T.box_ids) * len(T.slot_ids), dtype=object)
        self._have = np.zeros(len(T.box_ids) * len(T.slot_ids), dtype=bool)
        # DG pair memo: (dg_class, slot_index, other_id, other_slot) -> ok; class-pair kinds
        self._pair_memo: dict = {}
        self._pair_kind: dict = {}
        self._dg_rep = {}
        for bi in np.flatnonzero(T.is_dg):
            self._dg_rep.setdefault(int(T.dg_class[bi]), T.box_ids[bi])
        # exact fallback cache
        self._exact_memo: dict = {}
        self._slot_xy = self._slot_centres()
        self._oog_zone = self._oog_zones() if any(self._attr[b][7] for b in self._attr) else None
        # C2
        self._global: GlobalScreen | None = None
        self._global_error: str | None = None
        mode = config.extra.get("global_screen", 1)
        if mode and instance.vessel.hull is not None:
            try:
                self._global = GlobalScreen.from_instance(instance, T, margin_scale=config.stability_margin_scale)
                self.diagnostics["global_screen"] = "on"
            except (ValueError, KeyError) as exc:
                # A real hull whose linear model can't be built fails closed: C2 can't be shown to hold.
                self._global_error = str(exc)
                self.diagnostics["global_screen"] = f"unavailable: {exc}"
        elif instance.vessel.hull is None:
            self.diagnostics["global_screen"] = "off: vessel has no hull (fixture)"
        else:
            self.diagnostics["global_screen"] = "off: config extra['global_screen']=0 (diagnostic)"
        root = self._root_state()
        self._history: list[_State] = []
        self._cur = root
        self.diagnostics["construct_s"] = perf_counter() - t0

    # ================================================================== construction helpers
    def _slot_centres(self):
        T = self.T
        cellmap = {c.id: c for c in self.instance.vessel.cells}
        xy = np.zeros((len(T.slot_ids), 2))
        for i, s in enumerate(T.slot_ids):
            cs = [cellmap[c] for c in cells_of_slot(s)]
            xy[i] = (sum(c.lcg_m for c in cs) / len(cs), sum(c.tcg_m for c in cs) / len(cs))
        return xy

    def _oog_zones(self):
        """slot → stacks whose contents can matter for OOG clearance at that slot (generous radius)."""
        from stowage.checker.rules_special import _nonnegative, _parameter

        clearance = _nonnegative(_parameter(self.instance, "expert.e9.over_width_length", "oog_clearance",
                                            "minimum_clearance_m")) or 0.0
        over = max((max(b.oog.over_width_port_m, b.oog.over_width_starboard_m, b.oog.over_length_fore_m,
                        b.oog.over_length_aft_m) for b in self.instance.containers if b.oog), default=0.0)
        radius = clearance + 2 * over + 13.716 + 2.438
        T = self.T
        d = np.hypot(self._slot_xy[:, None, 0] - self._slot_xy[None, :, 0],
                     self._slot_xy[:, None, 1] - self._slot_xy[None, :, 1])
        zones = []
        for i in range(len(T.slot_ids)):
            near = np.flatnonzero(d[i] <= radius)
            st = set(T.slot_stacks[near][T.slot_stacks[near] >= 0].tolist())
            zones.append(tuple(sorted(st)))
        return zones

    def _root_state(self) -> _State:
        T = self.T
        J = len(T.stack_ids)
        ex = Expander(self.instance)
        st = _State(ex=ex, fill=np.zeros(J, dtype=np.int64), smass=np.zeros(J), sh=np.zeros(J),
                    n20=np.zeros(J, dtype=np.int64), nlong=np.zeros(J, dtype=np.int64), lowrank=np.full(J, _NORANK, dtype=np.int64),
                    nonheavy=np.zeros(J, dtype=bool), closed=np.zeros(J, dtype=bool),
                    poisoned=np.zeros(J, dtype=bool), ver=np.zeros(J, dtype=np.int64),
                    rem=np.zeros(len(T.box_ids), dtype=bool), dg_aboard=())
        self._restack(st, range(J))
        st.rem = self._remaining(ex)
        st.dg_aboard = self._dg_aboard(ex)
        if self._global is not None:
            fixed, free, R = self._global_state(ex)
            st.ref = self._global.reference(fixed, free, R)
            st.c2_dead = st.ref is None
            self._sync_lp()
        return st

    # ================================================================== state maintenance
    def _remaining(self, ex) -> np.ndarray:
        T = self.T
        rem = np.zeros(len(T.box_ids), dtype=bool)
        for i, b in enumerate(T.box_ids):
            loc = ex.where.get(b)
            rem[i] = loc is not None and loc[0] in ("yard", "quay")
        return rem

    def _dg_aboard(self, ex) -> tuple:
        return tuple(sorted((cid, loc[1]) for cid, loc in ex.where.items()
                            if loc[0] == "ship" and self._attr[cid][6]))

    def _restack(self, st: _State, stacks) -> None:
        """Recompute per-stack aggregates in place (``st`` arrays must be private to the new state)."""
        T, ex = self.T, st.ex
        e1 = T.e1_threshold
        stage_b = self.instance.stage == "B"
        for j in stacks:
            fill = 0
            mass = h = 0.0
            n20 = nlong = 0
            low = _NORANK
            nonheavy = closed = False
            arrival_here = []
            for ci in T.stack_cells[j]:
                cid = ex.occ.get(T.cell_ids[ci])
                if cid is None:
                    break
                fill += 1
                m, u, hh, L, closes, valid, _, _, rk = self._attr[cid]
                mass += m / u
                h += hh
                n20 += L == 20
                nlong += L != 20
                if valid:
                    low = min(low, rk)
                    if e1 is not None and m < e1:
                        nonheavy = True
                closed |= closes
                if stage_b and (cid, ex.where[cid][1]) in self._arrival:
                    arrival_here.append(cid)
            st.fill[j], st.smass[j], st.sh[j] = fill, mass, h
            st.n20[j], st.nlong[j], st.lowrank[j] = n20, nlong, low
            st.nonheavy[j], st.closed[j] = nonheavy, closed
            st.ver[j] += 1
            st.poisoned[j] = bool(arrival_here) and self._inherited_hard(ex, j)

    def _inherited_hard(self, ex, j) -> bool:
        """Stage B: does stack ``j`` (as it stands) already carry a hard 2.4 violation? Exact ``_core`` call."""
        from stowage.checker.rules import _core

        T = self.T
        placements = []
        for ci in T.stack_cells[j]:
            cid = ex.occ.get(T.cell_ids[ci])
            if cid is not None and Placement.model_construct(slot=ex.where[cid][1], container_id=cid) not in placements:
                placements.append(Placement.model_construct(slot=ex.where[cid][1], container_id=cid))
        ship = self.instance.onboard.model_copy(update={"placements": placements})
        violations, _ = _core(self.instance, ship, self.instance.call_index)
        return any(v.severity == "hard" for v in violations)

    def _global_state(self, ex):
        """Departure-model view: fixed cell masses (boxes aboard that stay), free cells, remaining mass."""
        T = self.T
        C = len(T.cell_ids)
        fixed = np.zeros(C)
        free = np.ones(C, dtype=bool)
        for cid, loc in ex.where.items():
            if loc[0] != "ship" or cid in T.discharge:
                continue
            cells = [T.cell_index[c] for c in cells_of_slot(loc[1])]
            m, u = self._attr[cid][0], self._attr[cid][1]
            fixed[cells] += m / u
            free[cells] = False
        R = float(sum(self._attr[T.box_ids[i]][0] for i in np.flatnonzero(self._remaining(ex))))
        return fixed, free, R

    def _sync_lp(self):
        g = self._global
        if g is not None:
            self.diagnostics["lp_calls"] = g.lp_calls
            self.diagnostics["lp_time_s"] = g.lp_time_s

    # ================================================================== candidate evaluation
    def _open_slots(self, st: _State) -> np.ndarray:
        T = self.T
        pad = T.slot_cells < 0
        fill = st.fill[np.maximum(T.slot_stacks, 0)]
        ok = np.all(pad | (fill == T.slot_k), axis=1)
        return np.flatnonzero(ok)

    def _liftable(self, st: _State) -> np.ndarray:
        """Remaining boxes whose expander source step can succeed (quay: always; yard: blockers placeable)."""
        T, ex = self.T, st.ex
        out = st.rem.copy()
        room: dict[tuple[str, int], int] = {}
        fresh: dict[tuple[str, int], bool] = {}
        for i in np.flatnonzero(st.rem):
            loc = ex.where[T.box_ids[i]]
            if loc[0] != "yard":
                continue
            stack = ex.yard[loc[1]]
            k = len(stack) - 1 - stack.index(T.box_ids[i])
            if k == 0:
                continue
            block = loc[1].split("-", 1)[0]
            L = ex.ylen[loc[1]]
            key = (block, L)
            if key not in room:
                room[key], fresh[key] = self._yard_room(ex, block, L)
            lim = ex.limit[block]
            own = lim - len(stack)
            blockers = stack[len(stack) - k:]
            restricted = any(ex.boxes[b].reefer for b in blockers) and \
                int(loc[1].split("-")[2]) in ex.blocks[block].reefer_rows
            if not restricted and (room[key] - own >= k or (fresh[key] and lim >= k)):
                continue
            self.diagnostics["dig_trials"] += 1
            try:
                ex.copy()._dig_out(T.box_ids[i])
            except ValueError:
                out[i] = False
        return out

    @staticmethod
    def _yard_room(ex, block, L):
        room = sum(ex.limit[block] - len(v) for k, v in ex.yard.items()
                   if ex.ylen[k] == L and k.startswith(block + "-"))
        blk = ex.blocks[block]
        w = 1 if L == 20 else 2
        fresh = False
        if L in blk.allowed_lengths_ft:
            for row in range(1, blk.n_rows + 1):
                for bay in range(1, blk.n_bays - w + 2):
                    if all((block, bay + o, row) not in ex.foot for o in range(w)):
                        fresh = True
                        break
                if fresh:
                    break
        return room, fresh

    def _slot_features(self, st: _State, O: np.ndarray):
        """Per open slot: mass room, height room, E2 cap, E1 flag, bad flag (vectorised)."""
        T = self.T
        stk = T.slot_stacks[O]
        pad = stk < 0
        j = np.maximum(stk, 0)
        is20 = T.slot_n[O] == 1
        lim = np.where(is20[:, None], np.where(st.nlong[j] == 0, T.stack_lim_20[j], T.stack_lim_mixed[j]),
                       np.where(st.n20[j] == 0, T.stack_lim_40[j], T.stack_lim_mixed[j]))
        room = np.min(np.where(pad, np.inf, lim - st.smass[j]), axis=1)
        hroom = np.min(np.where(pad, np.inf, T.stack_hmax[j] - st.sh[j]), axis=1)
        nonempty = st.fill[j] > 0
        # E2 (D25): a box may go on top iff its class rank ≤ the stack's lowest rank + tolerance
        if T.e2_valid:
            caprank = np.where(nonempty, st.lowrank[j] + T.e2_tol, _NORANK)
        else:
            caprank = np.where(nonempty, -1, _NORANK)      # missing E2 data blocks any stacked placement
        cap2 = np.min(np.where(pad, _NORANK, caprank), axis=1)
        e1 = T.e1_threshold
        if e1 is not None:
            nh = np.any(~pad & st.nonheavy[j], axis=1)
            e1bad = np.zeros(len(O), dtype=bool)
        else:
            nh = np.zeros(len(O), dtype=bool)
            e1bad = np.any(~pad & nonempty, axis=1)      # missing E1 data blocks any stacked placement
        bad = np.any(~pad & (st.closed[j] | st.poisoned[j]), axis=1)
        bad |= is20 & np.any(~pad & (st.nlong[j] > 0), axis=1)
        bad |= e1bad
        return room, hroom, cap2, nh, bad

    def _evaluate(self, st: _State) -> None:
        T = self.T
        st.mask = np.zeros(0, dtype=np.int64)
        disch = self._discharge_candidates(st)
        st.discharges = disch
        reasons: list[str] = []
        if st.c2_dead and self._screens:
            st.legal = ()
            st.dead = DeadEnd(self.depth, "No fractional completion meets the linear stability/strength rows.",
                              GLOBAL_RULE_IDS)
            return
        if self._global_error is not None:
            st.legal = ()
            st.dead = DeadEnd(self.depth, f"Global stability model unavailable: {self._global_error}",
                              ("checker.missing_vessel_data",))
            return
        O = self._open_slots(st)
        boxes = np.flatnonzero(self._liftable(st))
        if len(O) == 0 or len(boxes) == 0:
            st.legal = tuple(disch)
            if not st.legal and not self._complete(st):
                st.dead = DeadEnd(self.depth, "No remaining box can be lifted into an open slot.",
                                  ("expert.moves.top_access",))
            return
        room, hroom, cap2, nh, bad = self._slot_features(st, O)
        M = T.static[np.ix_(boxes, O)]
        M &= ~bad[None, :]
        mb = T.mass[boxes][:, None]
        M &= T.share[boxes][:, None] <= room[None, :] + _EPS
        M &= T.height[boxes][:, None] <= hroom[None, :] + 1e-9
        M &= T.rank[boxes][:, None] <= cap2[None, :]
        if T.e1_threshold is not None:
            M &= ~((mb >= T.e1_threshold) & nh[None, :])
        bi, oi = np.nonzero(M)
        b_idx, s_idx = boxes[bi], O[oi]
        keep = np.ones(len(b_idx), dtype=bool)
        # DG segregation against DG cargo aboard
        if st.dg_aboard:
            dgsel = np.flatnonzero(T.is_dg[b_idx])
            if len(dgsel):
                keep[dgsel] = [self._dg_ok(int(T.dg_class[b_idx[k]]), int(s_idx[k]), st.dg_aboard) for k in dgsel]
        # exact 2.4 call: fallback boxes, and every candidate near OOG cargo aboard
        # E8/E9 envelope rules (OOG clearance, over-height envelope): 2.4's special_violations on a minimal ship
        # (candidate + boxes whose envelopes could reach it + the boxes under each), for OOG candidates and for
        # ordinary candidates near OOG cargo aboard; all other rules come from the fast path above
        exact = np.zeros(len(b_idx), dtype=bool)
        if self._oog_zone is not None:
            aboard = [(cid, loc[1]) for cid, loc in st.ex.where.items() if loc[0] == "ship"]
            oog_aboard = [x for x in aboard if self._attr[x[0]][7]]
            geo = self._geometry(aboard) if aboard else None
            for k in np.flatnonzero(keep & T.fallback[b_idx]).tolist():
                keep[k] = self._special_ok(st, int(b_idx[k]), int(s_idx[k]), geo)
            if oog_aboard:
                near_slots = self._near_oog(oog_aboard)
                ogeo = self._geometry(oog_aboard)
                for k in np.flatnonzero(keep & ~T.fallback[b_idx] & np.isin(s_idx, near_slots)).tolist():
                    keep[k] = self._special_ok(st, int(b_idx[k]), int(s_idx[k]), ogeo)
        heat = np.flatnonzero(T.heat[b_idx] & keep & ~exact)
        for k in heat.tolist():
            keep[k] = self._heat_ok(st, int(b_idx[k]), int(s_idx[k]))
        if exact.any():
            ship = None
            for k in np.flatnonzero(exact & keep):
                ship = ship or self._ship(st)
                keep[k] = self._exact_ok(st, ship, int(b_idx[k]), int(s_idx[k]))
        b_idx, s_idx = b_idx[keep], s_idx[keep]
        if len(b_idx) and self._screens:
            ok = self._c1(st, b_idx, s_idx)
            if not ok.all():
                reasons.append("capacity")
            b_idx, s_idx = b_idx[ok], s_idx[ok]
        if len(b_idx) and st.ref is not None and self._screens:
            ok = self._c2(st, b_idx, s_idx)
            if not ok.all():
                reasons.append("global")
            b_idx, s_idx = b_idx[ok], s_idx[ok]
        # sort_key order (P, box id, slot id): box and slot indices are id-sorted, so the flat index sorts right
        flat = np.sort(b_idx * len(T.slot_ids) + s_idx)
        nS = len(T.slot_ids)
        for f in flat[~self._have[flat]].tolist():
            self._objs[f] = Place(T.box_ids[f // nS], T.slot_ids[f % nS])
        self._have[flat] = True
        objs = self._objs[flat]
        st.mask = flat
        st.legal = tuple(objs.tolist()) + tuple(disch)
        if not st.legal and not self._complete(st):
            # name the completion conditions that rejected candidates, with the catalogue rules behind each
            rules = {"c1a": ("expert.physical.size_fit", "expert.physical.slot_occupancy"),
                     "c1b": ("expert.e1.heavy_bottom", "expert.e2.weight_order"),
                     "c1c": ("expert.physical.size_fit", "expert.physical.slot_occupancy"),
                     "c1d": ("expert.physical.stack_weight",),
                     "c1e": ("expert.e2.weight_order", "expert.physical.size_fit"),
                     "c1f": ("expert.e6.heavy_under_deck",),
                     "c2": GLOBAL_RULE_IDS}
            fails = {k: v for k, v in (getattr(self, "_c1_fails", {}) if "capacity" in reasons else {}).items() if v}
            if "global" in reasons:
                fails["c2"] = fails.get("c2", 0) or 1
            ids = tuple(sorted({r for k in fails for r in rules[k]}))
            what = ", ".join(f"{k} rejected {v}" for k, v in sorted(fails.items()))
            st.dead = DeadEnd(self.depth, "Every candidate breaks a placement rule or a completion bound"
                              + (f" ({what})." if what else " (L1/L2: no box fits any open slot)."), ids)

    # ------------------------------------------------------------------ DG pairs
    def _dg_ok(self, cls: int, s: int, aboard: tuple) -> bool:
        from stowage.checker.rules_imdg import _pair

        T = self.T
        rep = self._boxes[self._dg_rep[cls]]
        slot = T.slot_ids[s]
        for other_id, other_slot in aboard:
            other = self._boxes[other_id]
            kind = self._pair_kind.get((cls, other_id))
            if kind is None:
                rec = _Recorder({c.id: c for c in self.instance.vessel.cells})
                deck = [c for c in T.slot_ids if parse_slot(c).on_deck] or [T.slot_ids[0]]
                out = _pair(self.instance, rep, deck[0], other, deck[-1], self.instance.call_index, rec)
                kind = ("geo", None) if rec.read else ("const", not any(v.severity == "hard" for v in out))
                self._pair_kind[(cls, other_id)] = kind
            if kind[0] == "const":
                if not kind[1]:
                    return False
                continue
            key = (cls, s, other_id, other_slot)
            ok = self._pair_memo.get(key)
            if ok is None:
                cellmap = {c.id: c for c in self.instance.vessel.cells}
                out = _pair(self.instance, rep, slot, other, other_slot, self.instance.call_index, cellmap)
                ok = self._pair_memo[key] = not any(v.severity == "hard" for v in out)
            if not ok:
                return False
        return True

    # ------------------------------------------------------------------ away-from-heat (E8)
    def _heat_ok(self, st: _State, b: int, s: int) -> bool:
        """2.4's heat-clearance check for the candidate, exactly (``special_violations`` focused on it), on a ship
        holding only the candidate and the boxes under it — the clearance depends on nothing else (the floor
        height comes from the boxes below). Cached on the floors."""
        from stowage.checker.rules_special import special_violations

        T, ex = self.T, st.ex
        below = []
        for n in range(2):
            j = int(T.slot_stacks[s, n])
            if j < 0:
                continue
            for ci in T.stack_cells[j][:int(st.fill[j])]:
                cid = ex.occ[T.cell_ids[ci]]
                if cid not in below:
                    below.append(cid)
        key = ("heat", b, s, tuple(sorted((ex.where[c][1], self._boxes[c].height_m) for c in below)))
        ok = self._exact_memo.get(key)
        if ok is None:
            self.diagnostics["exact_calls"] += 1
            placements = [Placement.model_construct(slot=ex.where[c][1], container_id=c) for c in below]
            placements.append(Placement.model_construct(slot=T.slot_ids[s], container_id=T.box_ids[b]))
            ship = self.instance.onboard.model_copy(update={"placements": placements, "covers_off": {}})
            vs = special_violations(self.instance, ship, call_index=self.instance.call_index,
                                    focus_container_id=T.box_ids[b])
            ok = self._exact_memo[key] = not any(v.severity == "hard" and T.box_ids[b] in v.containers for v in vs)
        return ok

    # ------------------------------------------------------------------ exact fallback
    def _ship(self, st: _State):
        placements = [Placement.model_construct(slot=loc[1], container_id=cid)
                      for cid, loc in sorted(st.ex.where.items()) if loc[0] == "ship"]
        return self.instance.onboard.model_copy(update={"placements": placements, "covers_off": {}})

    def _near_oog(self, oog_aboard) -> np.ndarray:
        """Slots whose OOG zone contains a stack holding OOG cargo."""
        T = self.T
        key = tuple(sorted(oog_aboard))
        hit = self._exact_memo.get(("near", key))
        if hit is None:
            stacks = set()
            for _, slot in oog_aboard:
                si = T.slot_index.get(slot)
                if si is not None:
                    stacks |= set(T.slot_stacks[si][T.slot_stacks[si] >= 0].tolist())
            hit = np.array([s for s in range(len(T.slot_ids)) if stacks & set(self._oog_zone[s])], dtype=np.int64)
            self._exact_memo[("near", key)] = hit
        return hit

    def _below(self, ex, slot) -> tuple:
        """(slot, box) of every box under ``slot`` in its supporting stacks (they fix its floor height)."""
        T = self.T
        out = []
        for c in cells_of_slot(slot):
            ci = T.cell_index[c]
            j = int(T.cell_stack[ci])
            for cj in T.stack_cells[j][:int(T.cell_k[ci])]:
                cid = ex.occ.get(T.cell_ids[cj])
                if cid is not None and (ex.where[cid][1], cid) not in out:
                    out.append((ex.where[cid][1], cid))
        return tuple(sorted(out))

    def _clearance(self) -> float:
        from stowage.checker.rules_special import _nonnegative, _parameter

        c = _nonnegative(_parameter(self.instance, "expert.e9.over_width_length", "oog_clearance",
                                    "minimum_clearance_m"))
        return c if c is not None else 0.0

    def _half_dims(self, cid):
        """(half length + fore/aft overhang max, half width + port/stbd overhang max) of a box's envelope, m."""
        from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M

        b = self._boxes[cid]
        o = b.oog
        hl = CONTAINER_LENGTH_M[b.length_ft] / 2 + (max(o.over_length_fore_m, o.over_length_aft_m) if o else 0.0)
        hw = CONTAINER_WIDTH_M / 2 + (max(o.over_width_port_m, o.over_width_starboard_m) if o else 0.0)
        return hl, hw

    def _geometry(self, placed):
        """Arrays for the horizontal-gap prefilter: ids, slots, centres, half dims."""
        T = self.T
        ids = [c for c, _ in placed]
        slots = [sl for _, sl in placed]
        xy = np.array([self._slot_xy[T.slot_index[sl]] for sl in slots]).reshape(-1, 2)
        hd = np.array([self._half_dims(c) for c in ids]).reshape(-1, 2)
        return ids, slots, xy, hd

    def _special_ok(self, st: _State, b: int, s: int, geo) -> bool:
        """2.4's E8/E9 envelope checks for the candidate (``special_violations`` focused on it), exactly, on a ship
        holding the candidate, every placed box whose envelope could come within the OOG clearance of it (a
        conservative horizontal-gap bound: centres minus half lengths/widths incl. overhangs) and the boxes under
        each of them (floors). A box beyond that bound can't take part in any pair check, so the answer equals the
        full call. Cached on exactly those placements."""
        from stowage.checker.rules_special import special_violations

        T, ex = self.T, st.ex
        slot, cid = T.slot_ids[s], T.box_ids[b]
        parts = [(slot, cid)] + list(self._below(ex, slot))
        if geo is not None and len(geo[0]):
            ids, slots, xy, hd = geo
            hl, hw = self._half_dims(cid)
            dx = np.abs(xy[:, 0] - self._slot_xy[s, 0]) - hd[:, 0] - hl
            dy = np.abs(xy[:, 1] - self._slot_xy[s, 1]) - hd[:, 1] - hw
            gap = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0))
            for i in np.flatnonzero(gap < self._clearance() + 1e-6).tolist():
                if ids[i] != cid:
                    parts.append((slots[i], ids[i]))
                    parts.extend(self._below(ex, slots[i]))
        parts = tuple(sorted(set(parts)))
        key = ("special", b, s, parts)
        ok = self._exact_memo.get(key)
        if ok is None:
            self.diagnostics["exact_calls"] += 1
            ship = self.instance.onboard.model_copy(update={
                "placements": [Placement.model_construct(slot=a, container_id=c) for a, c in parts],
                "covers_off": {}})
            vs = special_violations(self.instance, ship, call_index=self.instance.call_index,
                                    focus_container_id=T.box_ids[b])
            ok = self._exact_memo[key] = not any(v.severity == "hard" and cid in v.containers for v in vs)
        return ok

    def _exact_ok(self, st: _State, ship, b: int, s: int) -> bool:
        from stowage.checker.rules import placement_violations

        T = self.T
        stacks = T.slot_stacks[s][T.slot_stacks[s] >= 0]
        zone = self._oog_zone[s] if self._oog_zone is not None else ()
        vers = tuple(int(st.ver[j]) for j in sorted(set(stacks.tolist()) | set(zone)))
        key = (b, s, vers, st.dg_aboard if T.is_dg[b] else None)
        ok = self._exact_memo.get(key)
        if ok is None:
            self.diagnostics["exact_calls"] += 1
            box = self._boxes[T.box_ids[b]]
            vs = placement_violations(self.instance, ship, box, T.slot_ids[s])
            ok = self._exact_memo[key] = not any(v.severity == "hard" for v in vs)
        return ok

    # ------------------------------------------------------------------ C1
    def _lim_ub(self, has20, haslong):
        """Largest stack-mass limit any final composition can still use (E7 picks 20/40/mixed by composition)."""
        T = self.T
        return np.where(has20 & haslong, T.stack_lim_mixed,
                        np.where(has20, np.maximum(T.stack_lim_20, T.stack_lim_mixed),
                                 np.where(haslong, np.maximum(T.stack_lim_40, T.stack_lim_mixed),
                                          np.maximum.reduce([T.stack_lim_20, T.stack_lim_40, T.stack_lim_mixed]))))

    def _lim_ub_at(self, j, has20, haslong):
        T = self.T
        mixed, l20, l40 = T.stack_lim_mixed[j], T.stack_lim_20[j], T.stack_lim_40[j]
        return np.where(has20 & haslong, mixed, np.where(has20, np.maximum(l20, mixed),
                        np.where(haslong, np.maximum(l40, mixed), np.maximum(np.maximum(l20, l40), mixed))))

    def _usable(self, st: _State, hmin: float, smin: float, fill=None, sh=None, smass=None, closed=None):
        T = self.T
        fill = st.fill if fill is None else fill
        sh = st.sh if sh is None else sh
        smass = st.smass if smass is None else smass
        closed = (st.closed | st.poisoned) if closed is None else closed
        free = np.maximum(self._reach - fill, 0)
        mroom = self._lim_ub(st.n20 > 0, st.nlong > 0) - smass
        with np.errstate(divide="ignore", invalid="ignore"):
            nh = np.floor((T.stack_hmax - sh) / hmin + 1e-9) if hmin > 0 else np.full(len(fill), np.inf)
            nm = np.floor(mroom / smin + 1e-9) if smin > 0 else np.full(len(fill), np.inf)
        n = np.minimum(free, np.minimum(np.maximum(nh, 0), np.maximum(nm, 0)))
        return np.where(closed, 0, n).astype(np.int64)

    def _rank_cap(self, r: np.ndarray) -> np.ndarray:
        """Mass cap (inclusive) for boxes allowed above a stack whose lowest class rank is ``r``. With the natural
        class order L < M < H, allowed ranks r + tol form a VGM interval [0, boundary); otherwise E2 gives no mass
        cap (the capacity screens then ignore it — weaker, still necessary). Missing E2 data: nothing fits."""
        T = self.T
        r = np.asarray(r)
        if not T.e2_valid:
            return np.full(r.shape, -np.inf)
        if not T.e2_natural:
            return np.full(r.shape, np.inf)
        med, heavy = T.e2_bounds
        k = r + T.e2_tol
        return np.where(k >= 2, np.inf, np.where(k == 1, np.nextafter(heavy, -np.inf), np.nextafter(med, -np.inf)))

    def _caps(self, st: _State) -> np.ndarray:
        T = self.T
        e1 = T.e1_threshold
        nonempty = st.fill > 0
        cap = np.where(nonempty, self._rank_cap(st.lowrank), np.inf)
        if e1 is None:
            cap = np.where(nonempty, -np.inf, cap)
        else:
            cap = np.where(st.nonheavy, np.minimum(cap, np.nextafter(e1, -np.inf)), cap)
        return cap

    def _c1(self, st: _State, b_idx: np.ndarray, s_idx: np.ndarray) -> np.ndarray:
        T = self.T
        rem = np.flatnonzero(st.rem)
        if len(rem) == 0:
            return np.ones(len(b_idx), dtype=bool)
        # Usable cells per stack are counted with the shortest / lightest box of the whole instance (fixed), so a
        # placement changes the counts of its own stacks only and the per-candidate screen stays exact.
        hmin = hmin_c = float(T.height.min())
        smin = smin_c = float(T.share.min())
        usable = self._usable(st, hmin, smin)
        caps = self._caps(st)
        ok = np.ones(len(b_idx), dtype=bool)
        # ---- C1a class flow
        flow = self._flow
        demand = np.bincount(flow.box_class[rem], weights=T.units[rem], minlength=flow.nb).astype(np.int64)
        usable_cells = self._usable_cell_mask(st, usable)
        capacity = np.bincount(flow.cell_class[usable_cells], minlength=flow.nc).astype(np.int64)
        feasible, f = flow.flow(demand, capacity)
        self.diagnostics["c1a_flows"] += 1
        self._c1a_memo = {}
        fails = self._c1_fails = {}
        if not feasible:
            fails["c1a"] = len(b_idx)
            return np.zeros(len(b_idx), dtype=bool)
        spare = capacity - f.sum(axis=0)
        if self._c1_slack(st, rem, usable, caps, spare):
            r = self._c1f_screen(st, rem, b_idx, s_idx)
            fails["c1f"] = int((~r).sum())
            return ok & r
        bc = flow.box_class[b_idx]
        cells = T.slot_cells[s_idx]
        stacks = T.slot_stacks[s_idx]
        two = cells[:, 1] >= 0
        cc1 = flow.cell_class[cells[:, 0]]
        cc2 = flow.cell_class[np.maximum(cells[:, 1], 0)]
        # post usable counts per affected stack
        new_counts, old_counts, new_caps, old_caps = [], [], [], []
        e1 = T.e1_threshold
        mb = T.mass[b_idx]
        newcap_box = self._rank_cap(T.rank[b_idx])
        if e1 is not None:
            newcap_box = np.where(mb < e1, np.minimum(newcap_box, np.nextafter(e1, -np.inf)), newcap_box)
        else:
            newcap_box = np.full(len(b_idx), -np.inf)
        extra_loss = np.zeros(len(b_idx), dtype=bool)
        room_delta = np.zeros(len(b_idx))
        # per-stack quantities once, then gathered per candidate
        has20, haslong = st.n20 > 0, st.nlong > 0
        lim20_s = self._lim_ub(np.ones_like(has20), haslong) - st.smass      # room if a 20' box is added
        limlong_s = self._lim_ub(has20, np.ones_like(haslong)) - st.smass    # room if a long box is added
        room_old_s = np.where(usable > 0, np.maximum(self._lim_ub(has20, haslong) - st.smass, 0), 0)
        free_after_s = np.maximum(self._reach - st.fill - 1, 0)
        hroom_s = T.stack_hmax - st.sh
        dead_s = st.closed | st.poisoned
        is20 = T.length[b_idx] == 20
        sh_b, h_b, cl_b = T.share[b_idx], T.height[b_idx], T.closes_stack[b_idx]
        for n in range(2):
            j = np.maximum(stacks[:, n], 0)
            valid = stacks[:, n] >= 0
            free_after = free_after_s[j]
            mroom = np.where(is20, lim20_s[j], limlong_s[j]) - sh_b
            room_old_j = room_old_s[j]
            room_new_j = np.maximum(mroom, 0)          # zeroed below where the stack stops being usable
            hroom = hroom_s[j] - h_b
            with np.errstate(divide="ignore", invalid="ignore"):
                nh = np.where(hmin_c > 0, np.floor(hroom / hmin_c + 1e-9), np.inf)
                nm = np.where(smin_c > 0, np.floor(mroom / smin_c + 1e-9), np.inf)
            nn = np.minimum(free_after, np.minimum(np.maximum(nh, 0), np.maximum(nm, 0)))
            nn = np.where(cl_b | dead_s[j], 0, nn).astype(np.int64)
            no = usable[j]
            nn = np.where(valid, nn, no)
            room_delta += np.where(valid, room_old_j - np.where(nn > 0, room_new_j, 0), 0)
            new_counts.append(np.where(valid, nn, 0))
            old_counts.append(np.where(valid, no, 0))
            old_caps.append(np.where(valid, caps[j], -np.inf))
            new_caps.append(np.where(valid, np.minimum(caps[j], newcap_box), -np.inf))
            extra_loss |= valid & (no - nn > 1)
        # exact one-cell removal table (20' box losing only its own cell)
        single = flow.single_removal(f, demand, capacity)
        simple = ~two & ~extra_loss & ~T.closes_stack[b_idx]
        ok[simple] = single[bc[simple], cc1[simple]]
        # two-cell placement losing only its own two cells: closed form of the test below
        plain2 = two & ~extra_loss & ~T.closes_stack[b_idx]
        if plain2.any():
            k = np.flatnonzero(plain2)
            u = T.units[b_idx[k]]
            a1, a2, bb = cc1[k], cc2[k], bc[k]
            same = a1 == a2
            o_same = np.maximum(2 - spare[a1], 0)
            o1, o2 = np.maximum(1 - spare[a1], 0), np.maximum(1 - spare[a2], 0)
            q = np.where(same, (o_same <= f[bb, a1]) & (o_same <= u),
                         (o1 <= f[bb, a1]) & (o2 <= f[bb, a2]) & (o1 + o2 <= u))
            ok[k[q]] = True
            pending = k[~q]
        else:
            pending = np.zeros(0, dtype=np.int64)
        rest = np.flatnonzero(~simple & ~plain2)
        if len(pending):
            # exact flow per unique (box class, units, lost classes)
            keys = np.stack([bc[pending], T.units[b_idx[pending]], np.minimum(cc1, cc2)[pending],
                             np.maximum(cc1, cc2)[pending]], axis=1)
            uniq, inv = np.unique(keys, axis=0, return_inverse=True)
            res = np.array([self._c1a_post(demand, capacity, int(q[0]), int(q[1]), (int(q[2]), int(q[3])))
                            for q in uniq], dtype=bool)
            ok[pending] = res[inv.ravel()]
        if len(rest):
            # cells lost per class: each stack loses its next free cell and any usable cells above the new count
            SC, H = self._stack_cells_pad, self._stack_cells_pad.shape[1]
            L = np.zeros((len(rest), flow.nc), dtype=np.int64)
            rows = np.arange(len(rest))
            for n in range(2):
                j = stacks[rest, n]
                valid = j >= 0
                jj = np.maximum(j, 0)
                fl = st.fill[jj]
                uo = usable[jj]
                nn = new_counts[n][rest]
                for p in range(H):
                    pos = fl + p
                    lost = valid & (p < uo) & ((p == 0) | (p > nn)) & (pos < H)
                    if not lost.any():
                        continue
                    cell = SC[jj[lost], pos[lost]]
                    np.add.at(L, (rows[lost], flow.cell_class[cell]), 1)
            units = T.units[b_idx[rest]]
            own = f[bc[rest]]                                     # (n, nc) this box class's flow per cell class
            over = np.maximum(L - spare[None, :], 0)
            quick = np.all(over <= own, axis=1) & (over.sum(axis=1) <= units)
            ok[rest[quick]] = True
            hard = np.flatnonzero(~quick)
            if len(hard):
                keys = np.concatenate([bc[rest[hard]][:, None], units[hard][:, None], L[hard]], axis=1)
                uniq, inv = np.unique(keys, axis=0, return_inverse=True)
                res = np.array([self._c1a_post(demand, capacity, int(k[0]), int(k[1]),
                                               tuple(int(c) for c in np.repeat(np.arange(flow.nc), k[2:])))
                                for k in uniq], dtype=bool)
                ok[rest[hard]] = res[inv.ravel()]
        fails["c1a"] = int((~ok).sum())

        def rec(name, r):
            fails[name] = fails.get(name, 0) + int((~r).sum())
            return r
        # ---- C1d aggregate stack-mass room: Σ remaining mass ≤ Σ usable stacks' room
        room_all = np.where(usable > 0, np.maximum(self._lim_ub(st.n20 > 0, st.nlong > 0) - st.smass, 0), 0)
        total_room, total_mass = float(room_all.sum()), float(T.mass[rem].sum())
        if total_mass > total_room + 1e-6:
            fails["c1d"] = len(b_idx)
            return np.zeros(len(b_idx), dtype=bool)
        if total_room - total_mass < 2 * float(room_all.max(initial=0)) + 1e-6:
            ok &= rec("c1d", (total_mass - mb) <= (total_room - room_delta) + 1e-6)
        # ---- C1c long boxes need aligned stack pairs: #long ≤ Σ_pairs min(usable_a, usable_b)
        ok &= rec("c1c", self._c1c(st, rem, usable, b_idx, stacks, old_counts, new_counts))
        # ---- C1e long boxes vs stack pairs, nested by cap: pair cap = min of its stacks' caps
        ok &= rec("c1e", self._c1e(st, rem, usable, caps, b_idx, stacks, old_counts, new_counts, new_caps))
        # ---- C1b nested Hall: all boxes vs all usable cells (D25 class caps as VGM bounds)
        fam_all = NestedHall(T.mass[rem], T.units[rem], caps, usable)
        if not fam_all.ok:
            fails["c1b"] = len(b_idx)
            return np.zeros(len(b_idx), dtype=bool)
        ok &= rec("c1b", fam_all.screen(mb, T.units[b_idx], old_caps, new_caps, old_counts, new_counts,
                                        np.ones(len(b_idx), dtype=bool)))
        # ---- C1f E6 departure (D25): hold stacks a deck H box could still use must each get another box
        ok &= rec("c1f", self._c1f_screen(st, rem, b_idx, s_idx))
        return ok

    def _c1_slack(self, st, rem, usable, caps, spare) -> bool:
        """True if no single placement can break any C1 condition: every condition's slack exceeds the most one
        placement can take away (its ≤ 2 stacks' usable cells, ≤ 2·width stack pairs, its stacks' room). Exact
        shortcut: when it holds, the per-candidate screens would all pass."""
        T = self.T
        H = self._stack_cells_pad.shape[1]
        top = int(usable.max(initial=0))
        if (spare < 2 * H).any():
            return False
        if NestedHall(T.mass[rem], T.units[rem], caps, usable).min_surplus < 2 * top:
            return False
        P = self._pairs
        long_rem = rem[T.units[rem] == 2]
        if len(long_rem):
            if len(P) == 0:
                return False
            pc = np.minimum(usable[P[:, 0]], usable[P[:, 1]])
            loss = self._member.shape[1] * 2 * top
            if int(pc.sum()) - len(long_rem) < loss:
                return False
            hall = NestedHall(T.mass[long_rem], np.ones(len(long_rem), dtype=np.int64),
                              np.minimum(caps[P[:, 0]], caps[P[:, 1]]), pc)
            if hall.min_surplus < loss:
                return False
        room = np.where(usable > 0, np.maximum(self._lim_ub(st.n20 > 0, st.nlong > 0) - st.smass, 0), 0)
        if float(room.sum()) - float(T.mass[rem].sum()) < 2 * float(room.max(initial=0)):
            return False
        return True

    # ------------------------------------------------------------------ C1f: E6 departure fallback (D25)
    def _e6_qual(self) -> np.ndarray:
        """(B,) boxes whose E6 hold-compatibility can change only by placing boxes into that hold stack: valid
        class-H 20' boxes without DG, OOG or a heat request (DG pairs, OOG clearance and heat could change through
        cargo elsewhere, so counting them could be unsound; leaving them out only weakens the bound)."""
        T = self.T
        if getattr(self, "_e6q", None) is None:
            e6 = T.e6_threshold
            self._e6q = (T.valid_mass & (T.length == 20) & ~T.is_dg & ~T.fallback & ~T.heat
                         & (T.mass >= (e6 if e6 is not None else np.inf)))
            sl = np.full(len(T.cell_ids), -1, dtype=np.int64)
            for ci, cid in enumerate(T.cell_ids):
                sl[ci] = T.slot_index.get(cid, -1)
            self._cell_slot20 = sl
        return self._e6q

    def _e6_compat(self, d: np.ndarray, j: np.ndarray, fill, closed, nlong, smass, sh, lowrank, nonheavy, excl):
        """compat[d, j]: box d (B index) could legally take hold stack j's next free 20' cell, given per-stack
        values (arrays aligned with ``j``). Mirrors the L2 fast path used for candidates."""
        T = self.T
        H = self._stack_cells_pad.shape[1]
        nxt = np.where(fill < H, self._stack_cells_pad[j, np.minimum(fill, H - 1)], -1)
        slot = np.where(nxt >= 0, self._cell_slot20[np.maximum(nxt, 0)], -1)
        ok_j = (slot >= 0) & ~closed & (nlong == 0) & ~excl & ~T.stack_deck[j]
        st_ok = T.static[np.ix_(d, np.maximum(slot, 0))] & ok_j[None, :]
        room = T.stack_lim_20[j] - smass
        st_ok &= T.share[d][:, None] <= room[None, :] + _EPS
        st_ok &= T.height[d][:, None] <= (T.stack_hmax[j] - sh)[None, :] + 1e-9
        nonempty = fill > 0
        if T.e2_valid:
            capr = np.where(nonempty, lowrank + T.e2_tol, _NORANK)
        else:
            capr = np.where(nonempty, -1, _NORANK)
        st_ok &= T.rank[d][:, None] <= capr[None, :]
        e1 = T.e1_threshold
        if e1 is None:
            st_ok &= ~nonempty[None, :]
        else:
            st_ok &= ~((T.mass[d][:, None] >= e1) & nonheavy[None, :])
        return st_ok

    def _c1f_state(self, st: _State):
        """(applicable, D, blocking (J,), K, U, excluded stacks)."""
        T = self.T
        if T.e6_threshold is None or T.e6_fallback is not True:
            return False, None, None, 0, 0, None
        qual = self._e6_qual()
        ex = st.ex
        D = []
        excl = np.zeros(len(T.stack_ids), dtype=bool)
        for cid, loc in ex.where.items():
            if loc[0] != "ship":
                continue
            if self._attr[cid][7] and self._oog_zone is not None:   # OOG aboard: its zone is not counted
                si = T.slot_index.get(loc[1])
                if si is not None:
                    excl[list(self._oog_zone[si])] = True
            bi = T.box_index.get(cid)
            if bi is not None and qual[bi] and parse_slot(loc[1]).on_deck:
                D.append(bi)
        rem = np.flatnonzero(st.rem)
        U = int(T.units[rem].sum())
        J = np.arange(len(T.stack_ids))
        if not D:
            return True, np.zeros(0, dtype=np.int64), np.zeros(len(J), dtype=bool), 0, U, excl
        D = np.array(sorted(D), dtype=np.int64)
        comp = self._e6_compat(D, J, st.fill, st.closed | st.poisoned, st.nlong, st.smass, st.sh, st.lowrank,
                               st.nonheavy, excl)
        blocking = comp.any(axis=0)
        return True, D, blocking, int(blocking.sum()), U, excl

    def _c1f_screen(self, st: _State, rem, b_idx, s_idx) -> np.ndarray:
        T = self.T
        N = len(b_idx)
        app, D, blocking, K, U, excl = self._c1f_state(st)
        if not app:
            return np.ones(N, dtype=bool)
        qual = self._e6_qual()
        u_b = T.units[b_idx]
        Kp = np.full(N, K, dtype=np.int64)
        deck = T.slot_deck[s_idx]
        # A) a qualifying H box going on deck joins D: its compatible non-blocking hold stacks become blocking
        a = np.flatnonzero(deck & qual[b_idx])
        if len(a):
            ub, inv = np.unique(b_idx[a], return_inverse=True)
            J = np.arange(len(T.stack_ids))
            comp = self._e6_compat(ub, J, st.fill, st.closed | st.poisoned, st.nlong, st.smass, st.sh,
                                   st.lowrank, st.nonheavy, excl)
            added = (comp & ~blocking[None, :]).sum(axis=1)
            Kp[a] += added[inv.ravel()]
        # B) a hold placement changes its own stacks' next cell; recompute their blocking against D
        h = np.flatnonzero(~deck)
        if len(h) and len(D) and not (K + 2 <= U - 2):
            bh = b_idx[h]
            for n in range(2):
                j = T.slot_stacks[s_idx[h], n]
                valid = j >= 0
                jj = np.maximum(j, 0)
                e1 = T.e1_threshold
                post = self._e6_compat(
                    D, jj, st.fill[jj] + 1, st.closed[jj] | st.poisoned[jj] | T.closes_stack[bh],
                    st.nlong[jj] + (T.length[bh] != 20), st.smass[jj] + T.share[bh], st.sh[jj] + T.height[bh],
                    np.where(T.valid_mass[bh], np.minimum(st.lowrank[jj], T.rank[bh]), st.lowrank[jj]),
                    st.nonheavy[jj] | (T.valid_mass[bh] & (T.mass[bh] < (e1 if e1 is not None else -np.inf))),
                    excl[jj]).any(axis=0)
                Kp[h] += np.where(valid, post.astype(np.int64) - blocking[jj].astype(np.int64), 0)
        elif len(h) and len(D):
            pass  # slack: a hold placement moves K by at most +2 and U by at most -2
        return Kp <= U - u_b

    def _c1c(self, st, rem, usable, b_idx, stacks, old_counts, new_counts) -> np.ndarray:
        T, P = self.T, self._pairs
        long_dem = int((T.units[rem] == 2).sum())
        if long_dem == 0:
            return np.ones(len(b_idx), dtype=bool)
        if len(P) == 0:
            return np.zeros(len(b_idx), dtype=bool)
        cap = np.minimum(usable[P[:, 0]], usable[P[:, 1]])
        total = int(cap.sum())
        if total < long_dem:
            return np.zeros(len(b_idx), dtype=bool)
        # per candidate: new usable counts on its (one or two) stacks → recompute the affected pairs' minima
        N = len(b_idx)
        dem_after = long_dem - (T.units[b_idx] == 2)
        if (total - dem_after).min() >= 2 * int(usable.max(initial=0)):   # nothing a candidate does can matter
            return np.ones(N, dtype=bool)
        J = len(T.stack_ids)
        member = [[] for _ in range(J)]
        for pi, (a, b) in enumerate(P.tolist()):
            member[a].append(pi)
            member[b].append(pi)
        loss = np.zeros(N, dtype=np.int64)
        for k in range(N):
            new = {}
            for n in range(2):
                j = int(stacks[k, n])
                if j >= 0:
                    new[j] = int(new_counts[n][k])
            seen = set()
            for j in new:
                for pi in member[j]:
                    if pi in seen:
                        continue
                    seen.add(pi)
                    a, b = P[pi]
                    na, nb = new.get(int(a), int(usable[a])), new.get(int(b), int(usable[b]))
                    loss[k] += int(cap[pi]) - min(na, nb)
        return total - loss >= dem_after

    def _c1e(self, st, rem, usable, caps, b_idx, stacks, old_counts, new_counts, new_caps) -> np.ndarray:
        T, P = self.T, self._pairs
        N = len(b_idx)
        long_rem = rem[T.units[rem] == 2]
        if len(long_rem) == 0:
            return np.ones(N, dtype=bool)
        if len(P) == 0:
            return np.zeros(N, dtype=bool)
        pcap = np.minimum(caps[P[:, 0]], caps[P[:, 1]])
        pcnt = np.minimum(usable[P[:, 0]], usable[P[:, 1]])
        hall = NestedHall(T.mass[long_rem], np.ones(len(long_rem), dtype=np.int64), pcap, pcnt)
        if not hall.ok:
            return np.zeros(N, dtype=bool)
        if hall.min_surplus >= self._member.shape[1] * 2 * int(usable.max(initial=0)):
            return np.ones(N, dtype=bool)          # no placement can remove that much pair capacity
        # affected pairs per candidate: pairs containing either of its stacks (padded with -1)
        j1, j2 = stacks[:, 0], stacks[:, 1]
        m1 = self._member[np.maximum(j1, 0)]
        m2 = np.where((j2 >= 0)[:, None], self._member[np.maximum(j2, 0)], -1)
        cand = np.concatenate([m1, m2], axis=1)
        # drop duplicates (a pair containing both stacks appears twice)
        cand = np.where(np.concatenate([np.zeros((N, m1.shape[1]), dtype=bool),
                                        (m2[:, :, None] == m1[:, None, :]).any(axis=2)], axis=1), -1, cand)
        old_c, new_c, old_k, new_k = [], [], [], []
        for q in range(cand.shape[1]):
            pi = cand[:, q]
            valid = pi >= 0
            p = np.maximum(pi, 0)
            a, b = P[p, 0], P[p, 1]

            def after(stk, cur_cap, cur_cnt):
                cap_v, cnt_v = cur_cap.copy(), cur_cnt.copy()
                for n in range(2):
                    hit = stk == stacks[:, n]
                    cap_v = np.where(hit, new_caps[n], cap_v)
                    cnt_v = np.where(hit, new_counts[n], cnt_v)
                return cap_v, cnt_v
            ca, ka = after(a, caps[a], usable[a])
            cb, kb = after(b, caps[b], usable[b])
            old_c.append(np.where(valid, pcap[p], -np.inf))
            new_c.append(np.where(valid, np.minimum(ca, cb), -np.inf))
            old_k.append(np.where(valid, pcnt[p], 0))
            new_k.append(np.where(valid, np.minimum(ka, kb), 0))
        return hall.screen(T.mass[b_idx], np.ones(N, dtype=np.int64), old_c, new_c, old_k, new_k,
                           T.units[b_idx] == 2)

    def _usable_cell_mask(self, st: _State, usable: np.ndarray) -> np.ndarray:
        T = self.T
        cells = []
        for j, n in enumerate(usable.tolist()):
            if n:
                f = int(st.fill[j])
                cells.extend(T.stack_cells[j][f:f + n])
        return np.array(cells, dtype=np.int64)

    def _lost_classes(self, st, usable, s, new_counts) -> tuple:
        """Cell classes of the usable cells a placement in slot ``s`` removes (its own cells and any top cells
        that lose their usability), as a sorted tuple."""
        T, flow = self.T, self._flow
        lost = []
        for n in range(2):
            j = int(T.slot_stacks[s, n])
            if j < 0:
                continue
            f = int(st.fill[j])
            old = T.stack_cells[j][f:f + int(usable[j])]
            new = set(T.stack_cells[j][f + 1:f + 1 + new_counts[n]])
            lost.extend(int(flow.cell_class[ci]) for ci in old if ci not in new)
        return tuple(sorted(lost))

    def _c1a_post(self, demand, capacity, bc, units, lost) -> bool:
        """Exact class-flow test of a post-state given by (box class, units, lost cell classes)."""
        flow = self._flow
        self.diagnostics["c1a_flows"] += 1
        d = demand.copy()
        d[bc] -= units
        cap = capacity.copy()
        for cc in lost:
            cap[cc] -= 1
        feasible, _ = flow.flow(np.maximum(d, 0), np.maximum(cap, 0))
        return bool(feasible)

    # ------------------------------------------------------------------ C2
    def _c2(self, st: _State, b_idx: np.ndarray, s_idx: np.ndarray) -> np.ndarray:
        T, g = self.T, self._global
        slots, inv = np.unique(s_idx, return_inverse=True)
        L, U = g.intervals(st.ref, T.slot_cells[slots], T.slot_n[slots])
        mb = T.mass[b_idx]
        ok = (mb >= L[inv] - 1e-6) & (mb <= U[inv] + 1e-6)
        if not ok.all() and not st.ref.centered and (~ok).mean() > 1 - self._recenter_below:
            fixed, free, R = st.ref.fixed, st.ref.free, st.ref.R
            ref = g.reference(fixed, free, R)
            self.diagnostics["recenters"] += 1
            self._sync_lp()
            if ref is None:
                st.c2_dead = True
                return np.zeros(len(b_idx), dtype=bool)
            st.ref = ref
            L, U = g.intervals(st.ref, T.slot_cells[slots], T.slot_n[slots])
            ok = (mb >= L[inv] - 1e-6) & (mb <= U[inv] + 1e-6)
        if ok.all():
            return ok
        fail_slots = np.unique(s_idx[~ok])
        budget = self._lp_budget
        for s in fail_slots.tolist():
            sel = (~ok) & (s_idx == s)
            if budget <= 0:
                self.diagnostics["c2_unresolved"] += int(sel.sum())
                continue
            budget -= 1
            self.diagnostics["c2_slot_lps"] += 1
            cells = [int(c) for c in T.slot_cells[s] if c >= 0]
            sol = g.slot_interval(st.ref, cells, float(mb[sel].min()), float(mb[sel].max()))
            self._sync_lp()
            if sol is None:
                continue
            st.c2_sols[s] = sol
            lo, hi = sol[0][0], sol[1][0]
            ok |= sel & (mb >= lo - 1e-6) & (mb <= hi + 1e-6)
        return ok

    # ------------------------------------------------------------------ discharges (stage B)
    def _discharge_candidates(self, st: _State) -> tuple:
        out = []
        ex = st.ex
        limit = self.instance.port.quay_crane.max_on_deck_tiers
        for cid in sorted(self.instance.discharge_list):
            loc = ex.where.get(cid)
            if loc is None or loc[0] != "ship":
                continue
            trial = ex.copy()
            try:
                moves = trial.step(Discharge(cid))
            except ValueError:
                continue
            reach = True
            for m in moves:
                if m.kind == "restow_off" and m.container_id in self.T.discharge:
                    reach = False  # a discharge box parked on the quay could never be discharged

                src = getattr(m, "src", None)
                if isinstance(src, str) and len(src) == 7 and src.isdigit():
                    for cell in cells_of_slot(src):
                        sid, k = ex.cell_pos[cell]
                        if ex.stacks[sid].on_deck and k + 1 > limit:
                            reach = False
            if reach and self._screens:
                # restowed-off boxes become remaining again: the post-state must pass the completion model itself
                child = self._child(st, Discharge(cid))
                reach = not child.c2_dead and all(self._completion(child).values())
            if reach:
                out.append(Discharge(cid))
        return tuple(out)

    # ================================================================== protocol
    def _complete(self, st: _State) -> bool:
        ex = st.ex
        if st.rem.any() or ex.buffer:
            return False
        return all(ex.where.get(c, (None,))[0] == "yard" for c in self.instance.discharge_list)

    def legal_actions(self):
        t0 = perf_counter()
        self._stats.legal_calls += 1
        st = self._cur
        try:
            if st.legal is None:
                if self._complete(st):
                    st.legal, st.mask = (), np.zeros(0, dtype=np.int64)
                else:
                    self._evaluate(st)
                    if st.dead is not None:
                        self._stats.dead_ends += 1
            return st.legal
        finally:
            dt = perf_counter() - t0
            self._stats.legal_time_s += dt
            self._stats.max_legal_time_s = max(self._stats.max_legal_time_s, dt)

    def is_legal(self, action) -> bool:
        st = self._cur
        self.legal_actions()
        if isinstance(action, Place):
            b = self.T.box_index.get(action.box)
            s = self.T.slot_index.get(action.slot)
            if b is None or s is None:
                return False
            f = b * len(self.T.slot_ids) + s
            k = int(np.searchsorted(st.mask, f))
            return k < len(st.mask) and int(st.mask[k]) == f
        if isinstance(action, Discharge):
            return action in st.discharges
        return False

    def push(self, action) -> None:
        if not self.is_legal(action):
            raise IllegalActionError(f"action is not legal here: {action!r}")
        old = self._cur
        new = self._child(old, action)
        self._history.append(old)
        self._cur = new
        self._stats.pushes += 1

    def _child(self, old: _State, action) -> _State:
        """The state after ``action`` (no legality check)."""
        T = self.T
        ex = old.ex.copy()
        moves = ex.step(action)
        new = _State(ex=ex, fill=old.fill.copy(), smass=old.smass.copy(), sh=old.sh.copy(), n20=old.n20.copy(),
                     nlong=old.nlong.copy(), lowrank=old.lowrank.copy(), nonheavy=old.nonheavy.copy(),
                     closed=old.closed.copy(), poisoned=old.poisoned.copy(), ver=old.ver.copy(),
                     rem=old.rem.copy(), dg_aboard=old.dg_aboard, ref=old.ref, action=action)
        touched, released = set(), []
        for m in moves:
            for side in ("src", "dst"):
                slot = getattr(m, side, None)
                if isinstance(slot, str) and len(slot) == 7 and slot.isdigit():
                    for c in cells_of_slot(slot):
                        touched.add(int(T.cell_stack[T.cell_index[c]]))
            if m.kind == "restow_off":
                bi = T.box_index.get(m.container_id)
                if bi is not None:
                    new.rem[bi] = True
                    released.append(([T.cell_index[c] for c in cells_of_slot(m.src)], self._attr[m.container_id][0]))
            elif m.kind in ("load", "restow_on", "restow_shift"):
                bi = T.box_index.get(m.container_id)
                if bi is not None:
                    new.rem[bi] = False
        self._restack(new, sorted(touched))
        if any(self._attr[getattr(m, "container_id", "")][6] for m in moves if hasattr(m, "container_id")):
            new.dg_aboard = self._dg_aboard(ex)
        if self._global is not None and old.ref is not None:
            g = self._global
            ref = old.ref
            if isinstance(action, Place):
                cells = [T.cell_index[c] for c in cells_of_slot(action.slot)]
                m = self._attr[action.box][0]
                s = T.slot_index[action.slot]
                sol = old.c2_sols.get(s)
                L, U = g.intervals(ref, T.slot_cells[[s]], T.slot_n[[s]])
                if L[0] - 1e-6 <= m <= U[0] + 1e-6:
                    ref = g.shift(ref, cells, m)
                elif sol is not None:
                    ref = g.combine(ref, cells, m, sol[0], sol[1])
                else:  # pragma: no cover - legal implies one of the two
                    fixed, free, R = self._global_state(ex)
                    ref = g.reference(fixed, free, R)
            # restowed boxes leave after the shift was judged on the state with them fixed (a stronger test)
            if ref is not None:
                ref = g.release(ref, released)
            if ref is not None and not g.feasible(ref):
                fixed, free, R = self._global_state(ex)
                ref = g.reference(fixed, free, R)
                self.diagnostics["recenters"] += 1
            new.ref = ref
            new.c2_dead = ref is None
            self._sync_lp()
        return new

    def pop(self):
        if not self._history:
            raise IndexError("pop at depth zero")
        action = self._cur.action
        self._cur = self._history.pop()
        self._stats.pops += 1
        return action

    @property
    def depth(self) -> int:
        return len(self._history)

    def actions(self) -> list:
        return [s.action for s in self._history[1:]] + ([self._cur.action] if self._history else [])

    def is_complete(self) -> bool:
        return self._complete(self._cur)

    def dead_end(self):
        self.legal_actions()
        return self._cur.dead

    def to_plan(self):
        if not self.is_complete():
            raise ValueError("to_plan requires a complete action sequence")
        plan = self.space.expand(self.instance, self.actions())
        prov = current_provenance(created_by="feasibility").merge(plan.provenance, self.instance.provenance,
                                                                   parent_ids=[self.instance.id])
        prov.versions["feasibility-config"] = json.dumps(
            {"stability_margin_scale": self.config.stability_margin_scale, "lookahead": self.config.lookahead,
             "extra": self.config.extra}, sort_keys=True)
        return plan.model_copy(update={"provenance": prov})

    def copy(self):
        other = object.__new__(IncrementalEngine)
        other.__dict__.update(self.__dict__)
        other._history = list(self._history)
        other._stats = replace(self._stats)
        other.diagnostics = dict(self.diagnostics)
        return other

    def stats(self):
        return replace(self._stats)

    # ------------------------------------------------------------------ extras (not in the frozen protocol)
    def completion_check(self) -> dict[str, bool]:
        """The necessary completion conditions evaluated on the current state itself (C1 parts, C2). The per-
        candidate screens in ``legal_actions`` are built to equal this check on each candidate's post-state."""
        return self._completion(self._cur)

    def _completion(self, st: _State) -> dict[str, bool]:
        T = self.T
        app, _, _, K, U, _ = self._c1f_state(st)
        c1f = (not app) or K <= U
        rem = np.flatnonzero(st.rem)
        out = dict(c1a=True, c1b=True, c1c=True, c1d=True, c1e=True, c1f=bool(c1f), c2=True)
        if self._global is not None:
            if st.ref is None:
                out["c2"] = False
            elif not st.ref.centered:
                out["c2"] = self._global.reference(st.ref.fixed, st.ref.free, st.ref.R) is not None
                self._sync_lp()
        if len(rem) == 0:
            return out
        usable = self._usable(st, float(T.height.min()), float(T.share.min()))
        caps = self._caps(st)
        flow = self._flow
        demand = np.bincount(flow.box_class[rem], weights=T.units[rem], minlength=flow.nb).astype(np.int64)
        capacity = np.bincount(flow.cell_class[self._usable_cell_mask(st, usable)], minlength=flow.nc).astype(np.int64)
        out["c1a"] = bool(flow.flow(demand, capacity)[0])
        out["c1b"] = NestedHall(T.mass[rem], T.units[rem], caps, usable).ok
        P = self._pairs
        long_rem = rem[T.units[rem] == 2]
        if len(long_rem) and len(P):
            out["c1c"] = len(long_rem) <= int(np.minimum(usable[P[:, 0]], usable[P[:, 1]]).sum())
            out["c1e"] = NestedHall(T.mass[long_rem], np.ones(len(long_rem), dtype=np.int64),
                                    np.minimum(caps[P[:, 0]], caps[P[:, 1]]),
                                    np.minimum(usable[P[:, 0]], usable[P[:, 1]])).ok
        elif len(long_rem):
            out["c1c"] = out["c1e"] = False
        room = np.where(usable > 0, np.maximum(self._lim_ub(st.n20 > 0, st.nlong > 0) - st.smass, 0), 0)
        out["c1d"] = float(T.mass[rem].sum()) <= float(room.sum()) + 1e-6
        return out

    def forces_restow(self, action) -> bool:
        """True if expanding ``action`` now lifts boxes off the ship temporarily (a hold slot under a hatch cover
        carrying deck cargo, or a discharge under other boxes). Legal, but costs restow lifts; solvers and
        rollout policies use this to prefer cheap actions."""
        ex = self._cur.ex
        if isinstance(action, Place):
            for cell in cells_of_slot(action.slot):
                stack = ex.stacks[ex.cell_pos[cell][0]]
                cover = stack.hatch_cover_id
                if not stack.on_deck and cover not in ex.covers_off and any(
                        c in ex.occ for sid in ex.covers[cover].deck_stack_ids for c in ex.stacks[sid].cell_ids):
                    return True
            return False
        trial = ex.copy()
        return any(m.kind == "restow_off" for m in trial.step(action))
