"""D199: run_c1.py unchanged with the one-placement layer outside the K1b stack and no trace replay. Usage: one_place_decode.py <run_c1 args>"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT/'experiments/eval-c1'), str(ROOT/'src'), str(HERE)]

import llm.lookahead as lookahead
import one_place
import pooled_planner
import run_c1

plain = lookahead.lookahead_decoder_factory
lookahead.lookahead_decoder_factory = lambda **kw: one_place.outer_factory(plain(**kw))
pooled_planner.PooledStowLMPlanner._write_trace = lambda self, instance, metrics: {'trace_note': 'skipped: D199'}
run_c1.main()
