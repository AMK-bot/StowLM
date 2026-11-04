"""Time the reader with this process's network denied: fills {{reader_seconds}} and {{reader_peak_gb}}.

Run by `sandbox-exec` with SANDBOX, so the OS refuses this process any socket. The machine's
Wi-Fi stays on, because other jobs share it; filming must still switch it off. The child proves
the block, loads the model cold, reads the hero sentences, and reports Metal peak and process peak.
Usage: python -m experiments.mvp.pipeline.reader_final.offline_timing
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
from time import perf_counter

from .read_all import HERE, READINGS, instance_of
from .reader import ROOT, AdoptedReader, reader_label, timing

SANDBOX = '(version 1)(allow default)(deny network*)'
OUTPUT = READINGS / 'offline-timing.json'
GB = 1024 ** 3


def network_denied():
    try:
        socket.create_connection(('1.1.1.1', 443), 3).close()
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def child():
    if not network_denied():
        raise SystemExit('network was not denied; not an offline measurement')
    import mlx.core as mx
    from experiments.mvp.reader.v3 import record_traces
    began = perf_counter()
    reader = AdoptedReader()
    reader.warm()
    hero = json.loads((ROOT / 'experiments/mvp/pipeline/lattice/hero.json').read_text())['hero']
    context = instance_of(hero['path'], {})
    sentences = [record_traces.jobs()[0][0], *json.loads((ROOT / 'experiments/mvp/pipeline/lattice/fixtures/hero-instruction.json').read_text())['sentences']]
    rows, labels = [], set()
    for sentence in (sentences * 12)[:12]:
        _, evidence = reader.parse(sentence, context)
        rows.append({'evidence': evidence})
        labels.add(reader_label(evidence, reader.known))
    measured = timing(rows, reader.backend.load_s)
    OUTPUT.write_text(json.dumps({
        **measured, 'reader_seconds': measured['warm_median_s'], 'total_runtime_s': perf_counter() - began,
        'startup_to_first_answer_s': measured['cold_start_s'],
        'metal_peak_gb': mx.get_peak_memory() / GB, 'process_peak_rss_gb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / GB,
        'reader_peak_gb': max(mx.get_peak_memory(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / GB,
        'network': 'denied to this process by sandbox-exec; the machine stayed online', 'labels': sorted(labels),
        'machine_load1': os.getloadavg()[0], 'sentences_read': len(rows), 'adapter_sha256': reader.known['adapter_sha256']},
        indent=2) + '\n')


def main():
    code = subprocess.run(['sandbox-exec', '-p', SANDBOX, sys.executable, '-m',
                           'experiments.mvp.pipeline.reader_final.offline_timing', '--child'], cwd=ROOT).returncode
    sys.exit(code)


if __name__ == '__main__':
    child() if sys.argv[1:] == ['--child'] else main()
