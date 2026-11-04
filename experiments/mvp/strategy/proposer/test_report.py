"""Offline report provenance, smoke exclusion and equal-checked-budget regressions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from experiments.mvp.strategy.proposer.registration import collection_requirements
from experiments.mvp.strategy.proposer.report import compare
from experiments.mvp.strategy.proposer.run import atomic_json
from experiments.mvp.strategy.proposer.cost import validate_spark_source_cost, register_spark_pool
from experiments.mvp.strategy.proposer.rule import POLICY_VERSION, SCHEDULE
from experiments.mvp.strategy.headroom.layout import BlockLayout
from experiments.mvp.strategy.proposer.format import format_layout


PROMPT = "registered public block summary\n"
PROMPT_SHA = hashlib.sha256(PROMPT.encode()).hexdigest()
INPUT_SHA = hashlib.sha256(b"train instance").hexdigest()
RULE_SHA = hashlib.sha256(b"shared synthetic blockrule source").hexdigest()
RULE_POLICY = dict(policy_version=POLICY_VERSION, source_sha256={"rule.py": RULE_SHA},
                   schedule=SCHEDULE, fallback_checks_counted=True,
                   certificate_reuse=False, model_called=False)
COHORT = dict(format="D185-proposer-frozen-train-v1", manifest_sha256="maps",
              medium_source=dict(path="frozen-current-medium.json", sha256=INPUT_SHA),
              frozen_before_proposer_runs=True)
RULE_LAYOUT = BlockLayout({("P1", "light"): frozenset({"HC-1/hold"})}, "synthetic-rule")


def checked(moves):
    return dict(valid_layout=True, checked_evaluations=1,
                terminal_certificate_check_calls=1, check={"passed": True, "moves": moves})


def invalid():
    return dict(valid_layout=False, status="invalid_layout", checked_evaluations=0,
                terminal_certificate_check_calls=0)


def fixture(tmp_path, *, k=1, attempts=None, missing_medium=True):
    jobs = [dict(map_id="a", input="train/a.json", input_sha256=INPUT_SHA, size="small")]
    if missing_medium:
        jobs.append(dict(map_id="b", input="train/b.json", input_sha256=INPUT_SHA, size="medium",
                         release_epoch="D185-current-epoch"))
    paths = {}
    for arm, moves in (("spark", 10), ("glimmer", 14), ("search", 12), ("rule", 11)):
        folder = tmp_path / arm
        paths[arm] = folder
        rows = ([deepcopy(row) for row in attempts.get(arm, [checked(moves)] * k)]
                if attempts is not None else [checked(moves)])
        imported = []
        for attempt in range(1, len(rows) + 1):
            provenance = dict(collection_requirements(arm, attempt),
                              prompt_sha256=PROMPT_SHA, input_sha256=INPUT_SHA) if arm in {"spark", "glimmer"} else None
            imported.append(dict(map_id="a", arm=arm, attempt=attempt, text="LAYOUT 1",
                                 collection_provenance=provenance))
            if arm == "spark":
                imported[-1]["usage"] = {"billed_cost_usd": 0.0001, "billed_cost_includes_all_tokens": True}
        source_bytes = ("\n".join(json.dumps(row, sort_keys=True) for row in imported) + "\n").encode()
        source_sha = hashlib.sha256(source_bytes).hexdigest() if arm in {"spark", "glimmer"} else None
        source = folder / "provider-source.snapshot"
        folder.mkdir(parents=True)
        if arm in {"spark", "glimmer"}:
            source.write_bytes(source_bytes)
        spark_cost = validate_spark_source_cost(source_bytes) if arm == "spark" else None
        spark_budget = register_spark_pool(spark_cost, tmp_path / "spark-campaign.json") if spark_cost else None
        atomic_json(folder / "run.json", dict(version=2, protocol="D185-step2-v1", diagnostic_subset=False,
            budget_unit="completed_terminal_certificate_checks", config=dict(arm=arm, k=k, max_proposals=256,
            provider="jsonl" if arm in {"spark", "glimmer"} else arm, evaluation={"seed": 0},
            provider_snapshot=str(source.resolve()) if arm in {"spark", "glimmer"} else None),
            maps=jobs, maps_sha256="maps", sources={"code": "sha",
                "experiments/mvp/strategy/blockrule/rule.py": RULE_SHA},
            cohort_registration=dict(COHORT, full_maps=[{key: job[key] for key in
                ("map_id", "input", "input_sha256")} for job in jobs]),
            rule_policy=RULE_POLICY if arm == "rule" else None,
            spark_cost=spark_cost, spark_budget=spark_budget,
            provider_source_sha256=source_sha, no_model_calls=True))
        map_folder = folder / "maps" / "a"
        map_folder.mkdir(parents=True)
        (map_folder / "prompt.txt").write_text(PROMPT)
        for attempt, row in enumerate(rows, 1):
            row["attempt"] = attempt
            metadata = {"provider": arm}
            if arm == "rule":
                metadata = dict(provider="rule", policy_version=POLICY_VERSION,
                    source_sha256={"rule.py": RULE_SHA}, model_called=False,
                    attempt=attempt, selection=SCHEDULE, fallback_checks_counted=True,
                    layout=RULE_LAYOUT.to_dict(), fingerprint=RULE_LAYOUT.fingerprint,
                    kind="port-blocking" if attempt % 2 else "default-fallback")
            if arm in {"spark", "glimmer"}:
                source_row = {key: value for key, value in imported[attempt - 1].items() if key != "text"}
                metadata = dict(provider="jsonl", source_sha256=source_sha,
                    collection_provenance=source_row["collection_provenance"], model_called=False,
                    source_row=source_row)
            row["provider"] = metadata
            atomic_json(map_folder / "proposals" / f"proposal-{attempt:03d}.json",
                dict(text=format_layout(RULE_LAYOUT) if arm == "rule" else "LAYOUT 1",
                     metadata=metadata, attempt=attempt, prompt_sha256=PROMPT_SHA))
        atomic_json(map_folder / "summary.json", dict(**jobs[0], arm=arm, k=k,
            complete=sum(r["checked_evaluations"] for r in rows) == k, attempts=rows))
    return paths


def alter_summary(paths, arm, change):
    path = paths[arm] / "maps" / "a" / "summary.json"
    summary = json.loads(path.read_text())
    change(summary)
    atomic_json(path, summary)


def test_registered_jsonl_comparison_pairs_maps_and_preserves_collection_marker(tmp_path):
    report = compare(fixture(tmp_path))
    assert report["complete"] is False
    assert report["comparison_kind"] == "registered_zero_shot_offline"
    assert report["comparative_eligible"] is True
    assert len(report["arm_provenance"]["spark"]["provider_source_sha256"]) == 64
    small = report["prefixes"]["1"]["sizes"]["small"]
    assert small["paired"]["spark_vs_search"]["delta_mean"] == -2
    assert small["paired"]["glimmer_vs_search"]["delta_mean"] == 2
    assert small["paired"]["spark_vs_rule"]["delta_mean"] == -1
    assert small["arms"]["rule"]["passing_map_moves"] == {"a": 11}
    assert report["verdict"]["status"] == "INCONCLUSIVE"
    medium = report["prefixes"]["1"]["sizes"]["medium"]
    assert medium["arms"]["spark"]["incomplete_maps"] == ["b"]
    assert medium["paired"]["spark_vs_search"]["fixed_map_denominator"] == 1
    assert medium["paired"]["spark_vs_search"]["n"] == 0
    assert report["prefixes"]["4"]["supported_by_all_budgets"] is False


@pytest.mark.parametrize("provider", ["dry-run", None])
def test_canned_model_arm_cannot_produce_model_comparison(tmp_path, provider):
    paths = fixture(tmp_path)
    path = paths["spark"] / "run.json"
    run = json.loads(path.read_text())
    run["config"]["provider"] = provider
    atomic_json(path, run)
    with pytest.raises(ValueError, match="canned/dry-run"):
        compare(paths)


def test_canned_row_rejected_even_when_run_claims_jsonl(tmp_path):
    paths = fixture(tmp_path)
    alter_summary(paths, "spark", lambda summary: summary["attempts"][0].update(
        provider={"provider": "dry-run", "canned": "unrestricted"}))
    with pytest.raises(ValueError, match="jsonl"):
        compare(paths)


@pytest.mark.parametrize("field,value", [
    ("prompt_sha256", "0" * 64), ("input_sha256", "0" * 64),
    ("model", "fine-tuned-glimmer"), ("adapter", "target-trained"),
    ("feedback", "checker-assisted"), ("base_model", False),
    ("independent", False), ("zero_shot", False), ("seed", 999),
    ("generation_settings", {"temperature": 0}),
])
def test_report_rejects_contradictory_collection_provenance(tmp_path, field, value):
    paths = fixture(tmp_path)
    def tamper(summary):
        summary["attempts"][0]["provider"]["collection_provenance"][field] = value
    alter_summary(paths, "glimmer", tamper)
    with pytest.raises(ValueError, match="collection provenance"):
        compare(paths)


def test_report_rejects_missing_collection_provenance(tmp_path):
    paths = fixture(tmp_path)
    alter_summary(paths, "spark", lambda summary:
        summary["attempts"][0]["provider"].pop("collection_provenance"))
    with pytest.raises(ValueError, match="collection_provenance"):
        compare(paths)


def test_report_rejects_mutated_response_source_digest(tmp_path):
    paths = fixture(tmp_path)
    alter_summary(paths, "spark", lambda summary:
        summary["attempts"][0]["provider"].update(source_sha256="0" * 64))
    with pytest.raises(ValueError, match="source_sha256"):
        compare(paths)


def test_report_rejects_saved_prompt_drift(tmp_path):
    paths = fixture(tmp_path)
    (paths["spark"] / "maps" / "a" / "prompt.txt").write_text("different prompt")
    with pytest.raises(ValueError, match="prompt_sha256"):
        compare(paths)


def test_report_rejects_saved_proposal_metadata_drift(tmp_path):
    paths = fixture(tmp_path)
    path = paths["spark"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["metadata"]["source_sha256"] = "0" * 64
    atomic_json(path, saved)
    with pytest.raises(ValueError, match="artifact provenance"):
        compare(paths)


def test_report_rejects_imported_response_relabeled_to_another_map(tmp_path):
    paths = fixture(tmp_path)
    alter_summary(paths, "spark", lambda summary:
        summary["attempts"][0]["provider"]["source_row"].update(map_id="another-map"))
    with pytest.raises(ValueError, match="source_row collection identity"):
        compare(paths)


def test_invalid_proposals_do_not_finish_checked_budget_or_hide_later_check(tmp_path):
    attempts = {"spark": [invalid(), checked(10)], "glimmer": [checked(14)], "search": [checked(12)]}
    report = compare(fixture(tmp_path, attempts=attempts, missing_medium=False))
    spark = report["prefixes"]["1"]["sizes"]["all"]["arms"]["spark"]
    assert report["complete"] is True
    assert spark["proposal_tries"] == 2
    assert spark["checked_evaluations"] == 1
    assert spark["evaluations_used"] == 1
    assert spark["invalid_layouts"] == 1
    assert spark["invalid_proposals"] == spark["wasted_proposals"] == 1
    assert spark["completed_maps"] == 1
    assert spark["terminal_certificate_check_calls"] == 1
    assert spark["all_full_checker_invocations"] is None
    assert spark["v4_internal_checker_work"] == "unmeasured"
    assert "actual_full_checker_calls" not in json.dumps(report)
    assert report["prefixes"]["1"]["sizes"]["all"]["paired"]["spark_vs_search"]["delta_mean"] == -2


def test_incomplete_checked_budget_has_no_unequal_count_ranking(tmp_path):
    attempts = {"spark": [checked(1), invalid(), invalid(), invalid()],
                "glimmer": [checked(14)] * 4, "search": [checked(12)] * 4}
    report = compare(fixture(tmp_path, k=4, attempts=attempts, missing_medium=False))
    group = report["prefixes"]["4"]["sizes"]["all"]
    assert report["complete"] is False
    assert group["arms"]["spark"]["completed_maps"] == 0
    assert group["arms"]["spark"]["incomplete_maps"] == ["a"]
    pair = group["paired"]["spark_vs_search"]
    assert pair["n"] == 0
    assert pair["delta_mean"] is None
    assert pair["fixed_map_denominator"] == 1
    assert pair["excluded_maps"] == ["a"]
    assert group["paired"]["glimmer_vs_search"]["n"] == 1


def test_failed_full_check_spends_one_checked_evaluation(tmp_path):
    failure = dict(valid_layout=True, checked_evaluations=1,
                   terminal_certificate_check_calls=1, check={"passed": False, "moves": 2})
    attempts = {"spark": [failure], "glimmer": [checked(14)], "search": [checked(12)]}
    report = compare(fixture(tmp_path, attempts=attempts, missing_medium=False))
    group = report["prefixes"]["1"]["sizes"]["all"]
    assert group["arms"]["spark"]["proposal_tries"] == 1
    assert group["arms"]["spark"]["completed_maps"] == 1
    assert group["arms"]["spark"]["passing_maps"] == 0
    assert group["paired"]["spark_vs_search"]["n"] == 0


@pytest.mark.parametrize("field", ["sources", "maps_sha256", "maps", "evaluation"])
def test_comparison_refuses_incompatible_runs(tmp_path, field):
    paths = fixture(tmp_path)
    path = paths["glimmer"] / "run.json"
    run = json.loads(path.read_text())
    if field == "evaluation":
        run["config"][field] = {"seed": 1}
    else:
        run[field] = "changed"
    atomic_json(path, run)
    with pytest.raises(ValueError, match="incompatible"):
        compare(paths)


@pytest.mark.parametrize("field,value", [("version", 1), ("budget_unit", "proposal_slots"),
                                         ("budget_unit", None)])
def test_legacy_proposal_slot_budget_cannot_enter_checked_comparison(tmp_path, field, value):
    paths = fixture(tmp_path)
    path = paths["spark"] / "run.json"
    run = json.loads(path.read_text())
    run[field] = value
    atomic_json(path, run)
    with pytest.raises(ValueError, match="budget registration"):
        compare(paths)


def test_unequal_proposal_caps_cannot_enter_comparison(tmp_path):
    paths = fixture(tmp_path)
    path = paths["glimmer"] / "run.json"
    run = json.loads(path.read_text())
    run["config"]["max_proposals"] = 128
    atomic_json(path, run)
    with pytest.raises(ValueError, match="max_proposals cap"):
        compare(paths)


def test_checked_evaluation_overspend_is_rejected_instead_of_truncated(tmp_path):
    attempts = {"spark": [checked(10), checked(1)], "glimmer": [checked(14)], "search": [checked(12)]}
    with pytest.raises(ValueError, match="exceed configured budget"):
        compare(fixture(tmp_path, attempts=attempts, missing_medium=False))


def test_provider_errors_are_wasted_proposals_separate_from_invalid_layouts(tmp_path):
    attempts = {"spark": [invalid(), checked(10)], "glimmer": [checked(14)], "search": [checked(12)]}
    paths = fixture(tmp_path, attempts=attempts, missing_medium=False)
    def provider_failure(summary):
        summary["attempts"][0].update(status="provider_error", provider={"provider_error": "missing response"})
    alter_summary(paths, "spark", provider_failure)
    group = compare(paths)["prefixes"]["1"]["sizes"]["all"]["arms"]["spark"]
    assert group["wasted_proposals"] == group["provider_errors"] == 1
    assert group["invalid_proposals"] == group["invalid_layouts"] == 0
    assert group["checked_evaluations"] == 1


def test_report_rejects_changed_cached_text_bound_to_immutable_source(tmp_path):
    paths = fixture(tmp_path)
    path = paths["spark"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["text"] = "different cached response"
    atomic_json(path, saved)
    with pytest.raises(ValueError, match="differs from immutable source"):
        compare(paths)


def test_report_rejects_changed_snapshot_bytes(tmp_path):
    paths = fixture(tmp_path)
    path = paths["spark"] / "provider-source.snapshot"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source changed"):
        compare(paths)


def test_report_reconstructs_metadata_from_source_instead_of_trusting_matching_caches(tmp_path):
    paths = fixture(tmp_path)
    proposal_path = paths["spark"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(proposal_path.read_text())
    saved["metadata"]["source_row"]["usage"] = {"output_tokens": 999}
    atomic_json(proposal_path, saved)
    alter_summary(paths, "spark", lambda summary: summary["attempts"][0].update(provider=saved["metadata"]))
    with pytest.raises(ValueError, match="differs from immutable source"):
        compare(paths)


def test_report_uses_default_run_snapshot_if_no_explicit_snapshot_path(tmp_path):
    paths = fixture(tmp_path)
    for arm in ("spark", "glimmer"):
        path = paths[arm] / "run.json"
        run = json.loads(path.read_text())
        run["config"].pop("provider_snapshot")
        atomic_json(path, run)
    assert compare(paths)["comparative_eligible"] is True


def alter_run(paths, arm, change):
    path = paths[arm] / "run.json"
    run = json.loads(path.read_text())
    change(run)
    atomic_json(path, run)


def test_rule_arm_is_required(tmp_path):
    paths = fixture(tmp_path)
    del paths["rule"]
    with pytest.raises(ValueError, match="all four arms"):
        compare(paths)


def test_report_cli_requires_rule_arm():
    from experiments.mvp.strategy.proposer.report import main
    with pytest.raises(SystemExit) as error:
        main(["--spark", "unused", "--glimmer", "unused", "--search", "unused"])
    assert error.value.code == 2


@pytest.mark.parametrize("field,value", [("provider", "search"), ("model_called", True),
    ("source_sha256", {"rule.py": "0" * 64}), ("policy_version", "unregistered"),
    ("attempt", 2), ("selection", "post-hoc"), ("fallback_checks_counted", False),
    ("kind", "default-fallback")])
def test_rule_row_must_match_pinned_shared_policy(tmp_path, field, value):
    paths = fixture(tmp_path)
    alter_summary(paths, "rule", lambda summary: summary["attempts"][0]["provider"].update({field: value}))
    with pytest.raises(ValueError, match="rule proposal provenance"):
        compare(paths)


def test_rule_provider_label_cannot_claim_model_or_canned_source(tmp_path):
    paths = fixture(tmp_path)
    alter_run(paths, "rule", lambda run: run["config"].update(provider="dry-run"))
    with pytest.raises(ValueError, match="registered shared policy"):
        compare(paths)


def test_rule_source_pin_must_match_run_code_identity(tmp_path):
    paths = fixture(tmp_path)
    alter_run(paths, "rule", lambda run: run["rule_policy"].update(source_sha256={"rule.py": "0" * 64}))
    with pytest.raises(ValueError, match="source identity differs"):
        compare(paths)


def test_rule_cached_proposal_metadata_must_match_summary(tmp_path):
    paths = fixture(tmp_path)
    path = paths["rule"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["metadata"]["kind"] = "default-fallback"
    atomic_json(path, saved)
    with pytest.raises(ValueError, match="artifact provenance"):
        compare(paths)


def test_rule_cached_text_is_bound_to_recorded_layout(tmp_path):
    paths = fixture(tmp_path)
    path = paths["rule"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["text"] = "LAYOUT 1\nP1/light = HC-2/hold\n"
    atomic_json(path, saved)
    with pytest.raises(ValueError, match="cached text differs from recorded rule layout"):
        compare(paths)


def test_rule_changed_layout_cannot_reuse_saved_fingerprint(tmp_path):
    paths = fixture(tmp_path)
    path = paths["rule"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["metadata"]["layout"]["allowed"][0]["blocks"] = ["HC-2/hold"]
    atomic_json(path, saved)
    alter_summary(paths, "rule", lambda summary: summary["attempts"][0].update(provider=saved["metadata"]))
    with pytest.raises(ValueError, match="layout fingerprint mismatch"):
        compare(paths)


def test_rule_fingerprint_is_recomputed_instead_of_trusting_matching_caches(tmp_path):
    paths = fixture(tmp_path)
    path = paths["rule"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["metadata"]["fingerprint"] = "0" * 20
    atomic_json(path, saved)
    alter_summary(paths, "rule", lambda summary: summary["attempts"][0].update(provider=saved["metadata"]))
    with pytest.raises(ValueError, match="layout fingerprint mismatch"):
        compare(paths)


def test_rule_missing_recorded_layout_fails_closed(tmp_path):
    paths = fixture(tmp_path)
    path = paths["rule"] / "maps" / "a" / "proposals" / "proposal-001.json"
    saved = json.loads(path.read_text())
    saved["metadata"].pop("layout")
    atomic_json(path, saved)
    alter_summary(paths, "rule", lambda summary: summary["attempts"][0].update(provider=saved["metadata"]))
    with pytest.raises(ValueError, match="invalid recorded rule layout"):
        compare(paths)


@pytest.mark.parametrize("arm", ["spark", "glimmer", "search", "rule"])
@pytest.mark.parametrize("flag", [True, None])
def test_diagnostic_subset_cannot_supply_comparative_model_report(tmp_path, arm, flag):
    paths = fixture(tmp_path)
    alter_run(paths, arm, lambda run: run.update(diagnostic_subset=flag))
    with pytest.raises(ValueError, match="diagnostic subsets are ineligible"):
        compare(paths)


def test_subset_cannot_claim_full_cohort_by_switching_off_diagnostic_flag(tmp_path):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run.update(maps=run["maps"][:1], diagnostic_subset=False))
    with pytest.raises(ValueError, match="entire frozen cohort"):
        compare(paths)


def test_reordered_maps_cannot_claim_the_original_frozen_cohort(tmp_path):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run["maps"].reverse())
    with pytest.raises(ValueError, match="entire frozen cohort"):
        compare(paths)


def test_missing_full_cohort_registration_fails_closed(tmp_path):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run["cohort_registration"].pop("full_maps"))
    with pytest.raises(ValueError, match="entire frozen cohort"):
        compare(paths)


@pytest.mark.parametrize("field", ["input", "input_sha256"])
def test_full_cohort_identity_binds_paths_and_input_bytes(tmp_path, field):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run["cohort_registration"]["full_maps"][0].update({field: "changed"}))
    with pytest.raises(ValueError, match="entire frozen cohort"):
        compare(paths)


@pytest.mark.parametrize("arm", ["spark", "glimmer", "search", "rule"])
def test_legacy_protocol_cannot_enter_revised_d185_comparison(tmp_path, arm):
    paths = fixture(tmp_path)
    alter_run(paths, arm, lambda run: run.pop("protocol"))
    with pytest.raises(ValueError, match="D185 protocol"):
        compare(paths)


def test_archived_medium_map_is_ineligible_even_with_d185_run_label(tmp_path):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run["maps"][1].pop("release_epoch"))
    with pytest.raises(ValueError, match="archived medium"):
        compare(paths)


def test_missing_cohort_registration_fails_closed(tmp_path):
    paths = fixture(tmp_path)
    alter_run(paths, "spark", lambda run: run.pop("cohort_registration"))
    with pytest.raises(ValueError, match="incompatible cohort_registration"):
        compare(paths)


@pytest.mark.parametrize("field,value", [("format", "legacy"),
    ("manifest_sha256", "different"), ("frozen_before_proposer_runs", False),
    ("medium_source", {})])
def test_cohort_must_be_frozen_before_running_and_bound_to_source(tmp_path, field, value):
    paths = fixture(tmp_path)
    for arm in paths:
        alter_run(paths, arm, lambda run: run["cohort_registration"].update({field: value}))
    with pytest.raises(ValueError, match="frozen current-epoch cohort"):
        compare(paths)


def test_report_recomputes_full_source_spark_cost(tmp_path):
    paths = fixture(tmp_path)
    alter_run(paths, "spark", lambda run: run["spark_cost"].update(total_cost_usd=0))
    with pytest.raises(ValueError, match="cost metadata differs"):
        compare(paths)


def test_report_requires_registered_spark_campaign(tmp_path):
    paths = fixture(tmp_path)
    alter_run(paths, "spark", lambda run: run.pop("spark_budget"))
    with pytest.raises(ValueError, match="registered Spark campaign"):
        compare(paths)


def test_report_refreshes_campaign_instead_of_trusting_old_run_snapshot(tmp_path):
    paths = fixture(tmp_path)
    extra = json.dumps(dict(map_id="unused", arm="spark", attempt=1, text="invalid",
        usage={"billed_cost_usd": 1.5, "billed_cost_includes_all_tokens": True})).encode()
    register_spark_pool(validate_spark_source_cost(extra), tmp_path / "spark-campaign.json")
    report = compare(paths)
    assert report["spark_budget"]["campaign_total_cost_usd"] == 1.5001
    assert report["spark_budget"]["unique_pools"] == 2


def test_report_rejects_campaign_that_became_over_budget(tmp_path):
    paths = fixture(tmp_path)
    registry_path = tmp_path / "spark-campaign.json"
    registry = json.loads(registry_path.read_text())
    extra = json.dumps(dict(map_id="unused", arm="spark", attempt=1, text="invalid",
        usage={"billed_cost_usd": 1.9999, "billed_cost_includes_all_tokens": True})).encode()
    extra_cost = validate_spark_source_cost(extra)
    registry["pools"][extra_cost["source_sha256"]] = extra_cost
    atomic_json(registry_path, registry)
    with pytest.raises(ValueError, match="campaign total"):
        compare(paths)
