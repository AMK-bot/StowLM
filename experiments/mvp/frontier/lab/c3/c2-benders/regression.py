"""Stored-plan regression lane, explicitly not a cold-construction benchmark.

Preserve the stored reference recipe and run the unchanged full checker. The
shared runner checks the returned plan again. No historical pass is trusted.
"""
from pathlib import Path
import os
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE/'results/vessel-cache')
sys.dont_write_bytecode = True


def finish(instance, plan, *, seed=0, finalize_s=20., label='c3-regression', context=None):
    from stowage.checker import check
    from stowage.model import BallastCondition
    started = monotonic()
    stored = (context or {}).get('stored_plan') or {}
    if stored.get('ballast'):
        plan = plan.model_copy(update={'ballast': BallastCondition.model_validate(stored['ballast'])})
    report = check(instance, plan)
    metadata = dict(lane='stored-reference-regression', cold_construction=False,
        full_checker_passed=report.passed, full_checker_s=monotonic()-started)
    return (plan if report.passed else None), metadata
