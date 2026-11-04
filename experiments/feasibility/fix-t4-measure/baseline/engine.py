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

import itertools
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
from .engine import DeadEnd, FeasibilityStats, IllegalActionError
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
    legal: tuple | None = None      # materialised lazily from ``mask`` by legal_actions()
    evaluated: bool = False
    mask: np.ndarray | None = None  # sorted flat indices box*S + slot of legal Place actions
    discharges: tuple = ()
    dead: DeadEnd | None = None
    c2_sols: dict = field(default_factory=dict)
    sp: dict = field(default_factory=dict)      # (slot, half dims) → envelope neighbours, this state
    dgF: dict | None = None                     # C1g: DG class → (S,) slots still open to it (free, static, segregated)
    parent: object = None                       # previous state (for incremental C1g)


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
        self._stamp = itertools.count(1)
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
        self._kmax = H + 1
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
        self._donors = self._nearest_cells(12)
        hd = np.array([self._half_dims(c) for c in T.box_ids]).reshape(-1, 2)
        self._hl, self._hw = hd[:, 0], hd[:, 1]
        self._sigs: dict = {}
        self._dg_wit: dict = {}
        self._oog_heat = np.array([self._boxes[c].oog is not None and "away_from_heat" in self._boxes[c].special_stowage
                                   for c in T.box_ids], dtype=bool)
        self._sp_cache: dict = {}
        self._tz_cache: dict = {}
        self._oog_zone = self._oog_zones() if any(self._attr[b][7] for b in self._attr) else None
        if self._oog_zone is not None:
            Z = max(len(z) for z in self._oog_zone)
            self._zone_idx = np.full((len(T.slot_ids), max(Z, 1)), -1, dtype=np.int64)
            for i, z in enumerate(self._oog_zone):
                self._zone_idx[i, :len(z)] = z
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
    def _nearest_cells(self, k: int) -> np.ndarray:
        """(S, k) the k cells nearest each slot's centre (x, y, z of the cell centres), excluding its own cells:
        the donors of the C2 local shift."""
        T = self.T
        cellmap = {c.id: c for c in self.instance.vessel.cells}
        xyz = np.array([(cellmap[c].lcg_m, cellmap[c].tcg_m, cellmap[c].vcg_base_m) for c in T.cell_ids])
        sc = np.maximum(T.slot_cells, 0)
        cen = np.where((T.slot_cells >= 0)[:, :, None], xyz[sc], np.nan)
        cen = np.nanmean(cen, axis=1)
        out = np.full((len(T.slot_ids), k), -1, dtype=np.int64)
        for i in range(len(T.slot_ids)):
            d = np.linalg.norm(xyz - cen[i], axis=1)
            d[T.slot_cells[i][T.slot_cells[i] >= 0]] = np.inf
            nn = np.argsort(d, kind="stable")[:k]
            out[i, :len(nn)] = nn
        return out

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
            st.ver[j] = next(self._stamp)   # globally unique (shared by copies): a changed stack never repeats
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
        st.discharges = ()
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
        disch = self._discharge_candidates(st)
        st.discharges = disch
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
            ks = np.flatnonzero(keep & T.fallback[b_idx])
            if len(ks):
                fast = ~self._oog_heat[b_idx[ks]]
                if fast.any():
                    keep[ks[fast]] = self._e9_batch(st, b_idx[ks[fast]], s_idx[ks[fast]], all_boxes=True)
                if (~fast).any():
                    keep[ks[~fast]] = self._special_many(st, b_idx[ks[~fast]], s_idx[ks[~fast]], geo)
            if oog_aboard:
                ogeo = self._geometry(oog_aboard)
                cand = np.flatnonzero(keep & ~T.fallback[b_idx])
                if len(cand):
                    # vectorised gap prefilter: only candidates with an OOG box inside the clearance can break E9
                    hl, hw = self._hl[b_idx[cand]], self._hw[b_idx[cand]]
                    _, _, oxy, ohd = ogeo
                    dx = np.abs(oxy[None, :, 0] - self._slot_xy[s_idx[cand], 0][:, None]) - ohd[None, :, 0] - hl[:, None]
                    dy = np.abs(oxy[None, :, 1] - self._slot_xy[s_idx[cand], 1][:, None]) - ohd[None, :, 1] - hw[:, None]
                    gap = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0))
                    hit = cand[(gap < self._clearance() + 1e-6).any(axis=1)]
                    if len(hit):
                        keep[hit] = self._e9_batch(st, b_idx[hit], s_idx[hit], all_boxes=False)
        heat = np.flatnonzero(T.heat[b_idx] & keep & ~exact)
        if len(heat):
            _, _, nextz = self._env_state(st)
            stk = T.slot_stacks[s_idx[heat]]
            zs = np.round(np.max(np.where(stk >= 0, nextz[np.maximum(stk, 0)], -np.inf), axis=1), 6)
            memo = self._exact_memo
            for k, b, s_, z in zip(heat.tolist(), b_idx[heat].tolist(), s_idx[heat].tolist(), zs.tolist()):
                r = memo.get(("heatz", b, s_, z))
                keep[k] = r if r is not None else self._heat_ok(st, b, s_)
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
        # a placement that completes the plan: 2.4's exact E6 departure check on the final ship (C1f counts only
        # some deck H boxes; this closes the gap at departure)
        # (discharge-list boxes are not departure cargo, so pending discharges don't change that ship)
        # (the last box may be the quay buffer's only box: placing it empties the buffer too)
        if len(b_idx) and self._screens and int(st.rem.sum()) == 1 and len(st.ex.buffer) <= 1:
            done = np.array([not self.forces_restow(Place(T.box_ids[b], T.slot_ids[s_]))
                             and set(st.ex.buffer) <= {T.box_ids[b]}
                             for b, s_ in zip(b_idx.tolist(), s_idx.tolist())], dtype=bool)
            dk = np.flatnonzero(done)
            if len(dk):
                res = self._e6_last(st, [(T.box_ids[int(b_idx[k])], T.slot_ids[int(s_idx[k])]) for k in dk])
                done[dk] = res
                self._c1_fails["c1f"] = self._c1_fails.get("c1f", 0) + int((~res).sum())
            keep_last = done | np.array([self.forces_restow(Place(T.box_ids[b], T.slot_ids[s_]))
                                         or not set(st.ex.buffer) <= {T.box_ids[b]}
                                         for b, s_ in zip(b_idx.tolist(), s_idx.tolist())], dtype=bool)
            if not keep_last.all():
                reasons.append("capacity")
            b_idx, s_idx = b_idx[keep_last], s_idx[keep_last]
        # sort_key order (P, box id, slot id): box and slot indices are id-sorted, so the flat index sorts right
        flat = np.sort(b_idx * len(T.slot_ids) + s_idx)
        st.mask = flat
        if not len(flat) and not disch and not self._complete(st):
            # name the completion conditions that rejected candidates, with the catalogue rules behind each
            rules = {"c1a": ("expert.physical.size_fit", "expert.physical.slot_occupancy"),
                     "c1b": ("expert.e1.heavy_bottom", "expert.e2.weight_order"),
                     "c1c": ("expert.physical.size_fit", "expert.physical.slot_occupancy"),
                     "c1d": ("expert.physical.stack_weight",),
                     "c1e": ("expert.e2.weight_order", "expert.physical.size_fit"),
                     "c1f": ("expert.e6.heavy_under_deck",),
                     "c1h": ("expert.e2.weight_order", "expert.physical.reefer_plug", "expert.e8.under_deck"),
                     "c1g": ("imdg.segregation.7_2_4", "imdg.segregation.7_4_3", "imdg.stowage.3_2"),
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
            if not self._pair_ok(cls, s, other_id, other_slot):
                return False
        return True

    def _pair_ok(self, cls: int, s: int, other_id: str, other_slot: str) -> bool:
        """2.4's IMDG pair check (``rules_imdg._pair``) for a DG box of class ``cls`` at slot ``s`` against
        ``other_id`` at ``other_slot``; memoised; geometry-free class pairs answered once."""
        from stowage.checker.rules_imdg import _pair

        kind = self._pair_kind.get((cls, other_id))
        if kind is None:
            self._dg_ok(cls, s, ((other_id, other_slot),))
            kind = self._pair_kind[(cls, other_id)]
        if kind[0] == "const":
            return bool(kind[1])
        key = (cls, s, other_id, other_slot)
        ok = self._pair_memo.get(key)
        if ok is None:
            if getattr(self, "_cellmap", None) is None:
                self._cellmap = {c.id: c for c in self.instance.vessel.cells}
            out = _pair(self.instance, self._boxes[self._dg_rep[cls]], self.T.slot_ids[s], self._boxes[other_id],
                        other_slot, self.instance.call_index, self._cellmap)
            ok = self._pair_memo[key] = not any(v.severity == "hard" for v in out)
        return ok

    # ------------------------------------------------------------------ C1g: DG segregation capacity
    def _dg_tables(self):
        """Static: a representative box per DG class, each slot's overlapping slots (sharing a cell), slot cells."""
        if getattr(self, "_dgt", None) is None:
            T = self.T
            reps = {}
            for bi in np.flatnonzero(T.is_dg).tolist():
                reps.setdefault(int(T.dg_class[bi]), bi)
            by_cell = [[] for _ in T.cell_ids]
            for si, cells in enumerate(T.slot_cells.tolist()):
                for c in cells:
                    if c >= 0:
                        by_cell[c].append(si)
            W = max(len(set(sum((by_cell[c] for c in cells if c >= 0), []))) for cells in T.slot_cells.tolist())
            ov = np.full((len(T.slot_ids), W), -1, dtype=np.int64)
            for si, cells in enumerate(T.slot_cells.tolist()):
                o = sorted(set(sum((by_cell[c] for c in cells if c >= 0), [])))
                ov[si, :len(o)] = o
            self._dgt = (reps, ov)
        return self._dgt

    def _dep_free_slots(self, st: _State) -> np.ndarray:
        """(S,) slots whose cells are all free at departure (discharge-list boxes will have left)."""
        T = self.T
        occ = np.zeros(len(T.cell_ids), dtype=bool)
        for cid, loc in st.ex.where.items():
            if loc[0] == "ship" and cid not in T.discharge:
                for c in cells_of_slot(loc[1]):
                    occ[T.cell_index[c]] = True
        sc = T.slot_cells
        return np.all((sc < 0) | ~occ[np.maximum(sc, 0)], axis=1)

    def _dg_F(self, st: _State) -> dict:
        """C1g sets, incremental from the parent state when it has them: slots that filled drop out, new DG boxes
        aboard filter by 2.4's pair check, slots freed by restows (and any DG leaving) are re-judged in full."""
        if st.dgF is not None:
            return st.dgF
        T = self.T
        reps, ov = self._dg_tables()
        rem = np.flatnonzero(st.rem & T.is_dg)
        classes = sorted(set(T.dg_class[rem].tolist()))
        free = self._dep_free_slots(st)
        par = st.parent
        F = {}
        if par is not None and par.dgF is not None and set(par.dg_aboard) <= set(st.dg_aboard):
            new_dg = sorted(set(st.dg_aboard) - set(par.dg_aboard))
            pfree = getattr(par, "_dgfree", None)
            if pfree is None:
                pfree = self._dep_free_slots(par)
            freed = free & ~pfree
            for c in classes:
                f = par.dgF.get(c)
                if f is None:
                    f = self._dg_full(c, free, st.dg_aboard)
                else:
                    f = f & free
                    for s_ in np.flatnonzero(f).tolist():
                        for other_id, other_slot in new_dg:
                            if not self._pair_ok(c, s_, other_id, other_slot):
                                f[s_] = False
                                break
                    if freed.any():
                        cand = np.flatnonzero(freed & T.static[reps[c]])
                        for s_ in cand.tolist():
                            f[s_] = self._dg_ok(c, s_, st.dg_aboard)
                F[c] = f
        else:
            for c in classes:
                F[c] = self._dg_full(c, free, st.dg_aboard)
        st._dgfree = free
        st.dgF = F
        return F

    def _dg_full(self, c: int, free: np.ndarray, aboard: tuple) -> np.ndarray:
        T = self.T
        reps, _ = self._dg_tables()
        f = free & T.static[reps[c]]
        if aboard:
            for s_ in np.flatnonzero(f).tolist():
                f[s_] = self._dg_ok(c, s_, aboard)
        return f

    def _c1g(self, st: _State, b_idx: np.ndarray, s_idx: np.ndarray, rem: np.ndarray) -> np.ndarray:
        """Per candidate: every DG class keeps at least as many open slots (free, statically allowed,
        segregated from every DG box aboard incl. the candidate) as it has boxes left. A slot taken removes the
        slots overlapping it; a DG candidate also filters the others by 2.4's pair check (early exit once enough
        slots are found, farthest first)."""
        T = self.T
        N = len(b_idx)
        ok = np.ones(N, dtype=bool)
        remdg = rem[T.is_dg[rem]]
        if not len(remdg):
            return ok
        F = self._dg_F(st)
        _, ov = self._dg_tables()
        cls_b = T.dg_class[b_idx]
        counts = {c: int((T.dg_class[remdg] == c).sum()) for c in F}
        sizes = {c: int(F[c].sum()) for c in F}
        if any(sizes[c] < counts[c] for c in F):
            return np.zeros(N, dtype=bool)
        W = ov.shape[1]
        ovs = ov[s_idx]
        isdg = T.is_dg[b_idx]
        for c, f in F.items():
            if sizes[c] - W >= counts[c]:
                continue                      # a placement removes at most W overlapping slots: slack
            need = counts[c] - (cls_b == c).astype(np.int64)
            nd = ~isdg                        # DG candidates count their own slot inside the pair loop below
            loss = np.where(ovs[nd] >= 0, f[np.maximum(ovs[nd], 0)], False).sum(axis=1)
            ok[nd] &= sizes[c] - loss >= need[nd]
        # DG candidates: segregation from the new DG box. Boxes of one DG class at one slot share the answer
        # (the pair check reads only class facts), and slots found segregated before are re-used as witnesses
        # while they stay open — exact: the test is "at least `need` open slots pass 2.4's pair check".
        dk = np.flatnonzero(isdg & ok)
        if len(dk):
            xy = self._slot_xy
            reps, _ = self._dg_tables()
            orders = {}
            for c, f in F.items():
                cand = np.flatnonzero(f)
                srt = cand[np.argsort(xy[cand, 0], kind="stable")]
                orders[c] = (srt, srt[::-1], float(np.median(xy[cand, 0])) if len(cand) else 0.0)
            wit = self._dg_wit
            groups: dict = {}
            for k in dk.tolist():
                groups.setdefault((int(cls_b[k]), int(s_idx[k])), []).append(k)
            for (cd, q), ks in groups.items():
                rep_id, q_slot = T.box_ids[reps[cd]], T.slot_ids[q]
                qov = set(ov[q][ov[q] >= 0].tolist())
                good = True
                for c, f in F.items():
                    need = counts[c] - int(cd == c)
                    if need <= 0:
                        continue
                    srt, rev, med = orders[c]
                    if len(srt) < need:
                        good = False
                        break
                    wkey = (c, cd, q)
                    w = [x for x in wit.get(wkey, ()) if f[x] and x not in qov]
                    if len(w) >= need:
                        continue
                    seen = set(w)
                    for s_ in (srt if xy[q, 0] > med else rev).tolist():
                        if s_ in qov or s_ in seen:
                            continue
                        if self._pair_ok(c, s_, rep_id, q_slot):
                            w.append(s_)
                            if len(w) >= need:
                                break
                    wit[wkey] = tuple(w)
                    if len(w) < need:
                        good = False
                        break
                if not good:
                    ok[ks] = False
        return ok

    # ------------------------------------------------------------------ away-from-heat (E8)
    def _heat_ok(self, st: _State, b: int, s: int) -> bool:
        """2.4's heat-clearance check for the candidate, exactly (``special_violations`` focused on it), on a ship
        holding only the candidate and the boxes under it — the clearance depends on nothing else (the floor
        height comes from the boxes below). Cached on the floors."""
        from stowage.checker.rules_special import special_violations

        T, ex = self.T, st.ex
        _, _, nextz = self._env_state(st)
        vkey = ("heatz", b, s, round(float(max(nextz[j] for j in T.slot_stacks[s] if j >= 0)), 6))
        hit = self._exact_memo.get(vkey)
        if hit is not None:
            return hit
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
        self._exact_memo[vkey] = ok
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

    def _special_many(self, st, bs, ss, geo) -> np.ndarray:
        """``_special_ok`` for many candidates: one evaluation per (box signature, slot) whose zone is unchanged
        since it was last judged (zone stamp = newest stack version in the slot's OOG zone)."""
        out = np.empty(len(bs), dtype=bool)
        zmax = [int(st.ver[self._tight_zone(b, s)].max(initial=0)) for b, s in zip(bs.tolist(), ss.tolist())]
        for k, (b, s, z) in enumerate(zip(bs.tolist(), ss.tolist(), zmax)):
            sig = self._sigs.get(b)
            if sig is None:
                bx = self._boxes[self.T.box_ids[b]]
                sig = self._sigs[b] = (bx.length_ft, bx.height_m, bx.oog.model_dump_json() if bx.oog else None,
                                       tuple(sorted(map(str, bx.special_stowage))))
            key = ("zone", sig, s, z)
            r = self._exact_memo.get(key)
            if r is None:
                r = self._exact_memo[key] = self._special_ok(st, b, s, geo)
            out[k] = r
        return out

    # ------------------------------------------------------------------ E9 envelopes, vectorised
    def _env_state(self, st: _State):
        """Envelopes of every box aboard (2.4 rules_special geometry): x/y from cell centres, z from the actual
        heights below (floors), ISO 668 length/width plus asymmetric overhangs, height + over-height. Also
        per-stack floor of the next free cell. Cached per state."""
        if getattr(st, "_env", None) is not None:
            return st._env
        from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M

        T, ex = self.T, st.ex
        J = len(T.stack_ids)
        nextz = T.stack_base_z.copy()
        floor = {}
        for j in range(J):
            z = T.stack_base_z[j]
            for ci in T.stack_cells[j][:int(st.fill[j])]:
                cid = ex.occ[T.cell_ids[ci]]
                floor[ci] = z
                z += self._boxes[cid].height_m
            nextz[j] = z
        ids, rows = [], []
        for cid, loc in ex.where.items():
            if loc[0] != "ship":
                continue
            b = self._boxes[cid]
            cells = [T.cell_index[c] for c in cells_of_slot(loc[1])]
            si = T.slot_index[loc[1]]
            x, y = self._slot_xy[si]
            z = max(floor[c] for c in cells)
            o = b.oog
            L, W = CONTAINER_LENGTH_M[b.length_ft], CONTAINER_WIDTH_M
            rows.append((x - L / 2 - (o.over_length_aft_m if o else 0), x + L / 2 + (o.over_length_fore_m if o else 0),
                         y - W / 2 - (o.over_width_starboard_m if o else 0), y + W / 2 + (o.over_width_port_m if o else 0),
                         z, z + b.height_m + (o.over_height_m if o else 0),
                         bool(o and any((o.over_width_port_m, o.over_width_starboard_m, o.over_length_fore_m,
                                         o.over_length_aft_m)))))
            ids.append(cid)
        env = np.array(rows, dtype=float).reshape(-1, 7)
        st._env = (ids, env, nextz)
        return st._env

    def _e9_batch(self, st, bs, ss, *, all_boxes: bool) -> np.ndarray:
        """2.4's E9 rules for candidates (box ``bs`` at slot ``ss``), vectorised and exact: an OOG (width/length)
        box conflicts with any box whose envelope overlaps it vertically and horizontally or comes within the
        OOG clearance horizontally; an over-height candidate must fit the stack envelope (and any vertical
        clearance). ``all_boxes``: candidates are OOG boxes, checked against every box aboard; otherwise only
        against OOG boxes aboard."""
        from stowage.checker.rules_special import _nonnegative, _parameter
        from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M

        T = self.T
        ids, env, nextz = self._env_state(st)
        clr_raw = _parameter(self.instance, "expert.e9.over_width_length", "oog_clearance", "minimum_clearance_m")
        clr = _nonnegative(clr_raw)
        n = len(bs)
        ok = np.ones(n, dtype=bool)
        # candidate envelopes
        box = [self._boxes[T.box_ids[b]] for b in bs.tolist()]
        xy = self._slot_xy[ss]
        stk = T.slot_stacks[ss]
        z = np.max(np.where(stk >= 0, nextz[np.maximum(stk, 0)], -np.inf), axis=1)
        L = np.array([CONTAINER_LENGTH_M[b.length_ft] for b in box])
        W = CONTAINER_WIDTH_M
        oo = [b.oog for b in box]
        g = lambda f: np.array([getattr(o, f) if o else 0.0 for o in oo])  # noqa: E731
        cx0, cx1 = xy[:, 0] - L / 2 - g("over_length_aft_m"), xy[:, 0] + L / 2 + g("over_length_fore_m")
        cy0, cy1 = xy[:, 1] - W / 2 - g("over_width_starboard_m"), xy[:, 1] + W / 2 + g("over_width_port_m")
        h = np.array([b.height_m for b in box])
        cz0, cz1 = z, z + h + g("over_height_m")
        cand_wl = np.array([bool(o and any((o.over_width_port_m, o.over_width_starboard_m, o.over_length_fore_m,
                                            o.over_length_aft_m))) for o in oo])
        if all_boxes:
            # a width/length OOG candidate without a valid clearance parameter: 2.4 reports missing data
            if clr is None:
                ok &= ~cand_wl
        if len(env) and clr is not None:
            other = env if all_boxes else env[env[:, 6] > 0]
            if len(other):
                pair_rule = (cand_wl[:, None] | (other[None, :, 6] > 0))
                zov = (cz0[:, None] < other[None, :, 5]) & (other[None, :, 4] < cz1[:, None])
                gx = np.maximum(np.maximum(cx0[:, None] - other[None, :, 1], other[None, :, 0] - cx1[:, None]), 0)
                gy = np.maximum(np.maximum(cy0[:, None] - other[None, :, 3], other[None, :, 2] - cy1[:, None]), 0)
                hov = ((cx0[:, None] < other[None, :, 1]) & (other[None, :, 0] < cx1[:, None])
                       & (cy0[:, None] < other[None, :, 3]) & (other[None, :, 2] < cy1[:, None]))
                bad = pair_rule & zov & (hov | (np.hypot(gx, gy) + 1e-9 < clr))
                ok &= ~bad.any(axis=1)
        if all_boxes:
            ovh = g("over_height_m") > 0
            if ovh.any():
                vc = _parameter(self.instance, "expert.e9.over_height", "vertical_clearance", "available_clearance_m")
                avail = np.min(np.where(stk >= 0, (T.stack_base_z + T.stack_hmax)[np.maximum(stk, 0)], np.inf), axis=1)
                for k in np.flatnonzero(ovh).tolist():
                    a = avail[k]
                    c = vc.get(T.slot_ids[ss[k]]) if isinstance(vc, dict) else vc
                    if c is not None:
                        parsed = _nonnegative(c)
                        if parsed is None:
                            ok[k] = False
                            continue
                        a = min(a, cz0[k] + parsed)
                    if cz1[k] > a + 1e-9:
                        ok[k] = False
        return ok

    def _tight_zone(self, b: int, s: int) -> np.ndarray:
        """Stacks that can hold a box within the E9 clearance gap of box ``b`` at slot ``s`` (or its supports):
        a rectangle of half-sizes clearance + b's half length/width + the largest box half length/width in the
        instance (overhangs included). The envelope rules read nothing outside it."""
        key = (s, float(self._hl[b]), float(self._hw[b]))
        z = self._tz_cache.get(key)
        if z is None:
            T = self.T
            if getattr(self, "_hmax_dims", None) is None:
                dims = np.array([self._half_dims(c) for c in self._boxes]).reshape(-1, 2)
                self._hmax_dims = dims.max(axis=0)
                self._clr = self._clearance()
            hlm, hwm = self._hmax_dims
            dx = np.abs(self._slot_xy[:, 0] - self._slot_xy[s, 0])
            dy = np.abs(self._slot_xy[:, 1] - self._slot_xy[s, 1])
            near = (dx < self._clr + key[1] + hlm + 1e-6) & (dy < self._clr + key[2] + hwm + 1e-6)
            stk = T.slot_stacks[near]
            z = self._tz_cache[key] = np.unique(np.concatenate([stk[stk >= 0], T.slot_stacks[s][T.slot_stacks[s] >= 0]]))
        return z

    def _special_ok(self, st: _State, b: int, s: int, geo) -> bool:
        """2.4's E8/E9 envelope checks for the candidate (``special_violations`` focused on it), exactly, on a ship
        holding the candidate, every placed box whose envelope could come within the OOG clearance of it (a
        conservative horizontal-gap bound: centres minus half lengths/widths incl. overhangs) and the boxes under
        each of them (floors). A box beyond that bound can't take part in any pair check, so the answer equals the
        full call. Cached on exactly those placements."""
        from stowage.checker.rules_special import special_violations

        T, ex = self.T, st.ex
        slot, cid = T.slot_ids[s], T.box_ids[b]
        hl, hw = float(self._hl[b]), float(self._hw[b])
        nkey = (s, hl, hw)
        parts = st.sp.get(nkey)
        zone = self._oog_zone[s] if self._oog_zone is not None else ()
        zver = tuple(st.ver[list(zone)].tolist()) if zone else ()
        if parts is None:
            hit = self._sp_cache.get(nkey)
            if hit is not None and hit[0] == zver:
                parts = st.sp[nkey] = hit[1]
        if parts is None:
            parts = list(self._below(ex, slot))
            if geo is not None and len(geo[0]):
                ids, slots, xy, hd = geo
                dx = np.abs(xy[:, 0] - self._slot_xy[s, 0]) - hd[:, 0] - hl
                dy = np.abs(xy[:, 1] - self._slot_xy[s, 1]) - hd[:, 1] - hw
                gap = np.hypot(np.maximum(dx, 0), np.maximum(dy, 0))
                for i in np.flatnonzero(gap < self._clearance() + 1e-6).tolist():
                    parts.append((slots[i], ids[i]))
                    parts.extend(self._below(ex, slots[i]))
            parts = st.sp[nkey] = tuple(sorted(set(parts)))
            self._sp_cache[nkey] = (zver, parts)
        # the rules read only these box facts, so boxes sharing them share the answer
        sig = self._sigs.get(b)
        if sig is None:
            bx = self._boxes[cid]
            sig = self._sigs[b] = (bx.length_ft, bx.height_m, bx.oog.model_dump_json() if bx.oog else None,
                                   tuple(sorted(map(str, bx.special_stowage))))
        key = ("special", sig, s, parts)
        ok = self._exact_memo.get(key)
        if ok is None:
            self.diagnostics["exact_calls"] += 1
            ship = self.instance.onboard.model_copy(update={
                "placements": [Placement.model_construct(slot=a, container_id=c) for a, c in parts]
                + [Placement.model_construct(slot=slot, container_id=cid)], "covers_off": {}})
            vs = special_violations(self.instance, ship, call_index=self.instance.call_index,
                                    focus_container_id=cid)
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

    def _c1(self, st: _State, b_idx: np.ndarray, s_idx: np.ndarray, force: bool = False) -> np.ndarray:
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
        if feasible and not force:
            # any feasible flow will do; prefer one that leaves spare cells in every class (reserve 2H, then H),
            # so the slack shortcuts apply to more candidates
            H = self._stack_cells_pad.shape[1]
            for r in (2 * H, H):
                ok_r, f_r = flow.flow(demand, capacity - np.minimum(capacity, r))
                self.diagnostics["c1a_flows"] += 1
                if ok_r:
                    f = f_r
                    break
        fails = self._c1_fails = {}
        self._c1a_f = f if feasible else None
        if not feasible:
            fails["c1a"] = len(b_idx)
            return np.zeros(len(b_idx), dtype=bool)
        spare = capacity - f.sum(axis=0)
        if not force and self._c1_slack(st, rem, usable, caps, spare):
            r = self._c1f_screen(st, rem, b_idx, s_idx)
            fails["c1f"] = int((~r).sum())
            g_ = self._c1g(st, b_idx, s_idx, rem)
            fails["c1g"] = int((~g_).sum())
            return ok & r & g_
        if not force and self._c1_slack(st, rem, usable, caps, spare, check_a=False):
            # only C1a can bind: a candidate whose stacks' usable cells all lie in cell classes with spare ≥ 2H
            # removes only unused cells from the current flow, so it passes C1a; screen just the others in full
            H = self._stack_cells_pad.shape[1]
            low = spare < 2 * H
            SC = self._stack_cells_pad
            pos = st.fill[:, None] + np.arange(H)[None, :]
            inwin = (np.arange(H)[None, :] < usable[:, None]) & (pos < H)
            cell = SC[np.arange(len(SC))[:, None], np.minimum(pos, H - 1)]
            stack_low = (inwin & (cell >= 0) & low[flow.cell_class[np.maximum(cell, 0)]]).any(axis=1)
            stk = T.slot_stacks[s_idx]
            touch = stack_low[np.maximum(stk[:, 0], 0)] | ((stk[:, 1] >= 0) & stack_low[np.maximum(stk[:, 1], 0)])
            out = np.ones(len(b_idx), dtype=bool)
            rest = np.flatnonzero(~touch)
            if len(rest):
                r = self._c1f_screen(st, rem, b_idx[rest], s_idx[rest]) & self._c1g(st, b_idx[rest], s_idx[rest], rem)
                out[rest] = r
                nf = int((~r).sum())
            else:
                nf = 0
            idx = np.flatnonzero(touch)
            if len(idx):
                out[idx] = self._c1(st, b_idx[idx], s_idx[idx], force=True)
            self._c1_fails["c1f"] = self._c1_fails.get("c1f", 0) + nf
            return out
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
        # ---- C1b20: 20' boxes vs cells of stacks without a long box (no 20' may stand above a 40'/45')
        rem20 = rem[T.length[rem] == 20]
        if len(rem20):
            usable20 = np.where(st.nlong > 0, 0, usable)
            fam20 = NestedHall(T.mass[rem20], np.ones(len(rem20), dtype=np.int64), caps, usable20)
            if not fam20.ok:
                fails["c1b"] = len(b_idx)
                return np.zeros(len(b_idx), dtype=bool)
            is_long = T.length[b_idx] != 20
            old20, new20 = [], []
            for n in range(2):
                j = np.maximum(stacks[:, n], 0)
                valid = stacks[:, n] >= 0
                old20.append(np.where(valid, usable20[j], 0))
                new20.append(np.where(valid & ~is_long & (st.nlong[j] == 0), new_counts[n], 0))
            ok &= rec("c1b", fam20.screen(mb, np.ones(len(b_idx), dtype=np.int64), old_caps, new_caps, old20, new20,
                                          ~is_long))
        # ---- C1f E6 departure (D25): hold stacks a deck H box could still use must each get another box
        # ---- C1h per restricted compatibility class (reefer, under/on deck, DoC areas, ...): the weight-order
        # Hall checks over only the usable cells that class may use (20' classes: cells; long: stack pairs)
        ok &= rec("c1h", self._c1h(st, rem, usable, caps, b_idx, stacks, new_counts, old_caps, new_caps))
        # ---- C1g DG segregation capacity (7.2.4/7.4.3 against the DG cargo aboard, DoC areas)
        ok &= rec("c1g", self._c1g(st, b_idx, s_idx, rem))
        ok &= rec("c1f", self._c1f_screen(st, rem, b_idx, s_idx))
        return ok

    def _class_tables(self):
        """Per box class: compatible-cell indicator by stack position, prefix sums (nb, J, H+1); which classes
        are restricted (may not use some usable cell of their length)."""
        if getattr(self, "_cls_ps", None) is None:
            T, flow = self.T, self._flow
            SC = self._stack_cells_pad
            comp = np.where(SC[None, :, :] >= 0, flow.rows[:, np.maximum(SC, 0)], False)   # (nb, J, H)
            self._cls_ps = np.concatenate([np.zeros(comp.shape[:2] + (1,), dtype=np.int64),
                                           np.cumsum(comp, axis=2)], axis=2)
            n_cells = flow.rows.sum(axis=1)
            longs = np.zeros(flow.nb, dtype=bool)
            twenty = np.zeros(flow.nb, dtype=bool)
            np.logical_or.at(longs, flow.box_class, T.units == 2)
            np.logical_or.at(twenty, flow.box_class, T.units == 1)
            full20 = int(flow.rows[twenty].sum(axis=1).max(initial=0)) if twenty.any() else 0
            full40 = int(flow.rows[longs].sum(axis=1).max(initial=0)) if longs.any() else 0
            self._cls_restricted = np.where(longs & ~twenty, n_cells < full40,
                                            np.where(twenty & ~longs, n_cells < full20, True))
        return self._cls_ps, self._cls_restricted

    def _c1h(self, st, rem, usable, caps, b_idx, stacks, new_counts, old_caps, new_caps) -> np.ndarray:
        T, flow = self.T, self._flow
        N = len(b_idx)
        ok = np.ones(N, dtype=bool)
        PS, restricted = self._class_tables()
        H1 = PS.shape[2] - 1
        J = np.arange(len(T.stack_ids))
        bc_rem = flow.box_class[rem]
        top = int(usable.max(initial=0))
        P = self._pairs
        loss_pairs = self._member.shape[1] * 2 * top
        for c in np.unique(bc_rem).tolist():
            if not restricted[c]:
                continue
            fam = flow.box_class == c
            cnt = PS[c, J, np.minimum(st.fill + usable, H1)] - PS[c, J, np.minimum(st.fill, H1)]
            members = rem[bc_rem == c]
            twenty = T.units[members] == 1
            m20, ml = members[twenty], members[~twenty]
            cnt20 = np.where(st.nlong > 0, 0, cnt)
            hall20 = hall_l = None
            if len(m20):
                hall20 = NestedHall(T.mass[m20], np.ones(len(m20), dtype=np.int64), caps, cnt20)
                if not hall20.ok:
                    return np.zeros(N, dtype=bool)
                if hall20.min_surplus >= 2 * top:
                    hall20 = None                      # slack: no single placement can matter
            if len(ml):
                if len(P) == 0:
                    return np.zeros(N, dtype=bool)
                hall_l = NestedHall(T.mass[ml], np.ones(len(ml), dtype=np.int64),
                                    np.minimum(caps[P[:, 0]], caps[P[:, 1]]), self._pair_cnt(st, usable, cnt))
                if not hall_l.ok:
                    return np.zeros(N, dtype=bool)
                if hall_l.min_surplus >= loss_pairs:
                    hall_l = None
            if hall20 is None and hall_l is None:
                continue
            # only candidates that are members or take a compatible usable cell of this class can change it
            touch = fam[b_idx] | (cnt[np.maximum(stacks[:, 0], 0)] > 0) | (
                (stacks[:, 1] >= 0) & (cnt[np.maximum(stacks[:, 1], 0)] > 0))
            sub = np.flatnonzero(touch)
            if not len(sub):
                continue
            bs, ss = b_idx[sub], stacks[sub]
            inc = fam[bs]
            old_k, new_k = [], []
            for n in range(2):
                j = np.maximum(ss[:, n], 0)
                valid = ss[:, n] >= 0
                lo = np.minimum(st.fill[j] + 1, H1)
                hi = np.minimum(st.fill[j] + 1 + new_counts[n][sub], H1)
                old_k.append(np.where(valid, cnt[j], 0))
                new_k.append(np.where(valid, PS[c, j, hi] - PS[c, j, lo], 0))
            oc = [x[sub] for x in old_caps]
            nc = [x[sub] for x in new_caps]
            if hall20 is not None:
                is_long = T.length[bs] != 20
                o20 = [np.where(st.nlong[np.maximum(ss[:, n], 0)] > 0, 0, old_k[n]) for n in range(2)]
                n20 = [np.where(is_long | (st.nlong[np.maximum(ss[:, n], 0)] > 0), 0, new_k[n]) for n in range(2)]
                ok[sub] &= hall20.screen(T.mass[bs], np.ones(len(sub), dtype=np.int64), oc, nc, o20, n20,
                                         inc & ~is_long)
            if hall_l is not None:
                ok[sub] &= self._c1e(st, members, cnt, caps, bs, ss, old_k, new_k, nc, fam=fam,
                                     usable_all=usable, new_all=[x[sub] for x in new_counts])
        return ok

    def _c1_slack(self, st, rem, usable, caps, spare, check_a: bool = True) -> bool:
        """True if no single placement can break any C1 condition: every condition's slack exceeds the most one
        placement can take away (its ≤ 2 stacks' usable cells, ≤ 2·width stack pairs, its stacks' room). Exact
        shortcut: when it holds, the per-candidate screens would all pass."""
        T = self.T
        H = self._stack_cells_pad.shape[1]
        top = int(usable.max(initial=0))
        P = self._pairs
        loss_pairs = self._member.shape[1] * 2 * top
        if check_a and (spare < 2 * H).any():
            return False
        if NestedHall(T.mass[rem], T.units[rem], caps, usable).min_surplus < 2 * top:
            return False
        rem20 = rem[T.length[rem] == 20]
        if len(rem20) and NestedHall(T.mass[rem20], np.ones(len(rem20), dtype=np.int64), caps,
                                     np.where(st.nlong > 0, 0, usable)).min_surplus < 2 * top:
            return False
        P = self._pairs
        long_rem = rem[T.units[rem] == 2]
        if len(long_rem):
            if len(P) == 0:
                return False
            pc = self._pair_cnt(st, usable)
            loss = loss_pairs
            if int(pc.sum()) - len(long_rem) < loss:
                return False
            hall = NestedHall(T.mass[long_rem], np.ones(len(long_rem), dtype=np.int64),
                              np.minimum(caps[P[:, 0]], caps[P[:, 1]]), pc)
            if hall.min_surplus < loss:
                return False
        room = np.where(usable > 0, np.maximum(self._lim_ub(st.n20 > 0, st.nlong > 0) - st.smass, 0), 0)
        if float(room.sum()) - float(T.mass[rem].sum()) < 2 * float(room.max(initial=0)):
            return False
        # C1h classes: the same bound per restricted compatibility class
        PS, restricted = self._class_tables()
        flow = self._flow
        Jx = np.arange(len(T.stack_ids))
        for c in np.unique(flow.box_class[rem]).tolist():
            if not restricted[c]:
                continue
            members = rem[flow.box_class[rem] == c]
            cnt = PS[c, Jx, np.minimum(st.fill + usable, PS.shape[2] - 1)] - PS[c, Jx, np.minimum(st.fill, PS.shape[2] - 1)]
            m20, ml = members[T.units[members] == 1], members[T.units[members] == 2]
            if len(m20) and NestedHall(T.mass[m20], np.ones(len(m20), dtype=np.int64), caps,
                                       np.where(st.nlong > 0, 0, cnt)).min_surplus < 2 * top:
                return False
            if len(ml):
                if len(P) == 0 or NestedHall(T.mass[ml], np.ones(len(ml), dtype=np.int64),
                                             np.minimum(caps[P[:, 0]], caps[P[:, 1]]),
                                             self._pair_cnt(st, usable, cnt)).min_surplus < loss_pairs:
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
        """State-level E6 data (dict) or None when E6 is not a departure condition here (data missing or the deck
        fallback disabled — then the static mask already enforces it)."""
        T = self.T
        if T.e6_threshold is None or T.e6_fallback is not True:
            return None
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
        info = self._e6_eval(st, np.array(sorted(D), dtype=np.int64), excl, st.rem)
        info["excl"] = excl
        return info

    def _e6_departure_ok(self, st: _State, extra) -> bool:
        """2.4's exact E6 departure rule on the ship as it stands (plus ``extra`` = (slot, box) placed), departure
        cargo only. Missing E6 data / disabled fallback are handled by the static mask, so only E6 is read."""
        from stowage.checker.rules_weight import E6, e6_departure_violations

        T = self.T
        if T.e6_threshold is None or T.e6_fallback is not True:
            return True
        parts = [(loc[1], cid) for cid, loc in st.ex.where.items()
                 if loc[0] == "ship" and cid not in T.discharge]
        if extra is not None:
            parts.append(extra)
        if not any(self._attr[c][5] and self._attr[c][0] >= T.e6_threshold and parse_slot(sl).on_deck
                   for sl, c in parts):
            return True
        key = ("e6dep", tuple(sorted(parts)))
        ok = self._exact_memo.get(key)
        if ok is None:
            self.diagnostics["exact_calls"] += 1
            ship = self.instance.onboard.model_copy(update={
                "placements": [Placement.model_construct(slot=sl, container_id=c) for sl, c in sorted(parts)],
                "covers_off": {}})
            ok = self._exact_memo[key] = not any(
                v.rule_id == E6 for v in e6_departure_violations(self.instance, ship, call_index=self.instance.call_index))
        return ok

    def _e6_last(self, st: _State, cands) -> np.ndarray:
        """2.4's exact E6 departure rule for many final placements (box, slot) at once. 2.4 flags a deck class-H
        box d when some empty hold slot q accepts it (``compatible_empty_hold_slots``: d relocated to q, no hard
        ``placement_violations``). The sets for the ship without the candidate are computed once; a candidate
        changes them only where it can: slots it fills drop out; slots in its own stacks (incl. the one it opens
        above itself), pairs where DG segregation (both DG) or E9 clearance (either OOG) could involve it, and a
        deck-H candidate itself are re-judged by 2.4 on the post ship. Everything else is unchanged by 2.4's rules."""
        from stowage.checker.rules import placement_violations
        from stowage.checker.rules_weight import _compatible_slots

        T = self.T
        out = np.ones(len(cands), dtype=bool)
        if T.e6_threshold is None or T.e6_fallback is not True:
            return out
        thr = T.e6_threshold
        parts = [(loc[1], cid) for cid, loc in st.ex.where.items() if loc[0] == "ship" and cid not in T.discharge]
        ship_now = self.instance.onboard.model_copy(update={
            "placements": [Placement.model_construct(slot=sl, container_id=c) for sl, c in sorted(parts)],
            "covers_off": {}})
        heavy = lambda c: self._attr[c][5] and self._attr[c][0] >= thr  # noqa: E731
        # departure occupancy (discharge-list boxes gone), as 2.4 judges it
        occ_dep = {c for sl, _ in parts for c in cells_of_slot(sl)}

        def supported_empty(q, extra=()):
            cs = cells_of_slot(q)
            if any(c not in T.cell_index or c in occ_dep or c in extra for c in cs):
                return False
            for c in cs:
                ci = T.cell_index[c]
                below = T.stack_cells[int(T.cell_stack[ci])][:int(T.cell_k[ci])]
                if any(T.cell_ids[x] not in occ_dep and T.cell_ids[x] not in extra for x in below):
                    return False
            return True
        deck_h = [c for sl, c in parts if heavy(c) and parse_slot(sl).on_deck]
        key = ("e6base", tuple(sorted(parts)))
        base = self._exact_memo.get(key)
        if base is None:
            # = 2.4's compatible_empty_hold_slots, testing only supported empty slots (every cell the next free
            # cell of its stack): an unsupported one fails 2.4's bottom_up rule anyway
            base = {}
            for d in deck_h:
                qs = []
                for q in self._e6_fixed_slots(d, _compatible_slots):
                    if not supported_empty(q):
                        continue
                    if not any(v.severity == "hard" for v in placement_violations(
                            self.instance, ship_now, self._boxes[d], q, call_index=self.instance.call_index)):
                        qs.append(q)
                base[d] = qs
            self._exact_memo[key] = base
        occ_now = set(st.ex.occ)
        for i, (b, slot) in enumerate(cands):
            bcells = set(cells_of_slot(slot))
            bstacks = {int(T.cell_stack[T.cell_index[c]]) for c in bcells}
            b_dg, b_oog = self._attr[b][6], self._attr[b][7]
            post = None

            def post_ship():
                nonlocal post
                if post is None:
                    post = ship_now.model_copy(update={"placements": list(ship_now.placements)
                                                       + [Placement.model_construct(slot=slot, container_id=b)]})
                return post

            def legal_at(d, q):
                return not any(v.severity == "hard" for v in placement_violations(
                    self.instance, post_ship(), self._boxes[d], q, call_index=self.instance.call_index))

            bad = False
            # a deck-H candidate: its own compatible empty hold slots on the post ship
            if heavy(b) and parse_slot(slot).on_deck:
                for q in self._e6_fixed_slots(b, _compatible_slots):
                    if not supported_empty(q, bcells):
                        continue
                    if legal_at(b, q):
                        bad = True
                        break
            for d in deck_h:
                if bad:
                    break
                for q in base[d]:
                    qcells = set(cells_of_slot(q))
                    if qcells & bcells:
                        continue                                  # the candidate fills it
                    qstacks = {int(T.cell_stack[T.cell_index[c]]) for c in qcells}
                    near = (b_dg and self._attr[d][6]) or b_oog or self._attr[d][7] or (qstacks & bstacks)
                    if not near or legal_at(d, q):
                        bad = True
                        break
                if bad:
                    break
                # slots the candidate opens above itself (newly supported, empty): judged by 2.4
                for q in self._e6_fixed_slots(d, _compatible_slots):
                    qcells = set(cells_of_slot(q))
                    if q in base[d] or not supported_empty(q, bcells):
                        continue
                    qst = {int(T.cell_stack[T.cell_index[c]]) for c in qcells}
                    if not (qst & bstacks):
                        continue
                    if legal_at(d, q):
                        bad = True
                        break
            out[i] = not bad
        return out

    def _e6_fixed_slots(self, d, fn):
        """2.4's fixed hold capability of box d (``rules_weight._compatible_slots``), cached (static)."""
        key = ("e6fixed", d)
        r = self._exact_memo.get(key)
        if r is None:
            r = self._exact_memo[key] = tuple(fn(self.instance, self._boxes[d]))
        return r

    def _e6_blockers(self, D: np.ndarray) -> np.ndarray:
        """(B,) boxes whose placement into a hold stack makes it incompatible for every deck H box in D at once:
        a long box (no 20' above a 40'), a top-stowage / over-height box (closes the stack), a class lighter than
        every d by more than the E2 tolerance, a non-heavy box under E1 (all d heavy), or any box when E2/E1 data
        are missing (then nothing may stand on it)."""
        T = self.T
        blk = (T.length != 20) | T.closes_stack
        if not T.e2_valid or T.e1_threshold is None or len(D) == 0:
            return np.ones(len(T.box_ids), dtype=bool) if len(D) else blk
        blk |= T.valid_mass & (T.rank + T.e2_tol < T.rank[D].min())
        e1 = T.e1_threshold
        if T.mass[D].min() >= e1:
            blk |= T.valid_mass & (T.mass < e1)
        return blk

    def _e6_k(self, D, blk, fill, smass, sh, j):
        """Fewest non-blocker boxes that can end stack j's compatibility for every d (exhaust its cells, stack
        mass room or height room), best case: the heaviest/tallest non-blocker of the whole instance (fixed, so a
        single placement only changes its own stacks' values)."""
        T = self.T
        nb = ~blk
        maxs = float(T.share[nb].max()) if nb.any() else np.inf
        maxh = float(T.height[nb].max()) if nb.any() else np.inf
        sD, hD = float(T.share[D].min()), float(T.height[D].min())
        free = np.maximum(self._reach[j] - fill, 0)
        room = T.stack_lim_20[j] - smass
        hroom = T.stack_hmax[j] - sh
        with np.errstate(divide="ignore", invalid="ignore"):
            km = np.floor((room - sD) / maxs + 1e-9) + 1
            kh = np.floor((hroom - hD) / maxh + 1e-9) + 1
        k = np.minimum(free, np.minimum(np.maximum(km, 1), np.maximum(kh, 1)))
        return np.clip(k, 1, self._kmax).astype(np.int64)

    def _e6_eval(self, st, D, excl, rem_mask):
        T = self.T
        J = np.arange(len(T.stack_ids))
        rem = np.flatnonzero(rem_mask)
        U = int(T.units[rem].sum())
        if len(D) == 0:
            return dict(D=D, blocking=np.zeros(len(J), dtype=bool), k=np.zeros(len(J), dtype=np.int64), U=U,
                        n_lo=U, n_hi=0, need=0, ok=True, blk=None)
        comp = self._e6_compat(D, J, st.fill, st.closed | st.poisoned, st.nlong, st.smass, st.sh, st.lowrank,
                               st.nonheavy, excl)
        blocking = comp.any(axis=0)
        blk = self._e6_blockers(D)
        k = np.where(blocking, self._e6_k(D, blk, st.fill, st.smass, st.sh, J), 0)
        n_lo = int(T.units[rem][blk[rem]].sum())
        n_hi = U - n_lo
        hist = np.bincount(k[blocking], minlength=self._kmax + 1)[None, :]
        need = int(self._need(hist, np.array([n_lo]))[0])
        return dict(D=D, blocking=blocking, k=k, U=U, n_lo=n_lo, n_hi=n_hi, need=need, ok=need <= n_hi, blk=blk)

    @staticmethod
    def _need(hist: np.ndarray, n_lo: np.ndarray) -> np.ndarray:
        """Per row: Σ k over blocking stacks minus the n_lo largest k (stacks a blocker box unblocks for 1 unit;
        greedy on the largest k is optimal). ``hist[:, v]`` = number of blocking stacks with k = v."""
        V = hist.shape[1]
        vals = np.arange(V)
        total = (hist * vals[None, :]).sum(axis=1)
        top = np.zeros(len(hist), dtype=np.int64)
        left = n_lo.astype(np.int64).copy()
        for v in range(V - 1, 0, -1):
            t = np.minimum(hist[:, v], np.maximum(left, 0))
            top += t * v
            left -= t
        return total - top

    def _c1f_screen(self, st: _State, rem, b_idx, s_idx) -> np.ndarray:
        """Post-state E6 condition per candidate, exactly: (A) a qualifying H box going on deck joins D (full
        recompute per unique box; skipped when even the worst case fits); (B) a hold placement changes its own
        stacks' blocking / k; every candidate moves its units out of the blocker or non-blocker pool."""
        T = self.T
        N = len(b_idx)
        info = self._c1f_state(st)
        if info is None:
            return np.ones(N, dtype=bool)
        qual = self._e6_qual()
        D, blocking, k, U = info["D"], info["blocking"], info["k"], info["U"]
        u_b = T.units[b_idx]
        ok = np.ones(N, dtype=bool)
        deck = T.slot_deck[s_idx]
        Jh = int((~T.stack_deck).sum())
        # (A) deck H candidates
        a = np.flatnonzero(deck & qual[b_idx])
        if len(a) and Jh * self._kmax > U - 2:
            for b in np.unique(b_idx[a]).tolist():
                rm = st.rem.copy()
                rm[b] = False
                post = self._e6_eval(st, np.append(D, b), info["excl"], rm)
                ok[a[b_idx[a] == b]] = post["ok"]
        if len(D) == 0:
            return ok
        blk = info["blk"]
        n_lo = info["n_lo"] - np.where(blk[b_idx], u_b, 0)
        n_hi = info["n_hi"] - np.where(blk[b_idx], 0, u_b)
        rest = np.flatnonzero(~(deck & qual[b_idx]))
        if not len(rest):
            return ok
        base = np.bincount(k[blocking], minlength=self._kmax + 1)
        slack = info["n_hi"] - info["need"]
        # a hold placement changes ≤ 2 stacks' k (to smaller or none) and moves ≤ 2 units: bounded effect
        if slack >= 2 * (self._kmax + 1) + 2:
            return ok
        hist = np.tile(base, (len(rest), 1))
        r_b, r_s = b_idx[rest], s_idx[rest]
        hold = ~T.slot_deck[r_s]
        rows = np.arange(len(rest))
        e1 = T.e1_threshold
        for n in range(2):
            j = T.slot_stacks[r_s, n]
            valid = (j >= 0) & hold
            jj = np.maximum(j, 0)
            was = valid & blocking[jj]
            np.subtract.at(hist, (rows[was], k[jj[was]]), 1)
            fill, smass, sh = st.fill[jj] + 1, st.smass[jj] + T.share[r_b], st.sh[jj] + T.height[r_b]
            post = self._e6_compat(
                D, jj, fill, st.closed[jj] | st.poisoned[jj] | T.closes_stack[r_b],
                st.nlong[jj] + (T.length[r_b] != 20), smass, sh,
                np.where(T.valid_mass[r_b], np.minimum(st.lowrank[jj], T.rank[r_b]), st.lowrank[jj]),
                st.nonheavy[jj] | (T.valid_mass[r_b] & (T.mass[r_b] < (e1 if e1 is not None else -np.inf))),
                info["excl"][jj]).any(axis=0) & valid
            kp = self._e6_k(D, blk, fill, smass, sh, jj)
            np.add.at(hist, (rows[post], kp[post]), 1)
        need = self._need(hist, n_lo[rest])
        ok[rest] = need <= n_hi[rest]
        return ok

    @staticmethod
    def _aligned(fa, ua, ca, fb, ub, cb):
        """Long boxes a stack pair can still take: both cells free at the same tier, so tiers from the higher
        fill to the lower top (fill + usable); levelling the lower stack with 20' boxes never raises the higher
        fill. Also ≤ the pair's compatible counts ``ca``/``cb`` (class-restricted families)."""
        return np.maximum(np.minimum(np.minimum(fa + ua, fb + ub) - np.maximum(fa, fb), np.minimum(ca, cb)), 0)

    def _pair_cnt(self, st, usable, cnt=None):
        P = self._pairs
        cnt = usable if cnt is None else cnt
        a, b = P[:, 0], P[:, 1]
        return self._aligned(st.fill[a], usable[a], cnt[a], st.fill[b], usable[b], cnt[b])

    def _c1c(self, st, rem, usable, b_idx, stacks, old_counts, new_counts) -> np.ndarray:
        T, P = self.T, self._pairs
        long_dem = int((T.units[rem] == 2).sum())
        if long_dem == 0:
            return np.ones(len(b_idx), dtype=bool)
        if len(P) == 0:
            return np.zeros(len(b_idx), dtype=bool)
        cap = self._pair_cnt(st, usable)
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
                    a, b = int(P[pi][0]), int(P[pi][1])
                    fa, ua = (int(st.fill[a]) + 1, new[a]) if a in new else (int(st.fill[a]), int(usable[a]))
                    fb, ub = (int(st.fill[b]) + 1, new[b]) if b in new else (int(st.fill[b]), int(usable[b]))
                    loss[k] += int(cap[pi]) - max(min(min(fa + ua, fb + ub) - max(fa, fb), ua, ub), 0)
        return total - loss >= dem_after

    def _c1e(self, st, rem, usable, caps, b_idx, stacks, old_counts, new_counts, new_caps,
             fam=None, usable_all=None, new_all=None) -> np.ndarray:
        """Long boxes vs stack pairs nested by cap. ``fam``: (B,) bool restricting the boxes (a compatibility
        class; ``usable``/counts are then that class's compatible usable cells)."""
        T, P = self.T, self._pairs
        N = len(b_idx)
        long_rem = rem[T.units[rem] == 2]
        if fam is not None:
            long_rem = long_rem[fam[long_rem]]
        if len(long_rem) == 0:
            return np.ones(N, dtype=bool)
        if len(P) == 0:
            return np.zeros(N, dtype=bool)
        usable_all = usable if usable_all is None else usable_all
        new_all = new_counts if new_all is None else new_all
        pcap = np.minimum(caps[P[:, 0]], caps[P[:, 1]])
        pcnt = self._pair_cnt(st, usable_all, usable)
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

            def after(stk, cur_cap, cur_cnt, cur_fill, cur_use):
                cap_v, cnt_v, fill_v, use_v = cur_cap.copy(), cur_cnt.copy(), cur_fill.copy(), cur_use.copy()
                for n in range(2):
                    hit = stk == stacks[:, n]
                    cap_v = np.where(hit, new_caps[n], cap_v)
                    cnt_v = np.where(hit, new_counts[n], cnt_v)
                    fill_v = np.where(hit, cur_fill + 1, fill_v)
                    use_v = np.where(hit, new_all[n], use_v)
                return cap_v, cnt_v, fill_v, use_v
            ca, ka, fa, ua = after(a, caps[a], usable[a], st.fill[a], usable_all[a])
            cb, kb, fb, ub = after(b, caps[b], usable[b], st.fill[b], usable_all[b])
            old_c.append(np.where(valid, pcap[p], -np.inf))
            new_c.append(np.where(valid, np.minimum(ca, cb), -np.inf))
            old_k.append(np.where(valid, pcnt[p], 0))
            new_k.append(np.where(valid, self._aligned(fa, ua, ka, fb, ub, kb), 0))
        return hall.screen(T.mass[b_idx], np.ones(N, dtype=np.int64), old_c, new_c, old_k, new_k,
                           (T.units[b_idx] == 2) & (fam[b_idx] if fam is not None else True))

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
        """Exact class-flow test of a post-state given by (box class, units, lost cell classes): warm-started
        repair of the state's flow (``ClassFlow.post_feasible``)."""
        flow = self._flow
        self.diagnostics["c1a_flows"] += 1
        if getattr(self, "_c1a_f", None) is not None:
            return flow.post_feasible(self._c1a_f, demand, capacity, bc, units, lost)
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
        if not ok.all():
            # local donor shift for the slots that failed the ship-wide one
            fs = np.unique(s_idx[~ok])
            (Lu, Uu), (Ld, Ud) = g.local_intervals(st.ref, T.slot_cells[fs], T.slot_n[fs], self._donors[fs])
            pos = np.searchsorted(fs, s_idx)
            hitm = (~ok) & np.isin(s_idx, fs)
            pp = np.minimum(pos, len(fs) - 1)
            loc = ((mb >= Lu[pp] - 1e-6) & (mb <= Uu[pp] + 1e-6)) | ((mb >= Ld[pp] - 1e-6) & (mb <= Ud[pp] + 1e-6))
            ok |= hitm & loc
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

    def _ensure(self) -> _State:
        """Evaluate the current state once (index array + discharges); timed into ``stats``."""
        st = self._cur
        if not st.evaluated:
            t0 = perf_counter()
            if self._complete(st):
                st.legal, st.mask, st.discharges = (), np.zeros(0, dtype=np.int64), ()
            else:
                self._evaluate(st)
                if st.dead is not None:
                    self._stats.dead_ends += 1
            st.evaluated = True
            dt = perf_counter() - t0
            self._stats.legal_time_s += dt
            self._stats.max_legal_time_s = max(self._stats.max_legal_time_s, dt)
        return st

    def legal_actions(self):
        self._stats.legal_calls += 1
        st = self._ensure()
        if st.legal is None:
            T = self.T
            flat = st.mask
            nS = len(T.slot_ids)
            for f in flat[~self._have[flat]].tolist():
                self._objs[f] = Place(T.box_ids[f // nS], T.slot_ids[f % nS])
            self._have[flat] = True
            st.legal = tuple(self._objs[flat].tolist()) + tuple(st.discharges)
        return st.legal

    # ------------------------------------------------------------------ index-array access (for the 5.1 mask)
    @property
    def index_boxes(self) -> tuple[str, ...]:
        """Box ids in index order (sorted ids): ``box index = flat // n_slots``."""
        return tuple(self.T.box_ids)

    @property
    def index_slots(self) -> tuple[str, ...]:
        """Slot ids in index order (sorted BBBRRTT; 20' cells and 40'/45' slots): ``slot index = flat % n_slots``."""
        return tuple(self.T.slot_ids)

    @property
    def n_slots(self) -> int:
        return len(self.T.slot_ids)

    def legal_index(self) -> np.ndarray:
        """The legal ``Place`` actions of the current state as a sorted, read-only int64 array of flat indices
        ``box_index * n_slots + slot_index`` — the same set and order as the ``Place`` part of
        ``legal_actions()`` (sort_key order), without building any action objects. Discharges (stage B) are
        listed by ``legal_discharges()``. Cost after the state is evaluated: O(1) (a view)."""
        v = self._ensure().mask.view()
        v.flags.writeable = False
        return v

    def legal_pairs(self) -> tuple[np.ndarray, np.ndarray]:
        """``legal_index()`` split into (box_index, slot_index) arrays."""
        flat = self.legal_index()
        return flat // self.n_slots, flat % self.n_slots

    def legal_discharges(self) -> tuple:
        """The legal ``Discharge`` actions (stage B), sorted; they follow the Place actions in ``legal_actions()``."""
        return tuple(self._ensure().discharges)

    def decode_index(self, flat: int) -> Place:
        """The ``Place`` action of one flat index (shared object, no allocation after the first time)."""
        f = int(flat)
        if not self._have[f]:
            nS = len(self.T.slot_ids)
            self._objs[f] = Place(self.T.box_ids[f // nS], self.T.slot_ids[f % nS])
            self._have[f] = True
        return self._objs[f]

    def index_of(self, action) -> int | None:
        """Flat index of a ``Place`` action (None if its box or slot is unknown to this instance)."""
        b = self.T.box_index.get(getattr(action, "box", None))
        s = self.T.slot_index.get(getattr(action, "slot", None))
        return None if b is None or s is None else b * len(self.T.slot_ids) + s

    def is_legal(self, action) -> bool:
        st = self._ensure()
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
                     rem=old.rem.copy(), dg_aboard=old.dg_aboard, ref=old.ref, action=action, parent=old)
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
                (Lu, Uu), (Ld, Ud) = g.local_intervals(ref, T.slot_cells[[s]], T.slot_n[[s]], self._donors[[s]])
                if L[0] - 1e-6 <= m <= U[0] + 1e-6:
                    ref = g.shift(ref, cells, m)
                elif (Lu[0] - 1e-6 <= m <= Uu[0] + 1e-6) or (Ld[0] - 1e-6 <= m <= Ud[0] + 1e-6):
                    ref = g.local_shift(ref, cells, m, self._donors[s])
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
        return self._ensure().dead

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
        info = self._c1f_state(st)
        c1f = info is None or info["ok"]
        if c1f and not st.rem.any() and not st.ex.buffer:
            c1f = self._e6_departure_ok(st, None)
        rem = np.flatnonzero(st.rem)
        out = dict(c1a=True, c1b=True, c1c=True, c1d=True, c1e=True, c1f=bool(c1f), c1g=True, c1h=True, c2=True)
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
        rem20 = rem[T.length[rem] == 20]
        if len(rem20):
            out["c1b"] &= NestedHall(T.mass[rem20], np.ones(len(rem20), dtype=np.int64), caps,
                                     np.where(st.nlong > 0, 0, usable)).ok
        P = self._pairs
        long_rem = rem[T.units[rem] == 2]
        if len(long_rem) and len(P):
            out["c1c"] = len(long_rem) <= int(self._pair_cnt(st, usable).sum())
            out["c1e"] = NestedHall(T.mass[long_rem], np.ones(len(long_rem), dtype=np.int64),
                                    np.minimum(caps[P[:, 0]], caps[P[:, 1]]),
                                    self._pair_cnt(st, usable)).ok
        elif len(long_rem):
            out["c1c"] = out["c1e"] = False
        remdg = rem[T.is_dg[rem]]
        if len(remdg):
            F = self._dg_F(st)
            out["c1g"] = all(int(F[c].sum()) >= int((T.dg_class[remdg] == c).sum()) for c in F)
        PS, restricted = self._class_tables()
        J = np.arange(len(T.stack_ids))
        out["c1h"] = True
        P = self._pairs
        for c in np.unique(flow.box_class[rem]).tolist():
            if not restricted[c]:
                continue
            members = rem[flow.box_class[rem] == c]
            cnt = PS[c, J, np.minimum(st.fill + usable, PS.shape[2] - 1)] - PS[c, J, np.minimum(st.fill, PS.shape[2] - 1)]
            m20, ml = members[T.units[members] == 1], members[T.units[members] == 2]
            if len(m20):
                out["c1h"] &= NestedHall(T.mass[m20], np.ones(len(m20), dtype=np.int64), caps,
                                         np.where(st.nlong > 0, 0, cnt)).ok
            if len(ml):
                out["c1h"] &= bool(len(P)) and NestedHall(T.mass[ml], np.ones(len(ml), dtype=np.int64),
                                                         np.minimum(caps[P[:, 0]], caps[P[:, 1]]),
                                                         self._pair_cnt(st, usable, cnt)).ok
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
