"""Record a reaped external timeout using the unchanged worker's exact identity.

This does not solve, replay, finish, or check a plan. Call only AFTER the timed
out worker has stopped. A concurrently completed cached result is preserved.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from time import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CAP_S = 1800.
POLICY = dict(name='uniform-whole-arm-map-1800s-v1', cap_s=CAP_S,
              scope='all 39 frozen maps and all three arms',
              clock='controller elapsed wall time from worker Popen',
              includes='construction, search, finishing and independent full checking',
              cleanup='SIGINT registered worker, reap it, then record timeout; cleanup may add wall time')


def declare_policy(output):
    """Bind the uniform external rule to prior results without altering them."""
    import score
    output = Path(output).resolve()
    output.relative_to(HERE)
    policy_path = output / 'measurement-policy.json'
    if policy_path.exists():
        existing = json.loads(policy_path.read_text())
        if any(existing.get(key) != value for key, value in POLICY.items()):
            raise ValueError('existing immutable measurement policy differs from the uniform cap')
        return
    completed = []
    for arm in ('frontier09', 'engine', 'graph'):
        for path in (output / arm).glob('*.json'):
            row = json.loads(path.read_text())
            if row.get('complete'):
                if row['wall_s'] > CAP_S:
                    raise ValueError(f'completed row exceeds the uniform cap: {path}')
                completed.append(dict(arm=arm, instance_id=row['instance_id'], wall_s=row['wall_s'],
                                      sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    score.write_json(policy_path, dict(**POLICY,
        declared=time(), declared_by_pid=os.getpid(), completed_before_declaration=completed,
        source_sha256={name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                       for name in ('run.py', 'parallel_run.py', 'timeout_row.py', 'cohort-manifest.json')},
        prior_rows='retained uniformly: every completed row was already at or below the same cap'))


def record_timeout(entry, arm, out='results/main'):
    """Reuse run.worker's config/run_key/cache without invoking score.evaluate."""
    name = '_c3_multiport_timeout_kernel'
    spec = importlib.util.spec_from_file_location(name, HERE / 'run.py')
    kernel = importlib.util.module_from_spec(spec)
    sys.modules[name] = kernel
    spec.loader.exec_module(kernel)
    output = (HERE / out).resolve()
    output.relative_to(HERE)
    declare_policy(output)
    original = kernel.score.evaluate

    def timeout(instance, baseline, config, **kwargs):
        return dict(instance_id=instance.id, input=config['input'], size=baseline['size'],
            n_boxes=len(instance.load_list), passed=False, lifts=None, weighted=None,
            parts=None, assignment=None, actions=None, plan=None, propose_s=None,
            order_score_s=None, finish_s=None, wall_s=CAP_S, status='outer_timeout', outer_timeout=True,
            failure_reason='External 1800-second whole-arm/map wall cap; worker interrupted and reaped',
            tries=[], candidate_errors=[], method_metadata={}, replay_legal_known=False,
            resource_policy=dict(POLICY),
            replay_evidence='unknown: interrupted worker returned no completed measurement row')

    kernel.score.evaluate = timeout
    try:
        kernel.worker(SimpleNamespace(arm=arm, input=entry['path'], size=entry['size'],
                                      ports=entry['ports'], out=out))
    finally:
        kernel.score.evaluate = original
    target = output / arm / f'{entry["instance_id"]}.json'
    row = json.loads(target.read_text())
    if row.get('status') == 'outer_timeout':
        # run.worker normally derives a boolean gate from returned candidates;
        # an interrupted computation supplied no such evidence in either direction.
        row['replay_legal_complete'] = None
        kernel.score.write_json(target, row)
    return row


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', choices=('frontier09', 'engine', 'graph'), required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--out', default='results/main')
    args = parser.parse_args()
    entries = json.loads((HERE / 'cohort-manifest.json').read_text())['maps']
    entry = next(r for r in entries if r['path'] == args.input)
    record_timeout(entry, args.arm, args.out)
