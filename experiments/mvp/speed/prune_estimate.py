"""mvp-speed (4): how much decode would early pruning save? (CPU only, from decode_sweep traces.)

Idea: in a batch of k samples for one map, a sample whose PARTIAL plan already costs more moves than the best
finished candidate can never win, so it could be dropped from the batch at once (its KV row removed).

Partial cost = current-call weighted non-productive lifts (relocations + restow lifts + hatch-cover lifts, the
instance's objective weights) of ``space.expand(prefix actions)``. The final objective adds future-port restows
(>= 0), so if partial cost only grows with the prefix, partial cost is a valid lower bound of the final cost.
APPROXIMATION: monotonicity of expand() over prefixes is checked empirically here (violations are counted), not
proved; a production pruner would need the engine's running count instead.

Threshold at token t = min(greedy sample's final cost [runtime decodes the greedy group first],
final cost of samples of this batch finished by token t). Prune when partial > threshold (strictly worse:
safe for "fewest moves" selection; ties kept).

Run: uv run python experiments/mvp/speed/prune_estimate.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "out"
LIFT = dict(relocate="relocate", restow_off="restow", restow_on="restow", restow_shift="restow",
            hatch_cover_off="hatch_cover", hatch_cover_on="hatch_cover")


def main() -> None:
    from stowage.actions import get_action_space
    from stowage.io import load_instance
    from stowage.moves import apply_plan
    space = get_action_space()
    rows = [json.loads(l) for l in (OUT / "decode_sweep.jsonl").read_text().splitlines()]
    by_map = defaultdict(dict)
    for r in rows:
        by_map[r["map"]][r["B"]] = r
    report, monotone_violations, prefixes = [], 0, 0
    # measured p50 step ms per batch size (all maps), linear interpolation between measured B
    pts = sorted({r["B"]: sorted(x["step_ms_p50"] for x in rows if x["B"] == r["B"])[len([x for x in rows if x["B"] == r["B"]]) // 2]
                  for r in rows}.items())

    def step_curve(b):
        for (b0, m0), (b1, m1) in zip(pts, pts[1:]):
            if b <= b1:
                return m0 + (m1 - m0) * (b - b0) / (b1 - b0)
        return pts[-1][1]
    for path, runs in by_map.items():
        inst = load_instance(ROOT / path)
        w = inst.objective

        def partial(actions):
            c = 0.0
            for m in space.expand(inst, actions).moves:
                kind = LIFT.get(m.kind)
                if kind:
                    c += getattr(w, kind)
            return c

        def final(actions):
            return apply_plan(inst, space.expand(inst, actions)).count.weighted_nonproductive

        greedy = runs.get(1)
        g_cost = None
        if greedy and greedy["samples"][0]["status"] == "complete":
            g_cost = final([space.decode(a) for a in greedy["samples"][0]["actions"]])
        for B, r in sorted(runs.items()):
            if B == 1:
                continue
            samples = []
            for s in r["samples"]:
                acts = [space.decode(a) for a in s["actions"]]
                costs = [partial(acts[:k + 1]) for k in range(len(acts))]
                prefixes += len(costs)
                monotone_violations += sum(b < a for a, b in zip(costs, costs[1:]))
                fin = final(acts) if s["status"] == "complete" else None
                samples.append(dict(status=s["status"], n=s["n_tokens"], ends=s["action_ends"], costs=costs, final=fin))
            # simulate lock-step decode with pruning
            done_at = sorted((s["n"], s["final"]) for s in samples if s["final"] is not None)
            # target variant: the loop already knows a best-known cost per map (previous generations' winner /
            # LNS polish). Proxy: the best final cost of this run (greedy + all samples), known from step 0.
            known = min([s["final"] for s in samples if s["final"] is not None]
                        + ([g_cost] if g_cost is not None else []), default=None)

            def cuts(use_known, use_greedy=True):
                stop = []
                for s in samples:
                    cut = s["n"]
                    for end, c in zip(s["ends"], s["costs"]):
                        thr = [f for n, f in done_at if n <= end] + ([g_cost] if use_greedy and g_cost is not None else [])
                        if use_known and known is not None:
                            thr.append(known)
                        if thr and c > min(thr):
                            cut = end
                            break
                    stop.append(cut)
                return stop
            stop_known = cuts(True)
            stop_inbatch = cuts(False, use_greedy=False)  # patch 01 world: greedy decoded inside the same batch
            stop = cuts(False)
            tok_before = sum(s["n"] for s in samples)
            tok_after = sum(stop)
            steps_before, steps_after = max(s["n"] for s in samples), max(stop)
            step_ms = r["step_ms"]
            # wall estimate: per-step time scales with the live batch; use measured step_ms at each step, scaled by
            # live-after / live-before (APPROXIMATION: linear in batch, an upper bound on the saving per step)
            live_after = [sum(1 for c in stop if c > t) for t in range(len(step_ms))]
            wall_before = sum(step_ms)
            wall_after = sum(ms * (la / lb if lb else 0) for ms, la, lb in zip(step_ms, live_after, r["batch_sizes"]))
            # realistic: per-step time follows the measured p50 step-ms-vs-live-batch curve of this sweep
            curve_after = sum(ms * (step_curve(la) / step_curve(lb) if la else 0)
                              for ms, la, lb in zip(step_ms, live_after, r["batch_sizes"]))
            best = min([s["final"] for s in samples if s["final"] is not None] + ([g_cost] if g_cost is not None else []),
                       default=None)
            report.append(dict(map=r["instance_id"], B=B, greedy_cost=g_cost, best_cost=best,
                               finals=[s["final"] for s in samples], pruned=sum(c < s["n"] for c, s in zip(stop, samples)),
                               tokens_before=tok_before, tokens_after=tok_after, tokens_after_known=sum(stop_known),
                               token_saving_known=round(1 - sum(stop_known) / tok_before, 3),
                               token_saving_inbatch=round(1 - sum(stop_inbatch) / tok_before, 3),
                               token_saving=round(1 - tok_after / tok_before, 3),
                               steps_before=steps_before, steps_after=steps_after,
                               decode_wall_saving_upper=round(1 - wall_after / wall_before, 3) if wall_before else 0,
                               decode_wall_saving_curve=round(1 - curve_after / wall_before, 3) if wall_before else 0,
                               decode_wall_s=r["decode_wall_s"], prefill_s=r["prefill_s"], map_wall_s=r["map_wall_s"]))
    summary = dict(monotone_violations=monotone_violations, prefixes=prefixes, rows=report)
    for B in sorted({x["B"] for x in report}):
        sel = [x for x in report if x["B"] == B]
        tb, ta = sum(x["tokens_before"] for x in sel), sum(x["tokens_after"] for x in sel)
        dw = sum(x["decode_wall_s"] for x in sel)
        mw = sum(x["map_wall_s"] for x in sel)
        dws = sum(x["decode_wall_s"] * x["decode_wall_saving_upper"] for x in sel)
        dwc = sum(x["decode_wall_s"] * x["decode_wall_saving_curve"] for x in sel)
        summary[f"B{B}"] = dict(maps=len(sel), token_saving=round(1 - ta / tb, 3),
                                token_saving_known_target=round(1 - sum(x["tokens_after_known"] for x in sel) / tb, 3),
                                token_saving_inbatch_only=round(sum(x["token_saving_inbatch"] * x["tokens_before"] for x in sel) / tb, 3),
                                decode_wall_saving_upper=round(dws / dw, 3), map_wall_saving_upper=round(dws / mw, 3),
                                decode_wall_saving_curve=round(dwc / dw, 3), map_wall_saving_curve=round(dwc / mw, 3))
    (OUT / "prune_estimate.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    for x in report:
        print(x["map"], x["B"], "greedy", x["greedy_cost"], "finals", x["finals"], "pruned", x["pruned"],
              "tok_saving", x["token_saving"], "with_known_target", x["token_saving_known"])


if __name__ == "__main__":
    main()
