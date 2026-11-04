"""Measured D79 tiny-batch report; run after all three shards exit successfully."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean

from build import CORPUS, VESSELS, FAMILIES, atomic_json
from pin_tiny_epoch import files


def main():
    tools = Path(__file__).resolve().parent
    audits = []
    for split, requested in (("train", 870), ("val", 48)):
        rows = [json.loads(p.read_text()) for p in (CORPUS / split / "tiny").rglob("*.audit.json")]
        assert len(rows) == requested, (split, len(rows))
        expected = requested // 6
        assert Counter((r["config"]["vessel"], r["config"]["generator"]) for r in rows) == {
            (v, f): expected for v in VESSELS for f in FAMILIES}
        audits += rows
    for audit in audits:
        assert audit["accepted"] is True and audit["rule_freeze_v1_enabled"] is False
        assert audit["c2_screened"] is False
        assert audit["background_checks"] > 0 and audit["background_last"]["accepted"] is True
    prefix = "experiments/mvp/corpus_tools/tiny_epoch/src/stowage/"
    pinned = [a for a in audits if any(k.startswith(prefix) for k in a["source_files"])]
    states = [json.loads((tools / f"tiny-{i}.runtime.json").read_text()) for i in range(3)]
    assert all(s.get("complete") and s.get("last_exit_code") == 0 for s in states)
    pin = json.loads((tools / "tiny_epoch/source-pin.json").read_text())
    assert files(tools / "tiny_epoch/src/stowage") == pin["source_sha256"]
    row_counts = Counter((a["config"]["split"], a["config"]["vessel"], a["config"]["generator"]) for a in audits)
    peaks = {v: max(a["peak_rss_bytes"] for a in pinned if a["config"]["vessel"] == v) for v in VESSELS}
    report = dict(format="mvp-tiny-batch-report-v1", measured_at_utc=datetime.now(timezone.utc).isoformat(),
        counts={"train": 870, "val": 48, "total": len(audits)},
        row_counts=[dict(split=s, ship=v, family=f, count=n) for (s,v,f), n in sorted(row_counts.items())],
        producer_gate_passed=len(audits), d58_passed=len(audits), rule_freeze_v1_enabled=False,
        c2_screened=False, pinned_epoch_instances=len(pinned), earlier_epoch_instances=len(audits)-len(pinned),
        mean_producer_generation_s=mean(a["elapsed_generation_s"] for a in audits),
        mean_background_gate_s=mean(a["background_seconds"] for a in audits),
        generation_by_ship={v: dict(count=sum(a["config"]["vessel"] == v for a in audits),
            mean_producer_generation_s=mean(a["elapsed_generation_s"] for a in audits if a["config"]["vessel"] == v))
            for v in VESSELS},
        full_batch_wall_s=max(s["elapsed_wall_s"] for s in states),
        shard_wall_s={str(s["shard_index"]): s["elapsed_wall_s"] for s in states},
        max_worker_rss_bytes=max(peaks.values()), worker_rss_peaks=peaks,
        sum_worker_rss_peaks_upper_bound_bytes=sum(peaks.values()),
        effective_nice=sorted({s["effective_nice"] for s in states}),
        nice_errors=sorted({s.get("nice_error", "") for s in states}),
        approximation="Counts sized from prompt plus mandatory-placement token lower bounds on 45 test examples, total3000; gold lengths and adopted small/medium setting pending.",
        plan_checker_passed=None)
    atomic_json(tools / "tiny-summary.json", report)
    print(json.dumps({key: report[key] for key in ("counts", "mean_producer_generation_s", "mean_background_gate_s", "full_batch_wall_s", "max_worker_rss_bytes")}, sort_keys=True))


if __name__ == "__main__":
    main()
