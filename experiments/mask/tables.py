"""Print the README tables from experiments/mask/*.jsonl / gpu*.json. Usage: uv run python experiments/mask/tables.py"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name):
    p = HERE / name
    if not p.exists():
        return [], None
    rows, census = [], None
    for line in p.read_text().splitlines():
        r = json.loads(line)
        if "census" in r:
            census = r["census"]
        else:
            rows.append(r)
    return rows, census


def pct(xs, q):
    s = sorted(x for x in xs if x is not None)
    return s[min(len(s) - 1, int(q * len(s)))] if s else float("nan")


def cpu_tables():
    print("| rung | source | lookahead | samples | complete | dead end | timeout | mean depth at dead end | replay legal / static hard 0 "
          "| mask ms per action p50 / p95 | engine share of mask time | lookahead evals per action |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    censuses = {}
    for rung in ("tiny", "small", "medium", "medium-la8", "full"):
        rows, census = load(f"cpu-{rung}.jsonl")
        rung = rung.split("-")[0]
        if census:
            censuses[rung] = census
        keys = sorted({(r["source"], r["lookahead"]) for r in rows}, key=lambda k: (k[0], -k[1]))
        for src, la in keys:
            g = [r for r in rows if r["source"] == src and r["lookahead"] == la]
            comp = [r for r in g if r["status"] == "complete"]
            dead = [r for r in g if r["status"] == "dead_end"]
            per_action = [1e3 * r["mask_total_s"] / max(1, r["depth"] + 1) for r in g]
            share = sum(r["engine_legal_s"] for r in g) / max(1e-9, sum(r["mask_total_s"] for r in g))
            legal = sum(1 for r in comp if r.get("apply_legal") and not r.get("static_hard"))
            evals = sum(r["lookahead_evals"] for r in g) / max(1, sum(r["depth"] for r in g))
            dd = statistics.mean(r["depth"] for r in dead) if dead else float("nan")
            print(f"| {rung} | {src} | {la} | {len(g)} | {len(comp)} | {len(dead)} "
                  f"| {sum(r['status'] == 'timeout' for r in g)} | {dd:.1f} | {legal}/{len(comp)} "
                  f"| {pct(per_action, .5):.2f} / {pct(per_action, .95):.2f} | {100 * share:.0f}% | {evals:.2f} |")
    print()
    for rung, c in censuses.items():
        print(rung, json.dumps(c))
    rows, _ = load("cpu-tiny-check.jsonl")
    comp = [r for r in rows if r["status"] == "complete"]
    if rows:
        print(f"\ntiny full check: {len(rows)} samples, {len(comp)} complete, "
              f"{sum(r.get('check_passed', False) for r in comp)} passed; finalize statuses "
              f"{sorted({(r.get('finalize_status'), r.get('ballast_status')) for r in comp})}")
        rules: dict[str, int] = {}
        for r in comp:
            for x in r.get("check_hard", []):
                rules[x] = rules.get(x, 0) + 1
        print("  hard rule ids on failed checks:", json.dumps(dict(sorted(rules.items(), key=lambda kv: -kv[1]))))
        dead_rules: dict[str, int] = {}
    for rung in ("tiny", "small", "medium", "full"):
        rows, _ = load(f"cpu-{rung}.jsonl")
        dead_rules = {}
        for r in rows:
            if r["dead_end"]:
                k = ",".join(r["dead_end"]["rule_ids"]) or "(none)"
                dead_rules[k] = dead_rules.get(k, 0) + 1
        if dead_rules:
            print(rung, "dead-end rule ids:", json.dumps(dead_rules))


def gpu_tables():
    p = HERE / "gpu.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())
    print("\n| rung | prompt tokens | batch | unmasked greedy ms/step | masked ms/step | overhead | masked tok/s "
          "| host mask s / loop s |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in d.get("overhead", []):
        m = r["masked"]
        print(f"| {r['rung']} | {r['prompt_tokens']} | {r['batch']} | {r['unmasked_ms_p50']} | {r['masked_ms_p50']} "
              f"| {r.get('overhead_pct')}% | {r.get('masked_tok_s_full_batch')} | {m['host_mask_s']} / {m['wall_s']} |")
    plans_path = HERE / "gpu-plans.jsonl"
    if plans_path.exists():
        rows = [json.loads(x) for x in plans_path.read_text().splitlines()]
        print("\n| rung | plans | complete | dead end | max_tokens | mean depth (dead end) | mean actions/loads |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for rung in ("tiny", "small", "medium", "full"):
            g = [r for r in rows if r["rung"] == rung]
            if not g:
                continue
            c = lambda s: sum(r["status"] == s for r in g)  # noqa: E731
            dead = [r["depth"] for r in g if r["status"] == "dead_end"]
            print(f"| {rung} | {len(g)} | {c('complete')} | {c('dead_end')} | {c('max_tokens')} "
                  f"| {statistics.mean(dead) if dead else float('nan'):.1f} | "
                  f"{statistics.mean(r['depth'] for r in g):.1f}/{g[0]['n_load']} |")
    for r in d.get("plans", []):
        print(json.dumps({k: r[k] for k in ("rung", "instance", "batch", "wall_s", "status", "mean_depth")}),
              json.dumps({k: r["stats"][k] for k in ("tok_s", "host_mask_s", "wall_s", "step_ms_p50")}))


if __name__ == "__main__":
    cpu_tables()
    gpu_tables()
