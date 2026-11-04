"""Production-producer D72 yard measurement; no planner, no source modifications.

The real producer supplies the FIRST booking passing its pre-yard cargo screens.
Capture at its module-local simulator boundary, then terminate generation before
completion/C2/background qualification. Replay the identical calls/seed/horizon
at shares 0/.25/noise. These are pre-final-screen candidates, NOT emitted instances.
The producer's own demand-sized synthetic layout and workload remain unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SOURCE_ROOT = Path(os.environ.get("D72_SOURCE_ROOT", ROOT)).resolve()
SHIPS = ("feeder_500", "feeder_800", "feeder_1700")
PORTS = ("laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast")
NOISES = (0., .2, .5, .8)  # union of campaign and split/freeze noise families
RUNG = ("medium", "full")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def source_manifest():
    paths = [p for directory in (SOURCE_ROOT / "src/stowage", SOURCE_ROOT / "params")
             for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    paths += [SOURCE_ROOT / "experiments/splits/freeze.py", SOURCE_ROOT / "pyproject.toml",
              SOURCE_ROOT / "data/raw/kcs/kcs.fix"]
    files = {str(p.relative_to(SOURCE_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    files["experiment/run_measurement.py"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    files["environment/uv.lock"] = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    return {"sha256": hashlib.sha256(canonical(files).encode()).hexdigest(), "files": files}


class Captured(BaseException):
    def __init__(self, calls, parameters, kwargs):
        self.calls, self.parameters, self.kwargs = calls, parameters, kwargs


def worker(spec):
    started = time.perf_counter()
    before = source_manifest()
    row = {"schema_version": 1, "spec": spec,
           "worker_pid": os.getpid(), "driver_pid": os.getppid(),
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "qualification": "real producer pre-final-screen yard candidate; NOT an emitted/accepted instance",
           "source_before": before["sha256"], "source_root": str(SOURCE_ROOT), "arms": {},
           "load_average_start": os.getloadavg(),
           "runtime": {"python": sys.version, "dependencies": {name: version(name) for name in ("numpy", "scipy", "pydantic")}}}
    # Each case has a fresh process: loaded modules cannot silently retain a
    # previous case's source during concurrent producer edits.
    try:
        sys.path.insert(0, str(SOURCE_ROOT / "src"))
        from stowage.generate import instances
        from stowage.rules.catalogue import rule_freeze_v1
        from stowage.yard.simulator import simulate_port
        from stowage.yard.yield_measurement import measure_snapshot
        from stowage.ports import effective_yard_tiers
        from stowage.conventions import parse_yard
        row["import_origins"] = {"instances": instances.__file__, "simulator": sys.modules[simulate_port.__module__].__file__}

        audit = instances.GenerationReport()
        original = instances.simulate_port

        def capture(calls, parameters, **kwargs):
            raise Captured(calls, parameters, kwargs)

        preparation = time.perf_counter()
        with rule_freeze_v1():
            instances.simulate_port = capture
            try:
                instances.generate_simulated_instance(
                    spec["rung"], seed=spec["seed"], noise=spec["noise"],
                    vessel_name=spec["ship"], port_name=spec["port"], report=audit,
                    max_attempts=spec["max_attempts"], congestion_mix_share=.25)
            except Captured as captured:
                calls, parameters, kwargs = captured.calls, captured.parameters, captured.kwargs
            else:
                raise RuntimeError("producer returned without the expected yard capture")
            finally:
                instances.simulate_port = original
                row["producer_preparation_s"] = time.perf_counter() - preparation
                row["producer_report"] = asdict(audit)

            # Include full validated model payloads plus scheduling/noise inputs.
            from pydantic_core import to_jsonable_python
            input_data = to_jsonable_python({"calls": [asdict(c) for c in calls],
                "parameters": parameters.model_dump(),
                "kwargs": {k: v for k, v in kwargs.items() if k not in {"dig_out", "congestion_mix_share"}}})
            row["paired_input_sha256"] = hashlib.sha256(canonical(input_data).encode()).hexdigest()
            row["simulation_seed"] = kwargs["seed"]
            row["target_count"] = len(calls[0].instance.load_list)
            row["region_cells"] = len(calls[0].instance.vessel.cells)
            row["background_exports"] = len(calls[1].instance.load_list)
            row["background_kind"] = "real producer later-call dry20 empty exports; no imports in this producer"
            registry = [box for call in calls for box in call.instance.containers]
            arm_cache = {}
            for name, share in (("off", 0.), ("default", .25), ("old_n_coupled", spec["noise"])):
                # noise0 forces effective share0 in all arms, so identical runs
                # can share the measured yard without inventing additional draws.
                effective_share = share if spec["noise"] > 0 else 0.
                if share in arm_cache:
                    reused = json.loads(canonical(arm_cache[share]))
                    reused["requested_share"] = share
                    reused["reused_identical_effective_policy"] = True
                    row["arms"][name] = reused
                    continue
                current_input = to_jsonable_python({"calls": [asdict(c) for c in calls],
                    "parameters": parameters.model_dump(),
                    "kwargs": {k: v for k, v in kwargs.items() if k not in {"dig_out", "congestion_mix_share"}}})
                input_sha = hashlib.sha256(canonical(current_input).encode()).hexdigest()
                assert input_sha == row["paired_input_sha256"], "simulator mutated paired demand inputs"
                arm_start = time.perf_counter()
                arm_cpu_start = time.process_time()
                result = simulate_port(calls, parameters, **{**kwargs, "congestion_mix_share": share})
                yard_s = time.perf_counter() - arm_start
                snapshot = next(s for s in result.snapshots if s.call_id == "target")
                instance = calls[0].instance.model_copy(update={"yard": snapshot.yard, "containers": registry})
                metrics = measure_snapshot(instance)
                blocks = []
                by_block = defaultdict(list)
                for stack in snapshot.yard.stacks:
                    by_block[parse_yard(stack.key + "-01").block].append(len(stack.boxes))
                for block in instance.port.yard.blocks:
                    heights = by_block[block.id]
                    limit = effective_yard_tiers(instance.port, block)
                    blocks.append({"id": block.id, "bays": block.n_bays, "rows": block.n_rows,
                        "yard_limit": limit, "nonempty_stacks": len(heights),
                        "height_histogram": dict(sorted(Counter(heights).items())),
                        "max_height": max(heights, default=0),
                        "at_limit_stacks": sum(h == limit for h in heights),
                        "over_limit_stacks": sum(h > limit for h in heights)})
                present = {cid for stack in snapshot.yard.stacks for cid in stack.boxes}
                required = {b.id for b in calls[0].instance.containers if b.id in instance.load_list and not b.direct_delivery}
                arm = {"requested_share": share, "effective_share": effective_share,
                    "paired_input_sha256": input_sha,
                    "yard_time_s": yard_s, "yard_cpu_s": time.process_time() - arm_cpu_start,
                    "metrics": metrics, "blocks": blocks,
                    "simulator_version": result.simulator_version,
                    "congestion_mixing": result.provenance.congestion_mixing,
                    "complete_yard_admission": required <= present,
                    "missing_required_yard_exports": len(required - present),
                    "external_queue": len(result.external_queue), "missed_cutoffs": len(result.missed_cutoffs),
                    "queued_gate_events": result.queued_gate_events,
                    "unserved_loads": len(result.unserved_loads),
                    "unserved_discharges": len(result.unserved_discharges),
                    "reused_identical_effective_policy": False}
                row["arms"][name] = arm
                arm_cache[share] = arm
            row["status"] = "measured"
    except Exception as error:
        row["status"] = "producer_or_measurement_refusal"
        row["error"] = f"{type(error).__name__}: {error}"
    after = source_manifest()
    row.update(source_after=after["sha256"], source_changed_during_case=before["sha256"] != after["sha256"],
               elapsed_s=time.perf_counter() - started,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**30 if sys.platform == "darwin" else 2**20))
    return {"row": row, "manifests": [before, after]}


def execute(spec, timeout_s):
    token = f'{spec["ship"]}-{spec["rung"]}-{spec["port"]}-{spec["noise"]}-{spec["seed"]}'
    scratch = HERE / "shards"
    scratch.mkdir(exist_ok=True)
    job, output = scratch / (token + ".job.json"), scratch / (token + ".json")
    job.write_text(canonical(spec))
    start = time.perf_counter()
    with (scratch / (token + ".log")).open("w") as log:
        environment = {**os.environ, **{name: "1" for name in (
            "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")}}
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.setdefault("STOWAGE_VESSEL_CACHE_DIR", str(ROOT / "experiments/cache/vessels"))
        proc = subprocess.Popen([sys.executable, str(Path(__file__)), "--worker", str(job), "--output", str(output)],
                                cwd=ROOT, stdout=log, stderr=log, env=environment)
        (scratch / (token + ".pid")).write_text(str(proc.pid))
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()  # only the exact subprocess this function started
            proc.wait()
            return {"row": {"spec": spec, "status": "timeout", "elapsed_s": time.perf_counter() - start,
                            "pid_started_by_this_script": proc.pid}, "manifests": []}
    if proc.returncode or not output.exists():
        return {"row": {"spec": spec, "status": "worker_error", "exit_code": proc.returncode,
                        "elapsed_s": time.perf_counter() - start,
                        "log": str(log.name)}, "manifests": []}
    payload = json.loads(output.read_text())
    payload["row"]["process_wall_s"] = time.perf_counter() - start
    return payload


def interval(values):
    """95% t interval across independently seeded YARDS, not correlated boxes."""
    if not values:
        return None
    mean = statistics.mean(values)
    if len(values) < 2:
        return {"n": len(values), "mean": mean, "ci95": None, "half_width": None, "within_5pp": False}
    from scipy.stats import t
    width = float(t.ppf(.975, len(values) - 1)) * statistics.stdev(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": mean, "ci95": [max(0., mean - width), min(1., mean + width)],
            "half_width": width, "within_5pp": 0 < statistics.stdev(values) and width <= .05}


def report(rows):
    grouped = defaultdict(list)
    for row in rows:
        s = row["spec"]
        grouped[s["ship"], s["rung"], s["port"], s["noise"]].append(row)
    config = json.loads((HERE / "run.json").read_text()) if (HERE / "run.json").exists() else {"draws_per_cell": 8}
    expected_draws = config["draws_per_cell"]
    for ship in SHIPS:
        for rung in RUNG:
            for port in PORTS:
                for noise in NOISES:
                    grouped.setdefault((ship, rung, port, noise), [])
    lines = ["# D72 production-producer measurement", "",
        "Real `generate_simulated_instance` workloads/layouts, captured at its simulator boundary; no planner. "
        "These are first pre-yard-screen-passing candidates, **not emitted or background/C1/C2-qualified instances**. "
        "No producer screen is weakened: generation stops before those later gates. "
        "Medium uses the real decision region; full uses the real full ship. All original cargo/geometry restrictions remain.", "",
        "Paired arms share identical calls, gates, simulator seed, noise and A8. Only mix share changes: "
        "off=0; shipped default=.25 if noise>0 (effective0 at noise0); sensitivity=old share=noise. "
        "Independent producer calls would be unpaired because share is part of the producer random identity. "
        "The union of campaign and split noise levels is 0/.2/.5/.8.", "",
            "The producer uses synthetic demand-sized yards and later-call empty dry20 exports as foreign boxes, "
        "not import congestion. Per-block histograms, limits, requested/present export denominators, admissions, "
        "time per simulated yard, source manifests and refusals are retained in results.jsonl. "
        "`yard_rehandle_bound` is the shared unavoidable-rehandle bound (foreign blockers that must move).", "",
        "The snapshot-only retrieval hook intentionally defers every target load after recording the yard. "
        "Its unserved-load counter therefore is **not** a yard-admission failure; missing-required-yard exports, "
        "external queue and complete-yard-admission are the relevant admission measures. "
        "Source snapshots, if used, are exact copies of shipped producer/model/parameter files, not altered code. "
        "They isolate this diagnostic from concurrent edits; compare their hashes with the yield-matrix epoch before reuse.", "",
        "Uncertainty is a descriptive 95% Student-t interval across independent seeded **yard** shares, "
        "not a binomial interval treating correlated boxes as independent. Fixed draws were chosen after a timing pilot; "
        "no adaptive precision stopping. Few draws and generation refusals prevent a blanket ±5pp guarantee. "
        "Means use every measured candidate, including partial admissions; complete-admission counts are shown. "
        "Seed counts/CI do not establish qualification or emitted-instance yield. Full1700/small_feeder is "
        "structurally excluded by the v3 campaign; actual producer attempts/refusals are retained here too.", "",
        "| Ship | Rung | Port | Noise | Draws measured/requested | Complete off/default/old | Blocked % off / default / old [default 95% CI] | Yard LB off/default/old | Mean height off/default/old | Max height off/default/old | Seconds/yard off/default/old | ±5pp default |",
        "|---|---|---|---:|---:|---|---|---|---|---|---|---|"]
    summary = []
    height_lines = ["", "## Stack height distributions versus A8", "",
        "Histograms aggregate nonempty physical stacks over measured yards in each cell. "
        "Every raw block identity, geometry, limit and histogram is retained per draw in results.jsonl. "
        "Different block limits stay separate. This is a stack distribution, not TEU occupancy.", "",
        "| Ship | Rung | Port | Noise | Arm | A8 limit | Height:stack-count | At limit | Over limit |",
        "|---|---|---|---:|---|---:|---|---:|---:|"]
    for key, group in sorted(grouped.items()):
        measured = [r for r in group if r["status"] == "measured"]
        stats = {name: interval([r["arms"][name]["metrics"]["foreign_blocked_export_share"] for r in measured])
                 for name in ("off", "default", "old_n_coupled")}
        entry = {"cell": key, "requested": expected_draws, "completed": len(group),
                 "measured": len(measured), "shares": stats,
                 "refusals": dict(Counter(r["status"] for r in group if r["status"] != "measured"))}
        summary.append(entry)
        for arm_name in stats:
            by_limit = defaultdict(Counter)
            for row in measured:
                for block in row["arms"][arm_name]["blocks"]:
                    by_limit[block["yard_limit"]].update({int(h): n for h, n in block["height_histogram"].items()})
            for limit, hist in sorted(by_limit.items()):
                height_lines.append(f'| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {arm_name} | {limit} | '
                    f'{", ".join(f"{h}:{n}" for h,n in sorted(hist.items()))} | {hist[limit]} | {sum(n for h,n in hist.items() if h>limit)} |')
        def triple(field):
            return "/".join(f'{statistics.mean(r["arms"][a]["metrics"][field] for r in measured):.2f}'
                            for a in stats) if measured else "—"
        shares = " / ".join(f'{100 * stats[a]["mean"]:.1f}' for a in stats) if measured else "—"
        ci = stats["default"]["ci95"] if measured else None
        shares += f' [{100*ci[0]:.1f},{100*ci[1]:.1f}]' if ci is not None else " [CI unavailable]"
        complete = "/".join(str(sum(r["arms"][a]["complete_yard_admission"] for r in measured)) for a in stats)
        seconds = "/".join(f'{statistics.mean(r["arms"][a]["yard_time_s"] for r in measured):.3f}' for a in stats) if measured else "—"
        maxima = "/".join(str(max(r["arms"][a]["metrics"]["stack_height_max"] for r in measured)) for a in stats) if measured else "—"
        enough = bool(measured and stats["default"]["within_5pp"])
        lines.append(f'| {key[0]} | {key[1]} | {key[2]} | {key[3]} | {len(measured)}/{expected_draws} | {complete} | {shares} | '
                     f'{triple("yard_rehandle_bound")} | {triple("stack_height_mean_nonempty")} | {maxima} | {seconds} | {"yes*" if enough else "NO"} |')
    changes = sum(r.get("source_changed_during_case", False) for r in rows)
    versions = sorted({r["source_before"] for r in rows if "source_before" in r})
    violations = sum(b["over_limit_stacks"] for r in rows for a in r.get("arms", {}).values() for b in a["blocks"])
    lines += ["", f"Measured {sum(r['status']=='measured' for r in rows)}/{len(rows)} candidates; "
              f"{len(versions)} source-start epochs; {changes} cases changed source while running; "
              f"{violations} per-block A8 over-limit observations. Raw refusals stay in the denominator.", "",
              f"Expected grid: {len(SHIPS)*len(RUNG)*len(PORTS)*len(NOISES)*expected_draws} independent seed specifications; "
              f"{len(rows)} completed records. Missing cells remain visible above. Time/yard is observed "
              "wall time under shared-machine contention; per-arm CPU time and starting load are in raw records, "
              "so these are not isolated-machine final speed claims.", "",
              "*A narrow sample t interval is descriptive, especially for all-zero cells; it is not a distribution-free "
              "precision guarantee; zero-variance samples are marked NO. Source/parameter drift requires checking the content-addressed manifests before regeneration. "
              "This is a D72 yard-policy diagnostic, not an accepted-production yield matrix.", ""]
    lines += height_lines
    frozen = HERE / "source-snapshot.json"
    if frozen.exists():
        epoch = json.loads(frozen.read_text())
        changed = []
        for relative, digest in epoch["files"].items():
            path = ROOT / relative
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "MISSING"
            if current != digest:
                changed.append({"path": relative, "measured_source_sha256": digest, "current_workspace_sha256": current})
        (HERE / "source-drift.json").write_text(json.dumps(changed, indent=2) + "\n")
        lines += ["", "## Source epoch and reproduction", "",
            "`source-snapshot.json` binds the byte-for-byte source/input copy. Each raw row points to a "
            "content-addressed before/after manifest in `manifests/` (including the exact experiment script). "
            "The copy is not a new model: no code or parameter is patched. "
            "The ordinary content-addressed, model-validated vessel cache remains enabled. "
            "Main-tree edits after this copy do not change these results.", "",
            f"At report time, {len(changed)} frozen source/input files differ from the active workspace; "
            "`source-drift.json` contains both hashes. Producer/admission/arrival-ballast changes can change "
            "the cohort, so compare this inventory before deciding whether the yield matrix must rerun the measurement.", "",
            "Reproduce/resume the fixed experiment with the recorded snapshot and arguments:", "",
            "```sh", "UV_CACHE_DIR=$PWD/.uv-cache OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \\",
            "D72_SOURCE_ROOT=$PWD/experiments/yard-sim/d72-production/source-snapshot \\",
            "uv run python experiments/yard-sim/d72-production/run_measurement.py --draws 8 --workers 2 --max-attempts 8 --timeout 600", "```", ""]
    (HERE / "report.md").write_text("\n".join(lines))
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--draws", type=int, default=8)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--ships", nargs="+", choices=SHIPS, default=list(SHIPS))
    parser.add_argument("--rungs", nargs="+", choices=RUNG, default=list(RUNG))
    parser.add_argument("--ports", nargs="+", choices=PORTS, default=list(PORTS))
    parser.add_argument("--noises", nargs="+", type=float, default=list(NOISES))
    args = parser.parse_args()
    if args.worker:
        args.output.write_text(json.dumps(worker(json.loads(args.worker.read_text())), indent=2) + "\n")
        return
    (HERE / "driver.pid").write_text(str(os.getpid()))
    result_path = HERE / ("pilot.jsonl" if args.pilot else "results.jsonl")
    rows = [json.loads(line) for line in result_path.read_text().splitlines()] if result_path.exists() else []
    if args.report_only:
        report(rows)
        return
    done = {canonical(r["spec"]) for r in rows}
    specs = [{"ship": ship, "rung": rung, "port": port, "noise": noise,
              "seed": 720000 + draw, "max_attempts": args.max_attempts}
             for draw in range(args.draws) for ship in args.ships for rung in args.rungs
             for port in args.ports for noise in args.noises]
    if args.pilot:
        specs = [{"ship": ship, "rung": "full", "port": "laem_chabang_like",
                  "noise": .5, "seed": 720000, "max_attempts": args.max_attempts} for ship in args.ships]
    manifest_dir = HERE / "manifests"
    manifest_dir.mkdir(exist_ok=True)
    batch_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor, result_path.open("a") as output:
        futures = [executor.submit(execute, spec, args.timeout) for spec in specs if canonical(spec) not in done]
        for future in as_completed(futures):
            payload = future.result()
            for manifest in payload["manifests"]:
                path = manifest_dir / (manifest["sha256"] + ".json")
                if not path.exists():
                    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            row = payload["row"]
            output.write(canonical(row) + "\n")
            output.flush()
            rows.append(row)
            print(canonical({"completed": len(rows), "status": row["status"], "spec": row["spec"],
                             "elapsed_s": row["elapsed_s"]}), flush=True)
    (HERE / ("pilot-run.json" if args.pilot else "run.json")).write_text(json.dumps({
        "wall_s": time.perf_counter() - batch_start, "workers": args.workers,
        "draws_per_cell": args.draws, "max_attempts": args.max_attempts,
        "timeout_s": args.timeout, "cases": len(rows)}, indent=2) + "\n")
    if not args.pilot:
        report(rows)


if __name__ == "__main__":
    main()
