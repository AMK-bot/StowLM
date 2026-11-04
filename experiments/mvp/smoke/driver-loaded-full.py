"""Measured, resumable MVP OFF smoke; no model/GPU/cloud, no gold publication.

Each case retains current-generator input, raw/final plan, full checker report,
and actual production token-mask replay. C2 is disabled for construction and
the primary replay; medium also attempts C2-on replay without requalification.
APPROXIMATION: bounded constructor-only search (no objective-improvement LNS)
and finite stage deadlines measure readiness, not solver optimality or yield.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import statistics
import sys
import time
import traceback

for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
            "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[key] = "1"

ROOT = Path(__file__).resolve().parents[3]
SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
RUNGS = ("tiny", "small", "medium")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def source_hash():
    digest = hashlib.sha256()
    for folder, pattern in (("src/stowage", "*.py"), ("src/llm/stowlm", "*.py"),
                            ("params", "*.toml"), ("src/stowage/rules", "*.toml")):
        for path in sorted((ROOT / folder).rglob(pattern)):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


@contextmanager
def deadline(seconds):
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise RuntimeError("smoke requires ownership of its own process alarm")
    def expired(*args):
        raise TimeoutError(f"stage exceeded {seconds}s")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def token_replay(instance, plan, c2, row):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    actions = space.from_plan(instance, plan)
    start = time.perf_counter()
    engine = make_feasibility_engine(instance, space,
        config=FeasibilityConfig(extra={"global_screen": int(c2)}))
    row.update(engine_setup_s=time.perf_counter()-start,
               diagnostics=engine.diagnostics, mask_version=MASK_VERSION)
    decoder = MaskedDecoder(engine,
        ActionCodec(load_tokenizer(), [box.id for box in instance.containers]), MaskConfig())
    token_ms, action_ms = [], []
    row.update(actions_expected=len(actions), actions_completed=0, token_ms=token_ms,
               action_ms=action_ms, masked_action=None, status="running")
    for index, action in enumerate(actions):
        row["current_action_index"] = index
        action_start = time.perf_counter()
        for token in decoder.codec.encode_action(action):
            start = time.perf_counter()
            try:
                decoder.advance(token)
            except Exception:
                row["masked_action"] = {"index": index, "action": space.encode(action), "token": token}
                raise
            token_ms.append((time.perf_counter()-start)*1000)
        action_ms.append((time.perf_counter()-action_start)*1000)
        row["actions_completed"] = index+1
    for token in decoder.codec.end_plan_tokens:
        start = time.perf_counter()
        decoder.advance(token)
        token_ms.append((time.perf_counter()-start)*1000)
    row.update(status=decoder.status, actions_equal=decoder.actions == list(actions),
               tokens=len(decoder.tokens), engine_stats=asdict(engine.stats()))
    row["passed"] = decoder.status == "complete" and row["actions_equal"]


def summarize_replay(row):
    for name in ("token_ms", "action_ms"):
        values = row.get(name, [])
        if values:
            ordered = sorted(values)
            # Nearest-rank p95 includes slow state-mask construction steps.
            row[name + "_median"] = statistics.median(values)
            row[name + "_p95"] = ordered[max(0, (95*len(values)+99)//100-1)]


def run_case(ship, rung, replicate, args):
    from stowage.checker import check
    from stowage.feasibility import FeasibilityConfig
    from stowage.frozen import require_clean
    from stowage.generate.random_yard import RandomGenerationReport, generate_random_instance
    from stowage.io import load_instance, save_instance, save_plan
    from stowage.planner import finalize
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver.scalable import ScalableConfig, ScalableSolver
    assert not rule_freeze_v1_enabled(), "This driver never changes the rule switch"
    case = f"{rung}-{ship}-{replicate}"
    directory = args.output / case
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / "result.json"
    if result_path.exists() and not args.rerun:
        return json.loads(result_path.read_text())
    seed = 2026102900 + SHIPS.index(ship)*100 + RUNGS.index(rung)*10 + replicate
    # Smoke inputs are synthetic diagnostics and are never inserted into train/eval releases.
    row = dict(case=case, ship=ship, rung=rung, replicate=replicate, seed=seed,
        rule_freeze_v1=False, c2_construction=False, stages={}, passed=False,
        started_utc=datetime.now(timezone.utc).isoformat(), source_sha256_start=source_hash())
    save(result_path, row)
    current_stage = None
    start_case = time.perf_counter()

    @contextmanager
    def stage(name, limit):
        nonlocal current_stage
        current_stage = name
        phase = time.perf_counter()
        row["stages"][name] = dict(passed=False, limit_s=limit)
        save(result_path, row)
        try:
            with deadline(limit):
                yield row["stages"][name]
        except Exception as exc:
            row["stages"][name].update(error=f"{type(exc).__name__}: {exc}",
                                       traceback=traceback.format_exc())
            raise
        finally:
            row["stages"][name]["wall_s"] = time.perf_counter()-phase
            save(result_path, row)
    try:
        with stage("generation", args.generation_s) as details:
            input_path = directory / "instance.json"
            if input_path.exists():
                instance = load_instance(input_path)
                details["resumed_input"] = True
            else:
                audit = RandomGenerationReport()
                instance = generate_random_instance(rung, seed=seed, vessel_name=ship,
                    port_name="laem_chabang_like", max_attempts=args.max_attempts, report=audit)
                save_instance(instance, input_path)
                save(directory / "generation_audit.json", asdict(audit))
            require_clean(instance, what="MVP smoke input")
            details.update(passed=True, instance_id=instance.id, boxes=len(instance.load_list),
                           cells=len(instance.vessel.cells), sha256=hashlib.sha256(input_path.read_bytes()).hexdigest())
        with stage("constructor", args.solver_s+5) as details:
            planner = ScalableSolver(ScalableConfig(max_neighborhoods=0,
                feasibility_config=FeasibilityConfig(extra={"global_screen": 0})))
            result = planner.plan(instance, seed=seed, time_limit_s=args.solver_s)
            details.update(passed=result.status == "ok", status=result.status,
                           message=result.message, metrics=result.metrics)
            if result.plan is not None:
                save_plan(result.plan, directory / "raw_plan.json")
            if result.status != "ok" or result.plan is None:
                raise RuntimeError(f"constructor returned {result.status}: {result.message}")
        with stage("ballast", args.ballast_s+5) as details:
            result = finalize(instance, result, time_limit_s=args.ballast_s)
            details.update(passed=result.status == "ok", status=result.status,
                           message=result.message, metrics=result.metrics)
            if result.plan is not None:
                save_plan(result.plan, directory / "plan.json")
            if result.status != "ok":
                raise RuntimeError(f"finalize returned {result.status}: {result.message}")
        with stage("checker", args.checker_s) as details:
            report = check(instance, result.plan)
            save(directory / "checker.json", report.model_dump(mode="json"))
            hard = [v for v in report.violations if v.severity == "hard"]
            details.update(passed=report.passed, first_failing_rule=hard[0].rule_id if hard else None,
                           hard_violations=len(hard), violations=[v.model_dump(mode="json") for v in report.violations])
        for c2 in ([False, True] if rung == "medium" and replicate == 0 else [False]):
            try:
                with stage("mask_c2_" + ("on" if c2 else "off"), args.mask_s) as details:
                    token_replay(instance, result.plan, c2, details)
            except Exception:
                pass
            summarize_replay(details)
            save(result_path, row)
        row["passed"] = all(row["stages"].get(s, {}).get("passed", False)
            for s in ("generation", "constructor", "ballast", "checker", "mask_c2_off"))
    except Exception as exc:
        row.update(blocked_at=current_stage, error=f"{type(exc).__name__}: {exc}")
    finally:
        row.update(wall_s=time.perf_counter()-start_case, source_sha256_end=source_hash(),
                   process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        save(result_path, row)
    print(json.dumps({k: row.get(k) for k in ("case", "passed", "blocked_at", "error", "wall_s")}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "run-20261029")
    parser.add_argument("--ship", choices=SHIPS, nargs="+", default=list(SHIPS))
    parser.add_argument("--rung", choices=RUNGS, nargs="+", default=list(RUNGS))
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--generation-s", type=float, default=90)
    parser.add_argument("--solver-s", type=float, default=60)
    parser.add_argument("--ballast-s", type=float, default=45)
    parser.add_argument("--checker-s", type=float, default=120)
    parser.add_argument("--mask-s", type=float, default=180)
    parser.add_argument("--max-attempts", type=int, default=20)
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()
    rows = []
    # First cover all nine cells before spending the second replicate.
    for replicate in range(args.replicates):
        for rung in args.rung:
            for ship in args.ship:
                rows.append(run_case(ship, rung, replicate, args))
                save(args.output / "results.json", dict(rule_freeze_v1=False, outcomes=rows,
                    passed=sum(r["passed"] for r in rows), processed=len(rows)))


if __name__ == "__main__":
    main()
