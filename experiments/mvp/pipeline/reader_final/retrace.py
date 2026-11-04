"""Record the x-ray traces through the adopted reader. Traces from the earlier v2 adoption move aside, kept."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from experiments.mvp.reader.v3 import record_traces
from .reader import ROOT, identity

OLD = record_traces.TRACES / 'superseded-v2'


def main():
    known = identity()
    failed = 0
    for sentence, name, context in record_traces.jobs():
        output = record_traces.TRACES / name
        if output.exists():
            recorded = json.loads(output.read_text())['stage_record']['adapter_sha256']
            if recorded == known['adapter_sha256']:
                continue
            OLD.mkdir(exist_ok=True)
            shutil.move(str(output), OLD / name)
        command = [sys.executable, '-m', 'experiments.mvp.pipeline.xray.record', '--adapter', known['adapter_path'],
                   '--sentence', sentence, '--output', str(output)]
        if context:
            record_traces.HERO_CONTEXT.write_text(json.dumps(context) + '\n')
            command += ['--context', str(record_traces.HERO_CONTEXT)]
        failed += bool(subprocess.run(command, cwd=ROOT, env=record_traces.ENV).returncode)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
