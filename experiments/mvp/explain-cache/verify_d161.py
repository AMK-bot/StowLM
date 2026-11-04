"""CPU-only real stage-bundle check for D161's report-bound explanation cache.

Run after prefetch_d161.py; this never reads a key or permits network requests.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[name] = "1"

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def wait_for_explanation(stage, *, seconds, path, entry, eligibility_refusal):
    """Poll the real bundle; unrelated racer work need not delay verified prose."""
    deadline = monotonic() + seconds
    while True:
        bundle = stage.bundle()
        shown = bundle["explanation"]
        if eligibility_refusal:
            ready = (shown.get("source") == "template" and shown.get("cache_file") is None
                     and "cached text refused:" in (shown.get("cache_reason") or ""))
        else:
            ready = (shown.get("source") == ("spark-cached" if entry["mode"] == "spark" else "template")
                     and shown.get("cache_file") == str(path.relative_to(ROOT))
                     and shown.get("text") == entry["text"])
        if ready:
            return bundle
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise AssertionError("actual stage explanation did not bind before the wait deadline: " +
                                 json.dumps({key: shown.get(key) for key in
                                             ("source", "cache_file", "cache_reason")}))
        stage.wait_idle(min(20, remaining))
        print(json.dumps({"progress": "waiting for actual stage explanation", "entry": path.name,
                          "source": shown.get("source"), "cache_reason": shown.get("cache_reason"),
                          "remaining_seconds": round(max(0, deadline - monotonic()))}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=("all", "showcase", "legacy"), default="all")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--only", action="append", default=[], help="Only this cache filename or stem; repeatable")
    parser.add_argument("--wait-seconds", type=float, default=180,
                        help="Maximum background wait per stage phase; app request gate is unchanged")
    parser.add_argument("--output-label", default="", help="Unique evidence suffix for a targeted retry")
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("invalid verification shard")
    if args.wait_seconds <= 0:
        parser.error("--wait-seconds must be positive")
    if any(not (c.isalnum() or c in "-_") for c in args.output_label):
        parser.error("--output-label may contain only letters, numbers, hyphens and underscores")
    spec = importlib.util.spec_from_file_location("d171_prefetch", HERE / "prefetch_d161.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    epoch = runner.install_digest()

    from app import check_cache, stage_extras
    from app.flow import DemoService
    from app.inputs import load_inputs
    from app.stage_service import StageService
    from app.stored_explanations import _digest
    from llm.spark.client import SparkClient
    from stowage.model import CheckReport

    results = []
    # A network fence proves that the actual stage path only replays recorded answers.
    with patch("urllib.request.urlopen", side_effect=AssertionError("CPU verification forbids HTTP")), \
            patch.object(SparkClient, "complete", side_effect=AssertionError("CPU verification forbids Spark calls")):
        eligible_paths = []
        for path in sorted(HERE.glob("*__*.json")):
            if path.name.endswith(".old.json"):
                continue
            if args.only and path.name not in args.only and path.stem not in args.only:
                continue
            entry = json.loads(path.read_text())
            if args.group != "all" and (args.group == "showcase") != bool(entry.get("d161", {}).get("new_showcase")):
                continue
            eligible_paths.append(path)
        for index, path in enumerate(eligible_paths):
            if index % args.shard_count != args.shard_index:
                continue
            entry = json.loads(path.read_text())
            try:
                loaded = load_inputs(instance_path=ROOT / entry["instance_path"],
                                     plan_path=ROOT / entry["plan"]["run"] / "result.json")
                baseline = load_inputs(plan_path=ROOT / entry["baseline"]["run"] / "result.json",
                                       selected_instance=loaded.instance)
                demo = DemoService(loaded.instance, loaded.plan, loaded.metadata,
                                   comparison_inputs=[baseline], discover=False)
                stage = StageService(demo, live_physics=False, live_baseline=False)
                replay = demo.replay(demo.active_plan_id)
                # Reuse the real registered rival replay for this direct gate
                # check. StageService still exercises its ordinary background
                # baseline loader when it builds the actual bundle below.
                base_id = next(p["id"] for p in demo.catalog()["plans"] if p["id"] != demo.active_plan_id)
                base_replay = demo.replay(base_id)
                value, reason = stage_extras.cached_explanation(replay, baseline_replay=base_replay)
                report = CheckReport.model_validate(replay["checker"]["report"])
                base_report = CheckReport.model_validate(base_replay["checker"]["report"])
                assert _digest(report) == entry["report_sha256"], "primary report digest differs"
                assert _digest(base_report) == entry["baseline_report_sha256"], "baseline report digest differs"
                stored_approval = entry.get("d161") or {}
                eligibility_refusal = (entry.get("validator", {}).get("accepted") is False and any(
                    stored_approval.get(key, {}).get("eligible") is False
                    for key in ("primary_eligibility", "baseline_eligibility")))
                if eligibility_refusal:
                    from app.comparison import racer_evidence
                    assert not (racer_evidence(replay)["eligible"] and racer_evidence(base_replay)["eligible"]), (
                        "stored eligibility rejection does not match current stage evidence")
                    assert value is None and reason and "cached text refused:" in reason, reason
                    assert "this plan:" in reason or "baseline:" in reason, reason
                else:
                    assert value is not None, reason
                    assert value["text"] == entry["text"], "P1-8 changed cached text"
                bundle = wait_for_explanation(stage, seconds=args.wait_seconds, path=path, entry=entry,
                                              eligibility_refusal=eligibility_refusal)
                shown = bundle["explanation"]
                if eligibility_refusal:
                    assert shown["source"] == "template", shown
                    assert shown.get("cache_file") is None, "ineligible cached prose was served"
                    assert shown.get("cache_reason") and "cached text refused:" in shown["cache_reason"], shown
                else:
                    expected_source = "spark-cached" if entry["mode"] == "spark" else "template"
                    assert shown["source"] == expected_source, shown
                    assert shown.get("cache_file") == str(path.relative_to(ROOT)), shown
                    assert shown["text"] == entry["text"], "stage text differs"
                assert "two-step (industry practice)" not in shown["text"], "old unscoped wording"
                assert check_cache.epoch_drift() is None, "loaded source epoch changed"
                result = {"file": path.name, "bound": not eligibility_refusal, "source": shown["source"],
                          "fallback_verified": eligibility_refusal, "eligibility_rejected": eligibility_refusal,
                          "verified": True, "eligibility_rejection_reason": reason if eligibility_refusal else None,
                          "report_sha256": _digest(report), "baseline_report_sha256": _digest(base_report),
                          "validator_accepted": entry.get("validator", {}).get("accepted", entry["mode"] == "spark")}
            except Exception as error:
                result = {"file": path.name, "bound": False, "verified": False,
                          "error": f"{type(error).__name__}: {error}"}
            results.append(result)
            print(json.dumps(result), flush=True)

    evidence = {"cpu_only": True, "network_forbidden": True,
                "source_epoch_sha256": epoch, "authorization": "D171", "group": args.group,
                "shard_index": args.shard_index, "shard_count": args.shard_count,
                "only": args.only, "wait_seconds": args.wait_seconds,
                "entries": len(results), "bound": sum(r["bound"] for r in results),
                "fallback_verified": sum(r.get("fallback_verified", False) for r in results),
                "verified": sum(r.get("verified", False) for r in results), "results": results}
    suffix = f"-shard-{args.shard_index}-of-{args.shard_count}" if args.shard_count > 1 else ""
    if args.only:
        suffix += "-only"
    if args.output_label:
        suffix += "-" + args.output_label
    output = HERE / ("d161/verification-" + args.group + suffix + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({k: v for k, v in evidence.items() if k != "results"}), flush=True)
    return int(not results or evidence["verified"] != len(results))


if __name__ == "__main__":
    raise SystemExit(main())
