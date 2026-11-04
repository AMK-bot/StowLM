# Compare the 6.2 base-Glimmer-under-mask run with solver.scalable on the same val instances (harness records).
# D11 objective = moves.count_moves(...).weighted_nonproductive of the planner's own plan (before repair), so a
# failed ballast/repair does not hide what the planner chose. Usage: uv run python experiments/base-mask/compare.py
import json
import statistics as st
from pathlib import Path

from stowage.io import load_instance
from stowage.model import PlannerResult
from stowage.moves import count_moves

ROOT = Path("experiments/base-mask/runs")
INST = Path("experiments/base-mask/instances-v3-val")


def records(run):
    return {json.loads(p.read_text())["spec_key"]: json.loads(p.read_text()) for p in (ROOT / run / "instances").glob("*.json")}


base, scal = records("base-bo32-T1"), records("scalable")
rows = []
for key in sorted(base):
    b, s = base[key], scal.get(key)
    m = b["result"]["metrics"]
    row = dict(instance=b["instance_id"], rung=b["rung"], boxes=b["instance"]["boxes_to_load"],
               base_status=b["result"]["status"], n_complete=m.get("n_complete"), n_dead_end=m.get("n_dead_end"),
               base_objective=m.get("chosen_objective"), sample_obj_median=(st.median(m["sample_objectives"])
                                                                         if m.get("sample_objectives") else None),
               ballast=m.get("ballast_status"), base_passed=bool(b["shippable"]), repaired=b["result"]["repaired"],
               gen_s=m.get("gen_wall_s"), prefill_s=m.get("gen_prefill_s"), steps=m.get("gen_steps"),
               host_share=m.get("host_share"), tokens_chosen=m.get("tokens_out_chosen"),
               plan_s=b["timing"]["plan_s"], total_s=round(b["timing"]["total_s"], 1),
               lower_bound=b["lower_bound"].get("weighted"))
    if s is not None:
        sr = s["result"]
        row.update(scal_status=sr["status"], scal_passed=bool(s["shippable"]), scal_plan_s=round(s["timing"]["plan_s"], 1))
        if sr.get("metrics", {}).get("objective") is not None:
            row["scal_objective"] = sr["metrics"]["objective"]
        elif sr.get("plan") and sr["status"] in ("ok", "error"):
            inst = load_instance(b["instance"].get("path") or next(INST.rglob(f"*{b['instance_id'].split('.')[-1]}.json")))
            row["scal_objective"] = count_moves(inst, PlannerResult.model_validate(sr).plan).weighted_nonproductive
    rows.append(row)
Path("experiments/base-mask/compare.json").write_text(json.dumps(rows, indent=1))
for r in rows:
    print({k: r.get(k) for k in ("rung", "boxes", "base_status", "n_complete", "n_dead_end", "base_objective",
                                  "sample_obj_median", "scal_status", "scal_objective", "lower_bound", "ballast",
                                  "base_passed", "scal_passed", "gen_s", "steps", "host_share", "total_s")})
