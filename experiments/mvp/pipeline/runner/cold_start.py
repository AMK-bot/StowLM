"""Fresh-process startup probe; stops on the first reader call, before search.

Each launch is admitted by current fleet telemetry. Timings include interpreter
startup and imports; filesystem and vessel caches are explicitly left warm.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


class _FirstReader(BaseException):
    pass


def probe(packet_path, origin_ns):
    """Child-only measurement; never executes the engine or a real reader."""
    imported_ns = time.perf_counter_ns()
    from . import run_packet
    marks = {'imports_ready_s': (imported_ns - origin_ns) / 1e9}
    packet = json.loads(Path(packet_path).read_text())

    def stamp(name):
        marks[name] = (time.perf_counter_ns() - origin_ns) / 1e9

    def event(row):
        if row['kind'] == 'stage_end' and row['stage'] in {'assemble', 'envelope'}:
            stamp(row['stage'] + '_s')
            if row['stage'] == 'envelope':
                marks['in_envelope'] = row['data'].get('in_envelope')

    class FirstReader:
        def parse(self, sentence, context):
            stamp('first_reader_call_s')
            raise _FirstReader()

    with tempfile.TemporaryDirectory(prefix='runner-cold-') as directory:
        stamp('run_begin_s')
        try:
            outcome = run_packet(packet, parser=FirstReader(), out_dir=directory, on_event=event)
        except _FirstReader:
            marks['status'] = 'probe_complete'
        else:
            marks.update(status=outcome['status'], message=outcome['message'])
    print(json.dumps(marks, allow_nan=False))


def measure(packet_path=HERE / 'fixtures/packet.json', *, samples=3, admit=None):
    """JSON-able measured startup rows; each uses a new interpreter process."""
    from .benchmark import wait_for_capacity
    from experiments.mvp.pipeline.contract.schema import sha256_file
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
        raise ValueError('Cold-start samples must be a positive integer')
    packet_path = Path(packet_path).resolve()
    if 'sealed' in packet_path.parts:
        raise ValueError('Cold-start probes do not read sealed inputs')
    admission = admit or wait_for_capacity
    source_hashes = {name: sha256_file(HERE / name) for name in
                     ('__init__.py', 'core.py', 'assembly.py', 'profiles.py', 'cold_start.py')}
    rows = []
    for _ in range(samples):
        admitted = admission()
        env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', VECLIB_MAXIMUM_THREADS='1')
        env['PYTHONPATH'] = os.pathsep.join(str(ROOT / p) for p in ('src', 'experiments/mvp/frontier', 'experiments/mvp/frontier/lab'))
        origin = time.perf_counter_ns()
        command = [sys.executable, '-c',
                   'import sys; from experiments.mvp.pipeline.runner.cold_start import probe; '
                   'probe(sys.argv[1], int(sys.argv[2]))', str(packet_path), str(origin)]
        child = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
        if child.returncode:
            raise RuntimeError('Cold-start probe could not finish; inspect the selected packet and Python environment')
        try:
            row = json.loads(child.stdout)
        except ValueError as error:
            raise RuntimeError('Cold-start probe did not produce timing JSON') from error
        row['admission'] = admitted
        rows.append(row)
    keys = ['imports_ready_s', 'assemble_s', 'envelope_s', 'first_reader_call_s']
    medians = {key: statistics.median(row[key] for row in rows if key in row)
               for key in keys if any(key in row for row in rows)}
    phases = {}
    if all(key in medians for key in keys):
        phases = dict(interpreter_and_imports_s=medians['imports_ready_s'],
                      assembly_and_setup_s=medians['assemble_s'] - medians['imports_ready_s'],
                      envelope_s=medians['envelope_s'] - medians['assemble_s'],
                      reader_preparation_s=medians['first_reader_call_s'] - medians['envelope_s'])
    try:
        packet_name = str(packet_path.relative_to(ROOT))
    except ValueError:
        packet_name = str(packet_path)
    return dict(label='Measured cold process start; synthetic development input',
                measured_utc=datetime.now(timezone.utc).isoformat(), packet=packet_name,
                samples=samples, cold='New Python process per sample; filesystem and vessel caches left warm.',
                no_search=True, no_model=True, median_s=medians, phases_s=phases,
                dominates=max(phases, key=phases.get) if phases else None,
                source_sha256=source_hashes, rows=rows)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--packet', type=Path, default=HERE / 'fixtures/packet.json')
    cli.add_argument('--samples', type=int, default=3)
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args(argv)
    result = measure(args.packet, samples=args.samples)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
