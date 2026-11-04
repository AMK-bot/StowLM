"""Explicit D161 preparation, never imported by the stage page.

Run ``uv run python experiments/mvp/explain-cache/prefetch_d161.py --prepare``
then ``--run`` on the current source, authorized by D171. Only --run loads the
credential, and all paid requests retain the normal Spark project spend gates.
CPU stage-bundle verification is a separate step.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

for _thread_key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_thread_key, "1")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).parent
EVIDENCE = OUT / "d161"
PURPOSE = "d161-prefetch-20261030"
CAP = Decimal("5")
# D168's instruction parse explicitly shares D161's allocation. Conservatively
# include every Spark call since D161 was decided, including concurrent jobs.
ALLOCATION_START = "2026-10-30T17:05:00"
TWOSTEP = "two-step (our model of industry practice)"
OLD_WORDING = "two-step (industry practice)"
_PREPARED = {}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def install_digest() -> str:
    """D171 lifts the install gate; pin the current checker and explainer code."""
    from app.check_cache import checker_epoch, require_current
    require_current()
    source = (ROOT / "src/llm/spark/explain.py").read_bytes()
    return hashlib.sha256(checker_epoch().encode() + source).hexdigest()


def old_entries() -> list[Path]:
    return [p for p in sorted(OUT.glob("*__*.json")) if not p.name.endswith(".old.json")
            and json.loads(p.read_text()).get("schema") == "stowlm.explain-cache/1"
            and not json.loads(p.read_text()).get("d161", {}).get("new_showcase")]


def inventory() -> list[dict]:
    jobs = []
    for iid, vessel in (("random.small.29000201.0.7", "1700"),
                        ("random.small.29000022.0.7", "500")):
        jobs.append({"key": iid + "__stowlm", "instance_id": iid, "method": "stowlm",
                     "instance_path": "experiments/mvp/corpus/showcase/feeder_" + vessel + "-small.json",
                     "label": "StowLM 8.2", "run": "experiments/mvp/runs/stowlm-showcase/instances/" + iid,
                     "baseline_run": "experiments/mvp/runs/showcase-v2-twostep/instances/" + iid,
                     "paid": True, "new_showcase": True})
    # The feeder800 rival is dead_end with no plan; it and D170 use baseline-free,
    # report-bound sidecars (prefetch_instruction_d171.py).
    for path in old_entries():
        original = path.with_suffix(".old.json")
        data = json.loads((original if original.is_file() else path).read_text())
        jobs.append({"key": path.stem, "instance_id": data["instance_id"],
                     "method": data["plan"]["method"], "label": data["plan"]["label"],
                     "run": data["plan"]["run"], "baseline_run": data["baseline"]["run"],
                     "instance_path": data["instance_path"], "paid": OLD_WORDING in data["text"],
                     "new_showcase": False})
    # Demo-critical entries precede legacy regenerations, which precede offline metadata refreshes.
    return sorted(jobs, key=lambda j: (not j["new_showcase"], not j["paid"], j["key"]))


def accounting(ledger) -> dict:
    records = ledger._records()
    rows = [r for r in records if r.get("purpose", "").startswith(PURPOSE + "/")]
    measured, holds = ledger._totals(rows)
    shared = [r for r in records if r.get("purpose", "").startswith(PURPOSE + "/")
              or r.get("time", "") >= ALLOCATION_START]
    shared_measured, shared_holds = ledger._totals(shared)
    return {"measured_usd": str(measured), "holds_usd": str(holds),
            "charged_or_reserved_usd": str(measured + holds),
            "shared_d161_measured_usd": str(shared_measured), "shared_d161_holds_usd": str(shared_holds),
            "shared_d161_charged_or_reserved_usd": str(shared_measured + shared_holds),
            "calls": sum(r.get("phase") == "reservation" for r in rows), "cap_usd": str(CAP)}


def load_key() -> None:
    """Read one assignment as data; never evaluate the parent folder's dotenv."""
    source = ROOT.parent / ".env"
    found = None
    for line in source.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == "MODEL_API_KEY":
            parts = shlex.split(value, comments=True, posix=True)
            if len(parts) != 1 or not parts[0].strip() or any(c in parts[0] for c in "\r\n"):
                raise RuntimeError("The dotenv MODEL_API_KEY assignment is invalid")
            found = parts[0]
    if found is None:
        raise RuntimeError("MODEL_API_KEY assignment is missing from the parent dotenv")
    os.environ["MODEL_API_KEY"] = found
    os.environ["SPARK_CREDIT_READY"] = "1"


class CappedRecorder:
    """Full-context job reservation serialized with the client's project gate."""
    def __init__(self, client, key: str, marker: str):
        self.client, self.key, self.marker = client, key, marker
        self.last = None
        self.error = None

    def complete(self, prompt, **kwargs):
        from llm.spark.client import CONTEXT_TOKENS
        from llm.spark.spend import SpendCapExceeded, Usage, calculate_cost
        with self.client.ledger.locked():
            if install_digest() != self.marker:
                raise RuntimeError("Current source epoch changed during prefetch")
            totals = accounting(self.client.ledger)
            reservation = calculate_cost(Usage(CONTEXT_TOKENS - kwargs["max_output_tokens"],
                                                kwargs["max_output_tokens"], 0))
            if Decimal(totals["shared_d161_holds_usd"]) > 0:
                self.error = "prior call has unknown billing; no further calls"
                raise SpendCapExceeded(self.error)
            if Decimal(totals["shared_d161_charged_or_reserved_usd"]) + reservation > CAP:
                self.error = "next full-context reservation would exceed the $5 job cap"
                raise SpendCapExceeded(self.error)
            kwargs["purpose"] = PURPOSE + "/" + self.key
            try:
                response = self.client.complete(prompt, **kwargs)
            except Exception as error:
                self.error = type(error).__name__
                raise
            self.last = response
            # Preserve each paid answer before validation/output so a rerun never buys it again.
            write_json(EVIDENCE / "responses" / (self.key + ".json"),
                       {"raw": response.text, "cost_usd": str(response.cost_usd),
                        "request_id": response.request_id, "model": "muse-spark-1.3"})
            return response


def helper_module():
    spec = importlib.util.spec_from_file_location("d161_build_cache", OUT / "build_cache.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepared_inputs(job):
    if job["key"] in _PREPARED:
        return _PREPARED[job["key"]]
    from app.inputs import load_inputs, find_instance_path
    from app.stored_explanations import _digest
    from stowage.checker import report_matches_inputs
    from app.check_cache import cached_check as check
    loaded = load_inputs(plan_path=ROOT / job["run"] / "result.json",
                         instance_path=ROOT / job["instance_path"] if job.get("instance_path") else None)
    base = load_inputs(plan_path=ROOT / job["baseline_run"] / "result.json",
                       instance_path=ROOT / job["instance_path"] if job.get("instance_path") else None,
                       selected_instance=loaded.instance)
    if loaded.plan is None or base.plan is None or loaded.instance != base.instance:
        raise ValueError("Primary and baseline must have plans bound to the same complete instance")
    report, base_report = check(loaded.instance, loaded.plan), check(base.instance, base.plan)
    if not (report_matches_inputs(report, loaded.instance, loaded.plan)
            and report_matches_inputs(base_report, base.instance, base.plan)):
        raise ValueError("Fresh reports do not bind to the selected plans")
    instance_path = job.get("instance_path")
    if not instance_path:
        path = find_instance_path(loaded.instance.id)
        if path is None:
            raise ValueError("No complete frozen instance path found for this run")
        # Cache gate compares complete normalized instance contents on this path.
        from stowage.io import load_instance
        from app.identity import instance_content_digest
        if instance_content_digest(load_instance(path)) != instance_content_digest(loaded.instance):
            raise ValueError("Resolved instance path differs from the run's complete instance")
        instance_path = str(path.relative_to(ROOT))
    prepared = loaded, base, report, base_report, instance_path, _digest(report), _digest(base_report)
    _PREPARED[job["key"]] = prepared
    return prepared


def eligibility(loaded, report):
    """Use the stage's authoritative approval contract on the fresh full report."""
    from app.backend import certify_replay
    from app.comparison import racer_evidence
    from stowage.model import PlannerResult
    raw = loaded.metadata.get("planner_result")
    checker, candidate, shipping = certify_replay(
        loaded.instance, loaded.plan, PlannerResult.model_validate(raw) if raw else None,
        {"report": report.model_dump(mode="json")}, len(loaded.plan.moves))
    return racer_evidence({"instance": loaded.instance.model_dump(mode="json"),
                           "plan": loaded.plan.model_dump(mode="json"), "checker": checker,
                           "planner_result": candidate.model_dump(mode="json"), "metadata": shipping})


def generate(job, client, marker, helpers):
    from app.stored_explanations import _RecordedResponse
    from llm.spark import explain as ex
    loaded, base, report, base_report, instance_path, report_sha, base_sha = prepared_inputs(job)
    path = OUT / (job["key"] + ".json")
    existing = json.loads(path.read_text()) if path.is_file() else None
    baseline_label = TWOSTEP if job["new_showcase"] else (
        (existing or {})["baseline"]["label"].replace(OLD_WORDING, TWOSTEP))
    baseline = (baseline_label, base.plan, base_report)
    facts = ex.explanation_facts(loaded.instance, loaded.plan, report, baseline=baseline)
    approval, base_approval = eligibility(loaded, report), eligibility(base, base_report)
    paid_eligible = approval["eligible"] and base_approval["eligible"] and facts["verdict"] == "PASS"
    response_path = EVIDENCE / "responses" / (job["key"] + ".json")
    recorded = json.loads(response_path.read_text()) if response_path.is_file() else None
    if recorded is None and existing and existing.get("d161"):
        recorded = existing.get("spark")
    if not job["paid"] and recorded is None:
        recorded = existing.get("spark")
    # A previous verdict belongs to that particular recorded response. Hash-only
    # refreshes must never promote an answer that the validator already rejected.
    prior_rejection = list((recorded or {}).get("rejection") or [])
    if (recorded and existing and existing.get("validator", {}).get("accepted") is False
            and recorded.get("request_id") == (existing.get("spark") or {}).get("request_id")):
        prior_rejection = prior_rejection or list(existing["validator"].get("problems") or ["Previously rejected response"])
    recorder = None
    if not paid_eligible:
        run = ex.PlanExplainer(use_api=False)
    elif prior_rejection:
        run = ex.PlanExplainer(use_api=False)
    elif recorded and isinstance(recorded.get("raw"), str):
        run = ex.PlanExplainer(client=_RecordedResponse(recorded["raw"]))
    elif job["paid"] and paid_eligible:
        # A call which completed without a recoverable response may be billed. Never replay it on rerun.
        with client.ledger.locked():
            prior = any(r.get("purpose") == PURPOSE + "/" + job["key"]
                        and r.get("phase") == "reservation" for r in client.ledger._records())
        if prior or (existing and existing.get("d161")):
            run = ex.PlanExplainer(use_api=False)
        else:
            recorder = CappedRecorder(client, job["key"], marker)
            run = ex.PlanExplainer(client=recorder)
    else:
        run = ex.PlanExplainer(use_api=False)
    text = run.explain(loaded.instance, loaded.plan, report, baseline=baseline)
    if recorder and recorder.last is not None:
        recorded = json.loads(response_path.read_text())
    spark = text.startswith(ex._SPARK_HEADER) and not run.last_rejection
    if OLD_WORDING in text:
        raise ValueError("Regenerated text contains the unqualified industry-practice label")
    selection = ex._validated_selection(recorded["raw"], facts) if spark and recorded else None
    rejection = prior_rejection or list(run.last_rejection)
    if not paid_eligible:
        rejection = ["Stage binding refused: " + "; ".join(
            ["primary: " + reason for reason in approval["ineligible_reasons"]]
            + ["baseline: " + reason for reason in base_approval["ineligible_reasons"]])]
    if not spark and not rejection:
        rejection = (["Paid request refused: primary or baseline lacks clean stage shipping eligibility"]
                     if job["paid"] and not paid_eligible else
                     [recorder.error if recorder and recorder.error else "No validated Spark response available"])
    spark_data = ({**recorded, "model": "muse-spark-1.3", "rejection": rejection} if recorded else None)
    label = job["label"].replace(OLD_WORDING, TWOSTEP)
    result = loaded.metadata.get("planner_result") or {}
    entry = {"schema": "stowlm.explain-cache/1", "explainer_version": ex.EXPLAINER_VERSION,
             "created_utc": datetime.now(timezone.utc).isoformat(), "instance_id": loaded.instance.id,
             "instance_path": instance_path,
             "plan": {"method": job["method"], "label": label, "run": job["run"],
                      "plan_id": loaded.plan.id, "planner_status": result.get("status", "ok"),
                      "exact_label": (existing or {}).get("plan", {}).get("exact_label")},
             "baseline": {"method": "twostep" if job["new_showcase"] else existing["baseline"]["method"],
                          "label": baseline_label, "run": job["baseline_run"], "plan_id": base.plan.id},
             "report_sha256": report_sha, "baseline_report_sha256": base_sha,
             "verdict": facts["verdict"], "weighted_D11": facts["weighted"],
             "baseline_weighted_D11": facts["baseline"]["weighted"],
             "comparison": {"plan": helpers.breakdown(report), "baseline": helpers.breakdown(base_report),
                            "why": helpers.why(label, helpers.breakdown(report), baseline_label,
                                               helpers.breakdown(base_report)),
                            "source": "both fresh checker reports; deterministic, not Spark"},
             "mode": "spark" if spark else "template", "text": text, "statements": facts["statements"],
             "selection": {"headline": selection[0], "points": selection[1]} if selection else None,
             "spark": spark_data, "validator": {"accepted": bool(spark), "problems": rejection},
             "d161": {"purpose": PURPOSE, "source_epoch_sha256": marker, "authorization": "D171", "new_showcase": job["new_showcase"],
                      "primary_eligibility": approval, "baseline_eligibility": base_approval,
                      "paid_regeneration_requested": job["paid"],
                      "recorded_response_revalidated": bool(recorded and recorder is None)}}
    write_json(path, entry)
    return {"key": job["key"], "mode": entry["mode"], "validator": entry["validator"],
            "report_sha256": report_sha, "baseline_report_sha256": base_sha,
            "new_showcase": job["new_showcase"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--run", action="store_true")
    parser.add_argument("--group", choices=("all", "showcase", "legacy"), default="all")
    args = parser.parse_args(argv)
    marker = install_digest()
    jobs = inventory()
    jobs = [job for job in jobs if args.group == "all" or (args.group == "showcase") == job["new_showcase"]]
    inventory_data = {"source_epoch_sha256": marker, "authorization": "D171", "jobs": jobs, "cap_usd": str(CAP),
                      "paid_targets": sum(j["paid"] for j in jobs),
                      "recorded_only_targets": sum(not j["paid"] for j in jobs)}
    # Resolve every current input and full report before enabling any paid client.
    # --run reuses these reports, bound to the D171-authorized current source epoch.
    inventory_data["prepared"] = []
    for job in jobs:
        loaded, base, report, base_report, _, report_sha, base_sha = prepared_inputs(job)
        from llm.spark.explain import explanation_facts
        label = TWOSTEP if job["new_showcase"] else json.loads(
            (OUT / (job["key"] + ".json")).read_text())["baseline"]["label"].replace(OLD_WORDING, TWOSTEP)
        facts = explanation_facts(loaded.instance, loaded.plan, report, baseline=(label, base.plan, base_report))
        inventory_data["prepared"].append({"key": job["key"], "report_sha256": report_sha,
            "baseline_report_sha256": base_sha, "verdict": facts["verdict"],
            "primary_eligibility": eligibility(loaded, report), "baseline_eligibility": eligibility(base, base_report)})
        print(json.dumps({"prepared": job["key"], "verdict": facts["verdict"]}), flush=True)
    write_json(EVIDENCE / ("inventory-" + args.group + ".json"), inventory_data)
    if args.prepare:
        print(json.dumps({k: v for k, v in inventory_data.items() if k not in ("jobs", "prepared")}))
        return 0
    # Preserve the complete historical cache once, before changing any active entry.
    for path in (OUT / (job["key"] + ".json") for job in jobs if not job["new_showcase"]):
        backup = path.with_suffix(".old.json")
        if not backup.exists():
            with backup.open("xb") as handle:
                handle.write(path.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
    load_key()
    from llm.spark.client import SparkClient
    client = SparkClient(max_retries=0, timeout=90)
    helpers = helper_module()
    rows, failures = [], []
    for job in jobs:
        try:
            row = generate(job, client, marker, helpers)
            rows.append(row)
            print(json.dumps(row), flush=True)
        except Exception as error:
            # Never echo arbitrary exception text; provider credentials remain process-only.
            failures.append({"key": job["key"], "error_type": type(error).__name__})
            print(json.dumps(failures[-1]), flush=True)
        with client.ledger.locked():
            totals = accounting(client.ledger)
        summary = {"source_epoch_sha256": marker, "authorization": "D171", "group": args.group, "purpose": PURPOSE, "entries_written": len(rows),
                   "accepted": sum(r["validator"]["accepted"] for r in rows),
                   "rejected": sum(not r["validator"]["accepted"] for r in rows),
                   "entries": rows, "failures": failures, "spend": totals}
        write_json(EVIDENCE / ("summary-" + args.group + ".json"), summary)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__,
                          "message": "D161 preparation failed; no exception details are printed"}))
        raise SystemExit(1)
