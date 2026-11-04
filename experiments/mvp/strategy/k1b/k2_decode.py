"""D194: run_c1.py unchanged, without the trace replay, which is a second full mask pass and not part of the recipe (as in scale/run_scale.py)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT/'experiments/eval-c1'), str(ROOT/'src')]

import pooled_planner
import run_c1

pooled_planner.PooledStowLMPlanner._write_trace = lambda self, instance, metrics: {'trace_note': 'skipped: D194'}
run_c1.main()
