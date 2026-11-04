"""Production generator and feasibility C2 availability, without adapters."""
from dataclasses import asdict
import json
from pathlib import Path
import time
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.generate.random_yard import RandomGenerationReport, generate_random_instance

started = time.monotonic()
audit = RandomGenerationReport()
instance = generate_random_instance('tiny', seed=100000000, blocking_ratio=0.,
    vessel_name='feeder_500', port_name='laem_chabang_like', report=audit,
    max_attempts=1, c2_screen=True)
engine = make_feasibility_engine(instance, get_action_space('boxslot'),
    config=FeasibilityConfig(lookahead=0, extra={'global_screen': 1}))
checks = engine.completion_check()
result = dict(generator_audit=asdict(audit), feasibility_diagnostics=dict(engine.diagnostics),
    completion_checks=checks, elapsed_s=time.monotonic()-started,
    warning='C2 necessary screen only; no plan or full-checker certificate published.')
assert audit.c2_screened and not audit.c2_errors
assert engine.diagnostics['global_screen'] == 'on'
assert checks['c2'] is True
path = Path(__file__).with_name('claude-production-smoke.json')
path.write_text(json.dumps(result,indent=2,default=str)+'\n')
print(json.dumps(result,default=str))
