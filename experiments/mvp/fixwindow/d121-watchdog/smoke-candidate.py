"""Measured, resumable MVP OFF smoke; no model/GPU/cloud, no gold publication.

Each case retains current-generator input, raw/final plan, full checker report,
and actual production token-mask replay. C2 is disabled for construction and
the primary replay (D79). C2-on is an explicit diagnostic opt-in, never a gate.
APPROXIMATION: bounded constructor-only search (no objective-improvement LNS)
and finite stage deadlines measure readiness, not solver optimality or yield.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import multiprocessing
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
DRIVER_SOURCE = Path(__file__).read_bytes()
DRIVER_SHA256 = hashlib.sha256(DRIVER_SOURCE).hexdigest()
SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
RUNGS = ("tiny", "small", "medium")
C2_REASON = "C2 off (D78/D79); unqualified linear profiles are not used; full checker remains the gate"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def source_hash():
    digest = hashlib.sha256()
    for folder, pattern in (("src/stowage", "*.py"), ("src/llm/stowlm", "*.py"),
                            ("params", "*.toml"), ("src/stowage/rules", "*.toml"),
                            ("src/stowage/rules/imdg/data", "*.json"),
                            ("src/stowage/checker", "*.json")):
        for path in sorted((ROOT / folder).rglob(pattern)):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def source_files():
    result = {}
    for folder, pattern in (("src/stowage", "*.py"), ("src/llm/stowlm", "*.py"),
                            ("params", "*.toml"), ("src/stowage/rules", "*.toml"),
                            ("src/stowage/rules/imdg/data", "*.json"),
                            ("src/stowage/checker", "*.json")):
        for path in sorted((ROOT / folder).rglob(pattern)):
            result[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def relevant_to_smoke(path):
    return not path.startswith("src/llm/stowlm/") or path in {
        "src/llm/stowlm/__init__.py", "src/llm/stowlm/format.py", "src/llm/stowlm/mask.py"}


def generator_settings(args, rung):
    """Resolve explicit opt-ins without assuming another executive's API exists."""
    requested_boxes = {"small": args.small_boxes, "medium": args.medium_boxes}.get(rung)
    family = "v2_random" if requested_boxes is not None else args.generator_family
    options = {}
    if args.preset == "d83":
        if rung in ("small", "medium"):
            options = {"booking_slack": .25, "booking_mass_fraction": .55}
        return family, requested_boxes, options
    if args.booking_slack is not None:
        options[args.booking_slack_parameter] = args.booking_slack
    if args.booking_mass_fraction is not None:
        if "booking_mass_fraction" in options:
            raise ValueError("Booking slack keyword collides with --booking-mass-fraction")
        options["booking_mass_fraction"] = args.booking_mass_fraction
    return family, requested_boxes, options


def validate_opt_ins(args):
    """Only inspect APIs; never generate, construct, qualify C2, or warm caches."""
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        raise ValueError("MVP smoke requires rule-freeze OFF")
    if args.preset == "d83" and (args.booking_slack is not None
            or args.booking_mass_fraction is not None or args.small_boxes is not None
            or args.medium_boxes is not None or args.constructor_mode != "default"
            or args.compare_c2 or args.generator_family != "random_yard"
            or args.booking_slack_parameter != "booking_slack"):
        raise ValueError("--preset d83 fixes per-rung generator settings, random_yard, "
            "default constructor and C2 off; omit conflicting overrides")
    mappings = {"c2": "off", "c2_reason": C2_REASON,
                "constructor_mode": args.constructor_mode, "preset": args.preset, "generators": {}}
    if args.constructor_mode == "v4":
        from stowage.solver.scalable import ScalableConfig
        if "constructor_v4" not in inspect.signature(ScalableConfig).parameters:
            raise ValueError("Requested constructor_v4 is not available in ScalableConfig")
        mappings["constructor_option"] = {"constructor_v4": True}
    for rung in args.rung:
        family, _, options = generator_settings(args, rung)
        if options:
            if family == "random_yard":
                from stowage.generate.random_yard import generate_random_instance as generator
            else:
                from stowage.generate.instances_v2_random import generate_random_v2 as generator
            missing = set(options) - inspect.signature(generator).parameters.keys()
            if missing:
                raise ValueError(f"{family} does not expose {sorted(missing)}; "
                    "check solve47/slack/STATUS.md and --booking-slack-parameter")
        mappings["generators"][rung] = {"family": family, "options": options,
            "evidence_role": "diagnostic" if args.preset == "d83" and rung == "medium" else "smoke"}
    return mappings


@contextmanager
def deadline(seconds):
    """Operational watchdog: expiry is unavailable, never a physical verdict.

    A caught alarm still rejects normal return. Nested scopes preserve the
    earlier timer and restore its remaining time instead of renewing it.
    """
    if isinstance(seconds, bool) or not 0 < float(seconds) < float('inf'):
        raise ValueError('watchdog seconds must be finite and positive')
    seconds = float(seconds)
    clock = time.monotonic
    started = clock()
    previous = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer[0] > 0:
        raise RuntimeError("smoke requires ownership of its own process alarm")
    previous_due = started + previous_timer[0] if previous_timer[0] > 0 else None
    own_due = started + seconds
    due = min(own_due, previous_due) if previous_due is not None else own_due
    expired_flag = False
    forwarded_previous = False

    def expired(*args):
        nonlocal expired_flag, forwarded_previous
        expired_flag = True
        if (previous_due is not None and previous_due <= own_due
                and clock() >= previous_due and not forwarded_previous and callable(previous)):
            forwarded_previous = True
            previous(*args)
        raise TimeoutError(f'External watchdog exceeded {seconds}s (including swallowed alarm)')

    def reject_expiry():
        if expired_flag or clock() >= due:
            expired()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(1e-9, due-clock()))
    try:
        try:
            yield
        except Exception:
            # A late failure report/ordinary exception is still unavailable;
            # operator BaseException cancellation keeps its original meaning.
            reject_expiry()
            raise
        else:
            reject_expiry()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_due is not None:
            remaining = previous_due-clock()
            interval = previous_timer[1]
            if remaining <= 0 and interval > 0:
                remaining = interval-((-remaining) % interval)
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining, interval)


def token_replay(instance, plan, c2, row, *, require_complete=True):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder, MaskError
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    actions = space.from_plan(instance, plan)
    start = time.perf_counter()
    engine = make_feasibility_engine(instance, space,
        config=FeasibilityConfig(extra={"global_screen": int(c2)}))
    row.update(engine_setup_s=time.perf_counter()-start,
               diagnostics=engine.diagnostics, mask_version=MASK_VERSION)
    if c2 and engine.diagnostics.get("global_screen") != "on":
        raise RuntimeError(f"C2-on requested but engine reports {engine.diagnostics.get('global_screen')}")
    start = time.perf_counter()
    decoder = MaskedDecoder(engine,
        ActionCodec(load_tokenizer(), [box.id for box in instance.containers]), MaskConfig())
    row["decoder_codec_setup_s"] = time.perf_counter()-start
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
            except MaskError:
                row["masked_action"] = {"index": index, "action": space.encode(action), "token": token}
                row["decoder_diagnostics"] = decoder.diagnostics
                raise
            token_ms.append((time.perf_counter()-start)*1000)
        action_ms.append((time.perf_counter()-action_start)*1000)
        row["actions_completed"] = index+1
    if not require_complete:
        row.update(status="partial_prefix", actions_equal=decoder.actions == list(actions),
                   tokens=len(decoder.tokens), engine_stats=asdict(engine.stats()),
                   prefix_passed=decoder.actions == list(actions), passed=False,
                   plan_complete=engine.is_complete())
        return
    for token in decoder.codec.end_plan_tokens:
        start = time.perf_counter()
        decoder.advance(token)
        token_ms.append((time.perf_counter()-start)*1000)
    row.update(status=decoder.status, actions_equal=decoder.actions == list(actions),
               tokens=len(decoder.tokens), engine_stats=asdict(engine.stats()))
    row["passed"] = decoder.status == "complete" and row["actions_equal"]


def summarize_replay(row):
    row["latency_population"] = ("all completed steps" if row.get("passed") else
        "completed prefix only; failed/interrupted step is censored and excluded")
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
    prior = json.loads(result_path.read_text()) if result_path.exists() else None
    if prior is not None and args.rerun:
        raise ValueError("Existing smoke evidence is immutable; use a fresh output directory for retries")
    family, requested_boxes, generation_options = generator_settings(args, rung)
    request = dict(requested_boxes=requested_boxes, generation_s=args.generation_s,
        solver_s=args.solver_s, ballast_s=args.ballast_s, checker_s=args.checker_s,
        mask_s=args.mask_s, max_attempts=args.max_attempts,
        initial_strategy=args.initial_strategy, max_restarts=args.max_restarts,
        reduced_blocking_ratio=args.reduced_blocking_ratio if family == "v2_random" else None,
        generator_family=family, generator_options=generation_options,
        preset=args.preset,
        evidence_role="diagnostic" if args.preset == "d83" and rung == "medium" else "smoke",
        constructor_mode=args.constructor_mode, compare_c2=args.compare_c2,
        seed_base=args.seed_base, c2=False, c2_reason=C2_REASON)
    configuration_sha256 = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    current_source = source_hash()
    current_files = source_files()
    startup_files = getattr(args, "source_files_at_start", current_files)
    changed_since_start = [p for p in current_files.keys() | startup_files.keys()
        if current_files.get(p) != startup_files.get(p) and relevant_to_smoke(p)]
    if changed_since_start:
        raise ValueError(f"Pipeline sources changed since process imports; restart: {changed_since_start}")
    if result_path.exists() and not args.rerun:
        complete = prior.get("completed", "wall_s" in prior and "source_sha256_end" in prior)
        if not complete or prior.get("source_sha256_start") != current_source or prior.get("source_sha256_end") != current_source:
            raise ValueError(f"Incomplete/stale smoke result {result_path}; use a fresh output directory")
        if prior.get("request") != request or prior.get("driver_sha256") != DRIVER_SHA256:
            raise ValueError("Cached result settings/driver differ; use a fresh output directory")
        return prior
    seed = args.seed_base + SHIPS.index(ship)*100 + RUNGS.index(rung)*10 + replicate
    # Smoke inputs are synthetic diagnostics and are never inserted into train/eval releases.
    row = dict(case=case, ship=ship, rung=rung, replicate=replicate, seed=seed,
        rule_freeze_v1=False, c2_construction=False, stages={}, passed=False,
        started_utc=datetime.now(timezone.utc).isoformat(), source_sha256_start=current_source,
        driver_sha256=DRIVER_SHA256, completed=False, request=request,
        c2_reason=C2_REASON, campaign_id=args.output.name,
        preset=args.preset, evidence_role=request["evidence_role"],
        diagnostic_only=request["evidence_role"] == "diagnostic",
        worker_pid=os.getpid(), native_threads=1,
        configuration_sha256=configuration_sha256)
    if requested_boxes is not None:
        # APPROXIMATION: reduce booking quantity on the unchanged decision
        # geometry; retain the cargo mix, all hard rules, ballast and checker.
        row["approximation"] = f"Reduced booking quantity: {requested_boxes} boxes on unchanged {rung} geometry"
        row["requested_boxes"] = requested_boxes
    if "booking_slack" in generation_options:
        # APPROXIMATION: opt-in synthetic underbooking reduces demand; all
        # hard checker constraints and the original decision geometry remain.
        row["booking_slack_approximation"] = dict(value=generation_options["booking_slack"],
            parameter=args.booking_slack_parameter, family=family)
    if "booking_mass_fraction" in generation_options:
        # APPROXIMATION: condition synthetic demand on a fraction of the
        # scaled full-export cargo budget; retain every hard checker rule.
        row["booking_mass_approximation"] = dict(value=generation_options["booking_mass_fraction"],
            parameter="booking_mass_fraction", family=family,
            basis="scaled full-export cargo budget evaluated at realised booking mass")
    input_meta_path = directory / "input-metadata.json"
    save(result_path, row)
    save(directory / "source-before.json", current_files)
    current_stage = None
    interrupted = False
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
            if isinstance(exc, TimeoutError):
                row["stages"][name].update(unavailable=True, watchdog_timeout=True)
                row.update(unavailable=True, watchdog_timeout=True, operational_status='unavailable')
            row["stages"][name].update(passed=False, error=f"{type(exc).__name__}: {exc}",
                                       traceback=traceback.format_exc())
            raise
        finally:
            row["stages"][name]["wall_s"] = time.perf_counter()-phase
            save(result_path, row)
    try:
        with stage("generation", args.generation_s) as details:
            input_path = directory / "instance.json"
            if input_path.exists():
                if not input_meta_path.exists():
                    raise ValueError("Existing input lacks a source/content pin; use a fresh output directory")
                input_meta = json.loads(input_meta_path.read_text())
                if input_meta.get("sha256") != hashlib.sha256(input_path.read_bytes()).hexdigest():
                    raise ValueError("Existing input differs from its recorded content pin")
                if input_meta.get("configuration_sha256") != configuration_sha256:
                    raise ValueError("Existing input lacks/mismatches this campaign's configuration pin")
                instance = load_instance(input_path)
                details["resumed_input"] = True
                details["input_source_sha256"] = input_meta.get("source_sha256")
                details["historical_input"] = input_meta.get("source_sha256") != current_source
            else:
                if family == "v2_random":
                    from stowage.generate.instances import GenerationReport
                    from stowage.generate.instances_v2_random import generate_random_v2
                    audit = GenerationReport()
                else:
                    audit = RandomGenerationReport()
                try:
                    if family == "v2_random":
                        instance = generate_random_v2(rung, seed=seed, vessel_name=ship,
                            port_name="laem_chabang_like", n_boxes=requested_boxes,
                            noise=args.reduced_blocking_ratio, max_attempts=args.max_attempts,
                            report=audit, **generation_options)
                    else:
                        instance = generate_random_instance(rung, seed=seed, vessel_name=ship,
                            port_name="laem_chabang_like", max_attempts=args.max_attempts,
                            report=audit, c2_screen=False, **generation_options)
                finally:
                    save(directory / "generation_audit.json", asdict(audit))
                save_instance(instance, input_path)
                save(input_meta_path, dict(source_sha256=current_source,
                    sha256=hashlib.sha256(input_path.read_bytes()).hexdigest(),
                    configuration_sha256=configuration_sha256))
            require_clean(instance, what="MVP smoke input")
            if requested_boxes is not None and len(instance.load_list) != requested_boxes:
                raise ValueError("Existing/generated input does not match explicit booking quantity")
            details.update(passed=True, instance_id=instance.id, boxes=len(instance.load_list),
                           cells=len(instance.vessel.cells), sha256=hashlib.sha256(input_path.read_bytes()).hexdigest())
        with stage("constructor", args.solver_s+5) as details:
            # APPROXIMATION: measure bounded construction without the optional
            # objective-improvement search; the full checker still gates it.
            planner = ScalableSolver(ScalableConfig(max_neighborhoods=0,
                initial_strategy=args.initial_strategy, max_restarts=args.max_restarts,
                feasibility_config=FeasibilityConfig(extra={"global_screen": 0}),
                **({"constructor_v4": True} if args.constructor_mode == "v4" else {})))
            details["config"] = asdict(planner.config)
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
        for c2 in ([False, True] if args.compare_c2 and rung == "medium" and replicate == 0 else [False]):
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
    except BaseException as exc:
        interrupted = True
        row.update(interrupted=True, blocked_at=current_stage,
                   error=f"{type(exc).__name__}: {exc}")
        if current_stage in row["stages"]:
            row["stages"][current_stage].update(passed=False, interrupted=True,
                error=row["error"])
        raise
    finally:
        row.update(wall_s=time.perf_counter()-start_case, source_sha256_end=source_hash(),
                   process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   completed=not interrupted)
        final_files = source_files()
        save(directory / "source-after.json", final_files)
        row["changed_source_files"] = sorted(p for p in current_files.keys() | final_files.keys()
            if current_files.get(p) != final_files.get(p))
        row["broad_source_drift"] = row["source_sha256_start"] != row["source_sha256_end"]
        row["current_tree_evidence_valid"] = not any(relevant_to_smoke(p) for p in row["changed_source_files"])
        row["pipeline_passed"] = row["passed"] and not interrupted
        row["passed"] = row["pipeline_passed"] and row["current_tree_evidence_valid"]
        for name, details in row["stages"].items():
            if name == "mask_c2_on" or details.get("passed"):
                continue
            row["first_failure"] = dict(stage=name,
                rule=details.get("first_failing_rule"),
                error=details.get("error") or row.get("error"),
                masked_action=details.get("masked_action"))
            break
        save(result_path, row)
    print(json.dumps({k: row.get(k) for k in ("case", "passed", "blocked_at", "error", "wall_s")}), flush=True)
    return row


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=("d83",),
        help="D83: tiny defaults; small/medium slack .25 and mass fraction .55; medium diagnostic; C2 off/default constructor")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=1,
        help="Independent case processes (spawn); one native thread each; reserve compute before running")
    parser.add_argument("--output", type=Path,
        help="Fresh immutable campaign directory; default is a timestamped v54b directory")
    parser.add_argument("--ship", choices=SHIPS, nargs="+", default=list(SHIPS))
    parser.add_argument("--rung", choices=RUNGS, nargs="+", default=list(RUNGS))
    parser.add_argument("--replicates", type=int, default=2)
    parser.add_argument("--generation-s", type=float, default=90)
    parser.add_argument("--solver-s", type=float, default=60)
    parser.add_argument("--ballast-s", type=float, default=45)
    parser.add_argument("--checker-s", type=float, default=120)
    parser.add_argument("--mask-s", type=float, default=180)
    parser.add_argument("--max-attempts", type=int, default=20)
    parser.add_argument("--initial-strategy", type=int, choices=range(4), default=0)
    parser.add_argument("--max-restarts", type=int, default=3)
    parser.add_argument("--small-boxes", type=int)
    parser.add_argument("--medium-boxes", type=int)
    parser.add_argument("--reduced-blocking-ratio", type=float, default=.8)
    parser.add_argument("--generator-family", choices=("random_yard", "v2_random"), default="random_yard")
    parser.add_argument("--booking-slack", type=float, help="Opt-in booking slack fraction, e.g. 0.15")
    parser.add_argument("--booking-mass-fraction", type=float,
        help="Opt-in synthetic booking mass / scaled full-export cargo budget, in (0, 1]")
    parser.add_argument("--booking-slack-parameter", default="booking_slack",
        help="Generator keyword from solve47/slack/STATUS.md; validated before any solve")
    parser.add_argument("--constructor-mode", choices=("default", "v4"), default="default",
        help="v4 maps to ScalableConfig(constructor_v4=True); default leaves its default unchanged")
    parser.add_argument("--seed-base", type=int, default=202610295400)
    parser.add_argument("--compare-c2", action="store_true",
        help="Optional three-medium C2-on diagnostics; never requalifies profiles; default OFF (D79)")
    parser.add_argument("--validate-config", action="store_true",
        help="Validate opt-in API mappings and print config without generating or solving")
    parser.add_argument("--render-readiness", action="store_true",
        help="Render experiments/mvp/readiness.md from this campaign after saving results/table")
    parser.add_argument("--rerun", action="store_true", help="Reserved; existing evidence cannot be overwritten")
    return parser


def execute_cases(args):
    # First submit all nine cells before the second replicate. Each process
    # owns only its case directory, alarm, and per-case evidence checkpoints.
    cases = [(ship, rung, replicate) for replicate in range(args.replicates)
             for rung in args.rung for ship in args.ship]
    if args.workers == 1:
        for ship, rung, replicate in cases:
            yield run_case(ship, rung, replicate, args)
        return
    with ProcessPoolExecutor(max_workers=args.workers,
            mp_context=multiprocessing.get_context("spawn")) as executor:
        futures = [executor.submit(run_case, ship, rung, replicate, args)
                   for ship, rung, replicate in cases]
        for future in as_completed(futures):
            yield future.result()


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.replicates < 1 or args.replicates > 10:
        parser.error("--replicates must be between 1 and 10 (seed namespace)")
    if args.seed_base < 0:
        parser.error("--seed-base must be nonnegative")
    for name in ("generation_s", "solver_s", "ballast_s", "checker_s", "mask_s"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if args.max_attempts < 1:
        parser.error("--max-attempts must be positive")
    if args.booking_slack is not None and not 0 <= args.booking_slack < 1:
        parser.error("--booking-slack must be a finite fraction in [0, 1)")
    if args.booking_mass_fraction is not None and not 0 < args.booking_mass_fraction <= 1:
        parser.error("--booking-mass-fraction must be a finite fraction in (0, 1]")
    if len(set(args.ship)) != len(args.ship) or len(set(args.rung)) != len(args.rung):
        parser.error("duplicate ships/rungs would duplicate campaign cases")
    try:
        mapping = validate_opt_ins(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.validate_config:
        print(json.dumps(dict(mapping=mapping, expected_cases=len(args.ship)*len(args.rung)*args.replicates,
            seed_base=args.seed_base, workers=args.workers, native_threads=1,
            no_generation_or_solving=True), indent=2))
        return
    if args.output is None:
        args.output = Path(__file__).parent / ("v54b-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    args.output = args.output.resolve()
    args.source_files_at_start = source_files()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / f"driver-{DRIVER_SHA256}.py.txt").write_bytes(DRIVER_SOURCE)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    save(args.output / f"invocation-{stamp}.json", dict(argv=sys.argv,
        config={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        native_threads=1, workers=args.workers, gpu=False, pid=os.getpid(), source_sha256=source_hash(),
        driver_sha256=DRIVER_SHA256, python=sys.version, api_mapping=mapping,
        expected_cases=len(args.ship)*len(args.rung)*args.replicates, c2_reason=C2_REASON))
    rows = []
    for row in execute_cases(args):
        rows.append(row)
        save(args.output / "results.json", dict(rule_freeze_v1=False, outcomes=rows,
            passed=sum(r["passed"] for r in rows), processed=len(rows),
            expected_cases=len(args.ship)*len(args.rung)*args.replicates,
            campaign_complete=len(rows)==len(args.ship)*len(args.rung)*args.replicates,
            c2=False, c2_reason=C2_REASON, driver_sha256=DRIVER_SHA256,
            preset=args.preset, workers=args.workers,
            diagnostic_cases=sum(bool(r.get("diagnostic_only")) for r in rows)))
    # Regenerate only this campaign's measured table. The leader renders readiness.md.
    from summarize import render
    rendered = render(args.output)
    (args.output / "table.md").write_text(rendered)
    print(rendered, end="")
    if args.render_readiness:
        from render_readiness import render as render_readiness
        render_readiness(args.output)
    return 0 if all(r["passed"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
