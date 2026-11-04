"""D143: write the current-epoch code-ready marker that rebaseline_on_host.py checks before touching Metal.

The stale deployment-d129/D131-CODE.READY pins the D131 source, so its check rightly refuses the installed epoch. This
hashes the loaded source with the same source_pins() the check uses, runs the tests/llm/exit suite, hashes again, and
writes the marker only if the suite passed and the source did not move. It never copies pins from another marker.
Usage: uv run python .../revision2/restart/write_code_ready.py
"""
from __future__ import annotations

from datetime import datetime
import re
import subprocess
import sys

from llm.exit.runner import source_pins
from state import ROOT, DEFAULT_OUT, atomic_json

MARKER = DEFAULT_OUT/'D137-CODE.READY'
COMMAND = [sys.executable, '-m', 'pytest', 'tests/llm/exit']  # addopts already has -q; another hides the count


def main():
    before = source_pins()
    done = subprocess.run(COMMAND, cwd=ROOT, capture_output=True, text=True)
    tail = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ''
    if done.returncode != 0:
        print(done.stdout[-3000:], done.stderr[-1000:], sep='\n')
        raise SystemExit(f'no marker: tests failed ({tail})')
    if source_pins() != before:
        raise SystemExit('no marker: source changed while the tests ran')
    passed = re.search(r'(\d+) passed', tail)
    if passed is None:
        raise SystemExit(f'no marker: no pass count in pytest output ({tail})')
    atomic_json(MARKER, dict(utc=datetime.now().astimezone().isoformat(), source_pins=before,
                             tests_passed=int(passed.group(1)), tests_summary=tail,
                             command='uv run pytest tests/llm/exit', gpu_started=False))
    print(f'wrote {MARKER}: {tail}', flush=True)


if __name__ == '__main__':
    main()
