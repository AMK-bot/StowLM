"""Render observed generator/rung/ship calibration; never infer full from tiny."""
from __future__ import annotations

from eval.snapshot_io import read_snapshot

import argparse
from collections import Counter
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def measured_schedule(sample, counts=None, workers=1):
    """Project only observed generator/rung/ship cells, never across sizes.

    ``counts`` is the pending request count by generator/rung/canonical ship.
    Without targets the result reports observations only, with no projection.
    """
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be a positive integer")
    measured = sample.get("measured", {})
    cells, missing = {}, []
    targets = counts or {}
    for key in sorted(set(measured) | set(targets)):
        stats = measured.get(key, {})
        n = targets.get(key, 0)
        mean = stats.get("mean_wall_s")
        if n and (mean is None or not stats.get("samples")):
            missing.append(key)
        cells[key] = dict(samples=stats.get("samples", 0), mean_wall_s=mean,
                          pending_requests=n,
                          projected_core_s=n * mean if mean is not None else None,
                          peak_rss_gb=stats.get("peak_rss_gb"))
    total = sum(cells[key]["projected_core_s"] for key in targets) if counts is not None and not missing else None
    return dict(measured_cells=cells, unmeasured_target_cells=missing,
                projected_total_core_s=total,
                projected_total_wall_s=total / workers if total is not None else None,
                workers=workers,
                qualification="Only observed generator/rung/ship cells are projected; parallel wall time is an ideal packing estimate, not a deadline. Full is never projected from tiny.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--calibration", type=Path, required=True)
    p.add_argument("--workers", type=int, default=1)
    args = p.parse_args(argv)
    if not 1 <= args.workers <= 12:
        p.error("workers must be 1..12 after fleet admission")
    from eval.splits import load_manifest
    from stowage.conventions import canonical_vessel_id
    sample = read_snapshot(args.calibration).json()
    allocation = load_manifest(ROOT / "experiments/splits/v2/manifest.json")
    counts = Counter(("simulated_yard" if e["spec"]["generator"] == "simulated" else "random_yard")
                     + "/" + e["spec"]["rung"] + "/" + canonical_vessel_id(e["spec"]["vessel"])
                     for e in allocation["entries"])
    lines = ["# v2 epoch schedule and calibration\n",
        "Linear policy requalification runs before source freeze and is never a regeneration stage. Central cache warming must complete before regeneration admission. Named tranches extend the same epoch and reuse completed per-spec shards.\n",
        f"Calibration: `{args.calibration}`. {'Diagnostic, not evidence.' if sample.get('diagnostic') else 'Frozen-epoch measurements.'} Request times include generator rejection/retry costs. Counts below describe the full unchanged v2 allocation, not the current tranche. RSS is the generator process only; child peak RAM was not captured.\n",
        "| Generator / rung / canonical ship | v2 requests | Samples | Measured request wall s | Process RSS MiB |",
        "|---|---:|---:|---:|---:|"]
    unknown = []
    for key, n in sorted(counts.items()):
        value = sample.get("measured", {}).get(key)
        if value is None:
            unknown.append(key)
            lines.append(f"| {key} | {n} | 0 | unmeasured | unmeasured |")
        else:
            lines.append(f"| {key} | {n} | {value['samples']} | {value['mean_wall_s']:.3f} | {value['peak_rss_gb']*1e9/2**20:.1f} |")
    projection = measured_schedule(sample, counts, args.workers)
    total = projection["projected_total_wall_s"]
    total_text = (f"**Full allocation ideal packed wall time: {total:.1f} seconds.** Based only on measured matching cells; remeasure as shard costs vary."
                  if total is not None else "**Full allocation wall time: unknown.** Some requested generator/rung/ship cells remain unmeasured. No medium/full estimate is derived from tiny.")
    lines += ["", total_text,
        "", "| Stage | Active cores | RAM / wall-time basis |",
        "|---|---:|---|",
        "| Source freeze / v2 allocation validation | 1 | Metadata; v2's 9,400 specs unchanged |",
        f"| Instance generation | {args.workers} | Measured cells above only; D58 may reject or retry candidates |",
        "| Spec-to-content binding | 1 | Checksums, raw v2 keys and explicit epoch provenance |",
        "| 4.2 / 4.6 / 4.7 / 4.8 | Shared bounded pool | Recalibrate under final qualified epoch; primary stages interleave |",
        "| 5.1 / returned-plan replay | Same pool after primary barrier | Recalibrate each rung; replay count is actual returned artifacts |",
        "| 5.4 summary | 1 | Metadata synthesis; no physical approval is inferred |",
        "", "Packing: follow-up jobs used one computational CPU and a 2 GB RAM budget, no GPU. The shared ledger reserved 13 CPUs, leaving 3 of 16 available; the one-core probe and one-core tests fit concurrently. For a later freeze with four CPUs busy elsewhere, at most twelve one-core workers fit; reserve 6 GB per worker plus 1 GB coordinator (73 GB), leaving 39 GB of 112 GB elsewhere. Larger-rung peak RAM is unmeasured; reconcile the live ledger before admission.",
        "", "First-real-shard hook (after qualification and freeze):", "", "```sh",
        "uv run python experiments/campaign-g1/regen.py --workers 1 --calibrate-first-shard --calibration-rung tiny",
        "uv run python experiments/campaign-g1/regen.py --workers 1 --calibrate-first-shard --calibration-rung small",
        "uv run python experiments/campaign-g1/regen.py --workers 1 --calibrate-first-shard --calibration-rung medium",
        "uv run python experiments/campaign-g1/regen.py --workers 1 --calibrate-first-shard --calibration-rung full",
        "# Resume the same epoch; completed shards are reused.",
        "uv run python experiments/campaign-g1/regen.py --workers 1", "```",
        "", "Each calibration invocation stops after the first new shard of the selected rung and writes <epoch>/regen/calibration.json. Normal regeneration also prints a measured remaining-request schedule after its first shard of each new rung. Matching cells only are projected; any unobserved target cell makes the total unknown. Recompute with schedule.py --calibration PATH. Campaign estimates leave unobserved requested rungs unknown. Requalification/source changes need a new freeze, never edited pins."]
    (HERE / "SCHEDULE.md").write_text("\n".join(lines)+"\n")
    print(json.dumps(dict(split_version="v2", allocated=len(allocation["entries"]),
        measured_cells=len(counts)-len(unknown), unmeasured_cells=len(unknown), total_wall_s=total)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
