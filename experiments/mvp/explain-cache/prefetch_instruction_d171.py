"""D170 instruction explanation sidecar; only --run may make a paid call.

D171 permits the current source. --verify is CPU-only and fences all network.
The instruction has its own instance evidence, so this sidecar quotes no rival.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(name, "1")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from prefetch_d161 import (CappedRecorder, EVIDENCE, PURPOSE, accounting, eligibility,
                          install_digest, load_key, write_json)

KEY = "d170-instruction"
RUN = ROOT / "experiments/mvp/runs/stowlm-instruction-binding"
ENTRY = "instances/random.small.29000201.0.7/result.json"


def inputs_and_report():
    from app.check_cache import cached_check
    from app.inputs import load_inputs
    from stowage.checker import report_matches_inputs
    loaded = load_inputs(run_folder=RUN, entry=ENTRY)
    if loaded.plan is None:
        raise ValueError("D170 instruction run has no plan")
    report = cached_check(loaded.instance, loaded.plan)
    if not report_matches_inputs(report, loaded.instance, loaded.plan):
        raise ValueError("Fresh instruction report is not bound to current inputs")
    return loaded, report


def run():
    from app.stored_explanations import (_RecordedResponse, _bindings, _digest,
                                         explanation_sidecar_path, load_stored_explanation,
                                         prefetch_explanation)
    from llm.spark.client import SparkClient
    from llm.spark.explain import PlanExplainer
    marker = install_digest()
    loaded, report = inputs_and_report()
    approval = eligibility(loaded, report)
    path = explanation_sidecar_path(loaded.metadata["source_path"])
    existing = json.loads(path.read_text()) if path.is_file() else None
    if existing and existing.get("bindings") == _bindings(loaded.instance, loaded.plan, report):
        if existing.get("validator", {}).get("accepted") is False:
            # Rejection belongs to this saved response and must never be promoted.
            artifact = existing
        elif load_stored_explanation(path, loaded.instance, loaded.plan, report):
            artifact = existing
        else:
            artifact = None
    else:
        artifact = None
    response_path = EVIDENCE / "responses" / (KEY + ".json")
    saved = json.loads(response_path.read_text()) if response_path.is_file() else None
    load_key()
    client = SparkClient(max_retries=0, timeout=90)
    recorder = None
    if artifact is None:
        with client.ledger.locked():
            prior = any(row.get("purpose") == PURPOSE + "/" + KEY and row.get("phase") == "reservation"
                        for row in client.ledger._records())
        previously_rejected = bool(existing and existing.get("validator", {}).get("accepted") is False)
        if previously_rejected:
            explainer = PlanExplainer(use_api=False)
        elif saved and isinstance(saved.get("raw"), str):
            explainer = PlanExplainer(client=_RecordedResponse(saved["raw"]))
        elif prior or existing or not approval["eligible"] or not report.passed:
            explainer = PlanExplainer(use_api=False)
        else:
            recorder = CappedRecorder(client, KEY, marker)
            explainer = PlanExplainer(client=recorder)
        if path.is_file():
            backup = path.with_suffix(".old.json")
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
        artifact = prefetch_explanation(loaded.instance, loaded.plan, report,
                                       explainer=explainer, output_path=path)
        if previously_rejected:
            artifact["validator"] = existing["validator"]
    artifact["report_sha256"] = _digest(report)
    artifact["d161"] = {"purpose": PURPOSE + "/" + KEY, "current_source_sha256": marker,
                        "baseline_free": True, "eligibility": approval}
    if KEY == "showcase-800":
        artifact["d161"]["baseline_unavailable_reason"] = (
            "recorded two-step rival status dead_end; no plan in showcase-v2-twostep for this instance")
    write_json(path, artifact)
    with client.ledger.locked():
        spend = accounting(client.ledger)
    result = {"key": KEY, "sidecar": str(path.relative_to(ROOT)), "validator": artifact["validator"],
              "report_sha256": artifact["report_sha256"], "spend": spend,
              "recorded_response_reused": saved is not None,
              "new_paid_response": bool(recorder and recorder.last is not None)}
    prefix = "instruction" if KEY == "d170-instruction" else KEY
    write_json(EVIDENCE / (prefix + "-summary.json"), result)
    print(json.dumps(result), flush=True)
    return 0 if artifact["validator"]["accepted"] else 1


def verify():
    from app import check_cache
    from app.flow import DemoService
    from app.inputs import load_inputs
    from app.stage_service import StageService
    from app.stored_explanations import _digest, explanation_sidecar_path, load_stored_explanation
    from llm.spark.client import SparkClient
    from stowage.model import CheckReport
    result = {"key": KEY, "cpu_only": True, "network_forbidden": True, "bound": False}
    with patch("urllib.request.urlopen", side_effect=AssertionError("CPU verification forbids HTTP")), \
            patch.object(SparkClient, "complete", side_effect=AssertionError("CPU verification forbids Spark")):
        try:
            loaded = load_inputs(run_folder=RUN, entry=ENTRY)
            demo = DemoService(loaded.instance, loaded.plan, loaded.metadata, discover=False)
            stage = StageService(demo, live_physics=False, live_baseline=False)
            replay = demo.replay(demo.active_plan_id)
            report = CheckReport.model_validate(replay["checker"]["report"])
            path = explanation_sidecar_path(loaded.metadata["source_path"])
            artifact = json.loads(path.read_text())
            assert artifact["report_sha256"] == _digest(report), "top-level complete report digest differs"
            stored = load_stored_explanation(path, loaded.instance, loaded.plan, report)
            if artifact["validator"]["accepted"]:
                assert stored is not None, "saved instruction explanation no longer validates"
            else:
                assert stored is None, "rejected instruction explanation was served"
            stage.bundle()
            assert stage.wait_idle(60), "stage background binding timed out"
            bundle = stage.bundle()
            assert stage.wait_idle(60), "stage follow-up work timed out"
            bundle = stage.bundle()
            shown = bundle["explanation"]
            accepted = artifact["validator"]["accepted"]
            assert shown["source"] == ("spark-cached" if accepted else "template"), shown
            if accepted:
                assert shown["text"] == artifact["text"], "stage instruction prose changed"
            assert "two-step (industry practice)" not in (shown["text"] or "")
            assert check_cache.epoch_drift() is None, "loaded source changed"
            instruction = bundle.get("instruction") or {}
            if KEY == "d170-instruction":
                assert instruction.get("presented") is True, "instruction card is not presented"
                assert instruction.get("honoured") is True, "instruction is not honoured"
                assert instruction.get("not_stowlm") is False, "instruction card is not attributed to StowLM"
            result.update(bound=accepted, fallback_verified=not accepted, source=shown["source"],
                          report_sha256=_digest(report), validator=artifact["validator"],
                          source_path=loaded.metadata["source_path"], instruction_presented=instruction.get("presented"),
                          instruction_honoured=instruction.get("honoured"),
                          instruction_not_stowlm=instruction.get("not_stowlm"),
                          placements_broken_before=instruction.get("placements_broken_before"),
                          boxes_moved=instruction.get("boxes_moved"))
        except Exception as error:
            result["error"] = type(error).__name__ + ": " + str(error)
    prefix = "instruction" if KEY == "d170-instruction" else KEY
    write_json(EVIDENCE / (prefix + "-verification.json"), result)
    print(json.dumps(result), flush=True)
    return 0 if result.get("bound") or result.get("fallback_verified") else 1


def main():
    global KEY, RUN, ENTRY
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--showcase-800", action="store_true",
                        help="Explain feeder 800's own StowLM plan; its two-step rival has no plan")
    args = parser.parse_args()
    if args.showcase_800:
        KEY = "showcase-800"
        RUN = ROOT / "experiments/mvp/runs/stowlm-showcase"
        ENTRY = "instances/random.small.29000102.0.7/result.json"
    return run() if args.run else verify()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__, "message": "D170 instruction preparation failed"}))
        raise SystemExit(1)
