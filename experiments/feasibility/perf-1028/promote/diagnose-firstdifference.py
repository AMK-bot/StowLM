"""Bounded diagnosis only: replay the preserved first mismatch without fixes."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

import numpy as np

HERE = Path(__file__).resolve().parent
PERF = HERE.parent
ROOT = PERF.parents[2]


class DiagnosticDeadline(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path, value):
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def summarize(engine):
    g, st = engine._global, engine._cur
    ref = st.ref
    interval = st.c2_sols.get(engine.T.slot_index.get("0110182", -1), "not_queried")
    if isinstance(interval, str):
        slot = interval
    elif interval is None:
        slot = "infeasible"
    elif isinstance(interval, tuple):
        slot = [float(interval[0][0]), float(interval[1][0])]
    else:
        slot = "unknown"
    return dict(places=len(st.mask) if st.mask is not None else None,
                ref_type=type(ref).__name__, ref_centered=getattr(ref, "centered", None),
                lp_calls=g.lp_calls, lp_indeterminate=g.lp_indeterminate,
                endpoint_hits=getattr(g, "witness_hits", 0), parent_hits=getattr(g, "parent_witness_hits", 0),
                target_slot_interval=slot, diagnostics=dict(engine.diagnostics))


def main():
    started = time.perf_counter()
    deadline = started + 115.
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(DiagnosticDeadline("115 s deadline")))
    signal.alarm(115)
    artifact = HERE / "STOP_MISMATCH.json"
    artifact_sha = sha(artifact)
    saved = json.loads(artifact.read_text())
    details = saved["details"]
    instance_path = ROOT / details["instance"]["path"]
    assert sha(instance_path) == details["instance"]["sha256"]
    spec = importlib.util.spec_from_file_location("perf_1028_diagnosis_candidate", PERF / "candidate.py")
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    from stowage.feasibility import FeasibilityConfig
    from stowage.io import load_instance
    instance = load_instance(instance_path)
    output = dict(kind="first exact-mask mismatch diagnosis only; no fixes or promotion", pid=os.getpid(),
                  artifact=str(artifact.relative_to(ROOT)), artifact_sha256=artifact_sha,
                  instance_path=str(instance_path.relative_to(ROOT)), instance_sha256=sha(instance_path),
                  prefix_actions=details["prefix_actions"], variants={}, stopped=False,
                  thread_environment={k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")})
    output_path = HERE / "diagnose-firstdifference.json"
    print(json.dumps(dict(pid=os.getpid(), status="started", deadline_seconds=115)), flush=True)
    reference_final = None
    try:
        for variant in ("reference", "candidate", "LP_only", "DG_only", "parent_disabled", "endpoint_disabled", "pool_disabled"):
            if time.perf_counter() >= deadline:
                raise DiagnosticDeadline("deadline at variant boundary")
            t0 = time.perf_counter()
            engine = helper.make_engine(instance, FeasibilityConfig(extra={"global_screen": 1}), variant != "reference")
            assert engine.diagnostics["global_screen"] == "on", engine.diagnostics
            g = engine._global
            if variant == "LP_only":
                engine._perf_optimized = False
            elif variant == "DG_only":
                g.optimized = False
            elif variant == "parent_disabled":
                original = g.reference
                def reference_without_parent(*args, _g=g, _original=original, **kwargs):
                    previous = _g.optimized
                    _g.optimized = False
                    try:
                        return _original(*args, **kwargs)
                    finally:
                        _g.optimized = previous
                g.reference = reference_without_parent
            elif variant == "endpoint_disabled":
                g._witness_interval = lambda *args, **kwargs: None
            elif variant == "pool_disabled":
                g._remember_endpoints = lambda *args, **kwargs: None
            record = dict(states=[], status="running")
            output["variants"][variant] = record
            for depth in range(len(details["prefix_actions"]) + 1):
                if time.perf_counter() >= deadline:
                    raise DiagnosticDeadline("deadline at prefix boundary")
                mask = engine.legal_index()
                state = dict(depth=depth, mask_sha256=hashlib.sha256(mask.tobytes()).hexdigest(), **summarize(engine))
                record["states"].append(state)
                atomic_write(output_path, output)
                if depth == len(details["prefix_actions"]):
                    break
                action = engine.space.decode(details["prefix_actions"][depth])
                if not engine.is_legal(action):
                    record.update(status="prefix_action_rejected", rejected_depth=depth)
                    break
                engine.push(action)
            if record["status"] == "running":
                record["status"] = "finished"
                if reference_final is None:
                    reference_final = mask.copy()
                missing, extra = np.setdiff1d(reference_final, mask), np.setdiff1d(mask, reference_final)
                record.update(missing_index=missing.tolist(), extra_index=extra.tolist(),
                              missing_count=len(missing), extra_count=len(extra),
                              missing_actions=[engine.space.encode(engine.decode_index(int(i))) for i in missing])
                if len(missing):
                    bs, ss = missing // engine.n_slots, missing % engine.n_slots
                    record["missing_c2_admissions"] = engine._c2(engine._cur, bs, ss).tolist()
                    record["missing_masses_kg"] = engine.T.mass[bs].tolist()
                ref = engine._cur.ref
                if ref is not None and hasattr(ref, "y"):
                    np.savez_compressed(HERE / f"diagnose-medium-{variant}-ref.npz", **{
                        **{k: getattr(g, k) for k in ("A", "At", "const", "lo", "hi", "tank_lo", "tank_hi", "u0")},
                        **{f"ref_{k}": getattr(ref, k) for k in ("fixed", "free", "R", "y", "t", "v", "Y")}})
            record["wall_s"] = time.perf_counter() - t0
            print(json.dumps(dict(variant=variant, status=record["status"], wall_s=record["wall_s"],
                                  missing=record.get("missing_count"), extra=record.get("extra_count"))), flush=True)
            atomic_write(output_path, output)
    except DiagnosticDeadline as exc:
        output.update(stopped=True, stop_reason=str(exc))
    finally:
        signal.alarm(0)
        output.update(wall_s=time.perf_counter() - started,
                      maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      artifact_unchanged=sha(artifact) == artifact_sha)
        atomic_write(output_path, output)
    print(json.dumps(dict(status="done", wall_s=output["wall_s"], stopped=output["stopped"],
                          maxrss_bytes_macos=output["maxrss_bytes_macos"])), flush=True)


if __name__ == "__main__":
    main()
