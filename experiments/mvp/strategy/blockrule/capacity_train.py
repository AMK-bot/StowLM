"""Final TRAIN-only v2 controller. Root launches this after reviewing the code.

Wait for v1 computations and the old supervisor to drain. Preserve exact initial
artifacts, reuse audited identical-mask certificates, and evaluate only changed
medium permission masks. No validation is launched by this program.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE), str(ROOT)]
import study
from experiments.mvp.strategy.blockrule.capacity_candidate import capacity_relaxed_layout


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def status(phase, **fields):
    study.atomic_json(HERE / "capacity-status.json", dict(
        phase=phase, pid=os.getpid(), updated_at=time.time(), **fields))


def initial_dir(name):
    archived = HERE / "runs" / ("initial-" + name)
    return archived if archived.exists() else HERE / "runs" / name


def physical_epoch(initial_run):
    """Study's sole permitted drift is the explicitly archived metadata change."""
    for relative, expected in initial_run["sources"].items():
        path = ROOT / relative
        if relative == str((HERE / "study.py").relative_to(ROOT)):
            archive = HERE / "versions/v1/study.py"
            if not archive.is_file() or study.digest(archive) != expected:
                raise RuntimeError("Exact initial study.py archive missing/mismatched")
            def compute_tree(file):
                tree = ast.parse(file.read_text())
                tree.body = [node for node in tree.body if not (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "source_snapshot")]
                class AuthorizedPolicyImport(ast.NodeTransformer):
                    def visit_ImportFrom(self, node):
                        if (node.level == 0 and node.module == "capacity_candidate"
                                and len(node.names) == 1
                                and node.names[0].name == "capacity_relaxed_layout"
                                and node.names[0].asname == "port_blocking_layout"):
                            return ast.parse("from rule import port_blocking_layout").body[0]
                        return node
                for node in tree.body:
                    if isinstance(node, ast.FunctionDef) and node.name == "run_map":
                        AuthorizedPolicyImport().visit(node)
                    # Final CLI bootstrap may add exactly one str(ROOT) entry
                    # to the existing sys.path[:0] list; no other edits normalize.
                    if (isinstance(node, ast.Assign) and len(node.targets) == 1
                            and ast.dump(node.targets[0]) == ast.dump(ast.parse("sys.path[:0] = []").body[0].targets[0])
                            and isinstance(node.value, ast.List)):
                        root_entry = ast.dump(ast.parse("str(ROOT)", mode="eval").body)
                        matches = [entry for entry in node.value.elts if ast.dump(entry) == root_entry]
                        if len(matches) == 1:
                            node.value.elts = [entry for entry in node.value.elts if ast.dump(entry) != root_entry]
                return ast.dump(tree, include_attributes=False)
            if compute_tree(path) != compute_tree(archive):
                raise RuntimeError("Current study.py changed beyond metadata/authorized policy import and ROOT bootstrap")
        elif not path.is_file() or study.digest(path) != expected:
            raise RuntimeError(f"Pinned physical source changed: {relative}")


def final_sources(policy):
    frozen = study.read_json(policy)
    for key, path in (("candidate_sha256", HERE / "capacity_candidate.py"),
                      ("base_rule_sha256", HERE / "rule.py"),
                      ("medium_manifest_sha256", HERE / "cohort-medium.json")):
        if frozen.get(key) != study.digest(path):
            raise RuntimeError(f"Frozen capacity policy does not match {key}")
    sources = study.source_snapshot()
    for path in (Path(__file__), HERE / "capacity_candidate.py", policy):
        relative = str(path.resolve().relative_to(ROOT))
        if sources.get(relative) != study.digest(path):
            raise RuntimeError(f"Current study.source_snapshot must pin {relative}")
    return sources


def ready():
    if (HERE / "train-freeze.json").exists() or (HERE / "validation-once.json").exists():
        raise RuntimeError("Final train cannot start after a freeze/validation claim")
    supervisor = HERE / "supervisor-status.json"
    if not supervisor.exists() or study.read_json(supervisor).get("phase") != "error":
        return False
    for name in ("small-train", "medium-train"):
        folder = initial_dir(name)
        done = folder / "completion.json"
        if not done.exists() or (folder / "controller.lock").exists():
            return False
        if study.read_json(done).get("errors"):
            raise RuntimeError(f"Initial {name} has errors; not inheritable")
    return True


def clone_initial(name, policy, sources):
    original = initial_dir(name)
    final = HERE / "runs" / ("final-" + name)
    run = study.read_json(original / "run.json")
    physical_epoch(run)
    origin = dict(campaign=name, initial_run_sha256=study.digest(original / "run.json"),
                  initial_completion_sha256=study.digest(original / "completion.json"),
                  initial_sources_sha256=json_hash(run["sources"]),
                  initial_study_normalization="source_snapshot metadata, authorized final rule import and one ROOT path bootstrap only",
                  policy_sha256=study.digest(policy), final_sources_sha256=json_hash(sources))
    if final.exists():
        if study.read_json(final / "capacity-origin.json") != origin:
            raise RuntimeError(f"Existing {final} has a different origin/epoch")
    else:
        temporary = final.with_name(final.name + f".copy-{os.getpid()}")
        if temporary.exists():
            raise RuntimeError(f"Uncommitted clone exists: {temporary}")
        shutil.copytree(original, temporary)
        # Preserve original metadata exactly before writing the final epoch.
        shutil.copy2(temporary / "run.json", temporary / "initial-run.json")
        shutil.copy2(temporary / "completion.json", temporary / "initial-completion.json")
        (temporary / "completion.json").unlink()
        study.atomic_json(temporary / "capacity-origin.json", origin)
        study.atomic_json(temporary / "initial-sources.json", run["sources"])
        temporary.rename(final)
    record = dict(run, sources=sources, capacity_epoch="v2-boundary-capacity",
                  inheritance=dict(**origin, initial_archive=str((HERE / "runs" / ("initial-" + name)).relative_to(ROOT)),
                                   manifest="inheritance.json"))
    # A completed resumed run also contains the manifest digest added at commit.
    if (final / "run.json").exists() and (final / "completion.json").exists():
        existing = study.read_json(final / "run.json")
        comparable = dict(existing)
        comparable.pop("inheritance_manifest_sha256", None)
        if comparable != record:
            raise RuntimeError(f"Completed final run metadata changed: {name}")
    else:
        study.atomic_json(final / "run.json", record)
    return original, final, run, origin


def artifact_in(path, campaign, directory):
    """Rebase original lexical stable paths, without following published aliases."""
    path = Path(path)
    absolute = path if path.is_absolute() else ROOT / path
    prefix = HERE / "runs" / campaign
    try:
        relative = absolute.relative_to(prefix)
    except ValueError as exc:
        raise RuntimeError(f"Artifact is outside initial campaign: {path}") from exc
    return directory / relative


def inherited_record(record, arm, variant, map_id, campaign, original, final, origin, instance, model, layout):
    """Audit passing plans again; reuse, rather than rerun, original full checks."""
    from layout import audit_layout
    from stowage.model import PlannerResult
    value = dict(record)
    attempted = value.get("checker_attempted") is True
    provenance = dict(kind=("inherited full-check certificate; no new checker call" if attempted
                            else "inherited recorded outcome; checker not reached; no new checker call"),
                      original_run_sha256=origin["initial_run_sha256"],
                      original_sources_sha256=origin["initial_sources_sha256"],
                      physical_sources_verified=True, checker_called_this_campaign=False)
    check_file = Path("maps") / map_id / "checks" / f"{arm}-{variant}.json"
    source_check = original / check_file
    provenance.update(original_check_path=str((HERE / "runs" / ("initial-" + campaign) / check_file).relative_to(ROOT)),
                      original_check_sha256=study.digest(source_check))
    if value.get("checker_attempted"):
        report = artifact_in(value["report_path"], campaign, original)
        finished = artifact_in(value["finished_path"], campaign, original)
        copied_report = artifact_in(value["report_path"], campaign, final)
        copied_finished = artifact_in(value["finished_path"], campaign, final)
        for before, after in ((report, copied_report), (finished, copied_finished)):
            if study.digest(before) != study.digest(after):
                raise RuntimeError(f"Copied certificate artifact changed: {after}")
        certificate = study.read_json(report)
        result = PlannerResult.model_validate(study.read_json(finished))
        if certificate["passed"] is not value["checker_passed"]:
            raise RuntimeError("Initial checker report flag mismatch")
        provenance.update(original_report_sha256=study.digest(report), original_finished_sha256=study.digest(finished),
                          original_report_path=str((HERE / "runs" / ("initial-" + campaign) / report.relative_to(original)).relative_to(ROOT)),
                          original_finished_path=str((HERE / "runs" / ("initial-" + campaign) / finished.relative_to(original)).relative_to(ROOT)))
        if value.get("passed"):
            if result.status != "ok" or result.plan is None or certificate["passed"] is not True:
                raise RuntimeError("Initial passing certificate incomplete")
            if certificate["count"]["weighted_nonproductive"] != value["moves"]:
                raise RuntimeError("Initial checked move count differs")
            violations = audit_layout(instance, result.plan, layout, model=model) if layout else []
            if violations:
                raise RuntimeError(f"Inherited finished-plan layout audit failed: {violations}")
            provenance.update(layout_audit_reran=True, layout_violations=[],
                              final_layout_fingerprint=layout.fingerprint if layout else None)
    elif value.get("passed"):
        raise RuntimeError("Passing initial record did not attempt checker")
    value.update(inherited_outcome=True, inherited_certificate=attempted, inheritance=provenance)
    study.atomic_json(final / check_file, value)
    return value


def initialize():
    study.atomic_json(HERE / "runs/capacity-control/owned" / f"{os.getpid()}.json",
                      dict(pid=os.getpid(), role="v2 train map worker", started_at=time.time()))


def prepare_rule_cache(folder, fingerprint, sources):
    """Keep copied v1 artifacts; establish a distinct resumable v2 namespace."""
    marker = folder / "rule-cache-v2.json"
    value = dict(layout_fingerprint=fingerprint, sources_sha256=json_hash(sources))
    if marker.exists():
        if study.read_json(marker) != value:
            raise RuntimeError("Existing candidate rule cache has wrong mask/epoch")
        return
    # New v2 artifacts use a distinct layout id. Every copied v1 file stays
    # intact; old absolute references retain their bytes after alias publication.
    for category in ("evals", "plans", "checks", "reports", "finished"):
        if any((folder / category).glob("rule-v2*.json")):
            raise RuntimeError("Unmarked candidate artifacts exist; refusing unverified cache")
    study.atomic_json(marker, value)


def run_map(job, campaign, original, final, config, origin, sources):
    from layout import BlockLayout, BlockModel, _HOOK_MODULES
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1
    original, final = Path(original), Path(final)
    folder = final / "maps" / job["map_id"]
    cached = study.read_json(folder / "summary.json")
    if cached.get("capacity_epoch") == "v2-boundary-capacity" and cached.get("complete"):
        if cached.get("capacity_origin") != origin:
            raise RuntimeError("Cached final summary has wrong origin")
        return cached
    summary = dict(job=job, pid=os.getpid(), complete=False, arms={}, selected={},
                   capacity_epoch="v2-boundary-capacity", capacity_origin=origin)
    try:
        if study.digest(ROOT / job["input"]) != job["input_sha256"]:
            raise RuntimeError("Frozen map changed")
        instance = load_instance(ROOT / job["input"])
        model = BlockModel.from_instance(instance)
        for name in _HOOK_MODULES:
            importlib.import_module(name)
        initial = study.read_json(original / "maps" / job["map_id"] / "summary.json")
        if not initial.get("complete"):
            raise RuntimeError("Initial summary incomplete")
        layout = capacity_relaxed_layout(instance, model=model)
        previous = BlockLayout.from_dict(initial["arms"]["rule"]["layout"])
        unchanged = layout.fingerprint == previous.fingerprint
        if campaign == "small-train" and not unchanged:
            raise RuntimeError("Final small policy must be byte-identical in allowed mask")
        summary.update(rule_mask_unchanged=unchanged,
                       initial_summary_sha256=study.digest(original / "maps" / job["map_id"] / "summary.json"),
                       final_layout_fingerprint=layout.fingerprint, initial_layout_fingerprint=previous.fingerprint)
        study.atomic_json(folder / "layout.json", layout.to_dict())
        with rule_freeze_v1(enabled=False):
            for arm in ("default", "rule"):
                if arm == "default" or unchanged:
                    row = dict(initial["arms"][arm])
                    initial_eval = original / "maps" / job["map_id"] / "evals" / f"{arm}.json"
                    row.update(inherited_solver_result=True, solve_performed_this_campaign=False,
                               original_run_sha256=origin["initial_run_sha256"],
                               original_eval_sha256=study.digest(initial_eval),
                               original_eval_path=str((HERE / "runs" / ("initial-" + campaign)
                                   / initial_eval.relative_to(original)).relative_to(ROOT)))
                    if row.get("result_path"):
                        before = artifact_in(row["result_path"], campaign, original)
                        after = artifact_in(row["result_path"], campaign, final)
                        if study.digest(before) != study.digest(after):
                            raise RuntimeError("Copied raw result changed")
                        row["original_result_sha256"] = study.digest(before)
                    restriction = layout if arm == "rule" else None
                    for variant in ("raw", "compact"):
                        row[variant] = inherited_record(row[variant], arm, variant, job["map_id"], campaign,
                                                       original, final, origin, instance, model, restriction)
                    summary["arms"][arm] = row
                else:
                    # Initial files remain intact in both the original and clone.
                    prepare_rule_cache(folder, layout.fingerprint, sources)
                    row = study.headroom.evaluate(instance, model, layout, "rule-v2", "rule-capacity-v2", folder, config)
                    row.update(inherited_solver_result=False, solve_performed_this_campaign=True)
                    for variant in ("raw", "compact"):
                        row[variant] = study.certify(instance, model, row, folder, config["medium_finisher"], variant)
                    summary["arms"][arm] = row
                study.atomic_json(folder / "summary.json", summary)
            default, rule = summary["arms"]["default"], summary["arms"]["rule"]
            for variant in ("raw", "compact"):
                base = default["raw"] if variant == "raw" else study.best_variant(default["raw"], default["compact"])
                candidate = rule["raw"] if variant == "raw" else study.best_variant(rule["raw"], rule["compact"])
                summary["selected"][variant] = dict(default=base, rule=candidate, planner=study.choose(base, candidate))
            summary["complete"] = True
    except Exception as exc:
        summary.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
        study.atomic_json(folder / "summary.json", summary)
    return summary


def publish(campaigns):
    """Recoverable per-alias atomic installation; global commit is status file."""
    for name, info in campaigns.items():
        stable = HERE / "runs" / name
        archive = HERE / "runs" / ("initial-" + name)
        final = info["final"]
        if not archive.exists():
            if stable.is_symlink() or study.digest(stable / "run.json") != info["origin"]["initial_run_sha256"]:
                raise RuntimeError("Initial directory is not the expected immutable run")
            stable.rename(archive)
        if study.digest(archive / "run.json") != info["origin"]["initial_run_sha256"]:
            raise RuntimeError("Archived initial metadata changed")
        if stable.is_symlink():
            if stable.resolve() != final.resolve():
                raise RuntimeError("Stable alias points to an unexpected run")
        elif stable.exists():
            raise RuntimeError("Stable path occupied after archive publication")
        else:
            temporary = stable.with_name(stable.name + f".alias-{os.getpid()}")
            temporary.symlink_to(final.name, target_is_directory=True)
            temporary.replace(stable)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--policy", type=Path, default=HERE / "capacity-policy.json")
    parser.add_argument("--poll-seconds", type=float, default=30.)
    args = parser.parse_args(argv)
    if not args.policy.is_file():
        parser.error("Frozen capacity policy file is required")
    control = HERE / "runs/capacity-control"
    control.mkdir(parents=True, exist_ok=True)
    with study.headroom.controller_lock(control):
        while not ready():
            status("waiting_for_initial_drain_and_supervisor_error")
            time.sleep(min(60., max(1., args.poll_seconds)))
        sources = final_sources(args.policy)
        campaigns = {}
        for name in ("small-train", "medium-train"):
            original, final, initial, origin = clone_initial(name, args.policy, sources)
            campaigns[name] = dict(original=original, final=final, initial=initial, origin=origin)
        if campaigns["small-train"]["initial"]["config"] != campaigns["medium-train"]["initial"]["config"]:
            raise RuntimeError("Initial train configurations differ")
        status("final_train", workers=args.workers, sources_sha256=json_hash(sources))
        errors, rows = [], {name: [] for name in campaigns}
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn"), initializer=initialize) as pool:
            futures = {}
            for name, info in campaigns.items():
                for job in info["initial"]["jobs"]:
                    future = pool.submit(run_map, job, name, str(info["original"]), str(info["final"]),
                                         info["initial"]["config"], info["origin"], sources)
                    futures[future] = (name, job)
            for future in as_completed(futures):
                name, job = futures[future]
                try:
                    row = future.result()
                    rows[name].append(row)
                    if not row.get("complete") or row.get("error"):
                        errors.append(dict(campaign=name, map_id=job["map_id"], error=row.get("error", "incomplete")))
                    print(json.dumps(dict(campaign=name, map_id=job["map_id"], complete=row.get("complete"), error=row.get("error"))), flush=True)
                except Exception as exc:
                    errors.append(dict(campaign=name, map_id=job["map_id"], error=f"{type(exc).__name__}: {exc}"))
        if sources != final_sources(args.policy):
            raise RuntimeError("Final sources changed during v2 train")
        expected_changed = set(study.read_json(args.policy)["changed_medium_seeds"])
        actual_changed = {r["job"]["seed"] for r in rows["medium-train"] if r.get("rule_mask_unchanged") is False}
        if actual_changed != expected_changed:
            errors.append(dict(campaign="medium-train", map_id="policy-mask-census",
                               error=f"Changed seed set {sorted(actual_changed)} != frozen {sorted(expected_changed)}"))
        for name, info in campaigns.items():
            manifest = dict(initial_run_sha256=info["origin"]["initial_run_sha256"],
                            initial_sources_sha256=info["origin"]["initial_sources_sha256"],
                            maps=[dict(map_id=r["job"]["map_id"], unchanged=r.get("rule_mask_unchanged"),
                                       original_summary_sha256=r.get("initial_summary_sha256"),
                                       final_layout_fingerprint=r.get("final_layout_fingerprint")) for r in sorted(rows[name], key=lambda r: r["job"]["map_id"])])
            study.atomic_json(info["final"] / "inheritance.json", manifest)
            record = study.read_json(info["final"] / "run.json")
            record["inheritance_manifest_sha256"] = study.digest(info["final"] / "inheritance.json")
            study.atomic_json(info["final"] / "run.json", record)
            study.atomic_json(info["final"] / "completion.json", dict(maps=len(info["initial"]["jobs"]),
                              errors=[error for error in errors if error["campaign"] == name], ended_at=time.time()))
            physical_epoch(info["initial"])
        if errors:
            raise RuntimeError(f"Final train errors: {errors}")
        status("publishing_aliases")
        publish(campaigns)
        status("train_complete", validation_launched=False,
               final_runs={name: str(info["final"].relative_to(ROOT)) for name, info in campaigns.items()},
               initial_runs={name: str((HERE / "runs" / ("initial-" + name)).relative_to(ROOT)) for name in campaigns},
               sources_sha256=json_hash(sources))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        status("error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        raise
