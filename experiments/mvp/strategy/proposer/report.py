"""Compare offline proposer-run artifacts using checked passes and paired maps."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path[:0] = [str(Path(__file__).resolve().parents[4]),
                str(Path(__file__).resolve().parents[4] / "src")]

from experiments.mvp.strategy.proposer.run import atomic_json, prefix_summary
from experiments.mvp.strategy.proposer.registration import collection_requirements, validate_proposal_provenance
from experiments.mvp.strategy.proposer.providers import JsonlProvider
from experiments.mvp.strategy.proposer.verdict import ARMS, MODELS, d185_verdict
from experiments.mvp.strategy.proposer.cost import validate_spark_source_cost, assert_registry_covers


def load(path):
    return json.loads(Path(path).read_text())


def _rule_registration(run):
    from experiments.mvp.strategy.proposer.rule import POLICY_VERSION, SCHEDULE
    policy = run.get("rule_policy")
    if run["config"].get("provider") != "rule" or not isinstance(policy, dict):
        raise ValueError("rule: missing registered shared policy")
    expected = dict(policy_version=POLICY_VERSION, schedule=SCHEDULE,
                    fallback_checks_counted=True, certificate_reuse=False, model_called=False)
    if (set(policy) != set(expected) | {"source_sha256"}
            or any(policy.get(k) != v or type(policy.get(k)) is not type(v) for k, v in expected.items())):
        raise ValueError("rule: incompatible shared policy registration")
    sources = policy["source_sha256"]
    if (not isinstance(sources, dict) or set(sources) != {"rule.py"}
            or any(not isinstance(value, str) or len(value) != 64
                   or any(c not in "0123456789abcdef" for c in value) for value in sources.values())):
        raise ValueError("rule: missing immutable shared policy source identity")
    if run.get("sources", {}).get("experiments/mvp/strategy/blockrule/rule.py") != sources["rule.py"]:
        raise ValueError("rule: policy source identity differs from run source registration")
    return policy


def _verified_attempts(path, job, summary, arm, run, provider=None):
    """Revalidate consumed response provenance rather than trusting a summary flag."""
    rows = summary.get("attempts", [])
    if not rows:
        return rows
    folder = Path(path) / "maps" / job["map_id"]
    prompt_sha256 = None
    prompt = None
    if arm != "search":
        try:
            prompt_bytes = (folder / "prompt.txt").read_bytes()
            prompt_sha256 = hashlib.sha256(prompt_bytes).hexdigest()
            prompt = prompt_bytes.decode("utf-8")
        except OSError as error:
            raise ValueError(f"{arm}/{job['map_id']}: missing collection prompt") from error
    for attempt, row in enumerate(rows, 1):
        if row.get("attempt") != attempt:
            raise ValueError(f"{arm}/{job['map_id']}: attempts must be chronological and contiguous")
        count = row.get("checked_evaluations")
        if type(count) is not int or count not in (0, 1):
            raise ValueError("checked_evaluations must count completed terminal reports (0 or 1)")
        if arm == "search":
            continue
        metadata = row.get("provider", {})
        if "provider_error" in metadata:
            if count or row.get("status") != "provider_error":
                raise ValueError("provider failure cannot supply a checked evaluation")
            continue
        if arm == "rule":
            policy = run["rule_policy"]
            expected = dict(provider="rule", model_called=False,
                policy_version=policy["policy_version"], source_sha256=policy["source_sha256"],
                attempt=attempt, selection=policy["schedule"], fallback_checks_counted=True,
                kind="port-blocking" if attempt % 2 else "default-fallback")
            if any(metadata.get(key) != value or type(metadata.get(key)) is not type(value)
                   for key, value in expected.items()):
                raise ValueError(f"rule/{job['map_id']}: rule proposal provenance mismatch")
            try:
                saved = load(folder / "proposals" / f"proposal-{attempt:03d}.json")
            except (OSError, ValueError) as error:
                raise ValueError(f"rule/{job['map_id']}: missing or invalid proposal artifact") from error
            if (saved.get("metadata") != metadata or saved.get("attempt") != attempt
                    or saved.get("prompt_sha256") != prompt_sha256):
                raise ValueError(f"rule/{job['map_id']}: proposal artifact provenance mismatch")
            from experiments.mvp.strategy.headroom.layout import BlockLayout
            from experiments.mvp.strategy.proposer.format import format_layout
            try:
                layout = BlockLayout.from_dict(metadata.get("layout"))
                canonical_text = format_layout(layout)
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise ValueError(f"rule/{job['map_id']}: invalid recorded rule layout") from error
            if metadata.get("fingerprint") != layout.fingerprint:
                raise ValueError(f"rule/{job['map_id']}: recorded rule layout fingerprint mismatch")
            if saved.get("text") != canonical_text:
                raise ValueError(f"rule/{job['map_id']}: cached text differs from recorded rule layout")
            continue
        validate_proposal_provenance(metadata, arm=arm, attempt=attempt,
            prompt_sha256=prompt_sha256, input_sha256=job["input_sha256"],
            expected_source_sha256=run.get("provider_source_sha256"), map_id=job["map_id"])
        try:
            saved = load(folder / "proposals" / f"proposal-{attempt:03d}.json")
        except (OSError, ValueError) as error:
            raise ValueError(f"{arm}/{job['map_id']}: missing or invalid proposal artifact") from error
        if (saved.get("metadata") != metadata or saved.get("attempt") != attempt
                or saved.get("prompt_sha256") != prompt_sha256):
            raise ValueError(f"{arm}/{job['map_id']}: proposal artifact provenance mismatch")
        try:
            expected = provider.propose(instance=None, map_id=job["map_id"], arm=arm,
                attempt=attempt, prompt=prompt, input_sha256=job["input_sha256"])
        except LookupError as error:
            raise ValueError(f"{arm}/{job['map_id']}: proposal missing from immutable source") from error
        if saved.get("text") != expected.text or metadata != expected.metadata:
            raise ValueError(f"{arm}/{job['map_id']}: proposal differs from immutable source")
    if sum(row["checked_evaluations"] for row in rows) > run["config"]["k"]:
        raise ValueError(f"{arm}/{job['map_id']}: checked evaluations exceed configured budget")
    return rows


def _checked_prefix(rows, budget):
    """Include wasted proposals before the budget-th completed terminal report."""
    result, checked = [], 0
    for row in rows:
        result.append(row)
        checked += row["checked_evaluations"]
        if checked == budget:
            break
    return result, checked == budget


def compare(paths):
    runs = {arm: load(Path(path) / "run.json") for arm, path in paths.items()}
    if set(runs) != set(ARMS):
        raise ValueError("all four arms (spark, glimmer, search, rule) are required")
    reference = runs["spark"]
    for arm, run in runs.items():
        if run.get("protocol") != "D185-step2-v1":
            raise ValueError(f"{arm}: missing D185 protocol registration; archived cohorts are ineligible")
        if run.get("diagnostic_subset") is not False:
            raise ValueError(f"{arm}: diagnostic subsets are ineligible for a comparative model report")
        if run.get("version") != 2 or run.get("budget_unit") != "completed_terminal_certificate_checks":
            raise ValueError(f"{arm}: legacy or incompatible checked-evaluation budget registration")
        if run["config"]["arm"] != arm:
            raise ValueError(f"{arm}: run is labeled {run['config']['arm']}")
        for key in ("maps", "maps_sha256", "sources", "cohort_registration"):
            if key not in run or key not in reference or run[key] != reference[key]:
                raise ValueError(f"{arm}: incompatible {key}")
        if run["config"]["evaluation"] != reference["config"]["evaluation"]:
            raise ValueError(f"{arm}: incompatible evaluation config")
        if run["config"].get("max_proposals") != reference["config"].get("max_proposals"):
            raise ValueError(f"{arm}: incompatible max_proposals cap")
        if type(run["config"].get("max_proposals")) is not int or run["config"]["max_proposals"] < run["config"]["k"]:
            raise ValueError(f"{arm}: invalid max_proposals cap")
    cohort = reference.get("cohort_registration", {})
    if (cohort.get("format") != "D185-proposer-frozen-train-v1"
            or cohort.get("manifest_sha256") != reference["maps_sha256"]
            or cohort.get("frozen_before_proposer_runs") is not True
            or not isinstance(cohort.get("medium_source"), dict) or not cohort["medium_source"]):
        raise ValueError("incompatible D185 frozen current-epoch cohort registration")
    identities = [{key: job.get(key) for key in ("map_id", "input", "input_sha256")}
                  for job in reference["maps"]]
    if not identities or identities != cohort.get("full_maps"):
        raise ValueError("comparison maps differ from the entire frozen cohort registration")
    if any(job.get("release_epoch") != "D185-current-epoch"
           for job in reference["maps"] if job.get("size") == "medium"):
        raise ValueError("archived medium cohort is ineligible for D185")
    for arm in MODELS:
        if runs[arm]["config"].get("provider") != "jsonl":
            raise ValueError(f"{arm}: canned/dry-run arms are ineligible for a model comparison")
    rule_policy = _rule_registration(runs["rule"])
    providers = {}
    spark_budget = None
    for arm in MODELS:
        run = runs[arm]
        expected_sha256 = run.get("provider_source_sha256")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError(f"{arm}: missing immutable provider source identity")
        source = run["config"].get("provider_snapshot") or Path(paths[arm]) / "provider-source.snapshot"
        try:
            providers[arm] = JsonlProvider(source, expected_sha256=expected_sha256)
        except OSError as error:
            raise ValueError(f"{arm}: missing immutable provider snapshot") from error
        if arm == "spark":
            cost = validate_spark_source_cost(Path(source).read_bytes(), expected_sha256=expected_sha256)
            if cost != run.get("spark_cost"):
                raise ValueError("Spark cost metadata differs from immutable full response source")
            registry = run.get("spark_budget")
            if not isinstance(registry, dict):
                raise ValueError("missing registered Spark campaign budget")
            registry_path = registry.get("registry_path")
            if registry_path and Path(registry_path).is_file():
                registry = load(registry_path)
            spark_budget = assert_registry_covers(cost, registry)
    maps = reference["maps"]
    summaries = {}
    for arm, path in paths.items():
        summaries[arm] = {}
        for job in maps:
            target = Path(path) / "maps" / job["map_id"] / "summary.json"
            summary = load(target) if target.is_file() else {}
            if summary:
                for key in ("map_id", "input", "input_sha256"):
                    if summary.get(key) != job.get(key):
                        raise ValueError(f"{arm}/{job['map_id']}: summary {key} mismatch")
                if summary.get("arm") != arm or summary.get("k") != runs[arm]["config"]["k"]:
                    raise ValueError(f"{arm}/{job['map_id']}: summary arm/budget mismatch")
                _verified_attempts(path, job, summary, arm, runs[arm], providers.get(arm))
            summaries[arm][job["map_id"]] = summary
    result = dict(comparison_kind="registered_zero_shot_offline", comparative_eligible=True,
                  arm_provenance={arm: dict(provider=run["config"].get("provider"),
                      provider_source_sha256=run.get("provider_source_sha256"),
                      collection_registration=collection_requirements(arm, 1) if arm in MODELS else None,
                      generation_seed_rule="179 + proposal attempt - 1" if arm in MODELS else None)
                      for arm, run in runs.items()},
                  no_model_calls=all(run.get("no_model_calls") is True for run in runs.values()),
                  rule_policy=rule_policy, spark_budget=spark_budget,
                  protocol=reference["protocol"], cohort_registration=cohort,
                  map_count=len(maps), complete=all(
                      _checked_prefix(summaries[arm][job["map_id"]].get("attempts", []),
                                      runs[arm]["config"]["k"])[1]
                      for arm in runs for job in maps), prefixes={},
                  note="D185 verdict at k=1 or 4 only. Equal completed terminal checked-evaluation budgets; invalid layouts waste proposal tries, not checks. Means use maps where both arms reached the exact checked budget and have a checked pass. Incomplete frozen cohorts cannot supply a merit verdict. V4 internal full-checker work is unmeasured.")
    sizes = sorted({job.get("size", "unknown") for job in maps})
    for prefix in (1, 4, 16):
        supported = all(run["config"]["k"] >= prefix for run in runs.values())
        groups = {}
        for size in ["all", *sizes]:
            jobs = maps if size == "all" else [job for job in maps if job.get("size", "unknown") == size]
            by_arm = {}
            passing = {}
            for arm in runs:
                rows, incomplete, invalid, attempts, checks, terminal_calls, provider_errors = {}, [], 0, 0, 0, 0, 0
                for job in jobs:
                    summary = summaries[arm][job["map_id"]]
                    candidate_rows, reached = _checked_prefix(summary.get("attempts", []), prefix)
                    attempts += len(candidate_rows)
                    invalid += sum(row.get("status") == "invalid_layout" for row in candidate_rows)
                    provider_errors += sum(row.get("status") == "provider_error" for row in candidate_rows)
                    checks += sum(row.get("checked_evaluations", 0) for row in candidate_rows)
                    terminal_calls += sum(row.get("terminal_certificate_check_calls", 0) for row in candidate_rows)
                    if runs[arm]["config"]["k"] < prefix or not reached:
                        incomplete.append(job["map_id"])
                        continue
                    row = prefix_summary(candidate_rows, prefix)[str(prefix)]
                    rows[job["map_id"]] = row
                passing[arm] = {key: row["best_moves"] for key, row in rows.items() if row["passed"]}
                by_arm[arm] = dict(maps=len(jobs), completed_maps=len(rows), passing_maps=len(passing[arm]),
                                   passing_map_moves=passing[arm],
                                   incomplete_maps=incomplete, attempts=attempts, invalid_layouts=invalid,
                                   proposal_tries=attempts, checked_evaluations=checks,
                                   invalid_proposals=invalid, wasted_proposals=attempts - checks,
                                   provider_errors=provider_errors,
                                   terminal_certificate_check_calls=terminal_calls,
                                   all_full_checker_invocations=None, v4_internal_checker_work="unmeasured",
                                   evaluations_used=checks)
            pairs = {}
            for arm in MODELS:
                for baseline in ("search", "rule"):
                    shared = sorted(passing[arm].keys() & passing[baseline].keys())
                    model_values = [passing[arm][key] for key in shared]
                    baseline_values = [passing[baseline][key] for key in shared]
                    pairs[arm + "_vs_" + baseline] = dict(mutual_maps=shared, n=len(shared),
                        fixed_map_denominator=len(jobs), excluded_maps=[job["map_id"] for job in jobs if job["map_id"] not in shared],
                        model_mean=sum(model_values) / len(shared) if shared else None,
                        baseline_mean=sum(baseline_values) / len(shared) if shared else None,
                        **{baseline + "_mean": sum(baseline_values) / len(shared) if shared else None},
                        delta_mean=sum(a - b for a, b in zip(model_values, baseline_values)) / len(shared) if shared else None,
                        fewer=sum(a < b for a, b in zip(model_values, baseline_values)),
                        equal=sum(a == b for a, b in zip(model_values, baseline_values)),
                        more=sum(a > b for a, b in zip(model_values, baseline_values)))
            groups[size] = dict(arms=by_arm, paired=pairs)
        result["prefixes"][str(prefix)] = dict(supported_by_all_budgets=supported, sizes=groups)
    result["verdict"] = d185_verdict(result["prefixes"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for arm in ARMS:
        parser.add_argument("--" + arm, required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = compare({arm: getattr(args, arm) for arm in ARMS})
    if args.output:
        from experiments.mvp.strategy.proposer.run import HERE
        args.output.resolve().relative_to(HERE)
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
