"""PREPARED ONLY: fixed-prefix heat-ID diagnostic; execute only after parent GO.

Uses the immutable heat runtime, exact saved 17/18 prefix and deterministic
34-cell historical sample. No solver or ballast finisher is ever called.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import traceback

sys.dont_write_bytecode = True
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
            "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"
HERE = Path(__file__).resolve().parent
FENCE = HERE.parent
LIVE = FENCE.parents[2]
RUNTIME = FENCE / "heat_runtime"
HEAT = "expert.e8.away_from_heat"
MISSING = "checker.missing_vessel_data"
EXPECTED_INPUT = "d2453d9d2055f306fd6cbcea461fdb3543dd754e36af54eb5b9f218bccaa7694"
EXPECTED_PREFIX = "bb4f4209e827f91cfa7595f6a4b06514920c98ae739989a5c6aef1e29e056581"


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timed(call, seconds):
    """A caught checker TimeoutError cannot become a physical FAIL verdict."""
    marker = {"fired": False}
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise RuntimeError("Nested deadline is not supported")
    def expired(*unused):
        marker["fired"] = True
        raise TimeoutError(f"supplement measurement exceeded {seconds}s")
    started = time.perf_counter()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    value, error = None, None
    try:
        value = call()
    except Exception as exc:
        error = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    record = {"wall_s": time.perf_counter() - started, "deadline_fired": marker["fired"],
              "measurement_status": "unavailable_deadline" if marker["fired"] else
                  "unavailable_error" if error else "completed"}
    if error:
        record.update(error)
    return value, record


def missing_heat(v):
    return v["rule_id"] == MISSING and v.get("message", "").startswith(HEAT + ":")


def multiset(rows):
    return Counter(json.dumps(row, sort_keys=True) for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Requires parent GO after the80-reference run")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, default=HERE / "showcase_supplement")
    parser.add_argument("--limit", type=int, help="Pilot only: examine firstN of the fixed34; incomplete remains explicit")
    parser.add_argument("--full-diagnostic", action="store_true",
                        help="Also full-check one fixed mechanically legal18th-load diagnostic, never a reference")
    parser.add_argument("--local-call-s", type=float, default=8)
    parser.add_argument("--prefix-s", type=float, default=60)
    parser.add_argument("--checker-s", type=float, default=120)
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return 0
    if args.limit is not None and not 1 <= args.limit <= 34:
        parser.error("--limit must be between1 and34")
    output = args.output.resolve()
    if not output.is_relative_to(HERE):
        raise ValueError("Output must remain under fixwindow/heat")
    output.mkdir(parents=True, exist_ok=True)
    helper_path = FENCE / "heat_measure_snapshot.py"
    spec = importlib.util.spec_from_file_location("heat_snapshot_helpers", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    assert helper.ROOT == RUNTIME
    denied = helper.install_write_guard()
    # Strengthen the shared guard: even runtime-local numerical cache writes
    # must not mutate the independent immutable runtime or resolved symlinks.
    def immutable_runtime(event, values):
        paths = []
        if event == "open":
            path, mode, flags = values
            if ((isinstance(mode, str) and any(c in mode for c in "wax+")) or
                    flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                paths = [path]
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "os.truncate"}:
            paths = [values[0]]
        elif event in {"os.rename", "os.link", "os.symlink"}:
            paths = list(values[:2])
        for path in paths:
            if isinstance(path, int):
                continue
            resolved = Path(os.fsdecode(path)).resolve()
            if resolved.is_relative_to(RUNTIME):
                denied.append({"event": event, "path": str(resolved), "reason": "immutable heat runtime"})
                raise PermissionError(f"Immutable runtime write: {resolved}")
    sys.addaudithook(immutable_runtime)

    from stowage.actions import get_action_space
    from stowage.actions.expander import Expander
    from stowage.actions.space import Place
    from stowage.checker import check, rules_special
    from stowage.checker.rules import placement_violations
    from stowage.conventions import cells_of_slot
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance, load_plan
    from stowage.model.plan import MovePlan
    from stowage.moves import apply_plan
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    helper.assert_snapshot_imports()
    if rule_freeze_v1_enabled():
        raise ValueError("Supplement requires rule freeze OFF")

    input_path = LIVE / "experiments/mvp/corpus/showcase/feeder_800-small.json"
    prefix_path = LIVE / "experiments/mvp/runs/showcase-v2-800-attempt3/raw-plan.json"
    explanation_path = LIVE / "experiments/mvp/runs/showcase-v2-800-heat-explanation/explanation.json"
    candidate = HERE / "src/stowage/checker/rules_special.py"
    approved = read(HERE / "manifest.json")
    snapshot_config = read(FENCE / "heat_snapshot_results/config.json")
    evidence = read(explanation_path)
    historical = evidence["states"]["saved_partial"]
    assert sha(input_path) == evidence["instance_sha256"] == EXPECTED_INPUT
    assert sha(prefix_path) == evidence["plan_sha256"] == EXPECTED_PREFIX
    assert sha(candidate) == approved["scratch_sha256"] == snapshot_config["candidate_sha256"]
    assert sha(RUNTIME / approved["live_source"]) == approved["live_before_sha256"] == snapshot_config["baseline_special_sha256"]
    pins = helper.source_pins()
    assert pins == snapshot_config["source_pins"]
    candidate_ast = ast.parse(candidate.read_bytes())
    original_ast = ast.parse((RUNTIME / approved["live_source"]).read_bytes())
    strip = lambda tree: [ast.dump(node, include_attributes=False) for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "special_violations"]
    assert strip(candidate_ast) == strip(original_ast)
    namespace = dict(rules_special.__dict__)
    node = next(node for node in candidate_ast.body if isinstance(node, ast.FunctionDef)
                and node.name == "special_violations")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(candidate), "exec"), namespace)
    original, patched = rules_special.special_violations, namespace["special_violations"]

    def assert_bindings():
        assert sha(input_path) == EXPECTED_INPUT and sha(prefix_path) == EXPECTED_PREFIX
        assert sha(candidate) == approved["scratch_sha256"]
        assert helper.source_pins() == pins
        helper.assert_snapshot_imports()

    @contextmanager
    def use(function):
        old = rules_special.special_violations
        rules_special.special_violations = function
        try:
            yield
        finally:
            rules_special.special_violations = old

    config = {"driver_sha256": sha(__file__), "helper_sha256": sha(helper_path),
              "runtime_root": str(RUNTIME), "source_pins": pins,
              "snapshot_config_sha256": sha(FENCE / "heat_snapshot_results/config.json"),
              "input_sha256": EXPECTED_INPUT, "prefix_sha256": EXPECTED_PREFIX,
              "explanation_sha256": sha(explanation_path), "candidate_sha256": sha(candidate),
              "rule_freeze_v1": False, "feasibility": {"extra": {"global_screen": 0}},
              "native_threads": 1, "gpu": False, "local_call_s": args.local_call_s,
              "prefix_s": args.prefix_s, "checker_s": args.checker_s,
              "method": "fixed historical34 local placement comparisons; optional single fixed18th-load full diagnostic; no solver/finisher"}
    config_sha = helper.object_sha(config)
    config_path = output / "config.json"
    if config_path.exists():
        if not args.resume or read(config_path) != config:
            raise ValueError("Existing configuration differs or --resume missing; use fresh output")
    helper.save(config_path, config)
    instance, prefix = load_instance(input_path), load_plan(prefix_path)

    def replay_prefix():
        raw = apply_plan(instance, prefix)
        space = get_action_space()
        feasibility = make_feasibility_engine(instance, space,
            config=FeasibilityConfig(extra={"global_screen": 0}))
        expanded = Expander(instance)
        actions = space.from_plan(instance, prefix)
        for action in actions:
            feasibility.push(action)  # Public push verifies legality; C2 is OFF.
            expanded.step(action)
        return raw, expanded, len(actions)
    replayed, replay_measurement = timed(replay_prefix, args.prefix_s)
    helper.save(output / "prefix-measurement.json", replay_measurement)
    if replay_measurement["measurement_status"] != "completed":
        return 2
    raw, expanded, action_count = replayed
    ship = expanded.world_state().ship
    placed = {placement.container_id for placement in ship.placements}
    remaining = sorted(set(instance.load_list) - placed)
    assert len(remaining) == 1 and remaining[0] == "QZXU0000040"
    target = next(box for box in instance.containers if box.id == remaining[0])
    assert len(ship.placements) == historical["loaded_boxes"] == 17
    occupied = {cell for placement in ship.placements for cell in cells_of_slot(placement.slot)}
    cells = sorted((cell for cell in instance.vessel.cells if cell.accepts_20),
                   key=lambda cell: (cell.id in occupied, cell.id))
    slots = [cell.id for cell in cells[:historical["checked_slots"]]]
    assert historical["checked_slots"] == len(slots) == 34 and len(cells) == historical["total_slots"] == 92
    assert not occupied.intersection(slots) and slots[0] == "0010184" and "0010882" in slots
    raw_ship = raw.final_state.ship
    same_placements = sorted((p.container_id,p.slot) for p in ship.placements) == sorted(
        (p.container_id,p.slot) for p in raw_ship.placements)
    assert same_placements, "Canonical action replay changed the exact prefix's container placements"
    helper.save(output / "prefix.json", {"role": "exact incomplete17/18 prefix diagnostic",
        "remaining_container": target.id, "remaining_special_stowage": target.special_stowage,
        "prefix_actions_legal_with_c2_off": True, "prefix_action_count": action_count,
        "raw_move_replay": raw.model_dump(mode="json"),
        "canonical_placement_map_matches_raw": same_placements,
        "canonical_expanded_ship": ship.model_dump(mode="json"),
        "canonical_covers_match_raw": ship.covers_off == raw_ship.covers_off,
        "sample_slots": slots, "sample_selection": "exact saved algorithm: accepts20, free-first thenID, first34",
        "note": "Local checks use the saved diagnostic's canonical expanded state; raw replay is retained separately for mechanical append."})
    selected = slots[:args.limit] if args.limit else slots
    for slot in selected:
        assert_bindings()  # Also validate immutable inputs before a cached result.
        path = output / "cells" / (slot + ".json")
        if path.exists():
            assert args.resume and read(path)["config_sha256"] == config_sha
            continue
        row = {"slot": slot, "container_id": target.id, "config_sha256": config_sha, "phases": {}}
        for label, function in (("baseline", original), ("patched", patched)):
            with use(function):
                violations, measurement = timed(lambda: placement_violations(instance, ship, target, slot), args.local_call_s)
            if measurement["measurement_status"] == "completed":
                measurement["violations"] = [v.model_dump(mode="json") for v in violations]
            elif violations is not None:
                measurement["unavailable_raw_violations"] = [v.model_dump(mode="json") for v in violations]
            row["phases"][label] = measurement
            assert_bindings()
        helper.save(path, row)
        print(json.dumps({"slot": slot, "phase_status": {key: value["measurement_status"] for key,value in row["phases"].items()}}), flush=True)

    rows = [read(output / "cells" / (slot + ".json")) for slot in slots
            if (output / "cells" / (slot + ".json")).exists()]
    usable = [row for row in rows if all(phase["measurement_status"] == "completed" for phase in row["phases"].values())]
    counts = {phase: Counter() for phase in ("baseline", "patched")}
    missing_slots = {phase: [] for phase in counts}
    heat_slots = {phase: [] for phase in counts}
    newly_allowed, preservation = [], []
    for row in usable:
        before, after = [row["phases"][key]["violations"] for key in ("baseline", "patched")]
        for phase, violations in (("baseline", before), ("patched", after)):
            counts[phase].update({v["rule_id"] for v in violations if v["severity"] == "hard"})
            if any(missing_heat(v) for v in violations): missing_slots[phase].append(row["slot"])
            if any(v["rule_id"] == HEAT for v in violations): heat_slots[phase].append(row["slot"])
        unchanged = multiset([v for v in before if not missing_heat(v)]) == multiset([v for v in after if not missing_heat(v)])
        preservation.append({"slot": row["slot"], "all_other_local_violations_exactly_unchanged": unchanged})
        if any(v["severity"] == "hard" for v in before) and not any(v["severity"] == "hard" for v in after):
            newly_allowed.append(row["slot"])
    summary = {"role": "constructed local diagnostic, not an official reference or completed solver plan",
        "config_sha256": config_sha, "sample_size": 34, "recorded_cells": len(rows), "available_cells": len(usable),
        "local_complete": len(usable) == 34, "rule_counts_per_cell": {k:dict(v) for k,v in counts.items()},
        "historical_baseline_counts_match": dict(counts["baseline"]) == historical["free_slot_rule_counts"],
        "targeted_missing_heat_slots": missing_slots, "genuine_heat_clearance_slots": heat_slots,
        "five_genuine_heat_cells_preserved": len(heat_slots["baseline"]) == 5 and heat_slots["baseline"] == heat_slots["patched"],
        "all_other_local_violations_exactly_unchanged": bool(usable) and all(row["all_other_local_violations_exactly_unchanged"] for row in preservation),
        "newly_locally_allowed_slots": newly_allowed, "known_real_heat_cell": "0010882",
        "preservation_per_cell": preservation, "blocked_writes": list(denied),
        "caveat": "Local static allowance is not complete-mask allowance, mechanical replay, stability, or full checker approval."}
    summary["heat_blocker_demonstrated"] = (summary["local_complete"] and summary["historical_baseline_counts_match"]
        and len(missing_slots["baseline"]) == 34 and not missing_slots["patched"]
        and summary["five_genuine_heat_cells_preserved"] and summary["all_other_local_violations_exactly_unchanged"]
        and bool(newly_allowed))
    helper.save(output / "local-summary.json", summary)

    if "0010184" in newly_allowed and raw.executed == len(prefix.moves):
        slot = "0010184"
        # One fixed placement, deterministic mechanics only; never search/repair.
        ex = Expander(instance, state=raw.final_state)
        extra = list(ex.step(Place(target.id, slot)))
        extra.extend(ex.finish())
        provenance = prefix.provenance.model_copy(update={"created_by": "diagnostic.heat-id.fixed18",
            "created_at": None, "git_commit": None,
            "parents": sorted(set(prefix.provenance.parents + [prefix.id, instance.id]))})
        derived = prefix.model_copy(update={"id": prefix.id + ".heat-id-diagnostic.fixed18",
            "moves": list(prefix.moves) + extra, "actions": None, "action_space": None, "provenance": provenance})
        derived = MovePlan.model_validate(derived.model_dump(mode="json"))
        assert derived.ballast == prefix.ballast, "Diagnostic must not change inherited ballast"
        derived_path = output / "constructed-diagnostic-plan.json"
        helper.save(derived_path, derived.model_dump(mode="json"))
        mechanical = apply_plan(instance, derived)
        mechanical_row = {"role": "constructed diagnostic, never an official reference", "fixed_slot": slot,
            "plan_sha256": sha(derived_path), "additional_moves": [m.model_dump(mode="json") for m in extra],
            "inherited_ballast_unchanged": derived.ballast == prefix.ballast,
            "solver_called": False, "finisher_called": False,
            "all_loads_aboard": set(instance.load_list) <= {p.container_id for p in mechanical.final_state.ship.placements},
            "mechanical_replay": mechanical.model_dump(mode="json")}
        helper.save(output / "constructed-diagnostic-binding.json", mechanical_row)
        summary["constructed_diagnostic"] = {"fixed_slot": slot,
            "mechanical_legal": mechanical.legal, "all_loads_aboard": mechanical_row["all_loads_aboard"],
            "plan_sha256": sha(derived_path), "full_check_requested": args.full_diagnostic,
            "full_measurement_status": "not_requested" if not args.full_diagnostic else "blocked_mechanical_replay"}
        if args.full_diagnostic and mechanical.legal:
            phases = {}
            for phase, function in (("baseline", original), ("patched", patched)):
                assert_bindings()
                with use(function): report, measurement = timed(lambda: check(instance, derived), args.checker_s)
                if report is not None:
                    key = "report" if measurement["measurement_status"] == "completed" else "unavailable_raw_report"
                    measurement[key] = report.model_dump(mode="json")
                measurement["config_sha256"] = config_sha
                measurement["constructed_plan_sha256"] = sha(derived_path)
                phases[phase] = measurement
                assert_bindings()
                helper.save(output / ("constructed-" + phase + ".json"), measurement)
            helper.save(output / "constructed-comparison.json", {
                "role": "constructed diagnostic, no official reference/solver/finisher certification",
                "comparison": helper.comparison(phases["baseline"], phases["patched"]),
                "plan_sha256": sha(derived_path), "input_sha256": EXPECTED_INPUT})
            summary["constructed_diagnostic"]["full_measurement_status"] = {
                phase: value["measurement_status"] for phase, value in phases.items()}
            summary["constructed_diagnostic"]["full_checker_passed"] = {
                phase: value.get("report", {}).get("passed") for phase, value in phases.items()}
    elif args.full_diagnostic:
        summary["constructed_diagnostic"] = {"full_check_requested": True,
            "full_measurement_status": "blocked_fixed0010184_not_newly_allowed_or_incomplete_raw_prefix"}
    assert_bindings()
    helper.save(output / "local-summary.json", summary)
    helper.save(output / "source-closure.json", {"snapshot_pins_unchanged": True,
        "candidate_sha256": sha(candidate), "imported_modules": helper.assert_snapshot_imports(),
        "blocked_writes": list(denied), "completed_utc": datetime.now(timezone.utc).isoformat()})
    print(json.dumps(summary, indent=2))
    return 0 if summary["heat_blocker_demonstrated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
