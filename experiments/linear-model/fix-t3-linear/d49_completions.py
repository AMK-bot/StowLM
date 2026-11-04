"""Bounded concrete-completion search for the unchanged D49 C2 sample.

Local-rule masks propose actual box placements; both explicitly directed
ballast LPs and arrival ballast are tried, then the unchanged full checker
decides. Necessary LP solutions are search candidates, never certificates.
Failure to find a completion is not a proof of exact infeasibility.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import time


HERE = Path(__file__).resolve().parent


def helper():
    spec = importlib.util.spec_from_file_location("d49_sample", HERE / "c2_quick_sample.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serialize(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(type(value).__name__)


def write(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2,
                                   allow_nan=False, default=serialize) + "\n")
    temporary.replace(path)


def packing(instance, attempt, deadline):
    """Deterministic bounded heuristic over actual local-rule-legal actions."""
    from stowage.actions import get_action_space
    from stowage.conventions import cells_of_slot
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space("boxslot")
    engine = make_feasibility_engine(instance, space,
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
    boxes = {box.id: box for box in instance.containers}
    cells = {cell.id: cell for cell in instance.vessel.cells}
    stacks = {cid: stack for stack in instance.vessel.stacks for cid in stack.cell_ids}
    actions = []
    rng = random.Random(f"d49-packing:{instance.seed}:{attempt}")
    used_x = used_y = used_mass = 0.
    target_x = sum(cell.lcg_m for cell in cells.values()) / len(cells)
    total_mass = sum(boxes[cid].gross_mass_kg for cid in instance.load_list)
    for _ in range(len(instance.load_list)+len(instance.discharge_list)+1):
        if engine.is_complete():
            return space.expand(instance, actions), {"actions": [space.encode(a) for a in actions]}
        if time.monotonic() > deadline:
            return None, {"status": "packing budget exhausted", "placed": len(actions)}
        legal = [a for a in engine.legal_actions() if hasattr(a, "slot")]
        if not legal:
            return None, {"status": "local-mask dead end", "placed": len(actions),
                          "diagnostics": dict(engine.diagnostics)}
        by_box = {}
        for action in legal:
            by_box.setdefault(action.box, []).append(action)
        chosen_box = min(by_box, key=lambda cid: (len(by_box[cid]), -boxes[cid].length_ft,
                                                 -boxes[cid].gross_mass_kg, cid))
        box = boxes[chosen_box]
        def score(action):
            ids = cells_of_slot(action.slot)
            x = sum(cells[cid].lcg_m for cid in ids)/len(ids)
            y = sum(cells[cid].tcg_m for cid in ids)/len(ids)
            stack = stacks[ids[0]]
            return (stack.base_z_m + stack.cell_ids.index(ids[0])*box.height_m,
                    abs(used_y+box.gross_mass_kg*y),
                    abs(used_x+box.gross_mass_kg*x-(used_mass+box.gross_mass_kg)*target_x),
                    action.slot)
        candidates = sorted(by_box[chosen_box], key=score)
        action = candidates[0 if attempt == 0 else rng.randrange(min(3, len(candidates)))]
        ids = cells_of_slot(action.slot)
        used_mass += box.gross_mass_kg
        used_x += box.gross_mass_kg*sum(cells[cid].lcg_m for cid in ids)/len(ids)
        used_y += box.gross_mass_kg*sum(cells[cid].tcg_m for cid in ids)/len(ids)
        engine.push(action)
        actions.append(action)
    return None, {"status": "action cap reached", "placed": len(actions),
                  "mass_placed_kg": used_mass, "cargo_mass_kg": total_mass}


def audit_request(instance, output, label, *, attempts, seconds):
    from stowage.checker import check
    from stowage.io import save_plan
    from stowage.linear import band_for_instance, build_linear_model, solve_ballast_lp
    from stowage.model import BallastCondition
    from stowage.moves import apply_plan

    started = time.monotonic()
    deadline = started+seconds
    record = {"label": label, "max_packing_attempts": attempts,
              "soft_budget_s": seconds, "full_checker_calls": 0,
              "exact_completion_found": False, "proven_exact_infeasible": False,
              "packing_attempts": [], "checked_candidates": []}
    try:
        model = build_linear_model(instance, band=band_for_instance(instance))
        seen_plans = set()
        for attempt in range(attempts):
            if time.monotonic() > deadline:
                break
            plan, packing_info = packing(instance, attempt, deadline)
            record["packing_attempts"].append(packing_info)
            if plan is None:
                continue
            digest = hashlib.sha256(json.dumps(plan.actions, sort_keys=True).encode()).hexdigest()
            if digest in seen_plans:
                packing_info["duplicate_stow"] = True
                continue
            seen_plans.add(digest)
            replay = apply_plan(instance, plan)
            packing_info["move_replay_legal"] = replay.legal
            if not replay.legal:
                packing_info["move_violations"] = [v.model_dump(mode="json") for v in replay.violations]
                continue
            tank_candidates = []
            packing_info["lp"] = {}
            # Audit the candidate from the necessary LP first: its acceptance
            # is the D49 measurement, whereas the inward LP is a fallback.
            for direction in ("necessary", "conservative"):
                lp = solve_ballast_lp(model, replay.final_state.ship, instance,
                    direction=direction, margin_scale=1., objective="min_total_ballast")
                packing_info["lp"][direction] = asdict(lp)
                if lp.status == "optimal":
                    tank_candidates.append((direction+" LP", lp.tank_masses_kg))
            tank_candidates.append(("arrival ballast", {
                tank.id: instance.onboard.tank_masses_kg.get(tank.id, 0.)
                for tank in instance.vessel.tanks if tank.adjustable}))
            seen_tanks = set()
            for candidate_index, (source, masses) in enumerate(tank_candidates):
                if time.monotonic() > deadline:
                    break
                key = tuple(sorted((key, round(value, 6)) for key, value in masses.items()))
                if key in seen_tanks:
                    continue
                seen_tanks.add(key)
                candidate = plan.model_copy(update={"ballast": BallastCondition(
                    source="planner", tank_masses_kg=masses)})
                checked = check(instance, candidate)
                record["full_checker_calls"] += 1
                candidate_label = f"{label}-packing{attempt}-ballast{candidate_index}"
                save_plan(candidate, output / f"{candidate_label}-diagnostic-plan.json")
                write(output / f"{candidate_label}-check.json", checked)
                result = {"label": candidate_label, "ballast_source": source,
                    "passed": checked.passed, "shut_out": checked.shut_out,
                    "hard_rules": sorted({v.rule_id for v in checked.violations if v.severity == "hard"}),
                    "parts_ran": {name: part.ran for name, part in checked.parts.items()},
                    "modeled_parts_passed": all(checked.parts[name].ran and
                        not any(v.severity == "hard" for v in checked.parts[name].violations)
                        for name in ("stability", "strength")),
                    "stability_metrics": checked.parts["stability"].metrics,
                    "strength_metrics": checked.parts["strength"].metrics}
                result["checker_decided"] = all(part.ran for part in checked.parts.values()) and not any(
                    rule in {"checker.internal_error", "checker.missing_vessel_data"}
                    for rule in result["hard_rules"])
                record["checked_candidates"].append(result)
                if checked.passed:
                    record["exact_completion_found"] = True
                    record["passing_diagnostic_plan"] = f"{candidate_label}-diagnostic-plan.json"
                    break
            if record["exact_completion_found"]:
                break
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    record.update(elapsed_s=time.monotonic()-started,
        status="exact completion found" if record["exact_completion_found"] else "not found",
        warning="Bounded search failure is not an exact-infeasibility proof; necessary C2 allows dead ends")
    return record


def summary(records):
    checked = [candidate for record in records for candidate in record["checked_candidates"]]
    conservative = [c for c in checked if c["ballast_source"] == "conservative LP"]
    necessary = [c for c in checked if c["ballast_source"] == "necessary LP"]
    decided = [c for c in necessary if c.get("checker_decided", False)]
    rejected = sum(not c["passed"] for c in decided)
    return {
        "c2_pass_requests_audited": len(records),
        "requests_with_concrete_checked_candidate": sum(bool(r["checked_candidates"]) for r in records),
        "full_checker_candidates": len(checked),
        "full_checker_candidates_passed": sum(c["passed"] for c in checked),
        "exact_completion_found_requests": sum(r["exact_completion_found"] for r in records),
        "no_exact_completion_found_requests": sum(not r["exact_completion_found"] for r in records),
        "proven_exact_infeasible_requests": sum(r["proven_exact_infeasible"] for r in records),
        "necessary_c2_false_acceptance_proven": 0,
        "necessary_c2_false_acceptance_denominator_with_exact_feasibility_decided": sum(
            r["exact_completion_found"] or r["proven_exact_infeasible"] for r in records),
        "necessary_c2_false_acceptance_unknown": sum(not r["exact_completion_found"] and
            not r["proven_exact_infeasible"] for r in records),
        "necessary_lp_full_checker_candidates": len(necessary),
        "necessary_lp_full_checker_candidates_passed": sum(c["passed"] for c in necessary),
        "necessary_lp_full_checker_candidates_decided": len(decided),
        "necessary_lp_full_checker_candidates_rejected": rejected,
        "necessary_lp_full_checker_candidates_unresolved": len(necessary)-len(decided),
        "necessary_lp_candidate_full_checker_rejection_rate": rejected/len(decided) if decided else None,
        "conservative_lp_full_checker_candidates": len(conservative),
        "conservative_lp_full_checker_candidates_failed": sum(not c["passed"] for c in conservative),
        "conservative_lp_stability_or_strength_failed": sum(not c["modeled_parts_passed"] for c in conservative),
        "warning": "Candidate checker failures are not proofs that a C2-passing request has no exact completion; incomplete checker calls are unresolved, not exact rejection",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HERE / "d49-completions")
    parser.add_argument("--bank-sha256", required=True)
    parser.add_argument("--packing-attempts", type=int, default=2)
    parser.add_argument("--seconds-per-request", type=float, default=15.)
    parser.add_argument("--pilot", action="store_true", help="one C2-passing request only, no new draws")
    args = parser.parse_args()
    if not 1 <= args.packing_attempts <= 4 or not 0 < args.seconds_per_request <= 60:
        parser.error("packing attempts must be1..4 and per-request budget0..60s")
    h = helper()
    output = args.output.resolve()
    sample = args.sample.resolve()
    if output == HERE or not output.is_relative_to(HERE) or not sample.is_relative_to(HERE):
        parser.error("sample and dedicated output must stay under fix-t3-linear")
    output.mkdir(parents=True, exist_ok=True)
    before = h.snapshot()
    if before["bank_sha256"] != args.bank_sha256:
        parser.error("bank SHA256 does not match promoted bank")
    sample_summary = sample / "summary.json"
    if json.loads(sample_summary.read_text()).get("inputs") != before:
        parser.error("sample belongs to different qualified inputs; run a fresh sample")
    admitted = {"inputs": before, "sample_summary_sha256": h.sha(sample_summary),
        "packing_attempts": args.packing_attempts, "seconds_per_request": args.seconds_per_request}
    admission_path = output / "admission.json"
    if admission_path.exists() and json.loads(admission_path.read_text()) != admitted:
        parser.error("output has different admitted inputs")
    write(admission_path, admitted)
    requests = [json.loads(path.read_text()) for path in sorted(sample.glob("request-*.json"))]
    eligible = [r for r in requests if r["accepted"] and r["c2_screened"]]
    if args.pilot:
        eligible = eligible[:1]
    from stowage.io import load_instance
    records = []
    for request in eligible:
        label = request["label"]
        path = output / f"completion-{label}.json"
        if h.snapshot() != before or h.sha(sample_summary) != admitted["sample_summary_sha256"]:
            raise ValueError("qualified inputs/sample changed; retain finished shards, stop")
        if path.exists():
            record = json.loads(path.read_text())
        else:
            row = next(r for r in request["completion_calls"]
                       if not r["singleton_dg_probe"] and r["c2_status"] == "passed")
            instance_path = sample / f"{row['label']}-diagnostic-instance.json"
            instance = load_instance(instance_path)
            record = audit_request(instance, output, label,
                attempts=args.packing_attempts, seconds=args.seconds_per_request)
            record.update(size=request["size"], seed=request["seed"],
                          input_instance_sha256=h.sha(instance_path))
            if h.snapshot() != before:
                raise ValueError("qualified inputs changed while auditing; do not publish shard")
            write(path, record)
        records.append(record)
        write(output / "summary.json", {"admission": admitted, "pilot": args.pilot,
            "total_sample_requests": len(requests), "total_c2_pass_requests": sum(r["accepted"] and r["c2_screened"] for r in requests),
            "results": summary(records)})
        print(json.dumps({"request": label, "status": record["status"],
                          "seconds": record["elapsed_s"], "checks": record["full_checker_calls"]}), flush=True)


if __name__ == "__main__":
    main()
