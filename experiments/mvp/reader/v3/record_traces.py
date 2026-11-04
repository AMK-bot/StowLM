"""Record the real x-ray traces through the selected adapter. Local weights only.

The first trace uses the lattice's hero sentence if hero-instruction.json exists
when this runs, on a public context for the hero's ship; otherwise "Close bay 7."
on the recorder's fixture ship. The second always asks for a bay no ship has, so
the mask has something to block. Recording adopts nothing. A recording that
already exists is left alone; record.py refuses to overwrite one.
Usage: python -m experiments.mvp.reader.v3.record_traces
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from experiments.mvp.reader.evaluate import read_jsonl
from experiments.mvp.reader.v3.finish_run import RESULTS, log
from experiments.mvp.reader.v3.select_adapter import OUTPUT
from experiments.mvp.reader.v3.train import DATA, ROOT

HERO = ROOT / 'experiments/mvp/pipeline/lattice/fixtures/hero-instruction.json'
TRACES = ROOT / 'experiments/mvp/pipeline/xray/fixtures'
FALLBACK = 'Close bay 7.'
ILLEGAL_BAY = 'Close bay 99.'
HERO_CONTEXT = RESULTS / 'xray-hero-context.json'
ENV = {**os.environ, 'PYTHONPATH': 'src:experiments/mvp/frontier:experiments/mvp/frontier/lab'}  # README's command


def ship_id(instance_path):
    found = re.search(r'feeder[_-]?(\d+)', str(instance_path))
    return f'feeder_{found.group(1)}' if found else None


def public_context(ship, rows):
    """A public validation context for this ship that declares its classes, or None."""
    for row in rows:
        context = row['context']
        if context.get('id') == ship and context.get('allowed_imdg_classes'):
            return context
    return None


def jobs(hero_path=HERO, rows=None):
    """(sentence, output name, context) per trace; a bad hero file means no hero."""
    first = (FALLBACK, 'real-trace.json', None)
    try:
        hero = json.loads(Path(hero_path).read_text())
        first = (hero['sentences'][0], 'real-trace.json',
                 public_context(ship_id(hero['instance_path']), read_jsonl(DATA / 'valid.jsonl') if rows is None else rows))
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    return [first, (ILLEGAL_BAY, 'real-trace-illegal-bay.json', None)]


def main():
    adapter = json.loads(OUTPUT.read_text())['selected']['path']
    failed = 0
    for sentence, name, context in jobs():
        output = TRACES / name
        if output.exists():
            continue
        command = [sys.executable, '-m', 'experiments.mvp.pipeline.xray.record', '--adapter', adapter,
                   '--sentence', sentence, '--output', str(output)]
        if context:
            HERO_CONTEXT.write_text(json.dumps(context) + '\n')
            command += ['--context', str(HERO_CONTEXT)]
        code = subprocess.run(command, cwd=ROOT, env=ENV).returncode
        log(event='xray-trace', sentence=sentence, output=str(output), exit_code=code)
        failed += bool(code)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
