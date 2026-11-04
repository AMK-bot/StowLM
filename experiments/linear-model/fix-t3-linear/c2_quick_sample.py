"""Bounded post-promotion C1→C2 audit; no canonical instances are published.

Run only after epoch promotion and whole-fleet cache warm-up have succeeded.
Require the promoted bank's SHA256 explicitly. Twenty one-attempt requests
are fixed in advance; singleton DG diagnostics are not additional draws.
C2 is the existing necessary relaxation screen, not full-checker approval.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BANK = REPO / "src/stowage/linear/margin_policy_data.json"
ARCHIVES = {
    "tiny-six": "v3-tiny-c2-rejected.json",
    "tiny-five": "v3-tiny-five-c2-rejected.json",
    "simulated-small": "v3-sim-small-c2-rejected.json",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, record):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2,
                                   allow_nan=False, default=str) + "\n")
    temporary.replace(path)


def snapshot():
    from stowage.generate.instances_v3 import physics_metadata
    return {"bank_sha256": sha(BANK), "physics": physics_metadata(c2_screened=True)}


def condition_summary(instance):
    from stowage.vessel.fleet_conditions import slice_condition
    audit = slice_condition(instance.vessel)
    return {
        "recorded_condition": audit,
        "cells": len(instance.vessel.cells),
        "placements": len(instance.onboard.placements),
        "targets": len(instance.load_list),
        "fixed_load_count": len(instance.vessel.fixed_loads),
        "fixed_mass_kg": sum(load.mass_kg for load in instance.vessel.fixed_loads),
    }


@contextmanager
def necessary_direction():
    """Scoped D49 adapter for the unmigrated feasibility caller.

    GlobalScreen.from_instance imports this function from the package on each
    call. The explicit choice belongs to this experimental C2 caller only;
    production sources and plan-constructing LPs are untouched.
    """
    import stowage.linear as linear
    original = linear.linear_constraints

    def constraints(model, *, margin_scale=1., direction="necessary"):
        if direction != "necessary":
            raise ValueError("C2 experiment requires explicit necessary direction")
        return original(model, direction="necessary", margin_scale=margin_scale)

    linear.linear_constraints = constraints
    try:
        yield
    finally:
        linear.linear_constraints = original


def check(instance, *, output, label, diagnostic_force_c2=False):
    """Canonical screen semantics; archive-only diagnostics may also collect C2.

    Forced archive evaluation never removes C1 failures or supplies new policy
    or ratings. Its LP is numerical evidence only, never screen acceptance.
    """
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import save_instance

    row = {"label": label, "singleton_dg_probe": len(instance.load_list) == 1,
           "condition": condition_summary(instance), "c1_reached": False,
           "c2_screened": False, "screen_accepted": False}
    started = time.perf_counter()
    c1 = make_feasibility_engine(instance, get_action_space("boxslot"),
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
    checks = c1.completion_check()
    failed = tuple(sorted(key for key, ok in checks.items()
                          if key.startswith("c1") and not ok))
    row.update(c1_reached=True, c1_checks=checks, c1_diagnostics=dict(c1.diagnostics),
               c1_failures=list(failed), c2_status="not reached")
    row["diagnostic_forced_c2"] = bool(diagnostic_force_c2 and failed)
    if (not failed or diagnostic_force_c2) and not row["singleton_dg_probe"]:
        # A diagnostic snapshot, including rejected candidates, never a
        # canonical instance or a checker-approved loading plan.
        save_instance(instance, output / f"{label}-diagnostic-instance.json")
        try:
            engine = make_feasibility_engine(instance, get_action_space("boxslot"),
                config=FeasibilityConfig(lookahead=0, extra={"global_screen": 1}))
            row["c2_diagnostics"] = dict(engine.diagnostics)
            if engine.diagnostics.get("global_screen") != "on":
                failed = tuple(dict.fromkeys((*failed, "checker.missing_vessel_data")))
                row["c2_status"] = "unavailable"
            else:
                result = engine.completion_check()
                row.update(c2_screened=True, c2_checks=result,
                    c2_diagnostics=dict(engine.diagnostics),
                    c2_status="passed" if result.get("c2") is True else "failed")
                c2_failed = () if result.get("c2") is True else ("c2",)
                failed = tuple(dict.fromkeys((*failed, *c2_failed)))
                g = engine._global
                fixed, free, remaining = engine._global_state(engine._cur.ex)
                row.update(free_cells=int(free.sum()), fixed_cell_mass_kg=float(fixed.sum()),
                           remaining_mass_kg=float(remaining))
                np.savez(output / f"{label}-lp.npz", A=g.A, At=g.At, const=g.const,
                    lo=g.lo, hi=g.hi, tank_lo=g.tank_lo, tank_hi=g.tank_hi,
                    u0=g.u0, names=np.array(g.names), remaining_mass=remaining)
        except Exception as exc:
            failed = tuple(dict.fromkeys((*failed, "checker.missing_vessel_data")))
            row.update(c2_status="exception", c2_error=f"{type(exc).__name__}: {exc}")
    row.update(screen_failures=list(failed), screen_accepted=not failed,
               elapsed_s=time.perf_counter()-started)
    return failed, row


@contextmanager
def recording_screen(output, rows, request_label):
    from stowage.generate import random_yard
    original = random_yard.completion_failures

    def completion(instance):
        label = f"{request_label}-completion-{len(rows):02d}"
        failed, row = check(instance, output=output, label=label)
        rows.append(row)
        return failed

    random_yard.completion_failures = completion
    try:
        yield
    finally:
        random_yard.completion_failures = original


def run_request(size, seed, output):
    from stowage.generate.instances import GenerationReport
    from stowage.generate.instances_v2_random import generate_random_v2

    label = f"{size}-{seed}"
    report = GenerationReport()
    rows = []
    started = time.perf_counter()
    record = {"label": label, "size": size, "seed": seed,
              "generator": "random_yard", "noise": 0., "max_attempts": 1}
    try:
        with recording_screen(output, rows, label):
            instance = generate_random_v2(size, seed=seed, noise=0.,
                vessel_name="feeder_500", port_name="laem_chabang_like",
                n_boxes=None, dense_20ft=False, tiny_dg_enabled=True,
                report=report, max_attempts=1)
        record.update(status="screen accepted", instance_id=instance.id)
    except Exception as exc:
        record.update(status="rejected" if type(exc) is ValueError else "error",
                      error=f"{type(exc).__name__}: {exc}")
    record.update(report=asdict(report), completion_calls=rows,
        c1_reached=any(r["c1_reached"] and not r["singleton_dg_probe"] for r in rows),
        c2_screened=any(r["c2_screened"] and not r["singleton_dg_probe"] for r in rows),
        accepted=report.accepted,
        early_rejection=not any(not r["singleton_dg_probe"] for r in rows),
        elapsed_s=time.perf_counter()-started)
    return record


def summarize(records):
    return {size: {
        "requests": len(group),
        "candidate_draws": sum(r["report"]["attempts"] for r in group),
        "initialization_rejections": sum(r["report"]["attempts"] == 0 for r in group),
        "early_rejections": sum(r["early_rejection"] for r in group),
        "c1_reached": sum(r["c1_reached"] for r in group),
        "c2_screened": sum(r["c2_screened"] for r in group),
        "accepted": sum(r["accepted"] for r in group),
        "acceptance_per_request": sum(r["accepted"] for r in group)/len(group),
        "acceptance_per_c2_screened": (sum(r["accepted"] for r in group)
            /sum(r["c2_screened"] for r in group)) if any(r["c2_screened"] for r in group) else None,
    } for size in ("tiny", "small")
        if (group := [r for r in records if r["size"] == size])}


def replay_archive(output):
    from stowage.io import load_instance
    results = {}
    for label, filename in ARCHIVES.items():
        path = REPO / "experiments/instances" / filename
        row = {"input_path": str(path.relative_to(REPO)), "input_sha256": sha(path),
               "historical_background": True}
        try:
            instance = load_instance(path)
            _, checked = check(instance, output=output, label=f"archive-{label}",
                               diagnostic_force_c2=True)
            row.update(checked)
        except Exception as exc:
            row.update(error=f"{type(exc).__name__}: {exc}", status="replay failed")
        if sha(path) != row["input_sha256"]:
            raise ValueError("archived instance changed during replay")
        results[label] = row
        write(output / f"archive-{label}.json", row)
    # Compare the historical contradictory rows without regenerating them.
    script = HERE / "tiny-investigation/saved_lp.py"
    spec = importlib.util.spec_from_file_location("tiny_saved_lp", script)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    results["historical_lp"] = helper.replay()
    current = output / "archive-tiny-six-lp.npz"
    if current.exists():
        try:
            results["current_model_historical_tiny_lp"] = helper.replay(current)
        except Exception as exc:
            results["current_model_historical_tiny_lp"] = {"error": str(exc)}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank-sha256", required=True,
                        help="SHA256 of the independently promoted bank; no implicit admission")
    parser.add_argument("--output", type=Path, default=HERE / "post-promotion-c2")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        parser.error("output must be a dedicated directory under fix-t3-linear")
    before = snapshot()
    if before["bank_sha256"] != args.bank_sha256:
        parser.error("requested promoted bank SHA256 does not match current bank")
    output.mkdir(parents=True, exist_ok=True)
    admission = {"inputs": before, "request_count": 20, "max_attempts_per_request": 1,
                 "direction": "necessary", "caller": "experiment-local D49 adapter",
                 "warning": "C1/C2 screens only; no full-checker guarantee", "gpu": False}
    admission_path = output / "admission.json"
    if admission_path.exists():
        if json.loads(admission_path.read_text()) != admission:
            parser.error("output contains a different admitted epoch; choose a fresh directory")
    else:
        write(admission_path, admission)
    records = []
    for size, base_seed in (("tiny", 100000000), ("small", 100001000)):
        for offset in range(10):
            path = output / f"request-{size}-{base_seed+offset}.json"
            if snapshot() != before:
                raise ValueError("qualified physics/bank changed during sample; stop, retain shards")
            if path.exists():
                record = json.loads(path.read_text())
            else:
                with necessary_direction():
                    record = run_request(size, base_seed+offset, output)
            if not path.exists():
                if snapshot() != before:
                    raise ValueError("qualified physics/bank changed during request; discard incomplete shard")
                write(path, record)
            records.append(record)
            write(output / "summary.json", {"inputs": before, "rates": summarize(records),
                                            "completed_requests": len(records)})
            print(json.dumps({"request": record["label"], "status": record["status"],
                              "seconds": record["elapsed_s"]}), flush=True)
    with necessary_direction():
        archives = replay_archive(output)
    if snapshot() != before:
        raise ValueError("qualified physics/bank changed during archive replay")
    write(output / "summary.json", {"inputs": before, "rates": summarize(records),
        "completed_requests": len(records), "historical_replays": archives,
        "warning": "Screen acceptance is not full-checker approval; no new loading plans certified"})


if __name__ == "__main__":
    main()
