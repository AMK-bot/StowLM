"""Prepared, unmeasured two-loading-call development case. Launch only if approved.

Imports are standard-library-only until an explicit CLI mode is invoked. The
32-map cohort and its hashes are read-only. No earlier planner result is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter, time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
CASE = "optional-two-load-263360000-v1"
SOURCE_SEED = 263360000
CALLS = (("THLCH", "laem_chabang_like"), ("SGSIN", "singapore_like"),
         ("MYPKG", "port_klang_like"), ("VNSGN", "cat_lai_like"),
         ("HKHKG", "hong_kong_like"))
sys.dont_write_bytecode = True


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bootstrap(output):
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
        os.environ[name] = "1"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["STOWAGE_VESSEL_CACHE_DIR"] = str(output / "vessel-cache")
    for path in (ROOT / "src", HERE):
        sys.path.insert(0, str(path))
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        raise RuntimeError("The source cohort and this authored transition require unchanged legacy activation")


def source_data():
    manifest = json.loads((HERE / "cohort-manifest.json").read_text())
    entry = next(row for row in manifest["maps"] if row["seed"] == SOURCE_SEED)
    if not entry["development"]:
        raise RuntimeError("Optional case source must remain in the frozen development subset")
    path = ROOT / entry["path"]
    if sha(path) != entry["sha256"]:
        raise RuntimeError("Raw source instance changed")
    from stowage.model import Instance
    return Instance.model_validate_json(path.read_text()), entry


def authored_cargo(source):
    from stowage.model import Container
    first = set(sorted(source.load_list)[::2])
    second = set(source.load_list) - first
    first_stacks = [stack for stack in source.yard.stacks if first & set(stack.boxes)]
    second_stacks = [stack for stack in source.yard.stacks if second & set(stack.boxes)]
    if any(first & set(stack.boxes) and second & set(stack.boxes) for stack in source.yard.stacks):
        raise RuntimeError("Source yard has mixed first/second loading groups in one stack")
    first_ids = first | {cid for stack in first_stacks for cid in stack.boxes}
    second_ids = second | {cid for stack in second_stacks for cid in stack.boxes}
    cargo = {}
    for box in source.containers:
        value = box.model_dump()
        value["pod"] = "HKHKG" if box.pod == "SGSIN" else box.pod
        if box.id in first_ids:
            value["pol"] = "THLCH"
        elif box.id in second_ids:
            value["pol"] = "SGSIN"
        cargo[box.id] = Container.model_validate(value)
    assert first and second and not first_ids & second_ids
    return first, second, first_ids, second_ids, first_stacks, second_stacks, cargo


def rotation():
    from stowage.model import Rotation
    from stowage.model.port import PortCall
    return Rotation(service=CASE, calls=[PortCall(locode=locode, archetype=archetype, index=index)
                    for index, (locode, archetype) in enumerate(CALLS)])


def first_instance(source, groups):
    from stowage.generate.random_voyage import build_voyage_spec
    from stowage.model import Instance
    first, _, first_ids, _, first_stacks, _, cargo = groups
    route = rotation()
    value = source.model_dump()
    value.update(id=CASE + ".call0", stage="A", rotation=route.model_dump(), call_index=0,
        load_list=sorted(first), discharge_list=[], plan_calls=[], forecasts=[],
        containers=[cargo[cid].model_dump() for cid in sorted(first_ids)],
        yard={**source.yard.model_dump(), "stacks": [stack.model_dump() for stack in first_stacks],
              "quay_buffer": []},
        voyage=build_voyage_spec(route, 0, source.port, seed=SOURCE_SEED).model_dump())
    value["family"].update(cohort=CASE, split="TRAIN", optional="true", load_calls="2",
        source_seed=str(SOURCE_SEED), source_usage="raw_cargo_and_geometry_only",
        construction="fresh_cold_first_call")
    value["provenance"]["parents"].append("optional-multiload.py: authored two-port cargo split, no prior plan")
    return Instance.model_validate(value)


def second_yard(port, original_stacks, cargo):
    """Author independent Singapore inventory on its own compatible native yard."""
    from stowage.model import YardState
    from stowage.model.yard import YardStack
    available = [(block, bay, row) for block in port.yard.blocks
                 for bay in range(1, block.n_bays, 2) for row in range(1, block.n_rows + 1)]
    stacks = []
    groups = sorted(original_stacks, key=lambda stack: (
        not any(cargo[cid].reefer for cid in stack.boxes),
        not any(cargo[cid].dg is not None for cid in stack.boxes), stack.key))
    for original in groups:
        boxes = [cargo[cid] for cid in original.boxes]
        if any(box.direct_delivery for box in boxes):
            raise RuntimeError("This optional legacy-yard fixture does not author direct-delivery stacks")
        fit = next((i for i, (block, _, row) in enumerate(available)
                    if original.length_ft in block.allowed_lengths_ft
                    and (not any(b.reefer for b in boxes) or row in block.reefer_rows)
                    and (not any(b.dg is not None for b in boxes) or block.dg_block)
                    and len(boxes) <= block.max_tiers
                    and len(boxes) <= port.yard_max_tiers), None)
        if fit is None:
            raise RuntimeError("No compatible Singapore source-yard footprint; fixture preparation fails")
        block, bay, row = available.pop(fit)
        stacks.append(YardStack(key=f"{block.id}-{bay:03d}-{row:02d}",
                                length_ft=original.length_ft, boxes=list(original.boxes)))
    return YardState(yard_id=port.yard.id, stacks=stacks)


def advance(first, first_plan, source, groups):
    from stowage.checker import departure_condition
    from stowage.generate.random_voyage import build_voyage_spec
    from stowage.model import Instance, derive_arrival_liquid_state
    from stowage.ports.profiles import load_port
    first_ids, second, _, second_ids, _, second_stacks, cargo = groups
    departure = departure_condition(first, first_plan)
    if {p.container_id for p in departure.placements} != first_ids:
        raise RuntimeError("First checked departure does not contain exactly the first loading subset")
    liquid = derive_arrival_liquid_state(departure, first.voyage, vessel=first.vessel)
    arrival = departure.model_copy(deep=True, update={"tank_masses_kg": dict(liquid.tank_masses_kg)})
    for tank in first.vessel.tanks:
        if tank.adjustable and arrival.tank_masses_kg.get(tank.id, 0.) != departure.tank_masses_kg.get(tank.id, 0.):
            raise RuntimeError("Voyage unexpectedly changed adjustable ballast")
    port = load_port("singapore_like")
    if port.profile_kind != "operational" or port.locode != "SGSIN":
        raise RuntimeError("Second loading call requires the real synthetic operational Singapore profile")
    yard = second_yard(port, second_stacks, cargo)
    route = rotation()
    value = source.model_dump()
    value.update(id=CASE + ".call1", stage="B", call_index=1, rotation=route.model_dump(),
        port=port.model_dump(), yard=yard.model_dump(), onboard=arrival.model_dump(),
        containers=[cargo[cid].model_dump() for cid in sorted(first_ids | second_ids)],
        load_list=sorted(second), discharge_list=[], plan_calls=[], forecasts=[],
        voyage=build_voyage_spec(route, 1, port, seed=SOURCE_SEED).model_dump())
    value["family"].update(cohort=CASE, split="TRAIN", optional="true", load_calls="2",
        construction="operational_fixed_onboard_carryover", external_recipe="none")
    value["provenance"]["parents"].extend([first.id, first_plan.id,
        "onboard positions and tank masses derived from the newly checked first call"])
    second_instance = Instance.model_validate(value)
    fixed = {p.container_id: p.slot for p in departure.placements}
    assert {p.container_id: p.slot for p in second_instance.onboard.placements} == fixed
    transition = dict(first_departure=departure.model_dump(mode="json"),
        second_arrival=arrival.model_dump(mode="json"), arrival_liquid=liquid.model_dump(mode="json"),
        fixed_coordinates=fixed, voyage_ballast_operations=[], bunkering_or_background_reset=False,
        sea_ballast_unchanged=True)
    return second_instance, fixed, transition


def fixed_guard(instance, plan, fixed):
    from stowage.moves import apply_plan
    replay = apply_plan(instance, plan)
    positions = {p.container_id: p.slot for p in replay.final_state.ship.placements}
    changed = {cid: dict(before=slot, after=positions.get(cid))
               for cid, slot in fixed.items() if positions.get(cid) != slot}
    moved = [i for i, move in enumerate(plan.moves) if move.container_id in fixed]
    return dict(passed=bool(replay.legal and not changed and not moved),
                replay_legal=bool(replay.legal), changed_coordinates=changed,
                resident_move_indices=moved)


class Capture:
    """Keep failed native reports and select only full passes in the fixed family."""
    def __init__(self, instance, output, fixed):
        from frozen_reporting.tiers import load_table
        self.instance, self.output, self.fixed = instance, output, fixed
        self.table = load_table()
        self.seen, self.rows = set(), []
        self.best = self.best_complete = self.best_partial = None

    def observe(self, instance, plan, report):
        from frozen_reporting.tiers import analyze_report, rank_analysis
        from stowage.checker import check, report_matches_inputs
        if instance.id != self.instance.id:
            return
        if not report_matches_inputs(report, self.instance, plan):
            report = check(self.instance, plan)
        signature = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
        if signature in self.seen:
            return
        self.seen.add(signature)
        guard = fixed_guard(self.instance, plan, self.fixed)
        analysis = analyze_report(report, self.table, instance=self.instance)
        record = dict(index=len(self.rows), native_full_pass=bool(report.passed), fixed_guard=guard,
            analysis=analysis, plan=plan.model_dump(mode="json"), report=report.model_dump(mode="json"))
        self.rows.append(record)
        write_json(self.output / "checks" / f"{record['index']:04d}.json", record)
        if not guard["passed"] or not analysis["eligible"]:
            return
        if analysis["ashore_count"] == 0:
            rank = (analysis["level"], rank_analysis(analysis))
            if self.best_complete is None or rank < self.best_complete[0]:
                self.best_complete = (rank, record)
        elif analysis["level"] <= 3:
            rank = rank_analysis(analysis)
            if self.best_partial is None or rank < self.best_partial[0]:
                self.best_partial = (rank, record)
        if report.passed and (self.best is None or analysis["lifts"] < self.best["analysis"]["lifts"]):
            self.best = record

    def completion_row(self):
        row = dict(instance_id=self.instance.id, passed=self.best is not None,
                   completed=None, best_safe_partial=None, checked_candidates=len(self.rows),
                   status="passed" if self.best is not None else "no full fixed-onboard plan found")
        if self.best_complete is not None:
            record = self.best_complete[1]
            row["completed"] = dict(level=record["analysis"]["level"],
                diagnostic_only=not record["native_full_pass"], check_index=record["index"],
                broken_rules=record["analysis"]["broken_rules"])
        if self.best_partial is not None:
            record = self.best_partial[1]
            total = len(set(self.instance.load_list) | set(self.fixed))
            row["best_safe_partial"] = dict(check_index=record["index"],
                boxes_aboard_percent=100 * (total - record["analysis"]["ashore_count"]) / total,
                analysis=record["analysis"])
        return row


def solve_call(instance, output, fixed, args):
    from frozen_checks import set_observer
    from frozen_engine12.config import Config
    from frozen_engine12.planner import plan_and_check
    from stowage.checker import check
    from stowage.model import MovePlan
    capture = Capture(instance, output, fixed)
    started = perf_counter()
    set_observer(capture.observe)
    record = {}
    try:
        result, record = plan_and_check(instance, budget_s=args.budget_s,
            finalize_s=args.finalize_s, seed=0, config=Config(warm_polish=False))
        if result is not None and result.plan is not None:
            capture.observe(instance, result.plan, check(instance, result.plan))
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        set_observer(None)
        write_json(output / "engine-record.json", record)
        write_json(output / "completion.json", capture.completion_row())
    if capture.best is None:
        return None, None, capture.completion_row(), perf_counter() - started
    plan = MovePlan.model_validate(capture.best["plan"])
    report = check(instance, plan)  # Independent full checker, never a tier override.
    guard = fixed_guard(instance, plan, fixed)
    if not report.passed or not guard["passed"]:
        raise RuntimeError("Selected first/second plan failed independent full-check/fixed-coordinate audit")
    write_json(output / "selected-plan.json", plan.model_dump(mode="json"))
    write_json(output / "selected-report.json", report.model_dump(mode="json"))
    return plan, report, capture.completion_row(), perf_counter() - started


def current_cost(instance, report):
    count, weights = report.count, instance.objective
    return (weights.relocate * count.relocations + weights.restow * count.restow_lifts
            + weights.hatch_cover * count.hatch_cover_lifts)


def execute(args, output):
    from frozen_reporting.completion import markdown, table
    from stowage.checker import departure_condition
    source, entry = source_data()
    groups = authored_cargo(source)
    first = first_instance(source, groups)
    design = dict(case=CASE, source=entry["path"], source_sha256=entry["sha256"],
        source_usage="raw cargo and vessel/yard geometry only; never a prior checked plan",
        first_loads=sorted(groups[0]), second_loads=sorted(groups[1]), calls=list(CALLS),
        source_singapore_POD_replaced_by="HKHKG", first_call="fresh cold Engine12",
        second_call="fixed operational onboard, no stored assignment/recipe warm start",
        proposed_policy=dict(budget_s=args.budget_s, finalize_s=args.finalize_s,
                             native_small_finishing_floor_s=300, warm_polish=False),
        execute_approved=bool(args.execute_approved), fixed_onboard=True)
    write_json(output / "design.json", design)
    write_json(output / "call0-instance.json", first.model_dump(mode="json"))
    if not args.execute_approved:
        return
    rows = {first.id: dict(instance_id=first.id, passed=False, completed=None)}
    second_id = CASE + ".call1"
    rows[second_id] = dict(instance_id=second_id, passed=False, completed=None,
                          status="not attempted: requires a newly checked first-call full pass")
    outcome = dict(case=CASE, passed_both_calls=False, calls=rows, status="running first call")
    def save():
        outcome["completion_by_tier"] = table(rows)
        write_json(output / "outcome.json", outcome)
        (output / "completion.md").write_text(markdown({"optional two-call case": table(rows)}) + "\n")
    save()
    first_plan, first_report, rows[first.id], first_s = solve_call(first, output / "call0", {}, args)
    outcome["call0_wall_s"] = first_s
    if first_plan is None:
        outcome["status"] = "no first-call full pass found; second call remains blocked, not discarded"
        save()
        return
    second, fixed, transition = advance(first, first_plan, source, groups)
    write_json(output / "call1-instance.json", second.model_dump(mode="json"))
    write_json(output / "transition.json", transition)
    outcome["status"] = "running second call with fixed resident coordinates"
    save()
    second_plan, second_report, rows[second.id], second_s = solve_call(second, output / "call1", fixed, args)
    outcome["call1_wall_s"] = second_s
    if second_plan is None:
        outcome["status"] = "no second-call full fixed-onboard plan found; failed checks retained"
        save()
        return
    final_ship = departure_condition(second, second_plan)
    transition["second_departure"] = final_ship.model_dump(mode="json")
    transition["second_port_adjustable_ballast_change_kg"] = {
        tank.id: final_ship.tank_masses_kg.get(tank.id, 0.) - second.onboard.tank_masses_kg.get(tank.id, 0.)
        for tank in second.vessel.tanks if tank.adjustable}
    write_json(output / "transition.json", transition)
    first_now, second_now = current_cost(first, first_report), current_cost(second, second_report)
    future = (second.objective.future_restow * sum(f.restow_lifts for f in second_report.count.future)
              + second.objective.future_hatch_cover * sum(f.hatch_cover_lifts for f in second_report.count.future))
    outcome.update(passed_both_calls=True, status="both calls independently fully checked; residents unmoved",
        costs=dict(actual_current_call0=first_now, actual_current_call1=second_now,
            final_future_projection=future, combined_projected_rotation=first_now + second_now + future,
            first_report_weighted_diagnostic_only=first_report.count.weighted_nonproductive,
            second_report_weighted=second_report.count.weighted_nonproductive,
            caution="Only final future projection is added; no sum of the two complete D15 objectives"))
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--execute-approved", action="store_true",
                      help="Run only after the executive grants a numerical slot and approval")
    parser.add_argument("--out", default="results/optional-multiload-v1")
    parser.add_argument("--budget-s", type=float, default=120.)
    parser.add_argument("--finalize-s", type=float, default=120.)
    args = parser.parse_args()
    output = (HERE / args.out).resolve()
    output.relative_to(HERE)
    output.mkdir(parents=True, exist_ok=False)  # No reuse of an earlier plan/recipe.
    write_json(output / "pids/driver.json", dict(pid=os.getpid(), started=time(), argv=sys.argv))
    bootstrap(output)
    try:
        execute(args, output)
    except Exception as error:
        write_json(output / "failed.json", dict(error=f"{type(error).__name__}: {error}",
                                                time=time(), call_results_retained=True))
        raise


if __name__ == "__mp_main__":
    folder = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "results/optional-multiload-v1"
    output = (HERE / folder).resolve()
    output.relative_to(HERE)
    write_json(output / "pids" / f"native-{os.getpid()}.json",
               dict(pid=os.getpid(), parent_pid=os.getppid(), started=time(), kind="own native worker"))
elif __name__ == "__main__":
    main()
