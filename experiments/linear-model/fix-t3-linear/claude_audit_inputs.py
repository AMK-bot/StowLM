"""Bind the current saved populations used by production row regression tests.

This is an input manifest, not a claimed test pass or a replacement measurement.
Failed holdout observations remain untouched. Work-in-progress audit metadata
is not an input or verification claim; only immutable population pins bind tests.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location('claude_current_input_driver',
    HERE.parent/'epoch_requalification/driver.py')
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def pin(path):
    path = path.resolve()
    return dict(path=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


pins = []
for ship in ('feeder_500','feeder_1000','feeder_1700'):
    family, _, _, _, current = driver.prepare(ship,HERE/'epoch-claude')
    for epoch in ('epoch-claude','epoch-claude-v2','epoch-claude-v3'):
        for phase in ('train','holdout'):
            path = HERE/epoch/ship/f'necessary-edges-{phase}.json'
            record = json.loads(path.read_text())
            if (record['complete'] is not True or record['physical_inputs_start'] != current
                    or record['physical_inputs_end'] != current or not record['physical_inputs_unchanged']):
                raise ValueError('current regression input is incomplete or numerically stale')
            source = ROOT/record['sampler_source']['path']
            if pin(source) != record['sampler_source']:
                raise ValueError('regression producer source changed')
            pins.append(pin(path))
    if driver.snapshot(ship,family.parameters) != current:
        raise ValueError('numerical inputs changed while binding current test inputs')
result = dict(format=1,status='input binding only; pytest XML records actual verification',
    source_pins=pins,bank=pin(ROOT/'src/stowage/linear/margin_policy_data.json'),
    producer=pin(Path(__file__)),
    independent_holdouts='576 ordinary plus197 fresh edge; earlier edge holds are explicit calibration enrichment')
target = HERE/'claude-current-audit-inputs.json'
if target.exists():
    raise ValueError('preserve existing input manifest')
driver.write(target,result)
print(json.dumps(dict(populations=len(pins),output=str(target))))
