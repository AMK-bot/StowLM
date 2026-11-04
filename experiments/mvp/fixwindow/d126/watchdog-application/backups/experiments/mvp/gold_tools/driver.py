"""Streaming, resumable MVP gold: solve → ballast → full checker → token mask.

One fresh subprocess per instance prevents stale imports across concurrent source
edits. Only a passing full checker AND complete production mask publish plan.json.
APPROXIMATION: bounded search produces best-found gold unless exact proves optimal.
No repair, rule-freeze OFF, D79 global screen OFF, native threads one.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
from itertools import zip_longest
import json
import math
import os
from pathlib import Path
import resource
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

for _key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

LIVE_ROOT = Path(__file__).resolve().parents[3]
ROOT = LIVE_ROOT
RUNTIME_SNAPSHOT = None
RUNTIME_FILES = None
RUNTIME_EPOCH_SHA256 = None
RUNTIME_RESOURCE_MODULES = []
RUNTIME_RESOURCE_FILES = {}


def epoch_ballast_worker(snapshot, expected_hash, live_root, connection):
    """Bootstrap a fresh spawn before it receives any ballast calculation."""
    global LIVE_ROOT
    LIVE_ROOT = Path(live_root)
    configure_runtime(snapshot, expected_hash)
    from stowage.solver import ballast_deadline
    # configure_runtime wraps the child's future spawns too. Call the saved
    # original directly rather than recurse into that newly installed wrapper.
    ballast_deadline._gold_epoch_original_worker(connection)


def install_epoch_ballast_worker():
    """Carry verified source/resource isolation into deadline helper spawns."""
    filename = RUNTIME_SNAPSHOT / "src/stowage/solver/ballast_deadline.py"
    if not filename.exists():
        return
    from functools import partial
    from stowage.solver import ballast_deadline
    if not hasattr(ballast_deadline, "_gold_epoch_original_worker"):
        ballast_deadline._gold_epoch_original_worker = ballast_deadline._worker
    ballast_deadline._worker = partial(epoch_ballast_worker,
        str(RUNTIME_SNAPSHOT), RUNTIME_EPOCH_SHA256, str(LIVE_ROOT))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_epoch_resource_path(snapshot, files, live_root=None):
    snapshot = Path(snapshot).resolve()
    live_root = Path(live_root or LIVE_ROOT).resolve()
    concrete = type(Path())
    class EpochResourcePath(concrete):
        """APPROXIMATION: relocate pinned resource paths, never evidence bytes."""
        def __init__(self, *parts, **kwargs):
            plain = Path(*parts)
            if plain.is_absolute() and plain.is_relative_to(live_root):
                relative = str(plain.relative_to(live_root))
                if relative in files:
                    plain = snapshot / relative
            super().__init__(str(plain), **kwargs)
        def __truediv__(self, key):
            return type(self)(super().__truediv__(key))
        def joinpath(self, *parts):
            return type(self)(super().joinpath(*parts))
    return EpochResourcePath


def install_epoch_resource_paths():
    global RUNTIME_RESOURCE_MODULES
    proxy = make_epoch_resource_path(RUNTIME_SNAPSHOT, {**RUNTIME_FILES, **RUNTIME_RESOURCE_FILES})
    installed = []
    for name in ("stowage.linear.margin_policy", "stowage.generate.physics_identity"):
        filename = RUNTIME_SNAPSHOT / "src" / (name.replace(".", "/") + ".py")
        if not filename.exists():
            continue
        module = __import__(name, fromlist=["Path"])
        if not Path(module.__file__).resolve().is_relative_to(RUNTIME_SNAPSHOT / "src"):
            raise RuntimeError(f"Cannot relocate resources in a live module: {name}")
        module.Path = proxy
        if hasattr(module, "_ROOT"):
            module._ROOT = proxy(module._ROOT)
        installed.append(name)
    RUNTIME_RESOURCE_MODULES = installed
    return installed


def verify_runtime_resources():
    # ASSUMPTION: the copier verified content before sealing these private
    # read-only files. The core-hashed resource manifest commits their hashes
    # and post-chmod stat fingerprints; changed resources are rejected.
    for name, pin in RUNTIME_RESOURCE_FILES.items():
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f"Runtime resource missing/escaped: {name}")
        stat = path.stat()
        expected = (pin["size"], pin["mtime_ns"], pin["ctime_ns"])
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != expected:
            raise ValueError(f"Runtime resource sealed fingerprint changed: {name}")


def configure_runtime(snapshot, expected_hash=None):
    """Activate verified copied sources before importing any project packages."""
    global ROOT, RUNTIME_SNAPSHOT, RUNTIME_FILES, RUNTIME_EPOCH_SHA256, RUNTIME_RESOURCE_FILES
    snapshot = Path(snapshot).resolve()
    manifest = json.loads((snapshot / "MANIFEST.json").read_text())
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Runtime manifest needs a nonempty files mapping")
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError("Runtime epoch hash differs from requested hash")
    for key in ("epoch_sha256", "runtime_epoch_sha256", "content_sha256"):
        if manifest.get(key) is not None and manifest[key] != digest:
            raise ValueError(f"Runtime manifest {key} does not match files digest")
    for name, pin in files.items():
        path = snapshot / name
        if not path.resolve().is_relative_to(snapshot) or not path.is_file() or sha256(path) != pin:
            raise ValueError(f"Runtime snapshot file changed/escaped: {name}")
    for required in (snapshot / "src", snapshot / "params", snapshot / "pyproject.toml"):
        if not required.exists():
            raise ValueError(f"Runtime snapshot missing required resource: {required.name}")
    ROOT, RUNTIME_SNAPSHOT, RUNTIME_FILES, RUNTIME_EPOCH_SHA256 = snapshot, snapshot, files, digest
    resource_manifest = snapshot / "RESOURCE-MANIFEST.json"
    RUNTIME_RESOURCE_FILES = {}
    if resource_manifest.exists():
        if "RESOURCE-MANIFEST.json" not in files:
            raise ValueError("Resource manifest must be pinned by the core manifest")
        resources = json.loads(resource_manifest.read_text()).get("files", {})
        if not isinstance(resources, dict):
            raise ValueError("Runtime resources need a files mapping")
        for name, pin in resources.items():
            if not isinstance(pin, dict) or not isinstance(pin.get("sha256"), str):
                raise ValueError(f"Runtime resource lacks a content hash: {name}")
        RUNTIME_RESOURCE_FILES = resources
        verify_runtime_resources()
    source = str(snapshot / "src")
    sys.path[:] = [source] + [p for p in sys.path if p != source and p != str(LIVE_ROOT / "src")]
    os.environ["PYTHONPATH"] = source
    os.environ["PYTHONNOUSERSITE"] = "1"
    assert_snapshot_imports()
    install_epoch_resource_paths()
    install_epoch_ballast_worker()
    assert_snapshot_imports()
    return dict(runtime_snapshot=str(snapshot), runtime_epoch_sha256=digest)


def assert_snapshot_imports():
    if RUNTIME_SNAPSHOT is None:
        return {}
    origins = {}
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        filename = getattr(module, "__file__", None)
        project_name = name == "stowage" or name.startswith("stowage.") or name == "llm" or name.startswith("llm.")
        paths = [Path(filename).resolve()] if filename else []
        paths.extend(Path(p).resolve() for p in getattr(module, "__path__", []))
        live_file = any(path.is_relative_to(LIVE_ROOT / "src") for path in paths)
        epoch_file = any(path.is_relative_to(RUNTIME_SNAPSHOT / "src") for path in paths)
        if project_name or live_file or epoch_file:
            if not paths or any(not path.is_relative_to(RUNTIME_SNAPSHOT / "src") for path in paths):
                raise RuntimeError(f"Live project module leaked into runtime epoch: {name}: {paths}")
            origins[name] = str(paths[0])
    return origins


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temp = Path(filename)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(json.dumps(value, sort_keys=True, indent=2, default=str, allow_nan=False) + "\n")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def publish_manifest(output, corpus_root, config):
    with (output / ".manifest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = output / "manifest.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        configurations = previous.get("configurations", [])
        if previous.get("config") and previous["config"] not in configurations:
            configurations.append(previous["config"])
        if config not in configurations:
            configurations.append(config)
        payload = dict(configurations=configurations, corpus_root=str(corpus_root.resolve()),
            versions_recorded_per_instance=True, rule_freeze_v1=False, global_screen=0,
            structured_plans=True, native_threads=1, gpu=False)
        if any(c.get("legacy_small_root") for c in configurations):
            payload["legacy_quality_manifest"] = "legacy-decisions.json"
        atomic_json(path, payload)


def publish_legacy_decision(output, case, record):
    output = Path(output)
    with (output / ".legacy-quality.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = output / "legacy-decisions.json"
        payload = json.loads(path.read_text()) if path.exists() else dict(format="stowlm-legacy-decisions-v1", cases={})
        payload["cases"][case] = record
        payload["updated_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(path, payload)


def publish_runtime(output, value):
    # Preserve each supervisor's state as well as the compatibility latest view.
    atomic_json(output / "runtime" / f"{value['pid']}.json", value)
    with (output / ".runtime.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        atomic_json(output / "runtime.json", value)


def render_reports(output, corpus_root):
    """Reporting is retried; a presentation failure cannot stop gold production."""
    renderer = Path(__file__).with_name("render_status.py")
    if not renderer.exists():
        return
    error_path = output / "report-errors" / f"{os.getpid()}.json"
    for attempt in (1, 2):
        try:
            subprocess.run([sys.executable, str(renderer), "--output", str(output),
                "--corpus-root", str(corpus_root), "--solve47-root",
                str(LIVE_ROOT / "experiments/mvp/solve47")], check=True)
            error_path.unlink(missing_ok=True)
            return
        except (OSError, subprocess.SubprocessError) as exc:
            atomic_json(error_path, dict(attempt=attempt, error=f"{type(exc).__name__}: {exc}",
                retry="next completion or idle poll", updated_utc=datetime.now(timezone.utc).isoformat()))


def child_cpu_s():
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def source_pin():
    if RUNTIME_FILES is not None:
        verify_runtime_resources()
        return {**{name: sha256(ROOT / name) for name in sorted(RUNTIME_FILES)},
                **{name: pin["sha256"] for name, pin in sorted(RUNTIME_RESOURCE_FILES.items())}}
    files = {}
    for directory, pattern in (("src/stowage", "*.py"), ("src/llm/stowlm", "*.py"),
                               ("params", "*.toml"), ("src/stowage/rules", "*.toml"),
                               ("src/stowage/rules/imdg/data", "*.json")):
        for path in sorted((ROOT / directory).rglob(pattern)):
            files[str(path.relative_to(ROOT))] = sha256(path)
    return files


def relevant_source(path, *, used_scalable=True, used_twostep=False):
    """Keep shared/child solver dependencies, exclude unrelated baseline edits.

    The exact watchdog imports modules in its child, so loaded parent modules
    alone are insufficient. All non-baseline solver modules remain in the gate.
    Broad before/after hashes are retained even for excluded file changes.
    """
    if path == "src/stowage/ownership.py":
        # Administrative task/path constants have no pipeline imports (also
        # audited by D88). Keep gating if explicitly loaded in this process.
        return "stowage.ownership" in sys.modules
    if path.startswith("src/stowage/solver/twostep"):
        return used_twostep
    if not used_scalable and path.startswith(("src/stowage/solver/scalable",
                                              "src/stowage/solver/construct")):
        # Exact-only plans never invoke the scalable constructor or LNS. Keep
        # shared physics, ballast, securing and child exact-search code gated.
        return False
    if path.startswith(("src/stowage/generate/", "src/llm/stowlm/")):
        loaded = {str(Path(module.__file__).resolve().relative_to(ROOT))
                  for module in list(sys.modules.values())
                  if getattr(module, "__file__", None)
                  and Path(module.__file__).resolve().is_relative_to(ROOT)}
        return path in loaded
    return True


def discover(corpus_root, rungs, splits):
    """An accepted audit is the corpus writer's atomic shard commit marker."""
    paths = []
    for split in splits:
        for rung in rungs:
            for audit_path in sorted((Path(corpus_root) / split / rung).rglob("*.audit.json")):
                audit = json.loads(audit_path.read_text())
                path = audit_path.with_name(audit_path.name.removesuffix(".audit.json") + ".json")
                if audit.get("accepted") is True and audit.get("rule_freeze_v1_enabled") is False and path.exists():
                    paths.append(path)
    # Interleave split/rung/ship/family buckets so an early timing sample does
    # not consist entirely of whichever ship sorts first on the filesystem.
    buckets = {}
    for path in paths:
        key = path.relative_to(Path(corpus_root)).parts[:-1]
        buckets.setdefault(key, []).append(path)
    return [path for group in zip_longest(*buckets.values()) for path in group if path is not None]


def input_settings_for(config, settings):
    """Flat requirements remain valid; a ship map excludes unlisted ships."""
    if any(key.startswith("feeder_") for key in settings):
        return settings.get(config.get("vessel"))
    return settings


def reload_case_config(config, scalable_path=None, input_settings_path=None, policy=None):
    """Return a new shard configuration; never mutate an in-flight snapshot."""
    updated = dict(config)
    for path, key in ((scalable_path, "scalable_config"), (input_settings_path, "input_settings")):
        if path is not None:
            value = json.loads(Path(path).read_text())
            if not isinstance(value, dict):
                raise ValueError(f"{key} file must contain a JSON object")
            updated[key] = value
    if policy is not None:
        if "small_ballast_s" in policy:
            updated["small_ballast_s"] = policy["small_ballast_s"]
        else:
            updated.pop("small_ballast_s", None)
    return updated


def small_ballast_budgets(config):
    """D100 opt-in raises both warm baseline and external small finish limits."""
    override = config.get("small_ballast_s")
    if override is None:
        return 90.0, 120.0
    if type(override) not in (int, float) or not math.isfinite(override) or override <= 0:
        raise ValueError("small_ballast_s must be finite and positive")
    return float(override), float(override)


def policy_metadata(rung, config=None):
    # This labels the existing constructor cohort only. A future quality
    # adapter needs its documented API and an explicit leader go before use.
    # Metadata stays outside solver configuration and serialized plan bytes.
    if rung != "small":
        return {}
    config = config or {}
    return {"policy": ("gold-warmstart-v2" if config.get("gold_constructor_fallback_v1") else "gold-warmstart-v1")
            if config.get("gold_warmstart_v1") else "scalable-constructor-v1"}


@contextmanager
def scoped_internal_ballast_v4(calls):
    """The API already dispatches V4 directly; scope its flag for diagnostics."""
    from stowage.solver import ballast_v4_dispatch
    original = ballast_v4_dispatch.finish
    def finish(*args, **kwargs):
        started = time.perf_counter()
        call = dict(backend="stowage.solver.ballast_v4_dispatch.finish", backend_already_direct_v4=True,
                    time_limit_s=kwargs.get("time_limit_s", 30.0),
                    environment_before=os.environ.get("STOWAGE_BALLAST_V4"))
        try:
            with scoped_ballast_v4(True):
                call["environment_during"] = os.environ.get("STOWAGE_BALLAST_V4")
                outcome = original(*args, **kwargs)
                call.update(status=outcome.status, not_found_cause=getattr(outcome, "not_found_cause", None))
                return outcome
        finally:
            call.update(wall_s=time.perf_counter()-started,
                        environment_after=os.environ.get("STOWAGE_BALLAST_V4"))
            calls.append(call)
    ballast_v4_dispatch.finish = finish
    try:
        yield
    finally:
        ballast_v4_dispatch.finish = original


def solve_gold_candidate(instance, seed, lns_s=15.0, ballast_s=90.0):
    from stowage.solver import twostep
    from stowage.solver.gold_warmstart import solve_gold
    if twostep.VERSION != "4.3.0":
        raise RuntimeError(f"D97 gold requires two-step 4.3.0; runtime has {twostep.VERSION}")
    calls = []
    with scoped_internal_ballast_v4(calls):
        options = dict(seed=seed, lns_time_limit_s=lns_s)
        if ballast_s != 90.0:
            options["ballast_time_limit_s"] = ballast_s
        result = solve_gold(instance, **options)
    metadata = dict(two_step_weighted_moves=result.metrics.get("objective_before"),
                    gold_weighted_moves=result.metrics.get("objective_after"),
                    lns_elapsed_s=result.metrics.get("lns_elapsed_s"),
                    internal_ballast_calls=calls, internal_ballast_budget_s=ballast_s,
                    baseline_producer_version=twostep.VERSION,
                    baseline_producer_sha256={f"src/stowage/solver/{name}.py": sha256(ROOT / f"src/stowage/solver/{name}.py")
                        for name in ("twostep", "twostep_adapter", "twostep_terminal", "twostep_construct")})
    return result, metadata


def assess_legacy_candidate(instance, legacy_directory, input_sha256, warm_result):
    """Compare untouched archived plans using current measured move counts."""
    directory = Path(legacy_directory)
    record = dict(selected=False, retain_legacy=False, previously_passed=False, decision="drop",
                  drop_reason="legacy_quality_unverified", artifact=str(directory))
    result_path = directory / "result.json"
    if not result_path.exists():
        return None, dict(record, decision="no_legacy_candidate", drop_reason=None)
    try:
        prior = json.loads(result_path.read_text())
        record["previously_passed"] = bool(prior.get("passed"))
        if not prior.get("passed"):
            return None, dict(record, decision="not_previously_passing", drop_reason=None)
        if prior.get("input_sha256") != input_sha256:
            return None, dict(record, drop_reason="legacy_quality_input_mismatch")
        plan_path = directory / "plan.json"
        if not plan_path.exists() or sha256(plan_path) != prior.get("plan_sha256"):
            return None, dict(record, drop_reason="legacy_quality_plan_pin_mismatch")
        baseline = warm_result.metrics.get("objective_before")
        if (warm_result.status != "ok" or warm_result.plan is None or
                type(baseline) not in (int, float) or not math.isfinite(baseline)):
            return None, record
        from stowage.io import load_plan
        from stowage.frozen import require_clean
        from stowage.moves import apply_plan
        from stowage.checker import check
        legacy = load_plan(plan_path)
        require_clean(legacy, what="legacy gold candidate")
        old_replay = apply_plan(instance, legacy)
        fresh_replay = apply_plan(instance, warm_result.plan)
        if not old_replay.legal or not fresh_replay.legal:
            return None, dict(record, drop_reason="legacy_quality_move_replay")
        old_value = old_replay.count.weighted_nonproductive
        fresh_value = fresh_replay.count.weighted_nonproductive
        record.update(legacy_objective=old_value, baseline_objective=baseline,
                      fresh_gold_objective=fresh_value, legacy_input_sha256=prior["input_sha256"],
                      legacy_plan_sha256=prior["plan_sha256"], baseline_version="4.3.0",
                      baseline_gate_source="fresh solve_gold full checker and public C2-off action replay")
        if old_value > baseline:
            return None, dict(record, drop_reason="legacy_worse_than_two_step",
                              skipped_gates="legacy objective exceeds measured two-step baseline")
        checker = check(instance, legacy)
        record["legacy_checker"] = json.loads(checker.model_dump_json())
        mask = {"passed": False}
        if checker.passed:
            try:
                replay(instance, legacy, mask, securing=True)
            except Exception as exc:
                mask.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        record["legacy_mask"] = mask
        helper_path = Path(__file__).with_name("legacy_quality.py")
        record["comparison_helper_sha256"] = sha256(helper_path)
        spec = importlib.util.spec_from_file_location("gold_legacy_quality", helper_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        decision = module.comparison_record(old_value, baseline,
            legacy_checker_passed=checker.passed, legacy_mask_passed=mask.get("passed", False),
            baseline_checker_passed=warm_result.metrics.get("baseline_checker_passed", False),
            baseline_mask_passed=warm_result.metrics.get("baseline_mask", {}).get("passed", False),
            input_sha256=input_sha256, legacy_input_sha256=prior["input_sha256"],
            legacy_plan_sha256=prior["plan_sha256"], baseline_version="4.3.0")
        record.update(decision)
        if not decision["retain_legacy"]:
            return None, record
        if old_value <= fresh_value:
            return legacy, dict(record, selected=True, decision="selected_legacy")
        return None, dict(record, decision="superseded_by_better_gold")
    except Exception as exc:
        return None, dict(record, error=f"{type(exc).__name__}: {exc}")


def filter_input_settings(paths, settings):
    """Wait for locked producer settings without creating rejected gold rows."""
    if not settings:
        return list(paths)
    selected = []
    for path in paths:
        config = json.loads(Path(path).with_suffix(".audit.json").read_text()).get("config", {})
        required = input_settings_for(config, settings)
        if required is not None and all(key in config and config[key] == value for key, value in required.items()):
            selected.append(path)
    return selected


def load_policy(path=None, max_load=16):
    """Reload scheduler controls without changing immutable solver identity."""
    policy = dict(max_load=max_load, train_first=True, val_gold_caps={"tiny": 50, "small": 56},
                  admission_mode="reservations")
    if path is not None:
        supplied = json.loads(Path(path).read_text())
        if not isinstance(supplied, dict):
            raise ValueError("Scheduler policy must be a JSON object")
        for key in ("max_load", "train_first", "max_workers", "admission_mode", "small_ballast_s"):
            if key in supplied:
                policy[key] = supplied[key]
        caps = supplied.get("val_gold_caps", {})
        if not isinstance(caps, dict):
            raise ValueError("val_gold_caps must be a rung-to-integer object")
        policy["val_gold_caps"].update(caps)
    if (type(policy["max_load"]) not in (int, float) or
            not math.isfinite(policy["max_load"]) or policy["max_load"] <= 0):
        raise ValueError("Policy max_load must be finite and positive")
    if type(policy["train_first"]) is not bool:
        raise ValueError("Policy train_first must be boolean")
    if policy["admission_mode"] not in {"reservations", "live_load_first", "measured_cpu"}:
        raise ValueError("Policy admission_mode must be reservations, live_load_first or measured_cpu")
    if "max_workers" in policy and (type(policy["max_workers"]) is not int or not 0 <= policy["max_workers"] <= 12):
        raise ValueError("Policy max_workers must be an integer from 0 to 12")
    if any(rung not in {"tiny", "small", "medium"} or type(cap) is not int or cap < 0
           for rung, cap in policy["val_gold_caps"].items()):
        raise ValueError("Policy caps must be nonnegative integers for tiny/small/medium")
    if "small_ballast_s" in policy:
        small_ballast_budgets(policy)
    return policy


def train_inventory_waiting(corpus_root, output, rungs, input_settings=None):
    """Hold validation until every selected requested training row was attempted."""
    request = Path(corpus_root) / "train.requested.json"
    if not request.exists():
        return True
    specs = json.loads(request.read_text()).get("specs", [])
    settings = input_settings or {}
    for spec in specs:
        if spec.get("rung") not in rungs:
            continue
        required = input_settings_for(spec, settings)
        if required is None:
            continue
        # A producer inventory awaiting its locked settings is unfinished too.
        if any(key not in spec or spec[key] != value for key, value in required.items()):
            return True
        path = (Path(corpus_root) / "train" / spec["rung"] / spec["vessel"] /
                spec["generator"] / f"seed-{spec['seed']:09d}.json")
        result = result_dir(path, corpus_root, output) / "result.json"
        if not result.exists() or not json.loads(result.read_text()).get("completed"):
            return True
    return False


def select_pending(paths, output, corpus_root, workers, policy, *, train_waiting=False):
    if workers <= 0:
        return []
    paths = list(paths)
    training = [path for path in paths if Path(path).relative_to(Path(corpus_root)).parts[0] == "train"]
    if policy["train_first"]:
        if training:
            return training[:workers]
        if train_waiting:
            return []
    remaining = dict(policy["val_gold_caps"])
    for rung in remaining:
        count = sum(bool(row.get("completed") and row.get("passed"))
                    for path in (Path(output) / "val" / rung).rglob("result.json")
                    for row in [json.loads(path.read_text())])
        remaining[rung] = max(0, remaining[rung]-count)
    selected = []
    for path in paths:
        relative = Path(path).relative_to(Path(corpus_root))
        split, rung = relative.parts[:2]
        if split == "val" and rung in remaining:
            if remaining[rung] <= 0:
                continue
            remaining[rung] -= 1
        selected.append(path)
        if len(selected) >= workers:
            break
    return selected


def prioritize_pending(paths, corpus_root, priority_file=None):
    """Stable retry ordering is scheduler metadata, never solver identity."""
    paths = list(paths)
    if priority_file is None:
        return paths
    payload = json.loads(Path(priority_file).read_text())
    targets = payload.get("requested_rerun", [])
    if not isinstance(targets, list):
        raise ValueError("Priority requested_rerun must be a list")
    ranks = {}
    for row in targets:
        if not isinstance(row, dict) or not isinstance(row.get("case"), str):
            raise ValueError("Priority entries need a case string")
        ranks.setdefault(row["case"].removesuffix(".json"), len(ranks))
    return sorted(paths, key=lambda path: ranks.get(
        str(Path(path).relative_to(Path(corpus_root)).with_suffix("")), len(ranks)))


def result_dir(input_path, corpus_root, output):
    relative = Path(os.path.abspath(input_path)).relative_to(Path(corpus_root).resolve())
    if len(relative.parts) != 5 or relative.parts[0] not in ("train", "val"):
        raise ValueError(f"Unexpected corpus identity: {relative}")
    return Path(output) / relative.with_suffix("")


def verify_candidate(input_path, corpus_root, output=None):
    spec = importlib.util.spec_from_file_location("gold_split_admission", Path(__file__).with_name("split_admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_candidate(input_path, corpus_root, output)


def _mach_cpu_ticks():
    """Read Darwin HOST_CPU_LOAD_INFO: user, system, idle and nice ticks."""
    import ctypes
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    library.mach_host_self.restype = ctypes.c_uint
    library.host_statistics.argtypes = (ctypes.c_uint, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_uint))
    library.host_statistics.restype = ctypes.c_int
    ticks, count = (ctypes.c_uint * 4)(), ctypes.c_uint(4)
    status = library.host_statistics(library.mach_host_self(), 3,
        ctypes.cast(ticks, ctypes.POINTER(ctypes.c_int)), ctypes.byref(count))
    if status != 0 or count.value != 4:
        raise ValueError(f"host_statistics failed: status={status}, count={count.value}")
    return tuple(ticks)


def _mach_busy_cores(before, after, cpu_count):
    if (len(before) != 4 or len(after) != 4 or type(cpu_count) is not int
            or not 1 <= cpu_count <= 18
            or any(type(value) is not int or not 0 <= value <= 0xffffffff
                   for value in (*before, *after))):
        raise ValueError("invalid Mach CPU ticks or logical CPU count")
    # Darwin exposes unsigned 32-bit counters. Modular subtraction preserves
    # a short sample across one counter wrap, including the idle counter.
    deltas = [(end-start) & 0xffffffff for start, end in zip(before, after)]
    total = sum(deltas)
    if total <= 0:
        raise ValueError("Mach CPU tick sample did not advance")
    cores = sum(deltas[index] for index in (0, 1, 3))/total*cpu_count
    if not math.isfinite(cores) or not 0 <= cores <= cpu_count:
        raise ValueError("invalid measured Mach busy CPU cores")
    return cores


def measure_mach_cpu_usage(sample_s=.5):
    """Measure busy host CPU when sandbox rules deny ps and top."""
    if sys.platform != "darwin":
        raise OSError("Mach CPU measurement requires Darwin")
    if not math.isfinite(sample_s) or sample_s <= 0:
        raise ValueError("Mach CPU sample duration must be positive")
    before = _mach_cpu_ticks()
    time.sleep(sample_s)
    return _mach_busy_cores(before, _mach_cpu_ticks(), os.cpu_count())


def measure_cpu_usage():
    errors = []
    for command, source in ((["ps", "-A", "-o", "%cpu="], "ps"),
                             (["top", "-l1", "-n0"], "top")):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=4)
            if result.returncode:
                raise ValueError(f"exit {result.returncode}")
            if source == "ps":
                values = [float(value) for value in result.stdout.split()]
                if not values or any(not math.isfinite(value) or value < 0 for value in values):
                    raise ValueError("missing/invalid process CPU measurements")
                cores = sum(values)/100
            else:
                match = re.search(r"CPU usage:.*?([0-9.]+)%\s*idle", result.stdout)
                if not match or not 0 <= float(match.group(1)) <= 100:
                    raise ValueError("missing/invalid CPU idle percentage")
                cores = (100-float(match.group(1)))*18/100
            return dict(cores=cores, source=source, errors=errors)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append(f"{source}: {type(exc).__name__}: {exc}")
    try:
        return dict(cores=measure_mach_cpu_usage(), source="mach-host-cpu-ticks", errors=errors)
    except (OSError, ValueError, TypeError, AttributeError, OverflowError) as exc:
        errors.append(f"mach-host-cpu-ticks: {type(exc).__name__}: {exc}")
    return dict(cores=None, source="unavailable", errors=errors)


def shared_active_workers(output):
    pids = set()
    for path in (Path(output) / "runtime").glob("*.json"):
        row = json.loads(path.read_text())
        pids.update(row.get("active_worker_pids", []))
    return len(pids)


def admission(ledger, workers, ledger_owner=None, max_load=16,
              admission_mode="reservations", recent_start_workers=0, active_gold_workers=0):
    """Conservative reservation accounting; unknown ledger fields queue work."""
    cores, ram, unknown = 0.0, 0.0, []
    for line in Path(ledger).read_text().splitlines():
        fields = [p.strip() for p in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(fields) < 7 or fields[0] == "executive/job" or set(fields[0]) <= {"-"}:
            continue
        if fields[0] == ledger_owner:
            continue
        numbers = [re.search(r"[0-9]+(?:\.[0-9]+)?", value) for value in fields[2:4]]
        if not all(numbers):
            unknown.append(fields[0])
            continue
        cores += float(numbers[0].group())
        ram += float(numbers[1].group())
    load = os.getloadavg()[0]
    memory = subprocess.run(["memory_pressure"], capture_output=True, text=True, check=False)
    match = re.search(r"System-wide memory free percentage:\s*([0-9.]+)%", memory.stdout)
    free_percent = float(match.group(1)) if match else None
    detail = dict(other_reserved_cores=cores, other_reserved_ram_gb=ram, load_1m=load,
                  memory_free_percent=free_percent, unknown_reservations=unknown,
                  requested_cores=workers, requested_ram_gb=workers*2, max_load=max_load,
                  admission_mode=admission_mode, recent_start_workers=recent_start_workers)
    if admission_mode == "measured_cpu":
        measured = measure_cpu_usage()
        memory_ok = free_percent is not None and free_percent >= 15
        if measured["cores"] is None:
            available = max(0, math.floor(8-active_gold_workers-recent_start_workers))
            basis = "leader-authorized fallback: at most 8 total gold workers; recent starts counted conservatively"
        else:
            available = max(0, math.floor(15-measured["cores"]-recent_start_workers))
            basis = "measured CPU plus new workers <=15 cores; load and stale reservations informational"
        actual = min(workers, available) if memory_ok else 0
        detail.update(admitted=actual > 0, actual_workers=actual, cpu_measurement=measured,
                      active_gold_workers=active_gold_workers, reservation_policy=basis)
        return detail
    if admission_mode == "live_load_first":
        limit = min(max_load, 16)
        effective_load = load + recent_start_workers
        fits = free_percent is not None and free_percent >= 15 and effective_load <= limit
        if recent_start_workers and limit-effective_load < 1:
            # The one-worker probe is allowed only without unreflected starts;
            # a second supervisor cannot add another probe to a full ramp.
            fits = False
        available = max(1, math.floor(limit-effective_load)) if fits else 0
        actual = min(workers, available)
        detail.update(admitted=actual > 0, actual_workers=actual, live_load_limit=limit,
                      reservation_policy="Leader 15:30 explicit live-load-first; stale CPU/RAM reservations are informational",
                      effective_load_with_recent_starts=effective_load)
        return detail
    if admission_mode != "reservations":
        raise ValueError(f"Unknown admission mode: {admission_mode}")
    okay = not unknown and cores+workers <= 16 and ram+workers*2 <= 112 and load <= max_load
    if free_percent is not None:
        okay = okay and free_percent >= 15
    detail["admitted"] = okay
    return detail


def select_admission(ledger, workers, adaptive=False, ledger_owner=None, max_load=16,
                     admission_mode="reservations", recent_start_workers=0, active_gold_workers=0):
    options = dict(max_load=max_load)
    if admission_mode != "reservations" or recent_start_workers:
        options.update(admission_mode=admission_mode, recent_start_workers=recent_start_workers)
    if admission_mode == "measured_cpu":
        options["active_gold_workers"] = active_gold_workers
    detail = admission(ledger, workers, ledger_owner, **options)
    if admission_mode in {"live_load_first", "measured_cpu"}:
        return detail
    actual = workers if detail["admitted"] else 0
    if adaptive:
        actual = 0
        live_fits = (not detail["unknown_reservations"] and detail["load_1m"] <= max_load
                     and (detail["memory_free_percent"] is None or detail["memory_free_percent"] >= 15))
        if live_fits:
            actual = next((n for n in range(workers, 0, -1)
                           if detail["other_reserved_cores"]+n <= 16
                           and detail["other_reserved_ram_gb"]+n*2 <= 112), 0)
    return dict(detail, actual_workers=actual, admitted=actual > 0)


@contextmanager
def shared_admission_lock(output):
    """Serialize cooperating supervisors' check-and-reserve transactions."""
    path = Path(output) / ".admission.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _live_starts(output, now, lease_s=None):
    path = Path(output) / ".live-admission.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    fresh = {}
    for pid, row in state.items():
        groups = [g for g in row.get("groups", [row])
                  if 0 <= now-g["started_wall_s"] < min(g.get("lease_s", 60), lease_s or 60) and g["workers"] > 0]
        if groups:
            fresh[pid] = dict(groups=groups, workers=sum(g["workers"] for g in groups),
                              started_wall_s=max(g["started_wall_s"] for g in groups))
    return fresh


def recent_live_workers(output, now=None, *, lease_s=None):
    """Caller holds shared admission lock; recent starts bound load-average lag."""
    return sum(row["workers"] for row in _live_starts(output, time.time() if now is None else now, lease_s).values())


def reserve_live_start(output, workers, now=None, *, append=False, ticket=None, lease_s=60):
    """Lease new additions for 60 seconds, or release on completed shard.

    APPROXIMATION: load averages lag new work. Counting recent starts again is
    deliberately conservative; expired additions should be reflected in live load.
    Only the current supervisor's lease changes, under the shared admission lock.
    """
    now = time.time() if now is None else now
    state = _live_starts(output, now)
    key = str(os.getpid())
    if workers > 0:
        groups = state.get(key, {}).get("groups", []) if append else []
        groups.append(dict(workers=workers, started_wall_s=now, ticket=ticket, lease_s=lease_s))
        state[key] = dict(groups=groups, workers=sum(g["workers"] for g in groups), started_wall_s=now)
    else:
        state.pop(key, None)
    atomic_json(Path(output) / ".live-admission.json", state)


def release_live_ticket(output, ticket):
    state = _live_starts(output, time.time())
    row = state.get(str(os.getpid()))
    if row:
        for group in row["groups"]:
            if group.get("ticket") == ticket:
                group["workers"] = max(0, group["workers"]-1)
                break
        row["workers"] = sum(g["workers"] for g in row["groups"])
    atomic_json(Path(output) / ".live-admission.json", state)


def update_ledger_reservation(ledger, owner, workers):
    """Only this explicitly owned row changes; retain every other byte."""
    with Path(ledger).open("r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        lines = handle.readlines()
        found = False
        for index, line in enumerate(lines):
            if not line.startswith("|"):
                continue
            fields = line.rstrip("\n").split("|")
            if len(fields) < 9 or fields[1].strip() != owner:
                continue
            if found:
                raise ValueError(f"Duplicate owned ledger row: {owner}")
            found = True
            fields[3], fields[4] = f" {workers} ", f" {workers*2} "
            fields[6] = f" native1; {'active shard' if workers else 'queued/idle'}; owner PID {os.getpid()} "
            lines[index] = "|".join(fields) + "\n"
        if not found:
            raise ValueError(f"Leader must create owned ledger row first: {owner}")
        handle.seek(0)
        handle.write("".join(lines))
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def deadline(seconds):
    old = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError(f"Stage exceeded {seconds}s")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def replay(instance, plan, details, *, securing=False):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MASK_VERSION, MaskConfig, MaskedDecoder
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    actions = list(space.from_plan(instance, plan))
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra={"global_screen": 0}))
    codec = ActionCodec(load_tokenizer(), [b.id for b in instance.containers])
    if securing:
        from llm.securing import securing_decoder_factory
        factory = securing_decoder_factory()
        decoder = factory(instance, engine, codec, MaskConfig(), 1)[0]
        details["decoder_backend"] = factory.name
    else:
        decoder = MaskedDecoder(engine, codec, MaskConfig())
    details.update(mask_version=MASK_VERSION, actions_expected=len(actions), actions_completed=0,
                   engine_diagnostics=engine.diagnostics, masked_action=None)
    for i, action in enumerate(actions):
        for token in decoder.codec.encode_action(action):
            try:
                decoder.advance(token)
            except Exception:
                details["masked_action"] = dict(index=i, action=space.encode(action), token=token)
                raise
        details["actions_completed"] = i + 1
    for token in decoder.codec.end_plan_tokens:
        decoder.advance(token)
    details.update(status=decoder.status, actions_equal=decoder.actions == actions,
                   tokens=len(decoder.tokens), passed=decoder.status == "complete" and decoder.actions == actions)
    if not details["passed"]:
        raise RuntimeError("Production mask did not completely reproduce the plan")


@contextmanager
def scoped_ballast_v4(enabled):
    previous = os.environ.get("STOWAGE_BALLAST_V4")
    if enabled:
        os.environ["STOWAGE_BALLAST_V4"] = "1"
    else:
        os.environ.pop("STOWAGE_BALLAST_V4", None)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("STOWAGE_BALLAST_V4", None)
        else:
            os.environ["STOWAGE_BALLAST_V4"] = previous


def process_case(input_path, corpus_root, output, config, *, resume_finished=False):
    if config.get("runtime_snapshot"):
        requested = Path(config["runtime_snapshot"]).resolve()
        if RUNTIME_SNAPSHOT != requested or RUNTIME_EPOCH_SHA256 != config.get("runtime_epoch_sha256"):
            configure_runtime(requested, config.get("runtime_epoch_sha256"))
    input_path, output = Path(input_path), Path(output)
    directory = result_dir(input_path, corpus_root, output)
    directory.mkdir(parents=True, exist_ok=True)
    # OS releases the lock after a crash; no stale lock files block resumption.
    with (directory / "case.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Physics identities require the legacy absent-variable environment
        # outside finalization, including when a caller inherited the opt-in.
        with scoped_ballast_v4(False):
            return _process_locked(input_path, corpus_root, directory, config, resume_finished=resume_finished)


def _process_locked(input_path, corpus_root, directory, config, *, resume_finished=False):
    identity = dict(input_sha256=sha256(input_path), config=config)
    identity["configuration_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    target = directory / "result.json"
    prior = json.loads(target.read_text()) if target.exists() else None
    gate_started = time.perf_counter()
    try:
        split_evidence = verify_candidate(input_path, corpus_root, directory.parents[4])
    except (ValueError, KeyError, OSError, TypeError) as exc:
        relative = input_path.absolute().relative_to(Path(corpus_root).resolve())
        row = dict(prior or {}, **identity)
        row.update(case=str(relative.with_suffix("")), split=relative.parts[0], rung=relative.parts[1],
                   input_path=str(input_path.absolute()), completed=True, passed=False, drop_reason="input",
                   error=f"{type(exc).__name__}: {exc}", split_admission_passed=False,
                   wall_s=time.perf_counter()-gate_started,
                   stages={"input": dict(passed=False, wall_s=time.perf_counter()-gate_started,
                                         error=f"{type(exc).__name__}: {exc}")})
        row.update(policy_metadata(relative.parts[1], config))
        if RUNTIME_SNAPSHOT is not None:
            row.update(runtime_snapshot=str(RUNTIME_SNAPSHOT), runtime_epoch_sha256=RUNTIME_EPOCH_SHA256,
                       runtime_import_origins=assert_snapshot_imports(),
                       runtime_resource_path_modules=list(RUNTIME_RESOURCE_MODULES))
        atomic_json(target, row)
        return row
    if target.exists():
        if prior.get("input_sha256") != identity["input_sha256"]:
            raise ValueError(f"Immutable gold input differs: {target}")
        if (any(prior.get(k) != v for k, v in identity.items())
                and not (resume_finished and prior.get("completed"))):
            raise ValueError(f"Immutable gold settings/input differ: {target}; use a fresh output root")
        if prior.get("completed"):
            if prior.get("passed") and (not (directory / "plan.json").exists() or sha256(directory / "plan.json") != prior.get("plan_sha256")):
                raise ValueError(f"Published gold plan missing or changed: {directory}")
            return prior
    split, rung, ship, family, name = input_path.resolve().relative_to(Path(corpus_root).resolve()).parts
    before = source_pin()
    row = dict(identity, case="/".join((split, rung, ship, family, Path(name).stem)),
               split=split, rung=rung, ship=ship, family=family, input_path=str(input_path.resolve()),
               passed=False, completed=False, drop_reason=None, stages={}, rule_freeze_v1=False,
               c2=False, global_screen=0, started_utc=datetime.now(timezone.utc).isoformat(),
               driver_sha256=sha256(__file__), pid=os.getpid())
    row.update(split_admission=split_evidence, split_admission_passed=True)
    if RUNTIME_SNAPSHOT is not None:
        row.update(runtime_snapshot=str(RUNTIME_SNAPSHOT), runtime_epoch_sha256=RUNTIME_EPOCH_SHA256,
                   runtime_resource_path_modules=list(RUNTIME_RESOURCE_MODULES))
    row.update(policy_metadata(rung, config))
    finalizer_v4 = bool(config.get("ballast_v4", False) or (rung == "small" and config.get("gold_warmstart_v1", False)))
    row.update(ballast_v4=finalizer_v4,
               ballast_environment={"scope": "finalize_only", "other_stages": None,
                                    "STOWAGE_BALLAST_V4": "1" if finalizer_v4 else None})
    atomic_json(directory / "source-before.json", before)
    atomic_json(target, row)
    start, cpu_start, child_start = time.perf_counter(), time.process_time(), child_cpu_s()
    current_stage = "input"

    @contextmanager
    def stage(name, limit):
        nonlocal current_stage
        current_stage = name
        details = row["stages"][name] = dict(passed=False, limit_s=limit)
        atomic_json(target, row)
        tick, cpu, child = time.perf_counter(), time.process_time(), child_cpu_s()
        try:
            with deadline(limit):
                yield details
        finally:
            own_cpu, children_cpu = time.process_time()-cpu, child_cpu_s()-child
            details.update(wall_s=time.perf_counter()-tick, process_cpu_s=own_cpu,
                           child_cpu_s=children_cpu, total_cpu_s=own_cpu+children_cpu)
            atomic_json(target, row)

    interrupted = False
    try:
        from stowage.checker import check
        from stowage.generate.background_acceptance import requires_background_gate
        from stowage.feasibility import FeasibilityConfig
        from stowage.frozen import require_clean
        from stowage.io import load_instance, save_plan
        from stowage.planner import finalize
        from stowage.rules.catalogue import catalogue_snapshot, rule_freeze_v1_enabled
        from stowage.solver.exact import ExactPlanner
        from stowage.solver.scalable import ScalableConfig, ScalableSolver
        from stowage.versions import component_versions
        row["runtime_import_origins"] = assert_snapshot_imports()
        if rule_freeze_v1_enabled():
            raise RuntimeError("Gold requires rule-freeze OFF")
        row.update(versions=component_versions(), catalogue_sha256=catalogue_snapshot().content_sha256)
        audit = json.loads(input_path.with_suffix(".audit.json").read_text())
        if audit.get("accepted") is not True or audit.get("rule_freeze_v1_enabled") is not False or audit.get("instance_sha256") != identity["input_sha256"]:
            raise ValueError("Corpus audit does not match accepted OFF input")
        atomic_json(directory / "input-audit.json", audit)
        instance = load_instance(input_path)
        require_clean(instance, what="gold input")
        if instance.size_class != rung or instance.seed != audit["config"]["seed"]:
            raise ValueError("Corpus identity mismatches instance")
        spec = audit["config"]
        if (spec["split"], spec["rung"], spec["vessel"], spec["generator"]) != (split, rung, ship, family):
            raise ValueError("Corpus audit identity mismatches path")
        if len(instance.load_list) != audit["requested_targets"] or instance.voyage is None:
            raise ValueError("Corpus target count or voyage evidence is incomplete")
        if requires_background_gate(instance.vessel, size=instance.size_class):
            if not (audit.get("background_checks", 0) > 0 and
                    audit.get("background_last", {}).get("accepted") is True and
                    audit.get("background_last", {}).get("physics_sha256")):
                raise ValueError("Corpus background acceptance evidence missing")
        row.update(instance_id=instance.id, seed=instance.seed, boxes=len(instance.load_list))
        feasibility = FeasibilityConfig(extra={"global_screen": 0})
        result = None
        warmstart = rung == "small" and config.get("gold_warmstart_v1", False)
        constructor_fallback = False
        internal_ballast_budget, external_ballast_budget = small_ballast_budgets(config) if warmstart else (None, config["ballast_s"])
        if rung == "small":
            row["ballast_budgets_s"] = dict(internal=internal_ballast_budget, external=external_ballast_budget)
        if warmstart:
            legacy_directory = (Path(config["legacy_small_root"]) /
                                input_path.resolve().relative_to(Path(corpus_root).resolve()).with_suffix("")) if config.get("legacy_small_root") else None
            if legacy_directory is not None:
                old_result_path = legacy_directory / "result.json"
                previously_passed = bool(json.loads(old_result_path.read_text()).get("passed")) if old_result_path.exists() else False
                row["legacy_quality"] = dict(selected=False, retain_legacy=False,
                    decision="drop", previously_passed=previously_passed,
                    drop_reason="legacy_quality_unverified", artifact=str(legacy_directory))
            with stage("gold_warmstart", 120+internal_ballast_budget+15+config["checker_s"]+config["mask_s"]+5) as details:
                solve_options = dict(lns_s=15.0)
                if internal_ballast_budget != 90.0:
                    solve_options["ballast_s"] = internal_ballast_budget
                result, quality_metadata = solve_gold_candidate(instance, instance.seed, **solve_options)
                row.update(quality_metadata)
                details.update(status=result.status, metrics=result.metrics,
                               passed=result.status == "ok" and result.plan is not None,
                               planner_version=result.planner_version)
                complete = details["passed"] and result.plan.ballast is not None
                if config.get("gold_constructor_fallback_v1"):
                    baseline = result.metrics.get("objective_before")
                    complete = bool(complete and result.metrics.get("baseline_checker_passed") is True
                        and result.metrics.get("baseline_mask", {}).get("passed") is True
                        and type(baseline) in (int, float) and math.isfinite(baseline) and baseline >= 0)
                details["passed"] = complete
                if not complete:
                    if not config.get("gold_constructor_fallback_v1"):
                        raise RuntimeError(f"Warm-start did not return a complete finalized plan: {result.status}: {result.message}")
                    row["failed_warmstart"] = dict(status=result.status, message=result.message, metrics=result.metrics)
                    row.update(source="constructor-fallback (two-step failed)",
                               policy="constructor-fallback-two-step-failed-v1", baseline_available=False,
                               two_step_weighted_moves=None, gold_weighted_moves=None, quality_delta=None)
                    constructor_fallback = True
                    result = None
                else:
                    row["baseline_available"] = True
            if legacy_directory is not None and not constructor_fallback:
                with stage("legacy_quality", config["checker_s"]+config["mask_s"]+10) as details:
                    legacy_plan, comparison = assess_legacy_candidate(instance, legacy_directory,
                                                                      identity["input_sha256"], result)
                    row["legacy_quality"] = comparison
                    details.update(passed=True, selected=comparison.get("selected", False),
                                   decision=comparison.get("decision"), drop_reason=comparison.get("drop_reason"))
                    if legacy_plan is not None:
                        metrics = dict(result.metrics, source="qualified_legacy", selected_legacy=True,
                                       fresh_objective_after=result.metrics.get("objective_after"),
                                       objective=comparison["legacy_objective"], objective_after=comparison["legacy_objective"])
                        result = result.model_copy(update={"plan": legacy_plan, "metrics": metrics,
                            "provenance": result.provenance.merge(legacy_plan.provenance)})
        if rung == "tiny":
            try:
                with stage("exact", config["exact_s"] + 5) as details:
                    planner = ExactPlanner(num_workers=1, feasibility_config=feasibility)
                    result = planner.plan(instance, seed=instance.seed, time_limit_s=config["exact_s"])
                    details.update(status=result.status, metrics=result.metrics, passed=result.plan is not None,
                                   message=result.message, planner_version=result.planner_version)
                    if result.plan is not None and result.plan.ballast is None:
                        raise RuntimeError("ExactPlanner declared own ballast but omitted it")
                    if result.plan is not None:
                        # Timeout may carry a checker-approved incumbent. It still
                        # must independently pass our checker and production mask.
                        result = result.model_copy(update={"status": "ok"})
            except Exception as exc:
                row["stages"]["exact"].update(passed=False, error=f"{type(exc).__name__}: {exc}")
                result = None
        if result is None or result.plan is None:
            solver_budget = 120.0 if constructor_fallback else config["solver_s"]
            with stage("scalable", solver_budget + 5) as details:
                options = dict(config["scalable_config"])
                if "feasibility_config" in options:
                    raise ValueError("feasibility_config override forbidden: D79 C2 off")
                if config["constructor_mode"] == "v4":
                    options["constructor_v4"] = True
                if constructor_fallback:
                    options.update(securing_screen=True, heavy_first_ordering=True)
                planner = ScalableSolver(ScalableConfig(feasibility_config=feasibility, **options))
                result = planner.plan(instance, seed=instance.seed, time_limit_s=solver_budget)
                if constructor_fallback:
                    result = result.model_copy(update={"metrics": dict(result.metrics,
                        source="constructor-fallback (two-step failed)", baseline_available=False)})
                details.update(status=result.status, passed=result.status == "ok" and result.plan is not None,
                               metrics=result.metrics, message=result.message, config=options,
                               planner_version=result.planner_version)
                if not details["passed"]:
                    raise RuntimeError(f"Scalable solver: {result.status}: {result.message}")
        row.update(solver=result.planner, solver_version=result.planner_version,
                   solver_label=result.metrics.get("optimality_label", "best-found; optimality unproved"),
                   solver_metrics=result.metrics)
        require_clean(result, what="gold solver result")
        ballast_budget = external_ballast_budget
        with stage("ballast", ballast_budget + 5) as details:
            with scoped_ballast_v4(warmstart or config.get("ballast_v4", False)):
                details["ballast_environment"] = {"STOWAGE_BALLAST_V4": os.environ.get("STOWAGE_BALLAST_V4")}
                result = finalize(instance, result, time_limit_s=ballast_budget)
            details.update(status=result.status, metrics=result.metrics, passed=result.status == "ok" and result.plan is not None)
            if not details["passed"]:
                raise RuntimeError(f"Ballast finisher: {result.status}: {result.message}")
        with stage("checker", config["checker_s"]) as details:
            report = check(instance, result.plan)
            atomic_json(directory / "checker.json", json.loads(report.model_dump_json()))
            details.update(passed=report.passed, violations=[v.model_dump(mode="json") for v in report.violations])
            result._record_full_check(instance, report)
            if not result.shippable:
                raise RuntimeError("Full checker or clean-provenance shipping gate failed")
            row["count"] = report.count.model_dump(mode="json")
            row["objective"] = report.count.weighted_nonproductive
            if warmstart and not constructor_fallback:
                baseline = row.get("two_step_weighted_moves")
                if type(baseline) not in (int, float) or not math.isfinite(baseline) or baseline < 0:
                    current_stage = "quality"
                    raise RuntimeError("Warm-start lacks a measured two-step comparator")
                row["gold_weighted_moves"] = row["objective"]
                row["quality_delta"] = row["objective"]-baseline
                if row["objective"] > baseline:
                    current_stage = "quality"
                    raise RuntimeError("Independent checker objective is worse than the two-step incumbent")
            elif constructor_fallback:
                row["gold_weighted_moves"] = row["objective"]
        with stage("mask", config["mask_s"]) as details:
            if warmstart:
                replay(instance, result.plan, details, securing=True)
            else:
                replay(instance, result.plan, details)
        # Corpus shards can arrive while solving: recheck cohort identity before
        # publication so a newly committed duplicate cannot bypass admission.
        current_stage = "input"
        row["split_admission"] = verify_candidate(input_path, corpus_root, directory.parents[4])
        row["runtime_import_origins"] = assert_snapshot_imports()
        after = source_pin()
        atomic_json(directory / "source-after.json", after)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        row["changed_source_files"] = changed
        row["relevant_changed_source_files"] = [p for p in changed if relevant_source(
            p, used_scalable="scalable" in row["stages"], used_twostep=warmstart)]
        if row["relevant_changed_source_files"]:
            current_stage = "source_drift"
            raise RuntimeError("Pipeline source changed during instance; restart/import a fresh epoch")
        require_clean(instance, result, report, what="published gold")
        temporary = directory / ".plan.partial.json"
        save_plan(result.plan, temporary)
        temporary.replace(directory / "plan.json")
        row.update(passed=True, plan_sha256=sha256(directory / "plan.json"))
    except Exception as exc:
        row.update(drop_reason=current_stage, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    except BaseException as exc:
        interrupted = True
        row.update(passed=False, interrupted=True, drop_reason=current_stage,
                   error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        own_cpu, children_cpu = time.process_time()-cpu_start, child_cpu_s()-child_start
        row.update(completed=not interrupted, wall_s=time.perf_counter()-start,
                   process_cpu_s=own_cpu, child_cpu_s=children_cpu, total_cpu_s=own_cpu+children_cpu,
                   cpu_measurement="own process plus reaped children; nested grandchildren may be platform-dependent",
                   process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                   finished_utc=datetime.now(timezone.utc).isoformat())
        if "legacy_quality" in row:
            row["legacy_quality"].update(final_pipeline_passed=row.get("passed", False),
                published=bool(row.get("passed") and row["legacy_quality"].get("selected")),
                final_pipeline_drop_reason=row.get("drop_reason"))
            atomic_json(directory / "legacy-quality.json", row["legacy_quality"])
            publish_legacy_decision(directory.parents[4], row["case"], row["legacy_quality"])
        atomic_json(target, row)
    print(json.dumps({k: row.get(k) for k in ("case", "passed", "drop_reason", "wall_s")}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-root", type=Path, default=LIVE_ROOT / "experiments/mvp/corpus")
    parser.add_argument("--output", type=Path, default=LIVE_ROOT / "experiments/mvp/gold")
    parser.add_argument("--runtime-snapshot", type=Path, help="Immutable copied src/params/resources epoch with MANIFEST.json")
    parser.add_argument("--runtime-hash", help="Expected compact-sorted manifest files SHA256")
    parser.add_argument("--rung", nargs="+", choices=("tiny", "small", "medium"), default=["tiny"])
    parser.add_argument("--split", nargs="+", choices=("train", "val"), default=["train", "val"])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--adaptive-workers", action="store_true", help="Select largest admitted worker count up to --workers per shard")
    parser.add_argument("--resume-finished", action="store_true", help="Keep completed prior configurations; inputs/plans remain hash checked")
    parser.add_argument("--max-instances", type=int, help="Maximum newly processed instances this invocation")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--poll-s", type=float, default=15)
    parser.add_argument("--max-load", type=float, default=16, help="Maximum live one-minute load for admission")
    parser.add_argument("--policy-file", type=Path, help="Live scheduler policy JSON; reload max_load/train_first/val_gold_caps each shard")
    parser.add_argument("--priority-file", type=Path, help="Scheduling-only requested_rerun case list; prioritize pending retries")
    parser.add_argument("--exact-s", type=float, default=60)
    parser.add_argument("--solver-s", type=float, default=60)
    parser.add_argument("--ballast-s", type=float, default=45)
    parser.add_argument("--checker-s", type=float, default=120)
    parser.add_argument("--mask-s", type=float, default=180)
    parser.add_argument("--scalable-config", type=Path, help="JSON ScalableConfig keyword overrides; LNS enabled by default")
    parser.add_argument("--input-settings", type=Path, help="Required audit.config subset JSON; wait for matching inputs without dropping older shards")
    parser.add_argument("--constructor-mode", choices=("default", "v4"), default="default")
    parser.add_argument("--ballast-v4", action="store_true", help="Opt into STOWAGE_BALLAST_V4=1 only during finalize; absent for solve/check/mask")
    parser.add_argument("--gold-warmstart", action="store_true", help="D97 small-only checked two-step v4.3 plus 15s LNS and D95 securing decoder")
    parser.add_argument("--gold-constructor-fallback", action="store_true", help="Revision: screened constructor when no checked two-step baseline exists")
    parser.add_argument("--legacy-small-root", type=Path, help="Archived constructor cases to compare and freshly recheck in warmstart small mode")
    parser.add_argument("--admit-ledger", type=Path, help="Queue until shared reservations and live load fit")
    parser.add_argument("--ledger-owner", help="Exact owned ledger row to reserve/release; leader must create it first")
    parser.add_argument("--stop-file", type=Path, help="Exit cooperatively when this file exists, between shards or while queued")
    parser.add_argument("--case", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--config-json", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.workers <= 12 or (args.max_instances is not None and args.max_instances < 1):
        parser.error("workers must be 1..12; max-instances must be positive")
    for name in ("exact_s", "solver_s", "ballast_s", "checker_s", "mask_s", "poll_s", "max_load"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            parser.error(f"{name} must be finite and positive")
    if args.exact_s > 60:
        parser.error("Tiny exact search limit must be <=60s")
    if (args.adaptive_workers or args.ledger_owner) and not args.admit_ledger:
        parser.error("--adaptive-workers and --ledger-owner require --admit-ledger")
    config = json.loads(args.config_json) if args.config_json else dict(
        exact_s=args.exact_s, solver_s=args.solver_s, ballast_s=args.ballast_s,
        checker_s=args.checker_s, mask_s=args.mask_s, constructor_mode=args.constructor_mode,
        ballast_v4=args.ballast_v4,
        scalable_config={})
    if not isinstance(config, dict):
        parser.error("--config-json must contain a JSON object")
    if args.gold_warmstart:
        config["gold_warmstart_v1"] = True
    if args.gold_constructor_fallback:
        if not config.get("gold_warmstart_v1"):
            parser.error("--gold-constructor-fallback requires --gold-warmstart")
        config["gold_constructor_fallback_v1"] = True
    if args.legacy_small_root:
        config["legacy_small_root"] = str(args.legacy_small_root.resolve())
    if not args.config_json:
        try:
            config = reload_case_config(config, args.scalable_config, args.input_settings)
        except (OSError, ValueError) as exc:
            parser.error(f"Cannot read case configuration files: {exc}")
    if args.runtime_hash and not (args.runtime_snapshot or config.get("runtime_snapshot")):
        parser.error("--runtime-hash requires --runtime-snapshot")
    snapshot = args.runtime_snapshot or config.get("runtime_snapshot")
    if snapshot:
        metadata = configure_runtime(snapshot, args.runtime_hash or config.get("runtime_epoch_sha256"))
        config.update(metadata)
    if args.case:
        process_case(args.case, args.corpus_root, args.output, config, resume_finished=args.resume_finished)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    publish_manifest(args.output, args.corpus_root, config)
    done = 0
    effective_policy = load_policy(args.policy_file, args.max_load)
    active = set()
    state_lock = threading.Lock()
    current_workers = 0
    deferred_incomplete = []
    def runtime(status, **details):
        with state_lock:
            publish_runtime(args.output, dict(pid=os.getpid(), status=status,
                processed_this_invocation=done,
                workers=len(active) if effective_policy["admission_mode"] in {"live_load_first", "measured_cpu"} else current_workers,
                admitted_worker_slots=current_workers,
                active_worker_pids=sorted(active), follow=args.follow,
                active_workers=len(active),
                effective_policy=effective_policy,
                active_config=config,
                configuration_sha256=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                deferred_incomplete_configs=list(deferred_incomplete),
                updated_utc=datetime.now(timezone.utc).isoformat(), **details))
    def reserve(workers):
        if args.ledger_owner:
            update_ledger_reservation(args.admit_ledger, args.ledger_owner, workers)
    def release_live_start():
        with shared_admission_lock(args.output):
            reserve_live_start(args.output, 0)
    def launch(path, case_config=None, live_job=None):
        case_config = config if case_config is None else case_config
        live_job = effective_policy["admission_mode"] in {"live_load_first", "measured_cpu"} if live_job is None else live_job
        directory = result_dir(path, args.corpus_root, args.output)
        directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(Path(__file__).resolve()), "--case", str(path),
                   "--corpus-root", str(args.corpus_root), "--output", str(args.output),
                   "--config-json", json.dumps(case_config)]
        if args.resume_finished:
            command.append("--resume-finished")
        with (directory / "worker.log").open("a") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            atomic_json(directory / "process.json", dict(pid=process.pid, parent_pid=os.getpid(), argv=command))
            with state_lock:
                active.add(process.pid)
                if live_job:
                    reserve(len(active))
            runtime("running")
            try:
                code = process.wait()
            finally:
                with state_lock:
                    active.discard(process.pid)
                    if live_job:
                        reserve(len(active))
                runtime("running")
        if code:
            raise RuntimeError(f"Worker {process.pid} exited {code}; see {directory / 'worker.log'}")
    checked_finished = set()
    live_pool = ThreadPoolExecutor(max_workers=args.workers)
    live_futures = {}
    live_changed = threading.Event()
    def live_poll():
        live_changed.wait(timeout=args.poll_s)
        live_changed.clear()
    def finish_live(wait=False):
        nonlocal done
        for future, (path, ticket) in list(live_futures.items()):
            if wait or future.done():
                future.result()
                del live_futures[future]
                with shared_admission_lock(args.output):
                    release_live_ticket(args.output, ticket)
                done += 1
                render_reports(args.output, args.corpus_root)
    def run_shards():
      nonlocal done, current_workers, effective_policy, config, deferred_incomplete
      while True:
        finish_live()
        if args.stop_file and args.stop_file.exists():
            runtime("draining", stop_file=str(args.stop_file))
            finish_live(wait=True)
            runtime("stopped", stop_file=str(args.stop_file))
            break
        effective_policy = load_policy(args.policy_file, args.max_load)
        if not args.config_json:
            updated = reload_case_config(config, args.scalable_config, args.input_settings, effective_policy)
            if updated != config:
                config = updated
                publish_manifest(args.output, args.corpus_root, config)
                runtime("configuration_updated")
        if effective_policy.get("max_workers", args.workers) == 0:
            if not live_futures:
                reserve(0)
                release_live_start()
            current_workers = len(live_futures)
            runtime("policy_draining" if live_futures else "policy_paused")
            render_reports(args.output, args.corpus_root)
            live_poll() if live_futures else time.sleep(args.poll_s)
            continue
        pending = []
        deferred_incomplete = []
        for path in filter_input_settings(discover(args.corpus_root, args.rung, args.split),
                                          config.get("input_settings", {})):
            if any(path == record[0] for record in live_futures.values()):
                continue
            target = result_dir(path, args.corpus_root, args.output) / "result.json"
            if target.exists():
                prior = json.loads(target.read_text())
                if not prior.get("completed") and prior.get("config") != config:
                    # A live rollout affects new work, never rewrites an
                    # interrupted case's immutable request. Operators can
                    # explicitly finish that old configuration separately.
                    deferred_incomplete.append(str(target))
                    continue
                if prior.get("completed") and target not in checked_finished:
                    prior = process_case(path, args.corpus_root, args.output, config,
                                         resume_finished=args.resume_finished)
                    checked_finished.add(target)
                if (prior.get("input_sha256") != sha256(path) or
                        (prior.get("config") != config and not (args.resume_finished and prior.get("completed")))):
                    raise ValueError(f"Existing output identity mismatch: {target}")
                if prior.get("completed"):
                    if prior.get("passed"):
                        plan_path = target.parent / "plan.json"
                        if not plan_path.exists() or sha256(plan_path) != prior.get("plan_sha256"):
                            raise ValueError(f"Published gold plan missing or changed: {plan_path}")
                    continue
            pending.append(path)
        worker_limit = min(args.workers, effective_policy.get("max_workers", args.workers))
        live_mode = effective_policy["admission_mode"] in {"live_load_first", "measured_cpu"}
        if live_futures and not live_mode:
            # Drain the old live pool before switching back to batch admission.
            current_workers = len(live_futures)
            runtime("draining_mode_change")
            time.sleep(args.poll_s)
            continue
        worker_limit = max(0, worker_limit-len(live_futures))
        if args.max_instances is not None:
            worker_limit = min(worker_limit, max(0, args.max_instances-done-len(live_futures)))
        waiting_train = ("train" in args.split and effective_policy["train_first"] and
                         train_inventory_waiting(args.corpus_root, args.output, args.rung,
                                                 config.get("input_settings", {})))
        dispatch_policy = dict(effective_policy, val_gold_caps=dict(effective_policy["val_gold_caps"]))
        for path, _ in live_futures.values():
            split, rung = path.relative_to(args.corpus_root).parts[:2]
            if split == "val" and rung in dispatch_policy["val_gold_caps"]:
                dispatch_policy["val_gold_caps"][rung] = max(0, dispatch_policy["val_gold_caps"][rung]-1)
        pending = prioritize_pending(pending, args.corpus_root, args.priority_file)
        pending = select_pending(pending, args.output, args.corpus_root, worker_limit,
                                 dispatch_policy, train_waiting=waiting_train)
        if pending:
            current_workers = min(args.workers, len(pending))
            ticket = f"{os.getpid()}-{time.monotonic_ns()}"
            if args.admit_ledger:
                with shared_admission_lock(args.output):
                    options = dict(max_load=effective_policy["max_load"])
                    live = effective_policy["admission_mode"] in {"live_load_first", "measured_cpu"}
                    lease_s = 5 if effective_policy["admission_mode"] == "measured_cpu" else 60
                    if live:
                        options.update(admission_mode=effective_policy["admission_mode"],
                                       recent_start_workers=recent_live_workers(args.output, lease_s=lease_s))
                        if effective_policy["admission_mode"] == "measured_cpu":
                            options["active_gold_workers"] = shared_active_workers(args.output)
                    detail = select_admission(args.admit_ledger, current_workers,
                                              args.adaptive_workers, args.ledger_owner, **options)
                    current_workers = detail["actual_workers"] if detail["admitted"] else 0
                    if live:
                        if current_workers:
                            reserve_live_start(args.output, current_workers, append=True, ticket=ticket, lease_s=lease_s)
                        if not live_futures:
                            reserve(0)
                    else:
                        reserve(current_workers)
                atomic_json(args.output / "admission.json", detail)
                if not detail["admitted"]:
                    runtime("queued_admission", admission=detail)
                    live_poll() if live_futures else time.sleep(args.poll_s)
                    continue
            pending = pending[:current_workers]
            if not args.admit_ledger:
                reserve(current_workers)
            runtime("starting")
            if live_mode:
                for path in pending:
                    future = live_pool.submit(launch, path, config, True)
                    live_futures[future] = (path, ticket)
                    future.add_done_callback(lambda _: live_changed.set())
                current_workers = len(live_futures)
                runtime("running")
                live_poll()
                continue
            # Threads only supervise independent fresh one-core child processes.
            with ThreadPoolExecutor(max_workers=current_workers) as pool:
                for _ in pool.map(launch, pending):
                    done += 1
                    runtime("running")
                    render_reports(args.output, args.corpus_root)
            reserve(0)
            release_live_start()
            current_workers = 0
        if live_futures:
            current_workers = len(live_futures)
            runtime("running")
            live_poll()
            continue
        if (not args.follow and not pending) or (args.max_instances is not None and done >= args.max_instances):
            break
        if not pending:
            reserve(0)
            release_live_start()
            current_workers = 0
            runtime("waiting_corpus", input_settings=config.get("input_settings", {}),
                    waiting_train=waiting_train)
            render_reports(args.output, args.corpus_root)
            time.sleep(args.poll_s)
    failed = False
    try:
        run_shards()
    except BaseException as exc:
        failed = True
        runtime("error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        live_pool.shutdown(wait=True)
        reserve(0)
        release_live_start()
        current_workers = 0
        if not failed and not (args.stop_file and args.stop_file.exists()):
            runtime("finished")


if __name__ == "__main__":
    main()
