"""Fresh frozen-v2 tiny exact evidence after linear epoch requalification.

No historic input, physics mock, relaxed feasibility configuration, or authored
voyage declaration enters this experiment. Generation requests are selected
before outcomes, and each phase is checkpointed under its actual source epoch.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic, time

from fix_epoch_evidence import annotate, checker_replay, source_pin

PROJECT = Path(__file__).resolve().parents[2]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def pin():
    sources = source_pin()
    extra = [*PROJECT.joinpath("src/stowage/linear").glob("*.json"),
             *PROJECT.joinpath("src/eval").glob("splits*.py"),
             PROJECT / "experiments/splits/v2/manifest.json",
             PROJECT / "experiments/linear-model/fix-t3-linear/fingerprint-report.json"]
    for path in extra:
        sources[str(path.relative_to(PROJECT))] = sha256(path.read_bytes()).hexdigest()
    return sources


def select(inventory):
    entries = inventory.manifest["entries"]
    rows = []
    for generator, vessel in [("random_yard", "feeder_500"),
                              ("simulated", "feeder_500"),
                              ("random_yard", "feeder_1000")]:
        eligible = [e for e in entries if e["split"] == "train"
                    and e["spec"]["rung"] == "tiny"
                    and e["spec"]["generator"] == generator
                    and e["spec"]["vessel"] == vessel]
        eligible.sort(key=lambda e: (e["spec"]["port"] != "laem_chabang_like",
                                     e["spec"]["seed"]))
        entry = eligible[0]
        inventory.require_membership(entry["spec"], split="train")
        rows.append(entry)
    return rows


def request(spec):
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION
    return dict(campaign_version=CAMPAIGN_VERSION,
        generator="simulated_yard" if spec["generator"] == "simulated" else spec["generator"],
        profile="tiny", size=spec["rung"], seed=spec["seed"], vessel=spec["vessel"],
        port=spec["port"], noise=spec["noise"] if spec["generator"] == "simulated"
        else spec["blocking_ratio"], n_boxes=spec["n_boxes"], max_attempts=2,
        dense_20ft=spec["dense_20ft"], tiny_dg_enabled=spec["tiny_dg_enabled"],
        cargo_profile=spec["cargo_profile"], full_export=False, c2_screened=False,
        identity_version=spec["identity_version"], equipment_profile=spec["equipment_profile"],
        without_dg=spec["without_dg"])


def generate(selected):
    from stowage.generate.instances_v3 import generate as producer, CampaignRejection
    try:
        instance, audit = producer(selected)
        return dict(status="generated", instance=instance, audit=audit)
    except CampaignRejection as exc:
        return dict(status="rejected", error=str(exc), audit=exc.audit)
    except Exception as exc:
        return dict(status="error", error=f"{type(exc).__name__}: {exc}")


def run(args):
    from eval.splits import load_inventory, canonical_spec, spec_from_instance, spec_key
    from stowage.frozen import require_clean
    from stowage.io import save_instance, save_plan
    from stowage.bounds import lower_bound
    from stowage.solver.exact import solve_exact
    from stowage.solver.exact_deadline import run_with_deadline
    from stowage.solver.exact_oracle import checker_brute_force_certificate
    from stowage.versions import component_versions
    inventory = load_inventory()
    selected = select(inventory)
    if inventory.version != "v2":
        raise ValueError("current evidence requires frozen v2")
    selection = dict(entries=selected, split_version=inventory.version,
                     split_entries_sha256=inventory.entries_sha256)
    selection_path = args.output / "selection.json"
    if selection_path.exists():
        if json.loads(selection_path.read_text()) != selection:
            raise ValueError("refusing release/selection drift")
    else:
        write(selection_path, selection)
    for index in range(args.start, min(3, args.start + args.count)):
        entry = selected[index]
        directory = args.output / f"case-{index}"
        if (directory / "result.json").exists():
            raise ValueError("case already ran; use a fresh output directory")
        before = pin(); started = monotonic()
        row = dict(index=index, selected=entry, request=request(entry["spec"]),
            split_version=inventory.version, split_entries_sha256=inventory.entries_sha256,
            versions=component_versions(), started_unix_s=time(),
            engine_optimal=False, physical_optimal=False, checker_passed=None,
            objective=None, physical_objective=None, bound36=None,
            independent_agreement=False, phase="generation", limits_s=dict(
                generation=args.generation_s, solver=args.solver_s,
                oracle=args.oracle_s, witness_checker=10.))
        write(directory / "sources-before.json", before)
        write(directory / "result.json", row)
        phase = monotonic()
        generation = run_with_deadline(generate, (row["request"],), {},
                                        deadline=phase + args.generation_s)
        generated = generation.value or dict(status="timeout")
        instance = generated.pop("instance", None)
        row["generation"] = dict(**generated, wall_time_s=monotonic()-phase,
                                 timed_out=generation.timed_out)
        write(directory / "result.json", row)
        try:
            if instance is not None:
                require_clean(instance, what="fresh frozen exact input")
                actual = canonical_spec(spec_from_instance(instance))
                inventory.require_membership(actual, split="train")
                if actual != entry["spec"]:
                    raise ValueError("fresh generation identity differs from frozen request")
                if instance.voyage is None or instance.vessel.voyage_policy is None:
                    raise ValueError("producer did not supply genuine D50 voyage declaration/policy")
                path = directory / "instance.json"
                save_instance(instance, path)
                row.update(input_sha256=sha256(path.read_bytes()).hexdigest(),
                    input_annotations=annotate(instance), spec_key=spec_key(actual),
                    bound36=lower_bound(instance).weighted, phase="default_exact")
                write(directory / "result.json", row)
                exact = solve_exact(instance, time_limit_s=args.solver_s, max_nodes=100_000)
                row["exact"] = dict(status=exact.status, objective=exact.objective,
                    lower_bound=exact.lower_bound, gap=exact.gap, expanded_nodes=exact.expanded_nodes,
                    wall_time_s=exact.wall_time_s, metrics=exact.metrics)
                row.update(engine_optimal=exact.status == "OPTIMAL", objective=exact.objective,
                           phase="independent_oracle")
                if exact.plan:
                    save_plan(exact.plan, directory / "engine-plan.json")
                write(directory / "result.json", row)
                oracle = checker_brute_force_certificate(instance, incumbent=exact.plan,
                    time_limit_s=args.oracle_s, max_nodes=100_000)
                row["oracle"] = {k: getattr(oracle, k) for k in (
                    "status", "objective", "lower_bound", "relaxed_status", "relaxed_objective",
                    "brute_force_agreement", "checker_passed", "certificate_source",
                    "expanded_nodes", "wall_time_s", "message")}
                row.update(physical_optimal=oracle.status == "OPTIMAL",
                    physical_objective=oracle.objective,
                    independent_agreement=oracle.brute_force_agreement,
                    checker_passed=oracle.checker_passed if oracle.report is not None else None,
                    phase="completed")
                if oracle.report:
                    write(directory / "oracle-checker-report.json", oracle.report.model_dump(mode="json"))
                if oracle.plan:
                    save_plan(oracle.plan, directory / "physical-reference-plan.json")
                if exact.plan:
                    phase = monotonic()
                    checker = run_with_deadline(checker_replay, (instance, exact.plan), {},
                                                deadline=phase+10.)
                    row["engine_witness_recheck"] = dict(result=checker.value,
                        timed_out=checker.timed_out, wall_time_s=monotonic()-phase)
                    row["engine_optimal_plan_checker_passed"] = (
                        checker.value["passed"] if checker.value is not None else None)
        except Exception as exc:
            row.update(error=f"{type(exc).__name__}: {exc}", phase="error")
        after = pin()
        row.update(wall_time_s=monotonic()-started,
            source_unchanged=before == after,
            changed_sources=[p for p in sorted(set(before)|set(after))
                             if before.get(p) != after.get(p)])
        write(directory / "sources-after.json", after)
        write(directory / "result.json", row)
        print(json.dumps(row, sort_keys=True), flush=True)
    rows = [json.loads(p.read_text()) for p in sorted(args.output.glob("case-*/result.json"))]
    stable = [r for r in rows if r.get("source_unchanged")]
    generated = [r for r in stable if r.get("input_sha256")]
    checked_optimal = [r for r in generated if r["engine_optimal"]
                       and r.get("engine_optimal_plan_checker_passed") is not None]
    summary = dict(selection=selection, requested=len(rows), stable_source_cases=len(stable),
        generated_fresh_instances_total=sum(bool(r.get("input_sha256")) for r in rows),
        source_quarantined_cases=len(rows)-len(stable),
        stable_generation_rejections=sum(r.get("generation", {}).get("status") == "rejected"
                                         for r in stable),
        admitted_fresh_instances=len(generated), engine_optima=sum(r["engine_optimal"] for r in generated),
        physical_optima=sum(r["physical_optimal"] for r in generated),
        engine_proof_rate_per_request=sum(r["engine_optimal"] for r in generated)/len(rows),
        physical_proof_rate_per_request=sum(r["physical_optimal"] for r in generated)/len(rows),
        engine_optimal_plan_checker_pass_rate=(
            sum(r["engine_optimal_plan_checker_passed"] for r in checked_optimal)/len(checked_optimal)
            if checked_optimal else None),
        physical_optimal_plan_checker_pass_rate=(1. if any(r["physical_optimal"] for r in generated) else None),
        physical_optimum_gaps_to_36=[r["physical_objective"]-r["bound36"]
                                    for r in generated if r["physical_optimal"]],
        wall_time_s=sum(r["wall_time_s"] for r in rows))
    write(args.output / "summary.json", summary)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0, choices=(0, 1, 2))
    parser.add_argument("--count", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--generation-s", type=float, default=30.)
    parser.add_argument("--solver-s", type=float, default=30.)
    parser.add_argument("--oracle-s", type=float, default=30.)
    run(parser.parse_args())
