"""One-command TEST-v2.1 baseline launcher; invoke only after the leader's GO.

The caller owns admission and a fleet ledger reservation before execution.
The v4.4 source is pinned on first invocation, not during the solver campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def write_pinned(path: Path, value: dict) -> None:
    """Resume without silently replacing an earlier source identity."""
    encoded = json.dumps(value, sort_keys=True, indent=2) + '\n'
    if path.exists():
        if path.read_text() != encoded:
            raise RuntimeError(f'Existing launcher provenance differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=HERE / 'small-v21-release-frozen.json')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    parser.add_argument('--source-root', type=Path, default=HERE / 'frozen-runtime-twostep-v44')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'experiments/mvp/runs/test-twostep-small-v21-v44')
    parser.add_argument('--seed-policy', choices=('fixed', 'instance'), default='instance')
    parser.add_argument('--instance-list', type=Path)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    source = args.source_root.resolve()
    if not source.is_relative_to(HERE):
        raise ValueError('Baseline snapshot must remain inside refs_tools')
    output = args.output_dir.resolve()
    runs = ROOT / 'experiments/mvp/runs'
    if not output.is_relative_to(runs) or not output.relative_to(runs).parts[0].startswith('test-'):
        raise ValueError('Baseline output must remain inside runs/test-*')
    if not source.exists():
        subprocess.run([sys.executable, str(HERE / 'make_snapshot.py'), '--output', str(source)], check=True)
    snapshot = json.loads((source / 'snapshot.json').read_text())
    assert all(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
               for name, digest in snapshot['source_files'].items()), 'Baseline snapshot changed'
    text = (source / 'src/stowage/solver/twostep.py').read_text()
    version = re.search(r'^VERSION\s*=\s*[\"\']([^\"\']+)', text, re.MULTILINE)
    if not version or version.group(1) != '4.4.0':
        raise RuntimeError('This launcher requires the leader-qualified two-step v4.4.0')
    command = [sys.executable, str(HERE / 'run_small_v2.py'), 'twostep',
               '--release', str(args.release.resolve()), '--source-root', str(source),
               '--workers', str(args.workers), '--output-dir', str(output),
               '--seed-policy', args.seed_policy]
    if args.instance_list is not None:
        command.extend(['--instance-list', str(args.instance_list.resolve())])
    if args.limit is not None:
        command.extend(['--limit', str(args.limit)])
    source_hashes = {name: digest for name, digest in snapshot['source_files'].items()
                     if Path(name).name.startswith('twostep') and name.endswith('.py')}
    provenance = dict(version=version.group(1), source_root=str(source),
                      snapshot_sha256=hashlib.sha256((source / 'snapshot.json').read_bytes()).hexdigest(),
                      twostep_source_sha256=source_hashes,
                      launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      driver_sha256=hashlib.sha256((HERE / 'run_small_v2.py').read_bytes()).hexdigest(),
                      seed_policy=args.seed_policy)
    write_pinned(output / 'twostep-source-provenance.json', provenance)
    invocation = dict(provenance, command=command)
    command_hash = hashlib.sha256(json.dumps(command).encode()).hexdigest()
    write_pinned(output / 'launcher-invocations' / f'{command_hash}.json', invocation)
    os.execv(sys.executable, command)


if __name__ == '__main__':
    main()
