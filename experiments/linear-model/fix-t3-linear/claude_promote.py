"""Promote only after current holdouts AND all saved N1 regressions pass."""
import json
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PROMOTE = HERE.parent/'epoch_requalification/promote.py'
if sys.argv[1:] not in ([], ['v3'], ['v3-enriched'], ['v3-final']):
    raise ValueError('supported candidate protocols are v2 and v3')
V3 = bool(sys.argv[1:])
FINAL = sys.argv[1:] == ['v3-final']
ENRICHED = sys.argv[1:] in (['v3-enriched'], ['v3-final'])
ORDINARY, EDGES = HERE/'epoch-claude', HERE/('epoch-claude-v3' if V3 else 'epoch-claude-v2')
CANDIDATE = HERE/('claude-candidate-final' if FINAL else 'claude-candidate-v3-enriched' if ENRICHED else
                  'claude-candidate-v3' if V3 else 'claude-candidate')
PREFIX = 'claude-final' if FINAL else 'claude-v3-enriched' if ENRICHED else 'claude-v3' if V3 else 'claude'
SHIPS = ('feeder_500','feeder_1000','feeder_1700')


def run(name, command):
    wrapper = [sys.executable, str(HERE/'d49_run.py'), str(HERE/name),
               sys.executable, *map(str, command)]
    status = subprocess.call(wrapper)
    if status:
        raise RuntimeError(name+' rejected; production bank remains unchanged')


started = time.monotonic()
paths = [EDGES/ship/f'necessary-edges-{phase}.json' for ship in SHIPS
         for phase in ('train','holdout')]
while True:
    if all(path.exists() and json.loads(path.read_text()).get('complete') is True for path in paths):
        break
    if time.monotonic()-started > 1200:
        raise TimeoutError('fresh independent edge evidence did not finish')
    time.sleep(5)

common = [PROMOTE,'--input',ORDINARY,'--necessary-edge-input',EDGES,'--include-necessary']
if ENRICHED:
    common += ['--necessary-calibration-enrichment', HERE/'epoch-claude', HERE/'epoch-claude-v2']
run(PREFIX+'-candidate-build', [*common,'--output',CANDIDATE])
candidate = CANDIDATE/'candidate-bank.json'
run(PREFIX+'-residual-gate', [HERE/'claude-prior-holdout-gate.py','--bank',candidate,
    '--output', HERE/(PREFIX+'-residual-gate.json')])
run(PREFIX+'-candidate-rows', [HERE/'claude_edge_audit.py','--bank',candidate,
    '--output',HERE/(PREFIX+'-candidate-row-audit.json')])
# The owner promoter independently rechecks every numerical input and evidence
# pin before its atomic replacement. A previous gate failure never reaches it.
run('claude-promotion', [*common,'--output',HERE/'claude-promotion','--apply'])
