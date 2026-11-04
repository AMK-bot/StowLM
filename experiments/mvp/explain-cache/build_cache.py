"""Build cached Muse Spark plan explanations (explainer v3.0) for the UI's Spark panel (plan v4, from 5.10's executive).

    set -a; . /path/outside/repo/.env; set +a; export SPARK_CREDIT_READY=1
    uv run python experiments/mvp/explain-cache/build_cache.py [--offline] [--only ID ...]

For each of the 15 frozen tiny test instances, it explains:
- the exact solver's plan against the two-step plan on the same instance (why exact saves lifts);
- the two-step plan against the exact plan;
- for 3 base-mask runs (untuned Glimmer + mask), the plan against two-step.

Every plan and report is read from its run folder and must be bound to the frozen instance (the explainer refuses
otherwise). Nothing is re-planned or re-checked. Spend is guarded per job on the shared ledger ($1.50). ``--offline``
writes template-only entries (no Spark), for a dry run.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sys
from decimal import Decimal
from pathlib import Path

from llm.instructions.parser import JobGuard
from llm.spark import explain as ex
from stowage.io import load_instance, load_result
from stowage.model import CheckReport, Instance, MovePlan, PlannerResult

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
RUNS = ROOT / "experiments/mvp/runs"
JOB_START = "2026-10-29T16:45:00"  # UTC, start of this job (first call 16:51:38); earlier rows are other jobs
ALLOCATION = Decimal("1.50")
BASE_MASK = {"random.tiny.8000006.0.5": "tiny-rest", "random.tiny.8000002.0.2": "tiny",
             "random.tiny.8000004.0.2": "tiny"}
LABELS = {"exact": "exact solver", "twostep": "two-step (industry practice)", "base-mask": "untuned Glimmer + mask"}


def instances() -> dict[str, Path]:
    out = {}
    for path in glob.glob(str(ROOT / "experiments/mvp/corpus/test/tiny/*/*/seed-*[0-9].json")):
        out[json.loads(Path(path).read_text())["id"]] = Path(path)
    return out


def load_run(folder: Path) -> tuple[MovePlan, CheckReport, dict]:
    raw = json.loads((folder / "result.json").read_text())
    # The reference runs save through stowage.io (with schema_version); base-mask runs (eval-c1) save the bare model.
    result = load_result(folder / "result.json") if "schema_version" in raw else PlannerResult.model_validate(raw)
    report = CheckReport.model_validate_json((folder / "report.json").read_text())
    extra = {}
    for name in ("record.json", "outcome.json"):
        if (folder / name).exists():
            extra = json.loads((folder / name).read_text())
    return result.plan, report, {"status": result.status, "planner": result.planner, "extra": extra}


def breakdown(report: CheckReport) -> dict:
    c = report.count
    return {"crane_lifts_now": c.total_lifts_now, "yard_rehandles": c.relocations, "restow_lifts_now": c.restow_lifts,
            "hatch_cover_lifts_now": c.hatch_cover_lifts,
            "later_restow_lifts": sum(f.restow_lifts for f in c.future),
            "later_hatch_cover_lifts": sum(f.hatch_cover_lifts for f in c.future),
            "weighted_D11": c.weighted_nonproductive}


_WORDS = {"yard_rehandles": "yard rehandles", "restow_lifts_now": "restow lifts now",
          "hatch_cover_lifts_now": "hatch-cover lifts now", "later_restow_lifts": "projected later-port restow lifts",
          "later_hatch_cover_lifts": "projected later-port hatch-cover lifts"}


def why(label: str, mine: dict, base_label: str, base: dict) -> str:
    """Deterministic sentence from the two checker reports: which lift types differ (observed, not a proof)."""
    diff = base["weighted_D11"] - mine["weighted_D11"]
    parts = [f"{_WORDS[k]} {mine[k]} vs {base[k]}" for k in _WORDS if mine[k] != base[k]]
    if not diff and not parts:
        return f"The {label} plan and the {base_label} plan have identical lift counts on this instance."
    head = (f"The {label} plan uses {abs(diff):g} {'fewer' if diff > 0 else 'more'} weighted lifts than the "
            f"{base_label} plan" if diff else f"The {label} and {base_label} plans tie on weighted lifts")
    return head + (": " + "; ".join(parts) + "." if parts else ".") + " Observed on this instance; not a proof."


class Recorder:
    """Wraps the Spark client to keep the raw answer and measured cost of the one call per explanation."""

    def __init__(self, client, guard):
        self.client, self.guard, self.last = client, guard, None

    def complete(self, prompt, **kwargs):
        self.last = None  # reset first: a refused call must never inherit the previous entry's answer or cost
        # Sound per-call bound for this job's allocation: a token covers at least one UTF-8 byte, so input tokens
        # <= prompt bytes; output <= max_output_tokens (reasoning included). The client separately reserves its
        # full-context hold on the project ceiling.
        from llm.spark.spend import Usage, calculate_cost
        bound = calculate_cost(Usage(len(prompt.encode()), kwargs["max_output_tokens"], 0))
        if self.guard.spent() + bound > self.guard.allocation:
            from llm.spark.spend import SpendCapExceeded
            raise SpendCapExceeded("explain-cache job allocation would be exceeded")
        response = self.client.complete(prompt, **kwargs)
        self.last = response
        return response


def main() -> None:
    offline = "--offline" in sys.argv
    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    paths = instances()
    jobs = []
    for iid in sorted(paths):
        jobs.append((iid, "exact", RUNS / "test-exact/instances" / iid, "twostep", RUNS / "test-twostep/instances" / iid))
        jobs.append((iid, "twostep", RUNS / "test-twostep/instances" / iid, "exact", RUNS / "test-exact/instances" / iid))
    for iid, sub in BASE_MASK.items():
        jobs.append((iid, "base-mask", RUNS / f"base-mask/{sub}/instances" / iid, "twostep",
                     RUNS / "test-twostep/instances" / iid))
    recorder = guard = None
    if not offline:
        from llm.spark.client import SparkClient
        client = SparkClient(max_retries=0)
        guard = JobGuard(client.ledger.path, since=JOB_START, allocation_usd=ALLOCATION, purpose="plan-explainer")
        recorder = Recorder(client, guard)
    index = []
    for iid, method, folder, base_method, base_folder in jobs:
        key = f"{iid}__{method}"
        if only and key not in only and iid not in only:
            continue
        instance = load_instance(paths[iid])
        plan, report, meta = load_run(folder)
        base_plan, base_report, _ = load_run(base_folder)
        baseline = (LABELS[base_method], base_plan, base_report)
        facts = ex.explanation_facts(instance, plan, report, baseline=baseline)
        explainer = ex.PlanExplainer(recorder, use_api=not offline)
        text = explainer.explain(instance, plan, report, baseline=baseline)
        spark = text.startswith(ex._SPARK_HEADER)
        raw = recorder.last.text if (recorder and recorder.last is not None) else None
        selection = ex._validated_selection(raw, facts) if (spark and raw) else None
        entry = {
            "schema": "stowlm.explain-cache/1",
            "explainer_version": ex.EXPLAINER_VERSION,
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "instance_id": iid, "instance_path": str(paths[iid].relative_to(ROOT)),
            "plan": {"method": method, "label": LABELS[method], "run": str(folder.relative_to(ROOT)),
                     "plan_id": plan.id, "planner_status": meta["status"],
                     "exact_label": meta["extra"].get("exact_label")},
            "baseline": {"method": base_method, "label": LABELS[base_method], "run": str(base_folder.relative_to(ROOT)),
                         "plan_id": base_plan.id},
            "verdict": facts["verdict"],
            "weighted_D11": facts["weighted"], "baseline_weighted_D11": facts["baseline"]["weighted"],
            "comparison": {"plan": breakdown(report), "baseline": breakdown(base_report),
                           "why": why(LABELS[method], breakdown(report), LABELS[base_method], breakdown(base_report)),
                           "source": "both checker reports (count, future projection); deterministic, not Spark"},
            "mode": "spark" if spark else "template",
            "text": text,
            "selection": ({"headline": selection[0], "points": selection[1]} if selection else None),
            "statements": facts["statements"],
            "spark": ({"model": "muse-spark-1.3", "raw": raw, "cost_usd": str(recorder.last.cost_usd),
                       "request_id": recorder.last.request_id, "rejection": explainer.last_rejection}
                      if recorder and recorder.last is not None else None),
        }
        (OUT / f"{key}.json").write_text(json.dumps(entry, indent=1))
        index.append(entry)
        print(key, entry["mode"], entry["verdict"], entry["weighted_D11"], "vs", entry["baseline_weighted_D11"],
              entry["spark"] and entry["spark"]["cost_usd"], flush=True)
    if guard:
        print("job spend", guard.spent())


if __name__ == "__main__":
    main()
