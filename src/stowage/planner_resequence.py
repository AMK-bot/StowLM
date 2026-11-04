"""Shared in-port-aware lift resequencer (D75): terminal crane sequencing for every method's plan.

APPROXIMATION "terminal crane sequencing": one total lift order for concurrent cranes (D64 A5). The resequencer
permutes only lifts that are independent of each other; it never changes which moves happen, where a box goes or how
many moves there are. ``stowage.planner.finalize`` calls it first, so every path that reaches the ballast finisher
(``run_planner``, gold, StowLM best-of-k, GRPO rewards, repair, campaign rechecks) sees the same order. With
rule-freeze-v1 off it is a no-op (legacy pipeline unchanged bit for bit).

How independence is decided. The move engine's ``Replay.move`` validates a lift from the state of a small set of
resources: the box itself, the ship stacks of its source/target cells, the yard ground cells of its source/target
footprints, the hatch cover over a hold or deck stack, and the deck stacks / yard ground cells on its C6 crossing path
(``moves.crossing_paths``). Everything else it reads is static instance data. We replay the original plan once through
the engine and record, per lift, the resources it writes and reads. The dependency DAG then keeps:

- every pair of writes to the same resource in its original order (stack order on ship and in the yard, discharge
  before load in a cell, yard dig-outs before the dug box's lift, a box's own moves, hatch cover open/close);
- a strict read (a cover's state for hold access, a cover's deck stacks before it is lifted, a crossed yard ground
  cell) between the same two writes as originally;
- a crossed deck stack (D69) only inside the run of that stack's versions that stay below crane height H around the
  original read: the lift may move past writes that keep the stack below H, never past one that brings it to H.
  "Far rows before near rows while near rows still need lifts" is exactly this constraint.

Any order that respects the DAG gives every lift the same view of every resource it depends on as in the original
plan, so the engine accepts it and the final world is identical. The new order is replayed through the engine
anyway; if it does not replay exactly or does not reach the same final world, the original order is returned.

When it reorders (leader decision on logic-reseq). Only when the original order is predicted to fail in-port list:
its arrival-phase transverse moment (D64 pass 1: arrival fills plus the HEEL transfer) leaves the interval the HEEL
pair can absorb at some lift. Moments use the same formula as the checker's HEEL helper (cargo mass x cell TCG,
relocated covers); the interval comes from the HEEL pair's exact sounding moments between empty and full. A plan the
proxy predicts to pass is never touched.

How an order is chosen. Greedy with stable ties: take the earliest ready lift (original order) whenever the running
moment stays inside the interval shrunk by a spare band; otherwise take the ready lift that brings the moment closest
to it (then closest to the interval centre, then lowest original index). ASSUMPTION: a 25 % spare band and a one-step
greedy rule (no lookahead); both are tuning choices, not physics. The new order is adopted only if the peak HEEL
excess falls (or, with excess tied, the peak HEEL utilisation falls) AND neither guard proxy gets worse:
- trim: peak |sum of mass x (LCG - LCF)| over the lifts, the trimming moment change since arrival;
- bending: peak |sum of mass x (|LCG - Lpp/2| - Lpp/8)|, the midship still-water bending change for a weight added
  on a uniformly growing buoyancy.
APPROXIMATION: both guards are ranking proxies (LCF at design draught, uniform buoyancy increment, no trim
coupling); they only veto. Deterministic; ``time_limit_s`` is checked end to end, between phases and before
committing (one engine replay, O(n), is not interruptible). Any exception returns the original order. The full
checker's every-lift equilibrium replay still decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from stowage.model import Instance, MovePlan
from stowage.versions import RESEQUENCER_VERSION

SPARE_FRACTION = 0.25  # ASSUMPTION: share of the HEEL half-width kept in reserve before leaving the original order
_NOTE = ("APPROXIMATION: terminal crane sequencing (D75): one total lift order, only independent lifts permuted; "
         "reorders only on a predicted arrival HEEL list failure, trim/bending proxies must not worsen; "
         "the full checker decides")


@dataclass
class ResequenceOutcome:
    """``plan`` is the input plan when ``applied`` is False. ``report`` holds the pipeline report fields."""

    plan: MovePlan
    applied: bool
    report: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Lift:
    index: int
    writes: set = field(default_factory=set)
    strict_reads: set = field(default_factory=set)
    crossed_stacks: set = field(default_factory=set)
    moment_kgm: float = 0.0  # transverse moment change (mass x TCG)
    trim_kgm: float = 0.0  # trimming moment change (mass x (LCG - LCF))
    bending_kgm: float = 0.0  # midship bending change (mass x (|LCG - Lpp/2| - Lpp/8))


class _Timeout(Exception):
    pass


def _footprint(addr: str, length_ft: int) -> list[tuple]:
    from stowage.conventions import parse_yard

    parsed = parse_yard(addr)
    return [("ground", parsed.block, parsed.bay + offset, parsed.row)
            for offset in range(1 if length_ft == 20 else 2)]


def _lcf_m(instance: Instance) -> float:
    """LCF interpolated at the design draught (APPROXIMATION for the trim guard only)."""
    import numpy as np

    hull = instance.vessel.hull
    rows = hull.hydrostatics
    if not rows:
        return hull.lpp_m / 2
    return float(np.interp(hull.design_draught_m, [r.draught_m for r in rows], [r.lcf_m for r in rows]))


def _analyse(instance: Instance, plan: MovePlan, deadline: float):
    """Replay the original order once; return per-lift resources, stack heights and the final engine state.

    Every lift is validated by the engine before its resources are read, so an illegal original raises
    ``IllegalMoveError`` before any unchecked lookup.
    """
    from stowage.conventions import cells_of_slot
    from stowage.moves import initial_state
    from stowage.moves._paths import _CrossingGeometry, _indexed_crossing_paths
    from stowage.moves._physical import Replay

    replay = Replay(instance, initial_state(instance))
    geometry = _CrossingGeometry(instance, instance.port)
    cells = {cell.id: cell for cell in instance.vessel.cells}
    covers = {cover.id: cover for cover in instance.vessel.hatch_covers}
    boxes = replay.boxes
    use_paths = replay.crossing_enabled and instance.port.profile_kind != "next_call_environment"
    lpp, lcf = instance.vessel.hull.lpp_m, _lcf_m(instance)

    def height(sid):
        stack = replay.crossing_stacks[sid]
        return max((i + 1 for i, cell in enumerate(stack.cell_ids)
                    if cell in replay.occupied or cell in replay.fixed_occupied), default=0)

    def add_mass(lift, mass, tcg, lcg):
        lift.moment_kgm += mass * tcg
        lift.trim_kgm += mass * (lcg - lcf)
        lift.bending_kgm += mass * (abs(lcg - lpp / 2) - lpp / 8)

    def ship_side(slot, lift, mass):
        ids = cells_of_slot(slot)
        for cid in ids:
            stack = replay.stacks[cells[cid].stack_id]
            lift.writes.add(("stack", stack.id))
            if stack.hatch_cover_id is not None:
                lift.strict_reads.add(("cover", stack.hatch_cover_id))
        add_mass(lift, mass, sum(cells[c].tcg_m for c in ids) / len(ids), sum(cells[c].lcg_m for c in ids) / len(ids))

    initial_heights = {sid: height(sid) for sid in replay.crossing_stacks}
    lifts, stack_heights = [], {}  # stack_heights[sid] = [(lift index, height after)]
    for index, move in enumerate(plan.moves):
        if index % 64 == 0 and monotonic() > deadline:
            raise _Timeout("original replay")
        kind = move.kind
        rest = None
        src_length = None
        if kind == "hatch_cover_on":
            rest = replay.covers_off.get(move.cover_id)
        elif kind in ("relocate", "load", "gate_out") and getattr(move, "src", "truck") != "truck":
            src_length = replay.yard_lengths.get(move.src.rsplit("-", 1)[0])
        replay.move(move, move_index=index)  # validates first: raises IllegalMoveError for an illegal original
        lift = _Lift(index)
        if kind in ("hatch_cover_off", "hatch_cover_on"):
            cover = covers[move.cover_id]
            if kind == "hatch_cover_off":
                rest = move.dst
            lift.writes.add(("cover", move.cover_id))
            for cover_id in {move.cover_id, rest} - {None, "quay"}:
                lift.writes.add(("cover", cover_id))
                lift.strict_reads.update(("stack", sid) for sid in covers[cover_id].deck_stack_ids)
            # Off: the cover leaves its own centre for the quay or the cover it rests on; on: the reverse.
            sign = -1.0 if kind == "hatch_cover_off" else 1.0
            add_mass(lift, sign * cover.mass_kg, cover.tcg_m, cover.lcg_m)
            if rest in covers:
                add_mass(lift, -sign * cover.mass_kg, covers[rest].tcg_m, covers[rest].lcg_m)
        else:
            cid = move.container_id
            lift.writes.add(("box", cid))
            mass = boxes[cid].gross_mass_kg
            src, dst = getattr(move, "src", None), getattr(move, "dst", None)
            if src_length is not None:
                lift.writes.update(_footprint(src, src_length))
            if kind in ("relocate", "discharge", "gate_in") and dst not in (None, "truck"):
                lift.writes.update(_footprint(dst, boxes[cid].length_ft))
            if kind in ("discharge", "restow_shift", "restow_off"):
                ship_side(src, lift, -mass)
            if kind in ("load", "restow_shift", "restow_on"):
                ship_side(dst, lift, mass)
        if use_paths:
            path = _indexed_crossing_paths(move, port=instance.port, geometry=geometry)
            lift.crossed_stacks.update(sid for sid in path.ship_stack_ids if sid in replay.stacks)
            lift.strict_reads.update(("ground", *ground) for ground in path.yard_ground_slots)
        for resource in lift.writes:
            if resource[0] == "stack":
                stack_heights.setdefault(resource[1], []).append((index, height(resource[1])))
        lifts.append(lift)
    return lifts, initial_heights, stack_heights, replay.state(), replay.crane_height


def _dag(lifts, initial_heights, stack_heights, crane_height):
    """Successor lists and in-degrees of the precedence DAG (see module docstring)."""
    from bisect import bisect_left

    edges = set()
    writes: dict[tuple, list[int]] = {}
    for lift in lifts:
        for resource in lift.writes:
            writes.setdefault(resource, []).append(lift.index)
    for chain in writes.values():
        edges.update(zip(chain, chain[1:]))
    for lift in lifts:
        i = lift.index
        for resource in lift.strict_reads - lift.writes:
            chain = writes.get(resource, ())
            k = bisect_left(chain, i)
            if k:
                edges.add((chain[k - 1], i))
            if k < len(chain):
                edges.add((i, chain[k]))
        for sid in lift.crossed_stacks:
            history = stack_heights.get(sid, ())
            if not history:
                continue
            k = bisect_left([w for w, _ in history], i)  # history[:k] precede the read
            if crane_height is None:  # unknown H: the engine has no ship crossing check; keep strict order
                if k:
                    edges.add((history[k - 1][0], i))
                if k < len(history):
                    edges.add((i, history[k][0]))
                continue
            start = k
            while start > 0 and (history[start - 2][1] if start >= 2 else initial_heights[sid]) < crane_height:
                start -= 1
            # Versions history[start-1 .. k-1] (or the initial one) are all below H: the lift must follow the write
            # that opened this run and precede the first later write that reaches H.
            if start > 0:
                edges.add((history[start - 1][0], i))
            later = next((w for w, h in history[k:] if h >= crane_height), None)
            if later is not None:
                edges.add((i, later))
    successors = [[] for _ in lifts]
    indegree = [0] * len(lifts)
    for a, b in sorted(edges):
        if a != b:
            successors[a].append(b)
            indegree[b] += 1
    return successors, indegree, len(edges)


def _heel_interval(instance: Instance):
    """Arrival total transverse moment M0 and the interval of total moments the HEEL pair can absorb (kg m)."""
    from stowage.checker.in_port_liquids import prepare_context

    context = prepare_context(instance, instance.onboard).for_phase("arrival")
    tanks = {tank.id: tank for tank in instance.vessel.tanks}
    port, starboard = tanks[context.pair.port_tank], tanks[context.pair.starboard_tank]
    masses = instance.onboard.tank_masses_kg
    pm, sm = masses.get(port.id, 0.0), masses.get(starboard.id, 0.0)
    capacity = port.capacity_m3 * port.content_density_kg_m3
    lower, upper = max(-sm, pm - capacity), min(pm, capacity - sm)
    before = context._pair_moment(port, starboard, pm, sm)
    # The checker's residual is M - before + pair(after): transfer t absorbs M = before - pair(pm - t, sm + t).
    ends = [before - context._pair_moment(port, starboard, pm - t, sm + t) for t in (lower, upper)]
    moment = context._heel_moment(instance.onboard.model_copy(update={"tank_masses_kg": dict(masses)}), "arrival")
    return float(moment), min(ends), max(ends)


def _profile(order, lifts, m0, lo, hi):
    """Peak HEEL excess (kg m), peak HEEL utilisation, peak |trim moment change|, peak |bending change| (kg m)."""
    centre, half = (lo + hi) / 2, max((hi - lo) / 2, 1e-9)
    moment, excess, utilisation = m0, max(0.0, lo - m0, m0 - hi), abs(m0 - centre) / half
    trim = bending = trim_peak = bending_peak = 0.0
    for i in order:
        lift = lifts[i]
        moment += lift.moment_kgm
        trim += lift.trim_kgm
        bending += lift.bending_kgm
        excess = max(excess, lo - moment, moment - hi)
        utilisation = max(utilisation, abs(moment - centre) / half)
        trim_peak, bending_peak = max(trim_peak, abs(trim)), max(bending_peak, abs(bending))
    return excess, utilisation, trim_peak, bending_peak


def _canonical(state):
    return (sorted((p.container_id, p.slot) for p in state.ship.placements),
            sorted((s.key, tuple(s.boxes)) for s in state.yard.stacks),
            sorted(state.yard.quay_buffer), sorted(state.ship.covers_off.items()))


def resequence(instance: Instance, plan: MovePlan, *, time_limit_s: float = 30.0,
               spare_fraction: float = SPARE_FRACTION) -> ResequenceOutcome:
    """Return ``plan`` with its lifts reordered for in-port list, or unchanged (see module docstring). Never raises.

    The output has the same multiset of moves (asserted), replays exactly through the engine and reaches the same
    final world. Unchanged when rule-freeze-v1 is off, the plan has fewer than two moves, the instance has no usable
    HEEL pair, the original order is not predicted to fail list, the original plan does not replay, the time bound is
    hit, no improvement is found, a guard proxy would worsen, or anything raises.
    """
    from stowage.rules.catalogue import rule_freeze_v1_enabled

    started = monotonic()
    deadline = started + time_limit_s
    report: dict[str, Any] = {"version": RESEQUENCER_VERSION, "applied": False, "lifts_moved": 0,
                              "n_moves": len(plan.moves), "time_limit_s": time_limit_s, _NOTE: 1.0}

    def unchanged(reason):
        report.update(reason=reason, wall_s=monotonic() - started)
        return ResequenceOutcome(plan=plan, applied=False, report=report)

    if not rule_freeze_v1_enabled():
        return unchanged("rule_freeze_v1 off")
    if len(plan.moves) < 2:
        return unchanged("fewer than two moves")
    try:
        return _resequence(instance, plan, report, unchanged, started, deadline, spare_fraction)
    except _Timeout as exc:
        return unchanged(f"time bound {time_limit_s:g} s reached ({exc})")
    except Exception as exc:  # fail safe: the direct API never raises; the original order is kept
        from stowage.moves import IllegalMoveError

        if isinstance(exc, IllegalMoveError):
            return unchanged(f"original plan does not replay: {exc.violation.message}")
        return unchanged(f"resequencer error: {type(exc).__name__}: {exc}")


def _resequence(instance, plan, report, unchanged, started, deadline, spare_fraction):
    from bisect import insort

    from stowage.moves import apply_plan

    def check_deadline(phase):
        if monotonic() > deadline:
            raise _Timeout(phase)

    try:
        m0, lo, hi = _heel_interval(instance)
    except Exception as exc:  # no HEEL pair / geometry: nothing to balance against
        return unchanged(f"no HEEL interval: {type(exc).__name__}: {exc}")
    check_deadline("HEEL preparation")
    report.update(heel_interval_kgm=[lo, hi], arrival_moment_kgm=m0)
    lifts, initial_heights, stack_heights, final_state, crane_height = _analyse(instance, plan, deadline)
    check_deadline("original replay")
    before = _profile(range(len(lifts)), lifts, m0, lo, hi)
    tolerance = 1e-9 * max(1.0, hi - lo)
    predicted_fail = before[0] > tolerance
    report.update(raw_order_predicted_list_fail=predicted_fail, heel_excess_before_kgm=before[0],
                  heel_utilisation_before=before[1], trim_proxy_before_kgm=before[2],
                  bending_proxy_before_kgm=before[3])
    if not predicted_fail:
        return unchanged("original order not predicted to fail in-port list")
    successors, indegree, n_edges = _dag(lifts, initial_heights, stack_heights, crane_height)
    report["dag_edges"] = n_edges
    check_deadline("precedence graph")

    spare = spare_fraction * (hi - lo) / 2
    band_lo, band_hi, centre = lo + spare, hi - spare, (lo + hi) / 2

    def outside(value):
        return max(0.0, band_lo - value, value - band_hi)

    ready = sorted(i for i, d in enumerate(indegree) if d == 0)
    order, moment = [], m0
    while ready:
        if len(order) % 64 == 0:
            check_deadline("greedy order")
        first = ready[0]
        if outside(moment + lifts[first].moment_kgm) == 0.0:
            choice = first
        else:
            choice = min(ready, key=lambda i: (outside(moment + lifts[i].moment_kgm),
                                               abs(moment + lifts[i].moment_kgm - centre), i))
        ready.remove(choice)
        order.append(choice)
        moment += lifts[choice].moment_kgm
        for nxt in successors[choice]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                insort(ready, nxt)
    if len(order) != len(lifts):
        return unchanged("precedence graph has a cycle")  # cannot happen for a replayable plan; fail safe
    after = _profile(order, lifts, m0, lo, hi)
    moved = sum(1 for position, i in enumerate(order) if position != i)
    report.update(heel_excess_after_kgm=after[0], heel_utilisation_after=after[1],
                  trim_proxy_after_kgm=after[2], bending_proxy_after_kgm=after[3], candidate_lifts_moved=moved)
    if not moved:
        return unchanged("no independent lift can be moved")
    if after[0] < before[0] - tolerance:
        improved = True
    elif abs(after[0] - before[0]) <= tolerance:  # excess tied: fall back to spare HEEL capacity
        improved = after[1] < before[1] - 1e-9
    else:
        improved = False
    if not improved:
        return unchanged("no improvement over the original order")
    for name, a, b in (("trim", after[2], before[2]), ("bending", after[3], before[3])):
        if a > b * (1 + 1e-9) + tolerance:
            return unchanged(f"{name} proxy would get worse")
    check_deadline("profiling")

    moves = [plan.moves[i] for i in order]
    if sorted(m.model_dump_json() for m in moves) != sorted(m.model_dump_json() for m in plan.moves):
        return unchanged("move multiset changed")  # cannot happen: order is a permutation; fail safe
    candidate = plan.model_copy(update={"moves": moves, "actions": None})
    check_deadline("serialisation")
    original = apply_plan(instance, plan)
    check_deadline("original apply_plan")
    replayed = apply_plan(instance, candidate)
    check_deadline("candidate apply_plan")

    def verdict(result):  # an incomplete original (e.g. a partial plan) may still fail end-of-plan conditions
        return result.executed, [(v.rule_id, v.message) for v in result.violations], result.count
    if (replayed.executed != len(moves) or verdict(replayed) != verdict(original)
            or _canonical(replayed.final_state) != _canonical(final_state)):
        return unchanged("resequenced order failed exact engine replay")
    report.update(applied=True, lifts_moved=moved, actions_dropped=plan.actions is not None,
                  reason="original order predicted to fail list; resequenced", wall_s=monotonic() - started)
    return ResequenceOutcome(plan=candidate, applied=True, report=report)

