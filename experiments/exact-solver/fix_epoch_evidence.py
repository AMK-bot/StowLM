"""Bounded, source-pinned reruns; never substitute mocked physics for evidence.

Historical inputs are replayed as their original bytes. Missing newer evidence
is recorded and left to the actual checker/engine; no contract is backfilled.
No successful engine optimum is automatically a full-domain physical optimum.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "fix-current-epoch"


def source_pin():
    paths = [*ROOT.joinpath("src/stowage").rglob("*.py"),
             *ROOT.joinpath("params").rglob("*.toml"),
             *ROOT.joinpath("src/stowage/rules").rglob("*.toml"),
             *ROOT.joinpath("src/stowage/rules").rglob("*.json")]
    return {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def summarize(rows):
    result = {}
    for rung in sorted({r["rung"] for r in rows}):
        group = [r for r in rows if r["rung"] == rung]
        current = [r for r in group if r["source_unchanged"]]
        optimal = [r for r in current if r["status"] == "OPTIMAL"]
        physical = [r for r in current if r["checker_passed"] is True
                    and r["independent_proof"] is True]
        checked = [r for r in optimal if r["checker_passed"] is not None]
        result[rung] = dict(
            attempts=len(group), engine_optima=len(optimal),
            independent_physical_optima=len(physical),
            proof_rate=len(physical)/len(group),
            engine_proof_rate=len(optimal)/len(group),
            optimal_plan_checker_pass_rate=(
                sum(r["checker_passed"] is True for r in checked)/len(checked)
                if checked else None),
            physical_optimum_gaps_to_36=[
                r.get("physical_objective", r["objective"])-r["bound36"] for r in physical],
            quarantined_source_changes=len(group)-len(current),
            total_s=sum(r["wall_time_s"] for r in group),
            max_s=max(r["wall_time_s"] for r in group),
        )
    return result


def annotate(instance):
    return dict(
        schema=instance.schema_version,
        voyage_declared=instance.voyage is not None,
        vessel_voyage_policy_declared=instance.vessel.voyage_policy is not None,
        absent_csc_ratings_d38_fallback=sum(c.max_gross_mass_kg is None
                                            for c in instance.containers),
        background_cargo_aggregates_without_d42_boxes=sum(
            f.kind == "cargo" and f.mass_kg > 0 and f.cargo_boxes is None
            for f in instance.vessel.fixed_loads),
        n_load=len(instance.load_list),
    )


def checker_replay(instance, plan):
    from stowage.checker import check
    report = check(instance, plan)
    return dict(passed=report.passed, violation_ids=[v.rule_id for v in report.violations
        if v.severity == "hard"], part_errors={k: v.error for k, v in report.parts.items()
                                             if v.error})


def generation_pilot():
    from stowage.generate.instances_v3 import generate, scenario_specs
    spec = scenario_specs(profiles=["tiny"], generators=["random_yard"],
                          max_attempts=1, c2_screened=False)[0]
    try:
        instance, audit = generate(spec)
    except Exception as exc:
        return dict(status="error", error=f"{type(exc).__name__}: {exc}",
                    audit=getattr(exc, "audit", None), spec=spec)
    from stowage.io import save_instance
    save_instance(instance, OUT / "generation-pilot.json")
    return dict(status="generated", annotations=annotate(instance), audit=audit, spec=spec)


def current_pilot(args):
    from stowage.io import load_instance, save_plan
    from stowage.solver.exact import solve_exact
    from stowage.solver.exact_oracle import checker_brute_force_certificate
    from stowage.bounds import lower_bound
    from stowage.versions import component_versions
    before = source_pin(); started = monotonic()
    instance = load_instance(OUT / "generation-pilot.json")
    exact = solve_exact(instance, time_limit_s=args.limit, max_nodes=100_000)
    oracle = checker_brute_force_certificate(instance, incumbent=exact.plan,
        time_limit_s=args.oracle_limit, max_nodes=100_000)
    row = dict(rung="tiny", status=exact.status, objective=exact.objective,
        bound36=lower_bound(instance).weighted, solver_s=exact.wall_time_s,
        solver_metrics=exact.metrics,
        checker_passed=oracle.checker_passed if oracle.report is not None else None,
        physical_objective=oracle.objective,
        independent_proof=oracle.status == "OPTIMAL", input_annotations=annotate(instance),
        oracle={k: getattr(oracle, k) for k in (
            "status", "objective", "lower_bound", "relaxed_status", "relaxed_objective",
            "brute_force_agreement", "checker_passed", "certificate_source", "expanded_nodes",
            "wall_time_s", "message")}, versions=component_versions(),
        wall_time_s=monotonic()-started, source_unchanged=before == source_pin())
    if oracle.report:
        row["oracle_hard_violations"] = [dict(rule_id=v.rule_id, message=v.message)
            for v in oracle.report.violations if v.severity == "hard"]
    if oracle.plan:
        save_plan(oracle.plan, OUT / "current-pilot-checked-plan.json")
    (OUT / "current-pilot-sources.json").write_text(json.dumps(before, indent=2)+"\n")
    (OUT / "current-pilot-result.json").write_text(json.dumps(row, indent=2)+"\n")
    print(json.dumps(row), flush=True)


def run(args):
    from stowage.bounds import lower_bound
    from stowage.io import load_instance, load_plan
    from stowage.solver.exact import solve_exact
    from stowage.solver.exact_deadline import run_with_deadline
    from stowage.versions import component_versions
    OUT.mkdir(exist_ok=True)
    if args.mode == "current":
        current_pilot(args)
        return
    if args.mode == "generate":
        started = monotonic(); before = source_pin()
        outcome = run_with_deadline(generation_pilot, (), {}, deadline=started+args.limit)
        row = dict(status="TIME_LIMIT" if outcome.timed_out else "finished",
                   result=outcome.value, wall_time_s=monotonic()-started,
                   versions=component_versions(), source_unchanged=before == source_pin())
        (OUT / "generation-pilot-result.json").write_text(json.dumps(row, indent=2)+"\n")
        print(json.dumps(row), flush=True)
        return
    source = OUT / (args.label+"-sources.json")
    source.write_text(json.dumps(source_pin(), indent=2)+"\n")
    output = OUT / (args.label+"-results.jsonl")
    paths = sorted(Path(args.inputs).glob("*/*.json"))
    paths = [p for p in paths if not p.name.endswith(".audit.json")]
    if args.pilot:
        paths = [next(p for p in paths if p.parent.name == rung) for rung in ("tiny", "small")]
    rows = []
    for path in paths:
        before = source_pin(); started = monotonic()
        row = dict(path=str(path), input_sha256=sha256(path.read_bytes()).hexdigest(),
                   rung=path.parent.name, versions=component_versions(),
                   checker_passed=None, independent_proof=False, objective=None,
                   status="ERROR", bound36=None, limit_s=args.limit)
        try:
            instance = load_instance(path)
            row["input_annotations"] = annotate(instance)
            row["bound36"] = lower_bound(instance).weighted
            result = solve_exact(instance, time_limit_s=args.limit, max_nodes=100_000)
            row.update(status=result.status, objective=result.objective,
                       solver_s=result.wall_time_s, expanded_nodes=result.expanded_nodes,
                       lower_bound=result.lower_bound, solver_gap=result.gap, metrics=result.metrics)
            if result.plan:
                checked = run_with_deadline(checker_replay, (instance, result.plan), {},
                                            deadline=monotonic()+5.)
                row["checker"] = checked.value
                row["checker_passed"] = None if checked.value is None else checked.value["passed"]
            # Existing complete reference plans are rechecked with the actual
            # full checker even if today's engine cannot produce a candidate.
            for reference in sorted(Path(args.references).glob("*.json")):
                plan = load_plan(reference)
                if plan.instance_id == instance.id:
                    checked = run_with_deadline(checker_replay, (instance, plan), {},
                                                deadline=monotonic()+5.)
                    row["historical_reference_check"] = dict(path=str(reference),
                        timed_out=checked.timed_out, result=checked.value)
                    break
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        after = source_pin()
        row.update(source_unchanged=before == after,
                   changed_sources=[p for p in sorted(set(before)|set(after))
                                    if before.get(p) != after.get(p)],
                   source_before_sha256=sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                   wall_time_s=monotonic()-started)
        rows.append(row)
        with output.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True)+"\n")
        print(json.dumps(row, sort_keys=True), flush=True)
    (OUT / (args.label+"-summary.json")).write_text(json.dumps(summarize(rows), indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["pinned", "generate", "current"], default="pinned")
    parser.add_argument("--limit", type=float, default=5.)
    parser.add_argument("--oracle-limit", type=float, default=20.)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--label", default="pinned")
    parser.add_argument("--inputs", default="experiments/exact-solver/paired-quiet-current/instances")
    parser.add_argument("--references", default="experiments/exact-solver/paired-quiet-current/checked-reference-plans")
    run(parser.parse_args())
