"""Ablation table: per row own-plan pass, dead ends, finisher time limits, weighted lifts paired with two-step v4.4,
backtracks, time per instance. Writes <out>/ABLATION.md and ablation.json.
Usage: uv run python experiments/mvp/sft/ablation/summarize.py <out_dir>
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
TWOSTEP = ROOT / "experiments/mvp/sft/pilot2/val15-twostep"  # twostep_val.py on the same val15 maps
LABELS = {"a": "greedy", "b": "greedy + backtracking 32", "c": "greedy + backtracking 32 + look-ahead",
          "d": "best-of-2 (greedy + 1 x T=0.7), c's decoding (cut from best-of-4 for time)",
          "e": "d + 15 s LNS polish (labelled)"}


def twostep():
    out = {}
    for p in TWOSTEP.glob("*.json"):
        r = json.loads(p.read_text())
        out[r["instance_id"]] = r.get("weighted_nonproductive") if r.get("passed") else None
    return out


def row_from_run(run: Path):
    rows = {}
    for rec_path in run.glob("instances/*.json"):
        rec = json.loads(rec_path.read_text())
        folder = run / "instances" / rec["instance_id"]
        outcome = json.loads((folder / "outcome.json").read_text())["outcome"]
        report = json.loads((folder / "report.json").read_text()) or {}
        m = rec["metrics"] if "n_samples" in rec["metrics"] else rec["metrics"].get("repair_original_metrics", {})
        rows[rec["instance_id"]] = {"outcome": outcome, "pipeline_s": rec["pipeline_s"],
                                    "lifts": (report.get("count") or {}).get("weighted_nonproductive")
                                    if outcome == "own pass" else None,
                                    "backtracks": sum(m.get("gen_backtracks") or []),
                                    "override": m.get("gen_override_rate_action_start", m.get("mask_override_rate_decision")),
                                    "last_resort": m.get("last_resort"), "budget_timeouts": len(m.get("finisher_budget_timeouts") or [])}
    return rows


def main() -> None:
    out = Path(sys.argv[1])
    two = twostep()
    table, data = [], {}
    for key, label in LABELS.items():
        run = out / key
        if key == "e":
            if not (out / "e").exists() or not (out / "d").exists():
                continue
            rows = row_from_run(out / "d")
            for p in (out / "e").glob("*.json"):
                r = json.loads(p.read_text())
                rows[r["instance_id"]]["lifts"] = r["polished_lifts"]
                rows[r["instance_id"]]["pipeline_s"] += r["polish_s"]
        elif (run / "instances").exists():
            rows = row_from_run(run)
        else:
            if (out / f"{key}.SKIPPED").exists():
                table.append(f"| {key} | {label} | skipped: {(out / f'{key}.SKIPPED').read_text().strip()} |||||||")
            continue
        n = len(rows)
        own = {i: r for i, r in rows.items() if r["outcome"] == "own pass"}
        pairs = [(r["lifts"], two[i]) for i, r in own.items() if two.get(i) is not None]
        d = [a - b for a, b in pairs]
        s = {"n": n, "own_pass": len(own),
             "outcomes": {o: sum(r["outcome"] == o for r in rows.values()) for o in sorted({r["outcome"] for r in rows.values()})},
             "mean_lifts_own": st.mean(r["lifts"] for r in own.values()) if own else None,
             "paired": {"n": len(pairs), "stowlm": st.mean(a for a, _ in pairs) if pairs else None,
                        "twostep": st.mean(b for _, b in pairs) if pairs else None,
                        "diff": st.mean(d) if d else None, "better": sum(x < 0 for x in d),
                        "equal": sum(x == 0 for x in d), "worse": sum(x > 0 for x in d)},
             "backtracks_total": sum(r["backtracks"] for r in rows.values()),
             "budget_timeouts": sum(r["budget_timeouts"] for r in rows.values()),
             "last_resorts": sum(1 for r in rows.values() if r["last_resort"]),
             "s_mean": st.mean(r["pipeline_s"] for r in rows.values()) if rows else None,
             "s_max": max((r["pipeline_s"] for r in rows.values()), default=None)}
        data[key] = s
        f = lambda x: "—" if x is None else f"{x:.2f}"
        p = s["paired"]
        table.append(f"| {key} | {label} | {s['own_pass']}/{n} | {s['outcomes']} | {f(s['mean_lifts_own'])} | "
                     f"{p['n']}: {f(p['stowlm'])} vs {f(p['twostep'])} (Δ {f(p['diff'])}; {p['better']}/{p['equal']}/{p['worse']}) | "
                     f"{s['backtracks_total']} | {f(s['s_mean'])} / {f(s['s_max'])} |")
    tw = twostep()
    table.append(f"| two-step | live TwoStepPlanner, same pipeline | {sum(v is not None for v in tw.values())}/{len(tw)} | | "
                 f"{st.mean(v for v in tw.values() if v is not None) if any(v is not None for v in tw.values()) else '—'} | | | |")
    head = ["| row | config | own pass | outcomes | mean lifts (own) | paired vs two-step v4.4 (n: ours vs 2-step; Δ; b/e/w) | backtracks | s/instance mean / max |",
            "|---|---|---|---|---|---|---|---|"]
    (out / "ABLATION.md").write_text("# Pass-rate ablation on val15 (15 small validation maps, D110; all rows finisher-aware 90 s, no repair)\n\n" + "\n".join(head + table) + "\n")
    (out / "ablation.json").write_text(json.dumps(data, indent=1))
    print("\n".join(head + table))


if __name__ == "__main__":
    main()
