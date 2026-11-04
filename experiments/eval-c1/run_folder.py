"""Run-folder contract writer (experiments/fleet/orders/w2-stowlm-run-contract.md):

    <run>/manifest.json
    <run>/instances/<instance_id>/result.json   # PlannerResult.model_dump(mode="json") (final: after ballast/check/repair)
    <run>/instances/<instance_id>/report.json   # the CheckReport on that plan (extra file; the app re-checks anyway)
    <run>/instances/<instance_id>/trace.jsonl   # decode trace of StowLM's selected sample (decode_trace.py)

``result.json`` is the pipeline's final result: if 5.7 repair replaced the draft, ``repaired`` is true and the trace
still shows the StowLM draft that was decoded (the manifest says so).
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

BASE_MODEL = "meta-models/Muse-Glimmer-30B"


def _sha_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=1, default=str))
    tmp.replace(path)


def _quantisation(model_path: str) -> str | None:
    try:
        q = json.loads((Path(model_path) / "config.json").read_text()).get("quantization") or {}
    except (OSError, ValueError):
        return None
    return f"mlx-{q['bits']}bit-g{q.get('group_size', '?')}" if q.get("bits") else "mlx-unquantised"


def _package_version(name: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def build_manifest(planner, *, temperature: float, n_samples: int, seed: int, mask_workers: int, c2: bool,
                   epoch_id: str | None, instance_root_sha256: str | None, mode: str,
                   argv: list[str] | None = None, method: str | None = None, selection: dict | None = None,
                   dead_ends: dict | None = None, outcomes: dict | None = None) -> dict:
    from llm.stowlm.format import FORMAT_VERSION
    from llm.stowlm.format_prompt import PROMPT_VERSION
    from llm.stowlm.mask import MASK_VERSION
    from stowage.versions import component_versions

    config = planner.evaluation_config  # pins model/adapter content; refuses untrusted adapter lineage
    adapter = None if planner.adapter_path is None else Path(planner.adapter_path)
    sidecar = None if adapter is None else adapter / "adapters.safetensors.provenance.json"
    versions = component_versions()
    lineage = adapter_lineage(sidecar)
    return {
        "planner_kind": "stowlm_adapter" if adapter is not None else "base_glimmer_mask",
        "base_model": BASE_MODEL,
        "base_model_revision": config.get("model_content_sha256"),
        "quantisation": _quantisation(planner.model_path),
        "adapter_sha256": None if adapter is None else _sha_file(adapter / "adapters.safetensors"),
        "adapter_lineage": None if sidecar is None else {"path": str(sidecar), "sha256": _sha_file(sidecar)},
        # What the adapter was trained on, from its weight-bound sidecar. Consumers label a plan "StowLM" only when
        # adapter_clean is true (contract, "Consumer rules"); pipeline_test / diagnostic adapters never qualify.
        "adapter_data_mode": lineage["data_mode"], "adapter_stubs": lineage["stubs"],
        "adapter_clean": lineage["clean"],
        "method": method, "selection": selection,
        # 5.1 per-rule dead-end attribution summed over every sample of every instance in the run (rule freeze v1
        # on); unattributed = dead ends whose cause didn't reach the planner (legacy path or an old proxy).
        "dead_ends": dead_ends,
        # per-instance outcomes; "mask time limit" = the draft hit --time-limit (not counted as a model failure)
        "outcomes": outcomes,
        "pass_at_1_definition": "sample 0 by index, ballast + full checker, no selection (D76); reported beside "
                                "the checker-selected best-of-k from the same samples",
        "runtime": "mlx", "mlx_version": _package_version("mlx"), "mlx_lm_version": _package_version("mlx-lm"),
        "mask_version": MASK_VERSION, "feasibility_version": versions.get("feasibility"),
        "checker_version": versions.get("checker"), "prompt_version": PROMPT_VERSION,
        "format_version": FORMAT_VERSION,
        "epoch_id": epoch_id, "instance_root_sha256": instance_root_sha256,
        "decoding": {"temperature": temperature, "top_p": 1.0, "samples_per_instance": n_samples, "seed": seed,
                     "pool_size": mask_workers, "c2": c2 if isinstance(c2, str) else ("on" if c2 else "off (stub; diagnostic)"),
                     "selection": config.get("selection")},
        "mode": mode, "diagnostic": mode != "official",
        "trace_scope": ("selected sample of the StowLM draft; result.json is the pipeline's final result (repaired "
                        "plans keep the draft's trace)"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "command": " ".join(argv if argv is not None else sys.argv),
    }


def adapter_lineage(sidecar: Path | None) -> dict:
    """Data mode and taint of an adapter, read from its sidecar (llm.stowlm.train). Missing or unreadable
    metadata is never clean."""
    if sidecar is None:
        return {"data_mode": None, "stubs": {}, "clean": None}
    try:
        meta = json.loads(sidecar.read_text())
        stubs = dict(meta["provenance"]["stubs"])
        identity = (meta.get("training") or {}).get("data_identity") or {}
        mode = identity.get("claimed_kind") or identity.get("dataset_kind")
        clean = not stubs and identity.get("dataset_kind") in ("production_gold", "mvp_gold")  # mvp_gold: D79
        return {"data_mode": mode, "stubs": stubs, "clean": clean}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"data_mode": None, "stubs": {"adapter_lineage": f"unreadable: {exc}"}, "clean": False}


def aggregate_dead_ends(metrics_list) -> dict:
    """Run-level dead ends by rule from each instance's StowLM draft metrics (``gen_dead_ends_by_rule``)."""
    by_rule: dict[str, int] = {}
    unattributed = samples = 0
    collected = False
    for m in metrics_list:
        m = m or {}
        samples += int(m.get("n_dead_end") or 0)
        if "gen_dead_ends_by_rule" in m:
            collected = True
            for rule, n in (m.get("gen_dead_ends_by_rule") or {}).items():
                by_rule[rule] = by_rule.get(rule, 0) + int(n)
            unattributed += int(m.get("gen_dead_ends_unattributed") or 0)
    return {"dead_end_samples": samples, "by_rule": dict(sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))),
            "unattributed": unattributed if collected else None,
            "collected": collected, "note": "rule ids per dead-ended sample; one sample can cite several rules"}


def write_manifest(run_dir: str | Path, manifest: dict) -> None:
    _write_json(Path(run_dir) / "manifest.json", manifest)


MASK_TIME_LIMIT = "mask time limit"
FINISHER_TIME_LIMIT = "finisher time limit"  # complete draft, no checker verdict before the deadline (not a pass)


def instance_outcome(result: dict, report: dict | None) -> dict:
    """One outcome per instance. A draft that hit the per-instance time limit is its own outcome, "mask time limit"
    (logic-mask, leader decision): the mask/decode budget ran out, which is not a model failure, and is reported
    separately from checker failures, dead ends and errors, even when 5.7 repair then delivered a plan."""
    metrics = result.get("metrics") or {}
    draft = metrics if "n_samples" in metrics or "deadline_exceeded" in metrics else (
        metrics.get("repair_original_metrics") or {})
    passed = bool(report and report.get("passed"))
    repaired = bool(result.get("repaired"))
    budget_timeouts = draft.get("finisher_budget_timeouts") or []
    if (not passed and budget_timeouts and not (draft.get("n_checker_feasible") or 0)
            and len(budget_timeouts) >= len(draft.get("candidate_failures") or {})):
        # every checked candidate's ballast finisher ran out of its (equal) budget: not a planner or checker failure
        outcome = FINISHER_TIME_LIMIT
    elif draft.get("deadline_exceeded") and (draft.get("n_complete") or 0) > 0 and not passed:
        # decoding finished; the budget ran out in ballast finishing / checking of the complete sample(s)
        outcome = FINISHER_TIME_LIMIT
    elif draft.get("deadline_exceeded") or (result.get("status") == "timeout" and not repaired):
        outcome = MASK_TIME_LIMIT
    elif passed:
        outcome = "repaired pass" if repaired else "own pass"
    elif result.get("status") == "error" and not draft.get("n_samples"):
        outcome = "error"
    elif draft.get("n_complete") == 0 and (draft.get("n_dead_end") or 0) > 0:
        outcome = "dead end"
    else:
        outcome = "checker fail"
    p1 = draft.get("pass_at_1") or {}
    return {"outcome": outcome, "repaired": repaired, "checker_passed": passed,
            "delivered_after_time_limit": outcome == MASK_TIME_LIMIT and passed,
            # D76: pass@1 (sample 0 by index, no selection) beside the checker-selected best-of-k, same samples
            "k": draft.get("n_samples"), "pass_at_1": p1.get("checker_passed"),
            "pass_at_1_detail": p1 or None,
            "best_of_k_checker_feasible": (draft.get("n_checker_feasible") or 0) > 0 if draft else None}


def write_instance(run_dir: str | Path, instance_id: str, result: dict, report: dict | None) -> None:
    folder = Path(run_dir) / "instances" / instance_id
    _write_json(folder / "result.json", result)
    _write_json(folder / "report.json", report)
    _write_json(folder / "outcome.json", instance_outcome(result, report))
    if not (folder / "trace.jsonl").exists():  # planner errors before decoding leave no trace
        (folder / "trace.jsonl").write_text("")


def export_harness_records(run_dir: str | Path) -> int:
    """Harness mode: ``run_evaluation`` writes ``instances/<spec_key>.json`` records; mirror them into the contract
    layout (result.json / report.json per instance id)."""
    n = 0
    for path in sorted((Path(run_dir) / "instances").glob("*.json")):
        record = json.loads(path.read_text())
        if "result" in record and "instance_id" in record:
            write_instance(run_dir, record["instance_id"], record["result"], record.get("report"))
            n += 1
    return n


MODEL_FAILURES = ("checker fail", "dead end", "error")


def count_outcomes(run_dir: str | Path) -> dict:
    counts: dict[str, int] = {}
    for path in sorted((Path(run_dir) / "instances").glob("*/outcome.json")):
        outcome = json.loads(path.read_text())["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def outcome_summary(run_dir: str | Path) -> dict:
    """Run-level counts: pass@1 beside best-of-k (D76), and mask time limits apart from model failures."""
    rows = [json.loads(p.read_text()) for p in sorted((Path(run_dir) / "instances").glob("*/outcome.json"))]
    counts = count_outcomes(run_dir)
    ks = sorted({r.get("k") for r in rows if r.get("k")})
    return {"instances": len(rows), "outcomes": counts, "k": ks[0] if len(ks) == 1 else ks,
            "pass_at_1": sum(r.get("pass_at_1") is True for r in rows),
            "pass_at_1_unchecked": sum(r.get("pass_at_1") is None for r in rows),
            "best_of_k_checker_feasible": sum(r.get("best_of_k_checker_feasible") is True for r in rows),
            "own_pass": counts.get("own pass", 0), "repaired_pass": counts.get("repaired pass", 0),
            "mask_time_limit": counts.get(MASK_TIME_LIMIT, 0),
            "finisher_time_limit": counts.get(FINISHER_TIME_LIMIT, 0),
            "model_failures": sum(counts.get(o, 0) for o in MODEL_FAILURES),
            "note": "mask time limit = the per-instance budget ran out; never counted as a model failure"}
