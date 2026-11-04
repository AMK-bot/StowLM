"""Exact old/new mask comparisons, checkpointed by terminal masked rollout.

Use OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1.
The restored production engine and quarantined candidate use identical physical
inputs and checks. Index arrays are compared
directly (including order and dtype); hashes in the result are evidence, not the
comparison itself. A time-capped pilot is explicitly incomplete and never passes
the full-corpus test. Checkpoints are reusable only under identical source pins.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import resource
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MANIFEST = HERE / "corpus-manifest.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_fingerprint():
    import scipy
    from scipy.optimize._highspy import _core
    binary = Path(_core.__file__)
    return dict(python=sys.version, python_executable=sys.executable, platform=sys.platform,
                numpy=np.__version__, scipy=scipy.__version__, highs_version=_core._Highs().version(),
                highs_binary=str(binary), highs_binary_sha256=sha(binary),
                thread_environment={key: os.environ.get(key) for key in
                    ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
                project={name: sha(ROOT / name) for name in ("pyproject.toml", "uv.lock")})


def usage():
    value = resource.getrusage(resource.RUSAGE_SELF)
    return dict(user_s=value.ru_utime, system_s=value.ru_stime,
                peak_rss_bytes=value.ru_maxrss * (1 if sys.platform == "darwin" else 1024))


class StopRequested(RuntimeError):
    """Cooperative stop at a durable checkpoint; never a successful terminal walk."""


class MaskMismatch(AssertionError):
    def __init__(self, details):
        super().__init__(f"Allowed-action mismatch at depth {details['depth']}")
        self.details = details


def pins():
    paths = []
    for area in ("feasibility", "linear", "checker", "vessel", "model", "generate", "actions", "rules", "moves", "yard", "ports"):
        paths.extend((ROOT / "src/stowage" / area).rglob("*.py"))
    paths.extend((ROOT / "src/stowage").glob("*.py"))
    paths.extend(p for p in (ROOT / "src/stowage/rules").rglob("*") if p.suffix in (".toml", ".json"))
    paths.extend(p for p in (ROOT / "params").rglob("*") if p.suffix in (".toml", ".yaml", ".yml", ".json"))
    paths.extend((ROOT / "src/stowage/linear").glob("*.json"))
    paths.extend([HERE / "equivalence.py", HERE / "corpus.py", HERE / "corpus-measurement-prefixes.json",
                  HERE / "candidate.py", HERE / "candidate/manifest.json",
                  ROOT / "tests/feasibility/test_perf_equivalence_1028.py"])
    paths.extend((HERE / "candidate").glob("*.py"))
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths) if p.exists()}


def manifest(path=MANIFEST):
    data = json.loads(path.read_text())
    for row in data["instances"]:
        assert sha(ROOT / row["path"]) == row["sha256"], f"Changed corpus input: {row['id']}"
    return data


def make_pair(instance, c2):
    from stowage.feasibility import FeasibilityConfig
    module_name = "perf_1028_quarantined_candidate"
    candidate = sys.modules.get(module_name)
    if candidate is None:
        spec = importlib.util.spec_from_file_location(module_name, HERE / "candidate.py")
        candidate = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = candidate
        spec.loader.exec_module(candidate)
    engines = [candidate.make_engine(instance, FeasibilityConfig(extra={"global_screen": int(c2)}),
                                     optimized=bool(optimized)) for optimized in (0, 1)]
    for engine in engines:
        if c2:
            assert engine.diagnostics["global_screen"] == "on", engine.diagnostics["global_screen"]
            assert bool(getattr(engine._global, "optimized", False)) == (engine is engines[1])
        assert bool(engine.config.extra["c2_optimized"]) == (engine is engines[1])
    return engines


def compare_state(before, after):
    old = before.legal_index()
    new = after.legal_index()
    if (old.dtype != new.dtype or old.dtype != np.dtype("int64") or not np.array_equal(old, new)
            or before.legal_discharges() != after.legal_discharges()
            or before.is_complete() != after.is_complete()):
        missing = np.setdiff1d(old, new)
        extra = np.setdiff1d(new, old)
        raise MaskMismatch(dict(depth=before.depth, reference_dtype=str(old.dtype), candidate_dtype=str(new.dtype),
            reference_index=old.tolist(), candidate_index=new.tolist(),
            missing_index=missing.tolist(), extra_index=extra.tolist(),
            missing_actions=[before.space.encode(before.decode_index(int(i))) for i in missing],
            extra_actions=[after.space.encode(after.decode_index(int(i))) for i in extra],
            reference_discharges=[before.space.encode(a) for a in before.legal_discharges()],
            candidate_discharges=[after.space.encode(a) for a in after.legal_discharges()],
            reference_complete=before.is_complete(), candidate_complete=after.is_complete()))
    return dict(depth=before.depth, places=len(old), discharges=len(before.legal_discharges()),
                mask_sha256=hashlib.sha256(old.tobytes()).hexdigest())


def walk(row, c2, seed, *, max_steps=None, saved_actions=None, checkpoint=None):
    from stowage.io import load_instance
    path = ROOT / row["path"]
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row["sha256"], f"Changed corpus input: {row['id']}"
    if "uncompressed_sha256" in row:
        assert hashlib.sha256(raw).hexdigest() == row["uncompressed_sha256"]
    instance = load_instance(path)
    assert sha(path) == row["sha256"], f"Corpus changed while loading: {row['id']}"
    before, after = make_pair(instance, c2)
    rng = random.Random(seed)
    result = dict(instance=row["id"], c2=bool(c2), seed=seed, states=[], actions=[], status="running")
    started = time.perf_counter()
    while True:
        try:
            state = compare_state(before, after)
        except MaskMismatch as error:
            error.details.update(instance=row, c2=bool(c2), seed=seed, prefix_actions=list(result["actions"]))
            raise
        result["states"].append(state)
        result["wall_s"] = time.perf_counter() - started
        if checkpoint:
            checkpoint(result)
        actions = before.legal_actions()
        if not actions:
            result["status"] = "complete" if before.is_complete() else "dead"
            break
        if saved_actions is not None and before.depth == len(saved_actions):
            result["status"] = "measurement-prefixes"
            break
        if max_steps is not None and before.depth >= max_steps:
            result["status"] = "pilot-capped"
            break
        action = before.space.decode(saved_actions[before.depth]) if saved_actions is not None else rng.choice(actions)
        result["actions"].append(before.space.encode(action))
        before.push(action)
        after.push(action)
    result["wall_s"] = time.perf_counter() - started
    if saved_actions is not None:
        assert len(result["actions"]) == len(saved_actions), "Measurement replay ended before every saved prefix"
    result["diagnostics"] = [dict(engine.diagnostics) for engine in (before, after)]
    return result


def measurement_actions(row, c2, path):
    """The order's 12 seed-1704 prefixes, persisted by the earlier measurement."""
    data = json.loads(path.read_text())
    key = f"{row['rung']}/c2-{int(c2)}"
    assert data["blocks"][key]["instance_sha256"] == row.get("uncompressed_sha256", row["sha256"]), key
    states = data["blocks"][key]["states"]
    actions = [state["action"] for state in states if "action" in state]
    assert actions, f"Measurement source has no saved actions: {path}:{key}"
    return actions


def atomic_write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "equivalence-result.json")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--instances", nargs="+")
    parser.add_argument("--c2", nargs="+", type=int, choices=(0, 1), default=[0, 1])
    parser.add_argument("--seeds", nargs="+", type=int)
    parser.add_argument("--max-steps", type=int, help="Pilot only: capped walks do not prove full-rollout equivalence")
    parser.add_argument("--measurement-prefixes", action="store_true")
    parser.add_argument("--only-measurement-prefixes", action="store_true")
    parser.add_argument("--measurement-source", type=Path, default=HERE / "corpus-measurement-prefixes.json")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--stop-file", type=Path, help="Default: OUTPUT.stop; creates an explicitly incomplete result")
    parser.add_argument("--deadline", help="ISO-8601 timestamp with timezone; stop at the next durable checkpoint")
    parser.add_argument("--abort-file", type=Path, help="Shared mismatch sentinel checked by every shard")
    args = parser.parse_args()
    stop_file = args.stop_file or Path(str(args.output) + ".stop")
    deadline = datetime.fromisoformat(args.deadline.replace("Z", "+00:00")) if args.deadline else None
    if deadline is not None:
        assert deadline.tzinfo is not None, "Deadline requires an explicit timezone"
    corpus = manifest(args.manifest)
    source_pins = pins()
    runtime = runtime_fingerprint()
    replay_pin = sha(args.measurement_source) if args.measurement_prefixes or args.only_measurement_prefixes else None
    output = dict(schema=2, sources=source_pins, manifest_path=str(args.manifest), manifest_sha256=sha(args.manifest), runtime=runtime,
                  measurement_source_sha256=replay_pin,
                  runs={})
    if args.resume and args.output.exists():
        output = json.loads(args.output.read_text())
        assert output["sources"] == source_pins, "Source pins changed; use a fresh result filename"
        assert output["manifest_sha256"] == sha(args.manifest), "Corpus changed"
        assert output.get("runtime") == runtime, "Runtime, dependency binary, project lock, or thread environment changed"
        assert output.get("measurement_source_sha256") == replay_pin, "Measurement replay source changed"
        assert not output.get("changed_sources"), "Previous run had source drift"
        output.pop("failure", None)
    output["worker"] = dict(pid=os.getpid(), stop_file=str(stop_file), deadline=args.deadline,
                             start=datetime.now(timezone.utc).isoformat())
    started = time.perf_counter()
    for row in corpus["instances"]:
        if args.instances and row["id"] not in args.instances:
            continue
        for c2 in args.c2:
            seeds = args.seeds or corpus["seeds"]
            tasks = [] if args.only_measurement_prefixes else [(f"seed-{seed}", seed, None) for seed in seeds]
            if (args.measurement_prefixes or args.only_measurement_prefixes) and row.get("measurement", False):
                tasks.append(("measurement", 1704, measurement_actions(row, c2, args.measurement_source)))
            for label, seed, actions in tasks:
                if args.abort_file is not None and args.abort_file.exists():
                    raise StopRequested(f"Another shard reported a mismatch: {args.abort_file}")
                key = f"{row['id']}/c2-{c2}/{label}"
                if key in output["runs"] and output["runs"][key]["status"] in ("complete", "dead", "measurement-prefixes"):
                    continue
                def checkpoint(result):
                    output["runs"][key] = result
                    atomic_write(args.output, output)
                    if (stop_file.exists() or (args.abort_file is not None and args.abort_file.exists())
                            or (deadline is not None and datetime.now(timezone.utc) >= deadline)):
                        result["status"] = "stopped"
                        atomic_write(args.output, output)
                        raise StopRequested(f"Cooperative stop: {stop_file} or deadline {args.deadline}")
                try:
                    result = walk(row, c2, seed, max_steps=args.max_steps, saved_actions=actions, checkpoint=checkpoint)
                except Exception as error:
                    output["failure"] = dict(key=key, error=f"{type(error).__name__}: {error}")
                    if isinstance(error, MaskMismatch):
                        output["first_mismatch"] = error.details
                        if args.abort_file is not None:
                            atomic_write(args.abort_file, dict(key=key, output=str(args.output), details=error.details))
                    output["sources_after"] = pins()
                    output["changed_sources"] = {name: value for name, value in output["sources_after"].items()
                                                 if source_pins.get(name) != value}
                    output["wall_s"] = output.get("wall_s", 0.0) + time.perf_counter() - started
                    output["runtime_after"] = runtime_fingerprint()
                    output["usage"] = usage()
                    atomic_write(args.output, output)
                    raise
                output["runs"][key] = result
                atomic_write(args.output, output)
                print(key, result["status"], "steps", len(result["actions"]), "s", round(result["wall_s"], 3), flush=True)
    output["wall_s"] = output.get("wall_s", 0.0) + time.perf_counter() - started
    output["sources_after"] = pins()
    manifest(args.manifest)  # Recheck all immutable corpus archives before claiming success.
    output["runtime_after"] = runtime_fingerprint()
    output["usage"] = usage()
    output["changed_sources"] = {key: value for key, value in output["sources_after"].items() if source_pins.get(key) != value}
    atomic_write(args.output, output)
    assert not output["changed_sources"], f"Source drift during run: {list(output['changed_sources'])}"
    assert output["runtime_after"] == runtime, "Runtime changed during run"


if __name__ == "__main__":
    main()
