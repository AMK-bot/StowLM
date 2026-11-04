"""Render paired timing/LP-count/projection tables without editing other logs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--optimized", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--lp", type=Path, help="Supplemental frozen-LP replay JSON.")
    ap.add_argument("--preflight", type=Path, help="Production C2 qualification preflight JSON.")
    args = ap.parse_args()
    old = json.loads(args.reference.read_text())
    new = json.loads(args.optimized.read_text())
    process_note = ("One process" if old["config"].get("pair") else
                    "Separate fresh processes, reference first then candidate")
    lines = ["# Paired mask timing", "", process_note + "; seed 1704, one thread, cold `legal_index` per prefix.",
             "Construction, action materialization and push are excluded from mask latency.",
             "C2 on/off follow their own reference action sequences; optimized replays each exactly.", "",
             "| Rung / C2 | States | Reference median / p95 ms | Optimized median / p95 ms | Exact masks |",
             "|---|---:|---:|---:|---:|"]
    if new.get("candidate_status"):
        lines.insert(2, "Candidate status: quarantined, unshipped; measurements are research evidence.")
    for key, row in new["blocks"].items():
        before = old["blocks"][key]
        same = sum(s.get("same_mask", False) for s in row["states"])
        a, b = before["index_ms_p50_p95"], row["index_ms_p50_p95"]
        lines.append(f"| {key} | {len(row['states'])} | {a[0]:.3f} / {a[1]:.3f} | {b[0]:.3f} / {b[1]:.3f} | {same}/{len(row['states'])} |")
    lines += ["", "LP totals below include root construction, masks and pushes.", "",
              "| Rung / C2 | Reference native / cold / warm | Optimized native / cold / warm |",
              "|---|---:|---:|"]
    for key, row in new["blocks"].items():
        def totals(rec):
            chunks = [rec["construct_lp"], *[s["lp"] for s in rec["states"]],
                      *[s.get("push_lp", {}) for s in rec["states"]]]
            return " / ".join(str(int(sum(c.get(k, 0) for c in chunks))) for k in ("native", "cold", "warm"))
        lines.append(f"| {key} | {totals(old['blocks'][key])} | {totals(row)} |")
    lines += ["", "Projected base loading-plan mask time = initial boxes × sampled per-step latency.",
              "Extra restow reload/discharge steps increase these projections; this is not a complete-plan runtime guarantee.", "",
              "| Rung / C2 | Base PLACE steps | Reference median / p95 projection s | Optimized median / p95 projection s |",
              "|---|---:|---:|---:|"]
    for key, row in new["blocks"].items():
        a = old["blocks"][key]["projected_base_plan_ms_p50_p95"]
        b = row["projected_base_plan_ms_p50_p95"]
        lines.append(f"| {key} | {row['projected_base_steps']} | {a[0]/1000:.3f} / {a[1]/1000:.3f} | {b[0]/1000:.3f} / {b[1]/1000:.3f} |")
    lines += ["", f"Wall seconds, reference / optimized: {old['wall_s']:.3f} / {new['wall_s']:.3f}.",
              f"Peak process RAM: {new['maxrss_bytes_macos']/2**30:.3f} GiB.",
              f"Changed source hashes during reference / optimized: {len(old['changed_sources'])} / {len(new['changed_sources'])}.",
              "Small per-rung samples describe these immutable instances; p95 is a sample percentile, not a population guarantee."]
    if args.lp:
        lp = json.loads(args.lp.read_text())
        lines += ["", "## Supplemental frozen LP replay", "",
                  "Archived full-instance rows; 16 singleton and 16 two-cell queries with full/narrow/high/point mass windows.",
                  "This does not qualify current production C2 or measure full-mask latency.", "",
                  "| Variant | Center s | 32-query total s | Query native / cold / warm | Center + query native / cold / warm |",
                  "|---|---:|---:|---:|---:|"]
        for key, row in lp["variants"].items():
            def calls(include_center):
                return " / ".join(str(int(row["query_counts"].get(k, 0) +
                    (row["center_counts"].get(k, 0) if include_center else 0)))
                    for k in ("native", "cold", "warm"))
            lines.append(f"| {'Reference' if key == '0' else 'Candidate'} | {row['center_s']:.3f} | {row['query_total_s']:.3f} | {calls(False)} | {calls(True)} |")
        same = sum(c["same_status"] and c["same_discrete_allowed"] for c in lp["comparisons"])
        max_delta = max(max(c.get("endpoint_abs_differences", [0])) for c in lp["comparisons"])
        lines += ["", f"{same}/{len(lp['comparisons'])} statuses and mass-admission vectors match; maximum endpoint difference {max_delta:g}.",
                  f"{len(lp['comparisons']) * len(lp['discrete_masses'])} actual cargo-mass decisions compared; all sampled statuses were feasible.",
                  f"Replay wall {lp['wall_s']:.3f}s; peak {lp['maxrss_bytes_macos']/2**30:.3f} GiB; changed source hashes {len(lp['changed_sources'])}."]
    if args.preflight:
        preflight = json.loads(args.preflight.read_text())
        lines += ["", "## Current production C2", "",
                  f"Preflight: {preflight['reason']}; zero timing samples.",
                  "C2-on median/p95 and full-plan projection are unavailable. The candidate remains unshipped."]
    args.output.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
