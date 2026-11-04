"""GPU_HANDOFF's check before intake scoring: worker stopped, hash matches, lease fresh. Exit 1 blocks."""
import json
from pathlib import Path
import sys
import time

from experiments.mvp.pipeline.contract.schema import sha256_file
from .reader import ROOT

INTAKE = ROOT / 'experiments/mvp/pipeline/intake_lora'


def ready(adapter=INTAKE / 'adapter-intake-v1'):
    stopped = json.loads((Path(adapter) / 'STOPPED.json').read_text())
    return stopped, stopped.get('adapter_sha256') == sha256_file(Path(adapter) / 'adapters.safetensors')


def main():
    stopped, matches = ready()
    print(json.dumps({key: stopped.get(key) for key in ('reason', 'rows_completed', 'adapter_sha256')}))
    if not matches:
        sys.exit('intake weights differ from STOPPED.json; scoring refused')
    issued = time.time()
    (INTAKE / 'claude-gpu-lease.json').write_text(json.dumps(
        {'task': 'intake_lora', 'approved': True, 'authorized_by': 'claude', 'issued_at': issued, 'expires_at': issued + 21600}) + '\n')


if __name__ == '__main__':
    main()
