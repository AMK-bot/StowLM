"""Checker-gated loading order for an already chosen box/slot assignment.

This searches orders, never alternative final slots. Stack support and hold access
give a precedence graph; accessible yard boxes break ties. A bounded depth-first
search remembers the other ready actions when a production-mask prefix fails.
This is a heuristic, not a proof that an assignment has no executable order.
The D18 Expander owns yard retrieval and forced ship restows. Sources: the public
feasibility-engine contract; Caserta, Schwarze & Voss (2011), EJOR 219(1).

Diagnostics on rejected actions use a copied native engine's child-state builder
and public completion_check. These probes never admit an action or change the
search's engine. Successful actions always pass the original production mask.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place
from stowage.conventions import cells_of_slot, parse_slot
from stowage.feasibility import make_feasibility_engine
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.solver.securing_geometry import (
    securing_geometry_violation, securing_state_violations,
)


class SequenceFailure(ValueError):
    """A concrete rejected prefix and bounded-search diagnostics, never a proof."""

    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


@dataclass
class _Frame:
    engine: Any
    ex: Expander
    actions: tuple = ()
    choices: Any = None


def _pending(ex, targets):
    return {cid: slot for cid, slot in targets.items()
            if ex.where.get(cid) != ("ship", slot)}


def _covers(ex, slot):
    return {ex.stacks[ex.cells[cell].stack_id].hatch_cover_id
            for cell in cells_of_slot(slot)} - {None}


def _ready(instance, ex, targets):
    """Exact support predecessors and deliberate hold-before-deck precedence."""
    pending = _pending(ex, targets)
    hold_covers = set().union(*(_covers(ex, slot) for slot in pending.values()
                               if not parse_slot(slot).on_deck)) if pending else set()
    choices, deferred = [], []
    for cid in instance.discharge_list:
        location = ex.where.get(cid)
        if location and location[0] == "ship":
            address = parse_slot(location[1])
            choices.append(((-1, -address.tier, cid), Discharge(cid)))
    for cid, slot in pending.items():
        reason = None
        footprint = cells_of_slot(slot)
        if any(cell in ex.occ and ex.occ[cell] != cid for cell in footprint):
            reason = "target_occupied"
        elif any(any(below not in ex.occ for below in
                     ex.stacks[ex.cells[cell].stack_id].cell_ids[:ex.cell_pos[cell][1]])
                 for cell in footprint):
            reason = "support_predecessor_pending"
        elif parse_slot(slot).on_deck and _covers(ex, slot) & hold_covers:
            reason = "hold_before_deck_on_hatch"
        if reason:
            deferred.append({"box": cid, "slot": slot, "reason": reason})
            continue
        location, key = ex.where.get(cid, (None, None))
        blockers = (len(ex.yard[key])-ex.yard[key].index(cid)-1
                    if location == "yard" else 0)
        address = parse_slot(slot)
        # D18 can retrieve buried cargo; accessibility is a preference rather
        # than a hard rejection. Restore quay cargo after its hold prerequisites.
        if rule_freeze_v1_enabled():
            # D69 uses transverse coordinates, never numeric row order.
            y = sum(ex.cells[cell].tcg_m for cell in footprint) / len(footprint)
            far_first = y if instance.port.berthing_side == "port" else -y
            key = (int(address.on_deck), far_first, blockers, address.tier, slot, cid)
        else:
            key = (int(address.on_deck), blockers, address.tier, slot, cid)
        choices.append((key, Place(cid, slot)))
    return [action for _, action in sorted(choices, key=lambda item: item[0])], deferred


def _completion(engine):
    method = getattr(engine, "completion_check", None)
    if not callable(method):
        return {"available": False}
    try:
        result = {str(key): bool(value) for key, value in method().items()}
        error = getattr(engine, "_global_error", None)
        if error is not None:
            # Native completion_check reports C2=True when no screen exists;
            # a missing REQUIRED model is still fail-closed in the real mask.
            result.update(available=False, required_model_error=str(error))
        return result
    except Exception as error:
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _visibility(engine, ex):
    """Read D55's existing cap; this diagnostic never substitutes for the mask."""
    tables = getattr(engine, "T", None)
    caps = getattr(tables, "extra", {}).get("stack_visibility_top") if tables is not None else None
    if caps is None:
        return {"available": False}
    bad = []
    tops = {sid: float(stack.base_z_m) for sid, stack in ex.stacks.items()}
    for cid, (location, slot) in ex.where.items():
        if location != "ship":
            continue
        box = ex.boxes[cid]
        height = box.height_m + (box.oog.over_height_m if box.oog else 0.)
        for sid in {ex.cells[cell].stack_id for cell in cells_of_slot(slot)}:
            tops[sid] += height
    for index, sid in enumerate(tables.stack_ids):
        if tops[sid] > float(caps[index])+1e-9:
            bad.append({"stack": sid, "roof_m": tops[sid], "cap_m": float(caps[index])})
    return {"available": True, "passed": not bad, "violations": bad}


def _rejected_child(engine, action):
    """Read exact C1/C2 post-state checks without ever public-pushing rejection."""
    if not (callable(getattr(engine, "_child", None)) and hasattr(engine, "_cur")):
        return {"available": False, "reason": "native child diagnostics unavailable"}
    try:
        probe = engine.copy()
        probe._cur = probe._child(probe._cur, action)
        return {"source": "diagnostic copy: native _child then public completion_check",
                "checks": _completion(probe)}
    except Exception as error:
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def audit_candidate_stages(engine, action, *, full=True):
    """Instrument the original native evaluator on a diagnostic state copy.

    The normal full candidate batch is retained: restricting the batch to the
    target could conceal a vectorisation discrepancy. No method is patched on
    the search's engine or on the engine class.
    """
    if not isinstance(action, Place) or not callable(getattr(engine, "_evaluate", None)):
        return {"available": False}
    try:
        import numpy as np
        from dataclasses import fields, is_dataclass, replace
        probe = engine.copy()
        old = probe._cur
        if not is_dataclass(old):
            return {"available": False, "reason": "native state is not a dataclass"}
        changes = {f.name: dict(getattr(old, f.name)) for f in fields(old)
                   if isinstance(getattr(old, f.name), dict)}
        probe._cur = replace(old, **changes)
        tables = probe.T
        bi, si = tables.box_index[action.box], tables.slot_index[action.slot]
        report = {"available": True, "source": "copied native full-batch _evaluate instrumentation",
                  "target": {"box_index": int(bi), "slot_index": int(si)}, "stages": []}

        def record(name, present, passed=None, **values):
            report["stages"].append({"stage": name, "target_present": bool(present),
                                     "passed": None if passed is None else bool(passed), **values})

        original = probe._open_slots
        def opened(*args, **kwargs):
            result = original(*args, **kwargs)
            record("open_slot", bool(np.any(result == si)), bool(np.any(result == si)))
            return result
        probe._open_slots = opened
        lift_method = probe._liftable
        def liftable(*args, **kwargs):
            result = lift_method(*args, **kwargs)
            record("yard_liftable", True, result[bi])
            return result
        probe._liftable = liftable
        slot_method = probe._slot_features
        def slot_features(state, opened_slots):
            result = slot_method(state, opened_slots)
            indices = np.flatnonzero(opened_slots == si)
            if len(indices):
                at = int(indices[0])
                room, hroom, caprank, nonheavy, bad = (array[at] for array in result)
                values = dict(static_fit=bool(tables.static[bi, si]), share_kg=float(tables.share[bi]),
                    room_kg=float(room), height_m=float(tables.height[bi]), height_room_m=float(hroom),
                    rank=int(tables.rank[bi]), cap_rank=int(caprank), nonheavy_below=bool(nonheavy),
                    bad=bool(bad), mass_kg=float(tables.mass[bi]))
                tests = dict(static_fit=values["static_fit"], bad=not values["bad"],
                    stack_mass=values["share_kg"] <= values["room_kg"]+1e-6,
                    height=values["height_m"] <= values["height_room_m"]+1e-9,
                    rank=values["rank"] <= values["cap_rank"],
                    heavy_order=not (tables.e1_threshold is not None and
                        values["mass_kg"] >= tables.e1_threshold and values["nonheavy_below"]))
                record("local_fast", True, all(tests.values()), tests=tests, values=values)
            return result
        probe._slot_features = slot_features

        def wrap_pairs(name, bpos=1, spos=2):
            method = getattr(probe, name, None)
            if not callable(method):
                return
            def wrapped(*args, **kwargs):
                bs, ss = np.asarray(args[bpos]), np.asarray(args[spos])
                selected = (bs == bi) & (ss == si)
                result = method(*args, **kwargs)
                if selected.any():
                    data = {}
                    if name == "_c1":
                        data["failure_counts"] = {str(k): int(v) for k, v in
                                                  getattr(probe, "_c1_fails", {}).items() if v}
                    record(name, True, bool(np.asarray(result)[selected].all()), **data)
                else:
                    record(name, False)
                return result
            setattr(probe, name, wrapped)
        for name in ("_c1", "_c2", "_e9_batch", "_special_many", "_heat_many"):
            wrap_pairs(name)
        dg_method = probe._dg_ok
        def dg(*args, **kwargs):
            result = dg_method(*args, **kwargs)
            if int(args[1]) == si and int(args[0]) == int(tables.dg_class[bi]):
                record("DG", True, result)
            return result
        probe._dg_ok = dg
        exact_method = probe._exact_ok
        def exact(*args, **kwargs):
            result = exact_method(*args, **kwargs)
            if int(args[2]) == bi and int(args[3]) == si:
                record("exact_local", True, result)
            return result
        probe._exact_ok = exact
        if getattr(probe, "_global_error", None) is not None:
            record("required_model_data", True, False, error=str(probe._global_error))
        if full:
            probe._evaluate(probe._cur)
            flat = bi*len(tables.slot_ids)+si
            report["recomputed_mask_member"] = bool(np.any(probe._cur.mask == flat))
        else:
            opened_slots = probe._open_slots(probe._cur)
            probe._liftable(probe._cur)
            if np.any(opened_slots == si):
                probe._slot_features(probe._cur, np.asarray([si]))
            report["scope"] = "local filters only; full-batch audit cap reached"
        failed = [stage["stage"] for stage in report["stages"]
                  if stage["target_present"] and stage["passed"] is False]
        report["failed_stages"] = list(dict.fromkeys(failed))
        return report
    except Exception as error:
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def _static_trial(instance, ex):
    from stowage.checker.rules import check_static_rules
    from stowage.model import Placement
    ship = instance.onboard.model_copy(update={"placements": [
        Placement(container_id=cid, slot=slot) for cid, (location, slot) in ex.where.items()
        if location == "ship"]})
    try:
        report = check_static_rules(instance, ship, call_index=instance.call_index)
        return sorted({v.rule_id for v in report.violations if v.severity == "hard"})
    except Exception as error:
        return [f"diagnostic unavailable: {type(error).__name__}: {error}"]


def _final_assignment_c2(engine, targets, deadline):
    """Compare the fixed final stow with the very same C2 necessary rows.

    This is departure C2, not a full-checker or D57 certificate. Tank fills are
    free within the screen's own bounds. No conservative proxy is imported.
    """
    screen = getattr(engine, "_global", None)
    tables = getattr(engine, "T", None)
    if screen is None or tables is None or monotonic() >= deadline:
        return {"available": False}
    try:
        import numpy as np
        from ortools.linear_solver import pywraplp
        mass = np.zeros(len(tables.cell_ids))
        boxes = {box.id: box for box in engine.instance.containers}
        for cid, slot in targets.items():
            cells = cells_of_slot(slot)
            for cell in cells:
                mass[tables.cell_index[cell]] += boxes[cid].gross_mass_kg/len(cells)
        fixed = screen.A @ mass + screen.const
        solver = pywraplp.Solver.CreateSolver("GLOP")
        solver.SetNumThreads(1)
        solver.SetTimeLimit(max(1, int(min(1., max(0., deadline-monotonic())*.1)*1000)))
        tanks = [solver.NumVar(float(lo), float(hi), f"tank_{j}")
                 for j, (lo, hi) in enumerate(zip(screen.tank_lo, screen.tank_hi))]
        slack = screen._slack() if callable(getattr(screen, "_slack", None)) else np.zeros(len(fixed))
        for index, (base, lower, upper) in enumerate(zip(fixed, screen.lo-slack, screen.hi+slack)):
            scale = max(1., abs(float(base)), *(abs(float(v)) for v in (lower, upper) if np.isfinite(v)))
            row = solver.RowConstraint((float(lower)-base)/scale if np.isfinite(lower) else -solver.infinity(),
                                       (float(upper)-base)/scale if np.isfinite(upper) else solver.infinity(),
                                       str(index))
            for variable, coefficient in zip(tanks, screen.At[index]):
                if coefficient:
                    row.SetCoefficient(variable, float(coefficient)/scale)
        status = solver.Solve()
        return {"available": True, "source": "same engine C2 necessary departure rows; free bounded ballast",
                "row_count": len(fixed), "lp_status": int(status), "same_engine_numeric_slack": True,
                "feasible": True if status in (0, 1) else False if status == 2 else None,
                "certificate": False}
    except Exception as error:
        return {"available": False, "error": f"{type(error).__name__}: {error}"}


def sequence(instance, assignment, deadline, *, replay_mask=True, diagnostics=None,
             max_nodes=2048, max_backtracks=256, max_trace=256, feasibility_config=None):
    """Return ``(actions, plan)``; bounded failure retains its concrete witness.

    ``replay_mask=False`` is legacy diagnostic-only; activation always replays
    the mask. Real construction uses the production factory, with an optional
    shared feasibility config for controlled C2-off qualification.
    """
    replay_mask = bool(replay_mask or rule_freeze_v1_enabled())
    started = monotonic()
    stats = diagnostics if diagnostics is not None else {}
    stats.update(nodes=0, backtracks=0, mask_rejections=0, trace=[], rejection_trace=[],
                 rejection_family_counts={}, rejection_depth_counts={}, replay_mask=bool(replay_mask))
    space = get_action_space("boxslot")
    targets = {p.container_id: p.slot for p in instance.onboard.placements
               if p.container_id not in instance.discharge_list}
    targets.update(assignment)
    initial = Expander(instance)
    stack = []
    engine = None
    best_frame = _Frame(None, initial)
    last_ready, last_deferred = [], []

    def trace(event):
        if event["event"].endswith("rejected"):
            stats["last_rejection"] = event
        if event["event"] == "mask_rejected":
            if len(stats["rejection_trace"]) < 128:
                stats["rejection_trace"].append(event)
            else:
                stats["rejection_trace_truncated"] = True
            depth = str(event["depth"])
            stats["rejection_depth_counts"][depth] = stats["rejection_depth_counts"].get(depth, 0)+1
            families = [key for key, value in event.get("child_completion", {}).get("checks", {}).items()
                        if key.startswith(("c1", "c2")) and value is False]
            families.extend(event.get("candidate_stages", {}).get("failed_stages", []))
            families.extend("local/"+key
                for stage in event.get("candidate_stages", {}).get("stages", [])
                for key, value in stage.get("tests", {}).items() if value is False)
            if event.get("visibility", {}).get("passed") is False:
                families.append("D55_visibility")
            families.extend("static/"+rule for rule in event.get("static_trial_hard_rules", []))
            for family in set(families or ["unattributed_local_or_mask"]):
                stats["rejection_family_counts"][family] = stats["rejection_family_counts"].get(family, 0)+1
        if len(stats["trace"]) < max_trace:
            stats["trace"].append(event)
        else:
            stats["trace_truncated"] = True

    def fail(reason):
        stats["reason"] = reason
        stats["sequence_time_s"] = monotonic()-started
        ready, deferred = _ready(instance, best_frame.ex, targets)
        details = {"prefix_actions": [space.encode(a) for a in best_frame.actions],
                   "pending": _pending(best_frame.ex, targets),
                   "ready": [space.encode(a) for a in ready], "deferred": deferred,
                   "completion": _completion(best_frame.engine) if best_frame.engine else {}, **stats}
        raise SequenceFailure(f"fixed assignment sequencing failed: {reason} "
                              f"after {len(best_frame.actions)} actions", details)

    if max_nodes < 1 or max_backtracks < 0 or max_trace < 0:
        raise ValueError("invalid fixed-assignment search limits")
    if monotonic() >= deadline:
        fail("deadline")
    if set(instance.load_list)-set(targets):
        fail("missing_load_assignment")
    if set(assignment) & set(instance.discharge_list):
        fail("discharge_in_final_assignment")
    kwargs = {"config": feasibility_config} if feasibility_config is not None else {}
    engine = make_feasibility_engine(instance, space, **kwargs) if replay_mask else None
    # Audit once before search so a later search deadline cannot erase the
    # known-complete assignment comparison. Its LP has a one-second limit.
    stats["final_assignment_c2"] = _final_assignment_c2(engine, targets, deadline) if engine else {"available": False}
    root = _Frame(engine, initial)
    best_frame = root
    stack.append(root)
    while stack:
        if monotonic() >= deadline:
            fail("deadline")
        frame = stack[-1]
        pending = _pending(frame.ex, targets)
        discharges = [cid for cid in instance.discharge_list
                      if frame.ex.where.get(cid, (None,))[0] == "ship"]
        if not pending and not discharges and not frame.ex.buffer:
            geometry = securing_state_violations(instance, frame.ex)
            if not geometry and (frame.engine is None or frame.engine.is_complete()):
                stats["sequence_time_s"] = monotonic()-started
                stats["completion"] = _completion(frame.engine) if frame.engine else {}
                return frame.actions, space.expand(instance, frame.actions)
            trace({"depth": len(frame.actions), "event": "complete_rejected", "securing": list(geometry)})
            frame.choices = iter(())
        if frame.choices is None:
            last_ready, last_deferred = _ready(instance, frame.ex, targets)
            frame.choices = iter(last_ready)
        try:
            action = next(frame.choices)
        except StopIteration:
            stack.pop()
            if stack:
                stats["backtracks"] += 1
                if stats["backtracks"] > max_backtracks:
                    fail("backtrack_limit")
            continue
        if stats["nodes"] >= max_nodes:
            fail("node_limit")
        stats["nodes"] += 1
        event = {"depth": len(frame.actions), "action": space.encode(action)}
        trial = frame.ex.copy()
        try:
            trial.step(action)
        except (ValueError, KeyError) as error:
            trace({**event, "event": "physical_rejected", "error": str(error)})
            continue
        securing = securing_geometry_violation(instance, frame.ex, action)
        if securing:
            trace({**event, "event": "securing_rejected", "securing": securing})
            continue
        legal = frame.engine is None or frame.engine.is_legal(action)
        if monotonic() >= deadline:
            fail("deadline")
        if not legal:
            stats["mask_rejections"] += 1
            stats["last_rejection_prefix_actions"] = [space.encode(a) for a in frame.actions]
            stages = audit_candidate_stages(frame.engine, action, full=stats["mask_rejections"] <= 16)
            trace({**event, "event": "mask_rejected", "candidate_stages": stages,
                   "current_completion": _completion(frame.engine),
                   "child_completion": _rejected_child(frame.engine, action),
                   "visibility": _visibility(frame.engine, trial),
                   "static_trial_hard_rules": _static_trial(instance, trial),
                   "static_trial_note": "departure-only rules may be repaired by later cargo; diagnostic only"})
            continue
        child_engine = frame.engine.copy() if frame.engine is not None else None
        if child_engine is not None:
            child_engine.push(action)
        child = _Frame(child_engine, trial, (*frame.actions, action))
        # Preserve distinct histories even when they reach the same physical
        # arrangement: the native C2 reference/basis can differ. Node/backtrack
        # limits bound this search without discarding those order alternatives.
        trace({**event, "event": "accepted", "completion": _completion(child_engine) if child_engine else {},
               "visibility": _visibility(child_engine, trial) if child_engine else {"available": False},
               "securing": "geometry_passed; full lashing remains checker-owned"})
        if len(child.actions) > len(best_frame.actions):
            best_frame = child
        stack.append(child)
    fail("ready_orders_exhausted")
