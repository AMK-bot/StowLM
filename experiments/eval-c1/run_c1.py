"""Checkpoint C1 (plan 9.1) / 6.2 base+mask evaluation driver: StowLM (base or adapter) under the 5.1 mask, best-of-N,
then the authoritative pipeline (ballast finisher → full checker → 5.7 repair fallback), resumable per instance.

Two input modes, one planner path:

* ``--instances <epoch root>`` (C1 / official): the 5.2 harness ``run_evaluation``. It selects frozen split members,
  validates the epoch/regeneration manifest and instance bytes, refuses stale or changed inputs, pins model/adapter
  content, writes ``config.json``, ``instances/<spec_key>.json``, ``summary.json`` and the ``docs/evaluation.md``
  ``ROW.md``, and resumes per instance (a changed configuration is refused). ``--no-stub`` makes it official
  (C2 on, clean provenance required); otherwise it's a labelled ``tiny_run`` diagnostic.
* ``--loose <files or dirs>`` (DIAGNOSTIC ONLY): instance files outside any frozen release (today's only live inputs,
  e.g. the pilot-d58 tiny set). Same ``run_planner(..., allow_repair=True)`` gate per instance, resumable by input
  hash, config fingerprint refused on change, never a ``docs/evaluation.md`` row. ``--references`` reads exact/
  scalable/twostep ``done.json`` records (pilot-d58 format) for move gaps; a reference whose input hash differs from
  the evaluated bytes is ignored.

Mask backend: ``--mask-workers W`` runs the per-sample mask decoders on W spawned CPU processes (``pool.py``) while
the GPU decodes; 0 = the in-thread decoder. ``--c2`` uses the real engine (production); without it the C2 global
screen is off and every artifact is stub-tainted (diagnostic).

    uv run python experiments/eval-c1/run_c1.py --run-dir experiments/eval-c1/runs/<name> \
        --loose experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/tiny --limit 4 \
        --references experiments/solver-diag-1028/pilot-d58/runs --n-samples 8 --temperature 1 --mask-workers 2
"""

from __future__ import annotations

import argparse
import contextlib
from collections import Counter
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def load_references(root: Path | None) -> dict[str, dict]:
    """input sha256 → {method: {objective, lb36, shippable}} from pilot-d58 style ``runs/<method>/<i>/done.json``."""
    refs: dict[str, dict] = {}
    if root is None:
        return refs
    for done in sorted(root.glob("*/*/done.json")):
        r = json.loads(done.read_text())
        if not r.get("input_sha256"):
            continue
        entry = refs.setdefault(r["input_sha256"], {})
        entry[r.get("solver", done.parent.parent.name)] = {
            "objective": r.get("checked_objective") if r.get("checker_passed") and r.get("shippable") else None,
            "lower_bound_36": r.get("lower_bound_36"), "optimality": r.get("optimality_status"),
            "shippable": r.get("shippable"), "checker_versions": r.get("versions")}
    return refs


D79_LABEL = "off (D79: FeasibilityConfig extra global_screen=0; MVP production setting, no stub)"
HARNESS_WORKER_CAP = 6  # src/eval/harness.py: pool workers × planner num_workers must not exceed 6
OFFICIAL_MASK_WORKERS = 5  # parent + 5 mask workers = 6 processes; fits the cap with harness workers = 1
OFFICIAL_TIME_LIMIT_S = 3600.0  # per instance (a timeout is a counted failure); re-fit per rung from the full pilot


def method_label(adapter: str | None, n_samples: int, temperature: float) -> str:
    """Logic review: the row label says what "pass" means (at least one of k samples passes the checker)."""
    who = "StowLM (LoRA on 4-bit Muse Glimmer 30B)" if adapter else "4-bit base Muse Glimmer 30B + feasibility mask"
    return f"{who}, checker-selected best of {n_samples}, T = {temperature:g}"


def stratified_selection(rows: list[dict], n: int, keys=("vessel", "generator")) -> list[dict]:
    """n rows spread over the strata of ``keys`` (ship × generator), proportional to stratum size by the largest-
    remainder (Hamilton) rule with at least one per stratum, then the first by spec_key within each stratum. Uses
    only the frozen allocation (spec fields and spec_key), fixed before any planning; identical for every method."""
    strata: dict[tuple, list[dict]] = {}
    for row in sorted(rows, key=lambda r: r["spec_key"]):
        strata.setdefault(tuple(row["spec"].get(k) for k in keys), []).append(row)
    if n > len(rows):
        raise ValueError(f"requested {n} stratified rows; only {len(rows)} available")
    if n < len(strata):
        raise ValueError(f"{n} rows cannot cover {len(strata)} strata")
    total = len(rows)
    quota = {k: max(1, int(n * len(v) / total)) for k, v in strata.items()}
    remainder = sorted(strata, key=lambda k: (-(n * len(strata[k]) / total - int(n * len(strata[k]) / total)), k))
    i = 0
    while sum(quota.values()) < n:
        k = remainder[i % len(remainder)]
        if quota[k] < len(strata[k]):
            quota[k] += 1
        i += 1
    while sum(quota.values()) > n:  # the min-1 floor overshot: trim the largest strata
        k = max(quota, key=lambda q: (quota[q], q))
        quota[k] -= 1
    return [row for k in sorted(strata) for row in strata[k][:min(quota[k], len(strata[k]))]]


def nested_subset(rows: list[dict], n: int, subset: int | None) -> list[dict]:
    """The stratified n, and if ``subset`` is given, the stratified ``subset`` drawn *from those n* by the same rule.
    Within a stratum both take the first rows by spec_key, so the subset is nested in the n (leader decision: 6.2
    runs on all tiny/small/medium plus a stratified 10 of C1's 20 full specs; both runs share those 10)."""
    picked = stratified_selection(rows, n)
    return picked if subset is None else stratified_selection(picked, subset)


def count_outcomes_after_export(run_dir) -> dict:
    """Harness mode: per-instance outcome files come from the export; write them first, then count."""
    from run_folder import export_harness_records, outcome_summary

    export_harness_records(run_dir)
    return outcome_summary(run_dir)


# Leader decisions (docs/compute.md, 29 Oct): C1 = all tiny/small/medium + a stratified 20 full at best-of-8.
# 6.2 = all tiny/small + the paired full 10 at best-of-8 (run 6.2-a), and a stratified paired 40 of the 75 medium at
# best-of-4 (run 6.2-b). One harness run has one planner, hence one k, so 6.2 is two runs. The 10 and the 40 are
# nested in C1's 20 and 75 (``nested_subset``), so the paired rows are the same instances.
PRESETS = {
    "c1": {"limits": ["tiny=75", "small=75", "medium=75", "full=20"], "stratify": ["full=20"], "subset": None,
           "n_samples": 8},
    "6.2-a": {"limits": ["tiny=75", "small=75", "full=20"], "stratify": ["full=20"], "subset": ["full=10"],
              "n_samples": 8},
    "6.2-b": {"limits": ["medium=75"], "stratify": ["medium=75"], "subset": ["medium=40"], "n_samples": 4},
}


def apply_preset(a, defaults: dict) -> None:
    preset = PRESETS[a.preset]
    for key, value in preset.items():
        if getattr(a, key) != defaults[key]:
            raise SystemExit(f"--preset {a.preset} sets --{key.replace('_', '-')}; don't pass it as well")
        setattr(a, key, value)


def _port_mask():
    import importlib.util

    path = REPO / "experiments/mvp/strategy/k1b/port_mask.py"
    spec = importlib.util.spec_from_file_location("k1b_port_mask", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_planner(a):
    from pooled_planner import PooledStowLMPlanner

    kw = dict(n_samples=a.n_samples, temperature=a.temperature, max_tokens=a.max_tokens,
              mask_workers=a.mask_workers, c2=a.c2, trace_dir=a.run_dir)
    if a.model_path:
        kw["model_path"] = a.model_path
    if a.adapter:
        kw["adapter_path"] = a.adapter
    if getattr(a, "d79", False):
        from stowage.feasibility import FeasibilityConfig

        # D79 (leader, 29 Oct): the MVP's C2-off setting is a real engine config, not the tainting test stub; the
        # full checker stays the gate. Workers run unstubbed (c2=True only means "no stub" in pool.py).
        kw.update(engine_config=FeasibilityConfig(extra={"global_screen": 0}), c2=True)
    kw.update(backtrack=getattr(a, "backtrack", 0), greedy_first=getattr(a, "greedy_first", False),
              candidate_finalize_s=getattr(a, "candidate_finalize_s", None),
              last_resort=not getattr(a, "no_last_resort", False))
    if getattr(a, "securing", False):
        from llm.securing import securing_decoder_factory
        from llm.securing.mask_filter import wrap_engine

        # D95: the necessary deck-securing envelope composed over the production mask (in-thread only)
        kw.update(base_factory=securing_decoder_factory(), engine_wrapper=wrap_engine)
    if getattr(a, "lookahead", False):
        from llm.lookahead import lookahead_decoder_factory
        from llm.lookahead.mask_filter import wrap_engine as lookahead_wrap
        from llm.securing.mask_filter import wrap_engine as securing_wrap

        # necessary-only dead-end look-ahead (mvp-construct); its factory already composes the D95 securing layer
        kw.update(base_factory=lookahead_decoder_factory(securing=True),
                  engine_wrapper=lambda engine: lookahead_wrap(securing_wrap(engine)))
        if getattr(a, "port_rule", False):  # D190: the port rule as an instruction layer under look-ahead and securing
            port_wrap = _port_mask().wrap_engine
            kw.update(base_factory=lookahead_decoder_factory(securing=True, engine_wrapper=port_wrap),
                      engine_wrapper=lambda engine: lookahead_wrap(securing_wrap(port_wrap(engine))))
    return PooledStowLMPlanner(**kw)


def second_finalize_refused(result) -> bool:
    """D139 equal finisher budget: ``run_planner`` finalizes any plan without ballast, so a candidate whose finisher
    ran out of budget, or whose finished plan the checker rejected, would otherwise get a second, longer try that
    two-step never gets (measured: a 120 s timeout rescued by the 480 s pipeline pass). True when selection already
    tried this result's candidates and none passed. Selection's own calls carry no such metrics."""
    metrics = getattr(result, "metrics", None) or {}
    plan = getattr(result, "plan", None)
    return bool(getattr(result, "status", None) == "ok" and plan is not None and plan.ballast is None
                and metrics.get("n_candidates_checked") and not metrics.get("n_checker_feasible"))


@contextlib.contextmanager
def ballast_v4_finalize(enabled: bool, cap_s: float | None = None, decode_only: bool = False):
    """D88: STOWAGE_BALLAST_V4=1 only while ``stowage.planner.finalize`` runs (the planner's per-sample finishing and
    the pipeline's), unset for decoding, the checker and replay. ballast.py reads the variable at call time.
    ``cap_s`` (D167): no finisher call exceeds --candidate-finalize-s, including the pass@1 diagnostic on sample 0, which
    would otherwise get the whole remaining per-instance limit.
    ``decode_only`` (D183): no finisher runs, so selection ends at once; the stored candidates are judged elsewhere."""
    import os

    if not enabled:
        yield
        return
    import stowage.planner as sp

    if os.environ.get("STOWAGE_BALLAST_V4") is not None:
        raise SystemExit("STOWAGE_BALLAST_V4 must be unset globally; --ballast-v4-finalize scopes it to finalize")
    original = sp.finalize

    def finalize(instance, result, *args, **kw):
        if decode_only:
            return result.model_copy(update={"status": "error", "message": "decode only: finisher skipped"})
        if second_finalize_refused(result):  # one finisher attempt per candidate (D139): same as two-step
            metrics = {**result.metrics, "second_finalize_refused": True}
            return result.model_copy(update={"status": "error", "metrics": metrics,
                                             "message": "No second finalize: the candidate already had its one attempt"})
        if cap_s is not None and kw.get("time_limit_s") is not None:
            kw["time_limit_s"] = min(kw["time_limit_s"], cap_s)
        os.environ["STOWAGE_BALLAST_V4"] = "1"
        try:
            return original(instance, result, *args, **kw)
        finally:
            os.environ.pop("STOWAGE_BALLAST_V4", None)
    finalize.__wrapped__ = original
    sp.finalize = finalize
    try:
        yield
    finally:
        sp.finalize = original


@contextlib.contextmanager
def store_candidates(folder: str | None):
    """D183 opt-in: write every sample's plan to <folder>/<instance id>/sample-NN.json as it is decoded (run_c1 otherwise
    keeps only the selected plan). A dead-end sample is stored with plan null. Without --store-candidates this does nothing."""
    if folder is None:
        yield
        return
    import llm.stowlm.backtrack as bt

    original = bt.generate_backtracking
    counts: Counter = Counter()

    def generate(model, prompt, decoder, *, temperature, seed=0, **kw):
        result, stats = original(model, prompt, decoder, temperature=temperature, seed=seed, **kw)
        iid = decoder.inner.engine.instance.id
        index = counts[iid]
        counts[iid] += 1
        plan = decoder.inner.engine.to_plan().model_dump(mode="json") if result.status == "complete" else None
        path = Path(folder) / iid / f"sample-{index:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"instance_id": iid, "sample": index, "status": result.status, "temperature": temperature,
                                         "seed": seed, "backtracks": stats["backtracks"], "plan": plan}))
        temporary.replace(path)
        return result, stats
    bt.generate_backtracking = generate
    try:
        yield
    finally:
        bt.generate_backtracking = original


def run_harness(a, planner) -> dict:
    from eval.harness import run_evaluation
    from eval.splits import DEFAULT_MANIFEST

    from run_folder import aggregate_dead_ends, build_manifest, export_harness_records, write_manifest

    import inspect

    limits = {k: int(v) for k, v in (x.split("=") for x in a.limits)}
    if getattr(planner, "num_workers", 1) > HARNESS_WORKER_CAP:
        raise SystemExit(f"--mask-workers {planner.num_workers} exceeds the harness cap of {HARNESS_WORKER_CAP}")
    extra, selection = {"partial": bool(limits)}, None  # limited official allocations are labelled partial
    if getattr(a, "stratify", None):
        from eval.harness import select_instances

        if "spec_keys" not in inspect.signature(run_evaluation).parameters:
            raise SystemExit("--stratify needs eval.harness.run_evaluation(spec_keys=...)")
        subsets = dict(x.split("=") for x in (getattr(a, "subset", None) or []))
        stratified = dict(x.split("=") for x in a.stratify)
        selection, keys = {}, []
        for rung in limits:
            common = dict(split=a.split, rungs=(rung,), tiny_run=not a.no_stub,
                          split_manifest=a.split_manifest or DEFAULT_MANIFEST)
            if rung in stratified:
                rows = select_instances(a.instances, limits=None, **common)
                sub = subsets.get(rung)
                picked = nested_subset(rows, int(stratified[rung]), None if sub is None else int(sub))
            else:  # unstratified rungs keep the harness's first-N-by-spec_key rule
                picked = select_instances(a.instances, limits={rung: limits[rung]}, **common)
            selection[rung] = [r["spec_key"] for r in picked]
            keys += selection[rung]
        extra = {"spec_keys": keys, "partial": True}
        limits = None  # spec_keys replace the per-rung prefixes
        Path(a.run_dir).mkdir(parents=True, exist_ok=True)
        (Path(a.run_dir) / "selection.json").write_text(json.dumps(
            {"rule": "ship x generator, largest remainder, min 1 per stratum, first by spec_key; --subset re-applies "
                     "the rule to the stratified set (nested); other rungs: first N by spec_key",
             "stratify": a.stratify, "subset": getattr(a, "subset", None), **selection}, indent=1))
    summary = run_evaluation(planner, run_dir=a.run_dir, instances_dir=a.instances, split=a.split,
                             rungs=tuple(dict.fromkeys(selection or limits)), limits=limits, workers=1,
                             tiny_run=not a.no_stub, seed=a.seed, time_limit_s=a.time_limit,
                             split_manifest=a.split_manifest or DEFAULT_MANIFEST, **extra)
    config = json.loads((Path(a.run_dir) / "config.json").read_text())
    root = config.get("regen_manifest_sha256") or _sha(Path(a.instances) / "manifest.json")
    write_manifest(a.run_dir, build_manifest(
        planner, temperature=a.temperature, n_samples=a.n_samples, seed=a.seed, mask_workers=a.mask_workers,
        c2=D79_LABEL if getattr(a, "d79", False) else a.c2, epoch_id=config.get("epoch_id"), instance_root_sha256=root,
        mode="official" if a.no_stub else "diagnostic", method=method_label(a.adapter, a.n_samples, a.temperature),
        selection=selection, outcomes=count_outcomes_after_export(a.run_dir), dead_ends=aggregate_dead_ends(
            planner_metrics((json.loads(p.read_text()).get("result") or {}).get("metrics") or {})
            for p in sorted((Path(a.run_dir) / "instances").glob("*.json")))))
    summary["contract_instances"] = export_harness_records(a.run_dir)
    return summary


def loose_files(paths: list[str], limit: int | None) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        files += sorted(x for x in p.glob("*.json") if not x.name.endswith(".audit.json")) if p.is_dir() else [p]
    return files[:limit] if limit else files


def loose_config(a, planner, files: list[Path], refs: dict) -> dict:
    """Identity of a loose diagnostic run; resume refuses any change. Includes the reference records used for the
    gap columns (code review: a changed --references must not silently reuse old comparison values)."""
    from stowage.frozen import active_stubs, current_provenance

    inputs = [{"path": str(f.resolve().relative_to(REPO)), "sha256": _sha(f)} for f in files]
    used = {i["sha256"]: refs[i["sha256"]] for i in inputs if i["sha256"] in refs}
    return {"driver": "eval-c1/run_c1.py loose", "diagnostic": True, "official": False,
            "planner": planner.name, "planner_version": planner.version,
            "planner_parameters": planner.evaluation_config, "seed": a.seed, "time_limit_s": a.time_limit,
            "allow_repair": not getattr(a, "no_repair", False),
            "finalize_env": {"STOWAGE_BALLAST_V4": "1"} if getattr(a, "ballast_v4_finalize", False) else {},
            "versions": current_provenance().versions, "active_stubs": active_stubs(),
            "references": {"root": a.references, "sha256": _digest(used), "n": len(used)},
            "method": method_label(a.adapter, a.n_samples, a.temperature), "inputs": inputs}


def run_loose(a, planner) -> dict:
    import mlx.core as mx

    from stowage.frozen import active_stubs, current_provenance
    from stowage.io import load_instance
    from stowage.planner import run_planner

    from run_folder import aggregate_dead_ends, build_manifest, outcome_summary, write_instance, write_manifest

    run_dir = Path(a.run_dir)
    files = loose_files(a.loose, a.limit)
    refs = load_references(Path(a.references) if a.references else None)
    config = loose_config(a, planner, files, refs)
    fingerprint = _digest(config)
    (run_dir / "instances").mkdir(parents=True, exist_ok=True)
    cfg_path = run_dir / "config.json"
    if cfg_path.exists():
        if json.loads(cfg_path.read_text()) != json.loads(json.dumps(config, default=str)):
            raise SystemExit("run configuration changed (planner, weights, versions, stubs or inputs); "
                             "use a new --run-dir")
    else:
        cfg_path.write_text(json.dumps(config, indent=1, default=str))
    records = []
    for f in files:
        sha = _sha(f)
        out = run_dir / "instances" / f"{sha[:16]}.json"
        if out.exists():
            rec = json.loads(out.read_text())
            if rec.get("config_sha256") != fingerprint or rec.get("input_sha256") != sha:
                raise SystemExit(f"checkpoint {out} does not match this run")
            records.append(rec)
            continue
        instance = load_instance(f)
        mx.reset_peak_memory()
        t = time.perf_counter()
        result, report = run_planner(planner, instance, seed=a.seed, time_limit_s=a.time_limit,
                                     allow_repair=not getattr(a, "no_repair", False))
        write_instance(run_dir, instance.id, result.model_dump(mode="json"),
                       None if report is None else report.model_dump(mode="json"))
        pipeline_s = time.perf_counter() - t
        objective = None
        if report is not None and report.passed and report.count is not None:
            objective = report.count.weighted_nonproductive
        ref = refs.get(sha, {})
        exact = (ref.get("exact") or {}).get("objective")
        lb = next((r.get("lower_bound_36") for r in ref.values() if r.get("lower_bound_36") is not None), None)
        m = result.metrics
        rec = {"config_sha256": fingerprint, "input": config["inputs"][files.index(f)]["path"], "input_sha256": sha,
               "instance_id": instance.id, "rung": instance.size_class, "n_boxes": len(instance.load_list),
               "status": result.status, "shippable": result.shippable, "repaired": result.repaired,
               "report_passed": None if report is None else report.passed,
               "objective": objective, "exact_objective": exact,
               "scalable_objective": (ref.get("scalable") or {}).get("objective"),
               "twostep_objective": (ref.get("twostep") or {}).get("objective"), "lower_bound_36": lb,
               "gap_to_exact": None if objective is None or exact is None else objective - exact,
               "gap_to_lb36": None if objective is None or lb is None else objective - lb,
               "pipeline_s": round(pipeline_s, 2), "plan_s": planner_metrics(m).get("pool_plan_wall_s"),
               "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 2),
               "message": result.message, "metrics": m, "provenance_stubs": sorted(result.provenance.stubs)}
        (out.with_suffix(".tmp")).write_text(json.dumps(rec, indent=1, default=str))
        (out.with_suffix(".tmp")).replace(out)
        records.append(rec)
        print(json.dumps({k: rec[k] for k in ("instance_id", "status", "shippable", "repaired", "objective",
                                              "exact_objective", "pipeline_s")}), flush=True)
    manifest = build_manifest(
        planner, temperature=a.temperature, n_samples=a.n_samples, seed=a.seed, mask_workers=a.mask_workers,
        c2=D79_LABEL if getattr(a, "d79", False) else a.c2, epoch_id=None,
        instance_root_sha256=_digest(config["inputs"]),
        mode="mvp-loose (D79)" if getattr(a, "d79", False) else "diagnostic-loose",
        method=config["method"], dead_ends=aggregate_dead_ends(planner_metrics(r["metrics"]) for r in records),
        outcomes=outcome_summary(run_dir))
    manifest.update(mask_layer=getattr(planner, "mask_layer", None), finalize_env=config["finalize_env"],
                    allow_repair=config["allow_repair"])
    write_manifest(run_dir, manifest)
    summary = summarize(records)
    summary.update(config_sha256=fingerprint, label=("MVP v4 (loose frozen-corpus inputs, D79 C2 off)" if getattr(a, "d79", False)
                          else "DIAGNOSTIC (loose inputs; not a docs/evaluation.md row)"),
                   active_stubs=config["active_stubs"])
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def planner_metrics(metrics: dict) -> dict:
    """The StowLM draft's metrics: after a 5.7 repair, ``run_planner`` returns the repair's metrics and keeps the
    draft's under ``repair_original_metrics``."""
    if "gen_wall_s" in metrics or "n_samples" in metrics:
        return metrics
    return metrics.get("repair_original_metrics") or {}


def summarize(records: list[dict]) -> dict:
    n = len(records)
    shipped = [r for r in records if r["shippable"]]
    own = [r for r in shipped if not r["repaired"]]
    gen = [g for r in records if (g := planner_metrics(r["metrics"])).get("gen_wall_s")]
    tokens = sum(g.get("gen_tokens", 0) for g in gen)
    wall = sum(g.get("gen_wall_s", 0) for g in gen)
    steps = sum(g.get("gen_steps", 0) for g in gen)
    host = sum(g.get("gen_host_mask_s", 0) for g in gen)
    stall = sum(g.get("gen_host_wait_s", 0) for g in gen)
    gaps = [r["gap_to_exact"] for r in shipped if r["gap_to_exact"] is not None]
    return {"n": n, "shipped": len(shipped), "shipped_pass_rate": (sum(1 for r in shipped if r["report_passed"])
                                                                  / len(shipped)) if shipped else None,
            "fallback_rate": sum(1 for r in records if r["repaired"]) / n if n else None,
            "stowlm_own_shipped": len(own), "status_counts": {s: sum(r["status"] == s for r in records)
                                                              for s in {r["status"] for r in records}},
            "mean_gap_to_exact_shipped": sum(gaps) / len(gaps) if gaps else None,
            "decode_tok_s": tokens / wall if wall else None, "decode_steps": steps,
            "mask_host_ms_per_step": 1e3 * host / steps if steps else None,
            "gpu_host_stall_fraction": stall / wall if wall else None,
            "pipeline_s_mean": sum(r["pipeline_s"] for r in records) / n if n else None,
            "prefill_s_mean": sum(g.get("gen_prefill_s", 0) for g in gen) / len(gen) if gen else None,
            "peak_memory_gb_max": max((r["peak_memory_gb"] for r in records), default=None),
            "samples": sum(planner_metrics(r["metrics"]).get("n_samples", 0) for r in records),
            "samples_complete": sum(planner_metrics(r["metrics"]).get("n_complete", 0) or 0 for r in records),
            "samples_dead_end": sum(planner_metrics(r["metrics"]).get("n_dead_end", 0) or 0 for r in records),
            "candidates_checker_feasible": sum(planner_metrics(r["metrics"]).get("n_checker_feasible", 0) or 0
                                               for r in records),
            "no_decode": {r["instance_id"]: (r["message"] or "")[:160] for r in records
                          if not planner_metrics(r["metrics"]).get("gen_wall_s")}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--instances", help="frozen-epoch instance root (harness mode; C1)")
    src.add_argument("--loose", nargs="+", help="instance files/dirs outside a release (diagnostic only)")
    ap.add_argument("--split", default="test")
    ap.add_argument("--split-manifest", default=None)
    ap.add_argument("--limits", nargs="+", default=["tiny=75", "small=75", "medium=75", "full=75"])
    ap.add_argument("--limit", type=int, default=None, help="loose mode: first N files")
    ap.add_argument("--references", default=None, help="loose mode: pilot-d58 style runs/ root")
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--adapter", default=None, help="8.1 adapter dir (needs its provenance sidecar); none = base")
    ap.add_argument("--n-samples", type=int, default=16)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--max-tokens", type=int, default=20_000)
    ap.add_argument("--time-limit", type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mask-workers", type=int, default=OFFICIAL_MASK_WORKERS)
    ap.add_argument("--subset", nargs="+", default=None,
                    help="harness mode, with --stratify: rung=M takes a stratified M nested in the stratified N "
                         "(6.2: --stratify full=20 --subset full=10, so C1 and 6.2 share those 10)")
    ap.add_argument("--stratify", nargs="+", default=None,
                    help="harness mode: rung=N stratified by ship x generator (e.g. full=20); same for every method")
    ap.add_argument("--c2", action="store_true", help="real engine with C2 (production); default C2 off = stub")
    ap.add_argument("--d79", action="store_true",
                    help="MVP (v4): C2 off via FeasibilityConfig(extra={'global_screen': 0}), no stub (D79)")
    ap.add_argument("--securing", action="store_true",
                    help="D95: compose the necessary deck-securing mask layer (llm.securing; --mask-workers 0)")
    ap.add_argument("--ballast-v4-finalize", action="store_true",
                    help="D88: STOWAGE_BALLAST_V4=1 scoped to planner.finalize only")
    ap.add_argument("--lookahead", action="store_true",
                    help="compose the necessary future-capacity look-ahead layer (llm.lookahead; includes securing)")
    ap.add_argument("--backtrack", type=int, default=0,
                    help="bounded backtracking decode: max rewinds per sample (0 = off; sequential batch-1 decode)")
    ap.add_argument("--greedy-first", action="store_true", help="sample 0 greedy, the rest at --temperature")
    ap.add_argument("--candidate-finalize-s", type=float, default=None,
                    help="finisher-aware best-of-k: ballast budget per candidate (last resort gets the rest)")
    ap.add_argument("--no-last-resort", action="store_true",
                    help="equal finisher budget: never give a budget-capped candidate the remaining time (D128.5)")
    ap.add_argument("--store-candidates", default=None, help="D183: folder for every sample's plan (needs --backtrack or --greedy-first)")
    ap.add_argument("--port-rule", action="store_true", help="D190: enforce the D185 port rule in the mask (needs --lookahead)")
    ap.add_argument("--decode-only", action="store_true", help="D183: skip the finisher and selection; use with --store-candidates")
    ap.add_argument("--no-repair", action="store_true",
                    help="no 5.7 repair fallback: the model's own plans only (ablation rows)")
    ap.add_argument("--no-stub", action="store_true", help="harness mode: official run (needs --c2, clean inputs)")
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None,
                    help="harness mode: the leader's C1 / 6.2 selections (limits, stratification, subset, k)")
    a = ap.parse_args()
    if a.preset:
        if not a.instances:
            raise SystemExit("--preset needs --instances (harness mode)")
        apply_preset(a, {k: ap.get_default(k) for k in ("limits", "stratify", "subset", "n_samples")})
    if a.c2 and a.d79:  # rev-v4-a P2: --c2 means the C2-on engine; D79 is C2 off by config
        raise SystemExit("--c2 and --d79 conflict (D79 runs C2 off by configuration)")
    if a.lookahead and not a.securing:
        raise SystemExit("--lookahead composes over the securing layer: pass --securing too")
    if (a.securing or a.lookahead) and a.mask_workers:
        raise SystemExit("--securing runs in-thread: pass --mask-workers 0")
    if a.instances and (a.securing or a.ballast_v4_finalize):
        raise SystemExit("--securing / --ballast-v4-finalize are loose mode only (harness identity lacks them)")
    if a.instances and a.no_repair:  # rev-v4-a P1: the harness path has no allow_repair; refuse, never ignore
        raise SystemExit("--no-repair is loose mode only (the harness always allows 5.7 repair)")
    if a.no_stub and not (a.c2 or a.d79):
        raise SystemExit("--no-stub (official) requires --c2 or --d79")
    if a.no_stub and a.loose:
        raise SystemExit("--no-stub (official) is harness mode only; --loose runs are diagnostic")
    if a.no_stub and a.time_limit is None:
        a.time_limit = OFFICIAL_TIME_LIMIT_S
    if a.mask_workers > HARNESS_WORKER_CAP:
        raise SystemExit(f"--mask-workers {a.mask_workers} exceeds the harness cap of {HARNESS_WORKER_CAP}")
    import stowage.interfaces  # noqa: F401
    from stowage.frozen import stub_override

    from pool import STUB_REASON, c2_off_engine

    planner = make_planner(a)
    ctx = (contextlib.nullcontext() if a.c2 or a.d79
           else stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON))
    try:
        # the cap only under --no-repair: 5.7 repair keeps the finisher allocation 9.1 had (rev-recipec P2-1)
        cap_s = a.candidate_finalize_s if a.no_repair else None
        with ctx, store_candidates(a.store_candidates), ballast_v4_finalize(a.ballast_v4_finalize, cap_s, a.decode_only):
            summary = run_harness(a, planner) if a.instances else run_loose(a, planner)
    finally:
        planner.close()
    print(json.dumps({k: v for k, v in summary.items() if k != "by_rung"}, indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
