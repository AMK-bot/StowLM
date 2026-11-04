"""Re-fit the 6.2 / C1 GPU budget from the timing pilot (results-medium.json, results-full.json).

Per plan = prefill + actions x (measured lock-step seconds per action at that k) + checker selection.
- Seconds per action: MEASURED, pool of 4 workers, switch on, C2 off, no exact count in the loop (best-of-4/8).
  Tiny and small are not re-measured here: tiny from the 28 Oct trace smoke (0.43 s/action at k=4), small
  interpolated between tiny and medium (ASSUMED).
- Actions per plan: a trained planner loads each box once (tiny 5, small 34, medium 229); full 500-1,000 (order).
  Untuned base Glimmer (6.2) took ~3x the load list on medium (183-200 actions for 60 boxes); 6.2 is reported both
  ways.
- Selection: rank-order early stop, ASSUMED 3 ballast+full checks per instance at 30/60/120 s (small/medium/full)
  plus D64's in-port checker 8/15/25 s (model.py; unmeasured), tiny measured ~17 s/check.
- Per-instance time limit 60 min caps a plan (counted as "mask time limit", not a model failure).
C2 off: the C2 LP screen's cost on the current source is unmeasured (C2 is stale until requalification).

    uv run python experiments/eval-c1/timing-pilot/budget.py > experiments/eval-c1/timing-pilot/budget.md
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIMIT_S = 3600.0


def measured():
    runs = []
    for name in ("results-medium.json", "results-full.json"):
        path = HERE / name
        if path.exists():
            runs += json.loads(path.read_text())["runs"]
    return {(r["rung"], r["k"]): r for r in runs}


def per_plan(rung: str, k: int, actions: float, m: dict) -> float:
    spa = {"tiny": 0.43}
    pre = {"tiny": 0.8, "small": 2.0}
    check = {"tiny": 17.0, "small": 30 + 8, "medium": 60 + 15, "full": 120 + 25}
    if (rung, k) in m:
        spa[rung], pre[rung] = m[(rung, k)]["s_per_action_steady"], m[(rung, k)]["prefill_s"]
    if rung == "small":
        med = m.get(("medium", k), {}).get("s_per_action_steady", 1.0)
        spa["small"] = (0.43 + med) / 2  # ASSUMED interpolation
    total = pre[rung] + actions * spa[rung] + 3 * check[rung]
    return min(total, LIMIT_S)


def table(counts: dict, k: int, m: dict, full_actions: int, base_factor: float = 1.0) -> tuple[list[str], float]:
    actions = {"tiny": 5, "small": 34, "medium": 229, "full": full_actions}
    lines, total = [], 0.0
    for rung, n in counts.items():
        a = actions[rung] * (base_factor if rung != "full" else 1.0)
        t = per_plan(rung, k, a, m)
        total += n * t
        lines.append(f"| {rung} | {n} | {a:.0f} | {t / 60:.1f} | {n * t / 3600:.1f} |")
    return lines, total / 3600


def main():
    m = measured()
    print("Measured (pool of 4 workers, switch on, C2 off, no exact count; base Glimmer; timing inputs = w2-mask-perf"
          " feeder500 fixtures):\n")
    print("| rung | k | stop | actions | lock-step s/action (steady) | mask CPU s/action (all samples) | hidden by pool |"
          " exposed mask s/action | tok/s | prompt tokens | prefill s | peak GB |")
    print("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for (rung, k), r in sorted(m.items()):
        print(f"| {rung} | {k} | {r['stop']} | {r['actions']} | {r['s_per_action_steady']} | "
              f"{r['mask_busy_s_per_action']} | {r['mask_hidden_share']:.0%} | {r['mask_exposed_s_per_action']} | "
              f"{r['tok_s']} | {r['prompt_tokens']} | {r['prefill_s']} | {r['peak_memory_gb']} |")
    for label, counts in (("C1 (best of 8; 75/75/75 + 20 full)", {"tiny": 75, "small": 75, "medium": 75, "full": 20}),
                          ("6.2 paired subset (best of 8; 75/75/75 + 10 full)",
                           {"tiny": 75, "small": 75, "medium": 75, "full": 10})):
        for full_actions in (500, 1000):
            for factor in ((1.0,) if label.startswith("C1") else (1.0, 3.0)):
                lines, gpu_h = table(counts, 8, m, full_actions, factor)
                print(f"\n**{label}, full plans of {full_actions} actions"
                      + (f", base takes {factor:g}x the load list" if factor != 1.0 else "") + f": {gpu_h:.1f} GPU-h**\n")
                print("| rung | instances | actions/plan | min/plan | GPU-h |\n|---|---:|---:|---:|---:|")
                print("\n".join(lines))


if __name__ == "__main__":
    main()
