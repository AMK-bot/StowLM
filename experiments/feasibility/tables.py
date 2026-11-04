"""(v1 format) Print the v1 README tables from results-v1-synthetic.json: ``uv run python experiments/feasibility/tables.py``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("results-v1-synthetic.json"))
d = json.loads(path.read_text())


def f(x, nd=2):
    return "–" if x is None else f"{x:.{nd}f}"


print("| rung (boxes, C2) | construct | legal cold p50 / p95 / p99 | legal cached p50 | is_legal p50 / p95 | "
      "push p50 / p95 / p99 | pop p95 | copy p95 | n states |")
print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
for rung, R in d["rungs"].items():
    L = R["latency_ms"]
    c2 = "C2 OFF" if all("off" in i["global_screen"] for i in R["instances"]) else "C2 on"
    n = R["instances"][0]["n_load"]
    print(f"| {rung} ({n}, {c2}) | {f(max(R['construct_s']) * 1e3, 1)} (max) | "
          f"{f(L['legal_cold']['p50'])} / {f(L['legal_cold']['p95'])} / {f(L['legal_cold']['p99'])} | "
          f"{f(L['legal_cached']['p50'], 4)} | {f(L['is_legal']['p50'], 4)} / {f(L['is_legal']['p95'], 4)} | "
          f"{f(L['push']['p50'])} / {f(L['push']['p95'])} / {f(L['push']['p99'])} | {f(L['pop']['p95'], 4)} | "
          f"{f(L['copy']['p95'], 4)} | {L['legal_cold']['n']} |")
print()
print("| rung | policy | rollouts | complete | mean depth at dead end | mean actions / completed plan | "
      "apply_plan legal | static hard viol. | mean restow lifts (completed) |")
print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
for rung, R in d["rungs"].items():
    n = R["instances"][0]["n_load"]
    for pol, P in R["policies"].items():
        P = [p for p in P if p["status"] != "error"]
        done = [p for p in P if p["status"] == "complete"]
        dead = [p for p in P if p["status"] == "dead_end"]
        print(f"| {rung} ({n}) | {pol} | {len(P)} | {len(done)}/{len(P)} | "
              f"{f(mean(p['depth'] for p in dead), 1) if dead else '–'} | "
              f"{f(mean(p['n_actions'] for p in done), 1) if done else '–'} | "
              f"{sum(bool(p.get('apply_legal')) for p in done)}/{len(done)} | "
              f"{sum(bool(p.get('static_hard')) for p in done)} | "
              f"{f(mean(p.get('restow_lifts', 0) for p in done), 1) if done else '–'} |")
print()
print("| rung | n_legal min / p50 / mean / max | lp_calls | c1a_flows (mean/rollout) | exact_calls | c2_unresolved | "
      "dig_trials (mean/rollout) |")
print("|---|---:|---:|---:|---:|---:|---:|")
for rung, R in d["rungs"].items():
    D = R["diagnostics"]
    nl = R["n_legal"]

    def m(k):
        xs = [x[k] for x in D if x.get(k) is not None]
        return f(mean(xs), 1) if xs else "–"
    print(f"| {rung} | {nl['min']} / {f(nl['p50'], 0)} / {f(nl['mean'], 0)} / {nl['max']} | {m('lp_calls')} | "
          f"{m('c1a_flows')} | {m('exact_calls')} | {m('c2_unresolved')} | {m('dig_trials')} |")
print()
print("errors:", len(d["errors"]))
for e in d["errors"][:10]:
    print(" ", {k: v for k, v in e.items() if k != "tb"})
print("meta:", {k: v for k, v in d["meta"].items() if k not in ("synthetic_overrides",)})
