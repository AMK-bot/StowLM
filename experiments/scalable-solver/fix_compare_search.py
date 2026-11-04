"""One immutable small witness: production default versus bounded narrow search."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import time

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"

HERE = Path(__file__).resolve().parent
NARROW = dict(beam_width=1, early_beam_width=1, branch_factor=2,
              candidate_boxes=8, candidate_slots=16, max_neighborhoods=2,
              exact_window_time_limit_s=2, exact_whole_plan_time_limit_s=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "fix-search-comparison")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("search_comparison_g1", HERE / "g1_pilot_configured.py")
    g1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g1)
    helper = g1.helpers()
    from stowage.generate.instances_v3 import physics_metadata
    from stowage.io import load_instance

    baseline = HERE / "fix-epoch-shard-small"
    input_path = baseline / "inputs/random_yard-small-2126202700.json"
    old = json.loads((baseline / "results/random_yard-small-2126202700.json").read_text())
    admission = json.loads((baseline / "admission.json").read_text())
    input_sha = g1.sha(input_path)
    if input_sha != old["generation"]["input_sha256"]:
        raise ValueError("immutable witness disagrees with original generation SHA")

    def snapshot():
        return dict(source_sha256=helper.source_hash(), physics=physics_metadata(c2_screened=True),
                    input_sha256=g1.sha(input_path), gate_driver_sha256=g1.sha(HERE / "g1_pilot_configured.py"))

    start = time.monotonic()
    pinned = snapshot()
    if pinned["input_sha256"] != input_sha:
        raise ValueError("input changed while taking initial snapshot")
    identity = dict(**pinned, driver_sha256=g1.sha(Path(__file__)), input=str(input_path),
                    search_limit_s=15, overhead_reserve_s=20, ballast_limit_s=5,
                    solver_seed=0, variants=dict(default={}, narrow=NARROW))
    identity_path = args.output / "admission.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise ValueError("comparison source/config changed: choose a new output directory")
    g1.save(identity_path, identity)
    reusable = (admission["source_sha256"] == pinned["source_sha256"] and
                admission["physics"] == pinned["physics"] and
                admission["gate_driver_sha256"] == pinned["gate_driver_sha256"] and
                admission["search_limit_s"] == 15 and admission["overhead_limit_s"] == 20 and
                admission["variants"]["default"] == {} and
                not old["variants"]["default"].get("source_transition", False))
    rows = []
    for variant, config in identity["variants"].items():
        path = args.output / f"{variant}.json"
        if path.exists():
            rows.append(json.loads(path.read_text()))
            continue
        before = snapshot()
        if before != pinned:
            raise ValueError("source/input transition before variant: retain completed checkpoints")
        row = dict(variant=variant, config=config, before=before,
                   started_utc=datetime.now(timezone.utc).isoformat(), reused=False)
        if variant == "default" and reusable:
            row.update(reused=True, reused_from=str(baseline / "results/random_yard-small-2126202700.json"),
                       reuse_note="Same source/physics/input/gate driver and budgets; original driver checks epoch after every variant")
            row["measurement"] = old["variants"]["default"]
        else:
            instance = load_instance(input_path)
            measure_args = argparse.Namespace(solver_seed=0, solver_config=config,
                                             overhead_reserve_s=20, ballast_limit_s=5)
            row["measurement"] = g1.measure(instance, measure_args, 15, helper,
                args.output / "plans" / f"{variant}.approved-plan.json")
        row["after"] = snapshot()
        row["source_changed_during_run"] = row["before"] != row["after"]
        if row["source_changed_during_run"]:
            g1.quarantine_source_transition(row)
        m = row["measurement"]
        metrics = m.get("solver_metrics", {})
        row["counts"] = dict(candidate=int(m.get("plan_returned", False)),
            complete=int(m.get("completed", False)), checker=int(m.get("checker_passed", False)),
            mask=int(m.get("replay", {}).get("status") == "passed"),
            ballast_status=m.get("ballast_status", "not_reached"),
            approved=int(m.get("approved", False)), partial_depth=metrics.get("partial_depth"),
            deepest_depth=metrics.get("deepest_depth"), candidate_probes=metrics.get("candidate_probes"),
            legal_candidates=metrics.get("legal_candidates"),
            completion_lookahead_calls=metrics.get("completion_lookahead_calls"))
        g1.save(path, row)
        rows.append(row)
        print(json.dumps(dict(variant=variant, reused=row["reused"], status=m["status"],
                              counts=row["counts"], timings_s=m.get("timings_s"),
                              source_transition=row["source_changed_during_run"])), flush=True)
    g1.save(args.output / "summary.json", dict(identity=identity,
        matched_epoch=all(r["before"] == pinned and r["after"] == pinned for r in rows),
        variants={r["variant"]: dict(counts=r["counts"], timings_s=r["measurement"].get("timings_s"),
                                    reused=r["reused"], status=r["measurement"]["status"],
                                    source_transition=r["source_changed_during_run"]) for r in rows},
        wall_s=time.monotonic()-start))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
