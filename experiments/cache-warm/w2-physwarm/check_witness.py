"""Fresh-process, unchanged-budget public witness with observational wrappers."""
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
from time import perf_counter

MODE = sys.argv[1]
assert MODE in ("direct", "fast")
assert os.environ.get("STOWAGE_STABILITY_TIME_BUDGET_S") is None
assert os.environ.get("STOWAGE_STABILITY_DIRECT") is None
assert os.environ.get("STOWAGE_STABILITY_LIQUID_DIRECT") == (
    "1" if MODE == "direct" else None)

from stowage.checker import full, stability_curve, stability_tank_transfer
from stowage.io import load_instance, load_plan
from stowage.rules.catalogue import rule_freeze_v1

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
FIXTURE = ROOT / "experiments/checker/followup-1028/d71-refinement/small"
instance = load_instance(FIXTURE / "instance.json")
plan = load_plan(FIXTURE / "plan.json")
curves, envelope_calls = [], []
current = {}
original_part = full._run_part


@wraps(original_part)
def observe_part(context, state, name, function):
    previous = dict(current)
    current.update(condition=context.id if hasattr(context, "id") else str(context.call_index),
                   part=name)
    try:
        return original_part(context, state, name, function)
    finally:
        current.clear()
        current.update(previous)


full._run_part = observe_part
original_curve = stability_curve.build_stability_curve


@wraps(original_curve)
def observe_curve(*args, **kwargs):
    curve = original_curve(*args, **kwargs)
    curves.append(dict(context=dict(current), angles_rad=curve.angles.tolist(),
                       gz_m=curve.gz.tolist(), negative_gz_m=curve.negative_gz.tolist(),
                       flooding_angle_rad=float(curve.flooding_angle)))
    return curve


stability_curve.build_stability_curve = observe_curve
original_transfer = stability_tank_transfer.max_transfer


@wraps(original_transfer)
def observe_envelope(*args, **kwargs):
    started = perf_counter()
    try:
        return original_transfer(*args, **kwargs)
    finally:
        envelope_calls.append(dict(context=dict(current), seconds=perf_counter()-started))


stability_tank_transfer.max_transfer = observe_envelope
started = perf_counter()
with rule_freeze_v1():
    report = full.check(instance, plan)
result = dict(mode=MODE, pid=os.getpid(), seconds=perf_counter()-started,
              report=report.model_dump(mode="json"), curves=curves,
              envelope_calls=len(envelope_calls),
              envelope_seconds=sum(row["seconds"] for row in envelope_calls),
              peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              fixture_hashes={name: hashlib.sha256((FIXTURE/name).read_bytes()).hexdigest()
                              for name in ("instance.json", "plan.json")})
(OUT / f"witness-{MODE}.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps({key:value for key,value in result.items() if key not in ("report","curves")}),
      flush=True)
assert report.passed
