"""Render gold yield from atomic per-case results, without importing solver code."""
from __future__ import annotations

import argparse
import fcntl
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import tempfile

TARGETS = {"tiny": 1008, "small": 1166, "medium": 672}
STAGES = ("exact", "scalable", "ballast", "checker", "mask")


def load_records(root: Path) -> list[dict]:
    records = []
    paths = [path for cohort in ("train", "val", "quarantine-pre-epoch", "quarantine-d97-warmstart", "quarantine-tiny-ballast-d101")
             for path in (root / cohort).rglob("result.json")]
    for path in sorted(paths, key=lambda p: (p.stat().st_mtime_ns, str(p))):
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue  # A malformed/interrupted write is never counted as a completed attempt.
        if row.get("completed") is True and row.get("rung") in TARGETS:
            records.append(row)
    return records


def qualifies(row: dict) -> bool:
    stages = row.get("stages", {})
    return row.get("passed") is True and all(
        stages.get(stage, {}).get("passed") is True for stage in ("checker", "mask")
    )


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def stage_mean(rows: list[dict], stage: str) -> float | None:
    return mean([float(r["stages"][stage]["wall_s"]) for r in rows
                 if r.get("stages", {}).get(stage, {}).get("wall_s") is not None])


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def rung_runtime(root: Path, rung: str) -> dict:
    records = sorted(root.glob(f"{rung}-*.process.json"), key=lambda p: p.stat().st_mtime_ns)
    if records:
        pid = json.loads(records[-1].read_text())["pid"]
        own = root / "runtime" / f"{pid}.json"
        if own.exists():
            return json.loads(own.read_text())
    latest = root / "runtime.json"
    return json.loads(latest.read_text()) if latest.exists() else {}


def plan_manifest(root: Path) -> dict:
    plans = []
    for row in load_records(root):
        if not qualifies(row) or not row.get("plan_sha256"):
            continue
        config = row.get("config", {})
        plans.append(dict(case=row["case"], split=row["split"], rung=row["rung"],
            input_sha256=row.get("input_sha256"), plan_sha256=row["plan_sha256"],
            runtime_epoch_sha256=row.get("runtime_epoch_sha256"),
            runtime_snapshot=row.get("runtime_snapshot"),
            runtime_source_policy="immutable_epoch" if row.get("runtime_epoch_sha256") else "legacy_live_tree",
            plan_path=row["case"] + "/plan.json", solver=row.get("solver"), policy=row.get("policy", "scalable-constructor-v1" if row.get("rung") == "small" else None),
            source=row.get("source", row.get("solver_metrics", {}).get("source")),
            internal_ballast_calls=row.get("internal_ballast_calls"),
            two_step_weighted_moves=row.get("two_step_weighted_moves"),
            gold_weighted_moves=row.get("gold_weighted_moves"),
            lns_elapsed_s=row.get("lns_elapsed_s"),
            lns_budget_s=15 if config.get("gold_warmstart_v1") else None,
            two_step_version=row.get("baseline_producer_version"),
            two_step_producer_sha256=row.get("baseline_producer_sha256"),
            mask_backend=row.get("stages", {}).get("mask", {}).get("decoder_backend"),
            legacy_quality=row.get("legacy_quality"),
            securing_screen=bool(config.get("scalable_config", {}).get("securing_screen", False)),
            ballast_v4=bool(config.get("ballast_v4", False)),
            ballast_scope="internal_finish_and_finalize_only" if row.get("internal_ballast_calls") else row.get("ballast_environment", {}).get("scope"),
            global_screen=row.get("global_screen", 0), rule_freeze_v1=row.get("rule_freeze_v1", False),
            configuration_sha256=row.get("configuration_sha256"), catalogue_sha256=row.get("catalogue_sha256"),
            versions=row.get("versions", {}), finished_utc=row.get("finished_utc")))
    return dict(format="stowlm-gold-plan-manifest-v1", plans=plans)


def render(root: Path, solve47_root: Path | None = None, corpus_root: Path | None = None) -> str:
    rows = load_records(root)
    now = datetime.now(timezone.utc).isoformat()
    lines = ["# Gold yield", "", f"Rendered {now}; {len(rows)} completed attempts.", "",
             "Only records with both full-checker and production-mask passes count as gold. "
             "Rule-freeze OFF; C2 OFF. Failed attempts remain in the denominator. "
             "This report reads result records; it does not independently rerun the gates. "
             "Exact solver optimality is scoped to its modeled engine domain, not every physically possible stowage plan.", "",
             "Stage means use only attempts that reached and measured that stage; absent means unavailable, not zero.", "",
             "| Rung | Gold / completed | Yield | Exact s | Scalable s | Ballast s | Checker s | Mask s | Total wall s |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for rung in TARGETS:
        group = [r for r in rows if r["rung"] == rung]
        passed = sum(qualifies(r) for r in group)
        rate = f"{passed / len(group):.1%}" if group else "—"
        lines.append(f"| {rung} | {passed}/{len(group)} | {rate} | " + " | ".join(
            fmt(stage_mean(group, s)) for s in STAGES) + " | " +
            fmt(mean([float(r["wall_s"]) for r in group if r.get("wall_s") is not None])) + " |")
    epoch_pointer = root / "runtime-epoch.json"
    if epoch_pointer.exists():
        epoch = json.loads(epoch_pointer.read_text())
        epoch_rows = [r for r in rows if r.get("runtime_epoch_sha256") == epoch["runtime_epoch_sha256"]]
        drift = sum(r.get("drop_reason") == "source_drift" for r in epoch_rows)
        lines += ["", "## Immutable runtime epoch", "",
                  f"Active snapshot: `{epoch['runtime_epoch_sha256']}`. "
                  f"Completed epoch attempts: {len(epoch_rows)}; source-drift drops: {drift}. "
                  "Historical live-tree plans retain their original provenance and no invented epoch hash. "
                  "Source/config and referenced evidence are frozen; numerical caches are private. "
                  "A new snapshot requires leader instruction."]
    gold_tiny = [r for r in rows if r["rung"] == "tiny" and qualifies(r)]
    gold_train = [r for r in gold_tiny if r.get("split") == "train"]
    current_policy = [r for r in rows if r["rung"] == "tiny" and r.get("split") == "train"
                      and r.get("config", {}).get("exact_s") == 60][-30:]
    sample = current_policy or [r for r in rows if r["rung"] == "tiny" and r.get("split") == "train"][-30:]
    runtime = rung_runtime(root, "tiny")
    workers = runtime.get("workers", 0)
    success_count = sum(qualifies(r) for r in sample)
    remaining = max(0, 300-len(gold_train))
    lines += ["", "## Tiny pilot milestone: 300 train gold plans", "",
              f"Published tiny gold: {len(gold_tiny)} total; "
              f"{sum(r.get('split') == 'train' for r in gold_tiny)} train; "
              f"{sum(r.get('split') == 'val' for r in gold_tiny)} val. "
              f"Supervisor reports {workers} allocated workers ({runtime.get('status', 'unavailable')}).", "",
              f"Timing sample: {len(sample)} recent completed attempts; "
              + ("current exact-60-second policy." if current_policy else
                 "previous policy only; current exact-60-second throughput is unmeasured.")]
    if success_count:
        seconds_per_gold = sum(r.get("wall_s", 0) for r in sample) / success_count
        lines += ["", f"APPROXIMATION: measured attempt wall cost per published gold is {seconds_per_gold:.3f} s; "
                  f"{remaining} further train gold plans project to {remaining*seconds_per_gold/max(1,workers)/3600:.3f} h "
                  f"at {workers} live workers (zero workers means no finite live ETA). "
                  f"The ~800 train-gold campaign target projects to "
                  f"{max(0,800-len(gold_train))*seconds_per_gold/max(1,workers)/3600:.3f} h at that count. "
                  "This assumes the sampled yield and perfect utilisation persist; it excludes admission waiting."]
    else:
        lines += ["", "No successful timing sample yet; time to 300 is unverified."]
    coordination_path = root / "coordination.json"
    if coordination_path.exists():
        coordination = json.loads(coordination_path.read_text())
        projection = coordination.get("projection_0200", {})
        if coordination.get("small_paused"):
            lines += ["", "## D96: small paused; tiny priority", "",
                      "No new small jobs dispatch. Existing small plans are retained under policy "
                      "`scalable-constructor-v1`, awaiting comparison with two-step. Tiny ceiling: ten "
                      "workers through live load <=16 and free memory >=15%; 870 train inputs "
                      "are expected to yield about 800 gold (leader assumption, not a measurement)."]
        elif projection:
            lines += ["", "## Small critical-chain projection (leader 15:30)", "",
                      f"Snapshot {coordination.get('updated_utc')}; live workers "
                      f"{coordination.get('active_workers', {})}; ceilings small 10 / tiny 2. "
                      "Train first; validation caps small 56 / tiny 50. Admission uses live load <=16 "
                      "and free-memory >=15%; stale ledger reservations are informational.", "",
                      f"Measured recent small rate: {projection['measured_small_gold_per_worker_hour']:.2f} "
                      f"gold per worker-hour. By 02:00 EDT, projection is "
                      f"{projection['projected_small_train_at_current_workers']:.0f} train gold "
                      f"at {projection['active_small_workers']} continuously occupied workers, or "
                      f"{projection['projected_small_train_at_ten_workers']:.0f} at ten workers. "
                      f"APPROXIMATION: {projection['approximation']}"]
    lines += ["", "## Drops", ""]
    drops = Counter((r["rung"], r.get("drop_reason") or "gate evidence missing") for r in rows if not qualifies(r))
    lines += [f"- {rung}: {reason}: {n}" for (rung, reason), n in sorted(drops.items())] or ["No completed drops recorded."]
    tiny = [r for r in rows if r["rung"] == "tiny"][:30]
    tiny_mean = mean([float(r["wall_s"]) for r in tiny if r.get("wall_s") is not None])
    lines += ["", "## Projection", "",
              "ASSUMPTION: sizing includes validation: tiny 1,008, small 1,166, medium 672 input attempts. "
              "Dropped cases do not supply gold examples; no yield correction is possible at zero observed yield.", "",
              "APPROXIMATION: projected wall time divides measured case wall-seconds by perfectly occupied single-thread workers; "
              "it excludes dispatcher overhead and does not guarantee parallel scaling.", "",
              f"Tiny pilot: first {len(tiny)}/30 completed attempts; mean total wall {fmt(tiny_mean)} s. "
              + (f"Full 1,008-attempt run: {tiny_mean * 1008 / 3600:.3f} h on one core; "
                 f"{tiny_mean * 1008 / 43200:.3f} h on 12 cores." if tiny_mean is not None else "No measured tiny projection yet."), "",
              "Small + medium budget: 6 h × 12 cores = 259,200 core-seconds for 1,838 attempts, "
              "requiring ≤141.023 s per attempt on average. This is a capacity threshold, not a measured runtime.", ""]
    lines += ["Conditional budget check: if the solver uses the 120 s small / 300 s medium caps reported in "
              "the current solve47 pilots for every attempted input, solver time alone is "
              "341,520 core-seconds = 7.906 h / 12 cores, before ballast/checker/mask. "
              "This is a configured-cap scenario, not a measured runtime prediction; the lock must supply actual settings.", ""]
    for rung in ("small", "medium"):
        group = [r for r in rows if r["rung"] == rung]
        value = mean([float(r["wall_s"]) for r in group if r.get("wall_s") is not None])
        if value is not None:
            lines.append(f"- {rung}: {len(group)} completed gold-pipeline attempts; {value:.3f} s mean; "
                         f"{TARGETS[rung] * value / 43200:.3f} h / 12 cores for {TARGETS[rung]} attempts.")
        else:
            lines.append(f"- {rung}: no completed gold-pipeline timing; six-hour fit remains unverified.")
    lines += ["", "Recommendation: retain every checker and mask gate. If the locked settings exceed the budget, "
              "shorten only objective-improvement LNS first, then reduce the requested example count with the leader. "
              "Small/medium production must wait for the settings and corpus lock.", "", "## Solver qualification evidence", "",
              "Read-only solver status excerpts below are context, not gold qualification. "
              "They may describe different settings or failed-only samples; never interpret failure runtime as passing throughput.", ""]
    if corpus_root:
        counts = Counter()
        for split in ("train", "val"):
            path = corpus_root / f"{split}.requested.json"
            if path.exists():
                try:
                    counts.update(json.loads(path.read_text()).get("counts", {}))
                except (ValueError, TypeError):
                    pass
        if counts:
            lines += ["### Upstream requested corpus counts", "",
                      "These are requested input counts, not generated or passing examples. "
                      "They may differ from the SFT sizing assumption above; this report changes no upstream request.", "",
                      "| Rung | Upstream train + val requested | SFT sizing assumed | Projected h / 1 core | Projected h / 12 cores |",
                      "|---|---:|---:|---:|---:|"]
            for rung in TARGETS:
                group = [r for r in rows if r["rung"] == rung]
                observed = tiny_mean if rung == "tiny" else mean(
                    [float(r["wall_s"]) for r in group if r.get("wall_s") is not None])
                single = observed * counts[rung] / 3600 if observed is not None else None
                lines.append(f"| {rung} | {counts[rung]} | {TARGETS[rung]} | {fmt(single)} | "
                             f"{fmt(single / 12 if single is not None else None)} |")
            total = counts["small"] + counts["medium"]
            if total:
                lines += ["", f"Upstream small + medium: {total} attempts, requiring ≤{259200 / total:.3f} s/attempt "
                          "to fit six hours on 12 cores.", ""]
    if solve47_root:
        for path in sorted(solve47_root.glob("*/STATUS.md")):
            text = path.read_text()
            lines += [f"### {path.parent.name}", "", f"Source: `{path}`", ""]
            # Preserve evidence rows and explicit qualification statements without inventing a common schema.
            evidence = [line for line in text.splitlines() if
                        (line.startswith("|") and any(c.isdigit() for c in line)) or
                        any(phrase in line.lower() for phrase in ("no tested setting qualifies", "no admitted fresh plan", "completed cases;", "no complete plan"))]
            lines += evidence[:24] or ["No machine-readable completed timing or qualification statement found; inspect source."]
            lines.append("")
    return "\n".join(lines) + "\n"


def write_reports(root: Path, solve47_root: Path | None = None, corpus_root: Path | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".reports.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        content = render(root, solve47_root, corpus_root)
        documents = {"YIELD.md": content, "STATUS.md": content,
                     "plans.manifest.json": json.dumps(plan_manifest(root), indent=2, sort_keys=True) + "\n"}
        for name, document in documents.items():
            descriptor, filename = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=root)
            temp = Path(filename)
            try:
                with os.fdopen(descriptor, "w") as handle:
                    handle.write(document)
                temp.replace(root / name)
            finally:
                temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/mvp/gold"))
    parser.add_argument("--solve47-root", type=Path, default=Path("experiments/mvp/solve47"))
    parser.add_argument("--corpus-root", type=Path, default=Path("experiments/mvp/corpus"))
    args = parser.parse_args()
    write_reports(args.output, args.solve47_root, args.corpus_root)


if __name__ == "__main__":
    main()
