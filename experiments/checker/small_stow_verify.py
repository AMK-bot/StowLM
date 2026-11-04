"""Independently verify frozen initial and decision-empty public part verdicts.

Diagnostic only: an empty decision region intentionally omits required cargo,
so its visibility/lashing results are not full-checker approval of a load plan.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

from small_stow_common import ROOT, background_only, cases, load_case, source_identity


def main():
    from stowage.checker.visibility import check_visibility, _loading_condition
    from stowage.checker.lashing import check_lashing

    archived = {r["case"]: r for r in json.loads(
        (ROOT / "experiments/ballast/review_fix/diagnostics.json").read_text())}
    started = time.perf_counter()
    before = source_identity()
    rows = []
    for case in cases():
        instance, plan, full = load_case(case)
        input_json = instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json()
        states = {"full": full, "background_only": background_only(full)}
        row = {"case": case, "conditions": {}}
        for label, ship in states.items():
            condition = _loading_condition(instance, ship)
            entry = dict(gm_m=condition.gm_m, kg_m=condition.kg_m,
                mass_kg=condition.mass_kg, draught_aft_m=condition.t_aft_m,
                draught_fwd_m=condition.t_fwd_m, parts={})
            for name, function in (("visibility", check_visibility), ("lashing", check_lashing)):
                report = function(instance, ship, call_index=instance.call_index)
                assert report.ran, (case, label, name, report.error)
                if label == "full":
                    reference = archived[case]["departure"]["parts"][name]
                    assert len(report.violations) == len(reference["violations"]), (case, name)
                    # Every archived initial violation, not only a worst ratio.
                    for actual, old in zip(report.violations, reference["violations"]):
                        for field in ("rule_id", "severity", "call_index", "message", "slots", "containers", "unit"):
                            assert getattr(actual, field) == old[field], (case, name, field)
                        for field in ("measured", "limit"):
                            value = getattr(actual, field)
                            assert (value is None and old[field] is None) or math.isclose(
                                value, old[field], rel_tol=1e-9, abs_tol=1e-8), (case, name, field)
                    for metric, old in reference["metrics"].items():
                        assert math.isclose(report.metrics[metric], old, rel_tol=1e-9, abs_tol=1e-8), (case, name, metric)
                entry["parts"][name] = report.model_dump(mode="json")
            row["conditions"][label] = entry
        assert input_json == (instance.model_dump_json(), plan.model_dump_json(), full.model_dump_json())
        rows.append(row)
        print(json.dumps(dict(case=case, **{
            label: {name: dict(passed=not report["violations"], violations=len(report["violations"]))
                    for name, report in entry["parts"].items()}
            for label, entry in row["conditions"].items()})), flush=True)
    after = source_identity()
    # Record concurrent edits rather than hiding them. Used source hashes can
    # be inspected independently of unrelated rules/stability campaign files.
    changed = sorted(k for k in before["files"] if before["files"][k] != after["files"].get(k))
    campaign_matches = []
    for row in json.loads((ROOT / "experiments/ballast/review_fix/after-final-v2/results.json").read_text()):
        for call in row["calls"]:
            present = [name for name in ("visibility", "lashing") if name in call["parts"]]
            for name in present:
                assert call["parts"][name] == archived[row["case"]]["departure"]["parts"][name]
            campaign_matches.append(dict(case=row["case"], repeat=call["repeat"],
                present_parts=present, archived_initial_match=True))
    lashing_rows = {r["case"]: r for r in json.loads(
        (Path(__file__).parent / "small-stow-lashing.json").read_text())["rows"]}
    visibility_rows = {r["case"]: r for r in json.loads(
        (Path(__file__).parent / "small-stow-visibility.json").read_text())["rows"]}
    planned_ratio_failures = 0
    for row in rows:
        lash, view = lashing_rows[row["case"]], visibility_rows[row["case"]]
        for label, key in (("full", "original_report"), ("background_only", "background_only_report")):
            assert row["conditions"][label]["parts"]["lashing"] == lash[key]
            assert row["conditions"][label]["parts"]["visibility"] == view[label]["public_report"]
        for item in lash["every_violation"]:
            assert item["local_cause"] in ("planned", "background")
            if item["local_cause"] == "background":
                assert item["metric"] == "max_cell_guide_ratio"
            elif item["metric"] != "container_mass_rating":
                comparison = item["same_record_counterfactual_ratios"]
                assert comparison["no_background_mass_gm_only"] > comparison["original"]
                planned_ratio_failures += 1
    assert planned_ratio_failures == 72
    result = dict(diagnostic_only=True, decision_region_empty_not_full_plan_approval=True,
        archived_initial_reports_match=True, input_purity=True,
        frozen_final_campaign_call_matches=campaign_matches,
        independent_attribution_artifacts_match=True,
        planned_ratio_failures_worsen_without_background_gm=planned_ratio_failures,
        wall_s=time.perf_counter()-started, source_before=before, source_after=after,
        changed_sources=changed, rows=rows)
    (Path(__file__).parent / "small-stow-verified.json").write_text(json.dumps(result, indent=2)+"\n")


if __name__ == "__main__":
    main()
