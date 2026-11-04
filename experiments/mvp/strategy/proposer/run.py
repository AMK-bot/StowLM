"""Budgeted D179 train-map harness; model providers are deliberately offline.

--dry-run prints a pinned execution plan without loading or solving instances.
Without that flag, --provider dry-run evaluates canned layouts through the real
pipeline. Spark/Glimmer labels identify intended arms, never a live model call.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "HIGHS_THREADS")
for _name in THREAD_VARS:
    os.environ[_name] = "1"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_moves(row):
    check = row.get("check") or {}
    value = row.get("moves", check.get("moves"))
    if (row.get("checked_evaluations") == 1 and check.get("passed") is True and isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value)):
        return value
    return None


def checked_prefix(rows, target):
    """Return proposals through the target-th completed terminal report.

    A checker rejection counts; invalid text, solver/V4 failure and interrupted
    checker calls do not. Each proposal can complete at most one terminal check.
    """
    consumed, checked = [], 0
    for row in rows:
        count = row.get("checked_evaluations", 0)
        if type(count) is not int or count not in (0, 1):
            raise ValueError("each proposal must have zero or one checked evaluations")
        consumed.append(row)
        checked += count
        if checked == target:
            return consumed
    return None


def prefix_summary(rows, k):
    """Compare equal completed checker evaluations, with proposal waste separate."""
    result = {}
    for prefix in (1, 4, 16):
        if prefix > k:
            continue
        candidates = checked_prefix(rows, prefix)
        if candidates is None:
            continue
        passed = [row for row in candidates if checked_moves(row) is not None]
        best = min(passed, key=lambda row: (checked_moves(row), row["attempt"])) if passed else None
        result[str(prefix)] = {
            "attempts": len(candidates), "proposal_tries": len(candidates),
            "evaluations_used": prefix,
            "valid_layouts": sum(row.get("valid_layout") is True for row in candidates),
            "checked_evaluations": prefix,
            "wasted_proposals": sum(row.get("checked_evaluations", 0) == 0 for row in candidates),
            "invalid_proposals": sum(row.get("status") == "invalid_layout" for row in candidates),
            "provider_failures": sum(row.get("status") == "provider_error" for row in candidates),
            "terminal_certificate_check_calls": sum(row.get("terminal_certificate_check_calls", 0) for row in candidates),
            "all_full_checker_invocations": None,
            "v4_internal_checker_work": "unmeasured; includes child invocations and possibly interrupted checks",
            "passes": len(passed), "passed": best is not None,
            "best_moves": checked_moves(best) if best else None,
            "best_attempt": best["attempt"] if best else None,
        }
    return result


def aggregate_prefixes(summaries, k):
    result = {}
    for prefix in (1, 4, 16):
        if prefix > k:
            continue
        rows = [summary["prefixes"][str(prefix)] for summary in summaries
                if str(prefix) in summary.get("prefixes", {})]
        values = [row["best_moves"] for row in rows if row["best_moves"] is not None]
        result[str(prefix)] = dict(completed_maps=len(rows), passing_maps=len(values),
                                  mean_best_moves=sum(values) / len(values) if values else None,
                                  attempts=sum(row["attempts"] for row in rows),
                                  proposal_tries=sum(row["proposal_tries"] for row in rows),
                                  wasted_proposals=sum(row["wasted_proposals"] for row in rows),
                                  invalid_proposals=sum(row["invalid_proposals"] for row in rows),
                                  evaluations_used=sum(row["evaluations_used"] for row in rows),
                                  checked_evaluations=sum(row["checked_evaluations"] for row in rows))
    return result


class SearchProposer:
    """Random packing masks alternating with local flips around checked best.

    No default plan, headroom results, uncounted planner call, or raw-move score
    enters the search. Geometry generation uses headroom's bounded static code.
    Identical masks are skipped before evaluation; candidate generation uses no
    restricted planner/finisher/checker calls.
    """

    def __init__(self, instance, seed):
        from experiments.mvp.strategy.headroom.layout import BlockModel, random_layouts
        self.instance, self.seed = instance, seed
        self.model = BlockModel.from_instance(instance)
        self.generation_error = None
        try:
            self.random = iter(random_layouts(instance, count=256, seed=seed, model=self.model))
        except Exception as error:
            self.random = iter(())
            self.generation_error = f"{type(error).__name__}: {error}"
        self.used: set[str] = set()
        self.incumbent = None
        self.best = None

    def propose(self, attempt):
        from experiments.mvp.strategy.headroom.layout import local_neighbours
        from experiments.mvp.strategy.proposer.format import format_layout
        from experiments.mvp.strategy.proposer.providers import Proposal
        if self.generation_error:
            raise LookupError(f"static geometry generation failed: {self.generation_error}; wasted try")
        if attempt % 2 == 0 and self.incumbent is not None:
            candidates = local_neighbours(self.instance, self.incumbent, seed=self.seed + attempt,
                                          limit=32, model=self.model)
            layout = next((x for x in candidates if x.fingerprint not in self.used), None)
        else:
            layout = None
        if layout is None:
            layout = next((x for x in self.random if x.fingerprint not in self.used), None)
        if layout is None:
            raise LookupError("bounded random/local proposal space exhausted; wasted try")
        self.used.add(layout.fingerprint)
        return Proposal(format_layout(layout), {"provider": "search", "fingerprint": layout.fingerprint,
                                                "kind": "local" if layout.name.startswith("local") else "random",
                                                "layout": layout.to_dict(), "model_called": False})

    def observe(self, proposal, result):
        from experiments.mvp.strategy.headroom.layout import BlockLayout
        # Resume uses exact persisted proposal provenance; no search replay solve.
        layout_data = proposal.metadata.get("layout")
        if layout_data:
            layout = BlockLayout.from_dict(layout_data)
            self.used.add(layout.fingerprint)
            value = checked_moves(result)
            if value is not None and (self.best is None or value < self.best):
                self.incumbent, self.best = layout, value


def execute_map(instance, job, folder, config, *, evaluator=None, provider=None,
                prompt_builder=None, search_factory=None, rule_factory=None):
    """Generate independent proposals until k checked reports or a finite cap.

    Invalid text is a wasted proposal, never repaired. Provenance/source errors
    fail closed; missing rows/exhausted search stop an incomplete map explicitly.
    """
    from experiments.mvp.strategy.proposer.providers import Proposal, make_provider
    from experiments.mvp.strategy.proposer.registration import validate_proposal_provenance
    if evaluator is None:
        from experiments.mvp.strategy.proposer.evaluator import evaluate_text
        evaluator = evaluate_text
    if prompt_builder is None:
        from experiments.mvp.strategy.proposer.prompt import build_prompt
        prompt_builder = build_prompt
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    prompt = prompt_builder(instance)
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    prompt_path = folder / "prompt.txt"
    if prompt_path.exists() and prompt_path.read_text() != prompt:
        raise ValueError("persisted evaluation prompt changed")
    prompt_path.write_text(prompt)
    arm, k = config["arm"], config["k"]
    max_proposals = config.get("max_proposals", 256)
    if type(max_proposals) is not int or max_proposals < k:
        raise ValueError("max_proposals must be an integer at least k")
    strategy = None
    if arm == "search":
        strategy = (search_factory or SearchProposer)(instance, config["seed"])
    elif arm == "rule":
        from experiments.mvp.strategy.proposer.rule import RuleProposer
        strategy = (rule_factory or RuleProposer)(instance, config["seed"])
        if config.get("rule_policy") and strategy.registration != config["rule_policy"]:
            raise ValueError("shared rule policy changed since run registration")
    if arm in {"spark", "glimmer"} and provider is None:
        snapshot = config.get("provider_snapshot")
        provider = make_provider(config["provider"],
            proposals=snapshot if snapshot and config["provider"] == "jsonl" else config.get("proposals"),
            canned_text=snapshot if snapshot and config["provider"] == "dry-run" else config.get("canned_text"),
            expected_sha256=config.get("provider_source_sha256"))
    smoke = arm in {"spark", "glimmer"} and config["provider"] != "jsonl"
    summary = dict(**job, arm=arm, k=k, max_proposals=max_proposals, attempts=[], prefixes={}, complete=False,
                   budget_unit="completed_terminal_certificate_checks",
                   budget_rule="k completed terminal reports; wasted proposals separate; finite cap; no repairs",
                   result_kind="canned_smoke" if smoke else "zero_shot_collected" if arm in {"spark", "glimmer"} else arm,
                   diagnostic_subset=config.get('diagnostic_subset', False),
                   comparative_eligible=not smoke and not config.get('diagnostic_subset', False), no_model_calls=True)
    stop_reason = "proposal_limit_reached"
    for attempt in range(1, max_proposals + 1):
        attempt_id = f"proposal-{attempt:03d}"
        proposal_path = folder / "proposals" / f"{attempt_id}.json"
        result_path = folder / "attempts" / f"{attempt_id}.json"
        if proposal_path.exists():
            saved = json.loads(proposal_path.read_text())
            if saved["attempt"] != attempt or saved["prompt_sha256"] != prompt_sha256:
                raise ValueError("persisted proposal evaluation identity changed")
            proposal = Proposal(saved["text"], saved["metadata"])
            if arm == "rule" and "provider_error" not in proposal.metadata:
                if strategy.propose(attempt) != proposal:
                    raise ValueError("persisted rule proposal differs from registered policy")
            if arm in {"spark", "glimmer"} and config["provider"] == "jsonl" and "provider_error" not in proposal.metadata:
                expected = provider.propose(instance=instance, map_id=job["map_id"], arm=arm,
                    attempt=attempt, prompt=prompt, input_sha256=job.get("input_sha256"))
                if expected != proposal:
                    raise ValueError("persisted proposal text/provenance differs from immutable response snapshot")
        else:
            try:
                proposal = (strategy.propose(attempt) if strategy is not None else
                            provider.propose(instance=instance, map_id=job["map_id"], arm=arm,
                                             attempt=attempt, prompt=prompt,
                                             input_sha256=job.get("input_sha256")))
            except ValueError:
                raise  # unverifiable collection/source bytes are never evaluated
            except Exception as error:
                proposal = Proposal("", {"provider_error": f"{type(error).__name__}: {error}",
                                          "source_exhausted": isinstance(error, LookupError),
                                          "model_called": False})
            atomic_json(proposal_path, dict(text=proposal.text, metadata=proposal.metadata,
                                           attempt=attempt, prompt_sha256=prompt_sha256))
        if arm in {"spark", "glimmer"} and config["provider"] == "jsonl" and "provider_error" not in proposal.metadata:
            validate_proposal_provenance(proposal.metadata, arm=arm, attempt=attempt,
                prompt_sha256=prompt_sha256, input_sha256=job.get("input_sha256"),
                expected_source_sha256=config.get("provider_source_sha256"), map_id=job["map_id"])
        if result_path.exists():
            row = json.loads(result_path.read_text())
            if row.get("provider") != proposal.metadata:
                raise ValueError("persisted result/provider provenance mismatch")
        elif "provider_error" in proposal.metadata:
            row = dict(status="provider_error", valid_layout=False, evaluations_used=0,
                       proposal_tries=1, wasted_try=True, checked_evaluations=0,
                       terminal_certificate_check_calls=0, terminal_certificate_check_completed=0,
                       all_full_checker_invocations=None,
                       v4_internal_checker_work="unmeasured; includes child invocations and possibly interrupted checks",
                       message=proposal.metadata["provider_error"])
        else:
            row = evaluator(instance, proposal.text, folder, attempt_id=attempt_id,
                            config=config["evaluation"])
        count = row.get("checked_evaluations", 0)
        if type(count) is not int or count not in (0, 1):
            raise ValueError("invalid completed terminal checker count")
        row.update(attempt=attempt, proposal_tries=1, evaluations_used=count,
                   proposal_path=str(proposal_path), provider=proposal.metadata,
                   evaluation_prompt_sha256=prompt_sha256)
        atomic_json(result_path, row)
        if strategy is not None:
            strategy.observe(proposal, row)
        summary["attempts"].append(row)
        summary["prefixes"] = prefix_summary(summary["attempts"], k)
        summary["updated_at"] = time.time()
        atomic_json(folder / "summary.json", summary)
        if sum(item["checked_evaluations"] for item in summary["attempts"]) == k:
            stop_reason = "checked_budget_reached"
            break
        if proposal.metadata.get("source_exhausted"):
            stop_reason = "proposal_source_exhausted"
            break
    checked = sum(row["checked_evaluations"] for row in summary["attempts"])
    summary.update(complete=checked == k, stop_reason=stop_reason,
                   proposal_tries=len(summary["attempts"]), evaluations_used=checked,
                   checked_evaluations=checked,
                   wasted_proposals=sum(row["checked_evaluations"] == 0 for row in summary["attempts"]),
                   invalid_proposals=sum(row.get("status") == "invalid_layout" for row in summary["attempts"]),
                   provider_failures=sum(row.get("status") == "provider_error" for row in summary["attempts"]),
                   terminal_certificate_check_calls=sum(row.get("terminal_certificate_check_calls", 0) for row in summary["attempts"]),
                   all_full_checker_invocations=None,
                   v4_internal_checker_work="unmeasured; includes child invocations and possibly interrupted checks")
    atomic_json(folder / "summary.json", summary)
    return summary


def run_map(job, output, config):
    from experiments.mvp.strategy.proposer.cohort import _verify_row
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    verified = _verify_row(job)
    if digest(ROOT / verified["input"]) != job["input_sha256"]:
        raise ValueError(f"input changed: {job['input']}")
    try:
        with rule_freeze_v1(enabled=False):
            return execute_map(load_instance(ROOT / job["input"]), job,
                               Path(output) / "maps" / job["map_id"], config)
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()  # Only this process's owned, recorded V4 child.


def main(argv=None):
    from experiments.mvp.strategy.headroom.run import controller_lock, source_snapshot
    from experiments.mvp.strategy.proposer.cohort import DEFAULT_MANIFEST, manifest_jobs
    from experiments.mvp.strategy.proposer.evaluator import DEFAULT_CONFIG
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("spark", "glimmer", "search", "rule"), required=True)
    parser.add_argument("--k", type=int, choices=(1, 4, 16), required=True)
    parser.add_argument("--max-proposals", type=int, default=256,
                        help="Finite proposal-try cap per map; incomplete maps never satisfy checked budget")
    parser.add_argument("--provider", choices=("dry-run", "jsonl"), default="dry-run")
    parser.add_argument("--proposals", type=Path)
    parser.add_argument("--canned-text", type=Path)
    parser.add_argument("--maps", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1,
                        help="At most two map workers + two owned V4 children = four CPU workers")
    parser.add_argument("--seed", type=int, default=179)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Print plan only; no model or solver calls")
    args = parser.parse_args(argv)
    if args.provider == "jsonl" and args.proposals is None:
        parser.error("--provider jsonl requires --proposals")
    if args.provider == "jsonl" and args.canned_text:
        parser.error("--canned-text is only for --provider dry-run")
    if args.arm in {"search", "rule"} and (args.proposals or args.canned_text):
        parser.error("software arms generate their own proposals; external response files are not used")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.max_proposals < args.k:
        parser.error("--max-proposals must be at least k")
    output = args.output.resolve()
    try:
        output.relative_to(HERE)
    except ValueError:
        parser.error("--output must be inside experiments/mvp/strategy/proposer/")
    jobs = manifest_jobs(args.maps)
    raw_manifest = json.loads(args.maps.read_text())
    raw_rows = raw_manifest.get("maps", raw_manifest.get("instances", [])) if isinstance(raw_manifest, dict) else raw_manifest
    metadata = {str(row.get("input", row.get("path"))): row for row in raw_rows if isinstance(row, dict)}
    jobs = [dict(metadata.get(job["input"], {}), **job) for job in jobs]
    full_maps = [{key: job[key] for key in ('map_id', 'input', 'input_sha256')} for job in jobs]
    if args.only:
        missing = set(args.only) - {job["map_id"] for job in jobs}
        if missing:
            parser.error(f"unknown map IDs: {sorted(missing)}")
        jobs = [job for job in jobs if job["map_id"] in args.only]
    if args.limit:
        jobs = jobs[:args.limit]
    diagnostic_subset = len(jobs) != len(full_maps)
    provider_path = args.proposals or args.canned_text
    provider_bytes = provider_path.read_bytes() if provider_path else None
    provider_sha256 = hashlib.sha256(provider_bytes).hexdigest() if provider_bytes is not None else None
    snapshot_path = output / "provider-source.snapshot" if provider_path else None
    from experiments.mvp.strategy.proposer.rule import policy_registration
    rule_policy = policy_registration()
    spark_cost = None
    if args.arm == "spark" and args.provider == "jsonl":
        from experiments.mvp.strategy.proposer.cost import validate_spark_source_cost
        spark_cost = validate_spark_source_cost(provider_bytes, expected_sha256=provider_sha256)
    config = dict(arm=args.arm, k=args.k, max_proposals=args.max_proposals,
                  diagnostic_subset=diagnostic_subset,
                  provider=args.arm if args.arm in {"search", "rule"} else args.provider, seed=args.seed,
                  proposals=str(args.proposals.resolve()) if args.proposals else None,
                  canned_text=str(args.canned_text.resolve()) if args.canned_text else None,
                  provider_snapshot=str(snapshot_path) if snapshot_path else None,
                  provider_source_sha256=provider_sha256,
                  rule_policy=rule_policy,
                  evaluation=dict(DEFAULT_CONFIG))
    sources = source_snapshot()
    sources.update({str(p.relative_to(ROOT)): digest(p) for p in HERE.glob("*.py")})
    sources["experiments/mvp/strategy/blockrule/rule.py"] = rule_policy["source_sha256"]["rule.py"]
    from experiments.mvp.strategy.proposer.registration import MODEL_CONFIGS, GENERATION_SETTINGS, GENERATION_SEED_BASE
    record = dict(version=2, config=config, maps=jobs, maps_sha256=digest(args.maps), sources=sources,
                  protocol="D185-step2-v1",
                  diagnostic_subset=diagnostic_subset,
                  cohort_registration=dict(format=raw_manifest["format"], manifest_sha256=digest(args.maps),
                      medium_source=raw_manifest["medium_source"],
                      full_maps=full_maps,
                      frozen_before_proposer_runs=raw_manifest["frozen_before_proposer_runs"]),
                  rule_policy=rule_policy, spark_cost=spark_cost, spark_budget=None,
                  budget_unit="completed_terminal_certificate_checks",
                  provider_source_sha256=provider_sha256,
                  result_kind="canned_smoke" if args.arm in {"spark", "glimmer"} and args.provider == "dry-run" else "zero_shot_collected" if args.arm in {"spark", "glimmer"} else args.arm,
                  collection_registration=dict(models=MODEL_CONFIGS, generation_settings=GENERATION_SETTINGS,
                                               generation_seed_base=GENERATION_SEED_BASE),
                  cpu_only=True, no_model_calls=True, no_repairs=True, native_threads=1,
                  max_compute_workers=2 * args.workers, workers=args.workers,
                  warmup_evaluations=0, default_evaluations=0,
                  search_policy="random first; alternate random/local around strictly better checked incumbent")
    if args.dry_run:
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    with controller_lock(output):
        run_path = output / "run.json"
        previous = json.loads(run_path.read_text()) if run_path.exists() else None
        if previous is not None:
            record["spark_budget"] = previous.get("spark_budget")
            if previous != record:
                raise RuntimeError("Configuration, source hashes, or frozen inputs changed; use a new output directory")
        if spark_cost is not None:
            from experiments.mvp.strategy.proposer.cost import register_spark_pool, assert_registry_covers
            registry = register_spark_pool(spark_cost, HERE / 'budget' / 'd185-spark.json')
            assert_registry_covers(spark_cost, registry)
            if previous is None:
                record['spark_budget'] = registry
        if snapshot_path:
            if snapshot_path.exists():
                if snapshot_path.read_bytes() != provider_bytes:
                    raise RuntimeError("immutable provider snapshot changed; use a new output directory")
            else:
                snapshot_path.write_bytes(provider_bytes)
        atomic_json(run_path, record)
        errors = []
        summaries = []
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn")) as pool:
            futures = {pool.submit(run_map, job, str(output), config): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    summary = future.result()
                    summaries.append(summary)
                    print(json.dumps(dict(map_id=job["map_id"], complete=summary["complete"],
                                          prefixes=summary["prefixes"])), flush=True)
                except Exception as error:
                    errors.append(dict(map_id=job["map_id"], error=f"{type(error).__name__}: {error}"))
        completion = dict(maps=len(jobs), errors=errors,
                          complete=not errors and len(summaries) == len(jobs) and all(row["complete"] for row in summaries),
                          budget_unit="completed_terminal_certificate_checks",
                          proposal_tries=sum(row["proposal_tries"] for row in summaries),
                          wasted_proposals=sum(row["wasted_proposals"] for row in summaries),
                          incomplete_maps=[row["map_id"] for row in summaries if not row["complete"]],
                          prefixes=aggregate_prefixes(summaries, args.k),
                          mean_scope="passing maps within this arm; paired comparisons require same map intersection")
        atomic_json(output / "completion.json", completion)
        print(json.dumps(completion), flush=True)
        return 0 if completion["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
