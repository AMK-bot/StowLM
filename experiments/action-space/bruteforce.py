"""Brute-force check of the ``boxslot`` action space (plan 3.8): how much do we lose by letting a rule pick relocation
targets, and how much would an industry-style category action lose?

**The question.** StowLM writes actions, not crane lifts. In the common case it writes only ``Place(box, slot)`` and
the expander picks where yard blockers go (the *default relocation rule*). Overrides (``Relocate``) exist, but every
override is extra tokens and extra things to learn. So: on how many instances is the best plan reachable with
``Place`` actions alone? And what would we lose with the coarser industry action "a box of this category goes to this
slot" (E10's two-step: category → slot, then a fixed rule picks the actual yard box)?

**The method: exact search (uniform-cost search / Dijkstra, with an A* lower bound).** Think of every possible world
(where each box is, which hatch covers are off) as a node of a graph, and every action as an edge whose length is the
number of non-productive lifts it causes (relocations, restows, hatch-cover lifts — loads are free because every plan
loads every box exactly once). The best plan is the shortest path from the start node to any finished node (all
load-list boxes aboard, quay buffer empty), where finishing adds the cover-closing lifts and the projected later-port
restow/cover lifts (``project_future_moves``) of the final stow. Dijkstra's algorithm finds shortest paths by always
expanding the cheapest unexpanded node; since edge costs are never negative, the first finished node it pops is
optimal. A* is Dijkstra with an *admissible* estimate ``h`` of the remaining cost added to the priority (here: every
non-load box sitting above a not-yet-loaded box must be lifted at least once, so ``h`` = their number × relocate
weight — never an overestimate, so optimality is kept, it only skips hopeless nodes). Source: Hart, Nilsson & Raphael
(1968) "A formal basis for the heuristic determination of minimum cost paths"; any OR/AI textbook (Russell & Norvig,
ch. 3).

Two worlds are merged when they are the same up to swapping ship boxes with the same (POD, length): no stowage rule
is applied here, so such boxes are interchangeable for every future lift and for the later-port projection.

**Three action sets, searched with the same physics** (each expanded step is replayed lift by lift through the move
engine's transactional replay and dropped if the engine rejects it; the optimum's full plan is re-verified with
``apply_plan``, which must say legal with the same objective):
    (a) FULL — ``Place`` (load, put back from quay, or shift a ship box) plus voluntary ``Relocate(top box, any stack or
        empty ground position in the same block)`` and voluntary ``RestowOff``; at most ``CAP`` voluntary actions per
        plan (relocations + restows-off + shifts). By ``from_plan`` every legal move plan is an action sequence of
        this kind, so (a) is the true optimum among plans with ≤ CAP voluntary lifts.
    (b) DEFAULT-ONLY — ``Place`` actions only (yard relocations always by the default rule).
    (c) CATEGORY — action = (category, slot), category = (POD, length, heavy/light at 15,000 kg gross); the box is the
        category's yard box with the fewest boxes above it (tie: container id). Relocations by the default rule.
    (c) ⊆ (b) ⊆ (a) as sets of plans, so opt(a) ≤ opt(b) ≤ opt(c) must hold (asserted).

LIMITATIONS (labelled): stowage rules (weights, DG segregation, stability, lashing) are NOT applied — the checker is
not ready; the comparison is of move counts under identical physical constraints only. Instances are the tiny fixture
ship (16 cells, 2 hatch covers) and a 2-block yard, from ``stowage.actions.samples.tiny_instance``. Voluntary moves are
capped at CAP; voluntary relocations stay inside their block (one yard-crane lift, same as the default rule).

Run (≈ minutes, 1 core):
    nice -n 10 uv run python experiments/action-space/bruteforce.py --seeds 0-199 --grid --out experiments/action-space
    nice -n 10 uv run python experiments/action-space/bruteforce.py --stress --p40 0 --seeds 0-149 --grid \
        --time-cap 10 --out experiments/action-space --name bruteforce_stress
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import resource
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from stowage.actions import get_action_space
from stowage.actions.expander import COVER_KINDS, Expander
from stowage.actions.samples import legal_slots, tiny_instance
from stowage.actions.space import Place, Relocate, RestowOff
from stowage.model.ship import Placement, ShipState
from stowage.moves import IllegalMoveError, apply_plan, initial_state, project_future_moves
from stowage.moves._physical import Replay

CAP = 2  # voluntary actions (Relocate / RestowOff / ship-to-ship Place) per plan in action set (a)
HEAVY_KG = 15_000
MODES = ("full", "default", "category")


class Budget(Exception):
    pass


def copy_replay(r: Replay) -> Replay:
    """Cheap independent copy of the move engine's replay (its mutable indices only)."""
    new = object.__new__(Replay)
    new.__dict__.update(r.__dict__)
    new.yard = {k: list(v) for k, v in r.yard.items()}
    new.yard_lengths = dict(r.yard_lengths)
    new.footprints = dict(r.footprints)
    new.locations = dict(r.locations)
    new.ship = dict(r.ship)
    new.occupied = dict(r.occupied)
    new.buffer = dict(r.buffer)
    new.covers_off = dict(r.covers_off)
    return new


class Search:
    def __init__(self, instance, mode: str, *, time_cap: float, node_cap: int):
        self.inst = instance
        self.mode = mode
        self.w = instance.objective
        self.time_cap, self.node_cap = time_cap, node_cap
        self.load = list(instance.load_list)
        self.load_set = set(self.load)
        self.boxes = {b.id: b for b in instance.containers}
        self.cls = {cid: (b.pod, b.length_ft) for cid, b in self.boxes.items()}
        self.cat = {cid: (b.pod, b.length_ft, b.gross_mass_kg >= HEAVY_KG) for cid, b in self.boxes.items()}
        self.lift_cost = {"relocate": self.w.relocate, "restow_off": self.w.restow, "restow_on": self.w.restow,
                          "restow_shift": self.w.restow, "hatch_cover_off": self.w.hatch_cover,
                          "hatch_cover_on": self.w.hatch_cover, "load": 0.0}
        self.future_cache: dict = {}
        self.blocks = {b.id: b for b in instance.port.yard.blocks}
        self.expanded = 0

    # --------------------------------------------------------------- state helpers
    def key(self, ex: Expander, used: int):
        yard = tuple(sorted((k, tuple(v)) for k, v in ex.yard.items()))
        ship = tuple(sorted((loc[1], self.cls[c]) for c, loc in ex.where.items() if loc[0] == "ship"))
        buf = tuple(sorted(self.cls[c] for c in ex.buffer))
        return (yard, ship, buf, tuple(sorted(ex.covers_off.items())), used)

    def h(self, ex: Expander) -> float:
        blockers = set()
        for boxes in ex.yard.values():
            low = next((i for i, c in enumerate(boxes) if c in self.load_set), None)
            if low is not None:
                blockers.update(c for c in boxes[low + 1:] if c not in self.load_set)
        return len(blockers) * self.w.relocate

    def done(self, ex: Expander) -> bool:
        return not ex.buffer and all(ex.where.get(c, ("",))[0] == "ship" for c in self.load)

    def terminal_cost(self, ex: Expander, rp: Replay) -> float | None:
        ex2, rp2 = ex.copy(), copy_replay(rp)
        moves = ex2.finish()
        try:
            for m in moves:
                rp2.move(m)
        except IllegalMoveError:
            return None
        cost = sum(self.w.hatch_cover for m in moves if m.kind in COVER_KINDS)
        ship_key = tuple(sorted((loc[1], self.cls[c]) for c, loc in ex.where.items() if loc[0] == "ship"))
        fut = self.future_cache.get(ship_key)
        if fut is None:
            ship = ShipState(vessel_id=self.inst.vessel.id,
                             placements=[Placement(slot=loc[1], container_id=c)
                                         for c, loc in sorted(ex.where.items(), key=lambda t: t[1]) if loc[0] == "ship"])
            fm = project_future_moves(self.inst, ship, from_call=self.inst.call_index)
            fut = (self.w.future_restow * sum(f.restow_lifts for f in fm)
                   + self.w.future_hatch_cover * sum(f.hatch_cover_lifts for f in fm))
            self.future_cache[ship_key] = fut
        return cost + fut

    # --------------------------------------------------------------- successors
    def actions(self, ex: Expander, used: int):
        out: list[tuple[object, int]] = []
        for c in sorted(ex.buffer):
            out += [(Place(c, s), 0) for s in legal_slots(self.inst, ex, c)]
        todo = [c for c in self.load if ex.where.get(c, ("",))[0] == "yard"]
        if self.mode == "category":
            best: dict = {}
            for c in todo:
                key = ex.where[c][1]
                above = len(ex.yard[key]) - 1 - ex.yard[key].index(c)
                cand = (above, c)
                if self.cat[c] not in best or cand < best[self.cat[c]]:
                    best[self.cat[c]] = cand
            todo = sorted(c for _, c in best.values())
        for c in todo:
            out += [(Place(c, s), 0) for s in legal_slots(self.inst, ex, c)]
        if self.mode == "full" and used < CAP:
            for src, boxes in sorted(ex.yard.items()):
                top = boxes[-1]
                for dst in self.reloc_targets(ex, src, top):
                    out.append((Relocate(top, dst), 1))
            aboard = sorted(c for c, loc in ex.where.items() if loc[0] == "ship")
            for c in aboard:
                out.append((RestowOff(c), 1))
                out += [(Place(c, s), 1) for s in legal_slots(self.inst, ex, c)]
        return out

    def reloc_targets(self, ex: Expander, src: str, box: str) -> list[str]:
        blk = self.blocks[src.split("-")[0]]
        length = self.boxes[box].length_ft
        width = 1 if length == 20 else 2
        lim = ex.limit[blk.id]
        out = []
        for row in range(1, blk.n_rows + 1):
            for bay in range(1, blk.n_bays - width + 2):
                key = f"{blk.id}-{bay:03d}-{row:02d}"
                if key == src:
                    continue
                if key in ex.yard:
                    if ex.ylen[key] == length and len(ex.yard[key]) < lim:
                        out.append(key)
                elif all((blk.id, bay + o, row) not in ex.foot
                         or (ex.foot[(blk.id, bay + o, row)] == src and len(ex.yard[src]) == 1)
                         for o in range(width)):
                    out.append(key)
        return out

    # --------------------------------------------------------------- main loop
    def run(self):
        t0 = time.monotonic()
        ex0 = Expander(self.inst)
        rp0 = Replay(self.inst, initial_state(self.inst))
        tie = itertools.count()
        # node: (f, used, left, tiebreak, g, kind, ex, rp, used, path) — path = linked list (action, parent)
        # priority: (f, voluntary used, boxes still to load, tiebreak, ...): ties go to nodes nearer the goal, which
        # only changes the order among equal-f nodes, never the optimum
        heap = [(self.h(ex0), 0, len(self.load), next(tie), 0.0, "open", ex0, rp0, 0, None)]
        best_g: dict = {}
        while heap:
            f, _, _, _, g, kind, ex, rp, used, path = heapq.heappop(heap)
            if kind == "final":
                acts = []
                while path is not None:
                    acts.append(path[0])
                    path = path[1]
                return f, used, acts[::-1]
            k = self.key(ex, used)
            if k in best_g:
                continue
            # dominance: same world reached with fewer voluntary actions at no higher cost
            base = k[:-1]
            if any((base + (u,)) in best_g and best_g[base + (u,)] <= g + 1e-9 for u in range(used)):
                continue
            best_g[k] = g
            self.expanded += 1
            if self.expanded > self.node_cap or time.monotonic() - t0 > self.time_cap:
                raise Budget()
            if self.done(ex):
                tc = self.terminal_cost(ex, rp)
                if tc is not None:
                    heapq.heappush(heap, (g + tc, used, -1, next(tie), g + tc, "final", None, None, used, path))
                continue
            for action, dv in self.actions(ex, used):
                ex2, rp2 = ex.copy(), copy_replay(rp)
                ex2.moves = []
                try:
                    moves = ex2.step(action)
                    for m in moves:
                        rp2.move(m)
                except (ValueError, IllegalMoveError):
                    continue
                if any(m.kind in ("gate_in", "gate_out", "discharge") for m in moves):
                    continue
                ex2.moves = []
                g2 = g + sum(self.lift_cost[m.kind] for m in moves)
                u2 = used + dv
                if self.key(ex2, u2) in best_g:
                    continue
                left = len(ex2.buffer) + sum(ex2.where.get(c, ("",))[0] == "yard" for c in self.load)
                heapq.heappush(heap, (g2 + self.h(ex2), u2, left, next(tie), g2, "open", ex2, rp2, u2, (action, path)))
        return None, None, None


def verify(inst, acts, value):
    space = get_action_space()
    plan = space.expand(inst, acts)
    res = apply_plan(inst, plan)
    if not res.legal or abs(res.count.weighted_nonproductive - value) > 1e-9:
        raise AssertionError(f"{inst.id}: re-verification failed legal={res.legal} "
                             f"value={res.count.weighted_nonproductive} vs search {value} {res.violations}")
    canon = space.from_plan(inst, plan)
    return plan, res, canon


def run_instance(seed: int, n_load: int, n_other: int, p40: float, time_cap: float, node_cap: int,
                 max_tiers: int = 3) -> dict:
    inst = tiny_instance(seed, n_load=n_load, n_other=n_other, p40=p40, max_tiers=max_tiers)
    row: dict = dict(seed=seed, id=inst.id, n_load=n_load, n_other=n_other, max_tiers=max_tiers,
                     n40=sum(b.length_ft == 40 for b in inst.containers))
    for mode in MODES:
        t0 = time.monotonic()
        s = Search(inst, mode, time_cap=time_cap, node_cap=node_cap)
        try:
            value, used, acts = s.run()
        except Budget:
            row[mode] = dict(status="budget", nodes=s.expanded, secs=round(time.monotonic() - t0, 2))
            continue
        rec = dict(status="ok" if value is not None else "infeasible", nodes=s.expanded,
                   secs=round(time.monotonic() - t0, 2))
        if value is not None:
            plan, res, canon = verify(inst, acts, value)
            c = res.count
            rec.update(opt=value, voluntary_used=used, actions=[get_action_space().encode(a) for a in acts],
                       override_relocates=sum(isinstance(a, Relocate) for a in canon),
                       override_restow_off=sum(isinstance(a, RestowOff) for a in canon),
                       relocations=c.relocations, restow_lifts=c.restow_lifts, cover_lifts=c.hatch_cover_lifts,
                       future_restow=sum(f.restow_lifts for f in c.future),
                       future_cover=sum(f.hatch_cover_lifts for f in c.future))
        row[mode] = rec
    ok = [row[m].get("opt") for m in MODES]
    if all(v is not None for v in ok):
        assert ok[0] <= ok[1] + 1e-9 <= ok[2] + 2e-9, (seed, ok)
    return row


def summarise(rows: list[dict]) -> str:
    good = [r for r in rows if all(r[m].get("status") == "ok" for m in MODES)]
    lines = []
    n = len(good)
    if not n:
        return "no complete instances"
    gb = [r["default"]["opt"] - r["full"]["opt"] for r in good]
    gc = [r["category"]["opt"] - r["full"]["opt"] for r in good]
    ov = Counter(r["full"]["override_relocates"] + r["full"]["override_restow_off"] for r in good)
    lines.append(f"complete instances: {n} / {len(rows)}")
    lines.append(f"opt(b)==opt(a): {sum(g == 0 for g in gb)}/{n} = {100 * sum(g == 0 for g in gb) / n:.1f}%")
    lines.append(f"gap b-a: mean {statistics.mean(gb):.3f}, max {max(gb):g}")
    lines.append(f"opt(c)>opt(a): {sum(g > 0 for g in gc)}/{n} = {100 * sum(g > 0 for g in gc) / n:.1f}%; "
                 f"gap c-a mean {statistics.mean(gc):.3f}, max {max(gc):g}")
    lines.append(f"override tokens in (a)'s optimal plans (canonical): {dict(sorted(ov.items()))}")
    lines.append(f"mean opt(a) {statistics.mean(r['full']['opt'] for r in good):.2f}")
    for nl in sorted({r["n_load"] for r in good}):
        sub = [r for r in good if r["n_load"] == nl]
        eq = sum(r["default"]["opt"] == r["full"]["opt"] for r in sub)
        gcs = [r["category"]["opt"] - r["full"]["opt"] for r in sub]
        lines.append(f"  n_load={nl}: n={len(sub)} b==a {eq}/{len(sub)}, c>a {sum(g > 0 for g in gcs)}, "
                     f"mean c-a {statistics.mean(gcs):.2f}, mean opt(a) {statistics.mean(r['full']['opt'] for r in sub):.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0-199")
    ap.add_argument("--p40", type=float, default=0.15)
    ap.add_argument("--time-cap", type=float, default=20.0, help="seconds per instance per action set")
    ap.add_argument("--node-cap", type=int, default=300_000)
    ap.add_argument("--grid", action="store_true",
                    help="every seed with every (n_load 3..6, n_other 2..4); default: one grid point per seed")
    ap.add_argument("--stress", action="store_true",
                    help="congested yard: tier limit 2, n_load 4..6, n_other 36/40 (48 ground x tier positions), "
                         "20' boxes only (use --p40 0)")
    ap.add_argument("--out", default=None, help="directory for the jsonl")
    ap.add_argument("--name", default="bruteforce", help="jsonl file stem")
    args = ap.parse_args()
    lo, hi = (int(x) for x in args.seeds.split("-"))
    rows = []
    t0 = time.monotonic()
    sink = open(Path(args.out) / f"{args.name}.jsonl", "w") if args.out else None
    if args.stress:
        grid, max_tiers = [(nl, no) for nl in (4, 5, 6) for no in (36, 40)], 2
    else:
        grid, max_tiers = [(nl, no) for nl in range(3, 7) for no in range(2, 5)], 3
    jobs = [(seed, nl, no) for seed in range(lo, hi + 1)
            for nl, no in (grid if args.grid else [grid[seed % len(grid)]])]
    for seed, nl, no in jobs:
        row = run_instance(seed, nl, no, args.p40, args.time_cap, args.node_cap, max_tiers)
        rows.append(row)
        if sink:
            sink.write(json.dumps(row) + "\n")
            sink.flush()
        brief = " ".join(f"{m}={row[m].get('opt', row[m]['status'])}({row[m]['secs']}s)" for m in MODES)
        print(f"seed {seed} L{row['n_load']} O{row['n_other']}: {brief}", file=sys.stderr, flush=True)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # macOS: bytes
    print(summarise(rows))
    print(f"wall {time.monotonic() - t0:.0f}s, peak RSS {rss:.0f} MB")


if __name__ == "__main__":
    main()
