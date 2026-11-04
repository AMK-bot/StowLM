"""Seed hash-pinned numerical acceleration only; no cargo plans or sealed inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def seed(*, cache_root=None, report_path=None, admit=None):
    from .benchmark import wait_for_capacity
    from experiments.mvp.pipeline.contract.schema import sha256_file
    cache_root = Path(cache_root or HERE / 'cache').resolve()
    cache_root.relative_to(HERE)
    report_path = Path(report_path or HERE / 'measurements/profile-cache-seeding.json')
    report_path.resolve().relative_to(HERE)
    admission = (admit or wait_for_capacity)()
    snapshot_path = ROOT / 'experiments/mvp/pipeline/maps/runtime-snapshot.json'
    snapshot_bytes = snapshot_path.read_bytes()
    declared = json.loads(snapshot_bytes)['acceleration_cache_files']
    source_root = snapshot_path.parent / 'runtime'
    report = dict(measurement='measured', snapshot_sha256=hashlib.sha256(snapshot_bytes).hexdigest(),
                  started_utc_s=time.time(), admission=admission, files={}, plans_used=False,
                  source='maps/runtime acceleration_cache_files; exact bytes and dispatch mtimes retained')
    for relative, expected in sorted(declared.items()):
        parts = Path(relative).parts
        if parts[:3] == ('experiments', 'cache', 'stability') and len(parts) == 4:
            target = cache_root / 'stability' / parts[-1]
        elif parts[:3] == ('experiments', 'cache', 'vessels') and len(parts) == 4 and parts[-1].startswith(
                ('feeder_500.full.', 'feeder_800.full.')):
            target = cache_root / 'vessels' / parts[-1]
        else:
            continue
        source = source_root / relative
        if sha256_file(source) != expected:
            raise ValueError('Physical cache snapshot mismatch: '+relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        copied = target.is_symlink() or not target.exists() or sha256_file(target) != expected
        if copied:
            temporary = target.with_suffix(target.suffix+f'.{os.getpid()}.seed-tmp')
            shutil.copy2(source, temporary)
            if sha256_file(temporary) != expected:
                temporary.unlink()
                raise ValueError('Physical cache copy mismatch: '+relative)
            temporary.replace(target)
        # Only local regular files receive metadata; never follow an old shared-cache link.
        shutil.copystat(source, target)
        report['files'][str(target.relative_to(HERE))] = dict(
            source=relative, sha256=expected, bytes=source.stat().st_size, copied=copied)
    report.update(completed_utc_s=time.time(), file_count=len(report['files']),
                  bytes=sum(r['bytes'] for r in report['files'].values()))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2)+'\n')
    temporary.replace(report_path)
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--out', type=Path, default=HERE / 'measurements/profile-cache-seeding.json')
    args = cli.parse_args()
    report = seed(report_path=args.out)
    print(json.dumps({k: report[k] for k in ('file_count', 'bytes', 'admission')}) , flush=True)


if __name__ == '__main__':
    main()
