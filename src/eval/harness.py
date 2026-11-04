"""Reproducible evaluations against frozen, pre-solve splits (task 5.2).

The full checker decides whether a plan can ship. Failed plans remain in the
records and denominators: dropping them would reward a planner for difficult
instances it could not solve. Input hashes and configuration pin each resumable
shard. Split assignment uses only generation inputs, following the leakage
prevention principle in https://scikit-learn.org/stable/common_pitfalls.html .
CPU instances use a process pool; planners with ``plan_batch`` may instead use
one process and batch generation, followed by the identical per-plan pipeline.
The caller owns compute admission and the fleet ledger before using >1 core.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import importlib
import json
import math
import multiprocessing
from pathlib import Path
from time import perf_counter

from eval.splits import DEFAULT_MANIFEST, load_inventory
from eval.snapshot_io import read_snapshot
from stowage.conventions import canonical_vessel_id
from eval.harness_integrity import (pin_weight_content, validate_reference,
                                    validate_reference_run, report_has_current_versions)
from stowage.bounds import lower_bound
from stowage.frozen import active_stubs, current_provenance, require_clean
from stowage.model import PlannerResult, Provenance, Violation
from stowage.planner import run_planner

HARNESS_VERSION = "2.10"
_INDIVIDUAL = object()


class _SelectedRows(list):
    """Available inputs with the complete frozen allocation kept alongside."""

    def __init__(self, rows, allocated_rows):
        super().__init__(rows)
        self.allocated_rows = allocated_rows


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _configuration(value):
    """Pin all planner options, failing explicitly on opaque runtime state.

    GPU planners may expose evaluation_config with their model/adapter identity
    instead of serialising loaded tensors and runtime handles.
    """
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _configuration(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_configuration(v) for v in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    raise TypeError("planner configuration contains opaque state; expose JSON evaluation_config")


def _sha(path):
    return read_snapshot(path).sha256


def _write(path, value):
    """Atomic checkpoints survive interruption without accepting partial JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(value), sort_keys=True, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _json_safe(value):
    """Encode unbounded checker margins as strings, retaining standard JSON.

    Pydantic restores these numeric strings to floats when validating a report.
    Aggregates treat nonfinite diagnostics as missing, never as objective values.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _feasibility_status(result, *, witnessed):
    """A failed stow or an engine-model proof cannot prove the physical instance infeasible."""
    if witnessed:
        return "feasible-witnessed"
    metrics = result.metrics
    if (result.status == "infeasible" and metrics.get("ballast_status") != "infeasible_proven"
            and metrics.get("infeasibility_proven") is True
            and metrics.get("infeasibility_scope") in ("physical_instance", "physical_relaxation")):
        return "proven-infeasible"
    return "unknown"


def select_instances(instances_dir=None, *, split="val", rungs=("tiny", "small"),
                     limits=None, split_manifest=DEFAULT_MANIFEST, regen_manifest=None, tiny_run=False,
                     spec_keys=None):
    """Select an available subset of an explicitly pinned frozen inventory.

    The instance root is always explicit; the split release defaults to v2.
    Epoch roots enforce regeneration provenance; tiny_run allows diagnostic input.
    Legacy campaigns require an explicit historical release; policy-consistent
    nonmembers are never admitted.
    """
    from eval.harness_inventory import select_frozen_instances
    selection = select_frozen_instances(instances_dir, split_manifest=split_manifest,
                                       split=split, rungs=rungs, limits=limits,
                                       regen_manifest=regen_manifest, allow_diagnostic=tiny_run,
                                       spec_keys=spec_keys)
    return _SelectedRows([{**row, "inventory_allocated_count": selection.allocated_count,
                          "inventory_available_count": selection.available_count}
                         for row in selection.rows], selection.allocated_rows)


def _load_selected(row, tiny_run):
    if row.get("regen_manifest_path"):
        manifest_path = Path(row["regen_manifest_path"])
        manifest_snapshot = read_snapshot(manifest_path)
        if manifest_snapshot.sha256 != row["regen_manifest_sha256"]:
            raise ValueError("epoch regeneration manifest changed during evaluation")
        from eval.splits_epoch import validate_numerical_cache_snapshot
        frozen = manifest_snapshot.json()["freeze_manifest"]
        validate_numerical_cache_snapshot(frozen)
        if not tiny_run:
            from eval.splits_epoch import validate_active_numerical_cache_routes
            validate_active_numerical_cache_routes(frozen)
    from eval.splits_epoch import instance_matches_spec, load_bound_instance
    instance = load_bound_instance(row["path"], row["input_sha256"])
    if not instance_matches_spec(instance, row["spec"]):
        raise ValueError("instance identity disagrees with frozen split specification")
    if not tiny_run:
        require_clean(instance, current_provenance(), what="official evaluation input")
    return instance


def _clean_result(result, report, provenance):
    require_clean(result, provenance, *([] if report is None else [report]),
                  *result.later_plans, what="official evaluation result")


def _verify_publication_snapshot(rows, *, tiny_run, inventory, instances_dir, versions, allocation_rows=()):
    """Re-admit all selected inputs after work, immediately before publication.

    A planner, checker, bound computation, or another process can change an
    input after preflight. Validate each epoch once per publication, rather than
    scanning the same numerical cache separately for every selected instance.
    """
    if tiny_run:
        return
    require_clean(current_provenance(), what="official evaluation publication")
    if current_provenance().versions != versions:
        raise ValueError("component versions changed during official evaluation")
    checked = set()
    for row in allocation_rows:
        name = row.get("campaign_manifest_path")
        if name and name not in checked:
            read_snapshot(name, expected_sha256=row["campaign_manifest_sha256"])
            checked.add(name)
        if row.get("rejection_path"):
            read_snapshot(row["rejection_path"], expected_sha256=row["rejection_sha256"])
        epoch_name = row.get("regen_manifest_path")
        if epoch_name and (epoch_name, row["regen_manifest_sha256"]) not in checked:
            from eval.splits_epoch import (validate_epoch_manifest,
                                           validate_active_numerical_cache_routes)
            manifest = read_snapshot(epoch_name, expected_sha256=row["regen_manifest_sha256"]).json()
            validate_epoch_manifest(instances_dir, manifest, inventory)
            validate_active_numerical_cache_routes(manifest["freeze_manifest"])
            checked.add((epoch_name, row["regen_manifest_sha256"]))
    for row in rows:
        name = row.get("regen_manifest_path")
        if not name:
            # Explicit historical releases have no epoch snapshot, but their
            # selected input content and identity still need a final check.
            _load_selected(row, tiny_run)
            continue
        identity = (name, row["regen_manifest_sha256"])
        if identity not in checked:
            manifest_snapshot = read_snapshot(name)
            if manifest_snapshot.sha256 != row["regen_manifest_sha256"]:
                raise ValueError("epoch regeneration manifest changed during evaluation")
            from eval.splits_epoch import (validate_epoch_manifest,
                                           validate_active_numerical_cache_routes)
            manifest = manifest_snapshot.json()
            validate_epoch_manifest(instances_dir, manifest, inventory)
            validate_active_numerical_cache_routes(manifest["freeze_manifest"])
            checked.add(identity)
        if _sha(row["path"]) != row["input_sha256"]:
            raise ValueError(f"instance checksum mismatch: {row['path']}")


def _evaluate(planner, row, seed, time_limit_s, tiny_run, reference, precomputed=_INDIVIDUAL):
    instance = _load_selected(row, tiny_run)
    start = perf_counter()
    if precomputed is not _INDIVIDUAL and not isinstance(precomputed, PlannerResult):
        raise TypeError("precomputed batch output must be a PlannerResult")
    used_planner = planner if precomputed is _INDIVIDUAL else _PrecomputedPlanner(planner, precomputed)
    from eval.sample_reporting import is_spark_record, sample_reporting
    from eval.mask_observation import observe_mask_diagnostics
    with observe_mask_diagnostics() as mask_diagnostics:
        result, report = run_planner(used_planner, instance, seed=seed, time_limit_s=time_limit_s,
                                    **({'allow_repair': False} if is_spark_record({}, planner) else {}))
    # Every new evaluation revokes any previous authorization, even when the
    # pipeline returns the same already-checked object after a failure.
    result._shipping_evidence = None
    result.metrics["shippable"] = False
    checker_attempts = {"retries": 0}
    from llm.rl.checker_retry import check_with_retry, report_timed_out
    if report is not None and result.plan is not None and report_timed_out(report):
        from stowage.checker import check
        attempts = check_with_retry(instance, result.plan, checker=check, initial_report=report)
        report = attempts.report
        checker_attempts = {"retries": attempts.retries,
            "initial_report": attempts.initial_report.model_dump(mode="json"),
            "retry_report": None if attempts.retry_report is None else attempts.retry_report.model_dump(mode="json"),
            "retry_error": attempts.retry_error, "retry_budget_s": attempts.retry_budget_s}
    total_s = perf_counter() - start
    from stowage.checker import report_matches_inputs
    binding_valid = bool(report is not None and result.plan is not None
                         and report_matches_inputs(report, instance, result.plan)
                         and (tiny_run or report_has_current_versions(report)))
    if report is not None and report.passed and not binding_valid:
        # Keep detached evidence as a failed attempt, preserving diagnostics.
        report = report.model_copy(update={"passed": False, "violations": [
            *report.violations, Violation(rule_id="checker.report_binding", severity="hard",
                call_index=instance.call_index,
                message="Checker report lacks current component versions or evaluated input binding.")]})
    if report is not None:
        result._record_full_check(instance, report)
    result.metrics["shippable"] = binding_valid and result.shippable
    bound = lower_bound(instance)
    provenance = current_provenance(created_by="eval.harness", seed=seed).merge(
        instance.provenance, result.provenance,
        *([] if result.plan is None else [result.plan.provenance]),
        *([] if report is None else [report.provenance]), parent_ids=[instance.id])
    if not tiny_run:
        _clean_result(result, report, provenance)
    vessel_class = canonical_vessel_id(instance.vessel.slice_of or instance.vessel.id).split(".", 1)[0]
    from stowage.vessel.fleet import list_fleet, load_ship_params
    fleet_teu = (load_ship_params(vessel_class)["nominal_teu"]
                 if vessel_class in list_fleet() else instance.vessel.nominal_teu)
    record = {"official": not tiny_run, "tiny_run": tiny_run, "diagnostic": tiny_run,
            "evidence_eligible": not tiny_run, 'planner': planner.name,
            "instance_id": instance.id, "spec_key": row["spec_key"], "split": row["split"],
            "rung": row["rung"], "input_sha256": row["input_sha256"],
            "test_family": row.get("test_family", row.get("spec", {}).get("family", "unknown")),
            **({'dg_stress_pair': row['spec']['dg_stress_pair']}
               if 'dg_stress_pair' in row.get('spec', {}) else {}),
            "split_version": row.get("split_version"),
            "split_entries_sha256": row.get("split_entries_sha256"),
            "epoch_id": row.get("epoch_id"), "source_sha256": row.get("source_sha256"),
            "regen_manifest_sha256": row.get("regen_manifest_sha256"),
            "instance": {"vessel_id": instance.vessel.id, "vessel_class": vessel_class,
                         "vessel_teu": fleet_teu,
                         "vessel_raw_nominal_teu": instance.vessel.nominal_teu,
                         "objective": instance.objective.model_dump(mode="json"),
                         "boxes_to_load": len(instance.load_list),
                         "ports": len(instance.rotation.calls), "family": instance.family,
                         "voyage": None if instance.voyage is None else instance.voyage.model_dump(mode="json")},
            "result": result.model_dump(mode="json"),
            "report": None if report is None else report.model_dump(mode="json"),
            "checker_attempts": checker_attempts,
            "repair_prefix": _repair_prefix(result),
            "timing": {"total_s": total_s, "plan_s": result.metrics.get(
                "pipeline_timing", {}).get("planning_s", result.wall_time_s),
                "planner_reported_plan_s": result.wall_time_s},
            "lower_bound": bound.model_dump(mode="json"), "reference_solver": reference,
            "fallback_needed": bool(result.repaired or result.metrics.get("fallback_needed",
                                    report is None or not report.passed)),
            "shippable": binding_valid and result.shippable,
            "report_binding_valid": binding_valid,
            "batch_generation_error": (precomputed.message if isinstance(precomputed, PlannerResult)
                and precomputed.metrics.get("batch_generation_error") else None),
            "provenance": provenance.model_dump(mode="json")}
    from eval.harness_metrics import (voyage_conditions, checker_outcome,
                                     global_screen_status, arrival_stability)
    if mask_diagnostics:
        # Preserve the actual diagnostic, including an unavailable reason.
        statuses = [str(d.get("global_screen", "unknown")) for d in mask_diagnostics]
        record["mask_global_screen"] = next((s for s in statuses if s.startswith("unavailable")), statuses[0])
        record["mask_diagnostics"] = mask_diagnostics
    else:
        record["mask_global_screen"] = global_screen_status(record)
    record["mask_unavailable"] = bool(record["mask_global_screen"] and
                                      record["mask_global_screen"].startswith("unavailable"))
    record["checker_outcome"] = checker_outcome(record)
    record["feasibility_status"] = _feasibility_status(result,
        witnessed=binding_valid and report is not None and report.passed)
    record["arrival_stability"] = arrival_stability(record)
    record["voyage_conditions"] = voyage_conditions(record)
    from eval.yard_metrics import congestion_mixing, yard_rehandles
    record['yard_rehandles'] = yard_rehandles(record)
    record['congestion_mixing'] = congestion_mixing({
        'instance': record['instance'], 'provenance': instance.provenance.model_dump(mode='json')})
    from eval.dg_coverage import admitted_class_uns
    record['dg_coverage'] = {'admitted_class_uns': admitted_class_uns(instance)}
    from eval.plan_diagnostics import plan_diagnostics
    record.update(plan_diagnostics(record))
    record['sample_reporting'] = sample_reporting(record, planner=planner)
    record['k'] = record['sample_reporting']['k']
    from eval.training_lineage import adapter_training_lineage, training_lineage_fields
    record.update(training_lineage_fields(adapter_training_lineage(planner), row.get("epoch_id")))
    if not tiny_run:
        # Spawned workers also reject drift before returning an official result;
        # the parent independently checks its complete snapshot before saving.
        _load_selected(row, tiny_run)
    return record


def _batch_error(planner, seed, message):
    return PlannerResult(status="error", planner=planner.name, planner_version=planner.version,
                         seed=seed, message=f"Batch planning failed: {message}",
                         provenance=current_provenance(created_by="eval.harness", seed=seed),
                         metrics={"fallback_needed": True, "batch_generation_error": True})


def _repair_prefix(result):
    """Share of the final logical action sequence retained from the original plan."""
    kept, added = (result.metrics.get(key) for key in ("repair_actions_kept", "repair_actions_added"))
    valid = result.repaired and all(type(value) is int and value >= 0 for value in (kept, added))
    total = kept + added if valid else None
    return {"kept": kept if valid else None, "total": total,
            "share": kept / total if total else None}


@dataclass
class _PrecomputedPlanner:
    original: object
    result: PlannerResult

    def __getattr__(self, name):
        return getattr(self.original, name)

    def plan(self, instance, *, seed=0, time_limit_s=None):
        return self.result


def run_evaluation(planner, *, run_dir, instances_dir=None, split="val",
                   rungs=("tiny", "small"), limits=None, workers=1, tiny_run=False,
                   seed=0, time_limit_s=None, shard_index=0, shard_count=1,
                   reference_dir=None, batch_size=1, split_manifest=DEFAULT_MANIFEST, regen_manifest=None,
                   partial=False, spec_keys=None):
    """Run/resume one deterministic shard and write summary.json and ROW.md.

    ``tiny_run=True`` explicitly permits taint and marks the row unofficial; it
    describes a diagnostic run, not a restriction on the selected rung names.
    Active stub overrides require workers=1 because contexts do not cross process
    boundaries. Optional ``plan_batch(instances, *, seed, time_limit_s)`` returns
    one PlannerResult per input in order; only generation is batched, while every
    result still receives ballast, the full checker and repair through run_planner.
    The pool cap counts planner ``num_workers`` when exposed by the planner.
    All frozen allocations in the selected split/rungs remain in pass-rate
    denominators; missing, rejected and limit-unattempted requests stay explicit.
    ``spec_keys`` selects exact frozen members instead of per-rung prefixes,
    superseding ``limits``; keys are deduplicated and sorted before sharding.
    Explicit selection is recorded as partial and does not shrink denominators.
    """
    from eval.harness_metrics import aggregate_results, render_row, render_table_header
    from eval.harness_inventory import normalize_spec_keys
    spec_keys = normalize_spec_keys(spec_keys)
    explicit_selection = spec_keys is not None
    if limits and not tiny_run and not partial and not explicit_selection:
        raise ValueError("official evaluation with limits requires partial=True")
    if type(workers) is not int or not 1 <= workers <= 6:
        raise ValueError("workers must be between 1 and 6")
    internal_workers = getattr(planner, "num_workers", 1)
    if type(internal_workers) is not int or internal_workers < 1:
        raise ValueError("planner num_workers must be a positive integer")
    if workers * internal_workers > 6:
        raise ValueError("pool workers × planner num_workers must not exceed 6")
    if active_stubs() and workers != 1:
        raise ValueError("stub overrides require workers=1")
    if not tiny_run:
        require_clean(current_provenance(), what="official evaluation")
        if isinstance(getattr(planner, "provenance", None), Provenance):
            require_clean(planner.provenance, what="official evaluation planner")
    if (type(shard_count) is not int or shard_count < 1 or type(shard_index) is not int
            or not 0 <= shard_index < shard_count):
        raise ValueError("invalid shard index/count")
    if type(batch_size) is not int or batch_size < 1 or (batch_size > 1 and workers != 1):
        raise ValueError("batch generation requires positive batch_size and workers=1")
    if batch_size > 1 and not callable(getattr(planner, "plan_batch", None)):
        raise ValueError("planner does not provide plan_batch")
    selected = select_instances(instances_dir, split=split, rungs=rungs, limits=limits,
                            split_manifest=split_manifest, regen_manifest=regen_manifest,
                            tiny_run=tiny_run, spec_keys=spec_keys)
    allocated_rows = sorted(getattr(selected, "allocated_rows", selected), key=lambda row: row["spec_key"])[shard_index::shard_count]
    allocated_keys = {row["spec_key"] for row in allocated_rows}
    rows = [row for row in selected if row["spec_key"] in allocated_keys]
    if not allocated_rows:
        raise ValueError("empty shard")
    selection_metadata = selected[0] if selected else allocated_rows[0]
    run_dir = Path(run_dir)
    inventory = load_inventory(split_manifest)
    parameters = _configuration(getattr(planner, "evaluation_config", getattr(planner, "__dict__", {})))
    parameters = pin_weight_content(parameters)
    from eval.training_lineage import adapter_training_lineage, training_lineage_fields
    training_lineage = adapter_training_lineage(planner, parameters=parameters)
    weight_identity = {key: value for key, value in parameters.items() if key.endswith("_content_sha256")}
    if weight_identity and hasattr(planner, "_model"):
        previous = getattr(planner, "_harness_weight_identity", None)
        if planner._model is not None and previous != weight_identity:
            raise ValueError("run configuration changed or preloaded model lacks bound weight identity; reload planner")
        planner._harness_weight_identity = weight_identity
    config = {"harness_version": HARNESS_VERSION, "planner": planner.name, "planner_version": planner.version,
              "planner_parameters": parameters,
              "training_lineage": training_lineage,
              "versions": current_provenance().versions, "active_stubs": active_stubs(),
              "instances_dir": None if instances_dir is None else str(Path(instances_dir).resolve()),
              "epoch_id": selection_metadata.get("epoch_id"), "source_sha256": selection_metadata.get("source_sha256"),
              "regen_manifest_sha256": selection_metadata.get("regen_manifest_sha256"),
              "split_policy": inventory.manifest["policy"]["hash_salt"],
              "split_version": inventory.version, "split_entries_sha256": inventory.entries_sha256,
              "inventory_allocated_count": selection_metadata.get("inventory_allocated_count", len(allocated_rows)),
              "inventory_available_count": selection_metadata.get("inventory_available_count", len(rows)),
              "inventory_subset": True, "split": split, "rungs": list(rungs),
              "tiny_run": tiny_run, "seed": seed, "time_limit_s": time_limit_s,
              "partial": bool(partial or limits or explicit_selection), "limits": limits,
              "explicit_selection": explicit_selection, "spec_keys": spec_keys,
              "shard_index": shard_index, "shard_count": shard_count, "batch_size": batch_size,
              "inputs": [{k: v for k, v in row.items() if k != "path"} for row in rows],
              "allocations": allocated_rows}
    references = {}
    reference_config = None
    if reference_dir is not None:
        if not tiny_run:
            try:
                reference_config = read_snapshot(Path(reference_dir) / "config.json").json()
                reference_summary = read_snapshot(Path(reference_dir) / "summary.json").json()
            except (OSError, ValueError) as exc:
                raise ValueError("official reference requires readable run configuration and summary") from exc
            config["reference_config_sha256"] = validate_reference_run(
                reference_config, reference_summary, config)
            config["reference_summary_sha256"] = _digest(reference_summary)
        for path in sorted((Path(reference_dir) / "instances").glob("*.json")):
            record = read_snapshot(path).json()
            if (record.get("report") or {}).get("passed"):
                if not tiny_run:
                    _clean_result(PlannerResult.model_validate(record["result"]),
                                  _report(record["report"]),
                                  Provenance.model_validate(record["provenance"]))
                key = record["spec_key"]
                if key in references:
                    raise ValueError("duplicate reference instance specification")
                references[key] = record
        config["reference_sha256"] = _digest(references)
    # Validate and bind every selected comparison before any planning starts.
    references = {row["spec_key"]: _reference(references.get(row["spec_key"]), row,
                  instance=_load_selected(row, tiny_run), official=not tiny_run,
                  run_config=reference_config) for row in rows}
    fingerprint = _digest(config)
    config_path = run_dir / "config.json"
    if config_path.exists():
        if read_snapshot(config_path).json() != config:
            raise ValueError("run configuration changed; use a new run directory")
    else:
        _write(config_path, config)
    results, pending = [], []
    for row in rows:
        path = run_dir / "instances" / f"{row['spec_key']}.json"
        if path.exists():
            record = read_snapshot(path).json()
            expected_lineage = training_lineage_fields(training_lineage, row.get("epoch_id"))
            if any(record.get(key) != value for key, value in expected_lineage.items()):
                raise ValueError("checkpoint training lineage differs from the configured adapter")
            if (record.get("config_sha256") != fingerprint or record.get("spec_key") != row["spec_key"]
                    or record.get("input_sha256") != row["input_sha256"]
                    or any(record.get(key) is not value for key, value in {
                        "official": not tiny_run, "tiny_run": tiny_run, "diagnostic": tiny_run,
                        "evidence_eligible": not tiny_run}.items())):
                raise ValueError("checkpoint does not match run configuration")
            instance = _load_selected(row, tiny_run)
            from stowage.checker import report_matches_inputs
            cached_result = PlannerResult.model_validate(record["result"])
            cached_report = None if record["report"] is None else _report(record["report"])
            if cached_report is not None and not report_has_current_versions(cached_report):
                raise ValueError("checkpoint report is not bound to current checker versions")
            if cached_report is not None and cached_report.passed and (
                    cached_result.plan is None or not report_matches_inputs(
                        cached_report, instance, cached_result.plan)
                    or (not tiny_run and not report_has_current_versions(cached_report))):
                raise ValueError("checkpoint checker report is not bound to evaluated inputs")
            if not tiny_run:
                _clean_result(PlannerResult.model_validate(record["result"]),
                              None if record["report"] is None else _report(record["report"]),
                              Provenance.model_validate(record["provenance"]))
            results.append(record)
        else:
            pending.append(row)

    def save(row, record):
        if adapter_training_lineage(planner, parameters=parameters) != training_lineage:
            raise ValueError("adapter training lineage changed during evaluation")
        expected_lineage = training_lineage_fields(training_lineage, row.get("epoch_id"))
        if any(record.get(key) != value for key, value in expected_lineage.items()):
            raise ValueError("result training lineage differs from the configured adapter")
        _verify_publication_snapshot(rows, tiny_run=tiny_run, inventory=inventory,
                                     instances_dir=instances_dir, versions=config["versions"],
                                     allocation_rows=allocated_rows)
        record["config_sha256"] = fingerprint
        record["partial"] = config["partial"]
        record["explicit_selection"] = explicit_selection
        _write(run_dir / "instances" / f"{row['spec_key']}.json", record)
        results.append(record)

    if workers == 1:
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            generated = [_INDIVIDUAL] * len(batch)
            if batch_size > 1:
                batch_start = perf_counter()
                try:
                    from eval.mask_observation import observe_mask_diagnostics
                    with observe_mask_diagnostics() as batch_diagnostics:
                        generated = list(planner.plan_batch([_load_selected(row, tiny_run) for row in batch],
                                                           seed=seed, time_limit_s=time_limit_s))
                    if len(generated) != len(batch):
                        raise ValueError("plan_batch returned the wrong number of results")
                except Exception as exc:
                    generated = [_batch_error(planner, seed, f"{type(exc).__name__}: {exc}")
                                 for _ in batch]
                batch_elapsed = perf_counter() - batch_start
                generated = [item if isinstance(item, PlannerResult) else _batch_error(
                    planner, seed, f"invalid entry {type(item).__name__}; expected PlannerResult")
                    for item in generated]
                for row, item in zip(batch, generated, strict=True):
                    instance_id = _load_selected(row, tiny_run).id
                    observations = [d for d in batch_diagnostics
                                    if d.get("instance_id") == instance_id]
                    if observations:
                        statuses = [str(d.get("global_screen", "unknown")) for d in observations]
                        item.metrics["mask_global_screen"] = next(
                            (s for s in statuses if s.startswith("unavailable")), statuses[0])
                        item.metrics["mask_diagnostics"] = observations
            for row, precomputed in zip(batch, generated, strict=True):
                record = _evaluate(planner, row, seed, time_limit_s, tiny_run,
                                   references.get(row["spec_key"]), precomputed)
                if batch_size > 1:
                    record["timing"]["total_s"] += batch_elapsed / len(batch)
                    record["timing"]["plan_s"] = batch_elapsed / len(batch)
                    record["timing"]["batch_generation_share_s"] = batch_elapsed / len(batch)
                save(row, record)
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = {pool.submit(_evaluate, planner, row, seed, time_limit_s, tiny_run,
                                   references.get(row["spec_key"])): row for row in pending}
            for future in as_completed(futures):
                save(futures[future], future.result())
    results.sort(key=lambda record: record["spec_key"])
    evaluated_keys = {record["spec_key"] for record in results}
    allocation_results = [*results, *[{**row, **training_lineage_fields(training_lineage, row.get("epoch_id")),
        "allocation_only": True, "report": None,
        "shippable": False, "explicit_selection": explicit_selection}
        for row in allocated_rows if row["spec_key"] not in evaluated_keys]]
    from eval.sample_reporting import sample_reporting
    for record in allocation_results:
        record.setdefault('planner', planner.name)
        record['sample_reporting'] = sample_reporting(record, planner=planner, parameters=parameters)
        record['k'] = record['sample_reporting']['k']
    from eval.instance_facts import build_instance_facts, apply_instance_facts
    instance_facts = build_instance_facts({planner.name: allocation_results})
    allocation_results = apply_instance_facts(allocation_results, instance_facts)
    summary = aggregate_results(allocation_results, method=planner.name, split=split, tiny_run=tiny_run)
    summary["by_rung"] = {rung: aggregate_results(
        [record for record in allocation_results if record["rung"] == rung],
        method=planner.name, split=split, tiny_run=tiny_run) for rung in dict.fromkeys(rungs)
        if any(record["rung"] == rung for record in allocation_results)}
    summary.update(config_sha256=fingerprint, official=not tiny_run, tiny_run=tiny_run,
                   diagnostic=tiny_run, evidence_eligible=not tiny_run, versions=config["versions"],
                   harness_version=HARNESS_VERSION, shard_index=shard_index, shard_count=shard_count,
                   partial=config["partial"], explicit_selection=explicit_selection,
                   instance_facts_complete=False,
                   rungs=list(rungs), split=split,
                   split_version=inventory.version, split_entries_sha256=inventory.entries_sha256,
                   inventory_allocated_count=config["inventory_allocated_count"],
                   inventory_available_count=config["inventory_available_count"], inventory_subset=True,
                   epoch_id=config["epoch_id"], source_sha256=config["source_sha256"],
                   regen_manifest_sha256=config["regen_manifest_sha256"])
    instance_label = ", ".join(f"{data['n_allocated']} allocated / {data['n_instances']} attempted / "
                               f"{data['feasibility_status_counts']['generation-rejected']} rejected {rung}"
                               for rung, data in summary["by_rung"].items()) + f" {split}"
    repo_root = Path(__file__).resolve().parents[2]
    row_link = ("../" + run_dir.resolve().relative_to(repo_root).as_posix()
                if run_dir.resolve().is_relative_to(repo_root) else str(run_dir.resolve()))
    row_text = render_row(summary, method=planner.name, instances=instance_label,
                          run=row_link, tiny_run=tiny_run)
    # Include aggregation and row rendering in the work protected by postflight.
    # This path also runs when every shard was restored from a checkpoint.
    _verify_publication_snapshot(rows, tiny_run=tiny_run, inventory=inventory,
                                 instances_dir=instances_dir, versions=config["versions"],
                                 allocation_rows=allocated_rows)
    if adapter_training_lineage(planner, parameters=parameters) != training_lineage:
        raise ValueError("adapter training lineage changed before evaluation publication")
    _write(run_dir / "allocations.json", allocation_results)
    _write(run_dir / "instance_facts.json", instance_facts)
    for record in allocation_results:
        if not record.get("allocation_only"):
            _write(run_dir / "instances" / f"{record['spec_key']}.json", record)
    _write(run_dir / "summary.json", summary)
    (run_dir / "ROW.md").write_text(row_text.rstrip() + "\n")
    (run_dir / "TABLE.md").write_text(render_table_header() + "\n" + row_text.rstrip() + "\n")
    return summary


def _report(value):
    from eval.plan_diagnostics import restore_check_report
    return restore_check_report(value)


def consolidate_evaluation_runs(run_dirs, *, ledger_path=None):
    """Publish one D68 ledger and rejoin every method after all runs finish.

    This is also the entry point for separately scheduled solver, model and gold
    runs. Only matching allocated input identities can share a witness. Existing
    run configuration hashes stay unchanged: method attempts are immutable while
    the derived cross-method facts gain evidence.
    """
    from eval.instance_facts import build_instance_facts, apply_instance_facts
    from eval.harness_metrics import aggregate_results, render_row, render_table_header

    folders = [Path(folder) for folder in run_dirs]
    if not folders or len(set(folder.resolve() for folder in folders)) != len(folders):
        raise ValueError("consolidation requires distinct method run directories")
    if ledger_path is None:
        parents = {folder.resolve().parent for folder in folders}
        if len(parents) != 1:
            raise ValueError("ledger_path is required when runs have different parents")
        ledger_path = next(iter(parents)) / "instance_facts.json"
    snapshots, groups, runs = {}, {}, []
    for index, folder in enumerate(folders):
        values = {}
        for name in ("config.json", "summary.json", "allocations.json"):
            snapshot = read_snapshot(folder / name)
            snapshots[folder / name] = snapshot.sha256
            values[name] = snapshot.json()
        config, previous, records = (values[name] for name in ("config.json", "summary.json", "allocations.json"))
        if not config.get("tiny_run", False):
            if config.get("versions") != current_provenance().versions:
                raise ValueError("cannot consolidate stale component versions")
            for record in records:
                if not record.get("allocation_only") and record.get("config_sha256") != _digest(config):
                    raise ValueError("method record is detached from its run configuration")
                if record.get("report") and not report_has_current_versions(_report(record["report"])):
                    raise ValueError("cannot consolidate stale checker evidence")
        for record in records:
            path = folder / "instances" / f"{record['spec_key']}.json"
            if not record.get("allocation_only") and path.exists():
                snapshots[path] = read_snapshot(path).sha256
        groups[f"{config['planner']}:{index}"] = records
        runs.append((folder, config, previous, records))
    identity_fields = ("split", "split_version", "split_entries_sha256", "epoch_id",
                       "source_sha256", "regen_manifest_sha256")
    baseline = {field: runs[0][1].get(field) for field in identity_fields}
    if any({field: config.get(field) for field in identity_fields} != baseline
           for _, config, _, _ in runs[1:]):
        raise ValueError("evaluation identity differs across method runs")
    facts = build_instance_facts(groups)
    publications = []
    for folder, config, previous, records in runs:
        joined = apply_instance_facts(records, facts)
        summary = {**previous, **aggregate_results(joined, method=config["planner"],
                   split=config["split"], tiny_run=config.get("tiny_run", False))}
        summary["by_rung"] = {rung: aggregate_results([r for r in joined if r["rung"] == rung],
            method=config["planner"], split=config["split"], tiny_run=config.get("tiny_run", False))
            for rung in config["rungs"] if any(r["rung"] == rung for r in joined)}
        summary["instance_facts_ledger"] = str(Path(ledger_path).resolve())
        summary["instance_facts_sha256"] = _digest(facts)
        summary["instance_facts_complete"] = True
        summary["instance_facts_method_count"] = len(runs)
        label = (f"{summary['n_allocated']} allocated / {summary['n_attempted']} attempted / "
                 f"{summary['n_generation_rejected']} rejected {config['split']}")
        row = render_row(summary, method=config["planner"], instances=label, run=str(folder),
                         tiny_run=config.get("tiny_run", False))
        publications.append((folder, joined, summary, row))
    # Re-read immediately before publication; do not overwrite a concurrent resume.
    for path, digest in snapshots.items():
        read_snapshot(path, expected_sha256=digest)
    _write(ledger_path, facts)
    for folder, records, summary, row in publications:
        _write(folder / "instance_facts.json", facts)
        _write(folder / "allocations.json", records)
        for record in records:
            if not record.get("allocation_only"):
                _write(folder / "instances" / f"{record['spec_key']}.json", record)
        _write(folder / "summary.json", summary)
        (folder / "ROW.md").write_text(row + "\n")
        (folder / "TABLE.md").write_text(render_table_header() + "\n" + row + "\n")
    return facts


def run_multimethod_evaluation(planners, *, run_dir, **kwargs):
    """Evaluate a named method mapping and publish shared instance facts (D68)."""
    import re
    folders = {}
    for index, (label, planner) in enumerate(planners.items()):
        slug = re.sub(r"[^A-Za-z0-9_.-]", "_", str(label))
        folder = Path(run_dir) / f"{index:02d}-{slug}"
        run_evaluation(planner, run_dir=folder, **kwargs)
        folders[label] = folder
    facts = consolidate_evaluation_runs(folders.values(), ledger_path=Path(run_dir) / "instance_facts.json")
    return {"instance_facts": facts, "methods": {label: read_snapshot(folder / "summary.json").json()
                                                for label, folder in folders.items()}}


def run_paired_evaluation(first, second, *, run_dir, metric="weighted_nonproductive",
                          n_bootstrap=2000, confidence=0.95, bootstrap_seed=0,
                          population="own", **kwargs):
    """Evaluate two planners on identical selections and save paired differences.

    Generation and planner seeds are shared through kwargs. The paired bootstrap
    resamples whole instance pairs, preserving the difficulty shared by both
    planners; see harness_metrics.paired_comparison for the percentile method.
    Both runs remain independently resumable if either planner is interrupted.
    """
    from eval.harness_metrics import paired_comparison, paired_table_header, render_paired_comparison

    root = Path(run_dir)
    records = []
    for label, planner in (("first", first), ("second", second)):
        folder = root / label
        run_evaluation(planner, run_dir=folder, **kwargs)
        # Keep rejected, missing and unattempted allocations in the exclusion
        # count (and in pass-rate comparisons). Only both-pass plans contribute
        # move differences. Unbuilt allocations have no physical instance ID.
    consolidate_evaluation_runs([root / "first", root / "second"], ledger_path=root / "instance_facts.json")
    for label in ("first", "second"):
        records.append([{**record, "instance_id": record.get("instance_id") or
                         f"allocated:{record['spec_key']}"}
                        for record in read_snapshot(root / label / "allocations.json").json()])
    comparison = paired_comparison(*records, metric=metric, n_bootstrap=n_bootstrap,
                                   confidence=confidence, seed=bootstrap_seed, population=population)
    other_population = "shipped" if population == "own" else "own"
    other = paired_comparison(*records, metric=metric, n_bootstrap=n_bootstrap,
                              confidence=confidence, seed=bootstrap_seed, population=other_population)
    comparison[f"{other_population}_comparison"] = other
    comparisons = [comparison, other]
    from eval.yard_metrics import AVOIDABLE_YARD_METRIC
    if metric != AVOIDABLE_YARD_METRIC:
        yard = paired_comparison(*records, metric=AVOIDABLE_YARD_METRIC, n_bootstrap=n_bootstrap,
                                 confidence=confidence, seed=bootstrap_seed, population=population)
        yard_other = paired_comparison(*records, metric=AVOIDABLE_YARD_METRIC, n_bootstrap=n_bootstrap,
                                       confidence=confidence, seed=bootstrap_seed, population=other_population)
        yard[f'{other_population}_comparison'] = yard_other
        comparison['avoidable_yard_rehandles_comparison'] = yard
        comparisons.extend([yard, yard_other])
    _write(root / "paired.json", comparison)
    row = "\n".join(render_paired_comparison(value, first_method=first.name, second_method=second.name)
                    for value in comparisons)
    (root / "ROW.md").write_text(row + "\n")
    (root / "TABLE.md").write_text(paired_table_header() + "\n" + row + "\n")
    return comparison


def _reference(record, row, *, instance=None, official=False, run_config=None):
    metadata = validate_reference(record, row, instance=instance, official=official, run_config=run_config)
    if metadata is None:
        return None
    # Keep the checked evidence, not merely a scalar objective: a solver can
    # establish instance feasibility and stability even when this method fails.
    return {**metadata, "witness": {**record, "reference_validated": True}}


class PlaceholderPlanner:
    """UI's explicitly labelled first-fit diagnostic, evaluated by the full checker."""
    name = "ui-placeholder"
    version = "1.0"
    sets_ballast = False
    deterministic = True

    def plan(self, instance, *, seed=0, time_limit_s=None):
        from app.backend import make_demo_plan
        start = perf_counter()
        plan = make_demo_plan(instance)
        return PlannerResult(status="ok", plan=plan, planner=self.name, planner_version=self.version,
                             seed=seed, wall_time_s=perf_counter() - start,
                             provenance=current_provenance(created_by=self.name, seed=seed).merge(instance.provenance))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planner", default="placeholder", help="placeholder, twostep, exact, or module:Class")
    parser.add_argument("--instances-dir", "--instances", "--instance-root", type=Path, required=True)
    parser.add_argument("--regen-manifest", type=Path, help="Epoch manifest (defaults to INSTANCE_ROOT/manifest.json)")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    parser.add_argument("--rungs", nargs="+", default=["tiny", "small"])
    parser.add_argument("--limit", action="append", default=[], metavar="RUNG=COUNT")
    parser.add_argument("--spec-keys", nargs="+", metavar="KEY",
                        help="Exact frozen split/rung keys; replaces --limit prefixes and marks a partial run")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--time-limit-s", type=float)
    parser.add_argument("--tiny-run", action="store_true")
    parser.add_argument("--partial", action="store_true", help="Explicitly label a limited official allocation")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    choices = {"placeholder": "eval.harness:PlaceholderPlanner", "twostep": "stowage.solver.twostep:TwoStepPlanner",
               "exact": "stowage.solver.exact:ExactPlanner",
               "scalable": "stowage.solver.scalable:ScalableSolver"}
    module, name = choices.get(args.planner, args.planner).split(":", 1)
    planner = getattr(importlib.import_module(module), name)()
    limits = dict((rung, int(count)) for rung, count in (value.split("=", 1) for value in args.limit))
    kwargs = vars(args).copy()
    for key in ("planner", "limit"):
        kwargs.pop(key)
    summary = run_evaluation(planner, limits=limits, **kwargs)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
