"""Collect the dry-run records into dryrun.md / dryrun.json (one row per instance and backend)."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run_c1 import planner_metrics, summarize  # noqa: E402

rows, out = [], {}
for run in sorted((HERE / "runs").glob("dry-*")):
    if not run.is_dir():
        continue
    recs = [json.loads(p.read_text()) for p in sorted((run / "instances").glob("*.json"))]
    cfg = json.loads((run / "config.json").read_text())
    out[run.name] = {"summary": summarize(recs), "rule_catalogue": cfg["versions"].get("rule_catalogue"),
                     "mask_backend": cfg["planner_parameters"].get("mask_backend")}
    for r in recs:
        m = planner_metrics(r["metrics"])
        steps = m.get("gen_steps") or 0
        rows.append("| {} | {} | {}/{}/{} | {} | {} / {} | {:.1f} | {:.2f} | {:.1f} | {:.2f} | {:.1%} | {} | {:.0f} | {:.0f} | {:.1f} |".format(
            r["instance_id"].split(".", 1)[1], cfg["planner_parameters"].get("mask_backend"),
            m.get("n_complete"), m.get("n_dead_end"), m.get("n_checker_feasible"), r["report_passed"],
            r["objective"], r["exact_objective"], m.get("gen_tok_s", 0), m.get("gen_prefill_s", 0),
            m.get("gen_wall_s", 0), 1e3 * m.get("gen_host_mask_s", 0) / max(1, steps),
            m.get("gen_host_wait_s", 0) / m["gen_wall_s"] if m.get("gen_wall_s") else 0,
            m.get("pool_parent_wait_s"), m.get("candidate_selection_s", 0), r["pipeline_s"], r["peak_memory_gb"]))
head = ("| instance | mask backend | samples complete/dead/checker-feasible | final checker pass | objective / exact | "
        "decode tok/s | prefill s | decode s | mask host ms/step | GPU stall share | pool wait s | selection s | "
        "pipeline s | peak GB |\n|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
(HERE / "dryrun.md").write_text(head + "\n" + "\n".join(rows) + "\n")
(HERE / "dryrun.json").write_text(json.dumps(out, indent=1, default=str))
print(head); print("\n".join(rows)); print(json.dumps(out, indent=1, default=str)[:2500])
