"""CPU-only, resumable 15 s fixed-stow polish on audit30/reserve25 validation.

Uses the same improve_checked admission and independent final checker as
../eval91/polish91.py. Missing saved plans are explicit omissions: decoding or
recreating an incumbent would constitute a different experiment.

.venv/bin/python experiments/mvp/sft/final-validation/polish_val.py --inventory-only
.venv/bin/python experiments/mvp/sft/final-validation/polish_val.py --workers 6
.venv/bin/python experiments/mvp/sft/final-validation/polish_val.py --arms 8.3
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SCHEMA = 1
SETS = {"audit30": 30, "reserve25": 25}
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
AUDIT = ROOT / "experiments/mvp/exit/deployment-d137/parent-audit-8.2"
TWOSTEP_AUDIT = ROOT / "experiments/mvp/sft/val-audit30-twostep/120"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def validation_path(value):
    """Reject every input outside the explicitly allowed small val subtree."""
    path = Path(value)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    path.relative_to((ROOT / "experiments/mvp/corpus/val/small").resolve())
    if path.suffix != ".json" or path.name.endswith(".audit.json"):
        raise ValueError(f"not a small validation instance: {path}")
    return path


def source_rows(arm, set_name):
    """Only read the requested validation arm/set; never inspect tiny or test."""
    folder = HERE / arm / set_name
    if arm == "8.2" and set_name == "audit30" and not (folder / "rows.json").exists():
        path = AUDIT / "audit-baseline/summary.json"
    elif arm == "twostep" and not (folder / "rows.json").exists():
        folder = TWOSTEP_AUDIT if set_name == "audit30" else folder
        pairs = [(p, read_json(p)) for p in sorted(folder.glob("*.json"))]
        pairs = [(p, r) for p, r in pairs if isinstance(r, dict) and "instance_id" in r and "passed" in r]
        return pairs, [folder]
    else:
        path = folder / "rows.json"
    if not path.exists():
        return [], [folder]
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError(f"expected a row list: {path}")
    return [(path, r) for r in rows], ([AUDIT] if path.is_relative_to(AUDIT) else [folder])


def plan_payload(value):
    """Accept serialized PlannerResult or MovePlan, including common wrappers."""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("plan"), dict) and isinstance(value["plan"].get("moves"), list):
        return value if "planner" in value else {"plan": value["plan"]}
    if isinstance(value.get("moves"), list) and "instance_id" in value:
        return {"plan": value}
    for key in ("result", "planner_result", "chosen_result", "finalized_result"):
        found = plan_payload(value.get(key))
        if found:
            return found
    return None


def allowed_plan_path(value, base, roots):
    path = Path(value)
    candidates = [path] if path.is_absolute() else [base / path, ROOT / path]
    for candidate in candidates:
        candidate = candidate.resolve()
        if any(candidate.is_relative_to(r.resolve()) for r in roots) and candidate.is_file():
            return candidate
    return None


def locate_plan(row, source, roots):
    """Find stored incumbents only; never infer a finalized plan from tokens."""
    payload = plan_payload(row)
    if payload:
        return payload, source, "embedded"
    for key in ("plan_path", "result_path", "chosen_plan_path", "finalized_plan_path"):
        if row.get(key):
            path = allowed_plan_path(row[key], source.parent, roots)
            if path is not None:
                payload = plan_payload(read_json(path))
                if payload:
                    return payload, path, key
    iid = row["instance_id"]
    for root in roots:
        for name in (f"{iid}.plan.json", f"{iid}.result.json", f"plans/{iid}.json", f"results/{iid}.json"):
            path = root / name
            if path.is_file():
                payload = plan_payload(read_json(path))
                if payload:
                    return payload, path, "sidecar"
    return None, None, None


def inventory(arms, sets, seconds):
    jobs, groups = [], []
    for arm in arms:
        for set_name in sets:
            pairs, roots = source_rows(arm, set_name)
            split = ROOT / "experiments/mvp/valsplit" / ("audit_small30.txt" if set_name == "audit30" else "reserve_small25.txt")
            allowed_inputs = {validation_path(p) for p in split.read_text().split()}
            ids = [r["instance_id"] for _, r in pairs]
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate instance IDs in {arm}/{set_name}")
            group_jobs = []
            for source, row in pairs:
                # Even failed rows must be validation inputs before inclusion.
                instance = validation_path(row.get("instance_path") or row.get("input"))
                if instance not in allowed_inputs:
                    raise ValueError(f"input outside frozen {set_name} split: {instance}")
                iid = row["instance_id"]
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", iid):
                    raise ValueError(f"unsafe instance ID: {iid!r}")
                if row.get("passed") is not True:
                    continue
                payload, plan_source, kind = locate_plan(row, source, roots)
                job = dict(arm=arm, set=set_name, instance_id=iid,
                    input=str(instance), input_sha256=sha(instance), source_rows=str(source),
                    source_row_sha256=digest(row),
                    historical_checker_s=(row.get("verification") or {}).get("timings", {}).get("checker_s"),
                    before_moves=row.get("weighted_moves", row.get("weighted_nonproductive")),
                    source_plan=str(plan_source) if plan_source else None,
                    source_plan_sha256=sha(plan_source) if plan_source else None,
                    plan_location=kind, requested_polish_s=seconds,
                    status="ready" if payload else "missing_plan")
                if payload:
                    job["incumbent"] = payload
                job["fingerprint"] = digest({**job, "harness_sha256": sha(__file__)})
                jobs.append(job)
                group_jobs.append(job)
            groups.append(dict(arm=arm, set=set_name, source_rows=sorted({str(p) for p, _ in pairs}),
                expected_maps=SETS[set_name], n_rows=len(pairs), n_passed=len(group_jobs),
                n_ready=sum(j["status"] == "ready" for j in group_jobs),
                n_missing_plan=sum(j["status"] == "missing_plan" for j in group_jobs)))
    return dict(schema_version=SCHEMA, requested_polish_s=seconds, groups=groups, jobs=jobs,
                missing_plans=[{k: v for k, v in j.items() if k != "incumbent"}
                               for j in jobs if j["status"] == "missing_plan"])


def _cpu_environment():
    for key in THREADS:
        os.environ[key] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""


def polish_one(job):
    _cpu_environment()
    # No llm, MLX, model weights, generation or ballast reconstruction imports.
    from stowage.checker import check
    from stowage.io import load_instance
    from stowage.model import PlannerResult
    from stowage.moves import apply_plan
    from stowage.solver.gold_warmstart import improve_checked

    started = time.perf_counter()
    row = {k: v for k, v in job.items() if k != "incumbent"}
    row.update(status="error", after_moves=None, improved=False, polish_s=0.,
               own_check_s=0., candidate_check_s=0., qualification_check_s=0.,
               acceptance_check_s=0., check_s_total=0.)
    try:
        instance_path = validation_path(job["input"])
        if sha(instance_path) != job["input_sha256"]:
            raise ValueError("instance changed after inventory")
        instance = load_instance(instance_path)
        if instance.id != job["instance_id"]:
            raise ValueError("instance ID differs from source row")
        payload = job["incumbent"]
        if "planner" not in payload:
            payload = dict(status="ok", planner=f"validation.{job['arm']}",
                           planner_version="stored", seed=0, **payload)
        own = PlannerResult.model_validate(payload)
        if own.status != "ok" or own.plan is None:
            raise ValueError("stored incumbent is not a complete passing result")
        if own.plan.instance_id != instance.id:
            raise ValueError("stored plan instance ID differs from input")
        t = time.perf_counter()
        own_report = check(instance, own.plan)
        row["own_check_s"] = time.perf_counter() - t
        row["own_report_passed"] = own_report.passed
        if not own_report.passed:
            raise ValueError("stored incumbent failed the current full checker")
        before = own_report.count.weighted_nonproductive
        if before != job["before_moves"]:
            raise ValueError(f"stored plan objective {before} differs from row {job['before_moves']}")
        reference = apply_plan(instance, own.plan)
        if not reference.legal:
            raise ValueError("stored incumbent failed move replay")
        t = time.perf_counter()
        polished = improve_checked(instance, own, time_limit_s=job["requested_polish_s"], seed=0)
        row["polish_s"] = time.perf_counter() - t
        row["metrics"] = polished.metrics
        row["qualification_check_s"] = polished.metrics.get("baseline_check_s", 0.)
        row["acceptance_check_s"] = polished.metrics.get("acceptance_check_s", 0.)
        row["lns_elapsed_s"] = polished.metrics.get("lns_elapsed_s")
        row["lns_search_s"] = polished.metrics.get("lns_search_s")
        row["message"] = polished.message
        t = time.perf_counter()
        report = check(instance, polished.plan) if polished.plan is not None else None
        row["candidate_check_s"] = time.perf_counter() - t
        after = report.count.weighted_nonproductive if report is not None and report.passed else None
        mapping = lambda replay: {p.container_id: p.slot for p in replay.final_state.ship.placements}
        replay = apply_plan(instance, polished.plan) if polished.plan is not None else None
        fixed = replay is not None and replay.legal and mapping(replay) == mapping(reference)
        ballast = polished.plan is not None and polished.plan.ballast == own.plan.ballast
        kept = after is None or after > before or not fixed or not ballast
        chosen = own if kept else polished
        row.update(status="completed", before_moves=before, after_moves=before if kept else after,
            improved=not kept and after < before, kept_incumbent=kept,
            fallback_reason="candidate rejected by independent final gates" if kept else None,
            candidate_report_passed=report.passed if report is not None else False,
            candidate_assignments_preserved=fixed, candidate_ballast_preserved=ballast,
            assignments_preserved=True, ballast_preserved=True, final_report_passed=True,
            result=chosen.model_dump(mode="json"))
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
    row["check_s_total"] = sum(row[k] for k in
        ("own_check_s", "candidate_check_s", "qualification_check_s", "acceptance_check_s"))
    row["total_s"] = time.perf_counter() - started
    return row


def reusable(path, job):
    if not path.exists():
        return False
    try:
        row = read_json(path)
        return row.get("fingerprint") == job["fingerprint"] and row.get("status") in ("completed", "missing_plan")
    except (ValueError, OSError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", choices=("8.2", "twostep", "8.3"), default=["8.2", "twostep"])
    parser.add_argument("--sets", nargs="+", choices=tuple(SETS), default=list(SETS))
    parser.add_argument("--workers", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--seconds", type=float, default=15.)
    parser.add_argument("--output", type=Path, default=HERE / "polish")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and nonnegative")
    _cpu_environment()
    inv = inventory(list(dict.fromkeys(args.arms)), list(dict.fromkeys(args.sets)), args.seconds)
    # Adding 8.3 later preserves the already inventoried 8.2/two-step arms.
    previous = args.output / "inventory.json"
    if previous.exists():
        old = read_json(previous)
        replaced = {(g["arm"], g["set"]) for g in inv["groups"]}
        if old.get("schema_version") == SCHEMA:
            inv["groups"] += [g for g in old["groups"] if (g["arm"], g["set"]) not in replaced]
            inv["jobs"] += [j for j in old["jobs"] if (j["arm"], j["set"]) not in replaced]
            inv["missing_plans"] = [j for j in inv["jobs"] if j["status"] == "missing_plan"]
    write_json(args.output / "inventory.json", inv)
    print(json.dumps([{k: v for k, v in g.items() if k != "source_rows"} for g in inv["groups"]]), flush=True)
    if args.inventory_only:
        return 0
    pending = []
    for job in inv["jobs"]:
        if job["arm"] not in args.arms or job["set"] not in args.sets:
            continue
        target = args.output / job["arm"] / job["set"] / f"{job['instance_id']}.json"
        if reusable(target, job):
            continue
        if job["status"] == "missing_plan":
            write_json(target, {**job, "after_moves": None, "improved": False,
                               "polish_s": 0., "check_s_total": 0.})
        else:
            pending.append((job, target))
    errors = 0
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(polish_one, job): target for job, target in pending}
            for future in as_completed(futures):
                target = futures[future]
                row = future.result()
                write_json(target, row)
                errors += row["status"] == "error"
                print(json.dumps({k: row.get(k) for k in
                    ("arm", "set", "instance_id", "status", "before_moves", "after_moves", "improved", "polish_s", "error")}), flush=True)
    return 2 if errors or inv["missing_plans"] or any(g["n_rows"] != g["expected_maps"] for g in inv["groups"]) else 0


if __name__ == "__main__":
    sys.exit(main())
